"""S11–S13 离线回归：风格动态注入 / ff-lf 参考同源 / 字节哈希 / 二修守卫 / 关键帧策略.

这些用例只碰纯计算与 tmp_path 内的假图，不读生产 output、不发外部请求。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _png(path: Path, color=(0, 0, 0)) -> str:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path)
    return str(path)


# ---------------------------------------------------------------------------
# S11：风格随 VIDEO_STYLE 动态注入，画幅由调用方决定
# ---------------------------------------------------------------------------

def test_style_prefix_follows_video_style_and_canvas(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "ink_painting")
    from shared.style_prefix import style_prefix_for_canvas, style_lock_prompt

    frame = style_prefix_for_canvas("9:16")
    assert "16:9" not in frame
    assert "9:16画幅" in frame
    assert "水墨" in frame

    lock = style_lock_prompt()
    assert "水墨" in lock
    assert "写实古风真人" not in lock


def test_style_lock_keeps_gufeng_ban_list(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    from shared.style_prefix import style_lock_prompt

    lock = style_lock_prompt()
    assert "古风真人" in lock
    assert "动漫" in lock


def test_asset_prefix_keeps_landscape_ratio(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "ink_painting")
    from shared.style_prefix import asset_style_prefix

    asset = asset_style_prefix("16:9")
    assert "16:9画幅" in asset
    assert "电影宽幅" not in asset


# ---------------------------------------------------------------------------
# S12：参考图按文件字节入哈希；ff/lf 场景参考同源
# ---------------------------------------------------------------------------

def test_frame_hash_changes_when_reference_bytes_change(tmp_path):
    from shared.vimax_bridge import _get_frame_content_hash

    ref = Path(_png(tmp_path / "base.png"))
    shot = {"index": 1, "main_subject": "甲", "ff_desc": "甲站立", "lf_desc": "甲转身"}
    chars = [{"identifier": "甲", "static_features": "瘦高", "dynamic_features": "白衣"}]

    first = _get_frame_content_hash(shot, reference_paths=[str(ref)], characters=chars)
    _png(ref, (255, 255, 255))          # 路径不变、图像变了
    second = _get_frame_content_hash(shot, reference_paths=[str(ref)], characters=chars)
    assert first != second


def test_frame_hash_changes_when_character_appearance_changes(tmp_path):
    from shared.vimax_bridge import _get_frame_content_hash

    ref = Path(_png(tmp_path / "base.png"))
    shot = {"index": 1, "main_subject": "甲", "ff_desc": "甲站立"}
    a = _get_frame_content_hash(
        shot, reference_paths=[str(ref)],
        characters=[{"identifier": "甲", "static_features": "瘦高", "dynamic_features": "白衣"}])
    b = _get_frame_content_hash(
        shot, reference_paths=[str(ref)],
        characters=[{"identifier": "甲", "static_features": "矮胖", "dynamic_features": "白衣"}])
    assert a != b


def test_first_and_last_frame_share_scene_reference(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    scene = _png(tmp_path / "wide.png")
    monkeypatch.setattr(bridge, "_collect_scene_asset_refs", lambda shot: [scene])
    monkeypatch.setattr(bridge, "_collect_space_object_refs", lambda shot: [])
    monkeypatch.setattr(bridge, "_collect_portrait_refs_for_shot", lambda *a, **k: [])
    monkeypatch.setattr(bridge, "_collect_prop_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(bridge, "_frame_spec_closeup", lambda *a, **k: False)
    monkeypatch.setattr(bridge, "is_empty_scene", lambda shot, chars: False)
    monkeypatch.setattr(bridge, "shot_work_dir", lambda idx: tmp_path)

    shot = {"index": 1, "location": "湖面", "ff_desc": "甲站立", "lf_desc": "甲转身",
            "motion_desc": "甲转身"}
    ff = bridge._collect_frame_refs_for_shot(shot, [], for_frame="首帧")
    lf = bridge._collect_frame_refs_for_shot(shot, [], for_frame="尾帧")
    assert scene in ff and scene in lf


# ---------------------------------------------------------------------------
# S12：二修候选必须过严格 QC 才允许覆盖原图
# ---------------------------------------------------------------------------

class _Qc:
    def __init__(self, ok=True, error="", critical=False):
        self.ok = ok
        self.error = error
        self.has_hallucination = critical
        self.issues = []


def test_accept_doodle_rejects_skipped_qc(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_doodle_tmp.png", (255, 0, 0)))
    before = target.read_bytes()

    async def fake_qc(shot, path, kind, chars, empty, prompt=None, variation="", kf_index=None):
        shot["_qc_last"] = _Qc(ok=True, error="skipped")
        return False

    monkeypatch.setattr(bridge, "_maybe_qc_frame", fake_qc)
    accepted = asyncio.run(bridge._accept_doodle_refinement(
        str(candidate), str(target), {"index": 1}, "first_frame", [], False, "p"))
    assert accepted is False
    assert target.read_bytes() == before


def test_accept_doodle_rejects_failed_qc(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_doodle_tmp.png", (255, 0, 0)))
    before = target.read_bytes()

    async def fake_qc(shot, path, kind, chars, empty, prompt=None, variation="", kf_index=None):
        shot["_qc_last"] = _Qc(ok=False, error="timeout")
        return False

    monkeypatch.setattr(bridge, "_maybe_qc_frame", fake_qc)
    accepted = asyncio.run(bridge._accept_doodle_refinement(
        str(candidate), str(target), {"index": 1}, "first_frame", [], False, "p"))
    assert accepted is False
    assert target.read_bytes() == before


def test_qc_repair_routes_through_strict_acceptance(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge
    import shared.image_qc as image_qc

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_repair.png", (255, 0, 0)))
    before = target.read_bytes()
    calls = {"accept": 0}

    async def fake_doodle(*a, **k):
        return str(candidate)

    async def fake_accept(*a, **k):
        calls["accept"] += 1
        return False

    monkeypatch.setattr(bridge, "_doodle_refine_first_frame", fake_doodle)
    monkeypatch.setattr(bridge, "_accept_doodle_refinement", fake_accept)
    # 16x16 假图 < _validate_frame 的 5KB 下限，这里只验证路由
    monkeypatch.setattr(bridge, "_validate_frame", lambda *a, **k: True)
    monkeypatch.setattr(image_qc, "build_repair_plan", lambda *a, **k: {
        "action": "edit", "bbox_ratio": (0.1, 0.1, 0.9, 0.9),
        "edit_prompt": "fix", "matched_patterns": [], "area_pct": 10})

    ok = asyncio.run(bridge._try_qc_repair(
        None, {"index": 1, "_qc_last": object()}, str(target), "first_frame", [], False, 1))
    assert ok is False
    assert calls["accept"] == 1
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# S12：拼图落盘按内容寻址，同 tag 不同内容不会互相覆盖
# ---------------------------------------------------------------------------

def test_reference_sheet_names_are_content_addressed(tmp_path):
    from shared.ref_compose import fit_flat

    a = _png(tmp_path / "a.png", (0, 0, 0))
    b = _png(tmp_path / "b.png", (10, 10, 10))
    c = Path(_png(tmp_path / "c.png", (20, 20, 20)))
    sheets = tmp_path / "sheets"

    first = fit_flat([a, b, str(c)], 2, sheets, "shot1_ff")
    _png(c, (200, 200, 200))
    second = fit_flat([a, b, str(c)], 2, sheets, "shot1_ff")

    assert len(first) == len(second) == 2
    assert first[-1] != second[-1]
    assert Path(first[-1]).exists() and Path(second[-1]).exists()


# ---------------------------------------------------------------------------
# S13：关键帧按模型能力决定是否要求
# ---------------------------------------------------------------------------

def test_unknown_seedance_model_keeps_keyframes(monkeypatch):
    from shared.video_media_plan import video_consumes_intermediate_frames

    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-9-9-unknown")
    assert video_consumes_intermediate_frames() is True

    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    assert video_consumes_intermediate_frames() is False


def test_non_seedance_provider_keeps_keyframes(monkeypatch):
    from shared.video_media_plan import video_consumes_intermediate_frames

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    assert video_consumes_intermediate_frames() is True


# ---------------------------------------------------------------------------
# S11：输入敏感词 400 必须两级降级（sanitize + soften）
# ---------------------------------------------------------------------------

def test_input_sensitive_retry_applies_both_levels():
    from shared.content_safety import input_sensitive_retry_prompt

    revised = input_sensitive_retry_prompt("他握刀刺向听香，鲜血溅出")
    assert "指向" in revised          # soften_input_text
    assert "暗红" in revised          # sanitize
    assert input_sensitive_retry_prompt("干净的画面") == ""


# ---------------------------------------------------------------------------
# S10R：Step 01 规则指纹只覆盖 Step 01 消费的符号
# ---------------------------------------------------------------------------

def test_rule_fingerprint_excludes_step02b_only_symbols():
    from pathlib import Path
    import step01_script_gen.quality_issues as q

    style_prefix = Path(q.__file__).resolve().parent.parent / "shared" / "style_prefix.py"
    base = q._module_symbol_digest(style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS)
    # 稳定
    assert base == q._module_symbol_digest(style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS)
    # style_lock_prompt 是 Step02b 专用；把它加进名单必须改变摘要，
    # 说明默认名单确实没有把整份文件都算进去。
    with_extra = q._module_symbol_digest(
        style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS + ("style_lock_prompt",))
    assert base != with_extra
    # Step01 真正消费的符号必须在名单里
    assert "normalize_ff_desc" in q._STEP01_STYLE_PREFIX_SYMBOLS
    assert "is_empty_scene" in q._STEP01_STYLE_PREFIX_SYMBOLS


# ---------------------------------------------------------------------------
# S13B：--shots 只收敛到目标镜头实际出场的角色
# ---------------------------------------------------------------------------

def test_units_for_indices_includes_hook_and_selected_shots():
    from shared.vimax_bridge import _units_for_indices

    script = {"hook": {"index": 0}, "shots": [{"index": 1}, {"index": 2}]}
    units = _units_for_indices(script, {0, 2})
    assert [u["index"] for u in units] == [0, 2]


def test_characters_in_units_filters_out_absent_characters():
    from shared.vimax_bridge import _characters_in_units

    characters = [{"identifier": "甲"}, {"identifier": "乙"}]
    units = [{"index": 1, "ff_desc": "甲站在船头", "lf_desc": "甲转身"}]
    assert [c["identifier"] for c in _characters_in_units(characters, units)] == ["甲"]
    assert _characters_in_units(characters, []) == []
