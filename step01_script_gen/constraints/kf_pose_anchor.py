"""F4. kf 姿态+位置锚定硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 4 处关键帧质量告警：
  - 3 个中间 kf desc 缺角色姿态(仰卧/跪坐/站) + 位置(船头/船尾/画左/画右)
  - 3 个中间 kf 威胁源未写朝向目标

物理逻辑：kf 是独立生图的，图片模型看不到 ff/lf/前镜——如果 kf.desc 只写
动作过程（破水/举刀）不写角色此刻姿态+位置，模型会画成「漂浮在画面中
的角色，姿态不明」。

修法：扫每个 kf.desc：
  (A) P1-5 姿态锚定：若 desc 既无姿态词（POSE_REGISTRY 任何分类）又无位置
      词（船头/船尾/画左/画右/门口/车辕/岸边/画上方/画下方），追加通用
      锚定模板「（本帧各角色姿态位置见首帧/尾帧）」（承袭 W16 弱模板的
      「本帧画面」注入锚机制）。
  (B) P1-6 威胁源朝向：若 desc 含 THREAT_WORDS_ENLARGED（刺客/黑影/蒙面/潜伏）
      但无朝向词（侧身朝/紧盯/朝向/斜望/正对/背对镜头/侧身），追加
      「侧身朝目标，紧盯目标」。

幂等：已含「本帧各角色」/「侧身朝」视为已修。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.prompt_semantic.registry import (
    POSE_REGISTRY,
    THREAT_WORDS_ENLARGED,
)
from .base import Constraint
from .types import RepairRecord, Severity


# 位置词（场景内/画面空间）—— 简版，可与 scene_anchors 联动
_POSITION_WORDS = (
    "画左", "画右", "画上方", "画下方", "画中", "画前", "画后",
    "船头", "船尾", "船舷", "船中", "甲板",
    "车辕", "车前", "车后",
    "门口", "门内", "门边",
    "岸边", "水边",
    "左侧", "右侧", "上方", "下方", "中央",
    "亭中", "亭前", "廊下",
)

# 朝向词（威胁源-视线耦合）
_ORIENTATION_WORDS = (
    "侧身朝", "紧盯", "朝向", "斜望", "正对", "背对镜头", "侧身", "面向",
    "自水面下", "俯视", "仰视",
)


def _has_static_pose_word(text: str) -> bool:
    """检测 desc 是否含任一静态姿态词（reclining/sitting/standing/kneeling）.

    与 _has_pose_word 区别：in_motion 类的「举/挥/刺/跃」是动作动词，
    不是静态姿态，不能豁免威胁源朝向（动作态反而最需要写朝向）。
    """
    static_cats = ("reclining", "sitting", "standing", "kneeling")
    for cat in static_cats:
        for w in POSE_REGISTRY.get(cat, ()):
            if w in text:
                return True
    return False


def _has_pose_word(text: str) -> bool:
    """检测 desc 是否含任一姿态词（POSE_REGISTRY 全部分类）."""
    for cat in POSE_REGISTRY.values():
        for w in cat:
            if w in text:
                return True
    return False


def _has_position_word(text: str) -> bool:
    """检测 desc 是否含任一位置词."""
    return any(w in text for w in _POSITION_WORDS)


def _has_threat_word(text: str) -> bool:
    """检测 desc 是否含威胁源词."""
    return any(w in text for w in THREAT_WORDS_ENLARGED)


def _has_orientation_word(text: str) -> bool:
    """检测 desc 是否含朝向词."""
    return any(w in text for w in _ORIENTATION_WORDS)


class KfPoseAnchorConstraint(Constraint):
    """F4. kf 姿态+位置+威胁朝向三合一锚定.

    severity=WARN: 已有确定性重写，无需 regen.
    """

    name = "kf_pose_anchor"
    severity = Severity.WARN
    fields = ()  # 扫 keyframes[N].desc

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                desc = kf.get("desc", "") or ""
                if not isinstance(desc, str) or not desc.strip():
                    continue

                original = desc

                # (A) P1-5 姿态+位置锚定
                # 已含「本帧各角色」视为已修（W16 弱模板注入锚同源）
                if "本帧各角色" not in desc:
                    if not _has_pose_word(desc) or not _has_position_word(desc):
                        anchor = "（本帧各角色姿态位置见首帧静态定格）"
                        desc = desc.rstrip("。.，,;；") + "。" + anchor

                # (B) P1-6 威胁源朝向
                # 威胁源即使处于踞蹲/站立等静态姿态，也仍需要明确朝向目标；
                # 图片模型无法从「踞蹲举刃」推断刀尖应该指向谁。
                if _has_threat_word(desc) and "侧身朝目标" not in desc:
                    if not _has_orientation_word(desc):
                        # 在末尾追加朝向锚（如果 desc 已有标点，先剥再加）
                        if desc.endswith(("。", ".", "！", "!", "？", "?")):
                            desc = desc + "威胁源侧身朝目标，紧盯目标。"
                        else:
                            desc = desc.rstrip("。.，,;；") + "。威胁源侧身朝目标，紧盯目标。"

                if desc != original:
                    kf["desc"] = desc
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="append",
                            target=f"keyframes[{j}].desc",
                            before=original[:60] + ("..." if len(original) > 60 else ""),
                            after=desc[:60] + ("..." if len(desc) > 60 else ""),
                        )
                    )
        return repairs
