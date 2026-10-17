# -*- coding: utf-8 -*-
"""**计划修订控制器**：把「反馈 → 规划器修订 → 应用 → 完整重验」收成一个**有界闭环**.

2026-10-01 工作包（复核第 31 轮）。此前两处判定各走各的路：契约门发现「承载没写完」时
**直接返回**（不反馈、不修订、不写缓存，下一轮重新掷骰子）；预算不可行时另开一轮重选，
**自成额度**。控制器把两者收进同一个循环：**同一份验收链**判、**同一个额度与截止时刻**花、
**同一份指纹**查循环。

它本身**不做 IO、不碰模型**：`accept`（验收链）与 `revise`（调模型 + 应用操作）都由调用方
注入——离线矩阵因此可以逐格构造，不需要真的调用模型。
"""
from __future__ import annotations

import hashlib
import json
import time

#: 停止原因（**过程**口径）：为什么停下来。这些原因**都不单独证明**故事不可满足。
STOP_OK = "ok"
STOP_CALL_FAILED = "call_failed"            # 传输/通道失败
STOP_VERIFICATION_FAILED = "verification_failed"
STOP_ILLEGAL = "illegal_response"           # 回的东西不合法（不是 JSON / 不是操作 / 应用被拒）
STOP_NO_CHANGE = "no_change"                # 规划器明确没有可改之处
STOP_LOOP = "loop"                          # 改回一个已经出现过的状态
STOP_CALLS = "calls_exhausted"              # 额度用尽
STOP_DEADLINE = "deadline_exhausted"        # 截止时刻到
STOP_CONTRACT = "contract_unclosed"         # 契约仍未闭合
STOP_BUDGET = "budget_infeasible"           # 预算仍不满足
#: **目标身份失效**（复核 2026-10-08 P2 第 5 条）：本次诊断指向的修改实体不存在 /
#: 身份写错 ⇒ **一次模型调用都没发**、保留基线，把责任送回**记录生产者**。
#: 它不是"这份计划不行"的结论（那张记录坏在身份上，不在故事上），所以**不进**
#: `PLAN_LEVEL_STOPS`；这一轮也**不占额度**（见 `RevisionBudget.refund`）。
STOP_INVALID_TARGET = "invalid_target"

#: **只有这些**停止原因才落在「计划层已有结论」这一侧（仍不等于"故事不可满足"，
#: 后者要人看诊断）：调用失败/非法响应**不算**——那只是这一轮没跑成。
PLAN_LEVEL_STOPS = (STOP_NO_CHANGE, STOP_LOOP, STOP_CALLS, STOP_DEADLINE)

#: 停止原因里**由某一轮修订直接说出来**的：真实原因（传输/模型/应用拒绝）就在那一轮的
#: 详情里，必须当**主原因**用。额度与截止不是"某一轮说了什么"，不在此列。
ROUND_DETAIL_STOPS = (STOP_CALL_FAILED, STOP_ILLEGAL, STOP_NO_CHANGE, STOP_LOOP,
                      STOP_INVALID_TARGET)

#: 残余问题的类别（与停止原因**分账**）：停止原因说"为什么停"，它说"还剩什么"。
RESIDUAL_NONE = "none"
RESIDUAL_CONTRACT = "contract"
RESIDUAL_BUDGET = "budget"
RESIDUAL_MIXED = "mixed"

#: 规划调用的默认额度：契约修订与预算重选**共用**（复核第 31 轮第 3 条：最多 3 次，
#: 失败/非法/无变化都扣次数）。
DEFAULT_MAX_CALLS = 3

#: 循环指纹覆盖的字段 = **完整修订状态**里可编辑的那些（复核第 31 轮第 4 条）：
#: 节拍 / 对白 / 旁白条目 / 必保留事实（本体 + 承载声明）/ 钩子（承载 + 说出口）/
#: 已定核心事件 / **呈现关系块**（`presentation`：文字卡、事实绑定、事件归属、uid 表）。
#: **排除**：plan_id、时间戳、诊断元数据（plan_replan / plan_revision）——
#: 否则"只修了承载声明"的合法修订会被误判成"无变化"。
#:
#: ⚠️ `presentation` **必须**在内（2026-10-04）：新模式（`screen_hybrid`）的承载关系、
#: 卡片与事件归属**只存在于这一块**。缺了它，「只改了一张卡」会算出与基线相同的指纹，
#: 控制器把一次**有效修订**记成 `no_change`——与 R1 修掉的 `view_digest` 那个
#: 「只补出处被判没改」是同一类缺陷，只是发生在控制器这一层。
#:
#: `plan_verification` 同样排除：它是**核验记录**，不是计划内容；它的变化是核验结果变了，
#: 不是规划改了。核验记录自己的身份由 `content_verification.plan_digest_of` 管（它也排除
#: 这三个键）。
_FP_FIELDS = ("shot_beats", "dialogue_lines", "narration_lines",
              "required_facts", "hook_carrier", "hook_spoken", "main_events",
              "climax", "causal_chain", "presentation")


def revision_fingerprint(plan) -> str:
    """**完整修订状态**的指纹——循环判据，**不是** `plan_identity`.

    `plan_identity` 只反映节拍/对白/旁白的编号身份（那是给缓存与派生物用的），拿它代表
    修订状态会让"只改了事实承载或钩子声明"的合法修订看起来是"没变"。
    """
    if not isinstance(plan, dict):
        return ""
    _state = {_k: plan.get(_k) for _k in _FP_FIELDS}
    return hashlib.sha1(json.dumps(_state, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str
                                   ).encode("utf-8")).hexdigest()


class RevisionBudget:
    """契约修订与预算重选**共用**的额度与截止时刻（复核第 31 轮第 3 条）.

    - `max_calls`：规划调用上限（默认 3）。**每一次修订轮都扣**——调用失败、结果非法、
      无变化都扣：额度管的是"花掉的规划机会"，不是"成功的次数"。
    - `deadline_sec`：从构造时刻起的总秒数（本阶段自己的上限）。
    - `run_deadline_at`：**整次 Step 01 的绝对截止时刻**（monotonic，工作包 §4 B）。
      构造时**只算一次**：`deadline_at = min(整次截止, 现在 + deadline_sec)`，
      此后每轮不再重新开表。通道内的排队/退避/每次 POST 都用 `deadline_at` 重算余量
      （见 `_text_gen.deadline`），`remaining_sec()` 仍是给旧调用方的秒数视图。
    """

    def __init__(self, *, max_calls: int = DEFAULT_MAX_CALLS, deadline_sec=None,
                 run_deadline_at=None, clock=time.monotonic):
        self.max_calls = int(max_calls)
        self.deadline_sec = None if deadline_sec is None else float(deadline_sec)
        self._clock = clock
        self._t0 = float(clock())
        # **只创建一次**：min(整次截止, 本阶段上限)；两者都没有 ⇒ 不设截止（旧行为）
        _cands = []
        if self.deadline_sec is not None:
            _cands.append(self._t0 + self.deadline_sec)
        if run_deadline_at is not None:
            _cands.append(float(run_deadline_at))
        self.deadline_at = min(_cands) if _cands else None
        self.calls_used = 0

    @property
    def calls_left(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def remaining_sec(self):
        if self.deadline_at is None:
            return None
        return max(0.0, self.deadline_at - float(self._clock()))

    def expired(self) -> bool:
        _r = self.remaining_sec()
        return _r is not None and _r <= 0.0

    def stop_reason(self) -> str:
        """额度/时间哪个先耗尽（都够 → 空串）."""
        if self.calls_left <= 0:
            return STOP_CALLS
        if self.expired():
            return STOP_DEADLINE
        return ""

    def spend(self) -> None:
        self.calls_used += 1

    def refund(self) -> None:
        """**没有发起模型调用**的那一轮把额度退回来（复核 2026-10-08 P2 第 5 条）.

        `spend()` 在调用**之前**扣（额度管的是"花掉的规划机会"），但"目标身份失效"
        （`STOP_INVALID_TARGET`）在分派前就返回、**一次 POST 都没发**——把它算成花掉的
        机会，等于"上游记录写错了实体身份"要花模型的额度。所以那一轮退回来；
        退不会低于 0（重复退不会把额度退成负数）。
        """
        self.calls_used = max(0, self.calls_used - 1)

    def as_dict(self) -> dict:
        return {"max_calls": self.max_calls, "calls_used": self.calls_used,
                "deadline_sec": self.deadline_sec, "deadline_at": self.deadline_at,
                "remaining_sec": self.remaining_sec()}


def problem_records(problems) -> list:
    """问题清单 → 列表（`None` / 单条 / 列表都收；**类型稳定**的入口）.

    复核 2026-10-02 之四 D-1：记录形态可能不是列表（外部调用方给的单条、回退项），
    消费方各自假设"就是 list[dict]"⇒ 兜底项被 `.get()` 调用时抛 `AttributeError`。
    归一集中在这里与 `record_text`——附栏、日志、摘要都从这里取。
    """
    if problems is None:
        return []
    if isinstance(problems, (list, tuple)):
        return list(problems)
    return [problems]


def record_text(rec) -> str:
    """问题记录 → 一行可读文本（诊断的"另列"栏与日志用，不参与判据）.

    **字典与普通字符串都收**：记录由生产者给（`{"code","reason","detail"}`），但也可能是
    摘要字符串或回退项——这里一处归一，消费方不再假设 `.get()` 一定存在。
    """
    if isinstance(rec, dict):
        return str(rec.get("reason") or rec.get("detail") or rec.get("code") or rec)
    return str(rec)


def _rec_code(rec) -> str:
    """问题记录 → `code`（不是字典 / 没有 code 的记录按空串算）.

    空 code 仍算"有问题"：`residual_class` 因此把它归到**非预算**一侧，
    不会把"有记录但没 code"读成"没有问题"（旧实现同样如此）。
    """
    return str(rec.get("code") or "") if isinstance(rec, dict) else ""


#: 拒收原因在**日志/诊断展示**时的长度上限。
#:
#: ⚠️ 它**不约束存储**，也不约束进模型反馈的内容。复核 2026-10-04 P1-1：旧写法在
#: `rejection_record` 这一步就 `[:400]`，渲染层再宽也拿不到后面的问题——章 1 第 3 轮
#: 的 6 条问题（533 字）实测被截成 400 且**没有省略标记**，于是"整条进入下一轮请求"
#: 的结论不成立（当时的新增测试直接调渲染器，绕过了控制器这一跳）。
#: 现在的分工：**存储完整** → 展示有界且**显式标注省略量** → 回灌模型读完整原因。
REJECTION_DISPLAY_MAX = 400


def rejection_display(rec_or_text, limit: int = REJECTION_DISPLAY_MAX) -> str:
    """拒收原因的**展示形态**（有界 + 显式标注省略量）——日志与诊断用，不回灌模型.

    回灌模型走 `outline._rejection_feedback_block`（它读**完整** `detail`，越界才标注）。
    这里只服务人看的行：截了就写清还剩多少字，不静默省略。
    """
    _t = (rec_or_text.get("detail") if isinstance(rec_or_text, dict)
          else rec_or_text) or ""
    _t = str(_t).strip()
    if len(_t) <= max(1, int(limit)):
        return _t
    return "%s……（另有 %d 字未显示）" % (_t[:int(limit)], len(_t) - int(limit))


def rejection_record(round_no, kind, detail) -> dict:
    """**拒收记录**：哪一轮、什么类型、具体字段与原因（复核 2026-10-02 之五）.

    与 `problems`（验收问题）**分账**：验收问题说的是"这份计划哪里不合格"，拒收记录说的是
    "模型这一轮回的东西哪里不合法"。两条都要进下一轮请求，但**不能混成一张表**——
    `residual_class` 按 `problems` 判类别，把拒收项混进去会让"响应违约"被读成"计划层结论"。

    拒收原因**由生产者给**（`apply_replan_ops` 的 `why`，内含具体字段路径与判据），
    这里**原样保存**（不截断）；消费方不重新解释它。展示要有界时用
    `rejection_display()`——那条路会**显式标注**省略了多少字。
    """
    return {"round": int(round_no), "kind": str(kind or ""),
            "detail": str(detail or "").strip()}


def failure_detail(res, problems=None) -> str:
    """控制器结果 → **主失败原因**文本（复核 2026-10-02 之四 D-1）.

    优先级：**轮次详情**（`history[-1]["detail"]`，如 `call_failed: transport reset at
    gateway`）→ 残余问题摘要。基线计划自己的内容问题**不是**主原因——旧写法拿它顶替
    传输失败/非法响应，归因反了；基线问题是诊断里**另列**的附栏（`baseline_problems`）。

    `problems` 可覆盖 `res["problems"]`（调用方重验后手上那份更贴近下发内容）。
    """
    _res = res if isinstance(res, dict) else {}
    _hist = _res.get("history") or []
    _last = _hist[-1] if _hist and isinstance(_hist[-1], dict) else {}
    _d = str(_last.get("detail") or "").strip()
    _sum = "；".join(record_text(x)
                    for x in problem_records(
                        _res.get("problems") if problems is None else problems)[:3])
    if _d and str(_res.get("stop_reason") or "") in ROUND_DETAIL_STOPS:
        return _d
    return _sum or _d


def residual_class(problems) -> str:
    """残余问题的类别：`none` / `contract` / `budget` / `mixed`（按 `code` 分）."""
    _codes = {_rec_code(p) for p in problem_records(problems)}
    if not _codes:
        return RESIDUAL_NONE
    _budget = {c for c in _codes if c.startswith("budget")}
    _other = _codes - _budget
    if _budget and not _other:
        return RESIDUAL_BUDGET
    if _other and not _budget:
        return RESIDUAL_CONTRACT
    return RESIDUAL_MIXED


class RevisionController:
    """「反馈 → 修订 → 应用 → 重验」的有界循环（纯状态机，IO 由调用方注入）.

    - `accept(plan, novel_text) -> [问题]`：**唯一的验收链**（结构/原文/承载/预算），
      每项是 `{"code", "path", "detail", "basis"}`；空表 = 通过。
    - `revise(plan, problems, budget, rejections) -> {"kind", "plan", "detail"}`：**一轮**
      修订；`kind` 取 `applied` / `no_change` / `call_failed` / `illegal`，`applied`
      必须给出新计划。`rejections` 是**此前被拒收的响应记录**（`rejection_record`，
      只读；复核 2026-10-02 之五：拒收原因要**一并反馈**给下一轮请求）。
      实现方每次模型调用都要用 `budget.remaining_sec()` 作单次超时。
    """

    def __init__(self, *, accept, revise, budget=None, fingerprint=revision_fingerprint,
                 log=None, on_round=None):
        self.accept = accept
        self.revise = revise
        self.budget = budget or RevisionBudget()
        self.fingerprint = fingerprint
        self.log = log or (lambda *a: None)
        #: 每一轮的**完整证据**钩子（复核 2026-10-02 之七）。控制器不做 IO：
        #: 生产入口把它接到 `<章节目录>/step01_replan_rounds.jsonl`。
        self.on_round = on_round

    def _emit(self, rec, *, plan_in=None, problems_in=None, plan_out=None,
              problems_out=None, rejection=None):
        """把这一轮的**完整证据**交给 `on_round`（没有钩子就只把轻量字段补进 `_hist`）.

        记录内容（复核之七）：`call_id`（该轮响应的**完整正文**已按它落盘）、输入计划的
        **指纹与本体的引用**、候选的**指纹**、拒收记录、以及**完整验收结果**。据此可以
        重放真实应用器：读回归档正文 + `plan_in` → `apply_replan_ops` → 应当得到同样的
        拒收或验收结果——**不必再靠日志前缀猜**（日志对响应有 ~1000 字符截断）。

        留档失败**不改裁决**（内容、调用与时间判据照旧），但必须**看得见**。
        """
        rec["call_id"] = str(rec.get("call_id") or "")
        rec["fingerprint_in"] = self.fingerprint(plan_in) if plan_in is not None else ""
        rec["fingerprint_out"] = self.fingerprint(plan_out) if plan_out is not None else ""
        if rejection:
            rec["rejection"] = rejection
        if self.on_round is None:
            return
        try:
            self.on_round({
                "round": rec.get("round"), "kind": rec.get("kind"),
                "detail": rec.get("detail"), "call_id": rec["call_id"],
                "fingerprint_in": rec["fingerprint_in"],
                "fingerprint_out": rec["fingerprint_out"],
                "rejection": rec.get("rejection"),
                "plan_in": plan_in,
                "plan_out": (plan_out if isinstance(plan_out, dict) else None),
                "problems_in": list(problems_in or []),
                "problems_out": (None if problems_out is None else list(problems_out)),
                # **归因证据束**（复核之九）：修法可表达性 / 请求信息 / 原始响应形状 /
                # 应用结果——`no_change` 只说明"候选没变"，这四项与 `problems_out`
                # 一起才能支撑归因，**不许**由它单独推断模型意图
                "evidence": dict(rec.get("evidence") or {}),
            })
        except Exception as exc:                                   # noqa: BLE001
            print("[WARN] 修订轮留档失败（**证据缺失**，裁决不受影响）：%s: %s"
                  % (type(exc).__name__, exc))

    def _result(self, plan, stop, problems, history, rejections=None):
        _res = residual_class(problems)
        return {"plan": plan, "stop_reason": stop, "problems": list(problems or []),
                "residual_class": _res, "calls": self.budget.calls_used,
                "rounds": len(history), "history": history,
                # 工作包 §4 B：截止时刻到了 ⇒ 候选**不许采纳、不许缓存、不许提交**，
                # 返回的是**基线计划**（候选连验收都没跑完，没有下发资格）
                "deadline_exhausted": bool(stop == STOP_DEADLINE),
                # **分账**：只有"计划层已有结论"的停止原因 + 确实还剩计划层问题时，
                # 才允许下游把它当成"这份计划不可用"；调用失败/非法响应一律不是结论。
                "plan_level_conclusion": bool(stop in PLAN_LEVEL_STOPS
                                              and _res != RESIDUAL_NONE),
                #: **拒收记录**（与 `problems` 分账）：模型哪几轮回的东西不合法、具体
                #: 是哪个字段。诊断与下一轮请求都从这里取。
                "rejections": list(rejections or []),
                #: 额度是否已用尽——"连续非法直到额度耗尽"要**两个事实都记**
                #: （主终局是响应违约，额度耗尽是附带事实）。
                "quota_exhausted": bool(self.budget.calls_left <= 0)}

    def _stop_result(self, plan, stop, problems, hist, rejections):
        """停止原因的**主次分账**（复核 2026-10-02 之五）.

        额度耗尽（`calls_exhausted`）**本身不是**"这份计划不行"的结论：如果最后一轮回的是
        非法响应，主终局就是**响应违约**（`illegal_response`，额度耗尽另记在
        `quota_exhausted`）——否则 `calls_exhausted` 落在 `PLAN_LEVEL_STOPS` 里，下游会把
        "模型三次都没按契约回答"读成"故事装不下"。主原因取**最后一轮的真实详情**
        （`failure_detail` 同一条口径）：这一次是"响应违约"还是"额度耗尽"，差别就在这里。

        截止时刻（`deadline_exhausted`）优先，不改：过点后不再发 POST、不采纳、不写缓存
        ——它是**时间门**的结论，与"模型答得对不对"是两条轴。
        """
        _last = hist[-1] if hist and isinstance(hist[-1], dict) else {}
        if (stop == STOP_CALLS and rejections
                and str(_last.get("kind") or "") == "illegal"):
            # 复核 2026-10-04：这里原来直接 `[:160]` 硬切，再把 `）——额度已耗尽…`
            # 接在**切了一半的句子**后面 ⇒ 事故现场读起来像"一条节拍）——额度已耗尽"
            # 是那条问题本身，真正的规则整句丢失（章 1 run 20261004T052835-7596）。
            # 走同一个展示出口：截了就写清还剩多少字，不静默省略、不制造假边界。
            # 2026-10 复核：本次改写在把摘要前半截带出来的同时（见上），**丢掉了本模块
            # 自己文档里的结论术语「响应违约」**（见同文件 `STOP_ILLEGAL` 说明与
            # `residual_class` 注释：主终局就是"响应违约"，额度耗尽是附带事实）。
            # `failure_detail()` 在 STOP_ILLEGAL 下**直接返回本条 detail**，于是这条
            # 文案就是操作员看到的"主失败原因"——术语一丢，代码与自身口径就打架。
            # 现补回术语，保留上一版的全部改进（截断经 `rejection_display` 披露残余）。
            _note = ("连续 %d 次响应被拒收——主终局是**响应违约**（模型没按契约回答），"
                     "不是「故事不可行」；额度已耗尽（最后一次：%s）；"
                     "修订响应或编译结果未通过约束；须核对拒收阶段与原因"
                     % (len(rejections),
                        rejection_display(_last.get("detail"), limit=160)))
            if hist:
                hist[-1] = dict(_last, detail=_note, quota_exhausted=True)
            self.log("[REVISION] " + _note)
            return self._result(plan, STOP_ILLEGAL, problems, hist, rejections)
        return self._result(plan, stop, problems, hist, rejections)

    def run(self, plan, novel_text: str = "") -> dict:
        """跑完整个闭环 → `{plan, stop_reason, problems, residual_class, calls, rounds, history}`.

        **停止语义**（复核 2026-10-02 之五）：非法响应**绝不采纳**（计划一个字节不改、
        不写缓存），把"哪一处不合法"记成**拒收记录**（与验收问题分账）并在**下一次请求里
        一并反馈**；只要额度与截止还够就继续问。随后回来合法操作 ⇒ 照常应用 + 完整验收。
        **连续非法直到额度耗尽** ⇒ 主终局仍是响应违约（见 `_stop_result`）。
        """
        _problems = list(self.accept(plan, novel_text) or [])
        # **最终结果返回前**先核一次（复核第 45 轮 P1-①）：验收是纯函数（无 IO、不花时间），
        # 所以放在它之后既拿得到问题清单，也覆盖"进入控制器时已经截止"与"验收期间耗尽"——
        # 这两种情形下**合法计划同样不许返回 ok**（截止时刻是下发的必要条件）。
        if self.budget.expired():
            self.log("[REVISION] 首次验收后已过截止时刻 ⇒ 不下发（deadline_exhausted）")
            return self._result(plan, STOP_DEADLINE, _problems, [])
        if not _problems:
            return self._result(plan, STOP_OK, [], [])
        _seen = {self.fingerprint(plan)}
        _hist = []
        #: 被拒收的响应（第几轮、什么类型、具体字段与原因）——**与 `_problems` 分账**，
        #: 下一轮请求把它一并反馈（复核 2026-10-02 之五）。
        _rej = []
        while True:
            _stop = self.budget.stop_reason()
            if _stop:
                self.log("[REVISION] 停止：%s（已用 %d/%d 次）"
                         % (_stop, self.budget.calls_used, self.budget.max_calls))
                return self._stop_result(plan, _stop, _problems, _hist, _rej)
            self.budget.spend()
            # 这一轮的**输入**（留档要能重放：`plan_in` + 归档响应 → 真实应用器）
            _plan_in = plan
            _probs_in = list(_problems)
            _out = self.revise(plan, _problems, self.budget, _rej) or {}
            _kind = str(_out.get("kind") or STOP_CALL_FAILED)
            _hist.append({"round": len(_hist) + 1, "kind": _kind,
                          "detail": str(_out.get("detail") or "")[:200],
                          # 这一轮用的是**哪次调用**（复核之七）：完整正文已按 call_id
                          # 落盘，轮次记录与诊断都引用它，不靠日志前缀猜。
                          "call_id": str(_out.get("call_id") or ""),
                          "problems_before": len(_problems),
                          # **归因证据束**（复核之九）：`no_change` 只说明"候选没变"——
                          # 修法可表达性 / 请求信息 / 原始响应形状 / 应用结果一并留档，
                          # 残余问题由 `problems_out` 给；**不据此推断模型意图**。
                          "evidence": dict(_out.get("evidence") or {})})
            # **修订返回后**核一次，**所有**返回类型都走这条（复核第 45 轮 P1-②：
            # 此前只有 applied 分支查过，call_failed/illegal/no_change 会带着
            # `deadline_exhausted=False` 返回；底层原因仍留在历史详情里）
            if self.budget.expired():
                _hist[-1]["detail"] = ("修订返回时已过截止时刻 ⇒ 按 deadline_exhausted 收口"
                                       "（原返回类型 %s：%s）" % (_kind, _hist[-1]["detail"]))
                _hist[-1]["discarded_candidate"] = True
                self.log("[REVISION] 修订返回时已过截止时刻 ⇒ 丢弃本轮结果（原类型 %s）" % _kind)
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in)
                return self._result(plan, STOP_DEADLINE, _problems, _hist, _rej)
            if _kind == STOP_VERIFICATION_FAILED:
                self.budget.refund()
                _hist[-1]["no_model_call"] = True
                self.log("[REVISION] 核验未完成 ⇒ 零规划调用、保留候选，责任在核验记录生产者")
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in)
                return self._result(plan, STOP_VERIFICATION_FAILED, _problems, _hist, _rej)
            if _kind == STOP_INVALID_TARGET:
                # **上游记录契约失效**：本次诊断指向的修改实体不存在/身份写错。
                # `revise_plan_once` 在**分派之前**就返回了，所以这一轮
                # **一次 create_service / 一次 generate 都没发生** ⇒ 额度退回去
                # （`spend()` 在调用前扣过），并**立即收口**：问题清单一个字没变，
                # 再问一轮只会得到同一条记录。
                self.budget.refund()
                _hist[-1]["no_model_call"] = True
                _hist[-1]["detail"] = ("目标身份失效（上游记录契约，零调用）：%s"
                                       % _hist[-1]["detail"])
                self.log("[REVISION] 第 %d 轮：%s——零模型调用、保留基线，"
                         "责任在记录生产者（额度已退回）"
                         % (len(_hist), _hist[-1]["detail"]))
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in)
                return self._result(plan, STOP_INVALID_TARGET, _problems, _hist, _rej)
            if _kind == "call_failed":
                # 传输/通道失败**维持现有独立归因**：它不是"响应违约"，重发也修不好
                # （复核 2026-10-02 之五）。
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in)
                return self._result(plan, STOP_CALL_FAILED, _problems, _hist, _rej)
            if _kind == "illegal":
                # **拒收 → 反馈 → 继续**（复核 2026-10-02 之五）：计划与缓存一个字节不改，
                # 把生产者给的**具体字段与原因**记进拒收记录，交给下一轮请求一并反馈。
                # 额度/截止这时若已用尽，下一轮开头就会带着这条记录收口（`_stop_result`
                # 会把主终局改回响应违约）。
                _rej.append(rejection_record(len(_hist), _kind, _out.get("detail")))
                _hist[-1]["rejected"] = True
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in,
                           rejection=_rej[-1])
                self.log("[REVISION] 第 %d 轮响应被拒收（%s）——计划未改动、未写缓存，"
                         "把原因反馈给下一轮"
                         % (len(_hist), rejection_display(_hist[-1], limit=100)))
                continue
            if _kind == "no_change":
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in)
                return self._result(plan, STOP_NO_CHANGE, _problems, _hist, _rej)
            _new = _out.get("plan")
            if not isinstance(_new, dict):
                # 「说 applied 却没计划」也是**形态违约**（同一条拒收与反馈口径）：
                # 记成 `illegal` 而不是把整轮的 kind 留在 `applied` 上——否则额度耗尽时
                # `_stop_result` 会把它归成"额度用尽"（内容结论），归因反了。
                _hist[-1]["detail"] = "applied 却没给出计划：%s" % _hist[-1]["detail"]
                _hist[-1]["kind"] = "illegal"
                _rej.append(rejection_record(len(_hist), "illegal", _hist[-1]["detail"]))
                _hist[-1]["rejected"] = True
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in,
                           rejection=_rej[-1])
                continue
            _fp = self.fingerprint(_new)
            # 工作包 §4 C：**内容确实没变**（应用后与当前版本逐字相同）记 `no_change`；
            # **回到更早版本**才记 `loop`。两者都**已经扣过**本轮次数（`spend()` 在调用前）。
            if _fp == self.fingerprint(plan):
                self.log("[REVISION] 第 %d 轮应用后内容没变（指纹 %s…）——按 no_change 停止"
                         % (len(_hist), _fp[:10]))
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in, plan_out=_new)
                return self._result(plan, STOP_NO_CHANGE, _problems, _hist, _rej)
            if _fp in _seen:
                self.log("[REVISION] 第 %d 轮改回了已出现过的状态（指纹 %s…）——按循环停止"
                         % (len(_hist), _fp[:10]))
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in, plan_out=_new)
                return self._result(plan, STOP_LOOP, _problems, _hist, _rej)
            _seen.add(_fp)
            _prev_problems = _problems          # 返回的是基线计划 ⇒ 问题清单也要说基线
            _problems = list(self.accept(_new, novel_text) or [])
            _hist[-1]["problems_after"] = len(_problems)
            # **重验之后再核对一次**：重验期间过点 ⇒ 候选同样不许采纳
            # （"验完了"与"还能下发"是两件事：截止时刻是下发的必要条件）
            if self.budget.expired():
                _hist[-1]["detail"] = ("重验期间过截止时刻 ⇒ 候选丢弃（未采纳；"
                                       "候选自身还剩 %d 项问题）" % len(_problems))
                _hist[-1]["discarded_candidate"] = True
                self.log("[REVISION] 重验期间过截止时刻 ⇒ 丢弃候选、保留基线计划")
                self._emit(_hist[-1], plan_in=_plan_in, problems_in=_probs_in,
                           plan_out=_new, problems_out=_problems)
                return self._result(plan, STOP_DEADLINE, _prev_problems, _hist, _rej)
            _plan_out, _probs_out = _new, list(_problems)
            plan = _new
            _emit_rec = _hist[-1]
            _emit_kw = {"plan_in": _plan_in, "problems_in": _probs_in,
                        "plan_out": _plan_out, "problems_out": _probs_out}
            if not _problems:
                self.log("[REVISION] 第 %d 轮后验收通过（累计 %d 次调用）"
                         % (len(_hist), self.budget.calls_used))
                self._emit(_emit_rec, **_emit_kw)
                return self._result(plan, STOP_OK, [], _hist, _rej)
            # 候选被采纳但仍有问题 ⇒ 这一轮结束，证据先留档（下一轮是**新的一轮**）
            self._emit(_emit_rec, **_emit_kw)
