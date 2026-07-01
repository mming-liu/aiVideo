"""agnes ai 文本生成 — OpenAI 兼容协议.

POST {AGNES_BASE_URL}{AGNES_TEXT_ENDPOINT}，OpenAI chat completions 格式.
403 自动回退到 AGNES_TEXT_FALLBACK_MODELS 中的下一个模型.
"""

from typing import Optional

import requests

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService


class AgnesTextService(TextGenerationService):
    """agnes ai (OpenAI 兼容) 文本生成实现.

    403 回退: 主模型返回 403 时，自动切换到 AGNES_TEXT_FALLBACK_MODELS 中的下一个.
    回退在单次调用级别（与 TTS/图片一致）.
    """

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_TEXT_MODEL
        ] + config.AGNES_TEXT_FALLBACK_MODELS

    def _advance_index(self, idx: int) -> Optional[int]:
        """返回下一个回退模型的下标，无可用模型时返回 None."""
        if idx + 1 >= len(self._model_queue):
            return None
        return idx + 1

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        url = f"{config.AGNES_BASE_URL.rstrip('/')}{config.AGNES_TEXT_ENDPOINT}"
        idx = 0
        last_error = ""

        while idx < len(self._model_queue):
            model = self._model_queue[idx]
            headers = {
                "Authorization": f"Bearer {config.AGNES_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_prompt},
                ],
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            }

            print(f"[INFO] 正在调用 Agnes ({model}) 生成分镜脚本...")
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=120)

                # ---- 200: 提取文本 ----
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if not choices:
                        last_error = f"Agnes 返回无 choices: {str(data)[:300]}"
                        break
                    text = choices[0].get("message", {}).get("content", "")
                    if not text:
                        last_error = "Agnes 返回文本为空"
                        break
                    if idx > 0:
                        print(f"  [INFO] 文本已回退到模型: {model}")
                    return TextGenerationResult(success=True, text=text)

                # ---- 403: 额度耗尽或无权限，尝试回退 ----
                if res.status_code == 403:
                    nxt = self._advance_index(idx)
                    if nxt is not None:
                        print(f"  ⚠ 文本模型 {model} 返回 403（额度耗尽/无权限），"
                              f"自动切换到 {self._model_queue[nxt]}")
                        idx = nxt
                        continue
                    last_error = (f"所有模型均返回 403，已无可用回退模型。"
                                  f"尝试过: {', '.join(self._model_queue)}")
                    break

                # ---- 其他错误 ----
                last_error = f"Agnes 失败 ({res.status_code}): {res.text[:300]}"
                break

            except Exception as e:
                last_error = f"Agnes 异常: {e}"
                break

        return TextGenerationResult(success=False, error=last_error)
