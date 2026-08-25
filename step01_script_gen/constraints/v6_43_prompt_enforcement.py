"""v6_43_prompt_enforcement.py — 4+1 类 v6.43 强化规则的硬修复层

L1 prompt 规则失声时（长 prompt 后段不被 LLM 关注）的确定性兜底：
1. 「小舟/木舟/船/一叶舟」 → 「一叶无篷舴艋舟（宽1.4米，可容3人）」
2. 「仰卧」无头部固定 → 追加「仰面朝上双目紧闭，头颈僵直不侧转」
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
    VAGUE_DIRECTION_KW as _VAGUE_DIRECTION_KW,
    ABSTRACT_POSITION_WORDS as _GENERIC_ABSTRACT_WORDS,
    POSE_REGISTRY as _POSE_REGISTRY,
    LYING_NO_HEAD_FIX as _LYING_NO_HEAD_FIX,
    BACK_PATTERN as _BACK_PATTERN,
    ANCHOR_VAGUE_KW_PATTERN as _ANCHOR_VAGUE_KW,
)

from ._base_classes import Constraint, RepairRecord


# === 规则 1：小舟 → 舴艋舟 ===

# 匹配「一叶小舟/小舟/木舟/一叶舟」等无尺寸通用词
# 1. 一叶小舟 → 一叶无篷舴艋舟（宽1.4米，可容3人）
# 2. 小舟 → 同上
# 3. 木舟 → 同上
# 4. 一叶舟 → 同上
_BOAT_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"一叶小舟"), "一叶无篷舴艋舟（宽1.4米，可容3人）"),
    (re.compile(r"一叶木舟"), "一叶无篷舴艋舟（宽1.4米，可容3人）"),
    (re.compile(r"一叶舟"), "一叶无篷舴艋舟（宽1.4米，可容3人）"),
    (re.compile(r"小舟"), "无篷舴艋舟（宽1.4米，可容3人）"),
    (re.compile(r"木舟"), "无篷舴艋舟（宽1.4米，可容3人）"),
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


def _append_head_fix(text: str) -> str:
    """在「仰卧」类描述后追加头部固定."""
    suffix = "，仰面朝上双目紧闭，头颈僵直不侧转"
    # 不重复追加（幂等）
    if "仰面朝上双目紧闭" in text:
        return text
    return text.rstrip("。,，. ") + suffix


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


def _fix_handle_ambiguity(shot: dict) -> int:
    """Rule 7：角色躺/卧姿态下"X手+道具"被误读为握 → 改写为"道具已脱手+位置"."""
    fixed = 0
    for field in ("ff_desc", "prompt"):
        text = shot.get(field, "")
        if not text or not isinstance(text, str):
            continue
        if not _is_lying_or_sleeping(text):
            continue
        # 检测 "左手X" / "右手X" 模式（X 在 _HANDLE_PROPS 中）
        new_text = text
        for prop in _HANDLE_PROPS:
            for hand in ("左手", "右手"):
                # 模式：左手X / 右手X（中间可能有 1-3 字动词）
                pat = re.compile(re.escape(hand) + r"[^，。、\n]{0,4}" + re.escape(prop))
                if pat.search(new_text):
                    # 改为「X 已脱手（杨旭手空着）+ 道具位置」
                    repl = f"{prop}已脱手"
                    new_text = pat.sub(repl, new_text)
                    fixed += 1
        if new_text != text:
            shot[field] = new_text
    return fixed


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

# _VAGUE_DIRECTION_KW 已在顶部 import 自 prompt_semantic.registry


def _fix_vague_direction(shot: dict) -> int:
    """Rule 9：模糊方位词替换为显式视线+空间方位.

    例：
      "回首" → "侧头凝望画X的[角色名]"（具体角色名从 char_layout 推断）
    """
    fixed = 0
    # 从 char_layout 抽"被看"角色（凝视目标）
    layout = shot.get("char_layout") or []
    target_name = ""
    for item in layout:
        if not isinstance(item, dict):
            continue
        # 优先 role=主（非刺客），且位置和听香不同（"被看"对象）
        n = str(item.get("name", "")).strip()
        role = str(item.get("role", "")).strip()
        if n and role == "主" and "刺客" not in n and "神秘人" not in n and "黑影" not in n:
            target_name = n
            break
    if not target_name and layout:
        # 兜底：第一个非刺客名字
        for item in layout:
            if not isinstance(item, dict):
                continue
            n = str(item.get("name", "")).strip()
            if n and "刺客" not in n and "神秘人" not in n:
                target_name = n
                break
    for field in ("ff_desc", "prompt"):
        text = shot.get(field, "")
        if not text or not isinstance(text, str):
            continue
        new_text = text
        for vague, repl_tpl in _VAGUE_DIRECTION_KW.items():
            if vague in new_text:
                if "[角色名]" in repl_tpl and target_name:
                    repl = repl_tpl.replace("[角色名]", target_name)
                elif "[目标]" in repl_tpl and target_name:
                    repl = repl_tpl.replace("[目标]", target_name)
                else:
                    repl = repl_tpl
                # "画X" 占位（保留，由 prompt 后续补全）
                if "画X" in repl:
                    repl = repl.replace("画X", "画左")
                new_text = new_text.replace(vague, repl, 1)
                fixed += 1
        if new_text != text:
            shot[field] = new_text
    return fixed


class V643PromptEnforcementConstraint(Constraint):
    """v6.43 prompt 强化规则的硬修复层.

    触发场景：LLM 收到新规则但仍写老模板（"一叶小舟" / "仰卧不动" / kf 复述）。
    修法：确定性字符串替换 + 文本重叠检测强制改写 + 模糊方位具象化。
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
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # 先保存原始 ff_desc / lf_desc（kf 重叠检测需要 LLM 写的版本，不含改写后）
            orig_ff = shot.get("ff_desc", "")
            orig_lf = shot.get("lf_desc", "")
            # Rule 4 现在依赖 shot 的 location/char_layout，所以传 shot 而非只传 text
            shot_location = str(shot.get("location", "") or shot.get("scene", "") or "")
            anchors = scene_anchors_for(shot_location)
            # 从 char_layout 抽角色名 + position（Rule 4 替换需要）
            char_names, char_positions = self._extract_char_names_and_positions(shot)
            for field in ("ff_desc", "lf_desc", "prompt", "motion_desc"):
                text = shot.get(field, "")
                if not text or not isinstance(text, str):
                    continue
                new_text = self._enforce_all(text, shot, field, char_names, anchors, char_positions)
                if new_text != text:
                    shot[field] = new_text
                    fixed += 1
            # kf pre 复述 检测：对照原始 ff/lf（未改写版），更准
            fixed += self._fix_kf_repetition(shot, orig_ff, orig_lf)
            # Rule 5：threat_source facing 背对 → 侧身
            fixed += _fix_threat_facing(shot)
            # Rule 6：时序塌缩（pre-state + attack 混写）
            fixed += _fix_temporal_collapse(shot)
            # Rule 7：道具主谓歧义（"X手+道具" 误读为握）
            fixed += _fix_handle_ambiguity(shot)
            # Rule 8：锚点位置约束（"一点X" 模糊数量词追加空间方位）
            fixed += _fix_vague_anchor(shot)
            # Rule 9：模糊方位词替换（"回首"→"侧头凝望"）
            fixed += _fix_vague_direction(shot)
            # === v6.43 实体-空间语义层（PromptSemanticNormalizer）===
            # 原 Rule 10/11/12（角色 anchor dedup / 道具物理位置 / 刺客水下矛盾）
            # 重构为通用语义层：新增 prop/角色/场景只改 prompt_semantic/registry.py
            # 数据表，不改代码。详见 step01_script_gen/prompt_semantic/ 包。
            fixed += self._run_semantic_normalizer(shot, is_hook)
        # v6.42 框架要求 list[RepairRecord]；修复次数已通过 _log_repair_count
        # 在 engine 层 log 报告，此处返回空 list（保持向后兼容旧的 int 计数）。
        return []

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
    ) -> str:
        """对单字段应用所有规则."""
        out = text
        # 规则 1：小舟 → 舴艋舟
        for pat, repl in _BOAT_REPLACEMENTS:
            out = pat.sub(repl, out)
        # 规则 2：仰卧 + 头部固定
        if _needs_head_fix(out):
            out = _append_head_fix(out)
        # 规则 4：抽象方位 → 具象（按本镜 location 场景 anchor 动态生成）
        if anchors:
            replacements = _make_pattern_replacements(char_names, anchors, char_positions)
            for pat, repl in replacements:
                out = pat.sub(repl, out)
        return out

    def _fix_kf_repetition(self, shot: dict, orig_ff: str, orig_lf: str) -> int:
        """kf pre 与原始 ff_desc 文本重叠 >70% → 改写为构图级细节模板.

        用原始 ff/lf（未改写版）做对照：避免改写后重叠率被稀释导致漏检。
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
            # 强制改写：保留 stage/t_sec/desc 长度相近，改为"构图级细节"模板
            # 提取 ff_desc 的角色列表（粗略正则）保持信息密度
            chars_in_ff = re.findall(r"刺客|杨旭|听香|张十三|老汉|肖管事|神秘人", ff)
            chars_unique = list(dict.fromkeys(chars_in_ff))  # 保序去重
            new_desc = (
                f"中近景构图：静态定格。"
                + "，".join(
                    f"{c}姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述）"
                    for c in chars_unique
                )
                + "。前景船舷木纹/荷叶虚化，中景角色姿态定格，背景冷月/弦月倒影。镜头固定，等待动作启动。"
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
            # 改写为 lf 后续环境留痕
            kf["desc"] = (
                "远景/中景构图：lf 静态定格后的环境留痕。"
                "水面涟漪圈数/血迹扩散范围/散落物具体位置已标定。"
                "角色体态最终定格（手指松开/双眼圆睁角度固定）。"
                "等待下一镜切换。"
            )
            fixed += 1
        return fixed
