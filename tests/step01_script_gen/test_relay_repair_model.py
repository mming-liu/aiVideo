"""接力修复（v6.67）单测：互补模型选择 + model 透传 + 默认零变化.

核心约定：
  - _generate_script_candidates 每个候选的分数带 "_model"（生成本候选的模型）
  - main() 据此经 _complementary_repair_model 选互补模型传入两个修复轮
  - 候选池空/单模型/产出者无标识 → 互补=None → 修复轮走 provider 默认（零行为变化）
"""
from __future__ import annotations

import sys

import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# v6.131（2026-09-25 收口）：生成前计划门挂在**候选扇出前**（`_generate_script_candidates`
# 入口），缺计划/计划不可行即停。因此凡是要驱动候选扇出机制的用例，都必须给它一份
# **真实可验算**的计划（节拍 3 个/下限 9s、对白 1 句逐字来自下面的原文 → 可行）。
_SOURCE = "老陈说：这船今日不出港。舱里还有两箱货，天黑前必须走。"
from step01_script_gen import plan_feasibility as pf

_PLAN = {"main_events": ["老陈说船不出港", "舱里还有两箱货", "天黑前必须走"],
         "climax": "老陈说船不出港",
         "causal_chain": ["老陈说船不出港→舱里还有两箱货→天黑前必须走"],
         "required_facts": ["老陈说船今日不出港｜原文证据：“老陈说：这船今日不出港。”"
                            "｜承载：B1/台词1"],
         "shot_beats": ["节拍：老陈说船今日不出港｜本镜信息目的：交代｜原文依据：“老陈说："
                        "这船今日不出港。”｜载体：台词1",
                        "节拍：舱里还有两箱货｜本镜信息目的：交代｜原文依据：“舱里还有两箱货”"
                        "｜载体：画面",
                        "节拍：天黑前必须走｜本镜信息目的：推进｜原文依据：“天黑前必须走”"
                        "｜载体：画面"],
         "dialogue_lines": ["这船今日不出港"],
         "narration_lines": [], "hook_spoken": [], "hook_carrier": ""}

#: 计划身份（`plan_id`）与逐镜 `plan_refs`：**承载契约下的新生成稿必须声明**
#: （v2 起：带「载体：/承载：」的计划要求逐镜履约声明，否则候选层事件契约整稿拒收）。
#: 夹具因此要给 mock 稿补上——与本文件既有先例（夹具带真实计划、mock 稿带计划台词）同源。
_PLAN_ID = pf.plan_identity(_PLAN)


def _refs(fn):
    """把 mock 的解析结果补成**契约完整**的新稿（`plan_id` + 逐镜 `plan_refs`）.

    节拍声明按镜头序**非降**分配：第 k 镜声明第 k 个节拍，之后的镜头都声明最后一条
    （覆盖每个节拍、又不制造顺序回退）；台词只在首镜声明一次。
    """
    _seq = list(range(1, len(_PLAN["shot_beats"]) + 1)) or [1]

    def _w(raw, *a, **k):
        _s = fn(raw, *a, **k)
        if not isinstance(_s, dict):
            return _s
        _s.setdefault("plan_id", _PLAN_ID)
        _shots = [x for x in (_s.get("shots") or []) if isinstance(x, dict)]
        for _i, _sh in enumerate(_shots):
            if len(_shots) == 1:
                _sh.setdefault("plan_refs", {"beats": list(_seq), "lines": [1]})
            else:
                _b = _seq[min(_i, len(_seq) - 1)]
                _sh.setdefault("plan_refs", {"beats": [_b], "lines": [1] if _i == 0 else []})
        return _s
    return _w


#: 计划门会**收窄** `dialogue_lines`（核验通过的那一组），上面那句在原文里逐字存在


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
    assert _complementary_repair_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert _complementary_repair_model(None) == "deepseek-v4-flash"

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
    monkeypatch.setattr(m, "parse_response", _refs(  # v6.135：mock 稿带计划台词（台词完整性硬门），空稿会被判破损
                    lambda raw, **k: {"shots": [{"index": 1, "narration_segment": "老陈说：这船今日不出港",
                                                 "ff_desc": "f", "lf_desc": "l",
                                                 "prompt": "p", "duration_sec": 3.0}]}))
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 50.0, "detail": "x"})

    candidates = m._generate_script_candidates(_SOURCE, None, None, _PLAN)

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
    monkeypatch.setattr(m, "parse_response", _refs(  # v6.135：mock 稿带计划台词（台词完整性硬门），空稿会被判破损
                    lambda raw, **k: {"shots": [{"index": 1, "narration_segment": "老陈说：这船今日不出港",
                                                 "ff_desc": "f", "lf_desc": "l",
                                                 "prompt": "p", "duration_sec": 3.0}]}))
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 50.0, "detail": "x"})

    candidates = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
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
        # v6.83（二次审核发现 2）：告警文本必须能推出**字段路径**，否则该镜不进
        # 定向修复（无授权依据 = 不得改任何字段）。这里用真实告警形态。
        lambda s, n: {"_has_hard": True,
                      "shots": {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}},
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
        # v6.83（二次审核发现 2）：告警文本必须能推出**字段路径**，否则该镜不进
        # 定向修复（无授权依据 = 不得改任何字段）。这里用真实告警形态。
        lambda s, n: {"_has_hard": True,
                      "shots": {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}},
    )
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))

    script = {"shots": [{"index": 1, "ff_desc": "f", "lf_desc": "l",
                         "prompt": "p", "color_tone": "c"}],
              "total_duration": 8}
    m._repair_shot_warnings(script, "novel")
    assert captured and captured[0].model is None


# ───────────────── 修复的**通道覆盖**（2026-09-28 实测后新增）─────────────────
# 背景：agent_plan 通道把 json_schema 降级成 json_object → 修复的形状契约
# （整镜与首次生成同构、只有授权字段可改）失效 → `deepseek-v4.1-flash` **整镜原样回抄**
# （6/6 次 resolved=0、audit_field_touched=0）→ 修复轮不可用。故修复可单开通道。
def _patch_repair_service(monkeypatch, m, captured, providers):
    """把 `create_service` 换成「记录 provider 参数 + 捕获请求」的假服务（自动还原）."""
    def _factory(*a, **k):
        providers.append(a[0] if a else None)
        return _CaptureService(captured)
    monkeypatch.setattr(m, "create_service", _factory)


def test_resolve_channel_three_states(monkeypatch):
    from shared.text_channel import resolve_channel

    monkeypatch.delenv("X_PROVIDER", raising=False)
    monkeypatch.delenv("X_MODEL", raising=False)
    assert resolve_channel("X_PROVIDER", "X_MODEL") == (None, None)
    monkeypatch.setenv("X_PROVIDER", "deepseek")
    with pytest.raises(ValueError):
        resolve_channel("X_PROVIDER", "X_MODEL")
    monkeypatch.setenv("X_MODEL", "deepseek-flash")
    assert resolve_channel("X_PROVIDER", "X_MODEL") == ("deepseek", "deepseek-flash")


def test_audit_channel_shares_the_same_resolver(monkeypatch):
    """审计与修复的通道覆盖共用一份语义（单一来源），缺模型时都显式抛错."""
    import step01_script_gen.audit as a

    monkeypatch.setenv("AUDIT_TEXT_PROVIDER", "deepseek")
    monkeypatch.delenv("AUDIT_TEXT_MODEL", raising=False)
    with pytest.raises(ValueError):
        a.resolve_audit_channel()


def test_repair_channel_override_selects_provider_and_model(monkeypatch):
    import step01_script_gen.main as m

    captured, providers = [], []
    monkeypatch.setenv("REPAIR_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("REPAIR_TEXT_MODEL", "deepseek-flash")
    _patch_repair_service(monkeypatch, m, captured, providers)
    monkeypatch.setattr(
        m, "_collect_repair_warnings",
        lambda s, n: {"_has_hard": True,
                      "shots": {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}})
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    script = {"shots": [{"index": 1, "ff_desc": "f", "lf_desc": "l",
                         "prompt": "p", "color_tone": "c"}], "total_duration": 8}
    m._repair_shot_warnings(script, "novel", model="m-producer")
    assert providers == ["deepseek"], providers
    assert captured and captured[0].model == "deepseek-flash", captured[0].model


def test_repair_channel_absent_keeps_global_provider_and_caller_model(monkeypatch):
    import step01_script_gen.main as m

    captured, providers = [], []
    monkeypatch.delenv("REPAIR_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("REPAIR_TEXT_MODEL", raising=False)
    _patch_repair_service(monkeypatch, m, captured, providers)
    monkeypatch.setattr(
        m, "_collect_repair_warnings",
        lambda s, n: {"_has_hard": True,
                      "shots": {"shot_1": ["镜头1 FF/LF 相似度 92%（时间不足）"]}})
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    script = {"shots": [{"index": 1, "ff_desc": "f", "lf_desc": "l",
                         "prompt": "p", "color_tone": "c"}], "total_duration": 8}
    m._repair_shot_warnings(script, "novel", model="m-producer")
    assert providers == [None], providers
    assert captured and captured[0].model == "m-producer"