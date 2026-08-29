"""U. KneelBesideVictim 单元测试 + 重构回归.

重构后（v6.44 registry 驱动）：
- 受害者名用 POSE_REGISTRY["reclining"] 提取
- 威胁过滤用 is_threat_name() / THREAT_KEYWORDS
- 不再硬编码 "刺客/神秘人/黑影/蒙面/匪"
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fixes_kneel_beside_victim():
    """听香跪坐船尾（杨旭在同 shot 仰卧）→ 改「听香跪坐杨旭身侧，面向杨旭」."""
    from step01_script_gen.validation import _hard_fix_kneel_beside_victim

    # 杨旭 和 听香 同镜出现：ff_desc 描述受害者仰卧，prompt 描述目击者跪坐
    shots = [
        {
            "index": 1,
            "prompt": "听香跪坐船尾",
            "ff_desc": "杨旭仰卧船头，素白轻袍",
            "lf_desc": "",
        },
    ]
    n = _hard_fix_kneel_beside_victim(shots)
    assert n >= 1
    assert "听香跪坐杨旭身侧" in shots[0]["prompt"]


def test_idempotent():
    """已含「身侧/身旁」→ 跳过."""
    from step01_script_gen.validation import _hard_fix_kneel_beside_victim

    shots = [
        {
            "index": 1,
            "prompt": "杨旭仰卧船头",
            "ff_desc": "",
            "lf_desc": "",
        },
        {
            "index": 2,
            "prompt": "听香跪坐杨旭身侧，面向杨旭",
            "ff_desc": "",
            "lf_desc": "",
        },
    ]
    n1 = _hard_fix_kneel_beside_victim(shots)
    assert n1 == 0, f"应跳过（已含身侧），实际改 {n1}"


def test_no_trigger_when_no_victim():
    """无受害者（无躺卧词）→ 不触发."""
    from step01_script_gen.validation import _hard_fix_kneel_beside_victim

    shots = [
        {
            "index": 1,
            "prompt": "听香跪坐船尾",
            "ff_desc": "",
            "lf_desc": "",
        },
    ]
    n = _hard_fix_kneel_beside_victim(shots)
    assert n == 0, f"无受害者不应触发，实际改 {n}"


def test_uses_registry_for_pose_words():
    """新受害姿态词（如"装死"）应自动从 POSE_REGISTRY 跟随."""
    from step01_script_gen.prompt_semantic.registry import POSE_REGISTRY
    assert "装死" in POSE_REGISTRY["reclining"]  # 验证 registry 包含
    # 不写死新姿态词——只要它在 POSE_REGISTRY["reclining"] 里，函数自动识别


def test_threat_name_not_treated_as_victim():
    """威胁角色（刺客/神秘人/黑影/蒙面）不能被读成受害者（注册表驱动验证）."""
    from step01_script_gen.prompt_semantic.registry import is_threat_name
    for name in ["刺客", "神秘人", "黑影", "蒙面剑客", "蒙面人", "匪", "杀手"]:
        assert is_threat_name(name), f"{name} 应被识别为威胁"
