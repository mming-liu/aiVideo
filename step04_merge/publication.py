# -*- coding: utf-8 -*-
"""S06/M01：Step04 正式发布的可恢复批次（成片 + 字幕 + 质量报告）.

背景（2026-11-27 复核 P1）：发布曾按 ``os.replace(final)`` → ``os.replace(srt)`` →
随后单独写质量报告执行。后两步失败会留下"新成片 + 旧字幕/旧报告"的混合版本，
且被宣布成功的日志已经打出。**多个 ``os.replace`` 不是多文件原子事务**，因此这里
显式建模为可恢复批次：

1. 每个目标先备份旧内容（或记录"原先不存在"），再替换；进度逐步写入 journal；
2. 任一步失败按 journal 反向恢复已替换目标，恢复成功报 ``publish_failed``；
3. 恢复失败报 ``publish_unconfirmed``（退出码 3），保留备份与 journal；
4. 下次进入同一发布目标时先 :func:`recover_pending`，处理未完成批次后才允许新发布；
5. 每批目标各自加锁（复用 :class:`shared.enhancement_io.AttemptLock`），第二个
   写入者得到 ``publish_busy``，不会破坏第一批次。

本模块只做文件事务，不判定内容是否该发布；校验与终检仍由调用方完成。
"""
from __future__ import annotations

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


def _cleanup_backups(entries) -> None:
    for entry in entries:
        backup = entry.get("backup")
        if not backup:
            continue
        try:
            Path(backup).unlink(missing_ok=True)
        except OSError:
            pass


def _conflict(target: Path, journal_path: Path) -> PublicationError:
    return PublicationError(
        _CODE_CONFLICT,
        "发布恢复冲突：目标 %s 的当前版本既非旧值也非新值；不覆盖，保留材料供人工判断"
        % target, status=STATUS_ROLLBACK_FAILED, journal_path=str(journal_path))


def recover_pending(journal_path: Path | str, *, logger=None) -> dict | None:
    """处理上一次未完成的发布批次（幂等）；无 journal 时返回 None.

    - ``published``：只清备份与 journal；
    - 其余状态：把已替换的目标恢复为旧内容（或删除新内容并恢复备份）；
    - 目标版本无法判定（既非旧值也非新值）⇒ ``publish_conflict``，不覆盖。
    """
    path = Path(journal_path)
    data = read_journal(path)
    if data is None:
        return None
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
        backup = Path(entry["backup"]) if entry.get("backup") else None
        staged_sha = str(entry.get("staged_sha") or "")
        before_sha = str(entry.get("target_sha_before") or "")
        if target.is_file():
            current = _sha256(target)
            # 旧值与新值**字节相同**时无法靠摘要区分：按"已经是旧值"处理，
            # 不得删除目标（否则恢复会把正常文件删掉——2026-11-27 实测踩到）。
            if before_sha and current == before_sha:
                pass
            elif staged_sha and current == staged_sha:
                target.unlink()          # 这是本批新文件，先撤掉
            else:
                raise _conflict(target, path)
        if backup is not None and backup.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)
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
                  logger=None, replace=None) -> dict:
    """按批次发布 ``moves``（``[{"role","staged","target"}]``），失败自动回滚.

    Returns:
        ``{"status": "published", "journal_path", "entries": [...]}``

    Raises:
        PublicationError: ``publish_busy`` / ``publish_failed`` / ``publish_unconfirmed``
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
                        "state": "pending"})
    if not entries:
        raise PublicationError(_CODE_FAILED, "发布批次为空",
                               status=STATUS_PREPARING, journal_path=str(journal_path))

    from shared.enhancement_io import AttemptLock, BusyError
    lock = AttemptLock(work_dir / ".publish.lock", purpose="step04-publish")
    try:
        lock.acquire()
    except BusyError as exc:
        raise PublicationError(_CODE_BUSY,
                               "另一个发布进程正在写入同一目标：%s" % exc,
                               status="busy", journal_path=str(journal_path),
                               exit_code=5) from exc

    journal = {"schema": SCHEMA, "batch_id": batch_id or "batch",
               "status": STATUS_PREPARING, "entries": entries,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    backup_root = work_dir / ("backup-" + (batch_id or "batch"))
    _fault = _faults()

    def _do(role: str, src, dst) -> None:
        if role in _fault:
            raise OSError("注入的发布故障（%s=%s）" % (ENV_FAULT, role))
        replace(src, dst)

    try:
        _atomic_json(journal_path, journal)
        # ---- 阶段 1：备份旧内容（或记录原先不存在） ----
        journal["status"] = STATUS_PUBLISHING
        for entry in entries:
            target = Path(entry["target"])
            if target.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = backup_root / ("%s-%s" % (entry["role"] or "target",
                                                   target.name))
                if backup.exists():
                    backup.unlink()
                _do(entry["role"], target, backup)
                entry["backup"] = str(backup)
                entry["state"] = "backed_up"
            else:
                entry["state"] = "absent"
            _atomic_json(journal_path, journal)
        # ---- 阶段 2：替换新内容 + 逐项读回验证 ----
        for entry in entries:
            staged = Path(entry["staged"])
            target = Path(entry["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            _do(entry["role"], staged, target)
            entry["state"] = "published"
            _atomic_json(journal_path, journal)
            if not target.is_file() or _sha256(target) != entry["staged_sha"]:
                raise OSError("发布后读回校验失败：%s" % target)
        journal["status"] = STATUS_PUBLISHED
        _atomic_json(journal_path, journal)
    except Exception as exc:  # noqa: BLE001 - 统一走 rollback 并转 PublicationError
        raise _rollback(entries, journal_path, journal, replace, logger,
                        cause=exc) from exc
    finally:
        lock.release()

    _cleanup_backups(entries)
    journal_path.unlink(missing_ok=True)
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
    rollback_fault = "rollback" in _faults()
    errors: list[str] = []
    for entry in reversed(entries):
        if rollback_fault:
            errors.append("%s: 注入的恢复故障" % entry.get("target"))
            continue
        target = Path(entry["target"])
        staged = Path(entry["staged"])
        backup = Path(entry["backup"]) if entry.get("backup") else None
        try:
            if entry.get("state") == "published" and target.is_file():
                if staged.exists():
                    target.unlink()
                else:
                    replace(target, staged)
                entry["state"] = "rolled_back"
            if backup is not None and backup.is_file():
                if target.exists():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                replace(backup, target)
                entry["state"] = "restored"
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
           "publish_batch", "read_journal", "recover_pending"]
