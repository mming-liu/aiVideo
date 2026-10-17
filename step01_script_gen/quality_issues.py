"""Unified structured quality issues for Step01.

Rule version is intentionally explicit: reports produced by older rules must be
re-checked even when the script revision is unchanged.
"""
from __future__ import annotations

import contextlib
import io
import re

from shared import motion_segments as _ms
from dataclasses import dataclass, field
from typing import Iterable

from step01_script_gen.subject_attribution import leading_named_span

RULE_VERSION = "2026-09-23.4"
RULE_IMPLEMENTATION = "evidence-gated-writes-v1"


def _compute_rule_source_fingerprint() -> str:
    """**判定版本**指纹：决定「什么算 blocking / 什么能修 / 旧报告是否仍然有效」.

    只有会改变旧报告有效性的代码才进这里。**生成/修复提示词不进**——改提示词
    改变的是 script 内容，而内容变化已被 `script_revision` 覆盖，把它塞进来只会
    让门禁无谓失效（见 `_compute_prompt_source_fingerprint`）。
    """
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parent
    project = root.parent
    files = [
        root / "quality_issues.py",
        # 2026-09-27（复核 1-3）：kf ↔ 时段覆盖的**唯一判据**。质量门的新 blocking
        # `kf_segment_missing` 完全由它决定——只改覆盖判据（不动 quality_issues.py）
        # 时，旧报告必须失效，否则会被复用。
        root / "kf_coverage.py",
        root / "state_updates.py",
        root / "validation.py",
        root / "timecode.py",
        root / "split_visuals.py",
        project / "shared" / "style_prefix.py",
        project / "shared" / "vimax_bridge.py",
        # v6.83（二次审核发现 6）：这三份 shared 实现 + 文本 schema 直接决定
        # 「什么算 blocking / 事件表怎么校验 / 时间字段谁能写」，却不在指纹里
        # ——只改它们而不动 step01 目录，旧报告会被错误地当成仍然有效。
        # v6.111（审核 P2-5）：这三份**直接决定「什么算 blocking / 什么能修」**，
        # 却不在指纹里——改了检测器或审计实现，旧质量报告/审计结论会被错误地
        # 当成仍然有效。
        root / "audit.py",
        root / "cross_field_checks.py",
        root / "kf_structural_guard.py",
        root / "subject_attribution.py",
        root / "weapon_states.py",
        # v6.112：约束框架的版本索引——决定「哪些约束在跑、跑的是哪一版」，
        # 与下面的 constraints/ 目录同为判定实现。
        root / "rules_index.py",
        project / "shared" / "motion_segments.py",
        project / "shared" / "quality_gate.py",
        project / "shared" / "semantic_barrier.py",
        project / "_text_gen" / "deepseek_schema.py",
    ]
    # v6.112（总计划 §1.3 / 审核 P2-5 后半段）：判定实现还有两处成建制的目录——
    # `constraints/`（70+ 条硬修复 + engine + rules_index）与 `prompt_semantic/`
    # （关键词表，如 STRONG_INJURY_ANCHORS）。它们直接决定告警与阻塞口径，
    # 此前不在指纹里：**只改判定、不改内容**的改动（例如某条约束改为只降级不写入）
    # 会让旧报告/旧审计被错误地复用。按目录整体纳入，避免以后逐条往清单里补。
    for _sub in ("constraints", "prompt_semantic"):
        _dir = root / _sub
        if _dir.is_dir():
            files.extend(p for p in sorted(_dir.rglob("*.py")) if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:16]


def _compute_prompt_source_fingerprint() -> str:
    """**生成/修复提示词版本**指纹（诊断字段，**不参与门禁、不参与复用判定**）.

    覆盖「稿子怎么生成、被点名后怎么改」的那几处：`prompts.py` / `prompts/`
    （system prompt 与各站点模板）、`outline.py`（大纲与时长预算）、`main.py`
    （修复 prompt 与接受判据就写在这里面）。

    刻意**不**并入 `RULE_SOURCE_FINGERPRINT`：改这些只改变稿件内容，而内容变化
    已经由 `script_revision` 覆盖——把整个 `main.py` 加进判定指纹只会让门禁在
    「旧报告其实仍然有效」时无谓失效（审核 P2-5 的边界）。它只被记录：
    `step01_audit.json` 元数据 + `step01_commit.json` + 每轮审计台账。
    """
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parent
    files = [root / "prompts.py", root / "outline.py", root / "main.py"]
    _prompt_dir = root / "prompts"
    if _prompt_dir.is_dir():
        files.extend(p for p in sorted(_prompt_dir.rglob("*")) if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:16]


RULE_SOURCE_FINGERPRINT = _compute_rule_source_fingerprint()
PROMPT_SOURCE_FINGERPRINT = _compute_prompt_source_fingerprint()


@dataclass
class Issue:
    code: str
    severity: str  # hard | visual_risk | advisory | video_advisory | unverified
    message: str
    shot_id: object = None
    frame_id: str = ""
    entity_id: str = ""
    field_path: str = ""
    evidence: str = ""
    related_shot_ids: list = field(default_factory=list)
    # v6.119（方案 A 其二，2026-09-25 用户决定）：审计给出的**成对修复目标**
    # `[{shot_id, field_path}]`——「哪个镜头改哪个字段」。
    # `shot_id`/`field_path` 仍是**问题定位**（问题出现在哪）；只有这里的成对条目
    # 用于**授权**。两条平行数组（field_paths + related_shot_ids）会形成过宽的
    # 笛卡尔积授权，故不采用。
    #
    # **三态**（缺一不可，混成 `[]` 会让「模型明确说不修」被当成「旧条目没提供」
    # 而回退到定位字段推导 —— v6.119 审核实测）：
    #   - `None` ：**未提供**（旧审计产物 / 旧 payload）→ 走兼容路径（原 `code`/
    #              `field_path` 推导）；
    #   - `[]`   ：**显式**「这条问题没有明确修复目标」→ **不授权**（也不给结构码
    #              的 KF 列表能力），该目标本轮不进定向修复；
    #   - 非空   ：成对模式，**只有**这些 (镜头, 字段) 进授权面。
    repair_targets: "list | None" = None
    repairable: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "shot_id": self.shot_id,
            "frame_id": self.frame_id,
            "entity_id": self.entity_id,
            "field_path": self.field_path,
            "evidence": self.evidence,
            "related_shot_ids": self.related_shot_ids,
            "repair_targets": self.repair_targets,
            "repairable": self.repairable,
        }


def parse_shot_ref(message: str):
    """Extract (shot_id, related_ids) from checklist messages.

    Supports integers, half-integer split shots (13.5), hooks, and cross-shot
    messages such as 镜头13→14.  Structured callers should prefer Issue.shot_id,
    but legacy checklist text must still never collapse 13.5 into 13.
    """
    text = str(message or "")
    if "钩子镜头" in text or "镜头hook" in text or "钩子" in text or re.match(r"^hook(?:\s|:|：)", text, re.IGNORECASE):
        return 0, []
    m = re.search(r"(?:镜头|\bshot)\s*(\d+(?:\.\d+)?)\s*(?:→|->)\s*(?:镜头|shot)?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return m.group(1), [m.group(2)]
    m = re.search(r"(?:镜头|\bshot)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return m.group(1), []
    return None, []


def _frame_texts(shot: dict) -> list[tuple[str, str]]:
    out = [("ff", str(shot.get("ff_desc") or ""))]
    kfs = shot.get("keyframes") or []
    for i, kf in enumerate(kfs):
        if isinstance(kf, dict):
            out.append((f"kf{i}", str(kf.get("desc") or "")))
    out.append(("lf", str(shot.get("lf_desc") or "")))
    return out


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[，。；！？]", text or "") if c.strip()]


def _char_names(shot: dict) -> list[str]:
    names: list[str] = []
    for layout_key in ("frame_layouts", "char_layout"):
        layout = shot.get(layout_key)
        if isinstance(layout, dict):
            buckets = []
            for key in ("ff", "lf"):
                buckets.extend(layout.get(key) or [])
            layout = buckets
        if isinstance(layout, list):
            for item in layout:
                if not isinstance(item, dict):
                    continue
                n = str(item.get("name") or "").strip()
                if n and n not in names:
                    names.append(n)
    return names


def _eye_conflicts(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    open_words = ("双目圆睁", "双眼圆睁", "圆睁", "睁眼", "双目大睁", "双眼大睁")
    closed_words = ("双目紧闭", "双眼紧闭", "闭眼")
    names = _char_names(shot)
    for field_name in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
        text = str(shot.get(field_name) or "")
        if not text:
            continue
        # Subject inheritance works within a sentence, not across sentences.
        for sentence in re.split(r"[。；！？]", text):
            current_names: list[str] = []
            states: dict[str, set[str]] = {}
            has_explicit_time = bool(_ms.TIME_MENTION_RE.search(sentence))
            for clause in _clauses(sentence):
                if has_explicit_time:
                    states = {}
                mentioned = [n for n in names if n in clause]
                if mentioned:
                    current_names = mentioned
                has_open = any(w in clause for w in open_words)
                has_closed = any(w in clause for w in closed_words)
                if not (has_open or has_closed):
                    continue
                if len(current_names) == 1:
                    ent = current_names[0]
                    bucket = states.setdefault(ent, set())
                    if has_open:
                        bucket.add("open")
                    if has_closed:
                        bucket.add("closed")
                    if {"open", "closed"} <= bucket:
                        issues.append(Issue(
                            code="eye_state_conflict", severity="visual_risk",
                            message=f"角色{ent}同段同时出现睁眼/闭眼",
                            shot_id=shot_id, entity_id=ent, field_path=field_name,
                            evidence=sentence, repairable=True,
                        ))
                        states[ent] = set()
                elif has_open and has_closed:
                    issues.append(Issue(
                        code="eye_state_conflict_unverified", severity="unverified",
                        message="同句出现睁眼/闭眼但无法确认主语",
                        shot_id=shot_id, field_path=field_name, evidence=clause,
                        repairable=False,
                    ))
    return issues


def _action_time_issues(shot: dict, shot_id) -> list[Issue]:
    try:
        duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
    except (TypeError, ValueError):
        return []
    motion = str(shot.get("motion_desc") or "")
    if duration <= 0 or not motion:
        return []
    # v6.76：与 motion_time_bounds 合并到 shared.motion_segments.max_mentioned_second
    _max_sec = _ms.max_mentioned_second(motion)
    ends = [] if _max_sec is None else [_max_sec]
    if ends and max(ends) > duration + 0.01:
        return [Issue(
            code="action_time_out_of_range", severity="visual_risk",
            message=f"动作分段到第{max(ends):g}秒，超过镜头时长{duration:g}秒",
            shot_id=shot_id, field_path="motion_desc", evidence=motion,
            repairable=True,
        )]
    return []


def _state_order_issues(shot: dict, shot_id) -> list[Issue]:
    names = _char_names(shot)
    if not names:
        return []
    gone: set[str] = set()
    for frame_id, text in _frame_texts(shot):
        for clause in _clauses(text):
            for name in names:
                if name not in clause:
                    continue
                if any(w in clause for w in ("已没入水中", "已退场", "已消失", "已离开")):
                    gone.add(name)
                    continue
                if name in gone and any(w in clause for w in ("跃出", "跃起", "冲出", "登船", "跃上", "窜出")):
                    return [Issue(
                        code="state_order_replay", severity="visual_risk",
                        message=f"{name}已退场/入水后，后续帧又重复执行入场动作",
                        shot_id=shot_id, frame_id=frame_id, entity_id=name,
                        field_path=frame_id, evidence=clause, repairable=True,
                    )]
    return []


def _pose_conflicts(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    layouts = shot.get("frame_layouts") or {}
    if not isinstance(layouts, dict):
        return issues
    sitting = ("坐", "跪", "盘膝", "踞蹲", "蹲")
    lying = ("仰卧", "躺", "卧", "倒")
    buckets: list[tuple[str, list]] = []
    for key in ("ff", "lf"):
        if isinstance(layouts.get(key), list):
            buckets.append((key, layouts.get(key)))
    kfs = layouts.get("kfs")
    if isinstance(kfs, list):
        for i, frame in enumerate(kfs):
            if isinstance(frame, list):
                buckets.append((f"kf{i}", frame))
    for frame_id, frames in buckets:
        for item in frames:
            if not isinstance(item, dict):
                continue
            pose = str(item.get("pose") or "")
            if any(s in pose for s in sitting) and any(l in pose for l in lying):
                issues.append(Issue(
                    code="pose_conflict", severity="visual_risk",
                    message=f"{item.get('name','角色')}同帧姿态同时包含坐/跪和躺卧",
                    shot_id=shot_id, frame_id=frame_id,
                    entity_id=str(item.get("name") or ""), field_path=f"frame_layouts.{frame_id}.pose",
                    evidence=pose, repairable=True,
                ))
    return issues


def _surface_conflicts(shot: dict, shot_id) -> list[Issue]:
    # Only clearly incompatible carrier pairs.  “船头/船板” may describe the
    # same physical surface and must not be treated as a conflict.
    incompatible = (
        ("车厢内", "院内地面"), ("车厢内", "地面"),
        ("马背", "院内地面"), ("轿内", "院内地面"),
        ("船板", "院内地面"), ("船头", "院内地面"), ("船尾", "院内地面"),
    )
    names = _char_names(shot)
    issues: list[Issue] = []
    for frame_id, text in _frame_texts(shot):
        for clause in _clauses(text):
            found_pair = None
            for a, b in incompatible:
                if a in clause and b in clause:
                    found_pair = (a, b)
                    break
            if not found_pair:
                continue
            mentioned = [n for n in names if n in clause]
            if len(mentioned) == 1:
                issues.append(Issue(
                    code="surface_conflict", severity="visual_risk",
                    message=f"{mentioned[0]}同帧出现互斥承载面: {list(found_pair)}",
                    shot_id=shot_id, frame_id=frame_id, entity_id=mentioned[0],
                    field_path="ff_desc/lf_desc", evidence=clause, repairable=True,
                ))
    return issues


def _onscreen_prop_issues(shot: dict, shot_id) -> list[Issue]:
    layouts = shot.get("frame_layouts")
    if not isinstance(layouts, dict):
        return []
    issues: list[Issue] = []
    from shared.content_safety import WEAPON_WORDS as _WEAPON_WORDS
    frame_map = dict(_frame_texts(shot))
    for frame_id, text in frame_map.items():
        frames = layouts.get(frame_id)
        if frame_id.startswith("kf"):
            try:
                frames = (layouts.get("kfs") or [])[int(frame_id[2:])]
            except Exception:
                frames = []
        for item in frames or []:
            if not isinstance(item, dict) or item.get("on_screen") is not False:
                continue
            name = str(item.get("name") or "")
            for clause in _clauses(text):
                if name in clause and any(w in clause for w in ("攻击", "持刀", "持匕首", "刺出", "劈砍")):
                    issues.append(Issue(
                        code="offscreen_character_action", severity="visual_risk",
                        message=f"{name}标记离场但同帧仍要求其执行动作",
                        shot_id=shot_id, frame_id=frame_id, entity_id=name,
                        field_path=f"frame_layouts.{frame_id}.on_screen", evidence=clause,
                        repairable=True,
                    ))
        # Only explicit same-owner/same-weapon claims prove a contradiction.
        from step01_script_gen.weapon_states import explicit_weapon_conflicts
        for owner, weapon in explicit_weapon_conflicts(text, _char_names(shot)):
            issues.append(Issue(
                code="weapon_state_conflict", severity="visual_risk",
                message=f"{owner} 的武器「{weapon}」同帧既明确脱手又明确持握，武器状态矛盾",
                shot_id=shot_id, frame_id=frame_id, entity_id=owner,
                field_path="ff_desc/lf_desc", evidence=text[-180:],
                repairable=True,
            ))
        # Holder evidence can appear in another clause of the same frame.
        # 2026-10 裁决：持械**动作**与威胁**主体**拆两张表单判（单源 registry）。
        # 原先混写成一个元组，且身份词只有「神秘人/刺客」——「黑衣人/杀手/蒙面人」
        # 持刀的场景会被误判成「同帧没有明确持刀者」。
        from step01_script_gen.prompt_semantic.registry import (
            THREAT_VISIBILITY_KEYWORDS, WEAPON_HOLDER_VERBS,
        )
        if "刀尖" in text and not (
            any(v in text for v in WEAPON_HOLDER_VERBS)
            or any(t in text for t in THREAT_VISIBILITY_KEYWORDS)
        ):
            issues.append(Issue(
                code="weapon_holder_missing", severity="visual_risk",
                message="同帧出现刀尖但没有明确持刀者",
                shot_id=shot_id, frame_id=frame_id, field_path="prompt/ff_desc",
                evidence=text[-160:], repairable=False,
            ))
    return issues


def _kf_clean_text(text: str) -> str:
    text = str(text or "")
    text = text.split("【角色锚定】", 1)[0]
    text = re.sub(r"（本帧[^）]*）", "", text)
    text = re.sub(r"（画面延续[^）]*）", "", text)
    text = re.sub(r"画面延续[，,。；;]*保持该状态", "", text)
    return re.sub(r"[，。；！？、：:（）()【】\s]", "", text)


def _kf_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    aa = _kf_clean_text(a)
    bb = _kf_clean_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def _pose_categories(text: str) -> set[str]:
    cats: set[str] = set()
    if any(w in text for w in ("坐", "跪", "盘膝", "踞蹲", "蹲")):
        cats.add("sit")
    if any(w in text for w in ("仰卧", "躺", "卧倒", "倒地", "倒伏", "平躺")):
        cats.add("lie")
    return cats


def _prop_release_owner_issues(shot: dict, shot_id) -> list[Issue]:
    from shared.content_safety import WEAPON_WORDS
    from step01_script_gen.weapon_states import asserted_marker

    layouts = shot.get("frame_layouts")
    if not isinstance(layouts, dict):
        return []
    names = _char_names(shot)
    release_words = ("丢", "脱手", "滑落", "掉落", "坠入", "放下", "松开")
    issues: list[Issue] = []
    for frame_id, text in _frame_texts(shot):
        frames = layouts.get(frame_id)
        if frame_id.startswith("kf"):
            try:
                frames = (layouts.get("kfs") or [])[int(frame_id[2:])]
            except Exception:
                frames = []
        for clause in _clauses(text):
            if not asserted_marker(clause, release_words):
                continue
            weapon = next((w for w in WEAPON_WORDS if w in clause), "")
            if not weapon:
                continue
            if any(name in clause for name in names):
                continue
            owners = []
            for item in frames or []:
                if not isinstance(item, dict):
                    continue
                props = item.get("props") or []
                if any(weapon in str(p) or str(p) in weapon for p in props):
                    owner = str(item.get("name") or "").strip()
                    if owner and owner not in owners:
                        owners.append(owner)
            if len(owners) > 1:
                issues.append(Issue(
                    code="prop_release_owner_ambiguous", severity="visual_risk",
                    message=f"{'、'.join(owners)} 同持「{weapon}」，但“{clause}”未说明释放者，不能猜测归属",
                    shot_id=shot_id, frame_id=frame_id,
                    field_path=f"frame_layouts.{frame_id}.props",
                    evidence=clause, repairable=False,
                ))
    return issues


def _time_field_audit_issues(script: dict) -> list[Issue]:
    """阶段屏障上报的**审计段越权写入** → blocking（v6.83，二次审核发现 1）.

    `shared/semantic_barrier.TimeFieldWriteTracker` 在落盘前的 final pass 上实测
    一遍「审计段（`kf_time_window_alignment` 起）是否还有约束写时间字段」。
    第一版把结果只写日志——发现 1 指出那是 fail-open：越权写入照样重建 timeline、
    算 revision、落盘，正式结果里看不出任何异常。这里把它接进质量门，**任何
    审计段写入都是 blocking**，正式落盘状态不可能是 PASS。

    记录由 `main.py::finalize_step01_script` 写入 `script["_time_field_write_audit"]`
    （下划线键，不入盘；只在同一次运行的 gate 收集期可见）。
    """
    audit = script.get("_time_field_write_audit")
    if not isinstance(audit, dict):
        return []
    out: list[Issue] = []
    for rec in audit.get("unauthorized") or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("constraint") or "?")
        diff = rec.get("diff") or []
        first = str(diff[0]) if diff else ""
        out.append(Issue(
            "time_field_audit_write", "hard",
            f"审计段约束 {name} 写了时间字段（{len(diff)} 处）：{first}"
            " —— 链前 KF/时长检查的结论对最终对象不成立；"
            "该写入必须降为只报告或前移到 KF 生成之前",
            shot_id=_audit_shot_id(first),
            field_path="duration_sec",
            evidence="; ".join(str(d) for d in diff[:3]),
            repairable=False,
        ))
    return out


def _semantic_audit_shot_id(diff_line: str):
    """从 `镜头#3 语义变化` 取出 shot id（列表位置 +1，仅用于可读性）."""
    m = re.search(r"镜头#(\d+)", str(diff_line or ""))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# v6.101（2026-09-23）：**开篇空镜过多的单一检测源**
#
# 背景：两个空镜约束 v6.99 起改为 report-only（用户决定 B：不删镜头），删除诉求
# 交结构 regen。但「交结构 regen」当时没有落地——收敛轮的目标集合只看
# `_shot_duration_issues`，看不见空镜问题。这里提供**唯一一份**检测实现：
#   · `opening_empty_shot_issues()` → 结构化 issue（供 `_structural_shot_issues` 消费）
#   · `is_opening_empty_shot()`     → 谓词（供 `opening_empty_shot_cap` 约束复用）
# 两处消费同一实现，避免「同一规则两份启发式」再次漂移。
#
# 严重度取 **advisory**：它是**节奏**问题，不是硬正确性问题——接进收敛轮是为了
# 给它一条修复路径，不是为了让门禁再多拦一类。要改成阻塞只需把 severity 换成
# `visual_risk`（一行）。
# ---------------------------------------------------------------------------
_OPENING_EMPTY_WINDOW = 3      # 只看前 N 镜
_OPENING_EMPTY_MAX = 1         # 前 N 镜最多允许几个空镜

# 空镜关键词（与 `opening_empty_shot_insert` 同组词表）
_OPENING_EMPTY_HINTS = ("空镜", "环境", "远景", "水波", "树影")
# 核心事件词——含这些的镜头即使 main_subject 像空镜也不算
_OPENING_EVENT_HINTS = (
    "刺杀", "打斗", "跃出", "破水", "冲入", "现身", "遇刺", "倒地",
    "对峙", "质问", "怒喝", "惊叫", "揭露", "反转", "逼近",
)


def is_opening_empty_shot(shot) -> bool:
    """是否空镜/环境镜（**唯一实现**，`opening_empty_shot_cap` 也用它）."""
    if not isinstance(shot, dict):
        return False
    ms = str(shot.get("main_subject") or "")
    prompt = str(shot.get("prompt") or "")
    ff = str(shot.get("ff_desc") or "")
    lf = str(shot.get("lf_desc") or "")
    all_text = ms + prompt + ff + lf

    # 有台词 → 不是空镜
    if str(shot.get("narration_segment") or "").strip():
        return False
    if str(shot.get("subtitle") or "").strip():
        return False
    # 含核心事件词 → 不是空镜
    if any(h in all_text for h in _OPENING_EVENT_HINTS):
        return False
    # main_subject 含空镜关键词 → 算空镜
    if any(h in ms for h in _OPENING_EMPTY_HINTS):
        return True
    if "环境空镜" in ff or "无人" in ff or "空无一人" in ff:
        return True
    return False


def opening_empty_shot_issues(script: dict) -> list[Issue]:
    """前 N 镜空镜超上限 → advisory issue（交结构 regen 重做建立镜）."""
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if len(shots) < _OPENING_EMPTY_WINDOW:
        return []
    window = shots[:_OPENING_EMPTY_WINDOW]
    empty_idx = [i for i, sh in enumerate(window) if is_opening_empty_shot(sh)]
    if len(empty_idx) <= _OPENING_EMPTY_MAX:
        return []
    _names = ["镜头%s(%s)" % (window[i].get("index", i + 1),
                              str(window[i].get("main_subject") or "")[:16])
              for i in empty_idx]
    return [Issue(
        code="opening_empty_shot_overload", severity="advisory",
        message=("开篇前 %d 镜有 %d 个空镜（上限 %d）：%s —— 抖音黄金 3 秒必须有主体"
                 "入场；请**重做这几镜**（合并或替换成带主体的建立镜），"
                 "代码不再自动删镜头（v6.99 report-only）"
                 % (_OPENING_EMPTY_WINDOW, len(empty_idx), _OPENING_EMPTY_MAX,
                    "、".join(_names))),
        shot_id=window[empty_idx[0]].get("index", empty_idx[0] + 1),
        field_path="main_subject",
        evidence="; ".join(_names),
        repairable=True,
    )]


def _semantic_write_audit_issues(script: dict) -> list[Issue]:
    """阶段屏障上报的**语义/结构**写入 → blocking / advisory（v6.98，审核 P0-3）.

    屏障此前只快照**时间字段**，于是审计段里「`del shots[ri]`」这类语义级写入
    完全不可见：审核实测 `opening_empty_shot_remove` 在审计边界之后 `shots.pop(0)`，
    删掉的是完整镜头对象（prompt / ff / lf / motion / KF / 布局 / 姿态 / 道具），
    而屏障零报告——「审计段只读」这条契约只在注释里成立。

    两类落盘（由 `main.py::_record_semantic_write_audit` 写入 `_semantic_write_audit`）：

      - `unauthorized` → **blocking**：审计段里**未受豁免的结构写入**——
        **镜头数变化 / 对象增删替换（含等数量替换）/ 纯重排 / hook 有无 /
        角色 identifier 集合变化**。**逐镜语义字段的改写不在其列**：审计段之后的
        71 个硬修复约束本来就是靠写 `prompt`/`ff_desc`/`lf_desc` 工作的，
        把它们判越权会让每次运行必然 BLOCK（见 v6.98/v6.99 的两次收窄记录）。
      - `sanctioned` → **advisory 留证**：`STRUCTURAL_SANCTIONED_WRITERS` 里的约束。
        **v6.99 起该白名单为空集**（开篇空镜删除已改为 report-only），保留这条分支
        是为了让「审计段允许哪些结构写入者」继续是一个可审查的显式声明。
    """
    audit = script.get("_semantic_write_audit")
    if not isinstance(audit, dict):
        return []
    out: list[Issue] = []
    for rec in audit.get("unauthorized") or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("constraint") or "?")
        diff = rec.get("diff") or []
        first = str(diff[0]) if diff else ""
        out.append(Issue(
            "semantic_audit_write", "hard",
            f"审计段约束 {name} 改动了语义/结构（{len(diff)} 处）：{first}"
            " —— 审计段必须只读；镜头增删/重排/语义改写会让链前结论对最终对象不成立；"
            "该写入必须降为只报告，或登记进 STRUCTURAL_SANCTIONED_WRITERS 并给出理由",
            shot_id=_semantic_audit_shot_id(first),
            field_path="shots",
            evidence="; ".join(str(d) for d in diff[:3]),
            repairable=False,
        ))
    for rec in audit.get("sanctioned") or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("constraint") or "?")
        diff = rec.get("diff") or []
        first = str(diff[0]) if diff else ""
        out.append(Issue(
            "structural_write_sanctioned", "advisory",
            f"审计段受豁免的结构写入 {name}（{len(diff)} 处）：{first}"
            " —— 产品行为（开篇空镜合并），默认不阻塞；被删镜的 KF/环境多样性随 del 丢失",
            shot_id=_semantic_audit_shot_id(first),
            field_path="shots",
            evidence="; ".join(str(d) for d in diff[:3]),
            repairable=False,
        ))
    return out


def _audit_shot_id(diff_line: str):
    """从 `shot 3.duration_sec: 4 -> 8` 取出 shot id；取不到就返回 None."""
    m = re.search(r"index=([^)]+)\)", str(diff_line or ""))
    if m:
        return m.group(1)
    m = re.match(r"shot\s+(\S+?)\.", str(diff_line or ""))
    return m.group(1) if m else None


def final_time_invariant_issues(script: dict) -> list[Issue]:
    """**最终时间不变量**（v6.83，二次审核发现 1 实施 #4）.

    必须在**最后一个**时间写入者之后运行——即 `finalize_step01_script()` 里
    hard-fix 与 timeline 序列化都跑完之后。三条判据针对的是同一个失效模式：
    某个写入者在 KF 时间检查之后改了时长，于是链前的「KF 落在镜内」结论对最终
    对象不再成立。

      - 每个 KF `0 < t_sec <= duration_sec` → `hard`
      - `duration == duration_sec`（同值口径）→ `hard`
      - `duration == timecode.end - timecode.start`（时间轴自洽）→ `hard`

    与 `_keyframe_timeline_issues` 的分工：那里查帧**之间**的关系（递增/过近/
    重复），这里查帧与**镜头时长的边界**关系——两者此前都没有覆盖
    `t_sec > duration_sec`，所以 12s 夹成 8s 后 `t_sec=10` 的帧可以静默落盘。
    """
    from shared.kf_time import get_kf_t_sec

    out: list[Issue] = []
    units: list[tuple[object, dict]] = []
    if isinstance(script.get("hook"), dict):
        units.append(("hook", script["hook"]))
    units += [(s.get("index", i + 1), s) for i, s in enumerate(script.get("shots") or [])
              if isinstance(s, dict)]

    for shot_id, shot in units:
        # v6.84（二次审核发现 5）：`duration_sec` 缺失时**回退 `duration`**。
        # 原实现只读 `duration_sec`，而钩子/新生成镜头按 schema 只保证 `duration`
        # ——于是钩子质量门（跑在时间派生之前）里 dur=0，整个 KF 检查被
        # `if dur <= 0: continue` 跳过，`t_sec=0` 一路漏到落盘前才暴露。
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        raw_d = shot.get("duration")
        if raw_d is not None:
            try:
                if abs(float(raw_d) - dur) > 0.011:
                    out.append(Issue(
                        "duration_two_calibers", "hard",
                        f"duration({raw_d}) 与 duration_sec({shot.get('duration_sec')}) 不一致"
                        " —— 时长的唯一口径是 duration_sec 派生出的同值",
                        shot_id=shot_id, field_path="duration_sec",
                        evidence=f"{raw_d} vs {shot.get('duration_sec')}",
                        repairable=False,
                    ))
            except (TypeError, ValueError):
                pass
        tc = shot.get("timecode")
        if isinstance(tc, dict):
            try:
                span = float(tc.get("end")) - float(tc.get("start"))
            except (TypeError, ValueError):
                span = None
            if span is not None and abs(span - dur) > 0.011:
                out.append(Issue(
                    "timecode_span_mismatch", "hard",
                    f"timecode 跨度 {span:.2f}s ≠ duration_sec {dur:.2f}s",
                    shot_id=shot_id, field_path="duration_sec",
                    evidence=f"span={span:.3f} dur={dur:.3f}",
                    repairable=False,
                ))
        if dur <= 0:
            continue
        for j, kf in enumerate(shot.get("keyframes") or []):
            if not isinstance(kf, dict):
                continue
            t = get_kf_t_sec(kf, shot)
            if t is None:
                continue
            if t <= 0:
                # v6.84（发现 5）：`t_sec<=0` 是**首帧重复**问题（首帧与镜头起点
                # 重合，等于让图片模型连画两帧同一时刻），不是「时长被链后写坏」。
                # 单独编码，避免误导定位。
                out.append(Issue(
                    "kf_at_shot_start", "hard",
                    f"第{j}个关键帧 t_sec={t:.3f}s 落在镜头起点（契约要求 "
                    f"0 < t_sec <= duration_sec）——首帧与开场态重复",
                    shot_id=shot_id, frame_id=f"kf{j}", field_path="keyframes.t_sec",
                    evidence=f"t_sec={t:.3f} duration_sec={dur:.3f}",
                    repairable=False,
                ))
            elif t > dur + 0.01:
                out.append(Issue(
                    "kf_time_out_of_shot", "hard",
                    f"第{j}个关键帧 t_sec={t:.3f}s 越出镜长 {dur:.2f}s"
                    " —— 时长在 KF 检查之后被改过（链后写入），该帧不可能被拍到",
                    shot_id=shot_id, frame_id=f"kf{j}", field_path="keyframes.t_sec",
                    evidence=f"t_sec={t:.3f} duration_sec={dur:.3f}",
                    repairable=False,
                ))
    return out


def _kf_segment_missing_issues(shot: dict, shot_id) -> list[Issue]:
    """缺段检测：某明确动作时段没有任何帧归属它 → 结构化 blocking.

    v6.143（2026-09-27 用户裁决）：判据来自唯一事实源 `kf_coverage.analyze`
    ——「一帧只归属一个明确事件」，边界帧不再同时充当相邻两段的证据。
    CLAUDE.md v6.33 的「kf 个数 ≥ 时段数」此前只在约束层（HARD、无消费者）
    与校验层（warning-only）报，质量门不收，于是「约束报缺帧 + 质量门 PASS」
    并存。这里只接入**已核实**的那一类；其它约束 HARD 不顺带升级。

    **零帧也要报**：显式分 N 段却一帧都没有是最严重的一类，故本函数在
    `_keyframe_timeline_issues` 的「无帧早退」**之前**调用。
    """
    # v6.143（2026-09-27 用户裁决）：**缺段**接入结构化质量门。
    #
    # 判据来自唯一事实源 `kf_coverage.analyze`——「一帧只归属一个明确事件」，
    # 边界帧不再同时充当相邻两段的证据。CLAUDE.md v6.33 的「kf 个数 ≥ 时段数」
    # 此前只在约束层（HARD、无消费者）与校验层（warning-only）报，质量门不收，
    # 于是「约束报缺帧 + 质量门 PASS」并存。这里只接入**已核实**的那一类：
    # 某明确动作时段没有任何帧归属它。其它约束 HARD 不顺带升级。
    # 某明确动作时段没有任何帧归属它
    issues: list[Issue] = []
    motion = str(shot.get("motion_desc") or "").strip()
    if motion:
        from step01_script_gen.kf_coverage import analyze as _analyze_kf_cov2
        _cov2 = _analyze_kf_cov2(shot)
        for _si in _cov2.missing:
            _a, _b, _body = _cov2.segments[_si]
            issues.append(Issue(
                code="kf_segment_missing", severity="visual_risk",
                message=(
                    f"motion 时段 {_a:g}-{_b:g}s「{str(_body)[:40]}」没有**按时间"
                    f"归属**它的关键帧（本镜 {len(_cov2.slots)} 帧 / "
                    f"{len(_cov2.required)} 个需要独立帧的时段）。"
                    f"**必须动作**（①或②，也可并用；验收口径是**每个需帧时段都有"
                    f"一帧、且该帧文案描写的是它自己那段**）："
                    f"① 在 keyframes **新增 1 帧**（t_sec 取 {(_a + _b) / 2:g}，"
                    f"即该段中点；time_ratio = 段中点 / duration_sec），desc 描写"
                    f"**该段动作**，并在 frame_layouts.kfs **按同序号追加等长条目**"
                    f"（两表长度必须始终相等）；"
                    f"② 若已有帧与相邻段**时间/内容错位**（帧文案写的是别段事件，"
                    f"或它正落在两段交界），**允许一并调整**该帧的 t_sec、desc 与"
                    f"对应的 frame_layouts.kfs 条目——但**只搬 t_sec 不改文案**、"
                    f"或只补不调，都会把同一条缺陷挪到相邻段（实测：1-2s 的"
                    f"「反手一掌」被画在 2.5s、文案写「掌风扫向」）。"
                    f"**裁决口径**：只要满足「每个需帧时段都有一帧、且该帧文案"
                    f"描写它自己那段」，①②怎么组合都算修好——同一帧若另有"
                    f"`kf_event_window_mismatch`（那条要求不改 t_sec），照它的"
                    f"要求**改写文案 + 为本段补帧**即可，不必搬 t_sec。"
                    f"**禁止**改写、删除或合并 motion_desc 来消除本条"
                    f"——那只会让告警消失，动作证据并没有增加。"
                ),
                shot_id=shot_id, frame_id=f"seg{_si}", field_path="keyframes",
                evidence=f"seg[{_si}] {_a:g}-{_b:g}s = {str(_body)[:60]}",
                repair_targets=[
                    {"shot_id": shot_id, "field_path": "keyframes"},
                    {"shot_id": shot_id, "field_path": "frame_layouts.kfs"},
                ],
                repairable=True,
            ))
    return issues


def _keyframe_timeline_issues(shot: dict, shot_id) -> list[Issue]:
    """Validate the final keyframe contract as one unit.

    Count alone is not enough: frames must be strictly ordered, no two frames
    may repeat the same event, and each frame layout must describe the same
    character state as its description.
    """
    from shared.kf_time import get_kf_t_sec

    kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    # v6.143：缺段先判——**零帧**是最严重的缺段，不能落在早退之后
    issues: list[Issue] = _kf_segment_missing_issues(shot, shot_id)
    if not kfs:
        return issues

    timed: list[tuple[int, dict, float | None]] = []
    for i, kf in enumerate(kfs):
        timed.append((i, kf, get_kf_t_sec(kf, shot)))

    # Strict timeline; same/near-same moments are ambiguous for image/video
    # generation and must not be repaired by merely re-spreading identical text.
    prev_t: float | None = None
    prev_i = -1
    for i, _kf, t in timed:
        if t is None:
            issues.append(Issue(
                code="kf_time_missing", severity="visual_risk",
                message=f"第{i}个关键帧缺少可解析的 t_sec/time_ratio",
                shot_id=shot_id, frame_id=f"kf{i}", field_path="keyframes.t_sec",
                repairable=True,
            ))
            continue
        if prev_t is not None:
            if t <= prev_t + 1e-6:
                issues.append(Issue(
                    code="kf_time_not_strict", severity="visual_risk",
                    message=f"关键帧时间未严格递增：kf{prev_i}={prev_t:.3f}s，kf{i}={t:.3f}s",
                    shot_id=shot_id, frame_id=f"kf{prev_i}->kf{i}",
                    field_path="keyframes.t_sec", evidence=f"{prev_t:.3f} -> {t:.3f}",
                    repairable=True,
                ))
            elif t - prev_t < 0.08:
                issues.append(Issue(
                    code="kf_time_too_close", severity="visual_risk",
                    message=f"关键帧间隔过小：kf{prev_i}->kf{i} 仅 {t - prev_t:.3f}s，无法表达两个独立状态",
                    shot_id=shot_id, frame_id=f"kf{prev_i}->kf{i}",
                    field_path="keyframes.t_sec", repairable=True,
                ))
        prev_t, prev_i = t, i

    for i in range(len(timed)):
        ii, kf_i, ti = timed[i]
        for j in range(i + 1, len(timed)):
            jj, kf_j, tj = timed[j]
            if ti is None or tj is None or abs(tj - ti) > 0.30:
                continue
            similarity = _kf_similarity(
                str(kf_i.get("desc") or ""), str(kf_j.get("desc") or "")
            )
            if similarity >= 0.90:
                issues.append(Issue(
                    code="kf_event_duplicate", severity="visual_risk",
                    message=f"kf{ii} 与 kf{jj} 在 {abs(tj - ti):.3f}s 内重复表达同一事件（相似度 {similarity:.0%}）",
                    shot_id=shot_id, frame_id=f"kf{ii}->kf{jj}",
                    field_path="keyframes.desc", repairable=True,
                ))
                break

    layouts = shot.get("frame_layouts")
    layout_kfs = layouts.get("kfs") if isinstance(layouts, dict) else None
    if isinstance(layout_kfs, list) and len(layout_kfs) != len(kfs):
        issues.append(Issue(
            code="kf_layout_count_mismatch", severity="visual_risk",
            message=f"keyframes={len(kfs)} 与 frame_layouts.kfs={len(layout_kfs)} 数量不一致",
            shot_id=shot_id, field_path="frame_layouts.kfs", repairable=True,
        ))

    # Per-frame description/layout pose consistency.
    if isinstance(layout_kfs, list):
        for i, kf in enumerate(kfs):
            desc = str(kf.get("desc") or "")
            if not desc:
                continue
            frame = layout_kfs[i] if i < len(layout_kfs) and isinstance(layout_kfs[i], list) else []
            frame_names = [str(item.get("name") or "").strip() for item in frame
                           if isinstance(item, dict)]
            for item in frame:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or name not in desc:
                    continue
                clauses = []
                for clause in re.split(r"[，。；！？、]", desc):
                    subject, span = leading_named_span(clause, frame_names)
                    if subject == name:
                        clauses.append(span)
                if not clauses:
                    continue
                if any(w in "".join(clauses) for w in ("从", "转为", "随后", "继而", "起身", "坐起", "躺下", "倒下", "翻倒")):
                    continue
                desc_cats = set().union(*(_pose_categories(c) for c in clauses))
                layout_cats = _pose_categories(str(item.get("pose") or ""))
                if ({"sit", "lie"} <= desc_cats) or (
                    "sit" in desc_cats and "lie" in layout_cats
                ) or (
                    "lie" in desc_cats and "sit" in layout_cats
                ):
                    issues.append(Issue(
                        code="kf_desc_layout_pose_conflict", severity="visual_risk",
                        message=f"{name} 的关键帧描述与 frame_layouts.kfs[{i}] 姿态互斥",
                        shot_id=shot_id, frame_id=f"kf{i}", entity_id=name,
                        field_path=f"frame_layouts.kfs[{i}].pose",
                        evidence=f"desc={desc[-120:]} layout_pose={item.get('pose')}",
                        repairable=True,
                    ))

    # Event text must match the motion segment it occupies.  This catches a
    # frame that was only moved by timestamp while its content still belongs to
    # an earlier or later event.
    motion = str(shot.get("motion_desc") or "").strip()
    if motion and len(timed) >= 2:
        try:
            # 2026-09-27：归属与错位判据改走**唯一判据** `kf_coverage.analyze`
            # ——同一函数也是 `kf_event_order` 与 `_check_kf_time_window_alignment`
            # 的判据来源，三处不再各持一套口径。阈值未动（0.45 / 0.12）。
            from step01_script_gen.kf_coverage import analyze as _analyze_kf_coverage
            scene_prefix = str(shot.get("ff_desc") or "").split("，", 1)[0].strip()
            if scene_prefix:
                scene_prefix += "，"
            _cov = _analyze_kf_coverage(shot, scene_prefix=scene_prefix)
            segs = _cov.segments
            if len(segs) >= 2:
                for _slot in _cov.slots:
                    if not _slot.mismatched:
                        continue
                    own, best = _slot.own, _slot.best
                    # v6.106（2026-09-24 第二十八次重跑实测）：原标题只说「更匹配
                    # 另一个时段」，模型连改两轮都没改对（判据是字符重叠相似度，
                    # 它不知道往哪儿写）。把**本时段该写什么**直接给出——与缺段
                    # 告警给「缺段内容」同一做法：可确定的部分算好交给模型。
                    issues.append(Issue(
                        code="kf_event_window_mismatch", severity="visual_risk",
                        message=(
                            f"kf{_slot.index} 位于 {segs[own][0]:g}-{segs[own][1]:g}s，"
                            f"但内容更匹配 {segs[best][0]:g}-{segs[best][1]:g}s 的事件。"
                            f"**必须动作**：把该帧 desc 改写成描写**本时段**"
                            f"[{segs[own][0]:g}-{segs[own][1]:g}s] 的事件"
                            f"（该时段内容：「{str(segs[own][2] if len(segs[own]) > 2 else '')[:40]}」），"
                            f"不得再描写 {segs[best][0]:g}-{segs[best][1]:g}s 的事件"
                            f"（「{str(segs[best][2] if len(segs[best]) > 2 else '')[:30]}」），"
                            f"也不得改动该帧的 t_sec。"
                        ),
                        shot_id=shot_id, frame_id=f"kf{_slot.index}",
                        field_path="keyframes.desc",
                        evidence=_slot.desc[-120:], repairable=True,
                    ))
        except Exception:
            pass

    return issues


# ---------------------------------------------------------------------------
# v6.124（用户口径 (a) 窄版）：kf 描述里**明确可见**的人物缺 `frame_layouts.kfs[i]`
# 条目 → 结构化门 blocking
#
# 为什么要有它（2026-09-25 冻结稿实测）：
#   · 这类缺陷**确定性存在**——约束引擎 `frame_spec_consistency` 在冻结稿上报了
#     19 处 HARD（`[CONSTRAINT] HARD 未解决 37 处` 的一部分）；
#   · 但**质量门 blocking = 0**——`collect_structured_issues` 里没有这一类的检测器，
#     所以「把类别名加进 `_QUALITY_BLOCKING_CATEGORIES`」解决不了漏检（那只影响
#     checklist 告警的严重度分级）；
#   · 而模型审计只在部分轮次报出来（同一 revision 两次独立审计：3 vs 10 条 blocking、
#     code 无一重合）。
#   → 于是「最终 blocking 为零」推不出「没有已知 HARD 缺陷」。
#
# 为什么是**窄版**：`frame_spec_consistency._check_roster_coverage` 只要 desc 里
# 出现名字就报冲突（连「已无某人」「已走远」这类画外描述也报）。整类升级为 blocking
# 会造出大量**误拦**（用户 2026-09-25 核对）。这里只拦**能确认画面上看得见**的：
#   · 名字后紧跟可见状态/动作/体征词（`_KF_VISIBLE_MARKERS`）；
#   · 且名字附近没有画外/封闭/否定标记（`_KF_OFFSCREEN_MARKERS`）；
#   · 拿不准的（「听香在车厢内」「转向听香，盯着她」）**不拦**——只收窄召回，
#     不制造不可修的 BLOCK。
# ---------------------------------------------------------------------------
# 名字**之后**（同一分句内）出现的可见证据。刻意只收「能在画面里被看到」的词：
# 姿态/位置/外观/动作/伤情。**不含**「盯/看/望」这类注意动词——被注视的人未必入画。
_KF_VISIBLE_MARKERS = (
    # 姿态 / 位置 / 外观
    "撑伞", "静立", "站立", "站着", "起身", "跪坐", "跪", "坐", "躺", "仰卧",
    "俯身", "趴", "伏", "靠", "倚", "蹲", "披头散发", "满面", "脸色", "神情",
    # 动作（手/身体可被看到）
    "环住", "抱", "搂", "扶", "牵", "拎", "提", "握", "持", "抬", "伸", "挥",
    "按", "推", "拉", "扇", "拍", "抓", "捂", "指", "点", "摇", "跪伏",
    # 可见表情 / 发声动作
    "尖叫", "唱歌", "哭", "笑", "点头", "摇头", "瞪", "睁", "张口", "张嘴",
    # 伤情 / 体征（画面上可见）
    "心口", "胸口", "洇痕", "血痕", "血迹", "伤口", "暗红",
)
# 名字**附近**出现这些 → 不判「明确可见」（画外 / 封闭空间 / 否定 / 仅声音）
_KF_OFFSCREEN_MARKERS = (
    "已走远", "走远", "不见踪影", "没入", "入水", "沉入", "尚未现身", "未现身",
    "没有出现", "画外", "画面外", "背对", "背影", "只有声音", "传来", "声音",
    "听见", "车厢", "车内", "屋内", "屋里", "门内", "门后", "屏风后", "帐内",
    "远处", "消失", "不见", "已无", "不在了", "没有",
)


def _kf_name_clearly_visible(name: str, desc: str) -> bool:
    """`name` 在该 desc 里是否有**可确认的可见证据**（保守判据，无证据即不判）.

    **按分句判定**（v6.124 实测修正）：画外/否定标记必须与本名字在**同一分句**内
    才算数——按固定字符窗口看会把别人的状态算到这个名字头上：

        「…听香轻启朱唇唱歌，老农已走远」→ 12 字窗口里含「走远」→ 误判她画外。

    同一分句内再要求「名字**之后**」出现可见证据（`听香←撑伞/跪坐/尖叫`），
    避免把「A 盯着 B」里的 B 算成明确可见。
    """
    import re as _re

    for clause in _re.split(r"[，。；、！？：,;!?]", desc or ""):
        if name not in clause:
            continue
        if any(w in clause for w in _KF_OFFSCREEN_MARKERS):
            continue
        for m in _re.finditer(_re.escape(name), clause):
            after = clause[m.end():]
            if any(w in after for w in _KF_VISIBLE_MARKERS):
                return True
    return False


def _kf_person_visibility_issues(shot: dict, shot_id) -> list[Issue]:
    """明确可见但在该帧布局里没有条目的人物（v6.124，窄版）.

    **成对修复目标**（v6.124.1，用户 2026-09-25 核对）：本检测器自己声明精确的
    `repair_targets` = `keyframes[i].desc` + `frame_layouts.kfs[i]`，**点名到帧**。
    此前靠 `_KF_STRUCTURAL_CODES` 给「KF 列表本身」的列表级授权，会让模型连带
    获得同镜**其他帧**的 `keyframes`/`frame_layouts.kfs` 权限（用户两帧最小样例
    复现：只报 kf0 缺人，kf1 的描述与布局也可改）——超出窄检测器的本意。
    """
    from shared.frame_spec import is_on_screen

    fl = shot.get("frame_layouts")
    if not isinstance(fl, dict):
        return []
    # v6.124.1：**不得**先过滤掉非列表行再按下标取帧。`kfs` 里混进一行坏数据时，
    # 过滤会让其后的帧整体前移一位，`kfs[i]` 与 `keyframes[i]` 就此错位（漏真帧、
    # 报错帧）。按**原始下标**对齐：坏行 = 该帧没有任何有效条目，照常判缺人
    # ——坏数据不是「没问题」（准则 7），且修复目标 `frame_layouts.kfs[i]` 仍然
    # 是该镜的真实路径，能被授权改写回一个合法列表。
    kfs = fl.get("kfs")
    if not isinstance(kfs, list):
        return []
    roster = [str(e.get("name", "")).strip()
              for e in (shot.get("char_layout") or []) if isinstance(e, dict)]
    roster = [n for n in roster if n]
    if not roster or not kfs:
        return []

    issues: list[Issue] = []
    for i, kf in enumerate(shot.get("keyframes") or []):
        if not isinstance(kf, dict) or i >= len(kfs):
            continue
        desc = str(kf.get("desc") or "")
        if not desc:
            continue
        _row = kfs[i] if isinstance(kfs[i], list) else []
        present = {str(e.get("name", "")).strip() for e in _row
                   if isinstance(e, dict) and is_on_screen(e)}
        for name in roster:
            if name in present or name not in desc:
                continue
            if not _kf_name_clearly_visible(name, desc):
                continue
            issues.append(Issue(
                code="kf_person_missing_in_layout", severity="hard",
                message=(
                    f"关键帧 kf{i} 的描述明确可见「{name}」，但 "
                    f"frame_layouts.kfs[{i}] 里没有该角色条目——按布局出图会少一个人。"
                    f"修法二选一：①在 `frame_layouts.kfs[{i}]` 补该角色的条目"
                    f"（name/pose/position 等与描述一致）；②若她/他其实不在画面内，"
                    f"把 `keyframes[{i}].desc` 改成明确画外的写法。"),
                shot_id=shot_id, frame_id=f"kf{i}", entity_id=name,
                field_path=f"frame_layouts.kfs[{i}]",
                # 两种修法各自对应**本帧**的一个字段，点名到帧（不带下标 = 授权整镜
                # 同名字段，那正是要避免的越权面）
                repair_targets=[
                    {"shot_id": shot_id, "field_path": f"keyframes[{i}].desc"},
                    {"shot_id": shot_id, "field_path": f"frame_layouts.kfs[{i}]"},
                ],
                evidence=desc[:180], repairable=True,
            ))
    return issues


def _split_boundary_issues(script: dict) -> list[Issue]:
    """后镜重放了**前一镜尾帧已完成**的动作 → blocking（v6.84 起覆盖所有相邻镜对）.

    原实现只对**半整数** index 生效（`_do_pre_split_oversized_shots` 当年拆出的
    子镜）。阶段 1 的 `shot_index_renumber` 把 index 归一成连续整数之后，这条检测
    会彻底失效——而它抓的缺陷（后镜把前一镜已经演完的动作再演一遍）**与拆镜无关**，
    任何相邻镜对都可能发生。所以把范围从「拆镜对」放成「所有相邻镜对」，
    判据、阈值和状态回退守卫都不变。
    """
    from difflib import SequenceMatcher
    from step01_script_gen.state_updates import (
        extract_states, has_role_state_regression,
    )

    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    issues: list[Issue] = []

    for pair_i in range(1, len(shots)):
        shot, prev = shots[pair_i], shots[pair_i - 1]
        if not isinstance(prev, dict):
            continue
        prev_lf = str(prev.get("lf_desc") or "").strip()
        motion = str(shot.get("motion_desc") or "").strip()
        if not prev_lf or not motion:
            continue
        prev_clauses = [c.strip() for c in re.split(r"[，。；、]", prev_lf) if c.strip()]
        names = _char_names(shot)

        def _intermediate_change(earlier: list[str], candidate: str) -> bool:
            previous = extract_states(prev_lf, names)
            candidate_states = extract_states(candidate, names)
            earlier_states = [extract_states(body, names) for body in earlier]
            return has_role_state_regression(earlier_states, previous, candidate_states)

        segments = [x.strip() for x in re.split(r"[;；]", motion) if x.strip()]
        earlier_bodies: list[str] = []
        for seg_i, atom in enumerate(segments):
            m = re.match(
                r"(?P<range>\d+(?:\.\d+)?\s*(?:-|~|至|到)\s*\d+(?:\.\d+)?\s*秒?)"
                r"\s*[：:]\s*(?P<body>.*)",
                atom,
            )
            body = m.group("body").strip() if m else ""
            if seg_i == 0:
                if body:
                    earlier_bodies.append(body)
                continue
            if not m or not body:
                continue
            score = max(
                (SequenceMatcher(None, body, c).ratio() for c in prev_clauses),
                default=0.0,
            )
            if score >= 0.62 and not _intermediate_change(earlier_bodies, body):
                issues.append(Issue(
                    code="split_boundary_replay", severity="visual_risk",
                    message=(f"镜头{shot.get('index')} {m.group('range')} 重复前一镜"
                             f"（镜头{prev.get('index')}）尾帧已完成的动作"
                             f"（相似度 {score:.0%}）"),
                    shot_id=shot.get("index"),
                    field_path="motion_desc",
                    evidence=body,
                    repairable=True,
                ))
                break
            earlier_bodies.append(body)
    return issues


def _timeline_issues(script: dict) -> list[Issue]:
    if not script.get("timeline"):
        return []
    try:
        from step01_script_gen.timecode import timeline_consistency_errors
        errors = timeline_consistency_errors(script)
    except Exception as exc:
        return [Issue("CHECK_FAILED", "hard", f"时间轴检查异常: {exc}", repairable=False)]
    return [Issue("timeline_consistency", "hard", err,
                  field_path="timeline", repairable=False) for err in errors]


def _duration_budget_issues(script: dict) -> list[Issue]:
    """Make the project's 60-90s acceptance range a blocking invariant.

    The prompt plans for 70-80s and the old redguo checker only emitted
    advisory warnings.  Without this gate, a 100s script could pass while
    still being outside the product target.
    """
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        return []
    durations = []
    for shot in shots:
        try:
            durations.append(float(shot.get("duration_sec") or shot.get("duration") or 0))
        except (TypeError, ValueError):
            return []
    total = sum(durations)
    if 60.0 - 0.01 <= total <= 90.0 + 0.01:
        return []
    if total > 90.0:
        msg = (f"正片总时长 {total:.1f}s > 90s，违反 60-90s 验收区间；"
               "需精简剧情/动作或合并镜头，不能静默截断台词或动作")
    else:
        msg = (f"正片总时长 {total:.1f}s < 60s，低于 60-90s 验收区间；"
               "需补充有效镜头或重新分配时长")
    return [Issue("duration_budget", "visual_risk", msg,
                  field_path="total_duration", repairable=False)]


def _adjacent_duplicate_issues(script: dict) -> list[Issue]:
    """相邻镜视觉重复 → blocking issue（v6.79，2026-09-20）.

    2026-09-19 审核发现 2：`duplicate_shot_merge` 的自动合并已被删除
    （Jaccard 相似不等于事件等价，按比例缩放 KF 会把关键帧搬离它所属的事件）。
    检测本身仍然必要——重复视觉会白占时长、拉低观感——所以缺陷改由 gate 侧
    以 blocking 上报，走**完整镜头 regen** 重写其中一镜。

    判定与 Constraint 层共用 `duplicate_shot_merge.find_adjacent_duplicate_pairs()`，
    不存在第二套阈值。
    """
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if len(shots) < 2:
        return []
    try:
        from step01_script_gen.constraints.duplicate_shot_merge import (
            find_adjacent_duplicate_pairs,
        )
    except Exception:
        return []
    issues: list[Issue] = []
    for _i, _j, sim in find_adjacent_duplicate_pairs(shots):
        a, b = shots[_i], shots[_j]
        issues.append(Issue(
            code="adjacent_duplicate_visuals",
            severity="visual_risk",
            message=(
                f"镜头{a.get('index')}/{b.get('index')} 相邻且视觉重复"
                f"（动作文本相似度 {sim:.0%}，同地点同说话人）——"
                "需重写其中一镜的动作/构图，不能靠自动合并（会删除后镜事件与关键帧）"
            ),
            shot_id=b.get("index"),
            related_shot_ids=[a.get("index")],
            field_path="motion_desc",
            evidence=f"jaccard={sim:.4f}",
            repairable=False,
        ))
    return issues


# 结构收敛类阻塞码（v6.84，二次审核发现 1）：
# 这两类缺陷**不能**由字段级修复解决——它们要求重新规划镜头切分（把超载段落拆成
# 多个完整子镜、把过短镜头并入相邻剧情）。把它们单独成集，是为了让候选选择与
# 收敛轮能**精确点名**结构问题，而不是混在 29 条 blocking 里被稀释。
STRUCTURAL_SHOT_CODES = ("shot_content_over_cap", "shot_duration_below_min")


def _shot_duration_issues(script: dict) -> list[Issue]:
    """单镜时长硬不变量 → blocking（v6.79，2026-09-20）.

    2026-09-19 审核发现 1：链尾的四个时长写入者（`short_shot_merge` 拉长短镜、
    `motion_time_bounds` 按动作文本扩时长、`duplicate_shot_merge` 求和、
    `duration_budget` 按比例压缩）全部发生在 KF 时间检查（约束序号 8-11）之后，
    使「审计通过的时长」≠「最终时长」。它们已全部降为只报告，缺口由此处补上：

      - **单镜超上限** `min(项目 8s, 模型 cap)` → `hard`
        （链尾 `redguo_standard` 只产生 advisory 且不在 blocking 类别里，
         等于没有强制力——这里是它缺失的强制面）
      - **内容最低需求超上限**（台词念不完 / 动作时段超过 cap）→ `hard`
        v6.79 决定 3：拆镜 pre-pass 已移出生产路径，超 cap 镜头不再被悄悄
        复制成两镜，而是在这里 BLOCK 并进入结构 regen。必须报「需求」而不是
        「当前时长」——`_compute_timecodes` 会把时长夹到 cap，只看最终时长
        永远发现不了内容放不下。
      - **非末镜短于 3.5s** → `visual_risk`
        （末镜由 W6 单独保证 ≥ 4s）

    阈值与 `constraints/short_shot_merge.py` 同源，不另立一套。
    """
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        return []
    try:
        from step01_script_gen.timecode import (
            MAX_SHOT_DURATION, MIN_SHOT_DURATION, NARRATION_FIXED_DURATION,
            _estimate_duration, _extract_motion_duration,
        )
    except Exception:
        MAX_SHOT_DURATION, MIN_SHOT_DURATION, NARRATION_FIXED_DURATION = 15.0, 3.0, 4.0
        _estimate_duration = _extract_motion_duration = None
    try:
        from shared.dialogue import has_dialogue
    except Exception:
        has_dialogue = None
    try:
        from shared.model_caps import active_video_duration_cap
        _model_cap = float(active_video_duration_cap() or 0)
    except Exception:
        _model_cap = 0.0
    try:
        from step01_script_gen.timecode import active_shot_cap as _asc
        shot_cap = _asc()
    except Exception:
        shot_cap = min(float(MAX_SHOT_DURATION), _model_cap) if _model_cap > 0 \
            else float(MAX_SHOT_DURATION)
    try:
        from step01_script_gen.constraints.short_shot_merge import MIN_SHOT_DURATION_SEC
    except Exception:
        MIN_SHOT_DURATION_SEC = 3.5

    issues: list[Issue] = []
    for i, shot in enumerate(shots):
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue
        if dur > shot_cap + 0.01:
            issues.append(Issue(
                code="shot_duration_over_cap",
                severity="hard",
                message=(
                    f"镜头{shot.get('index')} 时长 {dur:.2f}s 超过单镜上限 {shot_cap:.1f}s"
                    "（项目 8s 与模型 cap 取严）——必须拆镜或精简动作，"
                    "不得依赖链尾约束事后压缩"
                ),
                shot_id=shot.get("index"),
                field_path="duration_sec",
                evidence=f"duration_sec={dur} shot_cap={shot_cap}",
                repairable=False,
            ))
        # 内容最低需求 vs 单镜上限：只在能拿到估时函数时算
        if _estimate_duration is not None:
            narration = str(shot.get("narration_segment") or "")
            motion_min = (_extract_motion_duration(str(shot.get("motion_desc") or "")) or 0.0)
            speech_min = 0.0
            if narration:
                if has_dialogue is not None and has_dialogue(narration):
                    speech_min = _estimate_duration(narration)
                elif has_dialogue is None:
                    speech_min = _estimate_duration(narration)
                else:
                    speech_min = float(NARRATION_FIXED_DURATION)
            need = max(motion_min, speech_min, float(MIN_SHOT_DURATION))
            if need > shot_cap + 0.01:
                issues.append(Issue(
                    code="shot_content_over_cap",
                    severity="hard",
                    message=(
                        f"镜头{shot.get('index')} 内容最低需求 {need:.1f}s 超过单镜上限 "
                        f"{shot_cap:.1f}s（台词估时 {speech_min:.1f}s / 动作时段 "
                        f"{motion_min:.1f}s）——必须拆成多个子镜或精简台词/动作。"
                        "拆镜 pre-pass 已停用，本镜不会被自动拆分"
                    ),
                    shot_id=shot.get("index"),
                    field_path="duration_sec",
                    evidence=f"need={need:.2f} shot_cap={shot_cap}",
                    repairable=False,
                ))
        is_last = (i == len(shots) - 1)
        if not is_last and dur < MIN_SHOT_DURATION_SEC - 0.01:
            issues.append(Issue(
                code="shot_duration_below_min",
                severity="visual_risk",
                message=(
                    f"镜头{shot.get('index')} 时长 {dur:.2f}s 低于 {MIN_SHOT_DURATION_SEC}s 下限"
                    "——需合并到相邻镜或补充 motion_desc/台词，不能靠事后加秒数"
                ),
                shot_id=shot.get("index"),
                field_path="duration_sec",
                evidence=f"duration_sec={dur}",
                repairable=False,
            ))
    return issues


def _text_integrity_issues(script: dict) -> list[Issue]:
    from shared.utils.text import text_integrity_errors
    issues: list[Issue] = []
    targets = [("hook", script.get("hook"))] if isinstance(script.get("hook"), dict) else []
    targets += [(s.get("index", i + 1), s) for i, s in enumerate(script.get("shots") or [])]
    for shot_id, shot in targets:
        if not isinstance(shot, dict):
            continue
        for field_name in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            text = str(shot.get(field_name) or "")
            for err in text_integrity_errors(text):
                issues.append(Issue(
                    code="text_integrity", severity="visual_risk",
                    message=err, shot_id=shot_id, field_path=field_name,
                    evidence=text[-120:], repairable=False,
                ))
    return issues


def collect_structured_issues(script: dict, blocking_categories: set[str],
                              video_only_categories: set[str],
                              checklist_runner=None) -> tuple[list[Issue], bool, str]:
    """Return (issues, checker_failed, rule_version)."""
    if checklist_runner is None:
        from step01_script_gen.validation import _run_prompt_checklist as checklist_runner
    issues: list[Issue] = []
    checker_failed = False
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = checklist_runner(script).get("results", {})
        for category, result in results.items():
            if not isinstance(result, dict):
                continue
            structured = result.get("structured_issues")
            if isinstance(structured, list):
                issues.extend(Issue(**record) for record in structured)
            for msg in ([] if isinstance(structured, list) else list(result.get("issues") or [])):
                sid, related = parse_shot_ref(str(msg))
                issues.append(Issue(category, "hard", str(msg), sid,
                                    related_shot_ids=related, field_path=category,
                                    repairable=True))
            for msg in list(result.get("warnings") or []):
                sid, related = parse_shot_ref(str(msg))
                if category in video_only_categories:
                    sev = "video_advisory"
                elif category in blocking_categories:
                    sev = "visual_risk"
                else:
                    sev = "advisory"
                issues.append(Issue(category, sev, str(msg), sid,
                                    related_shot_ids=related, field_path=category,
                                    repairable=sev == "visual_risk"))
    except Exception as exc:
        checker_failed = True
        issues.append(Issue("CHECK_FAILED", "hard", f"质量检查器异常: {exc}",
                            repairable=False))
    issues.extend(_text_integrity_issues(script))
    issues.extend(_timeline_issues(script))
    issues.extend(_duration_budget_issues(script))
    issues.extend(_shot_duration_issues(script))
    # v6.101：开篇空镜过多——**非阻塞**，但进结构化收集（结构收敛轮据此定位）
    issues.extend(opening_empty_shot_issues(script))
    issues.extend(_split_boundary_issues(script))
    issues.extend(_adjacent_duplicate_issues(script))
    targets = [("hook", script.get("hook"))] if isinstance(script.get("hook"), dict) else []
    targets += [(s.get("index", i + 1), s) for i, s in enumerate(script.get("shots") or [])]
    for shot_id, shot in targets:
        if not isinstance(shot, dict):
            continue
        issues.extend(_eye_conflicts(shot, shot_id))
        issues.extend(_action_time_issues(shot, shot_id))
        issues.extend(_state_order_issues(shot, shot_id))
        issues.extend(_pose_conflicts(shot, shot_id))
        issues.extend(_surface_conflicts(shot, shot_id))
        issues.extend(_onscreen_prop_issues(shot, shot_id))
        issues.extend(_prop_release_owner_issues(shot, shot_id))
        issues.extend(_keyframe_timeline_issues(shot, shot_id))
        # v6.124（用户口径 (a) 窄版）：kf 明确可见的人物缺布局条目 → blocking
        issues.extend(_kf_person_visibility_issues(shot, shot_id))
    # v6.103（2026-09-23 总计划阶段 2）：四类**跨字段**矛盾（在场/持物/帧阶段/
    # 结果动作支撑）。此前只有 model audit 能发现它们，结构化层看不见——于是
    # audit 报 blocking 而确定性层 PASS，两条口径互相矛盾、谁也无法驱动修复。
    # 检测是纯读的（`cross_field_checks` 不改写任何字段），修复走字段授权事务。
    # 局部 import：`cross_field_checks` 反向上依赖本模块的 `Issue`。
    from step01_script_gen.cross_field_checks import cross_field_issues
    _cf_issues, _cf_failures = cross_field_issues(script)
    issues.extend(_cf_issues)
    if _cf_failures:
        # 检测器异常 = 该镜头**没有被检测**，不能当成「没问题」（审核 P1-2）。
        checker_failed = True
        issues.append(Issue(
            "CHECK_FAILED", "hard",
            "跨字段检测器异常（该镜头未被检测，禁止按 PASS 处理）: "
            + "; ".join(str(x) for x in _cf_failures[:3]),
            repairable=False))
    # v6.83（二次审核发现 1）：阶段屏障 + 最终时间不变量——两者都必须在**最后一个**
    # 时间写入者之后判定，所以放在这里（gate 收集期即落盘前最终对象）。
    issues.extend(_time_field_audit_issues(script))
    # v6.98（2026-09-22 审核 P0-3）：语义/结构写入与时间写入**同一时点**判定。
    issues.extend(_semantic_write_audit_issues(script))
    issues.extend(final_time_invariant_issues(script))
    # Stable dedupe.
    seen = set()
    out = []
    for issue in issues:
        key = (issue.code, issue.message, str(issue.shot_id), issue.frame_id, issue.entity_id, issue.field_path)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out, checker_failed, RULE_VERSION


def is_blocking(issue: Issue) -> bool:
    return issue.severity in ("hard", "visual_risk")


__all__ = ["Issue", "RULE_VERSION", "RULE_IMPLEMENTATION", "RULE_SOURCE_FINGERPRINT", "PROMPT_SOURCE_FINGERPRINT",
    "collect_structured_issues", "is_blocking", "parse_shot_ref", "final_time_invariant_issues", "STRUCTURAL_SHOT_CODES", "is_opening_empty_shot", "opening_empty_shot_issues"]
