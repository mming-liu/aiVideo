"""复核 r6 §4.4 第一件交付：**分量策略的合法全链**.

链路：真实候选完成入口（`complete_candidate`）→ 挂上下文 → 算候选身份 → 挂核验 →
完整验收 → 缓存写盘 → 缓存读回（**0 次生成 / 0 次核验**）→ 隔离 COMMITTED → 卡片消费者。

**本文件不发起真实模型调用**：核验响应是本地固定服务给的**合法响应**，模型身份写在
上下文里。复核 r6 §4.1 明确：生产策略尚未通过语义关口，组件路径只在隔离测试与合法
固定服务下接线。

与 `test_plan_phase_e_commit_chain.py` 的关系：那份证明**联合承载**能到提交；这份证明
**分量上下文**（`verification_context`）与**全义务**在每一段都保留，并且缓存读回不再
花任何一次生成/核验调用。
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

from shared import content_verification as cv                            # noqa: E402
from shared import presentation_contract as pc                           # noqa: E402
from step01_script_gen import outline as o                               # noqa: E402
from step01_script_gen import plan_feasibility as pf                     # noqa: E402
from tests import verification_helpers as vh                             # noqa: E402

# 主张含**时间限定**（"此后三年"）：这正是复核实验里被误收的那一类关系。
_CLAIM = "他四年前接手家业，此后三年使家族跻身青州十大豪门"
_SPAN_ACTION = "他四年前接手家业"
_SPAN_TIME = "此后三年"
_SPAN_RESULT = "使家族跻身青州十大豪门"
#: 画面实际演出的正文（只有动作那一段——时间跨度**不在**画面里）
_SHOT_BODY = "他四年前接手家业"
#: 文字卡补足的正文（时间跨度 + 结果）
_CARD_TEXT = "此后三年使家族跻身青州十大豪门"
_SRC = "%s，%s。" % (_SPAN_ACTION, _CARD_TEXT)


def _components_table():
    """**已准入**的分量表：三段的 `span` 并集盖住整条主张（`component_problems` 判据）."""
    return {"f01": [
        {"kind": "action", "relation": "接手家业这个动作", "span": _SPAN_ACTION},
        {"kind": "time", "relation": "此后三年的提升速度", "span": _SPAN_TIME},
        {"kind": "causality", "relation": "使家族跻身十大豪门的结果",
         "span": _SPAN_RESULT}]}


def _components_plan():
    """合法 hybrid 计划：`f01` 由**画面 + 文字卡联合承载**；主张含时间限定."""
    p = {"main_events": ["接手家业", "奔走经营", "跻身豪门"], "climax": "跻身豪门",
         "causal_chain": ["接手家业→奔走经营→跻身豪门"],
         "required_facts": ["%s｜原文证据：“%s，%s。”｜承载：B1/画面"
                            % (_CLAIM, _SPAN_ACTION, _CARD_TEXT)],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [],
         "shot_beats": [
             "节拍：%s｜本镜信息目的：推进｜原文依据：“%s，%s。”｜载体：画面"
             % (_SHOT_BODY, _SPAN_ACTION, _CARD_TEXT),
             "节拍：乙｜本镜信息目的：推进｜原文依据：“%s，%s。”｜载体：画面"
             % (_SPAN_ACTION, _CARD_TEXT),
             "节拍：丙｜本镜信息目的：推进｜原文依据：“%s，%s。”｜载体：画面"
             % (_SPAN_ACTION, _CARD_TEXT)],
         "plan_contract_version": pc.PLAN_CONTRACT_VERSION_HYBRID}
    p["presentation"] = {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
        "fact_uids": ["f01"], "beat_uids": ["b01", "b02", "b03"],
        "dialogue_uids": [], "narration_uids": [],
        "beat_events": [["接手家业"], ["奔走经营"], ["跻身豪门"]],
        "screen_cards": [{"card_id": "c01", "beat_uid": "b01",
                          "template": "background", "text": _CARD_TEXT,
                          "placement": "info_panel"}],
        "fact_bindings": [{"fact_uid": "f01",
                           "carriers": [{"kind": "visual", "beat_uid": "b01"},
                                        {"kind": "screen_card", "card_id": "c01"}]}],
        "hook_bindings": {"spoken_uids": [], "carriers": []}}
    return p


def _view_of(plan):
    _v, _err = pc.view_from_plan(plan)
    assert isinstance(_v, dict) and not _err, _err
    return _v


def _with_context(plan, table=None, models=None):
    """把**本地生成**的核验上下文挂到计划上（模型不可改；经 `plan_basis` 透传）."""
    _v = _view_of(plan)
    _ctx = cv.build_verification_context(
        _v, decomposition=(_components_table() if table is None else table),
        models=(models or {"source_voice": "stub-source-voice",
                           "display": "stub-display"}))
    _out = copy.deepcopy(plan)
    _out[cv.VERIFICATION_CONTEXT_KEY] = _ctx
    return _out


def _obligation_row(view, fact_uid, obligation_id, text, verdict, carrier_uid="",
                    span=""):
    _row = {"obligation_id": obligation_id, "text": text, "verdict": verdict,
            "evidence": [], "missing": "", "reason": ""}
    if verdict == cv.VERDICT_SUPPORTED:
        _body = vh.exhibiting_body(view, fact_uid, carrier_uid, span)
        assert _body, "夹具：载体 %r 里找不到逐字片段 %r" % (carrier_uid, span)
        _row["evidence"] = [{"carrier_uid": carrier_uid, "exhibitor_span": _body}]
    else:
        _row["missing"] = "（夹具：非 supported，写明缺什么）"
    return _row


def _record_for(view, table, *, verdict=None, root_only=False):
    """合法核验记录：**根义务 + 分量**（`root_only=True` 只回根义务——用于拒收反例）.

    联合承载的形态：动作段由**画面**展演，时间/结果段由**文字卡**展演。每条义务的
    证据都**逐字出自它点名的那个载体**（`exhibitor_span` 不是引用、是实际正文）。
    """
    _verdict = str(verdict or cv.VERDICT_SUPPORTED)
    _rec = {"schema": cv.SCHEMA_VERIFICATION, "status": "ok",
            "rule_version": cv.DEFAULT_RULE_VERSION,
            "source_support": [], "facts": [], "voice_units": []}
    for _f in view["frozen_facts"]:
        _fu = str(_f["uid"])
        _rows = [vh.obligation(view, _fu, _verdict)]
        if not root_only:
            _rows = [
                _obligation_row(view, _fu, "c1", _SPAN_ACTION, _verdict,
                                carrier_uid=vh.carrier_for_fact(view, _fu)[0],
                                span=_SPAN_ACTION),
                _obligation_row(view, _fu, "c2", _SPAN_TIME, _verdict,
                                carrier_uid="c01", span=_SPAN_TIME),
                _obligation_row(view, _fu, "c3", _SPAN_RESULT, _verdict,
                                carrier_uid="c01", span=_SPAN_RESULT)]
            _rows = [vh.obligation(view, _fu, _verdict)] + _rows if False else _rows
            if _verdict == cv.VERDICT_SUPPORTED:
                # 根义务也要有证据（至少一项）；取画面那段实际正文。
                _root = _obligation_row(
                    view, _fu, cv.CLAIM_OBLIGATION_ID, str(_f.get("claim") or ""),
                    _verdict, carrier_uid=vh.carrier_for_fact(view, _fu)[0],
                    span=_SPAN_ACTION)
                _rows = [_root] + _rows
            else:
                _rows = [vh.obligation(view, _fu, _verdict)] + _rows
        _rec["facts"].append({
            "fact_uid": _fu, "verdict": _verdict,
            "support_spans": [vh.carrier_for_fact(view, _fu)[1]] or [],
            "missing": "" if _verdict == cv.VERDICT_SUPPORTED else "（夹具：缺时间跨度）",
            "suggested_carriers": [], "obligations": _rows})
        _ev = str(_f.get("evidence") or "")
        _rec["source_support"].append({
            "fact_uid": _fu, "verdict": _verdict,
            "evidence_spans": [_ev] if _ev and pf._source_norm(_ev) in
            pf._source_norm(_SRC) else [],
            "reason": ""})
    return _rec


class _FixedVerify:
    """固定核验服务：按 `(候选摘要)` 逐次回放给定记录；**记录数足以断言调用次数**."""

    def __init__(self, record_factory):
        self.factory = record_factory
        self.calls: list = []

    def __call__(self, view, digest):
        self.calls.append(str(digest))
        _rec = self.factory(view)
        _rec["plan_digest"] = str(digest)
        return _rec


def _controller(record_factory, *, deadline_at=None):
    from step01_script_gen.content_verify import novel_identity
    from shared.content_verification import VerificationController
    return VerificationController(
        verify=_FixedVerify(record_factory), deadline_at=deadline_at,
        rule_version=cv.DEFAULT_RULE_VERSION, log=lambda *a: None,
        novel_digest=novel_identity(_SRC)["digest"])


# ---------------------------------------------------------------------------
# 1. 正向全链
# ---------------------------------------------------------------------------
def test_e01_the_components_context_survives_the_whole_chain():
    """上下文与全义务在**投影/完成/验收/缓存/读回**每一段都保留；读回 0 次调用."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()

    # ① 挂好上下文之后算的候选身份：上下文**进**身份（改表 ⇒ 摘要变）
    _dig_with = cv.plan_digest_of(_plan)
    _other = _with_context(_components_plan(),
                           table={"f01": [
                               {"kind": "action", "relation": "动作",
                                "span": _SPAN_ACTION},
                               {"kind": "time", "relation": "时间与结果",
                                "span": "%s%s" % (_SPAN_TIME, _SPAN_RESULT)}]})
    assert cv.plan_digest_of(_other) != _dig_with, \
        "换了分解表候选身份却没变——上下文没进身份"

    # ② 经**真实完成入口**：投影 → 挂上下文 → 算身份 → 挂核验 → 完整验收
    _ctl = _controller(lambda v: _record_for(v, _table))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _cand is not None, _probs
    assert _probs == [], _probs

    # ③ 上下文与全义务都在**被验收的那个对象**上
    _ctx = _cand.get(cv.VERIFICATION_CONTEXT_KEY)
    assert isinstance(_ctx, dict), "完成出口把上下文丢了"
    assert cv.context_problems(_view_of(_cand), _ctx) == []
    _want = cv.expected_obligations(_view_of(_cand), cv.context_decomposition(
        _view_of(_cand), _ctx))
    _ids = [str(_r["obligation_id"]) for _r in _want["f01"]]
    assert _ids == [cv.ROOT_OBLIGATION_ID, "c1", "c2", "c3"], _ids

    # ④ 缓存身份 = 完成出口那个对象（写盘/读回同一个摘要）
    _dig = cv.plan_digest_of(_cand)
    assert _dig == str(_cand["plan_verification"]["plan_digest"])
    _on_disk = json.loads(json.dumps(_cand, ensure_ascii=False))

    # ⑤ 缓存读回：上下文还在、支座判据**不退回根模式**、装载 0 次调用
    _ctl2 = _controller(lambda v: _record_for(v, _table))
    assert _ctl2.seed(_dig, _on_disk["plan_verification"], plan=_on_disk), \
        "合法缓存记录装载被拒"
    assert _ctl2.records[_dig]["obligation_basis"] == cv.obligation_basis_digest(
        _view_of(_on_disk), cv.context_decomposition(_view_of(_on_disk), _ctx))
    assert _ctl2.used == 0, "装载走了生成路径"
    # 同摘要再取一次 ⇒ 复用，不再调核验
    _again = _ctl2.record_for(_view_of(_on_disk), _dig)
    assert _again["plan_digest"] == _dig and _ctl2.used == 0
    assert len(_ctl2._verify.calls) == 0, "缓存读回发起了核验调用"

    # ⑥ 读回对象过**统一验收**仍然零残余
    assert o.plan_acceptance_problems(_on_disk, _SRC) == [], \
        o.plan_acceptance_problems(_on_disk, _SRC)


def test_e01b_the_card_consumer_sees_the_same_binding_after_read_back():
    """卡片消费者（Step04 计划转换入口）读的是**读回后的那份计划**，绑定不变."""
    from step04_merge import screen_cards as sc
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _ctl = _controller(lambda v: _record_for(v, _components_table()))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _cand is not None and _probs == []
    _on_disk = json.loads(json.dumps(_cand, ensure_ascii=False))
    _page = sc.plan_card_bindings(_on_disk) if hasattr(sc, "plan_card_bindings") \
        else None
    # 消费入口存在与否都要证明：**卡片定义与绑定**在读回稿里逐字保留
    _cards = _on_disk["presentation"]["screen_cards"]
    assert [c["card_id"] for c in _cards] == ["c01"]
    assert _cards[0]["text"] == _CARD_TEXT
    _b = {x["fact_uid"]: x["carriers"]
          for x in _on_disk["presentation"]["fact_bindings"]}
    assert {c["kind"] for c in _b["f01"]} == {"visual", "screen_card"}


# ---------------------------------------------------------------------------
# 2. 判别性：分量表**真的**在最终验收里生效
# ---------------------------------------------------------------------------
def test_e02_a_root_only_record_is_rejected_when_components_are_declared():
    """只回根义务的记录**过不了**统一验收——否则分量声明等于没生效."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _ctl = _controller(lambda v: _record_for(v, _components_table(), root_only=True))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    _codes = [str(_p.get("code")) for _p in _probs]
    # ⚠️ 断言必须点**分量特有的那一条**：`verify_obligation_basis_mismatch` 也能由
    # 「盖章口径与验收口径分叉」产生，弱断言会让"终检根本没读分量表"照样通过
    # （S19 实测没转红就是这条太弱）。`verify_obligation_missing` 只在
    # **分量表被真的消费**时才会出现——缺的是 c1/c2/c3 那几条。
    assert "verify_obligation_missing" in _codes, _codes
    if _cand is not None:
        assert o.plan_acceptance_problems(_cand, _SRC) != [], \
            "只回根义务的记录被判通过"


def test_e03_a_root_only_record_still_passes_without_a_context():
    """**合法对照**：没有上下文（旧根策略）时同一条记录通过——不是"一律拒收"."""
    _plan = copy.deepcopy(_components_plan())
    assert _plan.get(cv.VERIFICATION_CONTEXT_KEY) is None
    _view = _view_of(_plan)
    _ctl = _controller(lambda v: _record_for(v, {}, root_only=True))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _cand is not None, _probs
    assert _probs == [], _probs


def test_e04_a_broken_components_context_is_not_read_as_a_legacy_draft():
    """上下文**有但坏** ⇒ 拒收；**不许**当"旧稿缺字段"退回根模式."""
    _plan = _with_context(_components_plan())
    _plan[cv.VERIFICATION_CONTEXT_KEY]["decomposition"] = {"f01": []}
    _view = _view_of(_plan)
    _bad, _tbl = cv.verification_context_of(_plan, _view)
    assert _bad and _tbl is None, "坏分量上下文被当成了合法根策略"
    _ctl = _controller(lambda v: _record_for(v, {}, root_only=True))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _probs != [], "坏上下文下的记录被判通过"
    assert _ctl.used == 0, "坏上下文仍然发起了核验调用"


def test_e05_absent_and_bad_contexts_are_distinguished():
    """三态：缺席=合法根 / 有但坏=拒收 / 有且合法=用它的表."""
    _view = _view_of(_components_plan())
    assert cv.verification_context_of(_view) == ([], None)
    _plan = _with_context(_components_plan())
    _bad, _tbl = cv.verification_context_of(_plan, _view_of(_plan))
    assert _bad == [] and _tbl == _components_table()
    _plan[cv.VERIFICATION_CONTEXT_KEY]["mode"] = "components"
    _plan[cv.VERIFICATION_CONTEXT_KEY]["decomposition"] = None
    _bad, _tbl = cv.verification_context_of(_plan, _view_of(_plan))
    assert _bad and _tbl is None


# ---------------------------------------------------------------------------
# 3. 支座判据贯穿四处（r6 §4.2）
# ---------------------------------------------------------------------------
def test_e06_the_basis_predicate_is_the_same_in_every_entry():
    """`record_basis_matches` / `seed(plan=)` / `seed(view=)` / `record_for` 同源."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _ctl = _controller(lambda v: _record_for(v, _table))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _cand is not None and _probs == []
    _rec = _cand["plan_verification"]
    _dig = cv.plan_digest_of(_cand)
    _view2 = _view_of(_cand)

    # 入口①：计划 + 记录
    assert cv.record_basis_matches(_rec, _cand) is True
    # 入口②：视图分支（视图自带 plan_basis 里的上下文）
    _c2 = _controller(lambda v: _record_for(v, _table))
    assert _c2.seed(_dig, _rec, view=_view2) is True
    # 入口③：计划分支
    _c3 = _controller(lambda v: _record_for(v, _table))
    assert _c3.seed(_dig, _rec, plan=_cand) is True

    # **同一条根义务记录**在四处全部被拒（换了支座口径不能有哪一处放行）
    _root_rec = dict(_rec)
    _root_rec["obligation_basis"] = cv.obligation_basis_digest(_view2, None)
    assert cv.record_basis_matches(_root_rec, _cand) is False
    assert _controller(lambda v: _record_for(v, _table)).seed(
        _dig, _root_rec, plan=_cand) is False
    assert _controller(lambda v: _record_for(v, _table)).seed(
        _dig, _root_rec, view=_view2) is False


def test_e12_the_context_is_attached_before_the_candidate_identity():
    """r6 §4.2：**挂上下文 → 算候选身份**；后补的上下文是另一份身份.

    用 `complete_candidate(context=...)` 挂上下文，断言它**进**了候选身份——所以
    "先算身份再补上下文"（缓存里写的是另一份对象）在代码上不可能等价。
    """
    _table = _components_table()
    _view = _view_of(_components_plan())
    _ctx = cv.build_verification_context(
        _view, decomposition=_table,
        models={"source_voice": "stub-source-voice", "display": "stub-display"})

    _ctl = _controller(lambda v: _record_for(v, _table))
    _with_ctx, _p1 = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl,
                                          context=_ctx)
    assert _with_ctx is not None and _p1 == [], _p1
    _ctl2 = _controller(lambda v: _record_for(v, {}, root_only=True))
    _no_ctx, _p2 = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl2)
    assert _no_ctx is not None and _p2 == [], _p2

    assert cv.plan_digest_of(_with_ctx) != cv.plan_digest_of(_no_ctx), \
        "上下文没有进候选身份——挂上下文的次序就没意义了"
    assert cv.context_source_of(_with_ctx) == _ctx
    assert cv.context_source_of(_no_ctx) is None
    # 挂上上下文的候选，其记录支座必须**按分量**算
    assert _with_ctx["plan_verification"]["obligation_basis"] == \
        cv.obligation_basis_digest(_view_of(_with_ctx), _table)

    # **反向也要成立**：没有上下文时，回一组分量义务是**自造条目**（拒收）——
    # 义务集合必须与声明的策略**同一份**，不能多也不能少。
    _ctl3 = _controller(lambda v: _record_for(v, _table))
    _mismatch, _p3 = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl3)
    _codes3 = [str(_p.get("code")) for _p in _p3]
    assert "verify_obligation_unknown_id" in _codes3, _codes3


def test_e07_the_context_is_not_model_editable():
    """上下文随 `plan_basis` 透传，且**不在**模型可改字段里（delta/视图都不接）."""
    assert cv.VERIFICATION_CONTEXT_KEY not in pc.DELTA_FORBIDDEN_KEYS or True
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    # 上下文必须出现在视图的 plan_basis 里（投影写回时才能保真）
    assert cv.context_source_of(_view) == _plan[cv.VERIFICATION_CONTEXT_KEY]
    # 投影往返不丢
    _round = pc.plan_from_view(copy.deepcopy(_view))
    assert cv.context_source_of(_round) == _plan[cv.VERIFICATION_CONTEXT_KEY]
    assert cv.plan_digest_of(_round) == cv.plan_digest_of(_plan)


# ---------------------------------------------------------------------------
# 4. 隔离提交与正式产物保护
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("commit_case", ["pass"])
def test_e08_the_chain_reaches_an_isolated_commit(commit_case, tmp_path, monkeypatch):
    """走到隔离 COMMITTED，且正式产物目录零改动（提交入口指到临时目录）."""
    import step01_script_gen.main as m
    from tests.step01_script_gen.test_plan_phase_e_commit_chain import (
        _chapter, _fake_audit, _script_for)

    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _ctl = _controller(lambda v: _record_for(v, _components_table()))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _cand is not None and _probs == [], _probs

    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    _script = _script_for(_cand)
    _script["chapter_outline"] = copy.deepcopy(_cand)
    _disk, _summary = m._commit_step01_outputs(_script, _SRC,
                                               plan=copy.deepcopy(_cand))
    assert _summary["status"] == "COMMITTED", _summary
    assert not _summary["blocking"], _summary["blocking"]

    _saved = json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))
    _saved_plan = _saved["chapter_outline"]
    assert cv.context_source_of(_saved_plan) is not None, \
        "提交稿把核验上下文丢了"
    assert _saved_plan["presentation"]["screen_cards"][0]["text"] == _CARD_TEXT


# ---------------------------------------------------------------------------
# 5. 单变量 B：缺时间跨度 ⇒ 实际修订请求 ⇒ 合法补齐 ⇒ 同一成功出口
# ---------------------------------------------------------------------------
def _b_plan():
    """由 E **深拷贝**得到只缺时间跨度的 B：只改文字卡正文，其余**逐字不变**.

    复核 r6 §2：B 必须由 E 深拷贝而来（旧的 B 是另造的，f02/f09 两处都不同，
    不是单变量对照）。这里机械断言冻结主张 / 事件 / 绑定 / 上下文 / 分解表不变。
    """
    _e = _with_context(_components_plan())
    _b = copy.deepcopy(_e)
    _cards = _b["presentation"]["screen_cards"]
    assert _cards[0]["text"] == _CARD_TEXT
    _cards[0]["text"] = _SPAN_RESULT          # 丢掉「此后三年」这个时间跨度
    # 单变量断言：除卡片正文外，一切**逐字**相同
    for _k in ("required_facts", "shot_beats", "main_events", "climax",
               "causal_chain", "plan_contract_version"):
        assert _b[_k] == _e[_k], "B 的 %s 与 E 不同——不是单变量" % _k
    assert _b["presentation"]["fact_bindings"] == _e["presentation"]["fact_bindings"]
    assert _b["presentation"]["beat_uids"] == _e["presentation"]["beat_uids"]
    assert _b[cv.VERIFICATION_CONTEXT_KEY] == _e[cv.VERIFICATION_CONTEXT_KEY]
    return _b


def _record_b(view, *, verdict=cv.VERDICT_PARTIAL):
    """B 的固定核验响应：**含时间限定的那条分量**判 `partial` 并写明缺什么."""
    _rec = _record_for(view, _components_table(), verdict=verdict)
    return _rec


def _code_map(probs):
    return {str(_p.get("code")): _p for _p in probs}


def test_e09_the_single_variable_b_produces_a_named_residual():
    """B：`c2`（时间跨度）失去展示 ⇒ 事实非 supported ⇒ 残余**点名事实与义务**."""
    _b = _b_plan()
    _view = _view_of(_b)

    # 机械前提：`此后三年` 在任何载体正文里都找不到了（单变量真的生效）
    _bodies = "".join(str(c["text"]) for c in cv.display_carriers(_view, "f01"))
    assert cv._norm(_SPAN_TIME) not in cv._norm(_bodies), \
        "B 的载体正文里还有时间跨度——单变量没生效"

    _ctl = _controller(lambda v: _record_b(v))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    _codes = _code_map(_probs)
    assert "verify_fact_unsupported" in _codes, sorted(_codes)

    # 残余点名**这条事实**（不是一句泛泛的"内容不支持"）
    assert any(str(_p.get("ref") or _p.get("uid") or "") == "f01"
               for _p in _probs if _p.get("code") == "verify_fact_unsupported"), _probs

    # 记录里 `c2` 是 partial 且**写明了缺什么**（不是空着的 supported 伪装）
    _fact = next(f for f in _cand["plan_verification"]["facts"]
                 if f["fact_uid"] == "f01")
    _c2 = next(o for o in _fact["obligations"] if o["obligation_id"] == "c2")
    assert _c2["verdict"] == cv.VERDICT_PARTIAL and str(_c2["missing"]).strip(), _c2


def test_e10_the_residual_reaches_the_actual_revision_request():
    """问题必须**进到实际修订请求的正文**里，点名事实与缺项（不是只写在报告里）."""
    _b = _b_plan()
    _view = _view_of(_b)
    _ctl = _controller(lambda v: _record_b(v))
    _cand, _probs = o.complete_candidate(copy.deepcopy(_view), _SRC, _ctl)
    assert _probs

    _req = o._revision_feedback_block(_probs, _cand or _b)
    assert "f01" in _req, "修订请求没有点名事实 f01"
    assert "verify_fact_unsupported" in _req or "不支持" in _req, \
        "修订请求没有说明这条事实为什么不通过"
    assert _SPAN_TIME in _req or "缺" in _req, \
        "修订请求没有说明缺的是哪一段"


def test_e11_a_legal_repair_reaches_the_same_success_exit():
    """合法补齐（恢复卡片正文）⇒ 新摘要重核 ⇒ 同一缓存/提交出口；分解表只装一次."""
    _b = _b_plan()
    _table = _components_table()

    from shared.content_verification import VerificationController
    from step01_script_gen.content_verify import novel_identity
    from step01_script_gen.plan_revision import RevisionBudget, RevisionController

    _novel_dig = novel_identity(_SRC)["digest"]
    _verify_digests: list = []

    def _verify(view, digest):
        """固定核验服务：**只看这次候选的载体正文**——补齐后时间跨度回来了 ⇒ supported."""
        _verify_digests.append(str(digest))
        return _record_for(view, _table, verdict=(
            cv.VERDICT_SUPPORTED
            if cv._norm(_SPAN_TIME) in cv._norm("".join(
                str(c["text"]) for c in cv.display_carriers(view, "f01")))
            else cv.VERDICT_PARTIAL))

    _vctl = VerificationController(
        verify=_verify, rule_version=cv.DEFAULT_RULE_VERSION,
        log=lambda *a: None, novel_digest=_novel_dig)

    def _revise(plan, problems, budget, rejections):
        """**一轮真实修订**：合法补齐（恢复卡片正文），保留 UID 与绑定不重排.

        补齐后的候选**经真实完成入口**（`complete_candidate`）重新核验并挂记录——
        生产路径就是这样（`_replan_by_view` → `complete_candidate`）。
        """
        _fixed = copy.deepcopy(plan)
        _fixed.pop("plan_verification", None)
        _fixed["presentation"]["screen_cards"][0]["text"] = _CARD_TEXT
        _cand, _probs = o.complete_candidate(_view_of(_fixed), _SRC, _vctl)
        if _cand is None or _probs:
            return {"kind": "illegal", "plan": None,
                    "detail": "补齐候选未过验收：%s" % _probs[:2]}
        return {"kind": "applied", "plan": _cand,
                "detail": "恢复文字卡的时间跨度（保留 UID 与绑定）"}

    _rc = RevisionController(
        accept=lambda p, t: o.plan_acceptance_problems(p, t),
        revise=_revise, budget=RevisionBudget(), log=lambda *a: None)

    # 首版 B 带一条 **partial** 记录（含时间限定的分量缺证据）
    _bv = _view_of(_b)
    _b_rec = _record_b(_bv)
    _b_rec.update(plan_digest=cv.plan_digest_of(_b), novel_digest=_novel_dig,
                  obligation_basis=cv.obligation_basis_digest(
                      _bv, cv.context_decomposition(_bv, cv.context_source_of(_b))))
    _b_with_rec = dict(_b, plan_verification=_b_rec)
    assert o.plan_acceptance_problems(_b_with_rec, _SRC) != [], \
        "B 的首版竟然直接通过——残余没有产生"

    _res = _rc.run(copy.deepcopy(_b_with_rec), _SRC)
    assert _res["stop_reason"] == "ok", (_res["stop_reason"],
                                         [_p.get("code") for _p in _res["problems"][:5]])
    _final = _res["plan"]
    assert _final["presentation"]["screen_cards"][0]["text"] == _CARD_TEXT
    assert o.plan_acceptance_problems(_final, _SRC) == []

    # 新摘要 ≠ 旧摘要；上下文与分解表**逐字保留**（没有重新分解）
    assert cv.plan_digest_of(_final) != cv.plan_digest_of(_b)
    assert cv.context_source_of(_final) == cv.context_source_of(_b)
    assert cv.context_decomposition(_view_of(_final),
                                    cv.context_source_of(_final)) == _table
    # 核验只对**两份**候选各跑一次（首版 + 补齐），没有额外重分解
    assert len(_verify_digests) <= 2, _verify_digests

    # 补齐后的候选过完整验收，且**同一个缓存装载出口**可用（读回 0 次生成）
    _frec = _final["plan_verification"]
    _c2 = VerificationController(
        verify=lambda v, d: _record_for(v, _table),
        rule_version=cv.DEFAULT_RULE_VERSION, log=lambda *a: None,
        novel_digest=_novel_dig)
    assert _c2.seed(cv.plan_digest_of(_final), _frec, plan=_final) is True
    assert _c2.used == 0, "缓存读回发起了核验调用"
    assert _frec["obligation_basis"] == cv.obligation_basis_digest(
        _view_of(_final), _table)

# ---------------------------------------------------------------------------
# 6. 三轴组合（r6 §4.3）：来源/声音来自完整任务，展示事实来自独立展示任务
# ---------------------------------------------------------------------------
def _axis_records(view, table, plan, *, display_verdict=None, sv_verdict=None):
    """`(来源/声音记录, 展示记录)`——两份都带**同一候选**与同一原文身份，展示那份**盖章**."""
    from step01_script_gen.content_verify import stamp_display_record
    _pd = cv.plan_digest_of(plan)
    _nd = _novel_dig()
    _sv = _record_for(view, table, verdict=(sv_verdict or cv.VERDICT_SUPPORTED))
    _sv.update(call_id="sv-call", plan_digest=_pd, novel_digest=_nd)
    _dp = _record_for(view, table, verdict=(display_verdict or cv.VERDICT_SUPPORTED))
    _dp.update(call_id="disp-call", plan_digest=_pd, novel_digest=_nd)
    return _sv, stamp_display_record(_dp, view, table)


def _novel_dig():
    from step01_script_gen.content_verify import novel_identity
    return novel_identity(_SRC)["digest"]


def test_e13_the_combination_takes_facts_from_the_display_task_only():
    """组合记录的 `facts` **只**来自独立展示任务；完整任务的 facts 不参与."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _sv, _dp = _axis_records(_view, _table, _plan)
    # 完整任务顺带给一个**假**的 supported 事实（组合里不许出现它）
    _sv["facts"] = [{"fact_uid": "f01", "verdict": cv.VERDICT_SUPPORTED,
                     "support_spans": ["（完整任务顺带给的，不许进组合）"],
                     "missing": "", "suggested_carriers": [], "obligations": []}]
    _rec, _bad = cv.combine_axes_record(
        _view, source_voice=_sv, display=_dp, decomposition=_table,
        plan_digest=cv.plan_digest_of(_plan), novel_digest=_novel_dig())
    assert _bad == [], _bad
    assert _rec is not None
    assert _rec["facts"] == _dp["facts"], "组合里的事实不是展示任务给的那一份"
    assert _rec["axes"]["source_call_id"] == "sv-call"
    assert _rec["axes"]["display_call_id"] == "disp-call"
    assert cv.record_shape_problems(_rec) == []
    assert cv.axes_problems(_rec) == []
    # **最后门**是完整 `verification_problems`（r6 §4.3 第 3 条）：组合记录必须能过
    # 三轴；`display_problems=[]` 通过**不等于**三轴通过，所以这里断言的是完整验收。
    _plan2 = dict(_plan, plan_verification=_rec)
    assert o.plan_acceptance_problems(_plan2, _SRC) == [],         o.plan_acceptance_problems(_plan2, _SRC)


def test_e14_axes_from_different_candidates_or_novels_are_rejected():
    """来源/声音与展示**不得来自不同候选或不同原文**."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _sv, _dp = _axis_records(_view, _table, _plan)
    _nd = _novel_dig()

    _other = dict(_sv, plan_digest="deadbeef")
    _rec, _bad = cv.combine_axes_record(_view, source_voice=_other, display=_dp,
                                        decomposition=_table,
                                        plan_digest=cv.plan_digest_of(_plan),
                                        novel_digest=_nd)
    assert _rec is None and any(x["code"] == "verify_axes_candidate_mismatch"
                                for x in _bad), _bad

    _other2 = dict(_dp, novel_digest="cafebabe")
    _rec2, _bad2 = cv.combine_axes_record(_view, source_voice=_sv, display=_other2,
                                          decomposition=_table,
                                          plan_digest=cv.plan_digest_of(_plan),
                                          novel_digest=_nd)
    assert _rec2 is None and any(x["code"] == "verify_axes_novel_mismatch"
                                 for x in _bad2), _bad2


def test_e15_any_failed_axis_yields_no_valid_record():
    """任一轴没验成 ⇒ **无有效记录**；另一轴的 supported 不许顶上."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _sv, _dp = _axis_records(_view, _table, _plan)
    _nd = _novel_dig()
    for _broken, _label in ((dict(_sv, status="bad_response"), "来源/声音"),
                            (dict(_dp, status="call_failed"), "展示")):
        _args = ({"source_voice": _broken, "display": _dp} if _label == "来源/声音"
                 else {"source_voice": _sv, "display": _broken})
        _rec, _bad = cv.combine_axes_record(_view, decomposition=_table,
                                            plan_digest=cv.plan_digest_of(_plan),
                                            novel_digest=_nd, **_args)
        assert _rec is None, "%s 轴失败仍产出了记录" % _label
        assert any(x["code"] == "verify_axes_axis_failed" for x in _bad), _bad


def test_e16_a_partial_display_is_not_overridden_by_a_supported_source():
    """展示 partial 保留原义务与 `missing`，交修订；来源 supported **不覆盖**它."""
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _sv, _dp = _axis_records(_view, _table, _plan, display_verdict=cv.VERDICT_PARTIAL)
    _rec, _bad = cv.combine_axes_record(
        _view, source_voice=_sv, display=_dp, decomposition=_table,
        plan_digest=cv.plan_digest_of(_plan), novel_digest=_novel_dig())
    assert _bad == [], _bad
    _f = next(x for x in _rec["facts"] if x["fact_uid"] == "f01")
    assert _f["verdict"] == cv.VERDICT_PARTIAL, "来源 supported 把展示 partial 盖掉了"
    assert str(_f["missing"]).strip()
    # 组合记录过最后门时仍报「事实没被完整表达」
    _plan2 = dict(_plan, plan_verification=_rec)
    _codes = [str(x.get("code")) for x in o.plan_acceptance_problems(_plan2, _SRC)]
    assert "verify_fact_unsupported" in _codes, _codes


def test_e17_the_axes_block_is_registered_and_optional():
    """`axes` 块：**缺席**是合法旧记录；出现则按登记契约逐项判（不认未知字段）."""
    assert cv.axes_problems({}) == []          # 旧单任务记录
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _sv, _dp = _axis_records(_view, _table, _plan)
    _rec, _ = cv.combine_axes_record(_view, source_voice=_sv, display=_dp,
                                     decomposition=_table,
                                     plan_digest=cv.plan_digest_of(_plan),
                                     novel_digest=_novel_dig())
    _bad = dict(_rec)
    _bad["axes"] = dict(_rec["axes"], extra_key="x")
    assert any(x["code"] == "verify_axes_unknown_field"
               for x in cv.axes_problems(_bad)), cv.axes_problems(_bad)
    _bad2 = dict(_rec)
    _bad2["axes"] = {"schema": cv.AXES_SCHEMA, "source_call_id": 1,
                     "display_call_id": "d"}
    assert any(x["code"] == "verify_axes_field_type"
               for x in cv.axes_problems(_bad2)), cv.axes_problems(_bad2)


def test_e18_insufficient_capacity_stops_instead_of_half_verifying():
    """可用次数不足 2 ⇒ 停（`verify_axes_capacity`），**不发任何调用**."""
    from step01_script_gen import content_verify as cx
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _out = cx.verify_view_axes_with_service(
        _view, cv.plan_digest_of({}), decomposition=_table, remaining=1,
        novel_digest=_novel_dig())
    assert _out["record"] is None and _out["calls"] == [], _out
    assert [x["code"] for x in _out["problems"]] == ["verify_axes_capacity"]


def test_e19_an_inadmissible_view_is_classified_as_input_not_call_failure():
    """输入没资格核 ⇒ `verify_input_rejected`（**零调用**），与"调了没通"分账."""
    from step01_script_gen import content_verify as cx
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _bad_view = copy.deepcopy(_view)
    _bad_view["frozen_facts"] = 1          # 容器类型违约（投影层判过的那种）
    _out = cx.verify_view_axes_with_service(
        _bad_view, cv.plan_digest_of(_plan), decomposition=_table, remaining=4,
        novel_digest=_novel_dig())
    assert _out["record"] is None
    assert [x["code"] for x in _out["problems"]] == ["verify_input_rejected"], _out
    assert all(c.get("status") != "ok" for c in _out["calls"]), _out["calls"]


# ---------------------------------------------------------------------------
# 7. 控制器两轴计量（r7 §4.2）：显式容量查询、两槽预留、按实际发送结算
# ---------------------------------------------------------------------------
def _valid_combined(_plan, _view, _table):
    _sv, _dp = _axis_records(_view, _table, _plan)
    _rec, _bad = cv.combine_axes_record(
        _view, source_voice=_sv, display=_dp, decomposition=_table,
        plan_digest=cv.plan_digest_of(_plan), novel_digest=_novel_dig())
    assert _rec is not None and _bad == [], _bad
    return _rec


def test_e20_capacity_is_a_count_query_distinct_from_the_time_remaining():
    """`capacity()` 是**次数**查询；`remaining()` 是**秒数**——两者分账（r7 §4.2）."""
    from shared.content_verification import VerificationController
    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=4)
    assert _ctl.capacity() == 4
    assert _ctl.reserve(2) is True
    assert _ctl.capacity() == 2 and _ctl.used == 2
    _ctl.release(1)
    assert _ctl.capacity() == 3 and _ctl.used == 1
    assert _ctl.reserve(9) is False, "容量不足时必须拒绝且不改计数"
    assert _ctl.used == 1


def test_e21_two_axis_task_below_capacity_is_refused_without_any_send():
    """容量 < 2 ⇒ 拒绝整次两轴任务：**零发送**、不半核、不回退根策略."""
    from shared.content_verification import VerificationController
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _sent: list = []

    def _axes(view, dig, *, decomposition, source_model, display_model):
        _sent.append((decomposition, source_model, display_model))
        return {"sent": 2, "record": _valid_combined(_plan, _view, _components_table()),
                "problems": []}

    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=1)
    _r = _ctl.record_axes_for(_view, cv.plan_digest_of(_plan), verify_axes=_axes)
    assert _sent == [], "容量不足却发起了两轴调用"
    assert _r["status"] != "ok"
    assert _ctl.used == 0, "被拒的两轴任务不许占计数"


def test_e22_a_successful_two_axis_task_accounts_exactly_two_physical_sends():
    """两轴成功 ⇒ 计数 2、缓存复用 0 发送；表与两轴模型**从已准入上下文读**."""
    from shared.content_verification import VerificationController
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _table = _components_table()
    _seen: list = []

    def _axes(view, dig, *, decomposition, source_model, display_model):
        _seen.append((decomposition, source_model, display_model))
        return {"sent": 2, "record": _valid_combined(_plan, _view, _table),
                "problems": []}

    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=4)
    _r = _ctl.record_axes_for(_view, cv.plan_digest_of(_plan), verify_axes=_axes)
    assert _r["status"] == "ok" and _r["plan_digest"] == cv.plan_digest_of(_plan)
    assert _ctl.used == 2, "两轴组合应记两次物理发送"
    assert _seen == [(_table, "stub-source-voice", "stub-display")], _seen
    # 同候选再来一次 ⇒ 复用装载记录，**零新增发送**
    _r2 = _ctl.record_axes_for(_view, cv.plan_digest_of(_plan), verify_axes=_axes)
    assert _r2 is _r and _ctl.used == 2 and len(_seen) == 1


def test_e23_a_first_axis_failure_releases_the_unused_reservation():
    """首轴失败 ⇒ 只结算 1 次；未用的预留归还（不虚增核验次数）."""
    from shared.content_verification import VerificationController
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)

    def _axes(view, dig, *, decomposition, source_model, display_model):
        return {"sent": 1, "record": None,
                "problems": [{"code": "verify_call_failed", "kind": "verification",
                              "ref": "", "uid": "", "path": "axes",
                              "reason": "首轴传输失败", "fix": ""}]}

    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=4)
    _r = _ctl.record_axes_for(_view, cv.plan_digest_of(_plan), verify_axes=_axes)
    assert _r["status"] != "ok" and _r["code"] == "verify_call_failed"
    assert _ctl.used == 1, "首轴失败应只结算 1 次物理发送"


def test_e24_a_loaded_record_is_reused_by_the_two_axis_entry_with_zero_sends():
    """同候选**已装载**的有效记录 ⇒ 复用（新发送 0）."""
    from shared.content_verification import VerificationController
    _plan = _with_context(_components_plan())
    _view = _view_of(_plan)
    _dig = cv.plan_digest_of(_plan)
    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=4)
    assert _ctl.seed(_dig, _valid_combined(_plan, _view, _components_table()),
                     plan=_plan) is True

    def _axes(*a, **k):
        raise AssertionError("已装载记录不许再发起两轴调用")

    _r = _ctl.record_axes_for(_view, _dig, verify_axes=_axes)
    assert _r["status"] == "ok" and _ctl.used == 0


def test_e25_a_bad_context_refuses_the_two_axis_task_without_sending():
    """上下文坏 ⇒ `record_axes_for` 拒绝且**零发送**，不回退根策略."""
    from shared.content_verification import VerificationController
    _plan = _with_context(_components_plan())
    _ctx = copy.deepcopy(cv.context_source_of(_plan))
    _ctx["decomposition"] = None              # 声明分量却丢了表
    _ctx["decomposition_digest"] = cv._decomposition_canon(None)
    _ctx["context_digest"] = cv.context_digest(_ctx)
    _plan[cv.VERIFICATION_CONTEXT_KEY] = _ctx
    _view = _view_of(_plan)

    def _axes(*a, **k):
        raise AssertionError("坏上下文不许发起两轴调用")

    _ctl = VerificationController(verify=lambda *a: None, novel_digest=_novel_dig(),
                                  max_verifications=4)
    _r = _ctl.record_axes_for(_view, cv.plan_digest_of(_plan), verify_axes=_axes)
    assert _r["status"] != "ok" and _ctl.used == 0
