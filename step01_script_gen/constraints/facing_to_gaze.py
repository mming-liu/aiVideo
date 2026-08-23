"""T2. char_layout「面向[人名]」→ 「目光凝望[人名]」约束（v6.42 迁移自 _hard_fix_facing_to_gaze）.

v6.40+（08-23）char_layout「面向[人名]」→ 改写为「目光凝望[人名]」。

08-23 实测：shot 1 char_layout 写"听香 面向杨旭"，prompt 只写"听香撑油纸伞立于身侧"
无任何"看/望杨旭"模式，_hard_fix_gaze_direction 不触发（它只匹配「看/望+人名」），
模型渲染时听香身体朝向杨旭但眼神飘向他身后的水面——动作 ≠ 视线。

修法：扫所有 char_layout 条目，若 facing="面向[人名]"（人名而非"画左/画右/画上"等方位），
且 prompt/ff/lf/motion/kf.desc 字段中无"目光/凝望/凝视"+该人名 → 在角色名首次出现
之后追加"目光凝望[人名]"。

幂等：已含 _F2G_GAZE_MARKERS 任一者 + 该人名则跳过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_F2G_GAZE_MARKERS = ("目光", "凝望", "仰望", "俯视", "凝视")

# 名字池：兜底用主名清单（实际从 char_layout 提取）
NAME_POOL = ("杨旭", "听香", "刺客", "张十三", "老汉", "肖管事", "神秘人",
             "那女子", "女子", "那男子", "男子", "公子", "姑娘")

# 方向词（不是人名，是"面向画左/画右"等）
DIRECTION_WORDS = ("画左", "画右", "画中", "画上", "画下", "画上方", "画下方",
                   "船头", "船尾", "镜头", "观众")

_FACING_RE = re.compile(r"^面向(.+)$")


def _has_gaze_marker(text: str, target: str) -> bool:
    """text 中是否含 gaze 标记词 + target 人名（标记后 ≤20 字符内）."""
    for mk in _F2G_GAZE_MARKERS:
        idx = text.find(mk)
        if idx < 0:
            continue
        # 检查 mk 后 20 字符内是否含 target
        tail = text[idx:idx + 20]
        if target in tail:
            return True
    return False


def _is_person_target(target: str) -> bool:
    """target 是否是人物名（而非方向词）."""
    target = target.strip()
    if not target:
        return False
    if target in DIRECTION_WORDS:
        return False
    if target in NAME_POOL:
        return True
    # 中文姓名短（≤4 字符）+ 不含数字英文 → 视为人名
    if len(target) <= 4 and all("一" <= c <= "鿿" for c in target):
        return True
    return False


class FacingToGazeConstraint(Constraint):
    """T2. char_layout「面向[人名]」→ 在 prompt 追加「目光凝望[人名]」.

    severity=WARN: 改写是确定性，不触发 regen.
    """

    name = "facing_to_gaze"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            cl = shot.get("char_layout") or []
            if not isinstance(cl, list):
                continue
            # 收集本镜所有「角色X → 面向Y（Y 是人名）」对
            facing_pairs: list[tuple[str, str]] = []
            for item in cl:
                if not isinstance(item, dict):
                    continue
                x_name = item.get("name", "")
                facing = item.get("facing", "")
                if not (x_name and facing):
                    continue
                m = _FACING_RE.match(str(facing).strip())
                if not m:
                    continue
                target = m.group(1).strip()
                if _is_person_target(target):
                    facing_pairs.append((str(x_name), target))
            if not facing_pairs:
                continue

            # 把所有要改写的字段路径收集出来（包含 keyframes[i].desc）
            fld_paths: list[tuple[str, str]] = []  # (target_key, field_path)
            for fld in self.fields:
                if isinstance(shot.get(fld), str) and shot.get(fld):
                    fld_paths.append((fld, fld))
            kfs = shot.get("keyframes") or []
            for j, kf in enumerate(kfs):
                if isinstance(kf, dict) and isinstance(kf.get("desc"), str) and kf.get("desc"):
                    fld_paths.append((f"keyframes[{j}].desc", f"keyframes[{j}].desc"))

            for x_name, target in facing_pairs:
                for target_key, fld_path in fld_paths:
                    # 每次都从 shot 读最新值（避免前一对修改被后一对覆盖）
                    if target_key.startswith("keyframes["):
                        m_kf = re.match(r"keyframes\[(\d+)\]\.desc", target_key)
                        if not m_kf:
                            continue
                        kf_idx = int(m_kf.group(1))
                        cur_kf = (shot.get("keyframes") or [{}])[kf_idx] if kf_idx < len(shot.get("keyframes") or []) else None
                        if not isinstance(cur_kf, dict):
                            continue
                        v = cur_kf.get("desc") or ""
                    else:
                        v = shot.get(target_key) or ""
                    if not isinstance(v, str) or not v:
                        continue
                    # 幂等：已含 gaze marker + target 则跳过
                    if _has_gaze_marker(v, target):
                        continue
                    # 找 x_name 首次出现位置 → 定位到其所在分句末尾再插入
                    # （v6.43 08-23 审计：紧贴名字后插入会切开括号/定语，产生
                    # 「（画中听香，目光凝望…杨旭，面朝公子站立。特写…」式断句；
                    # 改到分句边界（下一个 ，。；！？）前插入，句法完整）
                    m = re.search(re.escape(x_name), v)
                    if not m:
                        continue
                    insert_at = m.end()
                    while insert_at < len(v) and v[insert_at] not in "，。；！？":
                        insert_at += 1
                    # 防重复追加
                    tail = v[insert_at:insert_at + 20]
                    if "目光凝望" in tail and target in tail:
                        continue
                    suffix = f"，目光凝望{target}"
                    new_v = v[:insert_at] + suffix + v[insert_at:]
                    if new_v != v:
                        if target_key.startswith("keyframes["):
                            (shot.get("keyframes") or [{}])[kf_idx]["desc"] = new_v
                        else:
                            shot[target_key] = new_v
                        repairs.append(
                            RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="append",
                                target=fld_path,
                                before=v,
                                after=new_v,
                            )
                        )
        return repairs
