# -*- coding: utf-8 -*-
"""C03：钩子分类预算的运行期读取（0..3；非法回退 import 期默认）."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import step01_script_gen.main as m


def test_hook_max_sends_env_override_and_bounds(monkeypatch):
    monkeypatch.setenv("HOOK_MAX_SENDS", "1")
    assert m._hook_max_sends() == 1
    monkeypatch.setenv("HOOK_MAX_SENDS", "0")
    assert m._hook_max_sends() == 0
    monkeypatch.setenv("HOOK_MAX_SENDS", "9")
    assert m._hook_max_sends() == 3, "上限 3（改造前行为）"
    monkeypatch.setenv("HOOK_MAX_SENDS", "not-a-number")
    assert m._hook_max_sends() == 3, "非法值回退 import 期默认"


def test_hook_max_sends_module_override_wins(monkeypatch):
    monkeypatch.setattr(m, "HOOK_MAX_SENDS", 1)
    monkeypatch.setenv("HOOK_MAX_SENDS", "3")
    assert m._hook_max_sends() == 1, "显式改写模块常量视为强制覆盖"


def test_hook_max_attempts_config_bounds():
    """钩子请求尝试预算的默认值在 0..6（发送边界由 CallCounter 执行）."""
    from shared import config as c
    assert 0 <= c.HOOK_MAX_ATTEMPTS <= 6
