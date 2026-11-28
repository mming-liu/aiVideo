# -*- coding: utf-8 -*-
"""**G 阶段验收矩阵**（复核 2026-10-02 之五）：拒收 → 反馈 → 继续 + 首版重请求.

两条入口，同一个机制（"不合格就重问，不合格的部分**具体**说出来"）：

- **修订入口**（`RevisionController`）：非法操作**绝不采纳**，把拒收的**具体字段与原因**
  （与验收问题**分账**）反馈给下一轮；额度仍是**最多 3 次修订调用 + 同一截止时刻**。
  连续非法到额度耗尽 ⇒ 主终局仍是**响应违约**，额度耗尽另记，**不**把基线内容问题
  冒充成"故事不可行"；截止优先；传输失败维持独立归因。
- **首版入口**（`generate_outline_result`）：首版结构不合格（ch2 实跑形态：模型把
  `shot_beats` 写成对象数组）⇒ 终局诊断给出**具体记录**，并在**同一份额度与截止**内
  重请求**完整首版计划**；不安全的首版对象**不许**进操作应用器。

判据只用**真入口**；模型调用由本地桩替代，不发网络请求。
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import outline as o                                    # noqa: E402

#: 规划额度单一来源（2026-11-15 批准 4→6）
_N = o.DEFAULT_REVISION_MAX_CALLS
from step01_script_gen import plan_revision as pr                             # noqa: E402

_LINE = "甲板上有风，帆布猎猎作响。"
_LINE2 = "他走进屋子，四下打量了很久。"
_SRC = "开头。%s事件乙。事件丙。" % _LINE


def _legal_plan(**over):
    """**完整验收通过**的计划（与 C/D 阶段矩阵同一形状的夹具）.

    **必须补齐 schema 的 16 个必需顶层键**（复核之十）：首版那份 `OUTLINE_SCHEMA` 是
    `strict=True`，模型回完整计划时这些键**一定**都在（实跑响应 16–17 个顶层键）；
    完整计划修订的完整性门按同一份 `required` 校验，夹具缺键就会被判"不是完整计划"。
    """
    p = {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
         "characters": ["甲", "乙"], "emotional_arc": "平→紧", "key_scenes": ["甲板"],
         "character_motives": ["甲：推进"], "end_hook": "风起帆响", "omittable": [],
         "time_budget": "对白约3秒，画面约5秒",
         "required_facts": ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LINE],
         "shot_beats": ["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                        "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
                        "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]}
    p.update(over)
    return p


def _beats_as_objects():
    """**ch2 实跑的真实形态**：`shot_beats` 每一项是对象而不是字符串."""
    p = _legal_plan()
    p["shot_beats"] = [{"节拍": "B1：酒家内四人围坐，介绍各自身份",
                        "本镜信息目的": "交代四人身份和压抑氛围",
                        "原文依据": _LINE, "载体": "台词1"}]
    return p


class _Svc:
    """本地服务桩：按顺序回放响应（用尽 ⇒ 传输失败，供"调用失败"对照用）."""

    def __init__(self, payloads=(), error="stub: 没有更多响应", delay=0.0):
        self.payloads = list(payloads)
        self.error = error
        #: 人为延迟（时间门用例用）：模拟"响应回来时已经过点"
        self.delay = float(delay)
        self.reqs: list = []

    def generate(self, request):
        from _text_gen.models import TextGenerationResult
        self.reqs.append(request)
        if self.delay:
            time.sleep(self.delay)
        if not self.payloads:
            return TextGenerationResult(success=False, error=self.error)
        _p = self.payloads.pop(0)
        if isinstance(_p, str):
            return TextGenerationResult(success=True, text=_p)
        return TextGenerationResult(success=True, text=json.dumps(_p, ensure_ascii=False))


def _patch(monkeypatch, payloads=(), error="stub: 没有更多响应"):
    _svc = _Svc(payloads, error=error)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    # 2026-10（核验协议迁移）：本文件的 `payloads` 只按**规划**调用配量；核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` → `executable=False` → 缓存不写，成片误红。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录
    # （`verification_helpers.legal_record_for`：facts / voice_units / source_support /
    # coherence 四轴齐全、uid 原样回带、身份字段随 `digest` 绑定）。
    from tests import verification_helpers as vh
    _gor = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _SRC
        k.setdefault("verify",
                     lambda view, digest: vh.legal_record_for(view, _novel,
                                                             plan_digest=digest))
        return _gor(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    return _svc


# ---------------------------------------------------------------------------
# ① 修订入口：非法 → 反馈 → 继续（控制器）
# ---------------------------------------------------------------------------
def _ctl_accept():
    """一个只认 `ok` 键的验收桩：`ok=False` 时给一条**验收问题**（与拒收记录分账）."""
    def _accept(plan, novel_text=""):
        if plan.get("ok"):
            return []
        return [{"code": "carrier_fact_line_mismatch", "path": "required_facts[0].承载",
                 "reason": "承载错位", "detail": "承载错位", "basis": "统一验收"}]
    return _accept


def _controller(revise, *, clock=None, max_calls=3, deadline_sec=None):
    _kw = {}
    if clock is not None:
        _kw["clock"] = clock
    _b = pr.RevisionBudget(max_calls=max_calls, deadline_sec=deadline_sec,
                           run_deadline_at=(clock.deadline_in(30.0) if clock else None),
                           **_kw)
    return pr.RevisionController(accept=_ctl_accept(), revise=revise, budget=_b)


def test_g01_an_illegal_response_is_fed_back_and_the_next_legal_one_is_adopted():
    """**入口级**：非法 ⇒ 不采纳 + 把拒收原因反馈 ⇒ 下一轮按反馈改对 ⇒ 采纳."""
    _base = {"ok": False, "shot_beats": ["B1"]}
    # 修好的那一版必须**真的改动了修订状态**（`shot_beats` 在循环指纹里），否则会被
    # 判成"内容没变" ⇒ `no_change`（那是另一条判据，不是这条用例要测的）
    _fixed = {"ok": True, "shot_beats": ["B1", "B2 修好的那条"]}
    _seen: list = []

    def _revise(plan, problems, budget, rejections):
        _seen.append({"n": len(_seen) + 1, "plan": plan, "problems": list(problems),
                      "rejections": [dict(x) for x in rejections]})
        if len(_seen) == 1:
            return {"kind": "illegal",
                    "detail": "invalid_result: 必填键 `beat_ops` 是 null——"
                              "schema 里它是 array，`null` 不是合法取值"}
        return {"kind": "applied", "plan": _fixed}

    _r = _controller(_revise).run(_base)
    assert _r["stop_reason"] == pr.STOP_OK and _r["plan"] is _fixed, _r
    assert _r["calls"] == 2, "非法那一轮**照样扣次数**，但不再是终局"
    # **第二次请求确实含拒收原因**（这是"反馈"的落地证据，不是"记了但没用"）
    assert _seen[1]["rejections"], "第二轮必须收到拒收记录"
    assert "beat_ops" in _seen[1]["rejections"][0]["detail"], _seen[1]["rejections"]
    assert _seen[1]["rejections"][0]["round"] == 1
    # 拒收记录与**验收问题**分账：验收问题说的是计划哪里不合格，两条都在但不混
    assert [x["code"] for x in _seen[1]["problems"]] == ["carrier_fact_line_mismatch"]
    assert _r["rejections"] and all(x["kind"] == "illegal" for x in _r["rejections"])
    assert [x["code"] for x in _r["problems"]] == []


def test_g01b_the_illegal_round_never_changes_the_plan_or_the_cache():
    """非法响应**绝不采纳**：基线计划逐字不变，且不以任何形式被写回."""
    _base = {"ok": False, "shot_beats": ["B1"], "marker": "baseline"}
    _before = json.dumps(_base, ensure_ascii=False, sort_keys=True)

    def _revise(plan, problems, budget, rejections):
        return {"kind": "illegal", "detail": "invalid_result: 未知操作键 type（只支持 …）"}

    _r = _controller(_revise, max_calls=3).run(_base)
    assert _r["plan"] is _base, "返回的是**同一份基线对象**，不是改了一半的候选"
    assert json.dumps(_base, ensure_ascii=False, sort_keys=True) == _before
    assert _r["history"] and all(x.get("rejected") for x in _r["history"]), _r["history"]


def test_g02_consecutive_illegal_until_the_quota_runs_out_stays_a_response_violation():
    """**连续非法耗尽**：主终局是响应违约 + 额度耗尽另记；不冒充"故事不可行"."""
    _base = {"ok": False, "shot_beats": ["B1"], "marker": "baseline"}

    def _revise(plan, problems, budget, rejections):
        return {"kind": "illegal",
                "detail": "invalid_result: 未知操作键 type（只支持 dialogue_ops、beat_ops…）"}

    _r = _controller(_revise, max_calls=3).run(_base)
    assert _r["stop_reason"] == pr.STOP_ILLEGAL, _r["stop_reason"]
    assert _r["calls"] == 3 and _r["quota_exhausted"] is True, _r
    assert _r["plan_level_conclusion"] is False, "响应违约不是「计划不可用」的结论"
    assert _r["residual_class"] == pr.RESIDUAL_CONTRACT, "基线问题照旧另记，不当终局原因"
    assert len(_r["rejections"]) == 3, _r["rejections"]
    assert "额度已耗尽" in _r["history"][-1]["detail"], _r["history"][-1]
    assert "响应违约" in _r["history"][-1]["detail"], _r["history"][-1]
    # 主原因（诊断那一栏）也必须说"响应违约 + 额度耗尽"，而不是基线的内容问题
    _detail = pr.failure_detail(_r)
    assert "拒收" in _detail and "额度已耗尽" in _detail, _detail


def test_g03_the_deadline_wins_over_retrying_after_an_illegal_response():
    """**非法后截止**：截止时刻优先——过点不再发 POST、不采纳，终局是时间门."""
    class _Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

        def advance(self, dt):
            self.t += dt

        def deadline_in(self, sec):
            return self.t + float(sec)

    _clk = _Clock()
    _base = {"ok": False, "shot_beats": ["B1"]}
    _calls: list = []

    def _revise(plan, problems, budget, rejections):
        _calls.append(1)
        _clk.advance(40.0)                     # 这一次规划调用把时间用过了截止
        return {"kind": "illegal", "detail": "invalid_result: 未定义的键 type"}

    _r = _controller(_revise, clock=_clk, max_calls=3).run(_base)
    assert _r["stop_reason"] == pr.STOP_DEADLINE and _r["deadline_exhausted"] is True, _r
    assert len(_calls) == 1, "过点后**不再发 POST**（重试也要先过时间门）"
    assert _r["plan"] is _base and _r["rejections"] == [], "那一轮按截止收口，不计入拒收反馈"


def test_g04_a_transport_failure_keeps_its_own_attribution_and_is_not_retried():
    """**传输失败对照**：独立归因（`call_failed`）、不重发、拒收记录为空."""
    _base = {"ok": False, "shot_beats": ["B1"]}
    _calls: list = []

    def _revise(plan, problems, budget, rejections):
        _calls.append(1)
        return {"kind": "call_failed", "detail": "call_failed: transport reset at gateway"}

    _r = _controller(_revise).run(_base)
    assert _r["stop_reason"] == pr.STOP_CALL_FAILED and _r["calls"] == 1, _r
    assert len(_calls) == 1, "通道失败不是响应违约，重发修不好"
    assert _r["rejections"] == [] and _r["plan_level_conclusion"] is False
    assert "transport reset" in pr.failure_detail(_r)


def test_g05_an_applied_round_without_a_plan_is_a_response_violation_too():
    """`applied` 却没给计划 ⇒ 同样是响应违约（反馈 + 继续），不是"额度用尽"."""
    _base = {"ok": False, "shot_beats": ["B1"]}
    _fixed = {"ok": True, "shot_beats": ["B1", "B2 修好的那条"]}
    _seen: list = []

    def _revise(plan, problems, budget, rejections):
        _seen.append([dict(x) for x in rejections])
        if len(_seen) == 1:
            return {"kind": "applied"}                      # 形态违约：没有 plan
        return {"kind": "applied", "plan": _fixed}

    _r = _controller(_revise).run(_base)
    assert _r["stop_reason"] == pr.STOP_OK and _r["plan"] is _fixed, _r
    assert _r["calls"] == 2 and _seen[1] and "却没给出计划" in _seen[1][0]["detail"], _seen


# ---------------------------------------------------------------------------
# ② 首版入口：结构不合格 ⇒ 具体诊断 + 同一额度内重请求完整首版
# ---------------------------------------------------------------------------
def test_g06_a_structurally_bad_first_version_is_re_requested_with_a_concrete_reason(
        tmp_path, monkeypatch):
    """首版结构不合格（ch2 形态：`shot_beats` 是对象数组）⇒ 反馈具体字段与项号后重问."""
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_beats_as_objects(), _legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and _r["failure"] is None, _r
    assert _r["plan"]["shot_beats"] == _legal_plan()["shot_beats"]
    assert len(_svc.reqs) == 2, "首版 1 次 + 按反馈重问 1 次"
    # **重问的是完整首版**（同一份 schema），不是把不安全的对象丢进操作应用器
    _retry = _svc.reqs[1]
    assert _retry.json_schema is o.OUTLINE_SCHEMA, "重请求必须是完整首版计划，不是操作"
    assert "请**重新给出完整计划**" in _retry.user_prompt, _retry.user_prompt[-600:]
    assert "shot_beats" in _retry.user_prompt and "第 1 项" in _retry.user_prompt
    assert "不是字符串" in _retry.user_prompt, _retry.user_prompt[-600:]
    assert _r["revision"]["calls"] == 1, "重请求花掉一次规划机会（与修订共用同一张表）"
    assert json.loads(_cache.read_text(encoding="utf-8"))["outline"] == _r["plan"]


def test_g07_a_first_version_that_keeps_failing_exhausts_the_same_quota(monkeypatch):
    """连续不合格 ⇒ 额度耗尽后以**响应违约**收口，且每次的原因都留档."""
    _bad = {"main_events": [], "climax": "", "shot_beats": []}
    _svc = _patch(monkeypatch, [_bad] * (_N + 1))
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is False
    assert _r["failure"]["code"] == "outline_invalid", _r["failure"]
    assert _r["failure"]["detail"].startswith("首版结构校验未通过——"), _r["failure"]["detail"]
    assert "main_events" in _r["failure"]["detail"], _r["failure"]["detail"]
    assert len(_r["failure"]["problems"]) >= 2, _r["failure"]["problems"]
    assert len(_r["failure"]["attempts"]) == _N + 1, "首版 1 次 + 额度内重问 3 次"
    assert len(_svc.reqs) == _N + 1 and _r["revision"]["calls"] == _N, _r["revision"]
    assert _r["revision"]["quota_exhausted"] is True, _r["revision"]
    assert _r["plan"] is None


def test_g08_the_first_version_retry_spends_the_same_quota_as_revisions(monkeypatch):
    """**同一份额度**：首版重请求花掉的机会，修订那边就不能再花一次."""
    _cache = None
    # 首版：结构不合格（1 次）→ 重问：结构合格但验收不合格（1 次）→ 修订额度只剩 2 次，
    # 且这 2 次都回非法操作（`_broken_plan()` 是整份计划，不是操作）
    _svc = _patch(monkeypatch, [_beats_as_objects(), _legal_plan(required_facts=[
        "甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE]),
        _legal_plan()] + [_legal_plan()] * (_N - 1))
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is False
    assert len(_svc.reqs) == _N + 1, "首版 1 + 额度内重问/修订 %d" % _N
    # `calls` 是**这张共用表**花掉的机会（1 次首版重问 + 2 轮修订）；`rounds` 才是修订
    # 实际跑了几轮——两者差 1，正是"首版重问吃了同一份额度"的证据
    assert _r["revision"]["calls"] == _N and _r["revision"]["rounds"] == _N - 1, _r["revision"]
    assert _r["revision"]["stop_reason"] == "illegal_response", _r["revision"]
    assert _r["revision"]["quota_exhausted"] is True, _r["revision"]
    assert _r["revision"]["rejections"], _r["revision"]["rejections"]


def test_g09_the_diagnosis_carries_the_concrete_reason_and_the_retry_feedback(
        tmp_path, monkeypatch):
    """终局诊断（`step01_plan_infeasible.json`）必须带**可回灌的具体记录**."""
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _patch(monkeypatch, [_beats_as_objects()] * (_N + 1))
    _res = o.generate_outline_result(_SRC)
    with pytest.raises(Exception):
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    _doc = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _doc["conclusion"] == "outline_invalid" and _doc["verdict"] == "unevaluable"
    _fp = _doc["failure_problems"]
    assert _fp and _fp[0]["path"] == "shot_beats" and _fp[0]["index"] == 1, _fp
    assert "不是字符串" in _fp[0]["reason"] and "必须是**字符串**" in _fp[0]["reason"], _fp[0]
    assert "shot_beats" in _doc["detail"], _doc["detail"]
    # **没有**任何正式产物被改动（诊断自己证明"计划未被改动、未被缓存"）
    assert not (tmp_path / "chapter_outline.cache.json").exists()
    assert not (tmp_path / "script_result.json").exists()


def test_g10_an_unknown_key_is_rejected_and_fed_back_not_silently_dropped(monkeypatch):
    """`type` 这类 schema 之外的键 ⇒ 拒收 + 反馈给下一轮（**不静默丢弃**）."""
    _svc = _patch(monkeypatch, [
        _legal_plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE]),
        {"type": "json_object",                                  # F 轮实测的违约形态
         "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
    ])
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is True and _r["revision"]["calls"] == 2, _r["revision"]
    assert len(_svc.reqs) == 3
    assert "type" in _svc.reqs[2].user_prompt, "拒收的**具体键名**必须回灌到下一轮"
    assert "被本地校验拒收" in _svc.reqs[2].user_prompt, _svc.reqs[2].user_prompt[-800:]
    assert _r["revision"]["rejections"][0]["detail"].startswith("invalid_result:"), \
        _r["revision"]["rejections"]


# ---------------------------------------------------------------------------
# ③ 合法对照：没有拒收时提示与请求**逐字不变**（改造没有污染正常路径）
# ---------------------------------------------------------------------------
def test_g11_a_clean_first_version_still_costs_one_call_and_keeps_the_prompt(
        tmp_path, monkeypatch):
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_legal_plan()])
    # 2026-10 逐例迁移（本文件试点）：核验协议演化后，本用例的场景是"干净首版 1 次规划"，
    # 核验必须**不占用** `_svc` 的顺序回放预算、且给出合法结论，否则会退化成
    # `核验调用失败：stub: 没有更多响应` → `executable=False`。
    # 这里在**调用点**显式注入（而非文件级统一注入），答案随该用例场景而定。
    from tests import verification_helpers as vh
    _r = o.generate_outline_result(
        _SRC, cache_path=_cache,
        verify=lambda view, digest: vh.legal_record_for(view, _SRC, plan_digest=digest))
    assert _r["executable"] is True and len(_svc.reqs) == 1
    assert "【上一版计划被本地结构校验拒收" not in _svc.reqs[0].user_prompt
    assert "被本地校验拒收" not in _svc.reqs[0].user_prompt


def test_g11b_a_legal_revision_request_has_no_rejection_block(monkeypatch):
    _svc = _patch(monkeypatch, [
        _legal_plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE]),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
    ])
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is True and len(_svc.reqs) == 2
    assert "被本地校验拒收" not in _svc.reqs[1].user_prompt, "没有拒收就不许出现拒收块"
    assert "未通过的项目" in _svc.reqs[1].user_prompt, "验收问题的反馈必须照旧"


def test_g12_a_truncated_rejection_says_how_many_were_left_out(monkeypatch):
    """拒收原因截断到 6 条时**必须说出还剩几条**（"就这 6 条"与"还有别的"不是一回事）."""
    # 8 条同类形态问题（`replace_with` 该是字符串却给了整数；`index` 走安全归一，
    # 不在这里判）⇒ 提示只列前 6 条，剩下 2 条**必须显式说明**
    _ops = {"type": "json_object",
            "dialogue_ops": [{"index": _i, "replace_with": 123} for _i in range(1, 9)]}
    _svc = _patch(monkeypatch, [
        _legal_plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE]),
        _ops,
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
    ])
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is True, _r
    _det = _r["revision"]["rejections"][0]["detail"]
    # 形态问题共 9 条（1 条未定义键 + 8 条 `replace_with` 类型错）⇒ 只列 6 条，
    # 但**总数写在最前面**：消费方按前缀截断，尾巴上的"另有 N 条"等于没写
    assert "未知操作键" in _det and "9 条形态问题（只列前 6 条）" in _det, _det
    assert "type" in _svc.reqs[2].user_prompt, "拒收原因（含截断说明）要回灌到下一轮"


# ---------------------------------------------------------------------------
# ④ 逐轮记录要**看得见**（日志）与**留得下**（诊断）——`log` 口子与留档宽度
# ---------------------------------------------------------------------------
def _broken_carrier_plan():
    """结构合格、**验收不合格**（承载错位）的计划：用来把流程推进到修订控制器."""
    return _legal_plan(required_facts=[
        "甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE])


def test_g13_the_revision_rounds_reach_the_log(monkeypatch, capsys):
    """逐轮拒收要落日志：`log` 口子过去在生产入口从没接上，整轮轨迹只在返回对象里."""
    _patch(monkeypatch, [_broken_carrier_plan()] + [_legal_plan()] * _N)
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is False and _r["revision"]["calls"] == _N, _r["revision"]
    _out = capsys.readouterr().out
    assert "[REVISION]" in _out, _out[-800:]
    assert "响应被拒收" in _out and "额度已耗尽" in _out, _out[-800:]


def test_g14_the_diagnosis_keeps_every_round_rejection(tmp_path, monkeypatch):
    """**留档要宽**：三轮各自的拒收原因都要进诊断（`detail` 只留最后一条）."""
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _patch(monkeypatch, [_broken_carrier_plan()] + [_legal_plan()] * _N)
    _res = o.generate_outline_result(_SRC)
    with pytest.raises(Exception):
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    _doc = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _doc["conclusion"] == "illegal_response"
    _rej = _doc["replan_rejections"]
    assert [x["round"] for x in _rej] == list(range(1, _N + 1)), _rej
    assert all("未知操作键" in x["detail"] for x in _rej), _rej
    assert _doc["baseline_problems"], "基线问题仍要另列（与拒收记录分账）"


def test_g02b_a_long_last_rejection_is_marked_not_spliced_into_a_false_sentence():
    """复核 2026-10-04（章 1 run 20261004T052835-7596）：最高层的停止原因**也要标出省略量**.

    改前实测：最后一轮详情被 `[:160]` 硬切，紧跟的 `）——额度已耗尽…` 于是接在**半句**后面：

        ……但该节拍标的是「载体：台词2」——一条节拍）——额度已耗尽；这是**响应违约**…

    读起来像"一条节拍）——额度已耗尽"是那条问题本身，真正的规则（「一条节拍只能有**一个**
    载体，画面与台词/旁白必须分属**不同节拍**」）整句丢失，问题清单与停止原因的边界也不见了。
    """
    # 夹具用**本轮实跑那句逐字原文**（192 字，超过展示上限 160 ⇒ 真的触发截断）
    _detail = ("本轮响应仍有 **2 条**问题（**逐条列全**，此前只回第一条）：\n"
               "1) F6 声明的承载节拍 B3 的正文／信息目的与这条事实**没有交集**"
               "（共享要素不足：该节拍讲的是另一件事）："
               "「夜色舟中，听香服侍杨旭，二人缠绵亲昵。」\n"
               "2) F8 声明由 B8 的**画面**承载，但该节拍标的是「载体：台词2」——"
               "一条节拍只能有**一个**载体，画面与台词/旁白必须分属**不同节拍**")
    assert len(_detail) > 160, "夹具必须真的超过展示上限：%d" % len(_detail)

    def _revise(plan, problems, budget, rejections):
        return {"kind": "illegal", "detail": _detail}

    _r = _controller(_revise, max_calls=3).run({"ok": False, "shot_beats": ["B1"]})
    assert _r["stop_reason"] == pr.STOP_ILLEGAL, _r["stop_reason"]
    _note = _r["history"][-1]["detail"]
    # ① 主结论须完整保留；截断的拒收正文与控制器结论要有明确边界。
    assert "连续 3 次响应被拒收——主终局是**响应违约**" in _note, _note
    assert "不是「故事不可行」；额度已耗尽（最后一次：" in _note, _note
    # ② 截断必须显式说明省略量，不能把半句伪装成完整原因。
    assert "字未显示" in _note, _note
    # ③ 展示有界**不许**牵动留档：拒收记录仍必须是完整原文
    assert _r["rejections"][-1]["detail"] == _detail, "拒收记录必须完整（留档要宽）"


def test_g22_the_fatal_summary_is_single_line_and_complete():
    """复核 2026-10-04：`tb.splitlines()[-1]` 在异常消息**含换行**时只剩最后一行**片断**.

    计划层的停止原因恰好是多行的（问题逐条列全），实测控制台上只打出：

        [FATAL] 2) F8 声明由 B8 的**画面**承载，但该节拍标的是「载体：台词2」——一条节拍）——额度已耗尽；这是**响应违约**（模型没

    前半截（"连续 2 次响应被拒收…1) F6 …"）在别的行上，读失败摘要的人看不到。
    """
    from step01_script_gen import main as _m

    class _Boom(Exception):
        pass

    _msg = ("连续 2 次响应被拒收（最后一次：本轮响应仍有 **2 条**问题：\n"
            "1) F6 声明…没有交集\n2) F8 声明…一条节拍只能有一个载体）——额度已耗尽")
    _out = _m._fatal_summary(_Boom(_msg), "Traceback (most recent call last):\n  ...")
    assert "\n" not in _out, "失败摘要必须单行（与 shared.error_fmt 的单行约定一致）"
    for _frag in ("_Boom", "连续 2 次响应被拒收", "1) F6 声明", "2) F8 声明",
                  "一条节拍只能有一个载体", "额度已耗尽"):
        assert _frag in _out, "每一段都要在（缺 %r）：%s" % (_frag, _out)
    # 拿不到异常对象时退回 traceback 末行——**不编造**内容
    assert _m._fatal_summary(None, "Traceback...\nValueError: boom") == "ValueError: boom"
    assert _m._fatal_summary(None, "") == ""


def test_g14b_the_stop_projection_marks_omitted_text_but_the_diagnosis_keeps_it_all(
        tmp_path, monkeypatch):
    """复核 2026-10-04：失败投影走**有界且标注**的形态；诊断文件仍记**完整**详情.

    "留档要宽、展示有界"是同一条口径的两半——修展示**不许**把留档一起削掉。
    """
    from step01_script_gen import main as _m
    from step01_script_gen.plan_feasibility import PlanInfeasibleError

    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _detail = "长期详情" + "甲" * 300 + "；真正的结论在最后一句。"
    _res = {"executable": False,
            "failure": {"code": "illegal_response", "detail": _detail},
            "plan": {"main_events": ["甲"]},
            "acceptance": {"problems": []}}
    with pytest.raises(PlanInfeasibleError) as _ei:
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    _msg = str(_ei.value)
    assert "字未显示" in _msg, _msg
    _doc = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _doc["detail"] == _detail, "诊断文件的详情不许跟着展示口径一起截"


# ---------------------------------------------------------------------------
# ⑤ 复核之六 P1-a：**规划截止只有一份**（本阶段上限早于整次截止时不许被绕过）
# ---------------------------------------------------------------------------
def test_g15_the_local_planning_deadline_cannot_be_bypassed_by_the_run_deadline(
        tmp_path, monkeypatch):
    """`revision_deadline_sec` 早于整次截止：过点 ⇒ 终局 `deadline_exhausted`、**不写缓存**.

    旧写法只有首版之外看这张表：控制器判 `deadline_exhausted`，外层却按整次截止算资格
    ⇒ `executable=True` 且**真的写出了缓存**（离线实测复现）。这里桩故意慢于局部上限。
    """
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_legal_plan()])
    _svc.delay = 0.05
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=0.02)
    assert _r["executable"] is False, _r["acceptance"]
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]
    assert _r["revision"]["stop_reason"] == "deadline_exhausted", _r["revision"]
    assert _r["revision"]["deadline_exhausted"] is True, _r["revision"]
    assert _r["cache"]["written"] is False and not _cache.exists(), "过点不许写缓存"
    assert _svc.reqs and _svc.reqs[0].deadline_monotonic < _now + 60.0, \
        "首版请求必须带**更早的**那份截止（本阶段上限），不是整次截止"


def test_g15b_the_deadline_wins_even_when_the_controller_stops_after_a_revision(
        tmp_path, monkeypatch):
    """修订**返回后**过点：控制器判 `deadline_exhausted` ⇒ 外层同样不许下发/写缓存."""
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_broken_carrier_plan(), _legal_plan()])
    _svc.delay = 0.05                       # 两次调用合计 ~0.10s > 0.08s 的上限
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=0.08)
    assert _r["executable"] is False, _r["acceptance"]
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]
    assert _r["revision"]["deadline_exhausted"] is True, _r["revision"]
    assert not _cache.exists()


def test_g15c_a_deadline_hit_is_not_executable_even_with_a_perfect_cache(tmp_path, monkeypatch):
    """缓存命中对照：过点后**即使**缓存里那份计划可用，也不许下发、不许重写缓存."""
    _cache = tmp_path / "c.json"
    _patch(monkeypatch, [_legal_plan()])
    _r1 = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r1["executable"] is True and _cache.exists()
    _before = _cache.read_bytes()
    _svc2 = _patch(monkeypatch, [_legal_plan()])
    # 缓存命中时**一次模型调用都没有**，"慢响应"造不出过点——用"这张表进场即过期"
    # 的确定性入口（本阶段上限 0 ⇒ `deadline_at = 建表时刻`）。
    _now = time.monotonic()
    _r2 = o.generate_outline_result(_SRC, cache_path=_cache,
                                    deadline_monotonic=_now + 60.0,
                                    run_deadline_at=_now + 60.0,
                                    revision_deadline_sec=0.0)
    assert _r2["cache"]["hit"] is True and _svc2.reqs == [], _r2["cache"]
    assert _r2["executable"] is False and _r2["acceptance"]["verdict"] == "deadline", _r2
    assert _r2["failure"]["code"] == "deadline_exhausted", _r2["failure"]
    assert _cache.read_bytes() == _before, "过点不许动缓存文件（含留档/覆盖）"


def test_g15d_a_local_deadline_that_is_not_exhausted_changes_nothing(tmp_path, monkeypatch):
    """**合法对照**：局部上限没到（整次截止更早）⇒ 一切照旧，可执行且写缓存."""
    _cache = tmp_path / "c.json"
    _patch(monkeypatch, [_legal_plan()])
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 30.0,
                                   run_deadline_at=_now + 30.0,
                                   revision_deadline_sec=600.0)
    assert _r["executable"] is True and _r["failure"] is None, _r
    assert _r["cache"]["written"] is True and _cache.exists()


# ---------------------------------------------------------------------------
# ⑥ 复核之六 P1-b：声明独立修改不许被**基线旧问题**拒收（责任范围 = 本批）
# ---------------------------------------------------------------------------
_ORPHAN_FACTS = ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE]


_SRC2 = "开头。%s%s事件丙。" % (_LINE, _LINE2)


def _plan_with_orphan_dialogue():
    """基线里**本来就有**一条无人承载的台词（D2）：合法修订的对照面."""
    return _legal_plan(required_facts=_ORPHAN_FACTS,
                       dialogue_lines=[_LINE, _LINE2],
                       shot_beats=["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                                   "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
                                   "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE])


def test_g16_a_declaration_only_revision_is_not_rejected_for_a_baseline_orphan():
    """只改声明 ⇒ 用真实应用器：必须**应用成功**（基线孤儿由验收链报，不在这里拒收）."""
    _p = _plan_with_orphan_dialogue()
    _new, _why = o.apply_replan_ops(
        copy.deepcopy(_p), {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}, _SRC2)
    assert _new is not None and _why == "", _why
    assert o.plan_acceptance_problems(_new, _SRC2), "剩下的基线问题仍由验收链报告"
    # **不放宽**：动台词/节拍的批次仍要过闭合检查（同一条响应、加一条台词操作 ⇒ 拒收）
    _bad, _why2 = o.apply_replan_ops(
        copy.deepcopy(_plan_with_orphan_dialogue()),
        {"dialogue_ops": [{"index": 1, "replace_with": _LINE}],
         "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}, _SRC2)
    assert _bad is None, "改台词时闭合规则必须继续生效"


def test_g16b_the_partial_revision_is_applied_then_the_rest_is_fed_back(monkeypatch):
    """**贯通**：合法部分修订 → 应用 + 完整验收 → 剩余问题进下一轮 → 下一轮完成."""
    _p = _plan_with_orphan_dialogue()
    _svc = _patch(monkeypatch, [
        # ① 首版：只有 F1 承载错指 + 基线孤儿台词（结构合格 ⇒ 直接进验收）
        _p,
        # ② 只声明 fact_carriers：合法修订，应用成功但 D2 仍无人承载
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
        # ③ 补上承载 D2 的节拍：整批完成后验收为空
        {"beat_ops": [{"index": 3, "op": "replace", "new_carrier": "台词2",
                       "new_text": "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词2" % _LINE2}],
         "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
    ])
    _r = o.generate_outline_result(_SRC2)
    assert len(_svc.reqs) == 3, "首版 + 两轮修订"
    assert _r["revision"]["calls"] == 2, _r["revision"]
    # 第二轮请求必须带**剩余**问题（D2 无人承载）——这正是"反馈下一轮"的落地
    _second = _svc.reqs[2].user_prompt
    assert "D2" in _second and "未通过的项目" in _second, _second[-900:]
    assert _r["executable"] is True and _r["failure"] is None, _r
    assert _r["acceptance"]["problems"] == []


# ---------------------------------------------------------------------------
# ⑦ 复核之六 P2：反馈**保留完整问题身份**（旧写法 `[:12]` 静默省略）
# ---------------------------------------------------------------------------
_LONG = "他一步一顿地走进那间堆满旧物的屋子，四下打量了很久很久，最后什么也没有说出口。"
_SRC3 = "开头。%s%s" % (_LINE, _LONG * 12)


def _many_problems_plan():
    """>12 项、**承载与预算混合**的问题计划（承载错指 14 条 + 孤儿台词 + 超预算）."""
    return _legal_plan(
        required_facts=["事实%d｜原文证据：“%s”｜承载：B3/台词1" % (i, _LINE)
                        for i in range(1, 15)],
        # 11×11.4s + 1 条短句 ≈ 126s > 120s：每条都能单镜表示，问题才是**总量**
        # `budget_infeasible`；整条 `_LONG`（13.13s）会先落进逐条不可表示。
        dialogue_lines=[_LINE] + [_LONG[:45]] * 13)


def test_g17_the_feedback_keeps_every_problem_identity(monkeypatch):
    """实际请求断言：总数、未展开项的类别计数与身份、以及本轮修复范围都要在提示里."""
    _svc = _patch(monkeypatch, [_many_problems_plan()] + [_legal_plan()] * 3)
    _r = o.generate_outline_result(_SRC3)
    assert len(_svc.reqs) >= 2, "首版 + 至少一轮修订"
    _feedback = _svc.reqs[1].user_prompt
    assert "未通过的项目" in _feedback, _feedback[-500:]
    # 展开的仍是前 12 条（带判据依据），但**总数**必须写出来
    assert _feedback.count("\n- ") >= 12, "前 12 项要逐条展开"
    _head = [l for l in _feedback.splitlines() if "未通过的项目" in l]
    assert _head and "共" in _head[0] and "项" in _head[0], _head
    _total = int("".join(c for c in _head[0].split("共")[1].split("项")[0] if c.isdigit()))
    assert _total > o.FEEDBACK_DETAIL_LIMIT, ("问题总数必须大于展开上限才谈得上省略", _head[0])
    # **未展开项**：条数 + 类别计数 + 身份（code｜路径）都要给，且声明它们在本轮范围内
    assert "未展开的 %d 项" % (_total - o.FEEDBACK_DETAIL_LIMIT) in _feedback, _feedback[-900:]
    assert "按类别计数" in _feedback, _feedback[-900:]
    assert "carrier_fact_line_mismatch×" in _feedback, "类别计数里必须出现承载类"
    # 身份清单那一行**自己**必须带 `code｜字段路径`（复核之十一：请求末尾还会附
    # **逐字基线** JSON，所以不许再用"最后 40 行"这种与排版耦合的窗口来断言）
    _id_lines = [l for l in _feedback.splitlines() if "它们的身份" in l]
    assert _id_lines, "未展开项要给出身份清单"
    assert "｜" in _id_lines[0] and "（" in _id_lines[0], _id_lines[0][:200]
    assert "同样" in _feedback and "在本轮验收范围内" in _feedback, "必须说明它们也在本轮范围内"
    assert "下一轮继续反馈" in _feedback, "必须说明没改完的会继续反馈"
    # 预算轴确实混在里面（这条基线同时有承载与预算问题）
    assert any("budget" in str(x.get("code") or "")
               for x in o.plan_acceptance_problems(_r["plan"], _SRC3)
               if isinstance(x, dict)) or _r["executable"] is False, _r["acceptance"]["codes"]
    assert "budget_infeasible" in _r["acceptance"]["codes"], _r["acceptance"]["codes"]


def test_g15e_an_invalid_response_past_the_deadline_is_reported_as_the_time_gate(
        tmp_path, monkeypatch):
    """**非法响应 + 过点**：终局统一为 `deadline_exhausted`（不拿"首版不合格"顶替）."""
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_beats_as_objects()])
    _svc.delay = 0.05
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=0.02)
    assert len(_svc.reqs) == 1, "过点后不许再重问"
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]
    assert _r["revision"]["deadline_exhausted"] is True, _r["revision"]
    assert _r["executable"] is False and not _cache.exists()
    assert _r["failure"]["attempts"], "被拒的那一次仍要留档（原因不能被时间门抹掉）"


# ---------------------------------------------------------------------------
# ⑧ 复核之七：**发布截止守卫**——缓存准备期间过点也不许发布
# ---------------------------------------------------------------------------
def test_g18_a_plan_expiring_during_cache_prep_is_never_published(tmp_path, monkeypatch):
    """合法计划在**缓存留档/轮转**期间过点 ⇒ 不覆盖旧缓存、不返回 `executable=True`."""
    _cache = tmp_path / "c.json"
    # 旧缓存（source_hash 不匹配 ⇒ 这一轮必须重新规划，于是**会走到发布动作**）
    _cache.write_text(json.dumps({"source_hash": "旧键",
                                  "plan_contract_version": o.PLAN_CONTRACT_VERSION,
                                  "outline": _legal_plan()}, ensure_ascii=False),
                      encoding="utf-8")
    _before = _cache.read_bytes()
    _patch(monkeypatch, [_legal_plan()])

    def _slow_rotate(*a, **k):
        time.sleep(0.09)                      # 留档/轮转期间把时间用光
        return None

    monkeypatch.setattr(o, "_rotate_outline_cache_on_regen", _slow_rotate)
    _now = time.monotonic()
    _r2 = o.generate_outline_result(_SRC, cache_path=_cache,
                                    deadline_monotonic=_now + 60.0,
                                    run_deadline_at=_now + 60.0,
                                    revision_deadline_sec=0.05)
    assert _r2["executable"] is False, _r2["acceptance"]
    assert _r2["failure"]["code"] == "deadline_exhausted", _r2["failure"]
    assert _r2["revision"]["deadline_exhausted"] is True, _r2["revision"]
    assert _r2["cache"]["written"] is False, _r2["cache"]
    assert _cache.read_bytes() == _before, "准备期间过点 ⇒ 旧缓存逐字节保留"


def test_g18b_a_plan_whose_prep_finishes_in_time_is_published(tmp_path, monkeypatch):
    """**合法对照**：准备期间没过点 ⇒ 照常发布（守卫没有变成"一律不写"）."""
    _cache = tmp_path / "c.json"
    _cache.write_text(json.dumps({"source_hash": "旧键",
                                  "plan_contract_version": o.PLAN_CONTRACT_VERSION,
                                  "outline": _legal_plan()}, ensure_ascii=False),
                      encoding="utf-8")
    _patch(monkeypatch, [_legal_plan()])
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=30.0)
    assert _r["executable"] is True and _r["failure"] is None, _r
    assert _r["cache"]["written"] is True and _cache.exists()

# ---------------------------------------------------------------------------
# ⑨ 复核之八：**临时文件准备**与**替换**分开——准备期间过点不许发布
# ---------------------------------------------------------------------------
def _cache_with_old_plan(path):
    path.write_text(json.dumps({"source_hash": "旧键",
                                "plan_contract_version": o.PLAN_CONTRACT_VERSION,
                                "outline": _legal_plan()}, ensure_ascii=False),
                    encoding="utf-8")
    return path.read_bytes()


def test_g19_a_plan_expiring_during_the_temp_write_is_never_published(tmp_path,
                                                                     monkeypatch):
    """**临时文件写入期间**过点 ⇒ 删临时文件、保留旧缓存、不返回 executable.

    复核之八 P1 实测形态：注入写入延迟后返回 `deadline_exhausted`、`executable=False`，
    而 `cache.written=True`、旧缓存已被覆盖——终局检查只挡住了**下发**，没挡住**发布**。
    判据必须落在"准备完成后、替换之前"那道门上（注入点就是准备本身）。
    """
    import shared.json_io as _jio

    _cache = tmp_path / "c.json"
    _before = _cache_with_old_plan(_cache)
    _patch(monkeypatch, [_legal_plan()])
    _real = _jio.prepare_json_swap

    def _slow(path, obj, **kw):
        time.sleep(0.09)                       # 准备期间的耗时
        return _real(path, obj, **kw)

    monkeypatch.setattr(_jio, "prepare_json_swap", _slow)
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=0.05)
    assert _r["executable"] is False, _r["acceptance"]
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]
    assert _r["revision"]["deadline_exhausted"] is True, _r["revision"]
    assert _r["cache"]["written"] is False, _r["cache"]
    assert _cache.read_bytes() == _before, "过点 ⇒ 旧缓存逐字节保留（不许被换掉）"
    assert not list(tmp_path.glob("c.json.tmp")), "临时文件必须删掉，不留半截证据"


def test_g19b_a_temp_write_that_finishes_in_time_is_still_published(tmp_path,
                                                                    monkeypatch):
    """**合法对照**：准备期间没过点 ⇒ 照常替换（守卫没有退化成"一律不写"）."""
    import shared.json_io as _jio

    _cache = tmp_path / "c.json"
    _cache_with_old_plan(_cache)
    _patch(monkeypatch, [_legal_plan()])
    _now = time.monotonic()
    _r = o.generate_outline_result(_SRC, cache_path=_cache,
                                   deadline_monotonic=_now + 60.0,
                                   run_deadline_at=_now + 60.0,
                                   revision_deadline_sec=30.0)
    assert _r["executable"] is True and _r["failure"] is None, _r
    assert _r["cache"]["written"] is True and _cache.exists(), _r["cache"]
    assert _jio._swap_tmp_path(_cache).exists() is False, "替换后不许留下临时文件"


def test_g20_a_replace_failure_keeps_the_old_cache_and_the_plan(tmp_path, monkeypatch):
    """**替换**这一步失败 ⇒ 旧缓存原样、计划照旧可用，只降级缓存可用性（不留临时文件）."""
    import shared.json_io as _jio

    _cache = tmp_path / "c.json"
    _before = _cache_with_old_plan(_cache)
    _patch(monkeypatch, [_legal_plan()])

    def _bad_replace(src, dst):
        raise OSError("replace failed（注入）")

    monkeypatch.setattr(_jio.os, "replace", _bad_replace)
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and _r["failure"] is None, "缓存写失败不许改判计划"
    assert _r["cache"]["written"] is False and _r["cache"]["write_error"], _r["cache"]
    assert _cache.read_bytes() == _before, "旧缓存必须逐字节保留"
    assert not list(tmp_path.glob("c.json.tmp")), "失败后临时文件要被清理"

def test_g21_a_failed_temp_write_cleans_up_and_degrades_only_the_cache(tmp_path,
                                                                     monkeypatch):
    """**临时文件写入本身失败**（半截写入后抛错）⇒ 清残留、旧缓存原样、计划照旧可用.

    这是"临时写入"这个故障切点：`prepare_json_swap` 失败必须自己清掉半截文件，
    调用方把这一轮记成"缓存没写成"——**不改判计划**（缓存可用性 ≠ 计划可用性）。
    """
    import shared.json_io as _jio

    _cache = tmp_path / "c.json"
    _before = _cache_with_old_plan(_cache)
    _patch(monkeypatch, [_legal_plan()])
    _tmp = _jio._swap_tmp_path(_cache)
    _real_dump = _jio.json.dump

    def _half_write(obj, f, **kw):                 # 只截缓存这一份，别的照旧
        if isinstance(obj, dict) and "source_hash" in obj:
            f.write("{半截")                       # 先落一半
            raise ValueError("disk full（注入）")
        return _real_dump(obj, f, **kw)

    monkeypatch.setattr(_jio.json, "dump", _half_write)
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and _r["failure"] is None, "缓存写失败不许改判计划"
    assert _r["cache"]["written"] is False and _r["cache"]["write_error"], _r["cache"]
    assert _cache.read_bytes() == _before, "旧缓存逐字节保留"
    assert not _tmp.exists(), "半截临时文件必须被清掉"


# ---------------------------------------------------------------------------
# ⑩ S03（2026-11-21）：拒收反馈只回灌**最后一次成功应用之后**的轮次
# ---------------------------------------------------------------------------
def test_g23_the_request_after_an_applied_round_labels_applied_and_recent_rejections():
    """已应用轮次存在时：标题不再写"上一轮一个字节没改"，并列出已生效修订."""
    _rows = pr.RevisionRejections(
        [{"round": 4, "detail": "invalid_result: 未知键 delta.ops"}],
        applied_rounds=[
            {"round": 2, "kind": "applied", "label": "view_applied"},
            {"round": 3, "kind": "applied", "label": "source_basis_revision"},
        ])
    # 列表本体与旧接口同构（消费方仍可按 list 迭代/拷贝）
    assert isinstance(_rows, list) and [x["round"] for x in _rows] == [4]
    _block = o._rejection_feedback_block(_rows)
    assert "被本地校验拒收" in _block, _block
    assert "已生效的修订：第 2 轮（view_applied）、第 3 轮（source_basis_revision）" in _block, _block
    assert "这些改动已经应用，不要回退" in _block, _block
    assert "- 第 4 轮：invalid_result: 未知键 delta.ops" in _block, _block
    # 过时标题（"上一轮……一个字节没改"）对第 4 轮是假的，不许出现
    assert "上一轮你的回复被本地校验拒收" not in _block, _block
    # 合法对照：没有已应用轮次时，旧标题与旧排版逐字保留
    _plain = o._rejection_feedback_block([{"round": 2, "detail": "invalid_result: x"}])
    assert "上一轮你的回复被本地校验拒收：**计划一个字节都没有改**" in _plain, _plain
    # 仅一条时不需要条数说明；超过 3 条时显式说明只列最近 3 条
    assert "共 1 条" not in _block and "以下按轮次列出最近" not in _block, _block
    _many = o._rejection_feedback_block(
        [{"round": i, "detail": "invalid_result: r%d" % i} for i in range(1, 6)])
    assert "共 5 条，以下按轮次列出最近 3 条" in _many, _many
    assert "- 第 5 轮" in _many and "- 第 1 轮" not in _many, _many


def test_g24_the_controller_feeds_back_only_rejections_since_the_last_applied_round():
    """控制器：第 2 轮 applied 之后，第 3 轮请求不再看到第 1 轮拒收；已应用轮次要标明."""
    _base = {"ok": False, "shot_beats": ["B1"]}
    _fixed = {"ok": False, "shot_beats": ["B1", "B2"]}
    _final = {"ok": True, "shot_beats": ["B1", "B2", "B3"]}
    _seen = []

    def _accept(plan, novel_text=""):
        if plan.get("ok"):
            return []
        return [{"code": "carrier_fact_line_mismatch", "path": "required_facts[0]",
                 "reason": "承载错位", "detail": "承载错位", "basis": "统一验收"}]

    def _revise(plan, problems, budget, rejections):
        _seen.append({"rejections": [dict(x) for x in rejections],
                      "applied": list(getattr(rejections, "applied_rounds", []) or [])})
        _n = len(_seen)
        if _n == 1:
            return {"kind": "illegal", "detail": "invalid_result: 第 1 轮错误"}
        if _n == 2:
            return {"kind": "applied", "plan": _fixed,
                    "detail": "view_applied: 2 条残余"}
        if _n == 3:
            return {"kind": "illegal", "detail": "invalid_result: 第 3 轮错误"}
        return {"kind": "applied", "plan": _final, "detail": "view_applied: 0 条残余"}

    _b = pr.RevisionBudget(max_calls=4, deadline_sec=600.0)
    _r = pr.RevisionController(accept=_accept, revise=_revise, budget=_b).run(_base)
    assert _r["stop_reason"] == pr.STOP_OK and _r["plan"] is _final, _r
    # 第 2 轮请求：还没有应用过，第 1 轮拒收照旧回灌
    assert [x["round"] for x in _seen[1]["rejections"]] == [1], _seen[1]
    assert _seen[1]["applied"] == [], _seen[1]
    # 第 3 轮请求：第 2 轮已应用 ⇒ 第 1 轮拒收不再出现，并标明已应用轮次
    assert _seen[2]["rejections"] == [], _seen[2]
    assert [(x["round"], x["label"]) for x in _seen[2]["applied"]] == [(2, "view_applied")], \
        _seen[2]
    # 第 4 轮请求：第 3 轮刚被拒收（在第 2 轮应用之后）⇒ 只回灌这一条
    assert [x["round"] for x in _seen[3]["rejections"]] == [3], _seen[3]
    assert [(x["round"], x["label"]) for x in _seen[3]["applied"]] == [(2, "view_applied")], \
        _seen[3]
    # **留档不缩减**：结果里完整拒收记录（第 1、3 轮）与已应用轮次（第 2、4 轮）都在
    assert [x["round"] for x in _r["rejections"]] == [1, 3], _r["rejections"]
    assert [(x["round"], x["label"]) for x in _r["applied_rounds"]] == \
        [(2, "view_applied"), (4, "view_applied")], _r["applied_rounds"]


def test_g25_the_consecutive_rejection_count_starts_after_the_last_applied_round():
    """额度耗尽文案的"连续 N 次"只数最后一次应用之后的拒收；完整留档照旧全给."""
    _base = {"ok": False, "shot_beats": ["B1"]}
    _fixed = {"ok": False, "shot_beats": ["B1", "B2"]}
    _seen = []

    def _accept(plan, novel_text=""):
        return [{"code": "carrier_fact_line_mismatch", "path": "required_facts[0]",
                 "reason": "承载错位", "detail": "承载错位", "basis": "统一验收"}]

    def _revise(plan, problems, budget, rejections):
        _seen.append([dict(x) for x in rejections])
        _n = len(_seen)
        if _n == 1:
            return {"kind": "illegal", "detail": "invalid_result: 首轮违约"}
        if _n == 2:
            return {"kind": "applied", "plan": _fixed, "detail": "view_applied: 1 条残余"}
        return {"kind": "illegal", "detail": "invalid_result: 第 %d 轮违约" % _n}

    _b = pr.RevisionBudget(max_calls=4, deadline_sec=600.0)
    _r = pr.RevisionController(accept=_accept, revise=_revise, budget=_b).run(_base)
    assert _r["stop_reason"] == pr.STOP_ILLEGAL, _r["stop_reason"]
    # 完整留档：3 条拒收（第 1 轮 + 应用后的第 3、4 轮）都在
    assert [x["round"] for x in _r["rejections"]] == [1, 3, 4], _r["rejections"]
    _note = _r["history"][-1]["detail"]
    # 应用之后只有 2 次连续拒收 ⇒ 文案必须是 2，不是 3（过时计数会误导归因")
    assert "连续 2 次响应被拒收" in _note, _note
    assert "连续 3 次响应被拒收" not in _note, _note
    assert _r["applied_rounds"] and _r["applied_rounds"][0]["round"] == 2, _r["applied_rounds"]
