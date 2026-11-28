"""A 包渲染层判别性测试：帧计划 / 底座探测 / seek 取帧 / PNG 校验 / FFmpeg 编码。

本机没有 Playwright + Chromium（计划 §A1 明确不自动下载），所以：
  * **真实底座**用例断言"如实 not_run"（结构化 unavailable，不伪造成功）；
  * **整条链路**用例注入确定性取帧桩（`build_fixtures.DeterministicStubShooter`），
    跑通「帧计划 → 取帧 → 校验 → 编码 → 提交」——这是接线证据，不是模板渲染证据。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.media_contract import MERGE_FPS, quantize_frame_count
from shared.motion_render import render as R
from shared.motion_render import validate as V
from tests.enhancements import build_fixtures as bf

FIXTURE_ROOT = Path(os.environ.get("OUTPUT_BASE", Path(__file__).parent.parent / ".pytest_tmp")) \
    / f"motion_fixtures_{os.getpid()}"


@pytest.fixture(scope="module")
def built() -> dict:
    return bf.ensure_fixtures(FIXTURE_ROOT, scale=0.25)


@pytest.fixture(scope="module")
def title_case(built):
    bundle = json.loads(Path(built["cases"]["content_a__title"]["bundle"]).read_text(encoding="utf-8"))
    doc = V.load_fixture_doc(Path(built["asset_root"]), bundle["source"]["fixture_id"])
    report = V.validate_motion_bundle(bundle, asset_root=Path(built["asset_root"]), source_doc=doc)
    assert report.ok, report.problems
    spec = bundle["specs"][0]
    layout = V.layout_spec(spec, bundle["canvas"])
    return bundle, spec, layout, report


# ---------------------------------------------------------------- 帧计划

def test_frame_plan_is_zero_based_and_ends_one_frame_before_duration():
    times = R.frame_plan(3.0, MERGE_FPS)
    assert len(times) == 72 == quantize_frame_count(3.0, MERGE_FPS)
    assert times[0] == 0.0
    assert times[-1] == pytest.approx((72 - 1) / 24.0)
    assert times[-1] < 3.0, "seek 终点不得越过/重复采集量化时长"
    assert R.seek_endpoint(3.0, MERGE_FPS) == pytest.approx(71 / 24)


def test_frame_count_uses_single_quantizer():
    for duration in (0.5, 2.0, 2.98, 3.02, 6.0):
        assert R.frame_count_for(duration) == quantize_frame_count(duration)
    with pytest.raises(Exception):
        R.frame_count_for(0.0)


def test_frame_plan_is_monotonic_and_spaced_by_one_frame():
    times = R.frame_plan(5.0, MERGE_FPS)
    assert all(b - a == pytest.approx(1 / 24) for a, b in zip(times, times[1:]))


# ---------------------------------------------------------------- 底座探测

def test_runtime_status_is_probe_only(built):
    status = R.runtime_status()
    assert set(status) >= {"available", "missing", "node", "playwright", "chromium",
                           "install_hint", "download_facts"}
    assert status["node"]["path"], "node 应该能定位（本机已安装）"
    # 探测不得安装任何东西
    assert not (R.motion_runtime_dir() / "node_modules").exists(), \
        "探测阶段不得产生 node_modules（不自动下载依赖）"


def test_download_facts_are_recorded_not_guessed():
    facts = R.DOWNLOAD_FACTS
    assert facts["chromium_win64_zip_bytes"] > 100_000_000
    assert facts["chromium_url"].startswith("https://")
    assert facts["playwright_npm_unpacked_bytes"] > 1_000_000
    assert facts["note"], "必须写明是元数据核实而非实下载"


def test_real_shooter_reports_unavailable_without_fabricating(tmp_path, title_case):
    bundle, spec, layout, _ = title_case
    shooter = R.PlaywrightShooter()
    if shooter.available():
        pytest.skip("本机已装 Playwright：真实渲染路径应由 demo/render 实跑覆盖")
    result = R.render_spec(spec, bundle["canvas"], layout, work_dir=tmp_path / "s",
                           asset_paths={}, shooter=shooter)
    assert result.status == R.STATUS_UNAVAILABLE
    assert result.frame_count == 0
    assert [p["code"] for p in result.problems] == ["render_runtime_unavailable"]
    assert not (tmp_path / "s" / "frames" / "frame_00000.png").exists(), "缺底座不得产出帧"
    # HTML 是真正的产物（模板已写出），但帧没有 ⇒ 记录 not_run
    assert (tmp_path / "s" / f"{spec['spec_id']}.html").is_file()


# ---------------------------------------------------------------- 取帧（桩）与乱序

def _render_with_stub(tmp_path, title_case, *, mode="animated", order=None, encode=False):
    bundle, spec, layout, _ = title_case
    shooter = bf.DeterministicStubShooter()
    result = R.render_spec(spec, bundle["canvas"], layout, work_dir=tmp_path / spec["spec_id"],
                           asset_paths={}, shooter=shooter, mode=mode, encode_mp4=encode)
    if order is not None:
        # 乱序再取一遍：同一 t 的 PNG 必须逐字节一致
        times = R.frame_plan(float(spec["duration_sec"]), MERGE_FPS)
        work2 = tmp_path / f"{spec['spec_id']}_shuffled"
        requests = [R.FrameRequest(index=i, t_sec=times[i], out_path=work2 / f"f_{i}.png")
                    for i in order]
        spec2 = R.ShooterSpec(html_path=work2 / "spec.html", width=int(bundle["canvas"]["width"]),
                              height=int(bundle["canvas"]["height"]), fps=MERGE_FPS, mode=mode,
                              requests=requests)
        bf.DeterministicStubShooter().shoot(spec2)
        return result, {i: work2 / f"f_{i}.png" for i in order}
    return result, None


def test_stub_render_writes_rgba_frames_with_expected_count(tmp_path, title_case):
    bundle, spec, layout, _ = title_case
    result, _ = _render_with_stub(tmp_path, title_case)
    assert result.status == R.STATUS_PASS, result.problems
    frames = sorted(Path(result.frames_dir).glob("frame_*.png"))
    assert len(frames) == R.frame_count_for(float(spec["duration_sec"]))
    assert frames[0].name == "frame_00000.png"
    from PIL import Image
    with Image.open(frames[0]) as im:
        assert im.mode == "RGBA"
        assert im.size == (int(bundle["canvas"]["width"]), int(bundle["canvas"]["height"]))
        assert im.getchannel("A").getextrema()[0] == 0, "必须真的写了透明像素"


def test_out_of_order_seek_is_byte_identical(tmp_path, title_case):
    """乱序 seek 判别：同一 t 的 PNG 与顺序取帧完全一致（纯时间函数）。"""
    bundle, spec, layout, _ = title_case
    n = R.frame_count_for(float(spec["duration_sec"]))
    order = list(range(n))[::-1]
    result, shuffled = _render_with_stub(tmp_path, title_case, order=order)
    assert result.status == R.STATUS_PASS
    for i, path in shuffled.items():
        assert R.frames_identical(Path(result.frames_dir) / f"frame_{i:05d}.png", path), \
            f"帧 {i} 乱序取帧结果不一致（seek 不是纯时间函数）"


def test_static_mode_holds_state(tmp_path, title_case):
    result, _ = _render_with_stub(tmp_path, title_case, mode="static")
    assert result.status == R.STATUS_PASS
    assert result.frame_count == 72
    from PIL import Image
    first = Image.open(Path(result.frames_dir) / "frame_00000.png")
    last = Image.open(Path(result.frames_dir) / "frame_00071.png")
    assert first.tobytes() == last.tobytes(), "静态对照必须定格（首末帧一致）"


# ---------------------------------------------------------------- PNG 校验判别性

def test_verify_frame_sequence_detects_each_defect(tmp_path):
    from PIL import Image
    w, h = 32, 48
    good = tmp_path / "frame_00000.png"
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.putpixel((1, 1), (255, 0, 0, 255))
    img.save(good)
    assert R.verify_frame_sequence([good], width=w, height=h, expected_count=1) == []

    wrong_size = tmp_path / "frame_00001.png"
    Image.new("RGBA", (w + 1, h), (0, 0, 0, 0)).save(wrong_size)
    codes = {p["code"] for p in R.verify_frame_sequence([wrong_size], width=w, height=h,
                                                        expected_count=1)}
    assert "frame_size_mismatch" in codes

    rgb = tmp_path / "frame_00002.png"
    Image.new("RGB", (w, h), (0, 0, 0)).save(rgb)
    codes = {p["code"] for p in R.verify_frame_sequence([rgb], width=w, height=h,
                                                        expected_count=1)}
    assert "frame_not_rgba" in codes

    opaque = tmp_path / "frame_00003.png"
    Image.new("RGBA", (w, h), (5, 5, 5, 255)).save(opaque)
    codes = {p["code"] for p in R.verify_frame_sequence([opaque], width=w, height=h,
                                                        expected_count=1)}
    assert "frame_alpha_opaque" in codes, "只查通道数不够，必须查 alpha 是否真写了"

    codes = {p["code"] for p in R.verify_frame_sequence([good], width=w, height=h,
                                                        expected_count=3)}
    assert "frame_count_mismatch" in codes


# ---------------------------------------------------------------- FFmpeg 编码

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机无 ffmpeg")
def test_encode_display_mp4_terminates_and_has_no_audio(tmp_path, title_case):
    """回归：lavfi 背景是无限源，overlay 不写 shortest/frames 会**永远编下去**。"""
    import time
    result, _ = _render_with_stub(tmp_path, title_case)
    assert result.status == R.STATUS_PASS
    frames_dir = Path(result.frames_dir)
    out = tmp_path / "display.mp4"
    started = time.time()
    problems = R.encode_display_mp4(frames_dir, out, width=270, height=480, fps=MERGE_FPS)
    elapsed = time.time() - started
    assert problems == [], problems
    assert elapsed < 120, f"编码必须终止（实际 {elapsed:.1f}s）"
    probe = shutil.which("ffprobe")
    if not probe:
        pytest.skip("无 ffprobe，跳过媒体结构断言")
    info = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(out)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    data = json.loads(info.stdout)
    kinds = [s["codec_type"] for s in data["streams"]]
    assert kinds == ["video"], f"展示片必须无声：{kinds}"
    video = data["streams"][0]
    assert video["pix_fmt"] == "yuv420p"
    frames = len(list(frames_dir.glob("frame_*.png")))
    assert abs(float(data["format"]["duration"]) - frames / MERGE_FPS) < 0.2


# ---------------------------------------------------------------- HTML 模板契约

def _strip_js_comments(text: str) -> str:
    """去掉注释后再查禁用 API（注释里写"不使用某 API"不算违规）。"""
    import re
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_build_spec_html_is_self_contained_and_pure(title_case):
    import re

    bundle, spec, layout, _ = title_case
    html = R.build_spec_html(spec, bundle["canvas"], layout, asset_map={"font_sans": "assets/font_sans"})
    code = _strip_js_comments(html)
    for bad in ("requestAnimationFrame", "setTimeout", "setInterval", "Math.random",
                "Date.now", "performance.now", "fetch(", "XMLHttpRequest"):
        assert bad not in code, f"模板不得含 {bad}（禁自主播放/随机/计时/外网）"
    urls = set(re.findall(r"https?://[^\s\"'()]+", code))
    # 唯一允许的 URL 是 SVG 命名空间标识符（从不发起请求）
    assert urls <= {"http://www.w3.org/2000/svg"}, f"模板含外部 URL: {urls}"
    assert "<script src=" not in code and "<link " not in code
    assert "Array.from" in html, "浏览器侧必须用 Array.from 做 code point 切片"
    assert "__MOTION_MODEL__" not in html and "__MOTION_ENGINE__" not in html
    model = json.loads(html.split('<script type="application/json" id="motion-model">')[1]
                       .split("</script>")[0])
    assert model["frame_count"] == R.frame_count_for(float(spec["duration_sec"]))
    assert model["fps"] == MERGE_FPS
    line = model["segments"][0]["lines"][0]
    assert {"cp_start", "cp_end"} <= set(line), "行必须带 code point 区间（浏览器不自行断行）"
    assert model["segments"][0]["cp_len"] == len(spec["source_text"])
    assert model["color"][3] == pytest.approx(1.0)


def test_template_files_exist_and_are_original():
    tdir = R.template_dir()
    for name in ("spec.html", "motion.js"):
        assert (tdir / name).is_file(), f"模板缺失: {name}"
    assert (R.motion_runtime_dir() / "shoot.mjs").is_file()
    assert (R.motion_runtime_dir() / "package-lock.json").is_file()


# ---------------------------------------------------------------- 联系表 / 关键帧

def test_contact_sheet_and_keyframes(tmp_path, title_case):
    bundle, spec, layout, _ = title_case
    result, _ = _render_with_stub(tmp_path, title_case)
    frames_dir = Path(result.frames_dir)
    sheet = tmp_path / "sheet.png"
    problems = R.compose_contact_sheet(
        [{"label": "animated", "image": frames_dir / "frame_00010.png"},
         {"label": "static", "image": frames_dir / "frame_00020.png"}],
        sheet, font_path=Path(bf.ensure_fixtures(FIXTURE_ROOT)["fonts"]["label"]))
    assert problems == []
    from PIL import Image
    with Image.open(sheet) as im:
        assert im.width > 0 and im.height > 0

    keys, problems = R.copy_keyframes(frames_dir, tmp_path / "kf", [0, 71], "case")
    assert problems == [] and len(keys) == 2
    _, problems = R.copy_keyframes(frames_dir, tmp_path / "kf", [999], "case")
    assert [p["code"] for p in problems] == ["keyframe_missing"], "缺帧要如实报，不静默跳过"

    problems = R.compose_contact_sheet([{"label": "x", "image": tmp_path / "missing.png"}],
                                       tmp_path / "s2.png",
                                       font_path=Path(bf.ensure_fixtures(FIXTURE_ROOT)["fonts"]["label"]))
    assert {"contact_item_missing", "contact_sheet_empty"} <= {p["code"] for p in problems}
