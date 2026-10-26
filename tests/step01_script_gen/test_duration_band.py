"""Duration configuration and consumer identity, isolated before module imports.

All baseline inputs are constructed here. External frozen chapter replays use
tools/replay_duration_budget.py and are reported separately from these tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = {"min": 60, "max": 90, "target_min": 70, "target_max": 80}
EXTENDED = {"min": 60, "max": 120, "target_min": 105, "target_max": 115}
KEYS = ("DURATION_MIN_SEC", "DURATION_MAX_SEC", "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC")


def _process(tmp_path, code, band=DEFAULT, overrides=None, *, check=True):
    env = dict(os.environ)
    env.update(dict(zip(KEYS, map(str, band.values()))))
    env.update({"OUTPUT_BASE": str(tmp_path / "output"), "PYTHONIOENCODING": "utf-8",
                "STEP01_LOG_PATH": str(tmp_path / "step01.log"),
                "VOICE_ADAPTATION_ENABLED": "1", "CANDIDATE_EARLY_STOP": "1",
                "CANDIDATE_EARLY_STOP_MAX_SHOTS": "20", "CANDIDATE_EARLY_STOP_SCORE": "95"})
    env.update(overrides or {})
    wrapped = ("import json, os\nfrom pathlib import Path\n"
               "from tools.probe_isolation import block_external_transport\n"
               "with block_external_transport():\n" + textwrap.indent(textwrap.dedent(code), "    "))
    result = subprocess.run([sys.executable, "-c", wrapped], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if not check:
        return result
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(line[7:] for line in reversed(result.stdout.splitlines()) if line.startswith("RESULT:")))


@pytest.mark.parametrize("overrides,variable", [
    ({"DURATION_MAX_SEC": ""}, "DURATION_MAX_SEC"),
    ({"DURATION_MAX_SEC": "nonsense"}, "DURATION_MAX_SEC"),
    ({"DURATION_MAX_SEC": "nan"}, "DURATION_MAX_SEC"),
    ({"DURATION_MAX_SEC": "inf"}, "DURATION_MAX_SEC"),
    ({"DURATION_MAX_SEC": "-1"}, "DURATION_MAX_SEC"),
    ({"DURATION_MIN_SEC": "100"}, "DURATION_MIN_SEC"),
    ({"DURATION_TARGET_MIN_SEC": "81"}, "DURATION_TARGET_MIN_SEC"),
    ({"DURATION_TARGET_MAX_SEC": "91"}, "DURATION_TARGET_MAX_SEC"),
])
def test_invalid_configuration_fails_before_side_effects(tmp_path, overrides, variable):
    result = _process(tmp_path, "import shared.config", overrides=overrides, check=False)
    assert result.returncode != 0
    assert variable in result.stderr
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("band", [DEFAULT, EXTENDED])
def test_runtime_consumers_and_prompt_math(tmp_path, band):
    result = _process(tmp_path, r'''
        import copy
        from shared.config import duration_band
        from step01_script_gen import main as m, prompts as p, outline as o
        from step01_script_gen.constraints import apply_constraints
        from step01_script_gen.constraints.context import ConstraintContext
        from step01_script_gen.constraints.duration_budget import DurationBudgetConstraint
        from step01_script_gen import plan_feasibility as pf
        band = duration_band()
        def script(total):
            return {"shots": [{"index": 101+i, "duration_sec": total/16,
                              "narration_segment": "", "motion_desc": ""} for i in range(16)],
                    "hook": {"duration_sec": 2}}
        observed = []
        for total in (59.98, 59.995, 60, 75, 90, 90.005, 90.02, 113, 120, 120.005, 120.02):
            s = script(total)
            before = copy.deepcopy(s)
            report = apply_constraints(ConstraintContext.from_script(s), only=(DurationBudgetConstraint,))
            assert s == before
            q = m._build_step01_quality_gate(s, "")
            observed.append({"total": total, "hard": bool(report.violations_remaining),
                             "quality_duration": "duration_budget" in q["blocking_categories"]})
            assert {k: v for k, v in s.items() if k != "script_revision"} == before
            assert q["duration_band"] == band
        score = {"total": 100, "climax_complete": True, "quality_checked": True,
                 "integrity_ok": True, "gate_status": "PASS"}
        early = m._candidate_is_early_stop(score, script(113))
        retry = m._retry_degrade_reason(script(75), script(113), "")
        scores = [m._score_script(script(t), "")["total"] for t in (75, 113, 140, 160)]
        # A within-budget script must not ask for model regeneration.
        import shared.config as cfg
        cfg.SHOT_COUNT_MAX = 0
        def forbidden(*a, **k):
            raise AssertionError("unexpected model service")
        m.create_service = forbidden
        if band["max"] == 120:
            original = script(113)
            assert m._regen_over_limit(original, "", max_attempts=2) is original
        text = p._build_total_duration_constraint(0, 0)
        regen = p._build_regen_iron_rules()
        templates = [p.SYSTEM_PROMPT, p._load_prompt("doubao_system"), o.OUTLINE_PROMPT]
        assert all("{{TARGET" not in x and "{{DURATION" not in x for x in templates)
        formatted_outline = o.OUTLINE_PROMPT.format(novel_text="Explicit input", beat_edit_rule="Keep relation")
        print("RESULT:" + json.dumps({"band": band, "budget": pf.budget_band(), "observed": observed,
            "early": early, "retry": retry, "scores": scores, "prompt": text, "regen": regen,
            "templates": templates, "outline": formatted_outline}, ensure_ascii=False))
    ''', band)
    assert result["band"] == band
    assert result["budget"] == [60, band["max"]]
    for row in result["observed"]:
        assert row["hard"] == (row["total"] > band["max"] + .01)
        assert row["quality_duration"] == (row["total"] < 59.99 or row["total"] > band["max"] + .01)
    assert result["early"] == (band == EXTENDED)
    assert (result["retry"] is None) == (band == EXTENDED)
    assert result["scores"][0] - result["scores"][1] == (0 if band == EXTENDED else 3)
    assert result["scores"][1] - result["scores"][2] == (3 if band == EXTENDED else 4)
    assert f"{band['target_min']}-{band['target_max']}s" in result["prompt"]
    if band == EXTENDED:
        for text in [result["prompt"], result["regen"], *result["templates"]]:
            assert "60-90" not in text and "70-80" not in text and "~75s" not in text
        assert "≈ 21-25 镜" in result["prompt"]
        assert "均值 7.3s" in result["templates"][1]
    else:
        assert "≈ 14-17 镜" in result["prompt"]


@pytest.mark.parametrize("band", [DEFAULT, EXTENDED])
def test_gate_manifest_and_audit_identity(tmp_path, band):
    result = _process(tmp_path, r'''
        import copy
        from shared.config import duration_band, duration_band_matches
        from shared.quality_gate import (attach_gate_metadata, compute_script_revision, require_gate,
            require_semantic_gate, build_step01_commit_manifest, verify_step01_commit_manifest,
            write_step01_commit_manifest, QualityGateError)
        from step01_script_gen import main as m
        from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT, RULE_VERSION
        root = Path(os.environ["OUTPUT_BASE"])
        root.mkdir(parents=True, exist_ok=True)
        s = {"shots": [{"index": 9, "duration_sec": 75, "narration_segment": ""}]}
        q = attach_gate_metadata({"status": "PASS", "blocking": False,
            "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT}, s, stage="step01.quality_gate")
        a = {**m._final_audit_metadata(s), "status": "ok", "issues": []}
        def write(name, obj):
            (root/name).write_text(json.dumps(obj), encoding="utf-8")
        write("script_result.json", s)
        write("step01_quality.json", q)
        write("step01_audit.json", a)
        manifest = build_step01_commit_manifest(run_id="fixture", created_at="fixture",
            revision=compute_script_revision(s), rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
            rule_version=RULE_VERSION, audit_scope=a["audit_scope"], chapter_dir=root)
        write_step01_commit_manifest(root, manifest)
        require_semantic_gate(root/"step01_quality.json", root/"step01_audit.json", s)
        verify_step01_commit_manifest(root, s)
        rejected = []
        for name, original, validate in (
            ("step01_quality.json", q, lambda: require_gate(root/"step01_quality.json", s, stage="step02a")),
            ("step01_audit.json", a, lambda: require_semantic_gate(root/"step01_quality.json", root/"step01_audit.json", s)),
            ("step01_commit.json", manifest, lambda: verify_step01_commit_manifest(root, s)),
        ):
            for mutation in ("wrong", "missing", "null", "blank", "wrong_type"):
                bad = copy.deepcopy(original)
                if mutation == "missing":
                    bad.pop("duration_band")
                else:
                    bad["duration_band"] = {"wrong": {"min": 60, "max": 91, "target_min": 70, "target_max": 80},
                                             "null": None, "blank": "", "wrong_type": {"min": "60", "max": 120, "target_min": 105, "target_max": 115}}[mutation]
                write(name, bad)
                try:
                    validate()
                except QualityGateError as exc:
                    assert "duration_band" in str(exc)
                    rejected.append([name, mutation])
                else:
                    assert mutation == "missing" and duration_band()["max"] == 90
                write(name, original)
        assert m._memoizable_final_audit({**a, "duration_band": None}) is None
        m.CHAPTER_DIR = root
        assert m._reusable_final_audit(s) is not None
        bad_a = {**a, "duration_band": {"min": 60, "max": 91, "target_min": 70, "target_max": 80}}
        write("step01_audit.json", bad_a)
        assert m._reusable_final_audit(s) is None
        m._record_audit_diag(a, phase="fixture", round_index=0)
        print("RESULT:" + json.dumps({"band": duration_band(), "manifest_band": manifest["duration_band"],
            "audit_band": a["duration_band"], "rejected": rejected}))
    ''', band, {"AUDIT_TEXT_PROVIDER": "", "AUDIT_TEXT_MODEL": ""})
    assert result["band"] == result["manifest_band"] == result["audit_band"] == band
    assert len(result["rejected"]) == (12 if band == DEFAULT else 15)


@pytest.mark.parametrize("sample", [
    {"event": "旅人推开木门。", "claim": "旅人推开木门", "dialogues": ["旅人说：“请进来。”"], "ending": "来客走进小院。", "location": "小院", "characters": ["旅人", "来客"]},
    {"event": "管理员把两本账册放进柜子。", "claim": "管理员把两本账册放进柜子", "dialogues": ["助手说：“钥匙交给我保管吧。”", "管理员说：“好，请妥善保管。”"], "ending": "助手接过钥匙。", "location": "库房", "characters": ["管理员", "助手", "记录员"]},
])
def test_missing_record_stays_blocking_on_anonymous_inputs(tmp_path, sample):
    (tmp_path/"sample.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    result = _process(tmp_path, r'''
        import copy
        from step01_script_gen import outline as o, plan_feasibility as pf
        from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
        sample = json.loads((Path(os.environ["SAMPLE"])).read_text(encoding="utf-8"))
        event, dialogues, ending = sample["event"], sample["dialogues"], sample["ending"]
        novel = event + "".join(dialogues) + ending
        beats = ["节拍："+event+"｜本镜信息目的："+sample["claim"]+"｜原文依据：“"+event+"”｜载体：画面"]
        beats += ["节拍："+line+"｜本镜信息目的：人物交谈｜原文依据：“"+line+"”｜载体：台词"+str(i)
                  for i, line in enumerate(dialogues, 1)]
        beats += ["节拍："+ending+"｜本镜信息目的：收尾动作｜原文依据：“"+ending+"”｜载体：画面"]
        plan = {"main_events": [event, *dialogues, ending], "climax": event,
                "characters": sample["characters"], "emotional_arc": "平静到期待",
                "key_scenes": [sample["location"]], "character_motives": ["邀请来客"],
                "causal_chain": ["来客到达→开门→邀请→等待"], "end_hook": "等候答复",
                "hook_carrier": "", "hook_spoken": [], "omittable": [],
                "required_facts": [sample["claim"] + "｜原文证据：“" + event + "”｜承载：B1/画面"],
                "shot_beats": beats,
                "dialogue_lines": dialogues, "narration_lines": [], "time_budget": "动作与对白"}
        plan[LEGACY_ACCEPTANCE_FIELD] = LEGACY_ACCEPTANCE_VERSION
        before = copy.deepcopy(plan)
        problems = o.plan_acceptance_problems(plan, novel_text=novel)
        dest = Path(os.environ["OUTPUT_BASE"])
        try:
            pf.check_plan_before_generation(plan, chapter_dir=dest, novel_text=novel)
        except pf.PlanContractInvalidError:
            pass
        else:
            raise AssertionError("missing verification was accepted")
        diag = json.loads((dest/"step01_plan_contract_invalid.json").read_text(encoding="utf-8"))
        assert plan == before
        print("RESULT:"+json.dumps({"codes": sorted({p["code"] for p in problems}),
            "diagnostic_codes": sorted({p["code"] for p in diag["problem_records"]}),
            "band": diag["duration_band"]}))
    ''', EXTENDED, {"SAMPLE": str(tmp_path/"sample.json")})
    assert result["codes"] == result["diagnostic_codes"] == ["verify_record_missing"]
    assert result["band"] == EXTENDED
