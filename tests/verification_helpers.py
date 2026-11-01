# -*- coding: utf-8 -*-
"""测试侧的**核验记录夹具助手**（v3 义务支座）.

`shared/content_verification.py` 的 v3 记录里，**待核义务集合由代码从冻结主张派生**
（`frozen_obligations`：基线口径 = 整条主张一条，id 固定 `claim`），记录必须：

- 与那个集合**一一对应**（不许少 / 多 / 重复 / 改写文本）；
- 回带 `obligation_basis` 摘要（生产由 `VerificationController.record_for` 盖章，
  夹具用 `stamp_basis` 自己盖）；
- 事实裁决**由逐条义务派生**（全 `supported` 才能填 `supported`）。

造夹具记录的地方统一走这里——只有一份实现，不各自抄一份（抄四份就会各自漂移，
正是复核反复点名的那类问题）。

⚠️ 这些函数造的是**夹具**，不是核验结论：义务行只保证记录**自洽**（载体真实、片段
逐字出自该载体），不保证模型会这样答。
"""
from __future__ import annotations

from shared import content_verification as cv
from shared import presentation_contract as pc


def carrier_for_fact(view, fact_uid) -> tuple:
    """该事实**第一个真的持有正文的载体** → `(uid, text)`；没有 ⇒ `("", "")`."""
    for _b in (view.get("fact_bindings") or []):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in (_b.get("carriers") or []):
            for _t in cv.carrier_texts(view, _c):
                if _t:
                    return pc.ref_uid(_c), str(_t)
    return "", ""


def carrier_exhibiting(view, fact_uid, span) -> str:
    """哪个载体的实际正文**逐字含**这段片段（没有 ⇒ 空串）."""
    _n = cv._norm(span)
    if not _n:
        return ""
    for _b in (view.get("fact_bindings") or []):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in (_b.get("carriers") or []):
            for _t in cv.carrier_texts(view, _c):
                if _n in cv._norm(_t):
                    return pc.ref_uid(_c)
    return ""


def exhibiting_body(view, fact_uid, carrier_uid, span) -> str:
    """点名的载体里**真的含这段片段**的那段正文（取不到 ⇒ 空串）."""
    _n = cv._norm(span)
    if not _n:
        return ""
    for _b in (view.get("fact_bindings") or []):
        if str(_b.get("fact_uid")) != str(fact_uid):
            continue
        for _c in (_b.get("carriers") or []):
            if pc.ref_uid(_c) != str(carrier_uid):
                continue
            for _t in cv.carrier_texts(view, _c):
                if _n in cv._norm(_t):
                    return str(_t)
    return ""


def frozen_obligation(view, fact_uid) -> dict:
    """**代码给定的那一条**义务（`{obligation_id, text}`）；派生不出来 ⇒ 空 dict."""
    _rows = cv.frozen_obligations(view).get(str(fact_uid)) or []
    return dict(_rows[0]) if _rows else {}


def obligation(view, fact_uid, verdict=cv.VERDICT_SUPPORTED, *, span=None) -> dict:
    """一条**合法**义务行：id/text 取自冻结主张；`supported` ⇒ 至少一项真实证据.

    v4：证据是**数组**（联合承载）——本夹具按"一个载体一项"给，够用即可；
    非 `supported` 时证据留空，但 `missing` 要写明缺什么（契约要求）。
    """
    _o = frozen_obligation(view, fact_uid)
    _verdict = str(verdict or "")
    _row = {"obligation_id": str(_o.get("obligation_id") or ""),
            "text": str(_o.get("text") or ""),
            "verdict": _verdict if _verdict in cv.VERDICTS else cv.VERDICT_PARTIAL,
            "evidence": [], "missing": "", "reason": ""}
    if _verdict == cv.VERDICT_SUPPORTED:
        _s = str(span or "")
        _uid = (carrier_exhibiting(view, fact_uid, _s) if _s
                else carrier_for_fact(view, fact_uid)[0])
        _body = (exhibiting_body(view, fact_uid, _uid, _s) if _s
                 else carrier_for_fact(view, fact_uid)[1])
        if _uid and _body:
            _row.update(verdict=cv.VERDICT_SUPPORTED,
                        evidence=[{"carrier_uid": _uid, "exhibitor_span": _body}])
            return _row
    _row["missing"] = _row["missing"] or "（夹具：非 supported，未写明缺什么）"
    return _row


def obligations_for(view, fact_uid, verdict=cv.VERDICT_SUPPORTED) -> list:
    """**与事实结论自洽**的整套义务（v3：凡是这条事实，就只可能是这一条）."""
    return [obligation(view, fact_uid, verdict)]


def obligations_for_result(view, fact_uid, verdict, spans) -> list:
    """`(事实结论, 引用片段)` → 义务行（v3：一条冻结义务，裁决随事实结论）."""
    _s = next((str(_x) for _x in (spans or []) if str(_x).strip()), "")
    return [obligation(view, fact_uid, verdict, span=(_s or None))]


def stamp_basis(record, view) -> dict:
    """给夹具记录盖**义务支座摘要**（生产由 `VerificationController.record_for` 盖）."""
    if isinstance(record, dict) and isinstance(view, dict):
        record["obligation_basis"] = cv.obligation_basis_digest(view)
    return record


def legal_record_for(view, novel_text: str = "", plan_digest=None) -> dict:
    """**通用**合法核验记录：每个冻结事实给 `supported` + 其冻结义务 + 真实证据.

    与 `test_screen_hybrid_components_chain._record_for` 同类，但**不绑定任何具体夹具的
    span / 载体编号**：证据取该事实第一个真的持有正文的载体（`carrier_for_fact`），
    义务取 v3 的整套（`obligations_for`，一条事实一条冻结义务）。

    用途：核验协议（记录形状 / 义务支座）演化后，一批**只关心规划层行为**的老用例不该
    因为"核验响应预算被耗尽"而误红。它们可以用本函数当**中性**核验答案，把断言重新
    收敛到各自真正要测的那条不变量上。

    ⚠️ 证据边界：本函数产出的记录**只保证形状合法**，不代表内容真的被原文支持——
    需要"核验会拒收"的反例时不要用它。
    """
    _rec = {"schema": cv.SCHEMA_VERIFICATION, "status": "ok",
            "rule_version": cv.DEFAULT_RULE_VERSION,
            "source_support": [], "facts": [], "voice_units": []}
    # **身份字段必须写在记录里**：控制器的 `validate_record` 会判
    # `verify_novel_digest_mismatch` / `verify_digest_mismatch`——它**不替你盖**候选摘要
    # （只在 `record_for` 里盖原文摘要），缺一个就整条记录被判"违约"，
    # 实测表现为 `核验记录违约 × N → 核验未完成 ⇒ 零规划调用`。
    if str(novel_text or ""):
        from step01_script_gen.content_verify import novel_identity
        _rec["novel_digest"] = novel_identity(str(novel_text))["digest"]
    if plan_digest is not None:
        _rec["plan_digest"] = str(plan_digest)
    _src = cv._norm(str(novel_text or ""))
    for _f in (view.get("frozen_facts") or []):
        _fu = str(_f.get("uid"))
        _uid, _body = carrier_for_fact(view, _fu)
        _rec["facts"].append({
            "fact_uid": _fu, "verdict": cv.VERDICT_SUPPORTED,
            "support_spans": [_body] if _body else [],
            "missing": "", "suggested_carriers": [],
            "obligations": obligations_for(view, _fu, cv.VERDICT_SUPPORTED)})
        _ev = str(_f.get("evidence") or "")
        # 引文必须在**原文**里逐字可寻，否则留空（不伪造出处）。
        # ⚠️ 2026-10：定位不到出处时**不能再判 `supported`**——`verify_source_span_missing`
        # 规定"supported 却不给支持片段"是契约违约，会把整份记录打成不可复核
        # （实测：`核验记录违约 ×3 → 核验记录无法复核 → verification_failed`）。
        # 诚实结论是 `unknown`：本条无法核实，而不是"核实通过"。
        _spans = [_ev] if _ev and _src and cv._norm(_ev) in _src else []
        _rec["source_support"].append({
            "fact_uid": _fu,
            "verdict": cv.VERDICT_SUPPORTED if _spans else cv.VERDICT_UNKNOWN,
            "evidence_spans": _spans,
            "reason": ("" if _spans else
                       "夹具：本条事实的证据未能在原文里逐字定位")})
    # **声音单位轴**：整组缺失会判 `verify_voice_units_mismatch`（按轴给身份），
    # 故必须把计划里的每个稳定 uid **原样回带**，并回带该单位的正文（`echo_text`）。
    # `source_kind` 的期望值按生产同一条规则算（legacy 的旁白才是 `novel_narration`，
    # 其余是 `novel_speech`，见 `content_verification.py` 的 `_expected_source`）。
    # `novel_speech` 且 supported 时**必须有非空白 speaker**（那条判据只要求"有内容"、
    # 明说不替它猜人物）；取不到真实说话人时用**标注为夹具**的占位，不冒充真人。
    from shared.presentation_contract import ref_uid
    from shared.fact_metadata import (LEGACY_ACCEPTANCE_FIELD,
                                      LEGACY_ACCEPTANCE_VERSION)
    _legacy = ((view.get("plan_basis") or {}).get(LEGACY_ACCEPTANCE_FIELD)
               == LEGACY_ACCEPTANCE_VERSION)
    _pairs = ([(u, "对白") for u in (view.get("dialogue_units") or [])]
              + [(u, "旁白") for u in (view.get("narration_units") or [])])
    for _u, _kind in _pairs:
        _sk = ("novel_narration" if (_legacy and _kind == "旁白")
               else "novel_speech")
        _sobj = _u.get("source") if isinstance(_u.get("source"), dict) else {}
        _spk = str(_sobj.get("speaker") or _sobj.get("name")
                   or _u.get("speaker") or "").strip()
        if not _spk and _sk == "novel_speech":
            _spk = "（夹具：未指定说话人）"
        _rec["voice_units"].append({
            "unit_uid": ref_uid(_u), "verdict": cv.VERDICT_SUPPORTED,
            "source_kind": _sk, "echo_text": str(_u.get("text") or ""),
            "speaker": _spk, "reason": ""})
    # **整体一致性轴**（`require_coherence=True` 时必需）：`plan_coherence.shape_problems`
    # 要求 `coherence` **恰好**含这 6 个键，且不得多也不得少；`dependencies` 必须逐节拍
    # 提供、每条含 `beat_uid` / `requires_before` / 非空 `reason`。
    # 缺这一轴时控制器的记录校验判 `verify_coherence_record` ⇒ "记录违约 × N
    # ⇒ 核验未完成 ⇒ 零规划调用"（实测就是 g11 卡住的原因）。
    _beats = [pc.ref_uid(b) for b in (view.get("beats") or [])
              if isinstance(b, dict)]
    _buids = [u for u in _beats if u]
    _deps = [{"beat_uid": u, "requires_before": _buids[:i],
              "reason": "（夹具）按计划给定的先后顺序承接前序节拍"}
             for i, u in enumerate(_buids)]
    _rec["coherence"] = {
        "beat_uids": _buids, "verdict": cv.VERDICT_SUPPORTED,
        "conflict_beats": [], "evidence_spans": [],
        "reason": "（夹具）整份计划的先后依赖自洽", "dependencies": _deps}
    return stamp_basis(_rec, view)


__all__ = ["carrier_for_fact", "carrier_exhibiting", "exhibiting_body",
           "frozen_obligation", "obligation", "obligations_for",
           "obligations_for_result", "stamp_basis", "legal_record_for"]
