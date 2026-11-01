# -*- coding: utf-8 -*-
"""studio_server 的离线用例：合成夹具 + 桩替换门禁重建，不读生产 output、不调模型.

覆盖：
1. 帧缓存判据与 step02a --plan-only 同源（cached/stale/missing 三态一致）；
2. 「保持此图」把当前哈希写回、且旧哈希有备份；
3. 「标记重抽」先备份再移除图与哈希；
4. 锁定镜头拒绝补丁（409），锁定状态落在 _production/ui_review_state.json；
5. 补丁落盘：备份 -> 写四份门禁 + 脚本（门禁重建用桩，验证的是本模块的写入顺序）；
6. HTTP 层：overview / 首页 / media Range(206) / 越界 media(403)。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tools import production_context as pc
from tools import studio_server as ss


# ---------------------------------------------------------------------------
# 夹具：一个最小的"已采纳"章节（内容匿名，与任何具体小说无关）
# ---------------------------------------------------------------------------
@pytest.fixture()
def clean_env(monkeypatch):
    for key in pc.CONTEXT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _shot(index=1):
    return {
        "index": index,
        "main_subject": "a lone figure",
        "location": "empty street",
        "core_keywords": ["rain", "lamp"],
        "ff_desc": "wide shot, figure standing at the corner",
        "lf_desc": "wide shot, figure walking away",
        "motion_desc": "walks forward slowly",
        "camera_movement": "static",
        "prompt": "cinematic, muted colors",
        "duration_sec": 8.0,
        "subtitle": "line %d" % index,
        "narration_segment": "narration %d" % index,
    }


def _build_chapter(root: Path) -> tuple[Path, dict]:
    from shared import config, quality_gate
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    ch = root / "output" / "novel" / "part_01_book" / "chapter_001"
    ch.mkdir(parents=True)
    script = {"shots": [_shot(1)], "characters": [], "hook": None,
              "timeline": [{"index": 1}], "chapter_index": 1, "total_duration": 60.0}
    revision = quality_gate.ensure_script_revision(script)
    (ch / "script_result.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    band = config.duration_band()
    quality = {"status": "PASS", "report_type": "quality_gate", "script_revision": revision,
               "rule_version": quality_gate.QUALITY_RULE_VERSION,
               "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
               "duration_band": band, "blocking": []}
    (ch / "step01_quality.json").write_text(json.dumps(quality, ensure_ascii=False), encoding="utf-8")
    audit = {"status": "ok", "input_revision": revision,
             "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
             "audit_scope": "final_after_deterministic_fixes", "duration_band": band,
             "issues": []}
    (ch / "step01_audit.json").write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    manifest = quality_gate.build_step01_commit_manifest(
        run_id="run-test", created_at="2026-11-01T00:00:00+08:00", revision=revision,
        rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
        rule_version=quality_gate.QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes", chapter_dir=ch, status="COMMITTED")
    quality_gate.write_step01_commit_manifest(ch, manifest)
    receipt = {
        "schema": pc.RECEIPT_SCHEMA, "status": "adopted_and_verified",
        "formal_chapter": str(ch), "output_base_override": str(ch.parents[2]),
        "script_revision": revision,
        "scope": {"novel": "novel", "book_index": 1, "book_name": "book", "chapter": 1},
        "formal_sha256": {"script_result.json": pc._sha256_file(ch / "script_result.json")},
        "effective_config": {},
    }
    (ch / "step01_adoption.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return ch, script


@pytest.fixture()
def studio(tmp_path, clean_env):
    ch, _script = _build_chapter(tmp_path)
    return ss.Studio(ch / "step01_adoption.json")


def _write_frame(studio: ss.Studio, kind="ff") -> Path:
    """放一张真实 PNG + 用生成链入口算出的哈希（不手填哈希）."""
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
    shot_dir = studio.shot_dir(1)
    shot_dir.mkdir(parents=True, exist_ok=True)
    name = FIRST_FRAME_FILENAME if kind == "ff" else LAST_FRAME_FILENAME
    png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    # 尾帧哈希把首帧当作参考图入键，所以判据要求首帧已存在。
    if kind == "lf":
        (shot_dir / FIRST_FRAME_FILENAME).write_bytes(png)
    (shot_dir / name).write_bytes(png)
    info = studio.frame_state(studio.unit(1), kind)
    assert info["current_hash"], info
    hash_name = "frame_hash.txt" if kind == "ff" else "last_frame_hash.txt"
    (shot_dir / hash_name).write_text(info["current_hash"] + chr(10), encoding="utf-8")
    return shot_dir


# ---------------------------------------------------------------------------
# 1) 判据同源
# ---------------------------------------------------------------------------
def test_frame_state_matches_plan_only(studio):
    from step02a_frame_gen.main import _plan_frame_state

    shot_dir = _write_frame(studio, "ff")
    unit = studio.unit(1)
    assert studio.frame_state(unit, "ff")["state"] == "cached"
    assert _plan_frame_state(unit, shot_dir, studio.characters(), "first_frame") == "cached"

    (shot_dir / "frame_hash.txt").write_text("stale-hash" + chr(10), encoding="utf-8")
    assert studio.frame_state(unit, "ff")["state"] == "stale"
    assert _plan_frame_state(unit, shot_dir, studio.characters(), "first_frame") == "generate"

    (shot_dir / "frame_hash.txt").unlink()
    assert studio.frame_state(unit, "ff")["state"] == "stale"
    assert _plan_frame_state(unit, shot_dir, studio.characters(), "first_frame") == "generate"

    (shot_dir / "first_frame.png").unlink()
    assert studio.frame_state(unit, "ff")["state"] == "missing"
    assert _plan_frame_state(unit, shot_dir, studio.characters(), "first_frame") == "generate"


# ---------------------------------------------------------------------------
# 2) 保持此图
# ---------------------------------------------------------------------------
def test_frame_keep_writes_hash_and_backs_up(studio):
    shot_dir = _write_frame(studio, "ff")
    hash_path = shot_dir / "frame_hash.txt"
    hash_path.write_text("outdated" + chr(10), encoding="utf-8")
    assert studio.frame_state(studio.unit(1), "ff")["state"] == "stale"

    result = studio.frame_keep(1, "ff")
    assert result["ok"] and result["hash"]
    assert hash_path.read_text(encoding="utf-8").strip() == result["hash"]
    assert studio.frame_state(studio.unit(1), "ff")["state"] == "cached"
    backup = Path(result["backup"])
    assert (backup / "frame_hash.txt").read_text(encoding="utf-8").strip() == "outdated"
    assert json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["model_sends"] == 0
    assert studio.is_kept("step02a", "1:ff")


# ---------------------------------------------------------------------------
# 3) 标记重抽
# ---------------------------------------------------------------------------
def test_frame_regen_backs_up_then_removes(studio):
    shot_dir = _write_frame(studio, "lf")
    image = shot_dir / "last_frame.png"
    result = studio.frame_regen(1, "lf")
    assert result["ok"]
    assert not image.exists()
    assert not (shot_dir / "last_frame_hash.txt").exists()
    backup = Path(result["backup"])
    assert (backup / "last_frame.png").is_file()
    assert studio.frame_state(studio.unit(1), "lf")["state"] == "missing"


# ---------------------------------------------------------------------------
# 4) 锁定状态
# ---------------------------------------------------------------------------
def test_keep_state_persists_and_blocks_patch(studio):
    studio.set_keep("step01", "1", True)
    state_path = studio.chapter / "_production" / "ui_review_state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema"] == ss.STATE_SCHEMA
    assert state["keep"]["step01"] == ["1"]
    assert state["history"][-1]["model_sends"] == 0

    with pytest.raises(ss.StudioError) as exc:
        studio.patch_shot(1, {"prompt": "changed"}, apply=False)
    assert exc.value.code == "shot_kept"
    assert exc.value.status == 409

    studio.set_keep("step01", "1", False)
    assert not studio.is_kept("step01", "1")


# ---------------------------------------------------------------------------
# 5) 补丁落盘（门禁重建用桩：只验本模块的写入顺序与备份）
# ---------------------------------------------------------------------------
def test_patch_apply_writes_gate_and_script(studio, monkeypatch):
    called = {}

    def fake_gate(receipt, new_script, *, patch_meta, novel_file=None):
        called["meta"] = patch_meta
        ch = Path(receipt["formal_chapter"])
        files = {}
        for name in ("step01_quality.json", "step01_audit.json",
                     "step01_commit.json", "step01_adoption.json"):
            files[name] = (ch / name).read_text(encoding="utf-8")
        return files, {"formal_sha256": {}}, "rev-after-patch"

    monkeypatch.setattr(studio.pc, "build_shot_patch_gate", fake_gate)

    dry = studio.patch_shot(1, {"prompt": "new prompt"}, apply=False)
    assert dry["dry_run"] is True and dry["revision"] == "rev-after-patch"
    assert not (studio.chapter / "_production" / "shot_patches").exists()

    applied = studio.patch_shot(1, {"prompt": "new prompt"}, apply=True)
    assert applied["ok"] and applied["written"]
    assert Path(applied["backup"], "script_result.json").is_file()
    script = json.loads((studio.chapter / "script_result.json").read_text(encoding="utf-8"))
    assert script["shots"][0]["prompt"] == "new prompt"
    assert called["meta"]["changed_fields"] == ["prompt"]
    records = list((studio.chapter / "_production" / "shot_patches").glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["model_sends"] == 0


def test_patch_rejects_disallowed_field(studio):
    with pytest.raises(ss.StudioError) as exc:
        studio.patch_shot(1, {"subtitle": "nope"}, apply=False)
    assert exc.value.code == "patch_field_not_allowed"


# ---------------------------------------------------------------------------
# 6) HTTP 层
# ---------------------------------------------------------------------------
@pytest.fixture()
def http_server(studio):
    ss.StudioHandler.studio = studio
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ss.StudioHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, studio
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(request, timeout=10)


def test_http_overview_and_index(http_server):
    base, studio = http_server
    with _get(base + "/api/overview") as res:
        assert res.status == 200
        payload = json.loads(res.read().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["schema"] == ss.OVERVIEW_SCHEMA
    assert payload["counts"]["shots"] == 1
    assert payload["safety"]["model_sends"] == 0
    with _get(base + "/") as res:
        body = res.read().decode("utf-8")
    assert "aiVideo Studio" in body


def test_http_media_range_and_traversal(http_server, tmp_path):
    base, studio = http_server
    shot_dir = _write_frame(studio, "ff")
    image = shot_dir / "first_frame.png"
    url = base + "/media?path=" + urllib.parse.quote(str(image))
    with _get(url, {"Range": "bytes=0-3"}) as res:
        assert res.status == 206
        assert res.headers["Content-Range"] == "bytes 0-3/%d" % image.stat().st_size
        assert len(res.read()) == 4
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base + "/media?path=" + urllib.parse.quote(str(outside)))
    assert exc.value.code == 403


def test_http_keep_endpoint(http_server):
    base, studio = http_server
    payload = json.dumps({"stage": "step01", "key": "1", "keep": True}).encode("utf-8")
    request = urllib.request.Request(base + "/api/keep", data=payload,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as res:
        body = json.loads(res.read().decode("utf-8"))
    assert body["ok"] is True and body["keep"] is True
    assert studio.is_kept("step01", "1")

# ---------------------------------------------------------------------------
# 7) 素材策略 / 单镜审校 / 别名端点 / 单镜合成
# ---------------------------------------------------------------------------
def test_overview_media_policy_and_review(studio, monkeypatch):
    monkeypatch.delenv("VIDEO_MEDIA_POLICY", raising=False)
    ov = studio.overview()
    mp = ov["shots"][0]["media_policy"]
    assert mp["policy"] == "auto"
    assert mp["required"] == ["ff", "lf"]
    _write_frame(studio, "ff")
    rv = studio.review(1)
    assert rv["frames"]["ff"]["state"] == "cached"
    assert rv["media_policy"]["mode"]
    assert rv["prompts"]
    assert rv["model_sends"] == 0


def test_step03_units_and_files(studio):
    info = studio.step03()
    assert info["ok"] is True
    assert isinstance(info["units"], list)
    assert isinstance(info["files"], list)
    assert info["model_sends"] == 0


def test_http_review_and_alias_sync_hash(http_server):
    base, studio = http_server
    _write_frame(studio, "ff")
    with _get(base + "/api/review?shot=1") as res:
        payload = json.loads(res.read().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["frames"]["ff"]["state"] == "cached"

    body = json.dumps({"shot": 1, "kind": "ff"}).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/frame/sync_hash", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as res:
        out = json.loads(res.read().decode("utf-8"))
    assert out["ok"] is True and out["hash"]
    assert studio.frame_state(studio.unit(1), "ff")["state"] == "cached"


def test_http_alias_save_uses_stub_gate(http_server, monkeypatch):
    base, studio = http_server

    def fake_gate(receipt, new_script, *, patch_meta, novel_file=None):
        ch = Path(receipt["formal_chapter"])
        files = {name: (ch / name).read_text(encoding="utf-8")
                 for name in ("step01_quality.json", "step01_audit.json",
                              "step01_commit.json", "step01_adoption.json")}
        return files, {"formal_sha256": {}}, "rev-alias"

    monkeypatch.setattr(studio.pc, "build_shot_patch_gate", fake_gate)
    body = json.dumps({"shot": 1, "fields": {"prompt": "alias prompt"}}).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/shot/save", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as res:
        out = json.loads(res.read().decode("utf-8"))
    assert out["ok"] and out["revision"] == "rev-alias"
    script = json.loads((studio.chapter / "script_result.json").read_text(encoding="utf-8"))
    assert script["shots"][0]["prompt"] == "alias prompt"


def test_http_merge_shot_with_stub_ffmpeg(http_server, monkeypatch):
    from types import SimpleNamespace

    base, studio = http_server
    shot_dir = studio.shot_dir(1)
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "video.mp4").write_bytes(b"video")

    def fake_run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"merged")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    body = json.dumps({"shot": 1}).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/shot/merge", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as res:
        out = json.loads(res.read().decode("utf-8"))
    assert out["ok"] is True
    assert Path(out["output"]).is_file()
    assert out["model_sends"] == 0

