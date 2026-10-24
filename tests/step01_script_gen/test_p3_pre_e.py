# -*- coding: utf-8 -*-
"""P3-PreE：完整计划走**真实** `outline.plan_acceptance_problems` 的端到端离线验收.

补的是 P3-A…D 只到"事务/状态/桩验收"的最后一处边界：一份**结构完全合法**的
完整计划（schema 的 16 个必需顶层字段 + 稳定 `fact_uids`），带拆分上下文时
也必须由**生产验收入口**给出结论，而不是由调用方传一个回调桩代替。

覆盖：

- 正例：`P0(R0)` → `run_split_transaction` → `P2(R2)`，`split_state=effective`，
  `plan_acceptance_problems(P2) == []`；同一候选经生产大纲缓存的**写入 → 读回装载**
  后仍为 0 问题；
- 组合事务：`V0(R0) → V1（create_voice）→ V2（split）`，只对 V2 做一次 R，最终
  验收同样为 0 问题，基线 V0 保留；
- 反例 1（`split_pending`）：子事实未获 R 记录支持、或候选根本没有 R 记录；
- 反例 2（`split_invalid`）：篡改账本 `judgment.inputs_digest` 或抹掉 `call_id`。

样本是版本库内冻结的匿名最小样本 `tools/samples/p3_split_f02.json`（与 P3-E 真实
运行同一份），不依赖 `output/`、`.tmp` 或任何模型调用。样本里的 R0/R2 记录是
**夹具**（只声明本样本的已知裁决与真实载体文本），不是模型结论。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                 # noqa: E402
from shared import fact_split as fs                                           # noqa: E402
from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import content_verify as cverify                       # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen import split_judge as sj                               # noqa: E402
from step01_script_gen import split_transaction as st                         # noqa: E402

SAMPLE_PATH = _ROOT / "tools" / "samples" / "p3_split_f02.json"
_SAMPLE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
NOVEL: str = str(_SAMPLE["novel"])
PLAN: dict = _SAMPLE["plan"]
SPLIT: dict = _SAMPLE["split"]
PARENT: str = str(SPLIT["parent_uid"])
CHILDREN: list = copy.deepcopy(SPLIT["children"])
NEGATIVE_CHILDREN: list = copy.deepcopy(SPLIT["negative_children"])
SCOPE: dict = copy.deepcopy(SPLIT["scope"])
PARENT_SPANS: list = list(SPLIT["parent_evidence_spans"])

#: schema（严格模式）的 16 个必需顶层字段——完整计划必须全部在场。
SCHEMA_REQUIRED_TOP_LEVEL = (
    "main_events", "climax", "causal_chain", "characters", "emotional_arc",
    "key_scenes", "character_motives", "end_hook", "omittable", "time_budget",
    "required_facts", "hook_spoken", "hook_carrier", "narration_lines",
    "dialogue_lines", "shot_beats",
)


# ---------------------------------------------------------------------------
# 样本与夹具
# ---------------------------------------------------------------------------
def _carrier_texts(view) -> dict:
    _out = {}
    for _b in view.get("beats") or []:
        _out[str(_b.get("uid"))] = str(_b.get("text") or "")
    for _u in (view.get("dialogue_units") or []) + (view.get("narration_units") or []):
        _out[str(_u.get("uid"))] = str(_u.get("text") or "")
    return _out


def _record_for(view, plan_digest, *, partial=(), spans=None) -> dict:
    """夹具核验记录：形状/义务支座/证据与真实入口同源；裁决是本样本的既定值."""
    _texts = _carrier_texts(view)
    _binds = {str(b.get("fact_uid")): (b.get("carriers") or [])
              for b in (view.get("fact_bindings") or []) if isinstance(b, dict)}
    _facts, _sources, _voices = [], [], []
    for _f in view.get("frozen_facts") or []:
        _uid = str(_f.get("uid"))
        _verdict = "partial" if _uid in partial else "supported"
        _car = (_binds.get(_uid) or [{}])[0]
        _cuid = str(_car.get("uid") or "")
        _ctext = _texts.get(_cuid, "")
        _facts.append({
            "fact_uid": _uid, "verdict": _verdict,
            "support_spans": ([_ctext] if _verdict == "supported" and _ctext else []),
            "missing": ("未展示完整主张" if _verdict != "supported" else ""),
            "suggested_carriers": [],
            "obligations": [{
                "obligation_id": "claim", "text": str(_f.get("claim") or ""),
                "verdict": _verdict,
                "evidence": ([{"carrier_uid": _cuid, "exhibitor_span": _ctext}]
                             if _verdict == "supported" and _cuid and _ctext else []),
                "missing": ("未展示完整主张" if _verdict != "supported" else ""),
                "reason": ""}]})
        _spans = (spans or {}).get(_uid) or [str(_f.get("evidence") or "")]
        _sources.append({"fact_uid": _uid, "verdict": "supported",
                         "evidence_spans": [x for x in _spans if str(x)],
                         "reason": ""})
    for _u in view.get("dialogue_units") or []:
        _so = _u.get("source") if isinstance(_u.get("source"), dict) else {}
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_speech",
                        # 计划投影只保留台词正文，说话人由核验方给出；夹具用**明确标注**
                        # 的占位符，不冒充真实人物（契约只要求非空白）。
                        "speaker": str(_so.get("speaker") or "（夹具：未指定说话人）"),
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    for _u in view.get("narration_units") or []:
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_narration", "speaker": "",
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    _beats = [str(b.get("uid")) for b in (view.get("beats") or [])]
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": str(plan_digest),
        "novel_digest": cverify.novel_identity(NOVEL)["digest"],
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": _facts, "source_support": _sources, "voice_units": _voices,
        "obligation_basis": cv.obligation_basis_digest(view),
        "coherence": {
            "beat_uids": _beats, "verdict": "supported",
            "conflict_beats": [], "evidence_spans": [],
            "reason": "按计划给定的先后顺序承接",
            "dependencies": [
                {"beat_uid": _uid, "requires_before": _beats[:_i],
                 "reason": "按计划给定的先后顺序承接"}
                for _i, _uid in enumerate(_beats)]}}


def _baseline():
    """完整合法基线：`P0` 投影出 `V0`，`R0` 是夹具记录（f02 展示轴 partial）."""
    _view, _problems = pc.view_from_plan(PLAN)
    assert _problems == [], [(p.get("code"), p.get("path")) for p in _problems]
    _digest = cv.plan_digest_of(PLAN)
    _record = _record_for(_view, _digest, partial=(PARENT,),
                          spans={PARENT: PARENT_SPANS})
    return _view, _digest, _record


def _ledger(digest, record, *, children=None) -> dict:
    _kids = copy.deepcopy(children or CHILDREN)
    _base_view, _ = pc.view_from_plan(PLAN)
    _parent = next(f for f in _base_view["frozen_facts"] if f["uid"] == PARENT)
    return {
        "version": fs.VERSION, "novel_digest": record["novel_digest"],
        "novel_text": NOVEL, "base_digest": digest,
        "splits": [{
            "parent_uid": PARENT,
            "parent_claim": str(_parent["claim"]),
            "parent_evidence": str(_parent["evidence"]),
            "parent_evidence_spans": list(PARENT_SPANS),
            "baseline_claim": None,
            "authority": {
                "rule_version": cv.DEFAULT_RULE_VERSION, "plan_digest": digest,
                "fact_uid": PARENT, "code": "verify_fact_unsupported",
                "verdict": "partial"},
            "children": _kids, "narrowed_voices": [],
            "judgment": {
                "mode": "model", "verdict": "supported", "missing": "",
                "reason": "",
                "inputs_digest": fs.inputs_digest(str(_parent["claim"]), _kids),
                "call_id": "fixture-m", "prompt_digest": "c" * 64,
                "response_digest": "d" * 64}}]}


def _verify_candidate(*, partial=()):
    _calls: list = []

    def _verify(candidate):
        _calls.append(candidate)
        _plan = pc.plan_from_view(candidate)
        return _record_for(candidate, cv.plan_digest_of(_plan), partial=partial)
    return _verify, _calls


def _run_tx(*, partial=(), children=None, verify=None, calls=None):
    _view, _digest, _record = _baseline()
    _verify, _default_calls = _verify_candidate(partial=partial)
    _verify = verify or _verify
    _calls = calls if calls is not None else _default_calls
    return st.run_split_transaction(
        _view, PLAN, _record,
        {"parent_uid": PARENT, "children": copy.deepcopy(children or CHILDREN)},
        scope=copy.deepcopy(SCOPE),
        ledger=_ledger(_digest, _record, children=children),
        novel_text=NOVEL,
        m_call=lambda _p: {"verdict": "supported", "missing": "", "reason": ""},
        m_call_id="fixture-m",
        verify_candidate=_verify), _calls


def _codes(problems) -> list:
    return [str(p.get("code") or "") for p in (problems or [])]


# ---------------------------------------------------------------------------
# 正例：完整计划 + 真实验收入口 + 缓存往返
# ---------------------------------------------------------------------------
def test_the_frozen_sample_is_a_complete_legal_plan():
    _missing = [k for k in SCHEMA_REQUIRED_TOP_LEVEL if k not in PLAN]
    assert _missing == [], "样本缺 schema 必需顶层字段：%s" % _missing
    assert PLAN["fact_uids"] == ["f01", "f02", "f03", "f04"]
    _view, _digest, _record = _baseline()
    assert cv.record_basis_matches(_record, PLAN) is True
    assert cv.record_protocol_problems(
        _record, _view, plan_digest=_digest, novel_text=NOVEL,
        novel_digest=_record["novel_digest"]) == []
    _plan_rec = dict(PLAN, plan_verification=_record)
    assert o.plan_acceptance_problems(_plan_rec, NOVEL) == [], \
        _codes(o.plan_acceptance_problems(_plan_rec, NOVEL))


def test_positive_split_transaction_passes_real_plan_acceptance(monkeypatch):
    _seen: list = []
    _orig = o.plan_acceptance_problems

    def _spy(plan, novel_text=""):
        _result = _orig(plan, novel_text)
        _seen.append((cv.plan_digest_of(plan), list(_result)))
        return _result

    monkeypatch.setattr(o, "plan_acceptance_problems", _spy)
    _out, _r_calls = _run_tx()

    assert _out["ok"] is True and _out["stage"] == "committed", _out
    assert _out["state"] == "effective"
    assert _out["m_calls"] == 1 and len(_r_calls) == 1
    assert [f["uid"] for f in _out["candidate"]["frozen_facts"]] == [
        "f01", "f02.1", "f02.2", "f03", "f04"]
    assert _out["problems"] == []

    _p2 = _out["plan"]
    _split_view, _vp = pc.view_from_plan(_p2)
    _entry = (fs.context(_split_view).get("splits") or [])[0]
    assert fs.split_state(_split_view, _entry, record=_p2.get("plan_verification")) \
        == "effective"
    # **真实入口**被实际调用，且对 P2 的结论是空表（不是回调桩）。
    assert any(_seen), "验收入口未被调用"
    assert any(_digest == cv.plan_digest_of(_p2) and _problems == []
               for _digest, _problems in _seen), _seen
    assert _orig(_p2, NOVEL) == [], _codes(_orig(_p2, NOVEL))


def test_the_committed_candidate_survives_the_production_cache_roundtrip(
        tmp_path, monkeypatch):
    _out, _ = _run_tx()
    assert _out["ok"] is True, _out
    _p2 = {k: v for k, v in _out["plan"].items() if k != "plan_verification"}
    _cache = tmp_path / "chapter_outline.cache.json"

    class _Svc:
        def __init__(self, payloads=()):
            self.payloads = list(payloads)
            self.reqs: list = []

        def generate(self, request):
            from _text_gen.models import TextGenerationResult
            self.reqs.append(request)
            if not self.payloads:
                return TextGenerationResult(success=False, text="", error="stub: 无更多响应")
            _p = self.payloads.pop(0)
            return TextGenerationResult(
                success=True,
                text=(_p if isinstance(_p, str)
                      else json.dumps(_p, ensure_ascii=False)))

    _svc = _Svc([_p2])
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    _r1 = o.generate_outline_result(
        NOVEL, cache_path=_cache, max_revision_calls=0,
        verify=lambda view, digest: _record_for(view, digest))
    assert _r1["executable"] is True, (_r1.get("failure"),
                                       _codes(_r1["acceptance"]["problems"]))
    assert _r1["cache"]["written"] is True
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    assert _disk["outline"] == _r1["plan"]
    assert fs.context(pc.view_from_plan(_disk["outline"])[0]) is not None, \
        "缓存里的计划必须保留拆分上下文"
    assert o.plan_acceptance_problems(_disk["outline"], NOVEL) == [], \
        _codes(o.plan_acceptance_problems(_disk["outline"], NOVEL))

    _svc2 = _Svc([])
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc2)
    _r2 = o.generate_outline_result(
        NOVEL, cache_path=_cache, max_revision_calls=0,
        verify=lambda view, digest: _record_for(view, digest))
    assert _r2["executable"] is True and _r2["source"] == "cache", _r2
    assert _r2["cache"]["hit"] is True and _r2["cache"]["written"] is False
    assert _svc2.reqs == [], "缓存命中的读回装载不许再调模型"
    assert _r2["acceptance"]["problems"] == []
    assert o.plan_acceptance_problems(_r2["plan"], NOVEL) == [], \
        _codes(o.plan_acceptance_problems(_r2["plan"], NOVEL))


def test_create_voice_then_split_commits_only_v2_and_passes_real_acceptance():
    _v0, _digest, _r0 = _baseline()

    def _create_voice(view):
        _out = copy.deepcopy(view)
        _out["narration_units"].append({"uid": "n02", "text": "乙没有送他。",
                                        "source": {"text": "乙没有送他。"}})
        _out["beats"].append({"uid": "b05", "text": "乙没有送他。",
                              "event_uids": ["乙没有送他"], "purpose": "收束",
                              "evidence": "乙没有送他。", "source": {},
                              "voice_ref": {"kind": "narration", "uid": "n02"}})
        return _out

    _r_calls: list = []

    def _verify(candidate):
        _r_calls.append(candidate)
        _plan = pc.plan_from_view(candidate)
        return _record_for(candidate, cv.plan_digest_of(_plan))

    _out = st.run_create_voice_then_split(
        v0=_v0, r0=_r0, create_voice=_create_voice,
        split_args={"split_ops": {"parent_uid": PARENT,
                                  "children": copy.deepcopy(CHILDREN)},
                    "scope": copy.deepcopy(SCOPE),
                    "ledger": _ledger(_digest, _r0), "novel_text": NOVEL},
        verify_candidate=_verify,
        m_call=lambda _p: {"verdict": "supported", "missing": "", "reason": ""},
        m_call_id="fixture-combo")

    assert _out["ok"] is True and _out["stage"] == "committed", _out
    assert _out["state"] == "effective" and _out["m_calls"] == 1
    assert _out.get("rollback") is None
    assert _out["baseline"] is _v0 and _out["v1"] is not None and _out["v2"] is not None
    assert len(_r_calls) == 1 and _r_calls[0] is _out["v2"], "只对 V2 做一次 R"
    assert [f["uid"] for f in _out["v2"]["frozen_facts"]] == [
        "f01", "f02.1", "f02.2", "f03", "f04"]
    assert _out["plan"]["plan_verification"]["plan_digest"] == \
        cv.plan_digest_of(pc.plan_from_view(_out["v2"]))
    assert o.plan_acceptance_problems(_out["plan"], NOVEL) == [], \
        _codes(o.plan_acceptance_problems(_out["plan"], NOVEL))


# ---------------------------------------------------------------------------
# 反例 1：split_pending
# ---------------------------------------------------------------------------
def test_a_child_without_r_support_reports_split_pending():
    _out, _ = _run_tx(partial=("f02.1",))
    assert _out["ok"] is False and _out["stage"] == "r_state", _out
    assert _out["m_calls"] == 1
    assert _out["candidate"] is not None and _out["plan"] is not None
    assert "split_pending" in _codes(_out["problems"])
    _candidate_plan = _out["plan"]
    _codes_out = _codes(o.plan_acceptance_problems(_candidate_plan, NOVEL))
    assert "split_pending" in _codes_out, _codes_out
    assert "split_invalid" not in _codes_out
    _view, _vp = pc.view_from_plan(_candidate_plan)
    _entry = (fs.context(_view).get("splits") or [])[0]
    assert fs.split_state(_view, _entry,
                          record=_candidate_plan.get("plan_verification")) == "pending"


def test_a_candidate_without_any_r_record_reports_split_pending():
    _out, _ = _run_tx()
    assert _out["ok"] is True, _out
    _no_record = {k: v for k, v in _out["plan"].items()
                  if k != "plan_verification"}
    _codes_out = _codes(o.plan_acceptance_problems(_no_record, NOVEL))
    assert "split_pending" in _codes_out, _codes_out


# ---------------------------------------------------------------------------
# P3-E 运行器的计费端点守卫
# ---------------------------------------------------------------------------
def test_the_real_runner_only_allows_the_opencode_go_endpoint():
    """`provider == agent_plan` 不足以守住计费通道：必须核**最终生效 URL**.

    本轮真实发送地址为 OpenCode Go；配置变化后若 base URL 被换成 ark/其他网关，
    运行器必须在建 service / 发送前拒绝，而不是照常发送。
    """
    from tools import p3_split_real_run as runner

    _base = runner.OPENCODE_GO_BASE_URL
    assert _base == "https://opencode.ai/zen/go/v1"
    assert runner._base_allowed(_base, _base) is True
    assert runner._base_allowed(_base + "/", _base) is True
    assert runner._base_allowed("https://ark.cn-beijing.volces.com/api/plan/v3",
                                _base) is False
    assert runner._url_allowed(_base + "/chat/completions", _base) is True
    for _bad in ("https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                 "https://opencode.ai/zen/go/v1.evil.example/chat/completions",
                 "http://opencode.ai/zen/go/v1/chat/completions",
                 "https://opencode.ai/zen/go/v10/chat/completions",
                 "https://api.other.example/v1/chat/completions"):
        assert runner._url_allowed(_bad, _base) is False, _bad
    assert runner._url_allowed(_base + "/chat/completions", "") is False


# ---------------------------------------------------------------------------
# 反例 2：split_invalid
# ---------------------------------------------------------------------------
def _tamper_inputs_digest(entry):
    entry["judgment"]["inputs_digest"] = "0" * 64


def _tamper_call_id(entry):
    entry["judgment"]["call_id"] = ""


@pytest.mark.parametrize("tamper", [_tamper_inputs_digest, _tamper_call_id],
                         ids=["inputs_digest", "call_id"])
def test_a_tampered_split_judgment_reports_split_invalid(tamper):
    _out, _ = _run_tx()
    assert _out["ok"] is True, _out
    _tampered = copy.deepcopy(_out["plan"])
    _view, _vp = pc.view_from_plan(_tampered)
    assert _vp == []
    _entry = (fs.context(_view).get("splits") or [])[0]
    tamper(_entry)
    _codes_out = _codes(o.plan_acceptance_problems(_tampered, NOVEL))
    assert "split_invalid" in _codes_out, _codes_out
    assert "split_pending" not in _codes_out
    assert fs.split_state(_view, _entry,
                          record=_tampered.get("plan_verification")) == "invalid"
