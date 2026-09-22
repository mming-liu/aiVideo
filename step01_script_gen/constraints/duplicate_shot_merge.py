"""相邻镜重复视觉检测（2026-09-12 引入「HARD 合并」；v6.79 降为只报告）.

复盘实锤（2026-09-12）：镜12/12.5 的 ff/lf/motion 文本逐字相同（同一句结巴
台词被拆成两镜、视觉零差异），占用 12.8s / 全片 21% 的时长凑镜。

## v6.79（2026-09-20）为什么删除自动合并

原实现对同 location、说话人兼容、`motion_desc + prompt + lf_desc` 字符 bigram
Jaccard ≥ 0.85 的相邻镜对执行**就地合并**：duration 求和、narration 拼接、
`lf_desc` 取后镜、前镜全部 `keyframes[*].t_sec` 按 `new/old` 比例缩放。

两处根因使它不能继续存在（见
`docs/step01-temporal-writers-implementation-review-2026-09-19.md` 发现 2）：

1. **Jaccard 相似 ≠ 事件等价**。长篇共同的场景、服装、构图文本会淹没很短但
   关键的结果差异——「刀尖停在胸前」vs「收刀放人」补上相同长环境锚后实测
   相似度 0.8872，照样进入自动合并。合并保留前镜 motion 却取后镜 lf，于是
   变成「动作仍是刀尖停在胸前，尾帧却是收刀放人」，后镜的关键事件与 KF 被
   静默删除——没有任何事件级证据证明它是冗余内容。
2. **按比例缩放 KF 是假设，不是事件证据**。4 秒镜 `0-2 举刀；2-4 收刀` +
   KF `[1秒, 3秒]` 与相似镜合并成 8 秒后，KF 被摊成 `[2, 6]`：`derive_temporal_plan()`
   得到 `0-2 举刀 / 2-4 收刀 / 4-8 空白尾段` 三个窗口，`t=6` 落进**没有正文的
   隐式尾段**——"收刀"KF 被从它自己所属的事件里挪走了。代码既没有同步
   `motion_desc`，也没有保留后镜事件，更没有验证缩放后的事件覆盖。

## 现在的契约

本约束**只检测、不改写**：不再删除镜头、不拼接 narration、不求和 duration、
不缩放 KF。检出即记 HARD 违例（`repaired=False`），由 LLM 走完整镜头 regen
重写其中一镜。

`shared/quality_gate.py` 的 gate 侧同时上报同一条缺陷
（`quality_issues._adjacent_duplicate_issues`，blocking）——两处共用本模块的
`find_adjacent_duplicate_pairs()`，只有一套判定。

未来的受控去重必须同时证明三件事才允许：两个镜头的事件 ID 集合、原文证据
（`temporal_plan.events[].source_quote`）、角色/道具状态转换均等价。届时应生成
**一个新的完整镜头**（新 ff/motion/KF/lf/frame layouts/duration 一次性提交），
禁止复用「前镜 motion + 后镜 lf + 前镜 KF」的字段拼盘。

幂等：纯检测，无副作用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.split_similarity import jaccard_bigram, shot_action_text

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 相邻镜动作文本 Jaccard 阈值（A7 WARN 阈值 0.7，重复视觉判定要更高置信）
_MERGE_SIM = 0.85


_SPEAKER_SUFFIX_RE = re.compile(r"(厉声|颤声|沉声|低声|大声)?(喝道|问道|答道|喊道|说道|道|说)$")


def _speaker(text: str) -> str:
    """「说话人：台词」前缀提取；「听香道」归一为「听香」（口误变体会骗过同说话人判定）."""
    text = (text or "").strip()
    if "：" not in text:
        return ""
    spk = text.split("：", 1)[0].strip()
    return _SPEAKER_SUFFIX_RE.sub("", spk).strip()


def _same_location_family(shot_a: dict, shot_b: dict) -> bool:
    """同地点族判定（与 char_side_continuity 同口径）：location 精确/包含/模糊
    匹配（scene_matches_location ≥2 字公共子串）或 scene 字段相等."""
    from shared.assets_bank import scene_matches_location
    la = (shot_a.get("location") or "").strip()
    lb = (shot_b.get("location") or "").strip()
    if la and lb and (la == lb or la in lb or lb in la
                      or scene_matches_location(la, lb)
                      or scene_matches_location(lb, la)):
        return True
    sa = (shot_a.get("scene") or "").strip()
    sb = (shot_b.get("scene") or "").strip()
    return bool(sa and sb and sa == sb)


def _is_split_shot(shot: dict) -> bool:
    try:
        index = float(shot.get("index"))
        if abs(index - round(index)) >= 1e-9:
            return True
    except (TypeError, ValueError):
        pass
    return bool(shot.get("_split_from"))


def find_adjacent_duplicate_pairs(shots: list) -> list[tuple[int, int, float]]:
    """找出视觉重复的相邻镜对 → `[(i, i+1, similarity), ...]`.

    **全仓唯一判定**：Constraint 层（本模块的报告）与 gate 层
    （`quality_issues._adjacent_duplicate_issues`）共用它，避免同一件事两套阈值。

    条件（三者同时满足才计入）：
      - 非拆镜子镜（半整数 index / `_split_from`）
      - 同地点族
      - 说话人相同或一方无台词（不同人的对白不能缝进同一镜）
      - 动作文本 Jaccard ≥ `_MERGE_SIM`
    """
    out: list[tuple[int, int, float]] = []
    if not isinstance(shots, list) or len(shots) < 2:
        return out
    for i in range(len(shots) - 1):
        a, b = shots[i], shots[i + 1]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        if _is_split_shot(a) or _is_split_shot(b):
            continue
        if not _same_location_family(a, b):
            continue
        sa, sb = _speaker(a.get("narration_segment", "")), _speaker(b.get("narration_segment", ""))
        if sa and sb and sa != sb:
            continue
        sim = jaccard_bigram(shot_action_text(a), shot_action_text(b))
        if sim < _MERGE_SIM:
            continue
        out.append((i, i + 1, sim))
    return out


class DuplicateShotMergeConstraint(Constraint):
    """相邻镜重复视觉检测（治同 visual 凑镜拖时长）——只报告，不改写。

    类名保留 `...Merge`（历史名 + `CONSTRAINT_DUPLICATE_SHOT_MERGE_ENABLED`
    开关名兼容）；行为已不再是合并。改名会同时改动注册表、rules_index、
    feature flag 三处，收益不抵风险，故保留名字并在此注明。
    """

    name = "duplicate_shot_merge"
    severity = Severity.HARD
    fields = ()
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.script.get("shots", [])
        if not isinstance(shots, list) or len(shots) < 2:
            return repairs

        for i, j, sim in find_adjacent_duplicate_pairs(shots):
            a, b = shots[i], shots[j]
            idx_a, idx_b = a.get("index"), b.get("index")
            detail = (
                f"相邻镜 {idx_a}/{idx_b} 动作文本相似度 {sim:.2f} ≥ {_MERGE_SIM} 且同地点同说话人，"
                "视觉重复；本约束不再自动合并（删除后镜会连带删除其事件与关键帧，"
                "按比例缩放前镜 KF 也无法证明事件归属）——需 LLM 完整重写其中一镜"
            )
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                shot_index=idx_b,
                field="motion_desc",
                message=detail,
                evidence=f"shot_a={idx_a} shot_b={idx_b} jaccard={sim:.4f}",
                repaired=False,
            ))
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx_b,
                action="detect",
                target=f"shot_{idx_a}",
                before=f"相邻镜动作文本重复（{idx_a}/{idx_b} 同 visual, sim={sim:.2f}）",
                after="(HARD, 未改写；交完整镜头 regen 重写其中一镜)",
            ))
        return repairs
