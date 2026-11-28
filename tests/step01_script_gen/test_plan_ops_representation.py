# -*- coding: utf-8 -*-
"""计划表示的内部记录 + 三种节拍操作（2026-09-27 用户决定，合并方案第 2 步）.

## 为什么需要

章 2 计划连续两轮不收敛。评审给的根因之一是**表示与表达能力**：

- 节拍是「一整串靠 `｜` 拼起来的文本」，载体/原文依据靠**子串扫描**判断——
  多一个载体段只有事后数字符串才发现；
- 操作**只能替换**，改不了节拍**数量**——而承载关系缺陷恰恰需要「把一条节拍拆成
  画面 + 台词两条」或「补一条承载 D1 的节拍」。章 2 的 B2 正是「一条节拍要同时
  承载画面与台词」，用替换永远改不出来。

用户决定的第 2 步：把每拍的载体、事实编号、原文依据解析成**统一的内部记录**，
再渲染回现文本格式；操作支持 `replace` / `insert_after` / `split`；操作索引指向
**修改前**的稳定节拍身份；一批操作**先检查冲突、再统一应用、后重新编号**；
下游生成契约与 `plan_refs` 必须使用**应用后**的编号。

本文件只管**操作层**（记录层在 `test_beat_record_compat.py`）：按「三种操作 →
快照编号 → 冲突检查 → 承载闭合 → 下游编号」逐项取证。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.outline import apply_replan_ops  # noqa: E402
from step01_script_gen.plan_feasibility import (  # noqa: E402
    _SENT_END_CHARS,
    _strip_beat_carrier,
    CUT_ILLEGAL_CHARS,
    carrier_relation_problems,
    check_event_contract,
    check_dialogue_representable,
    is_sentence_end_terminated,
    plan_identity,
)


_CITE_A = "丙把茶壶踢翻了，茶水流了一地。"
_CITE_B = "丁吓得发抖，连手里的茶盏都拿不稳了。"
_SRC = "甲道：“%s”乙道：“%s”" % (_CITE_A, _CITE_B)
_LINE_A = "丙把茶壶踢翻了。"
_LINE_B = "丁吓得发抖。"


def _fc(beat, mode="画面", index=1):
    """本批的承载声明（2026-09-30 起：**动节拍同样要逐项声明**，编号用改之后的）."""
    return [{"index": index, "beat": beat, "mode": mode}]


def _beat(body, cite, carrier):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, cite, carrier)


def _plan():
    return {
        "main_events": ["甲喝止", "乙发抖", "丙失手"],
        "climax": "丙失手",
        "causal_chain": ["甲喝止→乙发抖→丙失手"],
        # 承载登记（2026-09-30 计划契约）：F1 是**单一命题**，由真的演出它的节拍承载
        # ——B2 的正文就是这件事，且它的载体标注是「画面」
        "required_facts": ["乙发抖｜原文证据：“%s”｜承载：B2/画面" % _CITE_B],
        "shot_beats": [
            _beat("甲喝止", _CITE_B, "台词1"),
            _beat("乙吓得发抖，手里的茶盏都拿不稳", _CITE_B, "画面"),
            _beat("丙失手", _CITE_B, "台词2"),
        ],
        "dialogue_lines": [_LINE_A, _LINE_B],
        "time_budget": "对白约50%",
    }


# ---------------------------------------------------------------------------
# ② 三种操作：replace / insert_after / split
# ---------------------------------------------------------------------------

def test_replace_op_still_works_and_inherits_carrier():
    """回归：不写 `op` = `replace`；`new_carrier` 留空沿用原载体."""
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"index": 2, "new_text": _beat("乙发抖", _CITE_B, "")}],
                  "fact_carriers": _fc(2)},
        _SRC)
    assert new is not None, why
    assert new["shot_beats"][1].endswith("｜载体：画面"), "留空必须沿用原载体"
    assert len(new["shot_beats"]) == 3


def test_insert_after_lands_after_the_named_beat():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 1,
                                "new_text": _beat("甲环视", _CITE_A, "画面"),
                                "new_carrier": "画面"}], "fact_carriers": _fc(3)}, _SRC)
    assert new is not None, why
    assert len(new["shot_beats"]) == 4
    assert new["shot_beats"][0].startswith("节拍：甲喝止")
    assert new["shot_beats"][1].startswith("节拍：甲环视"), "插在 B1 之后"
    assert new["shot_beats"][2].startswith("节拍：乙吓得发抖")


def test_insert_after_zero_inserts_at_head():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 0,
                                "new_text": _beat("开场空镜", _CITE_A, "画面"),
                                "new_carrier": "画面"}], "fact_carriers": _fc(3)}, _SRC)
    assert new is not None, why
    assert new["shot_beats"][0].startswith("节拍：开场空镜")
    assert len(new["shot_beats"]) == 4


def test_split_expands_in_place_with_its_own_carriers():
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [{"op": "split", "index": 2, "parts": [
            {"new_text": _beat("乙发抖", _CITE_B, "画面"), "new_carrier": "画面"},
            {"new_text": _beat("乙开口", _CITE_A, "台词2"), "new_carrier": "台词2"},
        ]}], "fact_carriers": _fc(2)},
        _SRC)
    assert new is not None, why
    assert len(new["shot_beats"]) == 4
    assert new["shot_beats"][1].startswith("节拍：乙发抖")
    assert new["shot_beats"][2].startswith("节拍：乙开口"), "原地展开，不是插在别处"


def test_split_rebuilds_carrier_segment_instead_of_appending():
    """模型把旧标注抄进 `new_text` 时，从**表示**上消除重复标注（旧实现靠事后数字符串）."""
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [{"op": "split", "index": 2, "parts": [
            {"new_text": _beat("乙发抖", _CITE_B, "画面"), "new_carrier": "画面"},
            {"new_text": _beat("乙开口", _CITE_A, "台词2") + "｜载体：画面",
             "new_carrier": "台词2"},
        ]}], "fact_carriers": _fc(2)},
        _SRC)
    assert new is not None, why
    assert new["shot_beats"][2].count("载体：") == 1, new["shot_beats"][2]
    assert new["shot_beats"][2].endswith("｜载体：台词2")


# ---------------------------------------------------------------------------
# ③ 快照编号：一批操作里 `index` 一律指**修改前**的身份（判别性）
# ---------------------------------------------------------------------------

def test_batch_indices_refer_to_the_pre_apply_snapshot():
    """`split` B1 + `replace` B3：替换必须落到**原来的** B3（应用后已顺延到第 4 位）.

    若边改边编号（逐条应用），`index=3` 会落到原来的 B2 上——这条用例就是为它写的。
    """
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "split", "index": 1, "parts": [
                {"new_text": _beat("甲喝止", _CITE_B, "画面"), "new_carrier": "画面"},
                {"new_text": _beat("甲抬手", _CITE_A, "台词1"), "new_carrier": "台词1"},
            ]},
            {"op": "replace", "index": 3,
             "new_text": _beat("丙失手（改写）", _CITE_A, "台词2"),
             "new_carrier": "台词2"},
        ], "fact_carriers": _fc(3)},
        _SRC)
    assert new is not None, why
    bodies = [b.split("｜")[0] for b in new["shot_beats"]]
    assert bodies == ["节拍：甲喝止", "节拍：甲抬手", "节拍：乙吓得发抖，手里的茶盏都拿不稳",
                      "节拍：丙失手（改写）"], bodies
    # 原来的 B2 不许被这条 replace 碰到
    assert "改写" not in new["shot_beats"][2]


# ---------------------------------------------------------------------------
# ④ 冲突检查：一批里同一编号至多一条操作（先检查、再应用）
# ---------------------------------------------------------------------------

def test_duplicate_beat_index_in_one_batch_is_rejected():
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "replace", "index": 2,
             "new_text": _beat("乙发抖", _CITE_B, "画面"), "new_carrier": "画面"},
            {"op": "split", "index": 2, "parts": [
                {"new_text": _beat("乙A", _CITE_A, "画面"), "new_carrier": "画面"},
                {"new_text": _beat("乙B", _CITE_B, "画面"), "new_carrier": "画面"},
            ]},
        ]},
        _SRC)
    assert new is None and why.startswith("操作冲突"), why


def test_two_inserts_on_the_same_index_are_rejected():
    """两条插入落在同一位置 → 谁在前不确定，是**真冲突**（与「改+插」不同）."""
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "insert_after", "index": 1,
             "new_text": _beat("甲环视", _CITE_A, "画面"), "new_carrier": "画面"},
            {"op": "insert_after", "index": 1,
             "new_text": _beat("乙起身", _CITE_B, "画面"), "new_carrier": "画面"},
        ]},
        _SRC)
    assert new is None and why.startswith("操作冲突"), why


def test_replace_and_insert_after_the_same_beat_are_compatible():
    """「改这条 + 在它后面另开一条」相容（章 2 的正解形态，见闭合用例）."""
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "replace", "index": 2,
             "new_text": _beat("乙发抖（改为画面）", _CITE_A, "画面"),
             "new_carrier": "画面"},
            {"op": "insert_after", "index": 2,
             "new_text": _beat("乙开口", _CITE_B, "画面"), "new_carrier": "画面"},
        ], "fact_carriers": _fc(2)},
        _SRC)
    assert new is not None, why
    assert [b.split("｜")[0] for b in new["shot_beats"]] == [
        "节拍：甲喝止", "节拍：乙发抖（改为画面）", "节拍：乙开口", "节拍：丙失手"]


def test_duplicate_dialogue_index_in_one_batch_is_rejected():
    new, why = apply_replan_ops(
        _plan(),
        {"dialogue_ops": [
            {"index": 1, "replace_with": _LINE_B},
            {"index": 1, "replace_with": _CITE_A},
        ]},
        _SRC)
    assert new is None and why.startswith("操作冲突"), why


# ---------------------------------------------------------------------------
# ⑤ 操作自身的合法性（显式失败，不静默放过）
# ---------------------------------------------------------------------------

def test_unknown_op_kind_is_rejected():
    # 2026-10-02（之三）复核 P1 后：`op` 的取值由**本地响应校验**按 schema 的 `enum`
    # 先判（诊断带上路径与允许集合），应用器那条"操作种类非法"不再轮到——两处同一裁决。
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "drop", "index": 1,
                                "new_text": _beat("X", _CITE_A, "画面"),
                                "new_carrier": "画面"}]}, _SRC)
    assert new is None and "只能是 replace / insert_after / split" in why, why


def test_split_needs_at_least_two_parts():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "split", "index": 1, "parts": [
            {"new_text": _beat("甲喝止", _CITE_A, "画面"), "new_carrier": "画面"}]}]},
        _SRC)
    assert new is None and "至少要给两段" in why, why


def test_inserted_beat_must_carry_evidence():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 1,
                                "new_text": "节拍：没有出处的新节拍",
                                "new_carrier": "画面"}]}, _SRC)
    assert new is None and "缺「原文依据」" in why, why


def test_carrier_value_must_be_visual_or_dialogue():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 1,
                                "new_text": _beat("甲环视", _CITE_A, "画面"),
                                "new_carrier": "环境画面"}]}, _SRC)
    assert new is None and "载体只能是" in why, why


def test_carrier_index_out_of_range_is_rejected():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 1,
                                "new_text": _beat("甲环视", _CITE_A, "画面"),
                                "new_carrier": "台词9"}]}, _SRC)
    assert new is None and "越界" in why, why


def test_insert_index_out_of_range_is_rejected():
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 4,
                                "new_text": _beat("甲环视", _CITE_A, "画面"),
                                "new_carrier": "画面"}]}, _SRC)
    assert new is None and "越界" in why, why


# ---------------------------------------------------------------------------
# ⑥ 承载关系闭合：拆分/插入**能**改出原本改不出的闭合（章 2 的形态）
# ---------------------------------------------------------------------------

def test_split_that_orphans_a_carried_line_is_rejected():
    """把承载 D2 的节拍拆成两条「画面」→ D2 无人承载 → 拒收（闭合是硬门）."""
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [{"op": "split", "index": 3, "parts": [
            {"new_text": _beat("丙失手", _CITE_B, "画面"), "new_carrier": "画面"},
            {"new_text": _beat("丙回望", _CITE_A, "画面"), "new_carrier": "画面"},
        ]}]},
        _SRC)
    assert new is None and "无任何节拍承载" in why, why


def test_insert_after_can_close_a_carrier_gap():
    """章 2 的正解形态：一条节拍只能有一个载体 → **另立一条**承载被丢下的台词.

    旧实现只有 `replace`，把 B2 改成「画面」就必然让 D1 成孤儿（实测两轮都卡在这），
    结构上改不出闭合。这条用例证明新操作能闭合。
    """
    plan = _plan()
    # B1 被要求改成纯画面（丢掉 D1）＋ 同批另立一条承载 D1 → 应当整批通过
    new, why = apply_replan_ops(
        plan,
        {"beat_ops": [
            {"op": "replace", "index": 1,
             "new_text": _beat("甲喝止（改为画面）", _CITE_B, "画面"),
             "new_carrier": "画面"},
            {"op": "insert_after", "index": 1,
             "new_text": _beat("甲开口下令", _CITE_A, "台词1"),
             "new_carrier": "台词1"},
        ], "fact_carriers": _fc(3)},
        _SRC)
    assert new is not None, why
    dup, orphan, unlabeled = carrier_relation_problems(new["shot_beats"], 2)
    assert (dup, orphan, unlabeled) == ([], [], []), (dup, orphan, unlabeled)


# ---------------------------------------------------------------------------
# ⑦ 下游编号 = **应用后**的编号（生成契约 / plan_refs）
# ---------------------------------------------------------------------------

def test_contract_and_plan_refs_use_post_apply_numbering():
    """应用后编号即下游编号：契约块的 `B2` 就是**新插入的**那条节拍."""
    from step01_script_gen.prompts import render_execution_contract
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "insert_after", "index": 1,
                                "new_text": _beat("甲环视", _CITE_A, "画面"),
                                "new_carrier": "画面"}], "fact_carriers": _fc(3)}, _SRC)
    assert new is not None, why
    assert new["shot_beats"][1].startswith("节拍：甲环视"), "插入后编号已顺延"
    block = render_execution_contract(new)
    assert "B1..B4" in block, block[block.index("四、"):][:200]
    assert "B2. 节拍：甲环视" in block, block[block.index("四、"):][:400]
    assert "B3. 节拍：乙吓得发抖" in block
    # 同一条计划上按**应用后**编号声明：B2 = 新插入的节拍
    # （声明了台词的那一镜必须真的说那句——2026-09-27 起事件门按镜核验 Dk 锚点）
    # 声明要与**承载关系**自洽（2026-09-30 起核对 B→D→镜头）：B1 承载 D1、
    # B4 承载 D2，说 D2 的那一镜就得声明 B4（而不是标着画面的 B3）
    shots = [{"index": i, "narration_segment": narr,
              "plan_refs": {"beats": bs, "lines": ls}}
             for i, (bs, ls, narr) in enumerate(
                 [([1, 2], [1], _LINE_A), ([], [], ""),
                  ([3, 4], [2], _LINE_B), ([], [], "")])]
    stamped = {"shots": shots, "plan_id": plan_identity(new)}
    ok, violations, declared = check_event_contract(stamped, new)
    assert declared and ok, violations
    # 反例：仍按**应用前**的编号声明（B2 指原来的「乙发抖」→ B4 无人承载）
    bad = [{"index": i, "narration_segment": narr,
            "plan_refs": {"beats": [b], "lines": ls}}
           for i, (b, ls, narr) in enumerate(
               [(1, [1], _LINE_A), (2, [], ""), (3, [2], _LINE_B), (3, [], "")])]
    ok2, violations2, _ = check_event_contract(
        {"shots": bad, "plan_id": plan_identity(new)}, new)
    assert not ok2 and any(v["code"] == "beats_not_carried" for v in violations2), violations2
    # 身份边界：把**重选前**那份计划的身份盖上去 → 即使编号全部合法也拒收
    # （`insert_after` 重排了 B 编号，旧声明指向的不是同一条节拍）
    stale_id = plan_identity(_plan())
    ok3, violations3, declared3 = check_event_contract(
        {"shots": shots, "plan_id": stale_id}, new)
    assert not ok3 and declared3
    assert [v["code"] for v in violations3] == ["plan_refs_stale_plan"], violations3


# ---------------------------------------------------------------------------
# ⑧ 守卫：提示里的切点规则 ↔ 判据函数（2026-09-27 复核口径）
# ---------------------------------------------------------------------------

def test_cut_point_rule_in_prompt_matches_the_criterion():
    """提示的非法切点字符表 ↔ 判据函数**逐字符**对齐（不靠人肉同步）.

    复核口径：提示文字是手写规则，与应用器「目前一致」不算长期保证。这条守卫把两件事
    钉死：① 提示渲染进了 `CUT_ILLEGAL_CHARS` 的**每一个**字符；② 判据函数对这批字符
    确实判非法、对句末标点确实判合法。
    """
    from step01_script_gen.outline import _PLAN_OPS_INSTRUCTION
    assert CUT_ILLEGAL_CHARS, "非法字符表为空 = 提示那句话已失效"
    assert not (set(CUT_ILLEGAL_CHARS) & set(_SENT_END_CHARS)), "非法与合法切点不得重叠"
    body = "这是一句足够长的话"
    for c in CUT_ILLEGAL_CHARS:
        assert c in _PLAN_OPS_INSTRUCTION, "提示没渲染非法字符 %r" % c
        assert not is_sentence_end_terminated(body + c), c
    for c in _SENT_END_CHARS:
        assert is_sentence_end_terminated(body + c), c


def test_comma_truncated_half_sentence_is_representable_but_illegal():
    """判别性：逗号截断的半句**能单镜表示**——只有句末判据拦得住（这正是它存在的理由）."""
    half = "几位，罗大人的手段你们是晓得的，"
    assert check_dialogue_representable(half)["representable"], "前提：短半句装得进单镜"
    assert not is_sentence_end_terminated(half)


def _plan_without_carriers():
    """旧缓存形态：节拍**整体**没有载体标注（章 1 缓存的真实形态）."""
    _p = _plan()
    _p["shot_beats"] = [_strip_beat_carrier(b) for b in _p["shot_beats"]]
    return _p


def test_three_ops_on_the_same_index_cannot_overwrite_the_first():
    """**P1-3 反例（复核给定）**：`replace → insert_after → replace` 必须整轮拒收.

    原判据只记「上一条操作种类」：第三条看到「上一条是插入」就被放行，随后**覆盖**
    掉第一条 replace——改动被静默吞掉。改该节拍与在其后插入是**两个槽位**，各自
    至多一次。
    """
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "replace", "index": 2,
             "new_text": _beat("乙发抖（第一次改写）", _CITE_A, "画面"),
             "new_carrier": "画面"},
            {"op": "insert_after", "index": 2,
             "new_text": _beat("乙开口", _CITE_B, "画面"), "new_carrier": "画面"},
            {"op": "replace", "index": 2,
             "new_text": _beat("乙发抖（第二次改写）", _CITE_B, "画面"),
             "new_carrier": "画面"},
        ]},
        _SRC)
    assert new is None and why.startswith("操作冲突"), why
    # 同型排列（改 → 插 → 拆）：同样不得放行
    new2, why2 = apply_replan_ops(
        _plan(),
        {"beat_ops": [
            {"op": "split", "index": 3, "parts": [
                {"new_text": _beat("丙失手", _CITE_B, "画面"), "new_carrier": "画面"},
                {"new_text": _beat("丙回望", _CITE_A, "台词2"), "new_carrier": "台词2"}]},
            {"op": "insert_after", "index": 3,
             "new_text": _beat("收尾", _CITE_A, "画面"), "new_carrier": "画面"},
            {"op": "replace", "index": 3,
             "new_text": _beat("丙失手（改）", _CITE_A, "台词2"), "new_carrier": "台词2"},
        ]},
        _SRC)
    assert new2 is None and why2.startswith("操作冲突"), why2


def test_new_beats_must_declare_a_carrier_even_in_an_unannotated_plan():
    """**P1-4 反例（复核给定）**：旧计划整体无标注时，**新建**的节拍也必须显式给载体.

    兼容分支（「计划带了标注才判未标注」）对整份无标注的旧计划不生效——留空就会静默
    多出一条没有承载记录的节拍，而 split 的契约是「每段自带载体」。
    """
    plan = _plan_without_carriers()
    assert plan["shot_beats"] and all("载体：" not in b for b in plan["shot_beats"])
    new, why = apply_replan_ops(
        plan, {"beat_ops": [{"op": "insert_after", "index": 1,
                             "new_text": _beat("甲环视", _CITE_A, ""),
                             "new_carrier": ""}]}, _SRC)
    assert new is None and "必须显式给" in why, why
    new2, why2 = apply_replan_ops(
        plan, {"beat_ops": [{"op": "split", "index": 1, "parts": [
            {"new_text": _beat("甲喝止", _CITE_B, ""), "new_carrier": ""},
            {"new_text": _beat("甲抬手", _CITE_A, "画面"), "new_carrier": "画面"}]}]},
        _SRC)
    assert new2 is None and "必须显式给" in why2, why2


def test_inserting_a_labeled_beat_into_an_unannotated_plan_is_rejected():
    """**交互，写准**：给无标注旧计划插入**带**载体的新节拍 → 同一份计划两种口径 → 拒收.

    这是既有判据（「计划带了标注就必须每条节拍恰有一个载体」）与新操作的交互结果：
    要么整份标注齐、要么整份不标注，不许混用。**不是**新缺陷——钉住它，免得下次
    被当成 bug 重新「修」一遍。
    """
    new, why = apply_replan_ops(
        _plan_without_carriers(),
        {"beat_ops": [{"op": "insert_after", "index": 1,
                       "new_text": _beat("甲环视", _CITE_A, "画面"),
                       "new_carrier": "画面"}]},
        _SRC)
    assert new is None and "缺载体标注" in why, why


# ---------------------------------------------------------------------------
# ⑨ 操作 index 的编号形态（2026-09-27 复核：与 plan_refs 同型的漏口）
# ---------------------------------------------------------------------------

def test_op_index_accepts_digit_string_and_matching_axis_tag():
    """`2` / `"2"` / `"B2"` 同义（节拍轴）；台词轴同理 —— 与 `plan_refs` 同口径.

    现场：提示写「节拍编号」而上下文一律叫 `B2`，schema 要 integer 但 agent_plan 通道
    降级成 json_object，应用器 `int(index)` 对 `"B2"` 抛 ValueError → 整轮拒收。
    """
    for form in (2, "2", "B2", "b2", " B 2 "):
        new, why = apply_replan_ops(
            _plan(), {"beat_ops": [{"op": "replace", "index": form,
                                    "new_text": _beat("乙发抖（改）", _CITE_A, "画面"),
                                    "new_carrier": "画面"}],
                  "fact_carriers": _fc(2)}, _SRC)
        assert new is not None, (form, why)
        assert new["shot_beats"][1].startswith("节拍：乙发抖（改）"), form
    for form in ("1", "D1", 1):
        new, why = apply_replan_ops(
            _plan(), {"dialogue_ops": [{"index": form, "replace_with": _LINE_B}],
                      # 动了台词就必须逐项声明事实承载，且承载节拍要与事实同源
                      # （B2 的「原文依据」正是该事实的证据）；B2 载体标注为画面
                      "fact_carriers": [{"index": 1, "beat": 2, "mode": "画面"}]},
            _SRC)
        assert new is not None, (form, why)
        assert new["dialogue_lines"][0] == _LINE_B, form


def test_wrong_axis_index_is_rejected():
    """**错轴标签必须拒收**：`"B1"` 出现在对话操作里 = 两套编号搞混.

    静默按数字照收会把改动落到**另一条**对象上（B1→1），比拒收危险得多。
    """
    new, why = apply_replan_ops(
        _plan(), {"dialogue_ops": [{"index": "B1", "replace_with": _LINE_B}]}, _SRC)
    assert new is None and "另一套编号" in why, why
    new2, why2 = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "replace", "index": "D2",
                                "new_text": _beat("乙发抖（改）", _CITE_A, "画面"),
                                "new_carrier": "画面"}]}, _SRC)
    assert new2 is None and "另一套编号" in why2, why2


def test_op_index_junk_and_out_of_range_are_rejected_separately():
    """非编号 → 「不是编号」；真越界 → 「越界」（两种原因分开报，便于归因）."""
    new, why = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "replace", "index": "第二条",
                                "new_text": _beat("乙发抖（改）", _CITE_A, "画面"),
                                "new_carrier": "画面"}]}, _SRC)
    assert new is None and "不是编号" in why, why
    new2, why2 = apply_replan_ops(
        _plan(), {"beat_ops": [{"op": "replace", "index": "B9",
                                "new_text": _beat("乙发抖（改）", _CITE_A, "画面"),
                                "new_carrier": "画面"}]}, _SRC)
    assert new2 is None and "越界" in why2, why2


def test_op_prompt_gives_bare_number_examples():
    """守卫：提示与 schema 都不许再暗示带前缀的编号写法（消歧在源头）."""
    from step01_script_gen.outline import _PLAN_OPS_INSTRUCTION
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    assert '"index": 1' in _PLAN_OPS_INSTRUCTION, "对话操作的 index 必须给纯数字样例"
    assert '"index": 2' in _PLAN_OPS_INSTRUCTION, "节拍操作的 index 必须给纯数字样例"
    assert "不要写 `D1`" in _PLAN_OPS_INSTRUCTION
    assert "不要写 `B2`" in _PLAN_OPS_INSTRUCTION
    _items = REPLAN_OPS_SCHEMA["schema"]["properties"]["beat_ops"]["items"]["properties"]
    assert "只填数字" in _items["index"]["description"]
    _d = REPLAN_OPS_SCHEMA["schema"]["properties"]["dialogue_ops"]["items"]["properties"]
    assert "只填数字" in _d["index"]["description"]


# ---------------------------------------------------------------------------
# v6.152（第二章可行化）：原文依据的**引号风格**不得造成误拒
# ---------------------------------------------------------------------------
def _evidence_reason(wrap, cite=_CITE_A):
    from step01_script_gen.outline import _plan_evidence_intact
    plan = {"shot_beats": ["节拍：甲喝止｜本镜信息目的：推进｜原文依据：%s｜载体：画面"
                           % wrap(cite)],
            "required_facts": []}
    return _plan_evidence_intact(plan, _SRC)


def test_evidence_quote_style_variants_are_all_accepted():
    """‘ ’、「 」、半角 " " 与**未闭合**的开引号都要认（内容逐字有据即可）.

    现场（2026-09-29 章 2）：引文 12 字在原文 idx 2551 **逐字存在**，模型却写成
    `‘吃的灯草灰，放的轻巧屁！`（单引号、且未闭合）；旧正则只认 `“ ”`/`" "`，
    一个都没抓到 → 判「逐字核不过」→ **整批重选操作丢弃**（其中正是能把
    101.59s→24.07s 的 4 条台词替换）→ 计划永远不可行、第二章永远进不了候选。
    """
    OL, CL, OB, CB = chr(0x2018), chr(0x2019), chr(0x300C), chr(0x300D)
    variants = [
        lambda c: OL + c + CL,     # ‘…’
        lambda c: OL + c,          # ‘…（未闭合，现场形态）
        lambda c: OB + c + CB,     # 「…」
        lambda c: '"' + c + '"',   # "…"
    ]
    for v in variants:
        assert _evidence_reason(v) == "", (v, _evidence_reason(v))


def test_fabricated_evidence_is_still_rejected():
    """判别性：引文**不在原文**时，任何引号风格都必须被拒（证据标准没放宽）."""
    OL, CL = chr(0x2018), chr(0x2019)
    for v in (lambda c: OL + c + CL, lambda c: OL + c):
        reason = _evidence_reason(v, cite="这几句话在原文里根本不存在")
        assert reason, "伪造引文必须被拒（否则等于放宽了 ≥8 字逐字标准）"


# ---------------------------------------------------------------------------
# v6.153（第二章内容承载）：替换台词后，事实与钩子的承载必须**逐项声明**
# ---------------------------------------------------------------------------
# 现场（2026-09-29 章 2，10 跑里 5 跑以「审计报内容缺失」收尾）：
# 重选把 D2 从「拿主意？拿什么主意？四年前…」换短成「拿主意？拿什么主意？」，
# 但 `required_facts` 仍引旧句——**出处不因换台词失效**（不据此拒收、也不改引文）；
# 真正的缺口是 F「四年前潜入青州」**没有成片承载**，且 `end_hook` 要求
# 「喃喃自语」绣春刀那句而所选台词不含它。生成侧只按 `dialogue_lines` 写、
# 审计侧按事实/钩子判 ⇒ 两边各自自洽，修复轮永远改不动。
_LONG_LINE = "拿主意？拿什么主意？四年前，你我四人奉命离开应天府，潜入这青州城。"
# ⚠️ 路线 3（2026-10-04）：换短后的那句**仍须表达事实的内容**——只砍掉与这条事实
# 无关的前缀。旧写法砍成「拿主意？拿什么主意？」，事实（潜入青州）就没有承载了；
# 而旧判据拿整条事实（含原文证据）去比，靠证据里的片段命中蒙过——本轮实测的漏口之一。
_SHORT_LINE = "四年前，你我四人奉命离开应天府，潜入这青州城。"
_HOOK_LINE = "绣春刀啊绣春刀，要到几时你的威风才能重现人间？"
_SRC2 = "甲道：“%s”乙道：“%s”" % (_LONG_LINE, _HOOK_LINE)
_FACT_OLD = "四年前奉命潜入青州｜原文证据：“%s”" % _LONG_LINE


def _plan_hook_unselected():
    """钩子要求说出口的那句**不在** dialogue_lines 里（现场形态）."""
    return {
        "main_events": ["甲发问", "乙独白"],
        "climax": "乙独白",
        "required_facts": [_FACT_OLD],
        "causal_chain": ["甲发问→乙独白"],
        "shot_beats": [
            "节拍：甲发问｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LONG_LINE,
            "节拍：乙独白｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _HOOK_LINE,
        ],
        "dialogue_lines": [_LONG_LINE],
        # 钩子那句**必须说出口**：2026-09-30 复核第 4 轮起是显式契约字段
        "hook_spoken": [_HOOK_LINE],
        "end_hook": "乙拿起绣春刀，喃喃自语“%s”暗示新行动" % _HOOK_LINE,
        "time_budget": "对白约50%",
    }


def _plan_hook_selected():
    """同一份计划，但钩子那句**已进入** dialogue_lines 并有节拍承载."""
    p = _plan_hook_unselected()
    p["dialogue_lines"] = [_SHORT_LINE, _HOOK_LINE]
    p["shot_beats"] = [
        "节拍：甲发问｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _SHORT_LINE,
        "节拍：乙独白｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词2" % _HOOK_LINE,
    ]
    return p


_OPS_SHORTEN = [{"index": 1, "replace_with": _SHORT_LINE}]
#: 与事实**同源**的承载：B1 承载的那句台词就是事实出处（旧句），且 B1 标注为台词1
_FACTS_OK = [{"index": 1, "beat": 1, "mode": "台词1"}]
#: 钩子那句（D2）由 B2 承载——2026-09-30 起钩子的承载也必须**显式声明**
_HOOK_OK = {"beat": 2, "mode": "台词2"}


def test_replan_rejected_when_facts_lose_their_carrier():
    """旧 D2 被换短 + F 未声明承载 ⇒ **整批拒收**（不接受带警告的计划）."""
    new, why = apply_replan_ops(_plan_hook_unselected(),
                                {"dialogue_ops": _OPS_SHORTEN}, _SRC2)
    assert new is None
    assert "必须逐项声明必保留事实的承载" in why, why


def test_replan_rejected_when_hook_line_is_not_in_dialogue_lines():
    """钩子要求说出口、那句却没进 dialogue_lines ⇒ 拒收（现场形态）."""
    new, why = apply_replan_ops(
        _plan_hook_unselected(),
        {"dialogue_ops": _OPS_SHORTEN, "fact_carriers": _FACTS_OK}, _SRC2)
    assert new is None
    assert "钩子要求说出口" in why, why


def test_replan_rejected_when_declared_mode_mismatches_beat_carrier():
    """声明「由 B2 承载台词1」但 B2 的载体标注是画面 ⇒ 拒收（声明必须与计划一致）."""
    new, why = apply_replan_ops(
        _plan_hook_selected(),
        {"dialogue_ops": _OPS_SHORTEN,
         "fact_carriers": [{"index": 1, "beat": 2, "mode": "台词1"}]}, _SRC2)
    assert new is None
    assert "载体标注不是它" in why, why


def test_replan_passes_when_evidence_quotes_old_source_but_carrier_is_declared():
    """判别性对照：引文仍引**旧原文**（出处不失效）＋事实有有效承载 ⇒ **通过**."""
    new, why = apply_replan_ops(
        _plan_hook_selected(),
        {"dialogue_ops": _OPS_SHORTEN, "fact_carriers": _FACTS_OK,
         "hook_carrier": _HOOK_OK}, _SRC2)
    assert new is not None, why
    assert new["dialogue_lines"][0] == _SHORT_LINE
    # 本体与原文证据一个字不许改；**只追加**承载段（承载要落进计划，随生成契约下发）
    assert new["required_facts"][0].startswith(_FACT_OLD), new["required_facts"]
    assert "承载：B1/台词1" in new["required_facts"][0], new["required_facts"]


def test_beat_ops_also_maintains_carriers():
    """**作用范围（2026-09-30 复核第 3 组）**：承载维护**不再**按「本批改没改台词」决定.

    计划带承载契约时，`beat_ops` 的插入/拆分同样会改变节拍编号——旧实现只看
    `dialogue_ops`，于是 `承载：Bn/…` 会指到顺延后的别条节拍上，而新旧编号都「在范围内」，
    错位静默。现在：动节拍也必须逐项声明承载，缺声明整轮拒收。"""
    _beat_op = {"index": 2, "new_text": "节拍：乙独白（改）｜本镜信息目的：推进"
                                        "｜原文依据：“%s”" % _HOOK_LINE,
                "new_carrier": "画面"}
    # 缺声明 ⇒ 整轮拒收（承载会指到顺延后的别条节拍上，且新旧编号都合法）
    new, why = apply_replan_ops(
        _plan_hook_unselected(), {"beat_ops": [dict(_beat_op)]}, _SRC2)
    assert new is None and "必须逐项声明" in why, why
    # 逐项声明（编号用改之后的）⇒ 通过。正例用「钩子那句已入选」的那份计划：
    # 插入一条节拍后 F1 仍指 B1、钩子那句仍在 D2 且由 B3 承载
    new2, why2 = apply_replan_ops(
        _plan_hook_selected(),
        {"beat_ops": [{"op": "insert_after", "index": 1,
                       "new_text": "节拍：甲环视｜本镜信息目的：推进｜原文依据：“%s”"
                                   % _LONG_LINE,
                       "new_carrier": "画面"}],
         "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
         "hook_carrier": {"beat": 3, "mode": "台词2"}}, _SRC2)
    assert new2 is not None, why2


# ---------------------------------------------------------------------------
# ⑦ 拒收反馈**一次给全**（台账 §19.1：章 1 三轮反馈量 1 / 1 / 2 对响应里 1 / ≥3 / 6）
# ---------------------------------------------------------------------------

def test_rejection_reports_every_axis_not_just_the_first():
    """多轴同时不合格 ⇒ 反馈把**每一轴**都列出来（旧写法第一轴 `return`，后面的判据一次不跑）.

    反例形态取章 1 第 2 轮的实测：一份响应既让 D2 丢了承载（承载关系轴），
    又把 F1 声明到一条讲别的事的节拍上（声明轴）。旧写法只回承载关系那 1 条，
    声明门**从未被调用**（离线探针记录到 `plan_contract_problems` 调用次数为 0）
    ——模型改完看到的这一条，下一轮又栽在从没被告知的那条上。
    """
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [{"index": 3, "new_text": _beat("丙失手", _CITE_B, "画面"),
                       "new_carrier": "画面"}],
         "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
        _SRC)
    assert new is None
    assert "无任何节拍承载" in why, why          # 轴 1：承载关系闭合
    assert "没有交集" in why, why                # 轴 2：声明门（旧写法根本不跑）
    assert "**2 条**" in why, why                # 总数写清楚，不静默省略
    assert why.count("\n") == 2, why             # 表头 + 逐条两行 = 一次给全


def test_legal_control_of_the_same_shape_still_applies():
    """合法对照：同样的操作形状，只要把承载声明给对，照常应用（新判据没有一律拒收）."""
    new, why = apply_replan_ops(
        _plan(),
        {"beat_ops": [{"op": "split", "index": 3, "parts": [
            {"new_text": _beat("丙失手", _CITE_B, "画面"), "new_carrier": "画面"},
            {"new_text": _beat("丙回望", _CITE_A, "台词2"), "new_carrier": "台词2"}]}],
         "fact_carriers": [{"index": 1, "beat": 2, "mode": "画面"}]},
        _SRC)
    assert new is not None, why
    assert len(new["shot_beats"]) == 4

def test_rejection_detail_reaches_the_next_request_in_full():
    """拒收原因**整条进请求**——不截断，也不靠抬高上限（复核 2026-10-04 P1-1）.

    旧写法 `[:300]`（章 1 第 3 轮 6 条问题只剩前 2 条）；中间一版是"上限 1200 + 显式
    标注"，那仍然在限制模型能看到的反馈。现在的分工：控制器**存完整**、展示走
    `plan_revision.rejection_display()`（有界且标注）、**回灌模型读完整**。
    """
    from step01_script_gen.outline import _rejection_feedback_block

    _detail = "；".join("F%d 声明由 B%d 的**画面**承载，但该节拍标的是「载体：台词1」"
                        "——一条节拍只能有**一个**载体" % (i, i) for i in range(1, 9))
    assert len(_detail) > 400, "要同时越过旧的 [:300] 与控制器那层 [:400] 才有判别性"
    _block = _rejection_feedback_block([{"round": 3, "detail": _detail}])
    assert _detail in _block, "拒收原因必须整条进请求"
    assert "未显示" not in _block
    # 极端长文本也**照原样**进请求（上限不在这条路上；有界的是展示形态）
    _huge = "甲" * 20000
    assert _huge in _rejection_feedback_block([{"round": 1, "detail": _huge}])
