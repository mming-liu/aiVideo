"""F 包地基测试：路径守卫 / 单写者锁 / 事件与投影 / 不可变 manifest / accepted 指针。

判别性要求（计划 §F3、§F4 与 CLAUDE.md 自测章）：每类失败独立触发并断言具体
code 与副作用（原指针是否被改、证据是否保留），合法输入同时验证成功出口。
"""
import json
import os
import time
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from shared.paths import (enhancement_experiment_dir, enhancement_package_dir,
                          execution_log_file, flat_clip_file, validate_experiment_id,
                          video_attempts_dir)


# ---------------------------------------------------------------- 路径与 id

def test_experiment_paths_single_source():
    root = enhancement_experiment_dir("exp1")
    assert root.name == "exp1" and root.parent.name == "_enhancement"
    assert enhancement_package_dir("video", "exp1") == root / "video"
    assert video_attempts_dir("exp1") == root / "video" / "attempts"
    assert eio.attempt_dir("tts", "exp1", "a-001") == root / "tts" / "attempts" / "a-001"


@pytest.mark.parametrize("bad", ["", "-lead", "_lead", "a" * 65, "a b", "a/b", "..", "a\\b"])
def test_experiment_id_rejected(bad):
    with pytest.raises(ValueError):
        validate_experiment_id(bad)


def test_attempt_id_rejected():
    with pytest.raises(eio.EnhancementError):
        eio.attempt_dir("video", "exp1", "../escape")


def test_candidate_path_guard(tmp_path):
    root = tmp_path / "_enhancement" / "exp1"
    root.mkdir(parents=True)
    good = root / "video" / "attempts" / "a1" / "outputs" / "candidate.mp4"
    assert eio.assert_candidate_path(good, experiment_root=root) == good.resolve()

    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(Path("relative/path.mp4"), experiment_root=root)
    assert ei.value.code == "relative_candidate_path"

    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(root / ".." / "outside.mp4", experiment_root=root)
    assert ei.value.code == "path_escape"

    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(tmp_path / "elsewhere.mp4", experiment_root=root)
    assert ei.value.code == "path_outside_experiment"

    frozen = root / "frozen" / "script_result.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("{}", encoding="utf-8")
    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(frozen, experiment_root=root, inputs=(frozen,))
    assert ei.value.code == "path_conflicts_with_input_or_formal"

    # 同名不同目录（无父子关系）不冲突；候选落在正式**目录**里才冲突
    assert eio.assert_candidate_path(root / "x" / "script_result.json", experiment_root=root,
                                     inputs=(frozen,))
    formal_dir = root / "formal_out"
    with pytest.raises(eio.EnhancementError) as ei:
        eio.assert_candidate_path(formal_dir / "cand.mp4", experiment_root=root,
                                  formal=(formal_dir,))
    assert ei.value.code == "path_conflicts_with_input_or_formal"


def test_production_paths_cover_formal_outputs():
    paths = {p.name for p in eio.production_paths()}
    assert {"script_result.json", "final.mp4", "subtitles.srt", "clips", "audio"} <= paths


def test_execution_log_file_context_and_production():
    from shared.paths import chapter_dir
    assert execution_log_file("step04") == chapter_dir() / "step04.log"

    class Ctx:
        log_dir = None
        attempt_dir = None

    ctx = Ctx()
    ctx.attempt_dir = Path("/tmp/attempt-1")
    assert execution_log_file("step04", ctx) == Path("/tmp/attempt-1") / "step04.log"
    with pytest.raises(ValueError):
        execution_log_file("step04", Ctx())  # 拒绝静默回退生产日志


def test_flat_clip_file_hook_and_body(tmp_path):
    assert flat_clip_file(0, explicit_root=tmp_path) == tmp_path / "clip_0.mp4"
    assert flat_clip_file(7, explicit_root=tmp_path).name == "clip_7.mp4"


# ---------------------------------------------------------------- 锁

def test_lock_acquire_release_roundtrip(tmp_path):
    lock = eio.AttemptLock(tmp_path / ".lock", purpose="t")
    lock.acquire()
    assert (tmp_path / ".lock" / eio.LOCK_OWNER_FILENAME).is_file()
    lock.release()
    assert not (tmp_path / ".lock").exists()


def test_lock_busy_reports_exit_5(tmp_path):
    a = eio.AttemptLock(tmp_path / ".lock", purpose="a").acquire()
    try:
        b = eio.AttemptLock(tmp_path / ".lock", purpose="b")
        with pytest.raises(eio.BusyError) as ei:
            b.acquire()
        assert ei.value.exit_code == eio.EXIT_BUSY
        assert ei.value.code == "lock_busy"
    finally:
        a.release()


def test_lock_stale_dead_pid_taken_over(tmp_path):
    lock_dir = tmp_path / ".lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / eio.LOCK_OWNER_FILENAME).write_text(json.dumps(
        {"pid": 999999999, "process_start": "win:deadbeef", "token": "old"}), encoding="utf-8")
    lock = eio.AttemptLock(lock_dir)
    lock.acquire()  # 不抛：确认 owner 已退出
    assert (lock_dir / eio.LOCK_OWNER_FILENAME).read_text(encoding="utf-8").find(str(os.getpid())) > -1
    lock.release()


def test_lock_unparsable_owner_never_broken(tmp_path):
    lock_dir = tmp_path / ".lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / eio.LOCK_OWNER_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(eio.BusyError):
        eio.AttemptLock(lock_dir).acquire()


def test_reclaim_rereads_owner_inside_the_gate(tmp_path):
    """P1-1 之二：回收者交错时只能有一个赢家，且**门内**复核能看到别人新建的锁.

    交错序列（复核给出的反例）：B 先判定旧 owner 已退出 → A 抢先回收并另建新锁 →
    B 醒来照旧判断去删 ⇒ 删掉 A 的新锁、两人同时持锁。缺 owner 窗口已修，这一条
    是"回收"这一步的两个赢家。
    """
    lock_dir = tmp_path / ".lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / eio.LOCK_OWNER_FILENAME).write_text(json.dumps(
        {"pid": 999999999, "process_start": "win:deadbeef", "token": "old"}),
        encoding="utf-8")

    b = eio.AttemptLock(lock_dir, purpose="B")
    alive, why = b.owner_alive()
    assert (alive, why) == (False, "pid_dead")        # B 此刻的判定：可以回收

    a = eio.AttemptLock(lock_dir, purpose="A").acquire()   # A 抢先回收并持有
    a_token = json.loads((lock_dir / eio.LOCK_OWNER_FILENAME).read_text(
        encoding="utf-8"))["token"]

    with pytest.raises(eio.BusyError):
        b._reclaim_stale()                            # B 醒来进回收门 ⇒ 必须发现 A
    # A 的锁一个字节没被删
    assert (lock_dir / eio.LOCK_OWNER_FILENAME).is_file()
    assert json.loads((lock_dir / eio.LOCK_OWNER_FILENAME).read_text(
        encoding="utf-8"))["token"] == a_token

    # 合法对照：B 重新 acquire 也是忙（不夺锁），A 释放后 B 才能拿到
    with pytest.raises(eio.BusyError):
        eio.AttemptLock(lock_dir, purpose="B2").acquire()
    a.release()
    c = eio.AttemptLock(lock_dir, purpose="C").acquire()
    c.release()


def test_reclaim_gate_left_by_a_dead_owner_is_reusable(tmp_path):
    """合法对照：回收者自己崩在门里 ⇒ 门可复用（判据与主锁同一份）."""
    lock_dir = tmp_path / ".lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / eio.LOCK_OWNER_FILENAME).write_text(json.dumps(
        {"pid": 999999999, "process_start": "win:deadbeef", "token": "old"}),
        encoding="utf-8")
    gate = lock_dir.with_name(lock_dir.name + eio.RECLAIM_SUFFIX)
    gate.mkdir()
    (gate / eio.LOCK_OWNER_FILENAME).write_text(json.dumps(
        {"pid": 999999999, "process_start": "win:deadbeef", "token": "dead-gate"}),
        encoding="utf-8")
    lock = eio.AttemptLock(lock_dir).acquire()
    lock.release()
    assert not gate.exists()


def test_complete_json_without_trailing_newline_is_unconfirmed(tmp_path):
    """P1-3 之二：完整 JSON 但**没有收尾换行**同样是未确认 —— 追加会粘成一行.

    旧写法把这种尾部当已确认，于是新事件与它粘成一行、原来可读的事件变成坏行，
    任务记录（task_id）就丢了。
    """
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "opened", after={"state": "draft"})
    with open(att / eio.EVENTS_FILENAME, "a", encoding="utf-8") as f:
        f.write('{"schema_version": "enhancement_event.v1", "seq": 2, "kind": '
                '"submit_post", "task_id": "T-1", "before": null, '
                '"after": {"state": "submitted"}}')      # 完整 JSON，无换行
    events, tail = eio.read_events_strict(att)
    assert len(events) == 1 and tail == "tail_incomplete:line_2"

    with pytest.raises(eio.EnhancementError) as ei:
        eio.append_event(att, "later", after={"state": "running"}, task_id="T-2")
    assert ei.value.code == "events_tail_incomplete"

    proj = eio.reconcile_lifecycle(att, transitions=_T)
    # 完整事件**没有丢**：补上换行后照常读回
    assert proj["state"] == "submitted" and proj["projected_from_event_seq"] == 2
    ids = [e.get("task_id") for e in eio.read_events_strict(att)[0]]
    assert "T-1" in ids and eio.read_events_strict(att)[1] is None
    ev = proj["tail_repaired"]
    assert ev["kind"] == "unterminated"
    assert "T-1" in Path(ev["evidence_path"]).read_text(encoding="utf-8")

    # 修尾之后可继续追加，新事件读得回来
    eio.append_event(att, "later", after={"state": "running"}, task_id="T-2")
    assert [e.get("task_id") for e in eio.read_events_strict(att)[0]][-1] == "T-2"


def test_complete_line_with_newline_is_confirmed(tmp_path):
    """合法对照：**整行 + 换行**才是确认点，正常写入不受影响."""
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "opened", after={"state": "draft"})
    eio.append_event(att, "submit_post", after={"state": "submitted"}, task_id="T-1")
    events, tail = eio.read_events_strict(att)
    assert tail is None and len(events) == 2
    raw = (att / eio.EVENTS_FILENAME).read_text(encoding="utf-8")
    assert raw.endswith("\n")


def test_lock_without_owner_is_not_stolen(tmp_path):
    """owner 尚未落盘的窗口内，第二个执行者**不得**夺锁（P1-1）.

    对照反例：旧实现把"缺 owner"当无人持锁，于是删掉对方刚建好的锁目录并自建一个
    ——两者同时持锁，可能并写事件与采纳指针。这里要求 fail-closed。
    """
    lock_dir = tmp_path / ".lock"
    lock_dir.mkdir(parents=True)          # == 第一个执行者 mkdir 成功、owner 还没写
    with pytest.raises(eio.BusyError) as ei:
        eio.AttemptLock(lock_dir).acquire()
    assert ei.value.fields.get("reason") == "owner_missing_or_unreadable"
    assert lock_dir.is_dir()              # 锁目录没有被删掉重建
    assert not (lock_dir / eio.LOCK_OWNER_FILENAME).exists()

    # 合法对照：owner 在位且进程确已退出 ⇒ 允许接管（不是一律拒锁）
    (lock_dir / eio.LOCK_OWNER_FILENAME).write_text(json.dumps(
        {"pid": 999999999, "process_start": "win:deadbeef", "token": "old"}),
        encoding="utf-8")
    lock = eio.AttemptLock(lock_dir).acquire()
    lock.release()


# ---------------------------------------------------------------- 事件与投影

#: 本文件核验的是**事务层**，用一条最小状态链；真实包各带自己的表
#: （video 见 ``shared.video_attempts.TRANSITIONS``，tts 见
#: ``tts.synthesis_job.ATTEMPT_TRANSITIONS``）。
_T = {"draft": set(), "submitted": {"draft"}, "running": {"draft", "submitted"},
      "accepted": {"running"}}

def test_events_seq_and_projection_catchup(tmp_path):
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    eio.append_event(att, "submitted", after={"state": "submitted"}, task_id="t-1")
    eio.write_lifecycle(att, {"state": "draft", "projected_from_event_seq": 1})
    proj = eio.reconcile_lifecycle(att, transitions=_T)
    assert proj["state"] == "submitted" and proj["projected_from_event_seq"] == 2


def test_projection_ahead_refuses_resume(tmp_path):
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    eio.write_lifecycle(att, {"state": "accepted", "projected_from_event_seq": 9})
    with pytest.raises(eio.EnhancementError) as ei:
        eio.reconcile_lifecycle(att, transitions=_T)
    assert ei.value.code == "projection_ahead"


def test_tail_incomplete_event_keeps_evidence_and_stops(tmp_path):
    """半截事件：**禁止追加**（会粘成一行），恢复截到最后确认点并把残片留成证据."""
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    # 半截事件（无换行）；用独特标记区分"残片"与事件里本来就有的 "before" 字段
    with open(att / eio.EVENTS_FILENAME, "a", encoding="utf-8") as f:
        f.write('{"seq": 2, "kind": "submitted", "marker": "TAILFRAG"')
    events, tail = eio.read_events_strict(att)
    assert len(events) == 1 and tail and tail.startswith("tail_incomplete")

    # 残片未修之前追加一律拒绝——旧写法在这里"成功"了，新事件却读不回来
    with pytest.raises(eio.EnhancementError) as ei:
        eio.append_event(att, "later", after={"state": "running"})
    assert ei.value.code == "events_tail_incomplete"

    proj = eio.reconcile_lifecycle(att, transitions=_T)
    assert proj["state"] == "draft"                     # 停在最后确认点
    assert proj["projected_from_event_seq"] == 1
    raw = (att / eio.EVENTS_FILENAME).read_text(encoding="utf-8")
    assert "TAILFRAG" not in raw                        # 残片不再留在事件文件里
    ev = proj["tail_repaired"]["evidence_path"]
    assert "TAILFRAG" in Path(ev).read_text(encoding="utf-8")   # 原文留成证据

    # 修尾之后可以继续追加，且新事件**真的读得回来**
    eio.append_event(att, "later", after={"state": "running"}, task_id="t-9")
    back = eio.reconcile_lifecycle(att, transitions=_T)
    assert back["state"] == "running" and back["projected_from_event_seq"] == 2


def test_event_seq_gap_refuses_resume(tmp_path):
    """跳号（1→9）不是"历史"，是损坏：拒绝自动续跑（P1-3）."""
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    with open(att / eio.EVENTS_FILENAME, "a", encoding="utf-8") as f:
        f.write('{"schema_version": "enhancement_event.v1", "seq": 9, "kind": "jump",'
                ' "before": null, "after": {"state": "submitted"}}\n')
    with pytest.raises(eio.EnhancementError) as ei:
        eio.reconcile_lifecycle(att, transitions=_T)
    assert ei.value.code == "events_seq_gap"


def test_illegal_state_chain_refuses_resume(tmp_path):
    """非法状态链（draft→accepted）拒绝续跑；合法对照 draft→submitted 放行."""
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    eio.append_event(att, "jump", after={"state": "accepted"})
    with pytest.raises(eio.EnhancementError) as ei:
        eio.reconcile_lifecycle(att, transitions=_T)
    assert ei.value.code == "illegal_state_chain"

    ok = tmp_path / "attempt-2"
    ok.mkdir()
    eio.append_event(ok, "prepared", after={"state": "draft"})
    eio.append_event(ok, "submitted", after={"state": "submitted"})
    assert eio.reconcile_lifecycle(ok, transitions=_T)["state"] == "submitted"


def test_broken_line_refuses_append(tmp_path):
    att = tmp_path / "attempt-1"
    att.mkdir()
    eio.append_event(att, "prepared", after={"state": "draft"})
    with open(att / eio.EVENTS_FILENAME, "a", encoding="utf-8") as f:
        f.write("{broken}\n")   # 中间断链（有换行 ⇒ 不是尾部不完整）
    events, tail = eio.read_events_strict(att)
    assert len(events) == 1 and tail == "broken_line_2"
    with pytest.raises(eio.EnhancementError) as ei:
        eio.append_event(att, "later", after={"state": "running"})
    assert ei.value.code == "events_broken"          # 中间断链 ⇒ 拒绝续跑/追加
    with pytest.raises(eio.EnhancementError) as ei:
        eio.reconcile_lifecycle(att, transitions=_T)
    assert ei.value.code == "events_broken"
    raw = (att / eio.EVENTS_FILENAME).read_text(encoding="utf-8")
    assert "{broken}" in raw                          # 原始证据保留


# ---------------------------------------------------------------- manifest / accepted

def _mk_attempt(tmp_path, name="a-1"):
    att = tmp_path / "pkg" / "attempts" / name
    att.mkdir(parents=True)
    out = att / "outputs"
    out.mkdir()
    media = out / "candidate.mp4"
    media.write_bytes(b"fake-mp4-bytes")
    return att, media


def test_manifest_write_once(tmp_path):
    att, media = _mk_attempt(tmp_path)
    sealed = eio.seal_manifest(att, {
        "package": "video", "status": "accepted",
        "artifacts": [eio.artifact_record(media, role="candidate")],
        # manifest 与指针必须记**同一个**请求身份（缺了不再跳过比较）
        "request_key": "k1",
        "checks": [{"name": "decode", "status": "pass"}],
    })
    assert sealed["manifest_sha256"]
    with pytest.raises(eio.EnhancementError) as ei:
        eio.seal_manifest(att, {"package": "video", "status": "rejected"})
    assert ei.value.code == "manifest_immutable"


def test_accepted_commit_and_verify(tmp_path):
    att, media = _mk_attempt(tmp_path)
    sealed = eio.seal_manifest(att, {
        "package": "video", "status": "accepted",
        "artifacts": [eio.artifact_record(media, role="candidate")],
        # manifest 与指针必须记**同一个**请求身份（缺了不再跳过比较）
        "request_key": "k1",
        "checks": [{"name": "decode", "status": "pass"}],
    })
    pkg = tmp_path / "pkg"
    back = eio.commit_accepted(pkg, attempt_id="a-1", manifest_path=att / eio.MANIFEST_FILENAME,
                               manifest_sha256=sealed["manifest_sha256"], input_key="k1")
    assert back["attempt_id"] == "a-1"
    ok = eio.verify_accepted(pkg, required_checks=("decode",), expect_input_key="k1")
    assert ok["manifest"]["status"] == "accepted"

    # 篡改产物 ⇒ 摘要不符拒收
    media.write_bytes(b"tampered")
    with pytest.raises(eio.EnhancementError) as ei:
        eio.verify_accepted(pkg)
    assert ei.value.code == "accepted_artifact_digest_mismatch"

    # required_checks 未满足 ⇒ 拒收并保留残余项
    media.write_bytes(b"fake-mp4-bytes")
    with pytest.raises(eio.EnhancementError) as ei:
        eio.verify_accepted(pkg, required_checks=("semantic",))
    assert ei.value.code == "required_checks_unmet" and ei.value.fields["residual_issues"] == ["semantic"]


def test_accepted_deadline_before_replace_keeps_old_pointer(tmp_path):
    pkg = tmp_path / "pkg"
    att1, media1 = _mk_attempt(tmp_path, "a-1")
    s1 = eio.seal_manifest(att1, {"package": "video", "status": "accepted",
                                  "artifacts": [eio.artifact_record(media1)], "checks": []})
    eio.commit_accepted(pkg, attempt_id="a-1", manifest_path=att1 / eio.MANIFEST_FILENAME,
                        manifest_sha256=s1["manifest_sha256"], input_key="k1")
    before = eio.read_accepted(pkg)

    att2, media2 = _mk_attempt(tmp_path, "a-2")
    s2 = eio.seal_manifest(att2, {"package": "video", "status": "accepted",
                                  "artifacts": [eio.artifact_record(media2)], "checks": []})

    class D:
        def expired(self):
            return True

    with pytest.raises(eio.EnhancementError) as ei:
        eio.commit_accepted(pkg, attempt_id="a-2", manifest_path=att2 / eio.MANIFEST_FILENAME,
                            manifest_sha256=s2["manifest_sha256"], input_key="k2", deadline=D())
    assert ei.value.code == "deadline_exhausted"
    assert eio.read_accepted(pkg) == before                   # 原指针一字节未改
    assert not list(pkg.glob("*.tmp"))                        # 无残留临时文件


def test_accepted_readback_mismatch_is_unconfirmed_not_rollback(tmp_path):
    att, media = _mk_attempt(tmp_path)
    eio.seal_manifest(att, {"package": "video", "status": "accepted",
                            "artifacts": [eio.artifact_record(media)], "checks": []})
    pkg = tmp_path / "pkg"
    with pytest.raises(eio.CommitUnconfirmedError) as ei:
        eio.commit_accepted(pkg, attempt_id="a-1", manifest_path=att / eio.MANIFEST_FILENAME,
                            manifest_sha256="0" * 64, input_key="k1")
    assert ei.value.exit_code == eio.EXIT_COMMIT_UNCONFIRMED
    payload = ei.value.payload()
    assert payload["replacement_performed"] is True and payload["confirmed"] is False
    # 指针**已被替换**（读回不一致），不做盲回滚
    assert eio.read_accepted(pkg) is not None


def test_cleanup_only_tmp_inside_attempt(tmp_path):
    att, media = _mk_attempt(tmp_path)
    (att / "input.json.tmp").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.tmp"
    outside.write_text("keep", encoding="utf-8")
    removed = eio.cleanup_attempt_tmp(att)
    assert removed and not (att / "input.json.tmp").exists()
    assert outside.exists()
