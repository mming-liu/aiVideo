"""S16：把事实重新绑定到实际表达它的既有视听内容（不新增内容、不改声音）.

首版计划常把“画面＋声音共同表达”的事实只绑到声音；后续压缩声音无法补回画面已
表达的部分。本事务只做一件事：在**既有节拍**上为事实追加视觉/声音承载，保留原承载、
主张、证据、节拍与声音文本；条数和时长不变。新增内容不在本事务内。
"""
from __future__ import annotations

import copy

from shared import presentation_contract as pc


def _problem(code, ref, reason):
    return {"code": code, "ref": str(ref or ""), "reason": str(reason or "")}


def _claim_evidence(fact):
    parts = str(fact or "").split("｜")
    return parts[0].strip(), next((p[len("原文证据："):].strip()
                                  for p in parts if p.strip().startswith("原文证据：")), "")


def rebind_fact_carriers(plan, bindings, *, novel_text=""):
    """`bindings`: `[{"fact_uid": "f06", "add": [{"beat": 10, "kind": "visual"}, ...]}]`.

    只允许给既有事实追加**既有节拍**上的承载；返回 `(new_plan, problems, meta)`。
    """
    if not isinstance(plan, dict) or not plan:
        return None, [_problem("binding_plan_missing", "", "计划缺失")], {}
    if not isinstance(bindings, list) or not bindings:
        return None, [_problem("binding_response_invalid", "", "bindings 必须是非空数组")], {}
    view, bad = pc.view_from_plan(copy.deepcopy(plan))
    if bad:
        return None, [_problem("binding_view_invalid", "", "计划投影不可读：%s" % bad[:2])], {}
    facts = list(view.get("frozen_facts") or [])
    by_uid = {f.get("uid"): f for f in facts}
    bind_rows = {str(b.get("fact_uid")): b for b in (view.get("fact_bindings") or [])}
    beats = list(view.get("beats") or [])
    problems = []
    touched = []
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {"fact_uid", "add"}:
            return None, [_problem("binding_response_invalid", "", "每项须 {fact_uid, add}")], {}
        uid = str(item["fact_uid"])
        if uid not in by_uid:
            problems.append(_problem("binding_fact_unknown", uid, "事实不存在"))
            continue
        add = item.get("add")
        if not isinstance(add, list) or not add:
            problems.append(_problem("binding_add_empty", uid, "add 必须是非空数组"))
            continue
        row = bind_rows.get(uid)
        if row is None:
            problems.append(_problem("binding_missing", uid, "事实没有绑定行"))
            continue
        carriers = list(row.get("carriers") or [])
        existing = {(c.get("kind"), pc.ref_uid(c)) for c in carriers}
        added = []
        before = len(problems)
        for ref in add:
            if not isinstance(ref, dict) or set(ref) != {"beat", "kind"}:
                problems.append(_problem("binding_ref_invalid", uid, "承载项须 {beat, kind}"))
                continue
            beat_no, kind = ref.get("beat"), str(ref.get("kind") or "")
            if type(beat_no) is not int or not 1 <= beat_no <= len(beats):
                problems.append(_problem("binding_beat_missing", uid, "节拍不存在"))
                continue
            beat = beats[beat_no - 1]
            voice = beat.get("voice_ref") if isinstance(beat.get("voice_ref"), dict) else {}
            if kind == pc.KIND_VISUAL:
                new_ref = {"kind": pc.KIND_VISUAL, "uid": pc.ref_uid(beat)}
            elif kind in (pc.KIND_DIALOGUE, pc.KIND_NARRATION):
                if voice.get("kind") != kind:
                    problems.append(_problem("binding_kind_mismatch", uid,
                                             "B%d 的载体不是 %s" % (beat_no, kind)))
                    continue
                new_ref = {"kind": kind, "uid": pc.ref_uid(voice)}
            else:
                problems.append(_problem("binding_kind_invalid", uid, "kind 只能是 visual/dialogue/narration"))
                continue
            key = (new_ref["kind"], new_ref["uid"])
            if key in existing or key in {(x["kind"], x["uid"]) for x in added}:
                continue
            added.append(new_ref)
        if not added:
            if len(problems) == before:
                problems.append(_problem("binding_no_change", uid, "没有新增承载"))
            continue
        row["carriers"] = carriers + added
        touched.append({"fact_uid": uid, "added": added,
                        "carriers": len(row["carriers"])})
    if problems:
        return None, problems, {}
    view["carrier_capacity"] = max(2, max(len(r.get("carriers") or []) for r in bind_rows.values()))
    try:
        new_plan = pc.plan_from_view(view)
    except Exception as exc:  # noqa: BLE001
        return None, [_problem("binding_projection_failed", "", str(exc)[:200])], {}
    # 保护字段：事实主张/证据、节拍、台词、旁白、声音条目必须原样。
    old_facts = list(plan.get("required_facts") or [])
    new_facts = list(new_plan.get("required_facts") or [])
    if len(old_facts) != len(new_facts) or any(
            _claim_evidence(a) != _claim_evidence(b) for a, b in zip(old_facts, new_facts)):
        return None, [_problem("binding_fact_content_changed", "", "重绑改动了主张/证据")], {}
    for key in ("shot_beats", "dialogue_lines", "narration_lines"):
        if list(plan.get(key) or []) != list(new_plan.get(key) or []):
            return None, [_problem("binding_protected_field_changed", key,
                                   "%s 被改动" % key)], {}
    return new_plan, [], {"touched": touched,
                          "voice_entries": len(new_plan.get("dialogue_lines") or [])
                          + len(new_plan.get("narration_lines") or [])}


__all__ = ["rebind_fact_carriers"]
