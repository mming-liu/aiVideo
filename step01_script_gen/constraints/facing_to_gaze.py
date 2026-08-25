"""T2. char_layout「面向[人名]」→ 「目光凝望[人名]」约束（v6.42 迁移自 _hard_fix_facing_to_gaze）.

v6.40+（08-23）char_layout「面向[人名]」→ 改写为「目光凝望[人名]」。

08-23 实测：shot 1 char_layout 写"听香 面向杨旭"，prompt 只写"听香撑油纸伞立于身侧"
无任何"看/望杨旭"模式，_hard_fix_gaze_direction 不触发（它只匹配「看/望+人名」），
模型渲染时听香身体朝向杨旭但眼神飘向他身后的水面——动作 ≠ 视线。

修法：扫所有 char_layout 条目，若 facing="面向[人名]"（人名而非"画左/画右/画上"等方位），
且 prompt/ff/lf/motion/kf.desc 字段中无"目光/凝望/凝视"+该人名 → 在角色名首次出现
之后追加"目光凝望[人名]"。

幂等：已含 _F2G_GAZE_MARKERS 任一者 + 该人名则跳过。

DIRECTION_WORDS 不再写死 10 词，改为从 scene_anchors 字典动态
生成（BASE_POS_WORDS + 所有 SCENE_ANCHORS + 镜头/观众）——新场景自动跟随。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import BASE_POS_WORDS, SCENE_ANCHORS
from .base import Constraint
from .types import RepairRecord, Severity


_F2G_GAZE_MARKERS = ("目光", "凝望", "仰望", "俯视", "凝视")

# 名字池已删：facing_to_gaze 实际从 shot.char_layout 抽角色名（_is_person_target 内
# 1-4 字符中文短串判定已足够识别"人名"，无需要兜底写死 13 个名字）。如未来有
# 真正需要"known name"集合（如过滤别名），从 prompt_semantic 或 character_bank
# 动态取，不用再写硬编码 NAME_POOL。


def _build_direction_words() -> tuple[str, ...]:
    """从 scene_anchors 字典动态构建方向词黑名单.

    组成：
      - BASE_POS_WORDS（画X/前景/中景/后景）—— 任何场景
      - 所有 SCENE_ANCHORS 值（船头/船尾/桥头/桥尾/街口/巷尾/正厅/院门/窗前/殿前...）
      - 镜头 / 观众（人像摄影常用方位词）

    返回 tuple 用于快速 in 判定.
    """
    extra = ("镜头", "观众", "画上", "画下", "画上方", "画下方")
    seen: set[str] = set(BASE_POS_WORDS) | set(extra)
    for anchors in SCENE_ANCHORS.values():
        seen.update(anchors)
    return tuple(seen)


# 模块级常量（启动时构建一次）
DIRECTION_WORDS = _build_direction_words()

_FACING_RE = re.compile(r"^面向(.+)$")
# 复合方位词前缀剥离：LLM 写「面向画右的杨旭」时 target="画右的杨旭" 长 5 字 + 含"的"
# → _is_person_target 拒绝（>4 字或含非汉字）。剥离画X/前方/上方/水面 等前缀后留"杨旭"。
_FACING_PREFIX_RE = re.compile(
    r"^(?:画[左右中上下](?:的|之)?|前方的?|后方?|上方的?|下方的?|"
    r"左方的?|右方的?|岸上|水面|水下)([一-鿿]{2,4})$"
)


def _extract_person_target(raw: str) -> str:
    """从 char_layout.facing 的 target 字符串抽人名.

    处理「面向画右的杨旭」「面向画右岸上」「面向画左的水面」等复合写法：
    复合方位前缀 → 剥离；否则返回原值。
    """
    raw = raw.strip()
    m = _FACING_PREFIX_RE.match(raw)
    if m:
        return m.group(1)
    return raw


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
    """target 是否是人物名（而非方向词）.

    判定规则（按优先级）：
      1) 在 DIRECTION_WORDS 里 → 不是人名
      2) 中文短串（≤4 字符 + 全汉字）→ 视为人名
      3) 其他 → 不是人名

    不再依赖写死 NAME_POOL：实际数据来源是 shot.char_layout，
    校验时取角色 position/facing 字段已带正确人名。
    """
    target = target.strip()
    if not target:
        return False
    if target in DIRECTION_WORDS:
        return False
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
                target = _extract_person_target(target)  # 剥离"画右的"等复合前缀
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
