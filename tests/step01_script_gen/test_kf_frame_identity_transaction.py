# -*- coding: utf-8 -*-
"""帧身份对齐的**事务入口**验收（S03 补正，2026-11-15，零模型 / 零网络）.

与 `test_kf_frame_identity.py`（单元入口）互补：本文件跑**真实**
`_repair_shot_warnings`，只打桩 LLM 响应、确定性检查器、日志与后处理 writer。
装配、授权合并、布局守卫、效果账本四节点、按目标回退、事务裁决全部是真实代码。

覆盖：
  ① 合法中间补帧获得实际接受（返回稿 / 裁决 / 映射 / 四节点证据）
  ② 真实原帧改写 → 单目标整轮否决、基线逐字保留
  ③ 一个目标在替换入口被拒、其他合法目标保留且映射与提交状态一致
  ④ 按目标回退：回退目标恢复恒等映射，其余目标保留各自映射
  ⑤ 合法布局增删经过账本四节点，`frame_locator` 落在位移后的帧
  ⑥ 布局违例只回退肇事目标，同轮合法目标与装配映射保留
  ⑦ 纯补帧零效果 + writer 注入帧结构变化 → 来源映射失效 → 整轮否决
  ⑧ 观测回调异常进入裁决，而非只留一条 warn

夹具为两份匿名样本；历史 run h 由 `tools/replay_run_h_frame_identity.py` 专项回放。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.quality_issues import Issue          # noqa: E402


# ---------------------------------------------------------------------------
# 匿名样本：镜 5（2 帧 / 2 实体）与镜 9（3 帧 / 3 实体）
# ---------------------------------------------------------------------------
def _entry(name, pose, gaze="画上"):
    return {"name": name, "pose": pose, "gaze_target": gaze, "on_screen": True}


def _shot(index, n_frames, names):
    kfs, layouts = [], []
    for i in range(n_frames):
        kfs.append({"t_sec": 1.0 + 2.0 * i,
                    "desc": "第%d段动作" % (i + 1),
                    "stage": "pre" if i == 0 else "approach"})
        layouts.append([_entry(n, "姿态%d" % i) for n in names])
    return {
        "index": index, "duration": 8.0, "duration_sec": 8.0,
        "narration_segment": "旁白原文", "subtitle": "字幕原文",
        "scene": "场景", "location": "地点", "main_subject": names[0],
        "prompt": "中景", "variation_type": "small",
        "ff_desc": "起始画面", "lf_desc": "结束画面",
        "motion_desc": "0-3秒：第一段；3-6秒：第二段；6-8秒：第三段",
        "keyframes": kfs,
        "frame_layouts": {"ff": [], "lf": [], "kfs": layouts},
        "temporal_plan": {"events": [
            {"start_sec": 0.0, "end_sec": 3.0, "description": "第一段", "source_quote": ""},
            {"start_sec": 3.0, "end_sec": 8.0, "description": "第二段", "source_quote": ""},
        ]},
    }


_BASE = {"hook": None, "fps": 24, "total_duration": 16,
         "shots": [_shot(5, 2, ["甲", "乙"]), _shot(9, 3, ["丙", "丁", "戊"])]}


def _issue(shot_id, code="kf_segment_missing", extra=None):
    """一条 blocking 审计问题：结构码授权整表补帧 + 精确帧点名触发布局守卫."""
    targets = ["keyframes", "frame_layouts.kfs", "frame_layouts.kfs[1]"]
    return Issue(code=code, severity="visual_risk", message="第 2 段缺关键帧",
                 shot_id=str(shot_id), frame_id="kf1", entity_id="",
                 field_path="keyframes", evidence="第 2 段动作无归属帧",
                 repair_targets=[{"shot_id": str(shot_id), "field_path": p}
                                 for p in targets] + list(extra or []))


def _insert_frame(shot_id, t=2.0, names=("甲", "乙"), *, pose="抬手"):
    return {"shot_id": str(shot_id), "keyframe_additions": [{
        "frame": {"t_sec": t, "desc": "补入的第2段动作", "stage": "approach"},
        "layout": [_entry(n, pose) for n in names],
    }]}


def _warnings_fn(*, frame_threshold=None, new_block_on=None):
    """检查器桩：按稿子内容判定，非常量.

    `frame_threshold = {镜号: 达标帧数}`；未达标即报一条确定性阻塞。
    补帧达标后该阻塞消失（提供 resolved）。`new_block_on` 用于注入新阻塞。
    """
    threshold = frame_threshold or {"5": 3, "9": 4}

    def _fn(script, _novel):
        by_id = {str(s.get("index")): s for s in script.get("shots") or []}
        issues, shots = [], {}
        for sid, need in threshold.items():
            s = by_id.get(sid) or {}
            if len(s.get("keyframes") or []) < need:
                issues.append({"code": "kf_segment_missing", "severity": "visual_risk",
                               "shot_id": sid, "frame_id": "kf1", "entity_id": "",
                               "field_path": "keyframes",
                               "message": "镜头%s 缺一段关键帧" % sid})
                shots["shot_%s" % sid] = ["[kf_segment_missing] 缺一段关键帧"]
        if new_block_on:
            s = by_id.get(str(new_block_on)) or {}
            if "触发新阻塞" in str(s.get("prompt") or ""):
                issues.append({"code": "repair_injected_block", "severity": "visual_risk",
                               "shot_id": str(new_block_on), "frame_id": "",
                               "entity_id": "", "field_path": "prompt",
                               "message": "注入的新阻塞"})
        return {"_has_hard": bool(issues), "shots": shots, "issues": issues}
    return _fn


class _TxHarness:
    """跑一次真实 `_repair_shot_warnings`（LLM / 检查器 / writer / 日志打桩）."""

    def __init__(self, monkeypatch, *, answers, issues, warnings_fn,
                 script=None, writer_mutator=None, writer_error=False,
                 tmp_path=None):
        import step01_script_gen.main as m
        import step01_script_gen.validation as validation
        import step01_script_gen.constraints as constraints

        self.m = m
        self.logs: list = []
        self.diag: list = []
        self.blocks: list = []
        self.calls = {"n": 0}
        self.writer_nodes: list = []
        _logs, _calls, _nodes = self.logs, self.calls, self.writer_nodes

        payloads = [json.dumps(a, ensure_ascii=False) if not isinstance(a, str) else a
                    for a in answers]

        class _Svc:
            def __init__(self):
                self.i = 0

            def generate(self, req):
                _calls["n"] += 1
                text = payloads[min(self.i, len(payloads) - 1)]
                self.i += 1

                class _R:
                    success = True
                    error = ""
                    truncated = False
                    usage = {"output_tokens": 900, "total_tokens": 3000}
                _R.text = text
                return _R()

        class _Log:
            def info(self, x, *a, **k): _logs.append(str(x))
            def warn(self, x, *a, **k): _logs.append("WARN " + str(x))
            def error(self, x, *a, **k): _logs.append("ERR " + str(x))
            def verbose(self, x, *a, **k): pass
            def console(self, x, *a, **k): _logs.append(str(x))

        monkeypatch.setattr(m, "_logger", _Log())
        monkeypatch.setattr(m, "_console", lambda x, *a, **k: _logs.append(str(x)))
        monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
        monkeypatch.setattr(m, "resolve_model_prompt_profile",
                            lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
        monkeypatch.setattr(m, "_collect_repair_warnings", warnings_fn)
        monkeypatch.setattr(m, "collect_structured_issues",
                            lambda *a, **k: ([], False, "test"))
        for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
            monkeypatch.setattr(m, _fn, lambda *a, **k: None)
        if tmp_path is not None:
            monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
            monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
            monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")

        # 后处理 writer：默认保持结构（identity），便于聚焦身份/守卫/裁决链；
        # `writer_mutator` 用于注入帧结构变化故障，`writer_error` 用于注入观测异常。
        #
        # ⚠️ 观测异常必须经 monkeypatch 挂载并在用例结束**自动撤销**：早先在这里
        # 直接赋值 `RepairEffectLedger._freeze = ...`，会让同一 pytest 会话里后续
        # 所有测试文件的账本都带上 error，进而把它们的真实事务全部误判为否决。
        if writer_error:
            from step01_script_gen.repair_effects import RepairEffectLedger
            _original_freeze = RepairEffectLedger._freeze

            def _failing(self, kind, writer, script, revision):
                node = _original_freeze(self, kind, writer, script, revision)
                if node.get("node") == "writer":
                    node["error"] = "injected_observation_fault"
                return node
            monkeypatch.setattr(RepairEffectLedger, "_freeze", _failing)

        def _identity_writer(s, **k):
            return s

        def _hard_fix(s, **k):
            if writer_mutator is not None:
                writer_mutator(s)
            return s

        monkeypatch.setattr(m.style_prefix, "inject_global_config_into_script",
                            _identity_writer)
        monkeypatch.setattr(m, "write_timeline_from_durations", _identity_writer)
        monkeypatch.setattr(validation, "post_fix_shot_prompts", _identity_writer)
        monkeypatch.setattr(constraints, "run_hard_fix", _hard_fix)

        self.script = copy.deepcopy(script or _BASE)
        self.issues = list(issues)
        self.monkeypatch = monkeypatch

    def run(self):
        self.out = self.m._repair_shot_warnings(
            copy.deepcopy(self.script), "novel", audit_issues=self.issues,
            max_rounds=1, repair_diag=self.diag, block_reasons=self.blocks)
        return self

    @property
    def tx(self) -> dict:
        recs = [r for r in self.diag if "groups" in r]
        assert recs, "没有产生修复事务记录: %s" % [r.get("kind") for r in self.diag]
        return recs[-1]

    def shots(self) -> dict:
        return {str(s.get("index")): s for s in self.out.get("shots") or []}


def _answer(*items):
    return {"shot_replacements": list(items), "hook_replacement": None}


# ---------------------------------------------------------------------------
# ① 合法中间补帧获得实际接受
# ---------------------------------------------------------------------------
def test_legal_middle_insert_is_accepted_by_transaction(monkeypatch, tmp_path):
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(_insert_frame(5), _insert_frame(9, t=2.0,
                                                         names=("丙", "丁", "戊")))],
        issues=[_issue(5), _issue(9)],
        warnings_fn=_warnings_fn()).run()

    assert h.calls["n"] == 1
    rec = h.tx
    assert rec["accepted"] is True, (rec.get("reason"), rec.get("layout_structural_violations"))
    assert rec["resolved"] >= 1
    assert len(h.shots()["5"]["keyframes"]) == 3
    assert len(h.shots()["5"]["frame_layouts"]["kfs"]) == 3
    # 映射由装配操作生成，绑定本轮基线
    m5 = rec["frame_mappings"]["shot_5"]
    assert m5["source"] == "assembly" and m5["old_to_new"] == [0, 2]
    assert m5["added_indices"] == [1] and m5["source_revision"]
    assert rec["frame_locator_integrity"] == []
    # 四节点齐备
    kinds = {n["node"] for n in rec["effect_ledger_nodes"]}
    assert {"candidate", "merged", "writer", "reverify"} <= kinds, kinds


# ---------------------------------------------------------------------------
# ② 真实原帧改写被拒，基线保留
# ---------------------------------------------------------------------------
def test_true_original_frame_rewrite_is_rejected(monkeypatch, tmp_path):
    """插入中间帧 + 改写原 kfs[1] 条目 pose ⇒ 守卫拒收 → **整轮**否决、基线保留.

    刻意用单目标：`_offend == picked` 时不适用按目标回退，事务必须整轮否决。
    多目标下「只回退肇事目标、合法目标保留」是另一条既有语义，由下一个用例覆盖。
    """
    item = _insert_frame(5)
    item["updates"] = [{"field_path": "frame_layouts.kfs.1",
                        "value": [_entry("乙", "被偷偷改写"),
                                  _entry("甲", "姿态1")]}]
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(item)],
        issues=[_issue(5)],
        warnings_fn=_warnings_fn(frame_threshold={"5": 3})).run()

    rec = h.tx
    assert rec["accepted"] is False, rec
    assert rec.get("origin_reason") == "layout_structure_invalid", rec
    assert any("字段被改写" in str(v)
               for v in (rec.get("layout_structural_violations") or [])), rec
    # 基线逐字保留（补帧不得留在稿上，保护字段逐字不变）
    assert len(h.shots()["5"]["keyframes"]) == 2, "被拒轮的补帧不得留在稿上"
    assert h.shots()["5"]["frame_layouts"]["kfs"] == _BASE["shots"][0]["frame_layouts"]["kfs"]
    assert h.shots()["5"]["subtitle"] == "字幕原文"
    assert h.shots()["5"]["narration_segment"] == "旁白原文"


def test_layout_violation_rolls_back_only_offending_target(monkeypatch, tmp_path):
    """多目标：布局违例只回退肇事目标，合法补帧目标及其来源映射保留."""
    item = _insert_frame(5)
    item["updates"] = [{"field_path": "frame_layouts.kfs.1",
                        "value": [_entry("乙", "被偷偷改写"),
                                  _entry("甲", "姿态1")]}]
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(item, _insert_frame(9, names=("丙", "丁", "戊")))],
        issues=[_issue(5), _issue(9)],
        warnings_fn=_warnings_fn()).run()

    rec = h.tx
    assert rec.get("origin_reason") == "layout_structure_invalid", rec
    assert "shot_5" in (rec.get("targets_rolled_back") or []), rec
    assert rec["accepted"] is True, "同轮合法目标不应被连带否决"
    # 回退目标逐字回到基线
    assert len(h.shots()["5"]["keyframes"]) == 2
    assert h.shots()["5"]["frame_layouts"]["kfs"] == _BASE["shots"][0]["frame_layouts"]["kfs"]
    # 同轮合法目标的补帧保留（3 → 4 帧）
    assert len(h.shots()["9"]["keyframes"]) == 4
    # 台账写的是**最终保留稿**的映射：回退目标恒等，合法目标仍是装配来源
    m5 = rec["frame_mappings"]["shot_5"]
    assert m5["source"] == "identity" and m5["old_to_new"] == [0, 1], m5
    assert rec["frame_mappings"]["shot_9"]["source"] == "assembly", rec["frame_mappings"]


# ---------------------------------------------------------------------------
# ③ 一个目标在替换入口被拒、其他合法目标保留
# ---------------------------------------------------------------------------
def test_rejected_target_has_no_mapping_while_others_keep_theirs(monkeypatch, tmp_path):
    bad = {"shot_id": "9", "keyframe_additions": [{"frame": {"t_sec": 2.0}}]}
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(_insert_frame(5), bad)],
        issues=[_issue(5), _issue(9)],
        warnings_fn=_warnings_fn(frame_threshold={"5": 3, "9": 3})).run()

    rec = h.tx
    assert any("9" in r for r in rec.get("rejected") or []), rec.get("rejected")
    assert "shot_5" in rec["frame_mappings"], "合法目标必须保留自己的映射"
    assert "shot_9" not in rec["frame_mappings"], "被拒目标不得留下映射"
    assert rec["frame_mappings"]["shot_5"]["old_to_new"] == [0, 2]
    assert len(h.shots()["5"]["keyframes"]) == 3
    assert len(h.shots()["9"]["keyframes"]) == 3, "被拒目标保持基线帧数"


# ---------------------------------------------------------------------------
# ④ 按目标回退：回退目标恢复恒等映射，其余目标保留
# ---------------------------------------------------------------------------
def test_target_rollback_restores_identity_and_drops_effects(monkeypatch, tmp_path):
    good5 = _insert_frame(5)
    bad9 = _insert_frame(9, names=("丙", "丁", "戊"))
    bad9["updates"] = [{"field_path": "prompt", "value": "触发新阻塞"}]
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(good5, bad9)],
        issues=[_issue(5), _issue(9, extra=[{"shot_id": "9", "field_path": "prompt"}])],
        warnings_fn=_warnings_fn(new_block_on=9)).run()

    rec = h.tx
    assert rec.get("new_blocking") or rec.get("new_blocking_keys"), \
        "检查器桩应注入新阻塞以触发按目标回退"
    assert rec.get("targets_rolled_back"), rec
    # 回退目标恢复基线，合法目标保留补帧
    assert len(h.shots()["9"]["keyframes"]) == 3
    assert "触发新阻塞" not in str(h.shots()["9"].get("prompt") or "")
    assert len(h.shots()["5"]["keyframes"]) == 3, "同轮合法目标不应被连带回退"
    assert rec.get("effects_rolled_back", 0) >= 0


# ---------------------------------------------------------------------------
# ⑤ 合法布局增删经过账本四节点，定位符落在位移后帧
# ---------------------------------------------------------------------------
def test_layout_addition_binds_to_shifted_frame_through_four_nodes(monkeypatch, tmp_path):
    item = _insert_frame(5)
    base_kfs1 = copy.deepcopy(_BASE["shots"][0]["frame_layouts"]["kfs"][1])
    item["updates"] = [{"field_path": "frame_layouts.kfs.1",
                        "value": base_kfs1 + [_entry("己", "新增入镜")]}]
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(item, _insert_frame(9, names=("丙", "丁", "戊")))],
        issues=[_issue(5), _issue(9)],
        warnings_fn=_warnings_fn()).run()

    rec = h.tx
    assert rec["accepted"] is True, (rec.get("reason"), rec.get("frame_locator_integrity"))
    nodes = rec["effect_ledger_nodes"]
    kinds = [n["node"] for n in nodes]
    assert "candidate" in kinds and "merged" in kinds and "writer" in kinds \
        and "reverify" in kinds, kinds
    # 帧结构快照在每个节点一致（writer 未改变帧结构）
    assert rec["writer_structure_changes"] == [], rec["writer_structure_changes"]
    assert rec["frame_locator_integrity"] == []
    assert len(h.shots()["5"]["frame_layouts"]["kfs"][2]) == 3, \
        "新增条目应落在位移后的 kfs[2]"


# ---------------------------------------------------------------------------
# ⑥ writer 注入帧结构变化 → 定位符失效 → 保留基线
# ---------------------------------------------------------------------------
def _inject_extra_frame(script):
    for s in script.get("shots") or []:
        if str(s.get("index")) != "5":
            continue
        s.setdefault("keyframes", []).append(
            {"t_sec": 7.0, "desc": "writer 擅自插入", "stage": "exit"})
        s.setdefault("frame_layouts", {}).setdefault("kfs", []).append(
            [_entry("庚", "越权插入")])


def test_writer_structure_change_invalidates_locator_and_keeps_baseline(
        monkeypatch, tmp_path):
    """纯补帧（零条目增删效果）+ writer 擅自插帧 ⇒ 来源映射失效、整轮否决.

    这条反例正是「账本只按 effects 取观测目标」会漏掉的场景：本轮没有任何
    已登记布局效果，但装配来源映射仍声明 after_count=3；writer 把它改成 4
    必须被四节点观测到并进入裁决。
    """
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(_insert_frame(5))],
        issues=[_issue(5)],
        warnings_fn=_warnings_fn(frame_threshold={"5": 3}),
        writer_mutator=_inject_extra_frame).run()

    rec = h.tx
    assert rec["frame_locator_integrity"], "结构变化必须产出定位符失效诊断"
    assert any("帧结构" in x or "after_count" in x
               for x in rec["frame_locator_integrity"]), rec["frame_locator_integrity"]
    assert rec["writer_structure_changes"], "账本须定位到责任 writer"
    assert any(c["target"] == "shot_5" for c in rec["writer_structure_changes"]), \
        rec["writer_structure_changes"]
    assert rec["accepted"] is False, rec
    assert len(h.shots()["5"]["keyframes"]) == 2, "失效轮必须回到基线"


# ---------------------------------------------------------------------------
# ⑦ 观测回调异常进入裁决
# ---------------------------------------------------------------------------
def test_observation_error_enters_adjudication(monkeypatch, tmp_path):
    h = _TxHarness(
        monkeypatch, tmp_path=tmp_path,
        answers=[_answer(_insert_frame(5), _insert_frame(9, names=("丙", "丁", "戊")))],
        issues=[_issue(5), _issue(9)],
        warnings_fn=_warnings_fn(), writer_error=True).run()

    rec = h.tx
    assert rec["writer_observation_errors"], "观测异常必须进台账"
    assert any("观测回调失败" in x for x in rec["frame_locator_integrity"]), \
        rec["frame_locator_integrity"]
    assert rec["accepted"] is False, "观测失败不得继续消费旧定位符"
