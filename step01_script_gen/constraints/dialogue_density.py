"""dialogue_density — 对白镜占比通用兜底（≥ 30%）+ P4.1 自动注入原文台词.

v6.63 (P4.1) 升级：从纯 WARN 升级为 WARN + hard-fix 自动注入原文对白。
对白不足时，在张力最高的无对白镜头中，从原文对应段落提取台词注入。

v6.63.1 质量提升：
- 跳过前 2 镜（开篇空镜/定场镜不应硬塞对白）
- 必须能从镜头中识别出具体角色才注入（群众/空镜跳过）
- 同一段原文台词不重复注入相邻镜头
- 说话人优先匹配 frame_layouts.ff 中的角色名
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

# Reuse extraction utilities from dialogue_grounding
from .dialogue_grounding import (
    _extract_dialogue_in_range,
    _locate_shot_in_novel,
)


_DIALOGUE_HINTS = ("道", "说", "答", "问", "喝", "喊", "诉", "叫", "叹", "笑", "怒", "骂", "言", "自语")
_HEAD_STOP_CHARS = "。！？；，、…·"
_NON_SPEAKER_LABELS = (
    "时间", "地点", "场景", "景别", "机位", "运镜", "画面", "镜头", "光位",
    "注意", "备注", "提示", "说明", "旁白", "字幕", "音效", "背景音", "台词",
)
# 这些 main_subject 前缀不算具体角色，注入前必须从 frame_layouts 找到具体人物
_VAGUE_SUBJECTS = ("空镜", "远景", "全景", "环境", "人群", "群演", "众人", "群众", "士兵", "侍卫", "家丁")


def _has_dialogue(narration: str) -> bool:
    """判断 narration_segment 是否承载对白（通用，不认任何角色名）."""
    if not narration or not isinstance(narration, str):
        return False
    s = narration.strip()
    if not s:
        return False
    m = re.match(r"^([^：:]{1,6})[：:](.+)$", s, flags=re.S)
    if m:
        head, body = m.group(1), m.group(2).strip()
        if body and not any(c in head for c in _HEAD_STOP_CHARS) \
                and not any(lb in head for lb in _NON_SPEAKER_LABELS):
            return True
    if ("「" in s and "」" in s) or ("\u201c" in s and "\u201d" in s):
        return True
    return any(h in s for h in _DIALOGUE_HINTS) and ("：" in s or ":" in s)


# Tension keywords: shots with these are more likely to need dialogue
_TENSION_KWS = (
    "质问", "怒喝", "喝道", "叫道", "说道", "答道", "问道",
    "冷声", "低声", "厉声", "低语", "轻声", "怒道",
    "对峙", "交锋", "冷笑", "沉声", "大喝",
)


def _shot_tension_score(shot: dict) -> float:
    """估计镜头的对话可能性分数（越高越可能该有对白）."""
    score = 0.0
    ms = str(shot.get("main_subject") or "")
    for kw in _TENSION_KWS:
        if kw in ms:
            score += 2.0
    kfs = shot.get("keyframes") or []
    for kf in kfs:
        stage = str(kf.get("stage") or "")
        if stage == "climax":
            score += 3.0
        elif stage == "approach":
            score += 1.5
        elif stage == "react":
            score += 1.0
    fl = shot.get("frame_layouts") or {}
    ff_chars = fl.get("ff") or []
    if len(ff_chars) >= 2:
        score += 1.5
    elif len(ff_chars) == 1:
        score += 0.5
    prompt = str(shot.get("prompt") or "")
    if any(w in prompt for w in ("对视", "相视", "对峙", "对话", "交谈", "商议", "密谈")):
        score += 2.0
    return score


def _get_shot_characters(shot: dict) -> list[str]:
    """从镜头中提取所有可识别的角色名列表（去重、保序）."""
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        name = name.strip()
        if not name or len(name) < 2 or len(name) > 4:
            return
        if name in seen:
            return
        if any(v == name for v in _VAGUE_SUBJECTS):
            return
        seen.add(name)
        names.append(name)

    # 1. main_subject 前缀
    ms = str(shot.get("main_subject") or "")
    m = re.match(r"^([\u4e00-\u9fff]{2,4})", ms)
    if m:
        _add(m.group(1))

    # 2. frame_layouts.ff 里的人物
    fl = shot.get("frame_layouts") or {}
    ff = fl.get("ff") or []
    if isinstance(ff, list):
        for c in ff:
            if isinstance(c, dict):
                _add(str(c.get("name") or ""))

    # 3. prompt 里的 "XX与YY" 模式
    prompt = str(shot.get("prompt") or "")
    for m2 in re.finditer(r"([\u4e00-\u9fff]{2,4})与([\u4e00-\u9fff]{2,4})", prompt):
        _add(m2.group(1))
        _add(m2.group(2))

    return names


def _find_speaker_in_shot(shot: dict) -> str:
    """从镜头信息中找最可能的说话角色名（取第一个具体角色）."""
    chars = _get_shot_characters(shot)
    if chars:
        return chars[0]
    return ""


def _format_dialogue_line(line: str, speaker: str) -> str:
    """格式化为 角色：台词（台词主体 ≤15 字）."""
    body = line.strip()
    if len(body) > 15:
        body = body[:14] + "…"
    if speaker:
        return speaker + "：" + body
    return body


def _line_similar(a: str, b: str) -> bool:
    """简单判断两句台词是否过于相似（前 6 字重叠或后半相同）."""
    a = re.sub(r"^[^：]+：", "", a).strip()
    b = re.sub(r"^[^：]+：", "", b).strip()
    if not a or not b:
        return False
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return True
    common = 0
    for ch in a:
        if ch in b:
            common += 1
    if len(a) >= 4 and common / len(a) > 0.6:
        return True
    return False


# ---------------------------------------------------------------------------
# P4.1.2 延续对白说话人回填（continuation dialogue speaker backfill）
#
# 问题：doubao 会把同一句原文引语拆成多镜旁白，首镜带「XX道：」前缀，
#       后续延续镜只剩裸句（如「在应天府老家那边订下一门亲事了」），
#       _has_dialogue() 不识别 → 对白镜占比被低估 → 打分/注入决策失真。
#
# 方案（零角色名硬编码，全部从原文动态提取）：
#   1. 找旁白在原文中的位置（原文 plain-find，避免归一化坐标漂移）
#   2. 从该位置向前回溯最近的引语起始符，校验旁白确在引语内
#   3. 说话人优先级：
#      a. 前几镜对白说话人名出现在引语归属句中（最近镜优先）
#      b. 引语归属句中「XX道/说」正则候选（过滤「们/等」等非人名结尾）
#   4. 回填「XX道：」前缀到 narration_segment + subtitle（保持同步）
# ---------------------------------------------------------------------------

_QUOTE_STARTS = ("\u201c", "\u300c")   # “ 「
_QUOTE_ENDS = ("\u201d", "\u300d")     # ” 」
_SPEAKER_CAND_RE = re.compile(r"([\u4e00-\u9fff]{2,4})[道曰说]")
_BAD_NAME_ENDINGS = ("们", "等", "的", "之", "人", "声")
_CONNECTOR_CHARS = set("却对向跟和与便就才只被把从在到了又不也都还很更太于是")
_QUOTE_BACKSCAN_WINDOW = 800   # 旁白位置向前回溯引语起始符的最大距离
_SPEAKER_PREFIX_WINDOW = 40    # 引语起始符向前取归属句的最大长度


def _extract_speaker_from_narration(narr: str) -> str:
    """从「XX道：台词」/「XX：台词」格式中提取说话人名（过滤非说话人标签）."""
    if not narr:
        return ""
    m = re.match(r"^([^：:]{1,6})[：:]", narr.strip())
    if not m:
        return ""
    head = m.group(1).strip()
    if not head or any(c in head for c in _HEAD_STOP_CHARS):
        return ""
    if any(lb in head for lb in _NON_SPEAKER_LABELS):
        return ""
    # 「肖管事道：」→「肖管事」；剥离尾缀动词，但保底长度 ≥ 2
    stripped = re.sub(r"[道曰说]$", "", head).strip()
    if len(stripped) >= 2:
        return stripped
    return head


def _find_narration_pos_in_original(narr: str, novel_text: str) -> int:
    """在原文（未归一化）中定位旁白文本，返回起始偏移；找不到返回 -1.

    策略：整句 plain-find → 按标点切块后取 ≥4 字块 plain-find。
    不用 gap_analysis._locate_narration_in_novel：其返回归一化坐标
    （…→... 会漂移），不能用于原文引语回溯。
    """
    if not narr or not novel_text:
        return -1
    s = narr.strip()
    pos = novel_text.find(s)
    if pos >= 0:
        return pos
    for chunk in re.split(r"[，。！？；：、…\s]", s):
        chunk = chunk.strip()
        if len(chunk) >= 4:
            p = novel_text.find(chunk)
            if p >= 0:
                return p
    return -1


def _find_speaker_before_quote(
    novel_text: str, pos: int, prev_speakers: list,
    known_names: frozenset = frozenset(),
) -> str:
    """从 pos 向前回溯引语块，提取该引语的说话人名.

    Args:
        novel_text: 原文全文
        pos: 旁白在原文中的起始偏移
        prev_speakers: 前几镜对白说话人名（最近优先）

    Returns:
        说话人名；无法确定返回 ""
    """
    if pos <= 0 or pos > len(novel_text):
        return ""
    window_start = max(0, pos - _QUOTE_BACKSCAN_WINDOW)
    before = novel_text[window_start:pos]
    q = max(before.rfind(_QUOTE_STARTS[0]), before.rfind(_QUOTE_STARTS[1]))
    if q < 0:
        return ""
    # 校验：引语起始符到旁白位置之间不能出现闭合引号（否则旁白在引语外）
    segment = before[q:]
    if any(c in segment for c in _QUOTE_ENDS):
        return ""
    prefix = before[max(0, q - _SPEAKER_PREFIX_WINDOW):q]
    # a) 前几镜说话人名出现在归属句中（最近镜优先）——强信号
    for sp in prev_speakers:
        if sp and len(sp) >= 2:
            p = prefix.rfind(sp)
            if p >= 0 and p >= len(prefix) - 30:
                return sp
    # b) 归属句中每个「道/说」动词，枚举其前 2/3/4 字候选名，
    #    过滤非人名结尾 + 剥离连接字；优先命中剧本已知角色名，其次短名。
    candidates: list[str] = []
    for m in re.finditer(r"[道曰说]", prefix):
        for length in (2, 3, 4):
            name = prefix[m.start() - length:m.start()]
            if not name or name.endswith(_BAD_NAME_ENDINGS):
                continue
            while name and name[0] in _CONNECTOR_CHARS:
                name = name[1:]
            if len(name) >= 2 and not name.endswith(_BAD_NAME_ENDINGS):
                if name not in candidates:
                    candidates.append(name)
    for name in candidates:
        if name in known_names:
            return name
    candidates.sort(key=len)
    return candidates[0] if candidates else ""


class DialogueDensityConstraint(Constraint):
    """对白镜占比 ≥ 30% 兜底 + 不足时自动注入原文台词."""

    name = "dialogue_density"
    severity = Severity.WARN
    fields = ("narration_segment", "subtitle")
    skip_hook = True

    _MIN_RATIO = 0.30
    _SKIP_FIRST_N = 2  # 跳过前 N 镜（开篇空镜/定场镜）

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or len(shots) < 4:
            return repairs
        if not all(isinstance(shot, dict) for shot in shots):
            return repairs

        novel_text = getattr(ctx, "novel_text", "") or ""

        # P4.1.2：延续对白说话人回填（正确性修复，任何对白比例下都执行）
        repairs.extend(self._backfill_continuation_speakers(
            shots, novel_text, self._collect_known_names(ctx)
        ))

        n = len(shots)
        diag_indices = [
            i for i, sh in enumerate(shots)
            if _has_dialogue(sh.get("narration_segment", ""))
        ]
        n_diag = len(diag_indices)
        ratio = n_diag / n if n else 0

        if ratio >= self._MIN_RATIO:
            return repairs

        # Density is a policy signal, not proof that a specific shot must speak.
        # Earlier versions selected the nearest original dialogue and could
        # attach it to the wrong speaker/shot (including a fallback to the
        # first character).  Keep the script unchanged and make the shortage
        # visible for audit/LLM repair.
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=None,
            action="warn",
            target="narration_segment",
            detail=(
                f"对白镜 {n_diag}/{n} = {int(ratio * 100)}%，低于 "
                f"{int(self._MIN_RATIO * 100)}%；保留原文，禁止按邻近段落自动注入台词。"
            ),
        ))
        return repairs

    @staticmethod
    def _collect_known_names(ctx) -> frozenset:
        """收集剧本已知角色名（characters + 各镜可识别人物），用于候选校验."""
        known: set = set()
        chars_list = (ctx.script or {}).get("characters") or []
        for c in chars_list:
            if isinstance(c, dict):
                nm = str(c.get("name") or "").strip()
            else:
                nm = str(c or "").strip()
            if len(nm) >= 2:
                known.add(nm)
        for sh in (ctx.shots or []):
            if isinstance(sh, dict):
                known.update(_get_shot_characters(sh))
        return frozenset(known)

    def _backfill_continuation_speakers(
        self, shots: list, novel_text: str,
        known_names: frozenset = frozenset(),
    ) -> list[RepairRecord]:
        """P4.1.2：延续对白说话人回填.

        扫描 narration 非空但 _has_dialogue() 为 False 的镜头：
        若旁白在原文中位于某引语块内部，则把说话人名回填为
        「XX道：」前缀（narration_segment 与 subtitle 同步）。

        通用性：说话人全部从原文动态提取，无任何角色名硬编码。
        """
        repairs: list[RepairRecord] = []
        if not novel_text:
            return repairs

        prev_speakers: list[str] = []  # 最近对白说话人，最新在前，最多 3 个
        for i, shot in enumerate(shots):
            if not isinstance(shot, dict):
                continue
            narr = str(shot.get("narration_segment") or "").strip()
            if not narr:
                continue
            if _has_dialogue(narr):
                sp = _extract_speaker_from_narration(narr)
                if sp:
                    prev_speakers.insert(0, sp)
                    del prev_speakers[3:]
                continue

            meta = shot.get("_run_metadata") or {}
            if meta.get("dialogue_continuation_fixed"):
                continue  # 幂等

            pos = _find_narration_pos_in_original(narr, novel_text)
            if pos < 0:
                continue  # 旁白无原文出处，不是延续句（交由 grounding 处理）

            speaker = _find_speaker_before_quote(
                novel_text, pos, prev_speakers, known_names
            )
            if not speaker:
                continue  # 无法确定说话人，不强行回填

            new_narr = speaker + "道：" + narr
            shot["narration_segment"] = new_narr
            shot["subtitle"] = new_narr
            shot.setdefault("_run_metadata", {})[
                "dialogue_continuation_fixed"
            ] = True
            prev_speakers.insert(0, speaker)
            del prev_speakers[3:]
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=i,
                action="backfill_continuation_speaker",
                target="narration_segment",
                before=narr,
                after=new_narr,
            ))
        return repairs
