"""F7. 克制情绪时间链硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处克制情绪告警：
  镜头 9 克制情绪镜头微表情不足（仅 1 拍），应形成时间链：
  低头隐忍（抿唇/垂眼）→ 泪光（噙泪）→ 苦笑/释怀，每拍一个面部细节，
  按时间先后写全。

修法：扫每镜 prompt/motion/ff/lf，若含克制情绪词（隐忍/失落/哽咽/噙泪
/告别/隐痛/不舍）但 motion_desc 微表情拍数 < 3 → 追加 3 拍时间链模板。

severity=WARN: 已有确定性改写.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

# 克制情绪词（与 validation._check_restrained_emotion 同源）
_RESTRAINED_EMOTION_WORDS = (
    "隐忍", "噙泪", "隐痛", "不舍", "哽咽", "失落", "酸涩",
    "告别", "心酸", "隐忧", "怅然", "黯然", "静默", "压抑",
    "沉郁", "心酸难言", "欲言又止", "难掩", "强忍",
)

# 微表情面部细节词（命中即算 1 拍）
_MICRO_EXPRESSION_WORDS = (
    "抿唇", "垂眼", "扬眉", "皱眉", "眉峰", "眼帘", "睫毛",
    "眼尾", "眼眶", "噙泪", "泪光", "泪痕", "鼻翼",
    "唇角", "下唇", "咬唇", "下颌", "颧骨", "脸颊", "面颊",
    "苦笑", "释怀", "微笑", "嘴角", "喉结", "吞咽",
    "强笑", "压抑的笑", "淡淡的",
)

# 3 拍时间链模板
_RESTRICTED_CHAIN_TEMPLATE = (
    "微表情时间链：①低头隐忍（抿唇/垂眼）→ ②泪光（噙泪/眼眶泛红）→ "
    "③苦笑/释怀（唇角微扬/下颌放松），每拍 1 个面部细节按时间先后写全"
)


def _has_restrained_emotion(text: str) -> bool:
    return any(w in text for w in _RESTRAINED_EMOTION_WORDS)


def _count_micro_expression_pats(text: str) -> int:
    """统计 motion_desc 中含多少个独立微表情词（去重）."""
    seen: set[str] = set()
    for w in _MICRO_EXPRESSION_WORDS:
        if w in text:
            seen.add(w)
    return len(seen)


class RestrainedEmotionChainConstraint(Constraint):
    """F7. 克制情绪时间链：拍数 < 3 → 追加 3 拍时间链模板."""

    name = "restrained_emotion_chain"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            text = " ".join(
                str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc")
            )
            if not _has_restrained_emotion(text):
                continue
            motion = str(shot.get("motion_desc", "") or "")
            count = _count_micro_expression_pats(motion)
            if count >= 3:
                continue  # 已有 ≥3 拍
            # 幂等：已含模板视为已修
            if _RESTRICTED_CHAIN_TEMPLATE in motion:
                continue
            # 追加到 motion_desc
            new_motion = (
                motion.rstrip("。.，,;；") + "。" + _RESTRICTED_CHAIN_TEMPLATE
                if motion.strip()
                else _RESTRICTED_CHAIN_TEMPLATE
            )
            shot["motion_desc"] = new_motion
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index") if not is_hook else None,
                    action="append",
                    target="motion_desc",
                    before=motion[:60] + ("..." if len(motion) > 60 else ""),
                    after=new_motion[:60] + ("..." if len(new_motion) > 60 else ""),
                )
            )
        return repairs
