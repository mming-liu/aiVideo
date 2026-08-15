"""百炼 (Bailian) 文生图 — 基于 dashscope SDK 的 MultiModalConversation API.

使用 MultiModalConversation.call() 同步调用 qwen-image 模型.
支持多模型回退 (qwen-image-2.0-pro / qwen-image-2.0-pro-2026-04-22).

API 文档: docs/bailian/api-docs/Qwen-Image.md
"""

import os
import re
import time
from typing import Optional

import dashscope
from dashscope import MultiModalConversation

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService
from shared.config import (
    BAILIAN_REGION,
    BAILIAN_WORKSPACE_ID,
    BAILIAN_API_KEY,
    BAILIAN_T2I_MODEL,
)
from shared.key_scheduler import KeyScheduler
from shared.error_fmt import parse_retry_after
from shared.ref_compose import get_ref_limit, fit_flat


# Bailian 图片 API 限速（每分钟请求数，默认 300，类似 Agnes 21s 冷却）
# 官方限流页：wan2.7-image / wan2.7-image-pro RPS=5（≈300/min），并发 5。
# 旧 qwen-image 系列是 RPM=1/min，若回退到该系列模型请覆盖此值。
_BAILIAN_IMAGE_RPM = int(os.getenv("BAILIAN_IMAGE_RPM", "300"))

from functools import partial  # noqa: E402
from shared.log_utils import verbose_write  # noqa: E402
_verbose_write = partial(verbose_write, log_file="step02a.log")


def _encode_image(path: Optional[str]) -> Optional[str]:
    """读取本地图片，返回 data URL (base64)，供 MultiModalConversation 图生图使用。"""
    if not path:
        return None
    try:
        import base64
        import mimetypes
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return None
        mime, _ = mimetypes.guess_type(p)
        mime = mime or "image/png"
        with open(p, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


class BailianImageService(ImageGenerationService):
    """百炼文生图实现.

    使用 dashscope SDK 的 MultiModalConversation 接口:
      - call() → 同步返回图片 URL

    模型回退:
      - 主模型返回 403 时，自动切换到 BAILIAN_I2V_FALLBACK_MODELS 中的下一个
    """

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        fallback_models = [
            m.strip() for m in os.getenv("BAILIAN_T2I_FALLBACK_MODELS", "").split(",")
            if m.strip()
        ]
        self._model_queue: list[str] = [BAILIAN_T2I_MODEL] + fallback_models

        # 模型参考图上限（I2I 模式）：
        # qwen-image-2.0-2in1 / qwen-image 系列支持 0~3 个 image content items
        # （0 = T2I，1~3 = I2I）。上层 force_multi_ref 会绕过 IMAGE_MULTI_REF 全传
        # 参考图（钩子 5 张 = 场景 + 3 角色 + 道具），provider 必须在此裁剪到模型上限，
        # 否则 400: "Model supports 0~3 image content items. Got N image items."
        # 可通过环境变量 IMAGE_MAX_REFS 覆盖（部分模型支持更多参考图）。
        self._max_image_refs = int(os.getenv("IMAGE_MAX_REFS", "3"))

        # 设置 dashscope API key 和 Bailian Workspace URL
        dashscope.api_key = BAILIAN_API_KEY
        # Bailian 使用 Workspace 专用端点
        if BAILIAN_WORKSPACE_ID and BAILIAN_REGION:
            dashscope.base_url = f"https://{BAILIAN_WORKSPACE_ID}.{BAILIAN_REGION}.maas.aliyuncs.com"
            # 静默—URL 仅在需要时查日志

        # Bailian 单 key 限速（类似 Agnes）
        self._key_scheduler = KeyScheduler(
            n_keys=1,
            cooldown=60 / _BAILIAN_IMAGE_RPM if _BAILIAN_IMAGE_RPM > 0 else 0,
            name="bailian_image",
        )

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片."""
        # Bailian 限速：每个请求等待冷却
        # #23 修复：acquire 加 timeout 上限（180s），429 长冷却时不无限阻塞
        self._key_scheduler.acquire(timeout=180)

        # 解析 size (如 "10x24" -> ["10", "24"])
        size_map = {
            "10x24": "10*24",
            "10x18": "10*18",
            "10x16": "10*16",
            "10x12": "10*12",
            "720x1280": "720*1280",
            "1024x1024": "10*10",
            "10*24": "10*24",
            "10*18": "10*18",
            "10*16": "10*16",
            "10*12": "10*12",
            "720*12": "720*1280",
            "10*10": "10*10",
        }
        size_str = size_map.get(req.size.replace("x", "*"), req.size.replace("x", "*"))

        # 解析 ratio (如 "9:16" -> ["9", "16"])
        ratio_str = req.ratio.replace(":", "*")
        # #17b 修复 2026-08-14：ratio 此前只进日志不进请求，长宽比配置静默失效。
        # qwen-image 用 size（宽*高）表达宽高比；当 req.size 是档位（如 "2K"，
        # 见 _image_gen/models.py 推荐用法 size="2K" + ratio="9:16"）而非像素时，
        # 按 ratio 推导官方推荐像素尺寸。已是 "W*H" 像素值的 size 原样保留。
        _RATIO_TO_SIZE = {
            "9:16": "1536*2688",
            "16:9": "2688*1536",
            "1:1": "2048*2048",
            "3:4": "1728*2368",
            "4:3": "2368*1728",
        }
        if not re.fullmatch(r"\d+\*\d+", size_str) and req.ratio in _RATIO_TO_SIZE:
            derived_size = _RATIO_TO_SIZE[req.ratio]
            _verbose_write(f"  [镜头{req.shot_index}] size={size_str!r} 非像素值，"
                           f"按 ratio={req.ratio} 推导 size={derived_size}")
            size_str = derived_size

        model_idx = 0
        last_error = ""
        # 429 重试参数
        # bailian 图生图模型（qwen-image 系列）限流为 RPM=1/min，429 表示 1 分钟冷却窗口。
        # 不能按 5/10/15s 递增快速重发——窗口没到重发必再 429，白烧免费额度。
        # 策略：优先读服务端 Retry-After/错误体剩余秒数；读不到则按完整冷却窗口兜底。
        # 冷却窗口按 BAILIAN_IMAGE_RPM 推导（wan2.7-image 默认 RPS=5 ≈ 窗口 0.2s，
        # qwen-image RPM=1 ≈ 窗口 60s），兜底至少 5s 避免高并发下瞬时限流。
        _MAX_429_RETRIES = 2
        _429_RATE_LIMIT_WINDOW = 60.0 / _BAILIAN_IMAGE_RPM if _BAILIAN_IMAGE_RPM > 0 else 60.0  # 秒
        _429_RATE_LIMIT_WINDOW = max(_429_RATE_LIMIT_WINDOW, 5.0)  # 下限 5s
        _429_RETRY_BACKOFF = 2  # 秒：窗口末尾的额外缓冲（网络抖动/时钟偏差）

        while model_idx < len(self._model_queue):
            model = self._model_queue[model_idx]
            # 模型感知参考图上限（qwen-image=3 / wan2.7-image=9，见 shared.ref_compose
            # 注册表）+ 无损压缩：超上限保留前 N-1 张原图 + 尾部拼 1 张参考表，
            # 参考图信息不丢失（替换旧版「丢弃尾部」）。403 回退到不同上限的
            # 模型时各自按注册表重算。在编码前、重试循环外只做一次。
            ref_limit = get_ref_limit(model, default=self._max_image_refs)
            ref_paths = list(req.reference_image_paths or [])
            if not ref_paths and req.reference_image_path:
                ref_paths = [req.reference_image_path]
            if len(ref_paths) > ref_limit:
                sheet_dir = os.path.join(config.OUTPUT_DIR, "ref_sheets")
                composed = fit_flat(ref_paths, ref_limit, sheet_dir, f"shot{req.shot_index}")
                _verbose_write(
                    f"  [镜头{req.shot_index}] {model} 参考图 {len(ref_paths)} 张超上限 "
                    f"{ref_limit}，无损压缩为 {len(composed)} 张（原图 + 尾部参考表）"
                )
                ref_paths = composed
            retry_429 = 0

            while retry_429 <= _MAX_429_RETRIES:
                # 构建 messages 格式（MultiModalConversation 需要 messages 列表）
                # content 数组支持 text + image 混合（图生图：同框多角色各自传入肖像）
                content_items = [{"text": req.prompt}]
                ref_urls = []
                for ref_path in ref_paths:
                    url = _encode_image(ref_path)
                    if url:
                        ref_urls.append(url)
                    else:
                        _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_path}")
                if not ref_urls and req.reference_image_path:
                    url = _encode_image(req.reference_image_path)
                    if url:
                        ref_urls.append(url)
                for url in ref_urls:
                    content_items.append({"image": url})
                messages = [
                    {
                        "role": "user",
                        "content": content_items,
                    }
                ]

                # 构建参数
                params = {
                    "model": model,
                    "messages": messages,
                    "parameters": {
                        "size": size_str,
                        "n": 1,  # 生成1张
                        # 官方 wan-image 文档：图像编辑模式（I2I/多图参考）下
                        # prompt_extend 默认 true，会智能改写正向提示词。
                        # 我们的首帧/尾帧 prompt 已精调（构图锚定/服装一致/站姿锁定），
                        # 改写会破坏锚定 → 显式关闭，用原始提示词。
                        "prompt_extend": False,
                    }
                }

                # 添加 negative_prompt
                if req.negative_prompt:
                    params["parameters"]["negative_prompt"] = req.negative_prompt

                # 添加 seed（用于可复现）
                if req.seed is not None:
                    params["parameters"]["seed"] = req.seed

                frame_label = req.frame_desc if req.frame_desc else "首帧"
                _verbose_write(f"  [镜头{req.shot_index}] 正在生成{frame_label} [{model}]...")
                # 完整请求参数日志（v1.1）：prompt 全文 + 参考图源路径（不打印 base64 内容）
                _verbose_write(f"[DEBUG] image_submit: model={model}")
                _verbose_write(f"[DEBUG] image_submit: size={size_str}, ratio={ratio_str}, seed={req.seed}, n=1")
                _verbose_write(f"[DEBUG] image_submit: prompt全文={req.prompt}")
                if req.negative_prompt:
                    _verbose_write(f"[DEBUG] image_submit: negative={req.negative_prompt}")
                # 参考图：打印源文件路径（用户需要知道发了哪些照片）+ base64 长度
                _ref_sources = list(req.reference_image_paths) if req.reference_image_paths else []
                if not _ref_sources and req.reference_image_path:
                    _ref_sources = [req.reference_image_path]
                for i, src in enumerate(_ref_sources):
                    _verbose_write(f"[DEBUG] image_submit: ref[{i}] 源路径={src}")
                for i, url in enumerate(ref_urls):
                    b64_len = len(url.split(",", 1)[-1]) if "," in url else len(url)
                    _verbose_write(f"[DEBUG] image_submit: ref[{i}] base64_len={b64_len}")
                if ref_urls:
                    _verbose_write(f"[DEBUG] image_submit: ref_images={len(ref_urls)} 张已注入（图生图）")

                # 记录时间，用于超时判断
                _req_start = time.time()
                _REQUEST_TIMEOUT = 120  # 单次请求最大等待秒数

                try:
                    rsp = MultiModalConversation.call(**params)

                    if rsp.status_code == 200:
                        # 解析响应获取图片 URL（#17 修复 2026-08-14：choices 可能为空，需边界保护）
                        output_choices = getattr(rsp.output, "choices", None) or []
                        if not output_choices:
                            return ImageGenerationResult(
                                success=False,
                                error=f"响应中无 choices: {str(rsp.output)[:300]}",
                            )
                        content = getattr(output_choices[0].message, "content", None) or []
                        if len(content) > 0:
                            img_data = content[0]
                            if "image" in img_data:
                                img_url = img_data["image"]
                                return self._download(req, img_url)

                        return ImageGenerationResult(
                            success=False,
                            error="响应中未找到图片 URL",
                        )

                    # ---- 429: 请求限速，等待后重试 ----
                    if rsp.status_code == 429:
                        retry_429 += 1
                        # 优先用服务端返回的剩余等待秒数（Retry-After 头 / 错误体 retry 字段）；
                        # 读不到则按 RPM 推导的完整冷却窗口兜底（收敛 shared/rate_limit）。
                        wait = None
                        retry_after_hdr = getattr(rsp, "headers", {}).get("Retry-After") \
                            or getattr(rsp, "headers", {}).get("retry-after")
                        if retry_after_hdr:
                            try:
                                wait = float(retry_after_hdr)
                            except (TypeError, ValueError):
                                wait = None
                        if wait is None:
                            from shared.rate_limit import compute_429_wait
                            wait = compute_429_wait(
                                getattr(rsp, "message", "") or getattr(rsp, "text", ""),
                                fallback_seconds=_429_RATE_LIMIT_WINDOW,
                                backoff_seconds=_429_RETRY_BACKOFF,
                            )
                        else:
                            wait = max(wait, 1.0) + _429_RETRY_BACKOFF
                        _verbose_write(f"  [镜头{req.shot_index}] [!] 429 限速 (retry {retry_429}/{_MAX_429_RETRIES})，"
                              f"等待 {wait:.0f}s（RPM={_BAILIAN_IMAGE_RPM}/min 冷却窗口）...")
                        time.sleep(wait)
                        continue

                    # ---- 403: 额度耗尽或无权限，尝试回退 ----
                    if rsp.status_code == 403:
                        if model_idx + 1 < len(self._model_queue):
                            model_idx += 1
                            _verbose_write(f"  [镜头{req.shot_index}] [!] 模型 {model} 返回 403，"
                                  f"切换到 {self._model_queue[model_idx]}")
                            break  # 跳出 retry 循环，进入下一个模型
                        last_error = f"所有模型均返回 403: {rsp.message}"
                        break

                    # ---- 其他错误 ----
                    last_error = f"API 错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}"
                    break

                except Exception as e:
                    # 异常路径：必须是显式标记退出条件。此前这里只 break 当前迭代，
                    # retry_429 不递增 → while retry_429 <= _MAX_429_RETRIES 永远成立 →
                    # 403 免费额度耗尽时无限重发（08-09 实测 645 次提交、0 张产出）。
                    # 显式赋退出标记，让下方统一 break 逻辑生效。
                    last_error = f"生成异常: {e}" if str(e) else "生成异常（无错误详情，疑似限流/额度/参数问题）"
                    break

            # 内部 retry 循环正常结束（没 break）说明超出 429 重试上限
            if retry_429 > _MAX_429_RETRIES:
                last_error = f"429 限速重试 {_MAX_429_RETRIES} 次后仍然失败"
                break

            # last_error 非空表示内层 break 了（非模型切换），不再尝试下一个模型
            # 注意：异常路径 rsp 可能未定义，必须用局部变量判断，避免 NameError
            if last_error:
                break

        return ImageGenerationResult(success=False, error=last_error)

    def _download(self, req: ImageGenerationRequest, img_url: str) -> ImageGenerationResult:
        """下载图片到本地，区分首帧/尾帧/关键帧文件名."""
        import requests
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            # 显式保存路径优先（资产图/肖像等非帧图，直接落盘到目标目录）
            frame_label = req.frame_desc if req.frame_desc else "首帧"
            if req.save_path:
                save_path = req.save_path
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            else:
                # 首帧/尾帧用前缀区分（避免尾帧覆盖首帧的 shot_N.png）；
                # 关键帧带序号（frame_desc 由 bridge 传「关键帧{i}」），
                # 防多个中间帧同名互相覆盖（P5 关键帧 2→1）
                kf_m = re.match(r"^关键帧(\d+)", frame_label or "")
                if kf_m:
                    fname_prefix = f"kf_{kf_m.group(1)}"
                else:
                    fname_prefix = {
                        "首帧": "ff",
                        "尾帧": "lf",
                    }.get(frame_label, "kf")
                save_path = os.path.join(
                    config.OUTPUT_DIR, f"shot_{req.shot_index}_{fname_prefix}.png"
                )

            resp = requests.get(img_url, timeout=180)
            resp.raise_for_status()
            img_bytes = resp.content
            if len(img_bytes) < 5 * 1024:
                raise RuntimeError(f"下载内容过小 ({len(img_bytes)} bytes)，可能是错误页面")
            with open(save_path, "wb") as f:
                f.write(img_bytes)

            size_kb = len(img_bytes) / 1024
            _verbose_write(f"  [镜头{req.shot_index}] [OK] {frame_label}已保存 -> {save_path} ({size_kb:.1f} KB)")
            return ImageGenerationResult(success=True, save_path=save_path)

        except Exception as e:
            return ImageGenerationResult(success=False, error=f"下载失败: {e}")