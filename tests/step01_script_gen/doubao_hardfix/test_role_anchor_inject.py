"""B.3.1 RoleAnchorInjectConstraint 单元测试（3 用例）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_injects_character_anchor_in_prompt():
    """主含人名 + prompt 无 [name: features] -> 头部注入."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="杨旭站立", prompt="中景, 杨旭端坐船头"),
    ])
    bank = {"杨旭": {"static_features": "白衣书生,二十出头,腰悬长剑"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    p = script["shots"][0]["prompt"]
    assert "[杨旭:" in p
    assert "_DOUBAO_FIX_RA:" in p


def test_uses_script_characters_when_bank_is_empty():
    """bank 为空但 script.characters 有输出 → 仍应注入锚点。"""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script(
        [make_shot(index=1, main_subject="杨旭站立", prompt="中景, 杨旭端坐船头")],
        characters=[{"identifier": "杨旭", "static_features": "白衣书生, 腰悬长剑"}],
    )
    ctx = make_ctx(script)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    assert "[杨旭: 白衣书生, 腰悬长剑]" in script["shots"][0]["prompt"]


def test_skips_when_anchor_already_present():
    """prompt 已含 [name: features] -> 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="杨旭站立", prompt="[杨旭: 白衣书生] 中景"),
    ])
    bank = {"杨旭": {"static_features": "白衣书生"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert repairs == []


def test_skips_empty_shot():
    """主是空镜 (无角色 anchor) -> 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(index=1, main_subject="空镜·水波", prompt="远景"),
    ])
    bank = {"杨旭": {"static_features": "白衣"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    from step01_script_gen.constraints.types import Severity
    c = RoleAnchorInjectConstraint()
    assert c.name == "doubao_role_anchor_inject"
    assert c.severity == Severity.WARN

def test_recovers_from_generic_main_subject_via_keywords():
    """主词是群演，但 core_keywords 含角色名 → B.3.1 从 keywords 提取角色名注入 prompt + main_subject。
    v6.62.0 阶段 D.3 升级补充。"""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="群演",
            core_keywords="杨旭, 舱梨, 船头, 垂钓",
            narration_segment="杨旭道：今日江面平静",
            prompt="中景, 船头架灯下",
        ),
    ])
    bank = {"杨旭": {"static_features": "素白轻袍, 右手持钓杆"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    p = script["shots"][0]["prompt"]
    ms = script["shots"][0]["main_subject"]
    assert "[杨旭:" in p
    assert "杨旭" in ms
    assert ms.startswith("杨旭")

def test_recovers_from_empty_main_subject_via_narration():
    """main_subject=某男子，narration 含角色名 → B.3.1 提取。"""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="某男子",
            narration_segment="听香道：公子小心！",
            prompt="近景",
        ),
    ])
    bank = {"听香": {"static_features": "青袍长发"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    repairs = RoleAnchorInjectConstraint().apply(ctx)
    assert len(repairs) == 1
    assert "[听香:" in script["shots"][0]["prompt"]

def test_idempotent_after_general_fallback():
    """连续调用 2 次：第二次该镜已含锚点，不再重复修复。"""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    script = make_script([
        make_shot(
            index=1,
            main_subject="群演",
            core_keywords="张十三, 船舶, 警告",
            prompt="中景",
        ),
    ])
    bank = {"张十三": {"static_features": "黑衣短袍"}}
    ctx = make_ctx(script, characters_bank=bank)
    from step01_script_gen.doubao_hardfix import RoleAnchorInjectConstraint
    c = RoleAnchorInjectConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) == 1
    assert r2 == []
    assert script["shots"][0]["main_subject"].startswith("张十三")
