"""F3. ff_desc 与 motion_desc 动作重叠硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处首帧时序违例（镜头 6）：
  - ff_desc「背对画面后仰翻入水中」
  - motion_desc「刺客倒纵入水消失不见…」
  → 动作完全重叠，视频模型会把动作做两遍。

现有 hard_fix 路径走 style_prefix.normalize_ff_desc（line 4358），
但新 Constraint 框架（29 条）未集成该函数，导致 hard_fix 实际未跑
而 check 仍 WARN。

修法：扫描所有 shot+hook 的 ff_desc，若 detect_ff_multi_action 报
「pre_action / multi_action / time_advance」中任一信号 → 调
normalize_ff_desc 重写 ff_desc。validate 重扫时看到的 ff_desc 已修，
首帧时序告警消失。

幂等：normalize_ff_desc 内部已用「已含前一刻词（即将/潜伏/欲）则跳过」
防护。

severity=WARN（不阻断；与 kf_pre_post_conflict 同级）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


class FfMotionOverlapConstraint(Constraint):
    """F3. ff_desc 与 motion_desc 动作重叠 → 调 normalize_ff_desc 重写."""

    name = "ff_motion_overlap"
    severity = Severity.WARN
    fields = ()  # 只动 ff_desc

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        # 惰性导入：style_prefix 是顶层模块，懒加载避免循环依赖
        from style_prefix import detect_ff_multi_action, normalize_ff_desc

        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            ff = shot.get("ff_desc")
            if not isinstance(ff, str) or not ff.strip():
                continue
            # 信号检测：detect_ff_multi_action 返回 list，empty = 无问题
            signals = detect_ff_multi_action(ff, None)
            if not signals:
                continue
            # 已在前一刻（即将/潜伏/欲/未）→ 跳过（幂等）
            if any(marker in ff for marker in ("即将", "潜伏", "欲呼", "尚未", "未落", "未刺")):
                continue
            new_ff = normalize_ff_desc(ff, shot, ctx.characters_bank)
            if new_ff == ff or not new_ff:
                continue
            shot["ff_desc"] = new_ff
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index") if not is_hook else None,
                    action="rewrite",
                    target="ff_desc",
                    before=ff[:80] + ("..." if len(ff) > 80 else ""),
                    after=new_ff[:80] + ("..." if len(new_ff) > 80 else ""),
                )
            )
        return repairs
