"""P. prompt 复读机约束（v6.42 迁移自 _hard_fix_prompt_repetition）.

按 4-char 滑动窗口，重复 ≥2 次的短语删第 2 次起。扫 4 字段
（prompt/ff_desc/lf_desc/motion_desc）。

v6.43 修（08-23 跑批审计）：
1. 旧实现 `new_v.replace(token, "", 1)` 删的是**第一次**出现而非第二次——
   滑窗按原文 v 检测、替换却作用于已改写的 new_v，位置错位会在文本上
   打洞（08-23 shot9「神（）（）（）情冷峻」「轻便疾驰」马车整体消失）。
   改为：在原文上收集「第 2 次起」的删除区间，一次性重建结果。
2. 括号保护区：（…）/【…】 内的内容（道具方位锚「（画右岸边）」、
   【角色锚定】块等）不参与去重——prop_spatial_anchor 插入的多份
   「（画右岸边）」曾被滑窗当成复读互删，残留「边）」「（）」碎片。

幂等：删后无重复则跳过；字段 < 8 字符跳过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_OPEN_TO_CLOSE = {"（": "）", "【": "】"}
_CLOSE_SET = set(_OPEN_TO_CLOSE.values())
# 句读分隔：滑窗不跨逗号/句号（同旧版）
_PUNCT_SKIP = ("，", "。")

# 角色名/短语连跑：「杨旭，杨旭，杨旭，杨旭」「张十三，张十三」——
# 4-char 滑窗对含逗号的重复单元天然失明（token 含 ， 即跳过），
# 实测 08-28 chapter_001 单镜最多「杨旭，」×4 混入 prompt/ff/motion。
# 同一 2-4 字 token 紧邻逗号连跑 ≥2 次即折叠为 1 次。
_NAME_RUN_RE = re.compile(r"([\u4e00-\u9fff]{2,4})(?:[，,]\1)+")


def _collapse_name_runs(v: str) -> str:
    """折叠「名，名，名」连跑为单次（修复滑窗对逗号重复的盲区）."""
    return _NAME_RUN_RE.sub(r"\1", v)


def _protected_mask(text: str) -> list[bool]:
    """括号保护掩码：True = 该字符位于 （…）/【…】 跨度内（含括号本身）.

    嵌套括号按最内层配对；未闭合的开括号保护到文末。
    """
    mask = [False] * len(text)
    stack: list[tuple[str, int]] = []
    for i, ch in enumerate(text):
        if ch in _OPEN_TO_CLOSE:
            stack.append((ch, i))
        elif ch in _CLOSE_SET:
            for k in range(len(stack) - 1, -1, -1):
                if _OPEN_TO_CLOSE[stack[k][0]] == ch:
                    _, open_i = stack.pop(k)
                    for j in range(open_i, i + 1):
                        mask[j] = True
                    break
    # 未闭合的开括号：保护到文末
    for _, open_i in stack:
        for j in range(open_i, len(text)):
            mask[j] = True
    return mask


def dedupe_text_4char(v: str) -> str:
    """4-char 滑窗去重：重复 ≥2 次的短语删第 2 次起（保留第一次）.

    - 删除区间在原文坐标上收集，最后一次性重建，避免「检测用原文、
      替换用改后文」的错位打洞。
    - （…）/【…】 跨度内的字符不参与检测（保护道具方位锚/角色锚定块）。
    - 与已有删除区间重叠的 token 跳过（避免半删）。
    """
    if not v or len(v) < 8:
        return v
    v = _collapse_name_runs(v)
    # v6.50：双字叠词折叠（「暗红暗红痕迹」）——其他约束层追加模板也可能撞出
    # 重叠，不只 content_safety 替换一处
    try:
        from shared.content_safety import collapse_doubled_tokens
        v = collapse_doubled_tokens(v)
    except ImportError:
        pass
    mask = _protected_mask(v)
    seen: set[str] = set()
    deletions: list[tuple[int, int]] = []
    for i in range(0, len(v) - 3):
        token = v[i:i + 4]
        if not token.strip() or any(p in token for p in _PUNCT_SKIP):
            continue
        # 保护区内 / 跨括号边界的 token 不参与
        if any(mask[i:i + 4]):
            continue
        if token in seen:
            # 与已删除区间重叠 → 跳过（避免半删产生碎片）
            if any(not (i + 4 <= s or i >= e) for s, e in deletions):
                continue
            deletions.append((i, i + 4))
        else:
            seen.add(token)
    if not deletions:
        return v
    parts: list[str] = []
    prev = 0
    for s, e in sorted(deletions):
        parts.append(v[prev:s])
        prev = e
    parts.append(v[prev:])
    return "".join(parts)


class PromptRepetitionConstraint(Constraint):
    """P. 复读机：4-char 短语 ≥2 次 → 删第 2 次起.

    severity=WARN: 复读可由后续 repair round 修.
    无 repair_marker（按字段实际重复检测）.
    """

    name = "prompt_repetition"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # v6.50：从 char_layout 收集本镜角色名，用于悬空名碎片清理
            char_names: list[str] = []
            for it in (shot.get("char_layout") or []):
                if isinstance(it, dict):
                    n = str(it.get("name", "")).strip()
                    if 2 <= len(n) <= 4 and n not in char_names:
                        char_names.append(n)
            for fld in self.fields:
                v = shot.get(fld)
                if not v or not isinstance(v, str) or len(v) < 8:
                    continue
                new_v = dedupe_text_4char(v)
                new_v = self._strip_dangling_names(new_v, char_names)
                if new_v != v:
                    shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs

    @staticmethod
    def _strip_dangling_names(text: str, char_names: list[str]) -> str:
        """清理悬空角色名碎片：「云鬓半偏，杨旭；」——逗号 + 角色名 + 子句
        边界标点，名字后无任何谓语（名字连跑折叠后残留的单发注入垃圾）.
        仅对 char_layout 已知角色名生效，限子句尾位置（不误伤句中主语）.
        """
        if not text or not char_names:
            return text
        for n in char_names:
            text = re.sub(rf"，{re.escape(n)}(?=[，。；！？]|$)", "", text)
        return text
