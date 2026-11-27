# -*- coding: utf-8 -*-
"""S06/M01：Step04 正式发布的可恢复批次（成片 + 字幕 + 质量报告）.

背景（2026-11-27 复核第一轮）：发布曾按 ``os.replace(final)`` → ``os.replace(srt)`` →
随后单独写质量报告执行，后两步失败会留下混合版本。第二轮的崩溃回放又指出两个缺陷，
本版据此重写：

1. **先持久化"备份意图"，再执行备份移动**：journal 在移动前就记录目标旧摘要、
   计划备份路径与 ``backup_pending`` 状态；崩溃落在"已移动、未记录"的窗口时，
   恢复仍能凭 journal 找到备份并校验摘要。
2. **恢复必须校验摘要与终态**：备份缺失、备份摘要不符、目标处于未知版本时，
   一律 ``publish_unconfirmed`` 并保留 journal/备份，**不得**宣布 ``recovered``。
3. **同一目标锁覆盖恢复、旧状态读取、发布、确认与清理**：调用方在入口取得锁后把
   锁对象传给 :func:`recover_pending` / :func:`publish_batch`；两者不再各自重复加锁，
   发布成功后的备份与 journal 清理也在锁内完成。
4. 失败按 journal 反向恢复；恢复失败保留材料，下次进入同一目标先恢复再开始。
5. 多个 ``os.replace`` 不是多文件原子事务；本模块提供的是**可恢复批次**，不是原子性。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

SCHEMA = "step04_publication.v1"
JOURNAL_FILENAME = "publish_journal.json"
ENV_FAULT = "STEP04_PUBLISH_FAULT"

STATUS_PREPARING = "preparing"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLISHED = "published"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_ROLLBACK_FAILED = "rollback_failed"
STATUS_RECOVERED = "recovered"

STATE_PENDING = "pending"
STATE_BACKUP_PENDING = "backup_pending"
STATE_BACKED_UP = "backed_up"
STATE_ABSENT = "absent"
STATE_PUBLISH_PENDING = "publish_pending"
STATE_PUBLISHED = "published"
STATE_ROLLED_BACK = "rolled_back"
STATE_RESTORED = "restored"

_CODE_FAILED = "publish_failed"
_CODE_UNCONFIRMED = "publish_unconfirmed"
_CODE_BUSY = "publish_busy"
_CODE_CONFLICT = "publish_conflict"


class PublicationError(RuntimeError):
    """发布失败（结构化：code / status / journal_path / 是否已回滚）."""

    def __init__(self, code: str, message: str, *, status: str = "",
                 journal_path: str | None = None, exit_code: int = 3):
        super().__init__(message)
        self.code = str(code)
        self.status = str(status)
        self.journal_path = journal_path
        self.exit_code = int(exit_code)

    def to_dict(self) -> dict:
        return {"code": self.code, "status": self.status,
                "journal_path": self.journal_path, "message": str(self)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp%08d" % (time.time_ns() % 10**8))
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _default_replace(src, dst) -> None:
    os.replace(src, dst)


def _faults() -> set[str]:
    raw = str(os.getenv(ENV_FAULT, "") or "").strip().lower()
    return {part.strip() for part in raw.split(",") if part.strip()}


def read_journal(journal_path: Path | str) -> dict | None:
    path = Path(journal_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def publish_lock(work_dir: Path | str):
    """目标级发布锁（调用方持锁覆盖恢复→发布→清理）.

    用法::

        with publish_lock(work_dir) as lock:
            recover_pending(journal, work_dir=work_dir, lock=lock)
            publish_batch(moves, work_dir=work_dir, lock=lock)
    """
    from shared.enhancement_io import AttemptLock

    @contextlib.contextmanager
    def _cm():
        lock = AttemptLock(Path(work_dir) / ".publish.lock", purpose="step04-publish")
        lock.acquire()
        try:
            yield lock
        finally:
            lock.release()

    return _cm()


def _acquire(work_dir: Path, lock):
    """返回 (锁对象, 是否本函数持有)；调用方已给锁时不重复加锁."""
    if lock is not None:
        return lock, False
    from shared.enhancement_io import AttemptLock, BusyError
    holder = AttemptLock(Path(work_dir) / ".publish.lock", purpose="step04-publish")
    try:
        holder.acquire()
    except BusyError as exc:
        raise PublicationError(_CODE_BUSY,
                               "另一个发布/恢复进程正在写入同一目标：%s" % exc,
                               status="busy", exit_code=5) from exc
    return holder, True


def _release(lock, owned: bool) -> None:
    if owned and lock is not None:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 - 释放失败不掩盖主错误
            pass


def _cleanup_backups(entries) -> None:
    for entry in entries:
        backup = Path(entry["backup"]) if entry.get("backup") else None
        if backup is None:
            continue
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass


def _conflict(target: Path, journal_path: Path, detail: str = "") -> PublicationError:
    return PublicationError(
        _CODE_CONFLICT,
        "发布恢复冲突：目标 %s 的当前版本无法与 journal 对上%s；不覆盖，保留材料"
        % (target, ("（%s）" % detail) if detail else ""),
        status=STATUS_ROLLBACK_FAILED, journal_path=str(journal_path))


def _unconfirmed(journal_path: Path, detail: str) -> PublicationError:
    return PublicationError(
        _CODE_UNCONFIRMED,
        "发布未确认：%s；备份与 journal 保留在 %s" % (detail, journal_path),
        status=STATUS_ROLLBACK_FAILED, journal_path=str(journal_path))


def _backup_ok(entry: dict) -> bool:
    backup = Path(entry["backup"]) if entry.get("backup") else None
    if backup is None or not backup.is_file():
        return False
    before = str(entry.get("target_sha_before") or "")
    return bool(before) and _sha256(backup) == before


def recover_pending(journal_path: Path | str, *, work_dir=None, lock=None,
                    logger=None) -> dict | None:
    """处理上一次未完成的发布批次（幂等）；无 journal 时返回 None.

    恢复不宣布成功，除非每个条目都满足：
    - 备份存在且摘要等于 ``target_sha_before``（如有旧文件）；
    - 目标已恢复为旧摘要，或按记录确认"原本就不存在"；
    - 目标上的新摘要只可能是 ``staged_sha``（可安全撤掉）。

    任一条件不满足 ⇒ 抛 ``publish_unconfirmed`` / ``publish_conflict``，保留 journal。
    """
    path = Path(journal_path)
    data = read_journal(path)
    if data is None:
        return None
    work = Path(work_dir) if work_dir is not None else path.parent
    holder, owned = _acquire(work, lock)
    try:
        return _recover_locked(path, data, logger=logger)
    finally:
        _release(holder, owned)


def _recover_locked(path: Path, data: dict, *, logger=None) -> dict:
    status = str(data.get("status") or "")
    entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
    if status == STATUS_PUBLISHED:
        _cleanup_backups(entries)
        path.unlink(missing_ok=True)
        return {"status": STATUS_RECOVERED, "action": "cleanup_published",
                "entries": len(entries)}
    for entry in reversed(entries):
        target = Path(entry.get("target") or "")
        staged = Path(entry["staged"]) if entry.get("staged") else None
        before = str(entry.get("target_sha_before") or "")
        staged_sha = str(entry.get("staged_sha") or "")
        state = str(entry.get("state") or "")
        target_exists = target.is_file()
        current = _sha256(target) if target_exists else ""
        backup_ok = _backup_ok(entry)

        if state == STATE_BACKUP_PENDING:
            # 意图已记录、移动可能发生也可能没发生：靠 target/backup 的实际状态判定。
            if target_exists and (not before or current == before):
                if entry.get("backup") and Path(entry["backup"]).is_file() and not backup_ok:
                    raise _conflict(target, path, "备份摘要与旧值不符")
                if entry.get("backup"):
                    Path(entry["backup"]).unlink(missing_ok=True)
                entry["state"] = STATE_RESTORED
                continue
            if not target_exists and backup_ok:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(Path(entry["backup"]), target)
                entry["state"] = STATE_RESTORED
                continue
            raise _unconfirmed(
                path, "备份意图条目的目标缺失且无有效备份：%s" % target)
        if state == STATE_BACKED_UP:
            if not backup_ok:
                raise _unconfirmed(path, "备份缺失或摘要不符：%s" % target)
            if target_exists:
                if before and current == before:
                    Path(entry["backup"]).unlink(missing_ok=True)
                    entry["state"] = STATE_RESTORED
                    continue
                if staged_sha and current == staged_sha:
                    target.unlink()
                else:
                    raise _conflict(target, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(entry["backup"]), target)
            entry["state"] = STATE_RESTORED
            continue
        if state in (STATE_PUBLISHED, STATE_PUBLISH_PENDING):
            if target_exists:
                if staged_sha and current == staged_sha:
                    target.unlink()
                elif before and current == before:
                    pass
                else:
                    raise _conflict(target, path)
            if entry.get("backup"):
                if not backup_ok:
                    raise _unconfirmed(path, "备份缺失或摘要不符：%s" % target)
                if target.is_file():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(Path(entry["backup"]), target)
                entry["state"] = STATE_RESTORED
            elif before:
                raise _unconfirmed(path, "目标缺失但 journal 无备份：%s" % target)
            continue
        # pending / absent / 其它未开始状态
        if target_exists:
            if before and current == before:
                pass
            elif staged_sha and current == staged_sha:
                target.unlink()
            elif before or staged_sha:
                raise _conflict(target, path)
        if entry.get("backup") and Path(entry["backup"]).is_file():
            if not backup_ok:
                raise _conflict(target, path, "备份摘要与旧值不符")
            if target.is_file():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(entry["backup"]), target)
            entry["state"] = STATE_RESTORED
    data["status"] = STATUS_RECOVERED
    data["recovered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _atomic_json(path, data)
    _cleanup_backups(entries)
    path.unlink(missing_ok=True)
    if logger is not None:
        try:
            logger.warn("[PUBLISH] 已恢复未完成发布批次（%d 个目标）" % len(entries))
        except Exception:
            pass
    return {"status": STATUS_RECOVERED, "action": "restored",
            "entries": len(entries)}


def publish_batch(moves, *, work_dir, journal_path=None, batch_id: str = "",
                  lock=None, logger=None, replace=None) -> dict:
    """按批次发布 ``moves``（``[{"role","staged","target"}]``），失败自动回滚.

    ``lock`` 由调用方提供时必须已持有（覆盖恢复→发布→清理的同一范围）。
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    journal_path = Path(journal_path) if journal_path else work_dir / JOURNAL_FILENAME
    replace = replace or _default_replace
    entries: list[dict] = []
    for move in moves or ():
        staged = Path(move["staged"])
        target = Path(move["target"])
        if not staged.is_file():
            raise PublicationError(_CODE_FAILED,
                                   "待发布文件不存在：%s（%s）"
                                   % (staged, move.get("role") or ""),
                                   status=STATUS_PREPARING,
                                   journal_path=str(journal_path))
        entries.append({"role": str(move.get("role") or ""),
                        "staged": str(staged), "target": str(target),
                        "staged_sha": _sha256(staged),
                        "target_sha_before": _sha256(target) if target.is_file() else "",
                        "backup": "", "existed": target.is_file(),
                        "state": STATE_PENDING})
    if not entries:
        raise PublicationError(_CODE_FAILED, "发布批次为空",
                               status=STATUS_PREPARING, journal_path=str(journal_path))

    holder, owned = _acquire(work_dir, lock)
    journal = {"schema": SCHEMA, "batch_id": batch_id or "batch",
               "status": STATUS_PREPARING, "entries": entries,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    backup_root = work_dir / ("backup-" + (batch_id or "batch"))
    faults = _faults()

    def _do(role: str, src, dst) -> None:
        if role in faults:
            raise OSError("注入的发布故障（%s=%s）" % (ENV_FAULT, role))
        replace(src, dst)

    try:
        _atomic_json(journal_path, journal)
        # ---- 阶段 1：先持久化备份意图，再移动 ----
        journal["status"] = STATUS_PUBLISHING
        for entry in entries:
            target = Path(entry["target"])
            if not target.exists():
                entry["state"] = STATE_ABSENT
                _atomic_json(journal_path, journal)
                continue
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / ("%s-%s" % (entry["role"] or "target", target.name))
            if backup.exists():
                backup.unlink()
            entry["backup"] = str(backup)
            entry["state"] = STATE_BACKUP_PENDING
            _atomic_json(journal_path, journal)     # 意图先落盘
            _do(entry["role"], target, backup)
            if _sha256(backup) != entry["target_sha_before"]:
                raise OSError("备份读回校验失败：%s" % backup)
            if "crash" in faults:
                # 模拟"移动完成、状态未落盘"的崩溃窗口：journal 仍是 backup_pending
                raise SystemExit(99)
            entry["state"] = STATE_BACKED_UP
            _atomic_json(journal_path, journal)
        # ---- 阶段 2：先记录发布意图，再替换 + 读回验证 ----
        for entry in entries:
            staged = Path(entry["staged"])
            target = Path(entry["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            entry["state"] = STATE_PUBLISH_PENDING
            _atomic_json(journal_path, journal)     # 意图先落盘
            _do(entry["role"], staged, target)
            entry["state"] = STATE_PUBLISHED
            _atomic_json(journal_path, journal)
            if not target.is_file() or _sha256(target) != entry["staged_sha"]:
                raise OSError("发布后读回校验失败：%s" % target)
        journal["status"] = STATUS_PUBLISHED
        _atomic_json(journal_path, journal)
        # 清理也在锁内：成功后不让第二个进程看到"已发布但 journal 还在"的窗口
        _cleanup_backups(entries)
        journal_path.unlink(missing_ok=True)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一走 rollback 并转 PublicationError
        raise _rollback(entries, journal_path, journal, replace, logger,
                        cause=exc) from exc
    finally:
        _release(holder, owned)

    if logger is not None:
        try:
            logger.info("[PUBLISH] 批次发布完成: %s"
                        % ", ".join("%s=%s" % (e["role"], e["target"])
                                    for e in entries))
        except Exception:
            pass
    return {"status": STATUS_PUBLISHED, "journal_path": str(journal_path),
            "entries": entries}


def _rollback(entries, journal_path: Path, journal: dict, replace, logger,
              cause: Exception) -> PublicationError:
    """反向恢复本批已替换目标；返回应抛出的 PublicationError."""
    errors: list[str] = []
    rollback_fault = "rollback" in _faults()
    for entry in reversed(entries):
        if rollback_fault:
            errors.append("%s: 注入的恢复故障" % entry.get("target"))
            continue
        target = Path(entry["target"])
        staged = Path(entry["staged"])
        backup = Path(entry["backup"]) if entry.get("backup") else None
        try:
            if entry.get("state") in (STATE_PUBLISHED, STATE_PUBLISH_PENDING):
                if target.is_file():
                    if staged.exists():
                        target.unlink()
                    else:
                        replace(target, staged)
                entry["state"] = STATE_ROLLED_BACK
            if backup is not None and backup.is_file():
                if not _backup_ok(entry):
                    raise OSError("备份摘要不符：%s" % backup)
                if target.is_file():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                replace(backup, target)
                entry["state"] = STATE_RESTORED
        except Exception as exc:  # noqa: BLE001 - 记录并继续尽力恢复其余目标
            errors.append("%s: %s" % (target, exc))
    if errors:
        journal["status"] = STATUS_ROLLBACK_FAILED
        journal["rollback_errors"] = errors
        _atomic_json(journal_path, journal)
        if logger is not None:
            try:
                logger.error("[PUBLISH] 恢复失败（发布未确认）：%s" % errors)
            except Exception:
                pass
        return PublicationError(
            _CODE_UNCONFIRMED,
            "发布未确认：部分目标无法恢复（%s）；备份与 journal 保留在 %s"
            % ("；".join(errors[:3]), journal_path),
            status=STATUS_ROLLBACK_FAILED, journal_path=str(journal_path))
    journal["status"] = STATUS_ROLLED_BACK
    _atomic_json(journal_path, journal)
    if logger is not None:
        try:
            logger.error("[PUBLISH] 发布失败，已恢复本批全部目标: %s" % cause)
        except Exception:
            pass
    return PublicationError(
        _CODE_FAILED,
        "发布失败，已恢复本批全部目标（既有成片/字幕/报告保持原样）: %s" % cause,
        status=STATUS_ROLLED_BACK, journal_path=str(journal_path))


__all__ = ["ENV_FAULT", "JOURNAL_FILENAME", "PublicationError", "SCHEMA",
           "publish_batch", "publish_lock", "read_journal", "recover_pending"]
