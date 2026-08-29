"""F7. RestrainedEmotionChainConstraint 单元测试（v6.49, 08-28）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_restrained_emotion_few_pats_appends_chain():
    """克制情绪镜拍数 < 3 → 追加 3 拍时间链."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=7.0,
            prompt="张十三强忍悲痛",
            motion_desc="张十三望前方",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.restrained_emotion_chain import RestrainedEmotionChainConstraint

    repairs = RestrainedEmotionChainConstraint().apply(ctx)

    assert len(repairs) == 1
    assert "微表情时间链" in script["shots"][0]["motion_desc"]


def test_already_has_3_pats_skipped():
    """已含 ≥3 个微表情词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=7.0,
            prompt="张十三噙泪",
            motion_desc="①抿唇 ②噙泪 ③苦笑，时间先后写全",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.restrained_emotion_chain import RestrainedEmotionChainConstraint

    repairs = RestrainedEmotionChainConstraint().apply(ctx)

    assert len(repairs) == 0


def test_no_restrained_emotion_skipped():
    """无克制情绪词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=7.0, prompt="杨旭静坐", motion_desc="静坐喝茶"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.restrained_emotion_chain import RestrainedEmotionChainConstraint

    repairs = RestrainedEmotionChainConstraint().apply(ctx)

    assert len(repairs) == 0


def test_idempotent_with_template():
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=7.0,
            prompt="张十三隐忍",
            motion_desc="微表情时间链：①低头隐忍（抿唇/垂眼）→ ②泪光（噙泪/眼眶泛红）→ ③苦笑/释怀（唇角微扬/下颌放松），每拍 1 个面部细节按时间先后写全",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.restrained_emotion_chain import RestrainedEmotionChainConstraint

    repairs = RestrainedEmotionChainConstraint().apply(ctx)

    assert len(repairs) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.restrained_emotion_chain import RestrainedEmotionChainConstraint
    from step01_script_gen.constraints.types import Severity
    c = RestrainedEmotionChainConstraint()
    assert c.name == "restrained_emotion_chain"
    assert c.severity == Severity.WARN
