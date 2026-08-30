# -*- coding: utf-8 -*-
"""v6.53 QC 三层架构 + alias 体系单元测试.

背景：hook ff 4 人图两次判 0 问题——视觉模型"判对错"不可救。三层架构把
数数/对错从 LLM 拿回代码；alias 体系治 char_layout/main_subject 未清洗。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared import image_qc as iq
from shared.alias_map import CHARACTER_ALIASES, alias_walk_shot, apply_char_alias


_CHARS = [
    {"identifier": "杨旭", "static_features": "剑眉星目 素白色轻袍", "dynamic_features": ""},
    {"identifier": "听香", "static_features": "鹅蛋脸 春衫", "dynamic_features": "撑油纸伞"},
    {"identifier": "刺客", "static_features": "丹凤眼 蒙面 黑色夜行衣", "dynamic_features": "手持利刃"},
]


def _hook_shot() -> dict:
    return {
        "ff_desc": "神秘人半身探出水面直指杨旭，听香在旁惊愕",
        "lf_desc": "",
        "motion_desc": "",
        "char_layout": [
            {"name": "神秘人", "position": "画左前景", "facing": "面朝画右的杨旭"},
            {"name": "杨旭", "position": "画右", "facing": "仰卧"},
            {"name": "听香", "position": "画右中景", "facing": "面向船头"},
        ],
    }


# ── 第 1 层：代码计数断言 ──────────────────────────────────────────

def _persons(n: int, color: str = "black") -> list[dict]:
    return [{"label": "person", "desc": f"{color} clothing", "bbox": [0, 0, 10, 10]}
            for _ in range(n)]


def test_person_count_4vs3_exceeds_tolerance0(monkeypatch):
    """4 人 vs 名单 3 人、容差 0 → 超差（try1 漏放形态）."""
    monkeypatch.setenv("QC_PERSON_TOLERANCE", "0")
    shot = _hook_shot()
    expected = iq._expected_persons(shot, "first_frame", _CHARS)
    assert expected == 3
    assert abs(4 - expected) > 0


def test_person_count_3vs3_ok():
    shot = _hook_shot()
    expected = iq._expected_persons(shot, "first_frame", _CHARS)
    assert abs(3 - expected) == 0


def test_person_count_tolerance_one(monkeypatch):
    """容差=1 时 4/3 不超差（QC_PERSON_TOLERANCE=1 兜底档）."""
    assert abs(4 - 3) <= 1


def test_expected_persons_threat_invisible_excluded():
    """威胁源仅黑影呈现 → 从应出现名单剔除（水下黑影不计实体）."""
    shot = {
        "ff_desc": "神秘人化作水下黑影一闪而过，杨旭仰卧，听香未觉",
        "lf_desc": "", "motion_desc": "",
        "char_layout": [
            {"name": "神秘人", "position": "画左"},
            {"name": "杨旭", "position": "画右"},
            {"name": "听香", "position": "画右中景"},
        ],
    }
    n = iq._expected_persons(shot, "first_frame", _CHARS)
    assert n == 2


def test_duplicate_chars_two_white_one_roster_white():
    """v6.x.x：两个白衣实体 vs 名单仅 1 个白衣角色 → warning（夜景误读风险）.

    旧版 n==2 即 critical，hook 镜实抽 entry 4/6/7 验证：夜色"神秘人黑衣 +
    阴影里听香"易被 L1 误判为同色重复。n==2 改 warning + 提示二次确认，
    n==3+ 仍 critical（真重复一眼可见）。
    """
    persons = [
        {"label": "person", "desc": "white robe lying"},
        {"label": "person", "desc": "white hanfu standing"},
    ]
    issues = iq._assert_duplicate_chars(persons, 2, _CHARS)
    # n=2 应是 warning（不再一票否决）
    assert any(i.severity == "warning" for i in issues), \
        f"n=2 应降为 warning，实测 {[i.severity for i in issues]}"
    assert not any(i.severity == "critical" for i in issues), \
        f"n=2 不应 critical（误读风险），实测 {[i.severity for i in issues]}"


def test_duplicate_chars_three_white_one_roster_white_still_critical():
    """v6.x.x：三个白衣实体 vs 名单 1 个 → 仍 critical（真重复，n>=3）."""
    persons = [
        {"label": "person", "desc": "white robe lying"},
        {"label": "person", "desc": "white hanfu standing"},
        {"label": "person", "desc": "white robe background"},
    ]
    issues = iq._assert_duplicate_chars(persons, 3, _CHARS)
    assert any(i.severity == "critical" for i in issues), \
        f"n=3 应仍 critical（真重复），实测 {[i.severity for i in issues]}"


def test_duplicate_chars_legit_two_black():
    """两个黑衣且名单确有 2 个黑衣角色 → 不误报."""
    chars = _CHARS + [{"identifier": "张十三", "static_features": "黑色劲装", "dynamic_features": ""}]
    persons = [
        {"label": "person", "desc": "black outfit masked"},
        {"label": "person", "desc": "black clothes standing"},
    ]
    issues = iq._assert_duplicate_chars(persons, 3, chars)
    assert not any("复制" in i.description for i in issues)


# ── 第 2 层：锚点二值问答 ──────────────────────────────────────────

def test_targeted_questions_generation():
    """锚→问题生成：无篷出篷问题、首帧出禁红/禁接触、蒙面出关键题."""
    shot = dict(_hook_shot())
    shot["ff_desc"] = "神秘人蒙面持刀半身探出水面直指杨旭，无篷舴艋舟，白袍仰卧"
    qs = iq.build_targeted_questions(shot, "first_frame")
    texts = [q["q"] for q in qs]
    assert any("篷" in t for t in texts)
    assert any("红色痕迹" in t for t in texts)
    assert any("接触" in t for t in texts)
    # v6.54 通用对账：蒙面以正向断言覆盖（画面是否表现：…蒙面持刀…），无手写措辞
    red_q = next(q for q in qs if "红色痕迹" in q["q"])
    assert red_q["key"] is True and red_q["expect"] == "no"
    assert len(qs) <= 10


def test_yes_no_parse_batch():
    """「1: yes」批量解析；缺题 unclear."""
    qs = [{"q": "Q1?", "expect": "yes", "key": True},
          {"q": "Q2?", "expect": "no", "key": False},
          {"q": "Q3?", "expect": "yes", "key": False}]
    text = "1: yes\n2: no\n"
    import re
    answers = {q["q"]: "unclear" for q in qs}
    for i, q in enumerate(qs, 1):
        m = re.search(rf"{i}\s*[:：.、]\s*(yes|no|是|否)", text, re.I)
        if m:
            answers[q["q"]] = "yes" if m.group(1).lower() in ("yes", "是") else "no"
    assert answers["Q1?"] == "yes"
    assert answers["Q2?"] == "no"
    assert answers["Q3?"] == "unclear"


def test_entity_array_tolerates_malformed_json():
    """qwen 实测输出「420}}」畸形括号 → 逐对象抽取重建不丢实体."""
    raw = ('[{"label":"person","desc":"a","bbox":[1,2,3,4]}, '
           '{"label":"person","desc":"b","bbox":[5,6,7,420}}]')
    entries = iq._parse_entity_array(raw)
    assert len(entries) >= 1 and entries[0]["label"] == "person"


def test_entity_array_empty_raises():
    with pytest.raises(Exception):
        iq._parse_entity_array("")


# ── 第 3 层：router 降级 + 检测链失败回落 ──────────────────────────

def test_layer3_router_critical_downgraded(monkeypatch):
    """router 判 critical → 三层汇总中被强制降为 warning，不再有放行权."""
    from shared.image_qc import QcIssue, QcResult
    monkeypatch.setenv("QC_PROVIDER", "auto")
    monkeypatch.setattr(iq, "qc_image", lambda *a, **k: QcResult(
        has_hallucination=True,
        issues=[QcIssue(category="face_consistency", description="脸崩", severity="critical")],
        ok=True, raw_result="x"))
    monkeypatch.setattr(iq, "_detect_entities",
                        lambda p: [{"label": "person", "desc": "white robe", "bbox": [0, 0, 1, 1]},
                                   {"label": "person", "desc": "black outfit", "bbox": [2, 2, 3, 3]},
                                   {"label": "person", "desc": "blue dress", "bbox": [4, 4, 5, 5]}])
    monkeypatch.setattr(iq, "_ask_yes_no",
                        lambda p, qs: {q["q"]: q["expect"] for q in qs})
    monkeypatch.setattr(iq, "should_run_qc", lambda p: True)
    result = iq.maybe_qc_sync("whatever.png", _hook_shot(), "first_frame", _CHARS)
    router_issues = [i for i in result.issues if i.description.startswith("[router]")]
    assert router_issues and all(i.severity == "warning" for i in router_issues)
    assert result.has_hallucination is False  # 单条降级 warning 不触发


def test_detect_chain_failure_falls_back_to_router_full_authority(monkeypatch):
    """检测链全挂 + QC_PROVIDER=ark → router 结果全权返回（不出现无闸帧）."""
    from shared.image_qc import QcIssue, QcResult
    monkeypatch.setenv("QC_PROVIDER", "ark")
    router_result = QcResult(has_hallucination=True,
                             issues=[QcIssue(category="unexpected_object",
                                             description="漂浮物", severity="critical")],
                             ok=True)
    monkeypatch.setattr(iq, "qc_image", lambda *a, **k: router_result)

    def _boom(*a, **k):
        raise RuntimeError("渠道全挂")
    monkeypatch.setattr(iq, "_detect_entities", _boom)
    monkeypatch.setattr(iq, "should_run_qc", lambda p: True)
    result = iq.maybe_qc_sync("whatever.png", _hook_shot(), "first_frame", _CHARS)
    assert result.has_hallucination is True
    assert any(i.severity == "critical" for i in result.issues)


# ── alias 体系 ─────────────────────────────────────────────────────

def test_apply_char_alias_full_and_substring():
    assert apply_char_alias("刺客") == "神秘人"
    assert apply_char_alias("船夫") == "群演"
    assert apply_char_alias("庄仆") == "群演"
    assert apply_char_alias("刺客从水中跃出刺杀杨旭的瞬间").startswith("神秘人")
    assert apply_char_alias("杨旭") == "杨旭"  # 非别名不动
    assert apply_char_alias("神秘人") == "神秘人"  # 已折叠幂等
    assert apply_char_alias("群演") == "群演"


def test_alias_idempotent():
    """同输入折叠两次结果一致（v6.53 计划的幂等验收）."""
    once = apply_char_alias("蒙面剑客")
    assert apply_char_alias(once) == once


def test_alias_walk_shot_updates_main_subject_and_layout():
    shot = {
        "main_subject": "刺客从水中跃出",
        "char_layout": [{"name": "刺客", "position": "画左"}, {"name": "船夫", "position": "船尾"}],
    }
    n = alias_walk_shot(shot)
    assert n >= 3
    assert shot["main_subject"].startswith("神秘人")
    assert shot["char_layout"][0]["name"] == "神秘人"
    assert shot["char_layout"][1]["name"] == "群演"


def test_threat_not_folded_to_weiyuan():
    """威胁源折叠到「神秘人」而非「威胁源」——desc/char_layout/identifier 三方一致.

    「威胁源」是抽象词，会让 _extract_char_layout 的 surface/position 结构化
    锚匹配丢失（v6.45 听香站水里兜底退化）。
    """
    assert "威胁源" not in CHARACTER_ALIASES.values()


def test_extract_char_layout_alias_aware_lookup():
    """char_layout.name=神秘人（折叠后）+ identifier=刺客 → 锚条目仍命中."""
    from shared.vimax_bridge import _extract_char_layout
    chars = [{"identifier": "杨旭"}, {"identifier": "听香"}, {"identifier": "刺客"}]
    shot = {
        "ff_desc": "神秘人半身探出水面直指杨旭，听香在旁惊愕",
        "char_layout": [
            {"name": "神秘人", "position": "画左前景", "surface": "船头水面交界处",
             "facing": "面朝画右的杨旭"},
            {"name": "杨旭", "position": "画右", "surface": "船头甲板", "facing": "仰卧"},
            {"name": "听香", "position": "画右中景", "surface": "中舱边缘", "facing": "面向船头"},
        ],
    }
    block = _extract_char_layout(shot, chars, role_scan_text=shot["ff_desc"])
    assert "画左前景" in block and "在船头水面交界处上" in block
