# -*- coding: utf-8 -*-
"""修复调用结果分类（v6.133，用户 2026-09-26 复核）：**"响应不可用"≠"修复无进展"**.

## 为什么需要它

09-25 那次 BLOCK 的直接原因是定向修复响应被 `max_output_tokens` 截断；上一轮已把
「API 调用失败/被截断」单独记成 `kind="call_failed"`。复核指出还剩一个归类缺口：
**调用成功、但补丁不可用**——「JSON 解析失败 / envelope 结构无效 / 没返回任何替换 /
替换全被校验拒」这四条早退路径**只打日志**，提交层看不到结构化记录，于是与
"补丁有效但零改善"共用 `no_progress`，把"收到不可用响应"说成了"修复没有进展"。

## 本文件覆盖

以**真实 `_repair_shot_warnings` 路径**（只有 LLM 打桩）逐条验证四种 `reason`
都落进台账；再验证 token 余量口径只看 `output_tokens`（它已含思维链 token，
`reasoning_tokens` 只是明细——两者相加会把思维链扣两遍）。

`_Harness` 复用自 `test_linked_group_capacity`（同一套「真实修复路径 + 打桩 LLM」装置）；
这里只把 `create_service` 换成返回指定文本的桩。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.test_linked_group_capacity import (  # noqa: E402
    _Harness, _group_issue, _tx, _warnings_fn)


def _service_with_text(text: str, box: dict):
    """返回指定原始文本的桩服务（`success=True`——模拟"调用成功但内容不可用"）.

    注意：`_Harness` 自带的桩服务计的是它自己的 `calls["n"]`，这里换掉了桩服务，
    所以调用次数记在 `box` 里（`_h.stub`），不能拿 `_h.calls` 断言。
    """
    class _R:
        success = True
        error = ""
        truncated = False
        usage = {"output_tokens": 900, "reasoning_tokens": 400, "total_tokens": 4000}

        def __init__(self, _text):
            self.text = _text

    class _Svc:
        def generate(self, req):
            box["n"] = box.get("n", 0) + 1
            return _R(text)

    return _Svc()


def _run(monkeypatch, text: str):
    """跑一次真实修复轮：LLM 返回 `text`，返回打桩装置（台账在 `_h.diag`）."""
    _box: dict = {}
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9), warnings_fn=_warnings_fn())
    monkeypatch.setattr(_h.m, "create_service",
                        lambda *a, **k: _service_with_text(text, _box))
    _h.stub = _box
    return _h.run()


def _unusable(diag: list) -> list:
    return [r for r in diag if r.get("kind") == "patch_unusable"]


# ---------------------------------------------------------------------------
# 四条"响应完整但补丁不可用"
# ---------------------------------------------------------------------------
def test_parse_failed_patch_is_recorded_as_unusable(monkeypatch):
    """调用成功但 JSON 解析失败 → 台账必须有 patch_unusable/parse_failed（不是零改善）."""
    _h = _run(monkeypatch, "{这不是 JSON")

    assert _h.stub["n"] == 1, "应当且只应发一次修复调用"
    _u = _unusable(_h.diag)
    assert _u, "解析失败没有落结构化记录：%s" % [r.get("kind") for r in _h.diag]
    assert _u[-1]["reason"] == "parse_failed", _u[-1]
    assert _u[-1]["repair_round"] == 1 and _u[-1]["detail"], _u[-1]
    # 与审计侧同一条理由：只留错误文案时，"回了散文"与"JSON 断在中间"事后分不开
    assert _u[-1]["raw_text_head"] == "{这不是 JSON", _u[-1]
    assert _u[-1]["raw_text_len"] == len("{这不是 JSON"), _u[-1]
    assert not _tx(_h.diag), "解析失败不该产生修复事务记录"


def test_invalid_structure_is_recorded_as_unusable(monkeypatch):
    """envelope 结构无效（shot_replacements 不是数组）→ invalid_structure."""
    _h = _run(monkeypatch, '{"shot_replacements": "nope", "hook_replacement": null}')

    _u = _unusable(_h.diag)
    assert _u and _u[-1]["reason"] == "invalid_structure", _u
    assert "str" in _u[-1]["detail"], _u[-1]


def test_empty_patch_is_recorded_as_unusable(monkeypatch):
    """模型返回了合法 envelope 但两个数组都空 → empty_patch（"没东西可应用"）."""
    _h = _run(monkeypatch, '{"shot_replacements": [], "hook_replacement": null}')

    _u = _unusable(_h.diag)
    assert _u and _u[-1]["reason"] == "empty_patch", _u


def test_all_replacements_rejected_is_recorded_as_unusable(monkeypatch):
    """补丁结构合法但**没有一条通过校验** → rejected_by_validation（带被拒原因）."""
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    # shot 2 有告警但**不在本轮点名集合内**（大组独占名额）⇒ 整条被拒 ⇒ n_applied=0
    _payload = ('{"shot_replacements": [{"shot_id": "2", "shot": %s}], '
                '"hook_replacement": null}'
                % __import__("json").dumps(_complete_shot(2), ensure_ascii=False))
    _h = _run(monkeypatch, _payload)

    _u = _unusable(_h.diag)
    assert _u and _u[-1]["reason"] == "rejected_by_validation", _u
    assert "shot 2" in _u[-1]["detail"], _u[-1]
    assert not _tx(_h.diag), "没有任何替换被应用，不该有事务记录"


def test_applied_patch_records_no_unusable(monkeypatch):
    """对照：补丁正常应用时**不得**留下 patch_unusable（避免把成功也报成不可用）."""
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9), warnings_fn=_warnings_fn()).run()

    assert not _unusable(_h.diag), _unusable(_h.diag)
    assert _tx(_h.diag) and _tx(_h.diag)[0]["accepted"], _tx(_h.diag)


# ---------------------------------------------------------------------------
# 上限与余量口径
# ---------------------------------------------------------------------------
def test_repair_cap_defaults_to_none_and_env_only_narrows(monkeypatch):
    """默认**不传**上限（跟随通道全局值）；`REPAIR_MAX_OUTPUT_TOKENS` 只作显式收窄."""
    monkeypatch.delenv("REPAIR_MAX_OUTPUT_TOKENS", raising=False)
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9), warnings_fn=_warnings_fn()).run()
    assert _h.reqs, "没有发出修复请求"
    assert _h.reqs[-1].max_tokens is None, _h.reqs[-1].max_tokens
    _ok = [r for r in _h.diag if r.get("kind") == "call_ok"]
    assert _ok and _ok[-1]["max_tokens"] is None, _ok

    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "20000")
    _h2 = _Harness(monkeypatch, audit_issues=[_group_issue()],
                   indexes=(4, 5, 7, 8, 9), warnings_fn=_warnings_fn()).run()
    assert _h2.reqs[-1].max_tokens == 20000, _h2.reqs[-1].max_tokens
    _ok2 = [r for r in _h2.diag if r.get("kind") == "call_ok"]
    assert _ok2 and _ok2[-1]["max_tokens"] == 20000, _ok2


def test_cap_margin_counts_output_tokens_only():
    """余量 = `max_tokens - output_tokens`；`output_tokens` **已含**思维链，不再减它."""
    import step01_script_gen.main as m

    diag: list = []
    m._record_repair_call_usage(diag, 1,
                                usage={"output_tokens": 8172, "reasoning_tokens": 3695},
                                max_tokens=16000)
    _r = diag[0]
    assert _r["max_tokens"] == 16000 and _r["output_tokens"] == 8172, _r
    assert m._repair_cap_margin(_r) == 7828, "余量口径只看 output_tokens"
    assert m._repair_cap_margin(_r) != 16000 - 8172 - 3695, (
        "重复扣思维链（4133）正是 2026-09-26 更正掉的错算")

    # `completion_tokens` 别名（非 Responses 通道）
    assert m._repair_cap_margin({"max_tokens": 100, "completion_tokens": 40}) == 60
    # 数不全 → 未知，不得瞎猜
    assert m._repair_cap_margin({"max_tokens": None, "output_tokens": 10}) is None
    assert m._repair_cap_margin({}) is None


# ---------------------------------------------------------------------------
# 上限校验（只许收窄）与服务初始化失败
# ---------------------------------------------------------------------------
class _Log:
    def __init__(self):
        self.lines: list = []

    def info(self, msg, *a, **k):
        self.lines.append(str(msg))

    def warn(self, msg, *a, **k):
        self.lines.append(str(msg))

    def error(self, msg, *a, **k):
        self.lines.append(str(msg))

    def verbose(self, *a, **k):
        pass

    def console(self, *a, **k):
        pass


def test_repair_cap_rejects_non_positive_and_widening_values(monkeypatch):
    """`REPAIR_MAX_OUTPUT_TOKENS` **只用于收窄**：0 / 负数 / 高于通道上限都不接受.

    用户 2026-09-26 复核：原实现接受任何整数——`0` 与负数是非法上限，
    高于通道默认值则会**放宽**上限，与"只用于收窄"的说明矛盾。
    """
    import step01_script_gen.main as m

    _log = _Log()
    monkeypatch.setattr(m, "_logger", _log)

    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "0")
    assert m._repair_max_output_tokens() is None, "0 不是合法上限"
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "-5")
    assert m._repair_max_output_tokens() is None, "负数不是合法上限"
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", "abc")
    assert m._repair_max_output_tokens() is None, "非整数不是合法上限"
    assert len(_log.lines) == 3, _log.lines

    _chan = m._channel_text_max_tokens()
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", str((_chan or 1000) + 1000))
    assert m._repair_max_output_tokens() is None, (
        "高于通道项目上限（%s）的值会放宽上限，必须拒绝" % _chan)
    assert any("只用于收窄" in l for l in _log.lines), _log.lines

    _narrow = max(1, (_chan or 1000) - 1000)
    monkeypatch.setenv("REPAIR_MAX_OUTPUT_TOKENS", str(_narrow))
    assert m._repair_max_output_tokens() == _narrow, "合法的收窄值必须生效"


def test_service_init_failure_is_recorded_as_call_failed(monkeypatch):
    """服务工厂抛异常也要落 `call_failed`（否则异常冒泡、提交层没有归因）."""
    _h = _Harness(monkeypatch, audit_issues=[_group_issue()],
                  indexes=(4, 5, 7, 8, 9), warnings_fn=_warnings_fn())

    def _boom(*a, **k):
        raise RuntimeError("DEEPSEEK_API_KEY 缺失")

    monkeypatch.setattr(_h.m, "create_service", _boom)
    _h.run()

    _cf = [r for r in _h.diag if r.get("kind") == "call_failed"]
    assert _cf, "服务初始化失败没有落 call_failed：%s" % [r.get("kind") for r in _h.diag]
    assert "服务初始化失败" in _cf[-1]["error"], _cf[-1]
    assert "DEEPSEEK_API_KEY" in _cf[-1]["error"], "要带出原始原因"
    assert not _tx(_h.diag), "初始化就失败，不该有修复事务记录"


# ---------------------------------------------------------------------------
# v6.152（D2）：保留「输出 token 余量」，**另加**「总预算剩余秒数」
# ---------------------------------------------------------------------------


def test_usage_record_keeps_margin_and_adds_total_budget_left(monkeypatch, capsys):
    """两个口径都要在：余量（离 token 上限多近）与总预算剩余（本跑还剩多少秒）.

    2026-09-29 复核：原来的「余量 未知」是 `max_tokens - output_tokens` 的**输出
    token 余量**，不是时间预算，保留不动；时间预算另加一个字段。
    """
    import time as _t

    import step01_script_gen.main as m

    monkeypatch.setenv("STEP01_TOTAL_BUDGET_SEC", "900")
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _t.time() - 100.0)

    diag: list = []
    m._record_repair_call_usage(diag, 1,
                                usage={"output_tokens": 8172, "reasoning_tokens": 3695},
                                max_tokens=16000)
    rec = diag[0]
    assert abs(rec["budget_left_sec"] - 800.0) < 10, rec["budget_left_sec"]
    assert m._repair_cap_margin(rec) == 7828, "原字段口径不得变"

    out = capsys.readouterr().out
    assert "余量 7828" in out, out
    assert "总预算剩余" in out, out


def test_usage_record_budget_left_unknown_without_run_start(monkeypatch):
    """未 start → 写 `None`（未知就写未知，不瞎猜一个数字）."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "_STEP_TIMING_T0", None)
    diag: list = []
    m._record_repair_call_usage(diag, 1, usage={"output_tokens": 10}, max_tokens=100)
    assert diag[0]["budget_left_sec"] is None


# ---------------------------------------------------------------------------
# 配对表长度类拒绝 → 带反馈重试（2026-11-15，run 20261115T134330 实测）
# ---------------------------------------------------------------------------
# 实测：提交点修复轮，模型整表替换 frame_layouts 时 kfs 与 keyframes 数目对不上，
# 三个镜头全部 rejected_by_validation → 一轮即 BLOCK（此前该拒因不重试）。
# 长度类是模型可自行纠正的格式错误 ⇒ 导出带反馈的重试状态（v6.137 通道复用）；
# 其余拒因（越权等系统性问题）保持原停止语义。
from step01_script_gen.quality_issues import Issue  # noqa: E402
from tests.step01_script_gen.test_repair_targets import _complete_shot  # noqa: E402


def _gaze_issue() -> Issue:
    """授权面 = `frame_layouts` 的单镜问题（`gaze_state_conflict` 的映射表授权）."""
    return Issue(code="model_audit_gaze_state_conflict", severity="visual_risk",
                 message="视线状态矛盾", shot_id="4", frame_id="ff", entity_id="",
                 field_path="frame_layouts", evidence="ff 与 lf 视线冲突",
                 repair_targets=[{"shot_id": "4", "field_path": "frame_layouts"}])


def _length_mismatch_replacement(index: int) -> dict:
    """整表替换 frame_layouts 但 kfs 只有 2 项（keyframes 有 3 项）——真实违约形状."""
    _shot = _complete_shot(index)
    _shot["frame_layouts"]["kfs"] = _shot["frame_layouts"]["kfs"][:2]
    return {"shot_id": str(index), "shot": _shot}


def test_all_length_mismatch_rejections_export_feedback_retry(monkeypatch):
    """全量被拒且拒因全是配对表长度类 ⇒ 导出带反馈的重试状态（脚本回到基线）."""
    _h = _Harness(monkeypatch, audit_issues=[_gaze_issue()], indexes=(4,),
                  warnings_fn=_warnings_fn(),
                  replacements=[_length_mismatch_replacement(4)])
    _rs: dict = {}
    _out = _h.m._repair_shot_warnings(
        _h.script, "novel", audit_issues=_h.audit_issues,
        max_rounds=1, repair_diag=_h.diag, block_reasons=_h.block,
        retry_state=_rs)
    _u = _unusable(_h.diag)
    assert _u and _u[-1]["reason"] == "rejected_by_validation", _h.diag
    assert "长度" in str(_u[-1]["detail"]), _u[-1]["detail"]
    assert "feedback" in _rs, _rs
    assert "keyframe_additions" in _rs["feedback"] and "同序对应" in _rs["feedback"], _rs
    from shared.quality_gate import compute_script_revision
    assert (compute_script_revision(_out)
            == compute_script_revision(_h.script)), "被拒补丁不许改稿"


def test_mixed_rejection_classes_keep_stop_semantics(monkeypatch):
    """混合拒因（长度类 + 越权字段）⇒ **不**导出反馈重试（v6.137 停止语义不变）."""
    _over = _complete_shot(5)
    _over["scene"] = "越权改动"          # shot 5 只授权 prompt → 越权拒
    _h = _Harness(monkeypatch,
                  audit_issues=[_gaze_issue(), _group_issue()],
                  indexes=(4, 5), warnings_fn=_warnings_fn(),
                  replacements=[_length_mismatch_replacement(4),
                                {"shot_id": "5", "shot": _over}])
    _rs: dict = {}
    _h.m._repair_shot_warnings(
        _h.script, "novel", audit_issues=_h.audit_issues,
        max_rounds=1, repair_diag=_h.diag, block_reasons=_h.block,
        retry_state=_rs)
    _u = _unusable(_h.diag)
    assert _u and _u[-1]["reason"] == "rejected_by_validation", _h.diag
    assert "feedback" not in _rs, _rs


def test_assembly_length_message_carries_counts_and_remediation():
    """装配校验的拒收信息必须带**两表计数**与修法指引（进反馈才可执行）."""
    import step01_script_gen.main as _m
    _target = _complete_shot(5)
    _target["frame_layouts"]["kfs"] = _target["frame_layouts"]["kfs"][:2]
    _add = [{"frame": {"t_sec": 5.5, "stage": "exit", "desc": "收尾动作"},
             "layout": [{"name": "张十三", "on_screen": True, "pose": "收尾"}]}]
    _new, _err = _m._assemble_keyframe_additions(_target, _add)
    assert _new is None and _err, _err
    assert "4 项" in _err and "3 项" in _err, _err
    assert "keyframe_additions" in _err and "同序对应" in _err, _err
