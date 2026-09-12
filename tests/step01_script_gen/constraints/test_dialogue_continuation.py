# -*- coding: utf-8 -*-
"""P4.1.2 延续对白说话人回填（dialogue_density）单元测试.

场景：doubao 把同一句原文引语拆成多镜旁白，首镜带「XX道：」前缀，
后续延续镜只剩裸句 → _has_dialogue() 不识别 → 对白镜占比被低估。
回填逻辑从原文动态提取说话人（零角色名硬编码）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
from step01_script_gen.constraints.dialogue_density import (
    DialogueDensityConstraint,
    _has_dialogue,
)


# 取自《锦衣夜行》第 1 章真实形态：引语归属句中说话人与「说」之间
# 隔着「却对媒人们」——正则候选会被「们」过滤，必须靠前镜说话人回填。
NOVEL_XIAO = (
    "三年孝期刚过，杨文轩杨公子又参加府学，一举考中了诸生。\n"
    "莫道君行早，更有早行人。可惜了，杨府肖管事却对媒人们说："
    "“抱歉的很，我家少爷自幼便由老爷作主，在应天府老家那边订下一门亲事了，"
    "我家少爷早晚是要回乡成亲的。”\n"
    "既然名草有主，此事自然休提。\n"
)

# 引语归属句「XX道：」直接紧邻引号——正则候选路径可独立命中。
NOVEL_REGEX = (
    "杨旭缓步走上船头，河面波光粼粼。\n"
    "杨府管家肖伯道：“抱歉的很，我家少爷自幼便由老爷作主，"
    "在应天府老家那边订下一门亲事了。”\n"
    "既然名草有主，此事自然休提。\n"
)


def _six_shots():
    """复刻实机输出形态：镜2 对白 + 镜3 延续裸句，其余空旁白."""
    return [
        make_shot(index=1),
        make_shot(
            index=2,
            narration_segment="肖管事道：抱歉的很，我家少爷自幼便由老爷作主",
            subtitle="肖管事道：抱歉的很，我家少爷自幼便由老爷作主",
        ),
        make_shot(
            index=3,
            narration_segment="在应天府老家那边订下一门亲事了",
            subtitle="在应天府老家那边订下一门亲事了",
        ),
        make_shot(index=4),
        make_shot(index=5),
        make_shot(index=6),
    ]


def test_backfill_from_prev_speaker():
    """延续裸句 + 前镜对白 → 回填前镜说话人（真实案例回归）."""
    shots = _six_shots()
    ctx = make_ctx(make_script(shots), novel_text=NOVEL_XIAO)
    repairs = DialogueDensityConstraint().apply(ctx)

    backfills = [r for r in repairs if r.action == "backfill_continuation_speaker"]
    assert len(backfills) == 1
    assert backfills[0].shot_index == 2
    assert shots[2]["narration_segment"] == (
        "肖管事道：在应天府老家那边订下一门亲事了"
    )
    # subtitle 同步
    assert shots[2]["subtitle"] == shots[2]["narration_segment"]
    assert _has_dialogue(shots[2]["narration_segment"])


def test_backfill_from_novel_attribution_regex():
    """无前镜对白 → 从原文「XX道：」归属句正则提取说话人."""
    shots = _six_shots()
    shots[1]["narration_segment"] = ""  # 去掉前镜对白，只走 regex 路径
    shots[1]["subtitle"] = ""
    ctx = make_ctx(make_script(shots, characters=["肖伯"]), novel_text=NOVEL_REGEX)
    repairs = DialogueDensityConstraint().apply(ctx)

    backfills = [r for r in repairs if r.action == "backfill_continuation_speaker"]
    assert len(backfills) == 1
    assert shots[2]["narration_segment"].startswith("肖伯道：")
    assert "在应天府老家那边订下一门亲事了" in shots[2]["narration_segment"]


def test_narration_not_in_novel_no_backfill():
    """旁白无原文出处（编造）→ 不回填（交由 dialogue_grounding 处理）."""
    shots = _six_shots()
    shots[2]["narration_segment"] = "完全编造的句子原文里根本没有"
    shots[2]["subtitle"] = "完全编造的句子原文里根本没有"
    ctx = make_ctx(make_script(shots), novel_text=NOVEL_XIAO)
    repairs = DialogueDensityConstraint().apply(ctx)

    assert not [r for r in repairs if r.action == "backfill_continuation_speaker"]
    assert shots[2]["narration_segment"] == "完全编造的句子原文里根本没有"


def test_narration_outside_quote_no_backfill():
    """旁白在原文中但不在引语内（纯叙述）→ 不回填."""
    shots = _six_shots()
    shots[2]["narration_segment"] = "既然名草有主，此事自然休提"
    shots[2]["subtitle"] = "既然名草有主，此事自然休提"
    ctx = make_ctx(make_script(shots), novel_text=NOVEL_XIAO)
    repairs = DialogueDensityConstraint().apply(ctx)

    assert not [r for r in repairs if r.action == "backfill_continuation_speaker"]
    assert shots[2]["narration_segment"] == "既然名草有主，此事自然休提"


def test_idempotent_no_double_prefix():
    """重复 apply 不叠加前缀（幂等）."""
    shots = _six_shots()
    ctx = make_ctx(make_script(shots), novel_text=NOVEL_XIAO)
    constraint = DialogueDensityConstraint()
    constraint.apply(ctx)
    first = shots[2]["narration_segment"]
    constraint.apply(ctx)
    assert shots[2]["narration_segment"] == first
    assert shots[2]["narration_segment"].count("肖管事道：") == 1


def test_dialogue_ratio_improves():
    """回填后 _has_dialogue 计数提升 → 对白镜占比修正."""
    shots = _six_shots()
    ctx = make_ctx(make_script(shots), novel_text=NOVEL_XIAO)
    DialogueDensityConstraint().apply(ctx)

    n = len(shots)
    before = 1  # 只有镜2
    after = sum(1 for s in shots if _has_dialogue(s.get("narration_segment", "")))
    assert after == before + 1
    assert after / n >= 1 / 3
