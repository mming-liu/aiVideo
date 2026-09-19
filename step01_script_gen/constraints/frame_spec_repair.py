"""Fill missing frame-layout fields without provider-specific behavior."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity
from shared.frame_spec import strip_polluted_char_anchor_block


class FrameSpecRepairConstraint(Constraint):
    """Complete pose, on-screen flags, and keyframe layout length."""

    name = "frame_spec_repair"
    severity = Severity.WARN
    fields = ("frame_layouts", "keyframes", "main_subject")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # v6.62（09-06）历史坏块自救：09-05/06 存盘 kf desc 的【角色锚定】
            # 块被 _FALLBACK_SURFACE="未指定" 占位符污染（root-fix 见
            # shared/vimax_bridge.py surface 空值守卫 + validation.py post_fix
            # 重生成）。存盘旧数据走新框架回放时在此剥坏块——下次锚定注入
            # （step02a / vimax_bridge 组装）会用干净 surface 重建。
            keyframes = shot.get("keyframes") or []
            for _kf in keyframes:
                if not isinstance(_kf, dict):
                    continue
                # v6.62（09-06）历史坏块自救：09-05/06 存盘 kf desc 的【角色锚定】
                # 块被 _FALLBACK_SURFACE="未指定" 占位符污染（root-fix 见
                # shared/vimax_bridge.py surface 空值守卫 + validation.py post_fix
                # 重生成）。存盘旧数据走新框架回放时在此剥坏块——下次锚定注入
                # （step02a / vimax_bridge 组装）会用干净 surface 重建。
                # 剥离逻辑单源 shared.frame_spec（与 validation.py post_fix 共用）。
                _kd = _kf.get("desc") or ""
                _kd_clean = strip_polluted_char_anchor_block(_kd)
                if _kd_clean != _kd:
                    _kf["desc"] = _kd_clean
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index", idx),
                            action="rewrite",
                            target="keyframes[*].desc",
                            before=_kd[:50],
                            after=_kd_clean[:50],
                        )
                    )
            layouts = shot.get("frame_layouts")
            if not isinstance(layouts, dict):
                continue
            for frame_key in ("ff", "lf"):
                frames = layouts.get(frame_key)
                if not isinstance(frames, list):
                    continue
                for frame in frames:
                    if not isinstance(frame, dict):
                        continue
                    if not frame.get("pose"):
                        # main_subject is not a pose.  Copying it into pose
                        # produced entries such as “杨旭” and then the anchor
                        # layer rendered that as a state fact.  Missing pose
                        # remains unresolved until a current-frame source or
                        # audit supplies evidence.
                        repairs.append(
                            RepairRecord(
                                constraint_name=self.name,
                                shot_index=shot.get("index", idx),
                                action="warn",
                                target=f"frame_layouts.{frame_key}[*].pose",
                                detail="帧姿态缺失；保留空值交由 audit，禁止用 main_subject 冒充姿态",
                            )
                        )
                    if "on_screen" not in frame:
                        # Visibility is semantic state.  Defaulting every
                        # missing entry to True can resurrect departed
                        # characters, so report it instead of guessing.
                        repairs.append(
                            RepairRecord(
                                constraint_name=self.name,
                                shot_index=shot.get("index", idx),
                                action="warn",
                                target=f"frame_layouts.{frame_key}[*].on_screen",
                                detail="帧在场状态缺失；保留未填交由 audit，禁止默认 True",
                            )
                        )

            keyframes = shot.get("keyframes") or []
            layouts_kfs = layouts.get("kfs")
            if not isinstance(layouts_kfs, list):
                layouts_kfs = []
            first_layout = layouts.get("ff")
            # v6.62（09-06）：补帧按「相邻 kf spec 插值」而非一律复制 ff——
            # 实测 shot9 跳水上船 4 个 kf 共享同一静态锚（张十三画左岸边提灯欲跃），
            # 缺的那帧 spec 回退到全镜静态 char_layout，锚定块凝固不动。
            # LLM 尾部漏写 kfs 时，缺的多是最后 1-N 帧 → 用已有的最后一个 kf spec
            # 兜底（动作时序上比 ff 更接近缺帧）；kfs 全空才回退 ff。
            last_valid = next(
                (k for k in reversed(layouts_kfs) if isinstance(k, list) and k),
                None,
            )
            fill_src = last_valid if last_valid is not None else (
                first_layout if isinstance(first_layout, list) else []
            )
            while len(layouts_kfs) < len(keyframes):
                layouts_kfs.append(copy.deepcopy(fill_src))
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index", idx),
                        action="set_field",
                        target="frame_layouts.kfs",
                        after=f"len={len(layouts_kfs)}",
                    )
                )
            changed = False
            while len(layouts_kfs) > len(keyframes):
                layouts_kfs.pop()
                changed = True
            if changed:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index", idx),
                        action="set_field",
                        target="frame_layouts.kfs",
                        after=f"len={len(layouts_kfs)}",
                    )
                )
            layouts["kfs"] = layouts_kfs
        return repairs
