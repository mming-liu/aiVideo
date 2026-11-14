"""v6.77 第四阶段：修复轮「完整镜头事务」测试.

覆盖：
  1. `_validate_replacement_shot` —— 结构不完整 / 字段非法 一律拒绝
  2. `_apply_shot_replacements` —— 只替换被点名的镜头；未点名镜头逐字节不动；
     任一条不通过则整条丢弃（不做部分应用）
  3. `_repair_acceptance_decision` —— `non_target_changed` 非空即拒绝
     （对应验收条件「修复一个镜头不能改变其他镜头的语义 revision」）
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _shot(index=1, prompt="中景，张十三立在船头", dur=4.0) -> dict:
    """一个结构完整的镜头（与首次生成同构）."""
    return {
        "index": index,
        "narration_segment": "张十三：都退下。",
        "subtitle": "张十三：都退下。",
        "scene": "照月湾", "location": "照月湾小舟", "main_subject": "张十三",
        "prompt": prompt,
        "motion_desc": "0-2秒：张十三抬手；2-4秒：庄仆退下",
        "ff_desc": "张十三立在画右船头，庄仆们围拢",
        "lf_desc": "张十三立在画右船头，庄仆们退去",
        "duration": dur, "duration_sec": dur, "variation_type": "small",
        # v6.78：时间结构必填
        "temporal_plan": {"events": [
            {"start_sec": 0.0, "end_sec": 2.0, "description": "张十三抬手", "source_quote": ""},
            {"start_sec": 2.0, "end_sec": dur, "description": "庄仆退下", "source_quote": ""},
        ]},
        "keyframes": [
            {"desc": "张十三抬手", "t_sec": 1.0, "stage": "approach"},
            {"desc": "庄仆退下", "t_sec": 3.0, "stage": "after"},
        ],
        "frame_layouts": {
            "ff": [{"name": "张十三", "on_screen": True, "position": "画右",
                    "pose": "站立", "gaze_target": "画左", "props": []}],
            "lf": [{"name": "张十三", "on_screen": True, "position": "画右",
                    "pose": "抬手", "gaze_target": "画左", "props": []}],
            "kfs": [
                [{"name": "张十三", "on_screen": True, "position": "画右",
                  "pose": "抬手", "gaze_target": "画左", "props": []}],
                [{"name": "张十三", "on_screen": True, "position": "画右",
                  "pose": "抬手", "gaze_target": "画左", "props": []}],
            ],
        },
    }


def _script():
    return {"characters": [], "hook": None, "fps": 24,
            "shots": [_shot(1), _shot(2, prompt="中景，听香跪坐船板")]}


# --------------------------------------------------------- 结构校验

def test_valid_replacement_accepted():
    from step01_script_gen.main import _validate_replacement_shot

    orig = _shot(1)
    assert _validate_replacement_shot(orig, copy.deepcopy(orig)) == ""


def test_missing_structural_field_rejected():
    """结构字段必须成套出现——缺一即拒（这正是旧 patch 契约的病灶）."""
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    del new["keyframes"]
    assert "结构字段缺失" in _validate_replacement_shot(_shot(1), new)






# 注：`temporal_plan` 在过渡态下是**只读**字段（决定 1），替换校验不强制它——
# 必填切换要等整镜覆盖/grounding/motion 一致性校验与 revision 纳入完成之后。
def test_index_change_rejected():
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["index"] = 99
    assert "index 被改动" in _validate_replacement_shot(_shot(1), new)


def test_bad_kf_stage_rejected():
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["keyframes"][0]["stage"] = "unknown_stage"
    assert "stage 非法" in _validate_replacement_shot(_shot(1), new)


def test_kf_out_of_duration_rejected():
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1, dur=4.0)
    new["keyframes"][1]["t_sec"] = 9.0
    assert "越出镜长" in _validate_replacement_shot(_shot(1), new)


def test_layout_kfs_length_mismatch_rejected():
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["frame_layouts"]["kfs"] = new["frame_layouts"]["kfs"][:1]
    assert "长度" in _validate_replacement_shot(_shot(1), new)


def test_narration_ballooning_rejected():
    """旁白暴涨 → 视为「原文引用越界」的代理判据（完整锚定由 grounding 下游把关）."""
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["narration_segment"] = "张十三：" + "都退下。" * 20
    assert "增长上限" in _validate_replacement_shot(_shot(1), new)


# --------------------------------------------------------- 原子替换

def test_only_picked_shot_is_replaced():
    """只替换被点名的镜头；未点名镜头**逐字节不动**."""
    from step01_script_gen.main import _apply_shot_replacements

    script = _script()
    untargeted_before = copy.deepcopy(script["shots"][1])
    new = _shot(1, prompt="近景，张十三抬手示意")
    merged, n, rejected = _apply_shot_replacements(script, [
        {"shot_id": "1", "shot": new},
    ], {"shot_1"})
    assert (n, rejected) == (1, [])
    assert merged["shots"][0]["prompt"] == "近景，张十三抬手示意"
    assert merged["shots"][1] == untargeted_before
    # 原 script 不被就地改动（事务在副本上进行）
    assert script["shots"][0]["prompt"] == "中景，张十三立在船头"


def test_unpicked_shot_is_not_replaced():
    from step01_script_gen.main import _apply_shot_replacements

    merged, n, rejected = _apply_shot_replacements(
        _script(), [{"shot_id": "1", "shot": _shot(1)}], set())
    assert n == 0
    assert any("未被点名" in r for r in rejected)



def test_invalid_entry_only_drops_itself():
    """**逐目标提交**（v6.86，用户决定 A1）：一条不通过只丢它自己.

    取代 v6.80/v6.83 的「整批全有或全无」。用户 2026-09-21 决定 A1：`expected_targets`
    要防的是「只提交一部分却当整批成功」（**报告失真**），不是数据损坏；而数据侧的
    安全保证全是单镜作用域、replacement 又不写任何跨镜字段，所以一条坏条目不该
    否决同批的合法条目。批次级安全网（全局完整性 / 非目标 revision / 阻塞退化）
    仍在修复轮调用侧。
    """
    from step01_script_gen.main import _apply_shot_replacements

    bad = _shot(1)
    del bad["frame_layouts"]
    good = _shot(2, prompt="近景，听香抬头")
    script = _script()
    before = copy.deepcopy(script)

    merged, n, rejected = _apply_shot_replacements(
        script, [{"shot_id": "1", "shot": bad}, {"shot_id": "2", "shot": good}],
        {"shot_1", "shot_2"},
    )
    assert n == 1, "合法的那条必须提交"
    assert any("shot 1" in r for r in rejected), rejected
    assert merged["shots"][1]["prompt"] == "近景，听香抬头", "镜头 2 的合法替换必须生效"
    assert merged["shots"][0]["prompt"] == before["shots"][0]["prompt"], "镜头 1 保持原样"
    assert script == before, "原脚本不得被就地改动"


def test_all_valid_entries_commit_together():
    """全部合法 → 整批一起提交."""
    from step01_script_gen.main import _apply_shot_replacements

    merged, n, rejected = _apply_shot_replacements(
        _script(),
        [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")},
         {"shot_id": "2", "shot": _shot(2, prompt="近景乙")}],
        {"shot_1", "shot_2"},
    )
    assert (n, rejected) == (2, [])
    assert merged["shots"][0]["prompt"] == "近景甲"
    assert merged["shots"][1]["prompt"] == "近景乙"


def test_duration_sec_two_calibers_rejected():
    """`duration_sec` 与 `duration` 不一致 → 拒（发现 9 #2：不得两种口径并存）."""
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["duration_sec"] = 9.0        # 与 duration=4.0 冲突
    assert "不一致" in _validate_replacement_shot(_shot(1), new)


def test_duration_sec_consistent_accepted():
    from step01_script_gen.main import _validate_replacement_shot

    new = _shot(1)
    new["duration_sec"] = new["duration"]     # 同口径 → 允许
    assert _validate_replacement_shot(_shot(1), new) == ""


# ------------------------------------- 发现 9 #5：端到端事务边界

def test_final_field_mask_catches_post_processing_write():
    """后处理在事务末尾偷改锁定字段 → 必须被抓到（发现 9 #4）."""
    from step01_script_gen.main import _final_field_mask_violations

    before = _script()
    after = copy.deepcopy(before)
    after["shots"][0]["narration_segment"] = "被后处理改掉的台词"
    v = _final_field_mask_violations(before, after, {"shot_1"})
    assert any("narration_segment" in x for x in v), v


def test_final_field_mask_ignores_duration_sec():
    """`duration_sec` 由序列化函数派生，**不算**越权（发现 9 #2/#4）."""
    from step01_script_gen.main import _final_field_mask_violations

    before = _script()
    after = copy.deepcopy(before)
    after["shots"][0]["duration_sec"] = 4.0   # 序列化回写
    after["shots"][0]["duration"] = 4.0
    assert _final_field_mask_violations(before, after, {"shot_1"}) == []


def test_final_field_mask_ignores_non_target_shots():
    """未点名镜头的锁定字段变化由 `non_target_changed` 单独判定，不在此重复."""
    from step01_script_gen.main import _final_field_mask_violations

    before = _script()
    after = copy.deepcopy(before)
    after["shots"][1]["narration_segment"] = "改了镜 2"
    assert _final_field_mask_violations(before, after, {"shot_1"}) == []


def test_hook_replacement_uses_dedicated_key():
    """hook 必须由 `hook_replacement` 承载（发现 3），不得塞进 `shot_replacements`."""
    from step01_script_gen.main import _apply_shot_replacements

    script = _script()
    script["hook"] = _shot(0, prompt="特写，匕首出鞘")
    merged, n, rejected = _apply_shot_replacements(
        script, [], {"hook"},
        hook_replacement={"shot_id": "hook", "shot": _shot(0, prompt="特写，寒光一闪")})
    assert (n, rejected) == (1, [])
    assert merged["hook"]["prompt"] == "特写，寒光一闪"

    # 塞进 shot_replacements → 类型不匹配，整批拒绝
    _m2, _n2, rej2 = _apply_shot_replacements(
        script, [{"shot_id": "hook", "shot": _shot(0, prompt="特写，寒光一闪")}], {"hook"})
    assert _n2 == 0 and any("hook_replacement" in r for r in rej2), rej2


# ------------------------------------------- 接受判据：非目标镜头不变

def test_acceptance_rejects_when_non_target_changed():
    from step01_script_gen.main import _repair_acceptance_decision

    d = _repair_acceptance_decision(
        {}, {}, [], {"shots": []}, False, non_target_changed=[2])
    assert not d["accepted"]
    assert d["reason"] == "non_target_changed"
    assert d["non_target_changed"] == [2]


def test_acceptance_passes_when_only_target_changed():
    from step01_script_gen.main import _repair_acceptance_decision

    d = _repair_acceptance_decision(
        {}, {}, [], {"shots": []}, False, non_target_changed=[])
    assert d["accepted"], d
    assert d["reason"] == ""


def test_non_target_changed_defaults_to_empty():
    """向后兼容：不传该参数时行为不变（旧调用点/旧测试不受影响）."""
    from step01_script_gen.main import _repair_acceptance_decision

    assert _repair_acceptance_decision({}, {}, [], {"shots": []}, False)["accepted"]


def test_per_shot_revisions_isolates_changes():
    """`per_shot_revisions` 能把改动精确定位到镜——正是上面判据的依据."""
    from shared.quality_gate import changed_shot_keys, per_shot_revisions

    a = _script()
    b = copy.deepcopy(a)
    b["shots"][0]["prompt"] = "改了第一镜"
    changed = changed_shot_keys(per_shot_revisions(a), per_shot_revisions(b))
    assert changed == [1], changed


# ------------------------------------------- 发现 3：修复轮不得重算 duration

def test_repair_timeline_serializer_never_reestimates_duration():
    """修复轮只做 timeline **序列化**：`duration` 不因动作文本而变（发现 3 最小复现）.

    原实现调 `_compute_timecodes(merged, allow_split=False)`——那个布尔只关掉拆镜
    pre-pass，**后续完整估时逻辑照跑**，会把 `duration=4` + `motion_desc="0-6秒：动作"`
    的镜头改成 6 秒。于是「duration_sec 是 Mask 字段」这条 prompt 契约在运行时
    不成立：LLM 只改一个允许字段，修复轮却顺带改了时长，而接受判定比较的是
    patch 直接造成的变化，看不见这个副作用。
    """
    from step01_script_gen.timecode import write_timeline_from_durations

    script = _script()
    script["shots"][0]["duration"] = 4.0
    script["shots"][0]["duration_sec"] = 4.0
    script["shots"][0]["motion_desc"] = "0-6秒：张十三抬手"      # 文本提到 6 秒
    before = [s["duration"] for s in script["shots"]]

    write_timeline_from_durations(script)

    assert [s["duration"] for s in script["shots"]] == before, "不得按 motion 重估时长"
    assert [s["duration_sec"] for s in script["shots"]] == before, "duration_sec 由 duration 派生"
    assert script["timeline"], "timeline 仍必须重建"


def test_only_target_shot_changes_and_untouched_shot_keeps_duration():
    """整轮事务：被选镜头只改 prompt，未选镜头的 duration 与 revision 都必须不变.

    对应审核发现 3 的实施方案 #5。这里跑的是**确定性部分**（替换 + 序列化 +
    最终字段 mask + 逐镜 revision 判据），不含 LLM 调用。
    """
    import copy as _copy

    from shared.quality_gate import changed_shot_keys, per_shot_revisions
    from step01_script_gen.main import (
        _apply_shot_replacements, _final_field_mask_violations,
    )
    from step01_script_gen.timecode import write_timeline_from_durations

    script = _script()
    # 未选镜头带一个「非规范」时长（与 motion_desc 文本不匹配，4.0 vs 提到 2/4 秒外的值）
    script["shots"][1]["duration"] = 5.5
    script["shots"][1]["duration_sec"] = 5.5
    before = _copy.deepcopy(script)
    picked = {"shot_1"}

    new = _shot(1, prompt="近景，张十三抬手指向船外")
    merged, n, rejected = _apply_shot_replacements(script, [{"shot_id": "1", "shot": new}], picked)
    assert (n, rejected) == (1, [])
    write_timeline_from_durations(merged)

    assert merged["shots"][0]["prompt"] == "近景，张十三抬手指向船外"
    assert merged["shots"][1]["duration"] == 5.5, "未选镜头时长不得被重算"
    assert merged["shots"][1]["duration_sec"] == 5.5
    # 未选镜头：逐镜语义 revision 逐字节不变
    changed = changed_shot_keys(per_shot_revisions(before), per_shot_revisions(merged))
    assert changed == [1], changed
    # 被选镜头：锁定字段（含 index/narration/subtitle/scene/location/variation_type）无越权改动
    assert _final_field_mask_violations(before, merged, picked) == []


def test_missing_or_extra_shot_rejected_by_revision_gate():
    """新增/删除镜头必须被逐镜 revision gate 判出（发现 3 实施方案 #4）.

    `changed_shot_keys` 对 key 集合做对称差——增删镜头会以「新 key / 消失 key」
    的形式出现，正是 `non_target_changed` 的依据。
    """
    import copy as _copy

    from shared.quality_gate import changed_shot_keys, per_shot_revisions

    before = _script()
    added = _copy.deepcopy(before)
    added["shots"].append(_shot(3))
    assert 3 in changed_shot_keys(per_shot_revisions(before), per_shot_revisions(added))

    removed = _copy.deepcopy(before)
    removed["shots"] = removed["shots"][:1]
    assert 2 in changed_shot_keys(per_shot_revisions(before), per_shot_revisions(removed))


# --------------------------------- 发现 2：按 issue 授权（越权改动必拒）

def _authorized_script():
    return {"shots": [_shot(1), _shot(2, prompt="中景，听香跪坐船板")], "hook": None}


def test_prompt_only_repair_may_not_touch_motion_or_plan_or_kf():
    """**只授权 prompt 时，顺带改 motion / temporal_plan / KF 必须整批拒绝**（发现 2）.

    二次审核发现 2 的最小复现：固定 mask 只挡 index/旁白/字幕/场景/地点/
    variation_type，于是「本轮只要求修 prompt」的 replacement 可以同时把 motion
    与事件表改成「凭空改变剧情」。`temporal_plan` 进 revision 只能证明变化被哈希
    记录，不能证明变化得到授权；逐镜 revision gate 又有意允许被点名镜头改变，
    所以两者都拦不住这条旁路。
    """
    from step01_script_gen.main import _apply_shot_replacements
    from step01_script_gen.repair_fields import allowed_repair_fields

    script = _authorized_script()
    orig = script["shots"][0]
    allowed = allowed_repair_fields(["镜头1 prompt 长度超预算（320 字）"])
    assert allowed, "fixture 的告警必须能推出字段路径"

    new = _shot(1, prompt="近景，张十三抬手指向船外")
    new["motion_desc"] = "0-2秒：张十三凭空倒地；2-4秒：周身起火"
    new["temporal_plan"] = {"events": [
        {"start_sec": 0.0, "end_sec": 2.0, "description": "凭空倒地", "source_quote": ""},
        {"start_sec": 2.0, "end_sec": 4.0, "description": "突然起火", "source_quote": ""},
    ]}
    new["keyframes"] = [{"desc": "倒地", "t_sec": 0.5, "stage": "react"},
                        {"desc": "起火", "t_sec": 3.5, "stage": "after"}]

    merged, n, rejected = _apply_shot_replacements(
        script, [{"shot_id": "1", "shot": new}], {"shot_1"},
        allowed_by_target={"shot_1": allowed}, expected_targets={"1"})

    # v6.91（用户决定，2026-09-22）：执行方式从「越权即整批拒绝」改为
    # 「未授权路径**从原镜强制回填**」。不变量完全一样——motion / temporal_plan /
    # KF / 未授权字段**逐字节等于原值**——但现在由**构造**保证，而不是靠拒绝：
    # 授权字段的修复能落地，越权改动被丢掉而不是把整条替换作废。
    assert n == 1, "授权字段的修复必须提交（不再是整条拒绝）"
    assert merged["shots"][0]["prompt"] == "近景，张十三抬手指向船外", "授权字段生效"
    assert merged["shots"][0]["motion_desc"] == orig["motion_desc"], "越权改动必须回填"
    assert merged["shots"][0]["temporal_plan"] == orig["temporal_plan"]
    assert merged["shots"][0]["keyframes"] == orig["keyframes"]


def test_prompt_only_repair_is_accepted_when_only_prompt_changes():
    """同一授权下只改 prompt → 正常提交（授权判据不是「一律拒绝」）."""
    from step01_script_gen.main import _apply_shot_replacements
    from step01_script_gen.repair_fields import allowed_repair_fields

    script = _authorized_script()
    allowed = allowed_repair_fields(["镜头1 prompt 长度超预算（320 字）"])
    merged, n, rejected = _apply_shot_replacements(
        script, [{"shot_id": "1", "shot": _shot(1, prompt="近景，张十三抬手指向船外")}],
        {"shot_1"}, allowed_by_target={"shot_1": allowed}, expected_targets={"1"})
    assert (n, rejected) == (1, [])
    assert merged["shots"][0]["prompt"] == "近景，张十三抬手指向船外"


def test_time_issue_unlocks_time_fields():
    """时间问题明确点名时间字段时才放开（发现 2 实施方案 #3）."""
    from step01_script_gen.main import _apply_shot_replacements
    from step01_script_gen.repair_fields import allowed_repair_fields

    script = _authorized_script()
    allowed = allowed_repair_fields(["镜头1 motion 时段缺关键帧覆盖，需重排 kf t_sec"])
    assert "keyframes.*.t_sec" in allowed and "motion_desc" in allowed

    new = _shot(1)
    new["motion_desc"] = "0-2秒：张十三抬手；2-4秒：庄仆退下"
    new["keyframes"] = [{"desc": "抬手", "t_sec": 1.0, "stage": "approach"},
                        {"desc": "退下", "t_sec": 3.0, "stage": "after"}]
    merged, n, rejected = _apply_shot_replacements(
        script, [{"shot_id": "1", "shot": new}], {"shot_1"},
        allowed_by_target={"shot_1": allowed}, expected_targets={"1"})
    assert (n, rejected) == (1, []), rejected


def test_target_without_field_path_may_not_be_repaired():
    """问题推不出字段路径 → 该目标不得走定向修复（发现 2 实施方案 #1）."""
    from step01_script_gen.main import _apply_shot_replacements

    script = _authorized_script()
    merged, n, rejected = _apply_shot_replacements(
        script, [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")}], {"shot_1"},
        allowed_by_target={}, expected_targets={"1"})
    assert n == 0
    assert any("未授权" in r for r in rejected), rejected


# ------------------- v6.86（用户决定 A1）：逐目标提交与如实回报

def test_duplicate_shot_id_keeps_first_drops_second():
    """同一 shot_id 出现两次 → 丢后者、留前者（后一条不得静默覆盖前一条）."""
    from step01_script_gen.main import _apply_shot_replacements

    merged, n, rejected = _apply_shot_replacements(
        _script(),
        [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")},
         {"shot_id": "1", "shot": _shot(1, prompt="近景乙")}],
        {"shot_1"}, expected_targets={"1"})
    assert n == 1
    assert any("重复" in r for r in rejected), rejected
    assert merged["shots"][0]["prompt"] == "近景甲", "先到者胜，后者不得覆盖"


def test_missing_target_is_reported_but_others_commit():
    """picked 两个但只返回一个 → 缺项**如实回报**，不否决已提交的目标.

    这正是 2026-09-21 22:21:22 的真实形态：模型漏回一个目标，含合法替换的整批
    在旧语义下被回滚、修复轮产出 0。
    """
    from step01_script_gen.main import _apply_shot_replacements

    merged, n, rejected = _apply_shot_replacements(
        _script(), [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")}],
        {"shot_1", "shot_2"}, expected_targets={"1", "2"})
    assert n == 1, "已返回且合法的目标必须提交"
    assert merged["shots"][0]["prompt"] == "近景甲"
    assert any("未返回替换" in r and "2" in r for r in rejected), rejected


def test_extra_target_dropped_but_expected_commits():
    """返回了未被点名的镜头 → 丢那一条，本轮点名的目标照常提交."""
    from step01_script_gen.main import _apply_shot_replacements

    before_prompt = _script()["shots"][1]["prompt"]
    merged, n, rejected = _apply_shot_replacements(
        _script(),
        [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")},
         {"shot_id": "2", "shot": _shot(2, prompt="近景乙")}],
        # 镜头 2 在 picked 里但不在 expected_targets 里 = 集合本身不一致的情况
        {"shot_1", "shot_2"}, expected_targets={"1"})
    assert n == 1
    assert any("不在本轮点名集合内" in r for r in rejected), rejected
    assert merged["shots"][1]["prompt"] == before_prompt, "越界条目不得生效"


# --------------------------------- 发现 3：hook-only 主流程可达

def test_hook_only_envelope_reaches_applier(monkeypatch):
    """**主调用路径**上的 hook-only 修复必须可达（发现 3）.

    旧实现要求 `shot_replacements` 非空，而只点名 hook 的一轮，符合新 schema 的
    自然响应是 `{"shot_replacements": [], "hook_replacement": {...}}`——它在调用
    applier **之前**就被「未返回有效替换镜头」拒掉。这里走 `_repair_shot_warnings`
    的真实路径（mock 掉 provider），断言 hook 真的被替换。
    """
    import json as _json

    import step01_script_gen.main as m

    hook_new = _shot(0, prompt="特写，寒光一闪")
    envelope = _json.dumps({
        "shot_replacements": [],
        "hook_replacement": {"shot_id": "hook", "shot": hook_new},
    }, ensure_ascii=False)

    class _Result:
        success = True
        error = ""
        text = envelope

    class _Svc:
        def generate(self, req):
            return _Result()

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_collect_repair_warnings", lambda s, n: {
        "_has_hard": True, "shots": {"hook": ["hook prompt 长度超预算（340 字）"]}})

    # 记录主路径交给 applier 的实参——发现 3 的病灶是「在调用 applier **之前**
    # 就被非空校验拒掉」，所以断言点必须是「applier 真的收到了 hook_replacement」。
    # 接受判定（后处理副作用是否越权）是另一层契约，另有专测，此处不掺进来。
    seen = {}
    _real_apply = m._apply_shot_replacements

    def _spy(script_, repls, picked, hook_replacement=None, **kw):
        seen["repls"] = repls
        seen["hook_replacement"] = hook_replacement
        seen["picked"] = set(picked)
        return _real_apply(script_, repls, picked,
                           hook_replacement=hook_replacement, **kw)

    monkeypatch.setattr(m, "_apply_shot_replacements", _spy)

    script = {"shots": [_shot(1)], "hook": _shot(0, prompt="特写，匕首出鞘"),
              "total_duration": 12, "fps": 24}
    m._repair_shot_warnings(script, "novel")

    assert seen, "hook-only envelope 不得在调用 applier 之前被拒（发现 3）"
    assert seen["repls"] == [], "空 shot_replacements 是合法 envelope"
    assert isinstance(seen["hook_replacement"], dict)
    assert seen["hook_replacement"]["shot"]["prompt"] == "特写，寒光一闪"


def test_acceptance_gate_actually_runs(monkeypatch):
    """接受判定必须**真的执行**（回归：`allowed_targets` 曾是未定义名）.

    工作树里 `_repair_acceptance_decision(...)` 的第 3 个实参被写成
    `allowed_targets`，而它从未被定义——整段接受判定抛 `NameError`，被外层
    `except` 吞掉并**回退整轮修复**。后果是 v6.79～v6.82 新增的 non-target gate
    与最终字段 mask 在生产里从未真正生效。本用例锁住「不再有 NameError」。
    """
    import json as _json

    import step01_script_gen.main as m

    class _Result:
        success = True
        error = ""
        text = _json.dumps({"shot_replacements": [], "hook_replacement": None})

    class _Svc:
        def generate(self, req):
            return _Result()

    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(("info", str(msg)))

        def warn(self, msg, *a, **k):
            logs.append(("warn", str(msg)))

        def error(self, msg, *a, **k):
            logs.append(("error", str(msg)))

    monkeypatch.setattr(m, "_logger", _Log())
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_collect_repair_warnings", lambda s, n: {
        "_has_hard": True, "shots": {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}})
    # 让 applier 原样返回：本轮没有真实改动，接受判定应当正常跑完
    monkeypatch.setattr(m, "_apply_shot_replacements",
                        lambda script_, r, p, **kw: (script_, 1, []))

    script = {"shots": [_shot(1)], "hook": None, "total_duration": 12, "fps": 24}
    m._repair_shot_warnings(script, "novel")

    assert not [t for t in logs if "接受条件检查失败" in t[1]], (
        "接受判定抛异常（被静默吞掉并回退整轮）：%s" % logs
    )


# ------------------------------- 发现 3：模型失败不得进入提交尾部

class _FailResult:
    success = False
    error = "finish_reason=length（输出被截断，整份丢弃）"
    text = ""


class _FailService:
    def __init__(self, sink):
        self.sink = sink

    def generate(self, req):
        self.sink.append(req)
        return _FailResult()


def _repair_fixture(monkeypatch, *, service_factory, warnings=None):
    """把修复轮跑起来所需的外部依赖全部替换掉，返回 (module, script)."""
    import step01_script_gen.main as m

    calls = {"metrics": [], "post_fix": 0, "hard_fix": 0}
    monkeypatch.setattr(m, "create_service", service_factory)
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_collect_repair_warnings", lambda s, n: {
        "_has_hard": True,
        "shots": warnings or {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}})
    monkeypatch.setattr(m, "_metrics_inc",
                        lambda k, *a, **kw: calls["metrics"].append(k))
    monkeypatch.setattr(m, "_metrics_set", lambda *a, **kw: None)
    monkeypatch.setattr(m, "_metrics_update", lambda *a, **kw: None)

    import step01_script_gen.validation as v
    _real_pfix = v.post_fix_shot_prompts

    def _pfix(script, *a, **kw):
        calls["post_fix"] += 1
        return _real_pfix(script, *a, **kw)

    monkeypatch.setattr(v, "post_fix_shot_prompts", _pfix)
    import step01_script_gen.constraints as cons
    _real_hfix = cons.run_hard_fix

    def _hfix(script, *a, **kw):
        calls["hard_fix"] += 1
        return _real_hfix(script, *a, **kw)

    monkeypatch.setattr(cons, "run_hard_fix", _hfix)
    script = {"shots": [_shot(1), _shot(2, prompt="中景，听香跪坐船板")],
              "hook": None, "fps": 24, "total_duration": 12}
    return m, script, calls


def test_truncated_repair_returns_without_touching_anything(monkeypatch):
    """**截断（success=False）→ 立即返回**：不改任何镜头、不跑后处理、不做接受判定.

    发现 3 实测链路：8000 token 截断 → JSON 整份丢弃 → **同一秒仍继续跑全局
    `post_fix_shot_prompts()` 与 `run_hard_fix()`** → 未点名镜头 7 被改变 →
    事务以 `non_target_changed` 回滚（`resolved=0`）。模型没产出任何合法 replacement，
    却付出了「整本脚本后处理 + 整轮回滚」的代价。
    """
    from shared.quality_gate import per_shot_revisions

    sent = []
    m, script, calls = _repair_fixture(
        monkeypatch, service_factory=lambda *a, **k: _FailService(sent))
    before = per_shot_revisions(copy.deepcopy(script))

    out = m._repair_shot_warnings(script, "novel")

    assert out == script, "失败路径必须原样返回"
    assert per_shot_revisions(out) == before, "任何镜头都不得被改动"
    assert calls["post_fix"] == 0, "失败路径不得运行 post_fix"
    assert calls["hard_fix"] == 0, "失败路径不得运行 hard_fix"
    assert "repair.decisions" not in calls["metrics"], "失败路径不得进入接受判定"


def test_bad_json_returns_without_touching_anything(monkeypatch):
    sent = []

    class _BadJson:
        success = True
        error = ""
        text = "{ 这不是 JSON"

    class _Svc:
        def generate(self, req):
            sent.append(req)
            return _BadJson()

    from shared.quality_gate import per_shot_revisions

    m, script, calls = _repair_fixture(monkeypatch, service_factory=lambda *a, **k: _Svc())
    before = per_shot_revisions(copy.deepcopy(script))
    out = m._repair_shot_warnings(script, "novel")
    assert per_shot_revisions(out) == before
    assert calls["post_fix"] == 0 and calls["hard_fix"] == 0


def test_post_processing_inside_transaction_cannot_touch_untargeted_shots(monkeypatch):
    """事务内后处理只作用于被点名镜头（发现 3 实施 #3）.

    构造：模型给出合法 replacement；随后 post_fix 试图顺手改一个**未点名**镜头。
    修复前这会被 `non_target_changed` 判违规并整轮回滚（合法修复白做）；
    修复后未点名镜头被逐字节还原，事务照常提交。
    """
    import json as _json

    new_shot = _shot(1, prompt="近景，张十三抬手指向船外")

    class _Ok:
        success = True
        error = ""
        text = _json.dumps({"shot_replacements": [
            {"shot_id": "1", "shot": new_shot}], "hook_replacement": None},
            ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            return _Ok()

    m, script, calls = _repair_fixture(monkeypatch, service_factory=lambda *a, **k: _Svc())

    # post_fix 顺手改了未点名的镜 2（模拟跨镜约束的正常行为）
    import step01_script_gen.validation as v

    def _sneaky(script_, *a, **kw):
        for s in script_.get("shots") or []:
            if s.get("index") == 2:
                s["lf_desc"] = "被后处理污染的尾帧描述"
        return script_

    monkeypatch.setattr(v, "post_fix_shot_prompts", _sneaky)

    out = m._repair_shot_warnings(script, "novel")
    assert out["shots"][1]["lf_desc"] != "被后处理污染的尾帧描述", (
        "未点名镜头必须在事务末尾被还原"
    )


def test_shot_id_with_shot_prefix_is_accepted():
    """`shot_id="shot_10"` 必须被接受（2026-09-21 重跑实测的整批回滚根因）.

    修复轮的 REPAIR FEEDBACK 按 `shot_<id>` 分组，模型照抄这个键回填 `shot_id`，
    而 applier 只认裸 `10` → `_find()` 判「未被点名或不存在」→ 整批（含合法 hook
    替换）一起回滚、`resolved=0`。**反馈格式在教模型一个 applier 不认的写法**，
    所以修 applier 一侧（容忍两种写法），而不是只改 prompt 措辞。
    """
    from step01_script_gen.main import _apply_shot_replacements

    script = _script()
    script["hook"] = _shot(0, prompt="特写，匕首出鞘")
    merged, n, rejected = _apply_shot_replacements(
        script,
        [{"shot_id": "shot_1", "shot": _shot(1, prompt="近景甲")},
         {"shot_id": "shot_2", "shot": _shot(2, prompt="近景乙")}],
        {"shot_1", "shot_2", "hook"},
        hook_replacement={"shot_id": "hook", "shot": _shot(0, prompt="特写，寒光一闪")},
        expected_targets={"1", "2", "hook"})
    assert (n, rejected) == (3, []), rejected
    assert merged["shots"][0]["prompt"] == "近景甲"
    assert merged["hook"]["prompt"] == "特写，寒光一闪"


def test_norm_target_id_helper():
    from step01_script_gen.main import _norm_target_id
    assert _norm_target_id("shot_10") == "10"
    assert _norm_target_id("10") == "10"
    assert _norm_target_id("hook") == "hook"
    assert _norm_target_id("shot_12.5") == "12.5"
    assert _norm_target_id(None) == ""
    assert _norm_target_id("  shot_3 ") == "3"


def test_snapshot_restore_is_index_shift_tolerant():
    """快照/还原**按列表位置**为键，容忍轮内 index 重编号（v6.94）.

    旧实现按 `shot_{index}` 为键：轮内一旦发生重编号（`shot_index_renumber` 把
    半整数子镜归一成整数），`_restore_untargeted` 会把快照还原到**错的镜头**上，
    未点名镜头于是看起来被改过 → `非目标镜头被改动` → 整轮假拒绝。
    """
    from step01_script_gen.main import _restore_untargeted, _snapshot_untargeted

    script = {"shots": [{"index": 1, "prompt": "原始1"},
                        {"index": 2.5, "prompt": "原始2.5"}], "hook": None}
    snap = _snapshot_untargeted(script, {"shot_2.5"})       # 2.5 被点名 → 不快照

    script["shots"][0]["prompt"] = "被后处理改了"            # 后处理动了未点名镜头
    script["shots"][1]["index"] = 3                          # 轮内重编号 2.5 → 3

    assert _restore_untargeted(script, snap) == 1
    assert script["shots"][0]["prompt"] == "原始1", "按位置还原，不受 index 平移影响"
    assert script["shots"][1]["index"] == 3, "重编号（受认可的归一化）必须保留"


# ===========================================================================
# v6.98（2026-09-22 审核 P2-1）：mask 检查的**比较对象**必须是 merged
#
# 原实现先取值、后赋值：
#     _mask_violations_after_patch = _final_field_mask_violations(
#         _round_before_script, script, set(picked))
#     script = merged
# 此刻 `script` 仍是**旧稿**（该区间唯一的 `script =` 就在下一行），于是比较的是
# 「旧稿 vs 旧稿」——`_mask_violations_after_patch` **恒为空**，LLM 真改了锁定字段
# 也看不见，`locked_by_llm_changed` 成了死代码。
#
# 本用例不是静态检查赋值顺序，而是**用一个改了锁定字段的合并结果**跑完整一轮修复，
# 断言语义上真的命中：修法前 `locked_by_llm_changed` 为空 → 这条日志不会出现。
# ===========================================================================

class _OkService:
    def __init__(self, envelope: str):
        self.envelope = envelope

    def generate(self, req):
        class _R:
            success = True
            error = ""
            text = self.envelope

        return _R()


class _CapLog:
    def __init__(self):
        self.lines: list = []

    def info(self, msg, *a, **k):
        self.lines.append(str(msg))

    def warn(self, msg, *a, **k):
        self.lines.append(str(msg))

    def error(self, msg, *a, **k):
        self.lines.append(str(msg))


def test_locked_field_written_by_merge_is_caught_at_call_site(monkeypatch):
    """合并结果改了锁定字段（subtitle）→ 必须命中 `locked_by_llm_changed`."""
    import json as _json

    import step01_script_gen.main as m

    envelope = _json.dumps({
        "shot_replacements": [{"shot_id": "1", "shot": _shot(1, prompt="近景甲")}],
        "hook_replacement": None,
    }, ensure_ascii=False)
    _m, script, _calls = _repair_fixture(
        monkeypatch, service_factory=lambda *a, **k: _OkService(envelope))

    log = _CapLog()
    monkeypatch.setattr(m, "_logger", log)

    def _stub(script_, repls, picked, hook_replacement=None, **kw):
        """模拟「合并结果里锁定字段被 LLM 写了」——授权层之外的纵深防御路径."""
        merged = copy.deepcopy(script_)
        merged["shots"][0]["subtitle"] = "被 LLM 改过的锁定字段"
        return merged, 1, []

    monkeypatch.setattr(m, "_apply_shot_replacements", _stub)

    out = m._repair_shot_warnings(script, "novel")

    assert any("LLM 改动了锁定字段" in t for t in log.lines), (
        "mask 检查必须真的命中（修法前比较的是旧稿 vs 旧稿 → 恒为空）：%s" % log.lines
    )
    assert out is not script, "命中后必须回退本轮"
    assert out["shots"][0]["subtitle"] == _shot(1)["subtitle"], "回退后锁定字段必须是原值"


# ===========================================================================
# v6.101（2026-09-23，用户决定提高修复预算）
#
# 第十四次重跑：22 条阻塞分布在 14 个镜头上，而修复预算只有 **1 轮 × 3 镜**
# （`before=25 → after=25`）——KF 类那 17 条**全在修复轮白名单里**，只是排不进
# 那 3 个名额。真正的瓶颈是预算，不是任何单个检查器。
#
# 放开轮数时有个**必须一起改**的地方：原实现只有第 1 轮限量，第 2 轮起
# `picked = dict(shot_items)`（全部带告警的镜头）——那条路径在 `_REPAIR_MAX_ROUNDS=1`
# 下从未执行过，轮数一旦放开，第 2 轮会一次性发 10+ 个完整镜头对象 →
# `finish_reason=length` 整份丢弃。所以下面的用例同时锁住「多轮」与「每轮都限量」。
# ===========================================================================

def test_repair_budget_rounds_and_batch_are_both_applied(monkeypatch):
    """多轮 + **每轮都限量**，且每轮是独立事务（v6.101）.

    - 轮数：`_REPAIR_MAX_ROUNDS`（1 → 3）
    - 批量：`_REPAIR_MAX_SHOTS_PER_ROUND`（3 → 4），**每一轮**都限量
      （原实现只有第 1 轮限量，第 2 轮起 `picked = dict(shot_items)` 全量——
      轮数一放开就会一次性发 10+ 个镜头对象，`finish_reason=length` 整份丢弃）
    - 事务：接受判定从「循环外一次」改成「每轮一次」（否则前两轮的提交完全不设防）

    观测点选在 **applier 的 `picked`**（= 本轮真正点名的目标集合），
    而不是 prompt 文本——prompt 里有 few-shot 示例，正则数目标会数错。
    """
    import json as _json

    import step01_script_gen.main as m

    shots = [_shot(i) for i in range(1, 9)]
    calls = {"n": 0}
    _seen: dict = {}   # 稿件对象身份 → 已调用次数（G7 的快照深拷贝独立计数）

    def _warns(script_, novel):
        """每轮的调用节奏固定为 [轮首 before][轮尾 after][合并快照 merged]：

        第 2 次给「轻」状态（0 条），其余给「重」（8 条结构化 issues），
        于是每轮 `resolved=8 > 0` ⇒ 被接受 ⇒ 才会走到下一轮。

        ⚠️ 必须带 `issues`（结构化记录）：`resolved` 是拿
        `_blocking_record_keys(state["issues"])` 做差算出来的——只给 `shots` 里的
        告警文本会让「本轮目标集合」为空，验收退化成无条件通过，也就测不出多轮。

        ⚠️ 2026-11-14 P1/G7：**不能再按全局调用次数奇偶**判断。G7 的「后处理
        目标丢失」判据需要「合并后、后处理前」的确定性状态，验收判定因此会
        **额外**调用一次本收集器（传入合并快照的深拷贝），每轮从 2 次变 3 次
        （次序：轮首 → 轮尾 → 合并快照）。旧实现 `n % 2 == 1` 的奇偶因此整体
        错位——实测第 2 轮被误判成轻轮 ⇒ 目标集为空 ⇒ 不再发请求
        （`每轮都应发出一次请求：实际 1 次`）。按对象身份计数也不可靠（脚本
        对象在后处理里会被重建），故按每轮固定节奏判断。
        """
        calls["n"] += 1
        heavy = (calls["n"] % 3) in (0, 1)
        issues = ([{"code": "ff_lf_redundancy", "severity": "visual_risk",
                    "shot_id": i, "frame_id": "", "entity_id": "",
                    "field_path": "lf_desc", "message": "镜头%d 冗余" % i}
                   for i in range(1, 9)] if heavy else [])
        return {"_has_hard": True, "checker_failed": False, "issues": issues,
                "shots": {"shot_%d" % i: (["镜头%d FF/LF 相似度 92%%（时间不足）" % i]
                                          * max(1, 9 - i)) if heavy else []
                          for i in range(1, 9)}}

    picked_sets: list = []
    _real_apply = m._apply_shot_replacements

    def _spy(script_, repls, picked, **kw):
        picked_sets.append(set(picked))
        return _real_apply(script_, repls, picked, **kw)

    class _Svc:
        def generate(self, req):
            payload = {"shot_replacements": [
                {"shot_id": str(i), "shot": _shot(i)} for i in range(1, 5)],
                "hook_replacement": None}

            class _R:
                success = True
                error = ""
                text = _json.dumps(payload, ensure_ascii=False)

            return _R()

    _m, script, _c = _repair_fixture(monkeypatch, service_factory=lambda *a, **k: _Svc())
    script["shots"] = shots
    monkeypatch.setattr(m, "_collect_repair_warnings", _warns)
    monkeypatch.setattr(m, "_apply_shot_replacements", _spy)

    m._repair_shot_warnings(script, "novel")

    assert m._REPAIR_MAX_ROUNDS >= 2, "修复预算必须放开（第十四次实测瓶颈在这里）"
    assert len(picked_sets) == m._REPAIR_MAX_ROUNDS,         "每轮都应发出一次请求：实际 %d 次" % len(picked_sets)
    for picked in picked_sets:
        assert len(picked) <= m._REPAIR_MAX_SHOTS_PER_ROUND, (
            "每轮都必须限量（第 2 轮起不限量会撑爆输出 token）：本轮 %d 个目标"
            % len(picked))
        assert picked, "空目标不应发请求"
    assert "shot_1" in picked_sets[0], "第 1 轮应含告警最多的镜头 1"


def test_repair_output_token_budget_matches_the_batch(monkeypatch):
    """每批 4 镜 → 输出上限必须够（否则重演 v6.84 的 `finish_reason=length`）.

    v6.133（用户 2026-09-25，实跑 BLOCK 根因）：写死 `16000` 的做法已废——该上限**含思维链
    token**，同一请求同一 16000 实测一次截断一次成功（截断 → 整份丢弃 → 稿件零变化 →
    提交层误报"零改善"→ BLOCK）。改成默认**不设上限**（跟随所在通道的全局值），
    本用例钉住"够用"这个意图：默认不传，且两个通道的全局值都覆盖实测需求。

    v6.133（2026-09-26 复核）：取值口只在修复轮里调用**一次**（`_repair_cap`），
    既发给请求也随调用记录落台账（否则台账里没有上限、余量算不出来）。
    """
    import inspect

    import step01_script_gen.main as m

    src = inspect.getsource(m._repair_shot_warnings)
    assert "_repair_cap = _repair_max_output_tokens()" in src, "上限必须走统一取值口"
    assert src.count("_repair_max_output_tokens()") == 1, (
        "上限只该取一次：请求与台账记录必须同一个值")
    assert "max_tokens=_repair_cap," in src, "请求必须用这个值"
    monkeypatch.delenv("REPAIR_MAX_OUTPUT_TOKENS", raising=False)
    assert m._repair_max_output_tokens() is None, "默认跟随通道全局值（不额外收窄）"
    from _text_gen.config import config as _tc
    from shared.config import AGENT_PLAN_TEXT_MAX_TOKENS as _plan_cap
    _caps = [c for c in (getattr(_tc, "DEEPSEEK_MAX_TOKENS", None), _plan_cap) if c]
    # 2026-09-25 探针实测：成功一次 output_tokens 8172（其中思维链 3695）；
    # 截断那次 output_tokens 顶到 16000 —— 余量口径只看 output_tokens
    assert _caps and min(_caps) >= 16000, (
        "两个通道的全局上限都要 ≥ 实测需求（deepseek=%s / agent_plan=%s）"
        % (getattr(_tc, "DEEPSEEK_MAX_TOKENS", None), _plan_cap))


def test_round_with_no_improvement_is_rolled_back_and_stops(monkeypatch):
    """零改善的轮次：**回退本轮 + 停止后续轮次**（v6.102 #1）.

    第十五次实测第 2/3 轮 `resolved=0` 却仍被接受（靠陈旧的 `audit_resolved`），
    同一批 4 镜被白改两次、烧掉 2 次 LLM 调用。这里用**静态告警**（轮首轮尾相同）
    模拟空转：应当只发 1 次请求，且脚本逐字节回到本轮之前。
    """
    import json as _json

    import step01_script_gen.main as m

    shots = [_shot(i) for i in range(1, 9)]
    before_fp = _json.dumps(shots, ensure_ascii=False, sort_keys=True)

    reqs: list = []

    class _Svc:
        def generate(self, req):
            reqs.append(req.user_prompt)
            payload = {"shot_replacements": [
                {"shot_id": str(i), "shot": _shot(i)} for i in range(1, 5)],
                "hook_replacement": None}

            class _R:
                success = True
                error = ""
                text = _json.dumps(payload, ensure_ascii=False)

            return _R()

    _m, script, _calls = _repair_fixture(monkeypatch, service_factory=lambda *a, **k: _Svc())
    script["shots"] = shots
    # 静态告警：轮首轮尾完全一样 → resolved=0；且没有 audit issue → 新解决=0
    monkeypatch.setattr(m, "_collect_repair_warnings",
                        lambda s, n: {"_has_hard": True, "shots": {
                            "shot_%d" % i: ["镜头%d FF/LF 相似度 92%%（时间不足）" % i]
                            for i in range(1, 9)}})

    m._repair_shot_warnings(script, "novel")

    assert len(reqs) == 1, "零改善的轮次不得继续发下一批（否则就是空转）：%d 次" % len(reqs)
    assert _json.dumps(script["shots"], ensure_ascii=False, sort_keys=True) == before_fp, \
        "零改善 → 本轮改写必须回退（不留无收益的改动）"
