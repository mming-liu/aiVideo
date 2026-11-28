"""A 包 → Step04 接线契约判别性测试（`motion_overlay_plan.v1`，§A2/§A4/§15）。

S 包不解析 MotionBundle；它只消费本模块产出的绝对时间层表。因此这里的每条守卫都是
**跨包契约**：越界 / 跨镜重叠 / hook 身份 / 跨 revision / map 不得叠加 / 身份由生产者给。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from shared.motion_render import overlay as O
from shared.motion_render import validate as V
from tests.enhancements import build_fixtures as bf

FIXTURE_ROOT = Path(os.environ.get("OUTPUT_BASE", Path(__file__).parent.parent / ".pytest_tmp")) \
    / f"motion_fixtures_{os.getpid()}"


@pytest.fixture(scope="module")
def built() -> dict:
    return bf.ensure_fixtures(FIXTURE_ROOT, scale=0.25)


@pytest.fixture(scope="module")
def doc(built):
    return json.loads(Path(built["fixtures"]["content_a"]).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def windows(doc):
    return bf.shot_windows_for(doc)


@pytest.fixture(scope="module")
def title_bundle(built):
    return json.loads(Path(built["cases"]["content_a__title"]["bundle"]).read_text(encoding="utf-8"))


def _codes(report: O.OverlayPlanReport) -> set[str]:
    return {p["code"] for p in report.problems}


# ---------------------------------------------------------------- 镜头表 / 窗表

def test_shot_table_derives_identity_from_source(doc):
    table = V.shot_table(doc)
    assert table["indexes"] == [1, 2]
    assert table["has_hook"] is True
    assert table["max_index"] == 2
    assert table["revision"] == "fixture-a-0001"


def test_shot_windows_are_contiguous_and_hook_is_zero(windows):
    assert windows[0]["is_hook"] is True and windows[0]["start_sec"] == 0.0
    assert windows[1]["start_sec"] == windows[0]["end_sec"]
    assert windows[2]["start_sec"] == windows[1]["end_sec"]
    assert windows[1]["shot_id"] != windows[0]["shot_id"], "hook 与正文镜头身份必须不同"
    assert len({w["shot_id"] for w in windows.values()}) == 3


# ---------------------------------------------------------------- 绝对时间与顺序

def test_absolute_window_is_shot_start_plus_local(title_bundle, windows):
    report = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True)
    assert report.ok, report.problems
    layer = report.plan["layers"][0]
    w = windows[1]
    assert layer["abs_start_sec"] == pytest.approx(w["start_sec"] + 0.0)
    assert layer["abs_end_sec"] == pytest.approx(w["start_sec"] + 3.0)
    assert layer["shot_id"] == w["shot_id"]
    assert layer["fps"] == 24
    assert layer["frame_count"] == 72
    assert report.plan["production_admission"] is False


def test_layer_order_is_bottom_to_top_of_overlay_list(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["overlays"].append({"overlay_id": "ov_top", "spec_id": "t_alt", "shot_index": 1,
                          "start_sec": 0.0, "end_sec": 3.0})
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert report.ok, report.problems
    ids = [l["overlay_id"] for l in report.plan["layers"]]
    assert ids == ["ov_main", "ov_top"], "同镜多层按 overlays 列表底→顶"
    assert [l["layer_index"] for l in report.plan["layers"]] == [0, 1]


def test_same_shot_overlap_allowed_different_shots_rejected(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["overlays"].append({"overlay_id": "ov_shot2", "spec_id": "t_alt", "shot_index": 2,
                          "start_sec": 0.0, "end_sec": 3.0})
    ok_report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert ok_report.ok, ok_report.problems

    # 让第二镜的窗与第一镜重叠 ⇒ 跨镜最终区间重叠必须拒收
    overlapping = dict(windows)
    overlapping[2] = {"start_sec": windows[1]["start_sec"] + 1.0,
                      "end_sec": windows[1]["end_sec"] + 3.0,
                      "shot_id": "shot_2", "is_hook": False}
    report = O.build_overlay_plan(b, shot_windows=overlapping, allow_fixture=True)
    assert "cross_shot_overlay_overlap" in _codes(report)


def test_overlay_must_not_exceed_shot_window(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["overlays"][0]["start_sec"], b["overlays"][0]["end_sec"] = 5.0, 8.0  # 镜窗只有 6s
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert "overlay_exceeds_shot_window" in _codes(report)


def test_shot_window_missing_rejected(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["overlays"][0]["shot_index"] = 7
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert "shot_window_missing" in _codes(report)


# ---------------------------------------------------------------- 身份：hook / 正文 / 重编号

def test_hook_is_an_independent_identity(title_bundle, windows):
    """三个 golden：hook、正文 1、以及"0 被当成正文"的冲突。

    §15「overlay编号」：index=0 是 hook 的独立身份。窗表把 0 明确标成 hook 时，
    指向 0 的叠层就是叠加在 **hook** 上（身份取 hook 的 shot_id）；窗表若把 0 当
    正文镜头（没有 is_hook），则必须拒收——不能把 hook 当第一正文镜头。
    """
    # golden 1：hook（时长贴合 hook 窗口 2.0s）
    b = copy.deepcopy(title_bundle)
    b["overlays"][0].update({"shot_index": 0, "start_sec": 0.0, "end_sec": 2.0})
    b["specs"] = [s2 for s2 in b["specs"] if s2["spec_id"] == "t_main"]
    b["specs"][0]["duration_sec"] = 2.0
    b["specs"][0]["segments"][0]["end_sec"] = 2.0
    report = O.build_overlay_plan(b, shot_windows={"0": dict(windows[0])}, allow_fixture=True)
    assert report.ok, report.problems
    assert report.plan["layers"][0]["shot_id"] == windows[0]["shot_id"]

    # golden 2：正文 1（身份必须是正文镜头的 shot_id，与 hook 不同）
    report2 = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True)
    assert report2.plan["layers"][0]["shot_id"] == windows[1]["shot_id"]

    # golden 3：窗表把 0 当正文（无 is_hook）⇒ 身份冲突拒收
    no_hook = {0: {"start_sec": 0.0, "end_sec": 6.0, "shot_id": "body_first", "is_hook": False}}
    report3 = O.build_overlay_plan(b, shot_windows=no_hook, allow_fixture=True)
    assert "hook_identity_conflict" in _codes(report3)


def test_body_shot_one_maps_to_its_own_shot_id(title_bundle, windows):
    report = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True)
    layer = report.plan["layers"][0]
    assert layer["shot_index"] == 1 and layer["shot_id"] == windows[1]["shot_id"]
    assert layer["shot_id"] != windows[0]["shot_id"]


def test_cross_revision_requires_explicit_id_map(title_bundle, windows):
    """跨 revision：无明确 ID map ⇒ 拒收；有 map ⇒ 按 map 重算并留痕。"""
    report = O.build_overlay_plan(title_bundle, shot_windows=windows,
                                  script_revision="fixture-a-0002", allow_fixture=True,
                                  bundle_revision="fixture-a-0001")
    assert "shot_index_revision_mismatch" in _codes(report)

    # fixture 来源不带 script_revision（§A2）：调用方不给 bundle_revision 就无法证明 ⇒ 拒收
    unknown = O.build_overlay_plan(title_bundle, shot_windows=windows,
                                   script_revision="fixture-a-0002", allow_fixture=True)
    assert "shot_index_revision_unknown" in _codes(unknown)

    remapped_windows = {3: dict(windows[1], shot_id="fixture-a-0002:shot_1"),
                        4: dict(windows[2], shot_id="fixture-a-0002:shot_2")}
    report2 = O.build_overlay_plan(title_bundle, shot_windows=remapped_windows,
                                   script_revision="fixture-a-0002",
                                   shot_index_map={1: 3, 2: 4}, allow_fixture=True,
                                   bundle_revision="fixture-a-0001")
    assert report2.ok, report2.problems
    layer = report2.plan["layers"][0]
    assert layer["shot_index"] == 3 and layer["remapped_from_index"] == 1
    assert layer["shot_id"] == "fixture-a-0002:shot_1"


def test_shot_id_must_come_from_producer(title_bundle, windows):
    bad = {1: {"start_sec": 0.0, "end_sec": 6.0}}  # 没有 shot_id
    report = O.build_overlay_plan(title_bundle, shot_windows=bad, allow_fixture=True)
    assert "shot_id_missing" in _codes(report)


# ---------------------------------------------------------------- 准入与产物

def test_fixture_source_cannot_enter_production_overlay(title_bundle, windows):
    report = O.build_overlay_plan(title_bundle, shot_windows=windows)
    assert "fixture_source_not_production" in _codes(report)


def test_empty_overlays_cannot_be_consumed_by_step04(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["overlays"] = []
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert "empty_overlay_plan" in _codes(report)


def test_map_spec_never_enters_overlay_plan(built, windows):
    b = json.loads(Path(built["cases"]["content_a__map"]["bundle"]).read_text(encoding="utf-8"))
    b["overlays"] = [{"overlay_id": "ov_map", "spec_id": "m_main", "shot_index": 1,
                      "start_sec": 0.0, "end_sec": 5.0}]
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert "map_not_overlayable" in _codes(report)


def test_rendered_frames_are_required_and_counted(title_bundle, windows, tmp_path):
    report = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True,
                                  rendered={"t_main": {"frames_dir": str(tmp_path / "nope"),
                                                       "frame_count": 72}})
    assert "rendered_frames_missing" in _codes(report)

    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_00000.png").write_bytes(b"x")
    bad_count = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True,
                                     rendered={"t_main": {"frames_dir": str(frames),
                                                          "frame_count": 71}})
    assert "frame_count_mismatch" in _codes(bad_count)

    good = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True,
                                rendered={"t_main": {"frames_dir": str(frames),
                                                     "frame_count": 72}})
    assert good.ok, good.problems
    layer = good.plan["layers"][0]
    assert layer["frames_dir"] == str(frames)
    assert layer["frame_pattern"] == "frame_%05d.png"


def test_unbound_specs_are_reported_as_isolated(built, windows):
    b = json.loads(Path(built["cases"]["content_a__title"]["bundle"]).read_text(encoding="utf-8"))
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert report.ok
    assert [i["spec_id"] for i in report.plan["isolated"]] == ["t_alt"]
    assert report.plan["isolated"][0]["reason"] == "unbound_spec"


# ---------------------------------------------------------------- FFmpeg 接线片段

def test_ffmpeg_contract_is_explicit(title_bundle, windows, tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    report = O.build_overlay_plan(title_bundle, shot_windows=windows, allow_fixture=True,
                                  rendered={"t_main": {"frames_dir": str(frames),
                                                       "frame_count": 72}})
    plan = report.plan
    assert plan["ffmpeg"]["input_order"] == ["clips", "tts", "narration", "png_sequences"]
    assert plan["ffmpeg"]["alpha"] == "straight"
    assert plan["ffmpeg"]["no_audio"] is True

    layer = plan["layers"][0]
    frag = O.overlay_filter_fragment(layer, "0:v", "3:v")
    assert "alpha=straight" in frag, "§15「RGBA与字体」要求显式直通 alpha"
    # **半开窗口**（复核第十二轮补查 P2）：`between(t,S,E)` 两端都含 ⇒ 多显示一帧；
    # 现在与 `step04_merge.backends.ffmpeg` 共用 `gte*lt` 且各退半帧的表达。
    assert "enable='gte(t," in frag and "*lt(t," in frag, frag
    args = O.png_input_args(layer)
    assert "-framerate" in args and "24" in args
    assert args[-1].endswith("frame_%05d.png")


def test_subtitle_exclusion_recheck(title_bundle, windows):
    b = copy.deepcopy(title_bundle)
    b["canvas"]["subtitle_exclusion_rect"] = [0, 0, 270, 480]
    report = O.build_overlay_plan(b, shot_windows=windows, allow_fixture=True)
    assert "rect_overlaps_subtitle_exclusion" in _codes(report)


# ---------------------------------------------------------------- Step04 固定的计划文件

def test_plan_file_shape_matches_frozen_s_contract(tmp_path, title_bundle):
    """S 已固定的接口：顶层只有五个键，overlay 条目只给局部时间 + kind/path/layer。"""
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_00000.png").write_bytes(b"x")
    rendered = {"t_main": {"frames_dir": str(frames), "frame_count": 72}}

    seq = O.build_overlay_plan_file(title_bundle, scope={"book": "卷一", "chapter": 1},
                                    script_revision="fixture-a-0001", rendered=rendered,
                                    mode="animated")
    assert seq.ok, seq.problems
    assert tuple(seq.plan) == O.OVERLAY_PLAN_KEYS, f"多/少键都会让 S 拒收: {tuple(seq.plan)}"
    entry = seq.plan["overlays"][0]
    assert tuple(entry) == O.OVERLAY_ENTRY_KEYS
    assert entry["kind"] == "png_sequence"
    assert entry["path"].endswith("frame_%05d.png")
    assert entry["start_sec"] == 0.0 and entry["end_sec"] == 3.0, "必须是镜头局部时间"
    assert entry["layer"] == 0 and entry["shot_index"] == 1
    assert seq.plan["fps"] == 24
    assert seq.plan["scope"] == {"book": "卷一", "chapter": 1}

    still = O.build_overlay_plan_file(title_bundle, scope={"book": "卷一", "chapter": 1},
                                      script_revision="fixture-a-0001", rendered=rendered,
                                      mode="static")
    assert still.ok, still.problems
    assert still.plan["overlays"][0]["kind"] == "image"
    assert still.plan["overlays"][0]["path"].endswith("frame_00000.png")


def test_plan_file_layer_follows_overlay_list_order(tmp_path, title_bundle):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_00000.png").write_bytes(b"x")
    b = copy.deepcopy(title_bundle)
    b["overlays"].append({"overlay_id": "ov_top", "spec_id": "t_alt", "shot_index": 1,
                          "start_sec": 0.0, "end_sec": 3.0})
    rendered = {"t_main": {"frames_dir": str(frames), "frame_count": 72},
                "t_alt": {"frames_dir": str(frames), "frame_count": 72}}
    report = O.build_overlay_plan_file(b, scope={}, script_revision="r",
                                       rendered=rendered, mode="animated")
    assert report.ok, report.problems
    assert [(o["overlay_id"], o["layer"]) for o in report.plan["overlays"]] == \
        [("ov_main", 0), ("ov_top", 1)]


def test_plan_file_rejects_missing_frames_and_map(tmp_path, title_bundle, built):
    report = O.build_overlay_plan_file(title_bundle, scope={}, script_revision="r",
                                       rendered={}, mode="animated")
    assert "rendered_frames_missing" in _codes(report)

    m = json.loads(Path(built["cases"]["content_a__map"]["bundle"]).read_text(encoding="utf-8"))
    m["overlays"] = [{"overlay_id": "ov_map", "spec_id": "m_main", "shot_index": 1,
                      "start_sec": 0.0, "end_sec": 5.0}]
    report2 = O.build_overlay_plan_file(m, scope={}, script_revision="r", rendered={}, mode="animated")
    assert "map_not_overlayable" in _codes(report2)


def test_plan_file_empty_overlays_is_not_applicable(title_bundle):
    b = copy.deepcopy(title_bundle)
    b["overlays"] = []
    report = O.build_overlay_plan_file(b, scope={}, script_revision="r", rendered={},
                                       mode="animated")
    assert "empty_overlay_plan" in _codes(report)


def test_write_plan_file_roundtrip(tmp_path, title_bundle):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_00000.png").write_bytes(b"x")
    report = O.build_overlay_plan_file(title_bundle, scope={"experiment_id": "e"},
                                       script_revision="r",
                                       rendered={"t_main": {"frames_dir": str(frames),
                                                            "frame_count": 72}})
    target = O.write_overlay_plan_file(report.plan, tmp_path / "out" / "motion_overlay_plan.json")
    assert json.loads(target.read_text(encoding="utf-8")) == report.plan


def test_derive_scope_is_deterministic(title_bundle, built):
    scope = O.derive_scope(title_bundle)
    assert scope["kind"] == "fixture" and scope["fixture_id"] == "content_a"
    assert scope["experiment_id"] == "motion-fixture"
    assert O.derive_scope(title_bundle) == scope
