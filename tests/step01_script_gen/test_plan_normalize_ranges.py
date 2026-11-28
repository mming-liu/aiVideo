# -*- coding: utf-8 -*-
"""保守规范化（载体区间展开 + 全部节拍引用重映射）的验收.

边界（工作包 §2）：**只处理明确、无歧义的表达转换**——`载体：旁白1-5` 的含义唯一
（这一条节拍承载旁白1..旁白5），展开后内容段逐字保留、节拍引用按展开结果重映射；
**需要内容判断的一律不动**：声明了某条节拍承载某个声音条目、而该节拍其实不承载 ⇒ 交规划器；
没有区间写法就一条都不补（"展开"不许凭空补出承载，例如 ch2）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.plan_feasibility import (  # noqa: E402
    BeatRecord, iter_decl_refs, normalize_plan_ranges, parse_carrier_decl,
    plan_contract_problems)

_FIX = Path(__file__).resolve().parent / "fixtures" / "plan_ch1_real_range_carriers.json"


def _beat(body: str, carrier: str) -> str:
    return "节拍：%s｜本镜信息目的：目的｜原文依据：“引文”｜载体：%s" % (body, carrier)


def _plan(beats, facts=None, hook=None) -> dict:
    return {"shot_beats": beats, "required_facts": facts or [],
            "hook_carrier": hook or "", "dialogue_lines": ["甲", "乙"],
            "narration_lines": [{"text": "n%d" % i, "source": "n%d" % i,
                                 "fact_refs": [i]} for i in range(1, 6)]}


def _carriers(plan) -> list:
    return [BeatRecord.parse(b).get("载体：").strip() for b in plan["shot_beats"]]


# ---------------------------------------------------------------------------
# ① 展开与重映射
# ---------------------------------------------------------------------------
def test_a_range_carrier_is_expanded_into_one_beat_per_entry():
    """`载体：旁白1-3` ⇒ 三条节拍，各自一个载体；**内容段逐字保留**."""
    _p = _plan([_beat("开场", "画面"), _beat("图外旁白介绍", "旁白1-3")])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [{"beat": 2, "range": "旁白1-3", "parts": [2, 3, 4]}], _ch
    assert len(_new["shot_beats"]) == 4, _new["shot_beats"]
    assert _carriers(_new) == ["画面", "旁白1", "旁白2", "旁白3"], _carriers(_new)
    # 内容段逐字复制（只有载体段不同）
    for _b in _new["shot_beats"][1:]:
        # `get()` 返回**段内容**（不含标签前缀）
        assert BeatRecord.parse(_b).get("节拍：") == "图外旁白介绍", _b
        assert BeatRecord.parse(_b).get("原文依据：") == "“引文”", _b


def test_the_beat_reference_is_remapped_to_the_beat_that_carries_it():
    """事实的 `承载：B2/旁白3` 随展开指向**真正承载旁白3** 的那一条（B4）."""
    _p = _plan([_beat("开场", "画面"), _beat("旁白块", "旁白1-3")],
               facts=["甲｜原文证据：“x”｜承载：B2/旁白3"])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch and _new["required_facts"][0].endswith("承载：B4/旁白3"), _new["required_facts"]


def test_later_beats_and_refs_shift_together():
    """区间之后的节拍编号与引用**同步平移**（不漏、不重）."""
    _p = _plan([_beat("A", "画面"), _beat("块", "台词1-2"), _beat("C", "画面")],
               facts=["f｜原文证据：“x”｜承载：B3/画面"], hook="B2/台词1")
    _new, _ch = normalize_plan_ranges(_p)
    assert len(_new["shot_beats"]) == 4, _new["shot_beats"]
    # 原 B3（台词块之后的画面节拍）平移到 B4
    assert _new["required_facts"][0].endswith("承载：B4/画面"), _new["required_facts"]
    # hook 指向承载台词1 的那条——就是展开块的第一条（仍是 B2）
    assert _new["hook_carrier"] == "B2/台词1", _new["hook_carrier"]


def test_a_hook_pointing_at_a_beat_that_does_not_carry_it_is_left_alone():
    """语义错配同样不"修好"：hook 声明 B3/台词1 而 B3 其实是画面节拍 ⇒ 只按位置平移."""
    _p = _plan([_beat("A", "画面"), _beat("块", "台词1-2"), _beat("C", "画面")],
               hook="B3/台词1")
    _new, _ = normalize_plan_ranges(_p)
    assert _new["hook_carrier"] == "B4/台词1", _new["hook_carrier"]


def test_a_mismatched_declaration_is_left_for_the_planner():
    """**语义错配不动**：`B1/旁白1` 而 B1 标的是 `画面` ⇒ 只按位置重映射，不"修好"它."""
    _p = _plan([_beat("开场", "画面"), _beat("旁白块", "旁白1-2")],
               facts=["f｜原文证据：“x”｜承载：B1/旁白1"])
    _new, _ = normalize_plan_ranges(_p)
    assert _new["required_facts"][0].endswith("承载：B1/旁白1"), _new["required_facts"]
    # 契约门仍然报它——规范化没有把它"改好"
    assert any("旁白1" in x for x in plan_contract_problems(_new)), plan_contract_problems(_new)


def test_a_descending_range_is_left_alone():
    """倒区间含义不唯一 ⇒ 一个字节都不改（交规划器）."""
    _p = _plan([_beat("块", "旁白5-1")])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [] and _new is _p


# ---------------------------------------------------------------------------
# ② 不许凭空补造
# ---------------------------------------------------------------------------
def test_a_plan_without_ranges_is_untouched():
    """ch2 那类**没有区间表达**的计划：原样返回，绝不借"展开"补出承载."""
    _p = _plan([_beat("开场", "画面"), _beat("快切", "画面")],
               facts=["f｜原文证据：“x”｜承载：B2/画面"])
    _before = json.dumps(_p, ensure_ascii=False, sort_keys=True)
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [] and _new is _p
    assert json.dumps(_new, ensure_ascii=False, sort_keys=True) == _before


def test_it_is_idempotent():
    """重复应用不再改写（幂等）."""
    _p = _plan([_beat("开场", "画面"), _beat("块", "旁白1-2")],
               facts=["f｜原文证据：“x”｜承载：B2/旁白2"])
    _a, _c1 = normalize_plan_ranges(_p)
    _b, _c2 = normalize_plan_ranges(_a)
    assert _c1 and _c2 == []
    assert json.dumps(_a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(_b, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# ③ 真实冻结样本
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _FIX.exists(), reason="冻结样本不在")
def test_the_real_ch1_plan_loses_only_the_notation_error():
    """真实冻结样本（09-30 ch1）：区间展开后**只消掉"标注非法"那一类**，其余原样交规划器."""
    _plan_real = json.loads(_FIX.read_text(encoding="utf-8"))
    _before = plan_contract_problems(_plan_real)
    _new, _ch = normalize_plan_ranges(_plan_real)
    _after = plan_contract_problems(_new)
    assert len(_ch) == 2, _ch                      # 旁白1-5、台词1-2
    assert [c["range"] for c in _ch] == ["旁白1-5", "台词1-2"], _ch
    assert len(_plan_real["shot_beats"]) == 6 and len(_new["shot_beats"]) == 11
    # 2026-10-18 契约变更：比原预期少 1 条——"声明「画面」而节拍标着台词/旁白"已**允许**
    # （声画可共镜）；少掉的另一条是区间展开消掉的"标注非法"。
    assert len(_before) == 9 and len(_after) == 7, (_before, _after)
    assert any("标注**非法**" in x for x in _before), _before
    assert not any("标注**非法**" in x for x in _after), _after
    # 语义错配没有被"修好"：条数只降 1，且剩下的仍是"载体不是它/无交集"
    assert sum(1 for x in _after if "与这条事实**没有交集**" in x) == 1, _after
    # 7 → 6 条"载体不是它"；F9 从"标注非法"重归到"标的是台词、声明要画面"（仍是语义错配）
    assert sum(1 for x in _after if "该节拍的载体标注不是它" in x) == 6, _after
    # 复核之十二 P2：生产者记录**中性**（不给操作建议）。2026-10-18 起不再说
    # "画面与台词/旁白必须分属不同节拍"——该口径已由"允许同镜声画共存"取代。
    assert not any("分属**不同节拍**" in x for x in _after), _after
    assert not any("拆成两条" in x for x in _after), _after
    # 幂等
    _again, _ch2 = normalize_plan_ranges(_new)
    assert _ch2 == []
    assert json.dumps(_again, ensure_ascii=False, sort_keys=True) == \
        json.dumps(_new, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# ④ 引用转换**事务**（2026-10-01 复核第 27 轮 P1 三条）
# ---------------------------------------------------------------------------
def _clean_plan(beats, facts=None, hook="", lines=2, narr=3) -> dict:
    """一份**没有旁白镜像噪声**的计划：旁白条目的 `fact_refs` 为空（不牵引事实侧登记）."""
    return {"shot_beats": beats, "required_facts": facts or [], "hook_carrier": hook,
            "dialogue_lines": ["甲%d" % i for i in range(1, lines + 1)],
            "narration_lines": [{"text": "旁白正文%d" % i, "source": "原文第%d句" % i,
                                 "fact_refs": []} for i in range(1, narr + 1)]}


def test_an_out_of_range_reference_is_not_legalized_by_expansion():
    """**非法引用不得因扩号变合法**（复核实测：2 条节拍 + `B3/画面`，问题 1 → 0）.

    `B3` 在旧计划里越界；展开把编号推高之后它**碰巧**落在原 B2（画面）上，契约于是从
    报错变成通过——我们并没有核过这个引用，只是运气好。所以：旧编号域里核不过 ⇒
    **整条计划一个字都不改**，交规划器。
    """
    _beats = [_beat("刺客破水而出，破水处受袭", "台词1-2"), _beat("收尾", "画面")]
    _p = _clean_plan(_beats, facts=["刺客破水而出，破水处受袭｜原文证据：“黑影”"
                                    "｜承载：B3/画面"], lines=2)
    _before = plan_contract_problems(_p)
    assert any("B3 不存在" in x for x in _before), _before
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [] and _new is _p, _ch
    assert plan_contract_problems(_new) == _before
    # 陷阱本身：若放任展开（台词1／台词2／画面），`B3/画面` 会**碰巧**合法 ⇒ 0 问题
    _trap = _clean_plan([_beat("刺客破水而出，破水处受袭", "台词1"),
                         _beat("刺客破水而出，破水处受袭", "台词2"),
                         _beat("刺客破水而出，破水处受袭", "画面")],
                        facts=["刺客破水而出，破水处受袭｜原文证据：“黑影”｜承载：B3/画面"],
                        lines=2)
    assert plan_contract_problems(_trap) == [], plan_contract_problems(_trap)


def test_a_legal_reference_written_with_spaces_is_remapped_too():
    """含空格的**合法**声明（`B2 / 画面`）必须一起重映射——否则规范化会把通过改不通过.

    复核实测：旧重映射器只认紧贴的 `B2/画面`，`B2 / 画面` 漏改；展开后原 B2（画面）
    已后移到 B4，引用却还指着新的 B2（旁白1）⇒ **本来 0 问题的计划被改出 1 条问题**。
    """
    _p = _clean_plan([_beat("旁白介绍", "旁白1-3"), _beat("刺客破水而出", "画面")],
                     facts=["刺客破水而出｜原文证据：“黑影”｜承载：B2 / 画面"], narr=3)
    assert plan_contract_problems(_p) == [], plan_contract_problems(_p)
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, _ch
    _f = _new["required_facts"][0]
    assert _f.endswith("承载：B4 / 画面"), _f          # 只改数字，模型自己的空白写法保留
    assert plan_contract_problems(_new) == [], plan_contract_problems(_new)


def test_only_the_carrier_segment_is_rewritten():
    """事实的**正文与原文证据**是本体与出处，逐字保留——只有「承载：」段参与重映射."""
    _p = _clean_plan([_beat("开场", "画面"), _beat("旁白块", "旁白1-2"),
                      _beat("收尾", "画面")], narr=2,
                     facts=["它紧接 B3/画面 那一拍｜原文证据：“原句里的 B3/画面”"
                            "｜承载：B2/旁白2"])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, _ch
    _f = _new["required_facts"][0]
    assert "它紧接 B3/画面 那一拍" in _f, _f          # 正文没被动
    assert "原句里的 B3/画面" in _f, _f                # 出处没被动
    assert _f.endswith("｜承载：B3/旁白2"), _f          # 只有承载段的节拍号跟着平移


def test_a_hook_reference_with_spaces_is_remapped():
    """`hook_carrier` 也是声明字段（整串都是声明）——同样按旧域核验、按新域重映射."""
    _p = _clean_plan([_beat("开场", "画面"), _beat("旁白块", "旁白1-2")], narr=2,
                     hook="B2 / 旁白2")
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, _ch
    # 声明里的条目落在区间第二位 ⇒ 指向**真正承载它**的那一条（展开后的 B3），空白照留
    assert _new["hook_carrier"] == "B3 / 旁白2", _new["hook_carrier"]


def test_a_range_beyond_the_entries_blocks_normalization():
    """区间上界超过条目数（`旁白1-9` 而只有 3 条旁白）⇒ 不展开：那一步会替模型造出
    指向不存在条目的节拍；原样交规划器，引用它的那条事实照旧报「标注非法」."""
    _p = _clean_plan([_beat("开场", "画面"), _beat("旁白块", "旁白1-9")], narr=3,
                     facts=["旁白块讲的那件事｜原文证据：“x”｜承载：B2/旁白1"])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [] and _new is _p, _ch
    assert any("标注**非法**" in x for x in plan_contract_problems(_p)), \
        plan_contract_problems(_p)


# ---------------------------------------------------------------------------
# ⑤ 一套文法（2026-10-01 复核第 28 轮 P1）：验收与重映射**消费同一份**解析结果
# ---------------------------------------------------------------------------
#: 排版变体（换行/制表/全角空格）——用 chr() 拼，避免补丁脚本折掉反斜杠
_SPACED = ["B2/画面", "B 2/画面", "B" + chr(10) + "2/画面", "B2 / 画面",
           "B2" + chr(0x3000) + "/" + chr(0x3000) + "画面",
           "B2/" + chr(9) + "画面"]


@pytest.mark.parametrize("decl", _SPACED)
def test_the_acceptor_and_the_remapper_share_one_grammar(decl):
    """验收接受的写法，重映射器**必须同样认得**：合法引用不该因为排版变体被改坏.

    旧实现里验收用 `decl.split()` 去掉全部空白再 fullmatch，重映射器另列了一份空白名单
    （空格+制表符+全角空格）——`B 2/画面` 因此"验收认、重映射不认"：一份 0 问题的计划
    展开后被改成 1 问题（第 28 轮复核实测）。
    """
    assert parse_carrier_decl(decl) == (2, "画面", 0), decl
    assert len(iter_decl_refs(decl)) == 1, decl
    _p = _clean_plan([_beat("旁白介绍", "旁白1-3"), _beat("刺客破水而出", "画面")], narr=3,
                     facts=["刺客破水而出｜原文证据：“黑影”｜承载：%s" % decl])
    assert plan_contract_problems(_p) == [], decl
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, decl
    _seg = _new["required_facts"][0].split("｜")[-1]
    # 去掉空白后应当只剩"节拍号换成 4"这一处变化；其余字符一个都没动
    _flat = "".join(_seg.split())
    assert _flat == "承载：B4/画面", _seg
    assert plan_contract_problems(_new) == [], decl


def test_the_original_layout_survives_the_remap():
    """保留排版靠**记录原始跨度**，不靠另一份空白名单：`B 2 / 画面` → `B 4 / 画面`."""
    _p = _clean_plan([_beat("旁白介绍", "旁白1-3"), _beat("刺客破水而出", "画面")], narr=3,
                     facts=["刺客破水而出｜原文证据：“黑影”｜承载：B 2 / 画面"])
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch
    assert _new["required_facts"][0].endswith("承载：B 4 / 画面"), _new["required_facts"][0]


def test_a_spaced_out_of_range_reference_still_blocks_normalization():
    """排版变体不许成为**绕过旧域核验**的口子：`B 3 / 画面`（旧计划 2 条节拍）⇒ 整条不动."""
    _p = _clean_plan([_beat("刺客破水而出，破水处受袭", "台词1-2"),
                      _beat("刺客破水而出，破水处受袭", "画面")], lines=2,
                     facts=["刺客破水而出，破水处受袭｜原文证据：“黑影”｜承载：B 3 / 画面"])
    _before = plan_contract_problems(_p)
    assert any("B3 不存在" in x for x in _before), _before
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch == [] and _new is _p
    assert plan_contract_problems(_new) == _before


# ---------------------------------------------------------------------------
# ⑥ 单源文法必须**完整实现**既定文法（2026-10-01 复核第 29 轮）
# ---------------------------------------------------------------------------
#: 空白用 chr() 拼（补丁脚本会把反斜杠折掉）：空格/制表/换行/回车/全角空格/NBSP
_DECL_WS = [" ", chr(9), chr(10), chr(13), chr(0x3000), chr(0xa0)]

#: 基串 → 应有的解析结果（去空白后就是这些）
_DECL_BASES = {"B1/画面": (1, "画面", 0), "B12/画面": (12, "画面", 0),
               "B1/台词3": (1, "台词", 3), "B12/旁白34": (12, "旁白", 34)}


def test_inserting_whitespace_anywhere_keeps_the_declaration_equivalent():
    """**文法性质**（第 29 轮复核）：原契约是"去掉全部空白再匹配" ⇒ 在**任何位置**插入
    **任何空白**都必须等价（`B1 2/画面` 就是 `B12/画面`，`B12/画 面` 就是 `B12/画面`）.

    上一版扫描器自己定义了"哪里可以有空白"，174 个变体里拒了 42 个（旧解析器全接受）。
    """
    _checked = 0
    for _base, _want in _DECL_BASES.items():
        assert parse_carrier_decl(_base) == _want, _base
        for _w in _DECL_WS:
            for _pos in range(len(_base) + 1):
                _v = _base[:_pos] + _w + _base[_pos:]
                assert parse_carrier_decl(_v) == _want, (_v, parse_carrier_decl(_v), _want)
                _refs = iter_decl_refs(_v)
                # 扫描器给的是**原始**读数（画面没有序号 ⇒ None）；验收解析器把画面归一成 0
                assert len(_refs) == 1, (_v, _refs)
                assert (_refs[0][4], _refs[0][5], _refs[0][6] or 0) == _want, (_v, _refs)
                _checked += 1
    # 四个基串 × 每个字符边界 × 6 种空白 = **174** 个变体（字面量：改枚举就要改这里，
    # 而不是让公式跟着一起漂——复核第 30 轮指出我上一轮把 210 这个数字报错了）
    assert _checked == 174, _checked


_ILLEGAL_DECLS = ["B" + chr(0xb2) + "/画面",            # 上标数字：isdigit() 为真、int() 抛错
                  "B1/台词" + chr(0xb2),
                  "B" + "9" * 5000 + "/画面",           # 超长数字串（int 转换上限）
                  "B/画面", "B1/", "B1/歌词1", "B1x/画面", "B1/画面X", ""]


@pytest.mark.parametrize("decl", _ILLEGAL_DECLS)
def test_illegal_declarations_never_raise_and_leave_a_diagnostic(decl):
    """非法输入**不崩溃**，并且**在生成前门留下契约诊断**（第 29 轮复核：实际执行时
    `int("²")` 的 `ValueError` 一路穿过生成前门，连诊断都没留下）."""
    assert parse_carrier_decl(decl) is None, decl
    _p = _clean_plan([_beat("开场", "画面"), _beat("旁白块", "旁白1")], lines=1, narr=1,
                     facts=["某事实｜原文证据：“x”｜承载：%s" % decl])
    _probs = plan_contract_problems(_p)                 # ← 不许抛异常
    assert _probs and any("承载" in x for x in _probs), (decl, _probs)


def test_a_number_with_interior_whitespace_is_remapped_as_one_number():
    """`B1 2/画面` 是**一个**节拍号（12）：重映射按整段数字跨度换新号，其余字符不动."""
    _beats = ([_beat("开场", "画面"), _beat("旁白块", "旁白1-2")]
              + [_beat("画面%d" % i, "画面") for i in range(3, 13)])
    _p = _clean_plan(_beats, facts=["画面12th｜原文证据：“x”｜承载：B1 2/画面"], lines=1, narr=2)
    assert parse_carrier_decl(_p["required_facts"][0].split("｜")[-1][len("承载："):]) \
        == (12, "画面", 0)
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, _ch
    assert _new["required_facts"][0].split("｜")[-1] == "承载：B13/画面", \
        _new["required_facts"][0]
