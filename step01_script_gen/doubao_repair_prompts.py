"""doubao_repair_prompts - doubao 专用修复轮 / GAP 重生 prompt 构造器（v6.60+, 2026-09-05）.

设计原则:
  - 与 deepseek 修复轮 prompt 解耦
  - temperature 透传 0.2
  - 复用 build_doubao_prompt 的 _DOUBAO_NOVEL_MAX=1600 截断

导出:
  - build_doubao_repair_prompt
  - build_doubao_gap_regen_prompt
  - build_doubao_regen_over_limit_prompt
"""
from __future__ import annotations

from typing import Any

from shared.config import SHOT_COUNT_MIN, SHOT_COUNT_MAX

from .prompts import (
    build_doubao_prompt,
    DOUBAO_SYSTEM_PROMPT,
    _build_regen_iron_rules,
)
# 时长/镜数规则数字单源：timecode（单镜/总时长下限）+ validation（红果总时长上限）
# + shared.config（镜数护栏）。prompt 文案禁止再写死这些数字（历史三方漂移实录：
# 规则行 8-20 / 签名默认 8,20 / doubao_pipeline 实参 4-40）。
from .timecode import MAX_SHOT_DURATION, MIN_TOTAL_SHOTS_DURATION
from .validation import _REDGUO_DURATION_MAX


def _shot_count_constraint_txt(shot_count_min: int, shot_count_max: int) -> str:
    """镜数约束文案：跟随 SHOT_COUNT_MIN/MAX 护栏（0=不限，与 validation _REDGUO_SHOT_* 同语义）."""
    if shot_count_min <= 0 and shot_count_max <= 0:
        return "镜数不限"
    return f"镜数 {shot_count_min}-{shot_count_max}"


_SHOT_COUNT_RULE_LINE = f"C. {_shot_count_constraint_txt(SHOT_COUNT_MIN, SHOT_COUNT_MAX)}，密度自然可分"

_DOUBAO_HARD_RULE_LINES = [
    f"A. 总时长 {MIN_TOTAL_SHOTS_DURATION:g}-{_REDGUO_DURATION_MAX:g}s（可放宽 30-150s 边界）",
    f"B. 单镜 <= {MAX_SHOT_DURATION:g}s（{MAX_SHOT_DURATION:g}s 硬上限）",
    _SHOT_COUNT_RULE_LINE,
    "D. 场景 <= 3 个硬约束",
    "E. 人物 <= 4 人（特殊卧底戏可破例）",
    "F. 末镜未完成态：危机/悬念/争夺/对望/行动中",
    "G. 受伤连续性：伤员后续镜保留伤处",
    "H. 视线方位：含「看/望/盯/视」必须写目标",
    "I. 服装色锚点：主角色不可缺",
    "J. 空镜禁止人物：main_subject=空镜/环境/远景 时 ff_desc 强写「无一人/无人/空无一人」",
    "K. kf t_sec 范围：0.05 <= t_sec < duration_sec",
    "K2. kf desc 自我完备：禁止占位模板",
    "M. 业务必填字段：core_keywords/fx_effect/clip_transition/context_bridge 不可省",
    "R1. 对白纯净：narration_segment 只写对话原文",
    "R2. 物件尺寸锚点：写尺寸+材质+结构",
    "R3. 双人对话同侧锁：禁止过肩跳轴",
    "R4. 钩子首帧 pre-state only",
    "R5. 物件空间锚点：保留道具空间位置",
    "R6. 禁止多余字段：严格 schema，多余字段直接丢弃",
    "1. 张力递进：高潮必入 80% 时段",
    "2. 反派/打斗/张力 4 段递进",
    "3. 强情/柔情节奏链：强情 4 段递进 + 柔情 2 段微推进",
    "4. 词库规范化：机位/景别/转场/借喻/句式规范",
    "5. 末镜下一段衔接钩子",
    "6. frame_layouts 6 字段全填",
    "7. prompt 末尾强制追加「画面稳定不闪烁、无重影、无伪影、模态自然、节奏平滑。」",
]

def _format_warnings_for_doubao(warnings):
    """把 validation warnings 列表格式化成 doubao 偏好的短句清单."""
    if not warnings:
        return ""
    lines = []
    for i, w in enumerate(warnings, 1):
        if isinstance(w, dict):
            msg = w.get("message") or w.get("detail") or str(w)
            shot = w.get("shot_index") or w.get("index") or w.get("shot") or "-"
            lines.append("%d. [shot %s] %s" % (i, shot, msg))
        else:
            lines.append("%d. %s" % (i, w))
    return "\n".join(lines)


def build_doubao_repair_prompt(shots, warnings, *, novel_text, bank=None, objects_bank=None, max_chars=1600):
    """构造 doubao 修复轮 prompt（对应 deepseek _repair_shot_warnings 调用）."""
    base_prompt = build_doubao_prompt(
        novel_text=novel_text,
        bank=bank,
        objects_bank=objects_bank,
    )
    warning_block = _format_warnings_for_doubao(warnings)
    rule_block = "\n".join("  - " + ln for ln in _DOUBAO_HARD_RULE_LINES)
    repair_block = (

"""

### 修复任务（doubao 修复轮）
以上 shots 经校验发现以下问题：
%s

请按以下硬规则逐条检查并修复（**只修改 shots 数组中违反规则的字段**）：
%s

### 修复约束
- 最小修改原则：只修违反规则的具体字段，未违反的字段一律不动
- 保持 scene/character 锚点：不重命名 scene/character 锚点
- narration_segment 限对话原文：R1 强约束，无对白的镜留空字符串
- 业务字段必填：core_keywords/fx_effect/clip_transition/context_bridge 缺一必补
- kf t_sec 必校验：0.05 <= t_sec < duration_sec
- 末镜 lf_desc 必写未完成态：危机/悬念/争夺/对望/行动中任一
- frame_layouts 6 字段必填：name/on_screen/position/pose/gaze_target/props
- 严格 schema 字段：禁止添加 schema 外的字段

### 输出
**只输出修复后的完整 JSON**（title + characters + shots + hook 可选），禁止任何额外文本。
"""
    ) % (warning_block or "(无警告，但请按硬规则再扫一遍)", rule_block)
    return base_prompt + repair_block

def build_doubao_gap_regen_prompt(validation_result, novel_text, *, bank=None, objects_bank=None, iron_rules="", checklist_feedback="", max_chars=1600):
    """构造 doubao GAP 重生 prompt（对应 main.py:1551-1634 GAP 重生调用）."""
    gap_lines = []
    if isinstance(validation_result, dict):
        gap_lines.append("- overall_pass: %s" % validation_result.get("overall_pass", "?"))
        for k in ("coverage", "density", "climax", "conflict", "pacing", "gap_score"):
            v = validation_result.get(k)
            if v is not None:
                gap_lines.append("- %s: %s" % (k, v))
        issues = validation_result.get("issues") or validation_result.get("problems") or []
        for i, iss in enumerate(issues, 1):
            if isinstance(iss, dict):
                gap_lines.append(
                    "- issue%d: %s: %s"
                    % (i, iss.get("type", iss.get("rule", "?")),
                       iss.get("message", iss.get("detail", iss)))
                )
            else:
                gap_lines.append("- issue%d: %s" % (i, iss))
    gap_summary = "\n".join(gap_lines) or "(无 GAP 报告)"

    base_prompt = build_doubao_prompt(
        novel_text=novel_text,
        bank=bank,
        objects_bank=objects_bank,
    )

    append = ""
    if iron_rules:
        append += "\n" + iron_rules
    if checklist_feedback:
        append += "\n" + checklist_feedback
    append += f"""

### GAP 重生约束（doubao 路径）
请按以下 GAP 报告**重新生成整段分镜**（不要逐镜 patch），重点修以下维度：
{gap_summary}

#### doubao 重点 GAP 修复指令
- 覆盖率（coverage）：core_keywords 命中率 >=60%，必命中高潮/冲突/反派/核心动作词
- 冲突密度（density）：{MIN_TOTAL_SHOTS_DURATION:g}-{_REDGUO_DURATION_MAX:g}s 内 >=2 个冲突节拍
- 高潮分布（climax）：climax kf 必须在 50-90% 时段，禁止全在前 50%
- 节奏（pacing）：3-5s 短镜与 {MAX_SHOT_DURATION - 2:g}-{MAX_SHOT_DURATION:g}s 长镜交错，禁止连续 3 镜同长
- gap_score：必须 >=92 才视为通过

#### 保留硬约束
- schema 严格：禁止额外字段
- kf t_sec 必校验
- narration_segment 限对话原文
- 业务字段必填
- 末镜未完成态

### 输出
**只输出完整 JSON**，禁止任何额外文本。
"""
    return base_prompt + append


def build_doubao_regen_over_limit_prompt(novel_text, *, bank=None, objects_bank=None,
                                         shot_count_min: int | None = None,
                                         shot_count_max: int | None = None):
    """构造 doubao 镜数超限重生 prompt（对应 deepseek _regen_over_limit 调用）.

    shot_count_min/max 缺省取 shared.config 护栏（SHOT_COUNT_MIN/MAX），与
    doubao_pipeline 实参同源，签名默认值不再独立维护一份 8/20。
    """
    shot_count_min = SHOT_COUNT_MIN if shot_count_min is None else shot_count_min
    shot_count_max = SHOT_COUNT_MAX if shot_count_max is None else shot_count_max
    base_prompt = build_doubao_prompt(
        novel_text=novel_text,
        bank=bank,
        objects_bank=objects_bank,
    )
    regen_block = f"""

### 镜数超限重生任务（doubao 路径）
请重新生成分镜，**严格控制{_shot_count_constraint_txt(shot_count_min, shot_count_max)}**，
同时保证：
- 总时长 {MIN_TOTAL_SHOTS_DURATION:g}-{_REDGUO_DURATION_MAX:g}s
- 高潮必入 80% 时段
- core_keywords 命中率 >=60%
- 业务字段必填

### 输出
**只输出完整 JSON**，禁止任何额外文本。
"""
    return base_prompt + regen_block