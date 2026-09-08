"""W6. 红果时长约束（v6.42 迁移自 _hard_fix_runtime_extend；08-23 加单镜上限）.

08-23 用户规则（本版核心）：
  - **正片分镜总时长（不含钩子）≥ 60s**——钩子时长另算，不计入 60s 下限
  - **单镜时长 ≤ 8s**（MAX_SHOT_DURATION，与视频模型 cap 取严）
  - 钩子时长另算：不受 8s 上限/60s 下限约束（hook 有自己的下限规则，见
    constraints/hook_duration.py）

历史背景：8-22 跑批实测 8 镜总时长 42.25s < 60s 红果下限，末镜 3.04s < 4s 红果下限。

修法：
  0. 单镜 > per_shot_cap（min(模型 cap, 8s)）→ 夹到 cap
  1. 末镜 ≤ 4s → 强制夹到 5-6s
  2. 正片分镜总时长（**不含钩子**）< 60s（红果下限，v6.49 收紧去掉 5s 安全裕度）
     → 循环按比例放大每镜 duration，直到达标或全部顶到 cap
     - 单镜上限不超 per_shot_cap
     - 单轮 per_add 上限 3.0s
  3. 全部顶到 cap 仍 < 60s（镜头数不够：8s × N < 60 即 N < 8）
     → 记 HARD Violation，走 regen 反馈要求**加镜头**（而非继续拉单镜时长）
  4. 不论是否拉伸，timecode 永远和 shot["duration"] 同步（关键修复）

幂等：单镜 ≤ cap 且总时长 ≥ 60s（或 need < 2 的贴线态）且末镜 > 4s → 跳过所有修改。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 08-23 用户规则常量从 timecode.py 单一事实源取（失败时回退同值常量）
try:
    from step01_script_gen.timecode import (
        MAX_SHOT_DURATION as _MAX_SHOT_SEC,
        MIN_TOTAL_SHOTS_DURATION as _REDGUO_MIN_SEC,
    )
except Exception:  # pragma: no cover - 导入失败回退
    _MAX_SHOT_SEC = 8.0
    _REDGUO_MIN_SEC = 60.0

_REDGUO_TARGET_SEC = 60.0   # v6.49（08-28）收紧：红果下限 60s 不加安全裕度——
                                   # 1.41x 拉伸比（原 65s 目标）会把过渡镜拉到 7-8s
                                   # 内容贫瘠；目标 60s 拉伸比 1.30x 更稳。
                                   # 安全裕度改由「末镜强制 ≥ 4s」承担（见 _LAST_SHOT_MIN_SEC）。
_LAST_SHOT_MIN_SEC = 4.0
_LAST_SHOT_TARGET_SEC = 5.0
_PER_ADD_CAP = 3.0
_MAX_STRETCH_PASSES = 8     # 循环拉伸安全阀（正常 1-2 轮收敛）

# v6.62.4（09-08）拉伸比告警阈值（**warning-only，不触发重生成**）。
# 依据：本模块 v6.49 注释已认可「目标 60s → 聚合 1.30x」可接受，故聚合阈值取
# 1.20 做提前预警；单镜取 1.35 抓离群镜。
# 09-08 chapter_001 实测：聚合 1.23x、单镜最高 1.50x（shot10 4.00→6.00），
# 且 7/11 镜 ≥1.25x —— 而「高节奏动作镜不拉伸」保护一次都没生效（该章无动作镜），
# 于是拉伸全部落在静态铺垫镜上，把空镜/群演镜拉得更长。
# 拉伸比高的**根因几乎总是内容不足**（镜头没覆盖原文高潮），正确解法是补镜头
# 而非调拉伸算法，故这里只做可观测性，不改拉伸行为、不记 Violation。
_STRETCH_WARN_TOTAL = 1.20
_STRETCH_WARN_SHOT = 1.35


# 高节奏动作词集（首次调用时构建）
_ACTION_TEMPO_WORDS: frozenset | None = None


def _action_tempo_words() -> frozenset:
    """高节奏动作词（拉伸会变慢动作 → 不参与拉伸分配）.

    复用 validation._COMBAT_KEYWORDS（打斗/刺杀/冲刺等，与 combat_impact 同源）
    + 补充高节奏位移/出击词。构建一次后缓存。
    """
    global _ACTION_TEMPO_WORDS
    if _ACTION_TEMPO_WORDS is None:
        try:
            from step01_script_gen.validation import _COMBAT_KEYWORDS
            base = set(_COMBAT_KEYWORDS)
        except Exception:  # pragma: no cover - 兜底
            base = {"打斗", "战斗", "格斗", "刺杀", "攻击", "砍杀", "挥刀", "拔剑", "跃出", "冲刺"}
        base |= {
            # 高节奏位移 / 出击（拉伸即慢动作）
            "追逐", "奔驰", "奔逃", "疾奔", "疾驰", "狂奔", "飞驰", "冲出", "奔袭",
            "追击", "搏斗", "交手", "破水", "跃上", "猛刺", "挥砍", "扬鞭", "快马", "扑出", "翻身跃",
        }
        _ACTION_TEMPO_WORDS = frozenset(base)
    return _ACTION_TEMPO_WORDS


def _is_high_tempo_shot(shot: dict) -> bool:
    """判定镜是否高节奏动作（拉伸会变慢动作 → 不参与拉伸分配）.

    08-27（P1-2）：W6 拉伸逐镜均匀 +0.9s，打斗/追逐镜被拉成慢动作。改为——
    动作镜不拉，反应/情绪/建立镜集中拉满（延长 = 蓄势悬念），观感自然。
    扫 prompt/motion/ff/lf + narration 双通道（与 combat_impact._is_combat 同款）。
    """
    text = " ".join(
        str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc")
    )
    narr = str(shot.get("narration_segment") or "")
    return any(kw in text or kw in narr for kw in _action_tempo_words())


def _align_frame_duration(raw: float, fps: float = 24.0) -> float:
    """24fps 整数帧对齐 + ≤3 位小数. 失败时回退 round(2)."""
    try:
        from step01_script_gen.timecode import _align_frame_duration as _afd
        return _afd(raw, fps)
    except Exception:
        return round(float(raw), 2)


def _video_duration_cap() -> float:
    try:
        from shared.model_caps import active_video_duration_cap
        return float(active_video_duration_cap() or 12)
    except Exception:
        return 12.0


def _shot_dur(s: dict) -> float:
    try:
        return float(s.get("duration_sec") or s.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_duration(items: list[dict]) -> float:
    total = 0.0
    for s in items:
        if not isinstance(s, dict):
            continue
        total += _shot_dur(s)
    return total


def _sync_timecode(shots: list, hook: dict | None, script: dict | None, fps: float) -> None:
    """timecode 永远和 shot["duration"] 同步（不论是否拉伸都跑）.

    v6.43（08-23）钩子在播放顺序头部：hook.timecode = 0..hook_duration，
    shots 从 hook_duration 开始累加。timeline[] 数组**不再重建**——
    _compute_timecodes 已按 hook-first 建好正确顺序，下游（step04/03/02b）不读
    timeline，W6 拉伸只更新 shot.timecode.start/end 即可，避免重复工作+顺序错乱。
    """
    if script is None:
        return
    # 钩子在头部：先设它的 timecode（0..hook_duration），shots 起始时间累加 hook_duration
    cumulative = 0.0
    hook_dur = 0.0
    if hook and isinstance(hook, dict):
        hook_dur = _shot_dur(hook)
        hook["timecode"] = {
            "start": 0.0,
            "end": round(hook_dur, 2),
        }
        cumulative = hook_dur
    for s in shots:
        if not isinstance(s, dict):
            continue
        d = _shot_dur(s)
        s["timecode"] = {
            "start": round(cumulative, 2),
            "end": round(cumulative + d, 2),
        }
        cumulative += d
    script["total_duration"] = round(cumulative, 2)
    if "total_effective_duration" in script:
        script["total_effective_duration"] = round(cumulative, 2)
    # timeline[] 数组不再重建（_compute_timecodes 已建好 hook-first 顺序；下游不读）


class RuntimeExtendConstraint(Constraint):
    """W6. 红果时长约束：正片分镜（不含钩子）≥ 60s / 单镜 ≤ 8s / 末镜 > 4s.

    severity=HARD: 60s 红果标准是硬约束（structural）。
    skip_hook=True: 钩子时长另算——不进总时长统计、不受 8s 上限约束。
    """

    name = "runtime_extend"
    severity = Severity.HARD
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return repairs

        # 单镜上限 = min(视频模型 cap, 8s 硬限)（08-23 用户规则）
        per_shot_cap = min(_video_duration_cap(), _MAX_SHOT_SEC)
        fps = 24.0
        if isinstance(ctx.script, dict):
            try:
                fps = float(ctx.script.get("fps", 24) or 24)
            except (TypeError, ValueError):
                fps = 24.0

        # 0) 单镜硬上限夹取：> per_shot_cap → 夹到 cap
        for s in ctx.shots:
            if not isinstance(s, dict):
                continue
            d = _shot_dur(s)
            if d > per_shot_cap:
                new_d = _align_frame_duration(per_shot_cap, fps)
                before_dur = s.get("duration_sec")
                s["duration_sec"] = new_d
                s["duration"] = new_d
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index"),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(new_d),
                    )
                )

        # 1) 末镜时长夹取
        last = ctx.shots[-1]
        if isinstance(last, dict):
            last_dur = _shot_dur(last)
            if 0 < last_dur <= _LAST_SHOT_MIN_SEC:
                new_last = _align_frame_duration(
                    min(_LAST_SHOT_TARGET_SEC, per_shot_cap), fps,
                )
                before_dur = last.get("duration_sec")
                last["duration_sec"] = new_last
                last["duration"] = new_last
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=last.get("index", len(ctx.shots) - 1),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(new_last),
                    )
                )

        # 2) 总时长计算——只算正片分镜，钩子时长另算（08-23 用户规则）
        total = _sum_duration(ctx.shots)

        # v6.62.4：拉伸前基线快照，供步骤 3.5 算拉伸比（按列表位置对齐——
        # 拉伸循环不增删镜头，位置稳定；index 可能是 8.5 这类小数不宜作键）
        _pre_stretch_durs = [
            _shot_dur(s) if isinstance(s, dict) else 0.0 for s in ctx.shots
        ]
        _pre_stretch_total = total

        # 3) 总时长不足：循环按比例拉伸，直到达标（60s）或全部顶到 cap
        for _pass in range(_MAX_STRETCH_PASSES):
            if total >= _REDGUO_TARGET_SEC:
                break
            need = _REDGUO_TARGET_SEC - total
            if need < 0.1:
                break  # 贴线态（如 FPS 对齐后 59.9s ≈ 60s）：不再微调
            # v6.49（08-28）：need < 2 的旧 break 太宽，会让 9 镜 5s → 9×6.625=59.625s
            # 卡死在 60s 下限下。改为 < 0.1s，允许多轮微调跨过 60s 阈值。
            extensible: list[dict] = []
            high_tempo: list[dict] = []
            for s in ctx.shots:
                if not isinstance(s, dict):
                    continue
                d = _shot_dur(s)
                if d > 0 and d < per_shot_cap - 1.0:
                    # 08-27（P1-2）智能分配：高节奏动作镜不拉（拉伸=慢动作），
                    # 只拉反应/情绪/建立镜——集中拉满观感自然
                    (high_tempo if _is_high_tempo_shot(s) else extensible).append(s)
            if not extensible:
                # 全是高节奏镜（纯打斗/追逐章）→ 回退拉全部，避免时长落空
                extensible = high_tempo
            if not extensible:
                break  # 全部顶到 cap，无余量
            per_add = min(_PER_ADD_CAP, need / len(extensible))
            # v6.49（08-28）贴线态保护：per_add < 0.2 时 FPS 对齐会吃光增量（如
            # 9×6.625=59.625 卡在 60s 下限下），强制最小 0.5 步长确保跨过阈值。
            if per_add < 0.5 and total >= _REDGUO_TARGET_SEC - 1.0:
                per_add = 0.5
            added_any = False
            for s in extensible:
                cur = _shot_dur(s)
                new_d = min(per_shot_cap, cur + per_add)
                if new_d - cur <= 0.1:
                    continue
                aligned = min(_align_frame_duration(new_d, fps), per_shot_cap)
                if aligned <= cur:
                    continue
                before_dur = s.get("duration_sec")
                s["duration_sec"] = aligned
                s["duration"] = aligned
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index"),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(aligned),
                    )
                )
                added_any = True
            total = _sum_duration(ctx.shots)
            if not added_any:
                break

        # 3.5) v6.62.4（09-08）拉伸比告警 —— warning-only：只产出 action="warn"
        #      的 RepairRecord，**不记 Violation、不触发任何重生成**（regen 由
        #      GAP 零高潮检测负责，见 gap_analysis.analyze_shot_distribution）。
        if _pre_stretch_total > 0:
            _total_ratio = total / _pre_stretch_total
            if _total_ratio > _STRETCH_WARN_TOTAL:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=None,
                        action="warn",
                        target="duration_sec",
                        before=f"{_pre_stretch_total:.2f}",
                        after=f"{total:.2f}",
                        detail=(
                            f"正片整体拉伸 {_total_ratio:.2f}x "
                            f"({_pre_stretch_total:.1f}s → {total:.1f}s) "
                            f"> 告警阈值 {_STRETCH_WARN_TOTAL:.2f}x。"
                            "根因通常是内容不足（镜头未覆盖原文高潮），"
                            "靠拉时长会把静态镜拉得更长；正确解法是补镜头/补动作。"
                        ),
                    )
                )
        for _s, _pre_d in zip(ctx.shots, _pre_stretch_durs):
            if not isinstance(_s, dict) or _pre_d <= 0:
                continue
            _cur_d = _shot_dur(_s)
            if _cur_d <= 0:
                continue
            _shot_ratio = _cur_d / _pre_d
            if _shot_ratio > _STRETCH_WARN_SHOT:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=_s.get("index"),
                        action="warn",
                        target="duration_sec",
                        before=f"{_pre_d:.2f}",
                        after=f"{_cur_d:.2f}",
                        detail=(
                            f"单镜拉伸 {_shot_ratio:.2f}x ({_pre_d:.1f}s → {_cur_d:.1f}s) "
                            f"> 告警阈值 {_STRETCH_WARN_SHOT:.2f}x —— 该镜内容量与时长不匹配，"
                            "建议拆分动作/补反应镜而非拉长单镜。"
                        ),
                    )
                )

        # 4) 全部顶到 cap 仍 < 60s 硬下限 → 镜头数不够，记 HARD Violation 走 regen
        #    （8s × N < 60 即 N < 8：加镜头才能解决，继续拉单镜时长违反 8s 上限）
        if total < _REDGUO_MIN_SEC:
            max_reach = len(ctx.shots) * per_shot_cap
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None,
                    field=None,
                    message=(
                        f"正片分镜总时长 {total:.1f}s < {_REDGUO_MIN_SEC:.0f}s 硬下限（不含钩子，钩子另算）。"
                        f"当前 {len(ctx.shots)} 镜即使全部顶到 {per_shot_cap:.0f}s/镜 也只有 {max_reach:.0f}s——"
                        f"必须**增加镜头数**（拆分关键动作/补反应镜，单镜 ≤ {per_shot_cap:.0f}s），"
                        f"禁止靠拉长单镜时长凑数。"
                    ),
                )
            )

        # 5) timecode 同步（不论是否拉伸）
        _sync_timecode(ctx.shots, ctx.hook, ctx.script if isinstance(ctx.script, dict) else None, fps)
        return repairs
