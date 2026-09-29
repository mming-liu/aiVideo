# -*- coding: utf-8 -*-
"""P4.6 跨 run 保优护栏（`_cross_run_best_guard`）单元测试.

v6.103（总计划阶段 4.1）：护栏**只选择候选**，不再落盘、不再 finalize、不再跑
钩子门。所以这里的断言从「文件被写成什么」改成「返回了哪个候选 + 是否写盘」：

  - 有更优快照 → 返回该脚本（`meta["selected"] is True`），**文件一个字节不动**；
  - 无更优 / 被禁用 / 快照未过初筛 → 返回 `None`；
  - 落盘由唯一提交点负责（见 `test_step01_commit.py`）。
"""
from __future__ import annotations

import json
import sys
import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _clean_quality_summary(monkeypatch):
    """Default guard tests exercise score comparison, not the full checklist."""
    import step01_script_gen.main as m
    # v6.84（二次审核发现 1）：`_candidate_quality_summary` 现在多一档
    # `structural_ok`（结构收敛类：内容超 cap / 低于下限）。`_quality_rank` 把
    # 「缺这一档」视为 unknown 并排在最差档——**有意为之**（unknown 不得压过
    # 已知干净版本）。所以这里的假摘要必须跟着真实摘要一起长。
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda script, novel_text="": {
        "quality_checked": True,
        "blocking_risk_count": 0,
        "integrity_ok": True,
        "integrity_issues": [],
        "structural_ok": True,
        "structural_issues": [],
    })


def _script_with_dialogue(n_dialogue: int, total: int = 10) -> dict:
    """构造对白镜数量可控的最小 script（时长/镜数都给 10 分标准值）.

    prompt 按镜号变化（2026-09-12）：本测试测的是跨 run 护栏契约，不是打分
    机制——相邻重复惩罚上线后，逐镜相同的 prompt 会被判凑镜重复扣分，
    破坏「恢复版 ≥60」的契约基线。非退化夹具 = 每镜视觉可区分。
    """
    shots = []
    for i in range(1, total + 1):
        narr = "杨旭道：这一句是测试对白" if i <= n_dialogue else ""
        shots.append({
            "index": i,
            "prompt": f"[中景] 测试镜头{i}：画面内容各不相同{i}号机位",
            "ff_desc": "", "lf_desc": "", "motion_desc": "",
            "duration_sec": 6.0,
            "narration_segment": narr,
            "subtitle": narr,
            "main_subject": "杨旭" if narr else "群演",
        })
    return {"shots": shots, "total_duration": 60.0}


def _write_snapshot(snap_dir: Path, ts: str, script: dict) -> Path:
    p = snap_dir / f"script_result_{ts}.json"
    p.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return p


def _setup(tmp_path, monkeypatch):
    import step01_script_gen.main as m
    out = tmp_path / "script_result.json"
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "OUTPUT_FILE", out)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    return m, out, snap_dir


def test_new_score_higher_no_selection(tmp_path, monkeypatch):
    """新终稿更优 → 不选候选、不写盘."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(1))
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(8), "")
    assert selected is None
    assert meta["selected"] is False
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_ledger_saturation_selects_no_snapshot(tmp_path, monkeypatch):
    """2026-09-27（用户裁决 4）：台账全饱和时**一份都不选**——让新稿进提交门检验.

    旧口径（v6.142 P1-4）：全欠账 → 整表放开、按欠账轮次升序选一份。实测 08:11
    run：全部 20 份快照都在台账内 → 选中上一轮刚 BLOCK 的 `072416`（那 2 分风险
    只是**确定性**风险，欠的语义问题不在这个数里）→ 重跑同一套审计、同样的
    4 条 blocking、`resolved=0` → 再 BLOCK。每轮原地空转，还把「确定性风险低」
    读成了「已能提交」。

    判别性：两份快照都在台账内（其中一份分数明显更高）→ 必须**一份都不选**；
    旧口径下会返回高分那份。
    """
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    _write_snapshot(snap_dir, "20260924_215530", _script_with_dialogue(2))
    assert m._score_script(_script_with_dialogue(4), "")["total"] > \
        m._score_script(_script_with_dialogue(2), "")["total"], "夹具必须有分差"

    (snap_dir / "_blocked_revisions.json").write_text(_json.dumps({
        "revisions": [_rev(_script_with_dialogue(4)), _rev(_script_with_dialogue(2))],
        "debt": {_rev(_script_with_dialogue(4)): 5,
                 _rev(_script_with_dialogue(2)): 1},
    }, ensure_ascii=False), encoding="utf-8")

    selected, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert selected is None, "欠账稿不得再作为「最佳」稿被重复修复"
    assert meta["best_path"] is None and meta["selected"] is False, meta
    assert meta["reason"] == "all_snapshots_owed", meta
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_criteria_change_re_admits_a_blocked_snapshot(tmp_path, monkeypatch):
    """判据指纹变了 → 旧欠账不再作数，该稿按**现行判据**重新评估（用户裁决 4）.

    理由：旧结论可能正是被修掉的那条判据得出的（本轮就有两条：
    `frame_presence_consistency` 的时态误判、缺段重复计量）。指纹不符就永久排除
    等于「判据修好了也换不回重评机会」。
    """
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _hot = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    rev = _rev(_script_with_dialogue(4))
    (snap_dir / "_blocked_revisions.json").write_text(_json.dumps({
        "revisions": [rev],
        "criteria": {rev: {"rules": "OLD-RULES", "prompts": "OLD-PROMPTS"}},
    }, ensure_ascii=False), encoding="utf-8")

    assert m._load_blocked_revisions() == set(), (
        "指纹不符的条目不得再当欠账——否则判据修好也换不回重评")
    _sel, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert meta["best_path"] == str(_hot), meta


def test_ledger_without_criteria_stays_blocked(tmp_path, monkeypatch):
    """老台账条目（无判据指纹）按**仍然欠账**处理——保守方向.

    无法核对旧结论是在哪版判据下得出的，就不凭它把该稿放回候选池。
    """
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    (snap_dir / "_blocked_revisions.json").write_text(
        _json.dumps({"revisions": [_rev(_script_with_dialogue(4))]}),
        encoding="utf-8")

    _sel, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert _sel is None and meta["reason"] == "all_snapshots_owed", meta


def test_recorded_criteria_match_the_current_fingerprints(tmp_path, monkeypatch):
    """登记时写入的指纹 = 现行指纹 → 同判据下再读仍是欠账（棘轮不被自己抹掉）."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    m._record_blocked_revision("rev_abc")
    data = json.loads((snap_dir / "_blocked_revisions.json").read_text(encoding="utf-8"))
    assert data["criteria"]["rev_abc"] == m._current_criteria()
    assert m._load_blocked_revisions() == {"rev_abc"}


def test_ledger_without_debt_counts_reads_as_one_episode(tmp_path, monkeypatch):
    """老台账（只有 `revisions`、没有 `debt`）按 1 轮计——只影响欠账轮次的读数."""
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    (snap_dir / "_blocked_revisions.json").write_text(
        _json.dumps({"revisions": [_rev(_script_with_dialogue(4))]}),
        encoding="utf-8")

    assert m._load_blocked_debt() == {_rev(_script_with_dialogue(4)): 1}


def test_ledger_survives_a_corrupt_debt_entry(tmp_path, monkeypatch):
    """一条脏计数不得废掉整张台账（静默丢掉护栏比计数不准更糟）."""
    import json as _json

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    (snap_dir / "_blocked_revisions.json").write_text(_json.dumps({
        "revisions": ["rev_a", "rev_b"],
        "debt": {"rev_a": "not-a-number", "rev_b": 4},
    }, ensure_ascii=False), encoding="utf-8")

    _debt = m._load_blocked_debt()
    assert _debt["rev_b"] == 4
    assert _debt["rev_a"] == 1, "脏值按最老台账口径记 1 轮，不牵连同表"
    assert m._load_blocked_revisions() == {"rev_a", "rev_b"}, "台账整体仍可读"

    m._record_blocked_revision("rev_a")   # 写入不得因脏值整体失败
    assert m._load_blocked_debt()["rev_a"] == 2


def test_record_blocked_revision_counts_episodes(tmp_path, monkeypatch):
    """每次登记 +1：反复卷进 BLOCK 的旧稿计数递增（棘轮靠它降位）."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    rev = "rev_abc"
    for _ in range(3):
        m._record_blocked_revision(rev)
    assert m._load_blocked_debt()[rev] == 3
    assert m._load_blocked_revisions() == {rev}


def test_new_script_winning_is_logged(tmp_path, monkeypatch):
    """09-26 P1：**新稿胜出也必须留日志**（此前静默 return，取舍不可复核）.

    实损（09-26 20:49 run）：日志里只有「快照胜出」那两行 WARN，新稿胜出时
    什么都不打——事后无法回答「快照为什么没赢」。判别性：把 warn 收进列表，
    本用例断言有一条同时含 new/best 分数与 tier 的 [BEST-GUARD] 行。
    v6.138 之前该断言失败（无任何日志）。
    """
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(1))
    warns: list = []
    monkeypatch.setattr(m._logger, "warn", lambda msg, *a, **k: warns.append(str(msg)))

    selected, meta = m._cross_run_best_guard(_script_with_dialogue(8), "")
    assert selected is None and meta["selected"] is False
    assert meta["reason"] == "same_tier_within_margin"
    bg = [w for w in warns if w.startswith("[BEST-GUARD] 保留新终稿")]
    assert bg, "新稿胜出路径没有任何日志：%s" % warns
    line = bg[0]
    assert str(meta["best_score"]) in line        # 快照分
    assert str(meta["best_score"]) not in ("", "None")
    assert "tier" in line and "margin=" in line    # 判据口径一并留痕
    assert "不替换" in line


def test_snapshot_best_is_selected_but_not_written(tmp_path, monkeypatch):
    """快照最优显著更高 + KEEP_BEST_SNAPSHOT=1 → **选中为候选**，但不落盘.

    这是 v6.103 的方向反转：历史快照不再被直接写回正式产物（旧行为让「最终落盘
    的 hook」逃过 final audit），而是作为一个候选回到公共提交管线。
    """
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))
    new = _script_with_dialogue(1)
    new_score = m._score_script(new, "")["total"]
    selected, meta = m._cross_run_best_guard(new, "")
    assert selected is not None, meta
    assert meta["selected"] is True
    assert meta["best_score"] > new_score
    assert m._score_script(selected, "")["total"] > new_score
    # **护栏不写盘**：正式产物保持原样，写入只发生在唯一提交点。
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_current_script_snapshot_is_not_its_own_candidate(tmp_path, monkeypatch):
    """与当前稿件**同一个 revision** 的快照不是候选（不能和自己比）."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    cur = _script_with_dialogue(3)
    _write_snapshot(snap_dir, "20260910_000000", cur)      # 就是当前这一版
    selected, meta = m._cross_run_best_guard(cur, "")
    assert selected is None
    assert meta["best_path"] is None


def test_keep_best_disabled_warn_only(tmp_path, monkeypatch):
    """KEEP_BEST_SNAPSHOT=0 → 仅告警，不选候选."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("KEEP_BEST_SNAPSHOT", "0")
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert selected is None
    assert meta["reason"] == "keep_best_disabled"
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_no_snapshots_no_action(tmp_path, monkeypatch):
    """无快照（首次运行）→ 不动作."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert selected is None
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_corrupted_high_score_snapshot_not_selected(tmp_path, monkeypatch):
    """A high raw score must not beat a clean new version when integrity fails."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)

    corrupt = _script_with_dialogue(8)
    corrupt["_corrupt"] = True
    _write_snapshot(snap_dir, "20260910_000000", corrupt)
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))

    def fake_quality(script, novel_text=""):
        if script.get("_corrupt"):
            return {"quality_checked": True, "blocking_risk_count": 0,
                    "integrity_ok": False, "integrity_issues": ["broken"],
                    "structural_ok": True, "structural_issues": []}
        return {"quality_checked": True, "blocking_risk_count": 0,
                "integrity_ok": True, "integrity_issues": [],
                "structural_ok": True, "structural_issues": []}

    monkeypatch.setattr(m, "_candidate_quality_summary", fake_quality)
    selected, _meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert selected is None
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_snapshot_failing_prefilter_is_not_a_candidate(tmp_path, monkeypatch):
    """历史快照必须通过**当前版本**的质量初筛才有资格成为候选."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))

    def fake_quality(script, novel_text=""):
        # 快照（8 句对白）风险非零 → 初筛不过；新稿干净
        risky = len(script.get("shots") or []) == 10 and any(
            s.get("narration_segment") for s in script["shots"])
        return {"quality_checked": True,
                "blocking_risk_count": 2 if risky else 0,
                "integrity_ok": True, "integrity_issues": [],
                "structural_ok": True, "structural_issues": []}

    monkeypatch.setattr(m, "_candidate_quality_summary", fake_quality)
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert selected is None
    assert meta["reason"] == "snapshot_failed_prefilter"


# ---------------------------------------------------------------------------
# v6.123（用户口径）：候选留痕——BEST-GUARD **选择之前**的新生成候选
#
# 此前：一旦选中历史稿，新稿只在内存里存在到 `script = _bg_selected` 那一刻；
# 失败快照写的是被选稿、成功快照写的也是被选稿 → 「本次生成质量」事后无证据，
# 而日志却宣称「本次 N 分版本保留在 snapshots/」（实际什么都没写）。
# ---------------------------------------------------------------------------
def test_new_candidate_is_snapshotted_before_selection(tmp_path, monkeypatch):
    """选中历史稿时，本次新候选必须有一份**带标记的独立快照**，且路径可回查."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))
    new = _script_with_dialogue(2)
    selected, meta = m._cross_run_best_guard(new, "")

    assert selected is not None, meta
    _path = meta.get("new_candidate_snapshot")
    assert _path, "没有给新候选留档：%s" % meta
    _snap = Path(_path)
    assert _snap.exists() and _snap.parent == snap_dir
    assert _snap.name.startswith("candidate_pre_bestguard_"), _snap.name
    assert json.loads(_snap.read_text(encoding="utf-8")) == new, (
        "留档内容不是**本次新稿**")


def test_pre_bestguard_snapshot_never_enters_the_candidate_pool(tmp_path, monkeypatch):
    """未定稿（pre-bestguard）**不得**被当作历史候选：命名前缀与候选扫描口径不同."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    (snap_dir / "candidate_pre_bestguard_20260910_000000.json").write_text(
        json.dumps(_script_with_dialogue(8), ensure_ascii=False), encoding="utf-8")
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert selected is None, "未定稿进了候选池：%s" % meta
    assert meta["selected"] is False


def test_snapshot_failure_is_reported_not_claimed(tmp_path, monkeypatch):
    """写入失败 → `meta` 里如实为空、日志说「未留档」，不得宣称已保留."""
    import shared.json_io as _json_io

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _write_snapshot(snap_dir, "20260910_000000", _script_with_dialogue(8))
    _write_snapshot(snap_dir, "20260911_120000", _script_with_dialogue(0))

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(_json_io, "atomic_dump_json", _boom)
    selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert selected is not None, meta
    assert meta.get("new_candidate_snapshot") == "", meta


# ---------------------------------------------------------------------------
# v6.124.2（用户 2026-09-25 决定）：候选**先验明正身**再评分
# 现场：审计诊断产物 `snapshots/script_result_20260925_173311.audit.compare.json`
# （非分镜稿）在 `_candidate_quality_summary` 下查不出任何 issue → 风险 0 →
# `_quality_rank` 给出 (0,0,0)，**优于任何真稿** → 被选为终稿 → 提交点 BLOCK。
# ---------------------------------------------------------------------------
def _risk_by_shape(script, novel_text=""):
    """复刻真实行为：**没有 shots 的 JSON 查不出 issue → 风险 0**（最干净的假象）."""
    _no_shots = not (isinstance(script, dict) and script.get("shots"))
    return {
        "quality_checked": True,
        "blocking_risk_count": 0 if _no_shots else 5,
        "integrity_ok": True, "integrity_issues": [],
        "structural_ok": True, "structural_issues": [],
    }


def test_non_script_snapshot_never_wins_the_guard(tmp_path, monkeypatch):
    """非分镜稿（无 shots）不得成为候选；拆掉闸门则它立刻凭「风险 0」夺冠."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_candidate_quality_summary", _risk_by_shape)

    _non_script = snap_dir / "script_result_20260925_173311.audit.compare.json"
    _non_script.write_text(json.dumps({
        "run_a": {}, "run_b": {}, "same_shot_evidence": [], "code_only": [],
        "code_matched_but_evidence_differs": [], "only_in_a": [], "only_in_b": [],
    }, ensure_ascii=False), encoding="utf-8")
    _valid = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(8))

    selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert meta["best_path"] == str(_valid), (
        "非分镜稿被当成了候选：%s" % meta)
    assert selected is not None
    assert any("audit.compare.json" in r for r in meta["invalid_candidates"]), meta

    # 判别性：把闸门拆掉 → 同一份非分镜稿立刻夺冠（正是 09-25 19:14 的现场）
    monkeypatch.setattr(m, "_non_script_snapshot_reason", lambda obj: "")
    _selected2, meta2 = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert meta2["best_path"] == str(_non_script), (
        "夹具没能复现现场：风险 0 的非分镜稿应当压过真稿：%s" % meta2)


def test_non_script_snapshot_never_enters_selection(tmp_path, monkeypatch):
    """欠账稿不再放进候选池（放宽分支已删）；非分镜稿也仍然进不了候选."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_candidate_quality_summary", _risk_by_shape)
    _non_script = snap_dir / "script_result_20260926_000000.audit.r1.json"
    _non_script.write_text(json.dumps({"issues": [], "diagnostic": True}),
                           encoding="utf-8")
    # 唯一真稿在欠账台账里 → 本轮没有任何合格候选
    _blocked = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(8))
    from shared.quality_gate import compute_script_revision as _rev
    monkeypatch.setattr(m, "_load_blocked_revisions",
                        lambda: {_rev(_script_with_dialogue(8))})
    _selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
    assert _selected is None and meta["reason"] == "all_snapshots_owed", meta
    assert not (meta.get("best_path") or "").endswith("audit.r1.json"), meta
    assert any("audit.r1.json" in r for r in meta.get("invalid_candidates") or []), meta


@pytest.mark.parametrize("obj", [
    [1, 2],                                        # 不是对象
    {},                                            # 缺 shots
    {"shots": []},                                 # 空 shots
    {"shots": "x"},                                # 不是数组
    {"shots": [{}, {}]},                           # 无可识别镜头对象
    {"run_a": {}, "only_in_a": [], "only_in_b": []},   # 审计比对产物
    {"issues": [], "diagnostic": True},            # 审计诊断产物
])
def test_non_script_snapshot_reason_rejects(obj):
    import step01_script_gen.main as m
    assert m._non_script_snapshot_reason(obj), "非分镜稿没被挡住：%r" % (obj,)


@pytest.mark.parametrize("obj", [
    {"shots": [{"index": 1, "prompt": "p"}]},                     # 最小稿件
    {"shots": [{"prompt": "p"}], "total_duration": 60.0},          # 无 index
    {"shots": [{"index": "hook", "prompt": "p"}]},
])
def test_minimal_but_valid_script_is_not_rejected(obj):
    """**只校验最小结构**（用户口径：别把旧版合法快照误挡掉）.

    刻意不要求 `timeline` / `characters` / `chapter_outline`——旧版快照与本项目
    自己的最小稿件夹具都没有这些键。
    """
    import step01_script_gen.main as m
    assert m._non_script_snapshot_reason(obj) == "", "合法最小编剧被误挡"


def test_blocked_revision_snapshot_is_skipped_in_selection(tmp_path, monkeypatch):
    """台账里的 revision 不参与选稿——配合第 1 项（BLOCK 时记下**原稿** revision）.

    现场：run 193736 只登记了修后 revision，被选中的历史稿自身 revision 不在台账内
    → 下一轮又能选中它、再 BLOCK。

    2026-09-27（用户裁决 4）：判据指纹未变时这是**排除**（不再是「有别的候选时才
    不选」的软护栏）；没有其它候选时返回 None，由新稿进提交门（见
    `test_ledger_saturation_selects_no_snapshot`）。
    """
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    # ⚠️ 打分在 dialogue≥4 处饱和（实测 53.0）——夹具必须**有分差**，否则两份并列、
    # 先按文件名序者胜，① 的判别性就没了。
    _hot = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    _calm = _write_snapshot(snap_dir, "20260924_215530", _script_with_dialogue(2))

    # ① 台账里记着高分那份的 revision → 它不参与，选另一份
    (snap_dir / "_blocked_revisions.json").write_text(
        _json.dumps({"revisions": [_rev(_script_with_dialogue(4))]}),
        encoding="utf-8")
    _sel, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert meta["best_path"] == str(_calm), meta

    # ② 判别性：台账空 → 同一份高分稿立刻赢下护栏
    (snap_dir / "_blocked_revisions.json").write_text(
        _json.dumps({"revisions": []}), encoding="utf-8")
    _sel2, meta2 = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert meta2["best_path"] == str(_hot), (
        "夹具没有让高分稿本该获胜（否则 ① 不具判别性）：%s" % meta2)


# ---------------------------------------------------------------------------
# v6.149（复核根因 3）：欠账按**具体未解决问题 + 其所依赖判据**记录
# ---------------------------------------------------------------------------


def test_debt_issue_records_split_by_criterion(monkeypatch):
    """审计类欠账依赖**审计提示版本**，确定性欠账依赖**规则源指纹**."""
    import step01_script_gen.audit as au
    import step01_script_gen.main as m
    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-1")

    recs = m._debt_issue_records([
        {"code": "model_audit_STATE_CHAIN_CONFLICT", "shot_id": "8",
         "field_path": "lf_desc"},
        {"code": "result_action_support", "shot_id": "8", "field_path": "motion_desc"},
    ])
    assert [r["crit_kind"] for r in recs] == ["audit", "rules"]
    assert recs[0]["crit_fp"] == "APV-1"
    assert recs[1]["crit_fp"] == str(m.RULE_SOURCE_FINGERPRINT or "")


def test_issue_in_force_uses_its_own_criterion(monkeypatch):
    """只有**该条**依赖的判据变了才算作废——另一类判据的变动不影响它."""
    import step01_script_gen.audit as au
    import step01_script_gen.main as m

    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-1")
    audit_rec = m._debt_issue_records(
        [{"code": "model_audit_X", "shot_id": "8"}])[0]
    assert m._debt_issue_in_force(audit_rec) is True
    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-2")
    assert m._debt_issue_in_force(audit_rec) is False

    monkeypatch.setattr(m, "RULE_SOURCE_FINGERPRINT", "R1")
    rules_rec = m._debt_issue_records(
        [{"code": "result_action_support", "shot_id": "8"}])[0]
    assert m._debt_issue_in_force(rules_rec) is True
    monkeypatch.setattr(m, "RULE_SOURCE_FINGERPRINT", "R2")
    assert m._debt_issue_in_force(rules_rec) is False

    # 认不出判据 → 保守按仍然欠账
    assert m._debt_issue_in_force({"crit_kind": "???", "crit_fp": "x"}) is True


def test_unrelated_prompt_change_does_not_wash_audit_debt(tmp_path, monkeypatch):
    """**改无关提示不能自动洗掉审计类欠账**（旧口径整批放行 59 份的根因）."""
    import step01_script_gen.audit as au
    import step01_script_gen.main as m
    m, out, snap_dir = _setup(tmp_path, monkeypatch)

    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-1")
    monkeypatch.setattr(m, "PROMPT_SOURCE_FINGERPRINT", "P1")
    m._record_blocked_revision("rev_x", issues=m._debt_issue_records(
        [{"code": "model_audit_X", "shot_id": "8", "field_path": "lf_desc"}]))
    assert m._load_blocked_revisions() == {"rev_x"}

    # 提示源指纹变了（与审计提示无关的改动）→ 旧口径会整批放行；新口径仍排除
    monkeypatch.setattr(m, "PROMPT_SOURCE_FINGERPRINT", "P2")
    assert m._load_blocked_revisions() == {"rev_x"}

    # 审计提示本身变了 → 该条欠账作废，恢复候选资格
    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-2")
    assert m._load_blocked_revisions() == set()


def test_partially_stale_debt_still_excluded(tmp_path, monkeypatch):
    """一条稿上两类欠账：只有一类判据变了 → 仍排除（只作废变了的那条）."""
    import step01_script_gen.audit as au
    import step01_script_gen.main as m
    m, out, snap_dir = _setup(tmp_path, monkeypatch)

    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-1")
    monkeypatch.setattr(m, "RULE_SOURCE_FINGERPRINT", "R1")
    m._record_blocked_revision("rev_y", issues=m._debt_issue_records([
        {"code": "model_audit_X", "shot_id": "8", "field_path": "lf_desc"},
        {"code": "result_action_support", "shot_id": "8", "field_path": "motion_desc"},
    ]))

    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-2")   # 审计那条作废
    assert m._load_blocked_revisions() == {"rev_y"}                    # 规则那条仍在

    monkeypatch.setattr(m, "RULE_SOURCE_FINGERPRINT", "R2")            # 规则那条也作废
    assert m._load_blocked_revisions() == set()


def test_snapshot_cross_field_blocking_is_the_commit_point_criterion(monkeypatch):
    """候选资格初筛用**与提交点同源**的跨字段判据（治「选中即将被判死的旧稿」）."""
    import step01_script_gen.main as m

    bad = {"shots": [{"index": 8, "duration_sec": 5.0,
                      "lf_desc": "寒光短刃刺中杨旭心口，白袍洇出暗红",
                      "motion_desc": "前2秒：那人右手一扬；2-4秒：寒光剑光掠过杨旭心口；"
                                     "4-5秒：那人推臂一送，白袍洇出暗红"}]}
    codes = [getattr(i, "code", "") for i in m._snapshot_cross_field_blocking(bad)]
    assert "result_action_support" in codes

    ok = {"shots": [{"index": 8, "duration_sec": 5.0,
                     "lf_desc": "白袍洇出暗红",
                     "motion_desc": "前2秒：那人右手一扬；2-4秒：寒光刺入杨旭心口；"
                                    "4-5秒：那人推臂一送，白袍洇出暗红"}]}
    assert m._snapshot_cross_field_blocking(ok) == []


def test_reblock_under_new_criteria_overwrites_the_stale_record(tmp_path, monkeypatch):
    """复核发现 2：R1 判据作废 → R2 下**同一问题再次 BLOCK** → 必须重新排除.

    旧实现按 `(code, shot, field)` 去重且**旧的排在前**，于是新记录被跳过，台账里
    留下已过期的 R1 指纹 → `_load_blocked_revisions` 判「可重评」把它放回候选池。
    """
    import step01_script_gen.audit as au
    import step01_script_gen.main as m
    m, out, snap_dir = _setup(tmp_path, monkeypatch)

    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-1")
    _issue = [{"code": "model_audit_X", "shot_id": "8", "field_path": "lf_desc"}]
    m._record_blocked_revision("rev_z", issues=m._debt_issue_records(_issue))
    assert m._load_blocked_revisions() == {"rev_z"}

    # 判据变了 → 该条作废，恢复候选资格
    monkeypatch.setattr(au, "audit_prompt_version", lambda: "APV-2")
    assert m._load_blocked_revisions() == set()

    # 新判据下**同一问题再次 BLOCK** → 记录必须被现行判据覆盖 → 重新排除
    m._record_blocked_revision("rev_z", issues=m._debt_issue_records(_issue))
    rows = (m._load_blocked_ledger().get("issues") or {}).get("rev_z") or []
    assert [r["crit_fp"] for r in rows] == ["APV-2"], rows
    assert m._load_blocked_revisions() == {"rev_z"}


def test_cross_field_validator_exception_means_unverified(monkeypatch):
    """复核发现 4：校验器异常 → `None`（**未能校验**），不得等同「无 blocking」."""
    import step01_script_gen.main as m
    import step01_script_gen.cross_field_checks as cfc

    def _boom(script):
        raise RuntimeError("validator down")

    monkeypatch.setattr(cfc, "cross_field_issues", _boom)
    assert m._snapshot_cross_field_blocking({"shots": []}) is None
