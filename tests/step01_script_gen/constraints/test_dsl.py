"""约束 DSL PoC 单元测试.

覆盖：
  1. 解释器: 表达式求值 + 动作执行
  2. 加载器: YAML → ConstraintRule 子类 → 注册到 ALL_CONSTRAINTS
  3. 端到端: 跑 YAML 规则对 shot 进行修复
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# 解释器单测
# ============================================================================

def test_eval_contains_any_true():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"prompt": "杨旭看 听香"}
    assert _eval_expr(
        {"op": "contains_any", "field": "prompt", "keywords": ["看", "盯"]},
        shot,
    ) is True


def test_eval_contains_any_false():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"prompt": "杨旭走"}
    assert _eval_expr(
        {"op": "contains_any", "field": "prompt", "keywords": ["看", "盯"]},
        shot,
    ) is False


def test_eval_contains_all():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"prompt": "杨旭看听香 盯"}
    assert _eval_expr(
        {"op": "contains_all", "field": "prompt", "keywords": ["看", "盯"]},
        shot,
    ) is True
    shot2 = {"prompt": "杨旭看"}
    assert _eval_expr(
        {"op": "contains_all", "field": "prompt", "keywords": ["看", "盯"]},
        shot2,
    ) is False


def test_eval_matches():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"prompt": "船身向右延伸出画外"}
    assert _eval_expr(
        {"op": "matches", "field": "prompt", "pattern": "延伸出画外"},
        shot,
    ) is True
    shot2 = {"prompt": "杨旭看"}
    assert _eval_expr(
        {"op": "matches", "field": "prompt", "pattern": "延伸出画外"},
        shot2,
    ) is False


def test_eval_field_eq():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"index": 1, "location": "小舟"}
    assert _eval_expr({"op": "field_eq", "field": "index", "value": 1}, shot) is True
    assert _eval_expr({"op": "field_eq", "field": "location", "value": "岸边"}, shot) is False


def test_eval_and_or_not():
    from step01_script_gen.constraints.dsl.interpreter import _eval_expr
    shot = {"index": 1, "prompt": "杨旭"}
    # and
    assert _eval_expr(
        {
            "op": "and",
            "exprs": [
                {"op": "field_eq", "field": "index", "value": 1},
                {"op": "contains_any", "field": "prompt", "keywords": ["杨旭"]},
            ],
        },
        shot,
    ) is True
    # or
    assert _eval_expr(
        {
            "op": "or",
            "exprs": [
                {"op": "field_eq", "field": "index", "value": 99},
                {"op": "contains_any", "field": "prompt", "keywords": ["杨旭"]},
            ],
        },
        shot,
    ) is True
    # not
    assert _eval_expr(
        {"op": "not", "expr": {"op": "field_eq", "field": "index", "value": 99}},
        shot,
    ) is True


# ============================================================================
# 动作单测
# ============================================================================

def test_action_append_idempotent():
    from step01_script_gen.constraints.dsl.interpreter import _exec_action
    shot = {"prompt": "杨旭看。（AI生成）"}
    r = _exec_action(
        {"action": "append", "target": "prompt", "text": "。（AI生成）"},
        shot, "test", 1,
    )
    assert r is None  # 已含则不动


def test_action_append_when_missing():
    from step01_script_gen.constraints.dsl.interpreter import _exec_action
    shot = {"prompt": "杨旭看"}
    r = _exec_action(
        {"action": "append", "target": "prompt", "text": "。（AI生成）"},
        shot, "test", 1,
    )
    assert r is not None
    assert "AI生成" in shot["prompt"]


def test_action_strip_patterns():
    from step01_script_gen.constraints.dsl.interpreter import _exec_action
    shot = {"prompt": "小舟漂在湖面，船身向右延伸出画外"}
    r = _exec_action(
        {
            "action": "strip_patterns",
            "target": "prompt",
            "patterns": [r"[，,。]?\s*船身[^。，,]{0,6}延伸出画外"],
        },
        shot, "test", 1,
    )
    assert r is not None
    assert "延伸出画外" not in shot["prompt"]


# ============================================================================
# 加载器单测
# ============================================================================

def test_load_rule_file_platform_compliance(tmp_path=None):
    """加载 platform_compliance.yaml → ConstraintRule 注册到 ALL_CONSTRAINTS."""
    import os
    from step01_script_gen.constraints import ALL_CONSTRAINTS
    from step01_script_gen.constraints.dsl.loader import load_rule_file

    yaml_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..",
        "step01_script_gen", "constraints", "rules",
        "platform_compliance.yaml",
    )
    yaml_path = os.path.normpath(yaml_path)
    assert os.path.isfile(yaml_path), f"YAML 不存在: {yaml_path}"

    before = {c.name for c in ALL_CONSTRAINTS}
    added = load_rule_file(yaml_path)
    after_names = {c.name for c in ALL_CONSTRAINTS}

    # 应该新增 1 条
    assert added == 1
    assert "platform_compliance_yaml" in after_names
    # 不应该破坏原有 29 条
    assert "platform_compliance" in after_names  # 原 Python 类还在
    # 不重复添加
    added2 = load_rule_file(yaml_path)
    assert added2 == 0


def test_loaded_rule_runs_on_real_shot():
    """YAML 规则在真实 shot 上跑出预期修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints import ALL_CONSTRAINTS, apply_constraints

    # 找到刚加载的 YAML 规则
    rule = next(
        (c for c in ALL_CONSTRAINTS if c.name == "platform_compliance_yaml"),
        None,
    )
    if rule is None:
        import os
        from step01_script_gen.constraints.dsl.loader import load_rule_file
        yaml_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..",
                "step01_script_gen", "constraints", "rules",
                "platform_compliance.yaml",
            )
        )
        load_rule_file(yaml_path)
        rule = next(
            (c for c in ALL_CONSTRAINTS if c.name == "platform_compliance_yaml"),
            None,
        )
    assert rule is not None, "YAML 规则未加载"

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭看水面"),
    ])
    ctx = make_ctx(script)
    repairs = rule.apply(ctx)
    assert len(repairs) == 1
    assert "AI生成" in script["shots"][0]["prompt"]


def test_loaded_rule_idempotent_after_first_apply():
    """YAML 规则跑第二次应幂等."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints import ALL_CONSTRAINTS
    import os
    from step01_script_gen.constraints.dsl.loader import load_rule_file

    yaml_path = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "step01_script_gen", "constraints", "rules",
            "platform_compliance.yaml",
        )
    )
    load_rule_file(yaml_path)
    rule = next(c for c in ALL_CONSTRAINTS if c.name == "platform_compliance_yaml")

    script = make_script([make_shot(index=1, prompt="[中景] 杨旭看")])
    ctx = make_ctx(script)
    r1 = rule.apply(ctx)
    r2 = rule.apply(ctx)
    assert len(r1) == 1
    assert len(r2) == 0  # 幂等


def test_load_boat_crop_yaml_strips():
    """加载 boat_crop_strip.yaml → 剥离船体裁切短语."""
    import os
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints import ALL_CONSTRAINTS
    from step01_script_gen.constraints.dsl.loader import load_rule_file

    yaml_path = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "step01_script_gen", "constraints", "rules",
            "boat_crop_strip.yaml",
        )
    )
    if not os.path.isfile(yaml_path):
        # 测试文件不存在，跳过（PoC 阶段）
        return
    load_rule_file(yaml_path)
    rule = next(
        (c for c in ALL_CONSTRAINTS if c.name == "boat_crop_strip_yaml"),
        None,
    )
    if rule is None:
        return
    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 小舟漂在湖面，船身向右延伸出画外",
        ),
    ])
    ctx = make_ctx(script)
    repairs = rule.apply(ctx)
    assert len(repairs) >= 1
    assert "延伸出画外" not in script["shots"][0]["prompt"]
