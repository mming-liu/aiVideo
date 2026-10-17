"""F10. ff/lf 时序协议约束（v6.50，08-28 深度分析 P1-3）.

背景（08-26/08-28 hook 实拍图验证）：钩子 ff_desc 被写成动作结束态
（「杨旭仍仰卧不动，胸口匕首依旧，白袍上暗红洇痕扩大」）——首尾两帧几乎
相同（都是刺杀后），图生视频没有动作可插值，「破水刺杀」完全丢失，
4 秒钩子变成静态画面轻微形变。

协议：**ff = 本镜动作起始态，lf = 动作结束态**。当 motion_desc 表明
本镜内发生攻击动作时，ff_desc 不得已是「动作结束态」（洇痕扩大/匕首
依旧/已刺入/刺中后…）——那会把时间轴压扁成「结束后 → 结束后」。

只检测不改写：确定性层无法"无中生有"写出正确的 pre-state 描述
（P25 同款结论），应进修复轮/regen 由 LLM 重写。

severity=WARN；fields=("ff_desc",)
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.prompt_semantic import ATTACK_KEYWORDS as _ATTACK_KW
from .base import Constraint
from .types import RepairRecord, Severity, Violation

# ff_desc 中的「动作已结束」状态词（须与本镜攻击动作同现才判违例）
# ⚠️ 本元组是**违例黑名单**（命中即 record_violation），不是放行白名单。
# 命中语义见 apply()：本镜 motion_desc 含攻击动词 且 ff_desc 命中本表 → 报违例，
# 因为 ff 是攻击**之前**的起始帧，不该已经写出「刺中后/血红洇开」这类结束态。
# 因此**不得删减**任何成员——删一个就少一类漏检。
# v6.x.x（08-30 实抽）：补 4 个「伤锚完整句」形态词——之前词表只覆盖
# 「洇痕扩大/已刺入/匕首依旧」等过渡态词，**没覆盖** hook.ff_desc 里
# 出现的「INJURY_ANCHOR_TAIL 完整句」（伤处白袍 / 血痕清晰可见 / 一片暗红
# 血迹洇开 / 暗红痕迹洇开）。Constraint 之前对这种"伤锚完整句"形态
# 漏判，模型拿到矛盾的 prompt 才自由发挥出"船里灌水/听香在船外"画面。
# 2026-10 复核：补入 `main.py` 旧伤处兜底曾直写的变体「暗红洇痕持续可见」——
# 它与 `INJURY_ANCHOR_TAIL` 是**两个不同字符串**，旧快照与模型幻觉都可能携带，
# 补进黑名单后无论哪个来源写进攻击镜头的 ff_desc 都会被检出。
_POST_STATE_WORDS = (
    "洇痕扩大", "洇开一片", "洇开大片", "已刺入", "刺中后", "匕首依旧",
    "已中刀", "已倒下", "已刺中", "洇痕清晰", "胸口插着", "心口插着",
    "一片暗红血迹洇开", "血痕清晰可见", "伤处白袍", "暗红痕迹洇开",
    "暗红洇痕持续可见",
)


class FfLfTemporalConstraint(Constraint):
    """F10. ff 时序协议：本镜有攻击动作时 ff 不得已是结束态（只报不修）."""

    name = "ff_lf_temporal"
    severity = Severity.WARN
    fields = ("ff_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            motion = shot.get("motion_desc") or ""
            ff = shot.get("ff_desc") or ""
            if not motion or not ff or not isinstance(motion, str) or not isinstance(ff, str):
                continue
            # 仅当本镜 motion 内含攻击动作（攻击发生在本镜时间轴内，
            # ff 是攻击前的起始帧）才约束 ff 状态
            if not any(k in motion for k in _ATTACK_KW):
                continue
            hits = [w for w in _POST_STATE_WORDS if w in ff]
            if not hits:
                continue
            label = "hook" if is_hook else f"shot{shot.get('index', idx)}"
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.WARN,
                    shot_index=None if is_hook else shot.get("index"),
                    field="ff_desc",
                    message=(
                        f"{label} ff_desc 已写成动作结束态（命中：{'、'.join(hits)}）——"
                        f"ff 应为本镜动作起始态，否则首尾帧无动作差、图生视频丢失动作"
                        f"（08-26 钩子匕首提前插胸口即此类）"
                    ),
                )
            )
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=None if is_hook else shot.get("index"),
                    action="flag",
                    target="ff_desc",
                    before="",
                    after=f"post-state words: {hits}",
                )
            )
        return repairs

