"""N. char_layout 同步约束（v6.42 迁移自 _hard_fix_char_layout_sync）.

扫 shot 文本（prompt/ff_desc/lf_desc/motion_desc）中"画左/画右"位置词，若
char_layout 同一角色 position 与文本矛盾 → 改 char_layout.position。

幂等：char_layout position 已匹配则跳过。
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
from shared.frame_spec import derive_char_layout


class CharLayoutSyncConstraint(Constraint):
    """N. char_layout 同步：text 写'画X'但 char_layout position 矛盾 → 改 position.

    severity=WARN: position 矛盾可由后续 repair round 修.
    无 repair_marker（按 position 实际值检测）.
    """

    name = "char_layout_sync"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            layout = shot.get("char_layout")
            if not isinstance(layout, list) or not layout:
                derived = derive_char_layout(shot)
                if derived:
                    shot["char_layout"] = derived
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=None if is_hook else shot.get("index", idx),
                            action="backfill",
                            target="char_layout",
                            before="[]",
                            after=f"{len(derived)} 个角色",
                        )
                    )
                continue
            # The first-frame spec is the authoritative source for the
            # initial staging.  Sync char_layout from it before any text-based
            # inference; this prevents two competing initial layouts.
            layouts = shot.get("frame_layouts")
            ff_spec = layouts.get("ff") if isinstance(layouts, dict) else None
            if isinstance(ff_spec, list):
                by_name = {
                    str(item.get("name") or "").strip(): item
                    for item in ff_spec
                    if isinstance(item, dict) and item.get("name")
                }
                for j, item in enumerate(layout):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    source = by_name.get(name)
                    if not source:
                        continue
                    source_pos = str(source.get("position") or "").strip()
                    current_pos = str(item.get("position") or "").strip()
                    if source_pos and source_pos != current_pos:
                        item["position"] = source_pos
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=f"char_layout[{j}].position",
                            before=current_pos,
                            after=source_pos,
                            detail="以首帧 frame spec 统一初始站位",
                        ))
                # Once a first-frame spec exists it is authoritative for
                # initial staging; do not let text proximity re-infer it.
                continue

            text = " ".join([
                shot.get("prompt", ""),
                shot.get("ff_desc", ""),
                shot.get("lf_desc", ""),
                shot.get("motion_desc", ""),
            ])
            if "画左" not in text and "画右" not in text:
                continue
            for j, item in enumerate(layout):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                pos_now = str(item.get("position", "")).strip()
                # v6.43（08-23 审计）：旧正则 `name[^。]*?(画左|画右)` 会把
                # 「刺客侧身朝画右的杨旭」误判成刺客 position=画右——紧跟
                # 朝向词（朝/向/望/盯）之后的画左/画右描述的是视线/面向，
                # 不是站位。改为：枚举名字后同句内的全部（画左|画右），
                # 跳过前面带朝向词的，取第一个真正的站位词。
                dir_in_text = None
                for nm in re.finditer(re.escape(name), text):
                    seg_end = text.find("。", nm.end())
                    seg = text[nm.end(): seg_end if seg_end >= 0 else len(text)]
                    for dm in re.finditer(r"(画左|画右)", seg):
                        prev2 = seg[max(0, dm.start() - 2):dm.start()]
                        if any(c in "朝向望盯" for c in prev2):
                            continue  # 朝向/视线词，非站位词
                        dir_in_text = dm.group(1)
                        break
                    if dir_in_text:
                        break
                if not dir_in_text:
                    continue
                if dir_in_text in pos_now:
                    continue
                new_pos = re.sub(r"画左|画右", dir_in_text, pos_now)
                if new_pos != pos_now:
                    item["position"] = new_pos
                else:
                    item["position"] = dir_in_text
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="rewrite",
                        target=f"char_layout[{j}].position",
                        before=pos_now,
                        after=item["position"],
                    )
                )
        return repairs
