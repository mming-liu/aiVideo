# -*- coding: utf-8 -*-
"""S04 verification: portrait selection by roster, prepare_single_image, execution_plan, and pre-POST checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _png(path: Path, color=(80, 120, 180)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return str(path)


def test_collect_portrait_refs_uses_roster_without_prose_names(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb

    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    p1 = _png(tmp_path / "characters_bank" / "角色甲" / "front.png", (200, 40, 40))
    p2 = _png(tmp_path / "characters_bank" / "角色乙" / "front.png", (40, 200, 40))
    reg_path = tmp_path / "vimax_working" / "character_portraits_registry.json"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps({
        "角色甲": {"front": {"path": p1, "video_style": "gufeng"}},
        "角色乙": {"front": {"path": p2, "video_style": "gufeng"}},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda: reg_path.parent)
    monkeypatch.setattr(vb, "_head_portrait_path", lambda p: p)

    chars = [
        {"identifier": "角色甲", "aliases": ["掌柜"]},
        {"identifier": "角色乙", "aliases": ["伙计"]},
    ]
    shot = {
        "index": 6,
        "ff_desc": "作坊里掌柜与伙计低声交谈。",
        "lf_desc": "掌柜独自整理铜秤。",
        "frame_layouts": {
            "ff": [
                {"name": "角色甲", "on_screen": True},
                {"name": "角色乙", "on_screen": True},
            ],
            "lf": [
                {"name": "角色甲", "on_screen": True},
                {"name": "角色乙", "on_screen": False},
            ],
        },
    }

    ff_details = vb._collect_portrait_refs_for_shot(
        shot, chars, limit=3, frame_desc=shot["ff_desc"], roster={"角色甲", "角色乙"},
        return_details=True,
    )
    assert ff_details["refs"] == [p1, p2]
    assert ff_details["identity_reference"] == "verified"

    lf_details = vb._collect_portrait_refs_for_shot(
        shot, chars, limit=3, frame_desc=shot["lf_desc"], roster={"角色甲"},
        return_details=True,
    )
    assert lf_details["refs"] == [p1]
    assert any(o["entity"] == "角色乙" and o["reason"] == "off_screen" for o in lf_details["omissions"])


def test_collect_portrait_refs_flags_missing_file_instead_of_silent_pass(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb

    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    reg_path = tmp_path / "vimax_working" / "character_portraits_registry.json"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps({
        "角色甲": {"front": {"path": str(tmp_path / "missing_front.png"),
                             "video_style": "gufeng"}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda: reg_path.parent)

    details = vb._collect_portrait_refs_for_shot(
        {"index": 1, "ff_desc": "角色甲走近"},
        [{"identifier": "角色甲"}],
        roster={"角色甲"},
        return_details=True,
    )
    assert details["refs"] == []
    assert details["identity_reference"] == "missing"
    assert details["missing"][0]["reason"] == "portrait_file_missing"


def test_prepare_single_image_fails_closed_and_attaches_contract(tmp_path, monkeypatch):
    from shared import vimax_adapters as va

    class _DummySvc:
        supports_multi_ref = True

    monkeypatch.setattr("shared.vimax_adapters._image_gen.create_service", lambda *a, **k: _DummySvc())
    monkeypatch.setattr("shared.ref_compose.active_image_ref_limit", lambda: 2)
    monkeypatch.setattr("shared.paths.shot_work_dir", lambda idx: tmp_path / f"shot_{idx}")

    adapter = va.ImageGeneratorAiVideoAdapter(size="1600x2848", enable_hallucination_lock=False)

    # Missing reference must fail closed instead of dropping to []
    with pytest.raises(FileNotFoundError):
        adapter.prepare_single_image(
            "测试提示词",
            [str(tmp_path / "not_exists.png")],
            materialize=True,
            shot_index=1,
            frame_desc="首帧",
        )

    r1 = _png(tmp_path / "characters_bank" / "角色甲" / "front.png", (210, 40, 40))
    r2 = _png(tmp_path / "scenes_bank" / "作坊" / "wide.png", (40, 120, 210))
    req = adapter.prepare_single_image(
        "测试提示词",
        [r2, r1],
        materialize=False,
        shot_index=1,
        frame_desc="首帧",
        force_multi_ref=True,
    )
    assert req.frame_contract is not None
    assert req.frame_contract["ref_paths"] == [r2, r1]
    assert "【参考图说明】" in req.prompt
    assert req.reference_image_paths == [r2, r1]


def test_volcengine_pre_post_blocks_corrupt_ref_and_revalidates_on_fallback(tmp_path, monkeypatch):
    from _image_gen.models import ImageGenerationRequest
    from _image_gen.volcengine_provider import VolcengineImageService
    from shared import vimax_adapters as va

    r1 = _png(tmp_path / "characters_bank" / "角色甲" / "front.png", (210, 40, 40))
    r2 = _png(tmp_path / "characters_bank" / "角色乙" / "front.png", (40, 210, 40))
    r3 = _png(tmp_path / "scenes_bank" / "作坊" / "wide.png", (40, 40, 210))
    r4 = _png(tmp_path / "props_bank" / "木尺" / "prop.png", (200, 200, 40))

    monkeypatch.setattr("shared.ref_compose.active_image_ref_limit", lambda: 10)
    monkeypatch.setattr("shared.paths.shot_work_dir", lambda idx: tmp_path / f"shot_{idx}")
    monkeypatch.setattr("shared.vimax_adapters._image_gen.create_service", lambda *a, **k: type("S", (), {"supports_multi_ref": True})())

    adapter = va.ImageGeneratorAiVideoAdapter(size="1600x2848", enable_hallucination_lock=False)
    req = adapter.prepare_single_image(
        "测试双人构图", [r3, r1, r2, r4], materialize=True,
        shot_index=2, frame_desc="首帧", force_multi_ref=True,
    )

    svc = VolcengineImageService.__new__(VolcengineImageService)
    svc._model_queue = ["doubao-seedream-5-0-pro-260628", "doubao-seedream-5-0-lite"]
    svc._base_url = "https://example.invalid/api/v3"
    svc._key_scheduler = type("KS", (), {"acquire": lambda self, timeout=180: None})()

    sent_payloads = []

    class _FakeResp:
        def __init__(self, code, payload_obj):
            self.status_code = code
            self._obj = payload_obj
            self.text = json.dumps(payload_obj, ensure_ascii=False)

        def json(self):
            return self._obj

    def fake_post(url, json=None, headers=None, timeout=180):
        sent_payloads.append(json)
        if len(sent_payloads) == 1:
            return _FakeResp(403, {"error": {"message": "ModelAccessDenied"}})
        return _FakeResp(200, {"data": [{"url": "https://example.invalid/out.png"}]})

    monkeypatch.setattr("_image_gen.volcengine_provider.requests.post", fake_post)
    monkeypatch.setattr("_image_gen.volcengine_provider.VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_download", lambda r, u, idx: type("Res", (), {"success": True, "save_path": str(tmp_path / "out.png"), "error": None, "soft_skip": False})())

    from shared.model_caps import get_seedream_cap as _orig_cap
    monkeypatch.setattr(
        "shared.model_caps.get_seedream_cap",
        lambda m: (type(_orig_cap(m))(**{**_orig_cap(m).__dict__, "max_refs": 2})
                   if "lite" in str(m) else _orig_cap(m)),
    )

    # Normal queue: first model sends 4 refs; after 403 fallback to lite (cap=2),
    # refs must compress cleanly to <=2 and update manifest without crashing or dropping blindly.
    res = svc.generate(req)
    assert res.success
    assert len(sent_payloads) == 2
    assert len(sent_payloads[0]["image"]) == 4
    assert len(sent_payloads[1]["image"]) <= 2
    assert "frame_contract" not in sent_payloads[0]

    # Tampered/missing ref before POST must block BEFORE requests.post
    bad_req = ImageGenerationRequest(
        prompt=req.prompt,
        shot_index=2,
        reference_image_paths=[str(tmp_path / "deleted.png")],
        frame_contract=req.frame_contract,
    )
    calls_before = len(sent_payloads)
    res_bad = svc.generate(bad_req)
    assert not res_bad.success
    assert len(sent_payloads) == calls_before


def test_execution_plan_covers_kf_and_lf_first_frame_dependency(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import build_visual_plan
    from shared import vimax_bridge as vb

    scene = _png(tmp_path / "scenes_bank" / "作坊" / "wide.png")
    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda _shot: [scene])
    monkeypatch.setattr(vb, "_collect_space_object_refs", lambda _shot: [])
    monkeypatch.setattr(vb, "_collect_portrait_refs_for_shot", lambda *a, **k: {"refs": [], "omissions": [], "missing": [], "identity_reference": "unverified"})
    monkeypatch.setattr(vb, "_collect_prop_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr("shared.paths.shot_work_dir", lambda idx: tmp_path / "shots" / str(idx))

    script = {
        "book_name": "测试卷",
        "book_index": 1,
        "chapter_index": 1,
        "script_revision": "a" * 64,
        "characters": [{"identifier": "角色甲", "static_features": "短褐"}],
        "shots": [{
            "index": 1,
            "location": "作坊",
            "ff_desc": "角色甲站在木案前。",
            "lf_desc": "角色甲拾起铁尺。",
            "keyframes": [{"desc": "角色甲弯腰伸手。", "t": 0.5}],
            "frame_layouts": {
                "ff": [{"name": "角色甲", "on_screen": True, "states": []}],
                "lf": [{"name": "角色甲", "on_screen": True, "states": ["心口暗红"]}],
                "kfs": [[{"name": "角色甲", "on_screen": True, "states": ["左袖湿透"]}]],
            },
        }],
    }
    plan = build_visual_plan(
        script,
        scope={"book_name": "测试卷", "book_index": 1, "part": 1, "chapter": 1},
        frames={"ff", "lf", "kf"},
    )
    by_frame = {f["frame"]: f for f in plan["frames"]}
    assert "关键帧" in by_frame["kf0"]["prompt"]
    assert "左袖湿透" in by_frame["kf0"]["prompt"]
    assert "心口暗红" in by_frame["lf"]["prompt"]
    lf_ref_roles = [r["role"] for r in by_frame["lf"]["references"]]
    assert "first_frame" in lf_ref_roles
    ff_dep = next(r for r in by_frame["lf"]["references"] if r["role"] == "first_frame")
    assert ff_dep["prepared"] is None
    assert ff_dep.get("materialized_at_execution") is True
