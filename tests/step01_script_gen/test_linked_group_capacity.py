# -*- coding: utf-8 -*-
"""联动组容量（v6.122，用户决定 B）：组上限 6、普通单轮上限仍 4（无网络）.

## 为什么需要它

真实 run `20260925T145218-32008`（BLOCK，修复轮 0）里有一条跨镜问题声明了 **5 镜**
目标：`4/ff_desc` + `5,7,8,9/prompt`。按 audited revision（`snapshots/
script_result_20260925_145317.json`，revision 与 `input_revision` 一致）逐字核实：
写「紧闭」的正是这 5 处文本（4 镜 prompt + 1 镜 ff_desc），写「圆睁」的 3 处不用改
——**5 镜全部必要**，组不过宽。而单轮上限是 4 → 组永远进不了本轮 → 只剩 BLOCK。

用户口径（B）：`_REPAIR_MAX_SHOTS_PER_ROUND`（4）继续管**互不相关**的目标；
**原子联动组**有专用上限 `_REPAIR_MAX_SHOTS_PER_GROUP`（6）；超过 4 镜的组
**独占本轮名额**、其他单镜目标留待下一轮；共享镜头合组后超过 6 → **立即 BLOCK**，
不再空耗修复轮与审计调用。拆组（半条修复）与删目标（漏声明）都禁止。

## 本文件覆盖

`整组入选`（含自身无告警的成员）、`应用`、`整组回退`、`已全入选`、`两个独立组`、
`合组超过上限 → 立即 BLOCK`。
"""
from __future__ import annotations


def _tx(diag: list) -> list:
    """台账里取**修复事务**记录：v6.133 起还有 call_ok / call_failed 两类
    （用户 2026-09-25），它们没有 groups/accepted 字段，不能按位置取第 0 条."""
    return [r for r in diag if "groups" in r]


import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.quality_issues import Issue  # noqa: E402
from tests.step01_script_gen.test_repair_targets import _complete_shot  # noqa: E402

# 真实 run 的 5 镜目标（顺序即声明顺序）
REAL_GROUP = ("4", "5", "7", "8", "9")


def _group_issue(targets=None, *, located: str = "4") -> Issue:
    """跨镜状态矛盾（真实形态）：问题定位在锚点镜 4，成对目标覆盖多镜."""
    _pairs = targets or [(s, "ff_desc" if s == "4" else "prompt")
                         for s in REAL_GROUP]
    return Issue(code="model_audit_STATE_CONTRADICTION", severity="visual_risk",
                 message="死亡状态在镜间自相矛盾，观众看到睁眼/闭眼跳变。",
                 shot_id=located, frame_id="ff", entity_id="杨旭",
                 field_path="ff_desc",
                 evidence="shot3 lf_desc为「双目圆睁」，shot4 ff_desc却写「双目紧闭」",
                 repair_targets=[{"shot_id": s, "field_path": f} for s, f in _pairs])


def _warnings_fn(*, new_block_on: str | None = None):
    """确定性检查器桩（**按稿件内容**判定，非常量——常量桩会让验收永远不通过）.

    - shot 2 常报：它会被大组腾位推迟，用来验证「其他单镜目标留待下一轮」；
    - shot 4 的 `ff_desc` 含「圆睁」即消失：提供 `resolved=1`，让本轮能通过验收；
    - `new_block_on` 那一镜的 prompt 出现「触发新阻塞」时报一条新阻塞（验整组回退）。
    """
    def _fn(_script, _novel):
        _shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        _issues: list = []
        _ws: dict = {}
        if "2" in _shots:
            _issues.append({"code": "kf_time_window_alignment",
                            "severity": "visual_risk", "shot_id": "2",
                            "frame_id": "", "entity_id": "",
                            "field_path": "kf_time_window_alignment",
                            "message": "shot 2 时段缺关键帧"})
            _ws["shot_2"] = ["[kf_time_window_alignment] shot 2 时段缺关键帧"]
        if "圆睁" not in str((_shots.get("4") or {}).get("ff_desc") or ""):
            _issues.append({"code": "state_chain_static", "severity": "visual_risk",
                            "shot_id": "4", "frame_id": "ff", "entity_id": "",
                            "field_path": "ff_desc", "message": "shot 4 状态链断裂"})
            _ws["shot_4"] = ["[state_chain_static] shot 4 状态链断裂"]
        if new_block_on and "触发新阻塞" in str(
                (_shots.get(new_block_on) or {}).get("prompt") or ""):
            _issues.append({"code": "kf_event_window_mismatch",
                            "severity": "visual_risk", "shot_id": new_block_on,
                            "frame_id": "kf1", "entity_id": "",
                            "field_path": "keyframes.desc",
                            "message": "新引入的阻塞"})
        return {"_has_hard": True, "shots": _ws, "issues": _issues}
    return _fn


def _group_replacements(*, new_block_on: str | None = None) -> list:
    """ LLM 回填的替换：每个成员只改它被授权的那个字段（锚点镜 ff_desc，其余 prompt）."""
    _out = []
    for _i in REAL_GROUP:
        _shot = _complete_shot(int(_i))
        if _i == "4":
            _shot["ff_desc"] = "杨旭双目圆睁仰卧船头"
        else:
            _shot["prompt"] = (f"中景，杨旭双目圆睁仰卧（{_i}）"
                               + ("触发新阻塞" if new_block_on == _i else ""))
        _out.append({"shot_id": _i, "shot": _shot})
    return _out


class _Harness:
    """跑一次真实 `_repair_shot_warnings`（LLM + 检查器 + 日志全部打桩）."""

    def __init__(self, monkeypatch, *, audit_issues, indexes, warnings_fn,
                 replacements=None, script=None):
        import step01_script_gen.main as m

        self.m = m
        self.logs: list = []
        self.calls = {"n": 0}
        self.diag: list = []
        self.block: list = []
        self.reqs: list = []
        _logs = self.logs

        class _Ok:
            success = True
            error = ""
            text = json.dumps(
                {"shot_replacements": replacements if replacements is not None
                 else _group_replacements(),
                 "hook_replacement": None}, ensure_ascii=False)

        class _Svc:
            def generate(self, req):
                self_calls["n"] += 1
                _reqs.append(req)
                return _Ok()

        self_calls = self.calls
        _reqs = self.reqs

        class _Log:
            def info(self, msg, *a, **k):
                _logs.append(str(msg))

            def warn(self, msg, *a, **k):
                _logs.append(str(msg))

            def error(self, msg, *a, **k):
                _logs.append(str(msg))

            def verbose(self, msg, *a, **k):
                pass

            def console(self, msg, *a, **k):
                # 「N 镜待修」是走 console 的（不是 logger）——不吞掉它，
                # 「本轮到底点名了哪几镜」才有可观测的断言点
                _logs.append(str(msg))

        monkeypatch.setattr(m, "_logger", _Log())
        # 「N 镜待修」走 `_console()`（stdout），不是 logger——不接管它，
        # 「本轮点名了哪几镜」就没有可断言的地方
        monkeypatch.setattr(m, "_console", lambda msg, *a, **k: _logs.append(str(msg)))
        monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
        monkeypatch.setattr(m, "resolve_model_prompt_profile",
                            lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
        monkeypatch.setattr(m, "_collect_repair_warnings", warnings_fn)
        for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
            monkeypatch.setattr(m, _fn, lambda *a, **k: None)

        self.script = script or {"shots": [_complete_shot(i) for i in indexes],
                                 "hook": None, "fps": 24, "total_duration": 36}
        self.audit_issues = list(audit_issues)

    def run(self):
        # `max_rounds=1`：提交点的生产配置（§1.1 每轮只发一次修复调用）；
        # 不传的话内部会跑 3 轮，把「本轮只该动大组」的断言污染掉。
        self.out = self.m._repair_shot_warnings(
            self.script, "novel", audit_issues=self.audit_issues,
            max_rounds=1, repair_diag=self.diag, block_reasons=self.block)
        return self

    @property
    def picked_line(self) -> str:
        _lines = [l for l in self.logs if "镜待修" in l]
        assert _lines, "没有走到修复轮：%s" % self.logs[:12]
        return _lines[0]

    def feedback(self) -> dict:
        """发给 LLM 的 REPAIR FEEDBACK JSON（第二个 user 输入里的 ```json 块）."""
        import re as _re

        assert self.reqs, "没有发出任何修复请求"
        _turn = self.reqs[-1].user_inputs[1]["content"]
        _m = _re.search(r"```json\n(.*?)\n```", _turn, _re.S)
        assert _m, _turn[:400]
        return json.loads(_m.group(1))

    def prompt_text(self) -> str:
        """实际发出的全部提示词（含单轮回退 `user_prompt`）."""
        assert self.reqs, "没有发出任何修复请求"
        _req = self.reqs[-1]
        return "\n".join([_req.system_prompt or "", _req.user_prompt or ""] + [
            str(x.get("content") or "") for x in (_req.user_inputs or [])])

    def shots(self) -> dict:
        return {str(x.get("index")): x for x in self.out["shots"]}


# ---------------------------------------------------------------------------
# 整组入选 / 应用
# ---------------------------------------------------------------------------
def test_five_shot_group_takes_the_round_and_is_applied(monkeypatch):
    """真实 5 镜目标：整组入选（含自身无告警的 7/8/9）→ 独占名额 → 应用落稿."""
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(2, 4, 5, 7, 8, 9),
                  warnings_fn=_warnings_fn()).run()

    assert _h.calls["n"] == 1, "应当且只应发一次修复调用：%s" % _h.logs[:12]
    assert not _h.block

    for _s in REAL_GROUP:
        assert f"shot_{_s}" in _h.picked_line, (
            "组内成员 %s 没进本轮（自身无告警也必须进）：%s" % (_s, _h.picked_line))
    assert "shot_2" not in _h.picked_line, (
        "大组独占名额，其他单镜目标须留待下一轮：%s" % _h.picked_line)
    assert any("独占本轮名额" in l for l in _h.logs), _h.logs[:12]
    assert _h.diag and _tx(_h.diag)[0]["groups"] == [
        sorted(f"shot_{s}" for s in REAL_GROUP)]

    assert any("应用完整镜头替换 5 镜" in l for l in _h.logs), _h.logs[:20]
    _shots = _h.shots()
    assert "圆睁" in _shots["4"]["ff_desc"], _shots["4"]["ff_desc"]
    for _s in ("5", "7", "8", "9"):
        assert "圆睁" in _shots[_s]["prompt"], (_s, _shots[_s]["prompt"])


def test_member_without_own_warnings_gets_the_issue_context(monkeypatch):
    """联动组成员自身无告警时，反馈里必须带上**驱动它的那条问题**（否则无从下手）."""
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9),
                  warnings_fn=_warnings_fn()).run()

    _fb = _h.feedback()["shots"]
    for _s in ("5", "7", "8", "9"):
        assert _fb[f"shot_{_s}"]["allowed_fields"] == ["prompt"], _fb[f"shot_{_s}"]
        _ai = _fb[f"shot_{_s}"]["audit_issues"]
        assert _ai, "成员 shot_%s 没有收到驱动它的那条问题" % _s
        assert "双目紧闭" in _ai[0]["evidence"], _ai[0]
    assert _fb["shot_4"]["allowed_fields"] == ["ff_desc"]
    # 自身无告警的成员在逐镜块里要明确指路（否则模型面对空「问题:」无从下手）
    assert "本镜自身无告警" in _h.prompt_text()


# ---------------------------------------------------------------------------
# 整组回退
# ---------------------------------------------------------------------------
def test_five_shot_group_rolls_back_as_a_whole(monkeypatch):
    """组内任一镜引入新阻塞 → **整组**回退（不留半组在稿子上）."""
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9),
                  warnings_fn=_warnings_fn(new_block_on="8"),
                  replacements=_group_replacements(new_block_on="8")).run()

    assert _h.calls["n"] == 1
    assert _h.diag and _tx(_h.diag)[0]["rolled_back_groups"] == [
        sorted(f"shot_{s}" for s in REAL_GROUP)], (
        "整组回退没登记：%s" % (_tx(_h.diag)[0] if _h.diag else None))
    assert not _tx(_h.diag)[0]["accepted"]
    _shots = _h.shots()
    for _s in REAL_GROUP:
        assert "圆睁" not in str(_shots[_s].get("prompt") or ""), (
            "shot %s 的改动没被回退" % _s)
    assert "圆睁" not in _shots["4"]["ff_desc"], "锚点镜的改动没被回退"


# ---------------------------------------------------------------------------
# 两个独立组 / 已全入选
# ---------------------------------------------------------------------------
def test_two_independent_groups_big_one_takes_the_round(monkeypatch):
    """两个独立组：大组独占本轮，小组**整组推迟**（不拆、也不互相挤掉）."""
    _small = _group_issue(targets=[("2", "ff_desc"), ("3", "prompt")], located="2")
    _h = _Harness(monkeypatch, audit_issues=[_group_issue(), _small],
                  indexes=(2, 3, 4, 5, 7, 8, 9),
                  warnings_fn=_warnings_fn()).run()

    assert _h.calls["n"] == 1 and not _h.block
    for _s in REAL_GROUP:
        assert f"shot_{_s}" in _h.picked_line, _h.picked_line
    assert "shot_3" not in _h.picked_line, (
        "小组不该与大组同轮：%s" % _h.picked_line)
    assert _tx(_h.diag)[0]["groups"] == [sorted(f"shot_{s}" for s in REAL_GROUP)], (
        "本轮只应登记大组：%s" % _tx(_h.diag)[0]["groups"])


def test_group_already_fully_picked_is_still_registered(monkeypatch):
    """组内镜头**已全在本轮目标里**时仍须登记为组（否则回退只回退肇事那一镜）."""
    _four = _group_issue(targets=[(s, "ff_desc" if s == "4" else "prompt")
                                  for s in ("4", "5", "7", "8")])

    def _warn_all(_script, _novel):
        _shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        _issues, _ws = [], {}
        for _i in ("4", "5", "7", "8"):
            if _i not in _shots:
                continue
            _issues.append({"code": "state_chain_static", "severity": "visual_risk",
                            "shot_id": _i, "frame_id": "ff", "entity_id": "",
                            "field_path": "ff_desc",
                            "message": f"shot {_i} 状态链断裂"})
            _ws[f"shot_{_i}"] = [f"[state_chain_static] shot {_i} 状态链断裂"]
        return {"_has_hard": True, "shots": _ws, "issues": _issues}

    _repls = []
    for _i in ("4", "5", "7", "8"):
        _shot = _complete_shot(int(_i))
        if _i == "4":
            _shot["ff_desc"] = f"杨旭双目圆睁仰卧（{_i}）"
        else:
            _shot["prompt"] = f"中景，圆睁（{_i}）"
        _repls.append({"shot_id": _i, "shot": _shot})

    _h = _Harness(monkeypatch, audit_issues=[_four], indexes=(4, 5, 7, 8),
                  warnings_fn=_warn_all, replacements=_repls).run()

    assert _h.diag and _tx(_h.diag)[0]["groups"] == [["shot_4", "shot_5", "shot_7",
                                                "shot_8"]], (
        "「已全入选」的组被丢弃了：%s" % (_tx(_h.diag)[0] if _h.diag else None))


# ---------------------------------------------------------------------------
# 合组超过上限 → 立即 BLOCK（不花 LLM、不再发审计）
# ---------------------------------------------------------------------------
def test_merged_group_over_the_ceiling_blocks_without_an_llm_call(monkeypatch):
    """两条问题共享镜头 → 合并后 7 镜 > 6 → 立即放弃整轮并给出显式 BLOCK 原因."""
    _a = _group_issue(targets=[(s, "prompt") for s in ("4", "5", "7", "8", "9")])
    _b = _group_issue(targets=[(s, "prompt") for s in ("5", "9", "10", "11")],
                      located="5")
    _h = _Harness(monkeypatch, audit_issues=[_a, _b],
                  indexes=(4, 5, 7, 8, 9, 10, 11),
                  warnings_fn=_warnings_fn()).run()

    assert _h.calls["n"] == 0, (
        "超过上限的组不得再花 LLM 调用：%s" % _h.logs[-4:])
    assert _h.block, "必须给出可执行的 BLOCK 原因"
    assert "上限 6" in _h.block[0] and "7 镜" in _h.block[0], _h.block[0]
    assert "无法整组修复" in _h.block[0], _h.block[0]
    assert _h.out["shots"], "稿件必须原样返回（不替换、不写盘）"


def test_group_ceiling_and_round_cap_are_distinct_constants():
    """两条上限各自独立：单轮 4（互不相关目标）/ 联动组 6（原子组）."""
    import step01_script_gen.main as m

    assert m._REPAIR_MAX_SHOTS_PER_ROUND == 4
    assert m._REPAIR_MAX_SHOTS_PER_GROUP == 6


def test_group_ceiling_is_the_single_lever(monkeypatch):
    """反例对照：把组上限退回 4（= 决定前的行为）→ 同一份 5 镜目标被整组放弃."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "_REPAIR_MAX_SHOTS_PER_GROUP", 4)
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(2, 4, 5, 7, 8, 9),
                  warnings_fn=_warnings_fn()).run()
    assert _h.calls["n"] == 0, "上限 4 时不该再花 LLM 调用"
    assert _h.block and "上限 4" in _h.block[0], _h.block
