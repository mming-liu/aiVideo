"""T1. 船体宽度硬修复约束（v6.42 迁移自 _hard_fix_boat_width）.

v6.40+（08-23）船体宽度硬修复：检测「窄身」「长X米…舴艋舟/小舟」但无宽度
→ 补「宽X米」。

08-23 实测：shot 1 prompt 写「一叶小舟（长6米窄身无篷舴艋舟）」，「窄身」被模型
忠实渲染成"木板"——杨旭和听香坐着就把船占满，无视觉余量。LLM 自改时倾向
把"舴艋舟"删掉换"画舫"，但舴艋舟是原文场景标识不该改。确定性兜底：保留船型，
把"窄身"删/替换成宽度。

宽度来源（v6.60.1 硬编码清理）：本镜 scene_objects 命中的船规格（v6.52 协议，
shared/scene_objects_bank.boat_width_text）动态取值；未命中回退
BOAT_FALLBACK_WIDTH_M=1.4（历史硬编码值降级为兜底常量）。

改写规则（按字段 prompt/ff_desc/lf_desc/motion_desc/keyframes[*].desc）：
  ① 「长X米窄身」 → 「长X米宽W米」（保留米数，仅替"窄身"）
  ② 「窄身无篷舴艋舟」/「窄身无篷小舟」 → 「宽W米无篷…」（删除"窄身"，插入宽度）
  ③ 「长X米（…）舴艋舟」无"宽"字 → 同样插入"宽W米"在"无篷"前

幂等：已含"宽X米"或"宽身"则跳过。
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
from shared.scene_objects_bank import boat_width_text


# ① 长X米窄身 → 长X米宽W米
_RE_LONG_NARROW = re.compile(r"(长\d+(?:\.\d+)?米)窄身")
# ③ 长X米无篷 → 长X米宽W米无篷
_RE_LONG_NO_CANOPY = re.compile(r"(长\d+(?:\.\d+)?米)无篷")
# ② 单独"窄身" → 删除
_RE_NARROW_BARE = re.compile(r"窄身")


class BoatWidthConstraint(Constraint):
    """T1. 船体宽度：检测「窄身」/无宽度 → 补「宽W米」（W 取 scene_objects 船规格）.

    severity=WARN: 船宽补全是确定性改写.
    """

    name = "boat_width"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        scene_objects = getattr(ctx, "script", None)
        scene_objects = scene_objects.get("scene_objects") or {} if isinstance(scene_objects, dict) else {}
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            width_txt = boat_width_text(shot, scene_objects)
            # 收集要改写的字段（含 kf.desc——v6.41+ kf 描述漏修）
            fld_text: list[tuple[str, str]] = []
            for fld in self.fields:
                v = shot.get(fld)
                if isinstance(v, str) and v:
                    fld_text.append((fld, v))
            kfs = shot.get("keyframes") or []
            for j, kf in enumerate(kfs):
                if isinstance(kf, dict):
                    d = kf.get("desc")
                    if isinstance(d, str) and d:
                        fld_text.append((f"keyframes[{j}].desc", d))

            for fld, v in fld_text:
                # 幂等：已含宽度或宽身则跳过
                if re.search(r"宽\d", v) or "宽身" in v:
                    continue
                new_v = v
                # ① 「长X米窄身」 → 「长X米宽W米」
                new_v = _RE_LONG_NARROW.sub(lambda m: m.group(1) + width_txt, new_v)
                # ② 单独「窄身」前后无"长X米" → 整段删"窄身"
                new_v = _RE_NARROW_BARE.sub("", new_v)
                # ③ 含"长X米...无篷"但无"宽"字 → 在"无篷"前插"宽W米"
                if "无篷" in new_v and not re.search(r"宽\d", new_v):
                    new_v = _RE_LONG_NO_CANOPY.sub(lambda m: m.group(1) + width_txt + "无篷", new_v)
                if new_v != v:
                    # 写回原字段（kf 的 desc 需要特殊处理）
                    if fld.startswith("keyframes["):
                        m = re.match(r"keyframes\[(\d+)\]\.desc", fld)
                        if m:
                            kfs[int(m.group(1))]["desc"] = new_v
                    else:
                        shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs
