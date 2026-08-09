"""shared.shot_diff - 分镜逐镜 diff（新旧脚本对比，复用未变镜头产物）.

背景（2026-08-09 用户需求）：重跑 step01 后分镜常只增删一两个镜头（数量变化、
总时长 ≤3min），但 step01 清理段会删掉整个 vimax_working、step02 会重新生成
全部帧/视频——白白烧额度。

本模块提供「新旧脚本逐镜内容对比」：
- shot_signature(shot): 镜头内容签名（不含 duration——时长变化不触发重做，
  与 submit_poll._get_shot_hash 的注释一致）
- build_shot_map(script): 旧/新脚本 → {index: signature}
- compute_shot_diff(old_script, new_script): 返回 {unchanged, added, changed, removed}
- should_preserve_artifacts(old, new): 是否满足「仅数量差异 + 总时长≤3min」放行条件

复用方：
- step01 清理段：放行时不删 vimax_working，只删 changed/removed 镜头的产物
- step02 生成段：对 unchanged 镜头直接 cache hit 跳过

签名字段刻意排除 duration/duration_sec：分镜数量变时 timecode 重算会改 duration，
但画面内容没变，不应触发重做。
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

# 单镜内容签名字段（排除 duration——数量变化触发重算但不改画面内容）
_SIGNATURE_FIELDS = (
    "main_subject", "location", "scene",
    "ff_desc", "lf_desc", "motion_desc", "prompt",
    "narration_segment", "subtitle",
    "context_bridge", "fx_effect", "color_tone",
)


def _normalize(text) -> str:
    """规范化文本：strip + 统一空白字符，消除 LLM 格式噪音."""
    t = str(text or "").strip()
    return re.sub(r"\s+", " ", t)


def shot_signature(shot: dict) -> str:
    """计算单镜内容签名（不含时长字段）."""
    parts = []
    for f in _SIGNATURE_FIELDS:
        parts.append(f"{f}={_normalize(shot.get(f, ''))}")
    # keyframes 纳入（帧内容变化）
    for kf in (shot.get("keyframes") or shot.get("intermediate_keyframes") or []):
        if isinstance(kf, dict):
            parts.append(_normalize(kf.get("desc", "")))
            parts.append(str(kf.get("time_ratio", "")))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_shot_map(script: dict) -> dict[int, str]:
    """script → {index: signature}."""
    result = {}
    for shot in script.get("shots", []):
        idx = shot.get("index")
        if idx is not None:
            result[idx] = shot_signature(shot)
    return result


def compute_shot_diff(old_script: dict, new_script: dict) -> dict:
    """对比新旧脚本镜头，返回差异分类.

    Returns:
        {
          "unchanged": [idx, ...],  # 内容签名相同 → 复用产物
          "added":     [idx, ...],  # 新脚本新增镜头 → 重新生成
          "changed":   [idx, ...],  # 同一 index 但内容变了 → 重新生成
          "removed":   [idx, ...],  # 旧脚本有、新脚本无 → 清理产物
        }
    """
    old_map = build_shot_map(old_script)
    new_map = build_shot_map(new_script)

    unchanged, added, changed, removed = [], [], [], []
    for idx, sig in new_map.items():
        if idx in old_map:
            if old_map[idx] == sig:
                unchanged.append(idx)
            else:
                changed.append(idx)
        else:
            added.append(idx)
    for idx in old_map:
        if idx not in new_map:
            removed.append(idx)
    return {
        "unchanged": unchanged,
        "added": added,
        "changed": changed,
        "removed": removed,
    }


# 放行阈值：总时长 ≤3 分钟（180 秒）。用户语义：只有数量差异且短片可复用。
MAX_PRESERVE_DURATION_SEC = 180.0


def should_preserve_artifacts(old_script: dict, new_script: dict,
                              diff: Optional[dict] = None) -> bool:
    """是否满足「仅数量差异 + 总时长≤3min」放行条件.

    放行 = 保留旧产物（vimax_working / 帧 / 视频），只重生成 changed/added 镜头。
    条件：
      1. 新脚本总时长 ≤3min（短片，重新生成成本低；超过则全量重做）
      2. 旧镜头内容多数未变（unchanged 占比高——说明是「微调数量」而非「大改内容」）
      3. 有可复用的旧镜头（unchanged 非空才有保留价值）
    """
    if diff is None:
        diff = compute_shot_diff(old_script, new_script)
    total = float(new_script.get("total_duration", 0) or 0)
    if total > MAX_PRESERVE_DURATION_SEC:
        return False
    unchanged = diff.get("unchanged", [])
    added = diff.get("added", [])
    changed = diff.get("changed", [])
    # 需要重生成的数量
    regen = len(added) + len(changed)
    # 无未变镜头（全新增/全改）→ 无保留价值
    if not unchanged:
        return False
    # 重生成数明显小于未变数 → 保留值得（避免重生成数也很大时仍全量）
    # 宽松条件：重生成 ≤ 未变 × 2（即改动不到旧镜头 2/3）即可放行
    return regen <= len(unchanged) * 2