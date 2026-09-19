"""同地点连续镜角色左右侧翻转 HARD 交换（2026-09-12，95 分稿复盘根治项）.

复盘实锤：镜10 张十三「快步走向画左船头」、听香在画左；镜11 同地点却写成
「听香跪坐画右、张十三面朝画右」——角色屏幕左右侧跨镜翻转（越轴），视频
模型的连续性会崩。既有 axis_jump（W11）查的是相机越轴，char_layout_sync
查的是镜内同步，角色**跨镜侧连续性**没有执行点。

不变量：同地点连续两镜，同一角色的屏幕左右侧不得无故翻转。检出翻转 →
后镜全部文本字段交换「画左↔画右」（镜内空间语法内部自洽，交换后与
前镜连续性恢复；比逐句改写更不易引入新矛盾）。

幂等：交换后两侧一致，约束不再触发。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

_SWAP_FIELDS = ("prompt", "ff_desc", "lf_desc", "motion_desc")

# 角色名附近的侧向词窗口（前后各 30 字，取最近一处）
_SIDE_RE = re.compile(r"画左|画右")


def _char_side(ff_desc: str, name: str) -> str | None:
    """角色在 ff_desc 中的屏幕侧（「画左/画右」，取距角色名最近的一处）."""
    best: tuple[int, str] | None = None
    for m in re.finditer(re.escape(name), ff_desc):
        for s in _SIDE_RE.finditer(ff_desc):
            dist = abs(s.start() - m.end())
            if dist <= 30 and (best is None or dist < best[0]):
                best = (dist, s.group(0))
    return best[1] if best else None


def _swap_sides(text: str) -> str:
    return text.replace("画左", "\x00").replace("画右", "画左").replace("\x00", "画右")


def _same_location_family(shot_a: dict, shot_b: dict) -> bool:
    """同地点族判定：location 精确相等 / 模糊匹配（scene_matches_location，≥2 字
    公共子串）/ scene 字段相等。船梢 vs 船头这类同船部位标签必须算同地点，
    否则跨镜连续性约束整条失效（2026-09-12 镜10→11 漏检实测）。"""
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


class CharSideContinuityConstraint(Constraint):
    """同地点连续镜角色侧翻转 → 后镜整体交换画左/画右."""

    name = "char_side_continuity"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.script.get("shots", [])
        characters = [
            c.get("identifier", "").strip()
            for c in (ctx.script.get("characters") or []) if c.get("identifier")
        ]
        if not isinstance(shots, list) or len(shots) < 2 or not characters:
            return repairs

        for i in range(len(shots) - 1):
            a, b = shots[i], shots[i + 1]
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            if not _same_location_family(a, b):
                continue
            ff_a = a.get("ff_desc") or ""
            ff_b = b.get("ff_desc") or ""
            flip = False
            for name in characters:
                side_a = _char_side(ff_a, name)
                side_b = _char_side(ff_b, name)
                if side_a and side_b and side_a != side_b:
                    flip = True
                    break
            if not flip:
                continue
            # A side flip can be a legitimate camera-axis change, a new
            # head-on framing, or a deliberate crossing.  Wording alone does
            # not prove which side should be authoritative, so keep the
            # authored layout and report the continuity risk.
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.WARN,
                shot_index=b.get("index", i),
                field="ff_desc,char_layout",
                message=(
                    f"同地点相邻镜角色左右侧翻转（镜{a.get('index')}→镜{b.get('index')}）；"
                    "保留原文，禁止自动交换画左/画右。"
                ),
                evidence=f"prev={ff_a[-120:]}; curr={ff_b[-120:]}",
                repaired=False,
            ))
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=b.get("index", i),
                action="warn",
                target="prompt,ff_desc,lf_desc,motion_desc",
                before=f"同地点与镜{a.get('index')}角色侧翻转",
                after=f"同地点与镜{a.get('index')}角色侧翻转",
                detail="角色侧翻转缺少机位证据，保留原文交由 audit",
            ))
        return repairs
