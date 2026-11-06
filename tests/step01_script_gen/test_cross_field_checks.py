# -*- coding: utf-8 -*-
"""四类跨字段一致性检测（总计划阶段 2）.

锁住三件事：

  1. **四条真实正例稳定命中**（取自第十六次 chapter_001 的真实镜头）；
  2. **反例不误报**——「仍在远处可见」「手边放着道具」、帧职责区分、环境结果、
     多角色多道具不串实体；
  3. **检测器是纯读的**：跑一遍前后 `script` 逐字节不变，issue 身份
     （code/shot_id/frame_id/entity_id/field_path）在文案轻微变化时不变。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.cross_field_checks import (  # noqa: E402
    CROSS_FIELD_CODES, cross_field_issues,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cases() -> dict:
    return json.loads(
        (FIXTURES / "cross_field_minimal.json").read_text(encoding="utf-8"))


def _script(shot: dict) -> dict:
    return {"hook": None, "shots": [shot], "characters": []}


def _codes(script: dict) -> list[str]:
    return [i.code for i in cross_field_issues(script)[0]]

# ---------------------------------------------------------------------------
# 5. weapon_hand_conflict（2026-11-06）
# ---------------------------------------------------------------------------
def _hand_shot(**over) -> dict:
    shot = {
        "index": 6,
        "ff_desc": "神秘人左手短刃抵住杨旭心口",
        "motion_desc": "0-2秒：神秘人蹲踞蓄力；2-4秒：右手寒光短刃扬起。",
        "lf_desc": "杨旭仰卧船头，心口暗红",
        "prompt": "水上刺杀，夜色",
        "keyframes": [],
        "frame_layouts": {"ff": [], "lf": [], "kfs": []},
    }
    shot.update(over)
    return shot


def test_weapon_hand_conflict_is_detected_across_fields():
    issues = cross_field_issues(_script(_hand_shot()))[0]
    hits = [i for i in issues if i.code == "weapon_hand_conflict"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit.severity == "visual_risk"
    assert {t["field_path"] for t in hit.repair_targets} == {"ff_desc", "motion_desc"}


def test_explicit_hand_switch_is_not_a_conflict():
    shot = _hand_shot(motion_desc="0-2秒：左手短刃扬起；2-4秒：换手，右手短刃刺下。")
    assert "weapon_hand_conflict" not in _codes(_script(shot))


def test_two_different_weapons_in_two_hands_are_not_a_conflict():
    shot = _hand_shot(ff_desc="神秘人左手持箫，右手握剑")
    assert "weapon_hand_conflict" not in _codes(_script(shot))


def test_weapon_hand_check_is_read_only():
    shot = _hand_shot()
    before = copy.deepcopy(shot)
    cross_field_issues(_script(shot))
    assert shot == before


def test_weapon_hand_conflict_is_registered():
    assert "weapon_hand_conflict" in CROSS_FIELD_CODES


# ---------------------------------------------------------------------------
# 正例：四条真实 blocking
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_index", [0, 1, 2, 3])
def test_real_blocker_is_detected(case_index):
    case = _cases()["cases"][case_index]
    issues = cross_field_issues(_script(case["shot"]))[0]
    hits = [i for i in issues if i.code == case["issue"]]
    assert hits, f"{case['issue']} 未命中：{case['why']}"
    assert all(i.severity == "visual_risk" for i in hits)
    for i in hits:
        # 每个 issue 必须给出稳定的定位五元组与证据
        assert i.shot_id is not None
        assert i.frame_id
        assert i.field_path
        assert i.evidence


def test_presence_blocker_points_at_the_right_frame_and_entity():
    """shot 1：离场的是「老农」，布局里叫「群演」——必须靠别名折叠对上."""
    case = _cases()["cases"][0]
    issues = [i for i in cross_field_issues(_script(case["shot"]))[0]
              if i.code == "frame_presence_consistency"]
    assert {i.frame_id for i in issues} == {"kf2", "lf"}
    assert {i.entity_id for i in issues} == {"群演"}
    assert all(i.field_path.startswith("frame_layouts.") for i in issues)


def test_possession_blocker_lists_both_released_props():
    case = _cases()["cases"][1]
    issues = [i for i in cross_field_issues(_script(case["shot"]))[0]
              if i.code == "prop_possession_consistency"]
    assert len(issues) == 1
    assert issues[0].frame_id == "ff"
    assert issues[0].entity_id == "杨旭"
    assert "钓杆" in issues[0].message and "酒壶" in issues[0].message


def test_stage_pose_blocker_is_scoped_to_the_actor():
    """shot 5：同帧「杨旭仰卧」不能替张十三证明结果态（角色级作用域）."""
    case = _cases()["cases"][2]
    issues = [i for i in cross_field_issues(_script(case["shot"]))[0]
              if i.code == "frame_stage_pose_consistency"]
    assert len(issues) == 1
    assert issues[0].entity_id == "张十三"
    assert "纵身" in issues[0].evidence


def test_result_action_blocker_is_one_issue_per_frame():
    """shot 7：「反手一掌」与「耳光扇在脸上」是同一动作链 → 一帧一条."""
    case = _cases()["cases"][3]
    issues = [i for i in cross_field_issues(_script(case["shot"]))[0]
              if i.code == "result_action_support"]
    assert len(issues) == 1
    assert issues[0].field_path == "motion_desc"
    assert "KF 描述里有" in issues[0].message


# ---------------------------------------------------------------------------
# 反例
# ---------------------------------------------------------------------------
def _layout(entries, kfs=None):
    return {"ff": entries, "lf": entries, "kfs": kfs or []}


def test_still_visible_far_away_is_not_an_exit():
    """「老农仍在远处田埂可见」不是离场——不得误报在场矛盾."""
    shot = {
        "index": 1, "duration_sec": 5.0,
        "ff_desc": "老农仍在远处田埂可见，牵着牛",
        "lf_desc": "老农仍在远处田埂可见，牵着牛",
        "motion_desc": "0-2秒：老农牵牛；2-5秒：老农走",
        "keyframes": [],
        "frame_layouts": _layout([
            {"name": "群演", "on_screen": True, "pose": "牵牛行走", "props": ["牵绳"]}]),
    }
    assert "frame_presence_consistency" not in _codes(_script(shot))


def test_present_but_absent_entity_is_not_reported():
    """描述说已离场，布局**也**标 on_screen=false → 已经一致，不报."""
    shot = {
        "index": 1, "duration_sec": 5.0,
        "ff_desc": "老农已走远", "lf_desc": "老农已走远",
        "motion_desc": "0-2秒：老农走远；2-5秒：水面平静",
        "keyframes": [],
        "frame_layouts": _layout([
            {"name": "群演", "on_screen": False, "pose": "牵牛行走", "props": []}]),
    }
    assert "frame_presence_consistency" not in _codes(_script(shot))


def test_prop_lying_beside_is_not_possession():
    """「酒壶放在手边」不含释放词 → 不得判成「已脱手仍持有」."""
    shot = {
        "index": 2, "duration_sec": 6.0,
        "ff_desc": "杨旭仰卧船头，酒壶放在手边", "lf_desc": "杨旭仰卧",
        "motion_desc": "0-3秒：杨旭仰卧；3-6秒：水面平静",
        "keyframes": [],
        "frame_layouts": _layout([
            {"name": "杨旭", "on_screen": True, "pose": "仰卧", "props": ["酒壶"]}]),
    }
    assert "prop_possession_consistency" not in _codes(_script(shot))


def test_released_prop_absent_from_layout_is_clean():
    shot = {
        "index": 2, "duration_sec": 6.0,
        "ff_desc": "钓杆已脱手垂湖面", "lf_desc": "杨旭仰卧",
        "motion_desc": "0-3秒：钓杆脱手；3-6秒：水面平静",
        "keyframes": [],
        "frame_layouts": _layout([
            {"name": "杨旭", "on_screen": True, "pose": "仰卧", "props": []}]),
    }
    assert "prop_possession_consistency" not in _codes(_script(shot))


def test_frame_stage_duty_is_respected():
    """FF 是准备态、KF 是过程态——只有 LF 判「结果帧里写过程态」."""
    shot = {
        "index": 3, "duration_sec": 5.0,
        "ff_desc": "张十三站在门口举灯盏", "lf_desc": "张十三落在船梢，稳稳站定",
        "motion_desc": "0-2秒：张十三出门；2-5秒：纵身跃上船",
        "keyframes": [{"desc": "张十三纵身跃起", "stage": "climax", "t_sec": 3.5}],
        "frame_layouts": {
            "ff": [{"name": "张十三", "on_screen": True, "pose": "站立举灯", "props": ["灯盏"]}],
            "lf": [{"name": "张十三", "on_screen": True, "pose": "站立", "props": []}],
            "kfs": [[{"name": "张十三", "on_screen": True, "pose": "纵身跃起", "props": []}]],
        },
    }
    assert "frame_stage_pose_consistency" not in _codes(_script(shot))


def test_process_pose_with_result_evidence_is_clean():
    """pose 虽写「纵身」，但同帧描述已给出落点 → 不是阶段错误."""
    shot = {
        "index": 5, "duration_sec": 5.0,
        "ff_desc": "张十三在堤上", "lf_desc": "张十三纵身跃起后落在船梢，站定",
        "motion_desc": "0-2秒：张十三出门；2-5秒：跃上船",
        "keyframes": [],
        "frame_layouts": {
            "ff": [{"name": "张十三", "on_screen": True, "pose": "站立", "props": []}],
            "lf": [{"name": "张十三", "on_screen": True, "pose": "纵身跃起", "props": []}],
            "kfs": [],
        },
    }
    assert "frame_stage_pose_consistency" not in _codes(_script(shot))


def test_environment_only_result_needs_no_motion_action():
    """LF 只是环境结果（荷叶乱动）→ 不强求 motion 里有对应过程."""
    shot = {
        "index": 6, "duration_sec": 5.0,
        "ff_desc": "水湾平静", "lf_desc": "荷叶乱动，水面泛起层层涟漪",
        "motion_desc": "0-3秒：水湾平静；3-5秒：起风",
        "keyframes": [],
        "frame_layouts": _layout([]),
    }
    assert "result_action_support" not in _codes(_script(shot))


def test_action_present_in_motion_is_supported():
    shot = {
        "index": 7, "duration_sec": 5.0,
        "ff_desc": "张十三怒视", "lf_desc": "张十三反手一掌，耳光扇在听香脸上",
        "motion_desc": "0-2秒：张十三厉声质问；2-5秒：张十三反手一掌扇出耳光",
        "keyframes": [],
        "frame_layouts": _layout([]),
    }
    assert "result_action_support" not in _codes(_script(shot))


def test_multi_entity_props_do_not_cross():
    """同镜多角色多道具：释放的是杨旭的酒壶，不能牵连听香的步摇."""
    shot = {
        "index": 2, "duration_sec": 6.0,
        "ff_desc": "杨旭酒壶已脱手，听香发间步摇完好",
        "lf_desc": "杨旭仰卧，听香跪坐",
        "motion_desc": "0-3秒：饮酒；3-6秒：脱手",
        "keyframes": [],
        "frame_layouts": _layout([
            {"name": "杨旭", "on_screen": True, "pose": "仰卧", "props": ["酒壶"]},
            {"name": "听香", "on_screen": True, "pose": "跪坐", "props": ["步摇"]},
        ]),
    }
    issues = [i for i in cross_field_issues(_script(shot))[0]
              if i.code == "prop_possession_consistency"]
    assert len(issues) == 1
    assert issues[0].entity_id == "杨旭"
    assert "步摇" not in issues[0].message


def test_no_layouts_is_never_reported():
    """没有 frame_layouts 的镜头不产生任何跨字段 issue（无从比较）."""
    shot = {
        "index": 1, "duration_sec": 5.0,
        "ff_desc": "老农已走远，钓杆已脱手", "lf_desc": "老农已走远",
        "motion_desc": "0-3秒：A；3-5秒：B", "keyframes": [],
    }
    assert _codes(_script(shot)) == []


def test_hook_is_checked_too():
    """钩子与正片同一套检查（hook 也是一条正式输出）."""
    hook = {
        "index": 0, "duration_sec": 4.0,
        "ff_desc": "老农已走远", "lf_desc": "老农已走远",
        "motion_desc": "0-2秒：A；2-4秒：B", "keyframes": [],
        "frame_layouts": _layout([
            {"name": "群演", "on_screen": True, "pose": "牵牛行走", "props": []}]),
    }
    issues = cross_field_issues({"hook": hook, "shots": []})[0]
    # ff 与 lf 的 desc 都写了「老农已走远」，两帧都还标着群演在场 → 两条
    assert [i.code for i in issues] == ["frame_presence_consistency"] * 2
    assert {i.frame_id for i in issues} == {"ff", "lf"}
    assert all(i.shot_id == "hook" for i in issues)


# ---------------------------------------------------------------------------
# 纯读 + 身份稳定
# ---------------------------------------------------------------------------
def test_checkers_do_not_write_anything():
    for case in _cases()["cases"]:
        script = _script(case["shot"])
        before = copy.deepcopy(script)
        cross_field_issues(script)[0]
        assert script == before, f"{case['issue']} 检测器改写了脚本"


def test_issue_identity_is_stable_under_wording_change():
    """身份 = (code, shot_id, frame_id, entity_id, field_path)，不含文案."""
    case = _cases()["cases"][0]

    def _identity(shot):
        return {(i.code, str(i.shot_id), i.frame_id, i.entity_id, i.field_path)
                for i in cross_field_issues(_script(shot))[0]}

    original = case["shot"]
    tweaked = copy.deepcopy(original)
    # 文案轻微变化：换掉无关修饰词与标点
    tweaked["lf_desc"] = (tweaked["lf_desc"]
                          .replace("，", "。")
                          .replace("老农", "那个老农"))
    assert _identity(original) == _identity(tweaked)


def test_cross_field_codes_are_registered():
    from step01_script_gen.repair_fields import _REPAIR_CODE_FIELDS
    for code in CROSS_FIELD_CODES:
        assert code in _REPAIR_CODE_FIELDS, f"{code} 未登记字段授权"


# ---------------------------------------------------------------------------
# 授权路径必须与 canonical 扁平化路径**同形**（真实运行踩过一次）
# ---------------------------------------------------------------------------
def test_layout_field_path_matches_flattened_canonical_paths():
    """检测器给出的 `field_path` 必须能授权**真实**的 canonical 路径.

    实测踩过：`frame_layouts.kfs` 是「列表的列表」，规范化路径是
    `frame_layouts.kfs.*.*.<leaf>`（两个通配层级）。只写一个 `*` 时
    `path_allowed` 永远不匹配，模型对该帧的修复被 `merge_authorized` 当成越权回填
    （日志形态：`shot 1: 未授权路径已回填 ['frame_layouts.kfs.2.1.on_screen']`）。
    这里用**真实镜头**把两边钉在一起，结构一变就会失败。
    """
    from step01_script_gen.cross_field_checks import _layout_field_path
    from step01_script_gen.repair_fields import _norm, flatten_paths, path_allowed

    script = json.loads((FIXTURES / "chapter001_16th" / "script_result.json")
                        .read_text(encoding="utf-8"))
    checked = 0
    for shot in script["shots"]:
        flat = flatten_paths(shot)
        for frame_id, bucket in (("ff", "frame_layouts.ff"),
                                 ("lf", "frame_layouts.lf")):
            for leaf in ("on_screen", "pose", "props"):
                canon = f"{bucket}.*.{leaf}"
                assert canon in flat, (shot.get("index"), canon)
                assert path_allowed(canon, {_layout_field_path(frame_id, leaf)}), canon
                checked += 1
        for i in range(len((shot.get("frame_layouts") or {}).get("kfs") or [])):
            canon = f"frame_layouts.kfs.*.*.on_screen"
            assert canon in flat, (shot.get("index"), canon)
            assert path_allowed(canon, {_layout_field_path(f"kf{i}", "on_screen")})
            checked += 1
    assert checked > 0
    # `_norm` 幂等：授权集与 canonical 路径用的是同一套归一
    assert _norm("frame_layouts.kfs.*.*.on_screen") == "frame_layouts.kfs.*.*.on_screen"


# ---------------------------------------------------------------------------
# 授权面：矛盾**两侧**都可修，方向交给原文；证据闸门只兜「方向已写死」的那一条
# ---------------------------------------------------------------------------
def _allow(shot_issue_list):
    from step01_script_gen.repair_fields import allowed_repair_fields
    return allowed_repair_fields([], shot_issue_list)


def test_presence_and_possession_authorize_both_sides():
    """在场/持物：布局侧 + **同帧描述**都要授权（哪侧错由原文定）.

    真实重跑实测（第二十次）：shot 2 的 ff_desc 写「钓杆已脱手」而原文是
    「左手钓杆垂在湖面」——正解是改描述。只授权布局侧时正解被回填，
    修复轮 resolved=0，只能 BLOCK。
    """
    cases = _cases()["cases"]
    presence = [i for i in cross_field_issues(_script(cases[0]["shot"]))[0]
                if i.code == "frame_presence_consistency"]
    allowed = _allow(presence)
    assert "lf_desc" in allowed, "同帧描述必须可改"
    assert any(p.startswith("frame_layouts.lf") for p in allowed)

    possession = [i for i in cross_field_issues(_script(cases[1]["shot"]))[0]
                  if i.code == "prop_possession_consistency"]
    allowed = _allow(possession)
    assert "ff_desc" in allowed
    assert "frame_layouts.ff.*.props" in allowed


def test_keyframe_paired_desc_is_authorized():
    """kf 帧的另一侧是 `keyframes.*.desc`（不是 ff/lf desc）."""
    case = _cases()["cases"][0]        # shot 1 的 kf2 也命中在场矛盾
    issues = [i for i in cross_field_issues(_script(case["shot"]))[0]
              if i.code == "frame_presence_consistency" and i.frame_id == "kf2"]
    assert issues
    allowed = _allow(issues)
    assert "keyframes.*.desc" in allowed
    assert "frame_layouts.kfs.*.*.on_screen" in allowed


def test_evidence_guard_only_applies_to_result_action_support():
    """证据闸门只兜「方向写死」的一条：保留 LF 结果、补 motion 过程.

    在场/持物两条**不设**该闸门——它们的正解可能正是改写描述，设了就会把
    原文支持的正解判成「删证据」。
    """
    import step01_script_gen.main as m

    assert set(m._CROSS_FIELD_EVIDENCE_GUARD) == {"result_action_support"}

    def _rec(code, frame_id, evidence):
        return {"code": code, "shot_id": "2", "frame_id": frame_id,
                "evidence": evidence, "severity": "visual_risk"}

    # 取走描述证据：在场/持物不拦，结果动作支撑要拦
    after = {"shots": [{"index": 2, "ff_desc": "杨旭仰卧船头", "lf_desc": "听香跪坐"}]}
    state = {"issues": [_rec("prop_possession_consistency", "ff", "钓杆已脱手"),
                        _rec("frame_presence_consistency", "lf", "老农已走远"),
                        _rec("result_action_support", "lf", "反手一掌")]}
    removed = m._cross_field_evidence_removed(state, after, {"shot_2"})
    assert len(removed) == 1
    assert "result_action_support" in removed[0]


def test_evidence_guard_passes_when_evidence_survives():
    import step01_script_gen.main as m

    state = {"issues": [{"code": "result_action_support", "shot_id": "7",
                         "frame_id": "lf", "evidence": "反手一掌",
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 7, "lf_desc": "张十三反手一掌扇在她脸上"}]}
    assert m._cross_field_evidence_removed(state, after, {"shot_7"}) == []
    # 未被点名的镜头不查
    assert m._cross_field_evidence_removed(state, {"shots": []}, set()) == []


def test_evidence_guard_allows_reworded_result_action():
    """v6.144：审计自己要求的**改写**不能再被判成「删证据」.

    实测形态（2026-09-29 第一章第 3 轮）：审计的 `minimal_fix` 明令把
    「寒光短刃刺中杨旭心口」改成「短刃已刺入心口，刺客推臂后踞蹲持刃」——
    逐字口径下这条正解被按目标回退，下一轮审计再报同一条，三轮空转。
    """
    import step01_script_gen.main as m

    state = {"issues": [{"code": "result_action_support", "shot_id": "8",
                         "frame_id": "lf", "evidence": "寒光短刃刺中杨旭心口",
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 8,
                        "lf_desc": "刺客推臂送刃，短刃已刺入心口，白袍洇出暗红"}]}
    assert m._cross_field_evidence_removed(state, after, {"shot_8"}) == []
    # 删掉准备态那句、结果句原样留着 → 同样通过
    after2 = {"shots": [{"index": 8, "lf_desc": "寒光短刃刺中杨旭心口，白袍洇出暗红"}]}
    assert m._cross_field_evidence_removed(state, after2, {"shot_8"}) == []


def test_evidence_guard_still_blocks_deleted_result_action():
    """事件级的松绑不能变成后门：结果动作整句消失仍判「删证据」."""
    import step01_script_gen.main as m

    state = {"issues": [{"code": "result_action_support", "shot_id": "8",
                         "frame_id": "lf", "evidence": "寒光短刃刺中杨旭心口",
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 8, "lf_desc": "刺客踞蹲于船头，目光阴沉"}]}
    removed = m._cross_field_evidence_removed(state, after, {"shot_8"})
    assert len(removed) == 1
    assert "结果动作被删除" in removed[0]
    assert "result_action_support" in removed[0]


def test_evidence_guard_falls_back_to_verbatim_without_known_action():
    """认不出结果动作 → 退回**严**口径（逐字），不放行."""
    import step01_script_gen.main as m
    from step01_script_gen.cross_field_checks import (
        result_action_evidence_groups)

    assert result_action_evidence_groups("他站在船头看月亮") == ()
    groups = result_action_evidence_groups("寒光短刃刺中杨旭心口")
    assert len(groups) == 1
    assert "刺中" in groups[0] and "刺入" in groups[0]     # 同组表述互为替代

    state = {"issues": [{"code": "result_action_support", "shot_id": "3",
                         "frame_id": "lf", "evidence": "他站在船头看月亮",
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 3, "lf_desc": "他立在船头"}]}
    removed = m._cross_field_evidence_removed(state, after, {"shot_3"})
    assert len(removed) == 1 and "判据证据被删除" in removed[0]


def test_evidence_guard_requires_every_action_group_to_survive():
    """v6.145（复核反例）：同一分句里的**第二个**动作整段被删，也必须判「删证据」.

    `_clauses` 只在 `，。；！？` 切分，所以「刺中杨旭心口、踹倒听香」是**一个**
    分句、命中**两个**动作组（刺中 / 踹）。把命中的组合并成一次 `any()` 时，
    修复后只剩「刺入」也判通过——踹倒事件被删却蒙混过关。
    """
    import step01_script_gen.main as m
    from step01_script_gen.cross_field_checks import result_action_evidence_groups

    ev = "刺客刺中杨旭心口、踹倒听香"
    assert len(result_action_evidence_groups(ev)) == 2
    state = {"issues": [{"code": "result_action_support", "shot_id": "8",
                         "frame_id": "lf", "evidence": ev,
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 8, "lf_desc": "刺客刺入杨旭心口，白袍洇出暗红"}]}
    removed = m._cross_field_evidence_removed(state, after, {"shot_8"})
    assert len(removed) == 1
    assert "1/2 个动作组无残留" in removed[0]
    # 两组都还剩一个表述 → 通过
    after2 = {"shots": [{"index": 8, "lf_desc": "刺客刺入杨旭心口，回身踹倒听香"}]}
    assert m._cross_field_evidence_removed(state, after2, {"shot_8"}) == []


@pytest.mark.xfail(strict=True, reason=(
    "已知缺口（2026-09-29 复核反例）：本闸门只做**动作组**保全，不覆盖当事角色/"
    "受事对象的替换——「刺中杨旭心口」改成「刺中听香心口」会通过。拦它需要另行"
    "定义可核验的对象依据（原文/计划的当事角色），见 result_action_evidence_groups "
    "的「未覆盖」声明。本测试断言「应当拦住」；缺口补上后它会 XPASS，strict 会"
    "失败以强制更新此处。"))
def test_evidence_guard_does_not_yet_block_participant_substitution():
    """缺口钉桩：换掉受事角色（杨旭→听香）当前**不被**拦——是已知未覆盖，不是通过."""
    import step01_script_gen.main as m

    state = {"issues": [{"code": "result_action_support", "shot_id": "8",
                         "frame_id": "lf", "evidence": "刺客刺中杨旭心口",
                         "severity": "visual_risk"}]}
    after = {"shots": [{"index": 8, "lf_desc": "刺客刺中听香心口"}]}
    assert m._cross_field_evidence_removed(state, after, {"shot_8"}) != []
