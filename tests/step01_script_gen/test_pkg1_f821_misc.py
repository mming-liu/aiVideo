# -*- coding: utf-8 -*-
"""第 1 包 M03：ruff F821 指出的未定义名字（离线、零模型）.

每条用例在修复前都会以 NameError 失败；修复后断言原本设计的行为。
"""
from __future__ import annotations

import sys
import typing
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_config_module_annotations_resolve():
    """`AGENT_PLAN_TEXT_MAX_TOKENS: Optional[int]` 的注解可被求值."""
    import shared.config as cfg
    hints = typing.get_type_hints(cfg)
    assert hints["AGENT_PLAN_TEXT_MAX_TOKENS"] == typing.Optional[int]


def test_build_verification_prompt_raises_contract_error_on_admission(monkeypatch):
    """准入不过必须抛 PresentationContractError（而不是 NameError）."""
    from shared.presentation_contract import PresentationContractError
    from step01_script_gen import content_verify as cv
    monkeypatch.setattr(cv, "frozen_obligations", lambda view: [])
    monkeypatch.setattr(cv, "_request_admission",
                        lambda view: [{"code": "bad_input", "detail": "x"}])
    with pytest.raises(PresentationContractError):
        cv.build_verification_prompt({})


def test_kf_structural_violations_tolerates_named_layout_nodes():
    """布局节点带 `name` 时不得崩溃（原先写入未定义的 `_roster`）."""
    import step01_script_gen.main as m
    shot = {"index": 1, "keyframes": [], "frame_layouts": {"ff": [{"name": "甲"}],
                                                          "lf": [], "kfs": []}}
    before = {"characters": [{"identifier": "甲"}], "frame_layouts": [{"name": "乙"}],
              "shots": [shot], "hook": {"frame_layouts": [{"name": "丙"}]}}
    after = {"characters": [{"identifier": "甲"}], "shots": [dict(shot)]}
    plain_before = {"characters": [{"identifier": "甲"}], "shots": [dict(shot, frame_layouts={})]}
    plain_after = {"characters": [{"identifier": "甲"}], "shots": [dict(shot, frame_layouts={})]}
    got = m._kf_structural_violations(before, after, {}, set(), "")
    ref = m._kf_structural_violations(plain_before, plain_after, {}, set(), "")
    assert got == ref
