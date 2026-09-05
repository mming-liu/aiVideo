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
  - max_completion_tokens 默认 16384：服务端默认仅 4096，对分镜生成不够；
    显式给到 16384 避免 finish_reason=length 截断且不至于无界
    （2026-09-04 现场压测：65536 fallback 导致 6+ 分钟长尾，已降为 16384）。
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

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService
from shared.key_scheduler import KeyScheduler
from shared.constants import AGENT_PLAN_TEXT_ATTEMPTS
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step01.log")


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
        # max_completion_tokens 优先级：req.max_tokens → config.AGENT_PLAN_TEXT_MAX_TOKENS → 16384
        # 2026-09-03 现场压测：服务端默认 4096 不够分镜生成
        # 2026-09-04 现场压测：65536 fallback 导致 3-5 分钟长尾（70k input + 65536 output 容量
        # 下模型倾向于"慢慢想"），已降为 16384（分镜 JSON 实际只需 20-40k，留 16384 足够避免
        # finish_reason=length 截断）。config.AGENT_PLAN_TEXT_MAX_TOKENS 默认 None（0=不传）
        # → 走下面的 16384 fallback。
        max_completion_tokens = (
            req.max_tokens
            or config.AGENT_PLAN_TEXT_MAX_TOKENS
            or 16384
        )

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

        # 仅做 1 次退避：超时和 5xx 各 1 次，避免长任务重试浪费额度
        for attempt in range(1, 1 + AGENT_PLAN_TEXT_ATTEMPTS):  # 1 次重试（初始 + 1 次 = 共 2 次提交）
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers, timeout=300  # 5 分钟客户端超时
                )
            except requests.exceptions.Timeout:
                # 长任务客户端超时：直接报错不重试（旧请求仍在模型侧跑）
                if attempt == 1:
                    _verbose_write(
                        f"[!] 客户端 300s 超时（长任务），不重试，"
                        f"改报「超时未完成」由上层业务决策"
                    )
                return TextGenerationResult(
                    success=False,
                    error=(
                        f"Agent Plan 客户端超时 (300s) — 长任务不应自动重试，"
                        f"旧请求仍在模型侧跑；请上层业务队列决定是否重发。"
                        f"（model={model}）"
                    ),
                )
            except requests.exceptions.RequestException as e:
                # 非超时网络异常：1 次退避后重试
                if attempt == 2:
                    return TextGenerationResult(
                        success=False,
                        error=f"Agent Plan 提交网络异常: {e}（重试 1 次后仍失败）",
                    )
                _verbose_write(f"[!] 提交网络异常: {e}，2s 后重试 (尝试 {attempt}/2)")
                time.sleep(2.0)
                continue

            _verbose_write(
                f"[DEBUG] agent_plan_text_submit response: status={resp.status_code}, "
                f"body={resp.text[:500]}"
            )

            if resp.status_code == 200:
                return self._parse_response(resp, model)

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
    def _parse_response(self, resp: requests.Response, model: str) -> TextGenerationResult:
        """从 200 响应中提取文本 + usage."""
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
        if not text:
            finish = choices[0].get("finish_reason")
            return TextGenerationResult(
                success=False,
                error=(
                    f"Agent Plan 返回文本为空（model={model}, "
                    f"finish_reason={finish}, full={str(data)[:200]}）"
                ),
            )

        usage = _usage_dict(data)
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
