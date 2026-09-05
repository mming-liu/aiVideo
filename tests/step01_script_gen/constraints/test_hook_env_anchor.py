"""W17 HookEnvironmentAnchor 单元测试.

测试 4 类场景：
  1. 修复：hook 水面场景 → 注入 [ENV-ANCHOR] + 古风水岸锚定 + 泳池禁词
  2. 幂等：已含 [ENV-ANCHOR] → 跳过
  3. 隔离：location 非水面 + 文本无水面词 → 不触发
  4. 露出矛盾改写：「头顶 2cm 露出」 → 「完全没入水下不可见」
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_injects_anchor_for_water_scene():
    """hook 是水面场景（location 含「舟」）→ ff_desc/prompt 注入 [ENV-ANCHOR] + 禁词."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    hook = {
        "location": "云河镇照月湾水面小舟上",
        "ff_desc": "冷月映夜，一叶无篷舴艋舟浮于荷花水湾，杨旭坐于船舷侧。",
        "lf_desc": "杨旭仰倒船板，胸口洇痕。",
        "motion_desc": "神秘人自水下破水跃出。",
        "prompt": "水花四溅。",
    }

    n = _hard_fix_hook_environment_anchor(hook)
    assert n >= 2, f"应至少改 2 字段（ff_desc + prompt），实际 {n}"
    assert "[ENV-ANCHOR]" in hook["ff_desc"]
    assert "[ENV-ANCHOR]" in hook["prompt"]
    assert "ancient Chinese cinematic realism" in hook["ff_desc"]
    assert "no swimming pool" in hook["ff_desc"]
    assert "no ceramic tile" in hook["ff_desc"]


def test_idempotent_when_anchor_already_present():
    """已含 [ENV-ANCHOR] → 0 改动（不重复注入）."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    hook = {
        "location": "云河镇照月湾水面小舟上",
        "ff_desc": "[ENV-ANCHOR] ancient Chinese cinematic realism. 杨旭坐船舷。",
        "lf_desc": "",
        "motion_desc": "",
        "prompt": "水花。",
    }

    n1 = _hard_fix_hook_environment_anchor(hook)
    assert n1 == 0, f"已注入应跳过，实际改 {n1}"


def test_no_trigger_for_non_water_scene():
    """非水面场景（厅堂/室内 + 无水面词）→ 不触发."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    hook = {
        "location": "花厅",
        "ff_desc": "林平之端坐于花厅正中。",
        "lf_desc": "",
        "motion_desc": "",
        "prompt": "林平之端坐。",
    }

    n = _hard_fix_hook_environment_anchor(hook)
    assert n == 0, f"非水面场景不应触发，实际改 {n}"


def test_rewrites_expose_contradiction():
    """「头顶 2cm 露出」类自相矛盾 → 改写「完全没入水下不可见」."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    hook = {
        "location": "照月湾水面小舟上",
        "ff_desc": "神秘人潜伏水下，头顶 2cm 露出水面。",
        "lf_desc": "神秘人已彻底没入水中。",
        "motion_desc": "神秘人头顶约 1cm 露出。",
        "prompt": "水波激荡。",
    }

    n = _hard_fix_hook_environment_anchor(hook)
    assert n >= 3, f"应改至少 3 字段（ff_desc+lf_desc+motion_desc），实际 {n}"
    # 露出矛盾改写
    assert "完全没入水下不可见" in hook["ff_desc"]
    assert "2cm 露出" not in hook["ff_desc"]
    assert "1cm 露出" not in hook["motion_desc"]
    # 锚定前缀也注入
    assert "[ENV-ANCHOR]" in hook["ff_desc"]


def test_handles_missing_location():
    """location 缺失时按文本词判定（合理 fallback）→ 仍触发锚定."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    hook = {
        "location": "",
        "ff_desc": "夜泊荷花水湾，杨旭坐船舷。",
        "lf_desc": "",
        "motion_desc": "",
        "prompt": "夜泊。",
    }

    n = _hard_fix_hook_environment_anchor(hook)
    assert n >= 1, f"含夜泊词应触发，实际改 {n}"
    assert "[ENV-ANCHOR]" in hook["ff_desc"]


def test_no_trigger_when_no_hook():
    """hook=None → 早退不报错."""
    from step01_script_gen.validation import _hard_fix_hook_environment_anchor
    n = _hard_fix_hook_environment_anchor(None)
    assert n == 0

    n2 = _hard_fix_hook_environment_anchor({})
    assert n2 == 0
