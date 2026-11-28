"""Self-contained follow-up checks; no chapter files or paid transports."""
from copy import deepcopy
import pytest
from shared import presentation_contract as pc
from step01_script_gen.frame_presence import presence_warnings


def presence(name, states, motion=""):
    def row(state):
        return [] if state is None else [{"name": name, "on_screen": state}]
    return {"ff_desc": "", "lf_desc": "", "motion_desc": motion,
            "duration_sec": 4, "duration": 4,
            "keyframes": [{"t_sec": 2, "desc": ""}],
            "frame_layouts": {"ff": row(states[0]), "kfs": [row(states[1])],
                              "lf": row(states[2])}}


@pytest.mark.parametrize("name", ["维修员", "旅客乙", "探测机器人"])
def test_presence_is_per_input_actor(name):
    shot = presence(name, (True, False, True))
    assert presence_warnings(shot), "没有任何过渡声明时必须报"
    # 过渡必须落在**它要解释的区间**里：这里 kf 在 2 s、镜长 4 s，
    # 所以离场要落在 (0,2]、入场要落在 (2,4]。
    shot["motion_desc"] = f"前2秒：{name}走出画面；2-4秒：{name}走入画面"
    assert not presence_warnings(shot)


def test_unrelated_interval_does_not_excuse_a_gap():
    """无关时段的进出场不能替另一段的无依据消失背书（M05 时间关联）."""
    shot = presence("维修员", (True, False, True))
    # 帧时刻：0 / 2 / 4 ⇒ 需要解释的区间是 (0,2] 与 (2,4]。
    # 这里的"离场"发生在 2.5-3s（属于第二段），"入场"发生在 0.5-1s（属于第一段）——
    # 两段都没有解释自己那一段。
    shot["motion_desc"] = "2.5-3秒：维修员走出画面；0.5-1秒：维修员走入画面"
    warnings = presence_warnings(shot)
    assert any("离场" in w for w in warnings), warnings
    assert any("入场" in w for w in warnings), warnings


def test_transition_in_its_own_gap_explains_only_that_gap():
    shot = presence("维修员", (True, False, True))
    shot["motion_desc"] = "前2秒：维修员走出画面；3.5-4秒：维修员走入画面"
    warnings = presence_warnings(shot)
    assert not any("离场" in w for w in warnings), warnings
    assert not any("重新在场" in w for w in warnings), warnings


def test_occlusion_in_the_gap_is_legal():
    shot = presence("维修员", (True, False, True))
    shot["motion_desc"] = "前1秒：维修员被立柱遮挡；2-3秒：维修员显露"
    assert not presence_warnings(shot)


def test_untimed_transition_is_inconclusive_not_legal():
    from step01_script_gen.frame_presence import presence_assessment
    shot = presence("维修员", (True, False, True))
    shot["motion_desc"] = "维修员走出画面；维修员走入画面"
    result = presence_assessment(shot)
    assert not result["warnings"], "时间无法确定时不判为缺陷"
    assert result["insufficient"], "但必须记录证据不足，不能当成已解释"


def test_another_actor_transition_does_not_excuse():
    shot = presence("维修员", (True, False, True))
    shot["motion_desc"] = "前2秒：旅客乙走出画面；2-4秒：旅客乙走入画面"
    assert presence_warnings(shot)


@pytest.mark.parametrize("motion", ["维修员没有离开画面", "维修员走入画面", "旅客乙走出画面"])
def test_false_exit_evidence_does_not_hide_disappearance(motion):
    assert presence_warnings(presence("维修员", (True, False, False), motion))


def test_omission_is_unknown_and_explicit_exit_is_legal():
    assert not presence_warnings(presence("维修员", (True, None, True)))
    assert not presence_warnings(presence("维修员", (True, False, False), "维修员走出画面"))
    assert not presence_warnings(presence("维修员", (False, False, True), "维修员走入画面"))


def base_view():
    p = {"main_events": ["维修员等待"], "required_facts": [],
         "shot_beats": ["维修员等待｜载体：旁白1"], "dialogue_lines": [],
         "narration_lines": [{"text": "还要等三分钟。", "source": "还要等三分钟。",
                              "fact_refs": [], "voice_role": "维修员"}], "hook_spoken": []}
    view, problems = pc.view_from_plan(p)
    assert not problems
    return view


def apply(base, changed):
    return pc.apply_editable_view(base, {
        "schema": pc.REVISION_SCHEMA, "rule_version": "test.v1",
        "base_plan_hash": pc.view_digest(base), "fact_basis_hash": pc.fact_basis_digest(base),
        "view": changed}, expected_rule_version="test.v1")


@pytest.mark.parametrize("change", ["omit", "replace"])
def test_full_revision_cannot_drop_voice_identity(change):
    """声源随 uid 冻结：删字段、改回 narrator 仍是换声源（2026-10-30 R06）."""
    base = base_view()
    changed = deepcopy(base)
    if change == "omit":
        del changed["narration_units"][0]["voice_role"]
    else:
        changed["narration_units"][0]["voice_role"] = "narrator"
    result, problems, outcome = apply(base, changed)
    assert result is None and outcome["kind"] == "illegal"
    assert "view_voice_role_changed" in [p["code"] for p in problems]


def test_full_revision_may_remove_a_voice_unit():
    """整条撤下是**合法修法**（2026-11-06）：正文不再出现 ⇒ 不是换声源.

    预算反馈的 must_delete 就要求这么做；旧口径把任何删除都判成换声源，
    第 2/3 轮照做被拒，额度耗尽（run 20261105T224857-15972）。
    """
    base = base_view()
    changed = deepcopy(base)
    changed["narration_units"] = []
    changed["beats"][0]["voice_ref"] = {"kind": pc.KIND_VISUAL,
                                        "uid": changed["beats"][0]["uid"]}
    result, problems, outcome = apply(base, changed)
    assert not problems and outcome["kind"] == "applied", (problems, outcome)
    assert result["narration_units"] == []


def test_legal_revision_preserves_role_and_request_exposes_guard():
    base = base_view()
    changed = deepcopy(base)
    changed["beats"][0]["text"] = "维修员坐在候车室等待"
    result, problems, outcome = apply(base, changed)
    assert not problems and outcome["kind"] == "applied", (problems, outcome)
    assert pc.plan_from_view(result)["narration_lines"][0]["voice_role"] == "维修员"
    assert pc.revision_request(base)["frozen_voice_roles"] == {base["narration_units"][0]["uid"]: "维修员"}


def test_delta_reuses_same_role_guard():
    base = base_view()
    env = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
           "base_plan_hash": pc.view_digest(base), "fact_basis_hash": pc.fact_basis_digest(base)}
    changed = dict(base["beats"][0], text="维修员坐下等待")
    good, problems, result = pc.apply_delta_view(base, dict(env, upsert={"beats": [changed]}))
    assert not problems and result["kind"] == "applied"
    assert good["narration_units"] == base["narration_units"]
    dropped = dict(base["narration_units"][0])
    del dropped["voice_role"]
    bad, problems, result = pc.apply_delta_view(base, dict(env, upsert={"narration_units": [dropped]}))
    assert bad is None and result["kind"] == "illegal"
    assert "view_voice_role_changed" in [p["code"] for p in problems]


def shot(states, motion="", duration=4, t_sec=2):
    def row(state):
        return [] if state is None else [{"name": "维修员", "on_screen": state}]
    kf = {"desc": ""}
    if t_sec is not None:
        kf["t_sec"] = t_sec
    return {"ff_desc": "", "lf_desc": "", "motion_desc": motion, "duration_sec": duration,
            "keyframes": [kf], "frame_layouts": {"ff": row(states[0]), "kfs": [row(states[1])],
                                                 "lf": row(states[2])}}


def test_suffix_label_is_read_as_starting_at_that_second():
    """`2秒后：` 是「从第 2 秒起」，不是 `(0,2)`；`后` 也不能破坏主体识别."""
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((False, False, True), "2秒后：维修员走入画面", duration=6, t_sec=2)
    result = presence_assessment(s)
    assert not result["warnings"], result
    assert not result["insufficient"], result


def test_touching_boundary_does_not_explain_the_earlier_gap():
    """需要 0–2s 的离场，过渡写在 2–4s：只是边界相接，不能算解释."""
    s = shot((True, False, True), "2-4秒：维修员走出画面；2-4秒：维修员走入画面")
    warnings = presence_warnings(s)
    assert any("离场" in w for w in warnings), warnings


def test_partial_overlap_is_insufficient_not_explained():
    """部分重叠但事件位置无法确定 ⇒ 证据不足，不判为已解释、也不判缺陷."""
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "1-3秒：维修员走出画面；2-4秒：维修员走入画面", duration=4)
    result = presence_assessment(s)
    assert not any("离场" in w for w in result["warnings"]), result
    assert any(i["code"] == "transition_position_uncertain" for i in result["insufficient"]), result


def test_missing_keyframe_time_is_not_fabricated():
    """关键帧缺 t_sec ⇒ 报缺口，不得均分成 3 秒."""
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "前2秒：维修员走出画面；2-6秒：维修员走入画面",
             duration=6, t_sec=None)
    result = presence_assessment(s)
    codes = {i["code"] for i in result["insufficient"]}
    assert "keyframe_time_missing" in codes, result
    assert "gap_time_unknown" in codes, result


def test_out_of_range_and_out_of_order_times_are_reported():
    from step01_script_gen.frame_presence import presence_assessment
    late = shot((True, False, True), "", duration=4, t_sec=9)
    assert any(i["code"] == "keyframe_time_at_or_past_end"
               for i in presence_assessment(late)["insufficient"])
    def two_kf(times):
        s = shot((True, False, True), "", duration=6, t_sec=None)
        s["keyframes"] = [{"desc": "", "t_sec": t} for t in times]
        s["frame_layouts"]["kfs"] = [[{"name": "维修员", "on_screen": False}]] * len(times)
        return s
    assert any(i["code"] == "keyframe_time_out_of_order"
               for i in presence_assessment(two_kf([4, 2]))["insufficient"])


def test_insufficient_evidence_reaches_the_validation_report():
    """证据不足必须出现在结构化报告里（不阻塞，但也不能静默消失）."""
    from step01_script_gen.validation import _check_kf_temporal_consistency
    s = shot((True, False, True), "维修员走出画面；维修员走入画面", duration=4)
    s["index"] = 12
    result = _check_kf_temporal_consistency([s])
    assert result["presence_insufficient"], result
    item = result["presence_insufficient"][0]
    assert item["shot"] == 12 and item["actor"] == "维修员" and item["frames"] == [0, 1]
    # 不升级为阻塞：同一类别里不出现「证据不足」的 warn（它走非阻塞诊断类别）
    assert not any("证据不足" in w for w in result["warnings"]), result["warnings"]
    from step01_script_gen.validation import _presence_insufficient_line
    line = _presence_insufficient_line(item)
    assert "维修员" in line and "帧0-1" in line and item["code"] in line


def test_missing_time_followed_by_valid_time_does_not_crash():
    """`[{}, {"t_sec": 4}]`：缺失时间后接合法帧不得 TypeError，且两个缺口都报."""
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "", duration=6, t_sec=None)
    s["keyframes"] = [{}, {"t_sec": 4}]
    s["frame_layouts"]["kfs"] = [[{"name": "维修员", "on_screen": False}]] * 2
    result = presence_assessment(s)
    codes = {i["code"] for i in result["insufficient"]}
    assert "keyframe_time_missing" in codes, result
    assert "gap_time_unknown" in codes, result


def test_out_of_range_time_is_not_used_for_intervals():
    """`t_sec=7`（镜长 6）不得产出 [0,7] 这类区间；只保留诊断."""
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "", duration=6, t_sec=7)
    result = presence_assessment(s)
    assert any(i["code"] == "keyframe_time_at_or_past_end" for i in result["insufficient"])
    windows = [i.get("window") for i in result["insufficient"] if i.get("window")]
    assert all(w[1] <= 6 for w in windows), windows


def test_out_of_order_time_is_dropped_from_intervals():
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "", duration=6, t_sec=None)
    s["keyframes"] = [{"t_sec": 4}, {"t_sec": 2}]
    s["frame_layouts"]["kfs"] = [[{"name": "维修员", "on_screen": False}]] * 2
    result = presence_assessment(s)
    assert any(i["code"] == "keyframe_time_out_of_order" for i in result["insufficient"])
    windows = [i.get("window") for i in result["insufficient"] if i.get("window")]
    assert all(w[0] <= w[1] for w in windows), windows
    assert not any(w == [4, 2] for w in windows), windows


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_non_finite_time_is_reported(bad):
    from step01_script_gen.frame_presence import presence_assessment
    s = shot((True, False, True), "", duration=6, t_sec=None)
    s["keyframes"] = [{"t_sec": float(bad)}]
    result = presence_assessment(s)
    assert any(i["code"] == "keyframe_time_not_finite" for i in result["insufficient"]), result


def test_invalid_times_reach_the_diagnostic_consumer():
    from step01_script_gen.validation import _check_kf_temporal_consistency
    s = shot((True, False, True), "", duration=6, t_sec=None)
    s["index"] = 9
    s["keyframes"] = [{}, {"t_sec": 7}]
    s["frame_layouts"]["kfs"] = [[{"name": "维修员", "on_screen": False}]] * 2
    result = _check_kf_temporal_consistency([s])
    codes = {i["code"] for i in result["presence_insufficient"]}
    assert {"keyframe_time_missing", "keyframe_time_at_or_past_end"} <= codes, codes
    assert not result["warnings"], "证据不足不得进阻塞类别的 warnings"


def test_presence_reaches_validation_consumer():
    from step01_script_gen.validation import _check_kf_temporal_consistency
    shot = presence("维修员", (True, False, True))
    shot.update(index=37, duration_sec=4)
    warnings = _check_kf_temporal_consistency([shot])["warnings"]
    assert any("维修员" in w and "显式离场" in w for w in warnings)


def test_optional_temporal_plan_is_derived_without_mutation():
    from shared.motion_segments import derive_temporal_plan, event_of
    from _text_gen.deepseek_schema import _SHOT, _HOOK
    assert "temporal_plan" not in _SHOT["required"]
    assert "temporal_plan" not in _HOOK["required"]
    shot = {"index": 37, "duration_sec": 6, "motion_desc": "前2秒：维修员抬头；2-6秒：维修员打开柜门"}
    before = deepcopy(shot)
    plan = derive_temporal_plan(shot)
    assert plan["source"] == "derived" and plan["events"][-1]["end_sec"] == 6
    assert [(e["start_sec"], e["end_sec"]) for e in plan["events"]] == [(0, 2), (2, 6)]
    assert event_of(plan, 2)["description"] == "维修员打开柜门"
    assert all(e["source_quote"] is None for e in plan["events"])
    assert shot == before

# ---------------------------------------------------------------------------
# 2026-10-28（M07）：行尾归一 + 合同不可消费必须 fail-closed
# ---------------------------------------------------------------------------
def _plan_with_adaptation(source="乙离开。"):
    """最小改编块：一条旁白基底 + 一条同文改编（出处可查）。"""
    from shared import voice_adaptation as va
    plan = {"main_events": ["乙离开"], "required_facts": [],
            "shot_beats": ["乙离开｜载体：旁白1"], "dialogue_lines": [],
            "narration_lines": [{"text": "乙离开。", "source": "乙离开。", "fact_refs": []}],
            "hook_spoken": []}
    base = va._base_units(plan)[0]
    entries = [{"base_uid": base["base_uid"], "kind": "narration",
                "base_text": base["base_text"], "text": "乙走了。", "source": source}]
    digest = va.entries_digest(entries)
    plan["voice_adaptations"] = {
        "schema": va.SCHEMA, "entries": entries, "digest": digest,
        "approval": {"approved": True, "scope": "candidate", "approved_by": "test fixture",
                     "approved_at": "2026-10-28T00:00:00+00:00",
                     "base_plan_digest": va.base_plan_digest(plan),
                     "entries_digest": digest}}
    return plan


def test_source_norm_ignores_line_endings():
    """同一句原文，CRLF 与 LF 必须判为同一句（否则改编块"查不到出处"）."""
    from step01_script_gen.plan_feasibility import _source_norm
    assert _source_norm("甲\r\n乙。") == _source_norm("甲\n乙。")
    assert _source_norm("甲\r乙。") == _source_norm("甲乙。")


def test_adaptation_is_consumable_for_crlf_novel(monkeypatch):
    """原文按字节读入（CRLF）时，改编块仍可消费——这是提交入口的读法."""
    import shared.config as cfg
    from shared import voice_adaptation as va
    monkeypatch.setattr(cfg, "VOICE_ADAPTATION_ENABLED", True)
    plan = _plan_with_adaptation()
    novel_lf = "前文。\n乙离开。\n后文。"
    novel_crlf = "前文。\r\n乙离开。\r\n后文。"
    assert va.adaptation_problems(plan, novel_lf) == []
    assert va.adaptation_problems(plan, novel_crlf) == []


def test_contract_unreadable_plan_aborts_finalize(monkeypatch):
    """约束因合同不可消费而失败 ⇒ finalize 返回错误（不得只 warn 后继续）."""
    import step01_script_gen.main as m
    from shared.voice_adaptation import AdaptationInvalidError
    from step01_script_gen.constraints import NarrationSubtitleConsistencyConstraint as C

    def boom(self, ctx):
        raise AdaptationInvalidError([{"code": "adaptation_source_not_in_novel"}])

    monkeypatch.setattr(C, "apply", boom)
    script = {"title": "t", "characters": [], "hook": None, "chapter_outline": {},
              "shots": [{"index": 1, "duration_sec": 5, "duration": 5,
                         "narration_segment": "乙离开。", "subtitle": "乙离开。",
                         "ff_desc": "甲站立", "lf_desc": "甲转身",
                         "motion_desc": "0-2秒：甲站立；2-5秒：甲转身",
                         "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}]}
    out, errs = m.finalize_step01_script(script, novel_text="前文。乙离开。", label="test")
    assert errs, "合同不可消费必须变成 finalize 错误"
    assert errs[0].startswith("constraint_failed:"), errs
    assert "AdaptationInvalidError" in errs[0]