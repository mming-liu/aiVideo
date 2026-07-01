"""DashScope (阿里百炼 Qwen) 文本生成 — HTTP REST.

直接 POST 到 DashScope 文本生成端点，返回 OpenAI 风格 choices.
"""

import json
import requests

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService


class DashScopeTextService(TextGenerationService):
    """百炼 DashScope 文本生成实现."""

    def __init__(self) -> None:
        config.validate()

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        headers = {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}
        payload = {
            "model": config.DASHSCOPE_TEXT_MODEL,
            "input": {
                "messages": [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_prompt},
                ]
            },
            "parameters": {
                "result_format": "json",
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            },
        }

        print(f"[INFO] 正在调用 DashScope ({config.DASHSCOPE_TEXT_MODEL}) 生成分镜脚本...")
        try:
            res = requests.post(
                config.DASHSCOPE_BASE_URL, json=payload, headers=headers, timeout=120
            )
            if not res.ok:
                return TextGenerationResult(
                    success=False,
                    error=f"HTTP {res.status_code}: {res.text[:500]}",
                )

            api_response = res.json()
            output = api_response.get("output", {})
            choices = output.get("choices", [])
            if choices and isinstance(choices, list):
                text = choices[0].get("message", {}).get("content", "")
            else:
                text = output.get("text", "")

            if not text:
                return TextGenerationResult(
                    success=False,
                    error=f"API 返回文本为空，完整响应: "
                          f"{json.dumps(api_response, ensure_ascii=False)[:500]}",
                )
            return TextGenerationResult(success=True, text=text)
        except Exception as e:
            return TextGenerationResult(success=False, error=str(e))
