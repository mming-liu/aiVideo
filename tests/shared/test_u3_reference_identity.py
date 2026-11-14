# -*- coding: utf-8 -*-
"""U3 验收：参考图身份保真（成员区域、说明一致性、实际入口）.

从**尾帧生成入口**（shared.vimax_bridge.gen_shot_frames, frames={"lf"}）用桩
替换生成调用，捕获最终参考列表；再用真实拼图 sidecar 校验区域与说明一致。
样本为匿名的纯色图，区域中心像素即成员身份的可判定证据。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import ref_compose as rc  # noqa: E402
from shared import vimax_bridge as vb  # noqa: E402


def _png(path: Path, color) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 120), color).save(path)
    return str(path)


def _read_center(sheet: str, region) -> tuple:
    x0, y0, x1, y1 = region
    with Image.open(sheet).convert("RGB") as im:
        return im.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))


def _fixtures(tmp_path: Path, chars=3, props=1):
    colors = [(210, 40, 40), (40, 180, 60), (50, 70, 220), (200, 180, 40), (160, 60, 190)]
    portraits, names = [], []
    for i in range(chars):
        name = "角色%d" % (i + 1)
        names.append(name)
        portraits.append(_png(tmp_path / "characters_bank" / name / "front.png",
                              colors[i % len(colors)]))
    prop_paths = []
    for i in range(props):
        prop_paths.append(_png(tmp_path / "props_bank" / ("道具%d" % (i + 1)) / "prop.png",
                               colors[(i + 3) % len(colors)]))
    scene = _png(tmp_path / "scenes_bank" / "场景A" / "near.png", (120, 160, 200))
    return {"portraits": portraits, "props": prop_paths, "scene": scene,
            "names": names, "colors": colors}


def _run_tail_entry(tmp_path, monkeypatch, fx, limit=3, closeup=False):
    shot = {"index": 2, "scene": "场景A", "location": "场景A",
            "ff_desc": "人物甲站在船头", "lf_desc": "人物甲抬手",
            "motion_desc": "前2秒抬手；后2秒放下",
            "camera_movement": "固定机位", "keyframes": [],
            "frame_layouts": {"ff": [], "lf": [], "kfs": []}}
    shots_dir = tmp_path / "vimax_working" / "shots"
    shot_dir = shots_dir / "2"
    shot_dir.mkdir(parents=True, exist_ok=True)
    _png(shot_dir / "first_frame.png", (90, 90, 90))

    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda _shot: [fx["scene"]])
    monkeypatch.setattr(vb, "_collect_portrait_refs_for_shot",
                        lambda *a, **k: list(fx["portraits"]))
    monkeypatch.setattr(vb, "_collect_prop_asset_refs",
                        lambda *a, **k: list(fx["props"]))
    monkeypatch.setattr(vb, "_frame_spec_closeup", lambda *a, **k: closeup)
    monkeypatch.setattr(rc, "active_image_ref_limit", lambda: limit)

    captured = {}

    async def fake_lf(image_adapter, _shots_dir, shot_, force,
                      script_mtime=None, reference_paths=None, max_retries=1,
                      empty_scene=False, characters=None):
        captured["refs"] = list(reference_paths or [])
        return str(shot_dir / "last_frame.png")

    monkeypatch.setattr(vb, "_gen_last_frame_async", fake_lf)
    asyncio.run(vb.gen_shot_frames(shot, shots_dir, object(), None,
                                   frames={"lf"}, characters=[]))
    return captured["refs"]


def _assert_consistent(refs, limit):
    assert len(refs) <= limit
    seen = []
    for path in refs:
        layout = rc.load_ref_layout(path)
        if layout is None:
            # 非拼合表：必须是可读原图
            assert Path(path).is_file()
            continue
        assert layout["sha256"] == rc._sha256_file(path)
        assert layout["size"]
        assert layout["members"]
        for m in layout["members"]:
            assert Path(m["source"]).is_file()
            assert m["region"][0] < m["region"][2]
            seen.append(m["entity"])
    return seen


@pytest.mark.parametrize("chars,props,limit", [(1, 0, 2), (2, 1, 3), (3, 2, 5)])
def test_tail_entry_refs_and_manifest_are_consistent(tmp_path, monkeypatch, chars, props, limit):
    fx = _fixtures(tmp_path, chars=chars, props=props)
    refs = _run_tail_entry(tmp_path, monkeypatch, fx, limit=limit)
    entities = _assert_consistent(refs, limit)

    manifest = rc.build_ref_manifest(refs, numbered=False, consumer_kind="last_frame")
    assert manifest
    for path in refs:
        layout = rc.load_ref_layout(path)
        if layout is None:
            continue
        for m in layout["members"]:
            if m.get("entity"):
                assert m["entity"] in manifest, "说明缺少成员身份: %s" % m["entity"]
        if len(layout.get("members") or []) > 1:
            assert "从左至右" not in manifest


def test_region_pixels_match_member_sources(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path, chars=3, props=1)
    refs = _run_tail_entry(tmp_path, monkeypatch, fx, limit=2)
    checked = 0
    for path in refs:
        layout = rc.load_ref_layout(path)
        if not layout:
            continue
        for m in layout["members"]:
            with Image.open(m["source"]).convert("RGB") as src:
                expected = src.getpixel((src.width // 2, src.height // 2))
            got = _read_center(path, m["region"])
            assert got == expected, "%s 区域与源图不一致: %s != %s" % (
                m.get("entity"), got, expected)
            checked += 1
    assert checked >= 2


def test_nested_sheet_records_member_regions(tmp_path):
    fx = _fixtures(tmp_path, chars=2)
    inner = rc.fit_refs([("chars", fx["portraits"])], 1, tmp_path / "s1", "inner")[0]
    inner_layout = rc.load_ref_layout(inner)
    assert inner_layout and len(inner_layout["members"]) == 2

    outer, _layout = rc._atomic_reference_sheet(
        [inner, fx["scene"]], tmp_path / "s2" / "ref_sheet_outer.png", kind="all")
    outer_layout = rc.load_ref_layout(outer)
    nested = [m for m in outer_layout["members"] if m.get("nested")]
    assert nested, "嵌套拼图未记录内层成员"
    assert nested[0]["nested"]["members"], "嵌套成员区域未换算"
    assert rc.describe_ref(outer)


def test_duplicate_and_missing_sources_are_normalized(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path, chars=2)
    dup = fx["portraits"] + [fx["portraits"][0], str(tmp_path / "missing.png")]
    refs = _run_tail_entry(tmp_path, monkeypatch,
                           dict(fx, portraits=dup), limit=3)
    assert len(refs) <= 3
    for path in refs:
        assert Path(path).is_file()
    _assert_consistent(refs, 3)


def test_closeup_omission_is_recorded_with_reason(tmp_path, monkeypatch):
    fx = _fixtures(tmp_path, chars=3, props=1)
    refs = _run_tail_entry(tmp_path, monkeypatch, fx, limit=2, closeup=True)
    sheet_dir = tmp_path / "vimax_working" / "shots" / "2"
    reports = list(sheet_dir.glob("ref_compose_*.json"))
    assert reports, "缺少组合报告（省略依据必须可追溯）"
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["omissions"] and payload["omissions"][0]["reason"] == "closeup_frame"
    assert all("scenes_bank" not in p for p in refs)


def test_legacy_sheet_without_sidecar_does_not_claim_identity(tmp_path):
    legacy = tmp_path / "ref_sheet_ff_chars_甲+乙.png"
    _png(legacy, (10, 10, 10))
    label = rc.describe_ref(str(legacy))
    assert "从左至右" not in label
    assert "未记录" in label


def test_manifest_uses_regions_not_whole_image_order(tmp_path):
    fx = _fixtures(tmp_path, chars=3)
    refs = rc.fit_refs([("chars", fx["portraits"])], 1, tmp_path / "s3", "ff")
    label = rc.describe_ref(refs[0])
    assert "占 x≈" in label
    assert rc.load_ref_layout(refs[0])["kind"] == "chars"


# ---------------------------------------------------------------------------
# 人工评阅证据：拼图预览 + 成员区域（仅在给定目录时生成）
# ---------------------------------------------------------------------------
def test_write_review_preview(tmp_path, monkeypatch):
    out_dir = os.getenv("U3_EVIDENCE_DIR", "").strip()
    if not out_dir:
        pytest.skip("未指定 U3_EVIDENCE_DIR（人工评阅证据不参与常规测试）")
    fx = _fixtures(tmp_path, chars=3, props=2)
    refs = _run_tail_entry(tmp_path, monkeypatch, fx, limit=2)
    evidence = Path(out_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    record = {"refs": [], "manifest": rc.build_ref_manifest(refs, numbered=False,
                                                           consumer_kind="last_frame")}
    for path in refs:
        layout = rc.load_ref_layout(path)
        if not layout:
            record["refs"].append({"path": path, "members": []})
            continue
        img = Image.open(path).convert("RGB")
        draw = ImageDraw.Draw(img)
        for i, m in enumerate(layout["members"]):
            x0, y0, x1, y1 = m["region"]
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 0, 0), width=6)
            draw.text((x0 + 10, y0 + 10), "M%d" % i, fill=(255, 0, 0))
        dest = evidence / ("preview_%s" % Path(path).name)
        img.save(dest)
        record["refs"].append({"path": path, "preview": str(dest),
                               "members": layout["members"]})
    (evidence / "u3_review.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    assert (evidence / "u3_review.json").is_file()


# ---------------------------------------------------------------------------
# U6-FIN WP1b：参考表落点上下文（plan-only 不得重写正式 shots/N/ref_compose_*.json）
# ---------------------------------------------------------------------------
def _three_buckets(fx):
    """3 个类别、总数 5 —— 超过 limit=3，走"多类别拼表 + 写 ref_compose 清单"分支.

    单类别 `limit=1` 分支不写清单，用它测不到本次要钉的那条写路径。
    """
    return [("scene", [fx["scene"]]),
            ("chars", fx["portraits"]),
            ("props", fx["props"])]


def test_ref_dir_context_redirects_sheets_and_compose_report(tmp_path):
    """设了上下文后，参考表与 ref_compose 清单全部落上下文目录，源目录保持空.

    2026-11-14 R6 收尾实测：`step02b --plan-only` 把 `ref_compose_ff/lf.json`
    重写回正式 `vimax_working/shots/2/`（内容只差 `at` 时间戳），production
    snapshot 因此报 `production_write_detected`。拼表逻辑只认参数、不知道
    "现在是只读预检"，所以由入口设上下文来兜。
    """
    fx = _fixtures(tmp_path, chars=3)
    production_dir = tmp_path / "shots" / "2"
    production_dir.mkdir(parents=True)
    redirect = tmp_path / "candidate" / "refs" / "2"

    rc.set_ref_dir_context(tmp_path / "candidate" / "refs")
    try:
        composed = rc.fit_refs(_three_buckets(fx), 3, production_dir, "ff")
    finally:
        rc.set_ref_dir_context(None)

    assert composed, "拼表应当成功"
    report = json.loads((redirect / "ref_compose_ff.json").read_text(encoding="utf-8"))
    assert report["tag"] == "ff" and report["included"] == 5
    # 拼出来的参考表（在 redirect 下）必须为 composed 里的每一项
    sheets = [p for p in composed if Path(p).parent == redirect]
    assert sheets, "至少有一张拼合参考表应落在实验目录"
    assert all(Path(p).exists() for p in sheets)
    assert list(production_dir.iterdir()) == [], "正式目录必须一个文件都没多"


def test_ref_dir_context_is_off_by_default(tmp_path):
    """未设上下文 → 行为与历史版本一致（落调用方给的目录）."""
    fx = _fixtures(tmp_path, chars=3)
    target = tmp_path / "shots" / "1"
    composed = rc.fit_refs(_three_buckets(fx), 3, target, "lf")
    assert composed
    assert (target / "ref_compose_lf.json").is_file()
    assert any(p.name.startswith("ref_sheet_") for p in target.iterdir())
    assert rc.current_ref_dir() is None
