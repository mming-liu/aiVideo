# -*- coding: utf-8 -*-
"""收口包 2：**说明 ←→ 判据同源**（写在提示里的，就是代码判的）.

复核 2026-10-06（第十一轮）："核对 schema、提示、解析和反馈中的字段、身份、版本及可修改
范围"。这类漂移在本项目里**已经发生过两次**（v2 修订的 prompt 拼进了 v5 词汇的规则块；
"每条 `event_uids` 覆盖全部事件"与"所有节拍共同覆盖"相反），两次都不是靠"再读一遍"发现的，
而是靠**响应被拒收**发现的。所以这里不做人工核对，把它变成**机械断言**：

- 增量 schema 的顶层属性集合 == 准入白名单 `DELTA_TOP_KEYS`；
- 说明块**逐个教到**白名单里的每一个键；
- 说明块里 `rule_version` / `schema` 承诺的字面量与常量**同一份**；
- 完整视图的响应骨架里那几个键 == `parse_view` 的必填键（不多不少）；
- 骨架里 `plan_basis` 教的键，`plan_basis()` 真的产得出来。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402

_S = json.loads((_ROOT / "tests" / "step01_script_gen" / "fixtures"
                 / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_LEGAL, _ = o.apply_replan_ops(_S["base_plan"], _S["response"], _S["source"])


def _skeleton(text):
    _m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    assert _m, "响应骨架没找到"
    return json.loads(_m.group(1))


# ---------------------------------------------------------------------------
# 增量：schema / 白名单 / 说明
# ---------------------------------------------------------------------------
def test_the_delta_schema_top_level_is_exactly_the_admission_whitelist():
    _props = set(o.REPLAN_DELTA_SCHEMA["schema"]["properties"])
    assert _props == set(pc.DELTA_TOP_KEYS), (
        sorted(_props - set(pc.DELTA_TOP_KEYS)), sorted(set(pc.DELTA_TOP_KEYS) - _props))
    assert set(o.REPLAN_DELTA_SCHEMA["schema"].get("required") or []) <= _props
    assert not (_props & set(pc.DELTA_FORBIDDEN_KEYS)), \
        "schema 里出现了增量明令禁止的键"


def test_the_delta_instruction_teaches_every_whitelisted_key():
    _txt = o._PLAN_DELTA_INSTRUCTION
    _miss = [_k for _k in pc.DELTA_TOP_KEYS if _k not in _txt]
    assert _miss == [], "说明里没教到的顶层键：%s" % _miss
    _miss2 = [_k for _k in pc.DELTA_ENTITY_KEYS if _k not in _txt]
    assert _miss2 == [], "说明里没教到的实体数组：%s" % _miss2


def test_the_delta_instruction_lists_the_forbidden_keys_as_forbidden():
    """禁用键**只能在"不许出现"那句里**出现（出现过 ≠ 教过）."""
    _txt = o._PLAN_DELTA_INSTRUCTION
    _m = re.search(r"\*\*不许\*\*出现([^。]*)", _txt)
    assert _m, "说明里没有「不许出现」那句"
    _clause = _m.group(1)
    _miss = [_k for _k in pc.DELTA_FORBIDDEN_KEYS if _k not in _clause]
    assert _miss == [], "禁用键没被禁在那句里：%s" % _miss


def test_the_delta_instruction_promises_the_versions_the_code_enforces():
    _txt = o._PLAN_DELTA_INSTRUCTION
    assert o.PLAN_DELTA_RULE_VERSION in _txt
    assert pc.DELTA_SCHEMA in _txt
    assert o.PLAN_DELTA_RULE_VERSION == pc.DELTA_RULE_VERSION
    assert o.PLAN_DELTA_RULE_VERSION != o.PLAN_VIEW_RULE_VERSION
    # 两个信封键都要求原样回带（准入层按这两个摘要核基线）
    assert "base_plan_hash" in _txt and "fact_basis_hash" in _txt


# ---------------------------------------------------------------------------
# 完整视图：骨架 / 必填 / plan_basis
# ---------------------------------------------------------------------------
def test_the_view_skeleton_keys_match_what_parse_view_requires():
    _sk = _skeleton(o._PLAN_VIEW_INSTRUCTION)
    _view = _sk["view"]
    _required = {"frozen_facts", "frozen_events", "beats", "dialogue_units",
                 "narration_units", "screen_cards", "fact_bindings", "hook_bindings"}
    assert _required <= set(_view), "骨架缺必填键：%s" % sorted(_required - set(_view))
    # 骨架**不许**多教键——多教的键会被模型照着写，而 `parse_view` 不认
    _extra = set(_view) - _required - {"schema", "mode", "event_binding",
                                       "evidence_updates", "plan_basis"}
    assert _extra == set(), "骨架教了 `parse_view` 不认的键：%s" % sorted(_extra)
    # 信封键
    assert set(_sk) == {"schema", "rule_version", "base_plan_hash",
                        "fact_basis_hash", "view"}, sorted(_sk)


def test_the_view_skeleton_matches_the_real_view_built_from_a_plan():
    """骨架是**照着一份真视图**写的：键集合与真视图逐字相同（少一个就是少教一个）."""
    _real, _ = pc.view_from_plan(_LEGAL)
    _sk = _skeleton(o._PLAN_VIEW_INSTRUCTION)["view"]
    assert set(_sk) == set(_real), (
        sorted(set(_sk) - set(_real)), sorted(set(_real) - set(_sk)))
    # `plan_basis` 的键**随计划而变**（`plan_basis()` 原样搬运关系之外的字段），
    # 所以要求是"骨架**覆盖**真视图里出现的每一个键"，不是两边集合相等。
    _miss = sorted(set(_real["plan_basis"]) - set(_sk["plan_basis"]))
    assert _miss == [], "骨架没教到的 plan_basis 键：%s" % _miss
    # hybrid 骨架不再示范非法旁白；legacy 的条目形状由实际请求契约补齐。
    _fields = o.replan_view_schema_for_request(pc.revision_request(_real))["schema"]["properties"]["view"]["properties"]
    assert _sk["narration_units"] == []
    assert "maxItems" not in _fields["narration_units"]
    # 每种对象字段仍须在骨架或实际编译的字段契约中完整给出。
    for _coll in ("beats", "dialogue_units", "narration_units"):
        _s_keys = set(_sk[_coll][0]) if _sk[_coll] else set()
        _s_keys |= set(_fields[_coll]["items"]["properties"])
        _r_keys = set(_real[_coll][0]) if _real[_coll] else set()
        assert _r_keys <= _s_keys | {"source_spans", "anchor", "card_id", "uid"}, \
            (_coll, sorted(_r_keys - _s_keys))


def test_the_view_instruction_promises_the_versions_the_code_enforces():
    _txt = o._PLAN_VIEW_INSTRUCTION
    assert o.PLAN_VIEW_RULE_VERSION in _txt
    assert pc.REVISION_SCHEMA in _txt
    assert pc.PRESENTATION_SCHEMA in _txt


# ---------------------------------------------------------------------------
# 事件覆盖：说明与判据同向
# ---------------------------------------------------------------------------
def test_the_event_coverage_wording_matches_the_judgement():
    """复核 P2：「每条覆盖全部事件」与判据相反；改后的文案必须与判据同向.

    判据（`view_problems`）：**所有节拍共同**覆盖全部冻结事件 + 事件先后不得回退。
    照旧文案改，一份原本零问题的合法视图会立刻触发 `view_event_order_reversed`。
    """
    for _txt in (o._PLAN_VIEW_INSTRUCTION, o.render_view_protected_rule(),
                 o._PLAN_DELTA_INSTRUCTION):
        assert "所有节拍共同" in _txt, _txt[:200]
        assert "每条 `event_uids` 必须覆盖全部" not in _txt, "旧文案又回来了"
    # 判据侧：合法视图零问题；按**旧文案**改（每条都覆盖全部事件）必须转红
    _view, _ = pc.view_from_plan(_LEGAL)
    _v = json.loads(json.dumps(_view))
    _v.update(event_binding="declared", narration_units=[], mode=pc.MODE_HYBRID)
    _ev = [str(e) for e in _v["frozen_events"]]
    for _i, _b in enumerate(_v["beats"]):
        _b["voice_ref"] = None
        _b["event_uids"] = [_ev[min(_i, len(_ev) - 1)]]
    _v["screen_cards"] = []
    for _fb in _v["fact_bindings"]:
        _fb["carriers"] = [{"kind": "visual", "uid": str(_v["beats"][0]["uid"])}]
    assert pc.view_problems(json.loads(json.dumps(_v))) == [], \
        [p["code"] for p in pc.view_problems(json.loads(json.dumps(_v)))]
    for _b in _v["beats"]:
        _b["event_uids"] = list(_ev)          # 旧文案让这么写
    _codes = [p["code"] for p in pc.view_problems(json.loads(json.dumps(_v)))]
    assert "view_event_order_reversed" in _codes, _codes
