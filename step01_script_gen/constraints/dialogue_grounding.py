"""F16. 失锚台词硬替换——narration_segment 不在原文中时，用最近的原文台词替换。

问题：doubao 经常编造原文没有的台词（如「神秘人：受死吧」），当前回退保护
只会"回退上一版"，但上一版可能结构更差（零高潮）。回退是两害相权取其轻，
不是真正的修复。

方案：hard_fix 级别的台词 grounding 修复——
1. 逐镜检查 narration_segment 是否在原文中有出处
2. 失锚 → 在该镜对应原文段（按 core_keywords / prompt 内容定位）±200 字内
   找最接近的原文台词替换
3. 找不到 → 置空（静音镜），不编造
4. subtitle 同步更新（与 narration_segment 一致，规则 1 锁定）

与回退保护的关系：
- 回退保护在 GAP 重试层工作（整版粒度）
- 本约束在 hard_fix 层工作（单镜粒度）
- 先 hard_fix 修台词 → 再跑回退保护 → 回退保护就不会因为"台词失锚"而回退
  （因为已经修好了）

severity=WARN: 修复后不触发 regen，硬修即可。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 引号正则（复用 gap_analysis 的模式，避免循环 import）
_DIALOGUE_QUOTE_RE = re.compile(
    r"「([^」]{2,60})」"
    r"|『([^』]{2,60})』"
    r"|\"([^\"]{2,60})\""
    r"|'([^']{2,60})'"
)

# 拟声词/纯感叹（过滤掉，不作有效台词）
_ONOMATOPOEIA = ("啊", "呃", "嗯", "哦", "呀", "哇", "哼", "呵")


def _extract_dialogue_in_range(text, start=0, end=None, limit=5):
    """从 text[start:end] 范围内提取引号内台词."""
    if end is None:
        end = len(text)
    out = []
    seen = set()
    for m in _DIALOGUE_QUOTE_RE.finditer(text, start, end):
        line = next(
            (g for g in m.groups() if g is not None), ""
        ).strip()
        if not (4 <= len(line) <= 30):
            continue
        if any(o in line for o in _ONOMATOPOEIA) and len(line) <= 2:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _locate_shot_in_novel(shot, novel_text):
    """根据镜头内容在原文中定位大致位置（字符偏移）.

    策略：用 core_keywords / prompt 关键词在原文中找最密集匹配段.
    返回 (start_offset, end_offset)；找不到返回 (0, len(novel_text)).
    """
    if not novel_text:
        return 0, 0

    # 收集关键词
    keywords = []
    ck = shot.get("core_keywords")
    if isinstance(ck, list):
        keywords.extend(str(k) for k in ck if len(str(k)) >= 2)
    # 从 prompt 里抽 2-4 字词（简单分词：取中文连续片段）
    prompt = str(shot.get("prompt") or "")
    ms = str(shot.get("main_subject") or "")
    for s in (ms, prompt):
        for m in re.findall(r"[\u4e00-\u9fff]{2,6}", s):
            if m not in keywords and len(m) >= 2:
                keywords.append(m)

    if not keywords:
        return 0, len(novel_text)

    # 滑动窗口：找关键词命中最多的位置
    window = 400  # 窗口大小
    step = 200
    best_pos = 0
    best_score = 0

    for pos in range(0, max(1, len(novel_text) - window), step):
        segment = novel_text[pos:pos + window]
        score = 0
        for kw in keywords[:10]:  # 最多用前10个关键词
            if kw in segment:
                score += 1
        if score > best_score:
            best_score = score
            best_pos = pos

    if best_score == 0:
        return 0, len(novel_text)

    # 返回命中区域 ±200 字
    start = max(0, best_pos - 200)
    end = min(len(novel_text), best_pos + window + 200)
    return start, end


def _pick_best_dialogue(candidates, bad_line):
    """从候选台词中选一句最合适的替换 bad_line.

    策略：
    1. 长度最接近的优先
    2. 有相同角色名的优先（如果 bad_line 含「XX：」格式）
    3. 含相同关键词的优先
    """
    if not candidates:
        return ""

    bad_len = len(bad_line)
    bad_stripped = bad_line

    # 提取角色名（如果是「角色：台词」格式）
    bad_role = ""
    if "：" in bad_line:
        parts = bad_line.split("：", 1)
        bad_role = parts[0].strip()
        bad_stripped = parts[1].strip()
    elif ":" in bad_line:
        parts = bad_line.split(":", 1)
        bad_role = parts[0].strip()
        bad_stripped = parts[1].strip()

    def score(cand):
        s = 0
        # 长度接近度
        s -= abs(len(cand) - len(bad_stripped)) * 2
        # 角色名匹配（如果 bad_line 有角色名，cand 可能没前缀，但台词内容匹配）
        if bad_role and bad_role in cand:
            s += 10
        # 关键词重叠
        bad_chars = set(bad_stripped)
        cand_chars = set(cand)
        overlap = len(bad_chars & cand_chars)
        s += overlap
        return s

    best = max(candidates, key=score)
    return best


class DialogueGroundingConstraint(Constraint):
    """F16. 失锚台词硬替换：用原文台词替换编造的对白."""

    name = "dialogue_grounding"
    severity = Severity.WARN
    skip_hook = False  # hook 分镜也检查

    def apply(self, ctx):
        repairs = []
        if not ctx.novel_text:
            return repairs  # 没有原文，没法 grounding

        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            narr = str(shot.get("narration_segment") or "").strip()
            if not narr:
                continue  # 没台词，跳过

            # 检查是否在原文中有出处
            from step01_script_gen.main import _locate_narration_in_novel
            s, e = _locate_narration_in_novel(narr, ctx.novel_text)
            if s >= 0:
                continue  # 有出处，跳过

            # 幂等性：已处理过的跳过
            meta = shot.get('_run_metadata') or {}
            if meta.get('dialogue_grounded'):
                continue

            # 失锚：找替换台词
            # 1. 定位镜头在原文中的大致位置
            seg_start, seg_end = _locate_shot_in_novel(shot, ctx.novel_text)
            if seg_start >= seg_end:
                seg_start, seg_end = 0, len(ctx.novel_text)

            # 2. 从该段原文中提取台词
            candidates = _extract_dialogue_in_range(
                ctx.novel_text, seg_start, seg_end, limit=8
            )

            # 3. 选最合适的一句
            replacement = _pick_best_dialogue(candidates, narr)

            old_narr = narr
            old_sub = str(shot.get("subtitle") or "")

            if replacement:
                # 保留原角色名前缀（如果有），替换台词内容
                new_narr = narr
                if "：" in narr:
                    role = narr.split("：", 1)[0]
                    new_narr = role + "：" + replacement
                elif ":" in narr:
                    role = narr.split(":", 1)[0]
                    new_narr = role + "：" + replacement
                else:
                    new_narr = replacement

                shot["narration_segment"] = new_narr
                shot["subtitle"] = new_narr

                shot.setdefault("_run_metadata", {})["dialogue_grounded"] = True
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="replace_grounded_dialogue",
                        target="narration_segment",
                        before=old_narr,
                        after=new_narr,
                    )
                )
            else:
                # 找不到合适的 → 置空（宁可静音也不编造）
                shot["narration_segment"] = ""
                shot["subtitle"] = ""

                shot.setdefault("_run_metadata", {})["dialogue_grounded"] = True
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="clear_ungrounded_dialogue",
                        target="narration_segment",
                        before=old_narr,
                        after="(empty - 未找到原文出处，已清空)",
                    )
                )

        return repairs