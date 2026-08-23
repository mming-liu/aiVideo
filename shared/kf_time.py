"""kf 字段统一读写（v6.34 stage + t_sec 迁移落地点，08-22）.

## 背景

v6.34 把 kf 的「时间」从 `time_ratio`（0-1 比例）迁移到 `t_sec`（直写秒数）+
`stage`（6 值 enum），避免 LLM 不会心算 `time_ratio × duration_sec`。

**但 2026-08-22 step01 跑批发现**：v6.34 迁移只完成了 LLM 侧（prompt.md /
main.md / hook.md 已要求 LLM 输出新字段），校验/硬修复代码仍读 `time_ratio`——
LLM 给的全是 `stage + t_sec`，`time_ratio` 不存在 → 硬修复层全部 `0 帧修复`
（filter `0 < tr < 1` 全过不去），但报告「kf 时段缺段 0 帧」无法区分是真没问题
还是校验层没读到数据。

**helper 单一事实源**：所有 16 处旧 `time_ratio` 调用点全部走本模块。
新增 kf 字段读写都走本模块，禁止直接 `kf.get("t_sec")` / `kf["time_ratio"] = 0.5`。

## 6 值 stage 词表

| 值 | 语义 | 典型 desc 词 | 禁用词 |
|---|---|---|---|
| `pre` | 动作前一刻 | 潜伏、微澜、未察觉、盘坐、静止、暗流 | 已刺入、洇痕、倒地、消失 |
| `approach` | 动作启动 | 破水、跃出、逼近、举刀、刀尖抵近、起身 | 已刺入、已倒、洇痕扩大 |
| `climax` | 接触瞬间 | 刺入、劈落、相击、砸下、此刻、溅起 | 已没入、已消失、恢复平静 |
| `react` | 受击方反应 | 闷哼、后仰、捂嘴、惊呼、初现洇痕、刚察觉 | 潜伏、未察觉、水面平静 |
| `exit` | 施动者退场 | 倒纵入水、没入、退入、消失、离去 | 逼近、举刀、破水而出 |
| `after` | 最终结果态 | 已倒、仰卧、洇痕扩大、只剩涟漪、余波 | 破水、举刀、正在刺 |

## 惰性重标定

LLM 直写 `t_sec` 时是基于"当时预估的 duration"，但落盘后 `_compute_timecodes`
会按模型 cap + fps 对齐重新计算 `duration_sec`，可能与原 duration 不同。

读侧 `get_kf_t_sec(kf, shot)` 做惰性重标定：
  `effective_t = t_sec × (duration_sec / duration)`
保持"t_sec 在原始 duration 中的语义位置"。

旧 chapter 走 `time_ratio × duration_sec` fallback（向后兼容 08-22 之前数据）。
"""
from __future__ import annotations

# ============================================================================
# 6 值 stage 枚举（与 prompts/system.md 规则 60.8 同源）
# ============================================================================
KF_STAGE_ENUM = ("pre", "approach", "climax", "react", "exit", "after")

# ============================================================================
# 6 值 stage 描述关键词（与 _check_kf_temporal_consistency 既有词表对齐）
# ============================================================================
KF_STAGE_TYPICAL_WORDS = {
    "pre": ("潜伏", "微澜", "未察觉", "盘坐", "静止", "暗流", "静坐", "未动"),
    "approach": ("破水", "跃出", "逼近", "举刀", "刀尖抵近", "起身", "跃起", "探出"),
    "climax": ("刺入", "劈落", "相击", "砸下", "此刻", "溅起", "相撞", "相搏"),
    "react": ("闷哼", "后仰", "捂嘴", "惊呼", "初现洇痕", "刚察觉", "初倒", "后倾"),
    "exit": ("倒纵入水", "没入", "退入", "消失", "离去", "退场", "遁水", "离去"),
    "after": ("已倒", "仰卧", "洇痕扩大", "只剩涟漪", "余波", "已没入", "已无", "静伏"),
}

# 词表冲突矩阵：stage → 禁用词（命中 = stage-desc 矛盾）
KF_STAGE_FORBIDDEN_WORDS = {
    "pre": ("已刺入", "洇痕", "倒地", "消失", "已倒", "已没入"),
    "approach": ("已刺入", "已倒", "洇痕扩大", "已没入"),
    "climax": ("已没入", "已消失", "恢复平静", "只剩涟漪"),
    "react": ("潜伏", "未察觉", "水面平静", "静坐"),
    "exit": ("逼近", "举刀", "破水而出", "正在刺"),
    "after": ("破水", "举刀", "正在刺", "即将刺"),
}


# ============================================================================
# 字段读写（强制走 helper）
# ============================================================================
def get_kf_t_sec(kf: dict, shot: dict) -> float | None:
    """读 kf 物理时刻（秒数），优先 `t_sec`，fallback `time_ratio`。

    惰性重标定：若 shot 同时有 `duration` + `duration_sec`（timecode 重算后），
    则 `effective_t = t_sec × (duration_sec / duration)`，保持 LLM 原意。

    Args:
        kf: kf dict（必须有 `desc` / `t_sec` / `time_ratio` 之一）
        shot: 所属 shot dict（提供 `duration` / `duration_sec`）

    Returns:
        物理秒数（float），或 None（字段缺失或无法解析）。
    """
    if not isinstance(kf, dict):
        return None
    # 1. 优先 t_sec
    raw_t = kf.get("t_sec")
    if raw_t is not None:
        try:
            t = float(raw_t)
        except (TypeError, ValueError):
            return None
        # 惰性重标定
        new_dur = shot.get("duration_sec") if isinstance(shot, dict) else None
        old_dur = shot.get("duration") if isinstance(shot, dict) else None
        if new_dur and old_dur:
            try:
                ratio = float(new_dur) / float(old_dur) if float(old_dur) else 1.0
                if abs(ratio - 1.0) > 0.01:  # 显著变化才重标定
                    t = t * ratio
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        return t
    # 2. Fallback time_ratio × duration_sec
    raw_tr = kf.get("time_ratio")
    if raw_tr is not None:
        try:
            tr = float(raw_tr)
        except (TypeError, ValueError):
            return None
        dur = None
        if isinstance(shot, dict):
            for k in ("duration_sec", "duration"):
                v = shot.get(k)
                if v is not None:
                    try:
                        dur = float(v)
                        break
                    except (TypeError, ValueError):
                        continue
        if dur:
            return tr * dur
        return None
    return None


def get_kf_stage(kf: dict) -> str | None:
    """读 stage 字段，必须是 6 值之一。

    Returns:
        6 值之一，或 None（缺失/非法）。
    """
    if not isinstance(kf, dict):
        return None
    s = kf.get("stage")
    if not isinstance(s, str):
        return None
    s = s.strip().lower()
    return s if s in KF_STAGE_ENUM else None


def set_kf_t_sec(kf: dict, t_sec: float) -> None:
    """写 t_sec 字段，并删除 time_ratio（schema strict 不可并存）。

    Args:
        kf: kf dict（in-place 修改）
        t_sec: 秒数（0 < t_sec < duration_sec）
    """
    if not isinstance(kf, dict):
        return
    try:
        kf["t_sec"] = round(float(t_sec), 3)
    except (TypeError, ValueError):
        return
    # 删 time_ratio 避免 schema strict 拒收
    kf.pop("time_ratio", None)


def set_kf_stage(kf: dict, stage: str) -> None:
    """写 stage 字段（in-place），非法值静默跳过。

    Args:
        kf: kf dict
        stage: 6 值之一
    """
    if not isinstance(kf, dict):
        return
    if not isinstance(stage, str):
        return
    s = stage.strip().lower()
    if s in KF_STAGE_ENUM:
        kf["stage"] = s


def is_middle_kf(kf: dict, shot: dict) -> bool:
    """判定 kf 是否为「中间帧」（排除首尾 t=0/t=duration_sec）。

    Returns:
        True = 中间帧（用于 step02a 中间帧图生成、step02b 视频拼接）
    """
    if not isinstance(kf, dict) or not isinstance(shot, dict):
        return False
    t = get_kf_t_sec(kf, shot)
    if t is None or t <= 0:
        return False
    dur = shot.get("duration_sec")
    if not dur:
        dur = shot.get("duration")
    try:
        dur = float(dur) if dur else 0
    except (TypeError, ValueError):
        dur = 0
    if dur <= 0:
        return False
    # 0 < t < dur 严格开区间（首帧/尾帧不算中间帧）
    return 0 < t < dur


def sort_kfs_by_t(kfs: list, shot: dict) -> list:
    """按物理时刻升序排 kfs（in-place）。缺失时刻的 kf 排到末尾。

    Args:
        kfs: kfs list（每个元素是 dict）
        shot: 所属 shot dict（提供 duration_sec）

    Returns:
        排序后的 kfs list（同对象引用）。
    """
    def _sort_key(kf):
        if not isinstance(kf, dict):
            return (1, 0)  # 非法 kf 排到末尾
        t = get_kf_t_sec(kf, shot)
        return (0, t if t is not None else 0)
    kfs.sort(key=_sort_key)
    return kfs


# ============================================================================
# kf 排序时辅助
# ============================================================================
def stage_to_index(stage: str) -> int:
    """stage → 0-5 顺序索引（用于排序时作为次级 key）。
    pre=0, approach=1, climax=2, react=3, exit=4, after=5
    """
    try:
        return KF_STAGE_ENUM.index(stage)
    except ValueError:
        return 99
