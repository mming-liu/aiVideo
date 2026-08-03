"""DeepSeek 文本生成 — Responses API 调用.

优先走官方 Responses API（POST /responses）：
  - 仅 deepseek-v4-flash 支持
  - reasoning.effort=none 语义化关闭思考模式（chat completions 的 thinking 参数
    会被 openai SDK 拒绝，responses API 原生支持）
  - text.format.json_schema 结构化输出（strict），分镜 JSON 天然合法
  - 输出独立 message item，思维链是独立的 reasoning item，不再混在正文里

非 v4-flash 模型（如 deepseek-v4-pro）回退到 Chat Completions。
"""

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService

import re


class DeepSeekTextService(TextGenerationService):
    """DeepSeek (Responses API) 文本生成实现.

    支持:
    - deepseek-v4-flash: Responses API（json_schema 结构化输出）
    - deepseek-v4-pro / deepseek-reasoner: 回退 Chat Completions
    """

    def __init__(self) -> None:
        config.validate()
        self._model = config.DEEPSEEK_MODEL

    def _is_v4_flash(self) -> bool:
        return "flash" in self._model.lower()

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        try:
            from openai import OpenAI
        except ImportError:
            return TextGenerationResult(
                success=False,
                error="使用 DeepSeek 需要安装 openai 库: pip install openai",
            )

        try:
            client = OpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
            )

            if self._is_v4_flash():
                return self._generate_responses(client, req)
            return self._generate_chat(client, req)
        except Exception as e:
            return TextGenerationResult(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Responses API（deepseek-v4-flash）
    # ------------------------------------------------------------------
    def _generate_responses(self, client, req: TextGenerationRequest) -> TextGenerationResult:
        """走 Responses API：关闭思考 + json_schema 结构化输出."""
        print(f"[INFO] 正在调用 DeepSeek Responses ({self._model}) 生成分镜脚本...")

        params = {
            "model": self._model,
            "instructions": req.system_prompt,
            "input": req.user_prompt,
            "max_output_tokens": req.max_tokens,
            "temperature": req.temperature,
            "reasoning": {"effort": "none"},  # 关闭思考模式，防止 CoT 吃光 token 导致正文为空
        }
        if req.json_schema:
            # strict json_schema 结构化输出：模型输出必合法 JSON。
            # text.format 需显式 type=json_schema + name + schema 三层结构。
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": req.json_schema["name"],
                    "schema": req.json_schema["schema"],
                }
            }

        response = client.responses.create(**params)

        # 截断/失败诊断
        if getattr(response, "status", None) == "incomplete":
            detail = getattr(response, "incomplete_details", None)
            reason = getattr(detail, "reason", None) if detail else None
            return TextGenerationResult(
                success=False,
                error=f"DeepSeek Responses 响应不完整 (incomplete_details.reason={reason})",
            )
        if getattr(response, "status", None) == "failed":
            err = getattr(response, "error", None)
            msg = getattr(err, "message", str(err)) if err else "未知错误"
            return TextGenerationResult(success=False, error=f"DeepSeek Responses 失败: {msg}")

        # 提取 message item 的 output_text
        text_parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", None) == "output_text":
                    text_parts.append(getattr(block, "text", "") or "")
        text = "".join(text_parts).strip()

        if not text:
            # json_schema 偶发空输出（官方已知），带诊断信息返回以便重试
            return TextGenerationResult(
                success=False,
                error=(
                    "DeepSeek Responses 返回空文本"
                    f"（status={getattr(response, 'status', None)}，"
                    f"output items={len(getattr(response, 'output', []) or [])}）"
                ),
            )
        return TextGenerationResult(success=True, text=text)

    # ------------------------------------------------------------------
    # Chat Completions（v4-pro / reasoner 回退）
    # ------------------------------------------------------------------
    def _generate_chat(self, client, req: TextGenerationRequest) -> TextGenerationResult:
        """走 Chat Completions（v4-pro / reasoner 老模型回退）."""
        api_params = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": req.user_prompt},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }

        # JSON Output：仅普通 chat 模型启用；reasoner 老模型不支持
        if "reasoner" not in self._model.lower():
            api_params["response_format"] = {"type": "json_object"}

        # 推理模型支持 reasoning_effort
        if "reasoner" in self._model.lower():
            api_params["reasoning_effort"] = config.DEEPSEEK_REASONING_EFFORT

        print(f"[INFO] 正在调用 DeepSeek ({self._model}) 生成分镜脚本...")

        response = client.chat.completions.create(**api_params)
        message = response.choices[0].message

        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            print(f"[DEBUG] DeepSeek 推理过程（前500字）: {reasoning[:500]}")

        text = message.content
        # OpenAI 兼容接口偶发把 content 返回为「分片数组」而非字符串，这里归一化
        if isinstance(text, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            ).strip()

        if not text:
            # 偶发把正文塞进 reasoning_content，且是 JSON 时回退
            reasoning_stripped = (reasoning or "").lstrip()
            reasoning_unfenced = re.sub(r"^```(?:json)?\s*", "", reasoning_stripped)
            if reasoning_unfenced.startswith(("{", "[")):
                print(f"[WARN] message.content 为空，回退使用 reasoning_content（长度 {len(reasoning)}）")
                text = reasoning
            else:
                finish = getattr(response.choices[0], "finish_reason", None)
                return TextGenerationResult(
                    success=False,
                    error=(
                        "DeepSeek 返回文本为空"
                        f"（finish_reason={finish}，reasoning 长度 {len(reasoning or '')}，"
                        f"前缀 {reasoning[:80] if reasoning else '无'}）"
                    ),
                )
        return TextGenerationResult(success=True, text=text)
