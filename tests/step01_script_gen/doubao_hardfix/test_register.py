"""register_doubao_hardfixes 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def restore_doubao_registration_state():
    """隔离 Constraint 注册状态，避免测试顺序影响结果。"""
    from step01_script_gen.constraints import engine as eng
    import step01_script_gen.doubao_hardfix as dh

    saved_constraints = list(eng.ALL_CONSTRAINTS)
    saved_flag = dh._DOUBAO_HARDFIX_REGISTERED
    yield
    eng.ALL_CONSTRAINTS[:] = saved_constraints
    dh._DOUBAO_HARDFIX_REGISTERED = saved_flag


def test_register_appends_seven_constraints():
    """register 应 append 7 个 doubao_* Constraint 到 ALL_CONSTRAINTS."""
    from step01_script_gen.constraints import engine as eng
    import step01_script_gen.doubao_hardfix as dh
    from step01_script_gen.doubao_hardfix import register_doubao_hardfixes
    # 先解注册（仅测试用）
    before = [c for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_")]
    for c in before:
        eng.ALL_CONSTRAINTS.remove(c)
    dh._DOUBAO_HARDFIX_REGISTERED = False
    # register 一次
    register_doubao_hardfixes()
    doubao_constraints = [c for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_")]
    assert len(doubao_constraints) == 7
    names = {c.name for c in doubao_constraints}
    assert "doubao_dialogue_narration_empty" in names
    assert "doubao_gap_keyword_force_inject" in names
    assert "doubao_role_anchor_inject" in names
    assert "doubao_opening_empty_shot_insert" in names
    assert "doubao_climax_back_inject" in names
    assert "doubao_overshoulder_consistency" in names
    assert "doubao_frame_spec_reconstruct" in names


def test_register_is_idempotent():
    """register 连调 2 次不会重复追加."""
    import step01_script_gen.doubao_hardfix as dh
    from step01_script_gen.constraints import engine as eng
    from step01_script_gen.doubao_hardfix import register_doubao_hardfixes
    # 清空 + 重置 flag
    for c in [x for x in eng.ALL_CONSTRAINTS if x.name.startswith("doubao_")]:
        eng.ALL_CONSTRAINTS.remove(c)
    dh._DOUBAO_HARDFIX_REGISTERED = False
    register_doubao_hardfixes()
    count_after_first = sum(1 for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_"))
    # 第二次调不需重置 flag（flag 已在第一次调时置 True）
    register_doubao_hardfixes()
    count_after_second = sum(1 for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_"))
    assert count_after_first == count_after_second == 7


def test_deepseek_path_does_not_auto_register():
    """TEXT_PROVIDER != agent_plan 时 import doubao_hardfix 不应自动 register."""
    import os
    os.environ.pop("TEXT_PROVIDER", None)
    os.environ["TEXT_PROVIDER"] = "deepseek"
    # 此时 import doubao_hardfix 已经发生过（conftest 触发）—— 强制重 import 检查
    import importlib
    # 清除 register flag
    import step01_script_gen.doubao_hardfix as dh
    dh._DOUBAO_HARDFIX_REGISTERED = False
    # 模拟 deepseek 路径：临时改 env
    saved = os.environ.get("TEXT_PROVIDER")
    os.environ["TEXT_PROVIDER"] = "deepseek"
    # 重 import
    importlib.reload(dh)
    from step01_script_gen.constraints import engine as eng
    # 此时不应该有 doubao_* Constraint 自动注册
    # （但前面测试已注册——只验证 module-level 自动 register 路径不走）
    # 此测试要求 register_doubao_hardfixes 不被自动调过——通过清空 doubao_ constraints
    doubao_count = sum(1 for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_"))
    # 因为 reload 时 TEXT_PROVIDER=deepseek, 自动 register 跳过
    # 但之前测试 register_appends 已经注册过 7 个 — 清掉
    for c in [x for x in eng.ALL_CONSTRAINTS if x.name.startswith("doubao_")]:
        eng.ALL_CONSTRAINTS.remove(c)
    # reload 模块会再尝试 module-level register（跳过因为 deepseek）
    dh._DOUBAO_HARDFIX_REGISTERED = False
    importlib.reload(dh)
    after = sum(1 for c in eng.ALL_CONSTRAINTS if c.name.startswith("doubao_"))
    assert after == 0, f"deepseek 路径不应自动注册, 但有 {after} 个 doubao_ constraint"
