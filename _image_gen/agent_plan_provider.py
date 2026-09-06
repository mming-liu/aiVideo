"""火山方舟 Agent Plan (/api/plan/v3) 文生图 — Seedream 系列.

与标准方舟 (api/v3) 的区别：
  - 端点：{base}/images/generations（路径与 /v3 一致，body/response 兼容）
  - 鉴权：Bearer AGENT_PLAN_API_KEY（**专属 key，不通用**——普通 ARK key 报 401）
  - 模型：doubao-seedream-5-0-lite（默认，免费档，参考图上限 3 张见 shared/ref_compose）
  - 切换：IMAGE_PROVIDER=agent_plan（.env.content）；volcengine provider 不受影响

设计要点（2026-08-30）：
  - 单一模型队列（Agent Plan 当前未配 fallback；后续新增可走 _model_queue 模式）
  - 复用 shared.model_caps.get_seedream_cap / shared.ref_compose.fit_flat 与 volcengine 同款
  - 复用 volcengine _encode_image（图片转 base64 data URL 逻辑一致）
  - 输出 save_path/shot_index/frame_desc 语义与 volcengine 对齐（首帧/尾帧/关键帧命名一致）
  - KeyScheduler 单 key 限速（默认 10 RPM，留 6s 间隔）

官方文档：火山方舟 Agent Plan
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
from functools import partial
from typing import Optional

import requests

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService
from shared.config import (
    AGENT_PLAN_API_KEY,
    AGENT_PLAN_BASE_URL,
    AGENT_PLAN_T2I_MODEL,
    AGENT_PLAN_IMAGE_RPM,
)
from shared.key_scheduler import KeyScheduler
from shared.image_size_fit import fit_pixel_size, fit_tier
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


class AgentPlanImageService(ImageGenerationService):
    """火山方舟 Agent Plan 文生图/图生图实现.

    与 volcengine provider 行为对齐（save_path/shot_index/frame_desc），
    但走 /api/plan/v3 通道，鉴权/端点/并发约束独立。
    """

    supports_multi_ref: bool = True

    def __init__(self) -> None:
        config.validate()
        if not AGENT_PLAN_API_KEY:
            raise ValueError(
                "没有配置 AGENT_PLAN_API_KEY（.env.keys 中设置，Agent Plan 专属 key，"
                "与 VOLCENGINE_API_KEY 不通用）"
            )
        self._model = AGENT_PLAN_T2I_MODEL
        self._base_url = AGENT_PLAN_BASE_URL.rstrip("/")
        # 单 key 限速：Agent Plan 免费档按 Small 套餐保守值 10 RPM（与 volcengine 默认对齐）
        rpm = max(AGENT_PLAN_IMAGE_RPM, 1)
        self._key_scheduler = KeyScheduler(
            n_keys=1, cooldown=60.0 / rpm, name="agent_plan_image"
        )

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {AGENT_PLAN_API_KEY}",
            "Content-Type": "application/json",
        }

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        self._key_scheduler.acquire(timeout=180)

        from shared.ref_compose import fit_flat
        from shared.model_caps import get_seedream_cap

        cap = get_seedream_cap(self._model)
        ref_limit = cap.max_refs
        original_refs = list(req.reference_image_paths or [])
        if not original_refs and req.reference_image_path:
            original_refs = [req.reference_image_path]

        ref_paths = list(original_refs)
        if len(ref_paths) > ref_limit:
            sheet_dir = os.path.join(config.OUTPUT_DIR, "ref_sheets")
            ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"shot{req.shot_index}")
            _verbose_write(
                f"  [镜头{req.shot_index}] Agent Plan 模型 {self._model} 参考图上限 {ref_limit}，"
                f"原 {len(original_refs)} 张无损压缩为 {len(ref_paths)} 张"
            )

        ref_urls: list[str] = []
        for ref_path in ref_paths:
            url = _encode_image(ref_path)
            if url:
                ref_urls.append(url)
            else:
                _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_path}")

        # Agent Plan 端点 body 形态与 /v3 一致（model + prompt + size + image）；
        # 鉴权 key 决定走 plan 通道（命中按 AFP 燃料计费）。
        size = (req.size or "").strip().lower().replace("*", "x")
        # 像素值合法性快速校验：拒绝空字符串、0x0、负数（按 Seedream 能力收敛）
        m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", size)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            total = w * h
            if total < cap.px_min or total > cap.px_max:
                fitted = fit_pixel_size(w, h, cap.px_min, cap.px_max)
                _verbose_write(
                    f"  [镜头{req.shot_index}] size {w}x{h}={total} 超出 {self._model} 像素区间 "
                    f"{cap.px_min}~{cap.px_max}，等比收敛到 {fitted}"
                )
                size = fitted
        else:
            # 档位值：不在模型支持档位时按档位序就近收敛（shared/image_size_fit.fit_tier）
            if size.upper() not in {t.upper() for t in cap.tiers}:
                size = fit_tier(size, cap.tiers)

        # v6.61（09-05 hook kf_2 两次 InputTextSensitiveContentDetected 根因）：
        # 输入文本含高敏词（「匕首柄抵近杨旭」）→ 400 拒收。此前直接返回失败，
        # 上游跳帧、磁盘留陈旧旧图。修法：命中输入侧敏感时用 shared.content_safety
        # 全表 sanitize（输出侧 OutputImageSensitiveContentDetected 已有 fallback/
        # soft-skip 路径，输入侧此路为唯一重试机会），重试 1 次——清洗后仍 400
        # 则按真失败返回（不让敏感词无限烧额度）。
        payload: dict = {
            "model": self._model,
            "prompt": req.prompt,
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        if ref_urls:
            payload["image"] = ref_urls[0] if len(ref_urls) == 1 else ref_urls

        frame_label = req.frame_desc if req.frame_desc else "首帧"
        _verbose_write(
            f"  [镜头{req.shot_index}] [Agent Plan] 正在生成{frame_label} [{self._model}]..."
        )
        _verbose_write(
            f"[DEBUG] agent_plan_image_submit: model={self._model}, size={size}, "
            f"refs={len(ref_urls)}, seed={req.seed}"
        )
        _verbose_write(f"[DEBUG] agent_plan_image_submit: prompt全文={req.prompt}")

        try:
            resp = requests.post(
                f"{self._base_url}/images/generations",
                json=payload,
                headers=self._headers,
                timeout=180,
            )
        except Exception as e:
            return ImageGenerationResult(success=False, error=f"提交异常: {e}")

        # ---- 400 + InputTextSensitiveContentDetected：两级清洗重试 ----
        # v6.61：第 1 级 sanitize（全表，常已在组装期跑过 → 多半无变化）；
        # 第 2 级 soften_input_text（输入侧专用降级表：匕首柄抵近→悬停、
        # 刺向→指向——09-05 hook kf_2 两次 400 实证，触发词是持械逼临
        # 组合，全表 sanitize 对已清洗 prompt 无效，必须二次降级）。
        if resp.status_code == 400 and "InputTextSensitiveContentDetected" in resp.text:
            from shared.content_safety import sanitize as _safety_sanitize, soften_input_text as _soften
            sanitized = _safety_sanitize(req.prompt)
            if sanitized == req.prompt:
                sanitized = _soften(req.prompt)
            if sanitized and sanitized != req.prompt:
                _verbose_write(
                    f"  [镜头{req.shot_index}] [!] InputTextSensitiveContentDetected，"
                    f"合规清洗 prompt 后重试 1 次"
                )
                payload["prompt"] = sanitized
                try:
                    resp = requests.post(
                        f"{self._base_url}/images/generations",
                        json=payload,
                        headers=self._headers,
                        timeout=180,
                    )
                except Exception as e:
                    return ImageGenerationResult(success=False, error=f"重试提交异常: {e}")

        _verbose_write(
            f"[DEBUG] agent_plan_image_submit response: status={resp.status_code}, "
            f"body={resp.text[:500]}"
        )

        if resp.status_code == 200:
            try:
                data = resp.json().get("data", [])
            except Exception as e:
                return ImageGenerationResult(success=False, error=f"响应解析失败: {e} ({resp.text[:200]})")
            if not data:
                return ImageGenerationResult(success=False, error=f"响应中无 data: {resp.text[:300]}")
            item = data[0]
            img_url = item.get("url")
            if not img_url:
                return ImageGenerationResult(success=False, error=f"响应中未找到图片 URL: {resp.text[:300]}")
            return self._download(req, img_url)

        # ---- 401: Agent Plan 专属 key 缺失或不通用 ----
        if resp.status_code == 401:
            return ImageGenerationResult(
                success=False,
                error=(
                    f"Agent Plan 鉴权失败 [401]: 普通 ARK key 不通用，请到方舟控制台"
                    f"「Agent → 接入」申请 AGENT_PLAN_API_KEY 专属 key。"
                    f" 响应: {resp.text[:200]}"
                ),
            )

        # ---- 403: 套餐无权限 / 燃料耗尽 ----
        if resp.status_code == 403:
            return ImageGenerationResult(
                success=False,
                error=(
                    f"Agent Plan 套餐无权限或燃料耗尽 [403]: Small 套餐当前不支持"
                    f"「{self._model}」模型或参考图；可查 {self._base_url}/fuel/query 燃料余额。"
                    f" 响应: {resp.text[:200]}"
                ),
            )

        # ---- 400: 参数错误（如模型不支持 negative_prompt/seed）----
        if resp.status_code == 400:
            return ImageGenerationResult(
                success=False, error=f"参数错误 [400]: {resp.text[:300]}"
            )

        return ImageGenerationResult(
            success=False, error=f"API 错误 [{resp.status_code}]: {resp.text[:300]}"
        )

    def _download(self, req: ImageGenerationRequest, img_url: str) -> ImageGenerationResult:
        """下载图片到本地，区分首帧/尾帧/关键帧文件名.

        文件名规则与 volcengine provider 对齐：ff/lf/kf_N.png。
        """
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
            _verbose_write(
                f"  [镜头{req.shot_index}] [OK] [Agent Plan] {frame_label}已保存 -> "
                f"{save_path} ({size_kb:.1f} KB)"
            )
            return ImageGenerationResult(
                success=True, save_path=save_path, image_url=img_url
            )
        except Exception as e:
            return ImageGenerationResult(success=False, error=f"下载失败: {e}")
