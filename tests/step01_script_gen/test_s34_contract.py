# -*- coding: utf-8 -*-
"""S34/S29/S30 contract tests: duration + voice limits, unified entry, shot design."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ("DURATION_MIN_SEC", "DURATION_MAX_SEC", "DURATION_TARGET_MIN_SEC",
        "DURATION_TARGET_MAX_SEC", "STEP01_VOICE_ALLOCATION_MAX_LINES",
        "STEP01_VOICE_ALLOCATION_MAX_HAN")


def _run(tmp_path, code, env_extra=None):
    env = dict(os.environ)
    env.update({"OUTPUT_BASE": str(tmp_path / "output"),
                "STEP01_LOG_PATH": str(tmp_path / "step01.log"),
                "PYTHONIOENCODING": "utf-8"})
    for key, value in (env_extra or {}).items():
        if value == "":
            env.pop(key, None)
        else:
            env[key] = value
    wrapped = ("import json, os\nfrom tools.probe_isolation import block_external_transport\n"
               "with block_external_transport():\n" + textwrap.indent(textwrap.dedent(code), "    "))
    proc = subprocess.run([sys.executable, "-c", wrapped], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = next(x[7:] for x in reversed(proc.stdout.splitlines()) if x.startswith("RESULT:"))
    return json.loads(line)


NEW = {"DURATION_MIN_SEC": "60", "DURATION_MAX_SEC": "120",
       "DURATION_TARGET_MIN_SEC": "85", "DURATION_TARGET_MAX_SEC": "110",
       "STEP01_VOICE_ALLOCATION_MAX_LINES": "12",
       "STEP01_VOICE_ALLOCATION_MAX_HAN": "360"}


def test_new_band_and_voice_limits_reach_all_consumers(tmp_path):
    out = _run(tmp_path, r'''
        from shared.config import duration_band, voice_allocation_limits
        from step01_script_gen import lightweight_voice_repair as vr, prompts, outline
        from step01_script_gen import lightweight_plan as lw, voice_allocation as va
        from step01_script_gen import quality_issues as qi
        lim = voice_allocation_limits()
        vrlim = vr.voice_limits()
        prompt = prompts._build_total_duration_constraint(0, 0)
        bad = (lambda t: [x for x in ("未到 85", "超过 110 就", "超时会被强制截断",
                                      "差 1s 都会被打回") if x in t])(prompt)
        def script(total):
            return {"shots": [{"index": 1, "duration_sec": total, "narration_segment": "",
                               "motion_desc": ""}]}
        issues = {n: [i.code for i in qi._duration_budget_issues(script(n))]
                  for n in (84, 116, 120.5)}
        print("RESULT:" + json.dumps({
            "band": duration_band(), "lim": lim, "vr": vrlim,
            "prompt_bad": bad, "prompt": prompt,
            "issues": issues,
            "calibrate_100": outline._calibrate_time_budget("对白约50s；动作约50s"),
            "calibrate_130": outline._calibrate_time_budget("对白约70s；动作约60s"),
            "va": va.effective_limits({"current_lines": 3}),
            "cache": lw._cache_key("prompt", "m"),
        }, ensure_ascii=False))
    ''', NEW)
    assert out["band"] == {"min": 60.0, "max": 120.0, "target_min": 85.0, "target_max": 110.0}
    assert out["lim"]["max_lines"] == out["vr"]["max_lines"] == out["va"]["max_lines"] == 12
    assert out["lim"]["max_han"] == out["vr"]["max_han"] == out["va"]["max_han"] == 360
    assert out["lim"]["target_sec"] == out["vr"]["target_sec"] == 97.5
    assert out["lim"]["max_sec"] == out["vr"]["max_sec"] == 120.0
    assert out["prompt_bad"] == []
    assert out["issues"] == {"84": [], "116": [], "120.5": ["duration_budget"]}
    assert out["calibrate_100"] == "对白约50s；动作约50s"
    assert "已按目标" in out["calibrate_130"]
    assert out["cache"]


def test_defaults_stay_legacy_until_explicitly_configured(tmp_path):
    out = _run(tmp_path, r'''
        from shared.config import duration_band, voice_allocation_limits
        from step01_script_gen.lightweight_plan import _cache_key
        print("RESULT:" + json.dumps({"band": duration_band(),
            "lim": voice_allocation_limits(), "cache": _cache_key("prompt", "m")},
            ensure_ascii=False))
    ''', {"DURATION_MIN_SEC": "", "DURATION_MAX_SEC": "", "DURATION_TARGET_MIN_SEC": "",
          "DURATION_TARGET_MAX_SEC": "", "STEP01_VOICE_ALLOCATION_MAX_LINES": "",
          "STEP01_VOICE_ALLOCATION_MAX_HAN": ""})
    assert out["band"] == {"min": 60.0, "max": 90.0, "target_min": 70.0, "target_max": 80.0}
    assert out["lim"]["max_lines"] == 10 and out["lim"]["max_han"] == 280
    assert out["lim"]["target_sec"] == 75.0


def test_unified_entry_cache_identity_and_contract(tmp_path, monkeypatch):
    from step01_script_gen import unified_plan as up
    from step01_script_gen.outline import _outline_result_skeleton

    plan = {"lightweight_plan": {"content_verification": "not_run"},
            "plan_verification": {}, "required_facts": [], "dialogue_lines": [],
            "shot_beats": []}

    def fake_generate(*args, **kwargs):
        res = _outline_result_skeleton()
        res.update(executable=True, plan=plan, failure=None)
        return res

    def fake_attach(candidate, novel_text, **kwargs):
        import copy
        out = copy.deepcopy(candidate)
        out["plan_verification"] = {"status": "ok"}
        out["lightweight_plan"]["content_verification"] = "verified"
        return out, [], {"used": 1}

    monkeypatch.setattr(up, "plan_source_setting", lambda source=None: "lightweight")
    monkeypatch.setattr("step01_script_gen.lightweight_plan.generate_lightweight_outline_result",
                        fake_generate)
    monkeypatch.setattr(up, "_attach_verification", fake_attach)
    monkeypatch.setattr("step01_script_gen.outline.plan_acceptance_problems",
                        lambda plan, novel_text="": [])
    cache = tmp_path / "outline.cache.json"
    first = up.generate_unified_outline_result("原文", cache_path=cache)
    assert first["executable"] and first["plan_source"] == "lightweight"
    assert first["unified_contract"] == up.UNIFIED_CONTRACT_VERSION
    assert (tmp_path / up.UNIFIED_CACHE_NAME).is_file()
    second = up.generate_unified_outline_result("原文", cache_path=cache)
    assert second["source"] == "cache" and second["cache"]["unified_hit"] is True


def test_verified_block_does_not_change_content_digest():
    from shared.content_verification import plan_digest_of
    plan = {"required_facts": [], "dialogue_lines": [], "shot_beats": [],
            "lightweight_plan": {"contract_version": "lightweight_outline.v1",
                                 "content_verification": "not_run"}}
    before = plan_digest_of(plan)
    plan["lightweight_plan"]["content_verification"] = "verified"
    assert plan_digest_of(plan) == before


def test_failed_result_cannot_keep_executable_true():
    from step01_script_gen.outline import _fail, _outline_result_skeleton
    res = _outline_result_skeleton()
    res["executable"] = True
    _fail(res, "verification_failed", "x")
    assert res["executable"] is False and res["failure"]["code"] == "verification_failed"


def test_shot_design_result_has_explicit_mapping(monkeypatch):
    from step01_script_gen.orchestrator import regen

    class Issue:
        code = "shot_content_over_cap"
        shot_id = "2"

    script = {"fps": 24, "shots": [{"index": "2", "plan_refs": {"beats": [1], "lines": []},
                                    "duration": 12, "duration_sec": 12,
                                    "narration_segment": "", "motion_desc": "动作"}]}
    children = [{"index": 2.5, "duration": 5, "duration_sec": 5,
                 "narration_segment": "", "motion_desc": "动作A", "plan_refs": {"beats": [1]}},
                {"index": 2.75, "duration": 5, "duration_sec": 5,
                 "narration_segment": "", "motion_desc": "动作B", "plan_refs": {"beats": [1]}}]
    monkeypatch.setattr(regen, "_scoped_split_one", lambda *a, **k: children)
    monkeypatch.setattr(regen, "_content_need", lambda shot: 14.0)
    out = regen.generate_shot_design_result("原文", {}, script=script, issues=[Issue()])
    assert out["ok"] is True and out["changed"] is True
    assert out["replacements"][0]["mapping"]["parent_index"] == "2"
    assert out["replacements"][0]["mapping"]["children"] == [2.5, 2.75]
    assert out["replacements"][0]["mapping"]["plan_refs_inherited"] is True
