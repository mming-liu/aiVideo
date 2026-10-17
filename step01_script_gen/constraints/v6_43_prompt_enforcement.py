"""v6_43_prompt_enforcement.py — 4+1 类 v6.43 强化规则的硬修复层

L1 prompt 规则失声时（长 prompt 后段不被 LLM 关注）的确定性兜底：
1. 「小舟/木舟/船/一叶舟」 → 「一叶无篷舴艋舟（宽1.4米，可容3人）」
2. 姿态缺少头部/眼态证据 → 保留原文并报告，不从伤情补造尸体态
3. kf pre 与 ff_desc 文本重叠 >70% → 强制改写为构图级细节
4. char_layout 抽象位置（身旁/旁边/其侧）+ 该镜 location 场景具象词 → 改具象
5. threat_source 角色 facing 含「背对镜头」→ 改「侧身朝目标」（规则 41）

Rule 4 改用 scene_anchors 查表（不再写死"船"）。
Rule 5 新增：threat_source 朝背对镜头的通用硬修复（任何小说通用）。

幂等：二次运行结果不变。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import scene_anchors_for, get_scene_type
from step01_script_gen.prompt_semantic import (
    THREAT_KEYWORDS as _THREAT_KEYWORDS,
    ATTACK_KEYWORDS as _ATTACK_KW,
    HANDLE_PROPS as _HANDLE_PROPS,
    ABSTRACT_POSITION_WORDS as _GENERIC_ABSTRACT_WORDS,
    POSE_REGISTRY as _POSE_REGISTRY,
    LYING_NO_HEAD_FIX as _LYING_NO_HEAD_FIX,
    BACK_PATTERN as _BACK_PATTERN,
    ANCHOR_VAGUE_KW_PATTERN as _ANCHOR_VAGUE_KW,
)

from ._base_classes import Constraint, RepairRecord
from shared.scene_objects_bank import build_boat_replacement


def _rule_enabled(flag: str) -> bool:
    """v6.43 单规则独立开关（对齐 base.py CONSTRAINT_<NAME>_ENABLED 惯例）.

    env: CONSTRAINT_V643_<FLAG>_ENABLED，默认 "1"（全开=历史行为）。
    每次调用现读（代价可忽略），支持 monkeypatch.setenv 测试与运行期热切。
    """
    import os
    return os.getenv(f"CONSTRAINT_V643_{flag}_ENABLED", "1").strip() == "1"


# === 规则 1：小舟 → 舴艋舟 ===

# 匹配「一叶小舟/小舟/木舟/一叶舟」等无尺寸通用词（替换文本动态生成，见
# shared/scene_objects_bank.build_boat_replacement——尺寸以 scene_objects 为
# 唯一来源（v6.52 教训），未命中才回退历史兜底「宽1.4米，可容3人」）
# 1. 一叶小舟 / 2. 小舟 / 3. 木舟 / 4. 一叶舟
_BOAT_PATTERNS: list[re.Pattern] = [
    re.compile(r"一叶小舟"),
    re.compile(r"一叶木舟"),
    re.compile(r"一叶舟"),
    re.compile(r"小舟"),
    re.compile(r"木舟"),
]
# 兜底用：若 ff_desc/prompt 完全没出现"船/舟"，保留空白让 LLM 补（不强加）


# === 规则 2：仰卧 + 头部固定 ===
# _LYING_NO_HEAD_FIX 已在顶部 import 自 prompt_semantic.registry


def _needs_head_fix(text: str) -> bool:
    """检测「仰卧」类但缺头部固定描述 → 需要追加."""
    if not _LYING_NO_HEAD_FIX.search(text):
        return False
    # 已有头部固定描述则跳过
    head_fix_keywords = ("仰面朝上", "头颈僵直", "不侧转", "头部固定", "不侧目", "脸朝上")
    if any(kw in text for kw in head_fix_keywords):
        return False
    return True


# 睁眼态词：仰卧子句内已有时不再追加「双目紧闭」（避免相邻自相矛盾，
# 如「仰卧船头，双眼圆睁」+「双目紧闭」——留给 LLM/修复轮裁决）
_EYE_OPEN_WORDS = ("双眼圆睁", "双目圆睁", "双目暴睁", "瞪", "睁眼", "双眼瞪",
                   "双眼半阖", "双目半阖", "眼半阖", "眼睑半阖",
                   "半睁", "微睁", "眼睑微张", "半闭半睁")

# v6.73: "仰卧" alone is not a death/unconscious signal.  Apply the rigid
# head-state suffix only when the shot itself carries a death/injury signal,
# otherwise a living character resting or fishing is turned into a corpse.
_HEAD_FIX_CONTEXT_WORDS = (
    "刺中", "刺入", "中刀", "中剑", "刀伤", "剑伤", "伤口", "伤处",
    "心口", "胸口", "暗红", "洇痕", "血痕", "血迹", "血染", "流血",
    "断气", "身亡", "尸", "死", "昏迷", "气绝", "失去意识", "毙命",
    "杀死", "击杀", "倒地",
)
_THREAT_WORDS = ("刺客", "神秘人", "黑衣人", "凶手")
_POST_ATTACK_WORDS = ("没入水中", "遁走", "逃走", "已死", "尸体", "断气", "气绝")


def _head_fix_context_ok(shot: dict | None, text: str) -> bool:
    """Whether a rigid "closed eyes / stiff neck" suffix is semantically safe.

    Direct helper tests may pass ``shot=None`` and retain the historical
    behavior.  Real constraint runs always pass the whole shot so a living
    "仰卧吹箫/饮酒" description is not converted into a corpse pose.
    """
    if not isinstance(shot, dict):
        return True
    parts = [text]
    for _field in ("prompt", "ff_desc", "lf_desc", "motion_desc", "main_subject",
                   "narration_segment", "subtitle"):
        _v = shot.get(_field)
        if isinstance(_v, str) and _v:
            parts.append(_v)
    combined = " ".join(parts)
    if any(_w in combined for _w in _HEAD_FIX_CONTEXT_WORDS):
        return True
    # Post-attack threat handoff: assassin has already submerged / fled.
    if any(_w in combined for _w in _THREAT_WORDS) and any(
        _w in combined for _w in _POST_ATTACK_WORDS
    ):
        return True
    return False


_HEAD_STATE_FRAGMENTS = (
    "，仰面朝上双目紧闭，头颈僵直不侧转",
    "。仰面朝上双目紧闭，头颈僵直不侧转",
    "仰面朝上双目紧闭，头颈僵直不侧转，",
    "仰面朝上双目紧闭，头颈僵直不侧转。",
    "仰面朝上双目紧闭，头颈僵直不侧转",
    "，仰面朝上双目紧闭",
    "仰面朝上双目紧闭，",
    "仰面朝上双目紧闭",
)


def _remove_invalid_head_state(text: str, shot: dict | None) -> str:
    """Remove a previously injected corpse-state fragment when it is invalid.

    The gate above prevents *new* bad injections.  Historical snapshots and
    model-authored fields may already contain the suffix, so the same rule
    must be able to undo it before they are promoted back to production.
    """
    if not any(_frag in text for _frag in _HEAD_STATE_FRAGMENTS):
        return text
    has_open_eye = any(_w in text for _w in _EYE_OPEN_WORDS)
    if _head_fix_context_ok(shot, text) and not has_open_eye:
        return text
    out = text
    for _frag in _HEAD_STATE_FRAGMENTS:
        out = out.replace(_frag, "")
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"[。]{2,}", "。", out)
    return out.strip("，,。 ")


_BOAT_SPEC_RE = re.compile(
    r"(?:无篷)?(?:舴艋舟|小舟|木舟)[（(][^）)]{0,48}(?:长|宽|高|可容)"
)


def _has_boat_spec(text: str) -> bool:
    """Return True when this field already carries one concrete boat spec."""
    return bool(_BOAT_SPEC_RE.search(text or ""))


# v6.50（08-29）shot13.5 变体双守卫：
# 1) 否定前缀——「并未昏迷/不曾熟睡」说的是“没躺”，不能追加头固定
_NEGATION_PREFIX = ("不", "未", "没", "莫", "勿", "非")
# 2) 直立/起身冲突——匹配子句已含身体朝向词时，追加「仰面朝上」直接矛盾
#    （shot13.5 实测：听香「捂着脸含泪俯首…失神」被拼进「仰面朝上双目紧闭」）
_UPRIGHT_CONFLICT_WORDS = (
    "俯首", "低头", "垂首", "埋头", "俯身", "躬身",
    "抬头", "仰起", "起身", "坐起", "站起", "抬起", "直起身", "挺直",
)


def _append_head_fix(text: str) -> str:
    """兼容入口：仰卧、受伤或昏迷不能证明闭眼与头颈僵直，保留原文交审计。"""
    return text


# === 规则 3：kf pre 与 ff_desc 文本重叠 >70% → 强制改写 ===

def _char_ngrams(text: str, n: int = 4) -> set[str]:
    """4-gram 字符集合用于重叠检测（中文无空格按字符切）."""
    text = re.sub(r"\s+", "", text)
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _overlap_ratio(a: str, b: str) -> float:
    """两个文本的 4-gram 重叠率 (0-1)."""
    ga, gb = _char_ngrams(a, n=4), _char_ngrams(b, n=4)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    smaller = min(len(ga), len(gb))
    return inter / smaller if smaller else 0.0


# === 规则 4：ff_desc 抽象→具象（按 location 场景查 scene_anchors）===

# 通用模糊词正则：捕获"角色名 + 1-3 字动词 + 模糊词"模式
# 角色名候选由 char_layout 动态提供；fallback 仅在极端情况下用
# _GENERIC_ABSTRACT_WORDS 已在顶部 import 自 prompt_semantic.registry
_FALLBACK_CHAR_NAMES: tuple = ()  # 兜底：空（强制 char_layout 提供）


def _make_pattern_replacements(char_names: list[str], anchors: tuple, char_positions: dict[str, str] | None = None) -> list:
    """生成 (regex, replacement) 列表，按"具象词成对"分配给角色.

    优先级：
      1) char_positions 提供（从 char_layout 抽）→ 优先用「角色实际 position 含的 anchor」
         例：char_layout 写"听香 position=船尾" → 模糊词替换用"船尾"而非 anchor[-1]
      2) 兜底：横向场景首/末 anchor（船头/船舷内）分配
    """
    if not anchors:
        return []
    char_positions = char_positions or {}
    pairs = []
    for name in char_names:
        # 1) 从 char_layout.position 抽 anchor（如 "船尾" 出现在 position 中）
        anchor = None
        pos = char_positions.get(name, "")
        for a in anchors:
            if a in pos:
                anchor = a
                break
        # 2) 兜底：首/末
        if not anchor:
            if len(anchors) >= 2:
                anchor = anchors[0] if char_names.index(name) == 0 else anchors[-1]
            else:
                anchor = anchors[0]
        for word in _GENERIC_ABSTRACT_WORDS:
            pat = re.compile(name + r"[^，。、\n]{0,4}" + word)
            repl = f"{name}立于{anchor}"
            pairs.append((pat, repl))
    return pairs


# === 规则 5：threat_source 角色 facing 含"背对" → 改"侧身朝目标"（规则 41）===

# _THREAT_KEYWORDS / _BACK_PATTERN 已在顶部 import 自 prompt_semantic.registry


def _is_threat_role(item: dict) -> bool:
    """char_layout 项是否为威胁源角色."""
    if not isinstance(item, dict):
        return False
    role = str(item.get("role", "")).strip()
    if role in ("威胁", "threat", "主-威胁", "次-威胁", "antagonist"):
        return True
    if item.get("is_threat") is True:
        return True
    name = str(item.get("name", "")).strip()
    if any(kw in name for kw in _THREAT_KEYWORDS):
        return True
    return False


def _fix_threat_facing(shot: dict) -> int:
    """Rule 5：threat_source 角色 facing 含"背对" → 改"侧身朝目标".

    返回修复数。
    """
    layout = shot.get("char_layout")
    if not isinstance(layout, list):
        return 0
    fixed = 0
    for item in layout:
        if not _is_threat_role(item):
            continue
        facing = str(item.get("facing", "")).strip()
        if not facing:
            continue
        # 已含「侧身/面向/面朝」视为已修
        if any(k in facing for k in ("侧身", "面向", "面朝")):
            continue
        # 含「背对」→ 改写
        if _BACK_PATTERN.search(facing):
            item["facing"] = "侧身朝目标"
            fixed += 1
    return fixed


# === 规则 6：时序塌缩检测（pre-state + attack + post-state 混写） ===

# _ATTACK_KW 已在顶部 import 自 prompt_semantic.registry

# 角色躺/卧/无意识姿态 — 从 POSE_REGISTRY["reclining"] 取
_RECLINING_KW: tuple = _POSE_REGISTRY["reclining"]


def _is_lying_or_sleeping(text: str) -> bool:
    """检测文本是否含"躺/卧/昏迷/睡着"等无意识姿态."""
    return any(kw in text for kw in _RECLINING_KW)


def _fix_temporal_collapse(shot: dict) -> int:
    """Rule 6：钩子/关键首帧 ff_desc 同时含 pre-state + attack → 删 attack 部分.

    策略：
      1) 含"躺/卧/昏迷" + 攻击动作词 → 删攻击动作所在句子
      2) 钩子（is_hook）更严格：含任何攻击词 + 受害者躺卧 → 强制改写为「pre-state only」
    """
    fixed = 0
    for field in ("ff_desc", "prompt"):
        text = shot.get(field, "")
        if not text or not isinstance(text, str):
            continue
        if not _is_lying_or_sleeping(text):
            continue
        # 检测攻击动作词
        if not any(kw in text for kw in _ATTACK_KW):
            continue
        # 删攻击句（按「。」分句）
        sentences = re.split(r"([。！？])", text)
        new_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            sent = sentences[i]
            punct = sentences[i + 1] if i + 1 < len(sentences) else ""
            if any(kw in sent for kw in _ATTACK_KW):
                # 攻击句删掉
                fixed += 1
                continue
            new_sentences.append(sent + punct)
        new_text = "".join(new_sentences).rstrip("。！？，, ")
        if new_text and new_text != text:
            shot[field] = new_text
    return fixed


# === 规则 7：道具主谓歧义（"X手+道具" 误读为"握"） ===

# _HANDLE_PROPS 已在顶部 import 自 prompt_semantic.registry


# 会**独立交代道具状态**的叙述层字段（总计划 §1.2：原文/旁白 > motion_desc >
# ff/kf/lf 描述 > layouts > prompt）。`lf_desc` 虽与 `ff_desc` 同级，但它描述的是
# **本镜末态**——两者矛盾属于镜头内的时序冲突，不该由姿态启发式单方面改写。
_PROP_STATE_TEXT_FIELDS = ("narration_segment", "subtitle", "motion_desc", "lf_desc")


def _prop_described_elsewhere(shot: dict, prop: str, current_field: str) -> bool:
    """Historical textual-mention helper; no longer authorizes state rewrites."""
    for fld in _PROP_STATE_TEXT_FIELDS:
        if fld == current_field:
            continue
        val = shot.get(fld)
        if isinstance(val, str) and val and prop in val:
            return True
    return False


def _fix_handle_ambiguity(shot: dict) -> int:
    """Compatibility hook: posture never establishes a prop release.

    A reclining person can hold a prop. Neither silence in other fields nor
    duplicated prose proves otherwise; changing this relationship requires
    source-backed content revision, not deterministic normalization.
    """
    return 0


# === 规则 8：锚点位置约束（"一点X"/"一截X" 模糊数量词追加空间方位） ===

# _ANCHOR_VAGUE_KW 已在顶部 import 自 prompt_semantic.registry


def _fix_vague_anchor(shot: dict) -> int:
    """Rule 8：模糊数量词 + 危险锚点 → 追加精确空间方位（治"匕首长头上"）.

    例：
      "一点刀尖" → "一点刀尖（刀尖位于[目标]头顶正上方约 1cm，不是从[目标]头里长出来的）"
      "仅露双眼" → "仅露双眼（不是上半身出水）"
    """
    fixed = 0
    for field in ("ff_desc", "prompt"):
        text = shot.get(field, "")
        if not text or not isinstance(text, str):
            continue
        # 1) 模糊数量词 + 武器 → 追加否定反例
        def _replace_anchor(m):
            vague, target = m.group(1), m.group(2)
            return f"{vague}{target}（{target}是独立物体，不是从角色身上长出来的）"
        new_text = _ANCHOR_VAGUE_KW.sub(_replace_anchor, text)
        # 2) "仅露双眼" 类 → 追加"不是上半身出水"
        if "仅露双眼" in new_text and "不是上半身" not in new_text:
            new_text = new_text.replace("仅露双眼", "仅露双眼（不是上半身出水）", 1)
            fixed += 1
        # 3) "潜伏水下" 类 → 追加"严格只露头顶 Xcm"
        if re.search(r"潜伏水下", new_text) and "约 2cm" not in new_text:
            new_text = re.sub(
                r"潜伏水下([^，。、\n]{0,8})",
                r"潜伏水下\1（仅头顶约 2cm 露出水面，不是上半身出水）",
                new_text, count=1
            )
            fixed += 1
        if new_text != text:
            shot[field] = new_text
            fixed += 1
    return fixed


# === 规则 9：模糊方位词替换（治"回首"→朝天看） ===

# 语义方位由检查与模型修订负责，不作猜测性替换。


def _fix_vague_direction(shot: dict) -> int:
    """方向、视线目标和睁闭眼属于语义关系，不能从布局次序猜测。

    保留原文，由既有方向检查及语义审计反馈给模型修订。
    """
    return 0


class V643PromptEnforcementConstraint(Constraint):
    """v6.43 prompt 强化规则的硬修复层.

    触发场景：LLM 收到新规则但仍写老模板（"一叶小舟" / "仰卧不动" / kf 复述）。
    修法：确定性字符串替换 + 文本重叠检测强制改写；方位语义保持原文。
    幂等：二次运行结果稳定。
    """

    name = "v6_43_prompt_enforcement"
    priority = 5  # 早执行——独立于 LLM 输出的基础改写

    def apply(self, ctx) -> list[RepairRecord]:
        """v6.43 合并 constraint：返回 list[RepairRecord]（v6.42 框架协议）.

        28 个兄弟 constraint 全部走 list 协议；本文件是 v6.42 迁移时唯一遗漏
        返回 int 的，导致 engine.apply_constraints 调 extend(int) 触发
        'int' object is not iterable 4 次。改为返回 []（修复次数已在 log 报告）。
        """
        fixed = 0
        warnings: list[RepairRecord] = []
        scene_objects = getattr(ctx, "script", None)
        scene_objects = scene_objects.get("scene_objects") or {} if isinstance(scene_objects, dict) else {}
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # 先保存原始 ff_desc / lf_desc（kf 重叠检测需要 LLM 写的版本，不含改写后）
            orig_ff = shot.get("ff_desc", "")
            orig_lf = shot.get("lf_desc", "")
            # Rule 4 现在依赖 shot 的 location/char_layout，所以传 shot 而非只传 text
            shot_location = str(shot.get("location", "") or shot.get("scene", "") or "")
            anchors = scene_anchors_for(shot_location)
            # 从 char_layout 抽角色名 + position（Rule 4 替换需要）
            char_names, char_positions = self._extract_char_names_and_positions(shot)
            # 规则 1 替换文本：船尺寸以 scene_objects 为唯一来源（v6.52 教训）
            boat_repl = build_boat_replacement(shot, scene_objects)
            for field in ("ff_desc", "lf_desc", "prompt", "motion_desc"):
                text = shot.get(field, "")
                if not text or not isinstance(text, str):
                    continue
                if (_rule_enabled("HEAD_FIX") and _needs_head_fix(text)
                        and _head_fix_context_ok(shot, text)):
                    warnings.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="warn", target=field, before=text[:120], after=text[:120],
                        detail="姿态/眼态需要原文或状态链证据，保留原文交 audit，不自动补闭眼或僵直。",
                    ))
                new_text = self._enforce_all(
                    text, shot, field, char_names, anchors, char_positions,
                    boat_repl=boat_repl,
                )
                if new_text != text:
                    shot[field] = new_text
                    fixed += 1
            # kf pre 复述检测：只报告，不再用固定构图模板覆盖 KF 事件正文。
            if _rule_enabled("KF_REPETITION"):
                for kf_i, kf in enumerate(shot.get("keyframes") or []):
                    if not isinstance(kf, dict):
                        continue
                    stage = str(kf.get("stage") or "")
                    desc = str(kf.get("desc") or "")
                    if not desc:
                        continue
                    if stage in ("pre", "approach") and orig_ff:
                        overlap = _overlap_ratio(desc, orig_ff)
                        if overlap < 0.40:
                            continue
                    elif stage in ("after", "exit", "react") and orig_lf:
                        overlap = _overlap_ratio(desc, orig_lf)
                        if overlap < 0.50:
                            continue
                    else:
                        continue
                    warnings.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="warn",
                        target=f"keyframes[{kf_i}].desc",
                        before=desc[:120],
                        after=desc[:120],
                        detail=(
                            f"关键帧与 {'ff' if stage in ('pre','approach') else 'lf'} "
                            f"文本重叠 {overlap:.2f}；保留事件原文，交 audit/LLM 改写"
                        ),
                    ))
            # Rule 5：threat_source facing 背对 → 侧身
            if _rule_enabled("THREAT_FACING"):
                fixed += _fix_threat_facing(shot)
            # Rule 6：时序塌缩（pre-state + attack 混写）
            if _rule_enabled("TEMPORAL_COLLAPSE"):
                fixed += _fix_temporal_collapse(shot)
            # Rule 7：道具主谓歧义（"X手+道具" 误读为握）
            if _rule_enabled("HANDLE_AMBIGUITY"):
                fixed += _fix_handle_ambiguity(shot)
            # Rule 8：锚点位置约束（"一点X" 模糊数量词追加空间方位）
            if _rule_enabled("VAGUE_ANCHOR"):
                fixed += _fix_vague_anchor(shot)
            # Rule 9：模糊方位词替换（"回首"→"侧头凝望"）
            if _rule_enabled("VAGUE_DIRECTION"):
                fixed += _fix_vague_direction(shot)
            # === v6.43 实体-空间语义层（PromptSemanticNormalizer）===
            # 原 Rule 10/11/12（角色 anchor dedup / 道具物理位置 / 刺客水下矛盾）
            # 重构为通用语义层：新增 prop/角色/场景只改 prompt_semantic/registry.py
            # 数据表，不改代码。详见 step01_script_gen/prompt_semantic/ 包。
            if _rule_enabled("SEMANTIC_NORMALIZER"):
                fixed += self._run_semantic_normalizer(shot, is_hook)
        # v6.42 框架要求 list[RepairRecord]；按修复次数填占位记录（engine
        # 层走 repairs_by_constraint 计数，不消费元素内容）。
        if fixed > 0:
            return warnings + [RepairRecord(
                constraint_name=self.name, shot_index=None,
                action="rewrite", target="multi", before="", after="",
            )] * fixed
        return warnings

    def _log_repair_count(self, count: int) -> None:
        """把 fixed 计数写入 log（替代旧的 return int）."""
        if count:
            try:
                from shared.tee_logger import get_logger
                get_logger().info(f"[CONSTRAINT] v6_43_prompt_enforcement 修复 {count} 处")
            except Exception:
                pass

    def _run_semantic_normalizer(self, shot: dict, is_hook: bool) -> int:
        """调用 prompt_semantic.PromptSemanticNormalizer 处理 3 类语义问题.

        对 ff_desc/lf_desc/prompt/motion_desc 4 个字段各跑一次 normalize().
        幂等：第二次跑无新 violation（normalizer 内部已用 priority+seen 防护）.
        """
        try:
            from step01_script_gen.prompt_semantic import PromptSemanticNormalizer
        except ImportError:
            return 0
        location = str(shot.get("location", "") or shot.get("scene", "") or "")
        normalizer = PromptSemanticNormalizer(
            scene=location,
            char_layout=shot.get("char_layout", []),
        )
        fixed = 0
        for field in ("ff_desc", "lf_desc", "prompt", "motion_desc"):
            text = shot.get(field, "")
            if not text or not isinstance(text, str):
                continue
            result = normalizer.normalize(text, is_hook=is_hook)
            if result.text != text:
                shot[field] = result.text
                fixed += 1
        return fixed

    def _extract_char_names(self, shot: dict) -> list[str]:
        """从 shot.char_layout 抽取所有角色名（去重保序）."""
        layout = shot.get("char_layout") or []
        names = []
        for item in layout:
            if not isinstance(item, dict):
                continue
            n = str(item.get("name", "")).strip()
            if n and n not in names:
                names.append(n)
        # 兜底：无 char_layout 用 fallback
        return names or list(_FALLBACK_CHAR_NAMES)

    def _extract_char_names_and_positions(self, shot: dict) -> tuple[list[str], dict[str, str]]:
        """从 shot.char_layout 抽 (角色名, 角色→position) —— 供 Rule 4 优先级用."""
        layout = shot.get("char_layout") or []
        names: list[str] = []
        positions: dict[str, str] = {}
        for item in layout:
            if not isinstance(item, dict):
                continue
            n = str(item.get("name", "")).strip()
            p = str(item.get("position", "")).strip()
            if not n:
                continue
            if n not in names:
                names.append(n)
            positions[n] = p
        return (names or list(_FALLBACK_CHAR_NAMES)), positions

    def _enforce_all(
        self, text: str, shot: dict, field: str,
        char_names: list[str], anchors: tuple, char_positions: dict[str, str] | None = None,
        boat_repl: str | None = None,
    ) -> str:
        """对单字段应用所有规则（各段可经 CONSTRAINT_V643_*_ENABLED 独立禁用）.

        boat_repl=None（默认）时按空 scene_objects 自动生成（走历史兜底文本）；
        apply() 传本镜动态生成的替换文本。
        """
        out = text
        # 规则 1：小舟 → 舴艋舟（替换文本按本镜 scene_objects 动态生成）
        if _rule_enabled("BOAT"):
            if boat_repl is None:
                boat_repl = build_boat_replacement(shot, {})
            # v6.62（09-06）守卫：替换文本自身必须括号平衡——09-05 hook 实测
            # 替换文本含未闭合括号时，其后所有锚点注入/消毒都在破损文本上跑
            # （prompt 半截截断），比不替换更糟。不平衡 → 剥掉括号段用裸词替换。
            _repl = boat_repl
            if _repl.count("（") != _repl.count("）") or _repl.count("(") != _repl.count(")"):
                _repl = _repl.split("（")[0].split("(")[0].rstrip("，, ") or "无篷舴艋舟"
            _seen_boat_spec = _has_boat_spec(out)
            for pat in _BOAT_PATTERNS:
                # v6.62（09-06）防叠：替换点紧邻前缀已是「舴艋/无篷/窄身」形制词时
                # 不整词替换（原文「舴艋小舟」→「舴艋无篷舴艋舟」重复实测）——
                # 只补尺寸段（含闭合括号），形制词由前缀 + 原文剩余字构成。
                def _sub_guard(m: "re.Match[str]") -> str:
                    nonlocal _seen_boat_spec
                    head = out[max(0, m.start() - 2): m.start()]
                    if any(w in head for w in ("舴艋", "无篷", "窄身")):
                        _i = _repl.find("（")
                        return _repl[_i:] if _i >= 0 else ""  # 「（长5.5米…，可容N人）」
                    # One concrete spec per field is enough.  re.sub invokes
                    # this guard for every match before assigning `out`, so a
                    # plain _has_boat_spec(out) check would still duplicate
                    # when two generic 小舟 exist in the same pass.
                    if _seen_boat_spec:
                        return "无篷舴艋舟"
                    _seen_boat_spec = True
                    return _repl
                out = pat.sub(_sub_guard, out)
        # 规则 2：仰卧 + 头部固定
        # 只允许死亡/受伤/昏迷上下文；活人仰卧吹箫、饮酒、仰望不得被修成尸体态。
        if _rule_enabled("HEAD_FIX"):
            out = _remove_invalid_head_state(out, shot)
            if _needs_head_fix(out) and _head_fix_context_ok(shot, out):
                out = _append_head_fix(out)
        # 规则 4：抽象方位 → 具象（按本镜 location 场景 anchor 动态生成）
        if _rule_enabled("ABSTRACT_ANCHOR") and anchors:
            replacements = _make_pattern_replacements(char_names, anchors, char_positions)
            for pat, repl in replacements:
                out = pat.sub(repl, out)
        return out

    def _fix_kf_repetition(
        self, shot: dict, orig_ff: str, orig_lf: str, candidate_names: list[str] | None = None,
    ) -> int:
        """kf pre 与原始 ff_desc 文本重叠 >70% → 改写为构图级细节模板.

        用原始 ff/lf（未改写版）做对照：避免改写后重叠率被稀释导致漏检。
        candidate_names：本镜可用角色名（char_layout + characters_bank），替代
        写死的小说角色正则（v6.60.1）；出现在 ff 里的才进改写句。
        返回 1 表示修复了至少一个 kf，0 表示无 kf 需修复。
        """
        fixed = 0
        ff = orig_ff or shot.get("ff_desc", "")
        lf = orig_lf or shot.get("lf_desc", "")
        kfs = shot.get("keyframes", []) or []
        if not ff or not kfs:
            return 0
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            kf_stage = kf.get("stage", "")
            kf_desc = kf.get("desc", "")
            if not kf_desc:
                continue
            # 仅检测 pre-stage + approach 前置帧（这些最容易复述 ff_desc）
            if kf_stage not in ("pre", "approach"):
                continue
            # kf pre 阈值 0.40：kf 短文本（30-50 字），50%+ 4-gram 重叠就是同义复述；
            # 严格 0.70 漏掉「ff 35 字 + kf 39 字、共享 15 个 4-gram」这种"近 1:1 复述"
            overlap = _overlap_ratio(kf_desc, ff)
            if overlap < 0.40:
                continue
            # 强制改写：保留 stage/t_sec/desc 长度相近，改为"构图级细节"模板。
            # 角色名从 char_layout/角色库动态取（v6.60.1 替代写死的小说角色正则），
            # 出现在 ff 文本里的才点名（保序去重）；场景景物不再写死——旧模板的
            # "船舷木纹/荷叶/冷月"是锦衣夜行专属，换小说即错，环境细节交给 scene_anchors。
            chars_unique = [n for n in dict.fromkeys(candidate_names or []) if n and n in ff]
            name_part = (
                "，".join(
                    f"{c}姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述）"
                    for c in chars_unique
                )
                if chars_unique
                else "出镜角色姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述）"
            )
            new_desc = (
                f"中近景构图：静态定格。"
                + name_part
                + "。前景环境虚化，中景角色姿态定格，背景光位与首帧一致。镜头固定，等待动作启动。"
            )
            kf["desc"] = new_desc
            fixed += 1
            # 同步检测 lf 复述（仅 after/exit）
        for kf in kfs:
            if not isinstance(kf, dict):
                continue
            kf_stage = kf.get("stage", "")
            kf_desc = kf.get("desc", "")
            if kf_stage not in ("after", "exit", "react"):
                continue
            if not lf or not kf_desc:
                continue
            # kf after/exit 阈值 0.50：尾镜后留痕应补 lf 之外的细节（涟漪圈数/血迹扩散）
            overlap = _overlap_ratio(kf_desc, lf)
            if overlap < 0.50:
                continue
            # 改写为 lf 后续环境留痕（留痕类型按本镜场景选择，不写死水乡意象）
            kf["desc"] = (
                "远景/中景构图：lf 静态定格后的环境留痕。"
                "环境留痕细节（水面涟漪/烟尘/散落物等，按本镜场景选择）位置已标定。"
                "角色体态最终定格（手指松开/双眼圆睁角度固定）。"
                "等待下一镜切换。"
            )
            fixed += 1
        return fixed
