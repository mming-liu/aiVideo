"""Report possible injury continuity gaps without inventing a wound or costume."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import (
    _INJURY_ANCHOR_TAIL,
)
from step01_script_gen.prompt_semantic import (
    STRONG_INJURY_ANCHORS as _STRONG_INJURY_ANCHORS,
    BLOOD_STATE_WORDS as _BLOOD_STATE_WORDS,
    COLLECTIVE_NAMES as _COLLECTIVE_NAMES,
    THREAT_KEYWORDS as _THREAT_KEYWORDS,
    POST_STATE_KEYWORDS as _POST_STATE_KEYWORDS,
)
from .base import Constraint
from .types import RepairRecord, Severity, Violation


def _is_victim_candidate(nm: str) -> bool:
    """个体受害者候选：非威胁、非泛指（群演/仆从等群像不是可追踪受害者）.

    16:34 run 实测：shot4 特写刺杀 main_subject='群演' 被当成受害者，
    shot6/7 伤锚延续漏补。与 validation._hard_fix_injury_continuity 同规则。
    """
    return bool(nm) and nm not in _THREAT_KEYWORDS and not any(w in nm for w in _COLLECTIVE_NAMES)


def _identify_victim(shot: dict) -> str | None:
    """从 build_idx 镜头推导 victim 角色名（4 来源 fallback）.

    来源顺序：① main_subject 剥离动作词（仅当与 char_layout name 完全匹配）
    ② char_layout role=次 非 threat/非泛指角色 ③ prompt/lf_desc 紧邻 anchor 词
    ④ char_layout 兜底.
    """
    cl_names_all = [
        str(item.get("name", "")).strip()
        for item in (shot.get("char_layout") or [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    # 来源①
    main_raw = shot.get("main_subject", "")
    if main_raw and "的" not in main_raw:
        stripped = main_raw
        for prefix in (
            "刺杀", "被刺", "倒地", "躺血", "奄奄一息", "濒死", "受伤", "倒下",
            "刺客", "神秘人", "黑影", "破水", "出水", "破门", "夜袭",
        ):
            stripped = stripped.replace(prefix, "")
        stripped = stripped.strip()
        if _is_victim_candidate(stripped) and stripped in cl_names_all:
            return stripped
    # 来源②
    for item in (shot.get("char_layout") or []):
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name", "")).strip()
        role = str(item.get("role", "")).strip()
        if not nm or role != "次":
            continue
        if _is_victim_candidate(nm):
            return nm
    # 来源③
    text = (shot.get("prompt", "") or "") + (shot.get("lf_desc", "") or "")
    for m in re.finditer(
        r"([一-鿿]{2,3})(?:胸口|心口|心窝|腹部|中刀|中剑|中枪|刀伤|剑伤|洇痕|暗伤|血痕)",
        text,
    ):
        nm = m.group(1)
        if _is_victim_candidate(nm):
            return nm
    # 来源④
    for item in (shot.get("char_layout") or []):
        if isinstance(item, dict):
            nm = str(item.get("name", "")).strip()
            if _is_victim_candidate(nm):
                return nm
    return None


def _is_victim_in_frame(shot: dict, victim: str) -> bool:
    """victim 出现在 4 字段任一 或 char_layout（背景角色也算）."""
    text = (
        (shot.get("ff_desc") or "")
        + (shot.get("motion_desc") or "")
        + (shot.get("prompt") or "")
        + (shot.get("lf_desc") or "")
    )
    if victim and victim in text:
        return True
    for item in (shot.get("char_layout") or []):
        if isinstance(item, dict) and victim == str(item.get("name", "")).strip():
            return True
    return False


class InjuryContinuityConstraint(Constraint):
    """A. 致命伤持续：build_idx 之后同 location 镜头缺 anchor 则追加.

    severity=WARN: 致命伤可由后续 repair round 修.
    repair_marker=_INJURY_ANCHOR_TAIL: 已含伤处描述视为已修.
    """

    name = "fatal_wound_continuity"
    severity = Severity.WARN
    repair_marker = _INJURY_ANCHOR_TAIL

    def apply(self, ctx) -> list[RepairRecord]:
        # 1. 找 build_idx（第一个含 STRONG anchor 词的 shot）——v6.51 与
        #    validation 版对齐：宽表含"白袍/胸口"等服装词，开篇即命中会误建
        build_idx = None
        build_loc = None
        for i, s in enumerate(ctx.shots):
            text = (
                (s.get("prompt", "") or "")
                + (s.get("ff_desc", "") or "")
                + (s.get("lf_desc", "") or "")
            )
            if any(w in text for w in _STRONG_INJURY_ANCHORS):
                build_idx = i
                build_loc = s.get("location", "") or s.get("scene", "")
                break
        if build_idx is None:
            return []

        # 2. 推导 victim
        victim = _identify_victim(ctx.shots[build_idx])
        if not victim:
            return []

        # 3. 后续同 location shot 缺 anchor → 追加
        #    v6.51：location 放宽「前 2 字一致」——LLM 自由文本 location
        #    （「小舟上，张十三质问听香」）与 build（「小舟船头」）精确串
        #    永不相等 → 延续层整体失明（16:34 run 实测）
        repairs: list[RepairRecord] = []
        for i in range(build_idx + 1, len(ctx.shots)):
            s = ctx.shots[i]
            loc = s.get("location", "") or s.get("scene", "")
            if not build_loc or (loc != build_loc and loc[:2] != build_loc[:2]):
                continue
            if not _is_victim_in_frame(s, victim):
                continue
            if self._already_repaired(s):
                continue

            prompt = s.get("prompt", "") or ""
            lf = s.get("lf_desc", "") or ""
            ff = s.get("ff_desc", "") or ""
            # 已锚判定用 BLOOD_STATE_WORDS（含 暗红/洇开 等变体），防重复追加
            prompt_has_anchor = any(w in prompt for w in _BLOOD_STATE_WORDS)
            lf_has_anchor = bool(lf) and any(w in lf for w in _BLOOD_STATE_WORDS)
            ff_has_anchor = bool(ff) and any(w in ff for w in _BLOOD_STATE_WORDS)

            if not prompt_has_anchor:
                before = prompt
                ctx.record_violation(Violation(
                    constraint_name=self.name, severity=Severity.WARN,
                    shot_index=s.get("index", i), field="prompt", repaired=False,
                    message="可能缺少伤情连续性；须复核同一人物及本帧可见范围，不注入默认衣服/伤处。",
                    evidence=before))
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="detect",
                        target="prompt",
                        before=before,
                        after=s["prompt"],
                    )
                )
            if lf and not lf_has_anchor:
                before = lf
                ctx.record_violation(Violation(
                    constraint_name=self.name, severity=Severity.WARN,
                    shot_index=s.get("index", i), field="lf_desc", repaired=False,
                    message="可能缺少伤情连续性；须复核同一人物及本帧可见范围，不注入默认衣服/伤处。",
                    evidence=before))
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="detect",
                        target="lf_desc",
                        before=before,
                        after=s["lf_desc"],
                    )
                )
            # v6.51：ff_desc 纳入——image 模式首帧图由 ff_desc 生成，缺锚会画出
            # 无伤受害者（16:34 run shot6/7 实测）。受害者出现在 ff 才补。
            if ff and not ff_has_anchor and victim in ff:
                before = ff
                ctx.record_violation(Violation(
                    constraint_name=self.name, severity=Severity.WARN,
                    shot_index=s.get("index", i), field="ff_desc", repaired=False,
                    message="可能缺少伤情连续性；须复核同一人物及本帧可见范围，不注入默认衣服/伤处。",
                    evidence=before))
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="detect",
                        target="ff_desc",
                        before=before,
                        after=s["ff_desc"],
                    )
                )

        # 4. v6.43 修复（08-25）：build_idx 之后镜头已处理，**hook 也需同步**
        # — 真实跑批发现 hook（独立于 shots 之外）含 victim + 缺 anchor 也会丢伤处描
        # 述（旧 _hard_fix_injury_continuity 同样不处理 hook；此 bug 由 v6.42 迁移
        # 完整保留）。hook 的 location 与 build_loc 不一定相同（hook 多在时间轴头部），
        # 故不强制 location 匹配：只要 hook 含 victim 且缺 anchor → 补。
        hook = ctx.hook
        if isinstance(hook, dict) and victim:
            h_text = (
                (hook.get("prompt", "") or "")
                + (hook.get("ff_desc", "") or "")
                + (hook.get("lf_desc", "") or "")
            )
            if victim in h_text and not self._already_repaired(hook):
                # v6.x.x（08-30 实抽）：hook 是刺杀前蓄势瞬间时，**不该**补伤锚。
                # 旧版只判定"含 victim + 缺 anchor"就补，导致 hook.prompt 末尾被
                # 注入"伤处白袍心口一片暗红血迹洇开"——与杨旭"闭目吟哦/仰卧未觉"
                # 自相矛盾，模型自由发挥成"船里灌水/听香在船外"画面。
                # 复用既有词表判定 hook 剧情态（零新建）：
                #   - 含 BLOOD_STATE_WORDS 或 POST_STATE_KEYWORDS → 刺杀后态 → 可补
                #   - 否则 → 刺杀前蓄势 → 跳过
                is_post_state = (
                    any(w in h_text for w in _BLOOD_STATE_WORDS)
                    or any(w in h_text for w in _POST_STATE_KEYWORDS)
                )
                if not is_post_state:
                    return repairs
                hp = hook.get("prompt", "") or ""
                hl = hook.get("lf_desc", "") or ""
                hp_has = any(w in hp for w in _BLOOD_STATE_WORDS)
                hl_has = bool(hl) and any(w in hl for w in _BLOOD_STATE_WORDS)
                if not hp_has and hp:
                    before = hp
                    ctx.record_violation(Violation(
                        constraint_name=self.name, severity=Severity.WARN,
                        shot_index="hook", field="prompt", repaired=False,
                        message="可能缺少伤情连续性；须复核同一人物及本帧可见范围，不注入默认衣服/伤处。",
                        evidence=before))
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index="hook",
                            action="detect",
                            target="prompt",
                            before=before,
                            after=hook["prompt"],
                        )
                    )
                if hl and not hl_has:
                    before = hl
                    ctx.record_violation(Violation(
                        constraint_name=self.name, severity=Severity.WARN,
                        shot_index="hook", field="lf_desc", repaired=False,
                        message="可能缺少伤情连续性；须复核同一人物及本帧可见范围，不注入默认衣服/伤处。",
                        evidence=before))
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index="hook",
                            action="detect",
                            target="lf_desc",
                            before=before,
                            after=hook["lf_desc"],
                        )
                    )
        return repairs
