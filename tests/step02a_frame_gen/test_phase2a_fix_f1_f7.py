# -*- coding: utf-8 -*-
"""Anonymous content-agnostic offline regressions for Phase 2A-Fix (F1-F7)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _block_network():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


def _png(path: Path, color=(80, 120, 180), size=(64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _anon_script(scene: str = "木器作坊", ff_desc: str = "角色甲站在案几前。") -> dict:
    from shared.quality_gate import ensure_script_revision

    script = {
        "title": "匿名章节",
        "book_name": "匿名作品集",
        "book_index": 1,
        "chapter_index": 1,
        "total_duration": 5.0,
        "characters": [{"identifier": "角色甲", "static_features": "圆脸束发", "dynamic_features": "灰布衫"}],
        "chapter_outline": {"narration_lines": []},
        "hook": None,
        "shots": [{
            "index": 1,
            "duration_sec": 5.0,
            "scene": scene,
            "location": scene,
            "main_subject": "角色甲",
            "core_keywords": ["铜秤"],
            "ff_desc": ff_desc,
            "lf_desc": "角色甲起身离开。",
            "motion_desc": "角色甲起身",
            "prompt": "[中景] 角色甲起身",
            "narration_segment": "",
            "clip_transition": "硬切",
            "context_bridge": "",
            "keyframes": [],
            "frame_layouts": {
                "ff": [{"name": "角色甲", "on_screen": True, "props": [], "states": []}],
                "lf": [{"name": "角色甲", "on_screen": True, "props": [], "states": []}],
            },
        }],
    }
    ensure_script_revision(script)
    return script


# ===========================================================================
# F1: QC triple-binding, restart rebuild, missing snapshot must NEVER pass
# ===========================================================================

def test_f1_triple_binding_rebuilds_across_restart_and_rejects_scene_or_rule_or_missing_request(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared.quality_gate import QualityGateError, require_gate
    from step02a_frame_gen import image_prompt_quality as ipq

    monkeypatch.setattr(ipq, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(ipq, "_keyframes_consumed_by_video", lambda: False)

    script = _anon_script(scene="木器作坊")
    shot = script["shots"][0]
    work = tmp_path / "vimax_working"
    ff = _png(work / "shots" / "1" / "first_frame.png")
    ref_p = _png(tmp_path / "ref_char.png", (20, 40, 60))

    iq.write_frame_request_snapshot(
        ff, shot=shot, frame_kind="first_frame",
        prompt="[中景] 角色甲站在案几前，作坊环境。", refs=[str(ref_p)],
        model_id="doubao-seedream-5-0-pro-260628", provider="volcengine", operation_id="op_f1",
    )
    iq.write_sidecar(
        ff, iq.QcResult(has_hallucination=False, issues=[], ok=True, raw_result="{}"),
        frame_meta=iq.build_qc_frame_meta(shot, "first_frame", script["characters"], frame_path=ff),
    )

    # 1. Valid 3-tuple passes & survives "process restart"
    rep = ipq.write_image_product_quality(script, work, media_policy="first_only")
    assert rep["status"] == "PASS"
    assert rep["outputs"]["1"]["qc_states"]["ff"]["request_verified"] is True
    require_gate(
        tmp_path / "step02a_quality.json", script,
        stage="step02a", required_report_type="product_acceptance",
        verify_outputs=True, output_indices={1},
    )

    # 2. Mutate scene ("作坊" -> "雪林") -> contract_digest invalidates old sidecar immediately!
    mutated = _anon_script(scene="雪林")
    rep_scene = ipq.build_image_product_quality(mutated, work, media_policy="first_only")
    assert rep_scene["status"] == "BLOCK"
    assert rep_scene["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_stale"
    with pytest.raises(QualityGateError):
        require_gate(
            tmp_path / "step02a_quality.json", mutated,
            stage="step02a", required_report_type="product_acceptance",
            verify_outputs=True, output_indices={1},
        )

    # 3. Mutate QC rule flag/version -> rule mismatch rejects immediately!
    flipped_sem = "0" if iq.os.getenv("QC_SEMANTICS_CRITICAL", "0") == "1" else "1"
    monkeypatch.setenv("QC_SEMANTICS_CRITICAL", flipped_sem)
    rep_rule = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_rule["status"] == "BLOCK"
    assert rep_rule["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_stale"
    monkeypatch.delenv("QC_SEMANTICS_CRITICAL", raising=False)
    monkeypatch.setattr(iq, "QC_PROMPT_VERSION", "2026-11-26.f1-mutated")
    rep_ver = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_ver["status"] == "BLOCK"
    assert rep_ver["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_stale"
    monkeypatch.undo()
    monkeypatch.setattr(ipq, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(ipq, "_keyframes_consumed_by_video", lambda: False)

    # 4. Delete request snapshot while PASS sidecar stays -> NEVER repackaged as PASS!
    iq.request_snapshot_path(ff).unlink()
    rep_incomplete = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_incomplete["status"] == "BLOCK"
    ff_slot = rep_incomplete["outputs"]["1"]["qc_states"]["ff"]
    assert ff_slot["status"] == "qc_incomplete"
    assert ff_slot["reason"] == "request_snapshot_missing"
    assert ff_slot["request_verified"] is False
    with pytest.raises(QualityGateError):
        require_gate(
            tmp_path / "step02a_quality.json", script,
            stage="step02a", required_report_type="product_acceptance",
            verify_outputs=True, output_indices={1},
        )


def test_f1_qc_prompt_reads_disk_snapshot_without_in_memory_desc_final(tmp_path):
    from shared import image_qc as iq

    shot = _anon_script(scene="木器作坊")["shots"][0]
    ff = _png(tmp_path / "first_frame.png")
    iq.write_frame_request_snapshot(
        ff, shot=shot, frame_kind="first_frame",
        prompt="从磁盘快照读取的最终提示词内容", refs=[],
    )
    assert "ff_desc_final" not in shot
    qc_prompt = iq.build_qc_prompt(shot, "first_frame", [], frame_path=ff)
    assert "从磁盘快照读取的最终提示词内容" in qc_prompt


# ===========================================================================
# F2: frame_text_spec provenance & consumption (prompt + QC + F1 digest)
# ===========================================================================

def test_f2_diegetic_text_spec_shared_by_prompt_qc_and_digest():
    from shared import image_qc as iq
    from shared.prompt_facts import extract_frame_text_spec
    from shared.style_prefix import add_image_strict_constraints

    notice_shot = {
        "index": 1,
        "scene": "木棚前",
        "location": "木棚前",
        "ff_desc": "告示特写，纸上写着“今晚关门”，角落木柱斑驳。",
        "narration_segment": "角色甲说道：“快来看看这告示！”",
        "frame_layouts": {"ff": []},
    }
    spec = extract_frame_text_spec(notice_shot, "first_frame")
    assert spec["mode"] == "explicit_diegetic"
    assert len(spec["allowed_texts"]) == 1
    assert spec["allowed_texts"][0]["carrier"] == "纸"
    assert spec["allowed_texts"][0]["exact_text"] == "今晚关门"
    assert spec["allowed_texts"][0]["source"] == "shot.ff_desc"

    prompt_txt = add_image_strict_constraints(notice_shot["ff_desc"], "首帧", shot=notice_shot, characters=[])
    assert "画面任意区域无任何文字" not in prompt_txt
    assert "今晚关门" in prompt_txt

    qc_prompt = iq.build_qc_prompt(notice_shot, "first_frame", [])
    assert "今晚关门" in qc_prompt
    qs = iq.build_targeted_questions(notice_shot, "first_frame")
    tw_qs = [q for q in qs if q.get("category") == "text_watermark"]
    assert len(tw_qs) == 1
    assert "今晚关门" in tw_qs[0]["q"]
    assert "是否完全没有任何文字" not in tw_qs[0]["q"]

    # Non-quoted UI display text ("屏幕显示断线")
    screen_shot = {
        "index": 2,
        "ff_desc": "控制台特写，屏幕显示断线，红灯闪烁。",
        "narration_segment": "旁白声：“信号中断。”",
    }
    screen_spec = extract_frame_text_spec(screen_shot, "first_frame")
    assert screen_spec["mode"] == "explicit_diegetic"
    assert any(a["carrier"] == "屏幕" and a["exact_text"] == "断线" for a in screen_spec["allowed_texts"])

    # F1 digest invalidates if allowed text changes
    d1 = iq.frame_qc_contract_digest(notice_shot, "first_frame", [])
    edited_notice = dict(notice_shot, ff_desc="告示特写，纸上写着“明日歇业”，角落木柱斑驳。")
    d2 = iq.frame_qc_contract_digest(edited_notice, "first_frame", [])
    assert d1 != d2


def test_f2_dialogue_quotes_never_become_screen_text_and_unknown_uses_conservative_guard():
    from shared import image_qc as iq
    from shared.prompt_facts import extract_frame_text_spec
    from shared.style_prefix import add_image_strict_constraints

    dialogue_only = {
        "index": 3,
        "ff_desc": "角色甲看着对面，口中说道：“今晚关门，明日再来。”",
        "narration_segment": "角色乙道：“屏幕显示断线。”",
    }
    spec = extract_frame_text_spec(dialogue_only, "first_frame")
    assert spec["allowed_texts"] == []
    assert spec["mode"] == "conservative"

    prompt_txt = add_image_strict_constraints(dialogue_only["ff_desc"], "首帧", shot=dialogue_only, characters=[])
    assert "画面任意区域无任何文字" not in prompt_txt
    assert "禁止在天空、水面、背景或角落添加任何额外地名标题" in prompt_txt
    qs = iq.build_targeted_questions(dialogue_only, "first_frame")
    tw_q = next(q for q in qs if q.get("category") == "text_watermark")
    assert "是否完全没有任何文字" not in tw_q["q"]


def test_f2_same_notice_with_extra_sky_title_is_rejected_critical(tmp_path, monkeypatch):
    from shared import image_qc as iq

    shot = {"index": 1, "ff_desc": "告示特写，纸上写着“今晚关门”。", "frame_layouts": {"ff": []}}
    ff = _png(tmp_path / "notice_with_sky_title.png")
    monkeypatch.setattr(iq, "QC_ENABLED", True)
    monkeypatch.setattr(iq, "_detect_entities", lambda _p: [])
    monkeypatch.setattr(iq, "_semantic_qc", lambda *a, **k: iq.parse_verdict(json.dumps({
        "has_hallucination": True,
        "has_unwanted_text": True,
        "issues": [{"category": "text_watermark", "description": "天空出现额外大字标题", "severity": "critical"}],
    }, ensure_ascii=False)))
    monkeypatch.setattr(iq, "_ask_yes_no", lambda p, qs: {q["q"]: "no" if q.get("category") == "text_watermark" else "yes" for q in qs})
    res = iq.maybe_qc_sync(ff, shot, "first_frame", [], True)
    assert res.ok is True
    assert res.has_hallucination is True
    assert any(i.category == "text_watermark" and i.severity == "critical" for i in res.issues)


# ===========================================================================
# F3: Characterless style anchor validation + generate_base_portrait style ref
# ===========================================================================

def test_f3_style_bank_rejects_character_anchor_and_base_portrait_accepts_style_ref(tmp_path):
    from shared.character_bank import StyleBank
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator
    from vimax.interfaces import CharacterInScene, ImageOutput

    bank = StyleBank(bank_dir=str(tmp_path / "style_bank"))
    char_frame = _png(tmp_path / "shot2_ff.png", (10, 20, 30))
    (tmp_path / "shot2_ff_hallucination.json").write_text(
        json.dumps({"person_count": 2, "status": "qc_passed"}), encoding="utf-8"
    )
    assert bank.lock(str(char_frame), 1, allow_characters=False) is False

    empty_anchor = _png(tmp_path / "shot1_empty.png", (40, 80, 120))
    assert bank.lock(str(empty_anchor), 1, anchor_kind="empty_scene", expected_persons=0) is True
    locked_path = bank.get_anchor_path()
    assert locked_path and Path(locked_path).is_file()

    captured = {}

    class _StubImgGen:
        async def generate_single_image(self, **kwargs):
            captured.update(kwargs)
            return ImageOutput(fmt="pil", ext="png", data=Image.new("RGB", (64, 64), (255, 255, 255)))

    gen = CharacterPortraitsGenerator(image_generator=_StubImgGen())
    char = CharacterInScene(idx=0, identifier_in_scene="角色甲", is_visible=True, static_features="圆脸", dynamic_features="青衫")
    asyncio.run(gen.generate_base_portrait(char, style_reference_path=locked_path))
    assert captured.get("reference_image_paths") == [locked_path]
    assert "DO NOT copy its background" in captured.get("prompt", "")
    assert "white background" in captured.get("prompt", "").lower()


# ===========================================================================
# F4: Layered prop relations (present/worn vs held vs active_use)
# ===========================================================================

def test_f4_prop_layers_allow_unsheathed_and_sheathed_while_blocking_same_layer_conflict():
    from shared.prompt_facts import project_frame_facts

    chars = [{"identifier": "角色甲"}]

    # 1. Valid: props=["长剑"] + states=["未拔出长剑"] (present vs !active_use)
    shot_undrawn = {
        "index": 1, "ff_desc": "角色甲静立。",
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "props": ["长剑"], "states": ["未拔出长剑"]}]},
    }
    p1 = project_frame_facts(shot_undrawn, "first_frame", chars)
    assert p1["spec_status"] == "valid"
    assert p1["spec_issues"] == []

    # 2. Valid: worn prop + empty hands ("腰间佩剑" + "双手空着")
    shot_worn = {
        "index": 2, "ff_desc": "角色甲抱拳致意。",
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "props": ["长剑"], "states": ["腰间佩剑", "双手空着"]}]},
    }
    p2 = project_frame_facts(shot_worn, "first_frame", chars)
    assert p2["spec_status"] == "valid"
    assert p2["spec_issues"] == []

    # 3. Deterministic block: same entity, same prop, same moment, same relation layer ("held")
    shot_conflict = {
        "index": 3, "ff_desc": "角色甲持剑。",
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "props": ["长剑"], "states": ["手持长剑", "手中无剑"]}]},
    }
    p3 = project_frame_facts(shot_conflict, "first_frame", chars)
    assert p3["spec_status"] == "invalid"
    assert any(i["code"] == "contradictory_entity_prop" and i.get("layer") == "held" for i in p3["spec_issues"])

    # 4. Unresolvable natural-language negation without explicit held conflict -> unverified_relation advisory, not blocked!
    shot_ambig = {
        "index": 4, "ff_desc": "角色甲站在门前。",
        "frame_layouts": {"ff": [{"name": "角色甲", "on_screen": True, "props": ["长剑"], "pose": "双手空着", "states": []}]},
    }
    p4 = project_frame_facts(shot_ambig, "first_frame", chars)
    assert p4["spec_status"] == "valid"
    assert any(a["code"] == "unverified_relation" for a in p4["spec_advisories"])


# ===========================================================================
# F5: Side-effect-free unified portrait resolver (gate & collector identical)
# ===========================================================================

def test_f5_unified_portrait_resolver_parity_and_rejections(tmp_path, monkeypatch):
    from shared import paths as sp
    from shared import vimax_bridge as vb
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    work_dir = tmp_path / "work"
    bank_dir = tmp_path / "global" / "characters_bank"
    sbank_dir = tmp_path / "global" / "style_bank"
    work_dir.mkdir(parents=True)
    bank_dir.mkdir(parents=True)
    _png(sbank_dir / "style_anchor.png", (10, 10, 10))
    monkeypatch.setattr(sp, "vimax_working_dir", lambda *a, **k: work_dir)
    monkeypatch.setattr(sp, "characters_bank_dir", lambda *a, **k: bank_dir)
    monkeypatch.setattr(sp, "style_bank_dir", lambda *a, **k: sbank_dir)

    chars = [{"identifier": "角色丙"}]
    shot = {"index": 1, "ff_desc": "角色丙站在桥头。", "frame_layouts": {"ff": [{"name": "角色丙", "on_screen": True}]}}
    script = {"characters": chars, "shots": [shot]}
    reg_file = work_dir / "character_portraits_registry.json"

    # Case 1: registry ONLY has base.path -> BOTH gate and collector succeed!
    base_p = _png(bank_dir / "角色丙" / "base.png")
    reg_file.write_text(json.dumps({"角色丙": {"base": {"path": str(base_p)}}}, ensure_ascii=False), encoding="utf-8")
    gate_base = check_pre_shot_asset_gate(script, only_indices={1}, frames={"ff"})
    col_base = vb._collect_portrait_refs_for_shot(shot, chars, roster={"角色丙"}, return_details=True, apply_head_crop=False)
    assert gate_base["status"] == "ok"
    assert col_base["refs"] == [str(base_p)]
    assert not (work_dir / "portrait_crops").exists(), "Read-only resolution must not create crop dirs"

    # Case 2: corrupt image -> BOTH reject with portrait_unreadable!
    bad_p = bank_dir / "角色丙" / "base.png"
    bad_p.write_bytes(b"corrupt-not-png")
    gate_bad = check_pre_shot_asset_gate(script, only_indices={1}, frames={"ff"})
    col_bad = vb._collect_portrait_refs_for_shot(shot, chars, roster={"角色丙"}, return_details=True, apply_head_crop=False)
    assert gate_bad["status"] == "blocked"
    assert gate_bad["missing_portraits"][0]["reason"] == "portrait_unreadable"
    assert col_bad["refs"] == []
    assert col_bad["missing"][0]["reason"] == "portrait_unreadable"

    # Restore valid image, test Case 3: wrong identity metadata -> BOTH reject with portrait_identity_mismatch!
    _png(bad_p)
    reg_file.write_text(json.dumps({
        "角色丙": {"front": {"path": str(bad_p), "character_id": "其他角色"}},
    }, ensure_ascii=False), encoding="utf-8")
    gate_id = check_pre_shot_asset_gate(script, only_indices={1}, frames={"ff"})
    col_id = vb._collect_portrait_refs_for_shot(shot, chars, roster={"角色丙"}, return_details=True, apply_head_crop=False)
    assert gate_id["status"] == "blocked" and gate_id["missing_portraits"][0]["reason"] == "portrait_identity_mismatch"
    assert col_id["refs"] == [] and col_id["missing"][0]["reason"] == "portrait_identity_mismatch"

    # Case 4: unapproved asset -> BOTH reject with portrait_unapproved!
    reg_file.write_text(json.dumps({
        "角色丙": {"front": {"path": str(bad_p), "approved": False}},
    }, ensure_ascii=False), encoding="utf-8")
    gate_unapp = check_pre_shot_asset_gate(script, only_indices={1}, frames={"ff"})
    col_unapp = vb._collect_portrait_refs_for_shot(shot, chars, roster={"角色丙"}, return_details=True, apply_head_crop=False)
    assert gate_unapp["status"] == "blocked" and gate_unapp["missing_portraits"][0]["reason"] == "portrait_unapproved"
    assert col_unapp["refs"] == [] and col_unapp["missing"][0]["reason"] == "portrait_unapproved"

    # Case 5: face-only rejected for full-body, accepted for closeup
    face_p = _png(bank_dir / "角色丙" / "face.png")
    reg_file.write_text(json.dumps({"角色丙": {"face": {"path": str(face_p)}}}, ensure_ascii=False), encoding="utf-8")
    gate_face = check_pre_shot_asset_gate(script, only_indices={1}, frames={"ff"})
    col_face = vb._collect_portrait_refs_for_shot(shot, chars, roster={"角色丙"}, return_details=True, apply_head_crop=False)
    assert gate_face["status"] == "blocked" and gate_face["missing_portraits"][0]["reason"] == "portrait_role_insufficient"
    assert col_face["refs"] == [] and col_face["missing"][0]["reason"] == "portrait_role_insufficient"


# ===========================================================================
# F6: Location preservation & standalone frame-level head/face guard
# ===========================================================================

def test_f6_location_preservation_and_standalone_head_guard():
    from shared.style_prefix import visual_location_hint
    from shared.vimax_bridge import _dedupe_char_anchor_guard, _inject_frame_spec

    for raw in ("地下城", "太空城", "古城城墙", "星港穹顶"):
        hint = visual_location_hint(raw)
        assert hint == raw
        assert "古风实景环境" not in hint

    assert visual_location_hint("甲溪镇水湾") == "水湾"
    assert visual_location_hint("陈府别庄") == "别庄"
    assert visual_location_hint("甲城", {"location_proper_name": "甲城", "scene": "雪原营帐"}) == "雪原营帐"

    # Head/face guard emitted as independent frame-level sentence outside last character
    chars = [
        {"identifier": "角色甲", "static_features": "猫系眼", "dynamic_features": "青衣"},
        {"identifier": "角色乙", "static_features": "鹿系眉眼", "dynamic_features": "白衣"},
    ]
    shot = {
        "ff_desc": "两人对坐。",
        "frame_layouts": {
            "ff": [
                {"name": "角色甲", "on_screen": True, "states": []},
                {"name": "角色乙", "on_screen": True, "states": []},
            ],
        },
    }
    cleaned, guard = _dedupe_char_anchor_guard([
        "a（标准人类头面部：人类圆形耳廓，头部除发饰外无其他附加物）",
        "b（标准人类头面部：人类圆形耳廓，头部除发饰外无其他附加物）",
    ])
    assert cleaned == ["a", "b"]
    assert "人类圆形耳廓" in guard and guard.endswith("。")
    out = _inject_frame_spec(shot["ff_desc"], shot, chars, "first_frame")
    assert out.count("人类圆形耳廓") == 1
    assert "。全员（标准人类头面部：" in out


# ===========================================================================
# F7: Independent HTTP attempt tracking & frame terminal metrics reduction
# ===========================================================================

def test_f7_independent_http_events_without_budget_and_fallback_model_resolution(tmp_path, monkeypatch):
    from _image_gen.models import ImageGenerationRequest
    from _image_gen.volcengine_provider import VolcengineImageService
    from shared import post_budget, request_events

    events_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_TOTAL", raising=False)
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_FILE", raising=False)
    post_budget.reset("image")

    svc = VolcengineImageService.__new__(VolcengineImageService)
    svc._model_queue = ["doubao-seedream-5-0-pro-260628", "doubao-seedream-5-0-lite"]
    svc._base_url = "https://example.invalid/api/v3"
    svc._key_scheduler = type("KS", (), {"acquire": lambda self, timeout=180: None})()

    class _FakeResp:
        def __init__(self, code, data):
            self.status_code = code
            self._data = data
            self.text = json.dumps(data, ensure_ascii=False)

        def json(self):
            return self._data

    sent_payloads = []

    def fake_post(url, json=None, headers=None, timeout=180):
        sent_payloads.append(json)
        if len(sent_payloads) == 1:
            return _FakeResp(403, {"error": {"message": "fallback"}})
        return _FakeResp(200, {"data": [{"url": "https://example.invalid/ok.png"}]})

    monkeypatch.setattr("_image_gen.volcengine_provider.requests.post", fake_post)
    monkeypatch.setattr("_image_gen.volcengine_provider.VOLCENGINE_API_KEY", "k")
    monkeypatch.setattr(svc, "_download", lambda r, u, i, **kw: type(
        "R", (), {"success": True, "save_path": str(tmp_path / "ok.png"), "error": None, "soft_skip": False, "request_meta": None}
    )())

    req = ImageGenerationRequest(prompt="test", shot_index=1, operation_id="op_fallback_1")
    out = svc.generate(req)
    assert out.success is True
    assert out.request_meta["operation_id"] == "op_fallback_1"
    assert "lite" in out.request_meta["model_id"]
    sent = [e for e in request_events.read_events(events_file) if e.get("event") == "request_sent"]
    assert len(sent) == 2
    assert all(e["operation_id"] == "op_fallback_1" and e["budget_enabled"] is False for e in sent)
    assert sent[0]["model_id"] != sent[1]["model_id"]


def test_f7_metrics_report_reduces_frame_terminal_state_and_preserves_unknowns():
    from tools.step02a_metrics_report import summarize_step02a_events

    events = [
        # 2 real HTTP attempts for operation op1 producing 1 final usable frame after initial rework
        {"event": "request_sent", "request_id": "r1", "operation_id": "op1", "model_id": "m1"},
        {"event": "request_sent", "request_id": "r2", "operation_id": "op1", "model_id": "m1"},
        {
            "event": "decision_ledger", "seq": 1, "work_id": "w1", "chapter_scope": "part_01/chapter_001",
            "script_revision": "revA", "shot_index": 1, "frame_role": "first_frame",
            "model_id": "m1", "ref_strategy": "multi_ref_contract", "shot_complexity": "two_person",
            "prompt_template_version": "tpl_v2", "request_digest": "hash_prompt_1",
            "operation_id": "op1", "attempt_index": 1, "image_sha256": "img_v1",
            "action_type": "generate", "defect_class": "stochastic_miss", "qc_verdict": "qc_rejected",
            "human_usability": "rework",
        },
        {
            "event": "decision_ledger", "seq": 2, "work_id": "w1", "chapter_scope": "part_01/chapter_001",
            "script_revision": "revA", "shot_index": 1, "frame_role": "first_frame",
            "model_id": "m1", "ref_strategy": "multi_ref_contract", "shot_complexity": "two_person",
            "prompt_template_version": "tpl_v2", "request_digest": "hash_prompt_2",
            "operation_id": "op1", "attempt_index": 2, "image_sha256": "img_v2",
            "action_type": "generate", "defect_class": "none", "qc_verdict": "qc_passed",
            "human_usability": "usable",
        },
        # Second frame without HTTP attempts & without review -> attempts_total unknown, human usability unknown
        {
            "event": "decision_ledger", "seq": 3, "work_id": "w1", "chapter_scope": "part_01/chapter_002",
            "script_revision": "revB", "shot_index": 2, "frame_role": "first_frame",
            "model_id": "m2", "ref_strategy": "text_only", "shot_complexity": "single_person",
            "prompt_template_version": "tpl_v2", "request_digest": "hash_prompt_3",
            "operation_id": "op_no_http", "attempt_index": 1, "image_sha256": "img_v3",
            "action_type": "generate", "defect_class": "none", "qc_verdict": "qc_passed",
        },
    ]
    rep = summarize_step02a_events(events)
    b1 = rep["buckets"]["m1|multi_ref_contract|two_person|tpl_v2"]
    assert b1["unique_frames"] == 1
    assert b1["attempts_total"] == 2
    assert b1["requests_per_usable_frame"] == 2.0
    assert b1["ever_reworked_rate"] == 1.0  # bounded <= 1.0, never 200%!
    assert b1["final_rework_rate"] == 0.0
    assert b1["unresolved_frames"] == 0  # failed attempt + final pass = resolved!
    assert b1["distinct_request_digests"] == 2  # aggregated into 1 template bucket!
    assert b1["cost_status"] == "unknown" and b1["estimated_cost_cny"] is None
    assert b1["non_target_damage_count"] == "unknown"

    b2 = rep["buckets"]["m2|text_only|single_person|tpl_v2"]
    assert b2["attempts_total"] == "unknown"
    assert b2["requests_per_usable_frame"] is None
    assert b2["human_usability"].get("unknown") == 1
    assert b1["terminal_selection_rule"] == "last_by_(seq,attempt_index,record_order)"
    assert b1["denominators"] == {
        "requests_per_usable_frame": 1,
        "ever_reworked_rate": 1,
        "final_rework_rate": 1,
        "unresolved_rate": 1,
        "non_target_damage_rate": 0,
    }


def test_f1_ref_changed_missing_corrupt_and_snapshot_write_failure_block_without_orphans(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import paths as sp
    from shared import vimax_bridge as vb
    from shared.quality_gate import QualityGateError, require_gate
    from step02a_frame_gen import image_prompt_quality as ipq

    monkeypatch.setattr(ipq, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(ipq, "_keyframes_consumed_by_video", lambda: False)

    script = _anon_script(scene="木器作坊")
    shot = script["shots"][0]
    work = tmp_path / "vimax_working"
    ff = _png(work / "shots" / "1" / "first_frame.png")
    ref_p = _png(tmp_path / "ref_char.png", (20, 40, 60))

    iq.write_frame_request_snapshot(
        ff, shot=shot, frame_kind="first_frame",
        prompt="[中景] 角色甲站在案几前。", refs=[str(ref_p)],
        model_id="doubao-seedream-5-0-pro-260628", provider="volcengine", operation_id="op_ref_1",
    )
    iq.write_sidecar(
        ff, iq.QcResult(has_hallucination=False, issues=[], ok=True, raw_result="{}"),
        frame_meta=iq.build_qc_frame_meta(shot, "first_frame", script["characters"], frame_path=ff),
    )

    # 1. Mutate reference image on disk -> request_ref_changed -> qc_stale -> BLOCK
    _png(ref_p, (210, 10, 10))
    rep_ref_changed = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_ref_changed["status"] == "BLOCK"
    assert rep_ref_changed["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_stale"
    assert rep_ref_changed["outputs"]["1"]["qc_states"]["ff"]["reason"] == "request_ref_changed"
    with pytest.raises(QualityGateError):
        require_gate(
            tmp_path / "step02a_quality.json", script,
            stage="step02a", required_report_type="product_acceptance",
            verify_outputs=True, output_indices={1},
        )

    # 2. Delete reference image -> request_ref_missing -> qc_incomplete -> BLOCK
    ref_p.unlink()
    rep_ref_missing = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_ref_missing["status"] == "BLOCK"
    assert rep_ref_missing["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_incomplete"
    assert rep_ref_missing["outputs"]["1"]["qc_states"]["ff"]["reason"] == "request_ref_missing"

    # 3. Corrupt snapshot JSON -> request_snapshot_corrupt -> qc_incomplete -> BLOCK
    iq.request_snapshot_path(ff).write_text("{corrupt", encoding="utf-8")
    rep_corrupt = ipq.build_image_product_quality(script, work, media_policy="first_only")
    assert rep_corrupt["status"] == "BLOCK"
    assert rep_corrupt["outputs"]["1"]["qc_states"]["ff"]["status"] == "qc_incomplete"
    assert rep_corrupt["outputs"]["1"]["qc_states"]["ff"]["reason"] == "request_snapshot_corrupt"

    # 4. Snapshot atomic write failure cleans .tmp & generation aborts without orphan QC sidecar
    orig_replace = iq.os.replace
    monkeypatch.setattr(iq.os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        iq.write_frame_request_snapshot(
            ff, shot=shot, frame_kind="first_frame", prompt="x", refs=[],
        )
    assert not iq.request_snapshot_path(ff).with_name(iq.request_snapshot_path(ff).name + ".tmp").exists()
    monkeypatch.setattr(iq.os, "replace", orig_replace)

    iso_work = tmp_path / "iso_snap_fail"
    iso_shots = iso_work / "shots"
    iso_shots.mkdir(parents=True)
    events_file = tmp_path / "snap_events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setattr(sp, "vimax_working_dir", lambda *a, **k: iso_work)
    qc_called = []
    monkeypatch.setattr(vb, "_validate_frame", lambda *_a, **_k: True)
    monkeypatch.setattr(vb, "_maybe_qc_frame", lambda *a, **k: qc_called.append(True))
    monkeypatch.setattr(
        vb, "_persist_frame_request_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError("snapshot write failed")),
    )

    class _GenAdapter:
        last_request_meta = None

        async def generate_single_image(self, **kwargs):
            return type("Out", (), {"data": Image.new("RGB", (64, 64), (10, 20, 30)), "request_meta": None})()

    shot2 = _anon_script(scene="木器作坊")["shots"][0]
    res = asyncio.run(vb._gen_first_frame_async(
        _GenAdapter(), iso_shots, shot2, True, characters=script["characters"], empty_scene=False,
    ))
    assert res is None
    assert qc_called == []
    assert not (iso_shots / "1" / "first_frame_hallucination.json").exists()
    assert not (iso_shots / "1" / "frame_hash.txt").exists()
    from shared import request_events as _re
    ev_rows = _re.read_events(events_file)
    assert any(
        e.get("event") == "decision_ledger" and e.get("qc_verdict") == "snapshot_write_failed"
        for e in ev_rows
    )


def test_f2_unknown_exact_text_carrier_uses_conservative_and_affects_digest():
    from shared import image_qc as iq
    from shared.prompt_facts import extract_frame_text_spec, render_frame_text_constraint

    shot_unknown = {
        "index": 1,
        "ff_desc": "角色甲走过巷口木牌。",
        "frame_text_spec": {"ff": {"allowed_texts": [{"carrier": "木牌", "position": "巷口", "exact_text": None}]}},
    }
    spec = extract_frame_text_spec(shot_unknown, "first_frame")
    assert spec["mode"] == "conservative"
    assert spec["allowed_texts"] == []
    assert any(
        u["carrier"] == "木牌" and u["exact_text"] is None and u["text_known"] is False
        for u in spec["unconstrained_carriers"]
    )
    rendered = render_frame_text_constraint(spec)
    assert "禁止在天空、水面、背景或角落添加任何额外地名标题" in rendered
    d1 = iq.frame_qc_contract_digest(shot_unknown, "first_frame", [])
    shot_other = dict(shot_unknown, ff_desc="角色甲走过巷口，毫无标牌。", frame_text_spec={})
    d2 = iq.frame_qc_contract_digest(shot_other, "first_frame", [])
    assert d1 != d2


def test_f3_isolated_asset_bank_style_anchor_workflow_leaves_formal_untouched(tmp_path, monkeypatch):
    from shared import paths as sp
    from shared import vimax_bridge as vb
    from shared.character_bank import CharacterBank, StyleBank
    from vimax.interfaces import ImageOutput

    iso_bank = tmp_path / "iso_global" / "characters_bank"
    iso_sbank = tmp_path / "iso_global" / "style_bank"
    iso_work = tmp_path / "iso_work"
    iso_bank.mkdir(parents=True)
    iso_sbank.mkdir(parents=True)
    iso_work.mkdir(parents=True)
    monkeypatch.setattr(sp, "characters_bank_dir", lambda *a, **k: iso_bank)
    monkeypatch.setattr(sp, "style_bank_dir", lambda *a, **k: iso_sbank)
    monkeypatch.setattr(sp, "vimax_working_dir", lambda *a, **k: iso_work)

    anchor_src = _png(tmp_path / "empty_style.png", (30, 60, 90))
    sbank = StyleBank(bank_dir=str(iso_sbank))
    assert sbank.lock(str(anchor_src), 1, anchor_kind="empty_scene", expected_persons=0) is True
    assert vb._resolve_style_anchor_path() == str(iso_sbank / "style_anchor.png")

    captured = {}

    class _StubPortGen:
        def __init__(self, image_generator=None):
            pass

        async def generate_base_portrait(self, char, **kwargs):
            captured.update(kwargs)
            return ImageOutput(fmt="pil", ext="png", data=Image.new("RGB", (64, 64), (255, 255, 255)))

        async def generate_side_portrait(self, char, **kwargs):
            return ImageOutput(fmt="pil", ext="png", data=Image.new("RGB", (64, 64), (255, 255, 255)))

        async def generate_back_portrait(self, char, **kwargs):
            return ImageOutput(fmt="pil", ext="png", data=Image.new("RGB", (64, 64), (255, 255, 255)))

    import shared.vimax_adapters as va
    import vimax.agents.character_portraits_generator as cpg
    monkeypatch.setattr(va, "ImageGeneratorAiVideoAdapter", lambda **kw: object())
    monkeypatch.setattr(cpg, "CharacterPortraitsGenerator", _StubPortGen)
    asyncio.run(vb._generate_missing_portraits(
        [{"identifier": "角色丁", "static_features": "短发", "dynamic_features": "布衣"}],
        CharacterBank(bank_dir=str(iso_bank)),
        None,
        chapter_idx=1,
    ))
    assert captured.get("style_reference_path") == str(iso_sbank / "style_anchor.png")


def test_f4_unverified_relation_propagates_non_blocking_to_quality_and_visual_plan(tmp_path, monkeypatch):
    from step02a_frame_gen import execution_plan as ep
    from step02a_frame_gen import image_prompt_quality as ipq
    from shared import paths as sp

    work = tmp_path / "work"
    bank = tmp_path / "bank"
    sbank = tmp_path / "sbank"
    work.mkdir()
    bank.mkdir()
    sbank.mkdir()
    _png(sbank / "style_anchor.png")
    p_ref = _png(bank / "角色甲" / "front.png")
    (work / "character_portraits_registry.json").write_text(
        json.dumps({"角色甲": {"front": {"path": str(p_ref)}}}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(sp, "vimax_working_dir", lambda *a, **k: work)
    monkeypatch.setattr(sp, "characters_bank_dir", lambda *a, **k: bank)
    monkeypatch.setattr(sp, "style_bank_dir", lambda *a, **k: sbank)

    script = _anon_script(scene="木屋前", ff_desc="角色甲站在屋前，手中空着，身背长剑。")
    script["shots"][0]["frame_layouts"]["ff"] = [
        {"name": "角色甲", "on_screen": True, "props": ["长剑"], "pose": "双手空着", "states": []},
    ]
    from shared.quality_gate import ensure_script_revision
    ensure_script_revision(script)

    q_rep = ipq.collect_image_prompt_quality(script, write=False)
    assert q_rep["status"] == "PASS"
    adv_warnings = [w for w in q_rep["warnings"] if w.get("category") == "unverified_relation"]
    assert adv_warnings and all(w.get("blocking") is False and w.get("severity") == "advisory" for w in adv_warnings)

    monkeypatch.setattr(ep, "validate_execution_binding", lambda *a, **k: [])
    monkeypatch.setattr(ep, "unresolved_gate_issues", lambda *a, **k: [])
    plan = ep.build_visual_plan(
        script,
        scope={"book_name": "匿名作品集", "book_index": 1, "part": 1, "chapter": 1},
        frames={"ff"},
    )
    assert plan["status"] == "planned"
    assert plan["shots"][0]["status"] == "planned"
    assert any(a["code"] == "unverified_relation" and a["blocking"] is False for a in plan["checks"]["advisories"])


def test_f7_keyframe_index_identity_and_budget_exhaustion_not_counted_as_http(tmp_path, monkeypatch):
    from _image_gen.models import ImageGenerationRequest
    from _image_gen.volcengine_provider import VolcengineImageService
    from shared import post_budget, request_events
    from tools.step02a_metrics_report import summarize_step02a_events

    events_file = tmp_path / "budget_events.jsonl"
    budget_file = tmp_path / "budget.json"
    monkeypatch.setenv("POST_EVENTS_FILE", str(events_file))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(budget_file))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "1")
    post_budget.reset("image")

    svc = VolcengineImageService.__new__(VolcengineImageService)
    svc._model_queue = ["doubao-seedream-5-0-pro-260628"]
    svc._base_url = "https://example.invalid/api/v3"
    svc._key_scheduler = type("KS", (), {"acquire": lambda self, timeout=180: None})()

    class _FakeResp:
        status_code = 200
        text = '{"data":[{"url":"https://example.invalid/ok.png"}]}'

        def json(self):
            return {"data": [{"url": "https://example.invalid/ok.png"}]}

    http_calls = []
    monkeypatch.setattr(
        "_image_gen.volcengine_provider.requests.post",
        lambda url, json=None, headers=None, timeout=180: (http_calls.append(json) or _FakeResp()),
    )
    monkeypatch.setattr("_image_gen.volcengine_provider.VOLCENGINE_API_KEY", "k")
    monkeypatch.setattr(svc, "_download", lambda r, u, i, **kw: type(
        "R", (), {"success": True, "save_path": str(tmp_path / "ok.png"), "error": None, "soft_skip": False, "request_meta": None}
    )())

    # 1st call succeeds -> 1 http_attempt + 1 requests.post
    out1 = svc.generate(ImageGenerationRequest(prompt="test1", shot_index=1, operation_id="op_kf_0"))
    assert out1.success is True
    # 2nd call fails on budget exhausted BEFORE HTTP dispatch -> 0 additional http_attempt!
    out2 = svc.generate(ImageGenerationRequest(prompt="test2", shot_index=1, operation_id="op_kf_1"))
    assert out2.success is False
    assert len(http_calls) == 1

    ev_rows = request_events.read_events(events_file)
    http_attempts = [e for e in ev_rows if e.get("event") == "http_attempt"]
    assert len(http_attempts) == 1
    assert http_attempts[0]["operation_id"] == "op_kf_0"

    # Distinct kf_index values under identical frame_role="keyframe" NEVER collide!
    # Also simulate a pre_send_failed request_sent event alongside http_attempt to prove no phantom POSTs.
    combined = list(ev_rows) + [
        {"event": "request_sent", "request_id": "pre_fail_1", "operation_id": "op_kf_1", "pre_send_failed": True},
        {
            "event": "decision_ledger", "seq": 1, "work_id": "w1", "chapter_scope": "part_01/chapter_001",
            "script_revision": "revA", "shot_index": 2, "frame_role": "keyframe", "kf_index": 0,
            "model_id": "doubao-seedream-5-0-pro-260628", "ref_strategy": "multi_ref_contract",
            "shot_complexity": "single_person", "prompt_template_version": "tpl_v2",
            "operation_id": "op_kf_0", "attempt_index": 1, "image_sha256": "kf0_sha",
            "action_type": "generate", "defect_class": "none", "qc_verdict": "qc_passed",
            "human_usability": "usable",
        },
        {
            "event": "decision_ledger", "seq": 2, "work_id": "w1", "chapter_scope": "part_01/chapter_001",
            "script_revision": "revA", "shot_index": 2, "frame_role": "keyframe", "kf_index": 1,
            "model_id": "doubao-seedream-5-0-pro-260628", "ref_strategy": "multi_ref_contract",
            "shot_complexity": "single_person", "prompt_template_version": "tpl_v2",
            "operation_id": "op_kf_1", "attempt_index": 1, "image_sha256": "",
            "action_type": "generate", "defect_class": "infra_unavailable", "qc_verdict": "qc_incomplete",
        },
    ]
    summary = summarize_step02a_events(combined)
    kb = summary["buckets"]["doubao-seedream-5-0-pro-260628|multi_ref_contract|single_person|tpl_v2"]
    assert kb["unique_frames"] == 2
    assert kb["attempts_total"] == 1
    assert kb["denominators"]["requests_per_usable_frame"] == 1

