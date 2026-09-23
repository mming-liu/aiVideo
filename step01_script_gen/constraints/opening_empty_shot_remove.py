"""opening_empty_shot_remove — 开头空镜硬删除（v6.64 P4.3+）.

doubao 经常开头 2-3 镜全是空镜，节奏太慢。
策略：只删真正的"空镜"（main_subject 明确标记为"空镜"），不删有人物的群演/远景镜。

通用化：从 script.characters 取角色名，不硬编码角色。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


def _is_pure_empty(shot: dict) -> bool:
    """判断是否是纯空镜（无人/纯风景）.
    
    判断依据：main_subject 含"空镜"关键词，
    且 ff_desc 不含真实人物描述（排除"无角色/无人"等否定句式）。
    """
    ms = str(shot.get("main_subject", "") or "")
    if "空镜" not in ms:
        return False
    
    # 检查 ff_desc / lf_desc 里有没有真实人物
    # 排除否定句式中的"人/角色"（如"无角色/无人/画面无角色"）
    ff = str(shot.get("ff_desc", "") or "")
    lf = str(shot.get("lf_desc", "") or "")
    desc = ff + " " + lf
    
    # 否定词（后面跟"人/角色"不算有人物）
    neg_prefixes = ["无", "没有", "不出现", "不得出现", "空无", "不包含"]
    has_real_human = False
    
    # 简单策略：找具体人物称谓
    human_terms = [
        "男子", "女子", "人物", "身影", "人影",
        "老者", "青年", "少年", "少女", "孩童",
        "刺客", "侍卫", "士兵", "仆人", "丫鬟",
    ]
    for term in human_terms:
        if term in desc:
            # 检查是否在否定语境中
            idx = desc.find(term)
            prefix = desc[max(0, idx-6):idx]
            if not any(neg in prefix for neg in neg_prefixes):
                has_real_human = True
                break
    
    return not has_real_human


def _total_duration(shots: list) -> float:
    """计算总时长."""
    return sum(float(s.get("duration_sec", 0) or 0) for s in shots if isinstance(s, dict))


class OpeningEmptyShotRemoveConstraint(Constraint):
    """开头空镜**报告**：第 1 镜是纯空镜时报出来（v6.99 起 report-only）.

    检测：如果第 1 镜是纯空镜，且前 3 镜内有角色镜，报告多余的空镜数量与证据。
    **不再删除**：`shots.pop(0)` 会连同 prompt/KF/布局一起丢掉一整个镜头对象，
    且会让后续镜头位置前移、把按位置比较的时间快照全部判成越权写入
    （2026-09-23 二次审核 P0-B + 用户决定 B）。
    """

    name = "opening_empty_shot_remove"
    severity = Severity.INFO
    fields = ("shots",)
    skip_hook = True

    _SCAN_WINDOW = 6
    _MAX_REMOVE = 2
    _MIN_SHOTS_AFTER = 8
    _MIN_DURATION_AFTER = 55.0

    def apply(self, ctx) -> list[RepairRecord]:
        repairs = []
        shots = ctx.shots
        if not isinstance(shots, list) or len(shots) < self._MIN_SHOTS_AFTER:
            return repairs

        # 获取已注册角色名
        script_chars = []
        script = getattr(ctx, "script", None) or {}
        for c in script.get("characters", []) or []:
            if isinstance(c, dict):
                name = c.get("identifier") or c.get("name")
                if name and isinstance(name, str):
                    name = name.strip()
                    if len(name) >= 2:
                        script_chars.append(name)

        if not script_chars:
            return repairs

        # 找第一个包含角色的镜头
        first_char_idx = None
        scan_end = min(self._SCAN_WINDOW, len(shots))
        for i in range(scan_end):
            shot = shots[i]
            if not isinstance(shot, dict):
                continue
            text = " ".join([
                str(shot.get("main_subject", "") or ""),
                str(shot.get("ff_desc", "") or ""),
                str(shot.get("lf_desc", "") or ""),
                str(shot.get("narration_segment", "") or ""),
            ])
            if any(name in text for name in script_chars):
                first_char_idx = i
                break

        # 计算可删除的纯空镜数量（从开头连续删）
        n_remove = 0
        removed = []
        total_dur = _total_duration(shots)

        for _ in range(min(self._MAX_REMOVE, len(shots))):
            if not shots:
                break
            shot = shots[0]
            if not isinstance(shot, dict):
                break
            if not _is_pure_empty(shot):
                break
            # 第一个角色镜就在第2镜，已经够快了
            if first_char_idx is not None and first_char_idx - n_remove <= 1:
                break
            # 时长约束
            shot_dur = float(shot.get("duration_sec", 0) or 0)
            if total_dur - shot_dur < self._MIN_DURATION_AFTER:
                break
            if len(shots) - (n_remove + 1) < self._MIN_SHOTS_AFTER:
                break

            # v6.99（二次审核 P0-B，用户决定**选项 B**）：**只记账，不删除**。
            removed.append(shot.get("main_subject", "")[:20])
            total_dur -= shot_dur
            n_remove += 1

        # ⚠️ 这里**没有** `shots.pop(0)` —— v6.99 起本约束是 report-only。
        #
        # 原实现 `shots.pop(0)` 最多删 2 个完整镜头对象：prompt / ff / lf / motion /
        # KF / 布局 / 姿态 / 道具随 `del` 一起消失，而判据只是「`main_subject` 含
        # 『空镜』且 ff/lf 不含人物称谓」这一条窄文本启发式——**无法证明被删镜
        # 不含独立的环境、道具、姿态、KF 或事件结果**。
        #
        # 而且它在**审计段之后**执行：删首镜 → 后续镜头按列表位置前移 → 时间快照
        # （按位置比较）把每个前移镜头的 duration/motion/KF 都算成「被写过」→
        # 实测「删 1 镜 → 20 条未授权时间写入」，同一动作既得 advisory 又得
        # `time_field_audit_write` HARD。
        #
        # 审核建议 B：把删除诉求交结构 regen（生成完整替代镜头 + 统一候选验收）。
        # 本约束只报告命中的镜头与判空证据。
        if n_remove > 0:
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=1,
                action="warn",
                target="shots",
                before=f"前{n_remove}镜为纯空镜（首镜即空镜）",
                after="**只报告，未删除任何镜头**（v6.99 report-only）",
                detail=(f"开篇 {n_remove} 个纯空镜建议由结构 regen 重新规划："
                        f"{removed}；本约束不再 `shots.pop()`"),
            ))

        return repairs