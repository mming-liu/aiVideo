# -*- coding: utf-8 -*-
"""P03 固定稿运行器的事务级用例（C02）：真实 `_repair_shot_warnings` + 桩服务.

覆盖六类：
1. 合法成功（一次请求即被采纳）；
2. 首轮拒收 → 下一次请求携带**真实拒收反馈**；
3. 中间补帧（插入一个关键帧）不被事务拒绝；
4. 目标未解决（改动未消除告警 ⇒ 不采纳、保留基线）；
5. 局部回退（坏目标回退、其余目标保留）；
6. 预算停止（终局状态在**发请求之前**生效）。

样本为匿名最小情节；桩只替换模型调用，应用器/验收/回退/反馈都是真实代码。
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import step01_script_gen.main as m                      # noqa: E402
from step01_script_gen.quality_issues import Issue      # noqa: E402
from tests.step01_script_gen.test_repair_targets import _complete_shot  # noqa: E402

MARKER = "独眼鹰标记只此处出现"


class _Log:
    def __init__(self):
        self.lines: list = []

    def info(self, msg, *a, **k):
        self.lines.append(str(msg))

    def warn(self, msg, *a, **k):
        self.lines.append(str(msg))

    def error(self, msg, *a, **k):
        self.lines.append(str(msg))

    def verbose(self, *a, **k):
        pass

    def console(self, msg, *a, **k):
        self.lines.append(str(msg))


class _Harness:
    """按调用次序返回不同补丁；捕获每个真实请求（含 user_inputs）."""

    def __init__(self, monkeypatch, *, responses, audit_issues, script, warnings_fn):
        self.log = _Log()
        self.reqs: list = []
        self.n = 0
        _reqs = self.reqs
        _state = {"n": 0}

        class _R:
            success = True
            error = ""

            def __init__(self, payload):
                self.text = json.dumps(payload, ensure_ascii=False)

        class _Svc:
            def generate(self, req):
                _reqs.append(req)
                idx = min(_state["n"], len(responses) - 1)
                _state["n"] += 1
                return _R(responses[idx])

        monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
        monkeypatch.setattr(m, "resolve_model_prompt_profile",
                            lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
        monkeypatch.setattr(m, "_collect_repair_warnings", warnings_fn)
        monkeypatch.setattr(m, "_logger", self.log)
        monkeypatch.setattr(m, "_console", lambda msg, *a, **k: self.log.lines.append(str(msg)))
        for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
            monkeypatch.setattr(m, _fn, lambda *a, **k: None)
        self.script = script
        self.audit_issues = list(audit_issues)
        self.diag: list = []
        self.block: list = []
        self.retry_state: dict = {}

    def run(self, *, max_rounds=1):
        self.out = m._repair_shot_warnings(
            copy.deepcopy(self.script), "novel", audit_issues=self.audit_issues,
            max_rounds=max_rounds, repair_diag=self.diag, block_reasons=self.block,
            retry_state=self.retry_state)
        self.n = len(self.reqs)
        return self

    def feedback(self, i=-1) -> str:
        req = self.reqs[i]
        return "\n".join([req.system_prompt or "", req.user_prompt or ""] + [
            str(x.get("content") or "") for x in (req.user_inputs or [])])

    def shots(self) -> dict:
        return {str(x.get("index")): x for x in self.out.get("shots") or []}


def _warnings_fn(_script, _novel):
    shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
    issues, ws = [], {}
    if "圆睁" not in str((shots.get("4") or {}).get("ff_desc") or ""):
        issues.append({"code": "state_chain_static", "severity": "visual_risk",
                       "shot_id": "4", "frame_id": "ff", "entity_id": "",
                       "field_path": "ff_desc", "message": "shot 4 状态链断裂"})
        ws["shot_4"] = ["[state_chain_static] shot 4 状态链断裂"]
    if "触发新阻塞" in str((shots.get("4") or {}).get("ff_desc") or ""):
        issues.append({"code": "kf_event_window_mismatch", "severity": "visual_risk",
                       "shot_id": "4", "frame_id": "kf1", "entity_id": "",
                       "field_path": "keyframes.desc", "message": "新引入的阻塞"})
    if MARKER in str((shots.get("9") or {}).get("prompt") or ""):
        issues.append({"code": "model_audit_X", "severity": "visual_risk",
                       "shot_id": "9", "frame_id": "", "entity_id": "",
                       "field_path": "prompt", "message": "shot 9 与原文矛盾"})
        ws["shot_9"] = ["[model_audit_X] shot 9 与原文矛盾"]
    return {"_has_hard": True, "shots": ws, "issues": issues}


def _base_script():
    s9 = _complete_shot(9)
    s9["prompt"] = "中景，" + MARKER
    return {"shots": [_complete_shot(4), s9], "hook": None,
            "fps": 24, "total_duration": 36}


def _issue9():
    return Issue(code="model_audit_X", severity="visual_risk",
                 message="shot 9 与原文矛盾", shot_id="9", frame_id="",
                 entity_id="", field_path="prompt", evidence=MARKER,
                 repair_targets=[{"shot_id": "9", "field_path": "prompt"}])


def _fixed4():
    s = _complete_shot(4)
    s["ff_desc"] = "甲双目圆睁仰卧船头"
    return s


def _fixed9():
    s = _complete_shot(9)
    s["prompt"] = "中景，已改成无标记的描述"
    return s


# ---------------------------------------------------------------------------
# 1. 合法成功
# ---------------------------------------------------------------------------

def test_legal_repair_is_applied(monkeypatch):
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [
                     {"shot_id": "4", "shot": _fixed4()},
                     {"shot_id": "9", "shot": _fixed9()}],
                     "hook_replacement": None}],
                 audit_issues=[_issue9()], script=_base_script(),
                 warnings_fn=_warnings_fn).run()
    assert h.n == 1
    assert not h.block
    shots = h.shots()
    assert "圆睁" in shots["4"]["ff_desc"]
    assert MARKER not in shots["9"]["prompt"]


# ---------------------------------------------------------------------------
# 2. 首轮拒收 → 下一次请求携带真实反馈
# ---------------------------------------------------------------------------

def test_rejected_patch_feeds_the_next_request(monkeypatch):
    """第一轮：shot_4 修好但引入新 blocking（肇事目标）、shot_9 仍未解决
    ⇒ 回退 shot_4、带反馈重试；第二次请求必须携带真实拒收反馈。"""
    bad4 = _fixed4()
    bad4["ff_desc"] = "甲双目圆睁仰卧船头触发新阻塞"
    still9 = _complete_shot(9)
    still9["prompt"] = "中景，" + MARKER
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [
                                 {"shot_id": "4", "shot": bad4},
                                 {"shot_id": "9", "shot": still9}],
                             "hook_replacement": None},
                            {"shot_replacements": [
                                {"shot_id": "9", "shot": _fixed9()}],
                             "hook_replacement": None}],
                 audit_issues=[_issue9()], script=_base_script(),
                 warnings_fn=_warnings_fn).run(max_rounds=2)
    assert h.n == 2, "拒收后必须带反馈重试一次：%s" % h.log.lines[:12]
    fb = h.feedback(1)
    assert ("上一轮补丁被拒" in fb) or ("已从本轮移除" in fb), fb[-800:]
    shots = h.shots()
    assert MARKER not in shots["9"]["prompt"]
    assert "触发新阻塞" not in shots["4"]["ff_desc"]      # 肇事目标已回退


# ---------------------------------------------------------------------------
# 3. 中间补帧
# ---------------------------------------------------------------------------

def test_intermediate_keyframe_insertion_is_not_rejected(monkeypatch):
    """两帧之间插入中间帧（关键帧字段被显式授权）：事务接受，不按帧数拒收."""
    def _warn_kf(_script, _novel):
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        descs = " ".join(str(k.get("desc") or "")
                         for k in (shots.get("4") or {}).get("keyframes") or [])
        if "中间补帧" not in descs:
            return {"_has_hard": True, "shots": {"shot_4": ["[kf_time_window_alignment] 关键帧间隔过大"]},
                    "issues": [{"code": "kf_time_window_alignment",
                                "severity": "visual_risk", "shot_id": "4",
                                "frame_id": "", "entity_id": "",
                                "field_path": "keyframes",
                                "message": "shot 4 关键帧间隔过大"}]}
        return {"_has_hard": True, "shots": {}, "issues": []}

    issue = Issue(code="model_audit_KF_GAP", severity="visual_risk",
                  message="shot 4 关键帧间隔过大", shot_id="4", frame_id="",
                  entity_id="", field_path="keyframes",
                  repair_targets=[{"shot_id": "4", "field_path": "keyframes"}])
    s4 = _complete_shot(4)
    kfs = s4["keyframes"]
    lay = s4["frame_layouts"]["kfs"]
    s4["keyframes"] = [kfs[0],
                       {"desc": "中间补帧：灯盏脱手", "t_sec": 2.0, "stage": "approach"},
                       kfs[2]]
    s4["frame_layouts"]["kfs"] = [lay[0],
                                  [{"name": "丙", "on_screen": True,
                                    "position": "画右", "pose": "松手",
                                    "gaze_target": "画左", "props": []}],
                                  lay[2]]
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [{"shot_id": "4", "shot": s4}],
                             "hook_replacement": None}],
                 audit_issues=[issue], script=_base_script(),
                 warnings_fn=_warn_kf).run()
    assert h.n == 1
    shots = h.shots()
    assert any("中间补帧" in str(k.get("desc") or "")
               for k in shots["4"]["keyframes"]), shots["4"]["keyframes"]


# ---------------------------------------------------------------------------
# 4. 目标未解决
# ---------------------------------------------------------------------------

def test_unresolved_target_keeps_the_baseline(monkeypatch):
    s4 = _complete_shot(4)                   # 改了，但没有「圆睁」⇒ 未解决
    s4["ff_desc"] = "甲仰卧船头"
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [{"shot_id": "4", "shot": s4}],
                             "hook_replacement": None}],
                 audit_issues=[], script=_base_script(),
                 warnings_fn=_warnings_fn).run()
    assert h.n == 1
    # 未采纳：基线原样返回（不能把「改过字」当已修）
    assert h.shots()["4"]["ff_desc"] == _base_script()["shots"][0]["ff_desc"]
    _codes = [str(r.get("code") or "")
              for r in (h.retry_state.get("feedback_records") or [])]
    assert "no_target_resolved" in _codes, h.retry_state


# ---------------------------------------------------------------------------
# 5. 局部回退：坏目标回退、好目标保留
# ---------------------------------------------------------------------------

def test_partial_rollback_keeps_good_targets(monkeypatch):
    bad9 = _complete_shot(9)
    bad9["prompt"] = "中景，触发新阻塞" + MARKER   # 未解决 + 引入新 blocking
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [
                     {"shot_id": "4", "shot": _fixed4()},
                     {"shot_id": "9", "shot": bad9}],
                     "hook_replacement": None}],
                 audit_issues=[_issue9()], script=_base_script(),
                 warnings_fn=_warnings_fn).run()
    shots = h.shots()
    # 好目标保留
    assert "圆睁" in shots["4"]["ff_desc"]
    # 坏目标回退到基线（标记仍在）
    assert MARKER in shots["9"]["prompt"]


# ---------------------------------------------------------------------------
# 6. 预算停止：终局在发请求之前生效
# ---------------------------------------------------------------------------

def test_terminal_state_stops_before_sending(monkeypatch):
    h = _Harness(monkeypatch,
                 responses=[{"shot_replacements": [], "hook_replacement": None}],
                 audit_issues=[_issue9()], script=_base_script(),
                 warnings_fn=_warnings_fn)
    h.retry_state = {"terminal": True, "stop_reason": "engineering_failure"}
    h.run()
    assert h.n == 0, "终局状态必须先于发送生效：%s" % h.log.lines[:8]
    assert any("终局" in x for x in h.block), h.block
    assert h.out == _base_script()
