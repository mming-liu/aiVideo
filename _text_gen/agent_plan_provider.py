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

import hashlib
import json
import os
import re
import time
import uuid
from functools import partial
from typing import Optional

import requests

from . import retry_policy
from .deadline import TransportGate
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
#: 排队（Key 冷却）等待上限（#23 修复：全 key 长冷却时不许无限占线程池）；
#: 有截止时刻时再与余量取 min（工作包 §4 B）
AGENT_PLAN_QUEUE_CAP_SEC = 180.0

#: 未显式配置 `AGENT_PLAN_SESSION_ID` 时发出去的**进程内稳定**会话 id
#: （opencode go 网关要求 `x-opencode-session`；文档不限格式，只要求"每次会话稳定"）。
_AUTO_SESSION_ID = "aivideo-%s" % uuid.uuid4().hex[:12]


#: 文本通道的**请求方言**——由**解析后的端点**决定。切换通道时必须同时改这里：
#: 端点即契约，方舟的 Doubao 字段名（`thinking.type`）在 OpenAI 兼容网关上没有意义。
#:
#: - `openai`：OpenAI 兼容网关（`AGENT_PLAN_TEXT_BASE_URL`；2026-10-02 起指向 OpenCode Go）。
#:   思考强度走 `reasoning_effort`——官方模型目录登记
#:   `[[reasoning_options]] type = "effort" / values = ["low", "high", "max"]`
#:   （2026-10-03 核对 https://raw.githubusercontent.com/anomalyco/models.dev/dev/providers/opencode-go/models/deepseek-v4.1-flash.toml）。
#: - `doubao`：火山方舟 plan v3（`AGENT_PLAN_BASE_URL`）。强度只有开关（`thinking.type`），
#:   没有档位；沿用既有映射（`none`→disabled，其余→enabled）。
#:
#: **背景（复核 2026-10-03）**：通道切到 OpenCode Go 后请求语义没有跟着适配，`low`/`high`/`max`
#: 三个档位被映射成同一份 `thinking.type=enabled` ⇒ **档位配置实际没生效**（离线实测三者
#: 请求体 SHA-256 相同）。审计的 299s 墙钟超时是否由此引起**尚未证明**，本改动只负责让
#: "要求发出去的强度"与"实际发出去的强度"一致。
_OPENAI_DIALECT_HOSTS = ("opencode.ai",)
#: OpenCode Go 目录登记的档位（发别的一律不发该字段，见 `_openai_effort_tier`）
_OPENAI_EFFORT_TIERS = ("low", "high", "max")
#: 未登记档位的**降档**映射（`minimal` 是 deepseek 侧的名字，该端点没有）
_OPENAI_EFFORT_ALIASES = {"minimal": "low"}


def _api_dialect(base_url: str) -> str:
    """按**端点**取请求方言（唯一判定处）."""
    _u = str(base_url or "").lower()
    return "openai" if any(_h in _u for _h in _OPENAI_DIALECT_HOSTS) else "doubao"


def sent_structured_output(payload: dict) -> str:
    """**实发的**结构化输出格式（脱敏：只报类型与 schema 名）.

    D（复核 2026-10-06 第八轮）：「请求被接受 / schema 被遵守 / 候选通过验收」必须
    **分别记账**。这条给出中间那一项的事实依据——本通道恒发 `json_object`，
    调用方给的 `json_schema` **不下发**，所以**不得**据此声称响应受 strict 约束。
    """
    _rf = (payload or {}).get("response_format") or {}
    _t = str(_rf.get("type") or "（未发）")
    if _t == "json_schema":
        _n = str((_rf.get("json_schema") or {}).get("name") or "")
        return "json_schema:%s" % _n
    return _t


def _resolve_effort(req) -> str:
    """请求级 `reasoning_effort` → 通道级默认（**单一事实源**，`_build_payload` 与
    `generate` 的阶段轨迹共用同一份解析，不各写一遍）."""
    _e = getattr(req, "reasoning_effort", None)
    return _e if _e is not None else config.AGENT_PLAN_TEXT_REASONING_EFFORT


def _openai_effort_tier(effort) -> str:
    """内部 effort 字符串 → 该端点的档位；返回 "" = **不发**该字段（走网关默认）.

    - `none`/空：OpenCode Go 目录只登记 low/high/max，**没有"关闭"档**；发一个目录里
      不存在的值有被网关拒收的风险，宁可不发（不发的语义就是网关默认，与今天一致）。
    - 未登记的档位（如 `minimal`）：取同侧最低档 `low`——静默发一个非法值比降一档风险大。
    - 其余未知值：不发，并由调用方**显式告警**（不静默吞掉）。
    """
    _e = str(effort or "").strip().lower()
    if not _e or _e == "none":
        return ""
    if _e in _OPENAI_EFFORT_TIERS:
        return _e
    return _OPENAI_EFFORT_ALIASES.get(_e, "")


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
        # **文本专用**端点（留空 ⇒ 回落共享的 `AGENT_PLAN_BASE_URL`）：图片通道与文本通道
        # 可以各自指向不同网关（2026-10-02 起方舟 / opencode go 并存）。回落**在读的时候**
        # 再判一次——"留空 = 用共享端点"这条语义在哪儿读都成立。
        self._base_url = (config.AGENT_PLAN_TEXT_BASE_URL
                          or config.AGENT_PLAN_BASE_URL).rstrip("/")
        # 单 key 限速：cooldown = 60 / RPM；默认 10 RPM = 6s
        rpm = max(int(config.AGENT_PLAN_TEXT_RPM), 1)
        self._key_scheduler = KeyScheduler(
            n_keys=1, cooldown=60.0 / rpm, name="agent_plan_text"
        )

    @property
    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {config.AGENT_PLAN_API_KEY}",
            "Content-Type": "application/json",
        }
        # 网关路由头 `x-opencode-session`：opencode go 网关**要求**它（缺失直接
        # `400 MissingSessionID`，2026-10-02 实测），火山方舟不需要。
        # 取值优先级：`AGENT_PLAN_SESSION_ID`（显式配置）→ **进程内稳定 id**。
        # 官方文档只说"每次会话一个稳定 id"、不限格式（2026-10-02 查证），所以未配置时
        # 自动生成一个**进程内不变**的值——不把"必须有一个会话 id"变成"必须往配置里
        # 塞一个静态串"（静态串会把每次运行并成同一个会话，路由/缓存都吃不到新会话）。
        _sid = str(getattr(config, "AGENT_PLAN_SESSION_ID", "") or "").strip() or _AUTO_SESSION_ID
        headers["x-opencode-session"] = _sid
        return headers

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
        effort = _resolve_effort(req)
        thinking_type = "disabled" if (not effort or effort == "none") else "enabled"
        # 端点方言：OpenCode Go 用 `reasoning_effort`（有档位），方舟用 `thinking.type`（只有开关）
        _dialect = _api_dialect(self._base_url)
        _effort_tier = _openai_effort_tier(effort) if _dialect == "openai" else ""
        if _dialect == "openai" and str(effort or "").strip().lower() not in (
                "", "none", *_OPENAI_EFFORT_TIERS, *_OPENAI_EFFORT_ALIASES):
            _verbose_write(
                "[WARN] 思考档位 %r 在 %s 上没有对应值——本次**不发**强度字段（走网关默认）"
                % (effort, _dialect))
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
        }
        # `thinking` **一律照旧发出**（两个端点都发）——复核 2026-10-03 P2：该字段在
        # OpenAI 兼容网关上是否被采纳**未经证实**，把它撤掉是**另一件事**（会连"关闭思考"
        # 这条既有契约一起改掉，而生成侧配置正是 `none`）。这里的改动**只做加法**：
        # 在 OpenAI 兼容端点上**多带一个**档位字段，其余请求逐字不变。
        payload["thinking"] = {"type": thinking_type}
        if _dialect == "openai" and _effort_tier:
            # 该端点目录登记的是**档位**（low/high/max），不是开关；
            # `none`（项目契约 = 关闭思考）在该端点**没有对应档位** ⇒ 不发这个字段，
            # 请求与改动前**逐字相同**——"关闭"在这条通道上是否真的生效仍未证实，
            # 与"默认档"和"不支持"三者必须分开说（见模块顶部 `_api_dialect` 的说明）。
            payload["reasoning_effort"] = _effort_tier
        # 结构化输出：方舟 Doubao 稳定支持 json_object；json_schema strict 视模型而定，
        # 当前 doubao-seed-2-1-turbo 走 json_object 强约束 + 解析端 schema 二次校验
        if req.json_schema or req.user_prompt:
            # 保留该端点可用的 JSON 模式。OpenCode Go 的 strict 请求在同日对照中
            # 先接受、后连最小 schema 也返回 400；不能把一次成功当成稳定能力。
            # 业务调用方须把内部契约交给模型并在本地验收；不在 400 后隐式重发。
            payload["response_format"] = {"type": "json_object"}
        return payload

    # -- 传输阶段的证据 ---------------------------------------------------
    @staticmethod
    def _request_fingerprint(payload: dict) -> str:
        """请求体指纹（**脱敏**：只留哈希，正文不进日志）."""
        try:
            _b = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            return hashlib.sha256(_b).hexdigest()[:16]
        except Exception:
            return ""

    def _send_once(self, url: str, payload: dict, timeout, trace: dict):
        """一次 POST，并把**分阶段时刻**记进 `trace`（复核 2026-10-03 要求）.

        为什么要 `stream=True`：非流式调用要等**整个响应体**到齐才返回，于是墙钟被
        切断时调用方手上没有任何中间信息——复核用 loopback 桩证明过：**持续发送正文**
        与**完全不返回正文**落到上层是同一条超时记录。

        **能给的**：响应头到没到、正文总共回了多少字节、什么时候收完。
        **不能给的**：区分"网关排队"与"远端在算"——该端点**算完才一次性交回**
        （2026-10-03 实测：响应头与正文同一时刻到达），只有"一个字节都没回来"这一条事实。

        ⚠️ 追踪**不许影响调用本身**：不改变读取方式（见下），任何异常都吞掉
        （测试桩、不支持的响应对象）。
        """
        _t0 = time.monotonic()
        resp = requests.post(url, json=payload, headers=self._headers,
                             timeout=timeout, stream=True)
        trace["headers_at"] = round(time.monotonic() - _t0, 3)
        trace["status"] = getattr(resp, "status_code", None)
        try:
            _h = getattr(resp, "headers", None) or {}
            for _k in ("x-request-id", "openai-request-id", "x-oneapi-request-id",
                       "cf-ray", "x-opencode-request-id"):
                _v = _h.get(_k)
                if _v:
                    trace["request_id"] = str(_v)
                    break
        except Exception:
            pass
        _n = 0
        _buf: list = []
        #: ⚠️ 这里**只观测、不改变**读取方式（见下）；失败时 `bytes/done_at` 仍如实记录
        try:
            # **读取路径与改动前逐字相同**（一趟 `iter_content(65536)`，全程不放弃生成器）。
            #
            # 为什么**不**去测"首字节"（复核 2026-10-03 P2 的收尾结论）：要测准必须换读取方式，
            # 而换法都把真实响应弄坏过——① 「先读 1 字节再放弃、另开一趟读余下」在**分块传输
            # 编码**下把正文**截断成 1 字节**（离线实测：服务端实发 150041 字节、读回 1 字节、
            # 服务端侧连接被中止）；② `chunk_size=None` 在 urllib3 下是"读到 EOF"；③ 单趟
            # 逐字节读在 150KB 上 3 分钟跑不完。仪表**不许**改变被观测对象 ⇒ 只保留能如实
            # 观测的量：**响应头时刻 / 正文总字节数 / 收完时刻**（该端点算完才交回，
            # 于是"有没有东西回来"就是这里能给出的全部阶段信息）。
            for _chunk in resp.iter_content(chunk_size=65536):
                if _chunk:
                    _n += len(_chunk)
                    _buf.append(_chunk)
            trace["bytes"] = _n
        except requests.exceptions.RequestException:
            # **传输异常照旧抛给调用方**——读正文期间的失败与"没读到"不是一回事，
            # 吞掉会把半截正文当成功下发（显式报告失败）。
            trace["done_at"] = round(time.monotonic() - _t0, 3)
            raise
        except Exception:
            pass                      # 追踪本身失败（测试桩/不支持的响应对象）不影响调用
        trace["done_at"] = round(time.monotonic() - _t0, 3)
        # **必须回填正文**：`iter_content` 会把响应标成 `_content_consumed`，此后
        # `.text` / `.json()` 会抛 RuntimeError("content ... already consumed")。
        # 这里把读到的字节放回 `_content`，下游消费方式与**非流式**路径逐字一致。
        # ⚠️ **空正文也要回填**（复核 2026-10-03 P1）：只在 `_buf` 非空时回填，会让
        # 「401 + 空正文」这类响应在真实入口抛 `RuntimeError: content already consumed`，
        # 把鉴权失败**遮蔽**成解析崩溃（`_error_from_response` 读 `resp.text`）。
        try:
            resp._content = b"".join(_buf)
        except Exception:
            pass
        return resp

    @staticmethod
    def _trace_note(trace: dict, payload: dict, effort, sent: bool = True) -> str:
        """一行**脱敏**轨迹：请求指纹 / 实际强度 / 关联 ID / 各阶段时刻.

        `sent=False` = **发送前**就没发（余量耗尽）；`sent=True` 但 `headers_at` 缺失
        ⇒ **一个字节都没有回来**。⚠️ 该端点**算完才一次性交回**（2026-10-03 实测：响应头
        到达即正文到达），所以这**不能**区分"网关排队"与"远端在算"——只报事实，不猜阶段。

        这里**不报"首字节时刻"**：要测准必须改读取方式，而改法都会改变被观测对象
        （见 `_send_once` 里的三条实测记录）。
        """
        if not sent:
            return "（未发出：发送前余量已耗尽）"
        _thinking = (payload.get("thinking") or {}).get("type")
        _strength = (payload.get("reasoning_effort")
                     or ("thinking=%s" % _thinking if _thinking else "")
                     or "（未发强度字段）")
        _head = ("指纹=%s｜强度=%s（要求 %r）｜关联ID=%s｜"
                 % (AgentPlanTextService._request_fingerprint(payload), _strength,
                    effort, trace.get("request_id") or "（无）"))
        if trace.get("headers_at") is None:
            # 措辞**不许**断言是网关排队（2026-10-03 实测：该端点算完才交回 ⇒ 排队与在算
            # 在此**不可分**）。
            return _head + "**无任何响应头/字节**（已发出；该端点算完才交回，排队与在算不可分）"
        return (_head + "响应头 %ss｜正文 %s 字节｜收完 %s"
                % (trace.get("headers_at"), trace.get("bytes", 0),
                   trace.get("done_at", "未收完")))

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        """提交一次文本生成请求（同步阻塞）.

        重试策略（v2，2026-09-03 现场压测后修正）：
          - 长任务客户端超时（read timeout）：**不重试**——重发会让旧请求仍在模型侧跑，
            增加额度浪费和排队。改报「超时未完成」由上层业务队列决策。
          - 5xx/网络异常：1 次退避（防瞬时抖）；用尽后报错。
          - 401/403/400：不重试直接报错（鉴权/参数问题重试无意义）。
        """
        # 工作包 §4 B：整次规划一个**绝对截止时刻**。排队（Key 冷却）、退避、
        # Retry-After 等待、每次 POST 都按它重算余量；耗尽 ⇒ 一个请求都不发。
        _gate = TransportGate(req, default_timeout=AGENT_PLAN_TEXT_TIMEOUT_SEC,
                              where="agent_plan")
        _wait_left = _gate.wait_budget()
        if _wait_left is not None and _wait_left <= 0:
            return TextGenerationResult(success=False, error=_gate.expiry_reason())
        try:
            # 排队等待：**base 上限仍是 180s**（#23：全 key 长冷却时不许无限占线程），
            # 有截止时刻时再与余量取最小值——排队同样不许睡过截止点。
            self._key_scheduler.acquire(
                timeout=AGENT_PLAN_QUEUE_CAP_SEC if _wait_left is None
                else max(0.0, min(AGENT_PLAN_QUEUE_CAP_SEC, _wait_left)))
        except TimeoutError as _e:
            return TextGenerationResult(
                success=False,
                error="%s（排队未取得可用 Key：%s）" % (_gate.expiry_reason(), _e))

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
            f"response_format={payload.get('response_format')}, "
            f"指纹={AgentPlanTextService._request_fingerprint(payload)}, "
            f"实发结构化输出={sent_structured_output(payload)}"
            # **静默降级不许再声称受约束**（D）：调用方要 schema、本通道不下发时，
            # 把这件事写进日志——否则读日志的人会以为响应受 strict 约束。
            + (("｜⚠️ 调用方要的是 json_schema:%s，**本通道不下发**（形状由解析端"
                "结构化拒收兜底）" % str((req.json_schema or {}).get("name") or ""))
               if (req.json_schema or {}).get("name")
               and sent_structured_output(payload)
               != "json_schema:%s" % str((req.json_schema or {}).get("name")) else "")
        )

        # 重试判定/次数/退避/时间预算统一走 `_text_gen.retry_policy`（N6，2026-09-28）：
        # provider 不再自带常量与 `except` 分支里的判定——否则「哪些错可重试」会随
        # provider 漂移，上层也无从判断该不该再叠一层重试。
        # 语义要点：**读超时不重试**（旧请求仍在模型侧跑，重发 = 双份长任务）。
        # v6.70.3（09-14）：超时随请求走（req.timeout_sec 分级），None 回退全局。
        # 工作包 §4 B：**真上限 = min(偏好, 余量)**——每次尝试前重算（`_gate.limit()`），
        # 且不取整（取整会把 0.4s 变成 0，再被当成"没配置"回退到 600s 默认）。
        # v6.152（2026-09-29 五次跑实测）：循环上限取**各类别里最宽的一次数**
        # （`TRANSPORT_CONNECT` = 共 3 次），每个分支再用**该类别自己的**次数与预算收口。
        # 此前上限写死为无类别的 `submit_attempts()`（= 2），于是「连接类共 3 次」这条
        # 判据永远够不到第 3 次：第 2 次失败时 `should_retry` 仍判「可重试」→ 退避一次
        # → `for` 已尽 → 落到尾部报「未知原因」，把已分类的 `transport_connect` 与
        # 底层异常一起丢掉（run 02 实测：16.1s 内两次连接重置后报「未知原因」）。
        _attempts = max(retry_policy.submit_attempts(_fc)
                        for _fc in retry_policy.FailureClass)
        # 尾部兜底要带上真因：异常分支与 5xx 分支都会写它
        last_error = ""
        _effort = _resolve_effort(req)          # 阶段轨迹里记"要求 vs 实发"
        _t0 = time.monotonic()
        for attempt in range(1, 1 + _attempts):
            _timeout, _deny = _gate.limit()
            if _deny:
                # 余量耗尽：**不发请求**（也不回退默认 600s 超时）
                return TextGenerationResult(success=False, error=_deny)
            _trace: dict = {}
            try:
                resp, _timed_out, _why = _gate.request(
                    lambda _t: self._send_once(url, payload, _t, _trace))
                if _why:
                    # 墙钟没等到 / 余量耗尽：**不重发**（旧请求可能仍在模型侧跑）
                    # 复核 2026-10-03：超时文案只说"没等到"，**必须**同时给出走到了哪一段
                    # ——否则"远端排队 / 远端在算 / 传输停顿"三者事后无从区分。
                    _note = self._trace_note(_trace, payload, _effort,
                                             sent=bool(_timed_out))
                    if _note:
                        _verbose_write("[TRACE] agent_plan_text 阶段轨迹（失败）：" + _note)
                        return TextGenerationResult(
                            success=False, error="%s｜阶段轨迹：%s" % (_why, _note))
                    return TextGenerationResult(success=False, error=_why)
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
                _elapsed = time.monotonic() - _t0
                # 类别可重试 **且** 次数与时间预算都有余量 → 由本层退避重发；
                # 否则把分类带回上层（上层**不得**再对传输类叠加重试）。
                last_error = (
                    f"Agent Plan 提交失败[{_fc.value}]: {e}"
                    f"（尝试 {attempt}/{retry_policy.submit_attempts(_fc)}，"
                    f"已用 {_elapsed:.1f}s；"
                    f"重试预算 {retry_policy.submit_budget_sec():g}s）"
                )
                if not retry_policy.should_retry(_fc, attempts_used=attempt,
                                                 elapsed_sec=_elapsed):
                    return TextGenerationResult(success=False, error=last_error)
                _wait = retry_policy.submit_backoff_sec(attempt)
                _verbose_write(
                    f"[!] 提交失败[{_fc.value}]: {e}，"
                    f"{_wait:g}s 后重试 (尝试 {attempt}/"
                    f"{retry_policy.submit_attempts(_fc)})"
                )
                if not _gate.wait(_wait):
                    return TextGenerationResult(success=False, error=_gate.expiry_reason())
                continue

            _verbose_write(
                f"[DEBUG] agent_plan_text_submit response: status={resp.status_code}, "
                f"body={resp.text[:500]}"
            )

            _verbose_write("[TRACE] agent_plan_text 阶段轨迹："
                           + self._trace_note(_trace, payload, _effort))
            if resp.status_code == 200:
                return self._parse_response(
                    resp, model, payload.get("max_completion_tokens"),
                    allow_truncated=req.allow_truncated, trace=_trace,
                )

            if resp.status_code == 429:
                # 限流：1 次退避后重试。次数仍按**该类自己的**上限收口
                # （`RATE_LIMIT` 未进 `RETRYABLE`，等待语义由本层处理，故这里
                # 显式读它的 `submit_attempts` = 2，保持既有行为不变）。
                if attempt >= retry_policy.submit_attempts(
                        retry_policy.FailureClass.RATE_LIMIT):
                    return self._error_from_response(resp, last_error)
                wait = 30.0
                m = re.search(r"retry[-_]after[\"':=\s]+(\d+)", resp.text, re.IGNORECASE)
                if m:
                    wait = float(m.group(1))
                _verbose_write(f"[!] 429 限速，等待 {wait:.0f}s 后重试 (尝试 {attempt}/2)")
                # Retry-After 同样受截止时刻约束：睡不过截止点
                if not _gate.wait(wait):
                    return TextGenerationResult(success=False, error=_gate.expiry_reason())
                continue

            if 500 <= resp.status_code < 600:
                if attempt >= retry_policy.submit_attempts(
                        retry_policy.FailureClass.SERVER_ERROR):
                    return self._error_from_response(resp, last_error)
                last_error = f"服务端 {resp.status_code}: {resp.text[:200]}"
                _verbose_write(
                    f"[!] 服务端 {resp.status_code}，2s 后重试 1 次 (尝试 {attempt}/2)"
                )
                if not _gate.wait(2.0):
                    return TextGenerationResult(success=False, error=_gate.expiry_reason())
                continue

            # 客户端错误（401/403/400/404）不重试
            break

        # 走到这里说明重试用尽 / 客户端错误
        if "resp" in locals():
            return self._error_from_response(resp, last_error)
        return TextGenerationResult(
            success=False,
            error=last_error or "Agent Plan 提交失败: 未知原因（未取得响应，也未捕获到异常）",
        )

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------
    def _parse_response(
        self, resp: requests.Response, model: str, cap: Optional[int] = None,
        allow_truncated: bool = False, trace: Optional[dict] = None,
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

        # 阶段轨迹的第二段：**响应体里自报的思考用量**——它回答"这次到底有没有在思考"，
        # 也是判断"不发强度字段时网关给的是哪一档"的唯一现场证据（不靠猜）。
        if trace is not None:
            try:
                _u = data.get("usage") or {}
                _det = _u.get("completion_tokens_details") or {}
                _rsn = _u.get("reasoning_tokens") or _det.get("reasoning_tokens")
                trace["reasoning_tokens"] = _rsn
                _verbose_write(
                    "[TRACE] agent_plan_text 用量：思维链=%s｜输出=%s"
                    % ("未知" if _rsn is None else _rsn, _u.get("completion_tokens")))
            except Exception:
                pass          # 轨迹失败不影响解析（真正的解析错误在下面各自返回）
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
                    finish_reason=str(finish or "length"),
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
                finish_reason=str(finish or "length"),
                error=(
                    f"Agent Plan 输出被截断（finish_reason=length, model={model}, "
                    f"max_completion_tokens={cap}, completion_tokens={_completion}）"
                    f"——输出 JSON 不完整，已整份丢弃（截断内容无法可靠修复）。"
                    f"如为全脚本生成，请调高 AGENT_PLAN_TEXT_MAX_TOKENS："
                    f"实测 11 镜全脚本约需 26k tokens。"
                ),
            )

        return TextGenerationResult(success=True, text=text, usage=usage,
                                    finish_reason=str(finish or ""))

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
