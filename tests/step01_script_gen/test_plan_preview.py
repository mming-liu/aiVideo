# -*- coding: utf-8 -*-
"""诊断预览工具的离线验收（**0 次模型调用、0 次外部传输**）.

覆盖：完整入口（匿名样本 a/b）、摘要不符、字体缺失、正式输出根拒绝、
声音复用与未绑定附录、长文本分页不截断、帧数守恒、片头固定 2 秒。

真实渲染使用本机 FFmpeg（项目既有依赖）；不在任何用例里调用模型服务：
`build_plan_preview` 内部安装服务构造守卫，任何构造都会计数并抛错。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                                # noqa: E402
from shared.content_verification import plan_digest_of                       # noqa: E402
from tools.probe_isolation import block_external_transport                   # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "build_plan_preview_under_test", _ROOT / "tools" / "build_plan_preview.py")
tool = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = tool
_SPEC.loader.exec_module(tool)

FONT = Path(r"C:\Windows\Fonts\simhei.ttf")
_ENV_KEYS = ("OUTPUT_BASE", "STEP01_LOG_PATH")


def _sha(text) -> str:
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@pytest.fixture()
def workdir():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    base = Path(tempfile.gettempdir()) / ("plan_preview_test_" + uuid.uuid4().hex[:8])
    base.mkdir(parents=True, exist_ok=False)
    try:
        yield base
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(base, ignore_errors=True)


def _inputs(sample_name: str, workdir: Path) -> dict:
    sample = json.loads((_ROOT / "tools" / "samples"
                         / ("p3_split_entry_%s.json" % sample_name))
                        .read_text(encoding="utf-8"))
    plan = copy.deepcopy(sample["plan"])
    view, problems = pc.view_from_plan(plan)
    if problems:
        from step01_script_gen import outline as o
        plan = o.normalize_plan_ranges(copy.deepcopy(sample["plan"]))[0]
        view, problems = pc.view_from_plan(plan)
        assert not problems, problems
    novel = str(sample["novel"])
    plan_path = workdir / ("plan_%s.json" % sample_name)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    novel_path = workdir / ("novel_%s.txt" % sample_name)
    novel_path.write_text(novel, encoding="utf-8")
    return {"plan": plan, "view": view, "novel": novel, "plan_path": plan_path,
            "novel_path": novel_path, "digest": str(plan_digest_of(plan)),
            "novel_digest": _sha(novel), "sample": sample}


def _build(inputs: dict, workdir: Path, **kw):
    with block_external_transport():
        return tool.build_plan_preview(
            inputs["plan_path"], inputs["novel_path"], workdir / "work",
            expected_plan_digest=inputs["digest"],
            expected_novel_digest=inputs["novel_digest"],
            font_path=kw.pop("font_path", FONT), width=360, height=640, **kw)


def _flat(segments, beat_uid):
    return "".join(s["text"].replace("\n", "") for s in segments
                   if s["beat_uid"] == beat_uid)


# ---------------------------------------------------------------------------
# 完整入口
# ---------------------------------------------------------------------------
def test_sample_a_full_entry_keeps_order_voices_and_text(workdir):
    inputs = _inputs("a", workdir)
    manifest = _build(inputs, workdir)

    out = Path(manifest["output"]["path"])
    assert out.is_file() and out.stat().st_size > 0
    assert manifest["diagnostic_preview"] is True
    assert manifest["guard"] == {"service_construction_attempts": 0, "model_calls": 0}
    for key in ("duration_within_one_frame", "has_audio", "decode_ok"):
        assert manifest["checks"][key] is True, (key, manifest["checks"])
    assert (manifest["output"]["width"], manifest["output"]["height"]) == (360, 640)

    # 片头固定 2 秒且计入清单
    assert manifest["segments"][0]["kind"] == "title"
    assert manifest["segments"][0]["frames"] == 48
    assert manifest["total_frames"] == sum(s["frames"] for s in manifest["segments"])

    view = inputs["view"]
    beat_uids = [str(b.get("uid")) for b in view.get("beats") or []]
    shown = [s["beat_uid"] for s in manifest["segments"] if s["kind"] == "beat"]
    assert list(dict.fromkeys(shown)) == beat_uids, "节拍顺序必须与计划一致"
    for beat in view.get("beats") or []:
        uid = str(beat.get("uid"))
        assert str(beat.get("text") or "") in _flat(manifest["segments"], uid), \
            "节拍正文不得被截断"
    for unit in ((view.get("dialogue_units") or [])
                 + (view.get("narration_units") or [])):
        uid = str(unit.get("uid"))
        assert any(s["voice_uid"] == uid for s in manifest["segments"]), uid
        assert str(unit.get("text") or "") in "".join(
            s["text"].replace("\n", "") for s in manifest["segments"]
            if s["voice_uid"] == uid), "声音正文不得被截断"

    # 预览身份：scope=plan_preview、revision=完整摘要、编号只在预览清单
    assert manifest["timeline"]["scope"] == "plan_preview"
    assert manifest["timeline"]["script_revision"] == inputs["digest"]
    assert len(inputs["digest"]) == 64
    assert manifest["timeline"]["clips"][0]["stable_id"].startswith(
        "plan_preview:%s:shot:" % inputs["digest"])

    reading = (Path(manifest["run_dir"]) / "reading_view.md").read_text(encoding="utf-8")
    story = (Path(manifest["run_dir"]) / "storyboard.md").read_text(encoding="utf-8")
    for beat in view.get("beats") or []:
        assert str(beat.get("text") or "") in reading
    assert "评审注记" in reading and "诊断预览" in story


def test_sample_b_full_entry_uses_its_own_ids(workdir):
    a = _inputs("a", workdir)
    b = _inputs("b", workdir)
    manifest = _build(b, workdir)
    uids = [str(x.get("uid")) for x in b["view"].get("beats") or []]
    assert manifest["counts"]["beats"] == len(uids) == 5
    assert manifest["counts"]["voice_source_entries"] == 2
    assert manifest["counts"]["voice_beat_displays"] == 2
    text = "".join(s["text"] for s in manifest["segments"])
    assert "是是是" not in text, "不得把样本 a 的内容带进样本 b"


# ---------------------------------------------------------------------------
# 输入拒绝（不产生任何预览目录）
# ---------------------------------------------------------------------------
def test_plan_digest_mismatch_stops_before_any_output(workdir):
    inputs = _inputs("a", workdir)
    with pytest.raises(tool.PreviewError) as excinfo:
        tool.build_plan_preview(
            inputs["plan_path"], inputs["novel_path"], workdir / "work",
            expected_plan_digest="0" * 64,
            expected_novel_digest=inputs["novel_digest"], font_path=FONT)
    assert excinfo.value.code == "digest_mismatch"
    assert not list((workdir / "work").glob("**/plan_preview_*"))


def test_missing_font_stops_before_any_output(workdir):
    inputs = _inputs("a", workdir)
    with pytest.raises(tool.PreviewError) as excinfo:
        tool.build_plan_preview(
            inputs["plan_path"], inputs["novel_path"], workdir / "work",
            expected_plan_digest=inputs["digest"],
            expected_novel_digest=inputs["novel_digest"],
            font_path=workdir / "nope.ttf")
    assert excinfo.value.code == "font_rejected"
    assert not list((workdir / "work").glob("**/plan_preview_*"))


def test_formal_output_root_is_refused(workdir):
    inputs = _inputs("a", workdir)
    with pytest.raises(tool.PreviewError) as excinfo:
        tool.build_plan_preview(
            inputs["plan_path"], inputs["novel_path"], _ROOT / "output" / "video",
            expected_plan_digest=inputs["digest"],
            expected_novel_digest=inputs["novel_digest"], font_path=FONT)
    assert excinfo.value.code == "work_root_in_formal_output"


# ---------------------------------------------------------------------------
# 纯规划规则：分页 / 帧数 / 复用与附录
# ---------------------------------------------------------------------------
def _fake_view(beats, voices):
    return {"beats": beats, "dialogue_units": voices.get("dialogue", []),
            "narration_units": voices.get("narration", []),
            "frozen_facts": [], "fact_bindings": []}


def test_voice_reuse_is_shown_each_time_and_unbound_goes_to_appendix():
    view = _fake_view(
        [{"uid": "b01", "text": "动作一", "voice_ref": {"kind": "dialogue", "uid": "d01"}},
         {"uid": "b02", "text": "动作二", "voice_ref": {"kind": "dialogue", "uid": "d01"}},
         {"uid": "b03", "text": "纯画面", "voice_ref": {"kind": "visual", "uid": "b03"}}],
        {"dialogue": [{"uid": "d01", "text": "同一句台词"}],
         "narration": [{"uid": "n99", "text": "没有节拍引用的旁白"}]})
    plan_spec = tool._build_segments(
        view, measure=lambda s: len(s) * 10, max_width=100, lines_per_page=5,
        voice_seconds_fn=lambda entries: 2.0, visual_sec=3.0, fps=24)
    stats = plan_spec["voice_stats"]
    assert stats["beat_displays"] == 2, "复用声音必须逐次展示，不去重"
    assert stats["reused"] == {"d01": ["b01", "b02"]}
    assert stats["unbound"] == ["n99"]
    kinds = [s["kind"] for s in plan_spec["segments"]]
    assert kinds[0] == "title"
    assert "appendix_header" in kinds and kinds[-1] == "appendix"
    assert stats["appendix_displays"] == 1


def test_long_text_paginates_without_truncation_and_reads_long_enough():
    long_action = "画" * 260
    long_voice = "白" * 300
    view = _fake_view(
        [{"uid": "b01", "text": long_action,
          "voice_ref": {"kind": "narration", "uid": "n01"}}],
        {"dialogue": [], "narration": [{"uid": "n01", "text": long_voice}]})
    plan_spec = tool._build_segments(
        view, measure=lambda s: len(s), max_width=12, lines_per_page=4,
        voice_seconds_fn=lambda entries: 8.0, visual_sec=3.0, fps=24,
        reading_cps=5.0)
    beat_pages = [s for s in plan_spec["segments"] if s["kind"] == "beat"]
    assert len(beat_pages) > 1, "长文本必须分页"
    assert all(s["frames"] >= 1 for s in beat_pages)
    assert sum(s["frames"] for s in beat_pages) >= round(8.0 * 24), \
        "阅读停留不得短于发声估时"
    assert all(s["voice_total_sec"] == 8.0 for s in beat_pages), \
        "发声估时仍单独保留，不被阅读停留覆盖"
    body = "".join(s["text"].replace("\n", "") for s in beat_pages)
    assert long_action in body and long_voice in body, "正文不得被截断"


def test_reading_dwell_expands_a_short_voice_page():
    text = "字" * 120
    view = _fake_view(
        [{"uid": "b01", "text": "动作",
          "voice_ref": {"kind": "narration", "uid": "n01"}}],
        {"dialogue": [], "narration": [{"uid": "n01", "text": text}]})
    plan_spec = tool._build_segments(
        view, measure=lambda s: len(s), max_width=200, lines_per_page=20,
        voice_seconds_fn=lambda entries: 3.0, visual_sec=3.0, fps=24,
        reading_cps=5.0)
    seg = [s for s in plan_spec["segments"] if s["kind"] == "beat"][0]
    assert seg["dwell_basis"] == "reading"
    assert seg["reading_frames"] == seg["frames"]
    assert seg["frames"] > round(3.0 * 24), "阅读量必须进入停留时间"
    assert seg["voice_total_sec"] == 3.0


def test_wrap_avoids_punctuation_at_line_edges():
    lines = tool._wrap_lines("他说。“好”（继续）", max_width=4, measure=len)
    for line in lines:
        assert not line or line[0] not in "，。、；：？！）", line
        assert not line or line[-1] not in "（【《「", line


def test_title_lines_fit_within_canvas():
    sizes = tool._font_sizes(720)
    margin = sizes["margin"]
    max_width = 720 - 2 * margin
    font, wrapped = tool._fit_title(
        ["剧情预览（诊断）", "计划 %s… / 原文 %s…" % ("a" * 64, "b" * 64)],
        FONT, sizes, max_width)
    assert wrapped and all(font.getlength(line) <= max_width for line in wrapped)


def test_frame_allocation_is_conserved_and_at_least_one_per_page():
    assert sum(tool._allocate_frames(48, [["a"], ["b"], ["c"]])) == 48
    assert min(tool._allocate_frames(48, [["a"], ["b"], ["c"]])) >= 1
    alloc = tool._allocate_frames(2, [["a"], ["b"], ["c"]])
    assert sum(alloc) == 3 and min(alloc) == 1


# ---------------------------------------------------------------------------
# 画面与失败保留
# ---------------------------------------------------------------------------
def test_rendered_pages_have_visible_text(workdir):
    from PIL import Image, ImageStat
    inputs = _inputs("a", workdir)
    manifest = _build(inputs, workdir)
    pages = sorted((Path(manifest["run_dir"]) / "frames").glob("seg_*.png"))
    assert pages, "每段必须留下一页画面"
    for page in pages:
        image = Image.open(page).convert("RGB")
        assert min(ImageStat.Stat(image).stddev) > 3, "%s 疑似空白" % page.name
        colors = image.getcolors(1_000_000)
        assert any(c[0] > 200 and 100 < c[1] < 190 and c[2] < 110
                   for _, c in colors), "%s 缺少诊断横幅" % page.name


def test_render_failure_keeps_previous_preview(workdir, monkeypatch):
    inputs = _inputs("a", workdir)
    first = _build(inputs, workdir)
    first_path = Path(first["output"]["path"])
    assert first_path.is_file()

    from step04_merge.backends import ffmpeg as backend

    def _boom(*_a, **_k):
        raise RuntimeError("stub render failure")

    monkeypatch.setattr(backend, "render", _boom)
    with pytest.raises(RuntimeError):
        _build(inputs, workdir)

    assert first_path.is_file(), "失败不得替换已有成功预览"
    runs = sorted((workdir / "work").glob("**/plan_preview_*"))
    assert len(runs) == 2
    assert any((run / "failure.json").is_file() for run in runs), "失败阶段必须留档"
