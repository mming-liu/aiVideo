# -*- coding: utf-8 -*-
"""**组合修法验收矩阵**（复核 2026-10-02 之九 第 2 项）：单条通过 ≠ 组合通过.

五种组合各走**真实链**（请求 → 响应 → 应用 → 完整重验 → executable）：

  C1 新增声音条目 **同时**新增承载（一条节拍只能有一个载体 ⇒ 必须同时 split）
  C2 删除声音条目 **同时**撤销/重绑引用（整表语义 + 引用范围按**本批完成后**的表）
  C3 拆分节拍 **同时**更新事实与钩子的承载登记
  C4 收窄/换选内容 **同时**保持核心事件与出处（守卫逐项比对）
  C5 基线已有问题：**局部修订成功后仍反馈剩余问题**（不是累积、也不是清空）

**索引口径**（每条用例里显式断言）：`beat_ops[].index` 对着**输入**（模型看到的那份计划）；
`fact_carriers[].beat` / `台词k` / `旁白k` 对着**输出**（本批完成后）；编号顺延由**代码**
重映射，承载指向必须由**模型重新决定**——两者不许混用（混用就会"修好一处、写坏另一处"）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from shared import call_ledger as cl                                          # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from test_plan_phase_g_rejection_feedback import _LINE, _LINE2, _Svc          # noqa: E402

#: 两句**都逐字来自原文**且 ≥8 字（证据核验要求连续 8 字命中）——组合用例要两句台词/旁白
_SRC2 = "开头。" + _LINE + _LINE2 + "事件丙。"


@pytest.fixture()
def ledger(tmp_path):
    cl.set_ledger_path(tmp_path / "step01_calls.jsonl")
    yield
    cl.set_ledger_path(None)


def _beat(text, carrier, evidence=None):
    """一条节拍（正文 / 信息目的 / 原文依据 / 载体）。`evidence` 缺省用 `_LINE`（逐字来自原文）."""
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (
        text, evidence or _LINE, carrier)


def _plan(**over):
    """三节拍、一句台词、一条必保留事实的**内部自洽**计划（承载双向闭合）."""
    p = {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
         "required_facts": ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LINE],
         "shot_beats": [_beat("甲板上有风", "台词1"), _beat("乙", "画面"), _beat("丙", "画面")]}
    # 2026-10：补齐 `_plan_required_fields()` 的 16 个必需顶层键（原夹具只有 9 个）。
    # 缺键 ⇒ `structurally_valid(plan)` 为 False ⇒ 核验根本不发起 ⇒ 计划上没有
    # `plan_verification` ⇒ 验收报 `verify_record_missing` ⇒ 被判“操作协议表达不了”
    # ⇒ 强制整份重写（而本文件测的正是 ops 组合）⇒ 4 条用例成片误红。
    # 补齐后核验可运行，路由才回到本文件真正要测的那条判定上。
    p.setdefault("characters", ["甲", "乙"])
    p.setdefault("emotional_arc", "平→紧")
    p.setdefault("end_hook", "风起帆响")
    p.setdefault("key_scenes", ["甲板"])
    p.setdefault("character_motives", ["甲：推进"])
    p.setdefault("omittable", [])
    p.setdefault("time_budget", "对白约3秒，画面约5秒")
    p.update(over)
    return p


def _run(monkeypatch, plan, payloads):
    _svc = _Svc(payloads)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    # 2026-10（核验协议迁移）：本文件的 `payloads` 只按**规划**调用配量。核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` → `executable=False` → 缓存不写，成片误红。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录
    # （`verification_helpers.legal_record_for`，四轴齐全、身份随 `digest` 绑定）。
    from tests import verification_helpers as _vh

    def _novel_text_for_verify():
        # 各文件的原文常量名不同（`_SRC` / `_SRC2` / `_SRC_LONG` / `_WIDE_NOVEL`），
        # 按运行时可用性择一，避免写死某个文件名下的常量名。
        for _n in ("_SRC", "_SRC2", "_SRC_LONG", "_WIDE_NOVEL", "_NOVEL"):
            _v = globals().get(_n)
            if isinstance(_v, str) and _v:
                return _v
        return ""

    _gor_orig = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _novel_text_for_verify()
        k.setdefault("verify",
                     lambda view, digest: _vh.legal_record_for(
                         view, _novel, plan_digest=digest))
        return _gor_orig(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    _r = o.generate_outline_result(_SRC2)
    return _r, _svc


def _kinds(_r):
    return [x.get("kind") for x in (_r["revision"]["history"] or [])]


def _shape(_r):
    return {x["code"]: x.get("path") for x in o.plan_acceptance_problems(_r["plan"], _SRC2)}


# ---------------------------------------------------------------------------
# C1 新增旁白条目 + 同时新增承载（一条节拍只有一个载体 ⇒ 必须同时 split）
# ---------------------------------------------------------------------------
def test_c1_add_a_narration_entry_with_its_carrier(monkeypatch, ledger):
    """事实声明由旁白承载、而旁白表为空 ⇒ 补一条旁白**并**给它承载.

    组合点：新旁白落表（**新增条目**）与"哪条节拍承载它"必须同一批完成；
    B1 已经承载台词1（一条节拍只能有一个载体）⇒ 必须 `split` 出第二条。
    索引口径：`beat_ops.index=1` 对**输入**；`旁白1`/`fact_carriers.beat` 对**输出**。
    """
    _p = _plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/旁白1" % _LINE])
    _before = [x for x in o.plan_acceptance_problems(_p, _SRC2)]
    assert _before, "基线必须真的不合格（否则这条用例什么都没测）"
    _ops = {
        # 复核 2026-10-04 第四轮 P1：新旁白的**发声正文**要真的表达所声明的事实
        # （事实的原文证据就是 `_LINE`）；旧写法给的是另一句 `_LINE2`。
        "narration_lines": [{"text": _LINE, "source": _LINE, "fact_refs": [1]}],
        "beat_ops": [{"op": "split", "index": 1, "parts": [
            {"new_text": "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
             "new_carrier": "台词1"},
            {"new_text": "节拍：旁白交代｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LINE2,
             "new_carrier": "旁白1"}]}],
        "fact_carriers": [{"index": 1, "beat": 2, "mode": "旁白1"}],
    }
    _r, _svc = _run(monkeypatch, _p,
                    [json.dumps(_p, ensure_ascii=False),
                     json.dumps(_ops, ensure_ascii=False)] + [_p] * 2)
    assert _kinds(_r) == ["applied"], _r["revision"]["history"]
    assert _r["executable"] is True, (_r["failure"], _shape(_r))
    _out = _r["plan"]
    assert len(_out["shot_beats"]) == 4, _out["shot_beats"]
    assert _out["narration_lines"][0]["text"] == _LINE
    assert "旁白1" in _out["shot_beats"][1] and "台词1" in _out["shot_beats"][0], _out["shot_beats"]
    # **索引版本**：`beat_ops.index=1` 指的是**输入**的第 1 条（已 split 成两条）；
    # 结果里的 `台词1`/`旁白1` 与 `fact_carriers.beat=2` 指的是**输出**编号
    assert _out["shot_beats"][0].startswith("节拍：甲板上有风"), "操作索引对输入"
    assert "旁白1" in _out["shot_beats"][1], "承载引用对输出（split 后新增的那条）"


# ---------------------------------------------------------------------------
# C2 删除声音条目 + 同时撤销/重绑引用
# ---------------------------------------------------------------------------
def test_c2_delete_a_narration_entry_and_rebind_its_carrier(monkeypatch, ledger):
    """删掉一条旁白 ⇒ 引用它的**事实承载**必须同一批撤销（整表语义 + 引用范围按输出）.

    基线两条问题：F2 声明由 B4 承载「旁白2」而 B4 标的是「画面」；N2 没有任何节拍承载。
    修法：`narration_lines` 给**完整表**（少一条 = 删除 N2）+ 把 F2 的承载改到已有节拍上。
    索引口径：`narration_lines` 是**完整表**（定义本批之后的表）；`fact_carriers.index`
    是**事实**序号，`beat` 对**输出**节拍编号。
    """
    _p = _plan(
        narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []},
                         {"text": _LINE, "source": _LINE, "fact_refs": []}],
        required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE,
                        "他走进屋子｜原文证据：“%s”｜承载：B4/旁白2" % _LINE2],
        shot_beats=[_beat("甲板上有风", "台词1"), _beat("乙", "画面"),
                    _beat("丙", "旁白1"), _beat("他走进屋子四下打量", "画面")])
    _ps = {x["code"] for x in o.plan_acceptance_problems(_p, _SRC2)}
    assert {"carrier_fact_narration_mismatch", "orphan_uncarried"} <= _ps, _ps
    _ops = {
        "narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
        "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"},
                          {"index": 2, "beat": 4, "mode": "画面"}],
    }
    _r, _svc = _run(monkeypatch, _p,
                    [json.dumps(_p, ensure_ascii=False),
                     json.dumps(_ops, ensure_ascii=False)] + [_p] * 2)
    assert _kinds(_r) == ["applied"], _r["revision"]["history"]
    assert _r["executable"] is True, (_r["failure"], _shape(_r))
    assert len(_r["plan"]["narration_lines"]) == 1, _r["plan"]["narration_lines"]
    assert "旁白2" not in " ".join(_r["plan"]["shot_beats"]), _r["plan"]["shot_beats"]


# ---------------------------------------------------------------------------
# C3 拆分节拍 + 同时更新承载登记
# ---------------------------------------------------------------------------
def test_c3_split_a_beat_and_update_the_carrier_registry(monkeypatch, ledger):
    """一条节拍写了两个载体 ⇒ `split` 成两条，**同时**重声明事实承载（索引属输出）."""
    _p = _plan(shot_beats=[_beat("甲板上有风", "台词1"), _beat("乙", "画面；台词1"),
                           _beat("丙", "画面")])
    assert {x["code"] for x in o.plan_acceptance_problems(_p, _SRC2)}, "基线必须不合格"
    _ops = {"beat_ops": [{"op": "split", "index": 2, "parts": [
                {"new_text": _beat("乙之一", "画面"), "new_carrier": "画面"},
                {"new_text": _beat("乙之二", "台词1"), "new_carrier": "台词1"}]}],
            "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}
    _r, _svc = _run(monkeypatch, _p,
                    [json.dumps(_p, ensure_ascii=False),
                     json.dumps(_ops, ensure_ascii=False)] + [_p] * 2)
    assert _kinds(_r) == ["applied"], _r["revision"]["history"]
    assert _r["executable"] is True, (_r["failure"], _shape(_r))
    assert len(_r["plan"]["shot_beats"]) == 4, _r["plan"]["shot_beats"]


# ---------------------------------------------------------------------------
# C4 收窄/换选内容 + 保持核心事件与出处
# ---------------------------------------------------------------------------
def test_c4_narrow_the_selection_and_keep_the_core_fields_intact(monkeypatch, ledger):
    """换掉一句台词 ⇒ 事件/顺序/事实本体与**原文证据**逐字不变（守卫真的在比对）."""
    _p = _plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
               dialogue_lines=[_LINE, _LINE2],
               shot_beats=[_beat("甲", "台词1"), _beat("乙", "台词2"), _beat("丙", "画面")])
    _core = {k: json.loads(json.dumps(_p[k], ensure_ascii=False))
             for k in ("main_events", "climax", "causal_chain", "required_facts")}
    assert o.plan_acceptance_problems(_p, _SRC2) == [], "这份基线是合法的（换选是可选收窄）"
    # 合法对照：**没有问题时也会走验收**（这里直接验证操作本身与其守卫）
    # 协议要求：动了 `dialogue_ops` 就必须**逐项**重声明必保留事实的承载
    _ops = {"dialogue_ops": [{"index": 2, "replace_with": _LINE}],
            "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}
    _new, _why = o.apply_replan_ops(json.loads(json.dumps(_p)), _ops, _SRC2)
    assert _new is not None, _why
    assert o._plan_core_preserved(_p, _new) == "", "换台词不许动核心字段"
    for _k, _v in _core.items():
        assert _new[_k] == _v, _k
    assert _new["dialogue_lines"] == [_LINE, _LINE], _new["dialogue_lines"]
    # 反例：模型顺手改了「原文证据」⇒ 守卫必须点名到字段
    _bad = json.loads(json.dumps(_new))
    _bad["required_facts"] = ["甲板上有风｜原文证据：“另一句原文”｜承载：B1/台词1"]
    assert o._plan_core_preserved(_p, _bad) == "required_facts", "改出处必须被点名"


def test_c5_a_partial_fix_still_reports_what_remains(monkeypatch, ledger, tmp_path):
    """**局部修正成功 ⇒ 剩余问题必须进下一轮反馈**（失败 → 修好一条 → 只剩另一条）.

    复核之十 第 2 项：旧写法"删掉唯一的孤儿旁白后直接全绿"，一轮就结束，**没有**验证
    "局部成功后继续反馈"。

    夹具设计（2026-10 强化判别性）：基线放**两条在当前验收模式下都成立**的登记问题
    （`carrier_narration_fact_unregistered` × 2，`index` 不同）。第一轮只登记其中一条
    旁白（声明式，不动节拍），**另一条必须原样留在下一轮**；第二轮登记另一条后才通过。
    这样"消除一项、保留另一项、且剩余项逐字进下一轮反馈"三步都有独立断言。
    """
    _p = _plan(
        narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []},
                         {"text": _LINE, "source": _LINE, "fact_refs": []}],
        required_facts=["甲板上有风｜原文证据：“%s”｜承载：B1/台词1" % _LINE,
                        "他走进屋子｜原文证据：“%s”｜承载：B2/旁白1" % _LINE2,
                        "四下打量｜原文证据：“%s”｜承载：B3/旁白2" % _LINE],
        shot_beats=[_beat("甲板上有风", "台词1"),
                    _beat("乙", "旁白1", _LINE2),
                    _beat("丙", "旁白2", _LINE)])
    _codes0 = [x["code"] for x in o.plan_acceptance_problems(_p, _SRC2)]
    assert _codes0.count("carrier_narration_fact_unregistered") == 2, (
        "基线必须**同时**有两条登记问题（否则本用例什么都没测）", _codes0)
    # 第一轮：只登记 N1（事实 2）；N2 仍为空 ⇒ 事实 3 仍未登记
    _ops1 = {"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": [2]},
                                 {"text": _LINE, "source": _LINE, "fact_refs": []}]}
    # 第二轮：登记 N2（事实 3）
    _ops2 = {"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": [2]},
                                 {"text": _LINE, "source": _LINE, "fact_refs": [3]}]}
    _r, _svc = _run(monkeypatch, _p,
                    [json.dumps(_p, ensure_ascii=False),
                     json.dumps(_ops1, ensure_ascii=False),
                     json.dumps(_ops2, ensure_ascii=False)] + [_p] * 2)
    _rounds = [json.loads(y) for y in
               (tmp_path / "step01_replan_rounds.jsonl").read_text(encoding="utf-8").splitlines()
               if y.strip() and '"first_version"' not in y]
    assert [x["kind"] for x in _rounds] == ["applied", "applied"], _rounds
    _out1 = [x for x in (_rounds[0]["problems_out"] or [])]
    _after1 = {x["code"] for x in _out1}
    _next_in = {x["code"] for x in (_rounds[1]["problems_in"] or [])}
    # ① 第一轮**确实消除了一条**（原先两条，现在只剩一条）
    assert [x["code"] for x in _out1].count("carrier_narration_fact_unregistered") == 1, (
        "第一轮应恰好修好两条中的一条", _out1)
    # ② 剩下的那条**仍被报出**（不是"修一条就全绿"）
    assert "carrier_narration_fact_unregistered" in _after1, (
        "局部修复后剩余的那条必须被报出", _after1)
    # ③ 下一轮反馈**正好**是剩余问题
    assert _next_in == _after1, ("下一轮反馈必须正好是剩余问题", _next_in, _after1)
    # ④ 第二轮补完后可执行
    assert _r["executable"] is True, (_r["failure"], _shape(_r))
