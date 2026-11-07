# -*- coding: utf-8 -*-
"""WP0：发布事务的**并发读者**压测（零模型调用）.

读者在任何时刻都不得读到半截 JSON，也不得读到"稿件 revision 与提交清单
revision 不一致"的组合。写者用与生产相同的原子替换，并在中间插入一次
"崩溃未提交 + recover_pending"的恢复流程。
"""
import json
import threading
import time

import pytest

from shared.publish_txn import (PublishTransaction, Publishing, read_consistent,
                                recover_pending)


from shared.json_io import atomic_dump_json


def _dump(path, payload):
    atomic_dump_json(path, payload, ensure_ascii=False)


def _write_rev(chapter, rev):
    _dump(chapter / "script_result.json", {"title": "t", "script_revision": rev, "shots": []})
    _dump(chapter / "step01_commit.json",
          {"status": "COMMITTED", "script_revision": rev, "blocking_count": 0})


def test_readers_never_see_torn_or_mixed_state(tmp_path):
    _write_rev(tmp_path, "revA")
    stop = threading.Event()
    errors = []
    reads = {"n": 0}
    lock = threading.Lock()

    def reader():
        while not stop.is_set():
            try:
                pair = read_consistent(tmp_path, max_wait=2.0)
                script = pair["script_result.json"]
                commit = pair["step01_commit.json"]
            except Publishing:
                # 发布中：有界等待没拿到一致组，读者重试（不是数据错误）
                continue
            except Exception as exc:          # 其它读取错误才算失败
                with lock:
                    errors.append("read: %s" % exc)
                continue
            except json.JSONDecodeError as exc:      # 半截 JSON = 撕裂读
                with lock:
                    errors.append("decode: %s" % exc)
                continue
            if script.get("script_revision") != commit.get("script_revision"):
                with lock:
                    errors.append("mixed: %s != %s"
                                  % (script.get("script_revision"), commit.get("script_revision")))
            with lock:
                reads["n"] += 1

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for th in threads:
        th.start()
    try:
        for rev in ("revB", "revC", "revD"):
            txn = PublishTransaction(tmp_path, label="w")
            txn.begin()
            _write_rev(tmp_path, rev)
            txn.commit()
            time.sleep(0.05)
    finally:
        stop.set()
        for th in threads:
            th.join(timeout=5)

    assert reads["n"] > 0
    assert errors == [], errors[:3]


def test_crash_leaves_recoverable_state_and_blocks_new_txn(tmp_path):
    _write_rev(tmp_path, "revA")
    # 写者写到一半"崩溃"：事务已 begin，产物被替换，但没有 commit
    txn = PublishTransaction(tmp_path, label="crash")
    txn.begin()
    _write_rev(tmp_path, "revB")
    assert (tmp_path / ".publish.lock").exists()

    # 恢复（模拟下一个进程启动）：必须完整还原，且允许新事务
    out = recover_pending(tmp_path)
    assert out["ok"] is True, out
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["script_revision"] == "revA"
    assert not (tmp_path / ".publish.lock").exists()

    txn2 = PublishTransaction(tmp_path, label="after")
    txn2.begin()
    _write_rev(tmp_path, "revC")
    txn2.commit()
    assert json.loads((tmp_path / "step01_commit.json").read_text(encoding="utf-8"))["script_revision"] == "revC"


def test_recovery_failure_blocks_publish(tmp_path):
    _write_rev(tmp_path, "revA")
    (tmp_path / "step01_publish_journal.json").write_text(json.dumps({
        "state": "begun", "backup_dir": "_publish_backup_missing",
        "backed_up": ["script_result.json"], "created": [],
    }), encoding="utf-8")
    with pytest.raises(Exception):
        PublishTransaction(tmp_path, label="blocked").begin()
