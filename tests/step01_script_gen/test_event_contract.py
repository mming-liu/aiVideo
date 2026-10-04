# -*- coding: utf-8 -*-
"""事件履约契约（v6.138，2026-09-26 九审四缺口）.

## 为什么需要它

09-26 18:46 实跑 BLOCK（10 条 blocking：3 审计 + 7 确定性）的三条根因链里，
**上游缺事件履约约束**是内容缺陷最早可见的位置：

- 入选快照 shot8 在供述台词进行中插入「再度尖叫」（修复补丁引入）→ 审计
  TIMELINE_CONFLICT；
- 规划重选压缩预算时删掉「恐吓听香」「赶车命令」的承载台词，节拍从此无承载
  → 审计 MISSING_PLOT / MISSING_ACTION——而修复轮不许改台词（逐字锁），
  结构性死锁。

契约把「哪个镜头承载哪些节拍/台词」变成**显式、可确定性核验**的声明：

- 计划侧：`shot_beats` 条目末尾标「｜载体：台词k / 载体：画面」；重选删承载
  台词必须同步更新载体标注（`_plan_core_preserved` 只放行纯载体差异），
  无载体又无替代 → 计划不可行（代码不代决）。
- 生成侧：`render_execution_contract` 不压缩块给出 B1..Bn / D1..Dm 编号与
  `plan_refs` 履约声明规则；每镜输出 `plan_refs`。
- 校验侧（本文件主体）：`check_event_contract` 在候选入池前核验
  **覆盖**（每节拍至少一镜承载）/ **顺序**（B、D 序列按镜序非降）/
  **越界**（引用不存在的编号）；旧稿整稿无声明 → 容忍（无可核 ≠ 被破坏）。

同文件覆盖九审其余缺口：说话人前缀单源判据（P1-3）、句末切点消费尾随引号
（边界项）、修复重试排除目标前置过滤（P1-2）。台词门排他性（P2-4）的
用例在 test_plan_feasibility.py。
"""
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.plan_feasibility import (  # noqa: E402
    _beat_carrier,
    _split_speaker_prefix,
    check_event_contract,
    check_script_dialogue_integrity,
    coerce_ref_index,
    evaluate_plan,
    bind_plan_identity,
    check_event_contract,
    find_legal_split,
    plan_identity,
    verify_dialogue_lines,
)

SOURCE = "张十三说：住嘴。他吓坏了。\n原文里有且只有这一句旁白。"


def _mk_script(*shots, plan_id=None):
    """最小稿：shots 逐镜给 {"index", "narration_segment", "plan_refs"}.

    2026-09-27：`plan_refs` 必须带**计划版本身份**（本文件所有声明都是对着唯一那份
    `PLAN` 作的，故默认按它盖戳）。不带身份的稿子无法核验 → 契约门返回
    `declared=False`、不再判覆盖/顺序（「不可核 ≠ 被破坏」，与缺声明同口径）。
    """
    return {"shots": list(shots),
            "plan_id": plan_identity(PLAN) if plan_id is None else plan_id}


def _refs(beats=(), lines=()):
    return {"beats": list(beats), "lines": list(lines)}


PLAN = {"shot_beats": ["B1 黄昏泛舟", "B2 刺杀", "B3 尖叫验尸",
                       "B4 恐吓隐瞒", "B5 赶车离开"],
        "dialogue_lines": ["张十三道：住嘴！", "听香道：杀人啦……"]}

#: 同一份计划 + **承载契约**（节拍标载体）。2026-09-30 起「新稿必须逐镜声明」的
#: 强制力挂在承载契约上：带契约的计划缺声明 = 显式失败；没有契约的历史计划沿用
#: 「无可核 ≠ 被破坏」（与 `carrier_relation_problems` 同口径）。
PLAN_V2 = {"shot_beats": [b + "｜载体：画面" for b in PLAN["shot_beats"]],
           "dialogue_lines": list(PLAN["dialogue_lines"])}

def test_event_contract_refs_from_another_plan_version_are_refused():
    """**同号误认**反例（2026-09-27 复核点名）：旧快照的 B2 编号仍合法，但指的不是同一条节拍.

    计划被重选改动（`insert_after` 在 B1 之后插了一条）→ 编号整体顺延；历史快照里按
    **旧计划**作的声明若不换号照用，B2 仍 ≤ 节拍数，只查范围的实现会静默放行——
    那正是「编号在范围内 ≠ 同一条节拍」。身份不符必须拒收。
    """
    new_plan = {"shot_beats": ["B0 新插的一条"] + PLAN["shot_beats"],
                "dialogue_lines": PLAN["dialogue_lines"]}
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2], [])},
        {"index": 2, "narration_segment": '张十三道：住嘴！听香道：杀人啦……',
         "plan_refs": _refs([3, 4, 5], [1, 2])},
        plan_id=plan_identity(PLAN))
    assert max(max(s["plan_refs"]["beats"]) for s in script["shots"])         <= len(new_plan["shot_beats"]), "前提：旧声明的编号在新计划里**全部**合法"
    ok, violations, declared = check_event_contract(script, new_plan)
    assert not ok and declared
    assert [v["code"] for v in violations] == ["plan_refs_stale_plan"], violations
    # 反证：同一份声明对着**它自己那版**计划仍然成立（不是把门一律关死）
    ok2, v2, _ = check_event_contract(script, PLAN)
    assert ok2, v2


def test_event_contract_unstamped_script_is_not_treated_as_this_plans_declaration():
    """不带身份 = **无法核验**：不得当成当前计划的声明，但也不据「不可核」判违规."""
    script = {"shots": [{"index": 1, "narration_segment": "",
                         "plan_refs": _refs([1, 2, 3, 4, 5], [1, 2])}]}
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok and violations == [] and declared is False


# ---------------------------------------------------------------------------
# ① check_event_contract：覆盖 / 顺序 / 越界 / 缺声明 / 旧稿容忍
# ---------------------------------------------------------------------------

CODES = "；".join


def test_event_contract_full_coverage_in_order_passes():
    """全节拍被承载、B/D 序列按镜序非降 → 通过."""
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2], [])},
        {"index": 2, "narration_segment": "张十三道：住嘴！",
         "plan_refs": _refs([3, 4], [1])},
        {"index": 3, "narration_segment": "听香道：杀人啦……",
         "plan_refs": _refs([5], [2])},
    )
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok, violations
    assert declared is True


def test_event_contract_uncarried_beat_is_a_violation():
    """判别反例（九审根因 1）：B4「恐吓隐瞒」无任何镜头承载 → 拒收.

    这正是 09-26 实跑 MISSING_PLOT 的来源：规划删掉承载台词后节拍在成片无承载，
    此前只能在提交点靠语义审计发现、修复轮又不可修。
    """
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2, 3], [])},
        {"index": 2, "narration_segment": "张十三道：住嘴！",
         "plan_refs": _refs([5], [1])},
    )
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    _missed = [v for v in violations if v["code"] == "beats_not_carried"]
    assert len(_missed) == 1 and _missed[0]["shot"] == ""
    assert "B4" in _missed[0]["detail"] and "恐吓" in _missed[0]["detail"]


def test_event_contract_out_of_order_beats_are_a_violation():
    """判据：B 序列按镜序展开必须非降——后位节拍在前位完成前呈现 = 越位."""
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2], [])},
        {"index": 2, "narration_segment": "", "plan_refs": _refs([5], [])},
        {"index": 3, "narration_segment": "", "plan_refs": _refs([3, 4], [])},
    )
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    _ord = [v for v in violations if v["code"] == "beats_order_violation"]
    assert len(_ord) == 1
    assert "B3" in _ord[0]["detail"] and "B5" in _ord[0]["detail"]
    assert _ord[0]["shot"] == "3"


def test_event_contract_out_of_order_lines_are_a_violation():
    """D 序列同理：台词呈现顺序不得回退（此前台词门不查相对顺序）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "张十三道：住嘴！",
         "plan_refs": _refs([], [1])},
        {"index": 2, "narration_segment": "听香道：杀人啦……",
         "plan_refs": _refs([], [2, 1])},
    )
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    assert any(v["code"] == "lines_order_violation" for v in violations)


def test_event_contract_out_of_range_refs_are_a_violation():
    """引用不存在的 B/D → 声明与计划脱节（防编号口径漂移）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 99], [7])})
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    codes = [v["code"] for v in violations]
    assert codes.count("plan_refs_out_of_range") == 2


def test_event_contract_partial_missing_refs_fail_fresh_generations():
    """部分镜头缺 plan_refs → 逐镜记缺失（新生成稿不该出现混合形态）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1], [])},
        {"index": 2, "narration_segment": ""},
    )
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    assert [v["code"] for v in violations] == ["plan_refs_missing"]
    assert violations[0]["shot"] == "2"


def test_event_contract_legacy_script_without_any_refs_is_tolerated():
    """旧稿（历史快照）整稿无 plan_refs → 契约未声明，不判违规.

    没有契约可核 ≠ 契约被破坏——硬拦会让跨 run 快照线全灭（快照线正是当前
    10 条 blocking 收敛的主要机制）。
    """
    script = _mk_script({"index": 1, "narration_segment": "张十三道：住嘴！"},
                        {"index": 2, "narration_segment": ""})
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok and violations == [] and declared is False


def test_event_contract_missing_plan_refs_is_declared_false_for_empty_script():
    """空 shots 也按「未声明」处理（与台词门的空稿路径互不踩脚）."""
    ok, violations, declared = check_event_contract({"shots": []}, PLAN)
    assert ok and declared is False and violations == []


# ---------------------------------------------------------------------------
# ② 计划侧载体：解析 / evaluate_plan 悬空判不可行 / 重选核心字段豁免
# ---------------------------------------------------------------------------

def test_beat_carrier_parsing():
    assert _beat_carrier("节拍：恐吓隐瞒｜信息目的：转折｜载体：台词3") == ("dialogue", 3)
    assert _beat_carrier("节拍：黄昏泛舟｜信息目的：建置｜载体：画面") == ("visual", None)
    # 旧缓存计划无载体段 → 未声明（无可核 ≠ 被破坏）
    assert _beat_carrier("节拍：黄昏泛舟｜信息目的：建置") == (None, None)


def _plan_with_carrier(beats, lines):
    return {"main_events": ["事件1", "事件2"], "climax": "对峙",
            "causal_chain": ["起因→行动→结果"],
            "shot_beats": beats, "dialogue_lines": lines,
            "required_facts": ["事实A｜原文证据：一道寒光刺进了心口"],
            "omittable": [], "time_budget": "对白约20秒"}


def test_evaluate_plan_dangling_beat_carrier_is_infeasible():
    """重选删掉承载台词且未更新载体 → 计划不可行（走重选反馈，不给生成）."""
    plan = _plan_with_carrier(
        ["节拍：黄昏泛舟｜载体：画面", "节拍：恐吓隐瞒｜载体：台词2"],
        ["张十三道：住嘴！"])          # 台词2 不存在（只有 1 句）
    rep = evaluate_plan(plan, novel_text="张十三说：住嘴。他吓坏了。")
    assert rep["verdict"] == "infeasible"
    assert rep["beat_carrier_dangling"], rep
    assert "B2" in rep["beat_carrier_dangling"][0]


def test_evaluate_plan_valid_carrier_stays_feasible():
    plan = _plan_with_carrier(
        ["节拍：喝止听香｜载体：台词1", "节拍：黄昏泛舟｜载体：画面"],
        ["张十三道：住嘴！"])
    rep = evaluate_plan(plan, novel_text="张十三说：住嘴。他吓坏了。")
    assert rep["verdict"] == "feasible", rep["reasons"]
    assert rep["beat_carrier_dangling"] == []


def test_evaluate_plan_legacy_beats_without_carrier_are_tolerated():
    """旧缓存计划节拍无载体段 → 不判悬空（否则每次重跑都被打回重选）."""
    plan = _plan_with_carrier(["节拍：黄昏泛舟", "节拍：刺杀"],
                              ["张十三道：住嘴！"])
    rep = evaluate_plan(plan, novel_text="张十三说：住嘴。他吓坏了。")
    assert rep["verdict"] == "feasible", rep["reasons"]


def test_evaluate_plan_no_carrier_marker_is_explicit_stop():
    """「载体缺失：无合适承载」标记 = 内容取舍交还用户，代码不代决."""
    plan = _plan_with_carrier(["节拍：恐吓隐瞒｜载体：台词2"], ["张十三道：住嘴！"])
    plan["time_budget"] = "载体缺失：无合适承载"
    rep = evaluate_plan(plan, novel_text="张十三说：住嘴。他吓坏了。")
    assert rep["verdict"] == "infeasible"
    assert any("代码不代决" in r for r in rep["reasons"])


def test_plan_core_preserved_allows_carrier_only_beat_diff():
    """重选只改「｜载体：…」标注 → 放行；改节拍本体 → 拒收（九审 P1-2 配套）."""
    from step01_script_gen.outline import _plan_core_preserved

    old = {"shot_beats": ["节拍：恐吓隐瞒｜载体：台词2", "节拍：泛舟｜载体：画面"],
           "required_facts": ["F"]}
    new = {"shot_beats": ["节拍：恐吓隐瞒｜载体：台词1", "节拍：泛舟｜载体：画面"],
           "required_facts": ["F"]}
    assert _plan_core_preserved(old, new) == ""

    new_bad = {"shot_beats": ["节拍：恐吓+灭口｜载体：台词1",
                              "节拍：泛舟｜载体：画面"],
               "required_facts": ["F"]}
    assert _plan_core_preserved(old, new_bad) == "shot_beats"


def test_replan_carrier_feedback_block_rendered():
    """载体悬空触发重选时，反馈块必须点名悬空节拍并给出更新规则."""
    from step01_script_gen.outline import _budget_feedback_block

    plan = _plan_with_carrier(["节拍：恐吓隐瞒｜载体：台词2"], ["张十三道：住嘴！"])
    rep = evaluate_plan(plan, novel_text="张十三说：住嘴。他吓坏了。")
    block = _budget_feedback_block(rep, plan)
    assert "载体" in block and "台词2" in block
    assert "上一版计划" in block


# ---------------------------------------------------------------------------
# ③ 生成执行契约块：节拍编号 + plan_refs 规则（九审缺口 1 的注入面）
# ---------------------------------------------------------------------------

def test_execution_contract_numbers_beats_and_declares_plan_refs():
    from step01_script_gen.prompts import render_execution_contract

    block = render_execution_contract(PLAN)
    assert "四、镜头节拍" in block
    assert "\nB1. B1 黄昏泛舟" in block      # 编号行存在（节拍文本自带 B1 前缀是夹具巧合）
    assert block.count("\nB") >= 5
    assert "五、事件履约声明" in block and "plan_refs" in block
    assert "不得提前呈现" in block
    # 台词编号保持既有口径（第一节的 1..N）
    assert "1. 张十三道：住嘴！" in block


def test_execution_contract_beats_are_not_a_shot_list():
    """v6.141 回归：节拍编号块必须显式否认「节拍=逐条一镜」，并重申镜数来自时长预算.

    实损（09-26 21:32~21:35）：v6.138 加入 B1..Bn 清单后，模型把它当镜头骨架、
    按「每节拍恰好一镜」最小满足覆盖要求——3/3 候选取 6 节拍→**7 镜 28s**
    （加块前同计划 13-14 镜 48-52s），塌缩稿装不下 6 句台词、3 句 not_found，
    被候选层台词门整体判破损，run exit 1。契约块长度 825→1576 字符与回归窗口
    逐字对上（`git diff` 段即增补第四/五节）。

    本用例锁住「切断映射」的那句话：删掉它等于把回归放回去。
    """
    from step01_script_gen.prompts import render_execution_contract

    block = render_execution_contract(PLAN)
    assert "节拍数 ≠ 镜头数" in block
    assert "不是逐条拍一镜的清单" in block
    assert "由**总时长目标反推**" in block
    # 覆盖要求仍在（判据未放宽），但明确允许一个节拍跨多镜承载
    assert "一个节拍可由多个镜头共同承载" in block
    # 编号口径不变：plan_feasibility 的 plan_refs 校验按 B/D 序号对齐，不得改动
    assert "B%d. " % len(PLAN["shot_beats"]) in block
    assert "B1..B%d" % len(PLAN["shot_beats"]) in block


# ---------------------------------------------------------------------------
# ④ 说话人前缀单源判据（九审 P1-3）：冒号 ≠ 前缀
# ---------------------------------------------------------------------------

def test_speaker_prefix_requires_punctuation_free_head():
    """头部含子句标点的冒号不是说话人前缀——正文不得被丢弃."""
    pfx, body = _split_speaker_prefix("且慢，我告诉你：你先走。")
    assert pfx == "" and body == "且慢，我告诉你：你先走。"
    pfx2, body2 = _split_speaker_prefix("张十三道：你先走。")
    assert pfx2 == "张十三道：" and body2 == "你先走。"


def test_verify_dialogue_lines_no_longer_passes_composite_colon_line():
    """判别反例：原文只有 `你先走。`，计划句 `且慢，我告诉你：你先走。` 必须判拒收.

    旧实现把冒号前段当前缀丢掉、用后半句就通过了核验（九审 P1-3 实锤）。
    """
    kept, rejected = verify_dialogue_lines(
        ["且慢，我告诉你：你先走。", "你先走。"], "他只留下一句：你先走。")
    assert kept == ["你先走。"]
    assert len(rejected) == 1 and "且慢" in rejected[0]["line"]


# ---------------------------------------------------------------------------
# ⑤ 句末切点消费尾随引号（九审边界项）
# ---------------------------------------------------------------------------

def test_legal_split_consumes_trailing_closers_after_sentence_end():
    """切点落在 `！”` 之后——下一镜不得以孤立的闭引号开头."""
    line = ("张十三道：你先走。”他喊道。”别回头。”听香哭喊。”快走啊。”"
            "他最后喊了一声。”")
    parts = find_legal_split(line, 4.0)
    if parts is not None:                      # 切得出时每段不得以孤立闭引号开头
        for seg in parts:
            body = _split_speaker_prefix(seg)[1]
            assert not body.startswith("”"), (parts, seg)
        # 拼回逐字连续
        from step01_script_gen.plan_feasibility import _speaker_body
        assert "".join(_speaker_body(p) for p in parts) == _speaker_body(line)


def test_deterministic_cuts_never_start_with_orphan_closer():
    """拆分器同判据：子镜正文不得以闭引号开头."""
    from step01_script_gen.main import _deterministic_narration_cuts
    from step01_script_gen.plan_feasibility import _split_speaker_prefix

    narr = ("张十三道：" + "你最好想清楚。”" * 8)
    parts = _deterministic_narration_cuts(narr, 6.0)
    if parts:
        for p in parts:
            assert not _split_speaker_prefix(p)[1].startswith("”"), parts


# ---------------------------------------------------------------------------
# ⑥ 修复重试排除目标前置过滤（九审 P1-2）
# ---------------------------------------------------------------------------

def test_pick_repair_targets_backfills_quota_after_exclusion():
    """判别反例：5+ 目标、前 2 个被排除 → 名额必须由后续目标补满.

    旧实现"取前 4 再剔除"只修 2 镜（2 个名额空转）；前置过滤后可用目标全进，
    且被排除目标绝不入选。
    """
    from step01_script_gen.main import _pick_repair_targets

    shot_items = {f"shot_{i}": [f"w{i}"] for i in range(1, 6)}   # shot_1..shot_5
    picked = _pick_repair_targets(shot_items, set(), 4,
                                  excluded={"shot_1", "shot_2"})
    # 剩余 3 个可用目标全部入选（不超过名额 4），被排除的两个绝不出现
    assert set(picked) == {"shot_3", "shot_4", "shot_5"}


def test_pick_repair_targets_exclusion_preserves_priority():
    """排除后仍按「带确定性阻塞优先」排序填名额."""
    from step01_script_gen.main import _pick_repair_targets

    shot_items = {"shot_1": ["w"] * 5, "shot_2": ["w"] * 9, "shot_3": ["w"],
                  "shot_4": ["w"] * 2, "shot_5": ["w"] * 7}
    picked = _pick_repair_targets(shot_items, {"shot_4", "shot_5"}, 3,
                                  excluded={"shot_5"})
    # 确定性目标 shot_4 优先；shot_5 被排除 → 名额落回按告警数的 shot_2
    assert set(picked) == {"shot_4", "shot_2", "shot_1"}


# ---------------------------------------------------------------------------
# ⑦ 大纲缓存重生成防护（v6.138.1，2026-09-26 实跑实证）
# ---------------------------------------------------------------------------

def test_outline_cache_rotation_backs_up_and_warns_on_regression(tmp_path, capsys):
    """缓存键变化（规则演进）→ 覆盖前旧计划留 .bak；新计划选材变少 → 告警.

    实证（2026-09-26 20:20 run）：outline prompt 变更令缓存失效重生成，新抽样
    6 句→2 句，成片 38s 撞 duration_budget，且全部历史快照因选材门失格——
    退化必须可见、旧计划必须可恢复。
    """
    import json

    from step01_script_gen.outline import _rotate_outline_cache_on_regen

    cache = tmp_path / "chapter_outline.cache.json"
    old = {"source_hash": "old-key",
           "outline": {"dialogue_lines": ["一", "二", "三", "四", "五", "六"],
                       "shot_beats": ["b"]}}
    cache.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    _rotate_outline_cache_on_regen(
        cache, "new-key",
        {"dialogue_lines": ["一", "二"], "shot_beats": ["b"]})

    bak = json.loads((tmp_path / "chapter_outline.cache.json.bak").read_text(encoding="utf-8"))
    assert bak["source_hash"] == "old-key"
    assert len(bak["outline"]["dialogue_lines"]) == 6
    assert "6 句 → 2 句" in capsys.readouterr().out


def test_outline_cache_rotation_silent_when_not_regressing_or_same_key(tmp_path, capsys):
    """新计划选材未变少 → 只留档不告警；键相同（重选回写）→ **不再 no-op**.

    v6.143（2026-09-27 复核）：同键覆盖正是**规划重选**的场景，旧实现在这里直接
    return，把原计划**就地抹掉**（09-26 实测：章 2 那份不可行计划被重选结果替换，
    `.bak` 不存在、没有恢复入口）。现在无论键是否变化都先落 `.bak`；告警仍只在
    键变化（规则演进）时报。
    """
    import json

    from step01_script_gen.outline import _rotate_outline_cache_on_regen

    cache = tmp_path / "chapter_outline.cache.json"
    cache.write_text(json.dumps(
        {"source_hash": "old-key",
         "outline": {"dialogue_lines": ["一", "二"], "shot_beats": ["b"]}},
        ensure_ascii=False), encoding="utf-8")
    _rotate_outline_cache_on_regen(
        cache, "new-key", {"dialogue_lines": ["一", "二", "三"], "shot_beats": ["b"]})
    assert (tmp_path / "chapter_outline.cache.json.bak").exists()
    out = capsys.readouterr().out
    assert "[WARN]" not in out

    # 键相同 → no-op（重选回写场景，不轮换不告警）
    cache.write_text(json.dumps(
        {"source_hash": "new-key",
         "outline": {"dialogue_lines": ["一", "二", "三"], "shot_beats": ["b"]}},
        ensure_ascii=False), encoding="utf-8")
    _rotate_outline_cache_on_regen(
        cache, "new-key", {"dialogue_lines": [], "shot_beats": ["b"]})
    assert "[WARN]" not in capsys.readouterr().out
    # 同键覆盖也必须留旧版（重选回写场景）
    _bak = json.loads((tmp_path / "chapter_outline.cache.json.bak").read_text(
        encoding="utf-8"))
    assert _bak["outline"]["dialogue_lines"] == ["一", "二", "三"], _bak


# ---------------------------------------------------------------------------
# ①b 候选门口径（2026-09-27 复核 P0-1）：**先绑定身份、再要求声明过且通过**
# ---------------------------------------------------------------------------

def _undeclared(*shots):
    """无 `plan_id` 的稿子（= 生成 schema 不产出该字段时新稿的真实形态）."""
    return _mk_script(*shots, plan_id="")


def test_undeclared_new_candidate_is_rejected_when_declaration_required():
    """**P0-1 反例（复核给定）**：无 `plan_id`、缺 B4 的新候选必须拒收入池.

    旧顺序（先核验、通过后才落戳）下，无 `plan_id` 的稿子在核验时直接返回「未声明」，
    于是**绕过覆盖/顺序核验**又被盖上「已核验」的身份——比不盖章更糟。
    """
    script = _undeclared(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2, 3], [])},
        {"index": 2, "narration_segment": "", "plan_refs": _refs([5], [1, 2])})
    # 默认（兼容口径）：不据「不可核」判违规
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok and violations == [] and declared is False
    # 带承载契约的计划 + 候选门口径：必须声明过 → 直接判未声明
    ok2, v2, declared2 = check_event_contract(script, PLAN_V2, require_declaration=True)
    assert not ok2 and declared2 is False
    assert [v["code"] for v in v2] == ["plan_refs_missing"], v2
    # 没有承载契约的历史计划：沿用容忍口径（不改旧语义）
    ok3, v3, declared3 = check_event_contract(script, PLAN, require_declaration=True)
    assert ok3 and v3 == [] and declared3 is False


def test_binding_then_requiring_declaration_still_validates_coverage():
    """**绑定 ≠ 免检**：绑定当前计划身份后仍逐项核验（缺 B2 被拒）——这才是 P0-1 的修法."""
    script = _undeclared(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1], [])},
        {"index": 2, "narration_segment": '张十三道：住嘴！听香道：杀人啦……',
         "plan_refs": _refs([3, 4, 5], [1, 2])})
    assert bind_plan_identity(script, PLAN) == plan_identity(PLAN)
    assert script["plan_id"] == plan_identity(PLAN)
    ok, violations, declared = check_event_contract(script, PLAN,
                                                    require_declaration=True)
    assert not ok and declared is True
    assert [v["code"] for v in violations] == ["beats_not_carried"], violations


def test_binding_never_overwrites_an_existing_identity():
    """已有身份**一律不覆盖**——覆盖等于把「对另一版计划作的声明」洗成对当前计划的."""
    script = {"plan_id": "deadbeef0000", "shots": []}
    assert bind_plan_identity(script, PLAN) == "deadbeef0000"
    assert script["plan_id"] == "deadbeef0000"


def test_binding_a_fresh_candidate_that_passes_is_then_verifiable():
    """正向：绑定 → 声明过且覆盖/顺序都对 → 通过；此后身份与本轮一致，不再是「不可核」."""
    script = _undeclared(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2, 3], [])},
        {"index": 2, "narration_segment": '张十三道：住嘴！听香道：杀人啦……',
         "plan_refs": _refs([4, 5], [1, 2])})
    bind_plan_identity(script, PLAN)
    ok, violations, declared = check_event_contract(script, PLAN,
                                                    require_declaration=True)
    assert ok and declared is True and violations == []


# ---------------------------------------------------------------------------
# ①c plan_refs 编号形态（2026-09-27 02:10 重跑实测：门误拒 "B1" 字符串）
# ---------------------------------------------------------------------------

def test_plan_refs_accepts_b_and_d_prefixed_strings():
    """**实跑反例**：模型按契约块字面填 `["B1"]`/`["D1"]` → 归一化后照常通过.

    修复前：`isinstance(b, int)` 直接判 `plan_refs_out_of_range`，detail 打出 `BB1`
    （模板 `"B%s 不存在"` 套在字符串上）——白烧 1/3 候选预算，最坏整池拒收终止整章。
    为何以前没暴露：P0-1 修复前无 `plan_id` 的稿子在校验函数里 early-return，这些
    非法 refs **静默通过**（正是「未核验却盖上已核验」的假绿）。
    """
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs(["B1", "B2"], [])},
        {"index": 2, "narration_segment": '张十三道：住嘴！',
         "plan_refs": _refs(["b3", "B4"], ["D1"])},
        {"index": 3, "narration_segment": '听香道：杀人啦……', "plan_refs": _refs(["B5"], ["2"])},
    )
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok, violations
    assert declared is True


def test_plan_refs_coercion_accepts_int_digit_string_and_prefixed_forms():
    """三种写法语义一致：`1` / `"1"` / `"B1"`（含小写、空格、分隔符）."""
    for v in (1, "1", "B1", "b1", " B 1 ", "B-1", "B_1", "B:1"):
        assert coerce_ref_index(v, "B") == 1, v
    assert coerce_ref_index("D2", "D") == 2
    assert coerce_ref_index("1", "D") == 1
    # 标签不匹配 / 非编号 → None（由调用方报 not_a_number）
    for v in ("", None, "第1个", "B", "Bx", True, False, [], {}):
        assert coerce_ref_index(v, "B") is None, v


def test_plan_refs_junk_gets_its_own_code_not_out_of_range():
    """非编号项报 `plan_refs_not_a_number`（不再冒充「越界」，detail 也不再打出 BB1）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "",
         "plan_refs": _refs([1, 2, 3, 4, 5], ["第1句"])})
    ok, violations, declared = check_event_contract(script, PLAN)
    assert not ok and declared
    codes = [v["code"] for v in violations]
    assert codes == ["plan_refs_not_a_number"], violations
    assert "BB" not in violations[0]["detail"], violations[0]["detail"]


def test_plan_refs_range_is_still_enforced_after_coercion():
    """归一化**不降判据强度**：`"B9"`（共 5 个节拍）照旧判越界，且 detail 是人话."""
    script = _mk_script(
        {"index": 1, "narration_segment": "",
         "plan_refs": _refs(["B1", "B2", "B3", "B4", "B9"], [])})
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    _oor = [v for v in violations if v["code"] == "plan_refs_out_of_range"]
    assert len(_oor) == 1 and "B9 不存在" in _oor[0]["detail"], violations


# ---------------------------------------------------------------------------
# ①d Dk 必须锚在**声明它的那一镜**（2026-09-27 复核：两门都过的假绿）
# ---------------------------------------------------------------------------

def test_line_declared_on_the_wrong_shot_is_rejected():
    """**复核给的假绿反例**：shot 1 声明 D1、shot 2 才说 D1.

    整稿台词门通过（D1/D2 都在稿里）、契约门当时只核编号范围与顺序 → 一路放行。
    现在按镜核验：D1 最早的声明镜是 shot 1，而 shot 1 的旁白锚不住这句 ⇒ 拒收。
    """
    script = _mk_script(
        {"index": 1, "narration_segment": "他转身就走。",
         "plan_refs": _refs([1, 2, 3], [1])},
        {"index": 2, "narration_segment": "张十三道：住嘴！听香道：杀人啦……",
         "plan_refs": _refs([4, 5], [2])})
    _dlg_ok, _ = check_script_dialogue_integrity(script, PLAN)
    assert _dlg_ok, "前提：台词确实都在稿里（只是不在声明它的那一镜）"
    ok, violations, declared = check_event_contract(script, PLAN)
    assert not ok and declared
    assert [v["code"] for v in violations] == ["plan_refs_line_not_in_shot"], violations
    assert "镜头 1" in violations[0]["detail"] and "D1" in violations[0]["detail"]


def test_line_declared_by_a_shot_without_narration_is_rejected():
    """声明了台词、但该镜**没有旁白** → 拒收（空旁白镜不可能承载台词）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "", "plan_refs": _refs([1, 2, 3], [1])},
        {"index": 2, "narration_segment": "听香道：杀人啦……",
         "plan_refs": _refs([4, 5], [2])})
    ok, violations, _ = check_event_contract(script, PLAN)
    assert not ok
    assert "没有旁白" in violations[0]["detail"], violations


def test_line_declared_on_its_own_shot_passes():
    """正向：声明镜确实说这句 → 通过（判据不误伤正常稿）."""
    script = _mk_script(
        {"index": 1, "narration_segment": "张十三道：住嘴！", "plan_refs": _refs([1, 2, 3], [1])},
        {"index": 2, "narration_segment": "听香道：杀人啦……", "plan_refs": _refs([4, 5], [2])})
    ok, violations, declared = check_event_contract(script, PLAN)
    assert ok and declared and violations == [], violations


def test_cross_shot_split_line_declared_on_its_first_shot_passes():
    """跨镜拆分的台词：声明在**首镜**上通过（判据与整稿门同源，不误伤合法拆分）."""
    long_line = "张十三道：住嘴！他转身便走，脚下生风，一路行去竟不回头。"
    plan = {"shot_beats": ["节拍一"], "dialogue_lines": [long_line]}
    script = _mk_script(
        {"index": 1, "narration_segment": "张十三道：住嘴！",
         "plan_refs": _refs([1], [1])},
        {"index": 2, "narration_segment": "他转身便走，脚下生风，一路行去竟不回头。",
         "plan_refs": _refs([1], [])},
        plan_id=plan_identity(plan))
    ok, violations, declared = check_event_contract(script, plan)
    assert ok and declared, violations
