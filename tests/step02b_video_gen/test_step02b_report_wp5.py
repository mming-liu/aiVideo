# -*- coding: utf-8 -*-
"""WP5：step02b 完成报告走**真实生产入口**（隔离根，零模型调用）.

不手填 PASS：调用 step02_video_gen.video_prompt_quality.write_video_product_quality
在临时 chapter_dir 里生成 step02b_quality.json，再用 require_gate 从正式装载入口
读回，核对 revision、逐镜摘要与路径。
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

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")

WIDTH, HEIGHT, FPS = 1440, 2560, 24


def _formal_script() -> dict:
    hits = sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式稿输入不唯一或缺失: %d 份" % len(hits))
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _clip(path: Path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=gray:s=%dx%d:r=%d:d=%.4f"
         % (WIDTH, HEIGHT, FPS, frames / FPS),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", "noise=alls=40:allf=t",   # 真实码流大小（<20KB 会被判 missing）
         "-frames:v", str(frames), "-shortest", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True)


def test_step02b_report_is_produced_by_the_real_writer(tmp_path, monkeypatch):
    from shared import paths

    chapter = tmp_path / "chapter_001"
    chapter.mkdir()
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: chapter)

    script = _formal_script()
    script.setdefault("chapter_index", 1)
    from shared.quality_gate import ensure_script_revision
    rev = ensure_script_revision(script)
    (chapter / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8")

    work = tmp_path / "vimax_working"
    for shot in script["shots"]:
        _clip(work / "shots" / str(shot["index"]) / "video.mp4",
              int(round(float(shot["duration_sec"]) * FPS)))

    from step02_video_gen.video_prompt_quality import write_video_product_quality
    # 该模块在**导入时**就绑定了 `from shared.paths import chapter_dir`；
    # 如果前面的测试已经导入过它，只 monkeypatch `shared.paths.chapter_dir`
    # 不会影响模块内的旧绑定，报告会写到真实章节目录（测试顺序依赖）。
    # 这里把模块内绑定也指向隔离目录。
    import step02_video_gen.video_prompt_quality as _vq
    monkeypatch.setattr(_vq, "chapter_dir", lambda *a, **k: chapter)
    payload = write_video_product_quality(script, work)
    assert payload["status"] == "PASS", payload.get("missing")
    assert payload["report_type"] == "product_acceptance"
    assert str(payload.get("script_revision") or rev) == rev

    on_disk = json.loads((chapter / "step02b_quality.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "PASS"
    assert sorted(on_disk["outputs"].keys()) == sorted(
        str(s["index"]) for s in script["shots"])
    for idx, item in on_disk["outputs"].items():
        assert Path(item["video"]["path"]).is_file()
        assert item["video"]["sha1"]
        assert item["duration_sec"] > 0
        assert item["has_audio"] is True
    # 从正式装载入口读回：门禁必须接受这份报告（身份 + 产物摘要）
    from shared.quality_gate import require_gate
    back = require_gate(chapter / "step02b_quality.json", script, stage="step02b",
                        required_report_type="product_acceptance",
                        verify_outputs=True)
    assert back["status"] == "PASS"
