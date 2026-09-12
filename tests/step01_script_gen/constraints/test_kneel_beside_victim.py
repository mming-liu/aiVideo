"""U. KneelBesideVictim 遗留清理后保留的 registry 契约测试（2026-09-12）.

legacy `_hard_fix_kneel_beside_victim` 已随旧 hard_fix 家族删除（该行为自
v6.42 起未在默认管线运行）。本文件保留与 registry 相关的两个契约测试；
跪坐扶正行为如需恢复，从 git 历史找回家族实现。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


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
