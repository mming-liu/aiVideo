"""DeepSeek 文本生成 — Responses API 调用.

优先走官方 Responses API（POST /responses）：
  - deepseek-v4-flash / deepseek-v4-pro 均支持（2026-08-12 V4-Pro-0813 起）
  - reasoning.effort 控制思考模式开关与强度（none 关闭；low/medium/high/max 递增；
    不传默认开启=high），effort 由请求 reasoning_effort 或 config.DEEPSEEK_REASONING_EFFORT 决定
  - text.format.json_object 约束 JSON 语法；原 schema 显式指导输出，响应仍经本地严格准入
  - 输出独立 message item，思维链是独立的 reasoning item，不在正文里
  - 注意：思考模式下 temperature/top_p 不生效（API 静默忽略，不报错）；
    max_output_tokens 包含思维链 token，思考强度越高越容易截断（incomplete/max_output_tokens）

非 v4 系列模型（如 deepseek-reasoner）回退到 Chat Completions。
"""

from .config import config
from .deadline import TransportGate
from .models import TextGenerationRequest, TextGenerationResult, truncated_result
from .service import TextGenerationService

import re
import json


# v6.118（2026-09-25）：客户端重试次数**显式钉 0**。
# OpenAI SDK 默认 `max_retries=2`（实测 openai 2.44.0 / `_constants.DEFAULT_MAX_RETRIES`），
# 即 5xx / 429 / 连接错误时会**静默重发两次**——审计单次约 16.8k input tokens，
# 一次抖动最坏变三次提交。本通道按「长任务不自动重试、由上层决策」的既有取向
# （见 agent_plan_provider.generate 的同类说明）钉成单次提交。
DEEPSEEK_MAX_RETRIES = 0


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
                # ⚠️ `max_retries` 是**构造器**参数，不是请求级参数（实测 openai 2.44.0：
                # `responses.create` / `chat.completions.create` 的签名里都没有它，
                # 传了直接 `TypeError: unexpected keyword argument` → 审计 request_failed
                # → 提交点 BLOCK，2026-09-25 13:25 实跑就是这个形态）。
                # 与 `timeout` 相反——`timeout` 两者都收（请求级优先）。别把两者写一处。
                max_retries=DEEPSEEK_MAX_RETRIES,
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
        """走 Responses API：思考模式 + JSON 输出，schema 约束写入指令."""
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
            # 用户裁定采用官方 JSON 模式：语法与结构分账，不信任 API 的格式声明。
            # 原 schema 完整交给模型；现有本地解析、字段授权和终检继续严格执行。
            params["text"] = {"format": {"type": "json_object"}}
            params["instructions"] += (
                "\n\n只输出一个严格合法 JSON 对象；字段及值类型必须遵守以下原请求 schema，"
                "未授权字段和内容守卫保持不变：\n"
                + json.dumps(req.json_schema, ensure_ascii=False)
            )

        # v6.117：`timeout_sec` 是**供应商无关契约**（见 models.py），本 provider 此前
        # 完全没实现它——全文 `timeout` 零命中，而 OpenAI client 只用 api_key/base_url
        # 构造，于是请求吃 SDK 默认 `read=600s`：审计传的 `AUDIT_TIMEOUT_SEC=180` 在原生
        # 通道**静默失效**（2026-09-25 探针实测）。None = 不传 = 保持 SDK 默认（零行为变化）。
        # 工作包 §4 B：真上限 = min(请求偏好, 整次规划余量)；余量耗尽 ⇒ **不发请求**。
        # 本通道原本不传超时就吃 SDK 默认（read=600s），故 `default_timeout=None`：
        # **没带截止时刻的旧请求行为不变**（仍旧不传 timeout）。
        _gate = TransportGate(req, default_timeout=None, where="deepseek")
        _limit, _deny = _gate.limit()
        if _deny:
            return TextGenerationResult(success=False, error=_deny)
        # 上限交给**发送当刻**：`_t` 是工作线程重算后的余量（复核第 45 轮 P1-③：
        # 提前写进 params 的旧 timeout 会在线程延迟调度时继续被消费）
        def send(timeout):
            actual = dict(params)
            if timeout is not None:
                actual["timeout"] = float(timeout)
            if not config.DEEPSEEK_STREAM:
                return client.responses.create(**actual)
            actual["stream"] = True
            stream = client.responses.create(**actual)
            try:
                for event in stream:
                    if _gate.deadline.expired():
                        raise TimeoutError(_gate.expiry_reason())
                    event_type = getattr(event, "type", "")
                    if event_type in ("response.completed", "response.incomplete", "response.failed"):
                        final = getattr(event, "response", None)
                        expected = event_type.split(".", 1)[1]
                        if final is None or getattr(final, "status", None) != expected:
                            raise RuntimeError("DeepSeek stream terminal event/status mismatch")
                        return final
                raise RuntimeError("DeepSeek stream ended without a terminal response; partial output rejected")
            finally:
                stream.close()

        response, _timed_out, _why = _gate.request(send)
        if _why:
            return TextGenerationResult(success=False, error=_why)

        # 失败诊断（截断诊断挪到正文提取之后——allow_truncated=True 时要把已生成的
        # 前缀交出去，v6.62.6）
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

        # 截断诊断（v6.62.6，09-09）：status=incomplete 且 reason=max_output_tokens
        # 才是「写到上限被切」，走通用 truncated_result 处置；其它 incomplete 原因
        # （content_filter 等）与 allow_truncated 无关，一律硬失败。
        if getattr(response, "status", None) == "incomplete":
            detail = getattr(response, "incomplete_details", None)
            reason = getattr(detail, "reason", None) if detail else None
            err = f"DeepSeek Responses 响应不完整 (incomplete_details.reason={reason})"
            if reason == "max_output_tokens":
                return truncated_result(
                    text=text,
                    usage=usage,
                    allow_truncated=req.allow_truncated,
                    finish_reason=str(reason or "max_output_tokens"),
                    error=(
                        f"{err}——输出 JSON 不完整，已整份丢弃。"
                        f"如为全脚本生成请调高 DEEPSEEK_MAX_TOKENS"
                        f"（当前 max_output_tokens={params.get('max_output_tokens')}，"
                        f"思考模式下该上限包含思维链 token）。"
                    ),
                )
            return TextGenerationResult(success=False, error=err, usage=usage)

        if not text:
            # 格式请求仍可能空输出；保留诊断并交调用方决定后续动作
            return TextGenerationResult(
                success=False,
                error=(
                    "DeepSeek Responses 返回空文本"
                    f"（status={getattr(response, 'status', None)}，"
                    f"output items={len(getattr(response, 'output', []) or [])}）"
                ),
                usage=usage,
            )
        # Responses API 没有 `finish_reason`：用 `status`（completed 等）当留档口径
        return TextGenerationResult(success=True, text=text, usage=usage,
                                    finish_reason=str(getattr(response, "status", "") or ""))

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

        # v6.117：同上——chat 回退路径同样必须执行请求级超时契约
        # 工作包 §4 B：同 Responses 路径，真上限 = min(偏好, 余量)，耗尽则不发
        _gate = TransportGate(req, default_timeout=None, where="deepseek")
        _limit, _deny = _gate.limit()
        if _deny:
            return TextGenerationResult(success=False, error=_deny)
        print(f"[INFO] 正在调用 DeepSeek ({model}) 生成分镜脚本...")

        # 同上：chat 回退路径也用**发送当刻**算出的上限
        response, _timed_out, _why = _gate.request(
            lambda _t: client.chat.completions.create(**api_params) if _t is None
            else client.chat.completions.create(**{**api_params, "timeout": float(_t)}))
        if _why:
            return TextGenerationResult(success=False, error=_why)
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
        # 截断诊断（v6.62.6，09-09）：正文非空但 finish_reason=length 说明写到上限
        # 被切。旧实现只在**正文为空**时才看 finish_reason，非空的半个 JSON 照样
        # success=True 下发——与 v6.62.5 在 agent_plan 修掉的是同一个缺陷。
        if getattr(response.choices[0], "finish_reason", None) == "length":
            return truncated_result(
                text=text,
                usage=usage,
                allow_truncated=req.allow_truncated,
                finish_reason=str(getattr(response.choices[0], "finish_reason", "") or "length"),
                error=(
                    f"DeepSeek 输出被截断（finish_reason=length, model={model}, "
                    f"max_tokens={req.max_tokens}）——输出 JSON 不完整，已整份丢弃。"
                ),
            )
        return TextGenerationResult(
            success=True, text=text, usage=usage,
            finish_reason=str(getattr(response.choices[0], "finish_reason", "") or ""))


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
