# -*- coding: utf-8 -*-
"""shared.content_verification —— **独立内容核验**的唯一责任与记录协议（R4 / 主专题 §3.3）.

## 为什么单独一个模块

确定性判据只能核**形状、引用、出处、时长和证据完整性**；"这段正文是否**完整表达**了
这条事实"是语义判断。R §2.3 的双向反例证明：拿**字符串重合**当完整语义的最终出口，
两个方向都会错——

- **误收**：事实「周明去年辞职」+ 证据「仓库管理员周明辞职。」，实际旁白只说
  「仓库管理员周明」，统一验收返回 0 问题（每分句只要任意连续 6 字重合）；
- **误拒**：证据「…辞职，仓库仍旧正常营业。」，旁白已经表达辞职，却因没复述
  营业情况被拒（事实主张、支持它的证据、引文**附带信息**没有分开）。

所以本模块把"完整支持"交给**独立核验**（模型调用由调用方注入），确定性一侧只做
**记录的形状与身份核验**：记录必须绑对计划摘要与规则版本、每个冻结事实恰好一项结果、
**支持片段必须逐字出自实际载体正文**（引用不能冒充已展示内容）。

## 三条内容结论（同一次核验里一起返回，不另开重试循环）

1. 原文 → **事实主张**（出处是否支持主张）
2. 原文 → **对白发言及说话人**（原文逐字命中 ≠ 原文人物发言）
3. 实际载体 → **完整事实**（正文是不是把主张表达全了）

## 调用上限

每个**结构合格的新计划摘要**最多核验一次；首版 + 3 次修订 ⇒ 最多 4 份。重复摘要复用
同身份、同规则版本记录。超时/坏响应记核验失败（`failed`），**不加隐藏重试**，共用既有
截止；核验失败**不当作内容可接受**。

`partial` / `contradicted` / `unknown` **不能**成为新模式的可执行计划。
"""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERIFICATION = "content_verification.v4"

VERDICT_SUPPORTED = "supported"
VERDICT_PARTIAL = "partial"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_UNKNOWN = "unknown"
VERDICTS = (VERDICT_SUPPORTED, VERDICT_PARTIAL, VERDICT_CONTRADICTED, VERDICT_UNKNOWN)

#: 可以判为**可执行**的结论只有这一个。
EXECUTABLE_VERDICTS = (VERDICT_SUPPORTED,)

#: 缺核验记录时的判据版本（计划摘要与它一起进缓存身份）。
#:
#: ⚠️ **v2 = 义务轴**（复核 2026-10-08 r2 P1「核验义务」）：记录多了一组**逐项义务**
#: （`facts[].obligations`），`supported` 必须覆盖该事实**全部**义务。
#: ⚠️ **v3 = 义务支座由代码定**（复核 2026-10-09 P1「核验方仍能缩减全部义务的集合」）：
#: 待核集合不再是核验方自报的，而是从**冻结主张**派生（`frozen_obligations`），记录须
#: 回带 `obligation_basis` 摘要并与之逐一对应。
#: ⚠️ **v4 = 联合证据**（复核 2026-10-09-r2「联合承载记录的表达缺口」）：义务行
#: **不再只能点名一个载体**——`carrier_uid`/`exhibitor_span` 两个单值字段换成
#: `evidence[]`（每项一个载体 + 一段逐字正文），于是"对白＋画面各表达一部分"这种
#: **联合承载**能被无损记录。旧规则的记录**不许复用**——版本变了就重新核验
#: （`seed` / 缓存读回 / 最终验收三处都按它判）。
DEFAULT_RULE_VERSION = "content_verification.v5.span_checkpoints.2"

#: 一次运行里最多核验几份计划摘要（首版 + 3 次修订）。
MAX_VERIFICATIONS_PER_RUN = 4

#: 核验失败的原因码（与"内容不支持"**分账**：失败是"没验成"，不是"验出来不行"）。
FAIL_CALL = "call_failed"
FAIL_BAD_RESPONSE = "bad_response"
FAIL_DEADLINE = "deadline_exhausted"
#: **输入准入失败**：交给核验的那份视图自己就不合法（容器/成员类型、身份冲突……）。
#: 与 `FAIL_CALL`（调了、没通）和 `FAIL_BAD_RESPONSE`（通了、答得不成形状）**分账**：
#: 那两类是"这次核验没成"，这一类是"这份输入不该被送去核验"——**一次模型调用都没发**。
#: 复核 2026-10-06 第十二轮 ① 的下游统一失败行为明文要求三者可分辨。
FAIL_INPUT = "input_admission_failed"

class DecompositionContractError(ValueError):
    """显式传入的分量表**不合法**（带 `problems`）——**不得静默退回根义务模式**.

    复核 2026-10-09-r4 P2："声明启用了分量但表坏了"与"没有启用分量"是两件事：
    前者必须结构化拒收（请求侧在 `create_service` **之前**拦下），后者才是合法旧模式。
    """

    def __init__(self, problems, where: str = ""):
        self.problems = list(problems or [])
        self.where = str(where or "")
        self.codes = [str((x or {}).get("code") or "") for x in self.problems]
        super().__init__("%s不合法：%s" % (("%s " % self.where) if self.where else "",
                                           "、".join(self.codes) or "（无记录）"))


class VerificationResponseError(ValueError):
    """核验响应**不满足记录契约**（带 `problems`）.

    与 `PresentationContractError`（输入没资格核）分开：这一条是"**通了、答得不成形状**"。
    控制器据此记 `FAIL_BAD_RESPONSE`——旧写法里它是一句 `ValueError`，被当成
    `FAIL_CALL`（"网络抖了一下"），两类失败在报告里长得一样。
    """

    def __init__(self, problems, where: str = ""):
        self.problems = list(problems or [])
        self.where = str(where or "")
        self.codes = [str((x or {}).get("code") or "") for x in self.problems]
        super().__init__("%s不满足记录契约：%s"
                         % (("%s " % self.where) if self.where else "",
                            "、".join(self.codes) or "（无记录）"))


#: `status` → 报告层用的 `code`（**由生产者盖章**，消费者只透传——不从文案猜身份）.
#: 四类失败各自可辨：输入没资格核 / 调了没通 / 通了答得不成形状 / 截止或额度耗尽。
FAILURE_CODES = {
    FAIL_INPUT: "verify_input_rejected",
    FAIL_CALL: "verify_call_failed",
    FAIL_BAD_RESPONSE: "verify_bad_response",
    FAIL_DEADLINE: "verify_deadline_exhausted",
}


def plan_digest_of(plan) -> str:
    """**被核验候选**的摘要（计划本体；排除核验记录自身与诊断元数据）.

    ⚠️ 这是**核验记录绑的那个身份**，生产与消费**共用这一个函数**（复核 2026-10-05 P1：
    原实现生产者按计划算、消费者按视图算，正常记录被判 `verify_digest_mismatch`）。
    要换身份口径就换这一个函数，不在两侧各算一遍。
    """
    if not isinstance(plan, dict):
        return ""
    _body = {k: v for k, v in plan.items() if k not in _NON_CONTENT_KEYS}
    _t = json.dumps(_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    default=str)
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


#: **不参与内容身份**的键：核验记录自身（否则记录一挂上身份就变，下一次核验永远对不上）
#: 与诊断元数据（它们的变化是"过程变了"，不是"内容变了"）。
_NON_CONTENT_KEYS = frozenset({"plan_verification", "verification_rejected",
                               "plan_revision", "plan_replan",
                               "plan_contract_version"})


def view_candidate(view, plan) -> dict:
    """视图 → **与它的投影逐字一致的候选计划**（核验与验收的对象只能是它）.

    这条是复核 P1 的根因所在：同一次修订里，视图被投影成计划后再挂核验记录，而记录
    绑的是**投影前**的视图摘要——于是"验收通过、返回候选却没有记录、最终真实验收又
    判缺记录"。现在规定**唯一**的候选形状：投影出来的计划。视图只是它的编辑面。
    """
    from shared.presentation_contract import plan_from_view
    return plan_from_view(view)


def rec_code(rec) -> str:
    return str((rec or {}).get("code") or "")


def _rec(code, kind, ref, reason, path="", fix=""):
    return {"code": code, "kind": kind, "ref": str(ref or ""),
            "uid": str(ref or ""), "path": path,
            "reason": reason, "fix": fix}


#: 核验记录的**字段契约**（**单源**）：三组结果各自的必填字段与类型.
#:
#: 复核 2026-10-07 三轮 P1：`evidence_spans=1` / `support_spans=1` /
#: `suggested_carriers=1` 都被记成 `status=ok`，随后在**统一验收**里抛 `TypeError`；
#: 结果组里夹的坏成员被 `isinstance(x, dict)` **过滤掉**（"少了一项"冒充"没问题"）；
#: `speaker=123` 一路通过。共同原因不是"漏了某个字段"，而是**没有一份记录契约**：
#: 生产者按形状拼 dict、控制器只判"是不是对象"、验收直接 `.get(...)` 后遍历。
#:
#: 所以契约只有这一份，**解析（生产者）、控制器装载、缓存读回、最终验收四处共用**：
#: 坏记录**整份结构化拒收**，不逐字段打补丁、不过滤坏成员、不做 `str()` 归一。
VERIFICATION_RECORD_CONTRACT = {
    "source_support": {"fact_uid": "str", "verdict": "str", "evidence_spans": "list[str]",
                       "reason": "str"},
    "facts": {"fact_uid": "str", "verdict": "str", "support_spans": "list[str]",
              "missing": "str", "suggested_carriers": "list[str]",
              "obligations": "list"},
    "voice_units": {"unit_uid": "str", "verdict": "str", "source_kind": "str",
                    "speaker": "str", "echo_text": "str", "reason": "str"},
}

#: **义务行**的字段契约（与上面**同源**：解析 / 控制器装载 / 缓存读回 / 最终验收共用）.
#:
#: 一条义务 = **冻结主张里必须被展示的某一部分**（主体 / 时间跨度 / 否定 / 条件 /
#: 因果……，**由核验方逐条说出**，不设关键词表、不按人物或时间词做特判）。
#: 记录要能看出"这一部分由**哪个载体**、**哪一段逐字正文**展示出来"——`supported`
#: 必须覆盖该事实的**全部**义务，"有一段被展示的正文"不等于"主张的每一部分都被展示"。
#:
#: `evidence` 是**证据项数组**：一条义务可以由**多个载体**共同展示（联合承载），
#: 每项各自绑定一个载体与一段**逐字出自该载体实际正文**的片段；`supported` 至少要有
#: 一项真实证据，`partial`/`unknown` 允许空表并必须在 `missing` 里写明缺什么。
#: 语义判断针对**所有证据的并集**，不要求单项自己表达整条义务。
OBLIGATION_RECORD_CONTRACT = {
    "obligation_id": "str", "text": "str", "verdict": "str",
    "evidence": "list", "missing": "str", "reason": "str",
}

#: **证据项**的字段契约（义务行 `evidence[]` 的成员）.
EVIDENCE_ITEM_CONTRACT = {"carrier_uid": "str", "exhibitor_span": "str"}

#: **义务支座**的派生口径版本：冻结主张 → 期望义务集合。口径改了必须换它
#: （记录绑的是它的摘要，支座一变旧记录不复用）。
OBLIGATION_BASIS_VERSION = "content_verification.basis.from_claim.v1"

#: 基线义务的**固定 id**（由代码给，不由核验方命名）：整条冻结主张作为一条义务。
CLAIM_OBLIGATION_ID = "claim"

#: 组内**嵌套成员表**：哪个组里的哪个字段是"对象数组"，那些对象按哪份契约判.
NESTED_RECORD_CONTRACT = {
    "facts": {"obligations": OBLIGATION_RECORD_CONTRACT},
    # 第二层：义务行里的 `evidence[]`（联合承载的证据项）
    "facts.obligations": {"evidence": EVIDENCE_ITEM_CONTRACT},
}

#: 三组结果（缺一组 = 那条轴没被回答）.
VERIFICATION_GROUPS = tuple(VERIFICATION_RECORD_CONTRACT)

#: 整组**缺失/不是数组**时用的 code：**按轴给身份**——报告与反馈都要能说清"是哪条轴
#: 没有结论"，而不是一句笼统的"记录形状不对"。逐项字段违约另有 `verify_record_*`。
GROUP_ABSENT_CODES = {"source_support": "verify_source_support_missing",
                      "facts": "verify_fact_missing",
                      "voice_units": "verify_voice_units_mismatch"}


def _shape_rec(code, path, reason, value=None, ref="") -> dict:
    _r = {"code": code, "kind": "verification", "ref": str(ref or ""),
          "uid": str(ref or ""), "path": path,
          "reason": reason, "fix": ""}
    if value is not None:
        _r["value"] = value
    return _r


def _shape_value_brief(value, limit: int = 40) -> str:
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"


def record_shape_problems(record, *, where: str = "核验记录") -> list:
    """记录**形状**是否可消费：容器 / 成员 / 必填字段 / 类型 / 结论取值.

    `status != "ok"` 的记录**只**核容器层（schema、status 类型）：失败记录不带内容结论，
    拿它的三组结果去判"缺字段"会把"这次没验成"误记成"答得不成形状"。

    ⚠️ **不 `str()` 归一、不筛成员**：`evidence_spans=1` 是违约（不是"一个片段"），
    `facts=[1, {...}]` 是违约（不是"少一项"）。每条记录带**字段路径 + 原值**。

    **结论取值与身份集合不在这里判**：`verdict` 是否合法由**轴层**判据给
    （`verify_source_verdict_invalid` / `verify_verdict_invalid`，消息更具体），
    身份集合（每个冻结事实/声音单位恰好一项）由 `verification_problems` 判——
    一条判据只在一处报，避免同一份坏记录冒出两套 code。

    `status` 拼错或缺失 ⇒ 只报这一条就停下（拿一份连状态都读不出来的记录去逐组判字段，
    只会得到一串噪音）。
    """
    _p: list = []
    if not isinstance(record, dict):
        return [_shape_rec("verify_record_not_object", "",
                           "%s 必须是对象（当前是 %s）"
                           % (where, type(record).__name__), record)]
    if record.get("schema") != SCHEMA_VERIFICATION:
        _p.append(_shape_rec("verify_schema_unknown", "schema",
                             "未知的核验记录 schema：%r（期望 %s）"
                             % (record.get("schema"), SCHEMA_VERIFICATION),
                             record.get("schema")))
    _status = record.get("status")
    if not isinstance(_status, str) or not _status.strip():
        # `status` 都读不出来 ⇒ 继续把三组症状一起报（同一份坏响应的其余症状），
        # 但不把它当"失败记录"（失败记录一定带 `status`，由控制器盖章）。
        _p.append(_shape_rec("verify_record_status_type", "status",
                             "核验记录缺 `status` 或它不是非空字符串（当前是 %s）"
                             % _shape_value_brief(_status), _status))
    elif _status != "ok":
        return _p
    from shared.presentation_contract import _type_ok
    _rid = str(record.get("plan_digest") or "")
    for _group, _fields in VERIFICATION_RECORD_CONTRACT.items():
        _items = record.get(_group)
        if not isinstance(_items, list):
            _p.append(_shape_rec(
                GROUP_ABSENT_CODES.get(_group, "verify_record_group_type"), _group,
                "核验记录的 `%s` %s（当前是 %s）——三条轴缺一条就是**没答那条轴**"
                % (_group, "缺失" if _group not in record else "必须是数组",
                   type(_items).__name__),
                _items if _group in record else None, _rid))
            continue
        for _i, _item in enumerate(_items):
            _at = "%s[%d]" % (_group, _i)
            if not isinstance(_item, dict):
                _p.append(_shape_rec("verify_record_item_not_object", _at,
                                     "%s 必须是对象（当前是 %s）——**不筛掉坏成员**"
                                     % (_at, type(_item).__name__), _item, _rid))
                continue
            for _f, _spec in _fields.items():
                if _f not in _item:
                    _p.append(_shape_rec("verify_record_field_missing",
                                         "%s.%s" % (_at, _f),
                                         "%s 缺必填字段 `%s`（要 %s）"
                                         % (_at, _f, _spec), None, _rid))
                elif not _type_ok(_item[_f], _spec):
                    _p.append(_shape_rec("verify_record_field_type",
                                         "%s.%s" % (_at, _f),
                                         "%s 的 `%s` 类型不对：要 %s，当前是 %s（%s）"
                                         "——不做字符串化、不按单项兜底"
                                         % (_at, _f, _spec,
                                            _shape_value_brief(_item[_f]),
                                            type(_item[_f]).__name__),
                                         _item[_f], _rid))
            # ---- 组内嵌套成员（义务行）：同一份契约、同一个出口逐项判 ----
            for _nested, _ncontract in (NESTED_RECORD_CONTRACT.get(_group) or {}).items():
                _members = _item.get(_nested)
                if not isinstance(_members, list):
                    # 缺字段 / 类型不对 **已经由上面那两处报过**，这里不重复报，
                    # 也不"过滤掉坏项继续走"（这里根本没有过滤，是没有可逐项判的内容）
                    continue
                for _j, _m in enumerate(_members):
                    _mat = "%s.%s[%d]" % (_at, _nested, _j)
                    if not isinstance(_m, dict):
                        _p.append(_shape_rec(
                            "verify_record_obligation_not_object", _mat,
                            "%s 必须是对象（当前是 %s）——**不筛掉坏成员**"
                            % (_mat, type(_m).__name__), _m, _rid))
                        continue
                    for _k, _kspec in _ncontract.items():
                        if _k not in _m:
                            _p.append(_shape_rec(
                                "verify_record_field_missing", "%s.%s" % (_mat, _k),
                                "%s 缺必填字段 `%s`（要 %s）——义务必须逐项回带"
                                "载体 UID / 展示片段 / 理由" % (_mat, _k, _kspec),
                                None, _rid))
                        elif not _type_ok(_m[_k], _kspec):
                            _p.append(_shape_rec(
                                "verify_record_field_type", "%s.%s" % (_mat, _k),
                                "%s 的 `%s` 类型不对：要 %s，当前是 %s（%s）"
                                % (_mat, _k, _kspec, _shape_value_brief(_m[_k]),
                                   type(_m[_k]).__name__), _m[_k], _rid))
                    for _k in sorted(set(_m) - set(_ncontract)):
                        _p.append(_shape_rec(
                            "verify_obligation_unknown_field", "%s.%s" % (_mat, _k),
                            "%s 有**未登记字段** `%s`（合法字段：%s）——未知义务不许"
                            "当成「多给的信息」收下" % (_mat, _k, list(_ncontract)),
                            _m[_k], _rid))
                    # ---- 第二层嵌套：义务行里的 `evidence[]`（联合承载的证据项）----
                    for _nk, _nkcontract in (NESTED_RECORD_CONTRACT.get(
                            "%s.%s" % (_group, _nested)) or {}).items():
                        _evs = _m.get(_nk)
                        if not isinstance(_evs, list):
                            # 缺字段 / 类型不对 **已由上面那两处报过**：这里不重复报，
                            # 也不"过滤掉坏项继续走"
                            continue
                        for _ei, _ev in enumerate(_evs):
                            _eat = "%s.%s[%d]" % (_mat, _nk, _ei)
                            if not isinstance(_ev, dict):
                                _p.append(_shape_rec(
                                    "verify_obligation_evidence_not_object", _eat,
                                    "%s 必须是对象（当前是 %s）——**不筛掉坏成员**；"
                                    "证据项要能点名载体与逐字片段"
                                    % (_eat, type(_ev).__name__), _ev, _rid))
                                continue
                            for _ek, _espec in _nkcontract.items():
                                if _ek not in _ev:
                                    _p.append(_shape_rec(
                                        "verify_record_field_missing",
                                        "%s.%s" % (_eat, _ek),
                                        "%s 缺必填字段 `%s`（要 %s）" % (_eat, _ek, _espec),
                                        None, _rid))
                                elif not _type_ok(_ev[_ek], _espec):
                                    _p.append(_shape_rec(
                                        "verify_record_field_type",
                                        "%s.%s" % (_eat, _ek),
                                        "%s 的 `%s` 类型不对：要 %s，当前是 %s（%s）"
                                        % (_eat, _ek, _espec,
                                           _shape_value_brief(_ev[_ek]),
                                           type(_ev[_ek]).__name__), _ev[_ek], _rid))
                            for _ek in sorted(set(_ev) - set(_nkcontract)):
                                _p.append(_shape_rec(
                                    "verify_obligation_evidence_unknown_field",
                                    "%s.%s" % (_eat, _ek),
                                    "%s 有**未登记字段** `%s`（合法字段：%s）"
                                    % (_eat, _ek, list(_nkcontract)), _ev[_ek], _rid))
    # **轴证据引用块**（复核 r6 §4.3）：**可选**——缺席是合法旧单任务记录，
    # 一旦出现就按登记契约逐项判（不认未知字段）。判据只有 `axes_problems` 一处。
    _p += axes_problems(record)
    if 'coherence' in record:
        from step01_script_gen.plan_coherence import shape_problems
        _p.extend(shape_problems(record['coherence']))
    if 'source_preservation' in record:
        from step01_script_gen.source_revision import shape_problems as preservation_shape
        _p.extend(preservation_shape(record['source_preservation']))
    return _p


#: 三个读取器都是**准入之后**的读法：调用方必须先跑 `record_shape_problems`（或经
#: 控制器 `seed`/`record_for` 装载），否则"成员不是对象"会被这里的 `isinstance`
#: 过滤成"少一项"——那正是复核 2026-10-07 三轮 ① 的形态。判断只在契约那一处。
def fact_results(record) -> list:
    return [x for x in ((record or {}).get("facts") or []) if isinstance(x, dict)]


def source_results(record) -> list:
    return [x for x in ((record or {}).get("source_support") or [])
            if isinstance(x, dict)]


def voice_results(record) -> list:
    return [x for x in ((record or {}).get("voice_units") or []) if isinstance(x, dict)]


def obligation_results(record, fact_uid) -> list:
    """某条事实的**义务行**（准入之后才读；口径同 `fact_results`）."""
    for _r in fact_results(record):
        if str(_r.get("fact_uid") or "") == str(fact_uid):
            return [x for x in (_r.get("obligations") or []) if isinstance(x, dict)]
    return []


def _dicts(value) -> list:
    """只取对象成员（**全函数**）。

    ⚠️ 这不是"过滤掉坏项继续走"——消费侧的准入（`view_admission_problems`）在
    `verify_view_with_service` / `build_verification_prompt` 的入口已经跑过，坏输入
    **根本到不了这里**。这一层只是保证"即使有人绕过边界也不会崩在半路"，判据仍然只有
    一处：**准入不过就不调用模型**。
    """
    return [x for x in (value if isinstance(value, list) else []) if isinstance(x, dict)]


def _texts_of(pool, uid) -> list:
    for _x in _dicts(pool):
        from shared.presentation_contract import ref_uid
        if ref_uid(_x) == uid:
            return [str(_x.get("text") or "")]
    return []


def carrier_texts(view, ref) -> list:
    """一条载体引用的**实际展示正文**——支持片段必须出自这里.

    ⚠️ **只有 `text`（观众看到的画面/卡片/台词）算数**（工作包 2 第 3 条）。原先这里
    把节拍的 `purpose`（「本镜信息目的」）也当成展示正文，于是核验模型可以拿一句**声明**
    冒充"画面里有这些信息"——那正是 R §2.3 的误收形态复现。`purpose` 是声明，观众在
    成片里看不到它。
    """
    from shared.presentation_contract import (KIND_CARD, KIND_DIALOGUE, KIND_NARRATION,
                                              KIND_VISUAL, ref_uid)
    if not isinstance(ref, dict):
        return []
    _kind = str(ref.get("kind") or "")
    _uid = ref_uid(ref)
    if _kind == KIND_VISUAL:
        return _texts_of((view or {}).get("beats"), _uid)
    _pool = {KIND_DIALOGUE: (view or {}).get("dialogue_units"),
             KIND_NARRATION: (view or {}).get("narration_units"),
             KIND_CARD: (view or {}).get("screen_cards")}.get(_kind) or []
    return _texts_of(_pool, _uid)


def all_carrier_texts(view, fact_uid) -> list:
    """某个事实的**全部载体正文**（联合承载时求并集）."""
    _out = []
    for _b in _dicts((view or {}).get("fact_bindings")):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in _dicts(_b.get("carriers")):
            _out.extend(carrier_texts(view, _c))
    return _out


def _norm(text) -> str:
    from step01_script_gen.plan_feasibility import _source_norm
    return _source_norm(text)


def span_is_exhibited(span, view, fact_uid) -> bool:
    """支持片段是否**逐字出自该事实的实际载体正文**（引用不能冒充已展示内容）.

    规范化后按子串判（标点/空白不参与，与全仓其余文本比对同一套口径）。
    """
    _s = _norm(span)
    if not _s:
        return False
    for _t in all_carrier_texts(view, fact_uid):
        if _s and _s in _norm(_t):
            return True
    return False


def _carrier_texts_by_uid(view, fact_uid, carrier_uid) -> list:
    """该事实的载体里，**uid 恰好等于 `carrier_uid`** 的那些的实际展示正文.

    义务行只点名一个 uid（没有 kind）——所以按"这条事实的载体集合"去解析，**不跨事实**
    找同名实体：别的实体的 uid 冒充展示出处就是**坏身份**。
    """
    from shared.presentation_contract import ref_uid
    _out = []
    for _b in _dicts((view or {}).get("fact_bindings")):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in _dicts(_b.get("carriers")):
            if ref_uid(_c) == str(carrier_uid):
                _out.extend(carrier_texts(view, _c))
    return _out


def frozen_obligations(view) -> dict:
    """**冻结主张 → 期望义务集合**（由代码派生，候选无关）.

    复核 2026-10-09 P1：旧口径让**核验方自己列**义务、验收只要求"至少一条 + 自洽"，
    于是把没被满足的义务删掉、只留一条能被展示的，整项照样 `supported`
    ——「完整主张被结果子集替代」。所以待核集合必须**独立于候选展示**地派生：

    - 输入只有**冻结事实**（`view.frozen_facts[].uid/.claim`）——不读载体、不读展示正文，
      因此"候选少演了什么"不可能反过来影响"该核哪几部分"；
    - **基线口径**：整条冻结主张作为**一条不可改写的义务**（id 固定
      `CLAIM_OBLIGATION_ID`）。少展示一部分就只能落到那一条的 `verdict` 上，删不掉；
    - **细分只在有独立冻结支座时加入**（例如主张自带一份被冻结的分量列表）。按候选
      少展示了什么反推要删哪条义务，正是本缺陷的形态，**不许**这么做。

    口径变了就换 `OBLIGATION_BASIS_VERSION`：记录绑它的摘要，支座一变旧记录不复用。
    """
    _out: dict = {}
    # ⚠️ **容器不对时不许遍历**（复核 r6 §4.5「坏类型」格）：旧写法 `view.get(...) or []`
    # 在 `frozen_facts=1` 时得到 `1`，`for _f in 1` 直接抛 `TypeError`——**准入自己崩了**，
    # 结构化拒收（容器类型问题）根本发不出来。低层访问器一律按"不合法就空表"处理，
    # 容器/成员的类型违约由准入层报（`view_admission_problems` / `record_shape_problems`）。
    _facts = view.get("frozen_facts")
    for _f in (_facts if isinstance(_facts, list) else []):
        if not isinstance(_f, dict):
            continue
        _u = str(_f.get("uid") or "")
        if not _u:
            continue
        _claim = str(_f.get("claim") or "").strip()
        _out[_u] = ([{"obligation_id": CLAIM_OBLIGATION_ID, "text": _claim}]
                    if _claim else [])
    return _out


#: **根义务** = 整条冻结主张，**不可删改**（分量只增加检查，不能替代它）。
ROOT_OBLIGATION_ID = CLAIM_OBLIGATION_ID

#: 分量分解的**规则版本**：进分解支座的键，规则变了旧表不复用。
DECOMPOSITION_RULE_VERSION = "content_verification.decomposition.v2"

#: 分量的**作用域**（封闭集合）。空值 / 未知取值一律拒收——不做「猜出来的作用域」。
COMPONENT_KINDS = ("subject", "action", "time", "negation", "condition",
                   "causality", "identity", "other")

#: 分量行的字段契约（`kind` / `relation` / `span`；`span` 必须**逐字落在冻结主张里**）。
COMPONENT_RECORD_CONTRACT = {"kind": "str", "relation": "str", "span": "str"}


def decomposition_basis_key(fact_uid, claim) -> str:
    """分解支座的键：**冻结事实 UID + 完整主张 + 分解规则版本**.

    ⚠️ **不含候选计划摘要**（复核 r3 §5②）：同一个冻结事实基座在首版 / 各候选 /
    缓存读回复用**同一张表**；展示改动**不重新分解**，只有冻结主张或规则合法变更才失效。
    """
    _t = json.dumps({"v": DECOMPOSITION_RULE_VERSION, "uid": str(fact_uid or ""),
                     "claim": _norm(claim)}, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"))
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


def component_problems(fact_uid, claim, components) -> list:
    """分量表的**结构与文字覆盖**判据（只证明结构与文字覆盖，**不证明语义完整**）.

    复核 r3 §5② 的保守实验契约：

    - 容器 / 成员 / 必填字段 / 类型 / 作用域取值 / **未登记字段**逐项判，不筛坏成员；
    - `span` 必须**逐字（归一后）落在冻结主张里**——"范围确实指向冻结文本"；
    - 分量 id **由代码派生**（`c1…cn`，按给定顺序），模型不维护编号；
    - **重复 / 冲突**：同一段文字被拆两次，拒收；
    - **文字覆盖**：所有分量的 `span` 并集必须**盖住整条主张的文字**——漏掉的那段
      会被点名报出来。**这一条是防止"模型漏拆某个限定关系、把遗漏冻结成新标准"**：
      漏掉的部分不是"没被检查"，而是**整张表不合法**。
    """
    _p: list = []
    if not isinstance(components, list):
        return [_rec("verify_decomposition_container", "verification", str(fact_uid),
                     "分量表必须是数组（当前是 %s）" % type(components).__name__)]
    _claim_n = _norm(claim)
    if not _claim_n:
        return [_rec("verify_decomposition_claim_blank", "verification", str(fact_uid),
                     "冻结主张为空，分解没有支座")]
    _seen_spans: list = []
    for _i, _c in enumerate(components):
        _at = "components[%d]" % _i
        if not isinstance(_c, dict):
            _p.append(_rec("verify_decomposition_member_not_object", "verification",
                           str(fact_uid), "%s 必须是对象（当前是 %s）"
                           % (_at, type(_c).__name__)))
            continue
        for _k, _spec in COMPONENT_RECORD_CONTRACT.items():
            if _k not in _c:
                _p.append(_rec("verify_decomposition_field_missing", "verification",
                               str(fact_uid), "%s 缺必填字段 `%s`（要 %s）"
                               % (_at, _k, _spec)))
            elif not isinstance(_c[_k], str):
                _p.append(_rec("verify_decomposition_field_type", "verification",
                               str(fact_uid), "%s 的 `%s` 类型不对：要字符串（当前 %s）"
                               % (_at, _k, type(_c[_k]).__name__)))
        for _k in sorted(set(_c) - set(COMPONENT_RECORD_CONTRACT)):
            _p.append(_rec("verify_decomposition_unknown_field", "verification",
                           str(fact_uid), "%s 有**未登记字段** `%s`（合法字段：%s）"
                           % (_at, _k, list(COMPONENT_RECORD_CONTRACT))))
        _kind = str(_c.get("kind") or "")
        if _kind not in COMPONENT_KINDS:
            _p.append(_rec("verify_decomposition_kind_invalid", "verification",
                           str(fact_uid), "%s 的作用域 %r 不在 %s 之内"
                           % (_at, _kind, list(COMPONENT_KINDS))))
        if not str(_c.get("relation") or "").strip():
            _p.append(_rec("verify_decomposition_relation_blank", "verification",
                           str(fact_uid), "%s 没写出它主张的是**哪一层关系**（`relation` 空白）"
                           % _at))
        _span = str(_c.get("span") or "")
        _sn = _norm(_span)
        if not _sn:
            _p.append(_rec("verify_decomposition_span_blank", "verification",
                           str(fact_uid), "%s 的 `span` 空白——分量必须指向主张里的一段文字"
                           % _at))
        elif _sn not in _claim_n:
            _p.append(_rec("verify_decomposition_span_not_in_claim", "verification",
                           str(fact_uid), "%s 的 `span` 不在**冻结主张**里（范围必须指向"
                           "冻结文本）：%r" % (_at, _span[:24])))
        elif _sn in _seen_spans:
            _p.append(_rec("verify_decomposition_duplicate", "verification",
                           str(fact_uid), "%s 与前面某条分量指向**同一段文字**（%r）"
                           % (_at, _span[:24])))
        else:
            _seen_spans.append(_sn)
    if _p:
        return _p
    _mask = [False] * len(_claim_n)
    for _sn in _seen_spans:
        _from = 0
        while True:
            _k = _claim_n.find(_sn, _from)
            if _k < 0:
                break
            for _x in range(_k, _k + len(_sn)):
                _mask[_x] = True
            _from = _k + 1
    _miss = [_claim_n[_i] for _i, _ok in enumerate(_mask) if not _ok]
    if _miss:
        _p.append(_rec(
            "verify_decomposition_gap", "verification", str(fact_uid),
            "分量表的文字覆盖不全：主张里有 %d 个字没有任何分量指向（漏掉的是 %r）"
            "——**漏拆不等于没被检查**：整张表因此不合法，不许悄悄少核一部分"
            % (len(_miss), "".join(_miss)[:40])))
    return _p


def component_obligations(fact_uid, components) -> list:
    """分量 → 义务行骨架（**id 由代码派生**：`c1…cn`，按给定顺序）.

    ⚠️ **待核文字直接取已准入的冻结 `span`，而不是模型的概括标签**（复核 2026-10-09-r4 P1）：
    实测 `span` 是「三年内使杨家跻身青州十大豪门」，而 `relation` 是「使家族跻身豪门」
    ——按 `relation` 编译等于**在编译时把限定关系丢掉**，实验问的不是计划要问的内容。
    `relation` 与 `kind` 保留为**说明与作用域**，参与留档，不替换待核内容。
    """
    return [{"obligation_id": "c%d" % (_i + 1),
             "text": str(_c.get("span") or ""),
             "kind": str(_c.get("kind") or ""),
             "relation": str(_c.get("relation") or ""),
             "source_span": str(_c.get("span") or "")}
            for _i, _c in enumerate(components or [])]


def _basis_digest_from(rows) -> str:
    """`{fact_uid: [(obligation_id, text), …]}` → 支座摘要（**唯一一份公式**）.

    文本按 `_norm` **归一**后参与：验收判"义务文本有没有被改写"用的也是归一口径，
    两侧必须同一把尺子——否则模型只是标点/空白不同，装载就会把一份**合法**记录判成
    "支座不符"。
    """
    _t = json.dumps(
        {"version": OBLIGATION_BASIS_VERSION,
         "facts": {_k: [[str(_i), _norm(_x)] for _i, _x in _v]
                   for _k, _v in sorted((rows or {}).items())}},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


#: 计划里存放核验上下文的键（随 `plan_basis` 保真透传；**不在模型可改字段里**）。
VERIFICATION_CONTEXT_KEY = "verification_context"

#: 核验上下文的 schema 名（写在计划里，随 `plan_basis` 保真透传）。
CONTEXT_SCHEMA = "plan_verification.context.v1"
MODE_ROOT = "root"                 # 旧根策略：没有启用分量
MODE_COMPONENTS = "components"     # 分量策略：必须带**已准入**的分解表

#: 上下文的字段契约（**本地生成、模型不可修改**；缺字段/类型不符一律拒收）。
CONTEXT_CONTRACT = {"schema": "str", "mode": "str", "fact_basis_digest": "str",
                    "decomposition_rule": "str", "decomposition_digest": "str",
                    "verification_rule": "str", "models": "dict", "context_digest": "str"}


def _context_body(ctx) -> dict:
    """上下文里**参与摘要**的字段（排除 `context_digest` 自身与 `decomposition` 本体——
    后者由 `decomposition_digest` 代表，避免同一份内容算两遍）。"""
    body = {_k: ctx.get(_k) for _k in ("schema", "mode", "fact_basis_digest",
                                       "decomposition_rule", "decomposition_digest",
                                       "verification_rule", "models")}
    if "display_protocol" in ctx:
        body["display_protocol"] = ctx["display_protocol"]
    if "temporal_requirements" in ctx:
        body["temporal_requirements"] = ctx["temporal_requirements"]
    return body


def context_digest(ctx) -> str:
    """上下文的规范摘要（**由代码重算**；模型或缓存自报不作证明）."""
    _t = json.dumps(_context_body(ctx or {}), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str)
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


#: 表不合法时的规范摘要占位（**不是摘要**，只是不让摘要函数抛异常）.
#: 复核 2026-10-09-r7 P1-B：表准入错误**必须先返回**，`context_problems` 不再往下算摘要；
#: 这里只是最后一道保险——`1` / `[]` / `{'f01': 1}` 这类表走到这儿也不抛。
_DECOMPOSITION_CANON_INVALID = "<invalid-decomposition-table>"


def _decomposition_canon(table) -> str:
    """分解表的规范摘要输入（`None` ⇒ 空串；**非法表 ⇒ 占位**，不抛 `TypeError`）."""
    if table is None:
        return ""
    if not isinstance(table, dict):
        return _DECOMPOSITION_CANON_INVALID
    try:
        _t = json.dumps({str(_k): [[str(_c.get("kind")), str(_c.get("relation")),
                                    str(_c.get("span"))] for _c in _v]
                         for _k, _v in sorted(table.items())},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        default=str)
    except (TypeError, AttributeError):
        return _DECOMPOSITION_CANON_INVALID
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


#: 两轴策略身份：上下文里必须逐轴登记**实际用于该轴的模型名**.
#: 复核 2026-10-09-r7 P1-B：旧写法只判 `models` 是不是字符串就放行，
#: `1` / `[]` / `{"source_voice": "", "display": "   "}` 全被当合法策略接受。
AXIS_MODEL_KEYS = ("source_voice", "display")


def _models_admission_problems(models) -> list:
    """`models` 的**容器 / 成员 / 空白**准入（上下文与构建函数**共用这一份**）.

    只判"是不是逐轴写了非空模型名"，**不猜默认模型**：猜出来的身份等于没有身份，
    事后无法从留档复现"这次到底用了哪个模型"。
    """
    if not isinstance(models, dict):
        return [_rec("verify_context_models_container", "verification", "",
                     "`models` 必须是对象（当前 %s）——两轴策略身份要逐轴登记模型名"
                     % type(models).__name__, "verification_context.models")]
    _p: list = []
    for _axis in AXIS_MODEL_KEYS:
        if _axis not in models:
            _p.append(_rec("verify_context_models_missing", "verification", _axis,
                           "`models` 缺 `%s` 轴（两轴策略身份必须逐轴登记）" % _axis,
                           "verification_context.models.%s" % _axis))
        elif not isinstance(models.get(_axis), str) or not models.get(_axis).strip():
            _p.append(_rec("verify_context_models_blank", "verification", _axis,
                           "`models.%s` 不是非空字符串（当前 %r）——"
                           "**不替调用方猜模型**" % (_axis, models.get(_axis)),
                           "verification_context.models.%s" % _axis))
    return _p


def _fact_basis_digest(view) -> str:
    """冻结主张基座（uid + 完整主张）的摘要——**分量/根模式共用**的身份的一半."""
    _facts = view.get("frozen_facts") if isinstance(view, dict) else None
    return _basis_digest_from(
        {str(_f.get("uid") or ""): [(CLAIM_OBLIGATION_ID, str(_f.get("claim") or ""))]
         for _f in (_facts if isinstance(_facts, list) else []) if isinstance(_f, dict)})


def build_verification_context(view, *, decomposition=None, models=None,
                               display_protocol=None, temporal_requirements=None) -> dict:
    """**本地生成**一份核验上下文（模型不能改它；写进计划的 `verification_context`）.

    `decomposition=None` ⇒ `mode=root`（合法旧策略，没有启用分量）；给了表 ⇒
    `mode=components`，**表必须已过准入**（空/缺/未知事实/文字缺口都在这里被拒）。
    上下文**不含候选摘要**（避免摘要循环）；展示改动只换候选/展示身份，
    冻结事实相同时复用同一张表与同一份上下文。

    ⚠️ **构建前先跑同一份准入**（复核 r7 P1-B）：旧写法把坏表直接塞进上下文、
    只靠 `_decomposition_canon` 字符串化——于是"造得出来"被当成了"内容合法"。
    现在表与 `models` 都走**与读取侧同一份**判据，坏输入抛结构化契约错误
    （带 `problems`），不由构建函数替调用方编一份看着合法的身份。
    """
    _bad = decomposition_problems(view, decomposition)
    _bad += _models_admission_problems(models)
    from shared.carrier_entailment import PROTOCOL, CLASSIC
    from shared.quantified_verification import PROTOCOL as QUANTIFIED, requirements_problems
    if display_protocol is not None and display_protocol not in (PROTOCOL, CLASSIC, QUANTIFIED):
        _bad.append(_rec("verify_context_protocol", "verification", "",
                         "未知展示核验协议", "verification_context.display_protocol"))
    if display_protocol == QUANTIFIED:
        _bad += requirements_problems(temporal_requirements, view)
    elif temporal_requirements is not None:
        _bad.append(_rec("verify_context_temporal_policy", "verification", "", "时间边表须选中定量协议"))
    if _bad:
        raise DecompositionContractError(_bad, where="核验上下文的构建")
    _ctx = {"schema": CONTEXT_SCHEMA,
            "mode": MODE_ROOT if decomposition is None else MODE_COMPONENTS,
            "fact_basis_digest": _fact_basis_digest(view),
            "decomposition_rule": DECOMPOSITION_RULE_VERSION,
            "decomposition": decomposition,
            "decomposition_digest": _decomposition_canon(decomposition),
            "verification_rule": DEFAULT_RULE_VERSION,
            "models": dict(models or {})}
    if display_protocol is not None:
        _ctx["display_protocol"] = display_protocol
    if display_protocol == QUANTIFIED:
        _ctx["temporal_requirements"] = temporal_requirements
    _ctx["context_digest"] = context_digest(_ctx)
    return _ctx




def context_problems(view, ctx) -> list:
    """上下文准入：字段/类型/空白 → 表与**本次冻结事实精确集合** → 摘要由代码重算.

    任何类型都不抛 `TypeError`；判不了就返回结构化问题。`mode=root` 时分解表必须
    明确缺席/为 None；`mode=components` 时表必须**完整有效**——**不许退回根模式**。
    """
    _p: list = []
    if not isinstance(ctx, dict):
        return [_rec("verify_context_missing", "verification", "",
                     "计划上没有核验上下文（或缺它不是对象）——"
                     "分量策略必须先把上下文挂上再算候选身份", "verification_context")]
    if str(ctx.get("schema") or "") != CONTEXT_SCHEMA:
        _p.append(_rec("verify_context_schema", "verification", "",
                       "核验上下文 schema 不是 %s（当前 %r）"
                       % (CONTEXT_SCHEMA, ctx.get("schema")), "verification_context"))
    for _k, _spec in CONTEXT_CONTRACT.items():
        if _k not in ctx:
            _p.append(_rec("verify_context_field_missing", "verification", "",
                           "核验上下文缺字段 `%s`（要 %s）" % (_k, _spec),
                           "verification_context.%s" % _k))
        elif _spec != "dict" and not isinstance(ctx.get(_k), str):
            _p.append(_rec("verify_context_field_type", "verification", "",
                           "`%s` 类型不对：要 %s（当前 %s）"
                           % (_k, _spec, type(ctx.get(_k)).__name__),
                           "verification_context.%s" % _k))
        # `decomposition_digest` 在 **root 模式**下合法为空（那里没有表）；
        # 它在 components 模式下由 `_decomposition_canon` 逐值核对（见下）。
        elif (_k not in ("models", "decomposition_digest") and not str(ctx.get(_k) or "").strip()):
            _p.append(_rec("verify_context_field_blank", "verification", "",
                           "`%s` 是空白" % _k, "verification_context.%s" % _k))
    # `models` 是**两轴策略身份**，不是普通字符串字段：`_spec == "dict"` 让上面的
    # 类型/空白分支整个跳过它，于是旧写法对 `1` / `[]` 都"没有意见"。容器、逐轴成员、
    # 非空字符串三项用与构建函数**同一份**判据（复核 r7 P1-B）。
    _p += _models_admission_problems(ctx.get("models"))
    from shared.carrier_entailment import PROTOCOL, CLASSIC
    from shared.quantified_verification import PROTOCOL as QUANTIFIED, requirements_problems
    if "display_protocol" in ctx and ctx["display_protocol"] not in (PROTOCOL, CLASSIC, QUANTIFIED):
        _p.append(_rec("verify_context_protocol", "verification", "",
                       "未知展示核验协议", "verification_context.display_protocol"))
    if ctx.get("display_protocol") == QUANTIFIED:
        _p += requirements_problems(ctx.get("temporal_requirements"), view)
    elif "temporal_requirements" in ctx:
        _p.append(_rec("verify_context_temporal_policy", "verification", "", "未选定量协议却带时间边表"))
    if _p:
        return _p
    _mode = str(ctx.get("mode") or "")
    if _mode not in (MODE_ROOT, MODE_COMPONENTS):
        return [_rec("verify_context_mode", "verification", "",
                     "未知的 mode %r（合法：%s）" % (_mode, [MODE_ROOT, MODE_COMPONENTS]),
                     "verification_context.mode")]
    if str(ctx.get("fact_basis_digest") or "") != _fact_basis_digest(view):
        _p.append(_rec("verify_context_fact_basis", "verification", "",
                       "上下文绑的冻结主张基座与本次事实不一致——上下文属**另一份冻结事实**",
                       "verification_context.fact_basis_digest"))
    if str(ctx.get("verification_rule") or "") != DEFAULT_RULE_VERSION:
        _p.append(_rec("verify_context_rule", "verification", "",
                       "上下文的核验规则版本 %r 与当前 %r 不一致"
                       % (ctx.get("verification_rule"), DEFAULT_RULE_VERSION),
                       "verification_context.verification_rule"))
    if str(ctx.get("decomposition_rule") or "") != DECOMPOSITION_RULE_VERSION:
        _p.append(_rec("verify_context_rule", "verification", "",
                       "上下文的分解规则版本 %r 与当前 %r 不一致"
                       % (ctx.get("decomposition_rule"), DECOMPOSITION_RULE_VERSION),
                       "verification_context.decomposition_rule"))
    if str(ctx.get("context_digest") or "") != context_digest(ctx):
        _p.append(_rec("verify_context_digest", "verification", "",
                       "上下文摘要与内容不符（模型/缓存自报不作证明）",
                       "verification_context.context_digest"))
    _tbl = ctx.get("decomposition")
    if _mode == MODE_ROOT:
        if _tbl not in (None, {}):
            _p.append(_rec("verify_context_mode_mismatch", "verification", "",
                           "mode=root 却带了分解表", "verification_context.decomposition"))
    else:
        # **声明了分量就必须给表**（复核 r7 P1-B）：旧写法对 `decomposition=None`
        # 只走 `decomposition_problems(None)` ⇒ `[]`，再拿 `_decomposition_canon(None)`
        # 去比 `decomposition_digest`——"components 却没有表"于是可能整条通过。
        if _tbl is None or (isinstance(_tbl, dict) and not _tbl):
            return [_rec("verify_context_mode_mismatch", "verification", "",
                         "mode=components 却没有分量表（当前 %r）——"
                         "**声明了分量就必须给表**，缺表不许退回根模式" % (_tbl,),
                         "verification_context.decomposition")]
        _p += decomposition_problems(view, _tbl)
        # **表准入错误一律先返回**（复核 r7 P1-B）：表已判不合法还要往下核"它的摘要"
        # 只是在给一份已知坏表盖章，且会掩盖上一条缺表拒收。
        if _p:
            return _p
        if str(ctx.get("decomposition_digest") or "") != _decomposition_canon(_tbl):
            _p.append(_rec("verify_context_decomposition_digest", "verification", "",
                           "分解表摘要与表内容不符",
                           "verification_context.decomposition_digest"))
    return _p


def context_decomposition(view, ctx) -> dict:
    """从**已准入**的上下文取分解表（`mode=root` ⇒ `None`）；上下文不合法 ⇒ 抛.

    这是「唯一支座判据」的取表入口：请求、控制器、缓存、终检都从这里拿表，
    **不在缺表时各自回退根模式**。
    """
    _bad = context_problems(view, ctx)
    if _bad:
        raise DecompositionContractError(_bad, where="核验上下文")
    return None if str(ctx.get("mode")) == MODE_ROOT else ctx.get("decomposition")


def context_source_of(obj) -> dict:
    """从**计划或视图**里取核验上下文（同一个键，两处同一份身份）.

    计划在顶层带 `verification_context`；视图把它连同其余非关系字段放在 `plan_basis`
    里（投影时原样写回计划）。两处**只读这一个键**，不另立"视图专用"的存储。

    ⚠️ **按"键是否存在"取值，不按"值是不是 None"**（复核 r7 P1-B）：旧写法
    `obj.get(KEY) is not None` 把显式 `null` 读成了**字段缺席** ⇒ 一份被写成
    "分量上下文但表丢了"的稿子会退化成合法旧稿，按根义务算一遍并通过。
    显式 `null` 是一种**存在但坏**的形态，交 `context_problems` 拒收。

    键在两处都不存在 ⇒ 返回 `None`（真正的旧稿，合法根策略，唯一允许回退的形态）。
    """
    if not isinstance(obj, dict):
        return None
    if VERIFICATION_CONTEXT_KEY in obj:
        return obj.get(VERIFICATION_CONTEXT_KEY)
    _pb = obj.get("plan_basis")
    if isinstance(_pb, dict) and VERIFICATION_CONTEXT_KEY in _pb:
        return _pb.get(VERIFICATION_CONTEXT_KEY)
    return None


def verification_context_of(plan_or_view, view=None) -> tuple:
    """**唯一一份**上下文解析 → `(problems, decomposition)`；四个入口共用.

    复核 r6 §4.2 要求「支座判据贯穿四处」。旧写法在每个入口各挑一次上下文
    （`record_basis_matches` 读了、`seed` 的视图分支与 `record_for` 没读），于是
    同一份计划在缓存侧按分量算、在控制器侧按根义务算，两份都"通过"。

    三态：

    - **字段缺席** ⇒ `([], None)`：合法旧稿/根策略。这是**唯一**允许退回根模式的形态；
    - **有但准入不过** ⇒ `(problems, None)`：fail-closed。调用方按"证明不了"处理，
      **不许**把它当"旧稿缺字段"再按根模式算一遍（那正是"读一次根策略通过就回退"）；
    - **有且合法** ⇒ `([], table)`；`mode=root` 的 table 为 `None`。

    `view` 不给时用 `plan_or_view` 判（计划本身就是视图投影的来源）。
    """
    _view = view if isinstance(view, dict) else plan_or_view
    _src, _found = None, False
    if isinstance(plan_or_view, dict):
        if VERIFICATION_CONTEXT_KEY in plan_or_view:
            _src, _found = plan_or_view.get(VERIFICATION_CONTEXT_KEY), True
        else:
            _pb = plan_or_view.get("plan_basis")
            if isinstance(_pb, dict) and VERIFICATION_CONTEXT_KEY in _pb:
                _src, _found = _pb.get(VERIFICATION_CONTEXT_KEY), True
    if not _found:
        return [], None                            # 字段**真的缺席**（唯一允许退回根模式的形态）
    _bad = context_problems(_view, _src)           # 显式 `null` / 非对象 ⇒ 坏上下文，fail-closed
    if _bad:
        return _bad, None
    _mode = str((_src or {}).get("mode") or "")
    return [], (None if _mode == MODE_ROOT else (_src or {}).get("decomposition"))


def decomposition_problems(view, table) -> list:
    """**显式传入**的分量表在消费边界的准入（`None` = 旧根义务模式，合法）.

    复核 2026-10-09-r4 P2：解析器核过**模型首次返回**的那张表，不等于每个外部 / 缓存 /
    手工传入的表都可信——实测 `1` / `{}` / `{'f01': []}` / 缺文字覆盖的表都会一路走到
    `create_service`（`{'f01': 1}` 还抛 `TypeError`）。

    判据：容器 → **事实精确集合**（不许缺、不许未知）→ 逐成员契约与文字覆盖
    （`component_problems`）。**任何类型都不抛 `TypeError`**：判不了就返回结构化问题。
    """
    if table is None:
        return []                                   # 明确表示"没有启用分量"，合法
    # 2026-10（裁决 B：统一"空表"口径）：`build_verification_context` 那处把 `None` 与
    # `{}` 都当缺席，却把 `[]` 当"声明了分量"——同一个"空表"在两处结论相反。实测后果：
    # 计划没有分量区间时 `view_from_plan` 返回 `[]`，于是**任何核验记录都通不过**
    # （`verify_decomposition_container`），核验恒被判"记录违约"，最终
    # "核验未完成 ⇒ 零规划调用、保留候选"——核验功能对这类计划等于不可用。
    # 现统一为：**空容器 = 未启用分量**（合法）；只有「**非空**且非 dict」才是形状违约。
    # 实质约束不变："声明了分量就必须给完整表"——那种情况拿到的是**非空** dict，
    # 仍要走下面的逐事实/逐成员契约与文字覆盖。
    if hasattr(table, "__len__") and len(table) == 0:
        return []
    if not isinstance(table, dict):
        return [_rec("verify_decomposition_container", "verification", "",
                     "显式传入的分量表必须是**非空对象**（当前是 %s）——"
                     "非空却不是对象，不能当成「没有分量」退回根义务模式"
                     % type(table).__name__, "decomposition")]
    _want = {str(_f.get("uid") or ""): str(_f.get("claim") or "")
             for _f in (view.get("frozen_facts") or []) if isinstance(_f, dict)}
    _p: list = []
    _extra = sorted(set(table) - set(_want))
    if _extra:
        _p.append(_rec("verify_decomposition_fact_unknown", "verification", _extra[0],
                       "分量表里有**不是本次冻结事实**的 uid：%s" % _extra,
                       "decomposition"))
    _miss = sorted(set(_want) - set(table))
    if _miss:
        _p.append(_rec("verify_decomposition_fact_missing", "verification", _miss[0],
                       "这些冻结事实没有分量表：%s（显式传表必须**逐个事实**都给）"
                       % _miss, "decomposition"))
    for _fu in sorted(set(table) & set(_want)):
        _p += component_problems(_fu, _want[_fu], table[_fu])
    return _p


def expected_obligations(view, decomposition=None) -> dict:
    """冻结事实 → **期望义务集合**：**根义务（完整主张）+ 有效分量**.

    复核 r3 §5②：**根义务永远在里面**——分量只增加检查，不能替代/删掉它，
    也不能降低原标准。"模型漏拆某个限定关系"因此不会被冻结成新的（更松的）标准：
    漏掉的那段文字会让整张分量表在 `component_problems` 里判不合法。

    `decomposition` 形如 `{fact_uid: [分量…]}`（须已过 `component_problems`）；
    没有表 ⇒ 只有根义务（现状口径，向后兼容）。
    """
    _out = frozen_obligations(view)
    if not isinstance(decomposition, dict):
        return _out              # 没有启用分量 ⇒ 只有根义务（合法旧模式）
    # ⚠️ **这里只负责"按已给的项把检查项编出来"**；"表该不该收"由**消费边界**判：
    # `decomposition_problems` 在请求侧（`create_service` 之前）、装载/缓存、展示轴与
    # 统一验收四处各跑一次。低层构造器**不做准入**——否则一处分派漏了就静默退回
    # 根义务模式（那正是 r4 P2 要禁止的形态）。
    for _fu, _rows in list(_out.items()):
        _comps = decomposition.get(_fu)
        if not isinstance(_comps, list) or not _comps:
            continue
        # 分量行在**输入快照**里带上作用域与原片段：展示身份摘要（`display_input_digest`）
        # 因此**同时绑定检查文字与作用域**（复核 r4 P1 的要求）；支座摘要仍只按
        # (id, 文字) 算——记录里没有这两个键，两边口径不同但各自自洽。
        _out[_fu] = list(_rows) + [{"obligation_id": _c["obligation_id"],
                                    "text": _c["text"],
                                    "kind": _c.get("kind"),
                                    "source_span": _c.get("source_span")}
                                   for _c in component_obligations(_fu, _comps)]
    return _out


def obligation_basis_digest(view, decomposition=None) -> str:
    """义务支座的摘要（**冻结主张 → 期望义务集合**）：记录与它绑定.

    与 `plan_digest_of` 同源同理——摘要变了就是"待核的东西变了"，旧记录不许再用
    （缓存读回、控制器装载、统一验收三处共用这一个口径）。
    """
    _b = expected_obligations(view, decomposition)
    return _basis_digest_from({_k: [(_o["obligation_id"], _o["text"]) for _o in _v]
                               for _k, _v in _b.items()})


#: 展示任务**输入快照**的 schema 名（与响应 schema 分开：这一份记的是「送进去的是什么」）。
DISPLAY_TASK_SCHEMA = "content_verification.display_task.v1"


def display_carriers(view, fact_uid) -> list:
    """该事实**当前绑定载体**的实际正文（`kind`/`uid`/`text`）——展示任务的全部材料.

    ⚠️ **不含 `purpose`（声明）**：观众看不到它，混进来就等于允许"用声明冒充展示"。
    """
    from shared.presentation_contract import ref_uid
    _out = []
    for _b in (view.get("fact_bindings") or []):
        if not isinstance(_b, dict) or str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in (_b.get("carriers") or []):
            if not isinstance(_c, dict):
                continue
            _k = str(_c.get("kind") or "")
            for _t in carrier_texts(view, _c):
                _out.append({"kind": _k, "uid": str(ref_uid(_c)), "text": str(_t)})
    return _out


def display_task_input(view, decomposition=None) -> dict:
    """展示任务的**输入单元**：冻结 uid + 给定义务 + 各载体实际正文（**可审计的快照**）.

    返回的对象就是"实际送去判的东西"——校准入口把它与请求正文一起留档，并断言
    `DISPLAY_FORBIDDEN_KEYS` 一个都没进去。
    """
    _want = expected_obligations(view, decomposition)
    _units = []
    for _f in (view.get("frozen_facts") or []):
        if not isinstance(_f, dict):
            continue
        _fu = str(_f.get("uid") or "")
        if not _fu:
            continue
        _units.append({
            "fact_uid": _fu,
            "claim": str(_f.get("claim") or ""),
            "obligations": [dict(_o) for _o in (_want.get(_fu) or [])],
            "carriers": display_carriers(view, _fu),
        })
    _task = {"schema": DISPLAY_TASK_SCHEMA, "facts": _units}
    from shared.quantified_verification import PROTOCOL as QUANTIFIED, table_of
    from shared.carrier_entailment import protocol_of
    if protocol_of(view) == QUANTIFIED:
        _task["display_protocol"] = QUANTIFIED
        _task["temporal_requirements"] = table_of(view)
    return _task


def display_input_digest(view, decomposition=None) -> str:
    """展示任务的**候选展示身份**：输入快照（含各载体实际正文）的摘要.

    与支座摘要（`obligation_basis_digest`）**分账**：支座管"该核哪几条"，这一份管
    ""看到的是哪一段正文"。展示正文变了 ⇒ 旧结论不复用。
    """
    _t = json.dumps(display_task_input(view, decomposition), ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"))
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()
def record_basis_self_consistent(record) -> bool:
    """记录自报的支座是否等于**它自己那套义务行**算出来的摘要（**装载入口**用）.

    装载（`seed`）在拿不到本次视图时要判"这个支座字段是不是随便填的"：一个全零或
    借来的摘要都能冒充"支座对得上"。所以最低要求是**自洽**——自报值必须与记录内容
    算出来的值相同。真正的"与**本次**冻结主张一致"由 `record_basis_matches(record, plan)`
    与统一验收判（那一层有视图）。两条都不过 ⇒ 不装载（fail-closed）。
    """
    _rows: dict = {}
    for _r in fact_results(record):
        _fu = str(_r.get("fact_uid") or "")
        _rows[_fu] = [(_o.get("obligation_id"), _o.get("text"))
                      for _o in obligation_results(record, _fu)]
    _got = str((record or {}).get("obligation_basis") or "")
    return bool(_got) and _got == _basis_digest_from(_rows)


def record_basis_matches(record, plan) -> bool:
    """记录回带的义务支座是否等于**这份计划**派生出来的支座（缓存读回复用判据）.

    §4.2（复核 2026-10-09-r6）：这里是**唯一支座判据**的一处入口——先读计划上的
    `verification_context`（本地生成、模型不可改）：

    - **没有该字段** ⇒ 合法旧稿，按**根策略**算（`decomposition=None`）；
    - **有但准入不过**（类型/规则/摘要/事实基座/表不合法）⇒ **不复用**（fail-closed）；
    - 有且合法 ⇒ 用**上下文里的分解表**算期望义务与支座——分量模式下缺一条分量
      就对不上摘要，旧记录不可能"只核根义务"混进来。

    读不出视图（计划不合法）⇒ `False`：**证明不了就是不复用**。
    """
    from shared.presentation_contract import view_from_plan
    try:
        _view, _err = view_from_plan(plan)
    except Exception:                                                  # noqa: BLE001
        return False
    if not isinstance(_view, dict) or _err:
        return False
    # 上下文解析走**唯一一份** helper（复核 r6 §4.2）：缺席=合法根策略，坏=不复用。
    _bad, _tbl = verification_context_of(plan, _view)
    if _bad:
        return False
    _want = obligation_basis_digest(_view, _tbl)
    from shared.carrier_entailment import record_problems
    from step01_script_gen.plan_coherence import protocol_problems
    from step01_script_gen.source_revision import protocol_problems as source_protocol
    return (str((record or {}).get("obligation_basis") or "") == _want
            and not record_problems(record, _view, _tbl)
            and not protocol_problems(record, _view)
            and not source_protocol(record, _view))

def _derived_fact_verdict(items) -> str:
    """逐条义务结果 → 该事实的总裁决（**由结果派生**，不让核验方另填一个）.

    取最坏的一档：`contradicted` > `partial` > `unknown` > `supported`。
    条目为空派生不出来（集合判据已另行报出）⇒ 返回空串；
    有条目的 `verdict` 不在 `VERDICTS` 里 ⇒ 也返回空串（那条已由取值判据报出）。
    """
    _vs = [str(_o.get("verdict") or "") for _o in (items or [])]
    if not _vs:
        return ""
    for _v in (VERDICT_CONTRADICTED, VERDICT_PARTIAL, VERDICT_UNKNOWN):
        if _v in _vs:
            return _v
    return VERDICT_SUPPORTED if all(_v == VERDICT_SUPPORTED for _v in _vs) else ""


def obligation_axis_problems(record, view, *, decomposition=None) -> list:
    """**义务轴**：冻结主张的每一部分，逐项回带载体 UID / 展示片段 / 裁决.

    与来源轴、载体轴**分账**。判据（复核 2026-10-08 r2 P1「核验义务」+
    2026-10-09 P1「核验方仍能缩减全部义务的集合」）：

    1. **待核集合由代码定**：期望义务来自 `frozen_obligations(view)`。记录里的义务必须与它
       **一一对应**——缺项（`..._missing`）/ 自造（`..._unknown_id`）/ 重复（`..._duplicate`）/
       改写文本（`..._text_substituted`）都拒收。核验方**不能靠删掉没被满足的义务**把整项
       做成 `supported`（"完整主张被结果子集替代"）；
    2. 记录回带的**义务支座摘要**必须与本次视图一致（`..._basis_mismatch`）：支座随主张或
       派生口径变化 ⇒ 旧记录不复用；
    3. 逐项：`verdict` 必须在 `VERDICTS` 里；判 `supported` 必须点名**这条事实的**载体，且
       片段**逐字出自该载体的实际正文**（引文不能冒充已展示内容）；
    4. **事实总裁决由逐条结果派生**（`_derived_fact_verdict`），记录里的事实裁决必须与派生值
       一致（`verify_fact_verdict_inconsistent`）；
    5. 整项判 `supported` 却有条目没被覆盖 ⇒ `verify_obligation_uncovered`（更具体的那条，
       即 §84.2 的真实误收形态）。

    ⚠️ 这里判的是**集合与自洽**，不判"主张该拆成几部分"——基线口径把整条主张作为一条
    不可改写的义务，**不设人物/时间词关键词表**；出处附带信息不自动增加义务。
    """
    _p: list = []
    _bad_table = decomposition_problems(view, decomposition)
    if _bad_table:
        return _bad_table                     # 表坏 ⇒ 结构化拒收，不继续编集合
    _want_all = expected_obligations(view, decomposition)
    _basis = obligation_basis_digest(view, decomposition)
    _got_basis = str(record.get("obligation_basis") or "")
    if _got_basis != _basis:
        _p.append(_rec(
            "verify_obligation_basis_mismatch", "verification", "",
            "核验记录绑的义务支座 %s 与本次冻结主张的支座 %s 不一致——%s"
            % (_got_basis or "（空）", _basis,
               "主张或派生口径变了，旧记录**不许复用**" if _got_basis
               else "记录没有回带义务支座身份（待核集合无法核对）"),
            "obligation_basis"))
    for _r in fact_results(record):
        _fu = str(_r.get("fact_uid") or "")
        _want = _want_all.get(_fu) or []
        if not _want:
            _p.append(_rec(
                "verify_obligation_basis_missing", "verification", _fu,
                "事实 %s 的**冻结主张**为空，派生不出核验义务——没有支座就无从判"
                "「哪一部分必须被展示」" % _fu, "facts"))
            continue
        _items = obligation_results(record, _fu)
        _want_ids = [_o["obligation_id"] for _o in _want]
        _got_ids = [str(_o.get("obligation_id") or "") for _o in _items]
        _want_text = {_o["obligation_id"]: _o["text"] for _o in _want}
        _miss = [_i for _i in _want_ids if _i not in _got_ids]
        if _miss:
            _p.append(_rec(
                "verify_obligation_missing", "verification", _fu,
                "事实 %s 的义务集合**缺项**：本次冻结主张要求 %s，实到 %s——"
                "核验方不能缩减待核集合（删掉没被满足的义务，不等于它被展示了）"
                % (_fu, _want_ids, _got_ids or "（空表）"), "facts"))
        _extra = sorted({_i for _i in _got_ids if _i and _i not in _want_ids})
        if _extra:
            _p.append(_rec(
                "verify_obligation_unknown_id", "verification", _fu,
                "事实 %s 出现了**不在冻结集合里**的义务 id %s（合法集合：%s）——"
                "自造的义务不许当成「多给的信息」收下" % (_fu, _extra, _want_ids),
                "facts"))
        _dup = sorted({_i for _i in _got_ids if _i and _got_ids.count(_i) > 1})
        if _dup:
            _p.append(_rec(
                "verify_obligation_duplicate", "verification", _fu,
                "事实 %s 的义务 %s 出现了不止一次——重复项不许后者覆盖前者，"
                "也不许把同一部分算两次" % (_fu, _dup), "facts"))
        for _i, _o in enumerate(_items):
            _at = "obligations[%d]" % _i
            _oid = str(_o.get("obligation_id") or "")
            _txt = str(_o.get("text") or "")
            if not _oid.strip():
                _p.append(_rec(
                    "verify_obligation_id_missing", "verification", _fu,
                    "事实 %s 的 %s 没有 `obligation_id`——逐项记录里每一行都要有身份，"
                    "否则指不出**哪一部分**没被展示" % (_fu, _at), "facts"))
            if not _txt.strip():
                _p.append(_rec(
                    "verify_obligation_text_blank", "verification", _fu,
                    "事实 %s 的义务 %s 没写出它说的是**主张里的哪一部分**（`text` 空白）"
                    % (_fu, _at), "facts"))
            elif _oid in _want_text and _norm(_txt) != _norm(_want_text[_oid]):
                _p.append(_rec(
                    "verify_obligation_text_substituted", "verification", _fu,
                    "事实 %s 的义务 %s 的 `text` 与冻结主张派生的那一条不一致"
                    "（要 %r，实到 %r）——待核的是**冻结主张**，不是核验方改写后的版本"
                    % (_fu, _at, _want_text[_oid][:40], _txt[:40]), "facts"))
            _v = str(_o.get("verdict") or "")
            if _v not in VERDICTS:
                _p.append(_rec(
                    "verify_obligation_verdict_invalid", "verification", _fu,
                    "事实 %s 的义务 %s 的结论 %r 不在 %s 之内"
                    % (_fu, _at, _v, list(VERDICTS)), "facts"))
                continue
            # **联合承载**：一条义务由 `evidence[]` 里**每个**载体各自展示一部分，
            # 判断针对并集；所以逐项核身份与片段，而不是只看一个载体。
            _evs = [x for x in (_o.get("evidence") or []) if isinstance(x, dict)]
            if _v == VERDICT_SUPPORTED and not _evs:
                _p.append(_rec(
                    "verify_obligation_evidence_missing", "verification", _fu,
                    "事实 %s 的义务 %s 判为 supported，却没有给出任何**证据项**"
                    "——没有证据的结论不可复核（`evidence[]` 至少一项：载体 + "
                    "逐字出自该载体实际正文的片段）" % (_fu, _at), "facts"))
                continue
            _ev_bad = False
            for _ei, _ev in enumerate(_evs):
                _eat = "%s.evidence[%d]" % (_at, _ei)
                _cu = str(_ev.get("carrier_uid") or "")
                _sp = str(_ev.get("exhibitor_span") or "")
                if not _cu:
                    _p.append(_rec(
                        "verify_obligation_carrier_missing", "verification", _fu,
                        "事实 %s 的义务 %s 的证据项 %s 没点名**哪个载体**展示的"
                        % (_fu, _at, _eat), "facts"))
                    _ev_bad = True
                    continue
                if not _carrier_texts_by_uid(view, _fu, _cu):
                    _p.append(_rec(
                        "verify_obligation_carrier_unknown", "verification", _fu,
                        "事实 %s 的义务 %s 的证据项 %s 点名的载体 %r **不是这条事实的"
                        "载体**（坏身份：拿别的实体的 uid 冒充展示出处）"
                        % (_fu, _at, _eat, _cu), "facts"))
                    _ev_bad = True
                    continue
                if not _sp.strip():
                    _p.append(_rec(
                        "verify_obligation_span_missing", "verification", _fu,
                        "事实 %s 的义务 %s 的证据项 %s 没有回带展示片段"
                        "——没有片段的证据不可复核" % (_fu, _at, _eat), "facts"))
                    _ev_bad = True
                    continue
                _n = _norm(_sp)
                if not any(_n and _n in _norm(_t)
                           for _t in _carrier_texts_by_uid(view, _fu, _cu)):
                    _p.append(_rec(
                        "verify_obligation_span_not_exhibited", "verification", _fu,
                        "事实 %s 的义务 %s 的证据项 %s 的展示片段不在它点名的载体 %r 的"
                        "**实际正文**里（引文不能冒充已展示内容）：%s"
                        % (_fu, _at, _eat, _cu, _sp[:24]), "facts"))
                    _ev_bad = True
            if _ev_bad:
                continue
            if _v != VERDICT_SUPPORTED and not str(_o.get("missing") or "").strip():
                _p.append(_rec(
                    "verify_obligation_missing_blank", "verification", _fu,
                    "事实 %s 的义务 %s 判为 %r 却**没写明缺什么**（`missing` 空白）"
                    "——非 supported 的结论要能直接指出差在哪一部分"
                    % (_fu, _at, _v), "facts"))
                continue
        _derived = _derived_fact_verdict(_items)
        _fv = str(_r.get("verdict") or "")
        if _fv == VERDICT_SUPPORTED and _derived and _derived != VERDICT_SUPPORTED:
            _bad = [str(_o.get("obligation_id") or ("obligations[%d]" % _i))
                    for _i, _o in enumerate(_items)
                    if str(_o.get("verdict") or "") != VERDICT_SUPPORTED]
            _p.append(_rec(
                "verify_obligation_uncovered", "verification", _fu,
                "事实 %s 整项判为 supported，却有 %d 条义务没被 supported 覆盖（%s）"
                "——`supported` 必须覆盖该事实的**全部**义务"
                % (_fu, len(_bad), "、".join(_bad[:4])), "facts"))
        elif _derived and _fv != _derived:
            _p.append(_rec(
                "verify_fact_verdict_inconsistent", "verification", _fu,
                "事实 %s 的裁决是 %r，但它的逐条义务派生出 %r——事实裁决**由逐条结果决定**，"
                "两者必须一致" % (_fu, _fv, _derived), "facts"))
    return _p

def fact_identity_problems(record, view) -> list:
    """**事实结果集合**：每个冻结事实**恰好一项**（缺项 / 重复 / 未知身份各有出口）.

    复核 2026-10-09-r4 P1（展示轴）：完整验收一直有这道门，独立展示入口却只遍历
    "实际返回的 facts"——空表 / 缺一条 / 重复一条都能过。抽成**一份**，两入口共用：
    集合不合格先拒收，不在后续轴靠遍历返回表代替完备性证明。
    """
    _p: list = []
    _facts = [str(_f.get("uid")) for _f in (view.get("frozen_facts") or [])
              if isinstance(_f, dict)]
    _got = [str(_x.get("fact_uid") or "") for _x in fact_results(record)]
    _miss = sorted(set(_facts) - set(_got))
    _dup = sorted({_u for _u in _got if _u and _got.count(_u) > 1})
    _extra = sorted({_u for _u in _got if _u and _u not in set(_facts)})
    if _miss:
        _p.append(_rec("verify_fact_missing", "verification", "",
                       "这些冻结事实在核验记录里没有结果：%s（每个冻结事实必须恰好一项）"
                       % _miss, "facts"))
    if _dup:
        _p.append(_rec("verify_fact_duplicate", "verification", "",
                       "这些事实在核验记录里有重复结果：%s" % _dup, "facts"))
    if _extra:
        _p.append(_rec("verify_fact_unknown", "verification", _extra[0],
                       "这些结果不是本次冻结事实：%s" % _extra, "facts"))
    return _p


def _fact_axis_problems(record, view) -> list:
    """**载体轴**：`supported` 的事实必须给出**逐字出自实际载体正文**的支持片段.

    抽出来是因为它有两个入口：完整验收（`verification_problems`）与**隔离展示任务**
    的展示轴验收（`display_problems`）。**同一份判据**，不各写一份。
    """
    _p: list = []
    for _r in fact_results(record):
            _fu = str(_r.get("fact_uid") or "")
            _v = str(_r.get("verdict") or "")
            if _v not in VERDICTS:
                _p.append(_rec("verify_verdict_invalid", "verification", _fu,
                               "事实 %s 的结论 %r 不在 %s 之内" % (_fu, _v, list(VERDICTS))))
                continue
            if _v != VERDICT_SUPPORTED:
                if _v == VERDICT_UNKNOWN:
                    _p.append(_rec("verify_fact_unknown", "verification", _fu,
                                   "事实 %s 无法判定支持关系（unknown）——**不能**据此判通过；"
                                   "缺信息要补齐或换载体" % _fu))
                else:
                    _p.append(_rec("verify_fact_unsupported", "verification", _fu,
                                   "事实 %s 的实际载体**没有完整表达**这条事实（%s）：%s%s"
                                   % (_fu, _v,
                                      str(_r.get("missing") or "").strip() or "（未说明缺什么）",
                                      "；建议改的载体：%s" % "、".join(
                                          map(str, _r.get("suggested_carriers") or []))
                                      if _r.get("suggested_carriers") else "")))
                continue
            # `supported` 的事实：支持片段必须**逐字出自实际载体正文**
            _spans = [str(s) for s in (_r.get("support_spans") or []) if str(s).strip()]
            if not _spans:
                _p.append(_rec("verify_support_span_missing", "verification", _fu,
                               "事实 %s 判为 supported，却没有给出**出自实际载体正文**的支持"
                               "片段——没有片段的结论不可复核" % _fu))
                continue
            _bad = [_s for _s in _spans if not span_is_exhibited(_s, view, _fu)]
            if _bad:
                _p.append(_rec("verify_support_span_not_exhibited", "verification", _fu,
                               "事实 %s 的支持片段不在**实际载体正文**里（引文不能冒充已展示"
                               "内容）：%s" % (_fu, "；".join(x[:24] for x in _bad[:3]))))
    return _p




def display_record_integrity_problems(record, view, *, decomposition=None) -> list:
    """展示记录的**形状 / 身份 / 支座完整性**（**不含**"内容支持不支持"的结论门）.

    复核 r6 §4.3 第 4 条：展示 **partial 要保留原义务与 `missing` 交修订**——所以
    组合侧只判"这份记录是不是关于**这一次展示**的、形状对不对、集合全不全"，
    **不判**"内容是否 supported"。后者是**最后门**（`verification_problems` →
    `_fact_axis_problems`）的事：拿它当组合的前置会把该走修订的候选直接判死。
    """
    _shape = record_shape_problems(record, where="展示核验记录")
    if _shape:
        return _shape
    if str((record or {}).get("status") or "") != "ok":
        return [_rec("verify_display_not_ok", "verification", "",
                     "展示核验记录不是 ok（%s）——**没验成**不等于内容可接受"
                     % (record or {}).get("status"))]
    _p: list = []
    _bad_table = decomposition_problems(view, decomposition)
    if _bad_table:
        return _bad_table
    _p += fact_identity_problems(record, view)
    # **展示身份**（复核 2026-10-09-r3 P1）：记录必须绑**这一次展示**——载体正文变了
    # 就换了一份输入，旧结论不复用。生产者出口盖章，缺字段同样拒收（fail-closed）。
    _got_disp = str((record or {}).get("display_digest") or "")
    _want_disp = display_input_digest(view, decomposition)
    if _got_disp != _want_disp:
        _p.append(_rec(
            "verify_display_digest_mismatch", "verification", "",
            "展示记录绑的输入摘要 %s 与本次展示正文的摘要 %s 不一致%s"
            % (_got_disp or "（空）", _want_disp,
               "——展示正文变了，旧结论不许复用" if _got_disp
               else "——记录没有回带展示身份（无法证明它判的就是这一次）"),
            "display_digest"))
    _p += obligation_axis_problems(record, view, decomposition=decomposition)
    from shared.carrier_entailment import record_problems
    _p += record_problems(record, view, decomposition)
    return _p


def display_problems(record, view, *, decomposition=None) -> list:
    """**展示轴的独立验收**：形状（facts 组）+ 载体轴 + 义务轴.

    刻意**不含**来源轴与声音轴（复核 2026-10-09-r2 §5②）：那两条轴按各自的依据判
    （小说原文 / 说话人），展示任务的结论**不能**替代它们，也不该被它们顶替。
    展示任务的记录只有 `facts` 一组，另两组是空表——所以这里不查"每个事实恰一条
    来源结论"（那属于完整验收）。

    = `display_record_integrity_problems`（形状/身份/支座）+ **内容门**
    （`_fact_axis_problems`：事实是否被完整表达）。三轴组合只取**前一半**。
    """
    _p = display_record_integrity_problems(record, view, decomposition=decomposition)
    if _p:
        return _p
    _p += _fact_axis_problems(record, view)
    return _p


#: **轴证据引用**块（复核 r6 §4.3 第 3 条）：组合记录要能指回是**哪两次调用**产出的
#: 来源/声音轴与展示轴。**可选**——旧单任务记录没有它，仍然合法（向后兼容）。
AXES_SCHEMA = "content_verification.axes.v1"
AXES_CONTRACT = {"schema": "str", "source_call_id": "str", "display_call_id": "str"}


def axes_problems(record) -> list:
    """`axes` 块的形状判据（**缺席 = 合法旧单任务记录**，不做"缺字段"处理）."""
    if "axes" not in (record or {}):
        return []
    _axes = record["axes"]
    if not isinstance(_axes, dict):
        return [_rec("verify_axes_container", "verification", "",
                     "`axes` 必须是对象（当前是 %s）" % type(_axes).__name__, "axes")]
    _p: list = []
    if str(_axes.get("schema") or "") != AXES_SCHEMA:
        _p.append(_rec("verify_axes_schema", "verification", "",
                       "`axes.schema` 不是 %s（当前 %r）"
                       % (AXES_SCHEMA, _axes.get("schema")), "axes.schema"))
    for _k, _spec in AXES_CONTRACT.items():
        if _k not in _axes:
            _p.append(_rec("verify_axes_field_missing", "verification", "",
                           "`axes` 缺字段 `%s`（要 %s）" % (_k, _spec), "axes.%s" % _k))
        elif not isinstance(_axes.get(_k), str):
            _p.append(_rec("verify_axes_field_type", "verification", "",
                           "`axes.%s` 类型不对：要 %s（当前 %s）"
                           % (_k, _spec, type(_axes.get(_k)).__name__), "axes.%s" % _k))
    for _k in sorted(set(_axes) - set(AXES_CONTRACT)):
        _p.append(_rec("verify_axes_unknown_field", "verification", "",
                       "`axes` 有**未登记字段** `%s`（合法字段：%s）"
                       % (_k, list(AXES_CONTRACT)), "axes.%s" % _k))
    for key in ("source_call_id", "display_call_id"):
        if isinstance(_axes.get(key), str) and not _axes[key].strip():
            _p.append(_rec("verify_axes_call_id_blank", "verification", "",
                           "轴调用身份不能为空白", "axes.%s" % key))
    if (_axes.get("source_call_id") and
            _axes.get("source_call_id") == _axes.get("display_call_id")):
        _p.append(_rec("verify_axes_call_id_duplicate", "verification", "",
                       "两轴必须使用不同的物理调用身份", "axes"))
    return _p


def combine_axes_record(view, *, source_voice, display, decomposition=None,
                        plan_digest=None, novel_digest=None) -> tuple:
    """**三轴组合** → `(记录, 问题)`：来源/声音取自完整任务，展示事实取自展示任务.

    复核 r6 §4.3：

    - **来源轴与声音轴**来自既有**完整任务**（它的原始三轴响应作证据保留），它的
      `facts` **不作为**组件展示事实结果——展示事实只认独立展示任务的结论；
    - **同一候选、同一原文**：两份记录的 `plan_digest` / `novel_digest` 必须一致，
      否则"来源是 A 的、展示是 B 的"这种拼接**不产生有效记录**；
    - **任一轴失败/形状不合格 ⇒ 无有效成功记录**（返回 `(None, 问题)`）；展示轴先过
      它自己的准入（含 `display_digest`），再与来源/声音组合；
    - 组合记录带完整 `source_support` / `facts` / `voice_units` + 候选/原文/支座身份
      + `axes` 轴证据引用。**最后门仍是完整 `verification_problems`**——`display_problems`
      通过**不等于**三轴通过。
    """
    for _name, _r in (("来源/声音", source_voice), ("展示", display)):
        if not isinstance(_r, dict):
            return None, [_rec("verify_axes_axis_missing", "verification", "",
                               "%s核验记录缺失或不是对象（当前 %s）"
                               % (_name, type(_r).__name__))]
        _shape = record_shape_problems(_r, where="%s核验记录" % _name)
        if _shape:
            return None, _shape
        if str(_r.get("status") or "") != "ok":
            return None, [_rec(
                "verify_axes_axis_failed", "verification", "",
                "%s记录不是 ok（%s）——**任一轴没验成 ⇒ 组合无有效记录**，"
                "不许拿另一轴的结论顶上" % (_name, _r.get("status") or "（缺 status）"))]
    _pd = str(plan_digest or plan_digest_of(view))
    _p: list = []
    for _name, _r in (("来源/声音", source_voice), ("展示", display)):
        if str(_r.get("plan_digest") or "") != _pd:
            _p.append(_rec(
                "verify_axes_candidate_mismatch", "verification", "",
                "%s记录绑的候选摘要 %s 与本次候选 %s 不一致——"
                "**来源/声音与展示不得来自不同候选**"
                % (_name, _r.get("plan_digest") or "（空）", _pd)))
        if novel_digest and str(_r.get("novel_digest") or "") != str(novel_digest):
            _p.append(_rec(
                "verify_axes_novel_mismatch", "verification", "",
                "%s记录绑的原文摘要 %s 与本次原文 %s 不一致"
                % (_name, _r.get("novel_digest") or "（空）", novel_digest)))
    if _p:
        return None, _p
    # **展示记录先过它自己的完整性门**（形状/身份/支座）——**不含**内容门：
    # 展示 partial 必须**保留原义务与 missing 交修订**（r6 §4.3 第 4 条），
    # 拿内容门当前置会把该走修订的候选直接判死。内容门在最后（`verification_problems`）。
    _disp_bad = display_record_integrity_problems(display, view,
                                                  decomposition=decomposition)
    if _disp_bad:
        return None, _disp_bad
    _out = {
        "schema": SCHEMA_VERIFICATION, "status": "ok",
        "rule_version": DEFAULT_RULE_VERSION,
        "plan_digest": _pd, "novel_digest": str(novel_digest or ""),
        "source_support": [dict(x) for x in (source_voice.get("source_support") or [])],
        # ⚠️ 展示事实**只**来自独立展示任务（完整任务的 facts 不参与）
        "facts": [dict(x) for x in (display.get("facts") or [])],
        "voice_units": [dict(x) for x in (source_voice.get("voice_units") or [])],
        **({"coherence": dict(source_voice["coherence"])} if "coherence" in source_voice else {}),
        **({"source_preservation": dict(source_voice["source_preservation"])}
           if "source_preservation" in source_voice else {}),
        "obligation_basis": obligation_basis_digest(view, decomposition),
        "display_digest": display_input_digest(view, decomposition),
        "axes": {"schema": AXES_SCHEMA,
                 "source_call_id": str(source_voice.get("call_id") or ""),
                 "display_call_id": str(display.get("call_id") or "")},
    }
    if "entailment" in display:
        _out["entailment"] = display["entailment"]
    _axes_bad = axes_problems(_out)
    from shared.carrier_entailment import record_problems
    _axes_bad += record_problems(_out, view, decomposition)
    return (None, _axes_bad) if _axes_bad else (_out, [])


def verification_problems(record, view, *, decomposition=None,
                          plan_digest=None, plan=None,
                          rule_version=DEFAULT_RULE_VERSION,
                          novel_digest=None, novel_text=None) -> list:
    """核验记录的**形状与身份**核验（纯函数；不做语义判断）.

    `view` 是**被核验候选的关系投影**——支持片段要对得上它的实际载体正文。
    `plan` 是**同一个候选计划**（身份摘要按它算）；不给时按 `plan_digest` 显式传入。

    ⚠️ `plan` 与 `view` **必须是同一份候选的两个投影**（工作包 1 第 1 条）。传错对象
    （拿别的计划、或拿投影前的视图去算身份）就是复核 2026-10-05 P1 那个
    「验收通过、返回候选却没记录」的形态。

    `novel_text` 是**本次冻结原文正文**：来源结论的 `evidence_spans` 必须在**这里**定位
    （"原文证据"栏是可能写错的那一份，不作核验依据）。
    ⚠️ 这个参数**必须与签名一起改**——复核 2026-10-05 P1：调用方已经传 `novel_text`，
    函数体也引用了它，签名却漏了，于是合法 hybrid 候选在统一验收处抛
    `unexpected keyword argument 'novel_text'`，直接调用则抛 `NameError`：
    **合法样本过不了自己的验收**。参数加漏一个就是整条链断在最后一步。
    """
    _p: list = []
    if not isinstance(record, dict):
        return [_rec("verify_record_missing", "verification", "",
                     "缺少内容核验记录（新规则下无记录不得判通过）", "plan_verification")]
    # **形状契约先跑、整份拒收**（复核 2026-10-07 三轮 ①）：旧写法逐处 `.get(...)`
    # 后直接遍历，坏字段（`evidence_spans=1`）走到这一层才抛 `TypeError`——而它是在
    # **统一验收**里抛，等于"验收器被坏输入打崩"，不是"拒收这份记录"。
    _shape = record_shape_problems(record, where="内容核验记录")
    if _shape:
        return _shape
    _dig = str(record.get("plan_digest") or "")
    # ⚠️ **生产与消费共用 `plan_digest_of`**（复核 2026-10-05 P1）。旧实现消费者拿
    # **视图**算摘要、生产者拿**计划**算——于是每一份正常记录都被判
    # `verify_digest_mismatch`。要换口径就改这一个函数，不在两侧各算一遍。
    _want = plan_digest if plan_digest is not None else (
        plan_digest_of(plan) if isinstance(plan, dict) else "")
    if _want and _dig != _want:
        _p.append(_rec("verify_digest_mismatch", "verification", "",
                       "核验记录绑的候选摘要 %s… 与本次候选 %s… 不符——不能拿别份候选的"
                       "核验结果当这一份的" % (_dig[:12], str(_want)[:12]),
                       "plan_digest"))
    # 原文身份也必须一致（核验请求带了哪一份原文）
    # 原文身份也必须一致（核验请求带了哪一份原文）。
    # ⚠️ 复核 2026-10-07 三轮 ②：旧写法 `and str(record.get("novel_digest") or "")`
    # 让**摘要为空/被清空**的记录**跳过**这条检查——"记录没绑本次原文"于是被读成
    # "身份没问题"。现在缺席也算不符（缺席不是通过）。失败记录不带内容结论，跳过。
    if (novel_digest is not None and str(record.get("status") or "") == "ok"
            and str(record.get("novel_digest") or "") != str(novel_digest)):
        _p.append(_rec("verify_novel_digest_mismatch", "verification", "",
                       "核验记录绑的原文摘要（%s）与本次原文（%s）不符——"
                       "没绑摘要、或绑的是别份原文，都得重新核验"
                       % (str(record.get("novel_digest") or "")[:12] or "（空）",
                          str(novel_digest)[:12]),
                       "novel_digest"))
    if str(record.get("rule_version") or "") != str(rule_version):
        _p.append(_rec("verify_rule_version_mismatch", "verification", "",
                       "核验记录的规则版本 %r 与本次（%s）不符——规则变了就得重新核验"
                       % (record.get("rule_version"), rule_version), "rule_version"))
    if str(record.get("status") or "") != "ok":
        # **失败类别由生产者盖章**（`FAILURE_CODES`），这里只透传：旧写法把所有失败
        # 一律写成 `verify_not_available`，于是"输入没资格核"与"网络抖了一下"在报告和
        # 测试里长得一样（复核第十二轮 ① 要求这几类**可分辨**）。
        return _p + [_rec(str(record.get("code") or "verify_not_available"),
                          "verification", "",
                          "核验未成功完成（status=%r，原因：%s）——核验失败**不当作**"
                          "内容可接受" % (record.get("status"),
                                          str(record.get("error") or "")[:120]))]

    _facts = [str(f.get("uid")) for f in (view.get("frozen_facts") or [])]
    _p += fact_identity_problems(record, view)
    # ---- 来源支座：原文是否支持这条**冻结主张**（工作包 2 第 2 条，与载体支座**分账**）----
    _src_got = [str(x.get("fact_uid") or "") for x in source_results(record)]
    if sorted(_src_got) != sorted(_facts):
        _p.append(_rec("verify_source_support_missing", "verification", "",
                       "「原文是否支持主张」这一问缺结果：需要 %s，实到 %s——"
                       "一个 `supported` 不能代替两问（来源支座与载体支座是**两条轴**）"
                       % (sorted(_facts), sorted(_src_got)), "source_support"))
    for _r in source_results(record):
        _fu = str(_r.get("fact_uid") or "")
        _v = str(_r.get("verdict") or "")
        if _v not in VERDICTS:
            _p.append(_rec("verify_source_verdict_invalid", "verification", _fu,
                           "事实 %s 的来源结论 %r 不在 %s 之内"
                           % (_fu, _v, list(VERDICTS))))
        elif _v != VERDICT_SUPPORTED:
            # 来源支座不成立 ⇒ **交回事实生产者补出处**，并**明确不许**通过
            _p.append(_rec("fact_basis_invalid", "fact", _fu,
                           "事实 %s 的**原文出处不支持这条主张**（%s）：%s"
                           "——补齐出处（或由故事侧改写主张）后才能继续；"
                           "**不许**为了让计划通过而悄悄改写主张"
                           % (_fu, _v, str(_r.get("reason") or "").strip()
                              or "（未说明理由）"),
                           "required_facts"))
        else:
            # **`supported` 的来源结果必须有可定位到本次冻结原文的片段**
            # （复核 2026-10-05 P1：空来源片段、虚构来源片段都能通过）。
            # 「原文证据」栏是**可能写错的那一份**，它不作核验依据；只有**本次冻结原文**
            # 才是。所以片段必须真的在原文里，且非空。
            _spans = [str(s) for s in (_r.get("evidence_spans") or [])
                      if str(s).strip()]
            if not _spans:
                _p.append(_rec("verify_source_span_missing", "verification", _fu,
                               "事实 %s 的来源结论判为 supported，却没有给出支持片段"
                               "——没有片段的结论不可复核" % _fu))
            elif novel_text is not None:
                _hay = _norm(novel_text)
                _bad = [_s for _s in _spans if _norm(_s) not in _hay]
                if _bad:
                    _p.append(_rec("verify_source_span_not_in_novel", "verification", _fu,
                                   "事实 %s 的来源片段不在**本次冻结原文**里（虚构的出处"
                                   "不能冒充原文支持）：%s"
                                   % (_fu, "；".join(x[:24] for x in _bad[:3]))))
            elif novel_digest is not None:
                # 没给原文正文但给了原文身份 ⇒ 无法定位，**不放行**
                _p.append(_rec("verify_source_span_unverifiable", "verification", _fu,
                               "事实 %s 的来源片段无法核对（没有原文正文）——"
                               "支持片段必须能在本次冻结原文里定位" % _fu))
    _p += _fact_axis_problems(record, view)

    # ---- 义务轴：整项 `supported` 必须覆盖该事实的**全部**义务（v2 新增，与上面三轴分账）----
    _p += obligation_axis_problems(record, view, decomposition=decomposition)
    from shared.carrier_entailment import record_problems
    _p += record_problems(record, view, decomposition)

    # ---- 声音单位：来源与说话人（原文逐字命中 ≠ 原文人物发言）----
    # ⚠️ uid **取计划里保存的**（`ref_uid`），**不按数组位置重新编号**（工作包 2 第 4 条）。
    # 旧写法硬编 `d%02d/n%02d`：计划的 `presentation.dialogue_uids` 存的是 `dz1/dz2`
    # 这类稳定身份时，"期望集合"与记录里的 uid 永远对不上，于是**每一条正常记录**都被
    # 判 `verify_voice_units_mismatch`；更糟的是它把"顺序"当身份——换个顺序就换了人。
    from shared.presentation_contract import ref_uid
    _units = ([(ref_uid(u), str(u.get("text") or ""), "对白")
               for u in (view.get("dialogue_units") or [])]
              + [(ref_uid(u), str(u.get("text") or ""), "旁白")
                 for u in (view.get("narration_units") or [])])
    _want_units = [_u for _u, _t, _k in _units if _u]
    #: uid → 该单位的**实际正文**（工作包 2 第 4 条：记录必须与**这条**声音单位对应，
    #: 不能只要求 `speaker` 非空——那样"给任意一句台词都填同一个说话人"也能过）
    _text_of = {_u: _t for _u, _t, _k in _units if _u}
    _got_units = [str(x.get("unit_uid") or "") for x in voice_results(record)]
    if sorted(_got_units) != sorted(_want_units):
        _miss = sorted(set(_want_units) - set(_got_units))
        _dup = sorted({u for u in _got_units if _got_units.count(u) > 1})
        _extra = sorted(set(_got_units) - set(_want_units))
        _why = []
        if _miss:
            _why.append("缺 %s" % _miss)
        if _dup:
            _why.append("重复 %s" % _dup)
        if _extra:
            _why.append("多出/悬空 %s" % _extra)
        _p.append(_rec("verify_voice_units_mismatch", "verification", "",
                       "声音单位的核验结果与计划不符（计划 %s；记录 %s）：%s"
                       "——uid 必须**原样回带**计划里保存的稳定身份，不得按位置重编"
                       % (sorted(_want_units), sorted(_got_units),
                          "；".join(_why) or "集合不同"),
                       "voice_units"))
    for _r in voice_results(record):
        _u = str(_r.get("unit_uid") or "")
        if _u not in _want_units:
            _p.append(_rec("verify_voice_unit_dangling", "verification", _u,
                           "核验记录里的声音单位 %r 不在计划中（计划 %s）"
                           % (_u, sorted(_want_units)), "voice_units"))
            continue
        # **记录必须对应这条单位的正文**（复核 2026-10-05 P1：`echo_text` 缺项时
        # 只查 speaker 非空，任意一句话都能配任意说话人）
        _echo = str(_r.get("echo_text") or "")
        _real = _text_of.get(_u, "")
        if not _echo:
            _p.append(_rec("verify_voice_echo_missing", "verification", _u,
                           "声音单位 %s 的核验结果没有回带正文（`echo_text`）——"
                           "无法确认这条结论说的是**这条**台词" % _u))
        elif _norm(_echo) != _norm(_real):
            _p.append(_rec("verify_voice_text_mismatch", "verification", _u,
                           "声音单位 %s 的核验结果回带的正文与计划不符（记录 %r / "
                           "计划 %r）——结论必须对**这条**声音单位成立"
                           % (_u, _echo, _real), "voice_units"))
        from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
        _legacy_semantic = ((view.get("plan_basis") or {}).get(LEGACY_ACCEPTANCE_FIELD)
                            == LEGACY_ACCEPTANCE_VERSION)
        _kind_of = {_uid: _kind for _uid, _text, _kind in _units}
        _expected_source = ("novel_narration" if _legacy_semantic and _kind_of.get(_u) == "旁白"
                            else "novel_speech")
        if str(_r.get("source_kind") or "") != _expected_source:
            _p.append(_rec("verify_voice_not_speech", "verification", _u,
                           "声音单位 %s 的来源结论是 %r，载体要求 %s——"
                           "原文叙述与人物发言必须分别进入对应声音槽"
                           % (_u, _r.get("source_kind"), _expected_source)))
        if str(_r.get("verdict") or "") != VERDICT_SUPPORTED:
            _p.append(_rec("verify_voice_unsupported", "verification", _u,
                           "声音单位 %s 的来源/说话人未获支持（%s）：%s"
                           % (_u, _r.get("verdict"),
                              str(_r.get("reason") or "")[:120])))
        elif _expected_source == "novel_speech" and not str(_r.get("speaker") or "").strip():
            # **说话人要有非空白内容**（复核 2026-10-07 四轮 P2）：旧写法只挡 `None`/`""`，
            # `"   "` 与全角空格照样判 `status=ok` 且统一验收零问题。这一层只要求
            # "有内容"，**不替它猜人物**（判断"是不是真名"不是确定性判据的职责）。
            _p.append(_rec("verify_voice_speaker_missing", "verification", _u,
                           "声音单位 %s 判为 supported，却没给出说话人身份（%s）——"
                           "说话人必须有**非空白**内容，空白串不算给出身份"
                           % (_u, _shape_value_brief(_r.get("speaker")))))
    from step01_script_gen.plan_coherence import problems as coherence_problems
    _p.extend(coherence_problems(record, view, novel_text))
    from step01_script_gen.source_revision import problems as preservation_problems
    _p.extend(preservation_problems(record, view, novel_text=novel_text,novel_digest=novel_digest))
    return _p


def unsupported_facts(record) -> list:
    """记录里**不能执行**的事实结论（`partial`/`contradicted`/`unknown`）."""
    return [x for x in fact_results(record)
            if str(x.get("verdict") or "") not in EXECUTABLE_VERDICTS]


# ---------------------------------------------------------------------------
# 控制器
# ---------------------------------------------------------------------------
def record_protocol_problems(record, view, *, plan_digest, novel_text, novel_digest):
    # These are record/quotation/identity errors, not negative semantic verdicts.
    codes = {"verify_source_span_missing", "verify_source_span_not_in_novel",
             "verify_source_support_missing", "verify_source_verdict_invalid",
             "verify_support_span_missing", "verify_support_span_not_exhibited",
             "verify_voice_units_mismatch", "verify_voice_unit_dangling",
             "verify_voice_echo_missing", "verify_voice_text_mismatch",
             "verify_obligation_missing", "verify_obligation_unknown_id",
             "verify_obligation_duplicate", "verify_obligation_id_missing",
             "verify_obligation_text_blank", "verify_obligation_text_substituted",
             "verify_obligation_verdict_invalid", "verify_obligation_evidence_missing",
             "verify_obligation_carrier_missing", "verify_obligation_carrier_unknown",
             "verify_obligation_span_missing", "verify_obligation_span_not_exhibited",
             "verify_fact_verdict_inconsistent", "verify_obligation_missing_blank"}
    context_bad, table = verification_context_of(view)
    if context_bad:
        return context_bad
    bad = verification_problems(record, view, plan_digest=plan_digest, decomposition=table,
                                 novel_text=novel_text, novel_digest=novel_digest)
    from step01_script_gen.plan_coherence import PROTOCOL_CODES
    from step01_script_gen.source_revision import PROTOCOL_CODES as SOURCE_CODES
    return fact_identity_problems(record, view) + [p for p in bad if p.get("code") in codes | PROTOCOL_CODES | SOURCE_CODES]


class VerificationController:
    """按候选摘要复用核验结果的有界控制器（调用由调用方注入）.

    - `verify(view, plan_digest) -> record | None`：真的去核验（调模型）。返回 `None`
      或抛异常都记 `failed`。
    - 同一摘要第二次来 ⇒ 直接复用**同身份、同规则版本**的记录，不再花钱。
    - 显式启用协议纠正时，记录违约可向原核验员重问；每笔发送都占同一额度。
    - 额度耗尽/过截止 ⇒ 记 `failed(deadline_exhausted)`；**失败不当作内容可接受**。

    与修订调用**分账**：`used` 只数核验次数，不改 `RevisionBudget`；两边共用同一个
    绝对截止时刻（`deadline_at`，由调用方传同一个值）。
    """

    def __init__(self, *, verify, deadline_at=None, clock=None,
                 max_verifications: int = MAX_VERIFICATIONS_PER_RUN,
                 rule_version: str = DEFAULT_RULE_VERSION, log=None, on_record=None,
                 novel_digest=None, validate_record=None, repair=None, require_coherence=False):
        import time as _time
        self.require_coherence = bool(require_coherence)
        self._verify = verify
        self._validate_record = validate_record
        self._repair = repair
        self.deadline_at = deadline_at
        self._clock = clock or _time.monotonic
        self.max_verifications = int(max_verifications)
        self.rule_version = str(rule_version)
        #: **本次冻结原文身份**（由调用方给）。控制器给自己产出的记录**盖这个章**：
        #: 核验方自报的 `novel_digest` 一律覆盖——"没绑原文身份"与"绑错了"都不许
        #: 混过统一验收（复核 2026-10-07 三轮 ②：缓存里那份记录被改错/清空摘要，
        #: 仍然一路 `executable=True`）。
        self.novel_digest = str(novel_digest or "")
        self.log = log or (lambda *a: None)
        self.on_record = on_record
        self.records: dict = {}
        self.corrections: list = []
        self.used = 0

    def remaining(self):
        """**剩余秒数**（不是可用次数！）——`deadline_at` 缺席时返回 `None`."""
        if self.deadline_at is None:
            return None
        return max(0.0, float(self.deadline_at) - float(self._clock()))

    def capacity(self) -> int:
        """**显式容量查询**：还剩几次物理核验发送可用（**次数**，与 `remaining()` 秒数分账）.

        复核 r7 §4.2：旧写法只有 `remaining()`（秒），调用方容易把它当次数用——
        组合需要两次发送，`remaining()=1.0` 既可能是"还剩 1 秒"也可能是"还能发 1 次"。
        这里给一个**只用单位明确的次数**的入口。
        """
        return max(0, int(self.max_verifications) - int(self.used))

    def reserve(self, count: int) -> bool:
        """为一次多轴任务**预占** `count` 个物理发送槽；不足则**不动计数**并返回 False.

        预占让"两次发送"在开始时就被整体保证——不会出现"先发了首轴、第二轴才发现没额度"
        的半核。实际发送数与预占数的差额由 `release()` 归还（见 `record_axes_for`）。
        """
        _n = int(count)
        if _n <= 0 or self.used + _n > self.max_verifications:
            return False
        self.used += _n
        return True

    def release(self, count: int) -> None:
        """归还**未实际发生**的预占槽（实际发送按真实次数结算）."""
        self.used = max(0, int(self.used) - max(0, int(count)))

    def expired(self) -> bool:
        _r = self.remaining()
        return _r is not None and _r <= 0.0

    def seed(self, plan_digest, record, *, view=None, plan=None) -> bool:
        """把**已核验过**的记录装进控制器（缓存复用入口）——先过**同一份记录契约**.

        复核 2026-10-07 三轮 ②：缓存读回侧核过身份、控制器侧**另写一份**装载判断，
        于是"被缓存拒绝的记录"仍可能被装进来当作已核验。现在装载只有这一个入口：
        候选摘要 + **本次原文身份** + 形状合格 + `status=ok` + 规则版本一致，五条全过
        才收；不合格返回 `False`，调用方按"没有记录"处理（该重核就重核）。
        """
        _dig = str(plan_digest or "")
        if not _dig or not isinstance(record, dict):
            return False
        if str(record.get("plan_digest") or "") != _dig:
            return False
        if str(record.get("status") or "") != "ok":
            return False
        if str(record.get("rule_version") or "") != str(self.rule_version):
            return False
        # **本次原文身份**（复核 2026-10-07 四轮 P2）：旧写法只核候选摘要/状态/规则版本，
        # 原文摘要被改错或清空的记录照样装载成功，`record_for()` 随后**零调用**把它当
        # "已核验"复用。身份只能由**控制器持有的那一份**给（自报不作数）：控制器没拿到
        # 身份（空）时**谁都不装**——"证明不了"不等于"对得上"（fail-closed），调用方按
        # "没有记录"处理并按既定策略重新核验。
        _nv = str(self.novel_digest or "")
        if not _nv or str(record.get("novel_digest") or "") != _nv:
            return False
        # **义务支座**（复核 2026-10-09-r2 P2 → r3 P2 收紧）：装载是**独立同源**的入口，
        # 只能接受**可独立核对**的本次支座——给了本次视图/计划就按共用判据算一遍再比。
        #
        # ⚠️ **自洽不算证据**（r3 实测）：把义务集合缩掉、再按缩小后的集合重算摘要，
        # `record_basis_self_consistent` 照样为真——"自洽"只说明这个字段不是随便填的，
        # 不能说明它**属于本次主张**。所以没有可信当前支座时**拒装**，
        # `record_basis_self_consistent` 降级为**诊断**（报告里可以看，不作准入）。
        if plan is not None:
            _basis_ok = bool(record_basis_matches(record, plan))
        elif view is not None:
            # ⚠️ 视图分支**同样要读它自己带的上下文**（复核 r6 §4.2）：视图的
            # `plan_basis` 就是计划的那一份。旧写法在这里按**根义务**算支座，
            # 于是分量模式下"只核根义务"的记录能从装载口混进来
            # ——"读取一次根策略通过后再回退"正是被点名禁止的形态。
            # 上下文坏 ⇒ `_bad` 非空 ⇒ 拒装（fail-closed，不退回根模式）。
            _bad, _tbl = verification_context_of(view)
            _basis_ok = (not _bad) and (
                str(record.get("obligation_basis") or "")
                == obligation_basis_digest(view, _tbl))
            if _basis_ok:
                from shared.carrier_entailment import record_problems
                _basis_ok = not record_problems(record, view, _tbl)
        else:
            _basis_ok = False
        if not _basis_ok:
            return False
        if record_shape_problems(record, where="缓存中的核验记录"):
            return False
        from step01_script_gen import plan_coherence
        if view is None and plan is not None:
            from shared.presentation_contract import view_from_plan
            view, _view_bad = view_from_plan(plan)
            if _view_bad:
                return False
        if view is not None and plan_coherence.protocol_problems(record, view):
            return False
        from step01_script_gen.source_revision import protocol_problems as source_protocol
        if view is not None and source_protocol(record, view):
            return False
        self.records[_dig] = dict(record)
        return True

    def _failed(self, digest, error, status="failed"):
        return {"schema": SCHEMA_VERIFICATION, "status": status,
                "code": FAILURE_CODES.get(str(status), "verify_not_available"),
                "rule_version": self.rule_version, "plan_digest": digest,
                "facts": [], "voice_units": [], "error": str(error)}

    def _correct_record(self, view, digest, failed, *, previous_response=""):
        """Protocol correction uses the same physical-call budget and deadline."""
        if self._repair is None:
            return None
        self.corrections.append({"plan_digest": digest, "attempt": self.used,
                                 "failure": failed, "previous_response": previous_response})
        self._emit(failed)
        if self.expired() or self.capacity() <= 0:
            return None
        self.log("[VERIFY] 核验记录违约 ⇒ 向核验员反馈，候选不变；纠正占共用核验额度")
        return self.record_for(view, digest, verify=lambda current, identity:
                              self._repair(current, identity, failed, previous_response))

    def record_for(self, view, plan_digest=None, *, verify=None,
                   allow_correction: bool = True):
        """取该摘要的核验记录（有就复用；没有就核验一次；额度/截止不够就记失败）.

        `allow_correction=False` 用于**拆分 R**：协议违约只落一条 failed record，
        不再向核验员重问（一次拆分事务最多一次 R 调用）。默认行为不变。
        """
        _dig = plan_digest or plan_digest_of(view)
        if _dig in self.records:
            return self.records[_dig]
        # **核验上下文先准入**（复核 r6 §4.2）：分量模式下上下文坏/表缺，是"这份输入
        # 不该被送去核验"，与调用失败/坏响应分账，且**不花额度、不发调用**。
        # ⚠️ 缺席仍是合法根策略（唯一允许回根的形态）；只有"有但坏"才拒。
        _ctx_bad, _tbl = verification_context_of(view)
        if _ctx_bad:
            _r = self._failed(_dig, "核验上下文不合格：%s"
                              % "；".join(str(x.get("code")) for x in _ctx_bad[:4]),
                              status=FAIL_INPUT)
            _r["problems"] = list(_ctx_bad)
            self.records[_dig] = _r
            self.log("[VERIFY] 核验上下文未通过准入 ⇒ 记为核验失败（**未发起模型调用**）：%s"
                     % "、".join(str(x.get("code")) for x in _ctx_bad[:4]))
            self._emit(_r)
            return _r
        if self.expired():
            _r = self._failed(_dig, "核验前已过截止时刻", status=FAIL_DEADLINE)
            self.records[_dig] = _r
            self.log("[VERIFY] 已过截止时刻 ⇒ 记为核验失败（**不当作内容可接受**）")
            self._emit(_r)
            return _r
        if self.used >= self.max_verifications:
            _r = self._failed(_dig, "核验次数已达上限 %d" % self.max_verifications)
            self.records[_dig] = _r
            self.log("[VERIFY] 核验次数已达上限 %d ⇒ 记为核验失败" % self.max_verifications)
            self._emit(_r)
            return _r
        self.used += 1
        _fn = verify or self._verify
        try:
            _rec = _fn(view, _dig) if _fn is not None else None
        except Exception as exc:                                       # noqa: BLE001
            from shared.presentation_contract import PresentationContractError
            if isinstance(exc, VerificationResponseError):
                # **通了、答得不成形状**：带问题记录整份拒收（不是 `call_failed`）。
                _r = self._failed(_dig, "核验响应不满足记录契约：%s" % exc,
                                  status=FAIL_BAD_RESPONSE)
                _r["problems"] = list(exc.problems)
                _corrected = (self._correct_record(
                    view, _dig, _r,
                    previous_response=str(getattr(getattr(exc, "result", None),
                                                 "text", "") or ""))
                    if allow_correction else None)
                if _corrected is not None:
                    return _corrected
                self.records[_dig] = _r
                self.log("[VERIFY] 核验响应形状非法 ⇒ 记为核验失败（%s）"
                         % "、".join(exc.codes[:4]))
                self._emit(_r)
                return _r
            if isinstance(exc, PresentationContractError):
                # **输入准入失败**：不是"这次核验没成"，是"这份输入不该被送去核验"。
                # 与调用失败 / 坏响应分账（复核第十二轮 ①）——归到 `call_failed` 会让
                # "视图本身坏了"在报告里长得像"网络抖了一下"。
                _r = self._failed(_dig, "核验输入未通过准入：%s" % exc, status=FAIL_INPUT)
                self.records[_dig] = _r
                self.log("[VERIFY] 输入准入未通过 ⇒ 记为核验失败（**未发起模型调用**）：%s"
                         % "、".join(getattr(exc, "codes", []) or []))
                self._emit(_r)
                return _r
            _r = self._failed(_dig, "%s: %s" % (type(exc).__name__, exc), status=FAIL_CALL)
            if allow_correction and bool(getattr(getattr(exc, "result", None),
                                                 "truncated", False)):
                _corrected = self._correct_record(view, _dig, _r,
                                                  previous_response=str(getattr(exc.result, "text", "") or ""))
                if _corrected is not None:
                    return _corrected
            self.records[_dig] = _r
            self.log("[VERIFY] 核验调用异常 ⇒ 记为核验失败：%s" % _r["error"][:160])
            self._emit(_r)
            return _r
        # **形状核验在控制器这一层**：旧写法只判"是不是对象/有没有三组/条目是不是对象
        # 且有 id"，字段类型一个都没判——于是任何 dict 都被 `setdefault("status","ok")`
        # 盖成"核验成功"，`{"nope": True}` 会被当成"没有不支持的事实"，计划因此通过。
        # 现在跑的是**同一份记录契约**（解析 / 缓存读回 / 最终验收同源）。
        _bad_shape = record_shape_problems(_rec, where="核验响应")
        if _bad_shape:
            _r = self._failed(_dig, "核验响应不满足记录契约：%s"
                              % "；".join("%s｜%s" % (x.get("path"), x.get("reason"))
                                          for x in _bad_shape[:4]),
                              status=FAIL_BAD_RESPONSE)
            _r["problems"] = _bad_shape
            _corrected = (self._correct_record(view, _dig, _r)
                          if allow_correction else None)
            if _corrected is not None:
                return _corrected
            self.records[_dig] = _r
            self.log("[VERIFY] 核验响应形状非法 ⇒ 记为核验失败（%s）"
                     % "、".join(str(x.get("code")) for x in _bad_shape[:4]))
            self._emit(_r)
            return _r
        # **形状核验在控制器这一层**：旧写法只判"是不是对象"，于是任何 dict 都被
        # `setdefault("status","ok")` 盖成"核验成功"——`{"nope": True}` 这种坏响应
        # 会被当成"没有不支持的事实"，计划因此通过。
        #
        _rec = dict(_rec)
        _rec.setdefault("schema", SCHEMA_VERIFICATION)
        _rec.setdefault("status", "ok")
        _rec.setdefault("rule_version", self.rule_version)
        _rec["plan_digest"] = _dig          # **身份由代码盖章**，不接受核验方自报
        if self.novel_digest:
            _rec["novel_digest"] = self.novel_digest      # 原文身份同样由代码盖章
        # **义务支座同样由代码盖章**：待核集合是"从冻结主张派生的那一份"，
        # 不是核验方自报的集合；验收按这个摘要核对（复核 2026-10-09 P1）。
        # ⚠️ 用**本次视图解析出来的分解表**（复核 r6 §4.2）：旧写法在这里按根义务
        # 盖章，于是分量模式下记录绑的是根支座、验收按分量支座核 ⇒ 同源判据分叉。
        _rec["obligation_basis"] = obligation_basis_digest(view, _tbl)
        if self._validate_record is not None:
            _protocol_bad = self._validate_record(_rec, view, _dig)
            if _protocol_bad:
                _r = self._failed(_dig, "核验记录无法复核", status=FAIL_BAD_RESPONSE)
                _r["problems"] = list(_protocol_bad)
                _corrected = (self._correct_record(
                    view, _dig, _r,
                    previous_response=json.dumps(_rec, ensure_ascii=False))
                    if allow_correction else None)
                if _corrected is not None:
                    return _corrected
                self.records[_dig] = _r
                return _r
        self.records[_dig] = _rec
        self._emit(_rec)
        return _rec

    def record_axes_for(self, view, plan_digest=None, *, verify_axes,
                        decomposition=None):
        """**显式选中的两轴执行路径**（复核 r7 §4.2；生产默认**不启用**）.

        与 `record_for`（单轴，入口处 `used += 1`）分账：两轴组合需要**两次物理发送**，
        所以这里**先预留两个槽**再执行，最后按生产者自报的**实际发送数**结算
        （未发生的预占归还）。计量口径（复核 r7 §4.2 表）：

        - 同候选**已装载**记录 ⇒ 复用（新发送 0）；
        - 容量 < 2 ⇒ **拒绝整次两轴任务**（发送 0；不半核、**不回退根策略**）；
        - 两笔可用 ⇒ 预留两笔，依次执行（每次实际发送前已记账）；
        - 首轴失败 ⇒ 不发第二轴（已发送 1，释放未用的预留）；
        - 第二轴失败 ⇒ 无有效组合记录（已发送 2，两份证据都留）。

        `verify_axes(view, plan_digest, *, decomposition, source_model, display_model,
        deadline_monotonic=None)`
        由调用方注入（生产实现是 `content_verify.verify_view_axes_with_service`），
        返回体带 `record` / `problems` / `sent`；这样共享模块**不反向依赖生产者**。
        """
        _dig = plan_digest or plan_digest_of(view)
        # **表与两轴模型从已准入上下文读**——不另收一份"另一处算的"。
        _ctx_bad, _tbl = verification_context_of(view)
        if _ctx_bad:
            _r = self._failed(_dig, "核验上下文不合格：%s"
                              % "；".join(str(x.get("code")) for x in _ctx_bad[:4]),
                              status=FAIL_INPUT)
            _r["problems"] = list(_ctx_bad)
            self.records[_dig] = _r
            self.log("[VERIFY] 两轴：核验上下文未通过准入 ⇒ 记为核验失败（**未发起调用**）")
            self._emit(_r)
            return _r
        if decomposition is not None and decomposition != _tbl:
            _r = self._failed(_dig, "外部表与已准入上下文不一致", status=FAIL_INPUT)
            self.records[_dig] = _r
            self._emit(_r)
            return _r
        decomposition = _tbl
        if _dig in self.records:
            _cached = self.records[_dig]
            if isinstance(_cached, dict) and _cached.get("status") != "ok":
                return _cached
            if self.seed(_dig, _cached, view=view):
                self.records[_dig] = _cached
                return _cached
            _r = self._failed(_dig, "复用记录未通过本次准入", status=FAIL_BAD_RESPONSE)
            self.records[_dig] = _r
            self._emit(_r)
            return _r
        _src = context_source_of(view) or {}
        _models = (_src.get("models") if isinstance(_src, dict) else None) or {}
        from shared.carrier_entailment import PROTOCOL, protocol_of
        from shared.quantified_verification import PROTOCOL as QUANTIFIED
        _slots = 3 if protocol_of(view) in (PROTOCOL, QUANTIFIED) else 2
        # **容量不足 2 ⇒ 拒绝本次两轴任务**（0 发送；不半核、不回退根策略）。
        if self.capacity() < _slots:
            _r = self._failed(_dig, "本协议需要 %d 次核验，当前仅剩 %d 次"
                              % (_slots, self.capacity()), status=FAIL_DEADLINE)
            self.records[_dig] = _r
            self.log("[VERIFY] 两轴：容量不足 2 ⇒ 拒绝（**不做半核，不回退根策略**）")
            self._emit(_r)
            return _r
        if self.expired():
            _r = self._failed(_dig, "核验前已过截止时刻", status=FAIL_DEADLINE)
            self.records[_dig] = _r
            self.log("[VERIFY] 两轴：已过截止时刻 ⇒ 记为核验失败（**未发起调用**）")
            self._emit(_r)
            return _r
        self.reserve(_slots)
        try:
            _kwargs = {"decomposition": decomposition,
                       "source_model": _models.get("source_voice"),
                       "display_model": _models.get("display")}
            if self.deadline_at is not None:
                _kwargs["deadline_monotonic"] = self.deadline_at
            _out = verify_axes(view, _dig, **_kwargs)
        except Exception as exc:                                       # noqa: BLE001
            self.release(_slots)
            _r = self._failed(_dig, "%s: %s" % (type(exc).__name__, exc), status=FAIL_CALL)
            self.records[_dig] = _r
            self.log("[VERIFY] 两轴组合异常 ⇒ 记为核验失败：%s" % _r["error"][:160])
            self._emit(_r)
            return _r
        _sent = int((_out or {}).get("sent") or 0)
        self.release(max(0, _slots - _sent))
        _rec = (_out or {}).get("record")
        _probs = list((_out or {}).get("problems") or [])
        if not _rec:
            _code = str((_probs[0] or {}).get("code") if _probs else "verify_call_failed")
            _r = self._failed(_dig, "两轴组合未产生有效记录：%s"
                              % "；".join(str((p or {}).get("code")) for p in _probs[:4]),
                              status=FAIL_CALL)
            _r["code"] = _code                 # 生产者已定类别，不在这里二次映射
            _r["problems"] = _probs
            _r["evidence"] = (_out or {}).get("evidence", {})
            _r["calls"] = (_out or {}).get("calls", [])
            _r["sent"] = _sent
            self.records[_dig] = _r
            self.log("[VERIFY] 两轴组合未成 ⇒ 记为核验失败（%s，已发送 %d）" % (_code, _sent))
            self._emit(_r)
            return _r
        # The producer binds identities; admission must never repair a foreign record.
        if (_sent != _slots or not isinstance(_rec, dict)
                or not isinstance(_rec.get("axes"), dict)
                or not self.seed(_dig, _rec, view=view)):
            _r = self._failed(_dig, "两轴记录未通过与缓存装载相同的准入", status=FAIL_BAD_RESPONSE)
            _r["evidence"] = (_out or {}).get("evidence", {})
            _r["calls"] = (_out or {}).get("calls", [])
            _r["sent"] = _sent
            self.records[_dig] = _r
            self._emit(_r)
            return _r
        _rec = self.records[_dig]
        self._emit(_rec)
        return _rec

    def _emit(self, rec):
        if self.on_record is None:
            return
        try:
            self.on_record(rec)
        except Exception as exc:                                       # noqa: BLE001
            self.log("[WARN] 核验记录留档失败（证据缺失，裁决不受影响）：%s: %s"
                     % (type(exc).__name__, exc))

    def as_dict(self) -> dict:
        return {"used": self.used, "max": self.max_verifications,
                "rule_version": self.rule_version,
                "deadline_at": self.deadline_at, "remaining_sec": self.remaining()}


__all__ = ["SCHEMA_VERIFICATION", "VERDICTS", "EXECUTABLE_VERDICTS",
           "record_protocol_problems",
           "VERDICT_SUPPORTED", "VERDICT_PARTIAL", "VERDICT_CONTRADICTED",
           "VERDICT_UNKNOWN", "DEFAULT_RULE_VERSION", "MAX_VERIFICATIONS_PER_RUN",
           "FAIL_CALL", "FAIL_BAD_RESPONSE", "FAIL_DEADLINE", "FAIL_INPUT",
           "OBLIGATION_RECORD_CONTRACT", "EVIDENCE_ITEM_CONTRACT", "obligation_results",
           "OBLIGATION_BASIS_VERSION", "CLAIM_OBLIGATION_ID", "frozen_obligations",
           "ROOT_OBLIGATION_ID", "DECOMPOSITION_RULE_VERSION", "COMPONENT_KINDS",
           "COMPONENT_RECORD_CONTRACT", "decomposition_basis_key",
           "component_problems", "component_obligations",
           "decomposition_problems", "fact_identity_problems",
           "CONTEXT_SCHEMA", "MODE_ROOT", "MODE_COMPONENTS", "CONTEXT_CONTRACT",
           "VERIFICATION_CONTEXT_KEY", "build_verification_context",
           "context_problems", "context_digest", "context_decomposition",
           "context_source_of", "verification_context_of",
           "DecompositionContractError",
           "obligation_basis_digest", "record_basis_matches",
           "record_basis_self_consistent",
           "obligation_axis_problems", "plan_digest_of", "verification_problems",
           "display_problems", "display_carriers", "display_task_input",
           "display_input_digest", "DISPLAY_TASK_SCHEMA",
           "unsupported_facts", "all_carrier_texts", "carrier_texts",
           "span_is_exhibited", "VerificationController"]
