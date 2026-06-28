"""DashScope TTS 供应商实现.

提供两种调用方式:
  - DashScopeSDKProvider: dashscope.audio.tts Python SDK (WebSocket)
  - DashScopeHttpProvider:  直接 HTTP POST 到 DashScope REST 端点

两种实现均支持 403 自动回退到下一个模型.
"""

from typing import Optional

import dashscope
import requests

from .config import config
from .models import TTSRequest, TTSSuccessResult, TTSFailureResult, TTSServiceResult
from .service import TTSServiceInterface


# ---------------------------------------------------------------------------
# 公共: 模型队列管理
# ---------------------------------------------------------------------------

def _build_model_queue(req_model: str) -> list[str]:
    """根据请求模型 + 配置回退列表，构建完整模型队列."""
    base = req_model or config.MODEL
    queue = [base]
    for m in config.FALLBACK_MODELS:
        if m != base:
            queue.append(m)
    return queue


# ---------------------------------------------------------------------------
# 1. DashScope Python SDK（推荐）
# ---------------------------------------------------------------------------

class DashScopeSDKProvider(TTSServiceInterface):
    """使用 dashscope.audio.tts.SpeechSynthesizer (WebSocket SDK).

    403 回退: 主模型返回 403 时，自动切换到 FALLBACK_MODELS 中的下一个.
    """

    def __init__(self) -> None:
        config.validate()
        dashscope.api_key = config.API_KEY

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        from dashscope.audio.tts.speech_synthesizer import SpeechSynthesizer

        model_queue = _build_model_queue(req.model)
        last_error = ""

        for idx, model in enumerate(model_queue):
            try:
                response = SpeechSynthesizer.call(
                    model=model,
                    text=req.text,
                    format=req.format,
                    sample_rate=req.sample_rate,
                    volume=req.volume,
                    rate=req.rate,
                )

                resp_dict = response.get_response()
                status = resp_dict.get("status_code", -1)

                if status == 200:
                    audio = response.get_audio_data()
                    if not audio:
                        return TTSFailureResult(error=f"模型 {model} 返回音频为空")

                    out = output or config.OUTPUT_PATH
                    self._save_audio(audio, out)
                    if idx > 0:
                        print(f"  [INFO] TTS 已回退到模型: {model}")
                    return TTSSuccessResult(audio_bytes=audio, save_path=out)

                # ---- 403: 额度耗尽或无权限 ----
                if status == 403:
                    next_model = model_queue[idx + 1] if idx + 1 < len(model_queue) else None
                    if next_model:
                        print(f"  ⚠ TTS 模型 {model} 返回 403（额度耗尽/无权限），"
                              f"自动切换到 {next_model}")
                        continue
                    else:
                        return TTSFailureResult(
                            error=f"所有模型均返回 403，已无可用回退模型。"
                                  f"尝试过: {', '.join(model_queue)}"
                        )

                # ---- 其他错误 ----
                last_error = (f"DashScope SDK 失败 (HTTP {status}): "
                              f"[{resp_dict.get('code', '')}] {resp_dict.get('message', '')}")
                break  # 非 403 错误不重试

            except Exception as e:
                last_error = f"DashScope SDK 异常: {e}"
                break

        return TTSFailureResult(error=last_error)


# ---------------------------------------------------------------------------
# 2. DashScope HTTP REST 端点
# ---------------------------------------------------------------------------

class DashScopeHttpProvider(TTSServiceInterface):
    """直接 HTTP POST 到 DashScope TTS REST 端点.

    403 回退: 主模型返回 403 时，自动切换到 FALLBACK_MODELS 中的下一个.
    """

    DASHSCOPE_TTS_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/speech-synthesis"
    )

    def __init__(self) -> None:
        config.validate()

    def synthesize(self, req: TTSRequest, output: Optional[str] = None) -> TTSServiceResult:
        model_queue = _build_model_queue(req.model)
        last_error = ""

        for idx, model in enumerate(model_queue):
            headers = {
                "Authorization": f"Bearer {config.API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "input": {"text": req.text},
                "parameters": {
                    "format": req.format,
                    "sample_rate": req.sample_rate,
                    "volume": req.volume,
                    "speech_rate": int((req.rate - 1.0) * 500),  # SDK rate → REST speech_rate
                },
            }

            try:
                res = requests.post(
                    self.DASHSCOPE_TTS_URL,
                    json=payload,
                    headers=headers,
                    timeout=120,
                )

                # ---- 200 + 音频数据 ----
                if res.status_code == 200:
                    ct = res.headers.get("Content-Type", "")
                    if "application/json" in ct:
                        data = res.json()
                        return TTSFailureResult(
                            error=f"DashScope HTTP 返回 JSON (200): {data.get('message', str(data))}"
                        )

                    audio = res.content
                    if not audio:
                        return TTSFailureResult(error=f"模型 {model} 返回音频为空")

                    out = output or config.OUTPUT_PATH
                    self._save_audio(audio, out)
                    if idx > 0:
                        print(f"  [INFO] TTS 已回退到模型: {model}")
                    return TTSSuccessResult(audio_bytes=audio, save_path=out)

                # ---- 403: 额度耗尽或无权限 ----
                if res.status_code == 403:
                    next_model = model_queue[idx + 1] if idx + 1 < len(model_queue) else None
                    if next_model:
                        print(f"  ⚠ TTS 模型 {model} 返回 403（额度耗尽/无权限），"
                              f"自动切换到 {next_model}")
                        continue
                    else:
                        return TTSFailureResult(
                            error=f"所有模型均返回 403，已无可用回退模型。"
                                  f"尝试过: {', '.join(model_queue)}"
                        )

                # ---- 其他错误 ----
                ct = res.headers.get("Content-Type", "")
                if "application/json" in ct:
                    data = res.json()
                    last_error = (f"DashScope HTTP 失败 ({res.status_code}): "
                                  f"{data.get('message', str(data))}")
                else:
                    last_error = f"DashScope HTTP 失败 ({res.status_code}): {res.text[:200]}"
                break  # 非 403 错误不重试

            except Exception as e:
                last_error = f"DashScope HTTP 异常: {e}"
                break

        return TTSFailureResult(error=last_error)
