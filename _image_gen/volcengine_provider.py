"""火山引擎方舟 (Volcengine Ark) 文生图 — Seedream 系列.

OpenAI 兼容协议:
  POST {base}/images/generations
  Authorization: Bearer {VOLCENGINE_API_KEY}

支持:
- 文生图 (T2I): model + prompt + size
- 图生图 (I2I): 附加 image 字段（单张字符串 / 多张数组，base64 data URL 或公网 URL）
- 多模型 403 回退 (seedream 4.5 → 4.0)
- 429 限速按服务端剩余时间等待

官方文档: 火山方舟 图片生成 API
https://www.volcengine.com/docs/82379/1541594
"""

import base64
import mimetypes
import os
import re
import time
from functools import partial
from typing import Optional

import requests

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService
from shared.config import (
    VOLCENGINE_API_KEY,
    VOLCENGINE_BASE_URL,
    VOLCENGINE_T2I_MODEL,
    VOLCENGINE_T2I_FALLBACK_MODELS,
    VOLCENGINE_IMAGE_SIZE,
)
from shared.key_scheduler import KeyScheduler
from shared.rate_limit import compute_429_wait
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02a.log")


def _encode_image(path: Optional[str]) -> Optional[str]:
    """读取本地图片，返回 base64 data URL（Seedream 接受 data URI）."""
    if not path:
        return None
    try:
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


# 官方 2K 档位标准宽高映射（Seedream 图片生成 API「实际映射宽高像素值」表，
# 5.0 pro / 4.5 / 4.0 / lite 共用的常见档位）
_RATIO_CANONICAL = {
    "9:16": (1600, 2848),
    "16:9": (2848, 1600),
    "1:1": (2048, 2048),
    "3:4": (1728, 2304),
    "4:3": (2304, 1728),
}


def _fit_pixel_size(w: int, h: int, pmin: int, pmax: int) -> str:
    """等比缩放宽高像素值到总像素 [pmin, pmax] 区间（偶数对齐，保持宽高比）.

    取整后可能仍越界，微调较短边逐步收敛；落到区间即可（比例仅漂移几个像素）。
    """
    total = w * h
    if pmin <= total <= pmax:
        return f"{w}x{h}"
    import math
    target = pmin if total < pmin else pmax
    scale = math.sqrt(target / total)
    w2 = max(16, int(w * scale) // 2 * 2)
    h2 = max(16, int(h * scale) // 2 * 2)
    for _ in range(8):
        t = w2 * h2
        if pmin <= t <= pmax:
            break
        if t < pmin:
            if h2 <= w2:
                h2 += 2
            else:
                w2 += 2
        else:
            if h2 >= w2:
                h2 -= 2
            else:
                w2 -= 2
    return f"{w2}x{h2}"


def _resolve_size(req: ImageGenerationRequest, model: str) -> str:
    """按模型能力把 req.size / ratio 转成 Seedream 认可的 size（档位或像素值）.

    不同 Seedream 档位像素范围差异大（4.0 最小 921600，4.5/lite 最小 3686400）：
    - 像素值：总像素在模型 [px_min, px_max] 内则透传，越界等比缩放兜底——
      保证流水线默认 1024x1792（帧图）/ 1600x900（资产图）在 4.5 上不被拒，
      而 4.0 可直接用小尺寸省钱。
    - 档位值：模型支持且 ratio 未知时原样传（方式1）；否则按 ratio 映射官方
      标准像素值（方式2，保证 9:16 竖屏不被模型默认成方形）。
    """
    from shared.model_caps import get_seedream_cap
    cap = get_seedream_cap(model)
    # 可选 override：VOLCENGINE_IMAGE_SIZE 非空则强制全局统一 size（档位或像素），
    # 仍按当前模型能力收敛；空则按分镜 req.size/ratio 推导
    size = (VOLCENGINE_IMAGE_SIZE or req.size or "").strip().lower().replace("*", "x")
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", size)
    if m:
        return _fit_pixel_size(int(m.group(1)), int(m.group(2)), cap.px_min, cap.px_max)
    tier = size.upper()  # 与 size 同源（override 优先）
    ratio = (req.ratio or "").strip().lower()
    if tier in cap.tiers and ratio not in _RATIO_CANONICAL:
        return tier
    base = _RATIO_CANONICAL.get(ratio, _RATIO_CANONICAL["9:16"])
    if tier and ratio in _RATIO_CANONICAL:
        _verbose_write(f"  [镜头{req.shot_index}] size={tier} 按 ratio={ratio} 推导像素 "
              f"size={base[0]}x{base[1]}")
    return _fit_pixel_size(base[0], base[1], cap.px_min, cap.px_max)


class VolcengineImageService(ImageGenerationService):
    """火山方舟 Seedream 文生图/图生图实现.

    403 回退: 主模型返回 403 时切换到 VOLCENGINE_T2I_FALLBACK_MODELS 中的下一个.
    """

    supports_multi_ref: bool = True

    def __init__(self) -> None:
        config.validate()
        if not VOLCENGINE_API_KEY:
            raise ValueError(
                "没有配置 VOLCENGINE_API_KEY（.env.keys 中设置，兼容 ARK_API_KEY）"
            )
        self._model_queue: list[str] = [VOLCENGINE_T2I_MODEL] + VOLCENGINE_T2I_FALLBACK_MODELS
        self._base_url = VOLCENGINE_BASE_URL.rstrip("/")
        # 单 key 限速
        rpm = max(int(os.getenv("VOLCENGINE_IMAGE_RPM", "10")), 1)
        self._key_scheduler = KeyScheduler(n_keys=1, cooldown=60.0 / rpm, name="volcengine_image")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {VOLCENGINE_API_KEY}",
            "Content-Type": "application/json",
        }

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        self._key_scheduler.acquire(timeout=180)

        # 原始参考图路径（来自 request，循环内按当前模型能力重新计算压缩后的 ref_paths/ref_urls）
        from shared.ref_compose import fit_flat
        from shared.model_caps import get_seedream_cap
        original_refs = list(req.reference_image_paths or [])
        if not original_refs and req.reference_image_path:
            original_refs = [req.reference_image_path]

        model_idx = 0
        last_error = ""
        # 2026-08-29：OutputImageSensitiveContentDetected 专用旗。
        # 当主模型 + 所有 fallback 都因输出安全过滤失败时，标记为软跳过：
        # 不阻塞整批生成（避免 1 张刺杀/流血图把整条 shot 链路 kill），
        # 仅记录日志 + 标记 soft_skip=True，下游（vimax_adapters）不 raise。
        # 与"真实失败"（如 5xx、限速、解析错误）区分——后者仍 raise 失败链路。
        sensitive_skip = False

        while model_idx < len(self._model_queue):
            model = self._model_queue[model_idx]
            cap = get_seedream_cap(model)
            # 关键修复：按当前模型能力重算 ref_limit（每个模型上限可能不同：
            # 4.0/4.5/5.0-lite 免费版=3，5.0-pro=10，4.5/4.0 付费版=14）。
            # 旧实现在循环外用主模型上限，回退到不同能力模型后可能超限或浪费额度。
            ref_limit = cap.max_refs
            ref_paths = list(original_refs)
            if len(ref_paths) > ref_limit:
                sheet_dir = os.path.join(config.OUTPUT_DIR, "ref_sheets")
                ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"shot{req.shot_index}")
                _verbose_write(f"  [镜头{req.shot_index}] 模型 {model} 参考图上限 {ref_limit}，"
                      f"原 {len(original_refs)} 张无损压缩为 {len(ref_paths)} 张")
            # base64 编码也按当前模型的最终 ref_paths 重新生成
            ref_urls = []
            for ref_path in ref_paths:
                url = _encode_image(ref_path)
                if url:
                    ref_urls.append(url)
                else:
                    _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_path}")
            size_str = _resolve_size(req, model)
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "size": size_str,
                "response_format": "url",
                "watermark": False,
            }
            if ref_urls:
                # Seedream: 单张用字符串，多张用数组
                payload["image"] = ref_urls[0] if len(ref_urls) == 1 else ref_urls
            # 官方图片生成 API 不提供 negative_prompt / seed；仅当模型能力声明
            # 支持时才发送，避免每张图都触发 400 重试（cap 默认 False）
            if req.negative_prompt and cap.supports_negative_prompt:
                payload["negative_prompt"] = req.negative_prompt
            if req.seed is not None and cap.supports_seed:
                payload["seed"] = req.seed

            frame_label = req.frame_desc if req.frame_desc else "首帧"
            _verbose_write(f"  [镜头{req.shot_index}] 正在生成{frame_label} [{model}]...")
            _verbose_write(f"[DEBUG] image_submit: model={model}, size={size_str}, "
                  f"ratio={req.ratio}, refs={len(ref_urls)}, seed={req.seed}")
            _verbose_write(f"[DEBUG] image_submit: prompt全文={req.prompt}")
            for i, src in enumerate(ref_paths):
                _verbose_write(f"[DEBUG] image_submit: ref[{i}] 源路径={src}")

            try:
                resp = requests.post(
                    f"{self._base_url}/images/generations",
                    json=payload,
                    headers=self._headers,
                    timeout=180,
                )
            except Exception as e:
                last_error = f"提交异常: {e}"
                break

            _verbose_write(f"[DEBUG] image_submit response: status={resp.status_code}, "
                  f"body={resp.text[:500]}")

            # ---- 200: 解析并下载 ----
            if resp.status_code == 200:
                try:
                    data = resp.json().get("data", [])
                except Exception as e:
                    last_error = f"响应解析失败: {e} ({resp.text[:200]})"
                    break
                if not data:
                    last_error = f"响应中无 data: {resp.text[:300]}"
                    break
                item = data[0]
                img_url = item.get("url")
                if not img_url:
                    last_error = f"响应中未找到图片 URL: {resp.text[:300]}"
                    break
                return self._download(req, img_url, model_idx)

            # ---- 429: 限速，等待后重试 ----
            if resp.status_code == 429:
                wait = compute_429_wait(resp.text, fallback_seconds=60.0 / 10, backoff_seconds=1.0)
                _verbose_write(f"  [镜头{req.shot_index}] [!] 429 限速，等待 {wait:.0f}s 后重试...")
                time.sleep(wait)
                continue

            # ---- 403: 额度耗尽/无权限，回退模型 ----
            if resp.status_code == 403:
                if model_idx + 1 < len(self._model_queue):
                    model_idx += 1
                    _verbose_write(f"  [镜头{req.shot_index}] [!] 模型 {model} 返回 403，"
                          f"切换到 {self._model_queue[model_idx]}")
                    continue
                last_error = f"所有模型均返回 403: {resp.text[:300]}"
                break

            # ---- 400 + OutputImageSensitiveContentDetected：输出图片被内容安全过滤拦截 ----
            # 2026-08-29：触发场景=古风权谋刺杀/流血/持械对峙（钩子首帧的「黑衣人刺」、
            # v6.32 强制伤处可见的「暗红洇在白袍」、尾帧「匕首没入水中」等）。
            # 哪怕 prompt 已被 sanitize_prompt 替换成"出人命/倒下/暗红洇痕"，
            # Seedream 生成的实际图片仍会含血/刀，output 端过滤器拒收。
            # 处理：① 走 _model_queue 下一个模型（4.0/5.0-lite 规则基线可能不同）
            #       ② 全失败则 soft_skip=True，下游不 raise 阻塞整批
            # 不重写 prompt（确定性兜底范围外，应触发 LLM regen 而非代码层硬填）。
            if resp.status_code == 400 and "OutputImageSensitiveContentDetected" in resp.text:
                if model_idx + 1 < len(self._model_queue):
                    model_idx += 1
                    _verbose_write(f"  [镜头{req.shot_index}] [!] 模型 {model} 输出触发内容安全过滤，"
                          f"切换到 {self._model_queue[model_idx]} 重试")
                    continue
                # 所有模型均因输出敏感失败 → soft_skip 标记，不阻塞后续分镜
                last_error = (f"所有模型均因输出内容安全过滤失败 (OutputImageSensitiveContentDetected)；"
                              f"该 shot 软跳过，需人工复核 prompt 是否含高敏画面元素")
                sensitive_skip = True
                _verbose_write(f"  [镜头{req.shot_index}] [SKIP] OutputImageSensitiveContentDetected "
                      f"全模型失败，soft-skip（不阻塞后续 shot）")
                break

            # ---- 400 且报 negative_prompt 不支持：去掉后重试一次 ----
            if resp.status_code == 400 and "negative_prompt" in resp.text and payload.get("negative_prompt"):
                _verbose_write(f"  [镜头{req.shot_index}] [!] {model} 不支持 negative_prompt，去掉后重试")
                payload.pop("negative_prompt", None)
                continue

            # ---- 5xx / 连接类：重试 ----
            if 500 <= resp.status_code < 600:
                _verbose_write(f"  [镜头{req.shot_index}] [!] 服务端 {resp.status_code}，3s 后重试...")
                time.sleep(3)
                continue

            last_error = f"API 错误 [{resp.status_code}]: {resp.text[:300]}"
            break

        return ImageGenerationResult(
            success=False, error=last_error,
            soft_skip=sensitive_skip,  # 2026-08-29：仅 OutputImageSensitiveContentDetected 全失败时为 True
        )

    def _download(self, req: ImageGenerationRequest, img_url: str, model_idx: int) -> ImageGenerationResult:
        """下载图片到本地，区分首帧/尾帧/关键帧文件名."""
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            frame_label = req.frame_desc if req.frame_desc else "首帧"
            if req.save_path:
                save_path = req.save_path
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            else:
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
            if model_idx > 0:
                _verbose_write(f"  [INFO] 图片已回退到模型: {self._model_queue[model_idx]}")
            _verbose_write(f"  [镜头{req.shot_index}] [OK] {frame_label}已保存 -> "
                  f"{save_path} ({size_kb:.1f} KB)")
            return ImageGenerationResult(
                success=True,
                save_path=save_path,
                image_url=img_url,
            )
        except Exception as e:
            return ImageGenerationResult(success=False, error=f"下载失败: {e}")
