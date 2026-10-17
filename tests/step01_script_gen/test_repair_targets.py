# -*- coding: utf-8 -*-
"""成对修复目标 `repair_targets`：授权、联动组、成组回退（v6.119，无网络）.

## 为什么需要它（2026-09-25 真实重跑 + 离线取证）

第 1 轮定向前，审计对 shot 5 的 kf 时序问题给出正解「将 kf2 改为丢灯盏后已在船上的
倾听/回应态并排在跃起之后，**props 去掉灯盏**」——这要求**一起**改三处：
`keyframes[2].desc` + `keyframes[2].stage` + `frame_layouts.kfs.2.0.props`。
而授权此前只从**单条 `field_path`** 推出 `keyframes.*.desc`，于是：

```
[REPAIR] shot 5: 未授权路径已回填 3 处: ['frame_layouts.kfs.2.0.pose',
                                         'frame_layouts.kfs.2.0.props', 'keyframes.2.stage']
[REPAIR] 接受条件: before=0+audit4 after=3 resolved=0 字段触达=4 new=3
[REPAIR] 按目标回退 ['shot_5']（这些目标引入了新阻塞）后通过验收：resolved=0 new=0
```

半条修复自相矛盾 → 引入 3 条新阻塞 → 整镜回退 → **同一缺陷活到第 3 轮**
（台账 `r1∩r3` 按 `(shot_id, field_path)` 命中 `('5','keyframes[2].desc')`，
两轮 evidence 同为「灯盏在丢后又出现」）→ BLOCK。

跨镜那条同理：`STATE_CONTRADICTION` 把 `field_path` 写成 shot 5 的 `ff_desc`，
evidence 明说「shot5 ff 及 prompt 却写『…双目紧闭』，**shot7-9 同样写『双目紧闭』**」，
而授权按单镜 `field_path` 推导、目标集只按「问题定位在哪」挑 → 联动镜头既进不了本轮、
也拿不到授权，结构性不可能修好。

用户口径（v6.119）：**成对**的 `repair_targets` 是唯一授权来源；联动目标必须作为
**一个事务**（整组修 / 整组验收 / 整组回退），超容量就整组留给 BLOCK，不拆半次。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.quality_issues import Issue  # noqa: E402
from step01_script_gen.repair_fields import (  # noqa: E402
    allowed_repair_fields, validate_repair_targets, _target_id_of)


def _shot(index: int) -> dict:
    """够 `flatten_paths` 判真实叶子路径的最小镜头对象."""
    return {
        "index": index,
        "duration_sec": 6.0,
        "ff_desc": "杨旭仰面朝上双目紧闭",
        "lf_desc": "张十三已丢下灯盏，纵身跃上船梢",
        "motion_desc": "0-2秒：仰卧饮酒；2-4秒：丢酒壶；4-6秒：跃上船梢",
        "prompt": "古风权谋，暗调",
        "narration_segment": "",
        "keyframes": [
            {"t_sec": 1.0, "desc": "仰卧饮酒", "stage": "pre"},
            {"t_sec": 3.5, "desc": "丢灯盏纵身跃起", "stage": "climax"},
            {"t_sec": 4.5, "desc": "举灯盏站立倾听", "stage": "approach"},
        ],
        "frame_layouts": {
            "ff": [{"pose": "仰卧", "props": ["酒壶"]}],
            "lf": [{"pose": "跃上船梢", "props": []}],
            "kfs": [[{"pose": "举杯", "props": ["酒壶"]}],
                    [{"pose": "脱手", "props": []}],
                    [{"pose": "站立倾听", "props": ["灯盏"]}]],
        },
    }


def _shots(*indexes) -> dict:
    return {f"shot_{i}": _shot(i) for i in indexes}


# ---------------------------------------------------------------------------
# 授权：成对目标是唯一来源
# ---------------------------------------------------------------------------
def test_repair_cap_defaults_to_the_channels_global_not_a_hardcoded_16k(monkeypatch):
    """修复轮输出上限**默认不传**（跟随通道全局值）——v6.133 实跑 BLOCK 的根因（用户 2026-09-25）.

    原来硬编码 16000，理由是"agent_plan 全局 32768，16k 不触顶"；切到原生 DeepSeek 后该假设
    不成立，而 `max_output_tokens` **含思维链 token**：同一请求同一 16000，一次截断（整份丢弃 →
    稿件零变化 → 提交层误报"零改善"→ BLOCK）、一次成功（`reasoning 3695 / output 8172`）。
    `.env.content` 对修复轮的推荐值本就是 50000。
    """
    import step01_script_gen.main as m

    monkeypatch.delenv("REPAIR_MAX_OUTPUT_TOKENS", raising=False)
    assert m._repair_max_output_tokens() is None, "默认必须跟随通道全局值，不得再写死 16k"
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "16000")
    assert m._repair_max_output_tokens() == 16000, "显式收窄仍要生效"
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "abc")
    assert m._repair_max_output_tokens() is None, "非法值宁可不设上限，也不要把上限变小"


def test_pairs_authorize_the_whole_linked_field_set():
    """shot 5 的 kf 时序正解要三处一起改：desc + stage + 布局 props 都必须授权.

    这正是 2026-09-25 被回填的那两处（`keyframes.2.stage` /
    `frame_layouts.kfs.2.0.props`）——不授权它们，模型给的正解必然被砍成半条。
    """
    issue = Issue(
        code="model_audit_KF_ORDER_PROP_CONFLICT", severity="visual_risk",
        message="kf1 丢灯盏跃起，kf2 又举灯盏倾听，灯盏在丢后又出现",
        shot_id="5", frame_id="kf2", field_path="keyframes[2].desc",
        repair_targets=[
            {"shot_id": "5", "field_path": "keyframes[2].desc"},
            {"shot_id": "5", "field_path": "keyframes[2].stage"},
            {"shot_id": "5", "field_path": "frame_layouts.kfs.2.0.props"},
        ])

    allowed = allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                    shots_by_id=_shots(5))
    assert allowed == {"keyframes.2.desc", "keyframes.2.stage",
                       "frame_layouts.kfs.2.0.props"}
    assert "keyframes.*.desc" not in allowed, (
        "成对目标必须**精确到下标**：点名 kf2 不得授权同镜其他关键帧"
    )


def test_audit_payload_keeps_repair_targets_through_the_commit_path():
    """提交点那一跳必须带上成对目标：审计 payload(dict) → `_audit_payload_to_issues()`.

    这是 v6.119 审核实测的**断点**：该函数此前不传 `repair_targets`，模型就算输出
    了正确的成对目标，进到修复轮也已经丢了、授权退回旧单字段推导——整条成对授权链
    在生产提交路径上是**死的**。离线单测直接构造 `Issue`，覆盖不到这一跳，所以
    必须单独回归它。
    """
    import step01_script_gen.main as m

    _pairs = [
        {"shot_id": "5", "field_path": "keyframes[2].desc"},
        {"shot_id": "5", "field_path": "keyframes[2].stage"},
        {"shot_id": "5", "field_path": "frame_layouts.kfs.2.0.props"},
    ]
    payload = {"issues": [{
        "code": "model_audit_KF_ORDER_PROP_CONFLICT", "severity": "visual_risk",
        "message": "灯盏在丢后又出现", "shot_id": "5", "frame_id": "kf2",
        "field_path": "keyframes[2].desc", "repair_targets": _pairs}]}

    issues = m._audit_payload_to_issues(payload)
    assert len(issues) == 1
    assert issues[0].repair_targets == _pairs, "成对目标在提交点被丢掉了"

    # 端到端：这批 issue 进授权后，被回填过的两处应重新获得授权
    allowed = allowed_repair_fields([], issues, target_shot_id="shot_5",
                                    shots_by_id=_shots(5))
    assert allowed == {"keyframes.2.desc", "keyframes.2.stage",
                       "frame_layouts.kfs.2.0.props"}


def test_audit_payload_legacy_entry_without_the_field_stays_legacy():
    """payload 里**缺**该字段（旧审计产物）→ `None` → 走兼容路径，不得当成显式空."""
    import step01_script_gen.main as m

    payload = {"issues": [{"code": "text_integrity", "severity": "visual_risk",
                           "message": "m", "shot_id": "5", "frame_id": "ff",
                           "field_path": "ff_desc"}]}
    issues = m._audit_payload_to_issues(payload)
    assert issues[0].repair_targets is None
    assert allowed_repair_fields([], issues, target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == {
        "prompt", "ff_desc", "lf_desc", "motion_desc"}


def test_explicit_empty_array_gets_no_authorization():
    """显式 `[]` = 「这条问题不修」→ **不凭定位字段 / code 表**获得授权."""
    issue = Issue(code="text_integrity", severity="visual_risk", message="m",
                  shot_id="5", field_path="ff_desc", repair_targets=[])
    assert allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == set()


def test_explicit_empty_array_does_not_grant_the_kf_list():
    """显式 `[]` 也不得经由**结构码**拿到 `keyframes` 的增删权限.

    否则「模型说这条不修」会绕过成对目标、通过结构码又拿回一个比出问题的地方
    宽得多的授权面。（示例码 2026-09-27 换成 `kf_segment_missing`——缺段的
    结构权限现在只由它给。）
    """
    _empty = Issue(code="kf_segment_missing", severity="hard",
                   message="时段缺关键帧", shot_id="5",
                   field_path="keyframes[2].desc", repair_targets=[])
    assert allowed_repair_fields([], [_empty], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == set()

    # 对照：旧条目（字段缺失 = None）仍保留 v6.104 的结构能力
    _legacy = Issue(code="kf_segment_missing", severity="hard",
                    message="时段缺关键帧", shot_id="5",
                    field_path="keyframes[2].desc")
    assert "keyframes" in allowed_repair_fields(
        [], [_legacy], target_shot_id="shot_5", shots_by_id=_shots(5))


def test_normalizer_keeps_none_versus_empty_distinct():
    """解析层也要保住三态：缺字段 → None；显式空/null/非数组 → []."""
    from step01_script_gen.audit import _normalize_repair_targets

    assert _normalize_repair_targets({"code": "x"}) is None
    assert _normalize_repair_targets({"code": "x", "repair_targets": []}) == []
    assert _normalize_repair_targets({"code": "x", "repair_targets": None}) == []
    assert _normalize_repair_targets({"code": "x", "repair_targets": "nope"}) == []
    # 合法对照：整组都有效 ⇒ 原样归一（顺序保留）
    assert _normalize_repair_targets({"code": "x", "repair_targets": [
        {"shot_id": "5", "field_path": "ff_desc"},
        {"shot_id": "7", "field_path": "lf_desc"}]}) == [
            {"shot_id": "5", "field_path": "ff_desc"},
            {"shot_id": "7", "field_path": "lf_desc"}]


def test_normalizer_does_not_filter_a_bad_group_into_a_half_group():
    """**坏成员不得被过滤成半组**（复核 2026-10-04 第三轮）。

    既有边界是「一条问题的成对目标**任一成员无效 ⇒ 整条拒绝**」（v6.119：半组修出来的
    东西自相矛盾）。旧实现在解析层就把非对象/缺字段的成员**丢掉**，剩下的组成"半组"
    继续往下走——授权层拿到的已经是过滤后的列表，看不到被丢的那一半，
    这条边界因此**可绕**。现在整组先校验：有一个坏成员就整条 `[]`（显式零授权）。
    """
    from step01_script_gen.audit import _normalize_repair_targets

    for _bad in ("junk", {"shot_id": "", "field_path": "ff_desc"},
                 {"shot_id": "5", "field_path": ""}, 42, None):
        _grp = [{"shot_id": "5", "field_path": "ff_desc"}, _bad]
        assert _normalize_repair_targets({"code": "x", "repair_targets": _grp}) == [], _bad
    # 反例（改前的形态）：合法目标**不得**因同组有坏成员而单独获得授权
    _one = _normalize_repair_targets(
        {"code": "x", "repair_targets": [
            {"shot_id": "5", "field_path": "ff_desc"}, "junk"]})
    assert _one == [] and _one != [{"shot_id": "5", "field_path": "ff_desc"}]


def test_pairs_do_not_authorize_other_indices_of_the_same_field():
    """点名 kf2 → kf0/kf1 上**同名**字段的顺手改动必须被回填（索引精确）.

    旧实现把 `keyframes[2].desc` 归一成 `keyframes.*.desc`，「精确授权」名不副实。
    """
    import copy

    from step01_script_gen.repair_fields import merge_authorized

    target = _shot(5)
    fixed = copy.deepcopy(target)
    fixed["keyframes"][0]["desc"] = "被顺手改掉的 kf0"
    fixed["keyframes"][1]["desc"] = "被顺手改掉的 kf1"
    fixed["keyframes"][2]["desc"] = "点名的 kf2"

    allowed = allowed_repair_fields([], [_kf_order_issue()],
                                    target_shot_id="shot_5", shots_by_id=_shots(5))
    merged, echoed = merge_authorized(target, fixed, allowed)

    assert merged["keyframes"][2]["desc"] == "点名的 kf2"
    assert merged["keyframes"][0]["desc"] == target["keyframes"][0]["desc"]
    assert merged["keyframes"][1]["desc"] == target["keyframes"][1]["desc"]
    assert "keyframes.0.desc" in echoed and "keyframes.1.desc" in echoed


def test_validation_rejects_out_of_range_indices():
    """下标必须**真实存在**：`keyframes[99].desc` / `frame_layouts.kfs[9][0].props` 都要被拒.

    旧实现用 `flatten_paths` 的通配键做归一比对，两个越界下标都能通过——
    凭空给出一条不存在的目标。
    """
    valid, rejected = validate_repair_targets(
        [{"shot_id": "5", "field_path": "keyframes[99].desc"}], _shots(5))
    assert valid == [] and any("keyframes[99].desc" in r for r in rejected)

    valid2, rejected2 = validate_repair_targets(
        [{"shot_id": "5", "field_path": "frame_layouts.kfs[9][0].props"}], _shots(5))
    assert valid2 == [] and rejected2

    # 对照：真实存在的下标照常通过，且**保留下标**（不归一成 `*`）
    valid3, _ = validate_repair_targets(
        [{"shot_id": "5", "field_path": "keyframes[2].desc"}], _shots(5))
    assert valid3 == [{"shot_id": "5", "field_path": "keyframes[2].desc"}]


def test_fully_selected_group_is_still_registered_for_group_rollback():
    """组内镜头**已全被选中**时也必须登记为组——否则回退只回退其中一镜.

    （旧实现的闭包在 `_members <= _targets` 分支直接丢弃候选，不登记 → 后续
    「按目标回退」留下半组。）
    """
    import step01_script_gen.main as m

    issue = Issue(code="model_audit_X", severity="visual_risk", message="m",
                  shot_id="5", field_path="ff_desc",
                  repair_targets=[{"shot_id": s, "field_path": "ff_desc"}
                                  for s in ("5", "7")])
    # 两镜本来就都在 `picked` 里
    groups, _, oversize = m._linked_repair_groups(
        [issue], _shots(5, 7), {"shot_5": [], "shot_7": []}, cap=4)
    assert oversize == []
    assert groups == [frozenset({"shot_5", "shot_7"})], (
        "已全入选的组被丢弃 → 组回退只回退一镜"
    )
    out, hit = m._expand_rollback_by_groups({"shot_5"}, groups)
    assert out == {"shot_5", "shot_7"} and hit


def test_new_blocking_keys_are_not_truncated():
    """`new_blocking_keys` 不截断：它参与「定位肇事目标」的逻辑，不只用于日志."""
    import step01_script_gen.main as m

    _keys = [("5", f"model_audit_K{i}") for i in range(9)]
    _state_after = {"issues": [
        {"code": f"model_audit_K{i}", "severity": "visual_risk", "shot_id": "5",
         "frame_id": "ff", "field_path": "ff_desc"} for i in range(9)]}
    _decision = m._repair_acceptance_decision(
        {"issues": []}, _state_after, [], {"shots": []}, False)
    assert len(_decision["new_blocking_keys"]) == 9, (
        "截断到 6 会让按目标回退漏掉肇事目标，台账也追不全"
    )


def test_one_invalid_target_rejects_the_whole_issue():
    """联动问题**任一条目标无效 → 整条拒绝**，不留半组继续修（用户口径）."""
    import step01_script_gen.main as m

    issue = Issue(code="model_audit_KF_ORDER_PROP_CONFLICT", severity="visual_risk",
                  message="m", shot_id="5", field_path="keyframes[2].desc",
                  repair_targets=[
                      {"shot_id": "5", "field_path": "keyframes[2].desc"},
                      {"shot_id": "5", "field_path": "keyframes[2].stage"},
                      {"shot_id": "5", "field_path": "ff_desc.props"},   # ← 无效
                  ])
    assert allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == set(), "不得保留有效的那两条"

    # 也不得形成「半组」（sha_5 + 别的镜的有效条目）
    _cross = Issue(code="model_audit_X", severity="visual_risk", message="m",
                   shot_id="5", field_path="ff_desc",
                   repair_targets=[
                       {"shot_id": "5", "field_path": "ff_desc"},
                       {"shot_id": "7", "field_path": "ff_desc"},
                       {"shot_id": "9", "field_path": "ff_desc"},       # 镜头不存在
                   ])
    groups, rejects, oversize = m._linked_repair_groups(
        [_cross], _shots(5, 7), {"shot_5": []}, cap=4)
    assert groups == [] and oversize == [], "无效目标 → 整组不成立"
    assert rejects, "被拒原因仍要显式报出来"


def test_model_audit_issue_without_pairs_gets_no_legacy_fallback():
    """模型审计的成对目标是契约的一部分：缺 / 空 → 不授权，**不**退回定位字段推导.

    用户口径：不必兼容旧模型审计产物（审计版本已变，旧结论本就不可复用）；
    保留旧路径的只有**没有该契约的确定性质量门 issue**。
    """
    _missing = Issue(code="model_audit_text_integrity", severity="visual_risk",
                     message="m", shot_id="5", field_path="ff_desc")   # 无 repair_targets
    assert allowed_repair_fields([], [_missing], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == set()

    # 对照：确定性 issue（无 model_audit_ 前缀）仍走原路径
    _det = Issue(code="text_integrity", severity="hard", message="m",
                 shot_id="5", field_path="ff_desc")
    assert allowed_repair_fields([], [_det], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == {
        "prompt", "ff_desc", "lf_desc", "motion_desc"}


# ---------------------------------------------------------------------------
# 提交点契约门：不可执行 → 显式 BLOCK，不自动重发审计
# ---------------------------------------------------------------------------
def _audit_payload(repair_targets_marker, *, severity="visual_risk", shot_id="5"):
    import json as _json

    rec = {"code": "model_audit_KF_ORDER_PROP_CONFLICT", "severity": severity,
           "message": "m", "shot_id": shot_id, "frame_id": "kf2",
           "field_path": "keyframes[2].desc"}
    if repair_targets_marker is not _MISSING:
        rec["repair_targets"] = repair_targets_marker
    return _json.loads(_json.dumps({"issues": [rec]}))


class _MissingMarker:
    pass


_MISSING = _MissingMarker()


def test_contract_gate_flags_missing_empty_and_invalid_targets():
    import step01_script_gen.main as m

    _shots_by_id = _shots(5)
    # 缺字段
    assert m._audit_repair_target_problems(_audit_payload(_MISSING),
                                           _shots_by_id)
    # 显式空
    assert m._audit_repair_target_problems(_audit_payload([]), _shots_by_id)
    # 任一条无效
    _bad = m._audit_repair_target_problems(
        _audit_payload([{"shot_id": "5", "field_path": "keyframes[2].desc"},
                        {"shot_id": "5", "field_path": "ff_desc.props"}]),
        _shots_by_id)
    assert _bad and "整条拒绝" in _bad[0]
    # 全部有效 → 无问题
    assert m._audit_repair_target_problems(
        _audit_payload([{"shot_id": "5", "field_path": "keyframes[2].desc"}]),
        _shots_by_id) == []


def test_contract_gate_ignores_advisory_issues():
    """advisory 不驱动定向修复 → 不构成阻塞，缺目标也不报."""
    import step01_script_gen.main as m

    assert m._audit_repair_target_problems(
        _audit_payload(_MISSING, severity="advisory"), _shots(5)) == []


def test_contract_gate_message_points_at_the_contract_not_a_rerun():
    """报错文案必须指向「检查契约或稿件」，不得暗示原样重跑能解决."""
    import step01_script_gen.main as m

    _problems = m._audit_repair_target_problems(_audit_payload([]), _shots(5))
    assert _problems
    assert "契约" in _problems[0] or "定向修复" in _problems[0]


def test_localization_field_and_code_table_do_not_authorize():
    """`shot_id`/`field_path` 只做**问题定位**；`code→字段`表在成对模式下不参与."""
    # text_integrity 的 code 表会给 prompt/ff_desc/lf_desc/motion_desc
    issue = Issue(
        code="text_integrity", severity="visual_risk", message="文本完整性",
        shot_id="5", frame_id="ff", field_path="ff_desc",   # ← 定位在 ff_desc
        repair_targets=[{"shot_id": "5", "field_path": "lf_desc"}])

    allowed = allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                    shots_by_id=_shots(5))
    assert allowed == {"lf_desc"}, "只认成对目标：定位字段与 code 表都不得进授权"


def test_pair_pointing_at_a_foreign_shot_does_not_authorize_this_shot():
    issue = Issue(code="model_audit_X", severity="visual_risk", message="m",
                  shot_id="5", field_path="ff_desc",
                  repair_targets=[{"shot_id": "7", "field_path": "ff_desc"}])

    allowed = allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                    shots_by_id=_shots(5, 7))
    assert allowed == set(), "成对目标指向别镜 → 本镜无授权"


# ---------------------------------------------------------------------------
# 校验：镜头存在 + 真实叶子路径（失败即不授权，绝不从 minimal_fix 反推）
# ---------------------------------------------------------------------------
def test_composite_path_is_rejected_and_authorizes_nothing():
    """`ff_desc.props` 这种**组合路径**不是真实字段 → 整条剔除、不猜映射."""
    valid, rejected = validate_repair_targets(
        [{"shot_id": "5", "field_path": "ff_desc.props"}], _shots(5))
    assert valid == []
    assert rejected and "ff_desc.props" in rejected[0]

    issue = Issue(code="model_audit_X", severity="visual_risk", message="m",
                  shot_id="5", field_path="ff", repair_targets=[
                      {"shot_id": "5", "field_path": "ff_desc.props"}])
    assert allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                 shots_by_id=_shots(5)) == set()


def test_missing_shot_or_field_is_rejected():
    valid, rejected = validate_repair_targets(
        [{"shot_id": "99", "field_path": "ff_desc"},
         {"shot_id": "5", "field_path": ""},
         {"shot_id": "", "field_path": "ff_desc"},
         "not-a-dict"],
        _shots(5))
    assert valid == []
    assert len(rejected) == 4
    assert any("不存在" in r for r in rejected)


def test_structural_list_path_is_accepted_only_for_structural_codes():
    """`keyframes`（列表本身）只在「必须增删 KF 才能修」的问题下合法."""
    ok, _ = validate_repair_targets([{"shot_id": "5", "field_path": "keyframes"}],
                                    _shots(5), structural=True)
    assert ok == [{"shot_id": "5", "field_path": "keyframes"}]
    bad, rejected = validate_repair_targets(
        [{"shot_id": "5", "field_path": "keyframes"}], _shots(5), structural=False)
    assert bad == [] and rejected


# ---------------------------------------------------------------------------
# 兼容：旧 issue（没有 repair_targets）走原路径
# ---------------------------------------------------------------------------
def test_legacy_issue_without_pairs_keeps_the_old_derivation():
    issue = Issue(code="prompt_length_budget", severity="advisory", message="太长",
                  shot_id="5", field_path="prompt")
    allowed = allowed_repair_fields([], [issue], target_shot_id="shot_5",
                                    shots_by_id=_shots(5))
    assert allowed == {"prompt"}


def test_pairs_mode_is_opt_in_for_callers_that_pass_no_shot_context():
    """没给 `target_shot_id` = 旧行为逐字不变：`code→字段`表 + `field_path` 都照旧.

    （旧路径是「表 ∪ 定位字段」，与本文件其它用例的「只认成对目标」正好相反——
    这正是 `repair_targets` 引入时刻意保留的兼容面：旧审计产物没有成对目标。）
    """
    issue = Issue(code="text_integrity", severity="visual_risk", message="m",
                  shot_id="5", field_path="ff_desc",
                  repair_targets=[{"shot_id": "5", "field_path": "lf_desc"}])
    assert allowed_repair_fields([], [issue]) == {
        "prompt", "ff_desc", "lf_desc", "motion_desc"}


# ---------------------------------------------------------------------------
# 联动组：跨镜 issue 必须能到达组内每一镜
# ---------------------------------------------------------------------------
def test_cross_shot_issue_reaches_every_group_member():
    """跨镜睁眼案例：定位在 shot 5，但 shot 7/8/9 的 ff_desc 也要一起改."""
    issue = Issue(
        code="model_audit_STATE_CONTRADICTION", severity="visual_risk",
        message="尸体睁眼状态在相邻镜头来回跳变",
        shot_id="5", frame_id="ff", field_path="ff_desc",
        repair_targets=[{"shot_id": s, "field_path": "ff_desc"}
                        for s in ("5", "7", "8", "9")])

    for _tid in ("shot_5", "shot_7", "shot_8", "shot_9"):
        allowed = allowed_repair_fields([], [issue], target_shot_id=_tid,
                                        shots_by_id=_shots(5, 7, 8, 9))
        assert allowed == {"ff_desc"}, f"{_tid} 应拿到 ff_desc 授权"


def test_groups_merge_when_they_share_a_shot():
    from step01_script_gen.main import _linked_repair_groups

    a = Issue(code="c1", severity="visual_risk", message="m", shot_id="5",
              field_path="ff_desc",
              repair_targets=[{"shot_id": s, "field_path": "ff_desc"}
                              for s in ("5", "7")])
    b = Issue(code="c2", severity="visual_risk", message="m", shot_id="7",
              field_path="lf_desc",
              repair_targets=[{"shot_id": s, "field_path": "lf_desc"}
                              for s in ("7", "9")])

    groups, rejects, oversize = _linked_repair_groups(
        [a, b], _shots(5, 7, 9), {"shot_5": []}, cap=4)
    assert not rejects and not oversize
    assert len(groups) == 1, "共享 shot_7 的两组必须合成一个事务单元"
    assert groups[0] == frozenset({"shot_5", "shot_7", "shot_9"})


def test_oversize_group_is_left_for_block_not_split():
    """超过单轮容量 → 整组交给 BLOCK，**不拆**成可能互相矛盾的半次修复."""
    from step01_script_gen.main import _linked_repair_groups

    issue = Issue(code="c1", severity="visual_risk", message="m", shot_id="3",
                  field_path="ff_desc",
                  repair_targets=[{"shot_id": s, "field_path": "ff_desc"}
                                  for s in ("3", "4", "5", "6", "7")])
    groups, _, oversize = _linked_repair_groups(
        [issue], _shots(3, 4, 5, 6, 7), {"shot_3": []}, cap=4)
    assert groups == []
    assert len(oversize) == 1 and len(oversize[0]) == 5


def test_irrelevant_group_is_ignored():
    from step01_script_gen.main import _linked_repair_groups

    issue = Issue(code="c1", severity="visual_risk", message="m", shot_id="11",
                  field_path="ff_desc",
                  repair_targets=[{"shot_id": s, "field_path": "ff_desc"}
                                  for s in ("11", "12")])
    groups, _, oversize = _linked_repair_groups(
        [issue], _shots(5, 11, 12), {"shot_5": []}, cap=4)
    assert groups == [] and oversize == [], "既不含本轮目标、定位也不在本轮 → 不参与"


# ---------------------------------------------------------------------------
# 成组回退
# ---------------------------------------------------------------------------
def test_rollback_expands_to_the_whole_group():
    """组内任一镜肇事 → 整组回退（否则留下的是自相矛盾的半次修复）."""
    from step01_script_gen.main import _expand_rollback_by_groups

    out, hit = _expand_rollback_by_groups(
        {"shot_5"}, [frozenset({"shot_5", "shot_7", "shot_8", "shot_9"})])
    assert out == {"shot_5", "shot_7", "shot_8", "shot_9"}
    assert hit == [["shot_5", "shot_7", "shot_8", "shot_9"]]


def test_rollback_leaves_unrelated_targets_alone():
    from step01_script_gen.main import _expand_rollback_by_groups

    out, hit = _expand_rollback_by_groups(
        {"shot_2"}, [frozenset({"shot_5", "shot_7"})])
    assert out == {"shot_2"} and hit == []


# ---------------------------------------------------------------------------
# 合并语义：同一份正解，旧授权砍半、成对授权保全
# ---------------------------------------------------------------------------
def _complete_shot(index: int = 5) -> dict:
    """结构完整的镜头（与首次生成同构，供真实 applier/校验器使用）."""
    return {
        "index": index,
        "narration_segment": "听香：老爷！",
        "subtitle": "听香：老爷！",
        "scene": "照月湾", "location": "照月湾小舟", "main_subject": "张十三",
        "prompt": "中景，张十三立在船头",
        "motion_desc": "0-2秒：举灯盏；2-3.5秒：丢灯盏跃起；4.5-6秒：跳跃后倾听",
        "ff_desc": "张十三举灯盏站立",
        "lf_desc": "张十三已跃上船梢",
        "duration": 6.0, "duration_sec": 6.0, "variation_type": "small",
        "temporal_plan": {"events": [
            {"start_sec": 0.0, "end_sec": 2.0, "description": "举灯盏", "source_quote": ""},
            {"start_sec": 2.0, "end_sec": 6.0, "description": "跃起", "source_quote": ""},
        ]},
        "keyframes": [
            {"desc": "举灯盏站立", "t_sec": 1.0, "stage": "pre"},
            {"desc": "丢灯盏纵身跃起", "t_sec": 3.5, "stage": "climax"},
            {"desc": "举灯盏站立倾听船外", "t_sec": 4.5, "stage": "approach"},
        ],
        "frame_layouts": {
            "ff": [{"name": "张十三", "on_screen": True, "position": "画右",
                    "pose": "站立", "gaze_target": "画左", "props": ["灯盏"]}],
            "lf": [{"name": "张十三", "on_screen": True, "position": "画右",
                    "pose": "跃上船梢", "gaze_target": "画左", "props": []}],
            "kfs": [
                [{"name": "张十三", "on_screen": True, "position": "画右",
                  "pose": "站立", "gaze_target": "画左", "props": ["灯盏"]}],
                [{"name": "张十三", "on_screen": True, "position": "画右",
                  "pose": "跃起", "gaze_target": "画左", "props": []}],
                [{"name": "张十三", "on_screen": True, "position": "画右",
                  "pose": "站立倾听", "gaze_target": "画左", "props": ["灯盏"]}],
            ],
        },
    }


def _kf_fix(shot: dict) -> dict:
    """模型对该问题的正解：kf2 改为「丢灯盏后已在船上倾听」，stage 归位、布局去掉灯盏.

    ⚠️ `stage` 必须落在 `KF_STAGE_ENUM`（pre/approach/climax/react/exit/after）内。
    本夹具原先写 `"result"`——**不在枚举里**，会被 `_apply_shot_replacements` 判
    「keyframes[2].stage 非法」整条丢弃。而那条事务用例只断言「授权字段实际改动」
    日志，**那行日志发生在替换合法性校验之前**，所以它当时看起来是绿的，实际
    从未走到应用点（2026-09-25 用户核对发现）。
    """
    import copy as _copy

    out = _copy.deepcopy(shot)
    out["keyframes"][2]["desc"] = "丢灯盏后已在船上倾听"
    out["keyframes"][2]["stage"] = "react"
    out["frame_layouts"]["kfs"][2][0]["props"] = []
    return out


def _kf_order_issue() -> Issue:
    return Issue(code="model_audit_KF_ORDER_PROP_CONFLICT", severity="visual_risk",
                 message="kf1 丢灯盏跃起，kf2 又举灯盏倾听，灯盏在丢后又出现",
                 shot_id="5", frame_id="kf2", field_path="keyframes[2].desc",
                 repair_targets=[
                     {"shot_id": "5", "field_path": "keyframes[2].desc"},
                     {"shot_id": "5", "field_path": "keyframes[2].stage"},
                     {"shot_id": "5", "field_path": "frame_layouts.kfs.2.0.props"},
                 ])


def test_merge_keeps_the_linked_fix_that_the_old_path_would_cut_in_half():
    """同一份正解：成对授权保下三处；旧单字段授权回填两处（对照，pin 住差异）."""
    from step01_script_gen.repair_fields import merge_authorized

    target = _complete_shot(5)
    fixed = _kf_fix(target)

    allowed_new = allowed_repair_fields([], [_kf_order_issue()],
                                        target_shot_id="shot_5",
                                        shots_by_id=_shots(5))
    merged_new, echoed_new = merge_authorized(target, fixed, allowed_new)
    assert merged_new["keyframes"][2]["desc"] == "丢灯盏后已在船上倾听"
    assert merged_new["keyframes"][2]["stage"] == "react", "stage 必须留下"
    assert merged_new["frame_layouts"]["kfs"][2][0]["props"] == [], "灯盏必须去掉"
    assert "keyframes.2.stage" not in echoed_new
    assert "frame_layouts.kfs.2.0.props" not in echoed_new

    # 旧口径对照：授权只由**单字段** `field_path` 推导出来（`code` 不带
    # `model_audit_` 前缀，因为新规则下模型审计缺成对目标时**没有**旧路径可退——
    # 那是 v6.119 刻意的 fail-closed；这里要 pin 的是「那套推导会砍半」这件事本身）
    _legacy = Issue(code="kf_order_single_field", severity="visual_risk",
                    message="同上", shot_id="5", frame_id="kf2",
                    field_path="keyframes[2].desc")
    allowed_old = allowed_repair_fields([], [_legacy], target_shot_id="shot_5",
                                        shots_by_id=_shots(5))
    assert allowed_old == {"keyframes.*.desc"}
    merged_old, echoed_old = merge_authorized(target, fixed, allowed_old)
    assert merged_old["keyframes"][2]["stage"] == "approach", "旧口径下被回填"
    assert "keyframes.2.stage" in echoed_old
    assert "frame_layouts.kfs.2.0.props" in echoed_old


# ---------------------------------------------------------------------------
# 事务级：接线（`_issue_reaches` / `_all_structured`）也要走成对目标
# ---------------------------------------------------------------------------
def test_transaction_authorizes_the_linked_three_field_fix(monkeypatch):
    """跑一次真实定向修复事务：三处改动不得被回填，且**必须真的落到稿子上**.

    之前实测的失败形态：`未授权路径已回填 3 处: ['frame_layouts.kfs.2.0.pose',
    'frame_layouts.kfs.2.0.props', 'keyframes.2.stage']` → 半条修复 → 新阻塞 → 回退。

    ⚠️ 断言分两层，缺一不可（2026-09-25 用户核对）：
      · 「授权字段实际改动」这行日志发生在**替换合法性校验之前**（`_apply_shot_replacements`
        在它之后才逐个目标校验），所以它只能证明**合并**没被回填，**不能**证明替换
        真的应用了——夹具当时给的 `stage="result"` 非法，这一层其实从未通过；
      · 因此必须再断言 `应用完整镜头替换` 与**改后镜头内容**（脚本是原地改写的）。
    """
    import copy
    import json as _json

    import step01_script_gen.main as m

    fixed = _kf_fix(_complete_shot(5))

    class _Ok:
        success = True
        error = ""
        text = _json.dumps({"shot_replacements": [{"shot_id": "5", "shot": fixed}],
                            "hook_replacement": None}, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            return _Ok()

    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(str(msg))

        def warn(self, msg, *a, **k):
            logs.append(str(msg))

        def error(self, msg, *a, **k):
            logs.append(str(msg))

        def verbose(self, msg, *a, **k):
            pass

        def console(self, msg, *a, **k):
            pass

    monkeypatch.setattr(m, "_logger", _Log())
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        monkeypatch.setattr(m, _fn, lambda *a, **k: None)

    def _fake_warnings(_script, _novel):
        """确定性问题在 kf2 修好之前**一直报**（真实检查器的形态）.

        它同时是验收判据的来源：`resolved = before_keys - after_keys` 只有在这个
        问题真的从稿子上消失时才 > 0。若用常量桩，`after` 恒等于 `before` →
        `resolved=0`、`audit_field_touched=0` → `no_target_resolved` → **整轮回退**，
        于是「断言改后内容」永远只能看到原样（这不是修复没生效，是没通过验收）。
        """
        _kfs = (_script.get("shots") or [{}])[0].get("keyframes") or []
        _kf2 = str(_kfs[2].get("desc") if len(_kfs) > 2 else "")
        return {
            "_has_hard": True,
            "shots": {"shot_5": ["[kf_time_window_alignment] 时段缺关键帧"]},
            "issues": [] if "丢灯盏后" in _kf2 else [{
                "code": "kf_event_window_mismatch", "severity": "visual_risk",
                "shot_id": "5", "frame_id": "kf2", "entity_id": "",
                "field_path": "keyframes.desc",
                "message": "kf2 与 motion 时段不一致"}],
        }

    monkeypatch.setattr(m, "_collect_repair_warnings", _fake_warnings)

    script = {"shots": [copy.deepcopy(_complete_shot(5))], "hook": None,
              "fps": 24, "total_duration": 6}
    # ⚠️ 必须用**返回值**：该事务内部是 `script = merged` 重绑定（不原地改写），
    # 忽略返回值就永远只能看到调用前的稿子——「断言改后内容」会变成一条恒错的假断言。
    _out = m._repair_shot_warnings(script, "novel", audit_issues=[_kf_order_issue()])

    _backfill = [t for t in logs if "未授权路径已回填" in t]
    assert not any("keyframes.2.stage" in t for t in _backfill), (
        "stage 仍被回填（成对授权没接上事务）: %s" % _backfill)
    assert not any("frame_layouts.kfs.2.0.props" in t for t in _backfill), (
        "布局 props 仍被回填: %s" % _backfill)

    _touched = [t for t in logs if "授权字段实际改动" in t]
    assert _touched, "事务没走到合并步骤：%s" % logs[:20]
    assert "keyframes.*.stage" in _touched[0]
    assert "frame_layouts.kfs.*.*.props" in _touched[0]

    # 第二层：必须真的应用（走到 applier 之后），并且三处改动落在改后稿子上。
    assert any("应用完整镜头替换 1 镜" in t for t in logs), (
        "替换没被应用（检查是否在合法性校验处被整条拒绝）：%s" % logs[:20])
    assert not [t for t in logs if "均未通过校验" in t], logs[:20]
    _after = _out["shots"][0]
    _accept = [t for t in logs if ("接受条件" in t or "回退" in t or "零改善" in t)]
    assert _after["keyframes"][2]["desc"] == "丢灯盏后已在船上倾听", (
        "授权字段的替换没有留在稿子上。验收轨迹：%s" % _accept)
    assert _after["keyframes"][2]["stage"] == "react", (
        "stage 没落到稿子上：%r" % _after["keyframes"][2].get("stage"))
    assert _after["frame_layouts"]["kfs"][2][0]["props"] == [], (
        "布局条目里的灯盏没被去掉：%r"
        % _after["frame_layouts"]["kfs"][2][0].get("props"))


# ---------------------------------------------------------------------------
# 台账：回退**之前**的新增 blocking 身份必须留下来
# ---------------------------------------------------------------------------
@pytest.fixture
def chapter(tmp_path, monkeypatch):
    import step01_script_gen.main as m
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    return tmp_path


def test_repair_ledger_records_new_blocking_keys_with_run_context(chapter):
    """`new=3` 不能只剩计数——keys、落点、回退目标、修前修后 revision 都要在."""
    import step01_script_gen.main as m

    m._record_repair_diag(
        run_id="20260925T123347-44600", attempt=1,
        records=[{
            "repair_round": 1,
            "targets": ["shot_2", "shot_4", "shot_5"],
            "groups": [["shot_5", "shot_7", "shot_8", "shot_9"]],
            "new_blocking_keys": [["5", "model_audit_STATE_CONTRADICTION"],
                                  ["5", "model_audit_KF_ORDER_PROP_CONFLICT"],
                                  ["2", "model_audit_PROP_STATE_CONTRADICTION"]],
            "new_blocking_targets": ["shot_2", "shot_5"],
            "rolled_back_groups": [["shot_5", "shot_7", "shot_8", "shot_9"]],
            "accepted": True, "round_ok": True, "resolved": 0,
            "reason": "new_blocking",
            "revision_before": "aaa", "revision_after": "bbb",
        }])

    rows = json.loads((chapter / "step01_repair_rounds.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    rec = rows[0]
    assert rec["run_id"] == "20260925T123347-44600"
    assert rec["commit_round"] == 1
    assert rec["repair_round"] == 1
    assert rec["new_blocking_keys"] == [["5", "model_audit_STATE_CONTRADICTION"],
                                        ["5", "model_audit_KF_ORDER_PROP_CONFLICT"],
                                        ["2", "model_audit_PROP_STATE_CONTRADICTION"]]
    assert rec["rolled_back_groups"] == [["shot_5", "shot_7", "shot_8", "shot_9"]]
    assert rec["revision_before"] == "aaa" and rec["revision_after"] == "bbb"


def test_repair_ledger_never_touches_the_official_artifacts(chapter):
    import step01_script_gen.main as m

    official = chapter / "step01_audit.json"
    sentinel = {"status": "ok", "sentinel": "do-not-touch"}
    official.write_text(json.dumps(sentinel), encoding="utf-8")

    m._record_repair_diag(run_id="r1", attempt=1,
                          records=[{"repair_round": 1, "new_blocking_keys": []}])

    assert json.loads(official.read_text(encoding="utf-8")) == sentinel
    assert (chapter / "step01_repair_rounds.json").exists()


def test_repair_ledger_write_failure_only_warns(chapter, monkeypatch):
    import step01_script_gen.main as m

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(m, "_read_json_list_quiet", _boom)
    m._record_repair_diag(run_id="r1", attempt=1,
                          records=[{"repair_round": 1}])          # 不得抛出


def test_repair_ledger_is_a_noop_without_records(chapter):
    import step01_script_gen.main as m

    m._record_repair_diag(run_id="r1", attempt=1, records=[])
    assert not (chapter / "step01_repair_rounds.json").exists()


# ---------------------------------------------------------------------------
# 镜头号记法归一（v6.120，2026-09-25 真实 BLOCK 的输入形态）
#
# 真实 BLOCK：同一份提示词、temperature=0.0，提交第 1 轮审计写 `"5"`、第 2 轮写
# `"shot5"`（`step01_audit_rounds.json` 逐轮实拍）。旧 `_target_id_of` 只会拼出
# `shot_shot5` → 判「该镜头不存在于本稿」→ 5 条 blocking **整条拒绝** → 契约门
# BLOCK，两条修复轮一次没花。根因是**提示词没钉死记法、而例子写的是 `shot5`**，
# 所以两侧一起修：契约侧（schema description + 规则举例改裸数字）+ 读取侧归一。
# ---------------------------------------------------------------------------
def _shots_with_hook() -> dict:
    d = _shots(5)
    d["hook"] = _shot(0)
    return d


def test_shot5_notation_is_canonicalized_and_authorizes():
    """`"shot5"` 必须与 `"5"` **等价**：指向不变，授权集合逐项相同."""
    _shot5_pairs = [{"shot_id": "shot5", "field_path": "keyframes[2].desc"},
                    {"shot_id": "shot5", "field_path": "keyframes[2].stage"},
                    {"shot_id": "shot5", "field_path": "frame_layouts.kfs.2.0.props"}]
    _bare_pairs = [{**p, "shot_id": "5"} for p in _shot5_pairs]

    _valid, _rejected = validate_repair_targets(_shot5_pairs, _shots(5))
    assert not _rejected and len(_valid) == 3, (
        "记法差异不得被当成「目标无效」：%s" % _rejected)
    assert {_target_id_of(p["shot_id"]) for p in _valid} == {"shot_5"}

    def _allowed(pairs):
        return allowed_repair_fields(
            [], [Issue(code="model_audit_KF_ORDER_PROP_CONFLICT",
                       severity="visual_risk", message="m", shot_id="5",
                       frame_id="kf2", field_path="keyframes[2].desc",
                       repair_targets=pairs)],
            target_shot_id="shot_5", shots_by_id=_shots(5))

    assert _allowed(_shot5_pairs) == _allowed(_bare_pairs) == {
        "keyframes.2.desc", "keyframes.2.stage", "frame_layouts.kfs.2.0.props"}


def test_alias_notations_cover_the_common_variants():
    for _raw, _want in (("shot5", "shot_5"), ("shot_5", "shot_5"),
                        ("SHOT_5", "shot_5"), ("shot-5", "shot_5"),
                        (" shot 5 ", "shot_5"), ("5", "shot_5"), ("05", "shot_5")):
        assert _target_id_of(_raw) == _want, _raw


def test_hook_aliases_map_to_the_hook_target():
    assert _target_id_of("0") == _target_id_of("hook") == "hook"
    _valid, _rejected = validate_repair_targets(
        [{"shot_id": "hook", "field_path": "ff_desc"}], _shots_with_hook())
    assert not _rejected and _valid


def test_gate_accepts_the_real_round2_payload_shape():
    """Real BLOCK payload（节选）：5 条 blocking 全部写 `"shotN"` → 门必须放行."""
    import step01_script_gen.main as m

    _real = {"issues": [
        {"code": "model_audit_MOTION_LF_MISMATCH", "severity": "visual_risk",
         "message": "m", "shot_id": "shot5", "frame_id": "lf",
         "field_path": "lf_desc",
         "repair_targets": [{"shot_id": "shot5", "field_path": "motion_desc"},
                            {"shot_id": "shot5", "field_path": "lf_desc"}]},
        {"code": "model_audit_KEYFRAME_ORDER_CONFLICT", "severity": "visual_risk",
         "message": "m", "shot_id": "shot5", "frame_id": "kf2",
         "field_path": "keyframes[2].desc",
         "repair_targets": [{"shot_id": "shot5", "field_path": "keyframes[1].desc"},
                            {"shot_id": "shot5", "field_path": "keyframes[2].desc"},
                            {"shot_id": "shot5",
                             "field_path": "frame_layouts.kfs[2][0].props"}]},
        {"code": "model_audit_TEXT_REPEAT_CORRUPTION", "severity": "visual_risk",
         "message": "m", "shot_id": "shot8", "frame_id": "",
         "field_path": "motion_desc",
         "repair_targets": [{"shot_id": "shot8", "field_path": "motion_desc"}]},
        {"code": "model_audit_LF_MOTION_CONTRADICTION", "severity": "visual_risk",
         "message": "m", "shot_id": "shot10", "frame_id": "",
         "field_path": "lf_desc",
         "repair_targets": [{"shot_id": "shot10", "field_path": "lf_desc"}]},
        {"code": "model_audit_TEXT_MERGE_CORRUPTION", "severity": "visual_risk",
         "message": "m", "shot_id": "shot4", "frame_id": "",
         "field_path": "motion_desc",
         "repair_targets": [{"shot_id": "shot4", "field_path": "motion_desc"}]},
    ]}
    _shots_by_id = _shots(*range(1, 12))

    assert m._audit_repair_target_problems(_real, _shots_by_id) == [], (
        "记法（`shot5`）不得让 5 条 blocking 全部整条拒绝 → BLOCK")


def test_gate_still_blocks_a_shot_that_really_does_not_exist():
    """归一**不能**放宽到「不存在的镜头」：`shot99` 照旧整条拒绝（fail-closed）."""
    import step01_script_gen.main as m

    _bad = _audit_payload([{"shot_id": "shot99", "field_path": "ff_desc"}],
                          shot_id="shot99")
    _problems = m._audit_repair_target_problems(_bad, _shots(5))
    assert _problems and "整条拒绝" in _problems[0]
    assert "shot_99" in _problems[0] and "不存在于本稿" in _problems[0], (
        "记法归一后仍是**真实不存在**的镜头 → 报错要给出归一后的 target 名：%s"
        % _problems[0])


def test_unresolvable_notation_is_never_guessed():
    """真指不明的写法**不做任何猜测**（`第5镜` / `shot_x` / `shot_5.1`）."""
    for _raw in ("第5镜", "shot_x", "shot_5.1", "5 号镜", "shot"):
        _valid, _rejected = validate_repair_targets(
            [{"shot_id": _raw, "field_path": "ff_desc"}], _shots(5))
        assert not _valid and _rejected, _raw


def test_alias_normalization_is_observable():
    """归一属输入形态变化，必须能在日志里看见（准则 7），且只报真正非规范的写法."""
    from step01_script_gen.repair_fields import shot_id_aliases

    _notes = shot_id_aliases([
        {"shot_id": "5", "repair_targets": [{"shot_id": "shot5"},
                                            {"shot_id": "5"}]},
        {"shot_id": "shot_8", "repair_targets": [{"shot_id": "8"}]},
        {"shot_id": "hook", "repair_targets": [{"shot_id": "0"}]},
    ])
    assert _notes == ["shot5→shot_5", "shot_8→shot_8"]
    assert shot_id_aliases([{"shot_id": "5"}]) == []


def test_main_target_of_record_is_the_single_chokepoint():
    """`main._target_of_record` 必须与 `_target_id_of` 同口径——两处派生过一次就漏一次."""
    import step01_script_gen.main as m

    assert m._target_of_record({"shot_id": "shot5"}) == "shot_5"
    assert m._target_of_record(Issue(code="model_audit_X", severity="visual_risk",
                                     message="m", shot_id="shot5")) == "shot_5"
    assert m._target_of_record({"shot_id": None}) == _target_id_of(None)
    assert m._target_of_record({"shot_id": "hook"}) == "hook"


def test_audit_contract_pins_the_bare_index_notation():
    """契约侧回归：规则举例如再用 `shot5`，模型就会照抄——这条把它钉死.

    真实证据：第 1 轮 `"5"`、第 2 轮 `"shot5"`，两轮提示词完全相同（temperature=0.0），
    差别只在读法——因为规则里唯一出现的镜头号举例写的就是 `shot5`。
    """
    import re as _re

    import step01_script_gen.audit as audit

    _rules = audit._AUDIT_RULES_TEMPLATE
    # `shot5` 只允许作为**反面例子**出现（「不要写 `shot5`」），绝不能再作为示例值——
    # 真实 BLOCK 的成因就是规则里唯一那处镜头号举例用的正是 `shot5`。
    _hits = [ln for ln in _rules.splitlines() if _re.search(r"shot\s?\d", ln)]
    assert all("不要写" in ln for ln in _hits), (
        "规则里出现了非反面的 `shotN` 写法（模型会照抄）：%s" % _hits)
    assert "裸数字" in _rules, "规则必须显式给出 `shot_id` 的规范写法"

    _schema = audit.AUDIT_SCHEMA["schema"]["properties"]["issues"]["items"]
    assert "裸数字" in _schema["properties"]["shot_id"]["description"]
    assert "裸数字" in (_schema["properties"]["repair_targets"]["items"]
                        ["properties"]["shot_id"]["description"]), (
        "两处 shot_id 都要在 schema 里钉死记法（成对目标那处才是授权来源）")


def test_transaction_never_asks_for_a_phantom_shot_target():
    """`shot_id` 写成 `"shot5"` 的审计条目，不得在修复事务里变成一个**幻影目标**.

    离线实拍（同一份输入，改前后各跑一遍真实事务）：

      · 旧实现（`audited.setdefault(f"shot_{_sid}", ...)`）：
        `[修复轮1] 2 镜待修（shot_5, shot_shot5）` → LLM 被要求修一个不存在的镜头；
        applier 的期望集合里也多出 `"shot5"` →
        `部分提交说明: ["目标 ['shot5'] 本轮未返回替换（其余 1 个目标已提交）"]`。
      · 新实现：`[修复轮1] 1 个目标：shot_5`，替换正常提交。

    注意：这条**不是**在测 `audit_targets`/`+auditN`——旧实现的幻影目标同样进了
    `picked`，所以那个计数在修复前也是 1（实测），拿它当判据会是一条永远绿的假回归。
    """
    import copy
    import json as _json

    import step01_script_gen.main as m

    _issue_alias = Issue(
        code="model_audit_KF_ORDER_PROP_CONFLICT", severity="visual_risk",
        message="kf1 丢灯盏跃起，kf2 又举灯盏倾听，灯盏在丢后又出现",
        shot_id="shot5", frame_id="kf2", field_path="keyframes[2].desc",
        repair_targets=[
            {"shot_id": "shot5", "field_path": "keyframes[2].desc"},
            {"shot_id": "shot5", "field_path": "keyframes[2].stage"},
            {"shot_id": "shot5", "field_path": "frame_layouts.kfs.2.0.props"},
        ])

    fixed = _kf_fix(_complete_shot(5))

    class _Ok:
        success = True
        error = ""
        text = _json.dumps({"shot_replacements": [{"shot_id": "5", "shot": fixed}],
                            "hook_replacement": None}, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            return _Ok()

    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(str(msg))

        def warn(self, msg, *a, **k):
            logs.append(str(msg))

        def error(self, msg, *a, **k):
            logs.append(str(msg))

        def verbose(self, msg, *a, **k):
            pass

        def console(self, msg, *a, **k):
            pass

    monkeypatch_holder = pytest.MonkeyPatch()
    monkeypatch_holder.setattr(m, "_logger", _Log())
    monkeypatch_holder.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch_holder.setattr(m, "resolve_model_prompt_profile",
                               lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch_holder.setattr(m, "_collect_repair_warnings", lambda s, n: {
        "_has_hard": True,
        "shots": {"shot_5": ["[kf_time_window_alignment] 时段缺关键帧"]}})
    for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        monkeypatch_holder.setattr(m, _fn, lambda *a, **k: None)
    try:
        script = {"shots": [copy.deepcopy(_complete_shot(5))], "hook": None,
                  "fps": 24, "total_duration": 6}
        m._repair_shot_warnings(script, "novel", audit_issues=[_issue_alias])

        # 判别性判据（离线实拍 old/new 两版日志得出的差异，见 docstring）：
        #   ① 任何日志都不得出现拼坏的目标名 `shot_shot5`；
        #   ② applier 只应期待**一个**目标（`shot_5`）——旧实现把 `shot_shot5`
        #      也算成 picked 目标，日志里会出现「目标 ['shot5'] 本轮未返回替换」；
        #   ③ 该镜的替换必须被提交。
        _bad = [t for t in logs if "shot_shot" in t]
        assert not _bad, "出现了拼坏的镜头名（`shot5` 被当成「shot」+ 原串）：%s" % _bad
        assert not [t for t in logs if "本轮未返回替换" in t], (
            "applier 把一个不存在的目标算成了「应返回替换」：%s" % logs[:20])
        assert any("应用完整镜头替换 1 镜" in t for t in logs), (
            "本轮唯一的真实目标（shot_5）没有被提交：%s" % logs[:20])
    finally:
        monkeypatch_holder.undo()


# ---------------------------------------------------------------------------
# 空 / 指不明的 `shot_id`：**无法证明已解决**（v6.120，用户口径）
#
# 旧实现把它读成「目标镜头已被结构补丁删除」→ `_audit_issue_resolved()` 返回 True。
# 正式修复入口传的是**修前稿**，改前改后同为 True → `newly_resolved` 仍为 False，
# 所以它不是本次 BLOCK 的成因；补的是「不把定位不到当成已解决」这条口径本身。
# ---------------------------------------------------------------------------
def _script_with_shots(*indexes) -> dict:
    return {"shots": [_complete_shot(i) for i in indexes], "hook": None}


def test_issue_shot_index_is_the_single_identity_mapping():
    """`_issue_shot_index` 必须与 `_target_id_of` 同口径（记法归一 + hook + 空）."""
    import step01_script_gen.main as m

    for _raw, _want in (("5", "5"), ("shot5", "5"), ("shot_5", "5"), ("05", "5"),
                        ("hook", "hook"), ("0", "hook")):
        assert m._issue_shot_index(Issue(code="c", severity="visual_risk",
                                         message="m", shot_id=_raw)) == _want, _raw
    # 「指不明」= 既不是 hook 也不是纯数字串（空串也在此列）→ 调用方按定位不到处理
    for _raw in ("", None, "shot_x", "第5镜"):
        _idx = m._issue_shot_index(Issue(code="c", severity="visual_risk",
                                         message="m", shot_id=_raw))
        assert not (_idx == "hook" or _idx.isdigit()), repr(_raw)


def test_unlocated_issue_is_not_reported_as_resolved():
    """空 / 指不明的 `shot_id` → 「无法证明已解决」（False），而不是「镜头已删除」."""
    import step01_script_gen.main as m

    _ev = "举灯盏站立倾听" * 2          # ≥8 字，且**不存在于**稿子里
    script = _script_with_shots(5)
    for _raw in ("", None, "shot_x", "第5镜"):
        _issue = Issue(code="model_audit_X", severity="visual_risk", message="m",
                       shot_id=_raw, field_path="ff_desc", evidence=_ev)
        assert m._issue_target_shot(script, _issue) is None, repr(_raw)
        assert m._audit_issue_resolved(script, _issue) is False, (
            "定位不到的 issue 被判成了「已解决」：%r" % _raw)


def test_removed_shot_and_hook_still_count_as_resolved():
    """对照：**能定位**但镜头确实不在稿里（结构补丁删镜 / 关掉钩子）仍算已解决."""
    import step01_script_gen.main as m

    _ev = "举灯盏站立倾听" * 2
    _gone = Issue(code="model_audit_X", severity="visual_risk", message="m",
                  shot_id="7", field_path="ff_desc", evidence=_ev)
    assert m._audit_issue_resolved(_script_with_shots(5), _gone) is True

    _hook = Issue(code="model_audit_X", severity="visual_risk", message="m",
                  shot_id="hook", field_path="ff_desc", evidence=_ev)
    assert m._audit_issue_resolved(_script_with_shots(5), _hook) is True


def test_alias_notated_issue_resolves_against_the_right_shot():
    """记法归一后，`shot_id="shot5"` 必须能定位到 index 5 并按证据判解决."""
    import step01_script_gen.main as m

    script = _script_with_shots(5)
    _kf2 = script["shots"][0]["keyframes"][2]["desc"]
    _issue = Issue(code="model_audit_X", severity="visual_risk", message="m",
                   shot_id="shot5", frame_id="kf2", field_path="keyframes[2].desc",
                   evidence=_kf2)
    assert m._issue_target_shot(script, _issue) is script["shots"][0]
    assert m._audit_issue_resolved(script, _issue) is False, "证据还在稿子里"

    script["shots"][0]["keyframes"][2]["desc"] = "丢灯盏后已在船上倾听"
    assert m._audit_issue_resolved(script, _issue) is True, "证据已消失"


# ---------------------------------------------------------------------------
# 布局路径形状（v6.121，2026-09-25 14:52 真实 run 的 BLOCK 原因）
#
# run_id=20260925T145218-32008：审计对 shot 5 写 `frame_layouts.kfs[1].props` 与
# `[2].props`（**漏人物下标**）→ 精确校验整条拒绝 → 提交点 BLOCK、零修复轮。
# 规则里当时只有一句通用的 `frame_layouts.<帧>[N].<叶子>`，正是漏下标的诱因。
# 真实结构是「关键帧列表 → 人物列表」两层；audited revision 的 shot 5 kfs 人数
# 为 [1, 1, 2]——kf2 有两个人，系统**不替它猜**该改谁（保留精确校验）。
# ---------------------------------------------------------------------------
def _two_person_shot(index: int = 5) -> dict:
    """真实形态：`kfs = [1人, 1人, 2人]`（run 20260925T145218 的 shot 5）."""
    s = _shot(index)
    s["frame_layouts"]["kfs"] = [
        [{"pose": "站立", "props": ["灯盏"]}],
        [{"pose": "跃起", "props": []}],
        [{"pose": "倾听", "props": ["灯盏"]}, {"pose": "仰卧", "props": []}],
    ]
    return s


def test_layout_path_shapes_are_pinned_in_the_audit_contract():
    """规则与 schema 都必须给出两种形状；通用形状（漏下标）不得再出现."""
    import step01_script_gen.audit as audit

    _rules = audit._AUDIT_RULES_TEMPLATE
    assert "frame_layouts.kfs[2][0].props" in _rules, "必须给带两个下标的例子"
    assert "frame_layouts.ff[0].pose" in _rules, "ff/lf 是人物列表一层"
    assert "frame_layouts.<帧>[N].<叶子>" not in _rules, "通用形状是漏下标的诱因"

    _items = audit.AUDIT_SCHEMA["schema"]["properties"]["issues"]["items"]
    _desc = (_items["properties"]["repair_targets"]["items"]
             ["properties"]["field_path"]["description"])
    assert "kfs[2][0].props" in _desc, "成对目标那处才是授权来源，schema 里也要钉"
    assert "kfs[2].props" in _desc, "schema 要显式点名这个无效形状"


def test_two_person_keyframe_requires_the_person_index():
    """kf2 有两个人 → `kfs[2].props` 无效（不猜是谁）；`kfs[2][0]`/`[2][1]` 都有效."""
    _sbi = {"shot_5": _two_person_shot(5)}
    for _bad in ("frame_layouts.kfs[1].props", "frame_layouts.kfs[2].props",
                 "frame_layouts.ff.props", "frame_layouts.kfs"):
        _valid, _rejected = validate_repair_targets(
            [{"shot_id": "5", "field_path": _bad}], _sbi)
        assert not _valid and _rejected, _bad
    for _ok in ("frame_layouts.kfs[2][0].props", "frame_layouts.kfs[2][1].props",
                "frame_layouts.ff[0].props", "frame_layouts.lf[0].pose"):
        _valid, _rejected = validate_repair_targets(
            [{"shot_id": "5", "field_path": _ok}], _sbi)
        assert _valid and not _rejected, _ok


def test_gate_blocks_on_the_missing_person_index_and_passes_with_it():
    """同一条真实 blocking：漏下标 → 整条拒绝（BLOCK）；补上下标 → 过门."""
    import step01_script_gen.main as m

    _sbi = {"shot_5": _two_person_shot(5)}
    _base = {"code": "model_audit_MOTION_MISSING", "severity": "visual_risk",
             "message": "m", "shot_id": "5", "frame_id": "kf2",
             "field_path": "motion_desc"}
    _bad = {**_base, "repair_targets": [
        {"shot_id": "5", "field_path": "motion_desc"},
        {"shot_id": "5", "field_path": "frame_layouts.kfs[1].props"},
        {"shot_id": "5", "field_path": "frame_layouts.kfs[2].props"}]}
    _good = {**_base, "repair_targets": [
        {"shot_id": "5", "field_path": "motion_desc"},
        {"shot_id": "5", "field_path": "frame_layouts.kfs[1][0].props"},
        {"shot_id": "5", "field_path": "frame_layouts.kfs[2][0].props"}]}

    _problems = m._audit_repair_target_problems({"issues": [_bad]}, _sbi)
    assert _problems and "整条拒绝" in _problems[0]
    assert "frame_layouts.kfs[1].props" in _problems[0], (
        "报错要能直接看出是哪条路径写错了：%s" % _problems[0])
    assert m._audit_repair_target_problems({"issues": [_good]}, _sbi) == []


# ---------------------------------------------------------------------------
# 缺失帧键的新建授权 + 新建值形状验收（台账 §19.2/§19.3 A2）
# ---------------------------------------------------------------------------

def _shot_without_ff():
    """有 `ff_desc` 却没有 `frame_layouts.ff` 的镜头（章 2 第 9 镜的真实形态）."""
    return {"index": 9, "ff_desc": "张十三立在船头", "lf_desc": "张十三转身",
            "frame_layouts": {"kfs": [[{"name": "张十三", "on_screen": True}]]}}


def test_missing_frame_key_is_authorizable():
    """`frame_layouts.ff` 整块缺失时，审计**点名它**要能授权新建它.

    改前：`_is_real_leaf` 要求路径已存在 ⇒ 这类问题**任何** `repair_targets` 都授权不出来
    ⇒ 修复轮零授权 ⇒ 模型按建议新建的帧被全部回填 ⇒ `no_target_resolved` ⇒ BLOCK
    （章 2 实测：5 条 blocking 里 2 条 `MISSING_FRAME_LAYOUT` 属这一类）。
    """
    from step01_script_gen.repair_fields import validate_repair_targets

    _sbi = {"shot_9": _shot_without_ff()}
    _v, _rej = validate_repair_targets(
        [{"shot_id": "9", "field_path": "frame_layouts.ff"}], _sbi)
    assert _v and not _rej, _rej
    _v, _rej = validate_repair_targets(
        [{"shot_id": "9", "field_path": "frame_layouts.lf"}], _sbi)
    assert _v and not _rej, _rej
    # 合法对照：该帧**已经存在**时按普通叶子路径走，同样通过
    _v2, _rej2 = validate_repair_targets(
        [{"shot_id": "9", "field_path": "frame_layouts.kfs[0][0].name"}], _sbi)
    assert _v2 and not _rej2, _rej2


def test_creatable_frame_key_does_not_open_other_missing_paths():
    """收窄边界：只认 `frame_layouts.ff` / `.lf`，且父容器必须存在——余者一律照旧拒收."""
    from step01_script_gen.repair_fields import validate_repair_targets

    _sbi = {"shot_9": _shot_without_ff()}
    for _bad in ("frame_layouts.zz", "frame_layouts.mf", "keyframes[0].desc",
                 "frame_layouts.kfs[9]", "prompt"):
        _v, _rej = validate_repair_targets(
            [{"shot_id": "9", "field_path": _bad}], _sbi)
        assert not _v and _rej, _bad
    # `frame_layouts` 本身都不存在 ⇒「新建帧」不成立（父容器必须先有）
    _v, _rej = validate_repair_targets(
        [{"shot_id": "9", "field_path": "frame_layouts.ff"}],
        {"shot_9": {"index": 9, "ff_desc": "x"}})
    assert not _v and _rej, _rej


def test_newly_created_frame_must_be_a_person_entry_list():
    """授权新建 ≠ 放开结构：新建的帧只许是「一帧的人物列表」."""
    from step01_script_gen.repair_fields import frame_layout_structural_violations

    _before = {"shots": [{"index": 9, "frame_layouts": {"kfs": [[]]}}]}
    _allowed = {"shot_9": {"frame_layouts.ff"}}

    def _after(ff):
        return {"shots": [{"index": 9, "frame_layouts": {"kfs": [[]], "ff": ff}}]}

    _good = [{"name": "张十三", "on_screen": True, "position": "画左",
              "pose": "持刀而立", "gaze_target": "", "props": []}]
    assert frame_layout_structural_violations(
        _before, _after(_good), _allowed, ["shot_9"]) == []
    for _bad in ({"name": "张十三"}, [], [{"on_screen": True}], [1]):
        _out = frame_layout_structural_violations(
            _before, _after(_bad), _allowed, ["shot_9"])
        assert _out, _bad


# ---------------------------------------------------------------------------
# 修复轮发起前的「blocking 覆盖」判据（台账 §19.2/§19.3 A4）
# ---------------------------------------------------------------------------

def test_round_without_blocking_coverage_is_skipped_before_the_call():
    """blocking 一条都授权不出来时，本轮**不该发起**修复调用（章 2 实测白烧一次调用）.

    反例：`repair_targets` 缺失（章 2 最后一轮 13/13 条如此）⇒ 该条授权面为空，
    而修复轮照样跑，模型改对了也被逐条回填 ⇒ `字段触达=0` ⇒ `no_target_resolved`
    ⇒ 整轮回退 ⇒ BLOCK。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue

    _no_rt = Issue(code="model_audit_MISSING_FRAME_LAYOUT", severity="visual_risk",
                   message="补写 frame_layouts.ff", shot_id="9", frame_id="ff",
                   field_path="frame_layouts.ff", evidence="本镜只有 kfs",
                   repair_targets=None)
    # 入轮目标的授权面来自确定性告警（`ff_desc` 等），与本条 blocking 点名的
    # `frame_layouts.*` **没有交集** ⇒ 这一轮不可能让它通过验收
    assert m._repair_blocking_coverage(
        [_no_rt], [], {"shot_9": {"ff_desc", "motion_desc"}}) == []
    # 合法对照 1：本轮授权**覆盖了问题定位的那条路径** ⇒ 覆盖非空，本轮照跑
    # （实测形态：模型审计成对目标缺失、但确定性告警恰好授权了同一字段）
    assert m._repair_blocking_coverage(
        [_no_rt], [], {"shot_9": {"frame_layouts.ff"}}) == ["shot_9"]
    # 合法对照 2：成对目标点名的路径被覆盖，同样成立
    _named = Issue(code="model_audit_TEXT_REPETITION", severity="visual_risk",
                   message="改 ff_desc", shot_id="9", frame_id="ff",
                   field_path="", evidence="e",
                   repair_targets=[{"shot_id": "9", "field_path": "ff_desc"}])
    assert m._repair_blocking_coverage(
        [_named], [], {"shot_9": {"ff_desc"}}) == ["shot_9"]
    # 合法对照 3：没有任何入轮目标 ⇒ 不成立（但调用方另外有 `_allowed_by_target` 为空的分支）
    assert m._repair_blocking_coverage([_no_rt], [], {}) == []


def test_coverage_binds_shot_identity_and_counts_deterministic_exit():
    """复核 2026-10-04 P1-3 的五类输入（旧写法在①②③上都会判错）.

    ① **确定性阻塞的成功出口**：它的改善出口是 `before_keys - after_keys`（与路径无关），
       只要带确定性阻塞的目标本轮有**任何**授权，就有改善出口。旧写法只喂模型审计
       blocking ⇒ 提前返回基线，而真实验收本会给 `accepted=True, resolved=1`。
    ② **跨镜同名路径**：镜 9 的问题只能由**镜 9** 的授权覆盖——旧写法对所有入轮目标
       求"任一路径被任一目标授权"，镜 10 的同名字段会把镜 9 的判成已覆盖。
    ③ **裸路径**（`pose` / `props`）：模型审计的 `field_path` 常只写字段名，
       `path_allowed` 对裸名永不命中 ⇒ 有能力的轮次被误判成零能力。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue

    _no_rt = Issue(code="model_audit_MISSING_FRAME_LAYOUT", severity="visual_risk",
                   message="m", shot_id="9", field_path="frame_layouts.ff",
                   repair_targets=None)
    _i9 = Issue(code="model_audit_TEXT_REPETITION", severity="visual_risk", message="m",
                shot_id="9", field_path="ff_desc", repair_targets=None)
    _bare = Issue(code="model_audit_STATE_CHAIN_CONFLICT", severity="visual_risk",
                  message="m", shot_id="9", field_path="pose", repair_targets=None)
    _prec = Issue(code="model_audit_STATE_CHAIN_CONFLICT", severity="visual_risk",
                  message="m", shot_id="9", field_path="frame_layouts.kfs[0][0].pose",
                  repair_targets=None)
    # ① 确定性合法修复（审计这条拿不到授权，但确定性阻塞有）
    assert m._repair_blocking_coverage(
        [_no_rt], ["shot_9"], {"shot_9": {"frame_layouts.kfs.0.0.pose"}}) == ["shot_9"]
    # ② 跨镜同名路径：镜 10 的授权**覆盖不了**镜 9 的问题
    assert m._repair_blocking_coverage([_i9], [], {"shot_10": {"ff_desc"}}) == []
    # ② 合法对照：同一镜的授权仍然成立
    assert m._repair_blocking_coverage([_i9], [], {"shot_9": {"ff_desc"}}) == ["shot_9"]
    # ③ 裸路径按"该镜有哪条授权路径以它收尾"判
    assert m._repair_blocking_coverage(
        [_bare], [], {"shot_9": {"frame_layouts.kfs.0.0.pose"}}) == ["shot_9"]
    # ④ 精确路径
    assert m._repair_blocking_coverage(
        [_prec], [], {"shot_9": {"frame_layouts.kfs.0.0.pose"}}) == ["shot_9"]
    # ⑤ 确实零能力：审计与确定性都没有出口
    assert m._repair_blocking_coverage(
        [_no_rt], [], {"shot_9": {"ff_desc", "motion_desc"}}) == []

def test_authorized_creation_survives_the_merge():
    """授权新建的帧要**真的写进合并结果**（旧写法无条件丢弃 n 多出的键）.

    反例（改前）：审计点名 `frame_layouts.ff`、授权也过了，但 `merge_authorized`
    的 `for k in n: if k not in t` 一律丢弃新增键 ⇒ 模型补的帧在合并层就没了，
    日志还写成「未授权路径已回填」（章 2 实测的形态）。
    """
    from step01_script_gen.repair_fields import merge_authorized

    _target = {"index": 9, "ff_desc": "x", "frame_layouts": {"kfs": [[]]}}
    _new = {"index": 9, "ff_desc": "x",
            "frame_layouts": {"kfs": [[]], "ff": [{"name": "张十三"}]}}
    _merged, _echoed = merge_authorized(_target, _new, {"frame_layouts.ff"})
    assert _merged["frame_layouts"].get("ff") == [{"name": "张十三"}], _merged
    assert "frame_layouts.ff" not in _echoed, _echoed
    # 反例：**没有授权**这个键时，仍然按基线回填并留证
    _merged2, _echoed2 = merge_authorized(_target, _new, {"ff_desc"})
    assert "ff" not in (_merged2.get("frame_layouts") or {}), _merged2
    assert "frame_layouts.ff" in _echoed2, _echoed2

def test_structural_guard_keeps_other_authorized_leaf_edits():
    """复核 2026-10-04 P1-2：同轮「补建 ff」与「改 kfs 里的 pose」**互不排斥**.

    旧写法只看"这一帧列表有没有被逐字点名"，没点名就报「未被点名却被改动」——
    于是**同轮的另一项合法修复被自己的守卫拒掉**（单目标整轮回退、多目标回退联动组）。
    责任分三种：新建帧形状、整帧纯增删、已授权叶子修改。
    """
    import copy as _copy

    from step01_script_gen.repair_fields import frame_layout_structural_violations

    _before = {"shots": [{"index": 9, "frame_layouts": {
        "kfs": [[{"name": "张十三", "on_screen": True, "pose": "拄刀而立", "props": []}]]}}]}
    _after = _copy.deepcopy(_before)
    _after["shots"][0]["frame_layouts"]["kfs"][0][0]["pose"] = "抬刀过顶"
    _after["shots"][0]["frame_layouts"]["ff"] = [
        {"name": "张十三", "on_screen": True, "position": "画左", "pose": "站立",
         "gaze_target": "", "props": []}]
    _both = {"shot_9": {"frame_layouts.ff", "frame_layouts.kfs.0.0.pose"}}
    assert frame_layout_structural_violations(_before, _after, _both, ["shot_9"]) == []
    # 反例：只授权补建 ff ⇒ 那处 pose 改动仍然是越权
    _only_ff = frame_layout_structural_violations(
        _before, _after, {"shot_9": {"frame_layouts.ff"}}, ["shot_9"])
    assert _only_ff and "frame_layouts.kfs[0][0].pose" in _only_ff[0], _only_ff
    # 反例：只授权 pose ⇒ 那个**没被点名的新帧**仍然是越权
    _only_pose = frame_layout_structural_violations(
        _before, _after, {"shot_9": {"frame_layouts.kfs.0.0.pose"}}, ["shot_9"])
    assert _only_pose and "frame_layouts.ff" in _only_pose[0], _only_pose
    # 合法对照：整帧被点名时仍只许纯增删（既有判据不变）
    _named = frame_layout_structural_violations(
        _before, _after, {"shot_9": {"frame_layouts.kfs[0]"}}, ["shot_9"])
    _joined = " | ".join(_named)
    # 整帧被点名 ⇒ 帧里的字段改写必须是违例（"本帧只授权增删条目"），
    # 且没被点名的新帧同样要被报出来
    assert "只授权增删条目" in _joined, _named
    assert any("frame_layouts.ff" in _m for _m in _named), _named


def test_structural_guard_recurses_into_container_leaves():
    """复核 2026-10-04 第三轮：容器内部的叶子（`props[0]`）不能被按整个容器误判.

    改前按条目的一级键整份比：`props` 列表一变就报 `…[0].props 未被点名却被改动`
    ——标量 `pose` 碰巧能过（一级键就是叶子），**容器内部的叶子一律误判**。
    """
    import copy as _copy

    from step01_script_gen.repair_fields import frame_layout_structural_violations

    _before = {"shots": [{"index": 9, "frame_layouts": {"kfs": [[
        {"name": "张十三", "on_screen": True, "props": ["刀"]}]]}}]}
    _after = _copy.deepcopy(_before)
    _after["shots"][0]["frame_layouts"]["kfs"][0][0]["props"] = ["弩"]
    _after["shots"][0]["frame_layouts"]["ff"] = [
        {"name": "张十三", "on_screen": True, "position": "画左", "pose": "站立", "props": []}]
    _both = {"shot_9": {"frame_layouts.ff", "frame_layouts.kfs.0.0.props.0"}}
    assert frame_layout_structural_violations(_before, _after, _both, ["shot_9"]) == []
    # 反例：**没授权**那个内部叶子 ⇒ 仍然要报（指名到具体下标）
    _only_ff = frame_layout_structural_violations(
        _before, _after, {"shot_9": {"frame_layouts.ff"}}, ["shot_9"])
    _joined = " | ".join(_only_ff)
    assert "frame_layouts.kfs[0][0].props[0]" in _joined, _only_ff
    # 反例：授权**另一个下标**不算（索引精确）
    _wrong_idx = frame_layout_structural_violations(
        _before, _after, {"shot_9": {"frame_layouts.ff", "frame_layouts.kfs.0.0.props.1"}},
        ["shot_9"])
    assert any("props[0]" in _m for _m in _wrong_idx), _wrong_idx


def test_coverage_accepts_parent_frame_authorization_for_bare_location():
    """复核 2026-10-04 第三轮：**父帧授权 + 裸定位**是合法出口，不许被判零能力.

    实测形态：blocking 定位在同镜 `kf0/pose`，另一条审计授权的是那一帧的人物列表
    `frame_layouts.kfs[0]`；按列表授权**删掉姿态错误的重复条目**后真实守卫通过、
    验收 `accepted=True`——只按"授权路径末段等于 pose"判会把这一轮提前跳过。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue

    _bare = Issue(code="model_audit_STATE_CHAIN_CONFLICT", severity="visual_risk",
                  message="m", shot_id="9", frame_id="kf0", field_path="pose",
                  repair_targets=None)
    assert m._repair_blocking_coverage(
        [_bare], [], {"shot_9": {"frame_layouts.kfs.0"}}) == ["shot_9"]
    # 合法对照：非裸定位仍按精确叶子判
    assert m._path_covered_by("shot_9", "frame_layouts.kfs[0][0].pose",
                              {"shot_9": {"frame_layouts.kfs.0"}}) is True
    # 反例：别的帧的列表授权**不**覆盖这一帧
    assert m._repair_blocking_coverage(
        [_bare], [], {"shot_9": {"frame_layouts.kfs.1"}}) == []
    # 反例：确实零能力仍然是零能力（章 2 的形态）
    assert m._repair_blocking_coverage(
        [_bare], [], {"shot_9": {"ff_desc", "motion_desc"}}) == []


def test_completion_to_commit_legal_chain():
    """**补全 → 授权 → 合并 → 守卫 → 验收**一条合法响应链走通（有限入口验收）.

    起点是章 2 的真实形态：审计给出的 blocking 缺 `repair_targets`（授权无从谈起）。
    补全把它补上之后，必须真能一路走到"验收接受"，而不是只证明"字段写进去了"。
    """
    import copy as _copy
    import json as _json

    import step01_script_gen.audit as audit
    import step01_script_gen.main as m
    from step01_script_gen.repair_fields import (frame_layout_structural_violations,
                                                 merge_authorized)
    from step01_script_gen.quality_issues import Issue

    _main = _json.dumps({"type": "json_object", "issues": [{
        "code": "MISSING_FRAME_LAYOUT", "severity": "blocking", "shot_id": "9",
        "frame_id": "ff", "entity_id": "张十三", "field_path": "frame_layouts.ff",
        "evidence": "本镜只有 kfs，缺 ff / lf 出镜表", "impact": "首尾帧无布局，无法执行",
        "minimal_fix": "补写 frame_layouts.ff（张十三一人，画中偏右）", "stage": "pre"}]},
        ensure_ascii=False)
    _fill = _json.dumps({"type": "json_object", "targets": [
        {"issue_index": 1,
         "repair_targets": [{"shot_id": "9", "field_path": "frame_layouts.ff"}]}]},
        ensure_ascii=False)

    class _R:
        success = True
        truncated = False
        error = ""
        usage = {"input_tokens": 1, "output_tokens": 1}
        finish_reason = "stop"

    _texts = [_main, _fill]

    class _S:
        def generate(self, req):
            _r = _R()
            _r.text = _texts.pop(0)
            return _r

    _orig = audit.create_service
    audit.create_service = lambda *a, **k: _S()
    try:
        _script = {"shots": [{"index": 9, "ff_desc": "张十三立在船头",
                              "lf_desc": "张十三转身离去",
                              "frame_layouts": {"kfs": [[{"name": "张十三",
                                                          "on_screen": True}]]}}]}
        _res = audit.run_same_model_audit_with_status("原文", {}, _script, model="m")
    finally:
        audit.create_service = _orig
    _issue = _res.issues[0]
    assert _issue.repair_targets == [{"shot_id": "9", "field_path": "frame_layouts.ff"}], _issue

    # ① 授权：这一跳以前是零授权（章 2 的死结）
    _shots = {"shot_9": _script["shots"][0]}
    from step01_script_gen.repair_fields import allowed_repair_fields
    _allowed = allowed_repair_fields([], [_issue], target_shot_id="shot_9",
                                     shots_by_id=_shots)
    assert "frame_layouts.ff" in _allowed, _allowed

    # ② 合并：授权的新增键**真的写进结果**（旧写法无条件丢弃）
    _new = _copy.deepcopy(_script["shots"][0])
    _new["frame_layouts"]["ff"] = [{"name": "张十三", "on_screen": True,
                                    "position": "画中偏右", "pose": "持刀而立",
                                    "gaze_target": "", "props": []}]
    _merged, _echoed = merge_authorized(_script["shots"][0], _new, _allowed)
    assert _merged["frame_layouts"].get("ff"), _merged
    assert "frame_layouts.ff" not in _echoed, _echoed

    # ③ 守卫：新建帧的形状验收通过
    _before = {"shots": [_script["shots"][0]]}
    _after = {"shots": [_merged]}
    assert frame_layout_structural_violations(
        _before, _after, {"shot_9": _allowed}, ["shot_9"]) == []

    # ④ 验收：走了确定性阻塞的**真实**出口（resolved=1 ⇒ accepted）
    _rec = {"code": "missing_frame_layout", "severity": "visual_risk", "shot_id": "9",
            "frame_id": "ff", "entity_id": "张十三", "field_path": "frame_layouts.ff"}
    _dec = m._repair_acceptance_decision(
        {"issues": [_rec], "checker_failed": False},
        {"issues": [], "checker_failed": False},
        [_issue], _after, False)
    assert _dec["accepted"] is True and _dec["resolved"] == 1, _dec
    assert _dec["new_blocking"] == 0

# ---------------------------------------------------------------------------
# A3 全链（复核 2026-10-04 第四轮 P2）：
#   缺字段审计 → 补全 → **真实修复入口** → 重验 → **真实提交点**
#
# 原用例止于 `_repair_acceptance_decision`：它只证明"验收函数会接受"，没证明这条链
# 在真实修复入口与提交出口上走得通。下面这条把外部依赖收成**一个**（模型服务），
# 并按复核要求同时保留"补全失败 ⇒ 保留原 blocking"的对照。
# ---------------------------------------------------------------------------
_A3_PHANTOM = "小厮"


def _a3_chain_script() -> dict:
    """一镜：`_complete_shot` 的**完整合法**形状 + 出镜表里多一个未定义条目（真实缺陷）."""
    _s = _complete_shot(1)
    _fl = _s["frame_layouts"]
    _fl["kfs"][0] = _fl["kfs"][0] + [
        {"name": _A3_PHANTOM, "on_screen": True, "position": "", "pose": "",
         "gaze_target": "无", "props": []}]
    return {"title": "t", "shots": [_s], "characters": [], "hook": None}


def _a3_has_phantom(script: dict) -> bool:
    return any(_e.get("name") == _A3_PHANTOM
               for _s in (script.get("shots") or [])
               for _f in (_s.get("frame_layouts") or {}).get("kfs") or [] for _e in _f)


def _a3_shot_id(script: dict) -> str:
    return str(((script.get("shots") or [{}])[0]).get("index"))


def _a3_delete_phantom(shot: dict) -> dict:
    import copy as _c
    _out = _c.deepcopy(shot)
    _out["frame_layouts"]["kfs"] = [[_e for _e in _f if _e.get("name") != _A3_PHANTOM]
                                    for _f in _out["frame_layouts"]["kfs"]]
    return _out


class _A3ChainService:
    """**只替换外部模型服务**：审计 / 补全 / 修复轮三种请求由同一个桩按内容分派.

    与既有提交链用例的关键区别：这里**不替换** `run_same_model_audit_with_status`
    ——A3 的补全就在它内部，替掉它等于把被测路径整条短路。桩只回答"模型会回什么"。
    """

    def __init__(self, holder: dict, *, fill: bool = True):
        self._holder = holder
        self._fill = fill
        self.kinds: list = []

    def generate(self, req):
        _up = str(getattr(req, "user_prompt", "") or "")
        if "## 要补的条目" in _up:                 # 补全请求
            self.kinds.append("completion")
            return self._resp(self._completion_text() if self._fill else "",
                              ok=self._fill)
        if "shot_replacements" in _up:             # 修复轮请求
            self.kinds.append("repair")
            return self._resp(self._repair_text())
        self.kinds.append("audit")                 # 语义审计请求
        return self._resp(json.dumps(self._audit_payload(), ensure_ascii=False))

    @staticmethod
    def _resp(text: str, *, ok: bool = True):
        class _R:
            success = True
            truncated = False
            error = ""
            usage = {"input_tokens": 1, "output_tokens": 1}
            finish_reason = "stop"
            call_id = "a3-chain-call"

        _r = _R()
        _r.text = text
        _r.success = ok
        if not ok:
            _r.error = "completion endpoint is down"
        return _r

    def _audit_payload(self) -> dict:
        import step01_script_gen.main as m
        from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
        from tests.step01_script_gen.test_step01_commit import _audit_payload

        _script = self._holder.get("script") or {}
        _issues = []
        if _a3_has_phantom(_script):
            _issues = [{
                "code": "model_audit_LAYOUT_PHANTOM_ENTITY",
                # ⚠️ 走**真实解析器**：schema 的 severity 域是 blocking/warning/info
                # （`visual_risk` 是归一化之后的 `Issue.severity`，写在这里会整份
                # parse_failed——`_fake_audit` 那条路绕过解析器，所以两者口径不同）
                "severity": "blocking",
                "shot_id": _a3_shot_id(_script), "frame_id": "",
                "entity_id": _A3_PHANTOM,
                "field_path": "frame_layouts.kfs[0][1].name",
                "message": "布局多出无名占位角色；建议：删除 kfs[0] 的「小厮」条目",
                # 证据用**真实运行里那一条**的长度与形状（`_audit_issue_resolved`
                # 对短证据恒返回 False，会让"是否动过点名字段"的兜底记账走不到）。
                "evidence": ("kfs[0]出镜表在张十三之外多出「小厮」条目，"
                             "position/pose 为空、gaze_target 为「无」"),
                # **刻意不给** `repair_targets`——这正是 A3 要补的形态
            }]
        return _audit_payload(_script, issues=_issues, scope=m.STEP01_AUDIT_SCOPE,
                              fingerprint=RULE_SOURCE_FINGERPRINT)

    def _completion_text(self) -> str:
        _sid = _a3_shot_id(self._holder.get("script") or {})
        return json.dumps({"type": "json_object", "targets": [
            {"issue_index": 1,
             "repair_targets": [{"shot_id": _sid,
                                 "field_path": "frame_layouts.kfs[0]"}]}]},
            ensure_ascii=False)

    def _repair_text(self) -> str:
        _script = self._holder.get("script") or {}
        _shot = _a3_delete_phantom((_script.get("shots") or [{}])[0])
        return json.dumps({"shot_replacements": [
            {"shot_id": str(_shot.get("index")), "shot": _shot}],
            "hook_replacement": None}, ensure_ascii=False)


def _a3_patch(monkeypatch, *, fill: bool = True):
    """把外部依赖收成一个：**模型服务**. 审计 / 补全 / 修复都走真实实现."""
    import copy as _c

    import step01_script_gen.audit as audit
    import step01_script_gen.main as m

    holder: dict = {}
    _svc = _A3ChainService(holder, fill=fill)
    _real = audit.run_same_model_audit_with_status

    def _wrapper(novel_text, plan, script, **kw):
        # 真实现照跑（解析 / 偏离记录 / **A3 补全**都在里面）；只把"当前稿"交给桩，
        # 让它按当轮稿件作答（审计的 `input_revision` 必须与那一份对上）。
        holder["script"] = _c.deepcopy(script)
        return _real(novel_text, plan, script, **kw)

    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _wrapper)
    monkeypatch.setattr(audit, "create_service", lambda *a, **k: _svc)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _svc)
    return holder, _svc


@pytest.fixture
def a3_commit_env(tmp_path, monkeypatch):
    """提交入口的隔离环境：只隔离**落盘位置与确定性质量门**，修复轮**不**打桩.

    与 `test_step01_commit.chapter` 的区别：那个把 `_repair_shot_warnings` 换成
    "原样返回"（提交时序用例不需要真修）。这条要走的**正是**真实修复事务。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_VERSION

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, RULE_VERSION))
    monkeypatch.setattr(m, "_snapshot_run_artifacts", lambda script=None: None)
    return tmp_path


def test_completion_chain_commits_through_the_real_repair_and_commit_entries(a3_commit_env,
                                                                            monkeypatch):
    """**缺字段审计 → 补全 → 真实修复 → 重验 → 真实提交点**：一条合法响应链走通.

    复核 2026-10-04 第四轮 P2 点名：原用例止于 `_repair_acceptance_decision`，没有经过
    真实修复、后处理和提交入口。这条把三跳全走完，并落到**真实消费准入**上。
    """
    import step01_script_gen.main as m
    from shared.quality_gate import (compute_script_revision, require_semantic_gate,
                                     verify_step01_commit_manifest)

    _holder, _svc = _a3_patch(monkeypatch)
    script, summary = m._commit_step01_outputs(_a3_chain_script(), "")

    assert ("audit" in _svc.kinds and "completion" in _svc.kinds
            and "repair" in _svc.kinds), "三跳都要真的走到：%s" % _svc.kinds
    assert summary["status"] == "COMMITTED", summary.get("detail") or summary
    assert not _a3_has_phantom(script), "补全给出的授权必须让删除真的落到正式稿里"

    # ① 三份产物 + 提交清单都落在隔离目录
    for _n in ("script_result.json", "step01_quality.json", "step01_audit.json",
               "step01_commit.json"):
        assert (a3_commit_env / _n).is_file(), _n
    # ② 提交清单绑的是**盘上那一份**
    _disk = json.loads((a3_commit_env / "script_result.json").read_text(encoding="utf-8"))
    _man = verify_step01_commit_manifest(a3_commit_env, _disk, stage="step01")
    assert _man["status"] == "COMMITTED"
    assert _man["script_revision"] == compute_script_revision(_disk)
    assert set(_man["artifacts"]) >= {"script_result.json", "step01_quality.json",
                                      "step01_audit.json"}
    # ③ **真实消费准入**：下游（step02a）启动前核的就是这一份
    require_semantic_gate(a3_commit_env / "step01_quality.json",
                          a3_commit_env / "step01_audit.json", _disk, stage="step01")


def test_completion_failure_keeps_the_blocking_and_the_commit_blocks(a3_commit_env,
                                                                    monkeypatch):
    """对照：**补全失败** ⇒ 原 blocking 保留、零授权 ⇒ 终局 BLOCK（不做半条修复）."""
    import step01_script_gen.main as m

    _holder, _svc = _a3_patch(monkeypatch, fill=False)
    script, summary = m._commit_step01_outputs(_a3_chain_script(), "")

    assert "completion" in _svc.kinds, "补全那一次确实发起了（只是失败）"
    assert summary["status"] == "BLOCK", summary
    assert _a3_has_phantom(script), "零授权 ⇒ 修复被回填，多余条目必须还在"
    assert not (a3_commit_env / "script_result.json").exists(), "BLOCK 不替换三份正式产物"
    assert (a3_commit_env / "step01_commit.json").is_file(), "BLOCK 仍要写清单（下游准入依据）"

def test_model_audit_warning_text_cannot_regrant_authorization():
    """复核 2026-10-04 第四轮（越界覆盖类）：模型审计缺成对目标时，**文案回退也不许**授权.

    改前实测（`.tmp/_probe_failclosed_bypass.py`）：

        ① 结构化路径（无成对目标）: []
        ② 同一条经**渲染后的告警文本**回来: ['frame_layouts']   ← 覆盖全部帧、全部叶子
        ③ 确定性告警（对照）        : ['keyframes.*.desc']

    ②是漏口：模型审计问题的授权**只**认成对目标（v6.119），它被渲染进回修告警只是
    为了让模型看得见问题，不能因此从关键词解析里拿回一个**更宽**的授权面。③必须保留
    ——确定性告警没有成对目标契约，文案回退是它们的合法出口。
    """
    from step01_script_gen.quality_issues import Issue
    from step01_script_gen.repair_fields import (MODEL_AUDIT_WARNING_PREFIX,
                                                 allowed_repair_fields)

    _shots = {"shot_1": {"index": 1,
                         "frame_layouts": {"ff": [], "lf": [], "kfs": [[], [], []]}}}
    _issue = Issue(code="model_audit_LAYOUT_PHANTOM_ENTITY", severity="visual_risk",
                   message="布局多出条目", shot_id="1", frame_id="",
                   field_path="frame_layouts.kfs[0][1].name", repair_targets=None)
    # ① 结构化路径：fail-closed（v6.119）
    assert allowed_repair_fields([], [_issue], target_shot_id="shot_1",
                                 shots_by_id=_shots) == set()
    # ② 同一条问题渲染成的回修告警：**同样不许**授权（改前的唯一漏口）
    _w = ("%s code=model_audit_LAYOUT_PHANTOM_ENTITY frame=- entity=小厮 "
          "field=frame_layouts.kfs[0][1].name evidence=布局多出条目 :: 建议：删除该条目"
          % MODEL_AUDIT_WARNING_PREFIX)
    assert allowed_repair_fields([_w], [], target_shot_id="shot_1",
                                 shots_by_id=_shots) == set()
    # ③ 合法对照：确定性告警的文案回退照常生效
    assert allowed_repair_fields(["关键帧2 描述称…（确定性）"], [],
                                 target_shot_id="shot_1",
                                 shots_by_id=_shots) == {"keyframes.*.desc"}
    # ④ 合法对照：模型审计**带了**成对目标 ⇒ 照常只授权点名的那一个下标
    _ok = Issue(code="model_audit_LAYOUT_PHANTOM_ENTITY", severity="visual_risk",
                message="布局多出条目", shot_id="1", frame_id="",
                field_path="frame_layouts.kfs[0][1].name",
                repair_targets=[{"shot_id": "1", "field_path": "frame_layouts.kfs[0]"}])
    assert allowed_repair_fields([], [_ok], target_shot_id="shot_1",
                                 shots_by_id=_shots) == {"frame_layouts.kfs.0"}
