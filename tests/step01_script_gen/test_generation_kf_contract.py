# -*- coding: utf-8 -*-
"""2026-09-27 用户裁决 1：把「每段一帧」写回**实际生成输入**，并与判据同源.

## 为什么需要这个文件

该规则（CLAUDE.md 记作「v6.33 源头规则 16.4」）在 `0dda62b`(08-23) 拆分
`prompts.py` 与 `ec727d0`(08-29) 换掉规则优先级表时**从生成面消失**了：
`system.md` 的硬约束 10 条无一条提帧数（K 只给 t_sec 范围，例子还写着「（4 帧）」；
K3 反过来允许短镜零帧），`main.md` 强制每镜写三段式 motion 却不说帧数，
生成执行契约块与 schema 也没有。结果：下游判据要求「帧数 ≥ 段数」，
上游一个字都没说 → 连续 4 次运行每镜都只写 2 帧、8-15 镜缺段。

## 本文件锁住

1. **三份生成面**（main.md / system.md / doubao_system.md）都写有这条规则
   ——按**模型**选剖面，漏一个就有一半路径失守；
2. 不再教**固定帧数**（旧文案「t_sec = 1.0/2.0/3.0/4.0（4 帧）」与「每段一帧」
   互相矛盾：3 段镜会被教成 4 帧、5 段镜会被教成 4 帧）；
3. K3 的「短镜可零帧」例外**带条件**（只限未显式分时段的单动作镜）；
4. 规则与判据**同源**：按提示语义构造的稿，`kf_coverage.analyze` 的结论一致
   （分了 N 段正文非空 → 要 N 帧；未分时段 → 不要求帧）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

PROMPTS = _PROJECT_ROOT / "step01_script_gen" / "prompts"
#: 三份生成面共用的判别短语——改文案时它必须一起改，否则本测试就失败
RULE_PHRASE = "每个正文非空的时段至少有一帧"
SURFACES = ("main.md", "system.md", "doubao_system.md")


class TestRuleIsInEveryGenerationSurface:
    def test_each_surface_states_the_per_segment_rule(self):
        missing = [name for name in SURFACES
                   if RULE_PHRASE not in (PROMPTS / name).read_text(encoding="utf-8")]
        assert missing == [], (
            "生成面缺少「每段一帧」规则（下游判据会要求帧数 ≥ 段数，"
            f"而模型收不到这条要求）：{missing}")

    def test_no_surface_teaches_a_fixed_frame_count(self):
        for name in SURFACES:
            text = (PROMPTS / name).read_text(encoding="utf-8")
            bad = re.findall(r"`?t_sec`?\s*=\s*[^\n]*（\d+\s*帧）", text)
            assert bad == [], (
                f"{name} 仍在教固定帧数（与「每段一帧」矛盾，"
                f"3 段/5 段镜都会被教成同一个数）：{bad}")

    def test_zero_frame_exception_is_scoped_to_unlabeled_motion(self):
        text = (PROMPTS / "system.md").read_text(encoding="utf-8")
        line = next((ln for ln in text.splitlines()
                     if "keyframes=[]`（无 kf 合法）" in ln), "")
        assert line, "K3 的零帧例外不见了——本守卫失去锚点"
        assert "未显式分时段" in line, (
            "零帧例外必须限定「motion_desc 未显式分时段的单动作镜」，"
            f"否则与「每段一帧」直接冲突：{line[:120]}")


class TestRuleMatchesTheChecker:
    """提示语义 → 判据结论必须一致（同源守卫，机器可判的部分）."""

    def _analyze(self, motion: str, kf_times: list, duration: float = 5.0):
        from step01_script_gen.kf_coverage import analyze

        shot = {"duration_sec": duration, "motion_desc": motion,
                "keyframes": [{"t_sec": t, "desc": "d"} for t in kf_times]}
        return analyze(shot)

    def test_labeled_motion_requires_one_frame_per_nonempty_segment(self):
        cov = self._analyze("前2秒：A动作；2-4秒：B动作；4-5秒：C动作", [1.0, 3.0])
        assert cov.required == [0, 1, 2]
        assert cov.missing == [2] and cov.shortfall is True
        cov_ok = self._analyze("前2秒：A动作；2-4秒：B动作；4-5秒：C动作",
                               [1.0, 3.0, 4.5])
        assert cov_ok.missing == [] and cov_ok.shortfall is False

    def test_unlabeled_single_action_shot_needs_no_frame(self):
        cov = self._analyze("轰然一声，众人回头", [])
        assert cov.required == [] and cov.missing == [] and cov.shortfall is False

    def test_extra_narrative_frames_are_allowed(self):
        cov = self._analyze("前2秒：A动作；2-4秒：B动作", [1.0, 2.0, 3.0, 4.0])
        assert cov.missing == [] and cov.shortfall is False
