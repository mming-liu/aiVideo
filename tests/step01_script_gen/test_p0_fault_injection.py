# -*- coding: utf-8 -*-
"""P0 故障注入判别性测试（离线，不调模型）.

两条都对应 2026-10 复核确认的缺陷，各自锚定一个**会静默降级**的路径：

1. `finalize_step01_script` 的环境故障（依赖缺失/语法不兼容）曾被
   `except Exception` 降级成一条 WARN 后继续——而它后面就是提交点，
   于是"约束链没跑完"仍会写出正式产物。这里断言异常**原样上抛**。
2. 致命伤兜底曾把「。伤处白袍暗红洇痕持续可见」直接拼进 `shot["prompt"]`，
   凭空造出模型没写过的状态，并用 `if "杨旭" not in _text` 绑死本作主角。
   这里断言它**只报 WARN、绝不改 prompt**。

判别方式：两条都在"撤掉修复"时转红（见交付记录里的 A/B 读数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _stub_env(monkeypatch, m):
    """把 logger / console / 指标打桩，避免真实 IO 与副作用."""
    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(str(msg))

        def warn(self, msg, *a, **k):
            logs.append(str(msg))

        def error(self, msg, *a, **k):
            logs.append(str(msg))

        def verbose(self, msg, *a, **k):
            pass

        def console(self, msg, *a, **k):
            logs.append(str(msg))

    monkeypatch.setattr(m, "_logger", _Log())
    monkeypatch.setattr(m, "_console", lambda msg, *a, **k: logs.append(str(msg)))
    for _fn in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        monkeypatch.setattr(m, _fn, lambda *a, **k: None)
    return logs


# ---------------------------------------------------------------------------
# ① 环境故障必须原样上抛（不得降级成 WARN 后继续走到提交点）
# ---------------------------------------------------------------------------
def test_finalize_reraises_environment_fault(monkeypatch):
    """`finalize_step01_script` 里 hard_fix 抛 ImportError → 原样上抛.

    反例（修复前）：该处 `except Exception as _hfe` 只打 WARN 就往下走，
    函数正常返回 ⇒ 后面的提交点照常写三份正式产物。判别点就是"是否上抛"。
    """
    import step01_script_gen.main as m
    import step01_script_gen.constraints as cons

    _stub_env(monkeypatch, m)

    def _boom(*a, **k):
        raise ImportError("simulated missing dependency (pydantic>=2)")

    monkeypatch.setattr(cons, "run_hard_fix", _boom, raising=True)

    script = {"shots": [], "hook": None, "characters": [], "fps": 24,
              "total_duration": 0}

    with pytest.raises(ImportError):
        m.finalize_step01_script(script, novel_text="原文")


def test_finalize_does_not_swallow_logic_fault(monkeypatch):
    """同类故障的另一面：NameError 同样不得被吞（同一 fail-closed 判据）."""
    import step01_script_gen.main as m
    import step01_script_gen.constraints as cons

    _stub_env(monkeypatch, m)

    def _boom(*a, **k):
        raise NameError("simulated undefined name in constraint")

    monkeypatch.setattr(cons, "run_hard_fix", _boom, raising=True)
    script = {"shots": [], "hook": None, "characters": [], "fps": 24,
              "total_duration": 0}

    with pytest.raises(NameError):
        m.finalize_step01_script(script, novel_text="原文")


# ---------------------------------------------------------------------------
# ② 伤情兜底只报 WARN，绝不改 shot["prompt"]
# ---------------------------------------------------------------------------
def _injury_block_source() -> str:
    """取出「伤情兜底」那一段（去注释），供结构守卫断言用."""
    src = (Path(_ROOT) / "step01_script_gen" / "main.py").read_text(encoding="utf-8")
    start = src.index("_injury_lay_words = (")
    end = src.index("_effect_ledger.observe_writer(WRITER_INJURY_ANCHOR", start)
    lines = [ln for ln in src[start:end].splitlines() if not ln.lstrip().startswith("#")]
    return chr(10).join(lines)


def test_injury_fallback_is_advisory_only():
    """源码守卫：伤情兜底段**不得**再写 `shot["prompt"]`，也不得绑死本作人名.

    ⚠️ **证据边界（必须随结论一起读）**：这是**结构性守卫**，不是行为验收。
    把 `_repair_shot_warnings` 驱动到该段，要满足它前面十余处早退的前置
    （真实硬违例、点名集合、修复台账…），本轮没有构造出可达的真实入口，
    因此「只 WARN 不写」的**行为**证据仍然缺失（见交付记录）。本守卫能拦的是
    "把注入器改回去"这一类回归，不能替代入口级验收。

    逐条对应 2026-10 撤销的三处缺陷：
      ① 往 prompt 拼「。伤处白袍暗红洇痕持续可见」→ 凭空造状态；
      ② 只有命中硬编码人名「杨旭」才补 → 人名不得作通用判据；
      ③ 与 `INJURY_ANCHOR_TAIL` 是不同字符串 → 两套幂等判据互相看不见、会叠加。
    """
    block = _injury_block_source()
    assert "_inj_candidates" in block, "兜底段不见了？守卫锚点失效，需同步更新"
    # ① 不得再往 shot 字段写：整段不允许出现 prompt 赋值
    assert '["prompt"]' not in block, "伤情兜底不得改写 shot['prompt']"
    assert "伤处白袍" not in block, "不得再写死「白袍」这类服装/伤处模板"
    assert "暗红洇痕持续可见" not in block, "不得再注入旧兜底变体"
    # ② 不得绑死本作人名
    assert "杨旭" not in block, "人名不得成为通用分支的生效条件"
    # ③ 只报告：候选必须被记录（否则等于静默丢弃）
    assert "_inj_candidates.append" in block
    assert "疑似缺伤处描述" in block


def test_injury_fallback_is_advisory_in_a_real_repair_round(monkeypatch):
    """行为级验收：跑**真实**修复轮，伤情兜底只 WARN、绝不往镜头里写伤处模板.

    复用 `test_linked_group_capacity` 的 `_Harness`（LLM/检查器/日志/指标全打桩，
    但 `_repair_shot_warnings` 是真实执行）。构造：
      * 镜 1 的 prompt 内含 `STRONG_INJURY_ANCHORS` 词 → 成为致命伤建立点；
      * 镜 4 的 ff_desc 含「仰卧」且**无**伤处锚词 → 应被判为候选。
    先断言**兜底段确实被执行到**（否则本用例会空过），再断言它只报告不写。
    """
    from tests.step01_script_gen.test_linked_group_capacity import (
        REAL_GROUP, _Harness, _group_issue, _warnings_fn)
    from tests.step01_script_gen.test_repair_targets import _complete_shot
    from step01_script_gen.prompt_semantic.registry import STRONG_INJURY_ANCHORS

    _anchor = STRONG_INJURY_ANCHORS[0]
    _shots = [_complete_shot(int(i)) for i in ("1", "2") + REAL_GROUP]
    # 镜 1 = 致命伤建立点（build_idx），且**不是**兜底要处理的目标镜
    _shots[0]["prompt"] = "中景，沈砚心口挨了一刀，%s" % _anchor

    _h = _Harness(monkeypatch, audit_issues=[_group_issue()], indexes=REAL_GROUP,
                  warnings_fn=_warnings_fn(),
                  script={"shots": _shots, "hook": None, "fps": 24,
                          "total_duration": 36})
    _h.run()

    # ① 可达性：兜底段确实跑到（否则下面两条断言毫无意义）
    assert any("疑似缺伤处描述" in str(l) for l in _h.logs), _h.logs[-12:]

    # ② 只报告不写：任何镜头的任何正文字段都不得出现伤处模板。
    # 注意必须断言**函数返回值**（`_h.out`）：`_repair_shot_warnings` 内部会把
    # `script` 重新绑定成合并/后处理后的副本，写在那份副本上不会回写到传入对象，
    # 只查 `_h.script` 会漏掉注入（本轮实测：注入器恢复后该断言仍为绿）。
    _out_shots = (_h.out or {}).get("shots") or []
    assert _out_shots, "修复轮没有返回稿件，用例无法验收"
    for _s in _out_shots:
        for _f in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            _v = str(_s.get(_f) or "")
            assert "伤处白袍" not in _v, "伤情兜底仍在写「白袍」伤处模板: %r" % _v
            assert "暗红洇痕持续可见" not in _v, "仍在写旧兜底变体: %r" % _v

