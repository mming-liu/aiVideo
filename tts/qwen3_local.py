"""tts.qwen3_local - Qwen3-TTS-12Hz-1.7B-CustomVoice 的**唯一**隔离客户端（计划 §T1/§T3）.

选型（计划 §5 第一段）：新模型只选 Qwen3-TTS-12Hz-1.7B-CustomVoice——固定 speaker 加
中文 instruction。本模块是它与主项目之间的**唯一**接口，三条边界写死：

1. **不进主进程**：主项目不装 CUDA/PyTorch 栈。本模块只通过 HTTP 与一个**独立进程/
   服务**通话（`QWEN3_LOCAL_SERVICE_URL`），绝不 `import torch`；
2. **默认零真实调用**：`QWEN3_LOCAL_ENABLE` 默认 `0`，未开启时 :func:`service_status`
   返回 `not_run`，:meth:`Qwen3LocalCustomVoiceProvider.synthesize` 返回**失败**结果
   （`status="not_run"`）——**不报成功、不伪造音频**；
3. **不自动下载**：权重目录（`QWEN3_LOCAL_MODEL_DIR`）不存在时就报 `not_run`，
   本模块没有任何下载行为。真实模型只在「已存在合法本地权重 + 已有运行授权」时执行。

工具侧（`tools/tts_candidate.py inspect` / `plan`）用 :func:`real_model_report` 输出
`real_model=not_run|<revision>`，把「接口包已通过」与「真实模型跑过」分开记账。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .config import config
from .models import (ProviderCapabilities, TTSFailureResult, TTSRequest,
                     TTSSuccessResult, TTSServiceResult)
from .service import TTSServiceInterface

PROVIDER_NAME = "qwen3_local_customvoice"
MODEL_ID_DEFAULT = "Qwen3-TTS-12Hz-1.7B-CustomVoice"

#: 状态枚举：ready = 服务在且权重在；not_run = 未启用/无权重（**不是失败，也不是成功**）；
#: unavailable = 启用了但服务探测不通（明确报错，不退化成 not_run）
STATE_READY = "ready"
STATE_NOT_RUN = "not_run"
STATE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LocalServiceStatus:
    state: str
    reason: str
    model_dir: str = ""
    service_url: str = ""
    model_id: str = MODEL_ID_DEFAULT
    detail: dict = None  # type: ignore[assignment]

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    def as_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason,
                "model_dir": self.model_dir, "service_url": self.service_url,
                "model_id": self.model_id, "detail": dict(self.detail or {})}


def _cfg(name: str, env: str, default: str = "") -> str:
    """读配置：**运行时 env 优先**，退回 tts.config 的 import 期快照.

    `tts/config.py` 的类属性是 import 时求值；本地客户端是「按需开、默认关」的开关，
    读运行时 env 才不会因为导入顺序把开关状态冻错（生产里两者一致，测试/工具里前者
    更可控）。默认值仍是 `QWEN3_LOCAL_ENABLE=0` —— 零真实调用。
    """
    raw = os.getenv(env, "").strip()
    if raw:
        return raw
    return str(getattr(config, name, default) or default).strip()


def _enabled() -> bool:
    return _cfg("QWEN3_LOCAL_ENABLE", "QWEN3_LOCAL_ENABLE", "0").lower() in (
        "1", "yes", "true", "on")


def _model_dir() -> str:
    return _cfg("QWEN3_LOCAL_MODEL_DIR", "QWEN3_LOCAL_MODEL_DIR")


def _service_url() -> str:
    return _cfg("QWEN3_LOCAL_SERVICE_URL", "QWEN3_LOCAL_SERVICE_URL").rstrip("/")


def _weights_present(model_dir: str) -> tuple:
    """权重是否就位：目录存在且有内容（**只读**，不下载）."""
    if not model_dir:
        return False, "未配置 QWEN3_LOCAL_MODEL_DIR"
    p = Path(model_dir)
    if not p.is_dir():
        return False, f"权重目录不存在: {p}"
    try:
        entries = [x for x in p.iterdir() if not x.name.startswith(".")]
    except OSError as exc:
        return False, f"权重目录读不动: {p}: {exc}"
    if not entries:
        return False, f"权重目录为空: {p}"
    return True, ""


def service_status(*, probe: bool = True, timeout: float = 5.0) -> LocalServiceStatus:
    """本地客户端当前状态（**默认零真实调用**：未启用时连探测都不发）."""
    model_id = os.getenv("QWEN3_LOCAL_MODEL_ID", "").strip() or MODEL_ID_DEFAULT
    model_dir = _model_dir()
    url = _service_url()
    ok, why = _weights_present(model_dir)
    if not _enabled():
        return LocalServiceStatus(
            STATE_NOT_RUN,
            "QWEN3_LOCAL_ENABLE 未开启（默认零真实调用；真实模型只在已有运行授权时执行）",
            model_dir, url, model_id, {"weights_present": ok, "weights_reason": why})
    if not ok:
        return LocalServiceStatus(STATE_NOT_RUN, why, model_dir, url, model_id,
                                  {"weights_present": False})
    if not probe:
        return LocalServiceStatus(STATE_READY, "已启用且权重就位（未探测服务）",
                                  model_dir, url, model_id, {"weights_present": True})
    try:
        res = requests.get(url + "/health", timeout=timeout)
        detail = {"status_code": res.status_code}
        try:
            detail.update({"body": res.json()})
        except ValueError:
            detail.update({"body": res.text[:200]})
        if res.status_code == 200:
            return LocalServiceStatus(STATE_READY, "本地服务就绪", model_dir, url,
                                      model_id, detail)
        return LocalServiceStatus(STATE_UNAVAILABLE,
                                  f"本地服务健康检查非 200: {res.status_code}",
                                  model_dir, url, model_id, detail)
    except requests.RequestException as exc:
        return LocalServiceStatus(STATE_UNAVAILABLE,
                                  f"本地服务探测失败（独立进程未启动？）: {exc}",
                                  model_dir, url, model_id, {})


def _static_capabilities() -> ProviderCapabilities:
    speakers = tuple(s.strip() for s in
                     os.getenv("QWEN3_LOCAL_SPEAKERS", "").split(",") if s.strip())
    raw_cap = os.getenv("QWEN3_LOCAL_MAX_CHARS", "").strip()
    try:
        max_chars = int(raw_cap) if raw_cap else None
    except ValueError:
        max_chars = None
    return ProviderCapabilities(
        provider=PROVIDER_NAME,
        max_chars_per_call=max_chars,
        supports_instruction=True,       # CustomVoice 的核心特性：固定 speaker + 中文 instruction
        supports_rate=False,             # 模型不做语速参数；不支持就如实声明
        supports_volume=False,
        supports_speaker=True,
        legal_speakers=speakers,
        output_formats=("wav",),
        sample_rates=(),
        model_id=os.getenv("QWEN3_LOCAL_MODEL_ID", "").strip() or MODEL_ID_DEFAULT,
        model_revision=str(getattr(config, "MODEL_REVISION", "") or ""),
        real_model=False,                # 静态声明**从不**声称真实模型已就绪
        evidence="Qwen3-TTS-12Hz-1.7B-CustomVoice（计划 §5 选型）：固定 speaker + "
                 "中文 instruction；单次长度上限未据官方依据声明（可用 "
                 "QWEN3_LOCAL_MAX_CHARS 显式声明）；本地服务协议见本模块 docstring",
    )


def declared_capabilities() -> ProviderCapabilities:
    """静态能力声明（不探测服务、不发网络请求；供服务登记表与规划使用）."""
    return _static_capabilities()


def real_model_report() -> dict:
    """给工具/报告用的 `real_model` 分账读数（**不调用模型**）."""
    st = service_status(probe=False)
    return {"provider": PROVIDER_NAME, "state": st.state, "reason": st.reason,
            "model_id": st.model_id, "model_dir": st.model_dir,
            "real_model": None if st.state != STATE_READY else st.model_id,
            "real_model_run": False}


class Qwen3LocalCustomVoiceProvider(TTSServiceInterface):
    """本地 CustomVoice 服务的客户端（**独立进程/服务**，主项目不装 torch）."""

    PROVIDER_NAME = PROVIDER_NAME

    def __init__(self, service_url: Optional[str] = None, *, probe: bool = True):
        self._url = (service_url or _service_url()).rstrip("/")
        self._status = service_status(probe=probe)
        if service_url:
            self._status = LocalServiceStatus(
                self._status.state, self._status.reason, self._status.model_dir,
                self._url, self._status.model_id, self._status.detail)

    # -- 能力事实 --
    def capabilities(self) -> ProviderCapabilities:
        base = _static_capabilities()
        if self._status.ready == base.real_model:
            return base
        return ProviderCapabilities(
            provider=base.provider, max_chars_per_call=base.max_chars_per_call,
            supports_instruction=base.supports_instruction,
            supports_rate=base.supports_rate, supports_volume=base.supports_volume,
            supports_speaker=base.supports_speaker, legal_speakers=base.legal_speakers,
            output_formats=base.output_formats, sample_rates=base.sample_rates,
            model_id=base.model_id, model_revision=base.model_revision,
            real_model=self._status.ready, evidence=base.evidence)

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        status = service_status()
        if not status.ready:
            # **不是静默跳过、更不是成功**：把 not_run / unavailable 如实返回
            return TTSFailureResult(
                error="本地 CustomVoice 未运行（%s）：%s" % (status.state, status.reason),
                code="not_run" if status.state == STATE_NOT_RUN else "service_unavailable",
                status=status.state, field_path="provider")
        text = str(getattr(req, "text", "") or "")
        if not text.strip():
            return TTSFailureResult(error="空正文不能合成", code="empty_text",
                                    field_path="text")
        speaker = str(getattr(req, "effective_speaker", lambda: "")() or "").strip()
        if not speaker:
            speaker = os.getenv("QWEN3_LOCAL_SPEAKER", "").strip()
        if not speaker:
            return TTSFailureResult(
                error="未给 speaker，且未配置 QWEN3_LOCAL_SPEAKER——"
                      "CustomVoice 的声线必须显式给出（不做静默默认）",
                code="speaker_not_legal", field_path="speaker")
        payload = {
            "model": str(getattr(req, "model", "") or "") or MODEL_ID_DEFAULT,
            "text": text,
            "speaker": speaker,
            "instruction": str(getattr(req, "instruction", "") or ""),
            "language": str(getattr(req, "language", "") or "zh"),
            "format": "wav",
        }
        try:
            res = requests.post(
                self._url + "/synthesize", json=payload,
                timeout=float(_cfg("QWEN3_LOCAL_TIMEOUT_SEC", "QWEN3_LOCAL_TIMEOUT_SEC",
                                   "600") or 600))
        except requests.RequestException as exc:
            return TTSFailureResult(error=f"本地服务调用失败: {exc}",
                                    code="service_unavailable", status=STATE_UNAVAILABLE)
        if res.status_code != 200:
            return TTSFailureResult(
                error=f"本地服务返回 {res.status_code}: {res.text[:300]}",
                code="provider_failed", field_path="provider")
        ctype = (res.headers.get("Content-Type") or "").lower()
        audio_bytes = res.content
        actual_format = "wav"
        if "application/json" in ctype:
            # 服务也可以回 {"audio_path": ...} / {"error": ...}
            try:
                data = res.json()
            except ValueError:
                return TTSFailureResult(error="本地服务返回非 JSON 且非音频",
                                        code="bad_response", field_path="provider")
            if data.get("error"):
                return TTSFailureResult(error=str(data["error"]), code="provider_failed",
                                        field_path="provider")
            audio_path = str(data.get("audio_path") or "")
            if not audio_path or not Path(audio_path).is_file():
                return TTSFailureResult(
                    error="本地服务未返回可用的 audio_path（不伪造音频）",
                    code="provider_output_missing", field_path="save_path")
            out = _actual_wav_path(output or audio_path)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(Path(audio_path).read_bytes())
            return TTSSuccessResult(save_path=out, actual_format=actual_format,
                                    applied={"instruction": bool(payload["instruction"]),
                                             "rate": False, "volume": False})
        if not audio_bytes:
            return TTSFailureResult(error="本地服务返回空音频", code="empty_audio",
                                    field_path="provider")
        out = _actual_wav_path(output)
        self._save_audio(audio_bytes, out)
        return TTSSuccessResult(audio_bytes=audio_bytes, save_path=out,
                                actual_format=actual_format,
                                applied={"instruction": bool(payload["instruction"]),
                                         "rate": False, "volume": False})


def _actual_wav_path(output) -> str:
    out = Path(output or config.OUTPUT_PATH)
    return str(out.with_suffix(".wav"))


def service_launch_hint() -> str:
    """给运维/报告看的启动提示（本模块**不**自动拉起服务、不下载权重）."""
    return (
        "本地 CustomVoice 需要**独立进程**：\n"
        "  1) 在项目外的隔离环境装 Qwen3-TTS-12Hz-1.7B-CustomVoice 并准备权重；\n"
        "  2) 启动 HTTP 服务（约定端点：GET /health、POST /synthesize）；\n"
        "  3) 设 QWEN3_LOCAL_MODEL_DIR=<权重目录>、QWEN3_LOCAL_SERVICE_URL=<地址>、\n"
        "     QWEN3_LOCAL_ENABLE=1 后再跑（默认 0 = 零真实调用）。\n"
        "本模块不自动下载权重、不在主进程 import torch；无权重时一律报 not_run。"
    )


__all__ = ["PROVIDER_NAME", "MODEL_ID_DEFAULT", "STATE_READY", "STATE_NOT_RUN",
           "STATE_UNAVAILABLE", "LocalServiceStatus", "service_status",
           "real_model_report", "service_launch_hint",
           "Qwen3LocalCustomVoiceProvider"]
