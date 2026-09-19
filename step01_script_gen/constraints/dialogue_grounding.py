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
# v6.63.1：引号正则/拟声词表/台词定位函数统一从 gap_analysis 取（单一事实源），
# 不再反向 import main.py（重依赖 + 循环导入风险 + 触发 style_prefix 弃用 shim）。
# 修复：旧引号正则缺中文弯引号 “”——真实小说 31110 处 “”、0 处「」，
# 导致 _extract_dialogue_in_range 永远抽不到候选 → 失锚台词全被清空而非替换。
from step01_script_gen.gap_analysis import (
    DIALOGUE_QUOTE_RE,
    ONOMATOPOEIA,
    _locate_narration_in_novel,
)


# 说话人识别：引号前紧邻的「XX道：/XX喝道：」形态（v6.63.1）
_SPEAKER_RE = re.compile(r"([\u4e00-\u9fff]{1,6})[道曰说]\s*[：:]?\s*$")


def _extract_dialogue_in_range(text, start=0, end=None, limit=5):
    """从 text[start:end] 范围内提取引号内台词.

    Returns:
        [(line, speaker), ...]——speaker 为引号前紧邻「XX道：」的说话人，
        识别不到为 ""（供 _pick_best_dialogue 做同角色优先匹配）。
    """
    if end is None:
        end = len(text)
    out = []
    seen = set()
    for m in DIALOGUE_QUOTE_RE.finditer(text, start, end):
        line = next(
            (g for g in m.groups() if g is not None), ""
        ).strip()
        if not (4 <= len(line) <= 30):
            continue
        # v6.63.1 修复拟声词过滤（旧条件 and len(line) <= 2 在 len>=4 保证下
        # 永远为假，是死代码）：剥掉非汉字后只剩拟声字 → 纯感叹，无实义，跳过
        han = re.sub(r"[^\u4e00-\u9fff]", "", line)
        if han and all(ch in ONOMATOPOEIA for ch in han):
            continue
        if line in seen:
            continue
        seen.add(line)
        # 说话人：引号前 ≤12 字内的「XX道：」
        prefix = text[max(0, m.start() - 12):m.start()]
        sm = _SPEAKER_RE.search(prefix)
        speaker = sm.group(1) if sm else ""
        out.append((line, speaker))
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

    Args:
        candidates: [(line, speaker), ...]——speaker 为原文「XX道：」识别的说话人
        bad_line: 被替换的失锚台词

    策略：
    1. 说话人匹配的强优先（原文「XX道：”与失锚台词「XX：」同角色）
       ——避免把张三的台词安进李四嘴里（v6.63.1 错位缓解）
    2. 台词内容含原角色名的次优先
    3. 长度最接近 + 字符重叠加权
    """
    if not candidates:
        return ""

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

    def _role_match(a, b):
        return bool(a) and bool(b) and (a == b or a in b or b in a)

    def score(item):
        cand, speaker = item
        s = 0
        # 长度接近度
        s -= abs(len(cand) - len(bad_stripped)) * 2
        # 角色名匹配（如果 bad_line 有角色名，cand 可能没前缀，但台词内容匹配）
        if bad_role and bad_role in cand:
            s += 10
        # 说话人匹配（原文「XX道：」标注与台词角色前缀一致）——强信号
        if _role_match(bad_role, speaker):
            s += 25
        # 关键词重叠
        bad_chars = set(bad_stripped)
        cand_chars = set(cand)
        overlap = len(bad_chars & cand_chars)
        s += overlap
        return s

    best = max(candidates, key=score)
    return best[0]


class DialogueGroundingConstraint(Constraint):
    """F16. 失锚台词硬替换：用原文台词替换编造的对白."""

    name = "dialogue_grounding"
    severity = Severity.WARN
    skip_hook = False  # hook 分镜也检查

    def apply(self, ctx):
        repairs = []
        if not ctx.novel_text:
            return repairs  # 没有原文，没法判断 grounding

        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            narr = str(shot.get("narration_segment") or "").strip()
            if not narr:
                continue
            s, _e = _locate_narration_in_novel(narr, ctx.novel_text)
            if s >= 0:
                continue
            # A fuzzy locator failure is not evidence that the authored line is
            # wrong.  Replacing/clearing it can attach an unrelated novel quote
            # to the wrong speaker and shot.  Keep the original and report.
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None if is_hook else shot.get("index", idx),
                action="warn",
                target="narration_segment",
                before=narr[:120],
                after=narr[:120],
                detail="台词未能定位到原文；保留原文，禁止自动替换或清空",
            ))
        return repairs


__all__ = ["DialogueGroundingConstraint"]
