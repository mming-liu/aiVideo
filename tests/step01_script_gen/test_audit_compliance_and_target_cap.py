# -*- coding: utf-8 -*-
"""2026-09-27 十次连跑根因 P1/P2：审计合规说明 + 超限成对目标按镜拆条.

## P1（合规替换被审计当错词）

合规层把 `杀人→出人命`（`shared/content_safety.NARRATION_SUBSTITUTIONS`），
确定性侧 `validation._check_grounding` 已做**双向归一化**，而审计提示
（`audit.py::_AUDIT_RULES_TEMPLATE`）此前**一句都没提**存在合规替换 →
审计必报 `DIALOGUE_WRONG_WORDS` 要求改回「杀人啦」→ 修复照办 →
`narration_segment` 是锁定字段 → `locked_changed` 整轮回退。
实测 10 次跑里 6 次触发（4 次直接成为停止原因，赔掉 35 条已完成的修复）。

## P2（单条审计声明覆盖 7-14 镜 → 构造上不可修）

修复侧联动组上限 6 且**拒绝拆组**（拆组=半条修复），而审计把 systemic 缺陷写成
**一条** issue + N 个成对目标（N = 7/8/10/12/14，取证见报告 P2）→
`_linked_repair_groups` 判 `oversize` → 不调 LLM、不再发审计，直接 BLOCK。

本文件锁住：
  1. 审计提示**写明**合规替换不算缺陷，且指向权威词表（同源守卫）；
  2. 审计提示**写明**单条 repair_targets ≤ 6 镜、systemic 按镜拆条；
  3. `audit_prompt_version()` 覆盖规则模板（改规则 → 旧审计自动失效）；
  4. `_split_oversize_repair_targets` **按镜**分块、**镜内目标保持整组**；
  5. 拆条后的 Issue 进 `_linked_repair_groups` **不再产生 oversize**；
  6. 上限与 `_REPAIR_MAX_SHOTS_PER_GROUP` 同值（两侧不许各自漂移）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# --------------------------------------------------------------- P1
class TestAuditKnowsComplianceSubstitutions:
    def test_rules_template_documents_compliance_substitution(self):
        from step01_script_gen.audit import _AUDIT_RULES_TEMPLATE

        assert "合规替换" in _AUDIT_RULES_TEMPLATE, (
            "审计提示没写合规替换 → 审计会把 `出人命` 判成改词 → 修复照办 → "
            "locked_changed 整轮回退（10 次跑里 4 次死于此）")
        assert "出人命" in _AUDIT_RULES_TEMPLATE
        assert "shared/content_safety" in _AUDIT_RULES_TEMPLATE, (
            "必须指向权威词表，否则文案与词表会各自漂移")

    def test_substitution_table_still_maps_the_documented_pair(self):
        from shared.content_safety import NARRATION_SUBSTITUTIONS

        assert NARRATION_SUBSTITUTIONS.get("杀人") == "出人命", (
            "提示里写的那对映射必须仍在词表里（否则提示在教一件不存在的事）")

    def test_prompt_version_covers_the_rules_template(self, monkeypatch):
        """改规则模板 → 版本必须变（否则旧审计结论会被复用给新稿盖章）."""
        import step01_script_gen.audit as _a

        before = _a.audit_prompt_version()
        monkeypatch.setattr(_a, "_AUDIT_RULES_TEMPLATE",
                            _a._AUDIT_RULES_TEMPLATE + "\n# 探针改动")
        assert _a.audit_prompt_version() != before


# --------------------------------------------------------------- P2
class TestAuditContractStatesTheCap:
    def test_rules_template_states_the_target_cap(self):
        from step01_script_gen.audit import _AUDIT_RULES_TEMPLATE

        assert "最多覆盖 6 个镜头" in _AUDIT_RULES_TEMPLATE, (
            "契约没写单条 repair_targets 的镜头数上限 → 模型会继续写 7-14 镜的声明")

    def test_cap_matches_the_repair_side_group_cap(self):
        from step01_script_gen.main import (_AUDIT_MAX_TARGET_SHOTS,
                                            _REPAIR_MAX_SHOTS_PER_GROUP)

        assert _AUDIT_MAX_TARGET_SHOTS == _REPAIR_MAX_SHOTS_PER_GROUP, (
            "声明侧上限与修复侧联动组上限必须同值——不同值时声明可以合法超过"
            "修复能力，结果又是一次「构造上不可修」的 BLOCK")


class TestOversizeTargetsAreSplitByShot:
    def _rt(self, shots):
        """每个镜头两条目标（desc + 布局）——用来验证**镜内目标不被拆散**."""
        out = []
        for s in shots:
            out.append({"shot_id": str(s), "field_path": "keyframes[0].desc"})
            out.append({"shot_id": str(s), "field_path": "frame_layouts.kfs[0][0].pose"})
        return out

    def test_small_declaration_is_untouched(self):
        from step01_script_gen.main import _split_oversize_repair_targets

        rt = self._rt([1, 2, 3])
        assert _split_oversize_repair_targets(rt) == [rt]

    def test_none_and_empty_keep_their_three_state_semantics(self):
        from step01_script_gen.main import _split_oversize_repair_targets

        assert _split_oversize_repair_targets(None) == [None]
        assert _split_oversize_repair_targets([]) == [[]]

    def test_oversize_declaration_splits_into_capped_chunks(self):
        from step01_script_gen.main import _split_oversize_repair_targets

        chunks = _split_oversize_repair_targets(self._rt(range(1, 15)))
        assert len(chunks) == 3, [len(c) for c in chunks]
        for c in chunks:
            shots = {t["shot_id"] for t in c}
            assert len(shots) <= 6, shots

    def test_every_shot_keeps_all_of_its_targets_in_one_chunk(self):
        """镜内原子性：同一镜的 desc+布局必须落在**同一块**（不得拆成半条修复）."""
        from step01_script_gen.main import _split_oversize_repair_targets

        chunks = _split_oversize_repair_targets(self._rt(range(1, 15)))
        seen: dict = {}
        for ci, c in enumerate(chunks):
            for t in c:
                seen.setdefault(t["shot_id"], set()).add(ci)
        assert all(len(v) == 1 for v in seen.values()), (
            "同一镜的目标被拆到了多块 → 半条修复", seen)
        assert len(seen) == 14, "拆条不得丢镜头"

    def test_no_target_is_lost_or_duplicated(self):
        from step01_script_gen.main import _split_oversize_repair_targets

        rt = self._rt(range(1, 15))
        flat = [t for c in _split_oversize_repair_targets(rt) for t in c]
        assert sorted((t["shot_id"], t["field_path"]) for t in flat) == \
            sorted((t["shot_id"], t["field_path"]) for t in rt)


class TestSplitReachesTheRepairGroupBuilder:
    def _payload(self, shots):
        return {"issues": [{
            "code": "INVALID_STAGE_VALUE", "severity": "blocking",
            "shot_id": str(shots[0]), "frame_id": "kf0", "entity_id": "",
            "field_path": "keyframes[0].stage",
            "evidence": "stage 取值非法", "impact": "无法出图",
            "minimal_fix": "改为 enum 内取值",
            "repair_targets": [{"shot_id": str(s), "field_path": "keyframes[0].stage"}
                               for s in shots],
        }]}

    def test_oversize_single_declaration_no_longer_blocks_the_group_builder(self):
        from step01_script_gen.main import (_audit_payload_to_issues,
                                            _linked_repair_groups)

        shots = list(range(1, 15))
        issues = _audit_payload_to_issues(self._payload(shots))
        assert len(issues) == 3, [len(i.repair_targets or []) for i in issues]
        shots_by_id = {f"shot_{s}": {"index": s, "keyframes": [{"desc": "d", "stage": "pre"}],
                                     "frame_layouts": {"ff": [], "lf": [], "kfs": [[]]}}
                       for s in shots}
        picked = {f"shot_{s}": [] for s in shots}
        groups, _rejects, oversize = _linked_repair_groups(
            issues, shots_by_id, picked, cap=6)
        assert oversize == [], (
            "拆条后仍出现超限组 → 修复侧还是会整轮停在门口：%s" % oversize)
        assert groups, "拆条后应至少产生一个可修组"

    def test_small_declaration_still_produces_one_group(self):
        from step01_script_gen.main import (_audit_payload_to_issues,
                                            _linked_repair_groups)

        shots = [1, 2, 3]
        issues = _audit_payload_to_issues(self._payload(shots))
        assert len(issues) == 1, "未超限的声明不得被拆"
        shots_by_id = {f"shot_{s}": {"index": s, "keyframes": [{"desc": "d", "stage": "pre"}],
                                     "frame_layouts": {"ff": [], "lf": [], "kfs": [[]]}}
                       for s in shots}
        groups, _r, oversize = _linked_repair_groups(
            issues, shots_by_id, {f"shot_{s}": [] for s in shots}, cap=6)
        assert oversize == [] and len(groups) == 1, (groups, oversize)
        assert groups[0] == frozenset({"shot_1", "shot_2", "shot_3"}), groups
