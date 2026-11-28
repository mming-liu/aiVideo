"""Ownership survives projections and is enforced before paid work/consumption."""
import copy
import pytest

from shared.narration_voice import narration_role
from step01_script_gen import plan_feasibility as pf
from shared import presentation_contract as pc


def plan(role="甲"):
    return {"main_events": ["甲等待"], "required_facts": [],
            "shot_beats": ["甲等待｜载体：旁白1"], "dialogue_lines": [],
            "narration_lines": [{"text": "我还要等。", "source": "我还要等。",
                                 "fact_refs": [], "voice_role": role}], "hook_spoken": []}


def script(role="甲"):
    return {"chapter_outline": plan(role), "characters": [{"name": "甲"}],
            "shots": [{"index": 7, "duration_sec": 8, "narration_segment": "我还要等。",
                       "plan_refs": {"beats": [1], "lines": []}}]}


@pytest.mark.parametrize("role", [None, "", " ", " 甲", 4, False])
def test_bad_shape_cannot_be_projected(role):
    with pytest.raises(ValueError, match="voice_role"):
        pf.plan_narration_entries(plan(role))
    view, problems = pc.view_from_plan(plan(role))
    assert problems


def test_roundtrip_and_identity():
    p = plan()
    view, problems = pc.view_from_plan(p)
    assert not problems, problems
    assert view["narration_units"][0]["voice_role"] == "甲"
    assert pc.plan_from_view(view)["narration_lines"][0]["voice_role"] == "甲"
    assert pf.voice_units(p, script()["shots"])[0]["voice_role"] == "甲"
    changed = plan("narrator")
    assert pf.plan_identity(p) != pf.plan_identity(changed)
    del changed["narration_lines"][0]["voice_role"]
    assert narration_role(changed["narration_lines"][0], []) == "narrator"
    v, probs = pc.view_from_plan(changed)
    assert not probs
    assert "voice_role" not in pc.plan_from_view(v)["narration_lines"][0]


def test_approval_covers_text_not_voice_source(monkeypatch):
    """批准锁定播出文本：换声源不令批准失效，改文本仍然失效."""
    from shared import voice_adaptation as va
    # 开关的单源是 `shared.config`（只在导入时从 env 读一次）；改 env 不影响已加载值。
    import shared.config as cfg
    monkeypatch.setattr(cfg, "VOICE_ADAPTATION_ENABLED", True)
    approved = plan("甲")
    digest = va.entries_digest([])
    fixed = va.base_plan_digest(approved)
    approved["voice_adaptations"] = {
        "schema": va.SCHEMA, "entries": [], "digest": digest,
        "approval": {"approved": True, "scope": "candidate", "approved_by": "test fixture",
                     "approved_at": "2026-10-27T00:00:00+00:00",
                     "base_plan_digest": fixed, "entries_digest": digest}}
    # 同一份文本换声源（含退回叙述者）：批准仍然有效，且播出投影带着新归属。
    for role in ("narrator", "甲"):
        approved["narration_lines"][0]["voice_role"] = role
        assert va.base_plan_digest(approved) == fixed
        assert va.adaptation_problems(approved) == []
        assert pf.broadcast_narration_entries(approved)[0]["voice_role"] == role
    # 去掉显式声源（旧稿形态）同样不改批准。
    del approved["narration_lines"][0]["voice_role"]
    assert va.base_plan_digest(approved) == fixed
    assert va.adaptation_problems(approved) == []
    # 被批准的**文本**一改，批准立刻失效（保护不能被声源豁免顺手放宽）。
    approved["narration_lines"][0]["text"] = "我还可以再等。"
    assert va.adaptation_problems(approved)[0]["code"] == "adaptation_approval_stale"


def test_role_audio_identity_reaches_merge_and_rejects_wrong_role(monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "step03_tts_audio"))
    from _tts_stub import StubProvider
    from step03_tts_audio import shot_tts as t
    from step04_merge import main as merge
    from shared.audio_identity import read_identity
    from tts.service import register_capabilities
    from tts.models import SpeechUnit
    from tts.synthesis_job import synthesize_unit
    provider = StubProvider()
    register_capabilities(provider.PROVIDER_NAME, provider.capabilities())
    unit = SpeechUnit(unit_id="narration-N1", kind="narration", text="我还要等。",
                      scope={"chapter": 1}, script_revision="test-role-revision", source_index=1,
                      voice_profile_id="role-a", provider=provider.PROVIDER_NAME,
                      model_id="stub-model", model_revision="stub-r1", speaker="stub-role-a",
                      format="wav", sample_rate=24000, mapping_version="v1",
                      voice_role="甲", planned_window=(0, 8))
    result = synthesize_unit(unit, provider=provider, output_path=tmp_path / "n.wav")
    ident = read_identity(result.path)
    assert ident["voice_role"] == "甲"
    s = script()
    tracks = merge._collect_narration_tracks(s, s["shots"], [(0, 8)],
        resolve=lambda kind, index: Path(result.path), identities={1: ident})
    assert len(tracks) == 1
    s["characters"].append({"identifier": "乙"})
    s["chapter_outline"]["narration_lines"][0]["voice_role"] = "乙"
    monkeypatch.setenv("NARRATION_ALLOW_STALE", "1")
    with pytest.raises(RuntimeError, match="voice_role"):
        merge._collect_narration_tracks(s, s["shots"], [(0, 8)],
            resolve=lambda kind, index: Path(result.path), identities={1: ident})


def test_unknown_and_ambiguous_role_rejected():
    for chars in ([], [{"name": "乙"}], [{"name": "甲"}, {"name": "甲"}]):
        with pytest.raises(ValueError, match="unknown or ambiguous"):
            narration_role({"voice_role": "甲"}, chars)
    assert narration_role({"voice_role": "甲"}, [{"identifier": "甲"}]) == "甲"


def test_planning_and_generation_select_same_role(monkeypatch, tmp_path):
    from step03_tts_audio import shot_tts as t
    from types import SimpleNamespace
    caps = SimpleNamespace(provider="fake", model_revision="")
    monkeypatch.setattr(t, "provider_capabilities", lambda *a, **k: caps)
    selected = []
    def resolve(registry, *, role_id, **kw):
        selected.append(role_id)
        return {"model_id": "fake", "speaker": role_id, "voice_profile_id": role_id,
                "mapping_version": "v1"}
    monkeypatch.setattr(t, "resolve_voice", resolve)
    monkeypatch.setattr(t, "_dialogue_speech", lambda *a: (None, ""))
    units = t.build_speech_units(script(), registry=object(), provider_name="fake", scope={"chapter": 1})
    assert len(units) == 1 and units[0].voice_role == "甲"
    assert selected == ["甲"]
    monkeypatch.setattr(t, "resolve_applied", lambda *a: {})
    captured = []
    def artifact(unit, *a, **kw):
        captured.append(unit)
        return SimpleNamespace(path=tmp_path / "test.mp3")
    monkeypatch.setattr(t, "_unit_artifact", artifact)
    monkeypatch.setattr(t, "_cache_hit", lambda *a: False)
    ctx = SimpleNamespace(for_unit=lambda *a: tmp_path / "test.mp3")
    t.gen_narration_tts(script(), tmp_path, provider=object(), voice_registry=object(),
                        candidate_context=ctx, scope={"chapter": 1})
    assert captured[0].voice_role == "甲" and selected == ["甲", "甲"]
    old = copy.deepcopy(units[0]); old.voice_role = ""
    assert t.request_key_for(old) != t.request_key_for(units[0])


def test_unknown_role_before_provider(monkeypatch, tmp_path):
    from step03_tts_audio import shot_tts as t
    monkeypatch.setattr(t, "create_provider", lambda: pytest.fail("provider instantiated"))
    with pytest.raises(ValueError, match="voice_role"):
        t.gen_narration_tts(script("乙"), tmp_path)


def test_finalizer_rejects_unknown_role():
    from step01_script_gen.main import finalize_step01_script
    s = script("乙")
    before = copy.deepcopy(s)
    final, errs = finalize_step01_script(s)
    assert errs and "voice_role" in errs[0] and final == before


def test_short_dagger_props_are_recognized_without_exempting_absence():
    from step01_script_gen.validation import _check_weapon_holder
    s = {"index": 3, "ff_desc": "刀尖贴近衣襟。", "frame_layouts": {
        "ff": [{"name": "乙", "on_screen": True, "props": ["短匕"]}]}}
    assert not _check_weapon_holder([s])["issues"]
    s["frame_layouts"]["ff"][0]["props"] = []
    assert _check_weapon_holder([s])["issues"]


@pytest.mark.parametrize("tail", ["乙仰卧，心口短匕留置，暗红洇痕扩大", "甲仰卧，右手仍举灯盏"])
def test_persistent_state_not_deleted_when_last_motion_omits_it(tail):
    from step01_script_gen.constraints.frame_text_state_cleanup import _clean_text
    text = "甲仰卧，目光转向门外；" + tail
    layout = {"甲": {"on_screen": True, "pose": "仰卧"},
              "乙": {"on_screen": True, "pose": "仰卧"}}
    actual = _clean_text(text, layout, ["甲", "乙"],
                         ff_text="甲举灯，乙仰卧", motion_text="0-2秒：甲举灯；2-4秒：甲目光转向门外",
                         is_lf=True)
    assert tail in actual
    assert _clean_text(actual, layout, ["甲", "乙"], is_lf=True) == actual


@pytest.mark.parametrize("middle,should_warn", [
    ("甲目光呆滞", False), ("甲脸颊血迹被头发遮住", False),
    ("甲脸颊无血迹", True), ("甲脸颊血迹消失", True)])
def test_blood_omission_is_not_explicit_disappearance(middle, should_warn):
    from step01_script_gen.validation import _check_kf_temporal_consistency
    s = {"index": 1, "duration_sec": 6, "ff_desc": "甲脸颊有血迹",
         "lf_desc": "甲脸颊有血迹", "keyframes": [
             {"t_sec": 1, "desc": "甲脸颊有血迹"}, {"t_sec": 3, "desc": middle}]}
    result = _check_kf_temporal_consistency([s])
    assert any("血状态回退" in x for x in result["warnings"]) == should_warn
