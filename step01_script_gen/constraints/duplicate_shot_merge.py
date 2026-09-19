"""相邻镜重复视觉 HARD 合并（2026-09-12，95 分稿复盘根治项）.

复盘实锤：镜12/12.5 的 ff/lf/motion 文本逐字相同（同一句结巴台词被拆成
两镜、视觉零差异），占用 12.8s / 全片 21% 的时长凑镜——打分器照常给 95 分
（已补相邻重复惩罚）。A7 split_content_similarity 只 WARN 不处理，重复稿
会带伤进 step02。

不变量：**相邻镜头必须有可区分的视觉内容**。同 location 且动作文本
Jaccard ≥0.85 的相邻镜对，后镜并入前镜（HARD）：
  - duration 求和；narration/subtitle 拼接（说话人相同或一方为空才合并）
  - ff/motion 取前镜（开场态），lf 取后镜（推进终点）
  - keyframes 保留前镜（重复视觉的后镜 kf 无信息量）

幂等：合并后相邻对不再重复，约束自然不再触发。
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
from .types import RepairRecord, Severity

# 相邻镜动作文本 Jaccard 阈值（A7 WARN 阈值 0.7，合并要更高置信）
_MERGE_SIM = 0.85


_SPEAKER_SUFFIX_RE = re.compile(r"(厉声|颤声|沉声|低声|大声)?(喝道|问道|答道|喊道|说道|道|说)$")


def _speaker(text: str) -> str:
    """「说话人：台词」前缀提取；「听香道」归一为「听香」（口误变体会骗过同说话人判定）."""
    text = (text or "").strip()
    if "：" not in text:
        return ""
    spk = text.split("：", 1)[0].strip()
    return _SPEAKER_SUFFIX_RE.sub("", spk).strip()


def _merge_narration(a: str, b: str) -> str:
    """两镜旁白拼接（结巴断句的原句两半直接相连）."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    return a + b



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


class DuplicateShotMergeConstraint(Constraint):
    """相邻镜重复视觉 HARD 合并（治同 visual 凑镜拖时长）."""

    name = "duplicate_shot_merge"
    severity = Severity.HARD
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.script.get("shots", [])
        if not isinstance(shots, list) or len(shots) < 2:
            return repairs

        merged = True
        while merged:
            merged = False
            for i in range(len(shots) - 1):
                a, b = shots[i], shots[i + 1]
                if not isinstance(a, dict) or not isinstance(b, dict):
                    continue
                if _is_split_shot(a) or _is_split_shot(b):
                    continue
                # 同地点（相等或包含关系）才谈「重复视觉」
                if not _same_location_family(a, b):
                    continue
                # 说话人相同或一方无台词才可拼（不同人的对白不能缝进同一镜）
                sa, sb = _speaker(a.get("narration_segment", "")), _speaker(b.get("narration_segment", ""))
                if sa and sb and sa != sb:
                    continue
                if jaccard_bigram(shot_action_text(a), shot_action_text(b)) < _MERGE_SIM:
                    continue

                # ---- 合并 b → a ----
                a["duration_sec"] = round(
                    float(a.get("duration_sec") or 0) + float(b.get("duration_sec") or 0), 3)
                a["narration_segment"] = _merge_narration(
                    a.get("narration_segment", ""), b.get("narration_segment", ""))
                a["subtitle"] = _merge_narration(
                    a.get("subtitle", ""), b.get("subtitle", ""))
                if b.get("lf_desc"):
                    a["lf_desc"] = b["lf_desc"]  # 推进终点取后镜
                if b.get("motion_desc"):
                    a["motion_desc"] = b["motion_desc"]
                # v6.76（09-19）：**不再** pop("keyframes")。
                #
                # 原注释称「重复视觉的后镜 kf 无信息量，丢弃由下游重派生」——但唯一
                # 的「重派生者」kf_time_window_alignment 已在本版降为只报告，pop 之后
                # 没有任何模块会重建 KF。实测后果比「无 KF」更重：
                #   frame_spec_repair / frame_spec_consistency 都拿 len(keyframes) 当
                #   对齐基准，keyframes 缺失（=0）会把 frame_layouts.kfs 一并弹空 →
                #   该镜在 step02a 一个关键帧图都不生成（实测 kfs: 1 → 0）。
                # 本约束 docstring 第 12 行本就写明「keyframes 保留前镜」，pop 与之矛盾。
                #
                # 保留后 KF 会被自动分摊到合并后的时间轴：本处只写 duration_sec 不写
                # duration（全仓唯一的非对称写入），shared/kf_time 的惰性重标定据此
                # 算出 ratio = 新时长/旧时长 并等比放大 KF 的有效时刻。这是全仓唯一
                # 会触发该重标定的位置——若日后把此处改成对称写入，KF 会退回旧时刻、
                # 尾部变成无覆盖时段（由 kf_time_window_alignment 上报）。
                shots.pop(i + 1)
                merged = True
                repairs.append(RepairRecord(
                    constraint_name=self.name,
                    shot_index=a.get("index"),
                    action="merge",
                    target=f"shot_{b.get('index')}",
                    before=f"相邻镜动作文本重复（{a.get('index')}/{b.get('index')} 同 visual）",
                    after=f"并入镜{a.get('index')}（时长求和，narration 拼接）",
                ))
                break  # 列表已变更，重新扫描

        return repairs
