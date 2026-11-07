# -*- coding: utf-8 -*-
"""S06：正式产物发布的**事务边界**（2026-11-07，复核修订）.

问题：提交点按文件顺序替换 audit → quality → script → manifest，单个文件的原子写
不能保证"整组同时切换"；进程在中途被杀会留下身份不一致的组合，且没有回滚记录。

本模块提供：

    txn = PublishTransaction(chapter_dir, expected_old=None, label=run_id)
    txn.begin()      # 恢复未完成 journal → 取锁 → 备份**真实产物清单** → journal=begun
    ...写产物...
    txn.commit()     # journal=committed
    # 任一步失败：txn.rollback()   # 全量还原；部分失败 → rollback_failed 且**不释放锁**

复核修订（2026-11-07）：
  · 备份清单直接取 shared.paths.STEP01_ARTIFACT_FILES（script_result.json 等），
    不再使用不存在的 step01_script.json；
  · journal 写失败**不再吞掉**：写不下去就不允许开始/提交；
  · 回滚部分失败 → 记 rollback_failed、保留锁、抛错，绝不冒充 rolled_back；
  · 锁按 pid 存活判断（Windows 用 OpenProcess），年龄只作兜底；
  · 提供 recover_pending()：崩溃后按 journal 把备份组还原。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from shared.paths import STEP01_ARTIFACT_FILES

JOURNAL_NAME = "step01_publish_journal.json"
LOCK_NAME = ".publish.lock"
LOCK_STALE_SEC = 600

#: 发布事务覆盖的文件组：三份正式产物 + 提交清单 + 采纳凭据。
PUBLISH_FILES = tuple(STEP01_ARTIFACT_FILES) + ("step01_commit.json", "step01_adoption.json")


class PublishError(RuntimeError):
    """发布事务无法开始/完成（锁被占用、备份失败、回滚不完整）。"""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    # Windows 共享冲突：读者握着目标文件时 replace 会失败，短重试后仍失败才抛。
    for _attempt in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    """进程是否还活着（Windows 用 OpenProcess，其它平台用 kill(0)）."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False

class PublishTransaction:
    def __init__(self, chapter_dir, *, expected_old=None, label: str = "") -> None:
        self.chapter = Path(chapter_dir)
        self.expected_old = str(expected_old or "")
        self.label = str(label or "")
        self.lock_path = self.chapter / LOCK_NAME
        self.journal_path = self.chapter / JOURNAL_NAME
        self.backup_dir = self.chapter / ("_publish_backup_%s_%d"
                                          % (time.strftime("%Y%m%d_%H%M%S"), os.getpid()))
        self.existing: list = []
        self.created: list = []
        self._locked = False
        self._begun = False
        self._done = False
        self.old_revision = ""
        self.recovered: dict = {}

    # -- 锁 ---------------------------------------------------------------
    def _lock_owner(self):
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return (0, "")
        parts = raw.split(None, 1)
        try:
            return (int(parts[0]), parts[1] if len(parts) > 1 else "")
        except (ValueError, IndexError):
            return (0, "")

    def _acquire(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("%d %s" % (os.getpid(), self.label)).encode("utf-8"))
                os.close(fd)
                self._locked = True
                return
            except FileExistsError:
                pid, who = self._lock_owner()
                if pid and _pid_alive(pid) and pid != os.getpid():
                    raise PublishError(
                        "发布锁被活进程占用（pid=%d %s）：另一个进程正在发布本章"
                        % (pid, who))
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except OSError:
                    age = 0
                if pid and _pid_alive(pid) and age <= LOCK_STALE_SEC:
                    raise PublishError("发布锁被占用（pid=%d，%.0fs）" % (pid, age))
                # 持锁进程已死 / 锁内容不可解析：回收一次
                try:
                    self.lock_path.unlink()
                except OSError as exc:
                    raise PublishError("陈旧发布锁无法回收: %s" % exc)
        raise PublishError("发布锁无法取得: %s" % self.lock_path)

    def _release(self) -> None:
        if self._locked:
            try:
                self.lock_path.unlink()
            except OSError:
                pass
            self._locked = False

    # -- journal ----------------------------------------------------------
    def _journal(self, state: str, detail: str = "") -> None:
        payload = {"state": state, "label": self.label, "pid": os.getpid(),
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "backup_dir": self.backup_dir.name,
                   "backed_up": self.existing, "created": self.created,
                   "old_revision": self.old_revision, "detail": detail}
        try:
            _atomic_write_bytes(self.journal_path,
                                json.dumps(payload, ensure_ascii=False,
                                           indent=1).encode("utf-8"))
        except Exception as exc:                                  # noqa: BLE001
            raise PublishError("发布 journal 无法写入（不允许在无记录的情况下发布）: %s"
                               % exc) from exc

    # -- 事务 -------------------------------------------------------------
    def begin(self) -> "PublishTransaction":
        self.chapter.mkdir(parents=True, exist_ok=True)
        self._acquire()
        try:
            # 上一次崩溃留下的未完成事务：先按备份还原，再开始本次发布。
            self.recovered = recover_pending(self.chapter, lock_held=True)
            if not self.recovered.get("ok"):
                raise PublishError(
                    "上一次发布未恢复完整（state=%s，失败: %s）——禁止开始新事务；"
                    "旧 journal 与备份保留，先修复恢复条件"
                    % (self.recovered.get("state"),
                       "; ".join(self.recovered.get("failed") or [])))
            if self.expected_old:
                from shared.quality_gate import compute_script_revision
                p = self.chapter / "script_result.json"
                if p.exists():
                    try:
                        self.old_revision = compute_script_revision(
                            json.loads(p.read_text(encoding="utf-8")))
                    except Exception:
                        self.old_revision = ""
                    if self.old_revision and self.old_revision != self.expected_old:
                        raise PublishError(
                            "旧正式产物 revision 不符（期望 %s，实际 %s）——拒绝覆盖"
                            % (self.expected_old[:12], self.old_revision[:12]))
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            for name in PUBLISH_FILES:
                src = self.chapter / name
                if src.exists():
                    shutil.copy2(src, self.backup_dir / name)
                    self.existing.append(name)
                else:
                    self.created.append(name)
            self._begun = True
            self._journal("begun")
            return self
        except Exception:
            self._release()
            raise

    def commit(self) -> None:
        if not self._begun:
            raise PublishError("事务未 begin 就 commit")
        try:
            self._journal("committed")
        except PublishError:
            self.rollback("commit 时 journal 写失败")
            raise
        self._done = True
        self._release()

    def rollback(self, detail: str = "") -> list:
        """把发布文件组恢复到 begin 时的状态；部分失败 → 保留锁并抛错."""
        touched: list = []
        failures: list = []
        if not self._begun:
            self._release()
            return touched
        for name in PUBLISH_FILES:
            src = self.backup_dir / name
            dst = self.chapter / name
            try:
                if src.exists():
                    _atomic_write_bytes(dst, src.read_bytes())
                    touched.append(name)
                elif name in self.created and dst.exists():
                    dst.unlink()
                    touched.append(name + "(removed)")
            except Exception as exc:                             # noqa: BLE001
                failures.append("%s: %s" % (name, exc))
        if failures:
            # **绝不**把不完整回滚记成 rolled_back，也不释放锁：留着让恢复入口处理。
            _jnote = ""
            try:
                self._journal("rollback_failed", detail + "；失败项: " + "; ".join(failures))
            except PublishError as _je:
                # journal 也写不下去：这是**双重失败**，必须原样暴露，不能吞。
                _jnote = "；journal 写入也失败: %s" % _je
            raise PublishError("回滚不完整（锁保留，等待 recover_pending）：%s%s"
                               % ("; ".join(failures), _jnote))
        self._journal("rolled_back", detail)
        self._done = True
        self._release()
        return touched

    def __enter__(self):
        return self.begin()

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and not self._done:
            self.rollback("异常退出: %s" % exc)
        elif not self._done:
            self.rollback("未 commit 即退出")
        return False




class Publishing(PublishError):
    """发布进行中：读者在有界等待内没有拿到一致的一组文件."""


def read_consistent(chapter_dir, names=("script_result.json", "step01_commit.json"),
                    *, max_wait: float = 2.0, poll: float = 0.01):
    """**一致读**：把一组正式文件读成同一 revision，或明确报"发布中".

    文件级原子替换不能保证**整组**同时切换：读者可能先读到新稿、再读到旧清单。
    这里用有界重试直到两边的 script_revision 一致；超时抛 Publishing，让调用方
    返回"暂不可读"而不是把混合状态当成结果。
    """
    chapter = Path(chapter_dir)
    deadline = time.monotonic() + max(0.0, float(max_wait))
    last = ""
    while True:
        try:
            payloads = {n: json.loads((chapter / n).read_text(encoding="utf-8"))
                        for n in names}
        except (FileNotFoundError, PermissionError, OSError,
                json.JSONDecodeError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
            payloads = None
        if payloads is not None:
            revs = {str(p.get("script_revision") or "")
                    for p in payloads.values() if isinstance(p, dict)}
            revs.discard("")
            if len(revs) <= 1:
                return payloads
            last = "revision 不一致: %s" % sorted(revs)
        if time.monotonic() >= deadline:
            raise Publishing("发布进行中（%s 内未取得一致的一组文件：%s）"
                             % (max_wait, last))
        time.sleep(poll)

def recover_pending(chapter_dir, *, lock_held: bool = False) -> dict:
    """按 journal 恢复未完成的发布事务.

    返回 {ok, state, restored, removed, failed}：
      ok=True  —— 没有未完成事务，或已完整恢复（state=recovered）；
      ok=False —— 任何未恢复完整的状态（journal 不可读、备份缺失、文件恢复失败、
                  持锁进程仍存活）。**调用方不得以当前文件组为新基线开始新事务**，
                  旧 journal 与备份必须原样保留，等待人工/再次恢复。
    """
    chapter = Path(chapter_dir)
    journal_p = chapter / JOURNAL_NAME
    if not journal_p.exists():
        return {"ok": True, "state": "none", "restored": [], "removed": [], "failed": []}
    try:
        journal = json.loads(journal_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "state": "unreadable", "restored": [], "removed": [],
                "failed": ["journal 不可读: %s" % exc]}
    state = str(journal.get("state") or "")
    if state in ("recovered", "rolled_back", "committed"):
        return {"ok": True, "state": state, "restored": [], "removed": [], "failed": []}
    if state not in ("begun", "rollback_failed"):
        return {"ok": True, "state": state, "restored": [], "removed": [], "failed": []}
    if not lock_held and (chapter / LOCK_NAME).exists():
        pid = 0
        try:
            raw = (chapter / LOCK_NAME).read_text(encoding="utf-8").strip().split(None, 1)
            pid = int(raw[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        # 同一进程内的嵌套调用 = 锁的**所有者**自己，不该被自己的锁挡住。
        if pid and pid != os.getpid() and _pid_alive(pid):
            return {"ok": False, "state": state, "restored": [], "removed": [],
                    "failed": ["持锁进程仍存活 pid=%d" % pid]}

    backup = chapter / str(journal.get("backup_dir") or "")
    backed = list(journal.get("backed_up") or [])
    created = list(journal.get("created") or [])
    restored, removed, failed = [], [], []
    if backed and not backup.is_dir():
        # 备份目录不在 ⇒ **没有任何文件被恢复**，绝不能报 recovered。
        failed.append("备份目录缺失: %s" % (backup.name or "(journal 未记录)"))
    else:
        for name in backed:
            src = backup / name
            if not src.exists():
                failed.append("%s: 备份文件缺失" % name)
                continue
            try:
                _atomic_write_bytes(chapter / name, src.read_bytes())
                restored.append(name)
            except Exception as exc:                             # noqa: BLE001
                failed.append("%s: %s" % (name, exc))
    for name in created:
        dst = chapter / name
        if dst.exists():
            try:
                dst.unlink()
                removed.append(name)
            except OSError as exc:
                failed.append("%s: %s" % (name, exc))

    _finish_journal(journal_p, journal,
                    "rollback_failed" if failed else "recovered",
                    restored, removed, failed)
    if not failed and not lock_held:
        # 独立恢复入口：恢复完成后清掉陈旧锁，把章节交还给下一个发布者。
        try:
            (chapter / LOCK_NAME).unlink()
        except OSError:
            pass
    return {"ok": not failed, "state": "rollback_failed" if failed else "recovered",
            "restored": restored, "removed": removed, "failed": failed}


def _finish_journal(journal_p: Path, journal: dict, state: str,
                    restored, removed, failed) -> None:
    """写恢复结论；**写不下去就抛错**，不能留下"看起来恢复了"的旧记录."""
    payload = dict(journal)
    payload.update({"state": state,
                    "recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "restored": list(restored), "removed": list(removed),
                    "failed": list(failed)})
    try:
        _atomic_write_bytes(journal_p,
                            json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))
    except Exception as exc:                                     # noqa: BLE001
        raise PublishError("恢复结论无法写入 journal（旧记录未更新，禁止开始新事务）: %s"
                           % exc) from exc


