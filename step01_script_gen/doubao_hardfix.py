"""doubao_hardfix - doubao 专用 7 个 hard_fix Constraint（v6.60+, 2026-09-05）.

设计原则（CLAUDE.md v6.32 幂等硬要求 + v6.43 双层架构）:
  - LLM 弱约束 + 代码强兜底：本模块在 agent_plan 路径上提供 7 个确定性修复
  - 与 deepseek 通用 hard_fix（validation.py 36 类）完全解耦，仅在 doubao 路径挂载
  - 全部走 Constraint 框架：register() 调时 append 到 ALL_CONSTRAINTS
  - 幂等分两类：B.1-B.4 靠 repair_marker 字符串 + 基类 _already_repaired；
    B.5 是「缺才补」型（repair_marker=None），由字段级检查天然幂等

7 个 hard_fix 覆盖 doubao-seed-2-1-turbo 78.7 分缺口（2026-09-04 A/B 实测）:
  - B.1   对白三件套缺失：main_subject 含对话关键词 + narration_segment 空 → 兜底填
  - B.2   GAP 关键词强制注入：core_keywords 命中率低 → ff_desc 补关键 anchor 词
  - B.3.1 角色锚点缺失：prompt 不含 [name: features] → 头部注入（≤30 字特征）
  - B.3.2 开篇空镜缺失：前 3 镜无空镜/特写 → 插入 index=1 空镜（末镜 -2s 联动）
  - B.3.3 高潮全在前半：后半无高潮词 → 中点镜追加「反转揭露」组合
  - B.4   过肩机位断裂：3 镜同景同 cam 同对白 → 中间镜改过肩镜头
  - B.5   frame_layouts 兜底：缺 pose / on_screen / kfs 长度不齐 → 字段补齐

挂载机制（main.py 走 agent_plan 时调）:
    from .doubao_hardfix import register_doubao_hardfixes
    register_doubao_hardfixes()

退出 doubao 模式只需 unset TEXT_PROVIDER，register 永不调，零影响 deepseek 路径。
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import ClassVar, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .constraints.base import Constraint
from .constraints.types import RepairRecord, Severity
from _text_gen.config import TEXT_PROVIDER_DEFAULT


# ============================================================================
# 7 个 doubao 专用 hard_fix 共享常量
# ============================================================================

# B.1 对白关键词（main_subject 命中 → 视为对白场景）
_DIALOGUE_KW = (
    "质问", "怒喝", "叫道", "对", "说", "答", "问",
    "喊", "吼", "低声", "怒道", "厉声", "低语", "轻声道", "冷声",
)

# B.3.2 / B.1 通用：空镜主（main_subject=空镜/环境/远景 → 跳过对白兜底）
# ⚠️ 与另外两套「空镜相关」词表用途不同，勿合并：
#   - style_prefix.is_empty_scene（step02）：全文判定（角色名/泛人物字眼），本表只匹配 main_subject
#   - constraints/post_combat_blank._CLOSING_WORDS：战后收尾词（含极光/月夜等意象）
_EMPTY_SHOT_KW = ("空镜", "远景", "特写", "环境", "水波", "树影", "风")

# B.4 过肩机位关键词（用于反向校验是否已修）
_OVERSHOULDER_KW = ("过肩", "过肩镜头", "过肩机位")

# B.3.3 高潮尾部词（命中 → 视为后半已有高潮）
_CLIMAX_TAIL_TUPLE: Tuple[str, ...] = (
    "冲突到达顶点",
    "反转揭露",
    "悬念陡升",
    "身份暴露",
    "矛盾激化",
)

# B.3.1 泛化主语词（main_subject 命中 → 视为泛指，需回填角色名）
# ⚠️ 与 prompt_semantic.registry.COLLECTIVE_NAMES（受害者侧泛指：群演/仆从/侍卫…）
# 用途不同、集合不同，勿合并——那边排除的是"不可追踪受害者"，这边判定的是
# "主语需要回填具体角色名"。
GENERAL_TOKENS = ("群演", "人群", "路人", "某男子", "某女子", "某", "主角", "空镜", "环境", "远景")

# ============================================================================
# 数字旋钮（2026-09-05 硬编码清理命名化：值 = 原字面量，语义见各 Constraint）
# ============================================================================

_DLG_TAIL_CHARS = 20        # B.1: 兜底对白从 ff_desc 取尾 N 字
_DLG_SUBJECT_CHARS = 10     # B.1: ff_desc 全空时从 main_subject 取前 N 字
_GAP_KEYWORD_TOP = 5        # B.2: 只检查 core_keywords 前 N 个
_GAP_INJECT_MAX = 3         # B.2: 单镜最多注入 N 个缺失 anchor 词
_ANCHOR_FEAT_CHARS = 30     # B.3.1: 角色特征截前 N 字（防 step02a 帧生成走偏）
_OPENING_WINDOW = 3         # B.3.2: 前 N 镜窗口内找空镜
_OPENING_INSERT_INDEX = 1   # B.3.2: 空镜插入位（重编号后 index）
_OPENING_EMPTY_SEC = 2      # B.3.2: 插入空镜时长——对齐 prompts.py「特写/空镜 2-4秒」
                            #   规则；有意低于 timecode.MIN_SHOT_DURATION=3.0（正片通用下限）
_OPENING_COMPENSATE_SEC = 2.0   # B.3.2: 末镜扣减秒数（保持正片总时长窗口
                            #   MIN_TOTAL_SHOTS_DURATION~_REDGUO_DURATION_MAX，见 timecode/validation）
_OPENING_MIN_LAST_SEC = 1.0     # B.3.2: 末镜扣减下限（有意低于 3s：插镜是兜底，宁短勿断流）
_CLIMAX_MIN_SHOTS = 4       # B.3.3: 少于 N 镜不分半（保证前后半各 ≥2 镜）
_FL_POSE_CHARS = 15         # B.5: pose 兜底取 main_subject 前 N 字


def _is_dialogue_shot(s: dict) -> bool:
    """判定镜头是否含对白（main_subject 含对话关键词 或 subtitle 非空）."""
    if not isinstance(s, dict):
        return False
    ms = (s.get("main_subject") or "").strip()
    if any(k in ms for k in _DIALOGUE_KW):
        return True
    sub = (s.get("subtitle") or "").strip()
    return bool(sub)


def _is_empty_shot(s: dict) -> bool:
    """判定镜头是否空镜（main_subject 含空镜关键词）."""
    if not isinstance(s, dict):
        return False
    ms = (s.get("main_subject") or "").strip()
    return any(k in ms for k in _EMPTY_SHOT_KW)


# ============================================================================
# B.1 对白 narration_segment 空兜底
# ============================================================================

class DialogNarrationEmptyConstraint(Constraint):
    """B.1 对白镜 narration_segment 双空 → 兜底填（古风权谋「某：……」占位）.

    缺口：doubao 对白镜 17% narration_segment 整段空（2026-09-04 实测）.
    兜底：从 ff_desc 末尾 20 字 + 「某：……」生成占位对白，同时填 subtitle.
    幂等：narration_segment 含"某："或 repair_marker 视为已修.
    """

    name: ClassVar[str] = "doubao_dialogue_narration_empty"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("narration_segment", "subtitle")
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_DLG:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            if self._already_repaired(shot):
                continue
            # 空镜主：跳过对白兜底
            if _is_empty_shot(shot):
                continue
            # 非对白场景：跳过
            if not _is_dialogue_shot(shot):
                continue
            narr = (shot.get("narration_segment") or "").strip()
            sub = (shot.get("subtitle") or "").strip()
            if narr and sub:
                # 双字段都已有内容，跳过
                continue
            # 兜底生成：从 ff_desc 取尾句作前缀
            ff = (shot.get("ff_desc") or "").rstrip("。. ")
            tail = ff[-_DLG_TAIL_CHARS:] if len(ff) > _DLG_TAIL_CHARS else ff
            if not tail:
                tail = (shot.get("main_subject") or "")[:_DLG_SUBJECT_CHARS]
            placeholder = f"{tail}，某：……"
            before_narr = narr
            before_sub = sub
            if not narr:
                shot["narration_segment"] = self.repair_marker + " " + placeholder
            if not sub:
                shot["subtitle"] = placeholder
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="set_field",
                    target="narration_segment/subtitle",
                    before=f"narr={before_narr!r};sub={before_sub!r}",
                    after=shot.get("narration_segment", ""),
                    detail="doubao 对白镜 narration_segment 双空，兜底填古风权谋对白占位",
                )
            )
        return repairs


# ============================================================================
# B.2 GAP 关键词强制注入
# ============================================================================

class GapKeywordForceInjectConstraint(Constraint):
    """B.2 core_keywords 低命中率 → ff_desc 末尾追加 anchor 词.

    缺口：doubao GAP 覆盖率 fail（2026-09-04 实测 gap_analysis fail）.
    兜底：取 core_keywords 前 5 个，若 ff_desc/prompt/lf_desc/motion_desc 任一不含
          → 追加到 ff_desc 末尾「，{kw1}、{kw2}、{kw3} 可见。」
    幂等：ff_desc 含 repair_marker 视为已修.
    """

    name: ClassVar[str] = "doubao_gap_keyword_force_inject"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("ff_desc", "prompt")
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_GAP:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            if self._already_repaired(shot):
                continue
            # core_keywords 解析：兼容 string "," 分隔 与 list 形式
            kws_raw = shot.get("core_keywords", "")
            if isinstance(kws_raw, list):
                kws_list = [str(k).strip() for k in kws_raw if str(k).strip()]
            elif isinstance(kws_raw, str):
                kws_list = [k.strip() for k in kws_raw.replace("，", ",").split(",") if k.strip()]
            else:
                kws_list = []
            if not kws_list:
                continue
            # 跨字段 blob
            blob = " ".join(
                (shot.get(k, "") or "") for k in ("prompt", "ff_desc", "lf_desc", "motion_desc")
            )
            missing = [k for k in kws_list[:_GAP_KEYWORD_TOP] if k and k not in blob]
            if not missing:
                continue
            inject = "、".join(missing[:_GAP_INJECT_MAX]) + " 可见。"
            before = shot.get("ff_desc", "")
            shot["ff_desc"] = (
                self.repair_marker + " "
                + (before.rstrip("。. ") if before else "")
                + "，" + inject
            )
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="append",
                    target="ff_desc",
                    before=before,
                    after=shot["ff_desc"],
                    detail=f"doubao GAP 关键词命中失败，强制注入 {len(missing)} 个 anchor 词",
                )
            )
        return repairs


# ============================================================================
# B.3.1 角色锚点注入（prompt 头部）
# ============================================================================

class RoleAnchorInjectConstraint(Constraint):
    """B.3.1 角色锚点缺失：prompt 不含 [name: features] → 头部注入.

    缺口：doubao 角色锚点缺失（step02a 帧生成走偏）.
    决策：注入到 prompt 头部（不注 char_layout，避免触发 W16 frame_layout pose/desc 一致性）.
    特征字符串截前 30 字（防 step02a 帧生成走偏）.
    幂等：prompt 含 [name: 或 repair_marker 视为已修.
    """

    name: ClassVar[str] = "doubao_role_anchor_inject"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("prompt",)
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_RA:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        bank = ctx.characters_bank or {}
        for ch in getattr(ctx, "script", {}).get("characters", []) or []:
            if not isinstance(ch, dict):
                continue
            ident = str(ch.get("identifier") or ch.get("name") or "").strip()
            if ident and ident not in bank:
                bank[ident] = ch
        if not bank:
            return repairs
        # 构造 {identifier: static_features[:_ANCHOR_FEAT_CHARS]} 字典
        anchor_map = {}
        for ident, ch in bank.items():
            if isinstance(ch, dict):
                feats = ch.get("static_features") or ch.get("appearance") or ch.get("desc") or ""
            else:
                feats = getattr(ch, "static_features", "") or ""
            anchor_map[str(ident)] = str(feats)[:_ANCHOR_FEAT_CHARS]
        if not anchor_map:
            return repairs
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            if self._already_repaired(shot):
                continue
            ms = (shot.get("main_subject") or "").strip()
            prompt = (shot.get("prompt") or "").strip()
            if not ms:
                continue
            # 定位角色名：优先 main_subject 最长匹配，否则从 keywords/narration 提取
            # （GENERAL_TOKENS 见模块顶部注释——与 registry.COLLECTIVE_NAMES 用途不同）
            is_generic = any(g in ms for g in GENERAL_TOKENS) or not ms
            # 从下列文本汇总提取出现的角色名
            def _str(v):
                if isinstance(v, list):
                    return ",".join(str(x) for x in v)
                return str(v) if v is not None else ""
            sources = [
                _str(shot.get("main_subject")),
                _str(shot.get("core_keywords")),
                _str(shot.get("narration_segment")),
                _str(shot.get("subtitle")),
            ]
            blob = "\n".join(sources)
            # 按名字长度降序尝试匹配
            sorted_names = sorted(anchor_map.keys(), key=len, reverse=True)
            chosen_name = None
            chosen_feats = None
            for name in sorted_names:
                if not name or name in ("", " ") or len(name) < 2:
                    continue
                if name in blob:
                    chosen_name = name
                    chosen_feats = anchor_map[name]
                    # main_subject 已含名b57且不是泛化词→优先级最高
                    if not is_generic and name in ms:
                        break
            if not chosen_name:
                continue
            # 已含锚点则跳过
            if re.search(rf"\[{re.escape(chosen_name)}[:：]", prompt):
                continue
            inject = f"[{chosen_name}: {chosen_feats}]" if chosen_feats else f"[{chosen_name}]"
            before = prompt
            shot["prompt"] = self.repair_marker + " " + inject + " " + prompt
            # 同步修复 main_subject （泛化词→"角色名+动作"）
            ms_before = ms
            if is_generic and chosen_name not in ms:
                shot["main_subject"] = chosen_name + ("，" + ms if ms else "")
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="prepend+sync_main_subject",
                    target="prompt,main_subject",
                    before=before,
                    after=shot["prompt"],
                    detail=f"doubao B.3.1 锚点从 {ms_before!r} 提取到 {chosen_name}，prompt 头部注入≤30 字特征",
                )
            )
        return repairs


# ============================================================================
# B.3.2 开篇空镜插入
# ============================================================================

_OPENING_EMPTY_TEMPLATE: dict = {
    "main_subject": "空镜·水波/树影/风",
    "prompt": "中景, 固定机位, 暖金光位, 画面稳定不闪烁。",
    "ff_desc": "环境空镜, 无人无角色, 暖金阳光斜照。",
    "lf_desc": "环境空镜, 无人无角色, 仍空无一人。",
    "motion_desc": "全程静景, 5s 内仅光线微移。",
    "core_keywords": "空镜,环境,氛围",
    "narration_segment": "",
    "subtitle": "",
    "audio_cue": "环境音",
    "color_tone": "暖金",
    "duration_sec": _OPENING_EMPTY_SEC,
    "variation_type": "small",
    "frame_layouts": {"ff": [], "lf": [], "kfs": [[]]},
    "characters": [],
}


class OpeningEmptyShotInsertConstraint(Constraint):
    """B.3.2 开篇空镜缺失：前 3 镜无空镜/特写 → 插入 index=1 空镜.

    缺口：doubao 开篇直入人物镜头，无环境建立（首镜即对话）.
    决策：插入到 index 1（前 3 镜最前位置），同时末镜 duration_sec -2s
          保持正片总时长窗口（timecode.MIN_TOTAL_SHOTS_DURATION ~
          validation._REDGUO_DURATION_MAX，CLAUDE.md v6.32 硬要求）.
    幂等：shots[0]["main_subject"] 含"空镜"或 repair_marker 视为已修.
    """

    name: ClassVar[str] = "doubao_opening_empty_shot_insert"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("main_subject",)
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_OPE:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list) or len(shots) < _OPENING_WINDOW:
            return repairs
        # 已修过：shots[0] 是空镜
        first = shots[0]
        if not isinstance(first, dict):
            return repairs
        ms0 = (first.get("main_subject") or "").strip()
        if any(k in ms0 for k in _EMPTY_SHOT_KW) or self.repair_marker in ms0:
            return repairs
        # 前 N 镜已含空镜/特写：跳过（避免重复插入）
        first3 = shots[:_OPENING_WINDOW]
        if any(
            isinstance(s, dict) and any(k in (s.get("main_subject") or "") for k in _EMPTY_SHOT_KW)
            for s in first3
        ):
            return repairs
        # 构造空镜（基于首镜 scene 锚点）
        template = dict(_OPENING_EMPTY_TEMPLATE)
        template["scene"] = (first.get("scene") or "")
        template["index"] = _OPENING_INSERT_INDEX
        template["main_subject"] = self.repair_marker + " " + template["main_subject"]
        shots.insert(0, template)
        # 重新编号
        for i, s in enumerate(shots, 1):
            if isinstance(s, dict):
                s["index"] = i
        # 末镜 -2s 联动（保持正片总时长窗口，常量见模块顶部）
        last = shots[-1]
        if isinstance(last, dict):
            old_dur = float(last.get("duration_sec", 5.0) or 5.0)
            new_dur = max(_OPENING_MIN_LAST_SEC, old_dur - _OPENING_COMPENSATE_SEC)
            last["duration_sec"] = new_dur
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=1,
                action="insert",
                target="shots[0]",
                before="",
                after="空镜·水波/树影/风 + 末镜 -2s 联动",
                detail="doubao 开篇无空镜，插入 index=1 环境空镜模板 + 末镜时长收紧",
            )
        )
        return repairs


# ============================================================================
# B.3.3 高潮回灌
# ============================================================================

_CLIMAX_INJECT_TAIL = "。冲突到达顶点，局势不可挽回，悬念陡升"


class ClimaxBackInjectConstraint(Constraint):
    """B.3.3 高潮全在前半：后半无高潮词 → 中点镜追加高潮尾部.

    缺口：doubao 高潮集中在前 50% 时段，后半塌陷（2026-09-04 实测 climax fail）.
    兜底：取中点镜（shots[half-1]），在 main_subject + prompt 追加
          「冲突到达顶点，局势不可挽回，悬念陡升」.
    幂等：后半任一镜含 _CLIMAX_TAIL_TUPLE 任一词 → 视为已修.
    """

    name: ClassVar[str] = "doubao_climax_back_inject"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("prompt", "main_subject", "core_keywords")
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_CLX:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list) or len(shots) < _CLIMAX_MIN_SHOTS:
            return repairs
        half = len(shots) // 2
        # GAP 只读 narration/subtitle/core_keywords；后半已有 3 镜命中才不修。
        # 旧逻辑"任一镜命中即跳过"会在 climax_shots=1/3 时误判已修。
        def _has_gap_climax(s: dict) -> bool:
            text = " ".join(
                str(s.get(k) or "") for k in ("narration_segment", "subtitle", "core_keywords")
            )
            return any(t in text for t in _CLIMAX_TAIL_TUPLE)

        back_half = [s for s in shots[half:] if isinstance(s, dict)]
        if sum(_has_gap_climax(s) for s in back_half) >= 3:
            return repairs
        # GAP 只读 narration/subtitle/core_keywords；后半至少 3 镜命中才满足蒙太奇规则。
        # 中点镜保留完整高潮句，其余两镜用短关键词，避免画面描述被重复句式淹没。
        targets = back_half[:3]
        short_tail = _CLIMAX_INJECT_TAIL.lstrip("。")
        for pos, shot in enumerate(targets):
            before_ms = (shot.get("main_subject") or "").rstrip("。. ")
            before_prompt = (shot.get("prompt") or "").rstrip("。. ")
            before_kws = shot.get("core_keywords", "")
            climax_text = _CLIMAX_INJECT_TAIL if pos == 0 else short_tail
            shot["main_subject"] = (
                before_ms + climax_text if before_ms else self.repair_marker + short_tail
            )
            if before_prompt:
                shot["prompt"] = before_prompt + climax_text
            if isinstance(before_kws, list):
                shot["core_keywords"] = list(before_kws) + ["冲突", "顶点", "悬念陡升"]
            else:
                raw_kws = str(before_kws or "").rstrip("。. ")
                shot["core_keywords"] = raw_kws + ",冲突,顶点,悬念陡升" if raw_kws else "冲突,顶点,悬念陡升"
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", half + pos),
                    action="append",
                    target="main_subject/prompt/core_keywords",
                    before=f"ms={before_ms!r};prompt={before_prompt!r};kws={before_kws!r}",
                    after=str(shot["main_subject"]),
                    detail="doubao 高潮全在前半，后半镜头追加高潮关键词",
                )
            )
        return repairs


# ============================================================================
# B.4 过肩机位一致性
# ============================================================================

class OvershoulderConsistencyConstraint(Constraint):
    """B.4 过肩机位断裂：3 镜同景同 cam 同对白 → 中间镜改过肩.

    缺口：doubao 对话组过肩机位断裂（2026-09-04 实测 camera_movement 仅 1 种）.
    兜底：3 镜连续对白且 location 相等且 camera_movement 相等 → 中间镜改"过肩镜头".
    幂等：中间镜 camera_movement 已含"过肩"或 prompt 含 repair_marker 视为已修.
    """

    name: ClassVar[str] = "doubao_overshoulder_consistency"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("camera_movement", "prompt")
    repair_marker: ClassVar[Optional[str]] = "_DOUBAO_FIX_OS:"

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list) or len(shots) < 3:
            return repairs
        for i in range(len(shots) - 2):
            s1, s2, s3 = shots[i], shots[i + 1], shots[i + 2]
            if not all(isinstance(s, dict) for s in (s1, s2, s3)):
                continue
            # 三镜必须都是对白场景
            if not all(_is_dialogue_shot(s) for s in (s1, s2, s3)):
                continue
            # 三镜 narration_segment 都非空
            if not all((s.get("narration_segment") or "").strip() for s in (s1, s2, s3)):
                continue
            # 三镜 location/scene 相等
            locs = {(s.get("location") or s.get("scene") or "").strip() for s in (s1, s2, s3)}
            locs.discard("")
            if len(locs) != 1:
                continue
            # 三镜 camera_movement 相等
            cams = {(s.get("camera_movement") or "").strip() for s in (s1, s2, s3)}
            cams.discard("")
            if len(cams) != 1:
                continue
            # 已修过：s2 camera_movement 含"过肩" 或 prompt 含 marker
            cam2 = (s2.get("camera_movement") or "").strip()
            if any(k in cam2 for k in _OVERSHOULDER_KW) or self.repair_marker in (s2.get("prompt") or ""):
                continue
            # 中间镜改过肩
            before_cam = cam2
            before_prompt = (s2.get("prompt") or "").rstrip("。. ")
            s2["camera_movement"] = "过肩镜头"
            if before_prompt:
                s2["prompt"] = before_prompt + "，过肩镜头。"
            else:
                s2["prompt"] = self.repair_marker + " 过肩镜头。"
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=s2.get("index", i + 1),
                    action="rewrite",
                    target="camera_movement",
                    before=before_cam,
                    after="过肩镜头",
                    detail="doubao 三镜同景同 cam 同对白，中间镜改过肩镜头",
                )
            )
        return repairs


# ============================================================================
# B.5 frame_layouts 兜底
# ============================================================================

class FrameSpecReconstructConstraint(Constraint):
    """B.5 frame_layouts 兜底：缺 pose / on_screen / kfs 长度不齐 → 字段补齐.

    缺口：doubao frame_layouts 字段缺 pose/on_screen 频次高（2026-09-04 实测）.
    保守策略：
      - ff/lf 每个 dict 缺 pose → 填 main_subject[:_FL_POSE_CHARS]
      - ff/lf 每个 dict 缺 on_screen → True
        （on_screen 矛盾：不动，plan B.5 决策，让阶段 C system prompt 修主路径）
      - kfs 数组长度对齐 keyframes 数组长度（少则补 ff 深拷贝，多则 pop）

    幂等（v6.60.1 重写）：repair_marker=None，幂等由字段级检查保证——全部操作
    都是「缺才补」（pose 有则跳 / on_screen 有则跳 / kfs 已对齐则跳），连跑两次
    第二次零改动。与 constraints/axis_jump、hook_duration 同款惯例。
    ⚠️ 旧实现把 repair_marker 写进 frame_layouts["_doubao_fix_marker"] 键：基类
    _already_repaired 只扫 str 与 list-of-dict 值，对 dict 字段检不出该标记
    （无效标记）；且多塞的键会污染 frame_layouts 结构。已移除。
    """

    name: ClassVar[str] = "doubao_frame_spec_reconstruct"
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = ("frame_layouts", "keyframes", "main_subject")
    repair_marker: ClassVar[Optional[str]] = None  # 字段级幂等，无需标记（见 docstring）

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        # 收集 hook + shots
        items: list[dict] = []
        if ctx.hook and isinstance(ctx.hook, dict):
            items.append(ctx.hook)
        for s in ctx.shots:
            if isinstance(s, dict):
                items.append(s)
        for s in items:
            fl = s.get("frame_layouts")
            if not isinstance(fl, dict):
                continue
            self._fix_ff_lf(s, fl, repairs)
            self._align_kfs(s, fl, repairs)
        return repairs

    # ------------------------------------------------------------------
    # 修复子步骤（每个都是「缺才补」，合并构成整体幂等）
    # ------------------------------------------------------------------

    def _fix_ff_lf(self, shot: dict, fl: dict, repairs: list[RepairRecord]) -> None:
        """ff/lf 逐 dict 补 pose / on_screen."""
        for frame_key in ("ff", "lf"):
            lst = fl.get(frame_key)
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not isinstance(item, dict):
                    continue
                if "pose" not in item or not item.get("pose"):
                    item["pose"] = (shot.get("main_subject") or "静止")[:_FL_POSE_CHARS]
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index"),
                            action="set_field",
                            target=f"frame_layouts.{frame_key}[*].pose",
                            before="",
                            after=str(item["pose"]),
                            detail="doubao frame_layouts pose 缺失，兜底填 main_subject[:15]",
                        )
                    )
                if "on_screen" not in item:
                    item["on_screen"] = True
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index"),
                            action="set_field",
                            target=f"frame_layouts.{frame_key}[*].on_screen",
                            before="",
                            after="True",
                            detail="doubao frame_layouts on_screen 缺失，兜底填 True",
                        )
                    )

    def _align_kfs(self, shot: dict, fl: dict, repairs: list[RepairRecord]) -> None:
        """kfs 长度对齐 keyframes：少则补 ff 深拷贝，多则 pop."""
        kfs = shot.get("keyframes") or []
        fl_kfs = fl.get("kfs")
        if not isinstance(fl_kfs, list):
            fl_kfs = []
        ff_layout = fl.get("ff")
        changed = False
        # 不足补：每帧必须独立深拷贝——旧实现 append 同一浅拷贝对象 N 份，
        # 所有补齐帧与 ff 共享 item dict，后续链路改任一帧会联动改写全部
        while len(fl_kfs) < len(kfs):
            fl_kfs.append(copy.deepcopy(ff_layout) if isinstance(ff_layout, list) else [])
            changed = True
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index"),
                    action="set_field",
                    target="frame_layouts.kfs",
                    before="",
                    after=f"+1 frame (len {len(fl_kfs)})",
                    detail="doubao frame_layouts.kfs 长度不足，复制 ff 兜底（独立深拷贝）",
                )
            )
        # 多余 pop
        while len(fl_kfs) > len(kfs) and fl_kfs:
            fl_kfs.pop()
            changed = True
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index"),
                    action="set_field",
                    target="frame_layouts.kfs",
                    before="",
                    after=f"-1 frame (len {len(fl_kfs)})",
                    detail="doubao frame_layouts.kfs 长度过多，pop 兜底",
                )
            )
        if changed:
            fl["kfs"] = fl_kfs


# ============================================================================
# register 入口
# ============================================================================

# 7 个 doubao 专用 Constraint 实例（按 ALL_CONSTRAINTS append 顺序定义）
_DOUBAO_CONSTRAINT_CLASSES = [
    DialogNarrationEmptyConstraint,         # B.1
    GapKeywordForceInjectConstraint,        # B.2
    RoleAnchorInjectConstraint,             # B.3.1
    OpeningEmptyShotInsertConstraint,       # B.3.2
    ClimaxBackInjectConstraint,             # B.3.3
    OvershoulderConsistencyConstraint,      # B.4
    FrameSpecReconstructConstraint,         # B.5
]


_DOUBAO_HARDFIX_REGISTERED = False


def register_doubao_hardfixes() -> None:
    """main.py 走 agent_plan 时调，append 7 个 doubao 专用 Constraint 到 ALL_CONSTRAINTS.

    幂等：进程内只 append 一次（多次调不会重复注册）.
    退出 doubao 模式只需 unset TEXT_PROVIDER，本函数永不调.
    """
    global _DOUBAO_HARDFIX_REGISTERED
    if _DOUBAO_HARDFIX_REGISTERED:
        return
    _DOUBAO_HARDFIX_REGISTERED = True
    try:
        from .constraints import engine as _engine
    except ImportError:
        return
    for cls in _DOUBAO_CONSTRAINT_CLASSES:
        _engine.ALL_CONSTRAINTS.append(cls())


# 模块级注册钩子：被 import 时自动尝试 register（agent_plan 路径才生效）
# 注意保留 raw env 读取：test_deepseek_path_does_not_auto_register 靠 reload +
# monkeypatch env 验证钩子行为；默认值单源 _text_gen.config.TEXT_PROVIDER_DEFAULT。
import os as _os
if (_os.environ.get("TEXT_PROVIDER") or TEXT_PROVIDER_DEFAULT).strip().lower() == "agent_plan":
    try:
        register_doubao_hardfixes()
    except Exception:
        pass


__all__ = [
    "DialogNarrationEmptyConstraint",
    "GapKeywordForceInjectConstraint",
    "RoleAnchorInjectConstraint",
    "OpeningEmptyShotInsertConstraint",
    "ClimaxBackInjectConstraint",
    "OvershoulderConsistencyConstraint",
    "FrameSpecReconstructConstraint",
    "register_doubao_hardfixes",
]
