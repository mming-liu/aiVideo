# -*- coding: utf-8 -*-
"""v6.55 QC 断言 reliability 预分级单元测试（D 方案）.

校准驱动：first_frame_qc_log.json 14 次实抽。
分级规则：q 文本含 _LOW_TRIGGERS 触发词 → low，否则 high。
  high + key + 答错 → critical（现状不变）
  low  + 答错 → 累计 streak；streak ≥ 2 且 key → critical；否则 warning
  low  + 答对不清零（跨图无意义）

抽出 _claim_severity 纯函数便于单测，不依赖 maybe_qc_sync 完整 mock。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.image_qc import (
    build_claim_checks,
    _claim_reliability,
    _claim_severity,
)


# ============================================================================
# _claim_reliability：触发词扫描
# ============================================================================

def test_claim_reliability_high_existence():
    """存在性问句（红痕/接触/匕首/湿衣/持杆）→ high."""
    high_qs = [
        "画面任何人身上是否出现红色痕迹、伤口或血迹？",
        "刀具是否已接触或切入任何人的身体？",
        "画面是否表现：神秘人湿衣伏于舟侧水下？",
        "画面是否表现：手中握钓杆垂向水面？",
        "画面是否表现：杨旭素白袍袒腹仰卧船头？",
        "画面是否表现：一手持匕首于水面之下？",
    ]
    for q in high_qs:
        assert _claim_reliability(q) == "high", f"应 high: {q}"


def test_claim_reliability_low_proportion():
    """比例/空间类 → low."""
    low_qs = [
        "画面是否表现：船宽约为人物肩宽的三倍？",
        "画面是否表现：人物躺卧后船舷两侧仍有空档？",
        "画面是否表现：船舷两侧空隙均匀？",
    ]
    for q in low_qs:
        assert _claim_reliability(q) == "low", f"应 low: {q}"


def test_claim_reliability_low_semantic():
    """抽象语义类（双目紧闭/发光/光刃）→ low."""
    low_qs = [
        "画面是否表现：仰面朝上双目紧闭？",
        "图中是否有发光的物体或光刃？（月亮/月光/水面反光/灯光/火把等自然光不算）",
        "画面是否表现：表情惊愕？",
    ]
    for q in low_qs:
        assert _claim_reliability(q) == "low", f"应 low: {q}"


def test_claim_reliability_gaze_stays_high():
    """视线/朝向类不进 low（用户决策：视线答错走 regen，不降级）."""
    # 关键：`_CLAIM_KEY_WORDS` 已含"目光/凝望/面朝"，不能与 _LOW_TRIGGERS 重叠
    gaze_qs = [
        "画面是否表现：神秘人目光紧盯杨旭？",
        "画面是否表现：刺客面朝画左？",
        "画面是否表现：听香凝望船头？",
    ]
    for q in gaze_qs:
        assert _claim_reliability(q) == "high", f"视线类应保持 high: {q}"


def test_claim_reliability_always_true_glowing_is_low():
    """恒真规则「图中是否有发光」→ low（虽然字面是存在性问句，但 VLM
    区分不了月光/灯光/光刃，实抽矛盾率 36%，归入 low）."""
    q = "图中是否有发光的物体或光刃？（月亮/月光/水面反光/灯光/火把等自然光不算）"
    assert _claim_reliability(q) == "low"


def test_claim_reliability_empty_string():
    """空 q → high（默认）."""
    assert _claim_reliability("") == "high"
    assert _claim_reliability(None) == "high"  # type: ignore[arg-type]


# ============================================================================
# _claim_severity：纯函数判定
# ============================================================================

def _item(q: str, expect: str, key: bool = True) -> dict:
    """构造测试用 item，自动带 reliability."""
    return {"q": q, "expect": expect, "key": key, "reliability": _claim_reliability(q)}


def test_severity_high_key_wrong_critical():
    """high + key + 答错 → critical（现状不变，零回归核心保证）."""
    item = _item("画面任何人身上是否出现红色痕迹？", "no", key=True)
    sev, _ = _claim_severity(item, "yes", streak={})
    assert sev == "critical"


def test_severity_high_nonkey_wrong_warning():
    """high + 非 key + 答错 → warning（现状不变）."""
    item = _item("画面是否表现：船舱色调偏冷？", "yes", key=False)
    sev, _ = _claim_severity(item, "no", streak={})
    assert sev == "warning"


def test_severity_high_unclear_key_critical():
    """high + key + unclear → critical（现状不变，解析失败关键锚按答错拦）."""
    item = _item("画面任何人身上是否出现红色痕迹？", "no", key=True)
    sev, _ = _claim_severity(item, "unclear", streak={})
    assert sev == "critical"


def test_severity_high_unclear_nonkey_warning():
    """high + 非 key + unclear → warning（现状不变）."""
    item = _item("画面是否表现：船舱色调偏冷？", "yes", key=False)
    sev, _ = _claim_severity(item, "unclear", streak={})
    assert sev == "warning"


def test_severity_high_correct_returns_ok():
    """high + 答对 → ok（不写入 issues）."""
    item = _item("画面是否表现：手持钓杆？", "yes", key=True)
    sev, _ = _claim_severity(item, "yes", streak={})
    assert sev == "ok"


def test_severity_low_key_first_wrong_warning():
    """low + key + 首次答错 → warning（不进 critical 通道）."""
    item = _item("画面是否表现：船宽约为肩宽三倍？", "yes", key=True)
    sev, streak = _claim_severity(item, "no", streak={})
    assert sev == "warning"
    assert streak == 1  # 累计 1


def test_severity_low_key_second_wrong_critical():
    """low + key + 连续 2 次答错 → critical（升级 = 系统性）."""
    item = _item("画面是否表现：船宽约为肩宽三倍？", "yes", key=True)
    # 第一次答错：warning，streak=1
    sev1, streak_after1 = _claim_severity(item, "no", streak={})
    assert sev1 == "warning" and streak_after1 == 1
    # 第二次答错：critical，streak=2
    sev2, streak_after2 = _claim_severity(item, "no", streak={item["q"]: 1})
    assert sev2 == "critical"
    assert streak_after2 == 2


def test_severity_low_nonkey_even_after_two_wrong_still_warning():
    """low + 非 key + 累计多次答错仍 warning（只有 key 升级）."""
    item = _item("画面是否表现：表情惊愕？", "yes", key=False)
    # 第一次：warning，streak=1
    sev1, s1 = _claim_severity(item, "no", streak={})
    assert sev1 == "warning"
    # 第二次：streak=2 但非 key，仍 warning
    sev2, s2 = _claim_severity(item, "no", streak={item["q"]: 1})
    assert sev2 == "warning"
    assert s2 == 2


def test_severity_low_unclear_not_counted():
    """low + unclear → warning 但 streak 不增（解析失败 ≠ 画面事实）."""
    item = _item("画面是否表现：双目紧闭？", "yes", key=True)
    # unclear 不计次
    sev1, s1 = _claim_severity(item, "unclear", streak={})
    assert sev1 == "warning" and s1 == 0
    # 后续即使答对，streak 应维持 0（"答对不清零" + "unclear 不计次"两条原则并存）
    sev2, s2 = _claim_severity(item, "yes", streak={})
    assert sev2 == "ok" and s2 == 0


def test_severity_low_correct_does_not_clear_streak():
    """low + 答对 → ok 但 streak 维持（设计原则：跨图无意义，反复=系统性）."""
    item = _item("画面是否表现：双目紧闭？", "yes", key=True)
    # 第一次答错，streak=1
    _, s1 = _claim_severity(item, "no", streak={})
    assert s1 == 1
    # 第二次答对，streak 维持 1（不清零）
    sev2, s2 = _claim_severity(item, "yes", streak={item["q"]: 1})
    assert sev2 == "ok"
    assert s2 == 1


def test_severity_streak_persists_across_attempts_via_external_state():
    """调用方维护 streak 跨 attempt 累计（纯函数本身不持久化）.

    模拟：attempt 1 答错 → warning；attempt 2 答错 → critical
    """
    item = _item("画面是否表现：双目紧闭？", "yes", key=True)
    streak = {}
    # attempt 1
    sev1, s1 = _claim_severity(item, "no", streak)
    streak[item["q"]] = s1
    assert sev1 == "warning" and streak[item["q"]] == 1
    # attempt 2（重新调用，但 streak 已带）
    sev2, s2 = _claim_severity(item, "no", streak)
    assert sev2 == "critical" and s2 == 2


# ============================================================================
# build_claim_checks 集成：reliability 标打在出口
# ============================================================================

def test_build_claim_checks_marks_reliability():
    """build_claim_checks 出口每条 item 都带 reliability 字段."""
    shot = {
        "ff_desc": "杨旭素白袍袒腹仰卧船头。船宽约为人物肩宽的三倍。"
                   "仰面朝上双目紧闭。",
    }
    qs = build_claim_checks(shot, "first_frame")
    assert qs, "应生成至少一条断言"
    for q in qs:
        assert "reliability" in q, f"断言缺 reliability: {q['q']}"
        assert q["reliability"] in ("high", "low")


def test_build_claim_checks_glowing_is_low():
    """恒真规则「发光」问句自动 low（虽然字面是存在性）."""
    shot = {
        "ff_desc": "杨旭手中持匕首。",  # 触发"刀"路径 → 生成发光问句
    }
    qs = build_claim_checks(shot, "first_frame")
    glowing = [q for q in qs if "发光" in q["q"]]
    assert glowing, "应生成发光问句（因 ff_desc 含'刀'）"
    assert all(q["reliability"] == "low" for q in glowing), \
        f"发光问句应全部 low，实测: {[q['reliability'] for q in glowing]}"


def test_build_claim_checks_red_mark_is_high():
    """恒真规则「红色痕迹」问句 → high（存在性，矛盾率 0%）."""
    shot = {"ff_desc": "杨旭仰卧船头。"}
    qs = build_claim_checks(shot, "first_frame")
    red = [q for q in qs if "红色痕迹" in q["q"]]
    assert red, "应生成红痕问句（first_frame 恒真规则）"
    assert all(q["reliability"] == "high" for q in red)


def test_build_claim_checks_knife_contact_is_high():
    """「刀具接触身体」问句 → high（存在性）."""
    shot = {"ff_desc": "杨旭被刺，胸口中刀。"}
    qs = build_claim_checks(shot, "first_frame")
    knife = [q for q in qs if "刀具" in q["q"] and "接触" in q["q"]]
    assert knife, "应生成刀接触问句（ff_desc 含'刺/刀'）"
    assert all(q["reliability"] == "high" for q in knife)


def test_build_claim_checks_proportion_clause_is_low():
    """分镜描述中含"约"的比例子句 → low."""
    shot = {"ff_desc": "船宽约为人物肩宽的三倍。"}
    qs = build_claim_checks(shot, "first_frame")
    proportion = [q for q in qs if "约为" in q["q"] or "三倍" in q["q"]]
    # size_line 注入的"约为肩宽三倍"也会进 text（build_claim_checks 内联）
    assert proportion, "应生成比例类问句"
    assert all(q["reliability"] == "low" for q in proportion)


# ============================================================================
# P1 (08-30 review): maybe_qc_sync 集成测试——streak 写回 / flip 留痕 /
# _qc_prev_answers 跨 attempt 更新。v6.54.1 AttributeError 藏身同款缝隙。
#
# 复用 v653 monkeypatch 模式（qc_image / _detect_entities / _ask_yes_no /
# should_run_qc）。shot dict 跨 attempt 复用——这是 streak/prev_answers 持久
# 化的载体。
# ============================================================================

import pytest
import shared.image_qc as iq_mod
from shared.image_qc import QcResult


def _integration_shot() -> dict:
    """含 low 类问题（船宽三倍）+ high 类问题（红色痕迹）."""
    return {
        "ff_desc": "船宽约为人物肩宽的三倍。无红色痕迹。",
        "lf_desc": "",
        "motion_desc": "",
        "char_layout": [],
    }


def _stub_qc_image(*a, **k):
    """第 3 层 router 结果——空（无 issue）让第 1/2 层结果主导."""
    return QcResult(has_hallucination=False, issues=[], ok=True, raw_result="")


def _stub_entities(p):
    """3 person 实体（与 _expected_persons 容差 0 通过）."""
    return [
        {"label": "person", "desc": "a", "bbox": [0, 0, 100, 200]},
        {"label": "person", "desc": "b", "bbox": [200, 0, 300, 200]},
        {"label": "person", "desc": "c", "bbox": [400, 0, 500, 200]},
    ]


@pytest.fixture
def patched_qc(monkeypatch):
    """monkeypatch QC 依赖（沿用 v653 模式）."""
    monkeypatch.setenv("QC_PROVIDER", "auto")
    monkeypatch.setattr(iq_mod, "qc_image", _stub_qc_image)
    monkeypatch.setattr(iq_mod, "_detect_entities", _stub_entities)
    monkeypatch.setattr(iq_mod, "should_run_qc", lambda p: True)


def test_integration_low_streak_escalates_after_two_attempts(patched_qc, monkeypatch):
    """low + key 答错 attempt 1 → warning；attempt 2 仍错 → critical.

    这是 v6.55 D 方案核心机制：累计 ≥ 2 次答错升级 critical。
    mock `_ask_yes_no` 返 "no"（期望 yes 答错），连续调用同一 shot 验证 streak 跨 attempt 累计。
    """
    monkeypatch.setattr(iq_mod, "_ask_yes_no",
                        lambda p, qs: {q["q"]: "no" for q in qs})  # 全错
    shot = _integration_shot()

    # attempt 1：low 类首错 → warning；high 类关键错（红痕期望 no 答 no=答对）→ ok
    result1 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    low_crit_1 = [i for i in result1.issues
                  if i.category == "anchor_mismatch" and "约为" in i.description]
    assert low_crit_1, "应至少一条 low 类 anchor_mismatch（船宽三倍答 no）"
    assert all(i.severity == "warning" for i in low_crit_1), \
        f"attempt 1 low 应只 warning，实测: {[i.severity for i in low_crit_1]}"
    # shot 状态：_qc_low_streak 应含 "约为" / "三倍" 触发器命中条目
    assert "_qc_low_streak" in shot and shot["_qc_low_streak"], \
        f"attempt 1 后 _qc_low_streak 应非空，实测: {shot.get('_qc_low_streak')}"
    assert "_qc_prev_answers" in shot and shot["_qc_prev_answers"], \
        f"attempt 1 后 _qc_prev_answers 应非空"

    # attempt 2：仍全错 → 累计升级 critical
    result2 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    low_crit_2 = [i for i in result2.issues
                  if i.category == "anchor_mismatch" and "约为" in i.description]
    assert low_crit_2, "attempt 2 应仍有 low 类 anchor_mismatch"
    assert all(i.severity == "critical" for i in low_crit_2), \
        f"attempt 2 low 应升级 critical，实测: {[i.severity for i in low_crit_2]}"


def test_integration_unstable_flips_recorded_bidirectionally(patched_qc, monkeypatch):
    """high + key 跨 attempt 翻转（双向）→ unstable_flips 留痕.

    attempt 1 答对（yes），attempt 2 答错（no）→ 记 flip
    attempt 3 又答对（yes）→ 再记 flip（双向留痕 P3-①）
    """
    answers_history = [
        {q["q"]: "yes" for q in []},  # placeholder
    ]

    # dynamic answer sequence per attempt
    sequence = [
        {q["q"]: "yes" for q in []},  # attempt 1: 全对
        {q["q"]: "no"  for q in []},  # attempt 2: 全错
        {q["q"]: "yes" for q in []},  # attempt 3: 又全对
    ]
    call_count = {"n": 0}

    def _dynamic_ask(p, qs):
        i = call_count["n"]
        call_count["n"] += 1
        return dict(sequence[i]) | {q["q"]: ("yes" if i % 2 == 0 else "no") for q in qs}

    monkeypatch.setattr(iq_mod, "_ask_yes_no", _dynamic_ask)
    shot = _integration_shot()

    # 简化 shot 让 build_claim_checks 至少产 1 条 high key 题
    shot["ff_desc"] = "无红色痕迹。"  # 红痕 high+key 题

    # attempt 1：全对
    r1 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    assert r1.unstable_flips in (None, []), f"attempt 1 无翻转应空，实测: {r1.unstable_flips}"

    # attempt 2：全错 → 红痕答 yes 期望 no，跨 attempt 翻转 yes→no
    r2 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    assert r2.unstable_flips, f"attempt 2 应有 flips，实测: {r2.unstable_flips}"
    flip = r2.unstable_flips[0]
    assert flip["prev"] == "yes" and flip["got"] == "no", f"应为 yes→no，实测: {flip}"

    # attempt 3：又全对 → 翻转 no→yes（双向留痕 P3-①）
    r3 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    assert r3.unstable_flips, f"attempt 3 应仍有 flips（双向）"
    flip2 = r3.unstable_flips[0]
    assert flip2["prev"] == "no" and flip2["got"] == "yes", f"应为 no→yes（双向），实测: {flip2}"


def test_integration_unclear_does_not_advance_low_streak(patched_qc, monkeypatch):
    """low + unclear 不计次（v6.55 行为矩阵）——即使 attempt 1 unclear，attempt 2 答错仍属首错."""
    answers_by_attempt = [
        {q["q"]: "unclear" for q in []},  # attempt 1: 全 unclear
        {q["q"]: "no" for q in []},       # attempt 2: 全错
    ]
    call_count = {"n": 0}

    def _dynamic(p, qs):
        i = call_count["n"]
        call_count["n"] += 1
        return {q["q"]: ("unclear" if i == 0 else "no") for q in qs}

    monkeypatch.setattr(iq_mod, "_ask_yes_no", _dynamic)
    shot = _integration_shot()
    shot["ff_desc"] = "船宽约为人物肩宽的三倍。"  # 确保生成 low 类问句

    # attempt 1: unclear → warning 但 streak 不增
    r1 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    streak_after_1 = shot.get("_qc_low_streak", {})
    # 任何 low 类问句都应该是 warning（unclear 也产 warning），但 streak 值全 0
    assert all(v == 0 for v in streak_after_1.values()), \
        f"unclear 不应增 streak，实测: {streak_after_1}"

    # attempt 2: 答错（明确 no）→ 应是首错（streak 从 0 升到 1），不是续错（不升级 critical）
    r2 = iq_mod.maybe_qc_sync("whatever.png", shot, "first_frame", None)
    low_crit_2 = [i for i in r2.issues
                  if i.category == "anchor_mismatch" and "约为" in i.description]
    assert low_crit_2
    assert all(i.severity == "warning" for i in low_crit_2), \
        f"unclear 不计次 → attempt 2 答错应为首错（warning），实测: {[i.severity for i in low_crit_2]}"
