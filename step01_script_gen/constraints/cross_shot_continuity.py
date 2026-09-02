"""K2. 跨镜衔接约束（v6.51 新增）.

扫描相邻镜头对，检测 4 类衔接问题：
  1. scene 切换缺转场：场景变了但 clip_transition 是硬切 → 改淡入淡出
  2. LF→FF 状态跳变：上镜 LF 角色终态 vs 下镜 FF 同角色初态，明显矛盾 → WARN
  3. narration 连续重复：相邻镜 narration_segment 重复度 > 70% → WARN
  4. LF 同态重复：相邻镜 LF 描述同主题（如 shot10/10.5 都写"马车驶出"）→ WARN

severity=WARN：只标记+确定性修复（转场补填），不触发 regen。
幂等：修复后 shot 含修复标记（转场已是淡入淡出/已加 transition note）。
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 状态跳变检测关键词对（上镜 LF 有 X + 下镜 FF 有 Y = 跳变）
# 方向：LF 的"终态" → FF 的"初态"，如果 FF 比 LF 更"早"则跳变
_JUMP_BACK_PAIRS = [
    # 入水 → 出水（时序倒流）
    (["没入水中", "潜入水中", "消失在水中", "落水", "入水", "沉入水"],
     ["跃出", "探出", "冲出", "破水", "从水中", "水里出来"]),
    # 死亡 → 站立（时序倒流）
    (["已无神", "双目无神", "仰倒", "僵卧", "倒下", "倒地", "晕厥"],
     ["站立", "端坐", "跪坐", "俯身", "跃起", "转身"]),
    # 尖叫完 → 张嘴欲呼（时序倒流）
    (["尖叫出声", "一声尖叫", "惊叫出声", "喊出声", "失声尖叫"],
     ["欲呼", "张嘴欲呼", "张口待叫", "将要喊"]),
]


class CrossShotContinuityConstraint(Constraint):
    """K2. 跨镜衔接检测 + 确定性修复."""

    name = "cross_shot_continuity"
    severity = Severity.WARN
    fields = ("clip_transition", "ff_desc", "lf_desc", "narration_segment")
    skip_hook = True  # hook 独立，不参与正片衔接

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots if isinstance(ctx.shots, list) else []
        if len(shots) < 2:
            return repairs

        for i in range(len(shots) - 1):
            curr = shots[i]
            next_shot = shots[i + 1]
            if not isinstance(curr, dict) or not isinstance(next_shot, dict):
                continue

            # --- 检测 1: 场景切换缺转场（scene 不同 OR location 空间词切换） ---
            curr_scene = curr.get("scene", "") or ""
            next_scene = next_shot.get("scene", "") or ""
            curr_loc = curr.get("location", "") or ""
            next_loc = next_shot.get("location", "") or ""
            is_scene_diff = bool(curr_scene and next_scene and curr_scene != next_scene)
            is_loc_switch = self._is_location_space_switch(curr_loc, next_loc)
            if is_scene_diff or is_loc_switch:
                ct = next_shot.get("clip_transition", "") or ""
                if "硬切" in ct or not ct:
                    old_ct = ct
                    next_shot["clip_transition"] = "淡入淡出，0.5s"
                    if is_scene_diff:
                        detail = f"场景切换：{curr_scene} → {next_scene}"
                    else:
                        detail = f"空间切换：{curr_loc} → {next_loc}"
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=next_shot.get("index"),
                            action="fix_transition",
                            target="clip_transition",
                            before=old_ct or "（空）",
                            after="淡入淡出，0.5s",
                            detail=detail,
                        )
                    )

            # --- 检测 2: LF→FF 状态跳变（WARN-only，不硬改） ---
            curr_lf = curr.get("lf_desc", "") or ""
            next_ff = next_shot.get("ff_desc", "") or ""
            if curr_lf and next_ff:
                jump = self._detect_state_jump(curr_lf, next_ff)
                if jump:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=next_shot.get("index"),
                            action="warn",
                            target="ff_desc",
                            before=f"上镜 LF 状态: {jump[0][:40]}",
                            after=f"本镜 FF 状态: {jump[1][:40]}",
                            detail="LF→FF 状态跳变（时序倒流）",
                        )
                    )

            # --- 检测 3: narration 连续重复 ---
            curr_narr = curr.get("narration_segment", "") or ""
            next_narr = next_shot.get("narration_segment", "") or ""
            if curr_narr and next_narr and len(curr_narr) > 5 and len(next_narr) > 5:
                ratio = difflib.SequenceMatcher(None, curr_narr, next_narr).ratio()
                if ratio > 0.7:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=next_shot.get("index"),
                            action="warn",
                            target="narration_segment",
                            before=curr_narr[:40],
                            after=next_narr[:40],
                            detail=f"相邻镜 narration 重复度 {ratio:.0%}",
                        )
                    )

            # --- 检测 4: LF 同态重复（相邻镜 LF 核心动作相同） ---
            if curr_lf and next_ff:
                lf_sim = difflib.SequenceMatcher(
                    None,
                    self._strip_static(curr_lf),
                    self._strip_static(next_shot.get("lf_desc", "") or ""),
                ).ratio()
                if lf_sim > 0.6:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=next_shot.get("index"),
                            action="warn",
                            target="lf_desc",
                            before=curr_lf[:40],
                            after=(next_shot.get("lf_desc", "") or "")[:40],
                            detail=f"相邻镜 LF 重复度 {lf_sim:.0%}（内容冗余）",
                        )
                    )

        return repairs

    @staticmethod
    def _detect_state_jump(lf_text: str, ff_text: str) -> tuple[str, str] | None:
        """检测 LF→FF 是否有状态跳变（时序倒流）.

        返回 (lf片段, ff片段) 或 None。
        """
        for lf_markers, ff_markers in _JUMP_BACK_PAIRS:
            lf_hit = next((m for m in lf_markers if m in lf_text), None)
            ff_hit = next((m for m in ff_markers if m in ff_text), None)
            if lf_hit and ff_hit:
                return (lf_hit, ff_hit)
        return None

    @staticmethod
    def _strip_static(text: str) -> str:
        """剥离 ff/lf 中的静态描述（角色外貌/场景环境），只留动作动词序列，
        用于相似度比较（避免"杨旭白袍杨旭白袍"导致的假重复）。"""
        # 简单实现：只保留动词+名词组合，去掉常见的静态形容词
        import re
        # 去掉所有外貌/穿着/环境描述（粗略但够用）
        static_patterns = [
            r"身穿[^，。；]*",
            r"[^，。；]*袍[^，。；]*",
            r"[^，。；]*色[^，。；]*光[^，。；]*",
            r"[，。；]?\s*【[^】]*】",  # 去掉【色调】块
        ]
        result = text
        for pat in static_patterns:
            result = re.sub(pat, "", result)
        return result

    @staticmethod
    def _is_location_space_switch(loc_a, loc_b):
        """检测相邻镜头 location 是否有明显空间跳转（同 scene 也能发生）.

        逻辑：两侧 location 都非空 且 价值集不同 → 跳转。
        不同集 = 至少一个空间词仅出现在一侧（不是两侧都有）。
        """
        if not loc_a or not loc_b:
            return False
        if loc_a == loc_b:
            return False
        # 空间词集（可添加，不确定性顺序）
        space_words = (
            "水下", "水面上", "水面", "水边",
            "船头", "船尾", "船舷", "船板",
            "岸边", "岸头", "岸上",
            "庄内", "庄外", "门内", "门外",
            "驾车", "车帘", "车辕",
            "室内", "室外", "院内", "院外",
            "舱内", "舱外",
        )
        words_a = {w for w in space_words if w in loc_a}
        words_b = {w for w in space_words if w in loc_b}
        # 双方都没有空间词，不能判定
        if not words_a and not words_b:
            return False
        # 两者集合不同 → 空间跳转
        return words_a != words_b
