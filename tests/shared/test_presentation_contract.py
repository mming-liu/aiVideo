# -*- coding: utf-8 -*-
"""R1：共用可编辑计划视图（`shared/presentation_contract.py`）.

规格：`docs/aiVideo-step01-planning-closure-2026-10-04.md` R1 +
`docs/aiVideo-screen-hybrid-plan-2026-10-04.md` §2.

钉住三件事：

1. **往返逐字一致**——旧计划经适配器进视图、再投影回来不许改写任何字段
   （这是"旧协议退为兼容适配器"的前提，改写会在缓存/比较处凭空制造"核心字段被改动"）。
2. **派生字段由代码生成**——B/D/N 展示编号、节拍的 `｜载体：…`、旁白的 `fact_refs`、
   `hook_carrier` 文案都不再由模型回填（R1 的"模型不再同时算三套编号"）。
3. **引用与模式合同 fail-closed**——重复 uid、悬空引用、新模式带旁白、旧模式带卡片、
   绑定缺项/多项、未知事实，一律结构化拒收。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402

# 样本与 R0 交付的**同一份** fixture（跨目录复用，避免两份样本漂移）
_SAMPLE = json.loads(
    (_ROOT / "tests" / "step01_script_gen" / "fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_LEGAL, _ = o.apply_replan_ops(_SAMPLE["base_plan"], _SAMPLE["response"], _SAMPLE["source"])

_FIELDS = ("required_facts", "shot_beats", "dialogue_lines", "narration_lines",
           "hook_spoken", "hook_carrier")


def _codes(probs):
    return [p["code"] for p in probs]


# ---------------------------------------------------------------------------
# ① 往返逐字一致
# ---------------------------------------------------------------------------
def test_a_legal_plan_round_trips_byte_for_byte():
    """合法计划：`plan → view → plan` **全字段逐字一致**（适配器路径的硬要求）."""
    _view, _p = pc.view_from_plan(_LEGAL)
    assert _p == [], _codes(_p)
    assert _codes(pc.parse_view(_view)[1]) == []
    _back = pc.plan_from_view(_view)
    for _k in _FIELDS:
        assert _back.get(_k) == _LEGAL.get(_k), "%s 被改写了" % _k


def test_a_self_contradictory_plan_normalises_to_the_authoritative_relation():
    """**自相矛盾**的旧计划：事实声明 `B1/旁白2` 而 B1 标的是 `旁白1` ⇒

    投影**不凭空造关系**：找不到标着「旁白2」的节拍就写 `B0/旁白2`（关系本就不成立），
    `required_facts` 因此与原文不同——这是**忠实**而不是改写，其余字段仍逐字一致。
    """
    _plan = json.loads(json.dumps(_LEGAL))
    _plan["required_facts"][1] = _plan["required_facts"][1].rsplit("｜承载：", 1)[0] + "｜承载：B1/旁白2"
    _view, _ = pc.view_from_plan(_plan)
    _back = pc.plan_from_view(_view)
    # 归一化方向：**视图以「节拍自己的 `voice_ref`」为权威** ⇒ 声明被改写到真正标着
    # 「旁白2」的那条节拍（B2）上，而不是保留那个自相矛盾的 B1。
    assert _back["required_facts"][1].endswith("｜承载：B2/旁白2"), _back["required_facts"][1]
    for _k in ("shot_beats", "dialogue_lines", "narration_lines"):
        assert _back.get(_k) == _plan.get(_k), _k


# ---------------------------------------------------------------------------
# ② 派生字段由代码生成（模型只填内容与归属）
# ---------------------------------------------------------------------------
def test_derived_numbers_carriers_and_fact_refs_are_produced_by_code():
    """视图里**只**给 `fact_bindings` 与单位归属；编号/载体段/`fact_refs` 全是派生的."""
    _view = {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_LEGACY,
        "frozen_facts": [{"uid": "f01", "claim": "甲辞职", "evidence": "“甲递了辞呈。”"},
                         {"uid": "f02", "claim": "乙接管", "evidence": "“乙随后接管。”"}],
        "frozen_events": ["甲辞职", "乙接管"],
        "beats": [
            {"uid": "b01", "event_uids": ["甲辞职"], "text": "甲把辞呈放在桌上",
             "purpose": "推进", "evidence": "“甲递了辞呈。”",
             "voice_ref": {"kind": pc.KIND_NARRATION, "uid": "n01"}},
            {"uid": "b02", "event_uids": ["乙接管"], "text": "乙翻开账册",
             "purpose": "推进", "evidence": "“乙随后接管。”",
             "voice_ref": {"kind": pc.KIND_VISUAL, "uid": "b02"}},
        ],
        "dialogue_units": [],
        "narration_units": [{"uid": "n01", "text": "甲递了辞呈。",
                             "source": {"text": "甲递了辞呈。"}}],
        "screen_cards": [],
        "fact_bindings": [
            {"fact_uid": "f01", "carriers": [{"kind": pc.KIND_NARRATION, "uid": "n01"}]},
            {"fact_uid": "f02", "carriers": [{"kind": pc.KIND_VISUAL, "uid": "b02"}]},
        ],
        "hook_bindings": {"spoken_uids": [], "carrier": None},
        "evidence_updates": [],
    }
    _plan = pc.plan_from_view(_view)
    # 节拍的载体段：由 `voice_ref` 派生
    assert _plan["shot_beats"][0].endswith("｜载体：旁白1"), _plan["shot_beats"][0]
    assert _plan["shot_beats"][1].endswith("｜载体：画面"), _plan["shot_beats"][1]
    # 旁白的 `fact_refs`：由绑定**反查**（视图里根本没写）
    assert _plan["narration_lines"][0]["fact_refs"] == [1], _plan["narration_lines"]
    # 事实的承载：由绑定 + 上述编号派生
    assert _plan["required_facts"][0].endswith("｜承载：B1/旁白1"), _plan["required_facts"][0]
    assert _plan["required_facts"][1].endswith("｜承载：B2/画面"), _plan["required_facts"][1]
    # 投影后必须能过真实验收（关系闭合）
    assert pc.view_problems(_view) == []


def test_evidence_updates_only_replace_the_source_fragment():
    """`evidence_updates` 只更正**来源片段**：正文主张冻结，基础摘要不变、证据摘要变化."""
    _view, _ = pc.view_from_plan(_LEGAL)
    _basis0, _ev0 = pc.fact_basis_digest(_view), pc.evidence_digest(_view)
    _v2 = json.loads(json.dumps(_view))
    _v2["evidence_updates"] = [{"fact_uid": "f01", "evidence": "“另一句原文。”"}]
    assert pc.fact_basis_digest(_v2) == _basis0, "证据更正不该动**基础**摘要（主张+事件序才是基础）"
    assert pc.evidence_digest(_v2) != _ev0, "证据摘要必须跟着变"
    _plan = pc.plan_from_view(_v2)
    assert "另一句原文。" in _plan["required_facts"][0], _plan["required_facts"][0]
    assert _plan["required_facts"][0].startswith(
        _LEGAL["required_facts"][0].split("｜")[0]), "正文主张不许被证据更正带走"


def test_evidence_updates_cannot_touch_the_claim():
    """证据更正**只能**改证据：把 `evidence_updates` 用来换主张是结构上做不到的."""
    _view, _ = pc.view_from_plan(_LEGAL)
    _v2 = json.loads(json.dumps(_view))
    _v2["evidence_updates"] = [{"fact_uid": "f01", "evidence": "x", "claim": "换掉主张"}]
    _plan = pc.plan_from_view(_v2)
    assert _plan["required_facts"][0].startswith(_LEGAL["required_facts"][0].split("｜")[0])
    assert pc.fact_basis_digest(_v2) == pc.fact_basis_digest(_view)


def _declared(base):
    """把视图标成「事件归属**已声明**」，并按节拍顺序声明一份（旧计划本身不带归属）."""
    _v = json.loads(json.dumps(base))
    _v["event_binding"] = "declared"
    _ev = _v["frozen_events"]
    for _i, _b in enumerate(_v["beats"]):
        _b["event_uids"] = [_ev[_i]] if _i < len(_ev) else []
    return _v


def test_event_coverage_and_order_are_enforced_only_when_declared():
    """**只有声明了归属才核**事件覆盖与顺序——未声明时既不核也不伪造覆盖率."""
    _base = _declared(pc.view_from_plan(_LEGAL)[0])
    assert _codes(pc.view_problems(_base)) == []          # 合法对照
    # ① 丢掉事件 ⇒ 未覆盖
    _v = json.loads(json.dumps(_base))
    _v["beats"][1]["event_uids"] = _v["frozen_events"][:1]
    assert "view_event_uncovered" in _codes(pc.view_problems(_v)), _codes(pc.view_problems(_v))
    # ② 引用不存在的事件
    _v2 = json.loads(json.dumps(_base))
    _v2["beats"][0]["event_uids"] = ["不存在的事件"]
    assert "view_event_unknown" in _codes(pc.view_problems(_v2))
    # ③ 事件顺序回退：把**声明的事件序**反过来，而节拍仍按原序承载 ⇒ 回退
    _v3 = json.loads(json.dumps(_base))
    _v3["frozen_events"] = list(reversed(_v3["frozen_events"]))
    assert "view_event_order_reversed" in _codes(pc.view_problems(_v3))
    # ④ 候选把归属退成「未确定」⇒ 拒收（不能靠不声明绕过覆盖检查）
    _v4 = json.loads(json.dumps(_base))
    _v4["event_binding"] = "unknown"
    assert "view_event_binding_downgraded" in _codes(
        pc.view_problems(_v4, base_view=_base))


def test_legacy_adapter_does_not_fabricate_event_ownership():
    """旧计划**没有**逐节拍归属 ⇒ 标「未确定」，**不按位置一对一编一份**.

    按位置编的后果是实测过的形态：一条"仓库外景"节拍被登记成"周明辞职"，事件覆盖检查
    照样通过——**伪造的关系比没有关系更危险**，它会冒充覆盖证明。
    """
    _v, _p = pc.view_from_plan(_LEGAL)
    assert _p == []
    assert _v["event_binding"] == "unknown"
    assert all(b["event_uids"] == [] for b in _v["beats"]), "旧计划不许被编出事件归属"
    assert _codes(pc.view_problems(_v)) == []              # 未声明 ⇒ 不核，不是"失败"
    # 新模式**必须**声明归属（缺了不许判通过）
    _h = json.loads(json.dumps(_v))
    _h["mode"] = pc.MODE_HYBRID
    _h["narration_units"] = []
    for _b in _h["beats"]:
        _b["voice_ref"] = {"kind": pc.KIND_VISUAL, "uid": _b["uid"]}
    for _b in _h["fact_bindings"]:
        _b["carriers"] = [{"kind": pc.KIND_VISUAL, "uid": _h["beats"][0]["uid"]}]
    assert "view_event_binding_undeclared" in _codes(pc.parse_view(_h)[1])


def test_fact_basis_change_against_the_base_is_rejected():
    _base, _ = pc.view_from_plan(_LEGAL)
    # 改**正文主张** ⇒ 基础被换（真正冻结的那部分动了）
    _v = json.loads(json.dumps(_base))
    _v["frozen_facts"][0]["claim"] = "被改过的正文主张"
    assert "view_fact_basis_changed" in _codes(pc.view_problems(_v, base_view=_base))
    # 改**事件顺序** ⇒ 同样是基础被换
    _v2 = json.loads(json.dumps(_base))
    _v2["frozen_events"] = list(reversed(_v2["frozen_events"]))
    assert "view_fact_basis_changed" in _codes(pc.view_problems(_v2, base_view=_base))
    # 合法对照：只更正来源片段 ⇒ 基础摘要**不变**（那是 `evidence_updates` 的正当用途）
    _v3 = json.loads(json.dumps(_base))
    _v3["frozen_facts"][0]["evidence"] = "“新出处。”"
    _v3["evidence_updates"] = [{"fact_uid": "f01", "evidence": "“新出处。”"}]
    assert "view_fact_basis_changed" not in _codes(pc.view_problems(_v3, base_view=_base))
    assert pc.evidence_digest(_v3) != pc.evidence_digest(_base)


# ---------------------------------------------------------------------------
# ③ 引用与模式合同 fail-closed
# ---------------------------------------------------------------------------
def _broken(**over):
    _v, _ = pc.view_from_plan(_LEGAL)
    _v = json.loads(json.dumps(_v))
    _v.update(over)
    return _v


def test_unknown_schema_and_unknown_mode_are_rejected():
    assert "view_schema_unknown" in _codes(pc.parse_view(_broken(schema="nope"))[1])
    assert "view_mode_invalid" in _codes(pc.parse_view(_broken(mode="whatever"))[1])


def test_duplicate_uid_is_rejected():
    _v = _broken()
    _v["narration_units"] = list(_v["narration_units"]) + [dict(_v["narration_units"][0])]
    assert "view_uid_duplicate" in _codes(pc.parse_view(_v)[1])


def test_dangling_references_are_rejected():
    _v = _broken()
    _v["fact_bindings"][0]["carriers"] = [{"kind": pc.KIND_NARRATION, "uid": "n99"}]
    assert "view_binding_dangling" in _codes(pc.parse_view(_v)[1])
    _v2 = _broken()
    _v2["hook_bindings"] = {"spoken_uids": ["d99"], "carrier": None}
    assert "view_hook_spoken_dangling" in _codes(pc.parse_view(_v2)[1])


def test_missing_or_extra_fact_binding_is_rejected():
    _v = _broken()
    _v["fact_bindings"] = _v["fact_bindings"][:-1]
    assert "view_binding_missing" in _codes(pc.parse_view(_v)[1])
    _v2 = _broken()
    _v2["fact_bindings"] = list(_v2["fact_bindings"]) + [
        {"fact_uid": "f99", "carriers": [{"kind": pc.KIND_VISUAL, "uid": "b01"}]}]
    assert "view_binding_unknown_fact" in _codes(pc.parse_view(_v2)[1])


def test_empty_carrier_list_is_rejected():
    """空不等于不改：一条事实没有任何载体必须拒收（重复登记也不能代替表达）."""
    _v = _broken()
    _v["fact_bindings"][0]["carriers"] = []
    assert "view_binding_empty" in _codes(pc.parse_view(_v)[1])


def test_legacy_mode_rejects_more_than_one_carrier_per_fact():
    """复核 2026-10-06 更正④：旧模式表示不了多载体，必须**拒收**而不是静默丢掉.

    实测（真实 `apply_editable_view`）：v5 计划的承载位只有一个（`承载：Bn/Xk`），
    `plan_from_view` 也只取**第一条**画面 + **第一条**声音。写 2 条载体时应用器返回
    `applied`，**投影回计划再读回只剩第一条** —— 画面+旁白、旁白+旁白、画面+画面
    三种组合全部丢第二条。静默丢数据比拒收更糟（R2「不允许静默丢失」），所以旧模式下
    多载体在关系判据这里就拦下；多载体是 `screen_hybrid` 的能力。
    """
    _v = _broken()
    assert _v["mode"] == pc.MODE_LEGACY
    _v["fact_bindings"][0]["carriers"] = [
        {"kind": pc.KIND_VISUAL, "uid": _v["beats"][0]["uid"]},
        {"kind": pc.KIND_NARRATION, "uid": _v["narration_units"][0]["uid"]}]
    assert "view_binding_multi_carrier_legacy" in _codes(pc.view_problems(_v))

    # 合法对照①：同样这份视图、**单载体**不得报这条
    _ok = _broken()
    _ok["fact_bindings"][0]["carriers"] = [
        {"kind": pc.KIND_VISUAL, "uid": _ok["beats"][0]["uid"]}]
    assert "view_binding_multi_carrier_legacy" not in _codes(pc.view_problems(_ok))

    # 合法对照②：判据**按模式**生效，新模式的多载体不受影响
    _hy = _broken(mode=pc.MODE_HYBRID)
    _hy["fact_bindings"][0]["carriers"] = list(_v["fact_bindings"][0]["carriers"])
    assert "view_binding_multi_carrier_legacy" not in _codes(pc.view_problems(_hy))


def _hybrid_view():
    """一份**合法**的新模式视图（每个字段都自洽：无旁白、有卡片、事件已声明）."""
    _v = _declared(pc.view_from_plan(_LEGAL)[0])
    _v["mode"] = pc.MODE_HYBRID
    _v["narration_units"] = []
    _v["screen_cards"] = [{"card_id": "c01", "beat_uid": "b01",
                           "template": "background", "text": "甲递了辞呈。",
                           "placement": "info_panel"}]
    _vis = _v["beats"][0]["uid"]
    for _k, _b in enumerate(_v["beats"]):
        _b["voice_ref"] = {"kind": pc.KIND_VISUAL, "uid": _b["uid"]}
    for _k, _b in enumerate(_v["fact_bindings"]):
        _c = _b["carriers"][0] if _b["carriers"] else {}
        if str(_c.get("kind")) == pc.KIND_VISUAL:
            _b["carriers"] = [{"kind": pc.KIND_VISUAL, "uid": _vis}]
        else:
            _b["carriers"] = [{"kind": pc.KIND_CARD, "uid": "c01"}]
    return _v


def test_mode_contract_is_enforced_both_ways():
    """新模式必须显式 `narration_units=[]`；旧模式不接受 `screen_cards`（不自动清空）."""
    _v, _ = pc.view_from_plan(_LEGAL)
    _v = json.loads(json.dumps(_v))
    _v["mode"] = pc.MODE_HYBRID
    assert "view_hybrid_narration_forbidden" in _codes(pc.parse_view(_v)[1])
    _ok = _hybrid_view()
    assert _codes(pc.parse_view(_ok)[1]) == [], _codes(pc.parse_view(_ok)[1])
    _legacy = json.loads(json.dumps(_ok))
    _legacy["mode"] = pc.MODE_LEGACY
    assert "view_legacy_cards_forbidden" in _codes(pc.parse_view(_legacy)[1])


def test_hybrid_plan_projection_carries_the_cards():
    """新模式的投影把卡片带进计划（`presentation.screen_cards`），且没有旁白表."""
    _plan = pc.plan_from_view(_hybrid_view())
    assert _plan["narration_lines"] == []
    assert _plan["plan_contract_version"] == pc.PLAN_CONTRACT_VERSION_HYBRID
    assert _plan["presentation"]["screen_cards"][0]["card_id"] == "c01"


# ---------------------------------------------------------------------------
# ④ 复核 2026-10-04 P1：身份、顺序、摘要、事件归属
# ---------------------------------------------------------------------------
def test_binding_order_does_not_change_relations():
    """**调换 `fact_bindings` 的排列**不能改变关系——按真实 UID 读，不按数组位置重写.

    旧写法按位置重写 `fact_uid`，于是"调换两行绑定"变成"两条事实串绑"，而按位置编的
    `required_facts` 与 `｜承载：` 也会跟着串。
    """
    _a = pc.plan_from_view(_hybrid_view())
    _b = json.loads(json.dumps(_a))
    _b["presentation"]["fact_bindings"] = list(
        reversed(_b["presentation"]["fact_bindings"]))
    _va, _pa = pc.view_from_plan(_a)
    _vb, _pb = pc.view_from_plan(_b)
    assert _pa == [] and _pb == []
    assert [(x["fact_uid"], [pc.ref_uid(c) for c in x["carriers"]])
            for x in _va["fact_bindings"]] == \
           [(x["fact_uid"], [pc.ref_uid(c) for c in x["carriers"]])
            for x in _vb["fact_bindings"]], "绑定排列改变了关系"
    assert pc.fact_basis_digest(_va) == pc.fact_basis_digest(_vb)
    assert pc.plan_from_view(_vb)["required_facts"] == _a["required_facts"]


def test_whole_plan_round_trips_including_non_relation_fields():
    """**整份计划**往返：关系字段重建，其余字段（`climax`/`causal_chain`/`time_budget` …）
    由 `plan_basis` 原样写回——只比六个关系字段证明不了"保真"."""
    _plan = pc.plan_from_view(_hybrid_view())
    _plan.update({"climax": "林青接管", "causal_chain": ["甲→乙"],
                  "time_budget": "对白约40%", "characters": ["甲", "乙"],
                  "emotional_arc": "平→紧", "end_hook": "钩子",
                  "omittable": ["x"], "key_scenes": ["s"]})
    _v, _p = pc.view_from_plan(_plan)
    assert _p == []
    _back = pc.plan_from_view(_v)
    for _k in ("climax", "causal_chain", "time_budget", "characters", "emotional_arc",
               "end_hook", "omittable", "key_scenes", "main_events"):
        assert _back.get(_k) == _plan.get(_k), "%s 在往返里丢了或被改" % _k


def _hook_plan():
    _ev = ["甲辞职", "乙接管", "清点"]
    return {
        "main_events": _ev, "climax": "乙接管", "causal_chain": ["a→b"],
        "time_budget": "约40%",
        "shot_beats": [
            "节拍：甲把辞呈放在桌上｜本镜信息目的：推进｜原文依据：“甲递了辞呈。”｜载体：台词1",
            "节拍：乙翻开账册｜本镜信息目的：推进｜原文依据：“乙随后接管。”｜载体：台词2",
            "节拍：镜头扫过货位｜本镜信息目的：收束｜原文依据：“货位若干。”｜载体：画面"],
        "dialogue_lines": ["甲递了辞呈。", "乙随后接管。"],
        "narration_lines": [],
        "required_facts": ["甲辞职｜原文证据：“甲递了辞呈。”｜承载：B1/台词1",
                           "乙接管｜原文证据：“乙随后接管。”｜承载：B2/台词2",
                           "货位｜原文证据：“货位若干。”｜承载：B3/画面"],
        "hook_spoken": ["甲递了辞呈。", "乙随后接管。"],
        "hook_carrier": "B1/台词1；B2/台词2",
        "plan_contract_version": pc.PLAN_CONTRACT_VERSION_HYBRID,
        "presentation": {
            "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
            "fact_uids": ["f11", "f22", "f33"],
            "beat_uids": ["bx1", "bx2", "bx3"],
            "dialogue_uids": ["dz1", "dz2"], "narration_uids": [],
            "beat_events": [[_ev[0]], [_ev[1]], [_ev[2]]],
            "screen_cards": [],
            "fact_bindings": [
                {"fact_uid": "f11", "carriers": [{"kind": "dialogue", "uid": "dz1"}]},
                {"fact_uid": "f22", "carriers": [{"kind": "dialogue", "uid": "dz2"}]},
                {"fact_uid": "f33", "carriers": [{"kind": "visual", "beat_uid": "bx3"}]}],
            "hook_bindings": {"spoken_uids": ["dz1", "dz2"],
                              "carriers": [{"kind": "dialogue", "uid": "dz1"},
                                           {"kind": "dialogue", "uid": "dz2"}]}},
    }


def test_stable_voice_ids_and_all_hook_relations_survive_the_round_trip():
    """稳定声音 ID 与**全部**钩子关系都要活过往返（多句钩子不许只剩第一句）."""
    _plan = _hook_plan()
    _v, _p = pc.view_from_plan(_plan)
    assert _p == [], _codes(_p)
    assert [u["uid"] for u in _v["dialogue_units"]] == ["dz1", "dz2"]
    assert [pc.ref_uid(c) for c in _v["hook_bindings"]["carriers"]] == ["dz1", "dz2"]
    _back = pc.plan_from_view(_v)
    assert _back["hook_carrier"] == "B1/台词1；B2/台词2", _back["hook_carrier"]
    assert _back["hook_spoken"] == ["甲递了辞呈。", "乙随后接管。"]
    _v2, _p2 = pc.view_from_plan(_back)
    assert _p2 == []
    assert [u["uid"] for u in _v2["dialogue_units"]] == ["dz1", "dz2"], "身份被重新编号"
    assert [b["uid"] for b in _v2["beats"]] == ["bx1", "bx2", "bx3"]
    assert [f["uid"] for f in _v2["frozen_facts"]] == ["f11", "f22", "f33"]


def test_evidence_only_change_is_not_no_change():
    """**只补出处**也是有效的内容变化：候选要返回，不能被判成 `no_change`."""
    _base, _ = pc.view_from_plan(_LEGAL)
    _v = json.loads(json.dumps(_base))
    _v["evidence_updates"] = [{"fact_uid": _base["frozen_facts"][0]["uid"],
                               "evidence": "“另一句原文。”"}]
    assert pc.view_digest(_v) != pc.view_digest(_base), "证据变化没进候选摘要"
    assert pc.fact_basis_digest(_v) == pc.fact_basis_digest(_base), "基础不该变"
    # `rule_version` 是**必填**（复核 2026-10-06 第十一轮 P2：缺失此前被判 applied）
    _resp = {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
             "base_plan_hash": pc.view_digest(_base),
             "fact_basis_hash": pc.fact_basis_digest(_base), "view": _v}
    _cv, _p, _res = pc.apply_editable_view(_base, _resp)
    assert _res["kind"] == "applied", _res
    assert _cv is not None


def test_illegal_inputs_return_structured_problems_not_exceptions():
    """非法清单/类型/引用一律**结构化拒收**：没有一条路径抛异常."""
    _base = pc.plan_from_view(_hybrid_view())

    def _run(mut):
        _p = json.loads(json.dumps(_base))
        mut(_p)
        _v, _probs = pc.view_from_plan(_p)
        if _v is not None:
            _probs = _probs + pc.parse_view(_v)[1] + pc.view_problems(_v)
        return _codes(_probs)

    _bad_beat = "节拍：x｜本镜信息目的：y｜原文依据：“z”｜载体：台词9"
    _cases = [
        (lambda p: p["shot_beats"].__setitem__(0, 7), "view_plan_item_type"),
        (lambda p: p.__setitem__("required_facts", "不是数组"), "view_plan_field_type"),
        (lambda p: p.__setitem__("dialogue_lines", 3), "view_plan_field_type"),
        (lambda p: p.__setitem__("narration_lines", None), "view_narration_null"),
        (lambda p: p["presentation"].__setitem__("mode", "whatever"), "view_mode_invalid"),
        (lambda p: p["presentation"]["screen_cards"].append("x"), "view_card_item_invalid"),
        (lambda p: p["presentation"]["fact_bindings"].append(
            dict(p["presentation"]["fact_bindings"][0])), "view_binding_duplicate"),
        (lambda p: p["presentation"]["fact_bindings"][0].__setitem__(
            "carriers", [{"kind": "dialogue", "uid": "nope"}]), "view_binding_dangling"),
        (lambda p: p["presentation"].__setitem__(
            "beat_events",
            [["不存在"]] + [list(x) for x in p["presentation"]["beat_events"][1:]]),
         "view_event_unknown"),
        (lambda p: p["shot_beats"].__setitem__(0, _bad_beat), "view_voice_ref_dangling"),
    ]
    for _mut, _want in _cases:
        _got = _run(_mut)
        assert _want in _got, "期望 %s，实得 %s" % (_want, _got)


def test_unknown_presentation_mode_is_rejected_not_silently_downgraded():
    """未知的 `presentation.mode` **结构化拒收**——静默回退会把卡片整块丢掉."""
    _plan = pc.plan_from_view(_hybrid_view())
    _plan["presentation"]["mode"] = "whatever"
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None and "view_mode_invalid" in _codes(_p)
    # 缺 `presentation` 的旧计划仍按旧模式读（合法对照）
    _legacy = dict(_plan)
    _legacy.pop("presentation")
    _legacy["narration_lines"] = []
    _v2, _p2 = pc.view_from_plan(_legacy)
    assert _v2 is not None and _v2["mode"] == pc.MODE_LEGACY, _codes(_p2)


def test_unknown_fact_in_evidence_updates_is_rejected():
    _v = _broken(evidence_updates=[{"fact_uid": "f99", "evidence": "x"}])
    assert "view_evidence_update_unknown_fact" in _codes(pc.parse_view(_v)[1])
    _v2 = _broken(evidence_updates=[{"fact_uid": "f01", "evidence": "   "}])
    assert "view_evidence_update_empty" in _codes(pc.parse_view(_v2)[1])

def test_the_legacy_read_refuses_lossy_conversions_instead_of_silently_dropping():
    """复核 2026-10-07 三轮 ②：旧模式读盘**不许**把违约转成"少一条 / 换了值 / 崩掉"。

    - `narration_lines=[1]` 曾被**筛掉**成 `[]`（丢的是一条声音单位，下游按这个数算）；
    - 旁白数字正文曾被 `str()` 成 `"123"`；
    - `hook_carrier=5` 曾被 `str()` 洗成 `"5"` ⇒ 静默变成"没有钩子载体"；
    - `presentation=1` 让**报错路径自己**抛 `AttributeError`。
    """
    _legacy = json.loads(json.dumps(_SAMPLE["base_plan"]))

    def _read(mut):
        _p = json.loads(json.dumps(_legacy))
        mut(_p)
        return pc.view_from_plan(_p, mode=pc.MODE_LEGACY)

    _v1, _p1 = _read(lambda p: p.__setitem__("narration_lines", [1]))
    assert _v1 is None and "view_narration_item_type" in _codes(_p1), _codes(_p1)
    _v2, _p2 = _read(lambda p: p.__setitem__("narration_lines",
                                             [{"text": 123, "source": "x"}]))
    assert _v2 is None and "view_narration_text_type" in _codes(_p2), _codes(_p2)
    _v3, _p3 = _read(lambda p: p.__setitem__("hook_carrier", 5))
    assert _v3 is None and "view_hook_carrier_type" in _codes(_p3), _codes(_p3)
    # `mode=None` 走"按计划自称的模式" ⇒ `presentation=1` 在**诊断路径**上不许崩
    _p4 = json.loads(json.dumps(_legacy))
    _p4["presentation"] = 1
    _v4, _p4p = pc.view_from_plan(_p4)
    assert _v4 is None and "view_mode_invalid" in _codes(_p4p), _codes(_p4p)
    assert "不是对象" in _p4p[0]["reason"], _p4p[0]
    # **合法对照**：原样的旧计划照读不误
    _vok, _pok = _read(lambda p: None)
    assert _vok is not None and _vok["mode"] == pc.MODE_LEGACY, _codes(_pok)

