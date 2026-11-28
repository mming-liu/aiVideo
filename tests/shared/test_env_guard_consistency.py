# -*- coding: utf-8 -*-
"""跨键配置一致性预检（shared.env_guard，2026-11-28 O1）.

覆盖 8 类规则的正反例、阶段过滤、strict / 非 strict 行为，以及
`tools/print_effective_config.py --check` 的真实进程退出码。

全部为纯函数调用：显式传 env 映射、不写文件、不发请求；唯一的外部动作是最后
一个用例按生产语义跑一次只读 CLI。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.env_guard import (ConfigConsistencyError, assert_config_consistent,
                              validate_config_consistency)

#: 与当前 .env.content 一致的「干净」配置；各用例只覆盖被测键。
BASE = {
    "VIDEO_SIZE": "1440*2560",
    "VIDEO_PROVIDER": "volcengine",
    "VIDEO_REFERENCE_MODE": "image",
    "VIDEO_MODEL_VOICE": "0",
    "VOLCENGINE_I2V_MODEL": "doubao-seedance-1-0-pro-250528",
    "MERGE_AUDIO_MODE": "tts_mix",
    "MERGE_RESOLUTION": "1440x2560",
    "VIDEO_STYLE": "gufeng",
    "RENDER_STYLE": "古风真人",
}


def env(**overrides: object) -> dict[str, str]:
    out = dict(BASE)
    out.update({key: str(value) for key, value in overrides.items()})
    return out


def codes(issues) -> list[str]:
    return [issue.code for issue in issues]


# --------------------------------------------------------------------------
# 基线
# --------------------------------------------------------------------------
def test_clean_config_reports_nothing():
    assert validate_config_consistency(env()) == []


# --------------------------------------------------------------------------
# 1) VIDEO_VOICE_UNSUPPORTED
# --------------------------------------------------------------------------
def test_voice_flag_on_silent_model_is_error():
    issues = validate_config_consistency(env(VIDEO_MODEL_VOICE="1"))
    assert codes(issues) == ["VIDEO_VOICE_UNSUPPORTED"]
    assert issues[0].severity == "error"
    assert "VIDEO_MODEL_VOICE" in issues[0].keys


def test_voice_flag_on_audible_model_is_fine():
    issues = validate_config_consistency(
        env(VIDEO_MODEL_VOICE="1",
            VOLCENGINE_I2V_MODEL="doubao-seedance-1-5-pro-251215"))
    assert issues == []


def test_voice_flag_off_never_complains():
    assert validate_config_consistency(env(VIDEO_MODEL_VOICE="0")) == []


# --------------------------------------------------------------------------
# 2) VIDEO_R2V_UNSUPPORTED
# --------------------------------------------------------------------------
def test_r2v_on_model_without_reference_image_is_error():
    issues = validate_config_consistency(env(VIDEO_REFERENCE_MODE="r2v"))
    assert codes(issues) == ["VIDEO_R2V_UNSUPPORTED"]
    assert issues[0].severity == "error"


def test_r2v_on_capable_model_is_fine():
    issues = validate_config_consistency(
        env(VIDEO_REFERENCE_MODE="r2v",
            VOLCENGINE_I2V_MODEL="doubao-seedance-2-0-260128"))
    assert issues == []


def test_image_mode_never_triggers_r2v_rule():
    assert validate_config_consistency(env(VIDEO_REFERENCE_MODE="image")) == []


# --------------------------------------------------------------------------
# 3) RATIO_CONFLICT_PROVIDER
# --------------------------------------------------------------------------
def test_provider_ratio_override_against_canvas_is_warning():
    issues = validate_config_consistency(env(VOLCENGINE_VIDEO_RATIO="16:9"))
    assert codes(issues) == ["RATIO_CONFLICT_PROVIDER"]
    assert issues[0].severity == "warning"


def test_provider_ratio_matching_canvas_is_fine():
    assert validate_config_consistency(env(VOLCENGINE_VIDEO_RATIO="9:16")) == []


def test_provider_ratio_empty_means_derive():
    assert validate_config_consistency(env(VOLCENGINE_VIDEO_RATIO="")) == []


def test_provider_ratio_garbage_is_reported():
    issues = validate_config_consistency(env(VOLCENGINE_VIDEO_RATIO="169"))
    assert codes(issues) == ["RATIO_CONFLICT_PROVIDER"]


def test_provider_ratio_only_checks_active_provider():
    # 非当前供应商的 ratio 配置不参与判定（避免误导性告警）
    assert validate_config_consistency(env(BAILIAN_VIDEO_RATIO="16:9")) == []


# --------------------------------------------------------------------------
# 4) RATIO_CONFLICT_MERGE
# --------------------------------------------------------------------------
def test_merge_resolution_opposite_orientation_is_warning():
    issues = validate_config_consistency(env(MERGE_RESOLUTION="2560x1440"))
    assert codes(issues) == ["RATIO_CONFLICT_MERGE"]
    assert issues[0].severity == "warning"


def test_merge_resolution_same_orientation_is_fine():
    assert validate_config_consistency(env(MERGE_RESOLUTION="1440x2560")) == []


def test_merge_resolution_garbage_is_reported():
    issues = validate_config_consistency(env(MERGE_RESOLUTION="1440*2560*2"))
    assert codes(issues) == ["RATIO_CONFLICT_MERGE"]


def test_merge_resolution_square_is_not_a_conflict():
    assert validate_config_consistency(env(MERGE_RESOLUTION="1440x1440")) == []


# --------------------------------------------------------------------------
# 5) AUDIO_MODE_VOICE_CONFLICT
# --------------------------------------------------------------------------
def test_unknown_audio_mode_is_error():
    issues = validate_config_consistency(env(MERGE_AUDIO_MODE="tts-mix"))
    assert codes(issues) == ["AUDIO_MODE_VOICE_CONFLICT"]
    assert issues[0].severity == "error"


def test_native_mode_with_voice_off_is_warning():
    issues = validate_config_consistency(env(MERGE_AUDIO_MODE="native",
                                             VIDEO_MODEL_VOICE="0"))
    assert codes(issues) == ["AUDIO_MODE_VOICE_CONFLICT"]
    assert issues[0].severity == "warning"


def test_native_mode_with_voice_on_is_fine():
    issues = validate_config_consistency(
        env(MERGE_AUDIO_MODE="native", VIDEO_MODEL_VOICE="1",
            VOLCENGINE_I2V_MODEL="doubao-seedance-1-5-pro-251215"))
    assert issues == []


# --------------------------------------------------------------------------
# 6) INVALID_NUMERIC_CONFIG
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key,value", [
    ("RENDER_FPS", "0"),
    ("RENDER_FPS", "-5"),
    ("RENDER_FPS", "abc"),
    ("DURATION_MIN_SEC", "0"),
    ("IMAGE_MAX_REFS", "0"),
    ("IMAGE_MULTI_REF", "-2"),
    ("VIDEO_MAX_RETRIES", "-1"),
    ("VIDEO_MAX_RETRIES", "abc"),
])
def test_invalid_numeric_value_is_error(key, value):
    issues = validate_config_consistency(env(**{key: value}))
    assert "INVALID_NUMERIC_CONFIG" in codes(issues), (key, value, issues)
    assert all(i.severity == "error" for i in issues)


def test_duration_bounds_inverted_is_error():
    issues = validate_config_consistency(
        env(DURATION_MIN_SEC="90", DURATION_MAX_SEC="60"))
    assert codes(issues) == ["INVALID_NUMERIC_CONFIG"]
    assert set(issues[0].keys) == {"DURATION_MIN_SEC", "DURATION_MAX_SEC"}


def test_video_max_retries_zero_is_legal():
    # 0 = 不重试（submit_poll 里 range(max_retries + 1) 仍会尝试一次）
    assert validate_config_consistency(env(VIDEO_MAX_RETRIES="0")) == []


def test_unset_numeric_keys_use_code_defaults():
    assert validate_config_consistency(env()) == []


# --------------------------------------------------------------------------
# 7) IMAGE_REFS_BELOW_MULTI
# --------------------------------------------------------------------------
def test_ref_cap_below_multi_ref_is_warning():
    issues = validate_config_consistency(
        env(IMAGE_MAX_REFS="2", IMAGE_MULTI_REF="3"))
    assert codes(issues) == ["IMAGE_REFS_BELOW_MULTI"]
    assert issues[0].severity == "warning"


def test_ref_cap_above_multi_ref_is_fine():
    assert validate_config_consistency(
        env(IMAGE_MAX_REFS="10", IMAGE_MULTI_REF="3")) == []


def test_ref_cap_unset_skips_rule():
    assert validate_config_consistency(env(IMAGE_MULTI_REF="3")) == []


# --------------------------------------------------------------------------
# 8) STYLE_LABEL_MISMATCH
# --------------------------------------------------------------------------
def test_style_label_pointing_to_another_preset_is_warning():
    issues = validate_config_consistency(
        env(VIDEO_STYLE="anime", RENDER_STYLE="古风真人"))
    assert codes(issues) == ["STYLE_LABEL_MISMATCH"]
    assert issues[0].severity == "warning"


def test_style_label_matching_preset_is_fine():
    assert validate_config_consistency(
        env(VIDEO_STYLE="gufeng", RENDER_STYLE="古风真人")) == []


def test_custom_render_style_is_not_judged():
    assert validate_config_consistency(
        env(VIDEO_STYLE="gufeng", RENDER_STYLE="用户自定义画风")) == []


def test_unknown_video_style_is_reported():
    issues = validate_config_consistency(env(VIDEO_STYLE="no-such-preset"))
    assert codes(issues) == ["STYLE_LABEL_MISMATCH"]
    assert issues[0].keys == ("VIDEO_STYLE",)


# --------------------------------------------------------------------------
# 阶段过滤与 strict
# --------------------------------------------------------------------------
def test_stage_filter_keeps_only_relevant_rules():
    mapping = env(VOLCENGINE_VIDEO_RATIO="16:9", MERGE_RESOLUTION="2560x1440")
    assert codes(validate_config_consistency(mapping, stage="step02b")) == \
        ["RATIO_CONFLICT_PROVIDER"]
    assert codes(validate_config_consistency(mapping, stage="step04")) == \
        ["RATIO_CONFLICT_MERGE"]
    assert set(codes(validate_config_consistency(mapping))) == \
        {"RATIO_CONFLICT_PROVIDER", "RATIO_CONFLICT_MERGE"}


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError):
        validate_config_consistency(env(), stage="step09")


def test_assert_is_non_blocking_by_default(monkeypatch):
    monkeypatch.delenv("CONFIG_GUARD_STRICT", raising=False)
    issues = assert_config_consistent(env(MERGE_AUDIO_MODE="tts-mix"))
    assert codes(issues) == ["AUDIO_MODE_VOICE_CONFLICT"]


def test_assert_strict_raises_on_error(monkeypatch):
    monkeypatch.delenv("CONFIG_GUARD_STRICT", raising=False)
    with pytest.raises(ConfigConsistencyError) as excinfo:
        assert_config_consistent(env(MERGE_AUDIO_MODE="tts-mix"), strict=True)
    assert codes(excinfo.value.issues) == ["AUDIO_MODE_VOICE_CONFLICT"]


def test_assert_strict_switch_comes_from_env(monkeypatch):
    monkeypatch.setenv("CONFIG_GUARD_STRICT", "1")
    with pytest.raises(ConfigConsistencyError):
        assert_config_consistent(env(RENDER_FPS="0"))


def test_assert_strict_ignores_warnings(monkeypatch):
    monkeypatch.setenv("CONFIG_GUARD_STRICT", "1")
    issues = assert_config_consistent(env(MERGE_RESOLUTION="2560x1440"))
    assert codes(issues) == ["RATIO_CONFLICT_MERGE"]          # 只警告，不阻断


# --------------------------------------------------------------------------
# CLI：--check 的真实退出码与 JSON 字段
# --------------------------------------------------------------------------
def test_print_effective_config_check_is_clean_on_current_env():
    proc = subprocess.run(
        [sys.executable, "tools/print_effective_config.py",
         "--stage", "all", "--check", "--json"],
        cwd=str(_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stderr[-800:]
    start = proc.stdout.index("{")
    report = json.loads(proc.stdout[start:])
    assert report["consistency_ok"] is True
    assert report["consistency_issues"] == []
