"""B.2 GapKeywordForceInjectConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_injects_missing_keywords():
    """core_keywords 5 个, blob 全无 -> ff_desc 末尾追加 3 个 anchor."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭转身",
            prompt="中景, 杨旭船头站立",
            ff_desc="杨旭站立",
            lf_desc="杨旭拔剑",
            motion_desc="缓慢移动",
            core_keywords="刺客,血,刀光,杀意,危机",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import GapKeywordForceInjectConstraint
    repairs = GapKeywordForceInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    ff = script["shots"][0]["ff_desc"]
    assert "_DOUBAO_FIX_GAP:" in ff
    assert "刺客" in ff or "血" in ff or "刀光" in ff


def test_skips_when_keywords_all_hit():
    """core_keywords 全部命中 -> 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="血战",
            prompt="刺客血刀光",
            ff_desc="刺客血刀光",
            lf_desc="血刀光继续",
            motion_desc="血刀光刀光刀光",
            core_keywords="刺客,血,刀光",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import GapKeywordForceInjectConstraint
    repairs = GapKeywordForceInjectConstraint().apply(ctx)
    assert repairs == []


def test_handles_list_form_core_keywords():
    """core_keywords 列表形式 -> 仍能解析."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="场景",
            prompt="无关键词",
            ff_desc="空",
            lf_desc="空",
            motion_desc="",
            core_keywords=["刺客", "血"],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import GapKeywordForceInjectConstraint
    repairs = GapKeywordForceInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    assert "_DOUBAO_FIX_GAP:" in script["shots"][0]["ff_desc"]


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import GapKeywordForceInjectConstraint
    from step01_script_gen.constraints.types import Severity
    c = GapKeywordForceInjectConstraint()
    assert c.name == "doubao_gap_keyword_force_inject"
    assert c.severity == Severity.WARN