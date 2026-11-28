# -*- coding: utf-8 -*-
"""时长收敛路径（发现 11 / 用户决定 B1 + B2，2026-09-21）.

实测：`22:15:48 [DURATION] 正片 103.0s > 90s（尝试 1/1）`，最终正片**实际** 93.3s，
`duration_budget` 一条留到质量门。落盘脚本的**投影**是 111.38s。

两个独立缺陷：

  - **B-1 判定口径**：`duration_over` 量的是 `project_post_split_metrics()` 的
    **拆镜后投影**（触发时 103.0s），而质量门量的是**实际** `Σduration_sec`（93.3s）。
    投影会把超 cap 的镜头按 cap 折算并计入拆镜次数，但拆镜 pre-pass 在生产路径
    **已停用**（`allow_split=False`）——投影描述的是一个**不会发生的剧本**。
  - **B-2 无采纳守卫**：`script = new_script` 解析成功即无条件替换，没有任何质量
    判据；且 `SHOT_COUNT_MAX<=0` 时连强制截断兜底也跳过 → 时长维度**零兜底**。

用户决定：B1 改实际值 + B2 加「严格改善 + 内容完整性」守卫；修复预算**暂时保持 1 次**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _legacy_duration_limit(monkeypatch):
    """本文件验证的是「超过生效上限才收敛」的判定/采纳逻辑，不是现场 120s 配置.

    固定 90s 上限，原有 96s/104s 夹具才继续打到 _regen_over_limit 的触发分支；
    若不固定，夹具全部落在 120s 合法区间，测试会因前提失效而假红。
    """
    from step01_script_gen.orchestrator import regen as _regen

    monkeypatch.setattr(_regen._mrt, "DURATION_MAX_SEC", 90.0, raising=False)


def _frames_for(motion, dur):
    """按时段中点给每段一帧——夹具**帧合规**.

    v6.143（P3）：结构收敛轮把「缺帧」也纳入结构问题集合（`kf_segment_missing`
    是十次跑第一大类阻塞）。本文件测的是**时长/超载**收敛，夹具必须帧合规，
    否则帧问题会混进 `_structural_shot_issues` 的计数、把断言带偏。
    """
    from step01_script_gen.kf_coverage import segments as _segments

    out = []
    for a, b, body in _segments(motion, dur):
        out.append({"t_sec": round((a + b) / 2, 3), "stage": "approach",
                    "desc": str(body or "定格")})
    return out


def _shot(index=1, dur=8.0, motion="0-1秒：抬手"):
    _kfs = _frames_for(motion, dur)
    return {"index": index, "duration_sec": dur, "duration": dur,
            "narration_segment": "", "subtitle": "",
            "motion_desc": motion, "prompt": "中景", "ff_desc": "f", "lf_desc": "l",
            "keyframes": _kfs,
            "frame_layouts": {"ff": [], "lf": [], "kfs": [[] for _ in _kfs]}}


def _script(shots):
    return {"shots": shots, "hook": None, "fps": 24, "characters": [],
            "total_duration": sum(s["duration_sec"] for s in shots)}


def _patch(monkeypatch, new_script, *, integrity_ok=True,
           old_integrity_count=0, new_integrity_count=None):
    """新稿/原稿的完整性异常数要能**分别**指定.

    v6.87 的判据是「**不退化**」（`new <= old`），所以 mock 必须能表达
    「原稿 2 条、新稿 1 条」这种情形——否则测不出这条判据的真实语义。
    用对象同一性区分原稿与新稿（`_regen_over_limit` 传的就是各自的 script 对象）。
    """
    import step01_script_gen.main as m

    calls = {"n": 0}

    class _R:
        success = True
        error = ""
        text = json.dumps(new_script, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            calls["n"] += 1
            return _R()

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "build_regen_feedback", lambda *a, **k: "")
    monkeypatch.setattr(m, "_compute_timecodes", lambda s: None)
    # 返回**同一个对象**：`json.loads(json.dumps(x))` 会产生副本，而下面用对象同一性
    # 区分「原稿 / 新稿」的完整性计数。
    monkeypatch.setattr(m, "parse_response", lambda raw, novel_text="", **_kw: new_script)
    _new_cnt = (0 if integrity_ok else 2) if new_integrity_count is None         else new_integrity_count

    def _summary(s, t=""):
        _c = _new_cnt if s is new_script else old_integrity_count
        return {"integrity_ok": _c == 0, "integrity_count": _c}

    monkeypatch.setattr(m, "_candidate_quality_summary", _summary)
    return calls


# ------------------------------------------------------------- B1：判定口径

def test_b1_actual_duration_helper_ignores_projection():
    """`_actual_shots_total` 只加 `duration_sec`，与拆镜投影无关."""
    from step01_script_gen.main import _actual_shots_total

    script = _script([_shot(1, 8.0), _shot(2, 8.0)])
    assert _actual_shots_total(script) == 16.0
    # 只有 `duration` 没有 `duration_sec` 时按 `duration` 回退（schema 只保证 duration）
    assert _actual_shots_total({"shots": [{"duration": 5.0}]}) == 5.0


def test_b1_triggers_on_actual_duration_even_when_projection_is_small(monkeypatch):
    """**B1 核心回归**：实际 > 90s 必须触发收敛，即使拆镜投影 < 90s.

    构造：12 镜 × 8.0s（实际 96s），但 motion 只写 1 秒 → 投影每镜 ≈1s ≈ 12s。
    旧实现量投影 → 12s ≤ 90s → `SHOT_COUNT_MAX<=0 and not duration_over` 直接
    **提前返回、一次模型调用都不发**，96s 一路走到质量门。
    """
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0, "0-1秒：抬手") for i in range(1, 13)])
    assert m._actual_shots_total(script) == 96.0
    from step01_script_gen.timecode import project_post_split_metrics
    # Authored durations now remain bounds in real projection too. Inject a
    # deliberately small projection to retain this independent trigger test.
    assert float(project_post_split_metrics(script["shots"], fps=24)["duration_sec"]) == 96.0
    monkeypatch.setattr("step01_script_gen.timecode.project_post_split_metrics",
                        lambda *a, **kw: {"duration_sec": 12.0,
                                          "shot_count": 12, "split_count": 0})

    calls = _patch(monkeypatch, _script([_shot(i, 7.0) for i in range(1, 12)]))
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert calls["n"] == 1, "实际 96s > 90s 必须触发收敛（旧口径下不会）"
    assert m._actual_shots_total(out) == 77.0, "严格改善且无退化 → 采纳"


# --------------------------------------------------- B2：严格改善 + 完整性守卫

def test_b2_rejects_regeneration_that_is_not_strictly_better(monkeypatch):
    """重生成**没有严格缩短**实际时长 → 保留原稿.

    旧实现 `script = new_script` 无条件替换：实测把 103.0s 改成 111.38s 也照单全收。
    """
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0) for i in range(1, 13)])          # 96s
    worse = _script([_shot(i, 8.0) for i in range(1, 14)])            # 104s
    calls = _patch(monkeypatch, worse)
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert calls["n"] == 1
    assert out is script, "未严格改善 → 必须保留原稿，不得采纳更差的版本"


def test_b2_adopts_strictly_shorter_draft(monkeypatch):
    """严格变短 → 采纳."""
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0) for i in range(1, 13)])           # 96s
    better = _script([_shot(i, 7.0) for i in range(1, 13)])           # 84s
    _patch(monkeypatch, better)
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert m._actual_shots_total(out) == 84.0, "严格改善的稿子必须被采纳"


def test_b2_refuses_shorter_draft_that_breaks_content_integrity(monkeypatch):
    """变短但引入完整性异常（状态回退/姿态互斥/时长预算等硬正确性问题）→ 保留原稿."""
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0) for i in range(1, 13)])           # 96s
    shorter = _script([_shot(i, 7.0) for i in range(1, 13)])          # 84s
    _patch(monkeypatch, shorter, old_integrity_count=0, new_integrity_count=2)
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert out is script, "完整性异常 0 → 2 条（退化）→ 保留原稿"


def test_b2_budget_stays_one_attempt(monkeypatch):
    """用户决定：修复预算**暂时保持 1 次**——不做「再多试一次看看」."""
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0) for i in range(1, 13)])
    calls = _patch(monkeypatch, _script([_shot(i, 7.5) for i in range(1, 13)]))  # 90s 仍超
    m._regen_over_limit(script, "novel", max_attempts=1)
    assert calls["n"] == 1, "预算 1 次：不得因仍超限而反复重生成"


# ------------------------- B2 完整判据：内容不退化（用户 2026-09-21 文件要求）

def test_b2_refuses_draft_that_increases_structural_overload(monkeypatch):
    """总时长变短但**结构非法镜头数增加** → 保留原稿.

    「压总时长」与「单镜超载」是两个方向：把内容挤进更少的镜头就会让单镜重新超载。
    用户 B2 判据明确要求「结构非法镜头数不增加」。
    """
    import step01_script_gen.main as m

    # 原稿：12 镜 × 8s = 96s，内容需求低（无结构问题）
    script = _script([_shot(i, 8.0, "0-1秒：抬手") for i in range(1, 13)])
    assert m._structural_shot_issues(script) == []
    # 新稿：10 镜 × 8s = 80s（严格变短），但每镜台词念不完 → 全部超 cap
    long_narr = "张十三道：" + "你最好想清楚再开口。" * 4
    overloaded = {"shots": [dict(_shot(i, 8.0), narration_segment=long_narr,
                                 subtitle=long_narr) for i in range(1, 11)],
                  "hook": None, "fps": 24, "characters": [], "total_duration": 80.0}
    assert m._structural_shot_issues(overloaded), "fixture 必须真的超 cap"

    _patch(monkeypatch, overloaded)
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert out is script, "结构非法镜头数增加 → 保留原稿"


def test_b2_refuses_draft_that_loses_source_coverage(monkeypatch):
    """总时长变短但**原文覆盖退化** → 保留原稿（缩短不得用「删内容」换）."""
    import step01_script_gen.main as m

    novel = "张十三立在船头，听香跪坐船舷。"
    old_shots = [dict(_shot(i, 8.0, "0-1秒：抬手"), core_keywords=["船头"],
                      prompt="中景，张十三在船头") for i in range(1, 13)]
    script = {"shots": old_shots, "hook": None, "fps": 24, "characters": [],
              "total_duration": 96.0}
    from step01_script_gen.gap_analysis import calculate_keyword_coverage
    assert calculate_keyword_coverage(old_shots[0], novel)["covered"] == 1

    new_shots = [dict(_shot(i, 8.0, "0-1秒：抬手"), core_keywords=["船头"],
                      prompt="中景，空镜") for i in range(1, 11)]
    assert calculate_keyword_coverage(new_shots[0], novel)["covered"] == 0

    _patch(monkeypatch, {"shots": new_shots, "hook": None, "fps": 24,
                         "characters": [], "total_duration": 80.0})
    out = m._regen_over_limit(script, novel, max_attempts=1)
    assert out is script, "原文覆盖退化 → 保留原稿"


def test_b2_refuses_draft_that_breaks_grounding(monkeypatch):
    """总时长变短但 **grounding 退化**（narration 在原文里定位不到）→ 保留原稿."""
    import step01_script_gen.main as m

    novel = "张十三立在船头道：你最好想清楚再开口。"
    grounded_narr = "张十三道：你最好想清楚再开口。"
    script = {"shots": [dict(_shot(i, 8.0, "0-1秒：抬手"),
                             narration_segment=grounded_narr, subtitle=grounded_narr)
                        for i in range(1, 13)],
              "hook": None, "fps": 24, "characters": [], "total_duration": 96.0}
    from step01_script_gen.validation import _check_grounding
    assert _check_grounding(script["shots"][0], novel) is True

    ungrounded_narr = "张十三道：今日风浪太大不宜开船。"
    assert _check_grounding({"narration_segment": ungrounded_narr}, novel) is False
    new_shots = [dict(_shot(i, 8.0, "0-1秒：抬手"),
                      narration_segment=ungrounded_narr, subtitle=ungrounded_narr)
                 for i in range(1, 11)]

    _patch(monkeypatch, {"shots": new_shots, "hook": None, "fps": 24,
                         "characters": [], "total_duration": 80.0})
    out = m._regen_over_limit(script, novel, max_attempts=1)
    assert out is script, "grounding 退化 → 保留原稿"


def test_b2_adopts_shorter_draft_when_nothing_degrades(monkeypatch):
    """三条退化判据都不触发时，严格变短的稿子必须采纳（守卫不得误杀）."""
    import step01_script_gen.main as m

    novel = "张十三立在船头道：你最好想清楚再开口。"
    narr = "张十三道：你最好想清楚再开口。"
    mk = lambda n, dur: {"shots": [dict(_shot(i, dur, "0-1秒：抬手"),
                                        narration_segment=narr, subtitle=narr,
                                        core_keywords=["船头"],
                                        prompt="中景，张十三在船头")
                                 for i in range(1, n + 1)],
                         "hook": None, "fps": 24, "characters": [],
                         "total_duration": float(n) * dur}
    script = mk(12, 8.0)     # 96s
    _patch(monkeypatch, mk(12, 7.0))   # 84s
    out = m._regen_over_limit(script, novel, max_attempts=1)
    assert m._actual_shots_total(out) == 84.0


def test_b2_allows_legitimate_merge_that_reduces_shot_count(monkeypatch):
    """**合法合并**（镜头数减少）不得被退化守卫误拒——比率口径存在的理由.

    计数口径会把「12 镜 → 6 镜」直接判成 grounding 退化（12 → 6 镜），**而合并
    正是时长收敛的正常手段**；那样这条守卫就变成新的「永不收敛」缺口，正是本次
    要修的那类 bug。所以两条度量都用比率（镜头数无关），问的才是真问题：
    同样的内容，覆盖/grounding 有没有变差。
    """
    import step01_script_gen.main as m

    novel = "张十三立在船头道：你最好想清楚再开口。"
    narr = "张十三道：你最好想清楚再开口。"

    def _mk(count_with_narr, count_without, dur):
        shots = [dict(_shot(i, dur, "0-1秒：抬手"), narration_segment=narr,
                      subtitle=narr) for i in range(1, count_with_narr + 1)]
        shots += [_shot(i, dur, "0-1秒：抬手")
                  for i in range(count_with_narr + 1, count_with_narr + count_without + 1)]
        return {"shots": shots, "hook": None, "fps": 24, "characters": [],
                "total_duration": float(len(shots)) * dur}

    script = _mk(10, 2, 8.0)      # 12 镜 / 96s，有台词镜 10 个全部 grounded
    merged = _mk(5, 1, 8.0)       # 6 镜 / 48s，有台词镜 5 个同样 grounded
    _patch(monkeypatch, merged)
    out = m._regen_over_limit(script, novel, max_attempts=1)
    assert out is merged or m._actual_shots_total(out) == 48.0, \
        "镜头数减少是合法合并，grounding 比率未退化 → 必须采纳"


def test_content_degradation_uses_rates_not_counts():
    """直接单测 `_content_degradation`：镜头数减半但每镜内容不变 → 不退化."""
    from step01_script_gen.main import _content_degradation

    novel = "张十三立在船头道：你最好想清楚再开口。"
    narr = "张十三道：你最好想清楚再开口。"
    kw = {"core_keywords": ["船头"], "prompt": "中景，张十三在船头"}

    def _mk(n, dur):
        return {"shots": [dict(_shot(i, dur, "0-1秒：抬手"), narration_segment=narr,
                               subtitle=narr, **kw) for i in range(1, n + 1)],
                "hook": None, "fps": 24, "characters": [], "total_duration": float(n) * dur}

    assert _content_degradation(_mk(12, 8.0), _mk(6, 8.0), novel) == [], \
        "镜头数减少不是内容退化（比率口径）"
    # 真的丢了 grounding → 必须报
    bad = _mk(6, 8.0)
    bad["shots"][0]["narration_segment"] = "张十三道：今日风浪太大不宜开船。"
    assert any("grounding" in r for r in _content_degradation(_mk(12, 8.0), bad, novel))


# --------------------- v6.87：完整性判据是「不退化」而不是「必须干净」

def test_integrity_not_degraded_semantics():
    """`_integrity_not_degraded` 的四种情形.

    2026-09-21 23:17 实测：结构轮 5 → 4 的改善稿被拒，日志给的两个原因是
    `正片总时长 92.2s > 90s` 与 `同帧出现刀尖但没有明确持刀者`——**而原稿本身
    就带完整性异常**（三个候选摘要全标「完整性异常」）。布尔判据 `integrity_ok is False`
    因此在原稿不干净时永远为真，采纳分支永不通过 = 没有收敛轮。
    """
    from step01_script_gen.main import _integrity_not_degraded

    # 两边都干净 → 通过
    assert _integrity_not_degraded({"integrity_count": 0}, {"integrity_count": 0})[0]
    # 原稿 2 条、新稿 1 条 → **不退化**，通过（这正是本轮被误拒的情形）
    assert _integrity_not_degraded({"integrity_count": 2}, {"integrity_count": 1})[0]
    # 原稿 2 条、新稿 3 条 → 退化，拒绝
    ok, why = _integrity_not_degraded({"integrity_count": 2}, {"integrity_count": 3})
    assert not ok and "2 → 3" in why
    # 新稿检查器未通过（计数 None）→ **未知不得当成干净**，拒绝
    ok, why = _integrity_not_degraded({"integrity_count": 0}, {"integrity_count": None})
    assert not ok and "无法验证" in why
    # 旧稿无基线（检查器也没通过）→ 只有新稿**明确干净**才放行
    assert _integrity_not_degraded({"integrity_count": None}, {"integrity_count": 0})[0]
    assert not _integrity_not_degraded({"integrity_count": None},
                                       {"integrity_count": 1})[0]
    # 两边都未知 → 拒绝（未知不得当成干净）
    assert not _integrity_not_degraded({"integrity_count": None},
                                       {"integrity_count": None})[0]


def test_b2_adopts_shorter_draft_when_integrity_is_not_degraded(monkeypatch):
    """原稿**本来就有**完整性异常时，新稿带同样多（或更少）的异常不得被拒.

    这是 23:17 实测的形态：原稿不干净 → 布尔判据把任何新稿都判成「引入异常」
    → 采纳分支永不通过。
    """
    import step01_script_gen.main as m

    script = _script([_shot(i, 8.0) for i in range(1, 13)])           # 96s
    shorter = _script([_shot(i, 7.0) for i in range(1, 13)])          # 84s
    _patch(monkeypatch, shorter, old_integrity_count=2, new_integrity_count=2)
    out = m._regen_over_limit(script, "novel", max_attempts=1)
    assert m._actual_shots_total(out) == 84.0, "异常数持平（未退化）→ 必须采纳"
