# -*- coding: utf-8 -*-
"""F 阶段退出验收：同一事务入口贯通三包（计划 §F退出/§15「F共用验证入口」）.

`tools/verify_enhancement_transaction.py` 是 F 的**唯一**验收命令；这里：
* 用**三份有效协议 fixture**（video/tts/merge，媒体都是本机 ffmpeg 现造的真实文件）
  走 ``input → manifest → accepted → 完整读回``；
* 断言故障矩阵真的有判别力（撤掉关键判据 ⇒ 工具转红）；
* 断言源 fixture 与正式产物目录摘要不变、工作目录全在隔离 temp 下。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import enhancement_io as eio          # noqa: E402
from tools import verify_enhancement_transaction as vet   # noqa: E402

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg 造有效协议 fixture")


# ---------------------------------------------------------------- fixture 生成

def _media(root: Path, name: str, *, kind: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    if kind == "wav":
        args = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=320:sample_rate=16000:duration=1",
                "-ac", "1", "-c:a", "pcm_s16le", str(path)]
    else:
        args = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=blue:s=160x120:r=10:d=1", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.run(args, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-500:]
    return path


def build_fixture(root: Path, package: str) -> Path:
    """一份**有效协议** fixture：真实媒体 + 来源/范围/revision + 要求的检查项。"""
    fixture_dir = Path(root) / package
    media_dir = fixture_dir / "media"
    if package == "video":
        media = [_media(media_dir, "shot_07.mp4", kind="mp4")]
        roles, checks = ["candidate_video"], [{"name": "decode", "status": "pass"},
                                              {"name": "media_qc", "status": "pass"}]
    elif package == "tts":
        media = [_media(media_dir, "shot_07.wav", kind="wav")]
        roles, checks = ["synthesis"], [{"name": "decode", "status": "pass"},
                                        {"name": "identity", "status": "pass"}]
    else:  # merge
        media = [_media(media_dir, "final.mp4", kind="mp4")]
        roles, checks = ["merged_video"], [{"name": "decode", "status": "pass"},
                                           {"name": "audio_track", "status": "pass"}]
    payload = {
        "schema_version": vet.SCHEMA_FIXTURE,
        "package": package,
        "scope": {"novel": "fixture-novel", "chapter": 1},
        "script_revision": "rev-fixture-0001",
        "source": "fixture",
        "request_key": f"rk-{package}-fixture",
        "checks": checks,
        "coverage": {"fixture": True},
        "artifacts": [{"role": roles[0], "path": str(p.relative_to(fixture_dir))}
                      for p in media],
    }
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "fixture.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixture_dir


@pytest.fixture
def built(tmp_path):
    root = tmp_path / "fixtures"
    return {p: build_fixture(root, p) for p in vet.PACKAGES}


def _out_dir(tmp_path, package: str) -> Path:
    d = tmp_path / "out" / package
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 成功出口

@pytest.mark.parametrize("package", vet.PACKAGES)
def test_transaction_success_path_for_each_package(built, tmp_path, package):
    fixture = built[package]
    fixture_digest = {str(p): eio.sha256_file(p) for p in sorted(fixture.rglob("*")) if p.is_file()}
    out = _out_dir(tmp_path, package)

    rc = vet.main(["--package", package, "--fixture", str(fixture), "--out-dir", str(out)])
    assert rc == vet.EXIT_OK

    report = vet.run_package(package, fixture, out)
    assert report["ok"] is True
    names = [c["name"] for c in report["checks"]]
    assert names[0] == "success_path"
    assert set(names) >= {"success_path", "mkdir_failure", "write_failure",
                          "commit_unconfirmed", "concurrent_writer",
                          "tamper_detected", "recovery"}
    assert all(c["ok"] for c in report["checks"]), report["checks"]
    # 源 fixture 只读：内容逐字节不变
    after = {str(p): eio.sha256_file(p) for p in sorted(fixture.rglob("*")) if p.is_file()}
    assert after == fixture_digest
    # 工作目录是调用方隔离 root 的**同级临时目录**（副本注入，不写共享树）
    work = Path(report["work_dir"])
    assert work.parent == out.parent and work.name.startswith(f"txn-{package}-")
    assert report["production_unchanged"] is True


@pytest.mark.parametrize("package", vet.PACKAGES)
def test_readback_chain_is_complete(built, tmp_path, package):
    """指针 → manifest → 全部产物 → 来源关系 → required_checks，缺一不可。"""
    fixture = built[package]
    out = _out_dir(tmp_path, package)
    report = vet.run_package(package, fixture, out)
    ok_check = report["checks"][0]
    assert ok_check["name"] == "success_path" and ok_check["ok"]

    pkg_root = Path(report["work_dir"]) / package
    verified = eio.verify_accepted(pkg_root, required_checks=("decode",))
    assert verified["manifest"]["package"] == package
    assert verified["manifest"]["source"] == "fixture"
    # 视频包：任务状态投影**不在**产物 manifest 里
    if package == "video":
        assert ok_check["lifecycle_outside_manifest"] is True
        keys = set(verified["manifest"].keys())
        assert not ({"lifecycle", "state", "task_id"} & keys)


def test_video_lifecycle_not_in_artifact_projection(built, tmp_path):
    """video lifecycle 不进产物 manifest：删掉 lifecycle 后读回仍成立。"""
    out = _out_dir(tmp_path, "video")
    report = vet.run_package("video", built["video"], out)
    assert report["checks"][0]["lifecycle_outside_manifest"] is True
    attempt = Path(report["checks"][0]["attempt"])
    assert not (attempt / eio.LIFECYCLE_FILENAME).exists()      # 已被证明可删
    assert (attempt / eio.MANIFEST_FILENAME).is_file()


# ---------------------------------------------------------------- 判别性反例

def test_tamper_check_is_discriminating(built, tmp_path, monkeypatch):
    """撤掉读者侧摘要核验（关键判据）⇒ 篡改项必须转红（不是"永远 pass"）。"""
    out = _out_dir(tmp_path, "video")
    report = vet.run_package("video", built["video"], out)
    tamper = [c for c in report["checks"] if c["name"] == "tamper_detected"][0]
    assert tamper["ok"] is True and tamper["verified_before_tamper"] is True

    real_verify = eio.verify_accepted

    def _blind_verify(package_root, **kwargs):
        """把"核验"降级成"文件存在"：判据撤掉的形态。"""
        pointer = eio.read_accepted(package_root)
        assert pointer is not None
        manifest_path = Path(pointer["manifest_path"])
        return {"pointer": pointer,
                "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
                "manifest_sha256": pointer["manifest_sha256"]}

    monkeypatch.setattr(eio, "verify_accepted", _blind_verify)
    try:
        report2 = vet.run_package("video", built["video"], _out_dir(tmp_path, "video"))
    finally:
        monkeypatch.setattr(eio, "verify_accepted", real_verify)
    tamper2 = [c for c in report2["checks"] if c["name"] == "tamper_detected"][0]
    assert tamper2["ok"] is False                 # 判据撤掉 ⇒ 篡改未被检出 ⇒ 该项不通过
    assert report2["ok"] is False                 # 整体验收随之转红


def test_commit_confirmation_failure_is_reported(built, tmp_path):
    """替换后读回失败 ⇒ commit_unconfirmed（退出码 6），且旧指针保留。"""
    out = _out_dir(tmp_path, "tts")
    report = vet.run_package("tts", built["tts"], out)
    check = [c for c in report["checks"] if c["name"] == "commit_unconfirmed"][0]
    assert check["ok"] is True
    assert check["exit_code"] == vet.EXIT_COMMIT_UNCONFIRMED
    assert check["replacement_performed"] is True and check["confirmed"] is False
    # 替换**已经发生**（盘上是新指针），既不能声称旧值仍在，也不盲目回滚
    assert check["first_commit_input_key"] == "old"
    assert check["pointer_after_failure_input_key"] == "new"


def test_lock_is_busy_for_second_writer(built, tmp_path):
    out = _out_dir(tmp_path, "merge")
    report = vet.run_package("merge", built["merge"], out)
    check = [c for c in report["checks"] if c["name"] == "concurrent_writer"][0]
    assert check["ok"] is True and check["busy"]["exit_code"] == vet.EXIT_BUSY
    assert check["recovered_after_owner_exit"] is True


def test_recovery_cases(built, tmp_path):
    out = _out_dir(tmp_path, "video")
    report = vet.run_package("video", built["video"], out)
    rec = [c for c in report["checks"] if c["name"] == "recovery"][0]
    detail = {d["case"]: d for d in rec["details"]}
    assert set(detail) == {"projection_lagging", "projection_ahead", "tail_incomplete"}
    assert all(d["ok"] for d in detail.values()), detail
    assert detail["tail_incomplete"]["evidence_kept"] is True       # 原始证据保留


# ---------------------------------------------------------------- 输入守卫

def test_wrong_package_rejected(built, tmp_path):
    rc = vet.main(["--package", "tts", "--fixture", str(built["video"]),
                   "--out-dir", str(_out_dir(tmp_path, "tts"))])
    assert rc == vet.EXIT_INVALID_INPUT


def test_out_dir_must_match_package(built, tmp_path):
    bad = tmp_path / "out" / "video-elsewhere"
    bad.mkdir(parents=True)
    rc = vet.main(["--package", "video", "--fixture", str(built["video"]),
                   "--out-dir", str(bad)])
    assert rc == vet.EXIT_INVALID_INPUT


def test_missing_fixture_media_rejected(built, tmp_path):
    fixture = built["merge"]
    payload = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
    Path(fixture / payload["artifacts"][0]["path"]).unlink()
    rc = vet.main(["--package", "merge", "--fixture", str(fixture),
                   "--out-dir", str(_out_dir(tmp_path, "merge"))])
    assert rc == vet.EXIT_INVALID_INPUT


def test_second_run_on_same_out_dir_still_passes(built, tmp_path):
    """同一隔离 root 上重复跑：事务入口必须幂等可用（新 attempt，不覆盖旧证据）。"""
    out = _out_dir(tmp_path, "video")
    first = vet.run_package("video", built["video"], out)
    second = vet.run_package("video", built["video"], out)
    assert first["ok"] and second["ok"]
    assert first["work_dir"] != second["work_dir"]
