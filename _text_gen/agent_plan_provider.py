"""火山方舟 Agent Plan (/api/plan/v3) 文本生成 — doubao-seed-2-1-turbo 默认.

与标准方舟（/api/v3）的区别：
  - 端点：POST {base}/chat/completions（OpenAI 兼容 messages 数组）
  - 鉴权：Bearer AGENT_PLAN_API_KEY（**专属 key，不通用**——普通 ARK key 报 401）
  - 模型：doubao-seed-2-1-turbo（默认；可按 .env AGENT_PLAN_TEXT_MODEL 覆盖）
  - 切换：TEXT_PROVIDER=agent_plan（.env.content）；deepseek 等其他路径不受影响

设计要点（2026-09-03 接入，2026-09-03 v2 修正）：
  - **火山方舟 Doubao 专用字段命名**（与 OpenAI 兼容层不完全一致）：
      - 输出 token 上限：max_completion_tokens（不是 OpenAI 的 max_tokens）
      - 思考模式：thinking: {type: "disabled"|"enabled"}（不是 OpenAI 的 reasoning_effort）
      - 结构化输出：response_format.type="json_object"（方舟 Doubao 稳定；json_schema strict 视模型而定）
  - 思考模式默认 disabled：seed-2-1-turbo 默认开启深度思考会大量消耗思维链 token，
    拉长长 JSON 任务（分镜/修复轮）的推理耗时到 180s+ 触发客户端超时。
  - max_completion_tokens **单一来源 .env.content**（AGENT_PLAN_TEXT_MAX_TOKENS）：
    服务端默认仅 4096 不够分镜生成；provider 内不再留兜底常量
    （v6.62.5 删——同一数字散三处的反模式）。沿革与阈值依据见 _build_payload 内注释。
  - **关闭自动重试**：长任务（>60s）的客户端超时不应立刻重发——旧请求仍在模型侧跑，
    重发只增加额度浪费和排队时间。改报"超时未完成"由上层业务队列决策。
  - 5xx/网络异常仍走 1 次退避（防瞬时网络抖）；401/403/400 不重试直接报错。
  - 响应解析：choices[0].message.content；usage 含 reasoning_tokens（思考模式下）供 bench。

错误文案与 _image_gen/agent_plan_provider.py / _video_gen/agent_plan_provider.py 对齐：
  - 401 → 「Agent Plan 鉴权失败 [401]」+ 提示申请专属 key
  - 403 → 「Agent Plan 套餐无权限或燃料耗尽 [403]」+ 提示查 fuel 余额

官方文档：火山方舟 Agent Plan / 文本生成
"""
from __future__ import annotations

import os
import re
import time
from functools import partial
from typing import Optional

import requests

from . import retry_policy
from .config import config
from .models import TextGenerationRequest, TextGenerationResult, truncated_result
from .service import TextGenerationService
from shared.key_scheduler import KeyScheduler
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step01.log")

# 客户端读超时（秒）。默认 600：doubao 长任务（18 镜 TOPK + 深度 prompt）
# 实测可超 300s，旧默认值会让 GAP 重试 100% 超时失败（2026-09-11 实机复盘）。
# 可用 env AGENT_PLAN_TEXT_TIMEOUT_SEC 覆盖；模型能力项不进配置文件，仅操作参数进 env。
AGENT_PLAN_TEXT_TIMEOUT_SEC = int(os.getenv("AGENT_PLAN_TEXT_TIMEOUT_SEC", "600"))


class AgentPlanTextService(TextGenerationService):
    """火山方舟 Agent Plan 文本生成服务.

    支持:
      - 基础 chat completions（system + user / 多轮 user_inputs）
      - 温度/top_p/seed/max_tokens 透传
      - response_format json_schema 结构化输出（OpenAI 兼容 strict）
      - reasoning_effort 思考模式档位
    """

    def __init__(self) -> None:
        config.validate()
        if not config.AGENT_PLAN_API_KEY:
            raise ValueError(
                "没有配置 AGENT_PLAN_API_KEY（.env.keys 中设置，Agent Plan 专属 key，"
                "与 VOLCENGINE_API_KEY 不通用）"
            )
        self._model = config.AGENT_PLAN_TEXT_MODEL
        self._base_url = config.AGENT_PLAN_BASE_URL.rstrip("/")
        # 单 key 限速：cooldown = 60 / RPM；默认 10 RPM = 6s
        rpm = max(int(config.AGENT_PLAN_TEXT_RPM), 1)
        self._key_scheduler = KeyScheduler(
            n_keys=1, cooldown=60.0 / rpm, name="agent_plan_text"
        )

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {config.AGENT_PLAN_API_KEY}",
            "Content-Type": "application/json",
        }

    def _build_messages(self, req: TextGenerationRequest) -> list[dict]:
        """组装 OpenAI 兼容 messages 数组."""
        messages: list[dict] = [{"role": "system", "content": req.system_prompt}]
        if req.user_inputs:
            # v6.37：多轮 user/assistant 拼接在 system 之后
            messages.extend(req.user_inputs)
        else:
            messages.append({"role": "user", "content": req.user_prompt})
        return messages

    def _build_payload(self, req: TextGenerationRequest) -> dict:
        """组装 chat completions 请求 body（火山方舟 Doubao 专用字段命名）.

        关键修正（v2，2026-09-03 现场压测后）：
          - max_completion_tokens（不是 OpenAI 的 max_tokens）— 服务端默认 4096 不够分镜生成
          - thinking: {type: "disabled"}（不是 OpenAI 的 reasoning_effort）— 默认开启深度思考
            会拉长推理耗时到 180s+ 触发客户端超时；分镜/修复轮结构化任务关掉思考收益最大
        """
        model = req.model or self._model
        # thinking.type 映射：req.reasoning_effort / config 都用 none→disabled / 其他→enabled
        # （保持与 deepseek 路径的 reasoning_effort 字符串契约，转换在 provider 内做）
        effort = (
            req.reasoning_effort
            if req.reasoning_effort is not None
            else config.AGENT_PLAN_TEXT_REASONING_EFFORT
        )
        thinking_type = "disabled" if (not effort or effort == "none") else "enabled"
        # max_completion_tokens 单一事实源：**只在 .env.content 配**
        # （AGENT_PLAN_TEXT_MAX_TOKENS，当前 32768）。req.max_tokens 可按调用点覆盖。
        #
        # 沿革：服务端默认 4096 不够分镜生成（09-03）→ 65536 撞 3-5 分钟长尾（09-04）
        # → 降 16384 → **16384 低于全脚本实际需求**（09-08 v6.62.5）：实测 11 镜全脚本
        # JSON ≈66k 字符 ≈26k tokens，4 个输出全脚本的站点必然 finish_reason=length，
        # 且旧代码把截断当 success 静默下发 → 表现为「多次 JSON 解析失败」。
        # （09-04 那版注释自称「实际只需 20-40k，留 16384 足够避免截断」自相矛盾。）
        #
        # v6.62.5 同时删掉了这里的 `or 16384` 兜底常量：同一个数字散在 .env + provider
        # + 测试三处，改一次动三个文件，正是 CLAUDE.md「限制写死在配置里就该迁走，
        # 别继续堆配置」的反模式。该值本质是**延迟 vs 输出长度的权衡参数**（非模型硬
        # 能力），按项目约定归 .env。env 缺失（0/空）→ 不传 → 服务端默认 4096 → 必然
        # 截断，但截断现在硬失败且打印 cap，配错立刻可见，无需代码再藏一个魔数兜底。
        max_completion_tokens = req.max_tokens or config.AGENT_PLAN_TEXT_MAX_TOKENS

        payload: dict = {
            "model": model,
            "messages": self._build_messages(req),
            "temperature": req.temperature,
            "top_p": req.top_p,
            # OpenAI 兼容 chat 支持 seed（Agent Plan / Doubao 系列行为对齐）
            "seed": req.seed,
            # 火山方舟 Doubao 专用：输出 token 上限（不是 OpenAI 的 max_tokens）
            "max_completion_tokens": max_completion_tokens,
            # 火山方舟 Doubao 专用：思考模式开关（不是 OpenAI 的 reasoning_effort）
            "thinking": {"type": thinking_type},
        }
        # 结构化输出：方舟 Doubao 稳定支持 json_object；json_schema strict 视模型而定，
        # 当前 doubao-seed-2-1-turbo 走 json_object 强约束 + 解析端 schema 二次校验
        if req.json_schema or req.user_prompt:
            # 默认开启 json_object（生成分镜 JSON 强约束），解析端 _validate_script 兜底
            payload["response_format"] = {"type": "json_object"}
        return payload

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        """提交一次文本生成请求（同步阻塞）.

        重试策略（v2，2026-09-03 现场压测后修正）：
          - 长任务客户端超时（read timeout）：**不重试**——重发会让旧请求仍在模型侧跑，
            增加额度浪费和排队。改报「超时未完成」由上层业务队列决策。
          - 5xx/网络异常：1 次退避（防瞬时抖）；用尽后报错。
          - 401/403/400：不重试直接报错（鉴权/参数问题重试无意义）。
        """
        self._key_scheduler.acquire(timeout=180)

        payload = self._build_payload(req)
        model = payload.get("model", self._model)
        url = f"{self._base_url}/chat/completions"

        _verbose_write(
            f"[INFO] 正在调用 Agent Plan ({model}) 生成分镜脚本... "
            f"max_completion_tokens={payload.get('max_completion_tokens')}, "
            f"thinking.type={payload.get('thinking', {}).get('type')}"
        )
        _verbose_write(
            f"[DEBUG] agent_plan_text_submit: model={model}, "
            f"messages={len(payload['messages'])}, "
            f"temperature={payload['temperature']}, top_p={payload['top_p']}, "
            f"seed={payload.get('seed')}, "
            f"response_format={payload.get('response_format')}"
        )

        # 重试判定/次数/退避/时间预算统一走 `_text_gen.retry_policy`（N6，2026-09-28）：
        # provider 不再自带常量与 `except` 分支里的判定——否则「哪些错可重试」会随
        # provider 漂移，上层也无从判断该不该再叠一层重试。
        # 语义要点：**读超时不重试**（旧请求仍在模型侧跑，重发 = 双份长任务）。
        # v6.70.3（09-14）：超时随请求走（req.timeout_sec 分级），None 回退全局
        _timeout = int(req.timeout_sec) if req.timeout_sec else AGENT_PLAN_TEXT_TIMEOUT_SEC
        _attempts = retry_policy.submit_attempts()
        _t0 = time.time()
        for attempt in range(1, 1 + _attempts):
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers,
                    timeout=_timeout,
                )
            except requests.exceptions.RequestException as e:
                _fc = retry_policy.classify_exception(e)
                if _fc is retry_policy.FailureClass.TRANSPORT_TIMEOUT:
                    _verbose_write(
                        f"[!] 客户端 {_timeout}s 超时（长任务），"
                        f"不重试，改报「超时未完成」由上层业务决策"
                    )
                    return TextGenerationResult(
                        success=False,
                        error=(
                            f"Agent Plan 客户端超时 ({_timeout}s) — "
                            f"长任务不应自动重试，旧请求仍在模型侧跑；"
                            f"请上层业务队列决定是否重发。"
                            f"（model={model}，可用 AGENT_PLAN_TEXT_TIMEOUT_SEC 调整或请求级 timeout_sec）"
                        ),
                    )
                _elapsed = time.time() - _t0
                # 类别可重试 **且** 次数与时间预算都有余量 → 由本层退避重发；
                # 否则把分类带回上层（上层**不得**再对传输类叠加重试）。
                if not retry_policy.should_retry(_fc, attempts_used=attempt,
                                                 elapsed_sec=_elapsed):
                    return TextGenerationResult(
                        success=False,
                        error=(
                            f"Agent Plan 提交失败[{_fc.value}]: {e}"
                            f"（尝试 {attempt}/{retry_policy.submit_attempts(_fc)}，"
                            f"已用 {_elapsed:.1f}s；"
                            f"重试预算 {retry_policy.submit_budget_sec():g}s）"
                        ),
                    )
                _wait = retry_policy.submit_backoff_sec(attempt)
                _verbose_write(
                    f"[!] 提交失败[{_fc.value}]: {e}，"
                    f"{_wait:g}s 后重试 (尝试 {attempt}/"
                    f"{retry_policy.submit_attempts(_fc)})"
                )
                time.sleep(_wait)
                continue

            _verbose_write(
                f"[DEBUG] agent_plan_text_submit response: status={resp.status_code}, "
                f"body={resp.text[:500]}"
            )

            if resp.status_code == 200:
                return self._parse_response(
                    resp, model, payload.get("max_completion_tokens"),
                    allow_truncated=req.allow_truncated,
                )

            if resp.status_code == 429:
                # 限流：1 次退避后重试
                if attempt == 2:
                    return self._error_from_response(resp, "")
                wait = 30.0
                m = re.search(r"retry[-_]after[\"':=\s]+(\d+)", resp.text, re.IGNORECASE)
                if m:
                    wait = float(m.group(1))
                _verbose_write(f"[!] 429 限速，等待 {wait:.0f}s 后重试 (尝试 {attempt}/2)")
                time.sleep(wait)
                continue

            if 500 <= resp.status_code < 600:
                if attempt == 2:
                    return self._error_from_response(resp, "")
                last_error = f"服务端 {resp.status_code}: {resp.text[:200]}"
                _verbose_write(
                    f"[!] 服务端 {resp.status_code}，2s 后重试 1 次 (尝试 {attempt}/2)"
                )
                time.sleep(2.0)
                continue

            # 客户端错误（401/403/400/404）不重试
            break

        # 走到这里说明重试用尽 / 客户端错误
        if "resp" in locals():
            return self._error_from_response(resp, "")
        return TextGenerationResult(
            success=False, error="Agent Plan 提交失败: 未知原因"
        )

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------
    def _parse_response(
        self, resp: requests.Response, model: str, cap: Optional[int] = None,
        allow_truncated: bool = False,
    ) -> TextGenerationResult:
        """从 200 响应中提取文本 + usage.

        cap: 本次请求的 max_completion_tokens，仅用于截断错误文案（v6.62.5）。
        allow_truncated: 见 TextGenerationRequest.allow_truncated（v6.62.6）。
        """
        try:
            data = resp.json()
        except Exception as e:
            return TextGenerationResult(
                success=False, error=f"响应 JSON 解析失败: {e} ({resp.text[:200]})"
            )

        choices = data.get("choices") or []
        if not choices:
            return TextGenerationResult(
                success=False,
                error=f"响应中无 choices: {str(data)[:300]}",
            )
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        finish = choices[0].get("finish_reason")
        usage = _usage_dict(data)

        # v6.62.5（09-08）可观测性：旧实现只打 body 前 500 字符（日志里每行恒定 580
        # 字符），finish_reason 与 usage 都在被截掉的尾部——这正是「截断导致多次 JSON
        # 解析失败」跨多次跑批未被诊断的原因（grep completion_tokens 命中的 21 次全是
        # max_completion_tokens 的子串误匹配，usage 一次都没记）。
        # 放在这里而不是 submit 处：data/finish/usage 已解析好，无需二次 resp.json()。
        _verbose_write(
            f"[DEBUG] agent_plan_text finish_reason={finish}, "
            f"completion_tokens={(usage or {}).get('output_tokens')}, "
            f"max_completion_tokens={cap}"
            + ("  <<< 输出被截断" if finish == "length" else "")
        )

        if not text:
            # v6.62.6 契约：**截断必须经 `truncated_result` 统一落法**（四个 provider
            # 同一份）——本分支此前把「空正文」一律当普通失败，于是
            # 「思维链吃光配额、正文一个字没吐」被记成 `request_failed`，上层
            # 按「不可重试」处置并直接终局（2026-09-28 审计实测 BLOCK）。
            # 截断的正确归类是 `truncated=True`：换一次采样就可能不再截断。
            if finish == "length":
                return truncated_result(
                    text="",
                    usage=usage,
                    allow_truncated=allow_truncated,
                    error=(
                        f"Agent Plan 输出被截断且正文为空（finish_reason=length, "
                        f"model={model}, max_completion_tokens={cap}）"
                    ),
                )
            return TextGenerationResult(
                success=False,
                usage=usage,
                error=(
                    f"Agent Plan 返回文本为空（model={model}, "
                    f"finish_reason={finish}, full={str(data)[:200]}）"
                ),
            )

        # v6.62.5（09-08）截断不再静默成功 —— 这是「多次 JSON 解析失败」的根因。
        # 旧实现只在 text **为空** 时才看 finish_reason；非空但被截断的半个 JSON
        # 被当作 success=True 交给下游，于是失败以「无法解析 JSON」的形态出现在
        # 很远的调用点，还把不可能完成的活（补全半个 JSON）派给
        # shared/utils/json_repair._truncate_fix。
        # 09-08 实测：单次跑批 20 次调用中 3 次 finish_reason=length，
        # 与 3 次「JSON 解析失败」**1:1 对应**——0 次非截断导致的解析失败。
        # 用户决策（09-08）：截断硬失败，不做自动升档重试。
        if finish == "length":
            # 注意 key：_usage_dict 把 completion_tokens 重命名为 output_tokens
            _completion = (usage or {}).get("output_tokens")
            # v6.62.6（09-09）：硬失败只对**不能容忍截断**的调用点（全脚本生成）。
            # 列表型提取显式 allow_truncated=True → 放行给 _truncate_fix 抢救完整
            # 列表项；v6.62.5 无条件硬失败把这条真实抢救路径也掐了（截断 → 上层
            # return [] → 物体库零增量），属于过度收敛。
            # 分支落法收敛到 models.truncated_result（4 个 provider 同一份）。
            return truncated_result(
                text=text,
                usage=usage,
                allow_truncated=allow_truncated,
                error=(
                    f"Agent Plan 输出被截断（finish_reason=length, model={model}, "
                    f"max_completion_tokens={cap}, completion_tokens={_completion}）"
                    f"——输出 JSON 不完整，已整份丢弃（截断内容无法可靠修复）。"
                    f"如为全脚本生成，请调高 AGENT_PLAN_TEXT_MAX_TOKENS："
                    f"实测 11 镜全脚本约需 26k tokens。"
                ),
            )

        return TextGenerationResult(success=True, text=text, usage=usage)

    def _error_from_response(
        self, resp: requests.Response, last_error: str
    ) -> TextGenerationResult:
        """非 200 响应映射到错误文案（与 image/video provider 对齐）."""
        if resp.status_code == 401:
            return TextGenerationResult(
                success=False,
                error=(
                    f"Agent Plan 鉴权失败 [401]: 普通 ARK key 不通用，请到方舟控制台"
                    f"「Agent → 接入」申请 AGENT_PLAN_API_KEY 专属 key。"
                    f" 响应: {resp.text[:200]}"
                ),
            )
        if resp.status_code == 403:
            return TextGenerationResult(
                success=False,
                error=(
                    f"Agent Plan 套餐无权限或燃料耗尽 [403]: 当前套餐不支持"
                    f"该模型或 fuel 已用完；可查 {self._base_url}/fuel/query 燃料余额。"
                    f" 响应: {resp.text[:200]}"
                ),
            )
        if resp.status_code == 400:
            return TextGenerationResult(
                success=False, error=f"参数错误 [400]: {resp.text[:300]}"
            )
        return TextGenerationResult(
            success=False,
            error=f"Agent Plan API 错误 [{resp.status_code}]: {resp.text[:300]}"
            + (f"（最后错误: {last_error}）" if last_error else ""),
        )


def _usage_dict(data: dict) -> Optional[dict]:
    """从 chat completions 响应提取 token 用量统计（供 bench 分析思维链开销）.

    火山方舟 Doubao usage 结构（v2 实测）：
      {
        "prompt_tokens": 201,
        "completion_tokens": 88,
        "total_tokens": 289,
        "completion_tokens_details": {"reasoning_tokens": 1234}  # 思考模式开启时
      }
    """
    usage = data.get("usage")
    if not usage or not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
