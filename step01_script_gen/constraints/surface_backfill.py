"""F12. char_layout.surface 承重面回填（v6.62.1，09-06）.

历史问题：v6.45 把 surface 定为「LLM 必填 + warning-only 不硬填」——但实测
doubao/deepseek 从不填（9/11 镜缺），每轮 8-9 条 warning 纯噪音；且 surface
为空 → bridge 锚定块缺「（在船头上）」承重面 → LLM 自由发挥站位（08-26 hook
听香站水里实测）。v6.62 删了「未指定」占位符后，空 surface 连兜底都没有。

v6.62.1 裁决：承重面**可确定性回填**——承重面是物理事实（船头/甲板/岸边/
厅内/亭内/车辕…），不是语义创作。从本镜已有文本（location/prompt/ff_desc/
lf_desc + position）按表匹配即得，违反「硬修复层不写死语义」的红线不成立：
表是封闭空间词枚举，与 scene_anchors 同构。

回填顺序（首个命中者胜）：
  1. 文本显式 surface 词（船头/船尾/甲板/岸边/亭内/厅中/车辕…）
  2. position 派生（画中央船头→船头；画右中舱→中舱）
  3. scene/location 场景派生（*湾*→船板；*厅*→厅内地面；*车*→车板）
全部未命中 → 不填（真无法确定，留 warning 给修复轮）。

幂等：已有非空 surface（≠"未指定"）跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

# 表 1：显式承重面词（长词优先——「船头甲板」命中「甲板」前先试「船头」）
_SURFACE_WORDS = (
    "船头甲板", "船尾甲板",
    "船头", "船尾", "船中", "船舷", "船梢", "中舱", "舱内", "甲板", "船板",
    "岸边", "田埂", "亭内", "亭中", "廊下", "廊上",
    "厅中", "厅内", "厅前", "堂内", "堂中",
    "楼上", "楼下", "车内", "车辕", "车前", "车后",
    "门口", "门内", "门边", "阶前", "石阶", "台阶",
    "窗边", "案前", "桌前", "院中", "院内", "庭中",
    "桥头", "桥上", "塔顶", "城墙", "屋顶", "树上",
    "水中", "水面", "水下", "马背", "鞍上",
    "床榻", "榻上", "床上", "席上", "地面", "地上",
)

# 表 2：场景（scene/location）→ 兜底承重面（scene_anchors 同构思路，
# 表是「场景类型 → 物理承重面」的封闭映射，跨小说通用）
_SCENE_FALLBACK = (
    # (场景词, 兜底承重面) — 长词优先
    ("船", "船板"),
    ("舟", "船板"),
    ("湾", "船板"),
    ("车", "车板"),
    ("轿", "轿内"),
    ("厅", "厅内地面"),
    ("堂", "堂内地面"),
    ("亭", "亭内地面"),
    ("庄", "院内地面"),
    ("府", "院内地面"),
    ("镇", "街面"),
    ("街", "街面"),
    ("市", "街面"),
    ("山", "坡地"),
    ("林", "林地"),
    ("田", "田埂"),
    ("郊", "地面"),
    ("野", "地面"),
    ("崖", "崖顶"),
    ("桥", "桥面"),
)


def _derive_surface(shot: dict, item: dict) -> str:
    """从本镜文本/position/scene 推导该角色的承重面（首个命中者胜）."""
    # 1) 本镜文本显式 surface 词（position 优先——它就是该角色的站位描述）
    position = str(item.get("position", "") or "")
    scan_fields = (
        position,
        shot.get("location", "") or "",
        shot.get("ff_desc", "") or "",
        shot.get("lf_desc", "") or "",
        shot.get("prompt", "") or "",
    )
    for text in scan_fields:
        if not text:
            continue
        for w in _SURFACE_WORDS:
            if w in text:
                return w
    # 2) scene/location 场景兜底
    scene = (shot.get("scene", "") or "") + (shot.get("location", "") or "")
    for key, surf in _SCENE_FALLBACK:
        if key in scene:
            return surf
    return ""


class SurfaceBackfillConstraint(Constraint):
    """F12. char_layout.surface 空值确定性回填（position→文本→场景三级派生）.

    severity=WARN: 回填失败仍留 warning（真无法确定的场景交给修复轮）。
    """

    name = "surface_backfill"
    severity = Severity.WARN

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            layout = shot.get("char_layout")
            if not isinstance(layout, list) or not layout:
                continue
            label = "hook" if is_hook else f"shot{shot.get('index', idx)}"
            for j, item in enumerate(layout):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                surface = str(item.get("surface", "") or "").strip()
                if surface and surface != "未指定":
                    continue  # 已有真值，幂等
                derived = _derive_surface(shot, item)
                if derived:
                    item["surface"] = derived
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="set_field",
                            target=f"char_layout[{j}].surface",
                            before=surface,
                            after=derived,
                        )
                    )
        return repairs
