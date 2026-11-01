# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_inserts_system_segment_when_no_source_record():
    from shared.video_prompt_system import apply_system_camera
    r = apply_system_camera("正文", "固定机位")
    assert r["status"] == "ok" and r["applied"] is True
    assert r["prompt"] == "运镜：固定机位\n正文"
    assert len(r["segments"]) == 1


def test_user_body_marker_is_not_a_system_segment():
    from shared.video_prompt_system import apply_system_camera
    r = apply_system_camera("运镜：固定机位\n用户正文", "固定机位")
    assert r["status"] == "ok" and r["applied"] is True
    assert r["prompt"] == "运镜：固定机位\n运镜：固定机位\n用户正文"
    assert len(r["segments"]) == 1


def test_missing_camera_is_zero_segments_even_if_text_starts_with_marker():
    from shared.video_prompt_system import apply_system_camera
    r = apply_system_camera("运镜：固定机位\n正文", "")
    assert r["status"] == "ok" and r["applied"] is False
    assert r["segments"] == []


def test_recorded_same_segment_is_unchanged():
    from shared.video_prompt_system import apply_system_camera
    existing = [{"origin": "system_camera", "camera_movement": "固定机位"}]
    r = apply_system_camera("运镜：固定机位\n正文", "固定机位", existing=existing)
    assert r["status"] == "unchanged" and r["applied"] is False


def test_recorded_segment_missing_from_text_is_conflict():
    from shared.video_prompt_system import apply_system_camera
    existing = [{"origin": "system_camera", "camera_movement": "固定机位"}]
    r = apply_system_camera("正文", "固定机位", existing=existing)
    assert r["status"] == "conflict"
    assert r["problems"][0]["code"] == "system_camera_missing"


def test_recorded_different_content_is_conflict():
    from shared.video_prompt_system import apply_system_camera
    existing = [{"origin": "system_camera", "camera_movement": "固定机位"}]
    r = apply_system_camera("运镜：固定机位\n正文", "推近", existing=existing)
    assert r["status"] == "conflict"
    assert r["problems"][0]["code"] == "system_camera_conflict"


def test_duplicate_records_are_conflict():
    from shared.video_prompt_system import apply_system_camera
    existing = [{"origin": "system_camera", "camera_movement": "固定机位"},
                {"origin": "system_camera", "camera_movement": "固定机位"}]
    r = apply_system_camera("正文", "固定机位", existing=existing)
    assert r["status"] == "conflict"
    assert r["problems"][0]["code"] == "system_camera_duplicate"


def test_over_limit_insert_is_rejected_without_truncation():
    from shared.video_prompt_system import apply_system_camera
    r = apply_system_camera("正文" * 10, "固定机位", max_chars=10)
    assert r["status"] == "over_limit"
    assert r["problems"][0]["code"] == "system_camera_over_limit"
    assert r["prompt"] == "正文" * 10


def test_over_limit_unchanged_is_also_rejected():
    from shared.video_prompt_system import apply_system_camera
    existing = [{"origin": "system_camera", "camera_movement": "固定机位"}]
    r = apply_system_camera("运镜：固定机位\n" + "正文" * 10, "固定机位",
                            max_chars=10, existing=existing)
    assert r["status"] == "conflict"
    assert r["problems"][0]["code"] == "system_camera_over_limit"
