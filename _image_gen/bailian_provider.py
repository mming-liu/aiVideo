"""百炼 (Bailian) 文生图 — 基于 dashscope SDK 的 MultiModalConversation API.

使用 MultiModalConversation.call() 同步调用 qwen-image 模型.
支持多模型回退 (qwen-image-2.0-pro / qwen-image-2.0-pro-2026-04-22).

API 文档: docs/bailian/api-docs/Qwen-Image.md
"""

import os
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


# Bailian 图片 API 限速（每分钟请求数，默认 3，类似 Agnes 21s 冷却）
_BAILIAN_IMAGE_RPM = int(os.getenv("BAILIAN_IMAGE_RPM", "3"))

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
        self._current_model_index: int = 0

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

    @property
    def _current_model(self) -> str:
        return self._model_queue[self._current_model_index]

    def _advance_model(self) -> Optional[str]:
        """切换到下一个回退模型. 返回新模型名，无可用模型时返回 None."""
        if self._current_model_index + 1 >= len(self._model_queue):
            return None
        self._current_model_index += 1
        return self._current_model

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片."""
        # Bailian 限速：每个请求等待冷却
        self._key_scheduler.acquire()

        # 解析 size (如 "10x24" -> ["10", "24"])
        size_map = {
            "10x24": "10*24",
            "10x18": "10*18",
            "10x16": "10*16",
            "10x12": "10*12",
            "720x1280": "720*12",
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

        model_idx = 0
        last_error = ""
        # 429 重试参数
        _MAX_429_RETRIES = 3
        _429_RETRY_DELAY = 5  # 秒

        while model_idx < len(self._model_queue):
            model = self._model_queue[model_idx]
            retry_429 = 0

            while retry_429 <= _MAX_429_RETRIES:
                # 构建 messages 格式（MultiModalConversation 需要 messages 列表）
                # content 数组支持 text + image 混合（图生图：同框多角色各自传入肖像）
                content_items = [{"text": req.prompt}]
                ref_urls = []
                for ref_path in req.reference_image_paths:
                    url = _encode_image(ref_path)
                    if url:
                        ref_urls.append(url)
                    else:
                        _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_path}")
                if not ref_urls and req.reference_image_path:
                    url = _encode_image(req.reference_image_path)
                    if url:
                        ref_urls.append(url)
                # 裁剪到模型 I2I 上限（0~3 张），超出丢弃尾部参考图。
                # 首帧/尾帧参考图组装时已按 场景→角色→道具 排序，优先级在前，
                # 超出的尾部（多为道具/次要角色）丢弃不致命。
                if len(ref_urls) > self._max_image_refs:
                    dropped = len(ref_urls) - self._max_image_refs
                    _verbose_write(
                        f"  [镜头{req.shot_index}] 参考图 {len(ref_urls)} 张超过模型上限 "
                        f"{self._max_image_refs}，裁剪掉后 {dropped} 张（保留 场景→角色→道具 优先级）"
                    )
                    ref_urls = ref_urls[: self._max_image_refs]
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
                _verbose_write(f"    prompt: {req.prompt[:120]}...")
                _verbose_write(f"    size: {size_str}, ratio: {ratio_str}, seed: {req.seed}")
                if req.negative_prompt:
                    _verbose_write(f"    negative: {req.negative_prompt[:80]}...")
                if req.reference_image_path:
                    _verbose_write(f"    ref_image: {req.reference_image_path}")
                if ref_urls:
                    _verbose_write(f"    ref_images: {len(ref_urls)} 张已注入（图生图）")

                # 记录时间，用于超时判断
                _req_start = time.time()
                _REQUEST_TIMEOUT = 120  # 单次请求最大等待秒数

                try:
                    rsp = MultiModalConversation.call(**params)

                    if rsp.status_code == 200:
                        # 解析响应获取图片 URL
                        content = rsp.output.choices[0].message.content
                        if content and len(content) > 0:
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
                        wait = _429_RETRY_DELAY * retry_429  # 5s, 10s, 15s
                        _verbose_write(f"  [镜头{req.shot_index}] [!] 429 限速 (retry {retry_429}/{_MAX_429_RETRIES})，等待 {wait}s...")
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
                    last_error = f"生成异常: {e}"
                    break

            # 内部 retry 循环正常结束（没 break）说明超出 429 重试上限
            if retry_429 > _MAX_429_RETRIES:
                last_error = f"429 限速重试 {_MAX_429_RETRIES} 次后仍然失败"
                break

            # last_error 非空表示内层 break 了（非模型切换），不再尝试下一个模型
            if last_error and rsp.status_code != 403:
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
                # 首帧/尾帧用前缀区分（避免尾帧覆盖首帧的 shot_N.png）
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