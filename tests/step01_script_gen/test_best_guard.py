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


def test_ledger_saturation_prefers_least_indebted(tmp_path, monkeypatch):
    """v6.142（09-26 用户决定，P1-4）：台账全饱和时按**欠账轮次升序**选，不再只看分数.

    旧实现（v6.115）：全部快照都在台账内 → 整表放开、仍按分数/风险选。于是
    「低风险但反复欠账」的那份照样被反复选中——台账唯一携带的信息（欠过几次）
    被丢掉了，棘轮形同失效。

    判别性：高分快照欠 5 轮、低分快照欠 1 轮 → 必须选低分那份。
    旧口径下高分者胜（`_quality_rank` 只看分数/风险），本用例会红。
    """
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _hot = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    _calm = _write_snapshot(snap_dir, "20260924_215530", _script_with_dialogue(2))
    assert m._score_script(_script_with_dialogue(4), "")["total"] >         m._score_script(_script_with_dialogue(2), "")["total"], "夹具必须有分差"

    (snap_dir / "_blocked_revisions.json").write_text(_json.dumps({
        "revisions": [_rev(_script_with_dialogue(4)), _rev(_script_with_dialogue(2))],
        "debt": {_rev(_script_with_dialogue(4)): 5,
                 _rev(_script_with_dialogue(2)): 1},
    }, ensure_ascii=False), encoding="utf-8")

    selected, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert meta["best_path"] == str(_calm), (
        "欠账少的必须优先，不能只按分数选：%s" % meta)


def test_ledger_without_debt_counts_reads_as_one_episode(tmp_path, monkeypatch):
    """老台账（只有 `revisions`、没有 `debt`）按 1 轮计——不写=0 会让老条目无条件胜出."""
    import json as _json

    from shared.quality_gate import compute_script_revision as _rev

    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    _old = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(4))
    (snap_dir / "_blocked_revisions.json").write_text(
        _json.dumps({"revisions": [_rev(_script_with_dialogue(4))]}),
        encoding="utf-8")

    assert m._load_blocked_debt() == {_rev(_script_with_dialogue(4)): 1}
    _sel, meta = m._cross_run_best_guard(_script_with_dialogue(0), "")
    assert meta["best_path"] == str(_old)


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


def test_non_script_snapshot_never_enters_the_relaxed_pool(tmp_path, monkeypatch):
    """「全部快照欠账 → 放开排除」的放宽分支**不得**放宽「是不是稿件」这一条."""
    m, out, snap_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_candidate_quality_summary", _risk_by_shape)
    _non_script = snap_dir / "script_result_20260926_000000.audit.r1.json"
    _non_script.write_text(json.dumps({"issues": [], "diagnostic": True}),
                           encoding="utf-8")
    # 唯一真稿在欠账台账里 → 触发放宽分支
    _blocked = _write_snapshot(snap_dir, "20260924_231100", _script_with_dialogue(8))
    from shared.quality_gate import compute_script_revision as _rev
    monkeypatch.setattr(m, "_load_blocked_revisions",
                        lambda: {_rev(_script_with_dialogue(8))})
    _selected, meta = m._cross_run_best_guard(_script_with_dialogue(1), "")
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

    ⚠️ 只保证「**有别的候选时**不选它」：全部候选都欠账时护栏会走「放开限制」的
    兜底分支（有意保留，否则护栏整个失效）——**不是**永久排除。
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
