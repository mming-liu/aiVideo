# -*- coding: utf-8 -*-
"""验收侧「逐目标回退」（v6.105，总计划 §6 停止条件的应用侧延伸）.

实测（第二十六次重跑）：一轮 `resolved=7 new=1`——7 条阻塞被真正解决，只因 shot 8
的补帧连带改动了已有帧、产生 1 条新的窗口错配，整轮（含另外 3 个目标的合法修复）
被一起丢掉。这与 v6.86 之前的「整批全有或全无」是同一缺陷的**验收侧**：v6.86 已把
*应用*改成逐目标提交，验收侧必须同步，否则逐目标提交在这一关又被收回。

安全性不变：被采纳的结果**仍然满足「没有新增 blocking」**（总计划 §6），只是不再
让一个坏目标连坐同轮其它好目标。
"""
from __future__ import annotations

from step01_script_gen.main import (
    _new_blocking_targets, _repair_acceptance_decision, _restore_targets,
)


def _shot(index, ff="原", kfs=2):
    return {"index": index, "ff_desc": ff, "duration_sec": 5.0,
            "keyframes": [{"t_sec": 1.0 * (i + 1)} for i in range(kfs)]}


# --------------------------------------------------------------- 定位
def test_new_blocking_targets_localizes_picked_shots():
    keys = [("kf_event_window_mismatch", "8", "*", "", "keyframes.desc")]
    assert _new_blocking_targets(keys, {"shot_8", "shot_9", "hook"}) == {"shot_8"}


def test_new_blocking_targets_hook_and_shot_ids():
    keys = [("x", "hook", "", "", ""), ("y", "3", "", "", "")]
    assert _new_blocking_targets(keys, {"hook", "shot_3"}) == {"hook", "shot_3"}


def test_new_blocking_targets_refuses_global_or_untargeted():
    """定位不到就别回退：全局问题、以及落在**非目标**镜头上的新阻塞.

    后者不是本轮写入造成的（本轮只写被点名的镜头），回退目标解决不了它。
    """
    assert _new_blocking_targets([("x", "", "", "", "")], {"shot_1"}) == set()
    assert _new_blocking_targets([("x", "5", "", "", "")], {"shot_1"}) == set()
    assert _new_blocking_targets([], {"shot_1"}) == set()


# --------------------------------------------------------------- 回退
def test_restore_targets_reverts_only_named_shots():
    before = {"shots": [_shot(1, "旧1"), _shot(2, "旧2"), _shot(8, "旧8")],
              "hook": {"index": "hook", "ff_desc": "旧hook"}}
    now = {"shots": [_shot(1, "新1"), _shot(2, "新2"), _shot(8, "新8")],
           "hook": {"index": "hook", "ff_desc": "新hook"}}
    out = _restore_targets(now, before, {"shot_8"})
    assert out["shots"][2]["ff_desc"] == "旧8"
    assert out["shots"][0]["ff_desc"] == "新1"      # 未被点名的目标保持修复
    assert out["shots"][1]["ff_desc"] == "新2"
    assert out["hook"]["ff_desc"] == "新hook"
    # 原对象不被就地改写
    assert now["shots"][2]["ff_desc"] == "新8"


def test_restore_targets_handles_hook_and_missing_index():
    before = {"shots": [_shot(1)], "hook": {"index": "hook", "ff_desc": "旧hook"}}
    now = {"shots": [_shot(1), _shot(99, "新99")], "hook": {"index": "hook", "ff_desc": "新hook"}}
    out = _restore_targets(now, before, {"hook", "shot_99"})
    assert out["hook"]["ff_desc"] == "旧hook"
    assert out["shots"][1]["ff_desc"] == "新99"     # 基线里没有 → 保持现状，不凭空造


# --------------------------------------------------------------- 决策口径
def _rec(code, shot_id, frame_id="", field_path=""):
    return {"code": code, "severity": "visual_risk", "shot_id": shot_id,
            "frame_id": frame_id, "entity_id": "", "field_path": field_path}


def test_decision_after_target_rollback_is_clean():
    """回退坏目标后重算的决策：resolved>0 且 new=0 → 可接受（这才是被采纳的结果）."""
    before = {"issues": [_rec("kf_time_window_alignment", "4"),
                         _rec("kf_time_window_alignment", "5"),
                         _rec("kf_time_window_alignment", "8")],
              "checker_failed": False}
    after_keep = {"issues": [_rec("kf_time_window_alignment", "8")],
                  "checker_failed": False}
    decision = _repair_acceptance_decision(before, after_keep, [], {"shots": []}, False)
    assert decision["accepted"] is True
    assert decision["resolved"] == 2 and decision["new_blocking"] == 0


# ----------------------------------------------------- 名额分配（v6.107）
def test_deterministic_targets_excludes_audit_codes():
    from step01_script_gen.main import _deterministic_targets

    issues = [
        {"code": "kf_event_window_mismatch", "severity": "visual_risk", "shot_id": "9"},
        {"code": "model_audit_issue", "severity": "visual_risk", "shot_id": "2"},
        {"code": "model_audit_issue", "severity": "visual_risk", "shot_id": "5"},
        {"code": "some_warn", "severity": "warn", "shot_id": "7"},
        {"code": "gaze_state_conflict", "severity": "hard", "shot_id": "0"},
    ]
    assert _deterministic_targets(issues) == {"shot_9", "hook"}


def test_repair_targets_prioritize_deterministic_blockers():
    """第二十九次重跑：带确定性阻塞的镜头必须先进名额，否则该轮没尝试它 → resolved=0."""
    from step01_script_gen.main import _pick_repair_targets

    shot_items = {"shot_2": ["a"], "shot_5": ["a"], "shot_8": ["a"],
                  "shot_9": ["a"], "shot_11": ["a"]}
    picked = _pick_repair_targets(shot_items, {"shot_9"}, 4)
    assert "shot_9" in picked
    assert len(picked) == 4
    # 无确定性阻塞时退回「按告警条数」
    picked2 = _pick_repair_targets(shot_items, set(), 2)
    assert len(picked2) == 2


# --------------------------------------------------------------- v6.137 带反馈重试
def test_new_blocking_rejection_retries_once_with_offender_excluded(monkeypatch):
    """v6.137：new_blocking 拒绝 → **带反馈重试一轮**（剔除肇事目标），不再一轮即死.

    生产实录（run 20260926T083731-78272）：4 个目标里只有 shot_4 的补丁引入新
    blocking（其余目标补丁也未通过验收，resolved=0/字段触达=0）→ 旧逻辑
    `not _round_ok` 直接 return——修复阶段一轮即死、提交层零改善 BLOCK。
    新逻辑：剔除 shot_4、其余目标带反馈重试一次；audit 问题在重试轮继续合并
    （遗留目标往往只有 audit 类告警）。
    """
    import json
    import re

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue
    from tests.step01_script_gen.test_linked_group_capacity import _Harness
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    MARKER = "独眼鹰标记唯有此处出现"

    class _Seq:
        """按调用次序返回不同补丁：第 1 轮 shot_4 引入新阻塞；第 2 轮修好 shot_9."""

        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)
            if self.n == 1:
                s4 = _complete_shot(4)
                # 修好 shot_4 确定性阻塞，但**同一授权字段**引入新 blocking
                # （生产实况：prop_possession_consistency 出现在授权路径上，回填救不了）
                s4["ff_desc"] = "杨旭双目圆睁仰卧船头触发新阻塞"
                s9 = _complete_shot(9)
                s9["prompt"] = "中景，" + MARKER              # 证据仍在 → 未解决
                reps = [{"shot_id": "4", "shot": s4},
                        {"shot_id": "9", "shot": s9}]
            else:
                s9 = _complete_shot(9)
                s9["prompt"] = "中景，已改成无标记的描述"     # 证据消失 → 新解决
                reps = [{"shot_id": "9", "shot": s9}]

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": reps,
                                   "hook_replacement": None}, ensure_ascii=False)

            return _R()

    seq = _Seq()
    audit_issues = [Issue(code="model_audit_X", severity="visual_risk",
                          message="shot 9 与原文矛盾", shot_id="9", frame_id="",
                          entity_id="", field_path="prompt",
                          evidence=MARKER,
                          repair_targets=[{"shot_id": "9", "field_path": "prompt"}])]

    def _warnings_fn(_script, _novel):
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        issues, ws = [], {}
        if "圆睁" not in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "state_chain_static", "severity": "visual_risk",
                           "shot_id": "4", "frame_id": "ff", "entity_id": "",
                           "field_path": "ff_desc", "message": "shot 4 状态链断裂"})
            ws["shot_4"] = ["[state_chain_static] shot 4 状态链断裂"]
        if "触发新阻塞" in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "kf_event_window_mismatch",
                           "severity": "visual_risk", "shot_id": "4",
                           "frame_id": "kf1", "entity_id": "",
                           "field_path": "keyframes.desc", "message": "新引入的阻塞"})
        return {"_has_hard": True, "shots": ws, "issues": issues}

    # 基线 shot_9 的 prompt 就含证据文本（生产实况：audit 是在真稿上跑的）——
    # 否则 v6.108 兜底口径（改前不可判 → 动过字段即计触达）会让按目标回退直接通过，
    # 走不到"重试也失败 → 整轮回退 → v6.137 重试"的路径。
    _s9 = _complete_shot(9)
    _s9["prompt"] = "中景，" + MARKER
    _base_script = {"shots": [_complete_shot(4), _s9],
                    "hook": None, "fps": 24, "total_duration": 36}
    _h = _Harness(monkeypatch, audit_issues=audit_issues, indexes=(4, 9),
                  warnings_fn=_warnings_fn, script=_base_script)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)
    _h.out = m._repair_shot_warnings(_h.script, "novel", audit_issues=audit_issues,
                                     max_rounds=2, repair_diag=_h.diag,
                                     block_reasons=_h.block)

    assert seq.n == 2, "应当重试一次（共两次修复调用）：%s" % _h.logs[:16]
    assert any("带反馈重试" in l for l in _h.logs), _h.logs[:16]
    # 重试轮提示词：点名拒绝原因 + 肇事目标已移除
    _turn2 = seq.reqs[1].user_inputs[1]["content"]
    assert "上一轮补丁被拒" in _turn2 and "已从本轮移除" in _turn2, _turn2[-600:]
    _fb2 = re.search(r"```json\n(.*?)\n```", _turn2, re.S)
    assert _fb2, _turn2[:400]
    _fb2 = json.loads(_fb2.group(1))
    assert "shot_4" not in _fb2.get("shots", {}),         "重试轮 REPAIR FEEDBACK 不得再包含肇事目标 shot_4"
    # 最终稿：shot_9 修复保留；shot_4 回到基线
    _shots = _h.shots()
    assert MARKER not in str(_shots["9"]["prompt"]), _shots["9"]["prompt"]
    assert "圆睁" not in str(_shots["4"]["ff_desc"]), "肇事目标必须回到基线"
    # 台账：两条事务记录；第二条=重试轮，accepted 且只剩 shot_9
    _txs = [r for r in _h.diag if "groups" in r]
    assert len(_txs) == 2 and _txs[0]["accepted"] is False and _txs[1]["accepted"] is True
    assert _txs[1]["targets"] == ["shot_9"], _txs[1]


def test_retry_state_crosses_calls_for_max_rounds_one(monkeypatch):
    """v6.137：max_rounds=1（提交路径）→ 重试状态**导出给调用方跨调用续跑**.

    第二次实跑（2026-09-26 18:10）实证：内层 continue 在 max_rounds=1 下装不下
    第二轮（§1.1 提交路径每次调用只发一次修复调用），带反馈重试打了日志就返回。
    契约改为：第一次调用返回回滚基线 + retry_state（剔除目标+反馈）；调用方用
    下一次 attempt 把同一个 dict 传回来，第二次调用消费状态（剔除+反馈预载）。
    """
    import json

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue
    from tests.step01_script_gen.test_linked_group_capacity import _Harness
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    MARKER = "独眼鹰标记唯有此处出现"

    class _Seq:
        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)
            if self.n == 1:
                s4 = _complete_shot(4)
                s4["ff_desc"] = "杨旭双目圆睁仰卧船头触发新阻塞"
                s9 = _complete_shot(9)
                s9["prompt"] = "中景，" + MARKER
                reps = [{"shot_id": "4", "shot": s4}, {"shot_id": "9", "shot": s9}]
            else:
                s9 = _complete_shot(9)
                s9["prompt"] = "中景，已改成无标记的描述"
                reps = [{"shot_id": "9", "shot": s9}]

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": reps,
                                   "hook_replacement": None}, ensure_ascii=False)

            return _R()

    seq = _Seq()
    audit_issues = [Issue(code="model_audit_X", severity="visual_risk",
                          message="shot 9 与原文矛盾", shot_id="9", frame_id="",
                          entity_id="", field_path="prompt",
                          evidence=MARKER,
                          repair_targets=[{"shot_id": "9", "field_path": "prompt"}])]

    def _warnings_fn(_script, _novel):
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        issues, ws = [], {}
        if "圆睁" not in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "state_chain_static", "severity": "visual_risk",
                           "shot_id": "4", "frame_id": "ff", "entity_id": "",
                           "field_path": "ff_desc", "message": "shot 4 状态链断裂"})
            ws["shot_4"] = ["[state_chain_static] shot 4 状态链断裂"]
        if "触发新阻塞" in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "kf_event_window_mismatch",
                           "severity": "visual_risk", "shot_id": "4",
                           "frame_id": "kf1", "entity_id": "",
                           "field_path": "keyframes.desc", "message": "新引入的阻塞"})
        return {"_has_hard": True, "shots": ws, "issues": issues}

    _s9 = _complete_shot(9)
    _s9["prompt"] = "中景，" + MARKER
    _base = {"shots": [_complete_shot(4), _s9],
             "hook": None, "fps": 24, "total_duration": 36}
    _h = _Harness(monkeypatch, audit_issues=audit_issues, indexes=(4, 9),
                  warnings_fn=_warnings_fn, script=_base)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)
    state: dict = {}

    out1 = m._repair_shot_warnings(_h.script, "novel", audit_issues=audit_issues,
                                   max_rounds=1, repair_diag=_h.diag,
                                   block_reasons=_h.block, retry_state=state)
    assert seq.n == 1
    assert state.get("excluded") == ["shot_4"], state
    assert "上一轮补丁被拒" in str(state.get("feedback")), state
    from shared.quality_gate import compute_script_revision
    assert compute_script_revision(out1) == compute_script_revision(_base), \
        "第一次调用应返回回滚基线（重试由调用方续跑）"

    out2 = m._repair_shot_warnings(out1, "novel", audit_issues=audit_issues,
                                   max_rounds=1, repair_diag=_h.diag,
                                   block_reasons=_h.block, retry_state=state)
    assert seq.n == 2 and not state, "第二次调用必须消费重试状态"
    _turn2 = seq.reqs[1].user_inputs[1]["content"]
    assert "上一轮补丁被拒" in _turn2 and "已从本轮移除" in _turn2
    _shots = {str(x.get("index")): x for x in out2["shots"]}
    assert MARKER not in str(_shots["9"]["prompt"]), "重试修复必须落稿"
    assert "圆睁" not in str(_shots["4"]["ff_desc"]), "肇事目标保持基线"


def test_retry_round_skips_group_containing_excluded_member(monkeypatch):
    """v6.138（九审 P1-2）：扩选入口同一排除约束——组含上轮肇事目标 → 整组跳过.

    判别反例（审查指定形态）：shot_2 与 shot_4 是一条审计问题的联动组；第 1 轮
    shot_4 的补丁引入新 blocking → shot_4 进排除集；重试轮里组若照发就会把
    shot_4 带回来。断言：重试轮**整组跳过**（不重发失败组）、shot_2 作为独立
    目标照常修复——组问题留待提交门，不再拖累其余目标的修复预算。
    """
    import json

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue
    from tests.step01_script_gen.test_linked_group_capacity import _Harness
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    M2 = "船桨裂纹唯有此处出现"

    class _Seq:
        """第 1 轮：shot_2 修好但 shot_4 引入新阻塞；第 2 轮：重修 shot_2."""

        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)
            s2 = _complete_shot(2)
            s2["prompt"] = "中景，船桨完好"          # 两轮都给出 shot_2 的正确修复
            s4 = _complete_shot(4)
            s4["ff_desc"] = ("杨旭双目圆睁仰卧船头触发新阻塞" if self.n == 1
                             else "杨旭双目紧闭仰卧船头")
            reps = [{"shot_id": "2", "shot": s2}, {"shot_id": "4", "shot": s4}]

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": reps,
                                   "hook_replacement": None}, ensure_ascii=False)

            return _R()

    seq = _Seq()
    audit_issues = [Issue(code="model_audit_STATE_CONTRADICTION", severity="visual_risk",
                          message="船桨状态在镜间自相矛盾。", shot_id="2", frame_id="",
                          entity_id="", field_path="prompt",
                          evidence=M2,
                          repair_targets=[{"shot_id": "2", "field_path": "prompt"},
                                          {"shot_id": "4", "field_path": "prompt"}])]

    def _warnings_fn(_script, _novel):
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        issues, ws = [], {}
        if M2 in str((shots.get("2") or {}).get("prompt") or ""):
            issues.append({"code": "state_chain_static", "severity": "visual_risk",
                           "shot_id": "2", "frame_id": "", "entity_id": "",
                           "field_path": "prompt", "message": "shot 2 状态链断裂"})
            ws["shot_2"] = ["[state_chain_static] shot 2 状态链断裂"]
        if "圆睁" not in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "state_chain_static", "severity": "visual_risk",
                           "shot_id": "4", "frame_id": "ff", "entity_id": "",
                           "field_path": "ff_desc", "message": "shot 4 状态链断裂"})
            ws["shot_4"] = ["[state_chain_static] shot 4 状态链断裂"]
        if "触发新阻塞" in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "kf_event_window_mismatch",
                           "severity": "visual_risk", "shot_id": "4",
                           "frame_id": "kf1", "entity_id": "",
                           "field_path": "keyframes.desc", "message": "新引入的阻塞"})
        return {"_has_hard": True, "shots": ws, "issues": issues}

    _s2 = _complete_shot(2)
    _s2["prompt"] = "中景，" + M2
    _base = {"shots": [_s2, _complete_shot(4)],
             "hook": None, "fps": 24, "total_duration": 36}
    _h = _Harness(monkeypatch, audit_issues=audit_issues, indexes=(2, 4),
                  warnings_fn=_warnings_fn, script=_base)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)
    _h.out = m._repair_shot_warnings(_h.script, "novel", audit_issues=audit_issues,
                                     max_rounds=2, repair_diag=_h.diag,
                                     block_reasons=_h.block)

    assert seq.n == 2, "应当重试一次：%s" % _h.logs[:16]
    assert any("整组跳过" in l for l in _h.logs), _h.logs[:20]
    # 重试轮：shot_2 照常修复落稿；shot_4 保持基线（组未重发）
    _shots = _h.shots()
    assert M2 not in str(_shots["2"]["prompt"]), _shots["2"]["prompt"]
    assert "圆睁" not in str(_shots["4"]["ff_desc"]), "肇事目标保持基线"
    _txs = [r for r in _h.diag if "groups" in r]
    assert len(_txs) == 2
    assert _txs[1]["accepted"] is True and _txs[1]["targets"] == ["shot_2"], _txs[1]
    assert _txs[1]["groups"] == [], "重试轮不得再发失败组"


def test_touch_fallback_credits_each_issue_identity_once(monkeypatch):
    """v6.139（修复 2，九审根因 2）：触达兜底对同一问题身份只记一次账.

    判别反例（09-26 20:49 run 实锤形态）：自由文本证据在改前稿里找不到 →
    「动过点名字段」就被当成已解决（accepted/resolved=0/触达=1）。同一问题
    第二轮再触达，必须**不再给兜底分**——要么真解决（证据判据不受影响），
    要么如实 no_target_resolved，不得把反复尝试当进展。
    """
    import json

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue
    from tests.step01_script_gen.test_linked_group_capacity import _Harness
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    EVIDENCE = "船桨裂纹唯有此处出现"          # 自由文本证据：稿子里**没有**这段字
    P1, P2 = "中景，水面泛起涟漪", "中景，水面泛起涟漪二"

    class _Seq:
        """两轮都只改 prompt（audit 点名字段）：第 1 轮兜底记账，第 2 轮被拒."""

        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)
            s9 = _complete_shot(9)
            s9["prompt"] = P1 if self.n == 1 else P2
            reps = [{"shot_id": "9", "shot": s9}]

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": reps,
                                   "hook_replacement": None}, ensure_ascii=False)

            return _R()

    seq = _Seq()
    audit_issues = [Issue(code="model_audit_STATE_CONTRADICTION", severity="visual_risk",
                          message="船桨状态前后矛盾。", shot_id="9", frame_id="",
                          entity_id="", field_path="prompt",
                          evidence=EVIDENCE,
                          repair_targets=[{"shot_id": "9", "field_path": "prompt"}])]

    def _warnings_fn(_script, _novel):
        """确定性问题挂在 ff_desc（补丁只改 prompt）→ resolved 恒 0，触达是唯一功绩."""
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        issues, ws = [], {}
        if "锚定姿态" not in str((shots.get("9") or {}).get("ff_desc") or ""):
            issues.append({"code": "state_chain_static", "severity": "visual_risk",
                           "shot_id": "9", "frame_id": "ff", "entity_id": "",
                           "field_path": "ff_desc", "message": "shot 9 状态链断裂"})
            ws["shot_9"] = ["[state_chain_static] shot 9 状态链断裂"]
        return {"_has_hard": True, "shots": ws, "issues": issues}

    _base = {"shots": [_complete_shot(9)],
             "hook": None, "fps": 24, "total_duration": 36}
    _h = _Harness(monkeypatch, audit_issues=audit_issues, indexes=(9,),
                  warnings_fn=_warnings_fn, script=_base)
    attempted: set = set()
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)
    _h.out = m._repair_shot_warnings(_h.script, "novel", audit_issues=audit_issues,
                                     max_rounds=4, repair_diag=_h.diag,
                                     block_reasons=_h.block,
                                     audit_attempted=attempted)

    assert seq.n == 2, "第 1 轮兜底通过、第 2 轮同身份被拒，共两次调用：%s" % _h.logs[:16]
    # 第 1 轮：accepted 靠兜底（resolved=0 / 触达=1），身份入「已尝试」集
    _txs = [r for r in _h.diag if "groups" in r]
    assert _txs[0]["accepted"] is True and _txs[0]["resolved"] == 0
    assert _txs[0].get("audit_field_touched") == 1, _txs[0]
    assert attempted == {("9", "state_contradiction")}, attempted
    # 第 2 轮：同身份再触达 → 兜底停用 → no_target_resolved 回退
    assert _txs[1]["accepted"] is False and _txs[1]["reason"] == "no_target_resolved"
    assert any("no_target_resolved" in l for l in _h.logs), _h.logs[:20]
    # 最终稿：第 1 轮补丁保留（第 2 轮被回退）
    _shots = _h.shots()
    # 后处理会给 prompt 追加防崩句 → 按前缀断言（第 1 轮补丁保留、第 2 轮被回退）
    assert str(_shots["9"]["prompt"]).startswith(P1), _shots["9"]["prompt"]


def test_commit_defers_failed_group_and_keeps_repairing_others(tmp_path, monkeypatch):
    """v6.140（修复 3，九审根因 3）：失败联动组**整组暂缓**，剩余预算继续修组外目标.

    判别反例（审查指定形态 + 09-26 20:49 run 实锤）：3 镜联动组零解决（证据仍在，
    触达也计不了账）→ 旧实现零改善即**全局停机**，shot 2 的独立问题还有预算也没人
    修。新行为：组暂缓到本 run 后续轮次（不重发），下一轮修 shot 2；组的 blocking
    保留、最终照常 BLOCK（不做半条修复提交）。
    """
    import json

    import step01_script_gen.main as m

    from tests.step01_script_gen.test_candidate_dialogue_integrity import (
        _LONG_LINE, _PLAN)

    GHOST = "GHOST_MARK 船桨状态矛盾"
    SOLO = "SOLO_MARK 独眼鹰标记"

    from tests.step01_script_gen.test_repair_targets import _complete_shot

    def _shot(i, prompt):
        s = _complete_shot(i)
        # 旁白与字幕同源同步：两个字段都得清空，否则 normalization 会从 subtitle
        # 把旁白还原回来（实测踩坑）
        s["narration_segment"] = ""
        s["subtitle"] = ""
        s["prompt"] = prompt
        return s

    _s1 = _complete_shot(1)
    _s1["narration_segment"] = "张十三道：" + _LONG_LINE
    _base = {"shots": [
        _s1,
        _shot(2, "中景，" + SOLO),
        _shot(3, "近景，" + GHOST),
        _shot(4, "特写，" + GHOST),
        _shot(5, "中景，" + GHOST),
    ], "total_duration": 24.0}

    def _audit_payload(with_solo):
        issues = [{
            "code": "model_audit_STATE_CONTRADICTION", "severity": "visual_risk",
            "shot_id": "3", "frame_id": "", "entity_id": "", "field_path": "prompt",
            "message": "船桨状态在镜间自相矛盾。", "evidence": GHOST,
            "repair_targets": [{"shot_id": s, "field_path": "prompt"}
                               for s in ("3", "4", "5")],
        }]
        if with_solo:
            issues.append({
                "code": "model_audit_TEXT_ERROR", "severity": "visual_risk",
                "shot_id": "2", "frame_id": "", "entity_id": "", "field_path": "prompt",
                "message": "描述无法执行。", "evidence": SOLO,
                "repair_targets": [{"shot_id": "2", "field_path": "prompt"}],
            })
        return {"status": "ok", "issues": issues}

    def _audit_wrapper(script, novel_text, plan, **k):
        """审计结论**按输入定**，不按「第几次调用」定.

        v6.153（2026-10-03 复核之十九 P1）起，提交循环会**复用**同一请求身份的
        已成功结论（同稿回退 + 反馈重试不再重新 POST）⇒ 调用次数 ≠ 轮次。
        计数式桩会因此把上一轮的结论错配给新稿件，本用例的分组语义就测不准了。
        """
        _solo = any("SOLO_MARK" in str(s.get("prompt") or "")
                    for s in script.get("shots", []))
        return _audit_payload(_solo)

    class _Seq:
        """第 1 轮只改组内三镜（证据保留 → 零解决）；第 2 轮修 shot 2（证据消除）."""

        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)
            if self.n == 1:
                reps = [{"shot_id": s,
                         "shot": _shot(int(s), "改写一，" + GHOST)}
                        for s in ("3", "4", "5")]
            else:
                reps = [{"shot_id": "2",
                         "shot": _shot(2, "中景，已改成无标记的描述")}]

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": reps,
                                   "hook_replacement": None}, ensure_ascii=False)

            return _R()

    seq = _Seq()
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "_run_final_semantic_audit", _audit_wrapper)
    monkeypatch.setattr(m, "_build_step01_quality_gate",
                        lambda s, n: {"hard": [], "visual_risk": [],
                                      "checker_failed": False, "status": "ok"})
    monkeypatch.setattr(m, "_collect_repair_warnings",
                        lambda s, n: {"_has_hard": False, "shots": {}, "issues": []})
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)

    script, summary = m._commit_step01_outputs(_base, "原文", plan=dict(_PLAN))

    assert seq.n == 2, "组失败后必须继续修组外目标（共 2 次修复调用）"
    _txs = [r for r in (summary.get("repair_transactions") or [])] \
        if summary.get("repair_transactions") else None
    # 第 2 次修复调用：目标是 shot_2、不再发失败组（组内三镜已被暂缓）
    _turn2 = seq.reqs[1].user_inputs[-1]["content"]
    assert "SOLO_MARK" in _turn2
    # 最终：shot 2 的证据已消除；组的 blocking 保留 → 照常 BLOCK（不做半条提交）
    assert summary["status"] == "BLOCK"
    _codes = [b.get("code") for b in (summary.get("blocking") or [])]
    assert _codes == ["model_audit_STATE_CONTRADICTION"], _codes
    _shots = {str(x.get("index")): x for x in script.get("shots", [])}
    assert SOLO not in str(_shots["2"]["prompt"])
    assert GHOST in str(_shots["3"]["prompt"]), "失败组保持原样（暂缓 ≠ 半条修复）"


def test_non_executable_target_does_not_kill_the_whole_round(tmp_path, monkeypatch):
    """v6.142（09-26 用户决定，P1-3）：一条坏目标不再连坐整轮修复.

    实损（09-26 20:25 run）：shot 3 的 12 条越界 repair_targets 让
    `_audit_repair_target_problems` 判「缺少可执行的定向修复目标」→ 进修复轮
    **之前**就显式 BLOCK，同轮里完全可修的其它目标一个都没动。

    判别性：审计同时给「越界目标」（shot 3）与「合法目标」（shot 2）。
    旧口径下 `seq.n == 0`（一次修复都没发）；新口径下修复照发，且请求里
    带的是合法目标那条的证据。坏条目**仍保留 blocking**（终局照常 BLOCK）。
    """
    import json

    import step01_script_gen.main as m

    from tests.step01_script_gen.test_candidate_dialogue_integrity import (
        _LONG_LINE, _PLAN)
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    BAD = "BAD_MARK 越界目标指向的缺陷"
    GOOD = "GOOD_MARK 可修的缺陷"

    def _shot(i, prompt):
        s = _complete_shot(i)
        s["narration_segment"] = ""
        s["subtitle"] = ""
        s["prompt"] = prompt
        return s

    # 旁白与字幕同源：提交层终稿台词门要求计划台词逐字保住，夹具必须带上
    _s1 = _shot(1, "中景，无标记的描述")
    _s1["narration_segment"] = "张十三道：" + _LONG_LINE
    _s1["subtitle"] = _s1["narration_segment"]
    _base = {"shots": [_s1, _shot(2, "近景，" + GOOD),
                       _shot(3, "特写，" + BAD)], "total_duration": 18.0}

    _audit = {"status": "ok", "issues": [
        # 越界路径：该镜 kfs 只有 3 项，[9][9] 不是它的真实字段 → 逐条剔除
        {"code": "model_audit_TEXT_ERROR", "severity": "visual_risk",
         "shot_id": "3", "frame_id": "", "entity_id": "", "field_path": "prompt",
         "message": "描述不可执行。", "evidence": BAD,
         "repair_targets": [{"shot_id": "3",
                             "field_path": "frame_layouts.kfs[9][9].pose"}]},
        {"code": "model_audit_TEXT_ERROR", "severity": "visual_risk",
         "shot_id": "2", "frame_id": "", "entity_id": "", "field_path": "prompt",
         "message": "描述不可执行。", "evidence": GOOD,
         "repair_targets": [{"shot_id": "2", "field_path": "prompt"}]},
    ]}

    class _Seq:
        def __init__(self):
            self.n = 0
            self.reqs = []

        def generate(self, req):
            self.n += 1
            self.reqs.append(req)

            class _R:
                success = True
                error = ""
                text = json.dumps({"shot_replacements": [
                    {"shot_id": "2", "shot": _shot(2, "近景，已改成无标记的描述")},
                ], "hook_replacement": None}, ensure_ascii=False)

            return _R()

    _warns: list = []

    class _Log:
        def warn(self, msg, *a, **k):
            _warns.append(str(msg))

        def error(self, msg, *a, **k):
            _warns.append(str(msg))

        def info(self, *a, **k):
            pass

        def verbose(self, *a, **k):
            pass

    seq = _Seq()
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "_logger", _Log())
    monkeypatch.setattr(m, "_run_final_semantic_audit", lambda *a, **k: dict(_audit))
    monkeypatch.setattr(m, "_build_step01_quality_gate",
                        lambda s, n: {"hard": [], "visual_risk": [],
                                      "checker_failed": False, "status": "ok"})
    monkeypatch.setattr(m, "_collect_repair_warnings",
                        lambda s, n: {"_has_hard": False, "shots": {}, "issues": []})
    monkeypatch.setattr(m, "create_service", lambda *a, **k: seq)

    _script, summary = m._commit_step01_outputs(_base, "原文", plan=dict(_PLAN))

    assert seq.n >= 1, "一条坏目标不得掐掉整轮修复（旧口径这里恒为 0）"
    _turn1 = seq.reqs[0].user_inputs[-1]["content"]
    assert GOOD in _turn1, "合法目标必须照常进修复轮"
    assert any("缺少可执行的定向修复目标" in w for w in _warns), (
        "坏目标必须留下显式告警（准则 7：不许静默降级）：%s" % _warns[:4])
    # 坏条目**保留 blocking** → 终局照常 BLOCK，不做半条提交
    assert summary["status"] == "BLOCK", summary
    assert any(b.get("code") == "model_audit_TEXT_ERROR"
               for b in (summary.get("blocking") or [])), summary.get("blocking")
