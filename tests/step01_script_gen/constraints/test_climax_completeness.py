"""F17. ClimaxCompletenessConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_shot(index, prompt="", narration="", core_keywords=None, **extra):
    from tests.step01_script_gen.conftest import make_shot
    return make_shot(
        index=index,
        prompt=prompt,
        narration_segment=narration,
        subtitle=narration,
        core_keywords=core_keywords or [],
        **extra,
    )


def test_incomplete_climax_triggers_hard_fix():
    """高潮只有起势没有结果 → 硬修复追加结果态（v6.64 P4.6）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "杨旭缓步走入大厅，四处打量。", "", ["杨旭", "大厅"]),
        _make_shot(2, "黑衣人举刀逼近，寒光闪烁。", "刺客：受死吧！", ["刺杀", "逼近", "匕首"]),
        _make_shot(3, "杨旭后退，面露惊色。", "杨旭：你敢！", ["对峙", "反抗"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)

    assert len(repairs) >= 1
    # v6.64: 硬修复——lf_desc 应被追加结果态
    assert any("result" in r.action for r in repairs)
    # 验证 lf_desc 被修改
    shots = ctx.shots
    assert any("刺入" in (s.get("lf_desc") or "") for s in shots)


def test_complete_climax_no_warning():
    """高潮有结果（刺中/倒地） → 不触发."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "杨旭缓步走入大厅。", "", ["杨旭"]),
        _make_shot(2, "黑衣人举刀逼近。", "", ["刺杀", "匕首"]),
        _make_shot(3, "匕首刺中杨旭心口，白袍洇红。", "杨旭：呃——", ["刺中", "流血", "高潮"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)

    assert len(repairs) == 0
    violations = ctx.drain_violations()
    assert len(violations) == 0


def test_no_climax_skips():
    """没有高潮镜 → 不归本约束管（归 gap_analysis 零高潮检测）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "阳光明媚，河面波光粼粼。", "", ["风景", "河面"]),
        _make_shot(2, "杨旭吹箫，衣袂飘飘。", "", ["杨旭", "吹箫"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)
    assert len(repairs) == 0


def test_result_in_last_shot():
    """结果在最后一个高潮镜 → 算完整."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "杨旭站在船头。", "", ["杨旭"]),
        _make_shot(2, "刺客举刀逼近。", "", ["刺杀", "逼近"]),
        _make_shot(3, "杨旭侧身闪开。", "", ["打斗", "闪避"]),
        _make_shot(4, "刺客倒地，被擒住。", "", ["倒地", "擒住", "结局"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)
    assert len(repairs) == 0


def test_result_in_narration():
    """结果在台词里也算."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "两人对峙。", "", ["对峙"]),
        _make_shot(2, "黑衣人冷笑。", "黑衣人：你已经败露了！", ["败露", "揭露", "对峙"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)
    assert len(repairs) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint
    from step01_script_gen.constraints.types import Severity
    c = ClimaxCompletenessConstraint()
    assert c.name == "climax_completeness"
    assert c.severity == Severity.INFO
    assert c.skip_hook is True


def test_threat_only_is_incomplete():
    """只有威胁没有结果 = 不完整（run6 刺杀模式）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "烈日当空，河面平静。", "", ["环境"]),
        _make_shot(2, "水下黑影潜行。", "", ["水下", "潜伏"]),
        _make_shot(3, "黑影破水而出，手持匕首。", "", ["破水", "匕首"]),
        _make_shot(4, "刺客举刃逼近杨旭，面露凶光。", "", ["刺杀", "逼近", "举刃"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)

    # 有冲突（刺杀/逼近）但没结果 → 硬修复
    assert len(repairs) >= 1
    assert any("result" in r.action for r in repairs)


def test_background_action_word_no_longer_completes():
    """v6.63.1 回归：背景镜含日常动作词（抓住/点头）不再把高潮误判为完整.

    旧版全片扫描结果词 + 词表含「抓住/点头」这类日常词 → 任意背景镜出现
    「抓住船舷」「点点头」高潮就被判完整，F17 实际永不触发。修复后：
    结果只看高潮镜 + 其后 2 镜，且泛化词已从词表剔除。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.climax_completeness import ClimaxCompletenessConstraint

    script = make_script([
        _make_shot(1, "杨旭抓住船舷，看着河面点点头。", "", ["杨旭", "船舷"]),
        _make_shot(2, "黑影破水而出，手持匕首。", "", ["破水", "匕首"]),
        _make_shot(3, "刺客举刃逼近，寒光闪烁。", "", ["刺杀", "逼近", "举刃"]),
        _make_shot(4, "杨旭后退半步，握紧折扇。", "", ["对峙", "折扇"]),
    ])
    ctx = make_ctx(script)
    repairs = ClimaxCompletenessConstraint().apply(ctx)

    # 背景镜的「抓住/点头」不算结果，高潮仍无结果 → 硬修复触发
    assert len(repairs) >= 1
    assert any("result" in r.action for r in repairs)