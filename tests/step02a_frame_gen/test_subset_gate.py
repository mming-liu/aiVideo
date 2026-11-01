"""Step02a/02b 子集门禁（S11）：--shots 子集不得被全章缺图阻断.

背景：write_image_product_quality 原先无条件核验全章（hook + 所有 shot）的
ff/lf 与 keyframe，导致 --shots 1 单镜验证必然写出 BLOCK，step02b 的
step02a 成品门随即拒绝执行。

改后：
- only_indices 指定时只核验这些镜头；
- 关键帧只在「当前视频模型真的消费 keyframe」时要求（1.0-pro 不消费）；
- 报告带 scope.shots，step02b 据此核对本次请求是否在覆盖范围内。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from step02a_frame_gen.image_prompt_quality import write_image_product_quality


@pytest.fixture(autouse=True)
def offline():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


def _script(n=3):
    shots = [{"index": i, "keyframes": [{"desc": "k0"}]} for i in range(1, n + 1)]
    return {"characters": [], "shots": shots}


def _png(path: Path):
    from PIL import Image
    Image.new("RGB", (8, 8), (0, 0, 0)).save(path)


def _write_frames(work_dir: Path, idx: int, with_kf: bool = False):
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
    d = work_dir / "shots" / str(idx)
    d.mkdir(parents=True, exist_ok=True)
    for name in (FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME):
        _png(d / name)
    if with_kf:
        _png(d / "keyframe_0.png")


def test_subset_run_passes_with_only_requested_shot(tmp_path, monkeypatch):
    from step02a_frame_gen import image_prompt_quality as q

    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: False)
    work = tmp_path / "work"
    _write_frames(work, 1)  # 只有 shot 1 的首尾帧

    report = write_image_product_quality(_script(), work, only_indices={1})

    assert report["status"] == "PASS", report["missing"]
    assert report["scope"]["shots"] == [1]
    assert report["scope"]["keyframes_required"] is False


def test_full_run_still_blocks_on_missing_shots(tmp_path):
    work = tmp_path / "work"
    _write_frames(work, 1)

    report = write_image_product_quality(_script(), work)

    assert report["status"] == "BLOCK"
    assert any("shot 2" in m for m in report["missing"])


def test_scope_check_rejects_uncovered_shots():
    from shared.quality_gate import QualityGateError
    from step02b_video_gen.main import _assert_step02a_scope

    report = {"scope": {"shots": [1]}}
    # 子集请求在范围内 → 放行
    _assert_step02a_scope(report, {1}, _script())
    # 整章请求 → 报告只覆盖 shot 1，必须拒绝
    with pytest.raises(QualityGateError):
        _assert_step02a_scope(report, None, _script())
    # 旧报告没有 scope → 视为全章覆盖，放行
    _assert_step02a_scope({}, None, _script())


def test_default_frames_skip_kf_when_model_ignores(monkeypatch):
    """模型不消费关键帧时，默认只生成 ff/lf（省约 60% 图费）。"""
    from step02a_frame_gen import main as m

    monkeypatch.setattr(m, "_keyframes_unused_by_video", lambda: True)
    assert m._default_frames(None, False) == {"ff", "lf"}
    # 显式 --all-frames 不被改写
    assert m._default_frames(None, True) is None
    # 显式 --frames 不被改写
    assert m._default_frames({"ff"}, False) == {"ff"}


def test_default_frames_kept_when_model_consumes_kf(monkeypatch):
    from step02a_frame_gen import main as m

    monkeypatch.setattr(m, "_keyframes_unused_by_video", lambda: False)
    assert m._default_frames(None, False) is None
