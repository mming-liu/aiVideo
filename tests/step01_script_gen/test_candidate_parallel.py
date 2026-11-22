# -*- coding: utf-8 -*-
"""v6.65 多候选并行生成（_generate_script_candidates）单元测试."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# v6.131（2026-09-25 收口）：生成前计划门挂在**候选扇出前**（`_generate_script_candidates`
# 入口），缺计划/计划不可行即停。因此凡是要驱动候选扇出机制的用例，都必须给它一份
# **真实可验算**的计划（节拍 3 个/下限 9s、对白 1 句逐字来自下面的原文 → 可行）。
_SOURCE = "老陈说：这船今日不出港。舱里还有两箱货，天黑前必须走。"
from step01_script_gen import plan_feasibility as pf

_PLAN = {"main_events": ["老陈说船不出港", "舱里还有两箱货", "天黑前必须走"],
         "climax": "老陈说船不出港",
         "causal_chain": ["老陈说船不出港→舱里还有两箱货→天黑前必须走"],
         "required_facts": ["老陈说船今日不出港｜原文证据：“老陈说：这船今日不出港。”"
                            "｜承载：B1/台词1"],
         "shot_beats": ["节拍：老陈说船今日不出港｜本镜信息目的：交代｜原文依据：“老陈说："
                        "这船今日不出港。”｜载体：台词1",
                        "节拍：舱里还有两箱货｜本镜信息目的：交代｜原文依据：“舱里还有两箱货”"
                        "｜载体：画面",
                        "节拍：天黑前必须走｜本镜信息目的：推进｜原文依据：“天黑前必须走”"
                        "｜载体：画面"],
         "dialogue_lines": ["这船今日不出港"],
         "narration_lines": [], "hook_spoken": [], "hook_carrier": ""}

#: 计划身份（`plan_id`）与逐镜 `plan_refs`：**承载契约下的新生成稿必须声明**
#: （v2 起：带「载体：/承载：」的计划要求逐镜履约声明，否则候选层事件契约整稿拒收）。
#: 夹具因此要给 mock 稿补上——与本文件既有先例（夹具带真实计划、mock 稿带计划台词）同源。
_PLAN_ID = pf.plan_identity(_PLAN)


def _refs(fn):
    """把 mock 的解析结果补成**契约完整**的新稿（`plan_id` + 逐镜 `plan_refs`）.

    节拍声明按镜头序**非降**分配：第 k 镜声明第 k 个节拍，之后的镜头都声明最后一条
    （覆盖每个节拍、又不制造顺序回退）；台词只在首镜声明一次。
    """
    _seq = list(range(1, len(_PLAN["shot_beats"]) + 1)) or [1]

    def _w(raw, *a, **k):
        k.pop("plan", None)
        _s = fn(raw, *a, **k)
        if not isinstance(_s, dict):
            return _s
        _s.setdefault("plan_id", _PLAN_ID)
        _shots = [x for x in (_s.get("shots") or []) if isinstance(x, dict)]
        for _i, _sh in enumerate(_shots):
            if len(_shots) == 1:
                _sh.setdefault("plan_refs", {"beats": list(_seq), "lines": [1]})
            else:
                _b = _seq[min(_i, len(_seq) - 1)]
                _sh.setdefault("plan_refs", {"beats": [_b], "lines": [1] if _i == 0 else []})
        return _s
    return _w


#: 计划门会**收窄** `dialogue_lines`（核验通过的那一组），上面那句在原文里逐字存在


def _setup(monkeypatch, scores, fail_idx=frozenset(), delay=0.0):
    """mock gen/parse/score；scores[i] = 第 idx 个候选的分数."""
    import step01_script_gen.main as m
    calls = {"idx": [], "start": [], "end": []}

    def fake_gen(novel_text, bank=None, objects=None, outline=None, temperature=0.2, model=None, timeout_sec=None):
        idx = len(calls["idx"])
        calls["idx"].append(idx)
        calls["start"].append(time.monotonic())
        if delay:
            time.sleep(delay)
        calls["end"].append(time.monotonic())
        if idx in fail_idx:
            raise RuntimeError("mock LLM 失败")
        return f"raw-{idx}"

    def fake_parse(raw, novel_text=""):
        # v6.135：mock 稿必须带计划台词（台词完整性硬门在候选层逐候选核验，
        #        空稿会被判破损——与本文件 v6.131「夹具带真实计划」同一先例）
        return {"shots": [{"index": 1, "narration_segment": "老陈说：这船今日不出港",
                            "ff_desc": "f", "lf_desc": "l", "prompt": "p",
                            "duration_sec": 3.0}],
                "mock_from": raw}

    def fake_score(script, novel_text):
        idx = int(script["mock_from"].split("-")[1])
        return {"total": scores[idx] if idx < len(scores) else 0, "detail": f"mock{idx}"}

    monkeypatch.setattr(m, "gen_spy_script", fake_gen)
    monkeypatch.setattr(m, "parse_response", _refs(fake_parse))
    monkeypatch.setattr(m, "_score_script", fake_score)
    # v6.84（发现 1）：假摘要必须带上 `structural_ok`——缺这一档会被 `_quality_rank`
    # 当作 unknown（最差档），候选选择会判「结构不合法」并重取。
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda script, novel_text="": {
        "quality_checked": True, "blocking_risk_count": 0,
        "integrity_ok": True, "integrity_issues": [],
        "structural_ok": True, "structural_issues": [],
    })
    return calls


def test_parallel_topk3_all_success(monkeypatch):
    """TOPK=3 → 3 候选并行完成，按分数降序返回."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    calls = _setup(monkeypatch, scores=[50, 80, 65], delay=0.2)
    t0 = time.monotonic()
    cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
    elapsed = time.monotonic() - t0

    assert [c[0]["total"] for c in cands] == [80, 65, 50]  # 分数降序
    assert sorted(calls["idx"]) == [0, 1, 2]
    # 并行验证：总耗时远小于串行（3×0.2s 串行=0.6s+，并行≈0.2s+）
    assert elapsed < 0.55


def test_parallel_retry_on_failure(monkeypatch):
    """第 1 个候选失败 → 自动补发，最终凑满 3 个."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    # 第 0 个失败；第 1/2/3 号成功
    calls = _setup(monkeypatch, scores=[0, 60, 70, 80], fail_idx={0})
    cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
    assert len(cands) == 3
    assert sorted(calls["idx"]) == [0, 1, 2, 3]  # 3 成功 + 1 次失败重试
    assert [c[0]["total"] for c in cands] == [80, 70, 60]


def test_serial_topk1_zero_change(monkeypatch):
    """TOPK=1 → 只调 1 次，串行语义."""
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    calls = _setup(monkeypatch, scores=[90])
    cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
    assert len(cands) == 1
    assert calls["idx"] == [0]


def test_all_fail_raises(monkeypatch):
    """全部失败 → RuntimeError（max_attempts 用尽）."""
    import step01_script_gen.main as m
    import shared.config as cfg
    import pytest
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)
    from shared.constants import SCRIPT_PARSE_RETRIES
    all_idx = frozenset(range(SCRIPT_PARSE_RETRIES * 3))
    calls = _setup(monkeypatch, scores=[], fail_idx=all_idx)
    with pytest.raises(RuntimeError):
        m._generate_script_candidates(_SOURCE, None, None, _PLAN)


# ============================================================================
# v6.70.2：候选高分早停（09-14 实测 100 分候选 64s 完成，仍被 600s 慢候选拖住）
# ============================================================================


def _script_with_shots(n_shots: int = 14):
    # v6.135：mock 镜头带计划台词（台词完整性硬门逐候选核验；首镜承载台词即可，
    # 其余镜纯画面）——与本文件 v6.131「夹具带真实可验算计划」同一先例
    _shots = []
    for _i in range(n_shots):
        _s = {"duration_sec": 90.0 / n_shots}
        if _i == 0:
            _s["narration_segment"] = "老陈说：这船今日不出港"
        _shots.append(_s)
    return {
        "shots": _shots,
        "hook": {"duration_sec": 5.0},
    }


def test_candidate_early_stop_skips_slow_candidates(monkeypatch):
    """高分候选完成时不再等待同批其他候选（as_completed 顺序不确定，允许已完成的先返回）.

    v6.103（总计划阶段 1.2）：**不再用「函数返回瞬间三个 worker 都已进入函数体」
    作断言**——那是线程调度事实，不是契约（全量测试偶发 1 failed）。改为：
      - 用 `Barrier(3)` 让三个 worker **必须都进入** `fake_gen`（executor 确实
        提交了 3 个任务，等价于「收到 3 个 submit」）；
      - 用 `Event` 让慢候选停在函数体里，直到测试主动放行；
      - 断言调用方**没有等待**慢 future（返回耗时 < 慢候选放行时间）。
    """
    import threading
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)

    entered = threading.Barrier(3, timeout=10)
    release_slow = threading.Event()
    counter = {"n": 0}
    counter_lock = threading.Lock()

    def fake_gen(novel_text, bank=None, objects=None, outline=None, temperature=0.2, model=None, timeout_sec=None):
        with counter_lock:
            idx = counter["n"]
            counter["n"] += 1
        entered.wait()               # 三个 worker 都进函数体后才继续
        if idx != 0:
            release_slow.wait(10.0)  # 慢候选：卡在这里，等测试放行
        return f"raw-{idx}"

    def fake_parse(raw, novel_text=""):
        idx = int(raw.split("-")[1])
        script = (
            _script_with_shots(20)
            if idx == 0 else
            {"shots": [{"index": 1, "narration_segment": "老陈说：这船今日不出港",
                             "duration_sec": 3.0}], "hook": None}
        )
        script["_mock_idx"] = idx
        return script

    def fake_score(script, novel_text):
        idx = int(script["_mock_idx"])
        return {
            "total": 100 if idx == 0 else 50,
            "climax_complete": idx == 0,
            "detail": f"mock{idx}",
        }

    monkeypatch.setattr(m, "gen_spy_script", fake_gen)
    monkeypatch.setattr(m, "parse_response", _refs(fake_parse))
    monkeypatch.setattr(m, "_score_script", fake_score)
    # v6.84（发现 1）：假摘要必须带上 `structural_ok`——缺这一档会被 `_quality_rank`
    # 当作 unknown（最差档），候选选择会判「结构不合法」并重取。
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda script, novel_text="": {
        "quality_checked": True, "blocking_risk_count": 0,
        "integrity_ok": True, "integrity_issues": [],
        "structural_ok": True, "structural_issues": [],
    })

    try:
        t0 = time.monotonic()
        cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
        elapsed = time.monotonic() - t0
        # executor 确实收到 3 个 submit（三个 worker 全部进过函数体）
        assert counter["n"] == 3
        assert cands[0][0]["total"] == 100
        assert len(cands) <= 3
        # 早停后调用方没有等待慢 future：慢候选仍在 release_slow 上阻塞
        assert not release_slow.is_set()
        assert elapsed < 5.0, f"早停未生效：等了 {elapsed:.1f}s"
    finally:
        release_slow.set()  # 放行孤儿线程，避免测试退出时留下阻塞线程


def test_candidate_early_stop_needs_structure(monkeypatch):
    """分数够但时长超 90s → 不早停."""
    import step01_script_gen.main as m
    script = {"shots": [{"duration_sec": 7.0} for _ in range(15)], "hook": {}}
    assert m._candidate_is_early_stop(
        {"total": 100, "climax_complete": True}, script
    ) is False


def test_candidate_early_stop_disabled(monkeypatch):
    """CANDIDATE_EARLY_STOP=0 → 回退旧行为，不早停."""
    import step01_script_gen.main as m
    monkeypatch.setenv("CANDIDATE_EARLY_STOP", "0")
    script = {"shots": [{"duration_sec": 5.0} for _ in range(14)], "hook": {}}
    assert m._candidate_is_early_stop(
        {"total": 100, "climax_complete": True}, script
    ) is False


def test_candidate_early_stop_accepts_twenty_shots(monkeypatch):
    """100 分、20 镜、90s、高潮完整 → 允许早停（09-15 实测）。"""
    import step01_script_gen.main as m
    script = {
        "shots": [{"duration_sec": 4.5} for _ in range(20)],
        "hook": {"duration_sec": 5.0},
    }
    assert m._candidate_is_early_stop(
        {"total": 100, "climax_complete": True}, script
    ) is True


def test_candidate_early_stop_rejects_twenty_one_shots(monkeypatch):
    """21 镜仍不放行，避免用早停掩盖镜头膨胀。"""
    import step01_script_gen.main as m
    script = {
        "shots": [{"duration_sec": 4.0} for _ in range(21)],
        "hook": {"duration_sec": 5.0},
    }
    assert m._candidate_is_early_stop(
        {"total": 100, "climax_complete": True}, script
    ) is False


def test_early_stop_actually_saves_time(monkeypatch):
    """v6.70.3 回归：早停必须真的不等慢线程.

    09-14 事故形态：早停 break 后 with 块退出仍 shutdown(wait=True)，被
    600s 慢候选拖住——100 分候选 64s 到手、总耗时 17m38s。此处慢候选
    sleep 3s，若仍串行等待总耗时必 >3s；shutdown(wait=False) 应 <1.5s。

    v6.103（总计划阶段 1.2）：慢候选用 `Barrier` + `Event` 控住，且**先让三个
    worker 都进入函数体**再放行快候选——「哪几个 worker 已经在跑」不再依赖
    线程调度（旧版靠 `fake_gen.calls` 计数，全量运行时偶发失败）。
    """
    import threading
    import step01_script_gen.main as m
    import shared.config as cfg
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 3)

    entered = threading.Barrier(3, timeout=10)
    release_slow = threading.Event()
    counter = {"n": 0}
    counter_lock = threading.Lock()

    def fake_gen(novel_text, bank=None, objects=None, outline=None,
                 temperature=0.2, model=None, timeout_sec=None):
        with counter_lock:
            idx_n = counter["n"]
            counter["n"] += 1
        entered.wait()               # 三个 worker 都进函数体后才继续
        if idx_n != 0:
            release_slow.wait(10.0)  # 慢候选：卡住直到测试放行
        return f"raw-{idx_n}"

    def fake_parse(raw, novel_text=""):
        return (
            _script_with_shots(20)
            if raw == "raw-0" else
            {"shots": [{"index": 1, "narration_segment": "老陈说：这船今日不出港",
                             "duration_sec": 3.0}], "hook": None}
        )

    def fake_score(script, novel_text):
        return {
            "total": 100 if script.get("shots") else 50,
            "climax_complete": bool(script.get("shots")),
            "detail": "mock",
        }

    monkeypatch.setattr(m, "gen_spy_script", fake_gen)
    monkeypatch.setattr(m, "parse_response", _refs(fake_parse))
    monkeypatch.setattr(m, "_score_script", fake_score)
    # v6.84（发现 1）：假摘要必须带上 `structural_ok`——缺这一档会被 `_quality_rank`
    # 当作 unknown（最差档），候选选择会判「结构不合法」并重取。
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda script, novel_text="": {
        "quality_checked": True, "blocking_risk_count": 0,
        "integrity_ok": True, "integrity_issues": [],
        "structural_ok": True, "structural_issues": [],
    })

    import time
    try:
        t0 = time.monotonic()
        cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
        elapsed = time.monotonic() - t0

        assert cands[0][0]["total"] == 100
        # 慢候选仍阻塞在 release_slow 上 → 调用方确实没有等它
        assert not release_slow.is_set()
        assert elapsed < 1.5, f"早停未真正生效：等了 {elapsed:.1f}s 才返回"
    finally:
        release_slow.set()


def test_candidate_timeout_cools_model_for_next_batch(monkeypatch):
    """候选超时后，本 run 不再用同模型补发，改用其他模型."""
    import step01_script_gen.main as m
    import shared.config as cfg

    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 2)
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_MODELS", ["model-a", "model-b"])
    models_seen: list[str] = []

    def fake_gen(novel_text, bank=None, objects=None, outline=None,
                 temperature=0.2, model=None, timeout_sec=None):
        models_seen.append(model)
        if model == "model-a":
            raise RuntimeError("Agent Plan 客户端超时 (300s)")
        return f"raw-{len(models_seen)}"

    def fake_parse(raw, novel_text=""):
        return {
            # v6.135：首镜带计划台词（台词完整性硬门）
            "shots": [{"duration_sec": 5.0,
                       **({"narration_segment": "老陈说：这船今日不出港"}
                          if _i == 0 else {})}
                      for _i in range(12)],
            "hook": {"duration_sec": 5.0},
        }

    def fake_score(script, novel_text):
        return {"total": 80, "climax_complete": False, "detail": "mock"}

    monkeypatch.setattr(m, "gen_spy_script", fake_gen)
    monkeypatch.setattr(m, "parse_response", _refs(fake_parse))
    monkeypatch.setattr(m, "_score_script", fake_score)

    cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
    assert len(cands) == 2
    assert models_seen.count("model-a") == 1
    assert models_seen.count("model-b") == 2


def test_candidate_early_stop_rejects_blocking_risk():
    import step01_script_gen.main as m
    script = _script_with_shots(14)
    score = {
        "total": 100, "climax_complete": True, "quality_checked": True,
        "blocking_risk_count": 1, "integrity_ok": True,
    }
    assert m._candidate_is_early_stop(score, script) is False


def test_candidate_early_stop_rejects_integrity_error():
    import step01_script_gen.main as m
    script = _script_with_shots(14)
    score = {
        "total": 100, "climax_complete": True, "quality_checked": True,
        "blocking_risk_count": 0, "integrity_ok": False,
    }
    assert m._candidate_is_early_stop(score, script) is False


def test_candidate_sort_prefers_clean_90_over_dirty_100():
    import step01_script_gen.main as m
    clean = ({"total": 90, "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True}, {})
    dirty = ({"total": 100, "quality_checked": True, "blocking_risk_count": 1, "integrity_ok": True}, {})
    assert sorted([dirty, clean], key=m._candidate_sort_key)[0] is clean


def test_candidate_early_stop_rejects_gate_block(monkeypatch):
    import step01_script_gen.main as m
    script = _script_with_shots(14)
    score = {
        "total": 100, "final_score": 95, "gate_status": "BLOCK",
        "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True,
        "climax_complete": True,
    }
    assert m._candidate_is_early_stop(score, script) is False


# ===========================================================================
# v6.102（第十六次修复项 #2）：只有「内容超载」的候选应**提前采纳**
#
# 实测（第十二～十五次每轮都一样）：同两条长台词（原文 50 / 59 字）让候选 1/2/3
# 全部结构不合法，每次白烧 3 个候选（各 ~26k token），而第三个候选手上已是 100 分。
# `shot_content_over_cap` 有确定性修复路径（后面的定向拆分），没必要为「结构干净」
# 再抽两次卡；`shot_duration_below_min`（过短）没有确定性路径，仍须拒收重取。
# ===========================================================================

def test_only_over_cap_candidates_are_splittable():
    from step01_script_gen.main import _candidate_only_splittable

    assert _candidate_only_splittable(
        {"structural_codes": ["shot_content_over_cap"]}) is True
    assert _candidate_only_splittable({"structural_codes": []}) is False, \
        "结构干净不需要走这条路（它本来就该被采纳）"
    assert _candidate_only_splittable(
        {"structural_codes": ["shot_content_over_cap", "shot_duration_below_min"]}) is False, \
        "还混着「过短」（无确定性路径）→ 仍应拒收重取"
    assert _candidate_only_splittable(
        {"structural_codes": ["shot_duration_below_min"]}) is False


def test_candidate_summary_exposes_structural_codes():
    """候选采纳按 **code** 分流——摘要必须给出 code（消息文本判不出类型）."""
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from step01_script_gen.main import _candidate_quality_summary

    script = {"shots": [{"index": 1, "duration": 8.0, "duration_sec": 8.0,
                         "narration_segment": "张十三道：" + "你最好想清楚再开口。" * 8,
                         "subtitle": "", "motion_desc": "0-2秒：开口；2-4秒：收势",
                         "prompt": "中景", "ff_desc": "f", "lf_desc": "l",
                         "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}],
              "hook": None, "fps": 24, "characters": []}
    q = _candidate_quality_summary(script, "novel")
    assert "structural_codes" in q
    assert "shot_content_over_cap" in q["structural_codes"], q["structural_codes"]
