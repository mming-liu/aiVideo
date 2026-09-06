"""MiniMax 文生图/图生图 — image-01 系列.

官方协议:
  POST https://api.minimax.io/v1/image_generation
  Authorization: Bearer {MINIMAX_API_KEY}

支持:
- 文生图 (T2I): model + prompt + aspect_ratio
- 图生图 (I2I): 附加 subject_reference（官方仅支持单张参考图，type=character）
- 多模型 403 回退 (image-01 → image-01-live，仅 MiniMax 系列)
- 429 限速按服务端剩余时间等待

官方文档: https://platform.minimax.io/docs/api-reference/image-generation-t2i
注意: subject_reference 只接受单张参考图；多参考图场景由适配器层
supports_multi_ref=False 自动降级为肖像拼图。
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
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    MINIMAX_T2I_MODEL,
    MINIMAX_T2I_FALLBACK_MODELS,
    MINIMAX_IMAGE_RPM,
)
from shared.key_scheduler import KeyScheduler
from shared.rate_limit import compute_429_wait
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02a.log")

# MiniMax 官方支持的纵横比（T2I API，9:16 = 720x1280 竖屏）
_RATIO_CANONICAL = ("1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9")


def _encode_image(path: Optional[str]) -> Optional[str]:
    """读取本地图片，返回 base64 data URL（subject_reference 接受 data URI）."""
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


def _resolve_ratio(req: ImageGenerationRequest) -> str:
    """按 req.ratio 映射 MiniMax aspect_ratio；未知比例回退 9:16 竖屏."""
    ratio = (req.ratio or "").strip().lower()
    if ratio in _RATIO_CANONICAL:
        return ratio
    # 兼容 "720x1280" 这类像素串：按宽高推导比例
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", ratio)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w == h:
            return "1:1"
        return "16:9" if w > h else "9:16"
    return "9:16"


class MiniMaxImageService(ImageGenerationService):
    """MiniMax image-01 文生图/图生图实现.

    403 回退: 主模型返回 403 时切换到 MINIMAX_T2I_FALLBACK_MODELS 中的下一个.
    """

    # subject_reference 官方只支持单张参考图，多参考图由适配器层拼图为一张
    supports_multi_ref: bool = False

    def __init__(self) -> None:
        config.validate()
        if not MINIMAX_API_KEY:
            raise ValueError(
                "没有配置 MINIMAX_API_KEY（.env.keys 中设置）"
            )
        self._model_queue: list[str] = [MINIMAX_T2I_MODEL] + MINIMAX_T2I_FALLBACK_MODELS
        self._base_url = MINIMAX_BASE_URL.rstrip("/")
        # 单 key 限速
        rpm = max(MINIMAX_IMAGE_RPM, 1)
        self._key_scheduler = KeyScheduler(n_keys=1, cooldown=60.0 / rpm, name="minimax_image")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        self._key_scheduler.acquire(timeout=180)

        # 参考图上限按模型从 ref_compose 注册表解析（image-01=1），不写死在配置里
        from shared.ref_compose import get_ref_limit, fit_refs
        ref_limit = get_ref_limit(MINIMAX_T2I_MODEL)
        ref_paths = list(req.reference_image_paths or [])
        if not ref_paths and req.reference_image_path:
            ref_paths = [req.reference_image_path]
        if len(ref_paths) > ref_limit:
            from shared.paths import shot_work_dir
            sheet_dir = shot_work_dir(req.shot_index)
            merged = fit_refs([("all", ref_paths)], ref_limit, sheet_dir, f"shot{req.shot_index}")
            if merged:
                _verbose_write(f"  [镜头{req.shot_index}] 参考图 {len(ref_paths)} 张超上限 "
                      f"{ref_limit}，压缩为 {len(merged)} 张")
                ref_paths = merged
            else:
                ref_paths = ref_paths[:ref_limit]

        ref_url = None
        if ref_paths:
            ref_url = _encode_image(ref_paths[0])
            if not ref_url:
                _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_paths[0]}")

        model_idx = 0
        last_error = ""

        while model_idx < len(self._model_queue):
            model = self._model_queue[model_idx]
            ratio = _resolve_ratio(req)
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "aspect_ratio": ratio,
                "response_format": "url",
                "prompt_optimizer": False,  # 已人工精调，关闭改写
            }
            if ref_url:
                payload["subject_reference"] = [{"type": "character", "image_file": ref_url}]

            frame_label = req.frame_desc if req.frame_desc else "首帧"
            _verbose_write(f"  [镜头{req.shot_index}] 正在生成{frame_label} [{model}]...")
            _verbose_write(f"[DEBUG] image_submit: model={model}, ratio={ratio}, "
                  f"refs={len(ref_paths)}, seed={req.seed}")
            _verbose_write(f"[DEBUG] image_submit: prompt全文={req.prompt}")
            for i, src in enumerate(ref_paths):
                _verbose_write(f"[DEBUG] image_submit: ref[{i}] 源路径={src}")

            try:
                resp = requests.post(
                    f"{self._base_url}/v1/image_generation",
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
                    body = resp.json()
                    data = body.get("data") or {}
                    image_urls = data.get("image_urls") or []
                except Exception as e:
                    last_error = f"响应解析失败: {e} ({resp.text[:200]})"
                    break
                if not image_urls:
                    last_error = f"响应中无 image_urls: {resp.text[:300]}"
                    break
                return self._download(req, image_urls[0], model_idx)

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

            # ---- 5xx / 连接类：重试 ----
            if 500 <= resp.status_code < 600:
                _verbose_write(f"  [镜头{req.shot_index}] [!] 服务端 {resp.status_code}，3s 后重试...")
                time.sleep(3)
                continue

            last_error = f"API 错误 [{resp.status_code}]: {resp.text[:300]}"
            break

        return ImageGenerationResult(success=False, error=last_error)

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
                    }.get(frame_label, "misc")  # v6.61：未知帧类不再落 kf 槽位（doodle_refine 曾存成 shot_N_kf.png 冒充关键帧）
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
