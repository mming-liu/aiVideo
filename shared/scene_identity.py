# -*- coding: utf-8 -*-
"""场景身份解析：显式 id > 完整 identifier/别名唯一命中；歧义只诊断，不猜.

设计约束（2026-11-08 WP7-Prep 复核）：
- 不再用「最长公共子串 + bank 顺序」选场景图：那会把「照月湾荷湾 vs 照月湾荷塘」
  这类同地点双条目变成静默选图。
- 镜头带显式场景身份（scene_asset_id / scene_id）时优先使用，且必须存在于资产库。
- 无显式身份时按**匹配特异性**取最优：精确相等 > 完整子串包含（按匹配串长度）>
  最长公共子串（按公共长度）；同分仍返回歧义诊断（不按 bank 顺序裁决），
  由输入侧提供显式 id 或收敛重复别名。
- 稳定排序只用于诊断展示，不承担身份裁决。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SceneMatch:
    """一次场景身份解析的结果（纯数据，可序列化）."""

    scene_id: str | None
    reason: str
    candidates: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.scene_id is not None

    def as_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "reason": self.reason,
            "candidates": list(self.candidates),
            "diagnostics": list(self.diagnostics),
        }


def _norm(value) -> str:
    """规范化文本：NFKC + 去首尾空白 + 合并内部空白；非字符串视为空."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).strip()
    return " ".join(text.split())


def _declared_terms(entry: dict, diagnostics: list[str]) -> list[str]:
    """读取并校验 consistent_terms：只接受 str / 序列，忽略空值，坏类型报诊断."""
    raw = (entry or {}).get("consistent_terms")
    if raw is None:
        return []
    if isinstance(raw, str):
        term = _norm(raw)
        return [term] if term else []
    if isinstance(raw, (list, tuple, set)):
        terms = []
        invalid = 0
        for item in raw:
            if not isinstance(item, str):
                invalid += 1
                continue
            term = _norm(item)
            if term:
                terms.append(term)
        if invalid:
            diagnostics.append(
                "consistent_terms 含 %d 个非字符串项，已忽略" % invalid)
        return terms
    diagnostics.append(
        "consistent_terms 类型非法（%s），已忽略" % type(raw).__name__)
    return []


def resolve_scene_identity(
    scenes: dict,
    *,
    explicit_id=None,
    scene_name=None,
    location=None,
) -> SceneMatch:
    """按「显式 -> 完整唯一命中」解析场景身份.

    Args:
        scenes: {identifier: scene dict}（已加载的场景库条目）。
        explicit_id: 镜头显式场景身份（scene_asset_id / scene_id）。
        scene_name: 镜头 scene 字段。
        location: 镜头 location 字段。

    Returns:
        SceneMatch；scene_id 为 None 时 reason 必为诊断类型之一。
    """
    entries = {str(k): (v or {}) for k, v in (scenes or {}).items() if str(k).strip()}
    diagnostics: list[str] = []

    explicit = _norm(explicit_id)
    if explicit:
        if explicit in entries:
            return SceneMatch(explicit, "explicit")
        return SceneMatch(
            None, "explicit_missing", (),
            ("显式场景身份 %r 不在资产库（现有 %d 个）" % (explicit, len(entries)),),
        )

    texts: list[tuple[str, str]] = []
    for label, value in (("scene", scene_name), ("location", location)):
        text = _norm(value)
        if text:
            texts.append((label, text))
    if not texts:
        return SceneMatch(None, "empty_input", (), ("镜头缺少 scene/location，无法匹配场景",))

    def _lcs_len(a: str, b: str) -> int:
        if not a or not b:
            return 0
        prev = [0] * (len(b) + 1)
        best = 0
        for ca in a:
            cur = [0] * (len(b) + 1)
            for j, cb in enumerate(b, 1):
                if ca == cb:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
        return best

    # 特异性打分：精确相等(3) > 完整子串包含(2，按匹配串长度) >
    # 最长公共子串(1，按公共长度)。取最高分；同分仍判歧义，不按 bank 顺序裁决。
    scored: dict[str, tuple] = {}
    for sid in entries:
        entry = entries[sid]
        sources = [("identifier", _norm(sid))]
        sources.extend(("alias", term) for term in _declared_terms(entry, diagnostics))
        best = None
        for _kind, source in sources:
            if not source:
                continue
            for _label, text in texts:
                if source == text:
                    score = (3, len(source), "exact")
                elif source in text or text in source:
                    score = (2, len(source), "contained")
                else:
                    common = _lcs_len(source, text)
                    if common < 2:
                        continue
                    score = (1, common, "common_substr")
                if best is None or score > best:
                    best = score
        if best is not None:
            scored[sid] = best

    if not scored:
        diagnostics.append(
            "场景无命中：%s；现有 %d 个场景条目" % (texts, len(entries)))
        return SceneMatch(None, "no_match", (), tuple(diagnostics))

    top = max(scored.values())
    winners = tuple(sorted(sid for sid, score in scored.items() if score == top))
    if len(winners) == 1:
        return SceneMatch(winners[0], top[2], tuple(sorted(scored)), tuple(diagnostics))

    detail = "; ".join("%s=%s" % (sid, scored[sid]) for sid in winners)
    diagnostics.append(
        "场景身份同分/歧义（%d 个候选）：%s；请提供 scene_asset_id 或收敛重复别名"
        % (len(winners), detail))
    return SceneMatch(None, "ambiguous", winners, tuple(diagnostics))
