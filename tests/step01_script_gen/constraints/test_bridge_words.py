"""W13. BridgeWordsConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_bridge_to_consecutive_same_location():
    """3+ 镜同 location 缺 transition word → 中段+末镜追加；段首跳过（v6.43）.

    v6.43（08-23 审计）：段首 shot 没有前镜，追加「前镜动作与构图自然过渡
    到本镜」与「开场建立」自相矛盾。改为只对段中 + 段末追加。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭", location="小舟"),
        make_shot(index=2, prompt="[中景] 听香", location="小舟"),
        make_shot(index=3, prompt="[中景] 二人", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.bridge_words import BridgeWordsConstraint

    repairs = BridgeWordsConstraint().apply(ctx)

    assert len(repairs) >= 1
    # 段首（idx=0）跳过「前镜动作…」；段中（1）+ 段末（2）追加
    assert "然后" not in script["shots"][0]["prompt"]
    for i in (1, 2):
        assert "然后" in script["shots"][i]["prompt"]
        assert script["shots"][i]["context_bridge"]  # 非空


def test_is_idempotent():
    """已含 transition word → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭，然后", location="小舟"),
        make_shot(index=2, prompt="[中景] 听香，然后", location="小舟"),
        make_shot(index=3, prompt="[中景] 二人，然后", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.bridge_words import BridgeWordsConstraint

    c = BridgeWordsConstraint()
    c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_skips_when_less_than_3_consecutive():
    """连续 2 镜同 location → 不构成 3+ 段，0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭", location="小舟"),
        make_shot(index=2, prompt="[中景] 听香", location="小舟"),
        make_shot(index=3, prompt="[中景] 岸边", location="岸边"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.bridge_words import BridgeWordsConstraint

    repairs = BridgeWordsConstraint().apply(ctx)
    assert repairs == []


def test_appends_only_context_bridge_when_prompt_already_has_word():
    """prompt 已含 transition word → 仅 context_bridge 兜底；旧版判定不修——但
    本实现以 prompt 为主判定，context_bridge 不修。验证连跑 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭然后", location="小舟"),
        make_shot(index=2, prompt="[中景] 听香然后", location="小舟"),
        make_shot(index=3, prompt="[中景] 二人然后", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.bridge_words import BridgeWordsConstraint

    repairs = BridgeWordsConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.bridge_words import BridgeWordsConstraint
    from step01_script_gen.constraints.types import Severity

    c = BridgeWordsConstraint()
    assert c.name == "bridge_words"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "然后"
    assert "prompt" in c.fields
    assert "context_bridge" in c.fields
