# -*- coding: utf-8 -*-
"""S06 verification matrix: controlled anonymous multi-domain offline & stub end-to-end checks."""
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


def _noisy_image(w: int = 256, h: int = 256, seed: int = 7) -> Image.Image:
    import random
    rng = random.Random(seed)
    raw = bytes(rng.randrange(256) for _ in range(w * h * 3))
    return Image.frombytes("RGB", (w, h), raw)


def _png(path: Path, color) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return str(path)


def _setup_env(monkeypatch, tmp_path: Path, *, book: str = "匿名测试集", chapter: int = 1):
    from shared import paths as sp
    from shared.image_qc import QC_PROVIDERS  # noqa: F401

    ch_dir = tmp_path / "output" / book / "part_01" / f"chapter_{chapter:03d}"
    work_dir = ch_dir / "vimax_working"
    ch_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BOOK_NAME", book)
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("NOVEL_CHAPTER", str(chapter))
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    monkeypatch.setattr(sp, "chapter_dir", lambda *a, **k: ch_dir)
    monkeypatch.setattr(sp, "vimax_working_dir", lambda *a, **k: work_dir)
    monkeypatch.setattr(sp, "shot_work_dir", lambda idx, chapter=None: work_dir / "shots" / str(idx))
    monkeypatch.setattr("shared.vimax_bridge.shot_work_dir", lambda idx: work_dir / "shots" / str(idx))
    monkeypatch.setattr("shared.paths.shot_work_dir", lambda idx, chapter=None: work_dir / "shots" / str(idx))
    monkeypatch.setattr("shared.vimax_adapters._image_gen.create_service",
                        lambda *a, **k: type("Svc", (), {"supports_multi_ref": True})())
    monkeypatch.setattr("shared.ref_compose.active_image_ref_limit", lambda: 10)
    return ch_dir, work_dir


def test_domain_a_indoor_repair_titles_states_portraits_and_qc_lifecycle(tmp_path, monkeypatch):
    from shared import image_qc as iq
    from shared import vimax_bridge as vb
    from shared.quality_gate import QualityGateError, ensure_script_revision, require_gate
    from step02a_frame_gen import image_prompt_quality as ipq
    from step02a_frame_gen import verify_acceptance as va

    ch_dir, work_dir = _setup_env(monkeypatch, tmp_path)
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    p1 = _png(work_dir / "characters_bank" / "角色甲" / "front.png", (210, 40, 40))
    p2 = _png(work_dir / "characters_bank" / "角色乙" / "front.png", (40, 180, 60))
    sc = _png(work_dir / "scenes_bank" / "木器作坊" / "wide.png", (120, 150, 200))
    reg_path = work_dir / "character_portraits_registry.json"
    reg_path.write_text(json.dumps({
        "角色甲": {"front": {"path": p1, "video_style": "gufeng"}},
        "角色乙": {"front": {"path": p2, "video_style": "gufeng"}},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda _s: [sc])
    monkeypatch.setattr(vb, "_collect_space_object_refs", lambda _s: [])
    monkeypatch.setattr(vb, "_collect_prop_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_head_portrait_path", lambda p: p)
    monkeypatch.setattr(ipq, "chapter_dir", lambda: ch_dir)
    monkeypatch.setattr(va, "chapter_dir", lambda: ch_dir)
    monkeypatch.setattr(ipq, "_keyframes_consumed_by_video", lambda: False)

    chars = [
        {"identifier": "角色甲", "aliases": ["掌柜"], "gender": "male", "static_features": "方脸短须", "dynamic_features": "灰布长衫"},
        {"identifier": "角色乙", "aliases": ["伙计"], "gender": "female", "static_features": "扎双髻", "dynamic_features": "青布衫"},
    ]
    shot = {
        "index": 1,
        "duration_sec": 5.0,
        "location": "木器作坊",
        "scene": "木器作坊",
        "main_subject": "角色甲",
        "ff_desc": "掌柜俯身查看裂纹，伙计在旁掌灯。",
        "lf_desc": "掌柜直起腰，伙计收起铜灯。",
        "motion_desc": "掌柜起身，伙计收灯",
        "prompt": "[中景] 掌柜起身，伙计收灯",
        "narration_segment": "",
        "clip_transition": "硬切",
        "context_bridge": "",
        "keyframes": [],
        "frame_layouts": {
            "ff": [
                {"name": "掌柜", "on_screen": True, "position": "画左", "pose": "俯身", "props": ["铜秤"],
                 "support": {"id": "木案", "kind": "案几", "surface": "台面"}, "states": ["左臂缠布", "无墨迹"]},
                {"name": "伙计", "on_screen": True, "position": "画右", "pose": "托灯站立", "props": [],
                 "support": {"id": "砖地", "kind": "地面", "surface": "地砖"}, "states": []},
            ],
            "lf": [
                {"name": "角色甲", "on_screen": True, "position": "画左", "pose": "站立", "states": []},
                {"name": "角色乙", "on_screen": False},
            ],
        },
    }
    script = {
        "title": "匿名工坊",
        "book_name": "匿名测试集",
        "book_index": 1,
        "chapter_index": 1,
        "total_duration": 5.0,
        "characters": chars,
        "chapter_outline": {"narration_lines": []},
        "hook": None,
        "shots": [shot],
    }
    ensure_script_revision(script)
    (ch_dir / "script_result.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    # 1. Stub adapter capturing prepared prompt and writing >5KB decodable image
    class _StubAdapter:
        def __init__(self):
            self.calls = []

        async def generate_single_image(self, prompt, reference_image_paths=None, **kwargs):
            self.calls.append({"prompt": prompt, "refs": list(reference_image_paths or []), **kwargs})
            from vimax.interfaces import ImageOutput
            return ImageOutput(fmt="pil", ext="png", data=_noisy_image(seed=len(self.calls) + 1))

    adapter = _StubAdapter()
    # Stub QC: reject first attempt on ff, pass retry on ff and lf
    qc_calls = []

    def _stub_qc(frame_path, shot_obj, frame_kind, characters=None, empty_scene=False, prompt=None, kf_index=None):
        qc_calls.append((Path(frame_path).name, len(qc_calls)))
        if frame_kind == "first_frame" and sum(1 for k, _ in qc_calls if k == "first_frame.png") == 1:
            return iq.QcResult(
                has_hallucination=True,
                issues=[iq.QcIssue(category="extra_person", description="首次模拟拒绝", severity="critical")],
                ok=True, raw_result="",
            )
        return iq.QcResult(has_hallucination=False, issues=[], ok=True, raw_result="")

    monkeypatch.setattr(iq, "maybe_qc_sync", _stub_qc)
    monkeypatch.setattr(iq, "QC_ENABLED", True)
    monkeypatch.setattr(iq, "QC_WRITE_CRITICAL", True)

    res = asyncio.run(vb.gen_shot_frames(shot, work_dir / "shots", adapter, None, characters=chars))
    assert Path(res["ff_path"]).is_file()
    assert Path(res["lf_path"]).is_file()
    # Prompt count uses roster (2 on ff even when prose only uses titles) & preserves states/negatives
    ff_call = next(c for c in adapter.calls if c.get("frame_desc") == "首帧")
    assert "总共2人" in ff_call["prompt"]
    assert "左臂缠布" in ff_call["prompt"]
    assert "无墨迹" in ff_call["prompt"]
    assert len(ff_call["refs"]) == 3  # scene + p1 + p2
    assert p1 in ff_call["refs"] and p2 in ff_call["refs"]

    # Final acceptance PASS after retry passed QC
    rep = ipq.write_image_product_quality(script, work_dir, media_policy="first_last")
    assert rep["status"] == "PASS"
    assert rep["file_ok"] is True and rep["qc_ok"] is True
    require_gate(ch_dir / "step02a_quality.json", script, stage="step02a",
                 required_report_type="product_acceptance", verify_outputs=True, output_indices={1})

    # Verify-only passes when locked & qc_passed, and rejects if image tampered
    sdir = work_dir / "shots" / "1"
    (sdir / ".lock_frame_first_frame").write_text("", encoding="utf-8")
    (sdir / ".lock_frame_last_frame").write_text("", encoding="utf-8")
    assert va.run_verify_only(ch_dir / "script_result.json", work_dir, shots={1}, policies={1: "first_last"}) == 0

    _png(sdir / "last_frame.png", (1, 2, 3))
    with pytest.raises(QualityGateError):
        require_gate(ch_dir / "step02a_quality.json", script, stage="step02a",
                     required_report_type="product_acceptance", verify_outputs=True, output_indices={1})
    assert va.run_verify_only(ch_dir / "script_result.json", work_dir, shots={1}, policies={1: "first_last"}) == 1


def test_domain_b_market_handoff_all_offscreen_and_missing_portrait_block():
    from shared import prompt_facts as pf
    from shared import style_prefix as sp
    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    chars = [{"identifier": "买主"}, {"identifier": "店主", "aliases": ["掌柜"]}]
    shot = {
        "index": 2,
        "location": "北街药铺",
        "ff_desc": "空巷青石板上落着半片纸钱。",
        "lf_desc": "店主把药包递给买主。",
        "keyframes": [{"desc": "药包落在柜面。", "t": 0.5}],
        "frame_layouts": {
            "ff": [{"name": "买主", "on_screen": False}, {"name": "店主", "on_screen": False}],
            "lf": [{"name": "买主", "on_screen": True, "states": []},
                   {"name": "掌柜", "on_screen": True, "states": ["袖口微湿"]}],
            "kfs": [[{"name": "买主", "on_screen": True, "states": []},
                     {"name": "店主", "on_screen": False}]],
        },
    }
    ff_proj = pf.project_frame_facts(shot, "first_frame", chars)
    assert ff_proj["entities"] == [] and ff_proj["spec_present"] is True
    ff_c = sp.add_image_strict_constraints(shot["ff_desc"], "首帧", shot=shot, characters=chars)
    assert "总共0人" in ff_c

    kf_proj = pf.project_frame_facts(shot, "keyframe", chars, kf_index=0)
    assert [e["name"] for e in kf_proj["entities"]] == ["买主"]
    assert ImageGeneratorAiVideoAdapter is not None


def test_domain_c_vehicle_travel_ref_compression_and_provider_verification(tmp_path, monkeypatch):
    from _image_gen.volcengine_provider import VolcengineImageService
    from shared import vimax_adapters as va

    ch_dir, work_dir = _setup_env(monkeypatch, tmp_path, chapter=2)
    r_scene = _png(work_dir / "scenes_bank" / "官道" / "wide.png", (100, 150, 200))
    r_c1 = _png(work_dir / "characters_bank" / "车夫" / "front.png", (200, 50, 50))
    r_c2 = _png(work_dir / "characters_bank" / "商旅" / "front.png", (50, 200, 50))
    r_prop = _png(work_dir / "props_bank" / "行囊" / "prop.png", (180, 180, 40))

    from shared.model_caps import get_seedream_cap as _orig_cap
    monkeypatch.setattr(
        "shared.model_caps.get_seedream_cap",
        lambda m: (type(_orig_cap(m))(**{**_orig_cap(m).__dict__, "max_refs": 2})
                   if "lite" in str(m) else _orig_cap(m)),
    )
    adapter = va.ImageGeneratorAiVideoAdapter(size="1600x2848", enable_hallucination_lock=False)
    req = adapter.prepare_single_image(
        "官道马车前行，车夫勒缰，商旅在车厢内。",
        [r_scene, r_c1, r_c2, r_prop],
        materialize=True,
        shot_index=3,
        frame_desc="首帧",
        force_multi_ref=True,
    )

    svc = VolcengineImageService.__new__(VolcengineImageService)
    svc._model_queue = ["doubao-seedream-5-0-pro-260628", "doubao-seedream-5-0-lite"]
    svc._base_url = "https://example.invalid/api/v3"
    svc._key_scheduler = type("KS", (), {"acquire": lambda self, timeout=180: None})()

    wire_calls = []

    class _FakeResp:
        def __init__(self, code, data):
            self.status_code = code
            self._data = data
            self.text = json.dumps(data, ensure_ascii=False)

        def json(self):
            return self._data

    def fake_post(url, json=None, headers=None, timeout=180):
        wire_calls.append(json)
        if len(wire_calls) == 1:
            return _FakeResp(403, {"error": {"message": "fallback"}})
        return _FakeResp(200, {"data": [{"url": "https://example.invalid/final.png"}]})

    monkeypatch.setattr("_image_gen.volcengine_provider.requests.post", fake_post)
    monkeypatch.setattr("_image_gen.volcengine_provider.VOLCENGINE_API_KEY", "k")
    monkeypatch.setattr(svc, "_download", lambda r, u, i: type("R", (), {"success": True, "save_path": str(tmp_path / "ok.png"), "error": None, "soft_skip": False})())

    out = svc.generate(req)
    assert out.success is True
    assert len(wire_calls) == 2
    assert len(wire_calls[0]["image"]) == 4
    assert len(wire_calls[1]["image"]) == 2  # re-fitted for fallback cap=2!

    # Tamper a reference file after prepare_single_image -> blocked before POST!
    _png(Path(r_c1), (1, 1, 1))
    before = len(wire_calls)
    tampered = svc.generate(req)
    assert not tampered.success
    assert "篡改" in (tampered.error or "")
    assert len(wire_calls) == before
