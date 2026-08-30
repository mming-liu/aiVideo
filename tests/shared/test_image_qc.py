# -*- coding: utf-8 -*-
"""shared/image_qc 行为规范测试（canonical）.

v6.56 起：image_qc 单元测试一律进本文件。历史文件 v653/v6541/v655 保留
（绿的不动），后续触碰对应行为时按条目并入本规范，不做一次性大迁移。

测试组织（按行为聚类）：
  1. 跨消费者一致性不变量：frame_narrative_desc 单一解析入口锁整类字段源分裂 bug
  2. _expected_persons 行为：lf/ff 期望人数
  3. 服装注入行为：_inject_character_costume 威胁源可见性门控
  4. 集成：image_qc 与 vimax_bridge 修复联动

不变量 #1 是 v6.56 类修复的核心锁：lf「神秘人已没入水中不见踪影」+ final
含「蒙面/湿衣」服装锚块时，三个 desc 消费方对「神秘人是否在画面」必须给
**一致结论**（均判不在场）——任一处不一致即视为 bug 复发。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# Test fixture helpers
# ============================================================================

def _assassin_char(ident="刺客"):
    """标准威胁源角色数据（与实际章节一致）."""
    return {
        "identifier": ident,
        "static_features": "蒙面，仅露双眼，细长丹凤眼，眼神清冷警觉",
        "dynamic_features": "一身湿衣，手持短刃（寒光）",
    }


def _generic_char(ident="听香"):
    """非威胁源角色."""
    return {
        "identifier": ident,
        "static_features": "鹅蛋脸，柳叶眉，秀发披散",
        "dynamic_features": "淡青襦裙，绣鞋",
    }


def _shot_with_characters():
    """构造带角色上下文 shot（避开 is_empty_scene 误判）.

    is_empty_scene 看 main_subject/motion_desc/ff_desc/prompt 是否含角色名 +
    是否有泛人物词；测试 fixture 必须让 shot 不是空镜，否则函数 early return
    跳过整段注入逻辑。
    """
    return {
        "ff_desc": "杨旭仰卧船头，胸口插着匕首",
        "lf_desc": "杨旭仍仰卧不动，胸口匕首依旧",
        "motion_desc": "听香捂住嘴，泪水夺眶而出",
    }


# ============================================================================
# 1. 跨消费者一致性不变量（v6.56 类修复核心锁）
# ============================================================================

def test_cross_consumer_invariant_threat_submerged_disagree_lf_final():
    """锁字段源分裂 bug：lf「神秘人已没入水中不见踪影」+ final 含其服装锚块
    → 三个 desc 消费方对「神秘人是否在画面」必须给一致结论（均判不在场）.

    消费方：
      - _expected_persons：期望人数判威胁源不在场 → 期望 2 人
      - build_claim_checks：拆出「神秘人已没入水中不见踪影」句 → 期望 no（在）
      - frame_narrative_desc 本身：直接返回 lf_desc

    旧 bug：_expected_persons 走 lf_desc_final → 服装锚块判威胁源可见 →
    期望 3 人；与「逐句断言」判「不在场」互斥 → count=3 vs 名单 3 临界
    通过但语义反转（"已没入水中"被吞）。修复：raw desc 优先，三处同源。
    """
    from shared.image_qc import (
        frame_narrative_desc,
        _expected_persons,
        build_claim_checks,
    )

    lf_desc = ("神秘人已没入水中不见踪影，匕首亦随人沉入水下。"
               "杨旭仰卧不动，听香披发跪坐僵在当场")
    lf_desc_final = (lf_desc +
                     "。刺客：蒙面，仅露双眼，一身湿衣，手持短刃（寒光）")

    shot = {
        "char_layout": [{"name": "杨旭"}, {"name": "听香"}, {"name": "神秘人"}],
        "lf_desc": lf_desc,
        "lf_desc_final": lf_desc_final,
    }
    characters = [
        {"identifier": "杨旭"},
        {"identifier": "听香"},
        _assassin_char("刺客"),
    ]

    # 1. frame_narrative_desc 返回 raw desc
    assert frame_narrative_desc(shot, "last_frame") == lf_desc, \
        "解析入口必须返回 raw lf_desc 优先"

    # 2. _expected_persons：威胁源不在场 → 期望 2 人
    n = _expected_persons(shot, "last_frame", characters)
    assert n == 2, f"期望人数应剔除已没入水中的威胁源，期望 2，实测 {n}"

    # 3. build_claim_checks：拆出「已没入水中不见踪影」句（v6.56 补「踪影」
    # 进 _NEGATION_TARGETS）→ 翻转为 expect=no 的存在性问句。
    # 否定句「不见踪影」触发翻转，生成「画面中是否出现「踪影」？」+ expect=no。
    # 这与 _threat_visible_in 同句判不可见语义一致——三个消费者结论对齐。
    checks = build_claim_checks(shot, "last_frame")
    trace_check = [c for c in checks
                   if "踪影" in c["q"]]
    assert trace_check, (
        "应至少生成一条「踪影」否定翻转问句（来自 lf 句「神秘人已没入水中不见踪影」），"
        f"实测问句: {[c['q'] for c in checks]}"
    )
    for c in trace_check:
        assert c["expect"] == "no", \
            f"「不见踪影」应翻转为 expect=no（不在场），实测 {c['expect']}"


# ============================================================================
# 2. _expected_persons 行为
# ============================================================================

def test_expected_persons_lf_drops_threat_when_raw_says_submerged():
    """lf_desc 写「已没入水中不见踪影」+ lf_desc_final 含服装锚块「蒙面/湿衣」
    → 期望人数 = char_layout 数 - 1（剔除威胁源）.

    旧实现读 lf_desc_final → 服装锚块命中「蒙面/湿透」可见词表 →
    _threat_visible_in 返 True → 威胁源保留 → 人数 = 3。新实现读 lf_desc
    优先 → 消失句「已没入水中」判不可见 → 威胁源剔除 → 人数 = 2。
    """
    from shared.image_qc import _expected_persons

    shot = {
        "char_layout": [
            {"name": "杨旭"},
            {"name": "听香"},
            {"name": "神秘人"},
        ],
        "lf_desc": "神秘人已没入水中不见踪影，匕首亦随人沉入水下。杨旭仰卧不动，听香披发跪坐僵在当场。水面涟漪四散",
        "lf_desc_final": "神秘人已没入水中不见踪影。杨旭仰卧不动，听香披发跪坐。刺客：蒙面，仅露双眼，一身湿衣，手持短刃（寒光）",
    }
    characters = [
        {"identifier": "杨旭"},
        {"identifier": "听香"},
        {"identifier": "刺客", "static_features": "蒙面，仅露双眼",
         "dynamic_features": "一身湿衣，手持短刃（寒光）"},
    ]

    result = _expected_persons(shot, "last_frame", characters)
    assert result == 2, f"威胁源应剔除（已没入水中），期望 2，实测 {result}"


def test_expected_persons_lf_keeps_threat_when_visible():
    """lf_desc 写「刺客踞蹲船舷」等可见实体特征 → 威胁源保留（人数 3）.

    回归：v6.56 修复对真实可见场景行为零变化。
    """
    from shared.image_qc import _expected_persons

    shot = {
        "char_layout": [
            {"name": "杨旭"},
            {"name": "听香"},
            {"name": "神秘人"},
        ],
        "lf_desc": "神秘人踞蹲船舷，右手短刃高举，目光凝望杨旭。杨旭仰卧不动，听香披发跪坐",
        "lf_desc_final": "神秘人踞蹲船舷，右手短刃高举。刺客：蒙面，仅露双眼",
    }
    characters = [
        {"identifier": "杨旭"},
        {"identifier": "听香"},
        {"identifier": "刺客"},
    ]

    result = _expected_persons(shot, "last_frame", characters)
    assert result == 3, f"威胁源可见应保留，期望 3，实测 {result}"


def test_expected_persons_ff_keeps_threat_when_partially_visible():
    """ff_desc「仅露头部」→ 威胁源保留（人数 3）.

    ff 路径回归：field priority 修复对 first_frame 行为零变化。
    """
    from shared.image_qc import _expected_persons

    shot = {
        "char_layout": [
            {"name": "杨旭"},
            {"name": "听香"},
            {"name": "神秘人"},
        ],
        "ff_desc": "弦月当空，杨旭仰卧船头。神秘人湿衣伏于舟侧水下，仅露头部，一手持匕首于水面之下",
        "ff_desc_final": "弦月当空，杨旭仰卧船头。神秘人仅露头部。刺客：蒙面，一身湿衣，手持短刃",
    }
    characters = [
        {"identifier": "杨旭"},
        {"identifier": "听香"},
        {"identifier": "刺客"},
    ]

    result = _expected_persons(shot, "first_frame", characters)
    assert result == 3, f"ff 威胁源仅露头部仍应保留，期望 3，实测 {result}"


def test_expected_persons_lf_no_final_field_falls_back_cleanly():
    """lf_desc 存在但 lf_desc_final 缺失（罕见路径）→ 不崩，回退 raw desc."""
    from shared.image_qc import _expected_persons

    shot = {
        "char_layout": [{"name": "神秘人"}, {"name": "听香"}],
        "lf_desc": "神秘人已没入水中不见踪影，听香披发跪坐",
    }
    characters = [{"identifier": "刺客"}, {"identifier": "听香"}]

    result = _expected_persons(shot, "last_frame", characters)
    assert result == 1, f"回退 raw desc 应剔除威胁源，期望 1，实测 {result}"


def test_expected_persons_lf_only_final_field_no_raw():
    """lf_desc 缺失但 lf_desc_final 存在 → 用 final 兜底（仍判威胁源）.

    极端路径：raw desc 缺失时 final 兜底是合理行为。
    """
    from shared.image_qc import _expected_persons

    shot = {
        "char_layout": [{"name": "神秘人"}, {"name": "听香"}],
        "lf_desc_final": "神秘人踞蹲船舷。刺客：蒙面，仅露双眼",
    }
    characters = [{"identifier": "刺客"}, {"identifier": "听香"}]

    result = _expected_persons(shot, "last_frame", characters)
    assert result == 2, f"final 兜底判可见应保留，期望 2，实测 {result}"


# ============================================================================
# 3. 服装注入行为（_inject_character_costume 威胁源可见性门控）
# ============================================================================

def test_inject_costume_lf_submerged_skips_threat_anchor():
    """lf_desc 写「已没入水中不见踪影」+ ident=刺客 → 刺客锚块不注入.

    旧实现 ident="刺客" in _THREAT_IDS → 走可见性门控 → 已没入水中 → 跳过。
    本测试是**回归锁**：确保现有正确路径不被新代码破坏。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("刺客"), _generic_char("听香")]
    shot = _shot_with_characters()
    lf_desc = "神秘人已没入水中不见踪影，匕首亦随人沉入水下。杨旭仰卧不动，听香披发跪坐僵在当场"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")

    assert "蒙面" not in out, f"刺客服装锚块应被剔除，实测: {out}"
    assert "一身湿衣" not in out, f"刺客 dynamic 锚块应被剔除，实测: {out}"
    assert "鹅蛋脸" in out, f"听香锚块应照常注入，实测: {out}"


def test_inject_costume_lf_visible_threat_keeps_anchor():
    """lf_desc 写「刺客踞蹲船舷」等可见实体特征 → 刺客锚块照常注入.

    回归：威胁源真实可见时不被误剔（与 v6.56 设计原则一致）。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("刺客"), _generic_char("听香")]
    shot = _shot_with_characters()
    lf_desc = "神秘人踞蹲船舷，右手短刃高举，目光凝望杨旭。杨旭仰卧不动，听香披发跪坐"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")

    assert "蒙面" in out, f"刺客可见应保留锚块，实测: {out}"
    assert "一身湿衣" in out, f"刺客 dynamic 锚块应保留，实测: {out}"


def test_inject_costume_lf_submerged_new_threat_keyword_disguised():
    """ident="蒙面人"（不在旧 _THREAT_IDS 但属威胁源）+ lf_desc「已没入水中」
    → 蒙面人锚块不注入.

    修复点：旧 _THREAT_IDS 仅 3 词（刺客/杀手/黑衣人），"蒙面人" 漏过 → 服装
    锚块注入。新 _THREAT_KEYWORDS 6 词覆盖 "蒙面人"，ident 命中即视为威胁源。

    注：lf_desc 故意不含 "蒙面" 作为可见词（避免角色名里 "蒙" 误命中可见词表
    "蒙面"），不含 "船头/船舷"（避免邻句角色词跨窗口误判）。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("蒙面人"), _generic_char("听香")]
    shot = _shot_with_characters()
    lf_desc = "蒙面来客已潜入深水消失不见，杨旭仰卧，听香跪坐"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")

    assert "蒙面，仅露双眼" not in out, f"蒙面人服装锚块应被剔除，实测: {out}"
    assert "一身湿衣" not in out, f"蒙面人 dynamic 锚块应被剔除，实测: {out}"
    assert "鹅蛋脸" in out, f"听香锚块应照常注入，实测: {out}"


def test_inject_costume_lf_submerged_new_threat_keyword_black_shadow():
    """ident="黑影"（不在旧 _THREAT_IDS）+ lf_desc「已没入水中」→ 不注入.

    同上：扩表覆盖「黑影」威胁源变体。lf_desc 避免"黑"作为可见词/邻句角色词。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("黑影"), _generic_char("听香")]
    shot = _shot_with_characters()
    lf_desc = "暗影来客已潜入深水消失不见，杨旭仰卧，听香跪坐"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")

    assert "蒙面，仅露双眼" not in out, f"黑影服装锚块应被剔除，实测: {out}"


def test_inject_costume_ff_partially_visible_keeps_threat_anchor():
    """ff_desc「神秘人仅露头部」→ 刺客锚块照常注入.

    ff 路径回归：v6.56 修复对 first_frame 行为零变化。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("刺客"), _generic_char("听香")]
    shot = _shot_with_characters()
    ff_desc = "弦月当空，杨旭仰卧船头。神秘人湿衣伏于舟侧水下，仅露头部，一手持匕首于水面之下"

    out = _inject_character_costume(ff_desc, characters, shot, frame_kind="first_frame")

    assert "蒙面" in out, f"ff 刺客仅露头部应保留锚块，实测: {out}"


def test_inject_costume_kf_uses_scan_text_submerged():
    """kf 路径用 scan_text（合并全文）+ ident=刺客 + lf_desc 消失句
    → 刺客锚块不注入.

    设计原则：kf role_scan_text 合并全文（ff+lf+motion），如果 lf/motion 里
    刺客已消失，scan_text 应作为可见性判定文本（旧代码用 desc 只看 kf_desc，
    是另一类 bug）。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_assassin_char("刺客"), _generic_char("听香")]
    shot = _shot_with_characters()
    kf_desc = "中近景：杨旭仰卧，胸口洇红"
    # 不用"船头"等可见词作为邻句角色词（_threat_visible_in 8+18 窗口跨句跨词
    # 易误命中"船头"等贴靠实体可见词）
    role_scan = "杨旭仰卧。来客已潜入深水消失不见，听香跪坐"

    out = _inject_character_costume(kf_desc, characters, shot,
                                    frame_kind="keyframe", role_scan_text=role_scan)

    assert "蒙面" not in out, f"kf scan_text 判消失应剔除刺客锚块，实测: {out}"
    assert "一身湿衣" not in out, f"kf scan_text 判消失应剔除刺客 dynamic，实测: {out}"


def test_inject_costume_non_threat_unaffected():
    """非威胁源角色（听香）任何场景 → 照常注入，不受门控影响.

    回归：plan 明确「非威胁源角色不受影响」——确保扩表没误伤普通角色。
    """
    from shared.vimax_bridge import _inject_character_costume

    characters = [_generic_char("听香")]
    shot = _shot_with_characters()
    lf_desc = "听香披发跪坐，目光凝望杨旭，僵在当场"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")

    assert "鹅蛋脸" in out, f"听香锚块应照常注入，实测: {out}"
    assert "淡青襦裙" in out, f"听香 dynamic 应照常注入，实测: {out}"


# ============================================================================
# 4. 集成：image_qc 与 vimax_bridge 修复联动
# ============================================================================

def test_integration_lf_count_matches_injection_decision():
    """集成：lf 消失句场景下，_expected_persons 剔除威胁源 + _inject 也不注入
    → 人数与实际 prompt 锚块一致（人数 2，prompt 无刺客锚块）.

    旧 bug：人数 3（threat 保留）+ prompt 注入刺客锚块（count=3 实证）→
    QC 判「人数 vs 名单」critical 互斥。修复后人数 2 + 无刺客锚块 = 一致。
    """
    from shared.image_qc import _expected_persons
    from shared.vimax_bridge import _inject_character_costume

    lf_desc = "神秘人已没入水中不见踪影，匕首亦随人沉入水下。杨旭仰卧不动，听香披发跪坐僵在当场"
    lf_desc_final = lf_desc + "。刺客：蒙面，仅露双眼，一身湿衣，手持短刃（寒光）"

    shot = {
        "char_layout": [{"name": "杨旭"}, {"name": "听香"}, {"name": "神秘人"}],
        "lf_desc": lf_desc,
        "lf_desc_final": lf_desc_final,
    }
    characters = [
        {"identifier": "杨旭"},
        {"identifier": "听香"},
        _assassin_char("刺客"),
    ]

    n = _expected_persons(shot, "last_frame", characters)
    assert n == 2, f"人数应剔除威胁源，期望 2，实测 {n}"

    out = _inject_character_costume(lf_desc, characters, shot, frame_kind="last_frame")
    injected_threat = "蒙面" in out or "一身湿衣" in out
    assert not injected_threat, f"lf prompt 不应含刺客锚块，实测: {out}"

    assert n == 2 and not injected_threat, "人数与 prompt 锚块已对齐，bug 修复"


# ============================================================================
# 5. frame_narrative_desc 直接单元测试
# ============================================================================

def test_frame_narrative_desc_first_frame_raw_priority():
    """first_frame 路径：ff_desc 优先，ff_desc_final 兜底."""
    from shared.image_qc import frame_narrative_desc

    raw = "杨旭仰卧船头，闭目吟哦"
    final = raw + "。【角色锚定】"
    assert frame_narrative_desc({"ff_desc": raw, "ff_desc_final": final}, "first_frame") == raw
    assert frame_narrative_desc({"ff_desc_final": final}, "first_frame") == final
    assert frame_narrative_desc({}, "first_frame") == ""


def test_frame_narrative_desc_last_frame_raw_priority():
    """last_frame 路径：lf_desc 优先，lf_desc_final 兜底."""
    from shared.image_qc import frame_narrative_desc

    raw = "神秘人已没入水中不见踪影"
    final = raw + "。刺客：蒙面"
    assert frame_narrative_desc({"lf_desc": raw, "lf_desc_final": final}, "last_frame") == raw
    assert frame_narrative_desc({"lf_desc_final": final}, "last_frame") == final
    assert frame_narrative_desc({}, "last_frame") == ""


def test_frame_narrative_desc_merged_falls_through():
    """merged / 其他 frame_kind 走 ff+lf 合并路径."""
    from shared.image_qc import frame_narrative_desc

    shot = {"ff_desc": "杨旭仰卧", "lf_desc": "听香跪坐"}
    assert frame_narrative_desc(shot, "merged") == "杨旭仰卧 听香跪坐"
    # 未识别 frame_kind 同样走合并
    assert frame_narrative_desc(shot, "anything_else") == "杨旭仰卧 听香跪坐"
