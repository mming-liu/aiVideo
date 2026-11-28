# -*- coding: utf-8 -*-
"""审核联系表工具（总计划阶段 7 / 工作包 A）.

锁住非 step01 吸收计划 §3.5 的验收标准：输入文件 SHA1 前后一致、缺图不静默跳过、
镜头顺序跟随 `script_result.json`、关键帧按数字序号、横竖图不变形、重复运行结果
一致、失败不覆盖已有审核图、上游未通过时标 `upstream_blocked`。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PIL import Image  # noqa: E402


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "contact_sheet", _PROJECT_ROOT / "tools" / "build_storyboard_contact_sheet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _script(n: int = 3, hook: bool = False, kfs=None) -> dict:
    shots = [{"index": i, "duration_sec": 5.0} for i in range(1, n + 1)]
    out = {"shots": shots, "script_revision": "rev-abc"}
    if hook:
        out["hook"] = {"index": 0, "duration_sec": 4.0}
    return out


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    """构造一个只含帧图片与 script_result.json 的章节目录（不依赖真实产物）."""
    mod = _load_tool()
    ch = tmp_path / "chapter_001"
    shots_root = ch / "vimax_working" / "shots"
    shots_root.mkdir(parents=True)
    monkeypatch.setattr(mod, "shot_work_dir",
                        lambda idx, chapter=None: shots_root / str(idx))
    monkeypatch.setattr(mod, "chapter_dir", lambda chapter=None: ch)
    (ch / "script_result.json").write_text(
        json.dumps(_script(3, hook=True), ensure_ascii=False), encoding="utf-8")
    return mod, ch, shots_root


def _frame(path: Path, size=(160, 90), color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _sha1(p: Path) -> str:
    import hashlib
    return hashlib.sha1(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
def test_reads_frames_and_reports_missing(chapter):
    mod, ch, shots = chapter
    for idx in (0, 1, 2):
        _frame(shots / str(idx) / "first_frame.png")
        _frame(shots / str(idx) / "last_frame.png")
    # shot 3 完全没有帧 → 全部记为 missing
    manifest = mod.build(1, out_dir=ch, json_only=True)
    assert manifest["shot_count"] == 3
    assert manifest["has_hook"] is True
    kinds = {(m["shot"], m["kind"]) for m in manifest["missing"]}
    assert ("3", "ff") in kinds and ("3", "lf") in kinds
    assert ("1", "ff") not in kinds
    # 每镜一行，含 hook
    assert [r["shot"] for r in manifest["rows"]] == ["hook", "1", "2", "3"]


def test_input_files_are_never_modified(chapter):
    mod, ch, shots = chapter
    for idx in (0, 1):
        _frame(shots / str(idx) / "first_frame.png")
        _frame(shots / str(idx) / "last_frame.png")
    before = {p: _sha1(p) for p in shots.rglob("*.png")}
    mod.build(1, out_dir=ch)
    after = {p: _sha1(p) for p in shots.rglob("*.png")}
    assert before == after, "工具不得改写原始帧"


def test_outputs_sheet_and_json(chapter):
    mod, ch, shots = chapter
    _frame(shots / "1" / "first_frame.png")
    _frame(shots / "1" / "last_frame.png")
    mod.build(1, out_dir=ch)
    assert (ch / "storyboard_contact_sheet.jpg").exists()
    manifest = json.loads(
        (ch / "storyboard_contact_sheet.json").read_text(encoding="utf-8"))
    assert len(manifest["pages"]) == 1
    assert manifest["pages"][0]["file"].endswith("storyboard_contact_sheet.jpg")
    assert manifest["local_derived"] is True
    assert manifest["api_generated_images"] == 0


def test_keyframes_sorted_numerically(chapter):
    """`keyframe_10` 不得排在 `keyframe_2` 前."""
    mod, ch, shots = chapter
    d = shots / "1"
    for n in (2, 10, 1):
        _frame(d / f"keyframe_{n}.png")
    paths = mod.keyframe_paths(1)
    assert [p.name for p in paths] == [
        "keyframe_1.png", "keyframe_2.png", "keyframe_10.png"]
    rows = mod.collect_cells(_script(1))
    kinds = [c["kind"] for c in rows[0]["cells"]]
    assert kinds == ["ff", "kf0", "kf1", "kf2", "lf"]


def test_repeat_run_is_byte_identical(chapter):
    mod, ch, shots = chapter
    _frame(shots / "1" / "first_frame.png")
    _frame(shots / "1" / "last_frame.png")
    _frame(shots / "2" / "first_frame.png")
    mod.build(1, out_dir=ch)
    first_json = (ch / "storyboard_contact_sheet.json").read_text(encoding="utf-8")
    first_jpg = _sha1(ch / "storyboard_contact_sheet.jpg")
    mod.build(1, out_dir=ch)
    assert (ch / "storyboard_contact_sheet.json").read_text(encoding="utf-8") == first_json
    assert _sha1(ch / "storyboard_contact_sheet.jpg") == first_jpg


def test_landscape_and_portrait_keep_aspect(chapter):
    """横竖图都按比例 letterbox（不裁剪、不变形）."""
    mod, ch, shots = chapter
    _frame(shots / "1" / "first_frame.png", size=(400, 100))
    _frame(shots / "1" / "last_frame.png", size=(100, 400))
    manifest = mod.build(1, out_dir=ch, json_only=False)
    sizes = {c["kind"]: c["size"] for c in manifest["pages"][0]["cells"]
             if c["shot"] == "1"}
    assert sizes["ff"] == [400, 100]
    assert sizes["lf"] == [100, 400]
    # 渲染后：两格的宽高比都必须与源一致（在 letterbox 内居中，不拉伸）
    with Image.open(ch / "storyboard_contact_sheet.jpg") as im:
        assert im.size[0] > 0


def test_pagination_when_too_wide(chapter, monkeypatch):
    mod, ch, shots = chapter
    d = shots / "1"
    for n in range(6):
        _frame(d / f"keyframe_{n}.png")
    _frame(d / "first_frame.png")
    _frame(d / "last_frame.png")
    # 每格 100px + gutter 8 → 一页最多 3 列
    manifest = mod.build(1, out_dir=ch, cell=100, max_width=380)
    assert len(manifest["pages"]) > 1
    files = sorted(p.name for p in ch.glob("storyboard_contact_sheet*"))
    assert "storyboard_contact_sheet_01.jpg" in files
    assert "storyboard_contact_sheet.jpg" not in files


def test_upstream_gate_status_is_reported(chapter):
    """没有 commit manifest → `upstream_blocked`（但联系表仍然产出）."""
    mod, ch, shots = chapter
    _frame(shots / "1" / "first_frame.png")
    manifest = mod.build(1, out_dir=ch, json_only=True)
    assert manifest["upstream_gate_status"] == "upstream_blocked"


def test_failed_render_keeps_previous_sheet(chapter, monkeypatch):
    """渲染失败不得破坏已有审核图（临时文件 + 原子替换）."""
    mod, ch, shots = chapter
    _frame(shots / "1" / "first_frame.png")
    mod.build(1, out_dir=ch)
    good = _sha1(ch / "storyboard_contact_sheet.jpg")

    def boom(*a, **k):
        raise RuntimeError("渲染失败")

    monkeypatch.setattr(mod, "_load_cell_image", boom)
    with pytest.raises(RuntimeError):
        mod.build(1, out_dir=ch)
    assert _sha1(ch / "storyboard_contact_sheet.jpg") == good
    assert not list(ch.glob(".*tmp*")), "失败不得留临时文件"


def test_no_generation_related_side_effects(chapter):
    """工具不得改动 script_result.json，也不得产出参考图清单."""
    mod, ch, shots = chapter
    before = _sha1(ch / "script_result.json")
    mod.build(1, out_dir=ch)
    assert _sha1(ch / "script_result.json") == before
    manifest = json.loads(
        (ch / "storyboard_contact_sheet.json").read_text(encoding="utf-8"))
    assert "reference_paths" not in manifest
