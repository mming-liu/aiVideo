# -*- coding: utf-8 -*-
"""**I 阶段验收矩阵**（复核 2026-10-02 之八 P1）：`unevaluable` 必须给出**具体原因**.

背景（ch1 实跑的真实形态）：底层明确报「`dialogue_lines` 为空」，但验收链只有一条
`acceptance_incomplete`，`reason` 是「验收未完成：」——**冒号后面什么都没有**；反馈给
模型的也就是这四个字。两个根因：① 报告层读的是生产者**不存在**的单数 `reason`
（生产者给的是 `reasons` 列表）；② 停止分支只产字符串，**不产带路径与身份的记录**。

判据：每个 `unevaluable` 分支都要有 ①具体的 `path`、②原因原话、③**修正要求**（`basis`），
并且这条原因要真的进**下一次请求**（不是只落在诊断里）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:                                                # 复用 G 的夹具
    sys.path.insert(0, str(_HERE))

from step01_script_gen import outline as o                                    # noqa: E402
from test_plan_phase_g_rejection_feedback import _SRC, _legal_plan, _patch    # noqa: E402

_MARKER = "acceptance_incomplete"


def _by_code(problems, code):
    return [x for x in problems if x.get("code") == code]


# ---------------------------------------------------------------------------
# I01/I02 停止分支的具体记录：字段路径 + 原因原话 + 修正要求
# ---------------------------------------------------------------------------
def test_i01_no_selected_dialogue_lines_says_which_field_and_how_to_fix():
    """`dialogue_lines` 为空 ⇒ 记录指向 `dialogue_lines`，并给出修正要求."""
    _p = _legal_plan()
    _p["dialogue_lines"] = []
    _ps = o.plan_acceptance_problems(_p, _SRC)
    _spec = _by_code(_ps, "plan_no_dialogue_lines")
    assert _spec, [x["code"] for x in _ps]
    assert _spec[0]["path"] == "dialogue_lines", _spec
    assert "未选用任何逐字台词" in _spec[0]["reason"], _spec
    assert "逐字" in _spec[0]["basis"] and "修正要求" in _spec[0]["basis"], _spec
    assert _spec[0]["index"] is None, "不是逐条问题，不许编造项号"


def test_i02_every_ungrounded_line_gets_its_own_indexed_record():
    """全部台词不在原文 ⇒ **逐句**给身份（`dialogue_lines[i]`）、原因与修正要求."""
    _p = _legal_plan(dialogue_lines=["原文里根本没有的句子。", "另一句也不在。"])
    _ps = o.plan_acceptance_problems(_p, _SRC)
    _recs = _by_code(_ps, "dialogue_line_not_in_novel")
    assert [x["path"] for x in _recs] == ["dialogue_lines[0]", "dialogue_lines[1]"], _recs
    assert [x["index"] for x in _recs] == [1, 2], _recs
    assert all("未在原文里逐字找到" in x["reason"] for x in _recs), _recs
    assert all("逐字" in x["basis"] for x in _recs), _recs


def test_i03_a_plan_missing_required_fields_names_them():
    """缺 `dialogue_lines` ⇒ 停止分支点名是**哪个**字段（不是"计划不合格"）."""
    _p = _drop(_legal_plan(), "dialogue_lines")
    _ps = o.plan_acceptance_problems(_p, _SRC)
    _spec = _by_code(_ps, "plan_fields_missing")
    assert _spec and _spec[0]["path"] == "dialogue_lines", _ps
    assert "缺少可核算字段" in _spec[0]["reason"], _spec


def test_i03b_a_missing_shot_beats_is_named_by_the_structure_gate():
    """缺 `shot_beats` 由**更早的结构门**拦下——两条路都必须点名到具体字段."""
    _ps = o.plan_acceptance_problems(_drop(_legal_plan(), "shot_beats"), _SRC)
    assert [x["path"] for x in _ps] == ["shot_beats"], _ps
    assert _ps[0]["code"].startswith("structure"), _ps


def test_i04_a_missing_novel_text_says_so_with_its_own_path():
    """缺原文 ⇒ 记录指向 `novel_text`（fail-closed，但不许只说"验收未完成"）."""
    _ps = o.plan_acceptance_problems(_legal_plan(), "")
    _spec = _by_code(_ps, "source_text_missing")
    assert _spec and _spec[0]["path"] == "novel_text", _ps
    assert "缺少原文文本" in _spec[0]["reason"], _spec


# ---------------------------------------------------------------------------
# I05 每个 unevaluable 分支都不许留空原因（含标记记录本身）
# ---------------------------------------------------------------------------
def _drop(plan, key):
    _p = dict(plan)
    _p.pop(key)
    return _p


_UNABLE_CASES = [
    ("缺可核算字段", lambda: (_drop(_legal_plan(), "dialogue_lines"), _SRC), "dialogue_lines"),
    ("缺原文", lambda: (_legal_plan(), ""), "novel_text"),
    ("没有逐字台词", lambda: (dict(_legal_plan(), dialogue_lines=[]), _SRC), "dialogue_lines"),
    ("台词全不在原文",
     lambda: (dict(_legal_plan(), dialogue_lines=["不在原文的句子。"]), _SRC), "dialogue_lines"),
]


@pytest.mark.parametrize("name,build,expect_path", _UNABLE_CASES,
                         ids=[x[0] for x in _UNABLE_CASES])
def test_i05_no_unevaluable_branch_leaves_an_empty_reason(name, build, expect_path):
    """不变式：`unevaluable` 的标记记录必须带**生产者原话**，且至少要有一条定位记录."""
    _p, _novel = build()
    _ps = o.plan_acceptance_problems(_p, _novel)
    _codes = [x["code"] for x in _ps]
    _marker = _by_code(_ps, _MARKER)
    assert _marker, (name, _codes)
    _why = str(_marker[0]["reason"])
    assert _why.startswith("验收未完成："), (name, _why)
    assert len(_why) > len("验收未完成：") + 4, ("原因不许是空冒号", name, _why)
    # 兜底文案出现 = 生产者没给 `reasons`（**回归信号**）：原话必须在场
    assert "未给出具体原因" not in _why, ("生产者的原话必须在场", name, _why)
    assert [x for x in _ps if str(x.get("path") or "").startswith(expect_path)], \
        (name, _codes, [x.get("path") for x in _ps])
    assert all(str(x.get("reason") or "").strip() for x in _ps), (name, _ps)


# ---------------------------------------------------------------------------
# I06 **反馈闭环**：具体原因必须进入下一次请求（不是只落在诊断里）
# ---------------------------------------------------------------------------
def test_i06_the_concrete_reason_reaches_the_next_request(monkeypatch):
    """首版选不出逐字台词 ⇒ 第一次修订请求里要带上字段路径、原因原话与修正要求.

    这条是"反馈闭环"的判据：诊断里写得多清楚都不算数——模型**看不到**就等于没有。
    """
    _p = _legal_plan()
    _p["dialogue_lines"] = []
    # 响应给**非法**的：本用例只问「原因有没有进请求」，不重复 J03 的
    # 「合法响应能否走到成功出口」（复核之九：路由改对之后，空台词表这一轮其实
    # **能修好**——那由 J03 验收）
    _svc = _patch(monkeypatch, [_p] + ["不是 JSON"] * 3)
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is False, _r["acceptance"]
    assert len(_svc.reqs) >= 2, "首版之后必须真的发起过修订请求"
    _rev = _svc.reqs[1].user_prompt
    assert "dialogue_lines" in _rev, "字段路径要进请求"
    assert "未选用任何逐字台词" in _rev, "生产者原因原话要进请求"
    assert "修正要求" in _rev, "怎么改也要进请求"
    assert "验收未完成：" + chr(10) not in _rev, "旧形态（空原因）不许再出现"
