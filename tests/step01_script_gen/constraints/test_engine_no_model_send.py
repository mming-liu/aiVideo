# -*- coding: utf-8 -*-
"""约束引擎不发模型请求（迁移自已删除的 test_regen_trigger.py 的两条引擎保护用例）.

2026-11-21（精简联合计划 M02）：`constraints/regen_trigger.py` 自 v6.98 起没有生产调用方，
已删除。它留下的唯一仍有效的保护是：确定性 hard-fix 层**不得**调用 LLM——即使存在
未解决的 HARD 违例、即使 `REGEN_AUTO_TRIGGER=1`。这里在真实发送边界
（`_text_gen.service.create_service` 与 `requests` 的发送函数）拦截，不依赖已删除模块的名字。
"""
from __future__ import annotations

import inspect

import pytest


def _script_with_unresolved_hard():
    """最小匿名样本：总时长远低于下限 → runtime_extend 留下 HARD 未解决."""
    return {
        "shots": [{"index": 1, "duration": 4.0, "duration_sec": 4.0,
                   "prompt": "中景", "ff_desc": "f", "lf_desc": "l",
                   "motion_desc": "0-2秒：A", "narration_segment": "",
                   "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}],
        "hook": {"index": 0, "duration": 3.0, "duration_sec": 3.0, "prompt": "钩子",
                 "ff_desc": "f", "lf_desc": "l", "motion_desc": "0-1秒：A",
                 "narration_segment": "", "keyframes": []},
        "characters": [{"identifier": "角色甲", "static_features": "白袍"}],
        "fps": 24, "total_duration": 7.0,
    }


@pytest.fixture
def send_spy(monkeypatch):
    import requests

    from _text_gen import service

    sends: list = []

    def _blocked(*a, **k):
        sends.append(("create_service", a, k))
        raise AssertionError("约束引擎不得创建文本生成服务")

    def _blocked_http(*a, **k):
        sends.append(("http", a, k))
        raise AssertionError("约束引擎不得发 HTTP 请求")

    monkeypatch.setattr(service, "create_service", _blocked)
    monkeypatch.setattr(requests, "post", _blocked_http)
    monkeypatch.setattr(requests.Session, "request", _blocked_http)
    return sends


@pytest.mark.parametrize("auto_trigger", ["0", "1"])
def test_unresolved_hard_is_reported_without_any_model_send(monkeypatch, send_spy, auto_trigger):
    monkeypatch.setenv("REGEN_AUTO_TRIGGER", auto_trigger)
    from step01_script_gen.constraints import run_hard_fix

    script, report = run_hard_fix(_script_with_unresolved_hard(),
                                  is_final_pass=True, return_report=True)

    assert send_spy == [], "确定性 hard-fix 层不得发模型请求（审核 P0-4）"
    assert report.hard_unresolved, "未解决的 HARD 违例必须以结构化报告交还调用方"
    assert isinstance(script, dict)


def test_engine_source_has_no_regen_call_site():
    """静态锁：engine 源码里不得出现 regen 触发 / 合并调用（已删模块的旧入口名）."""
    from step01_script_gen.constraints import engine

    code = "\n".join(ln.split("#", 1)[0] for ln in inspect.getsource(engine).splitlines())
    for name in ("trigger_regen_and_merge", "trigger_regen_if_needed", "regen_trigger"):
        assert name not in code, f"run_hard_fix 里又接回了 LLM regen：{name}"
