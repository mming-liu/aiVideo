# -*- coding: utf-8 -*-
"""S14-04：载体通道的字段白名单必须写进请求（与应用侧拒收同一份 allowed_fields）."""
from __future__ import annotations

import copy

import pytest

from _text_gen.models import TextGenerationResult
from step01_script_gen import outline as o
from step01_script_gen.plan_revision import RevisionBudget
from step01_script_gen.revision_route import CARRIER_FIELDS
from tests.step01_script_gen import test_voice_split_entry as vs

pytestmark = pytest.mark.v2_request


class _Svc:
    def __init__(self):
        self.prompts = []

    def generate(self, req):
        self.prompts.append(req.user_prompt)
        return TextGenerationResult(success=True, call_id="x", text="不是 JSON")


def _run(monkeypatch, allowed):
    plan, _v0, ctl, problems = vs._setup()
    svc = _Svc()
    monkeypatch.setattr(o, "create_service", lambda *a, **k: svc)
    ev = {}
    o._replan_by_view(copy.deepcopy(plan), problems, vs.NOVEL,
                      RevisionBudget(max_calls=2, deadline_sec=600), evidence=ev,
                      mode="legacy_audio", verification=ctl,
                      protocol=o.ROUTE_VIEW_DELTA, allowed_fields=allowed)
    return svc.prompts[0], ev


def test_carrier_route_declares_allowed_fields(monkeypatch):
    prompt, ev = _run(monkeypatch, list(CARRIER_FIELDS))
    assert "本轮通道只允许改这些字段" in prompt
    assert all(f in prompt for f in CARRIER_FIELDS)
    assert ev["allowed_fields_declared"] == sorted(CARRIER_FIELDS)


def test_unrestricted_route_has_no_whitelist(monkeypatch):
    prompt, ev = _run(monkeypatch, None)
    assert "本轮通道只允许改这些字段" not in prompt
    assert "allowed_fields_declared" not in ev
