"""J3. 运镜缺失补填约束（v6.47，08-27）.

_check_camera_movement 对「prompt/ff/motion 无任何运镜/机位/静态词」的镜头告警
「缺少运镜/机位描述」（08-27 实测 10 镜）。LLM 常漏写运镜语言 → 确定性补填：
  - 命中角色运镜 registry（远景建立→推近 / 打斗→跟拍 / 质问→推近 / 反应→环绕）
    → 补对应运镜 tail（复用 camera_variety._CAMERA_BY_SHOT_ROLE 单一事实源）
  - 未命中任何角色 → 补「固定机位」（_check_camera_movement 认可的合法默认）

幂等：文本已含任一运镜/机位/静态词 → 跳过；补填后词已含 → 再跑 0 修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import (
    _MOVEMENT_TYPE_TERMS, _ANGLE_TECHNIQUES, _STATIC_MARKERS,
)
from .base import Constraint
from .types import RepairRecord, Severity


class CameraMovementFillConstraint(Constraint):
    """J3. 运镜缺失补填：无运镜语言 → 按角色补运镜 / 兜底固定机位.

    severity=WARN: 确定性补填，无需 regen.

    v6.51: 同步填充 shot['camera_movement'] 结构化字段（供 step02b provider 直接消费），
    不再只把运镜词塞到 prompt 末尾。
    """

    name = "camera_movement_fill"
    severity = Severity.WARN
    fields = ("prompt", "camera_movement")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            prompt = str(shot.get("prompt") or "")
            text = " ".join([
                prompt,
                str(shot.get("ff_desc") or ""),
                str(shot.get("motion_desc") or ""),
                str(shot.get("camera_movement") or ""),
            ])
            if self._has_camera_language(text):
                # v6.51: 已有运镜语言 → 从文本中提取填入 camera_movement 字段
                # （不重复 append 到 prompt，但保证结构化字段非空）
                if not shot.get("camera_movement"):
                    extracted = self._extract_camera(text)
                    if extracted:
                        shot["camera_movement"] = extracted
                continue
            tail = self._pick_tail(shot)
            if not tail:
                continue
            # v6.51: 提取干净的运镜词填入 camera_movement 字段
            # tail 格式如 "。缓慢推近" / "。固定机位"，strip 掉前缀标点
            cam_value = tail.lstrip("。 ")
            if cam_value:
                shot["camera_movement"] = cam_value
            before_cam = str(shot.get("camera_movement") or "")
            if cam_value:
                shot["camera_movement"] = cam_value
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="set_field",
                    target="camera_movement",
                    before=before_cam,
                    after=shot.get("camera_movement", ""),
                )
            )
        # --- P1-2: 连续多镜同 camera_movement 告警（全片扫描） ---
        repairs.extend(self._scan_consecutive_camera(shots=ctx.shots))
        return repairs

    @staticmethod
    def _extract_camera(text: str) -> str:
        """从已有文本中提取运镜词（用于填 camera_movement 字段）.
        优先级：推/拉/摇/移/跟/环绕 > 固定/静止/空镜。
        """
        # 长词优先匹配
        _CAMERA_TERMS = [
            "缓慢推近", "缓推近", "慢推近", "慢推", "推近", "推进",
            "快速跟拍", "跟拍", "跟随",
            "缓缓拉远", "慢慢拉远", "拉远", "拉开",
            "环绕镜", "环绕",
            "横摇", "摇镜", "平移",
            "缓慢下移", "下移", "上移",
            "固定机位", "固定镜头", "静止", "固定", "静态",
            "空镜", "远景", "中景", "近景", "特写",
        ]
        for term in _CAMERA_TERMS:
            if term in text:
                return term
        return "固定机位"  # 兜底

    @staticmethod
    def _has_camera_language(text: str) -> bool:
        """与 _check_camera_movement 判定一致：命中运镜/机位/静态/空镜任一类即算有."""
        for terms in _MOVEMENT_TYPE_TERMS.values():
            if any(t in text for t in terms):
                return True
        if any(a in text for a in _ANGLE_TECHNIQUES):
            return True
        if any(m in text for m in _STATIC_MARKERS):
            return True
        return "空镜" in text

    @staticmethod
    def _pick_tail(shot: dict) -> str:
        """按角色运镜 registry 选 tail；未命中 → 固定机位（检查认可的默认）."""
        try:
            from .camera_variety import _CAMERA_BY_SHOT_ROLE
            for label, tail, cond in _CAMERA_BY_SHOT_ROLE:
                try:
                    if cond(shot):
                        return tail
                except Exception:
                    continue
        except Exception:
            pass
        return "。固定机位"

    @staticmethod
    def _scan_consecutive_camera(shots) -> list:
        """P1-2: 连续 >=3 镜同 camera_movement → WARN（不静默改 LLM 输出）.

        返回 RepairRecord 列表。仅 WARN，不实际修改（避免引入新冲突）。
        """
        from .types import RepairRecord
        if not isinstance(shots, list) or len(shots) < 3:
            return []
        out = []
        run_start = 0
        run_cam = ""
        for i, s in enumerate(shots):
            if not isinstance(s, dict):
                continue
            cam = (s.get("camera_movement") or "").strip()
            if cam and cam == run_cam:
                continue
            # 当前 cam 与 run 不同 → 先结算上一段
            run_len = i - run_start
            if run_len >= 3 and run_cam:
                start_idx = shots[run_start].get("index", run_start)
                end_idx = shots[i - 1].get("index", i - 1)
                out.append(
                    RepairRecord(
                        constraint_name="camera_movement_fill",
                        shot_index=start_idx,
                        action="warn",
                        target="camera_movement_run",
                        before=f"shot#{start_idx}..#{end_idx} 全为 '{run_cam}'",
                        after=f"建议其中至少 {run_len - 2} 镜改用其它运镜",
                        detail=f"连续 {run_len} 镜 camera_movement='{run_cam}'，镜头语言单调",
                    )
                )
            run_cam = cam
            run_start = i
        # 末尾段
        run_len = len(shots) - run_start
        if run_len >= 3 and run_cam:
            start_idx = shots[run_start].get("index", run_start)
            end_idx = shots[-1].get("index", len(shots) - 1)
            out.append(
                RepairRecord(
                    constraint_name="camera_movement_fill",
                    shot_index=start_idx,
                    action="warn",
                    target="camera_movement_run",
                    before=f"shot#{start_idx}..#{end_idx} 全为 '{run_cam}'",
                    after=f"建议其中至少 {run_len - 2} 镜改用其它运镜",
                    detail=f"连续 {run_len} 镜 camera_movement='{run_cam}'，镜头语言单调",
                )
            )
        return out
