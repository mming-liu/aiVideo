"""DeepSeek 文本生成 — Responses API 调用.

优先走官方 Responses API（POST /responses）：
  - deepseek-v4-flash / deepseek-v4-pro 均支持（2026-08-12 V4-Pro-0813 起）
  - reasoning.effort 控制思考模式开关与强度（none 关闭；low/medium/high/max 递增；
    不传默认开启=high），effort 由请求 reasoning_effort 或 config.DEEPSEEK_REASONING_EFFORT 决定
  - text.format.json_schema 结构化输出（strict），分镜 JSON 天然合法
  - 输出独立 message item，思维链是独立的 reasoning item，不在正文里
  - 注意：思考模式下 temperature/top_p 不生效（API 静默忽略，不报错）；
    max_output_tokens 包含思维链 token，思考强度越高越容易截断（incomplete/max_output_tokens）

非 v4 系列模型（如 deepseek-reasoner）回退到 Chat Completions。
"""

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService

import re


class DeepSeekTextService(TextGenerationService):
    """DeepSeek (Responses API) 文本生成实现.

    支持:
    - deepseek-v4-flash / deepseek-v4-pro: Responses API（json_schema 结构化输出 + 思考模式）
    - deepseek-reasoner 等老模型: 回退 Chat Completions
    """

    def __init__(self) -> None:
        config.validate()
        self._model = config.DEEPSEEK_MODEL

    def _uses_responses(self) -> bool:
        """v4 系列（flash/pro）走 Responses API；老 reasoner 模型回退 chat."""
        m = self._model.lower()
        return "flash" in m or "pro" in m

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

            if self._uses_responses():
                return self._generate_responses(client, req)
            return self._generate_chat(client, req)
        except Exception as e:
            return TextGenerationResult(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Responses API（deepseek-v4-flash / deepseek-v4-pro）
    # ------------------------------------------------------------------
    def _generate_responses(self, client, req: TextGenerationRequest) -> TextGenerationResult:
        """走 Responses API：思考模式 + json_schema 结构化输出."""
        model = req.model or self._model
        effort = req.reasoning_effort or config.DEEPSEEK_REASONING_EFFORT
        print(f"[INFO] 正在调用 DeepSeek Responses ({model}, effort={effort}) 生成分镜脚本...")

        # v6.37：结构化多轮输入（user_inputs）优先于 user_prompt 字符串
        _input_value = req.user_inputs if req.user_inputs else req.user_prompt
        # max_output_tokens 优先级：req.max_tokens → config.DEEPSEEK_MAX_TOKENS → 不传
        # 思考模式下 max_output_tokens 包含思维链 token，留出余量是分镜脚本不退化的关键
        _effective_max_tokens = req.max_tokens or config.DEEPSEEK_MAX_TOKENS
        params = {
            "model": model,
            "instructions": req.system_prompt,
            "input": _input_value,
            "temperature": req.temperature,
            "top_p": req.top_p,                # v6.41+ 稳定化：核采样收窄到 0.2
            # 注意：DeepSeek Responses API 不支持 seed 参数（仅 Chat Completions 支持），
            # 故 seed 只在 _generate_chat 传。
            # 思考模式开关/强度（none 关闭；思考模式下 temperature 被 API 忽略）
            "reasoning": {"effort": effort},
        }
        if _effective_max_tokens:
            params["max_output_tokens"] = _effective_max_tokens
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

        usage = _usage_dict(response)

        if not text:
            # json_schema 偶发空输出（官方已知），带诊断信息返回以便重试
            return TextGenerationResult(
                success=False,
                error=(
                    "DeepSeek Responses 返回空文本"
                    f"（status={getattr(response, 'status', None)}，"
                    f"output items={len(getattr(response, 'output', []) or [])}）"
                ),
                usage=usage,
            )
        return TextGenerationResult(success=True, text=text, usage=usage)

    # ------------------------------------------------------------------
    # Chat Completions（reasoner 老模型回退）
    # ------------------------------------------------------------------
    def _generate_chat(self, client, req: TextGenerationRequest) -> TextGenerationResult:
        """走 Chat Completions（reasoner 老模型回退）."""
        model = req.model or self._model
        # v6.37：结构化多轮输入（user_inputs）优先于 user_prompt 字符串
        if req.user_inputs:
            messages = [{"role": "system", "content": req.system_prompt}]
            messages.extend(req.user_inputs)
        else:
            messages = [
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": req.user_prompt},
            ]
        api_params = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "top_p": req.top_p,                # v6.41+ 稳定化：核采样收窄到 0.2
            "seed": req.seed,                  # v6.41+ 稳定化：固定 seed（Chat Completions 支持）
        }

        # JSON Output：仅普通 chat 模型启用；reasoner 老模型不支持
        if "reasoner" not in model.lower():
            api_params["response_format"] = {"type": "json_object"}

        # 推理模型支持 reasoning_effort
        effort = req.reasoning_effort or config.DEEPSEEK_REASONING_EFFORT
        if "reasoner" in model.lower():
            api_params["reasoning_effort"] = effort

        print(f"[INFO] 正在调用 DeepSeek ({model}) 生成分镜脚本...")

        response = client.chat.completions.create(**api_params)
        if not getattr(response, "choices", None):
            # #17 修复 2026-08-14：choices 为空时越界保护（用 usage/错误信息定位）
            return TextGenerationResult(
                success=False,
                error=f"DeepSeek 返回无 choices: {str(response)[:300]}",
                usage=_usage_dict(response),
            )
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

        usage = _usage_dict(response)

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
                    usage=usage,
                )
        return TextGenerationResult(success=True, text=text, usage=usage)


def _usage_dict(response) -> dict | None:
    """从 Responses / Chat 响应提取 token 用量统计（供 bench 分析思维链开销）."""
    usage = getattr(response, "usage", None)
    if not usage:
        return None
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "reasoning_tokens": getattr(usage, "output_tokens_details", None) and getattr(
            usage.output_tokens_details, "reasoning_tokens", None
        ),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
