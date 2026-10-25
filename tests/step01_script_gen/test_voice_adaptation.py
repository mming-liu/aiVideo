# -*- coding: utf-8 -*-
"""改编层离线贯通测试（匿名样本；零模型调用）."""
from __future__ import annotations

import copy

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from shared import voice_adaptation as va
from step01_script_gen import plan_feasibility as pf

NOVEL = "甲说：走。乙离开。"


def _flag(monkeypatch, on: bool) -> None:
    import shared.config as cfg
    monkeypatch.setattr(cfg, "VOICE_ADAPTATION_ENABLED", bool(on))


def _base_plan():
    return {
        "dialogue_lines": ["甲说：走。"],
        "narration_lines": [{"text": "乙离开。", "source": "乙离开。",
                             "fact_refs": []}],
        "shot_beats": [
            "节拍：甲说走｜本镜信息目的：说｜原文依据：甲说：走。｜载体：台词1",
            "节拍：乙离开｜本镜信息目的：离开｜原文依据：乙离开。｜载体：旁白1",
        ],
        "main_events": ["甲说走", "乙离开"],
        "required_facts": [],
    }


def _entries(plan):
    units = va._base_units(plan)
    d = [u for u in units if u["kind"] == "dialogue"][0]
    n = [u for u in units if u["kind"] == "narration"][0]
    return [
        {"base_uid": d["base_uid"], "kind": "dialogue", "base_text": d["base_text"],
         "source": d["base_text"], "text": "甲说：走吧。", "speaker": "甲",
         "reason": "压缩"},
        {"base_uid": n["base_uid"], "kind": "narration", "base_text": n["base_text"],
         "source": n["source"], "text": "乙转身离开。", "reason": "压缩"},
    ]


def _adapted(monkeypatch, *, entries=None):
    _flag(monkeypatch, True)
    plan = _base_plan()
    entries = entries if entries is not None else _entries(plan)
    block = {
        "schema": va.SCHEMA,
        "approval": {
            "approved": True,
            "base_plan_digest": va.base_plan_digest(plan),
            "entries_digest": va.entries_digest(entries),
            "approved_by": "test", "approved_at": "2026-10-25T19:00:00+08:00",
            "scope": "candidate", "note": "离线样本",
        },
        "entries": entries,
        "digest": va.entries_digest(entries),
    }
    plan["voice_adaptations"] = block
    return plan


def _reapprove(plan, entries):
    plan["voice_adaptations"]["entries"] = entries
    plan["voice_adaptations"]["digest"] = va.entries_digest(entries)
    plan["voice_adaptations"]["approval"]["entries_digest"] = va.entries_digest(entries)


def test_disabled_rejects_block(monkeypatch):
    _flag(monkeypatch, False)
    plan = _adapted(monkeypatch, entries=_entries(_base_plan()))
    # _adapted 已置开；清掉后重新判定
    _flag(monkeypatch, False)
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert codes == ["adaptation_disabled"]


def test_valid_adaptation_broadcasts(monkeypatch):
    plan = _adapted(monkeypatch)
    assert va.adaptation_problems(plan, NOVEL) == []
    voice = pf.plan_voice_entries(plan)
    assert [e["text"] for e in voice] == ["甲说：走吧。", "乙转身离开。"]
    assert va.broadcast_dialogue_lines(plan, NOVEL) == ["甲说：走吧。"]
    assert va.broadcast_narration_entries(plan, NOVEL)[0]["text"] == "乙转身离开。"


def test_entries_changed_without_any_digest(monkeypatch):
    plan = _adapted(monkeypatch)
    plan["voice_adaptations"]["entries"][0]["text"] = "甲说：快走。"
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert codes == ["adaptation_digest_mismatch"]


def test_entries_changed_top_digest_recomputed(monkeypatch):
    plan = _adapted(monkeypatch)
    entries = plan["voice_adaptations"]["entries"]
    entries[0]["text"] = "甲说：快走。"
    plan["voice_adaptations"]["digest"] = va.entries_digest(entries)
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert codes == ["adaptation_entries_unapproved"]


def test_base_change_invalidates_approval(monkeypatch):
    plan = _adapted(monkeypatch)
    plan["dialogue_lines"][0] = "甲说：快走。"
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert codes == ["adaptation_approval_stale"]


def test_base_uid_or_text_mismatch(monkeypatch):
    plan = _adapted(monkeypatch)
    entries = plan["voice_adaptations"]["entries"]
    entries[0]["base_uid"] = "v-000000000000"
    _reapprove(plan, entries)
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert "adaptation_base_uid_missing" in codes


def test_source_not_in_novel(monkeypatch):
    plan = _adapted(monkeypatch)
    entries = plan["voice_adaptations"]["entries"]
    entries[1]["source"] = "原文没有这句。"
    _reapprove(plan, entries)
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert "adaptation_source_not_in_novel" in codes


def test_view_roundtrip_preserves_base_and_approval(monkeypatch):
    plan = _adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    assert [u["text"] for u in view["dialogue_units"]] == ["甲说：走吧。"]
    assert [u["text"] for u in view["narration_units"]] == ["乙转身离开。"]
    back = pc.plan_from_view(view)
    assert back["dialogue_lines"] == ["甲说：走。"]
    assert back["narration_lines"][0]["text"] == "乙离开。"
    assert back["voice_adaptations"]["approval"]["entries_digest"] == \
        plan["voice_adaptations"]["approval"]["entries_digest"]


def test_view_text_mismatch_rejected(monkeypatch):
    plan = _adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    view["dialogue_units"][0]["text"] = "甲说：随便。"
    with pytest.raises(pc.PresentationContractError):
        pc.plan_from_view(view)


def test_base_reader_guard(monkeypatch):
    plan = _adapted(monkeypatch)
    with pytest.raises(va.AdaptationConsumerError):
        va.base_dialogue_lines(plan)
    assert va.base_dialogue_lines(plan, allow_adapted_base=True) == ["甲说：走。"]


def test_integrity_uses_broadcast_and_rejects_base(monkeypatch):
    plan = _adapted(monkeypatch)
    script = {"shots": [
        {"index": 1, "narration_segment": "甲说：走吧。"},
        {"index": 2, "narration_segment": "乙转身离开。"},
    ]}
    ok, broken = pf.check_script_dialogue_integrity(script, plan)
    assert ok, broken
    base_script = {"shots": [{"index": 1, "narration_segment": "甲说：走。"}]}
    ok2, broken2 = pf.check_script_dialogue_integrity(base_script, plan)
    assert not ok2 and broken2


def test_voice_units_use_broadcast(monkeypatch):
    plan = _adapted(monkeypatch)
    pid = pf.plan_identity(plan)
    shots = [
        {"index": 1, "narration_segment": "甲说：走吧。", "plan_id": pid,
         "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 2, "narration_segment": "乙转身离开。", "plan_id": pid,
         "plan_refs": {"beats": [2], "lines": []}},
    ]
    units = pf.voice_units(plan, shots)
    texts = [(u["kind"], u["text"]) for u in units]
    assert texts == [("dialogue", "甲说：走吧。"), ("narration", "乙转身离开。")]
    assert all(u["shot_indexes"] for u in units)


def _two_line_plan(monkeypatch):
    _flag(monkeypatch, True)
    plan = {
        "dialogue_lines": ["甲说：走。", "乙说：好。"],
        "narration_lines": [{"text": "丙离开。", "source": "丙离开。",
                             "fact_refs": []}],
        "shot_beats": [
            "节拍：甲说走｜本镜信息目的：说｜原文依据：甲说：走。｜载体：台词1",
            "节拍：乙说好｜本镜信息目的：应｜原文依据：乙说：好。｜载体：台词2",
            "节拍：丙离开｜本镜信息目的：离开｜原文依据：丙离开。｜载体：旁白1",
        ],
        "main_events": ["甲说走", "乙说好", "丙离开"],
        "required_facts": [],
    }
    units = va._base_units(plan)
    d1, d2 = [u for u in units if u["kind"] == "dialogue"]
    n1 = [u for u in units if u["kind"] == "narration"][0]
    entries = [
        {"base_uid": d1["base_uid"], "kind": "dialogue", "base_text": d1["base_text"],
         "source": d1["base_text"], "text": "甲说：走吧。", "speaker": "甲", "reason": "压缩"},
        {"base_uid": d2["base_uid"], "kind": "dialogue", "base_text": d2["base_text"],
         "source": d2["base_text"], "text": "乙说：好呀。", "speaker": "乙", "reason": "压缩"},
        {"base_uid": n1["base_uid"], "kind": "narration", "base_text": n1["base_text"],
         "source": n1["source"], "text": "丙转身离开。", "reason": "压缩"},
    ]
    plan["voice_adaptations"] = {
        "schema": va.SCHEMA,
        "approval": {"approved": True, "base_plan_digest": va.base_plan_digest(plan),
                     "entries_digest": va.entries_digest(entries),
                     "approved_by": "test", "approved_at": "2026-10-25T19:00:00+08:00",
                     "scope": "candidate"},
        "entries": entries, "digest": va.entries_digest(entries),
    }
    return plan


def test_event_contract_uses_broadcast_and_rejects_wrong_binding(monkeypatch):
    plan = _two_line_plan(monkeypatch)
    pid = pf.plan_identity(plan)
    good = {"plan_id": pid, "shots": [
        {"index": 1, "narration_segment": "甲说：走吧。", "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 2, "narration_segment": "乙说：好呀。", "plan_refs": {"beats": [2], "lines": [2]}},
        {"index": 3, "narration_segment": "丙转身离开。", "plan_refs": {"beats": [3], "lines": []}},
    ]}
    ok, violations, declared = pf.check_event_contract(good, plan)
    assert ok, violations
    wrong = copy.deepcopy(good)
    wrong["shots"][1]["plan_refs"]["lines"] = [1]
    ok2, violations2, _ = pf.check_event_contract(wrong, plan)
    assert not ok2 and violations2


def test_event_contract_rejects_in_shot_order_reversal(monkeypatch):
    plan = _two_line_plan(monkeypatch)
    pid = pf.plan_identity(plan)
    reversed_script = {"plan_id": pid, "shots": [
        {"index": 1, "narration_segment": "乙说：好呀。甲说：走吧。",
         "plan_refs": {"beats": [1, 2], "lines": [1, 2]}},
        {"index": 2, "narration_segment": "丙转身离开。",
         "plan_refs": {"beats": [3], "lines": []}},
    ]}
    ok, violations, _ = pf.check_event_contract(reversed_script, plan)
    assert not ok
    assert any(v.get("code") == "lines_order_violation" for v in violations)


def test_tts_build_speech_units_broadcast(monkeypatch):
    plan = _adapted(monkeypatch)
    pid = pf.plan_identity(plan)
    script = {"chapter_outline": plan, "plan_id": pid,
              "shots": [
                  {"index": 1, "narration_segment": "甲说：走吧。", "duration_sec": 3.0,
                   "plan_id": pid, "plan_refs": {"beats": [1], "lines": [1]}},
                  {"index": 2, "narration_segment": "乙转身离开。", "duration_sec": 3.0,
                   "plan_id": pid, "plan_refs": {"beats": [2], "lines": []}},
              ],
              "characters": [{"identifier": "甲", "name": "甲"}]}
    from step03_tts_audio import shot_tts
    units = shot_tts.build_speech_units(script, scope={}, script_revision="test")
    narr = [u for u in units if u.kind == "narration"]
    assert narr and narr[0].text == "乙转身离开。"
    dialogue = [u for u in units if u.kind == "dialogue"]
    assert dialogue and "走吧" in dialogue[0].text


def test_direct_tts_consumer_cannot_bypass_invalid_approval(monkeypatch):
    plan = _adapted(monkeypatch)
    plan["voice_adaptations"]["entries"][0]["text"] = "甲说：偷换。"
    with pytest.raises(va.AdaptationInvalidError):
        pf.broadcast_dialogue_lines(plan, NOVEL)
    pid = pf.plan_identity(plan)
    script = {"chapter_outline": plan, "plan_id": pid, "shots": [], "characters": []}
    from step03_tts_audio import shot_tts
    with pytest.raises(va.AdaptationInvalidError):
        shot_tts.build_speech_units(script, scope={}, script_revision="test")


def _minimal_plan():
    from step01_script_gen import outline as o  # noqa: F401
    view = {
        "mode": pc.MODE_LEGACY,
        "frozen_facts": [
            {"uid": "f01", "claim": "甲说走", "evidence": "甲说：走。"},
            {"uid": "f02", "claim": "乙离开", "evidence": "乙离开。"},
        ],
        "frozen_events": ["甲说走", "乙离开", "丙看见"],
        "beats": [
            {"uid": "b01", "event_uids": [], "text": "甲说走", "purpose": "说",
             "evidence": "甲说：走。", "source": {},
             "voice_ref": {"kind": "dialogue", "uid": "d01"}},
            {"uid": "b02", "event_uids": [], "text": "乙离开", "purpose": "离开",
             "evidence": "乙离开。", "source": {},
             "voice_ref": {"kind": "narration", "uid": "n01"}},
            {"uid": "b03", "event_uids": [], "text": "丙看见", "purpose": "看见",
             "evidence": "丙看见。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b03"}},
        ],
        "dialogue_units": [{"uid": "d01", "text": "甲说：走。", "source": {}}],
        "narration_units": [{"uid": "n01", "text": "乙离开。",
                             "source": {"text": "乙离开。"}}],
        "fact_bindings": [
            {"fact_uid": "f01", "carriers": [{"kind": "dialogue", "uid": "d01"}]},
            {"fact_uid": "f02", "carriers": [{"kind": "narration", "uid": "n01"}]},
        ],
        "plan_basis": {
            "climax": "甲说走", "time_budget": "约30秒",
            "causal_chain": ["甲说走", "乙离开", "丙看见"],
            "characters": [], "emotional_arc": [], "end_hook": "",
            "key_scenes": [], "character_motives": [], "omittable": [],
            "prop_state_candidates": [],
        },
        "hook_bindings": {"spoken_uids": [], "carriers": []},
    }
    plan = pc.plan_from_view(view)
    plan["planning_acceptance_version"] = "legacy_semantic.v3"
    return plan


def _minimal_adapted(monkeypatch):
    _flag(monkeypatch, True)
    plan = _minimal_plan()
    units = va._base_units(plan)
    d = units[0]
    n = [u for u in units if u["kind"] == "narration"][0]
    entries = [
        {"base_uid": d["base_uid"], "kind": "dialogue", "base_text": d["base_text"],
         "source": d["base_text"], "text": "甲说：走吧。", "speaker": "甲",
         "reason": "压缩"},
        {"base_uid": n["base_uid"], "kind": "narration", "base_text": n["base_text"],
         "source": n["source"], "text": "乙转身离开。", "reason": "压缩"},
    ]
    plan["voice_adaptations"] = {
        "schema": va.SCHEMA,
        "approval": {"approved": True, "base_plan_digest": va.base_plan_digest(plan),
                     "entries_digest": va.entries_digest(entries),
                     "approved_by": "test", "approved_at": "2026-10-25T19:00:00+08:00",
                     "scope": "candidate"},
        "entries": entries, "digest": va.entries_digest(entries),
    }
    return plan, entries


def test_full_acceptance_with_adapted_record(monkeypatch):
    from step01_script_gen import outline as o
    from tests.step01_script_gen import p3_entry_fixtures as fx
    plan, _ = _minimal_adapted(monkeypatch)
    assert [x["code"] for x in o.plan_acceptance_problems(plan, NOVEL)] == \
        ["verify_record_missing"]
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    record = fx.record_for(view, cv.plan_digest_of(plan), novel=NOVEL)
    plan["plan_verification"] = record
    assert o.plan_acceptance_problems(plan, NOVEL) == []


def test_old_record_rejected_after_broadcast_change(monkeypatch):
    from step01_script_gen import outline as o
    from tests.step01_script_gen import p3_entry_fixtures as fx
    plan, entries = _minimal_adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    record = fx.record_for(view, cv.plan_digest_of(plan), novel=NOVEL)
    plan["plan_verification"] = record
    # 新文本 + 新批准；旧核验记录保留。
    entries = copy.deepcopy(entries)
    entries[0]["text"] = "甲说：快走。"
    _reapprove(plan, entries)
    codes = [x["code"] for x in o.plan_acceptance_problems(plan, NOVEL)]
    assert "verify_digest_mismatch" in codes


def test_prompt_contract_uses_broadcast(monkeypatch):
    from step01_script_gen import prompts
    plan = _adapted(monkeypatch)
    text = prompts.render_execution_contract(plan)
    lines_block = text.split("二、")[0]
    assert "甲说：走吧。" in lines_block
    assert "甲说：走。" not in lines_block
    assert "已批准改编" in lines_block
    assert "乙转身离开。" in text


def test_tts_sources_have_no_base_narration_reader():
    import inspect
    from step03_tts_audio import shot_tts
    src = inspect.getsource(shot_tts)
    assert "plan_narration_entries" not in src
    assert "broadcast_narration_entries" in src
    assert "broadcast_narration_entries" in inspect.getsource(
        shot_tts.gen_narration_tts)


def test_gen_narration_tts_completes_with_broadcast(monkeypatch, tmp_path):
    import wave
    from pathlib import Path
    from step03_tts_audio import shot_tts
    plan = _adapted(monkeypatch)
    pid = pf.plan_identity(plan)
    script = {"chapter_outline": plan, "plan_id": pid,
              "shots": [
                  {"index": 1, "narration_segment": "甲说：走吧。", "duration_sec": 3.0,
                   "plan_id": pid, "plan_refs": {"beats": [1], "lines": [1]}},
                  {"index": 2, "narration_segment": "乙转身离开。", "duration_sec": 3.0,
                   "plan_id": pid, "plan_refs": {"beats": [2], "lines": []}},
              ],
              "characters": [{"identifier": "甲", "name": "甲"}]}
    seen = []

    class FakeProvider:
        def capabilities(self):
            return shot_tts.UNKNOWN_CAPABILITIES

    def fake_synth(unit, candidate_context=None, *, provider=None,
                   output_path=None, work_dir=None):
        seen.append(unit.text)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 12000)       # 0.5s 静音，够过 0.2s 下限
        return shot_tts.AudioArtifact(
            unit_id=unit.unit_id, kind=unit.kind, path=out, sha256="",
            bytes=out.stat().st_size, format="wav", sample_rate=24000,
            duration_sec=0.5, request_key="", identity={},
            provider="fake", model_id="fake")

    monkeypatch.setattr(shot_tts, "synthesize_unit", fake_synth)
    outdir = tmp_path / "va_tts_test"
    outdir.mkdir()
    paths = shot_tts.gen_narration_tts(
        script, outdir, provider=FakeProvider(), script_revision="test")
    assert len(paths) == 1 and Path(paths[0]).exists()
    assert seen == ["乙转身离开。"]


def test_acceptance_rejects_disabled_block(monkeypatch):
    from step01_script_gen import outline as o
    plan, _ = _minimal_adapted(monkeypatch)
    _flag(monkeypatch, False)
    codes = [x["code"] for x in o.plan_acceptance_problems(plan, NOVEL)]
    assert "adaptation_disabled" in codes


def test_plan_identity_tracks_adaptation(monkeypatch):
    plan = _adapted(monkeypatch)
    pid1 = pf.plan_identity(plan)
    entries = plan["voice_adaptations"]["entries"]
    entries[0]["text"] = "甲说：快走。"
    _reapprove(plan, entries)
    assert pf.plan_identity(plan) != pid1


def test_verification_prompt_uses_broadcast_echo(monkeypatch):
    from step01_script_gen import content_verify as cverify
    plan = _adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    prompt = cverify.build_verification_prompt(view, novel_text=NOVEL)
    assert "甲说：走吧。" in prompt
    assert "已批准改编" in prompt
    assert "`echo_text` 填这条的**播出文本**" in prompt


def test_view_does_not_mutate_plan(monkeypatch):
    plan = _adapted(monkeypatch)
    before = copy.deepcopy(plan["voice_adaptations"])
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    view["voice_adaptations"]["entries"][0]["text"] = "改视图"
    view["voice_adaptations"]["approval"]["approved_by"] = "改视图"
    view["dialogue_units"][0]["text"] = "改视图"
    assert plan["voice_adaptations"] == before
    assert plan["dialogue_lines"] == ["甲说：走。"]


@pytest.mark.parametrize("field,value,code", [
    ("scope", "all", "adaptation_scope_invalid"),
    ("approved_by", "", "adaptation_approver_missing"),
    ("approved_at", "not-a-time", "adaptation_approved_at_invalid"),
])
def test_invalid_approval_metadata_rejected(monkeypatch, field, value, code):
    plan = _adapted(monkeypatch)
    plan["voice_adaptations"]["approval"][field] = value
    codes = [p["code"] for p in va.adaptation_problems(plan, NOVEL)]
    assert codes == [code]


def test_plain_plan_readers_reuse_legacy_normalization():
    plan = _base_plan()
    plan["dialogue_lines"] += ["   ", ""]
    plan["narration_lines"] += [{"text": "   ", "source": "", "fact_refs": []}, "bad"]
    expected_d = pf.plan_dialogue_lines(plan)
    expected_n = pf.plan_narration_entries(plan)
    assert pf.broadcast_dialogue_lines(plan) == expected_d
    assert pf.broadcast_narration_entries(plan) == expected_n
    entries = pf.plan_voice_entries(plan)
    assert [e["text"] for e in entries if e["kind"] == "dialogue"] == expected_d
    assert [e["text"] for e in entries if e["kind"] == "narration"] == \
        [e["text"] for e in expected_n]


def test_roundtrip_reacceptance(monkeypatch):
    from step01_script_gen import outline as o
    from tests.step01_script_gen import p3_entry_fixtures as fx
    plan, _ = _minimal_adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    back = pc.plan_from_view(view)
    assert va.adaptation_problems(back, NOVEL) == []
    view2, problems2 = pc.view_from_plan(back)
    assert problems2 == []
    record = fx.record_for(view2, cv.plan_digest_of(back), novel=NOVEL)
    back["plan_verification"] = record
    assert o.plan_acceptance_problems(back, NOVEL) == []


def test_production_consumers_have_no_base_reader_calls():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    forbidden = {"plan_dialogue_lines", "plan_narration_entries",
                 "base_dialogue_lines", "base_narration_entries"}
    for rel in ("step01_script_gen/prompts.py",
                "step03_tts_audio/shot_tts.py",
                "shared/audio_identity.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else "")
                assert name not in forbidden, "%s:%s calls %s" % (rel, node.lineno, name)
    # plan_feasibility 内这几个消费者必须走 plan_voice_entries（播出文本）。
    pf_tree = ast.parse((root / "step01_script_gen/plan_feasibility.py")
                        .read_text(encoding="utf-8"))
    wanted = {"voice_units", "shot_voice_entries",
              "check_script_dialogue_integrity", "check_event_contract"}
    for fn in [n for n in pf_tree.body
               if isinstance(n, ast.FunctionDef) and n.name in wanted]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else "")
                assert name not in forbidden, "%s calls %s" % (fn.name, name)


def test_old_verification_digest_changes(monkeypatch):
    plan = _adapted(monkeypatch)
    old = cv.plan_digest_of(plan)
    plan["voice_adaptations"]["entries"][0]["text"] = "甲说：快走。"
    new = cv.plan_digest_of(plan)
    assert old != new


def test_adapted_record_differs_from_base(monkeypatch):
    plan = _adapted(monkeypatch)
    view, problems = pc.view_from_plan(plan)
    assert problems == []
    base_plan = _base_plan()
    assert cv.plan_digest_of(plan) != cv.plan_digest_of(base_plan)
