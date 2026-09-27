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
    evaluate_plan,
    find_legal_split,
    verify_dialogue_lines,
)

SOURCE = "张十三说：住嘴。他吓坏了。\n原文里有且只有这一句旁白。"


def _mk_script(*shots):
    """最小稿：shots 逐镜给 {"index", "narration_segment", "plan_refs"}."""
    return {"shots": list(shots)}


def _refs(beats=(), lines=()):
    return {"beats": list(beats), "lines": list(lines)}


PLAN = {"shot_beats": ["B1 黄昏泛舟", "B2 刺杀", "B3 尖叫验尸",
                       "B4 恐吓隐瞒", "B5 赶车离开"],
        "dialogue_lines": ["张十三道：住嘴！", "听香道：杀人啦……"]}

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
    """新计划选材未变少 → 只留档不告警；键相同（重选回写）→ 完全 no-op."""
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
