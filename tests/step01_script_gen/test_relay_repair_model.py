"""接力修复（v6.67）单测：互补模型选择 + model 透传 + 默认零变化.

核心约定：
  - _generate_script_candidates 每个候选的分数带 "_model"（生成本候选的模型）
  - main() 据此经 _complementary_repair_model 选互补模型传入两个修复轮
  - 候选池空/单模型/产出者无标识 → 互补=None → 修复轮走 provider 默认（零行为变化）
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# _complementary_repair_model：互补模型选择
# ---------------------------------------------------------------------------

def _set_candidate_models(monkeypatch, models):
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_MODELS", models)


def test_complementary_picks_the_other_model(monkeypatch):
    from step01_script_gen.main import _complementary_repair_model
    _set_candidate_models(monkeypatch,
                          ["deepseek-v4-flash", "doubao-seed-2-1-turbo"])
    assert _complementary_repair_model("deepseek-v4-flash") == "doubao-seed-2-1-turbo"
    assert _complementary_repair_model("doubao-seed-2-1-turbo") == "deepseek-v4-flash"


def test_complementary_three_model_rotation(monkeypatch):
    from step01_script_gen.main import _complementary_repair_model
    _set_candidate_models(monkeypatch, ["m-a", "m-b", "m-c"])
    assert _complementary_repair_model("m-a") == "m-b"
    assert _complementary_repair_model("m-c") == "m-a"


def test_complementary_zero_change_cases(monkeypatch):
    """候选池空/单模型/产出者 None 或不在列表 → None（=默认模型，零行为变化）."""
    from step01_script_gen.main import _complementary_repair_model

    _set_candidate_models(monkeypatch, [])
    assert _complementary_repair_model("deepseek-v4-flash") is None
    assert _complementary_repair_model(None) is None

    _set_candidate_models(monkeypatch, ["deepseek-v4-flash"])
    assert _complementary_repair_model("deepseek-v4-flash") is None

    _set_candidate_models(monkeypatch,
                          ["deepseek-v4-flash", "doubao-seed-2-1-turbo"])
    assert _complementary_repair_model(None) is None
    assert _complementary_repair_model("unknown-model") is None


# ---------------------------------------------------------------------------
# 候选生成：分数挂 "_model"
# ---------------------------------------------------------------------------

def test_candidates_carry_model_marker(monkeypatch):
    """每个候选的 score dict 必须带 _model，且与 SCRIPT_CANDIDATE_MODELS 轮转一致."""
    import shared.config as cfg
    import step01_script_gen.main as m

    _set_candidate_models(monkeypatch, ["m-deepseek", "m-doubao"])
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 2)
    monkeypatch.setattr(m, "gen_spy_script",
                        lambda *a, **k: '{"shots": []}')  # raw 占位，下游被 mock
    monkeypatch.setattr(m, "parse_response", lambda raw, **k: {"shots": []})
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 50.0, "detail": "x"})

    candidates = m._generate_script_candidates("novel")

    assert len(candidates) == 2
    got_models = sorted(c["_model"] for c, _ in candidates)
    assert got_models == ["m-deepseek", "m-doubao"]


def test_candidates_without_pool_have_none_model(monkeypatch):
    """SCRIPT_CANDIDATE_MODELS 空 → _model=None（回默认模型，接力自动关闭）."""
    import shared.config as cfg
    import step01_script_gen.main as m

    _set_candidate_models(monkeypatch, [])
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    monkeypatch.setattr(m, "gen_spy_script", lambda *a, **k: '{"shots": []}')
    monkeypatch.setattr(m, "parse_response", lambda raw, **k: {"shots": []})
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 50.0, "detail": "x"})

    candidates = m._generate_script_candidates("novel")
    assert candidates[0][0]["_model"] is None


# ---------------------------------------------------------------------------
# 修复轮：model 透传到 TextGenerationRequest
# ---------------------------------------------------------------------------

class _StopResult:
    """success=False 的假结果：让修复轮拿到 req 后立即走「保留原版」路径."""

    def __init__(self):
        self.success = False
        self.error = "fake-stop（测试用，只为捕获 req）"
        self.text = ""


class _CaptureService:
    def __init__(self, captured):
        self._captured = captured

    def generate(self, req):
        self._captured.append(req)
        return _StopResult()


def test_regen_over_limit_passes_model(monkeypatch):
    import shared.config as cfg
    import step01_script_gen.main as m

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    monkeypatch.setattr(cfg, "SHOT_COUNT_MAX", 5)
    # v6.68: builder/system 走 resolve_model_prompt_profile，patch 掉避免真实拼模板
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "build_regen_feedback", lambda *a, **k: "")
    monkeypatch.setattr(m, "_compute_timecodes", lambda s: None)

    script = {"shots": [{"index": i} for i in range(9)], "total_duration": 60}
    m._regen_over_limit(script, "novel", model="m-doubao")
    assert captured and captured[0].model == "m-doubao"
    assert captured[0].system_prompt == "SYS"


def test_regen_over_limit_default_model_is_none(monkeypatch):
    import shared.config as cfg
    import step01_script_gen.main as m

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    monkeypatch.setattr(cfg, "SHOT_COUNT_MAX", 5)
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "build_regen_feedback", lambda *a, **k: "")
    monkeypatch.setattr(m, "_compute_timecodes", lambda s: None)

    script = {"shots": [{"index": i} for i in range(9)], "total_duration": 60}
    m._regen_over_limit(script, "novel")
    assert captured and captured[0].model is None


def test_repair_shot_warnings_passes_model(monkeypatch):
    import step01_script_gen.main as m

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    monkeypatch.setattr(
        m, "_collect_repair_warnings",
        lambda s, n: {"_has_hard": True, "shots": {"shot_1": ["[fake] w1"]}},
    )
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))

    script = {"shots": [{"index": 1, "ff_desc": "f", "lf_desc": "l",
                         "prompt": "p", "color_tone": "c"}],
              "total_duration": 8}
    m._repair_shot_warnings(script, "novel", model="m-deepseek")
    assert captured and captured[0].model == "m-deepseek"
    assert captured[0].system_prompt == "SYS"


def test_repair_shot_warnings_default_model_is_none(monkeypatch):
    import step01_script_gen.main as m

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    monkeypatch.setattr(
        m, "_collect_repair_warnings",
        lambda s, n: {"_has_hard": True, "shots": {"shot_1": ["[fake] w1"]}},
    )
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))

    script = {"shots": [{"index": 1, "ff_desc": "f", "lf_desc": "l",
                         "prompt": "p", "color_tone": "c"}],
              "total_duration": 8}
    m._repair_shot_warnings(script, "novel")
    assert captured and captured[0].model is None
