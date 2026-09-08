"""dialogue_density — 对白镜占比通用兜底（≥ 30%）.

通用化（v6.62.2）：R3 兜底——对白判定仅看 narration_segment 是否含
通用对话关键词（道/说/答/问/喝/喊/喊/诉/叫）或「：」中文冒号，**完全通用**——
不绑小说角色名/具体台词。换小说自动跟随。
"""
from __future__ import annotations

import re

from .base import Constraint
from .types import RepairRecord, Severity


_DIALOGUE_HINTS = ("道", "说", "答", "问", "喝", "喊", "诉", "叫", "叹", "笑", "怒", "骂", "言", "自语")
# 说话人标签内不应出现的断句标点（出现即说明冒号前是整句叙述而非说话人名）
_HEAD_STOP_CHARS = "。！？；，、…·"
# 非说话人的结构性标签词：这些是**流水线元数据字段名**（时间/地点/景别…），
# 不是任何小说的角色称谓 —— 通用，换小说无需改动。命中即判定「标签：值」而非对白。
_NON_SPEAKER_LABELS = (
    "时间", "地点", "场景", "景别", "机位", "运镜", "画面", "镜头", "光位",
    "注意", "备注", "提示", "说明", "旁白", "字幕", "音效", "背景音", "台词",
)


def _has_dialogue(narration: str) -> bool:
    """判断 narration_segment 是否承载对白（通用，不认任何角色名）.

    v6.62.3（09-08）修漏判：旧实现要求冒号前的说话人标签**必须含对话动词**
    （道/说/答…），于是「肖管事：抱歉的很，我家少爷…」这类**纯人名+冒号**的标准
    落库形态被判成非对白（09-08 实测 2/11 报成 0/11，比例结论对但数字错）。
    「说话人：台词」本就是本项目对白落库约定（见 doubao B.1 兜底「某：……」占位），
    人名不带动词是常态，故改为只要求标签短且不含断句标点。
    """
    if not narration or not isinstance(narration, str):
        return False
    s = narration.strip()
    if not s:
        return False
    # 形态 1：「说话人：台词」——标签 ≤6 字且无断句标点，冒号后有内容
    m = re.match(r"^([^：:]{1,6})[：:](.+)$", s, flags=re.S)
    if m:
        head, body = m.group(1), m.group(2).strip()
        if body and not any(c in head for c in _HEAD_STOP_CHARS) \
                and not any(lb in head for lb in _NON_SPEAKER_LABELS):
            return True
    # 形态 2：引号台词（「…」/“…”）
    if ("「" in s and "」" in s) or ("“" in s and "”" in s):
        return True
    # 形态 3：长标签带对话动词（如「张十三厉声问道：凶手是谁」，标签 7 字超形态 1 上限）
    return any(h in s for h in _DIALOGUE_HINTS) and ("：" in s or ":" in s)


class DialogueDensityConstraint(Constraint):
    """对白镜占比 ≥ 30% 通用兜底（warning-only）.

    设计原则：
    - 检测即报，不强行改写（改写对白会破坏剧情连续性）
    - 不绑小说角色名/具体台词（关键词列表完全通用）
    - 例外：hook 分镜（短开场，无对白要求）跳过
    """

    name = "dialogue_density"
    severity = Severity.WARN
    fields = ("narration_segment",)
    skip_hook = True

    _MIN_RATIO = 0.30  # ≥ 30%

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or len(shots) < 4:  # 镜头数太少不强制（短章节）
            return repairs
        if not all(isinstance(shot, dict) for shot in shots):
            return repairs

        n_diag = sum(1 for sh in shots if _has_dialogue(sh.get("narration_segment", "")))
        ratio = n_diag / len(shots)
        if ratio >= self._MIN_RATIO:
            return repairs

        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=None,
            action="warn",
            target="narration_segment",
            detail=(
                f"对白镜仅 {n_diag}/{len(shots)} = {ratio:.0%}（要求 ≥ {self._MIN_RATIO:.0%}）。"
                f"短剧应有 ≥ 30% 镜含对白/质问/对话以推进剧情；当前偏视觉静默。"
            ),
        ))
        return repairs
