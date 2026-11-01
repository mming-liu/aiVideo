"""S19-3 端到端：step04 --shots 子集试合并的**真实 FFmpeg 编码**闭环.

用 lavfi 合成 2 秒测试片段，实跑 CLI：
门禁（step01 + step02b 子集投影 + scope 守卫）→ compile_graph → render →
final_shots_1.mp4 / subtitles_shots_1.srt / step04_shots_1_quality.json。
正式 final.mp4 必须不被创建。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(not (_FFMPEG and _FFPROBE),
                                reason="ffmpeg/ffprobe not available")


def _run(cmd, *, cwd=_REPO, env=None, timeout=180):
    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(cmd, cwd=str(cwd), env=merged, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _make_clip(path: Path, seconds: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = _run([_FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i",
                 f"testsrc2=size=360x640:rate=24:duration={seconds}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)])
    assert proc.returncode == 0, proc.stderr[-800:]


def _probe_duration(path: Path) -> float:
    proc = _run([_FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)])
    assert proc.returncode == 0, proc.stderr[-400:]
    return float(proc.stdout.strip())


def _write_step01_artifacts(ch: Path, script: dict) -> str:
    from shared.config import duration_band
    from shared.quality_gate import (QUALITY_RULE_VERSION,
                                     build_step01_commit_manifest,
                                     ensure_script_revision,
                                     write_step01_commit_manifest)
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    revision = ensure_script_revision(script)
    (ch / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    quality = {
        "source": "step01.quality_gate", "report_type": "quality_gate",
        "rule_version": QUALITY_RULE_VERSION,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "script_revision": revision, "duration_band": duration_band(),
        "status": "PASS", "blocking": False, "hard_count": 0,
        "visual_risk_count": 0, "blocking_categories": [], "hard": [],
        "visual_risk": [], "advisory": [], "video_advisory": [],
    }
    (ch / "step01_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = {
        "status": "ok", "input_revision": revision,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "duration_band": duration_band(),
        "audit_scope": "final_after_deterministic_fixes", "issues": [],
    }
    (ch / "step01_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = build_step01_commit_manifest(
        run_id="e2e-subset", created_at="2026-11-01T00:00:00",
        revision=revision, rule_source_fingerprint=RULE_SOURCE_FINGERPRINT,
        rule_version=QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes",
        chapter_dir=ch, status="COMMITTED", blocking=[],
        prompt_source_fingerprint="", audit_status="ok")
    write_step01_commit_manifest(ch, manifest)
    return revision


def _write_step02b_quality(ch: Path, revision: str, clips: dict) -> None:
    import hashlib
    from shared.quality_gate import QUALITY_RULE_VERSION
    outputs = {}
    for idx, clip in clips.items():
        outputs[str(idx)] = {
            "video": {"path": str(clip),
                      "sha1": hashlib.sha1(clip.read_bytes()).hexdigest()},
            "duration_sec": 2.0, "has_audio": False,
        }
    report = {
        "source": "step02b.product_acceptance", "report_type": "product_acceptance",
        "rule_version": QUALITY_RULE_VERSION, "script_revision": revision,
        "status": "PASS", "blocking": False, "missing": [],
        "outputs": outputs,
        "issues_by_shot": {str(i): [] for i in clips},
        "scope": {"shots": sorted(clips)},
    }
    (ch / "step02b_quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare(tmp_path: Path):
    chapter = Path("锦衣夜行") / "part_01_杀青州" / "chapter_001"
    root = tmp_path / "output"
    ch = root / chapter
    ch.mkdir(parents=True)
    script = {
        "chapter_index": 1,
        "characters": [],
        "shots": [
            {"index": 1, "prompt": "甲站在船头", "ff_desc": "甲站在船头",
             "lf_desc": "甲转身", "motion_desc": "甲转身", "narration_segment": "",
             "main_subject": "甲", "duration_sec": 2.0, "camera_movement": "固定机位"},
            {"index": 2, "prompt": "甲坐下", "ff_desc": "甲坐下",
             "lf_desc": "甲低头", "motion_desc": "甲低头", "narration_segment": "",
             "main_subject": "甲", "duration_sec": 2.0, "camera_movement": "固定机位"},
        ],
    }
    revision = _write_step01_artifacts(ch, script)
    clip = ch / "vimax_working" / "shots" / "1" / "video.mp4"
    _make_clip(clip)
    (ch / "clips").mkdir()
    _write_step02b_quality(ch, revision, {1: clip})
    env = {
        "OUTPUT_BASE": str(root),
        "NOVEL_NAME": "锦衣夜行", "BOOK_INDEX": "1",
        "BOOK_NAME": "杀青州", "NOVEL_CHAPTER": "1",
        "VIDEO_PROVIDER": "volcengine",
        "VOLCENGINE_I2V_MODEL": "doubao-seedance-1-0-pro-250528",
        "PYTHONPATH": str(_REPO),
    }
    return ch, env


def test_step04_subset_merge_encodes_isolated_outputs(tmp_path):
    ch, env = _prepare(tmp_path)
    proc = _run([sys.executable, str(_REPO / "step04_merge" / "main.py"),
                 "--shots", "1"], env=env)
    assert proc.returncode == 0, (proc.stdout[-1500:], proc.stderr[-1500:])

    final = ch / "final_shots_1.mp4"
    assert final.is_file() and final.stat().st_size > 20 * 1024
    assert _probe_duration(final) == pytest.approx(2.0, abs=0.35)
    assert (ch / "subtitles_shots_1.srt").is_file()

    quality = json.loads((ch / "step04_shots_1_quality.json").read_text(encoding="utf-8"))
    assert quality["status"] == "PASS"
    assert quality["scope"] == {"shots": [1], "subset": True}
    assert quality["outputs"]["final"]["duration_sec"] == pytest.approx(2.0, abs=0.35)

    # 正式产物绝不能被创建/覆盖
    assert not (ch / "final.mp4").exists()
    assert not (ch / "step04_quality.json").exists()


def test_step04_full_merge_rejects_subset_step02b_report(tmp_path):
    ch, env = _prepare(tmp_path)
    proc = _run([sys.executable, str(_REPO / "step04_merge" / "main.py")], env=env)
    assert proc.returncode != 0
    combined = (proc.stdout or "") + (proc.stderr or "")
    # 子进程日志按控制台编码输出，中文可能乱码；这里只断言可判定的结构信息：
    # 报错点名 step02b，并列出未被覆盖的 shot 2。
    assert "step02b" in combined and "[2]" in combined, combined[-800:]
    assert not (ch / "final.mp4").exists()
