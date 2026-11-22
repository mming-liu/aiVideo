"""背景档两档判据（2026-11-15 §10.6）：计划级 `background_facts` 的结构校验与契约入口.

四条事实（f02/f03 纯背景 partial、f06 主线因果）混进同一轮修复请求、模型试图一个
delta 全修而 under-defined 被拒（run 20261115T122229）——落地裁决：
`required_facts` 不动字符串格式，另立计划级 `background_facts`（uid 列表）；
背景档事实的载体 `partial` 放行（`shared/content_verification.py` 门控，另有
`tests/shared/test_content_verification.py` §⑤），本文件只测**计划侧**的形状判据。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from step01_script_gen.outline import outline_structure_problems  # noqa: E402
from step01_script_gen.plan_revision import DEFAULT_MAX_CALLS  # noqa: E402
from _text_gen.deepseek_schema import OUTLINE_SCHEMA  # noqa: E402


def _plan(**extra):
    """一份结构合法的最小计划（4 条必保留事实 → f01–f04）."""
    plan = {
        "main_events": ["黄昏泛舟", "晚餐", "刺杀", "驾车回城"],
        "climax": "刺客从水中窜出刺伤甲",
        "required_facts": [
            "甲今年刚及弱冠｜原文证据：“甲家的主人名叫甲，今年刚及弱冠。”｜承载：B2/旁白1",
            "甲十六岁接手家业｜原文证据：“四年前把家当交到了他年仅十六岁的独生子甲手中。”｜承载：B2/旁白2",
            "乙是买来的美人｜原文证据：“甲公子也不会花了两百贯宝钞的高价把她买下”｜承载：B3/旁白4",
            "丙驾车载甲回城｜原文证据：“公子爷吃醉了酒，险些跌落水中。”｜承载：B8/台词1",
        ],
        "causal_chain": ["泛舟→刺杀→回城"],
        "shot_beats": [
            "节拍：黄昏泛舟｜本镜信息目的：交代场景｜原文依据：甲家的主人名叫甲｜载体：旁白1",
            "节拍：晚餐｜本镜信息目的：交代背景｜原文依据：年仅十六岁的独生子甲手中｜载体：旁白2",
            "节拍：刺杀｜本镜信息目的：高潮｜原文依据：甲公子也不会花了两百贯宝钞的高价把她买下｜载体：画面",
            "节拍：驾车回城｜本镜信息目的：结局钩子｜原文依据：公子爷吃醉了酒｜载体：台词1",
        ],
        "dialogue_lines": ["公子爷吃醉了酒，险些跌落水中。"],
    }
    plan.update(extra)
    return plan


def _codes(probs):
    return [p["code"] for p in probs]


# ---------------------------------------------------------------------------
# 结构校验：合法声明放行，三类畸形各有出口
# ---------------------------------------------------------------------------
def test_valid_background_declaration_passes_structure():
    """合法声明（已有事实编号）⇒ 不产生任何 background_facts 结构问题."""
    got = _codes(outline_structure_problems(_plan(background_facts=["f02", "f03"])))
    assert not [c for c in got if c.startswith("structure_background")], got


def test_background_declaration_absent_means_all_causal():
    """缺省（无该字段）＝ 全部主线档：结构与不声明时完全一致（向后兼容）."""
    assert _codes(outline_structure_problems(_plan())) == \
        _codes(outline_structure_problems(_plan(background_facts=[])))


def test_background_facts_type_problems():
    got = _codes(outline_structure_problems(_plan(background_facts="f02")))
    assert "structure_background_facts_type" in got, got
    got = _codes(outline_structure_problems(_plan(background_facts=[2, 3])))
    assert "structure_background_facts_type" in got, got
    got = _codes(outline_structure_problems(_plan(background_facts=["f02", "  "])))
    assert "structure_background_facts_type" in got, got


def test_background_facts_duplicate_uid_rejected():
    got = _codes(outline_structure_problems(_plan(background_facts=["f02", "f02"])))
    assert "structure_background_facts_duplicate" in got, got


def test_background_facts_unknown_uid_rejected():
    """引用不存在的编号 ⇒ 拒收（背景档只收已登记的必保留事实）."""
    got = _codes(outline_structure_problems(_plan(background_facts=["f09"])))
    assert "structure_background_facts_unknown_uid" in got, got


def test_background_uids_follow_declared_fact_uid_table():
    """计划声明了 `presentation.fact_uids` 时，编号按那张表核（不按默认 f 序列）."""
    plan = _plan(background_facts=["fx02"],
                 presentation={"fact_uids": ["fx01", "fx02", "fx03", "fx04"]})
    got = _codes(outline_structure_problems(plan))
    assert not [c for c in got if c.startswith("structure_background")], got
    # 表外的编号照样拒
    plan2 = _plan(background_facts=["f02"],
                  presentation={"fact_uids": ["fx01", "fx02", "fx03", "fx04"]})
    got2 = _codes(outline_structure_problems(plan2))
    assert "structure_background_facts_unknown_uid" in got2, got2


# ---------------------------------------------------------------------------
# JSON 契约：OUTLINE_SCHEMA 必须允许模型输出该字段（additionalProperties: False）
# ---------------------------------------------------------------------------
def test_outline_schema_accepts_background_facts():
    try:
        import jsonschema
    except ImportError:                              # pragma: no cover
        pytest.skip("jsonschema 未安装")
    plan = _plan(background_facts=["f02"])
    jsonschema.validate(plan, OUTLINE_SCHEMA)        # 不抛即通过


def test_outline_schema_rejects_non_list_background_facts():
    try:
        import jsonschema
    except ImportError:                              # pragma: no cover
        pytest.skip("jsonschema 未安装")
    with pytest.raises(Exception):
        jsonschema.validate(_plan(background_facts="f02"), OUTLINE_SCHEMA)


# ---------------------------------------------------------------------------
# 额度（2026-11-15 用户批准：先 4，同日第 2 次批准 6）
# ---------------------------------------------------------------------------
def test_revision_budget_default_raised_to_six():
    """内容修复轮额度批准：3 → 4 → 6（run 131952c 实测 4/4 用满仍剩两条主线 partial）."""
    assert DEFAULT_MAX_CALLS == 6


def test_budget_json_roundtrip_mentions_approval():
    """批准出处可追溯：常量上方注释记录了两次实测 run 依据."""
    src = (Path(__file__).resolve().parents[2]
           / "step01_script_gen" / "plan_revision.py").read_text(encoding="utf-8")
    assert "20261115T122229" in src and "20261115T131952" in src
    assert "DEFAULT_MAX_CALLS = 6" in src
