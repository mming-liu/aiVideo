"""shared.enhancement_io - 增强实验的文件事务、单写者锁与 accepted 指针（计划 §F2/§F3/§15）.

本模块是**唯一**负责"候选 attempt 落盘协议"的实现：
准备目录 / 冻结输入 / 事件追加(seq+fsync) / lifecycle 投影 / 不可变 manifest /
accepted 指针的两阶段替换与读回确认 / 路径守卫。其它包（video/tts/merge/motion/
inspect/alignment/integration）一律调用这里，不得自建另一套提交协议。

关键不变量（对应计划条款）：
- **任务状态与产物状态分开**：video 的 ``lifecycle.json`` 是可恢复的当前投影，
  ``events.jsonl`` 是权威历史；产物状态只由一次写成的不可变 ``manifest.json`` 表达。
- **seq 连续**：事件在锁内先 append 并 fsync，再更新投影；恢复时"投影落后"可按
  合法事件补投影，"投影领先或中间断链"拒绝自动续跑，"尾部不完整事件"保留原始
  证据并停在最后确认点。
- **accepted 是唯一采纳事实源**：读者必须核 指针→manifest→全部产物→来源关系→
  自身 required_checks；不能凭"文件存在/返回码 0/单条 signature"采纳。
- **提交后读回**：替换成功但读回失败 ⇒ ``commit_unconfirmed``（退出码 6），
  既不能声称"旧值仍在"，也不盲目回滚。
- **路径守卫**：候选路径必须绝对、在实验根内、非输入等价路径、非正式产物路径，
  且不含 ``..`` / 重解析点逃逸。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from shared.json_io import commit_json_swap, discard_json_swap, prepare_json_swap
from shared.paths import (
    ENHANCEMENT_PACKAGES,
    chapter_dir,
    enhancement_package_dir,
    validate_experiment_id,
)

# ---------------------------------------------------------------- schema / 退出码

SCHEMA_ATTEMPT_MANIFEST = "enhancement_attempt_manifest.v1"
SCHEMA_ACCEPTED = "enhancement_accepted.v1"
SCHEMA_LIFECYCLE = "enhancement_lifecycle.v1"
SCHEMA_EVENT = "enhancement_event.v1"

#: §F3 统一退出码（仅适用于新增 enhancement CLI 与 worker 宿主封装；
#: 旧生产 CLI 保持原码，见 §15「F退出码范围」）。
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_TOOL_FAIL = 3
EXIT_REJECTED = 4
EXIT_BUSY = 5
EXIT_COMMIT_UNCONFIRMED = 6
EXIT_SUBMISSION_UNKNOWN = 7

#: manifest 自报这些状态时**不是**可消费的候选（即使指针存在也不许采纳）。
#: 指针表达"被采纳"，manifest 状态表达"这一份自己怎么看"；两者矛盾以拒收收口。
REJECTING_MANIFEST_STATUSES = frozenset({
    "rejected", "failed", "unverified", "commit_unconfirmed", "provider_failed",
    "submission_unknown", "invalidated", "render_failed", "qc_failed",
})

ATTEMPTS_DIRNAME = "attempts"
ACCEPTED_FILENAME = "accepted.json"
MANIFEST_FILENAME = "manifest.json"
LIFECYCLE_FILENAME = "lifecycle.json"
EVENTS_FILENAME = "events.jsonl"
INPUT_FILENAME = "input.json"
LOCK_DIRNAME = ".lock"
LOCK_OWNER_FILENAME = "owner.json"

_ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EnhancementError(Exception):
    """带结构化载荷的失败（``payload()`` 即计划 §F3 的错误 JSON）。"""

    def __init__(self, code: str, stage: str, message: str, *, exit_code: int = EXIT_TOOL_FAIL, **fields):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.exit_code = int(exit_code)
        self.fields = fields

    def payload(self) -> dict:
        """统一错误 JSON：code/stage/request_id/attempt_id/field_path/message/
        residual_issues/replacement_performed/confirmed（缺省为 None/空）。"""
        return {
            "code": self.code,
            "stage": self.stage,
            "request_id": self.fields.get("request_id"),
            "attempt_id": self.fields.get("attempt_id"),
            "field_path": self.fields.get("field_path"),
            "message": self.message,
            "residual_issues": list(self.fields.get("residual_issues") or []),
            "replacement_performed": bool(self.fields.get("replacement_performed", False)),
            "confirmed": bool(self.fields.get("confirmed", False)),
            **{k: v for k, v in self.fields.items() if k not in {
                "request_id", "attempt_id", "field_path", "residual_issues",
                "replacement_performed", "confirmed"}},
        }


class BusyError(EnhancementError):
    def __init__(self, message: str, **fields):
        super().__init__("lock_busy", "lock", message, exit_code=EXIT_BUSY, **fields)


class CommitUnconfirmedError(EnhancementError):
    def __init__(self, message: str, **fields):
        super().__init__("commit_unconfirmed", "commit", message,
                         exit_code=EXIT_COMMIT_UNCONFIRMED, replacement_performed=True,
                         confirmed=False, **fields)


# ---------------------------------------------------------------- 基础工具

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(chunk), b""):
            h.update(blob)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def canonical_json_digest(obj) -> str:
    """事实输入的规范摘要（key 排序、紧凑分隔、UTF-8）。"""
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows signal 0 is CTRL_C_EVENT, not POSIX's existence probe.
        # Query/wait on a process handle without sending any signal. Inability
        # to prove exit keeps the owner alive (the lock remains fail-closed).
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() not in (87, 1168)  # absent PID
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0  # signaled = exited
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限发信号
    except OSError:
        return False
    return True


def _process_start_identity(pid: int) -> str | None:
    """进程开始身份（防 PID 复用误判锁主）。

    Windows 走 ``GetProcessTimes``（创建时间）；POSIX 走 ``/proc/<pid>/stat`` 的
    starttime。取不到时返回 None —— 此时锁的健壮性退化为"PID 存活检查"，调用方
    的错误报告须如实标注（见 ``AttemptLock.owner_alive``）。
    """
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
                if not k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                           ctypes.byref(kernel), ctypes.byref(user)):
                    return None
                return f"win:{creation.dwHighDateTime:08x}{creation.dwLowDateTime:08x}"
            finally:
                k32.CloseHandle(handle)
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
        tail = stat.rsplit(")", 1)[-1].split()
        return f"proc:{tail[19]}" if len(tail) > 19 else None
    except Exception:
        return None


# ---------------------------------------------------------------- 路径守卫

def assert_attempt_id(attempt_id: str) -> str:
    aid = str(attempt_id or "")
    if not _ATTEMPT_ID_RE.match(aid):
        raise EnhancementError("invalid_attempt_id", "paths",
                               f"非法 attempt_id: {attempt_id!r}", exit_code=EXIT_INVALID_INPUT,
                               field_path="attempt_id")
    return aid


def production_paths(chapter: int | None = None) -> tuple[Path, ...]:
    """正式（生产）产物路径清单：候选路径**永远**不得与它们相等或互为父子。"""
    from shared.paths import (audio_dir, clips_dir, final_file, images_dir, script_file,
                              step01_log_file, stretched_dir, subtitles_file, vimax_working_dir)
    ch = chapter
    return tuple(p.resolve() for p in (
        script_file(ch), final_file(ch), subtitles_file(ch), clips_dir(ch), audio_dir(ch),
        images_dir(ch), vimax_working_dir(ch), stretched_dir(ch), step01_log_file(ch),
        chapter_dir(ch) / "step01_quality.json", chapter_dir(ch) / "step01_audit.json",
        chapter_dir(ch) / "step02b_quality.json", chapter_dir(ch) / "step04.log",
        chapter_dir(ch) / "step02.log", chapter_dir(ch) / "step02b.log",
    ))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def assert_candidate_path(path: Path | str, *, experiment_root: Path | str,
                          inputs: tuple = (), formal: tuple = (),
                          field_path: str = "candidate_path", must_exist: bool = False) -> Path:
    """候选路径守卫（计划 §F1/F3）。

    规则：必须是绝对路径；解析后（含重解析点）须在 ``experiment_root`` 内；
    不得与输入路径 / 正式产物路径相等或互为父子；``..`` 逃逸或解析到根外即拒收。
    """
    root = Path(experiment_root)
    if not root.is_absolute():
        raise EnhancementError("invalid_experiment_root", "paths",
                               f"实验根必须是绝对路径: {root}", exit_code=EXIT_INVALID_INPUT,
                               field_path="experiment_root")
    p = Path(path)
    if not p.is_absolute():
        raise EnhancementError("relative_candidate_path", "paths",
                               f"候选路径必须是绝对路径: {p}", exit_code=EXIT_INVALID_INPUT,
                               field_path=field_path)
    if ".." in p.parts:
        raise EnhancementError("path_escape", "paths",
                               f"候选路径含 .. 逃逸: {p}", exit_code=EXIT_INVALID_INPUT,
                               field_path=field_path)
    resolved = p.resolve()
    root_resolved = root.resolve()
    if not _is_relative_to(resolved, root_resolved) or resolved == root_resolved:
        raise EnhancementError("path_outside_experiment", "paths",
                               f"候选路径不在实验根内: {resolved}", exit_code=EXIT_INVALID_INPUT,
                               field_path=field_path)
    for other in tuple(inputs) + tuple(formal):
        o = Path(other).resolve()
        if resolved == o or _is_relative_to(resolved, o) or _is_relative_to(o, resolved):
            raise EnhancementError("path_conflicts_with_input_or_formal", "paths",
                                   f"候选路径与输入/正式路径冲突: {resolved} ↔ {o}",
                                   exit_code=EXIT_INVALID_INPUT, field_path=field_path)
    if must_exist and not resolved.exists():
        raise EnhancementError("candidate_path_missing", "paths",
                               f"候选路径不存在: {resolved}", exit_code=EXIT_TOOL_FAIL,
                               field_path=field_path)
    return resolved


# ---------------------------------------------------------------- 单写者锁

#: 回收门的目录后缀（同目录下的兄弟名，用 mkdir 的原子性保证只有一个赢家）
RECLAIM_SUFFIX = ".reclaim"


def owner_status(lock_dir: Path | str) -> tuple[bool, str]:
    """``(是否仍被持有, 原因)``。**读不出 owner 一律按仍被持有**（fail-closed）.

    主锁与回收门共用这一份判据：两者都只有"确认持有者已退出"才允许清理。
    """
    p = Path(lock_dir) / LOCK_OWNER_FILENAME
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return True, "owner_missing_or_unreadable"
    try:
        owner = json.loads(raw)
    except Exception:
        return True, "owner_file_unparsable"
    if not isinstance(owner, dict):
        return True, "owner_file_unparsable"
    pid = int(owner.get("pid") or 0)
    if not _process_alive(pid):
        return False, "pid_dead"
    recorded = owner.get("process_start")
    latest = _process_start_identity(pid)
    if recorded and latest and recorded != latest:
        return False, "pid_reused"
    return True, "alive"


def _write_owner(lock_dir: Path, *, purpose: str = "") -> str:
    """写入 owner（含 PID + 进程开始身份 + token），返回 token."""
    token = f"{os.getpid()}-{time.time_ns()}"
    payload = {
        "pid": os.getpid(),
        "process_start": _process_start_identity(os.getpid()),
        "token": token,
        "purpose": purpose,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    Path(lock_dir, LOCK_OWNER_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return token


def _remove_tree(target: Path) -> None:
    try:
        for p in sorted(Path(target).rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            else:
                p.rmdir()
        Path(target).rmdir()
    except OSError:
        pass


class AttemptLock:
    """原子 mkdir 锁：owner 含 PID 与进程开始身份；忙 ⇒ 退出码 5。

    恢复只在**确认 owner 已退出**（PID 不存活，或 PID 存活但开始身份不符=PID 复用）
    后进行；**不得按年龄破锁**。
    """

    def __init__(self, lock_dir: Path | str, *, purpose: str = ""):
        self.lock_dir = Path(lock_dir)
        self.purpose = purpose
        self._held = False
        self._token: str | None = None

    # -- owner 文件读写 --
    def _owner_path(self) -> Path:
        return self.lock_dir / LOCK_OWNER_FILENAME

    def read_owner(self) -> dict | None:
        try:
            raw = self._owner_path().read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return {"unparsable": True, "raw": raw[:400]}
        return data if isinstance(data, dict) else {"unparsable": True, "raw": raw[:400]}

    def owner_alive(self) -> tuple[bool, str]:
        """(是否仍存活, 原因描述)。**读不出 owner 一律按仍被持有处理**（不破锁）。

        为什么（2026-10-03 复核 P1-1）：``mkdir`` 成功与 owner 落盘之间有窗口。
        旧写法把"owner 文件不存在"当作无人持锁，窗口内的第二个执行者因此会删掉
        第一个执行者刚建好的锁目录并自建一个——两者**同时**持锁，可能并写事件与
        采纳指针。"缺失/不可读"与"持锁者崩在窗口内"无法区分 ⇒ fail-closed；
        只有确认 owner 已退出（PID 不在或 PID 复用）才允许清理残留锁。
        """
        return owner_status(self.lock_dir)

    def acquire(self) -> "AttemptLock":
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_dir.mkdir()
        except FileExistsError:
            alive, why = self.owner_alive()
            if alive:
                owner = self.read_owner() or {}
                raise BusyError(
                    f"锁被占用（{why}）：{self.lock_dir}",
                    lock_dir=str(self.lock_dir), owner_pid=owner.get("pid"), reason=why,
                )
            # 确认 owner 已退出 ⇒ 走回收门（单赢家）清理，再重试
            self._reclaim_stale()
            try:
                self.lock_dir.mkdir()
            except FileExistsError as exc:
                raise BusyError(f"回收残留锁后仍被占用：{self.lock_dir}",
                                lock_dir=str(self.lock_dir), reason="race") from exc
        token = f"{os.getpid()}-{time.time_ns()}"
        owner = {
            "pid": os.getpid(),
            "process_start": _process_start_identity(os.getpid()),
            "token": token,
            "purpose": self.purpose,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            (self.lock_dir / LOCK_OWNER_FILENAME).write_text(
                json.dumps(owner, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            self._clear_stale()
            raise EnhancementError("lock_write_failed", "lock",
                                   f"无法写入锁 owner 文件: {exc}") from exc
        self._held = True
        self._token = token
        return self

    def _reclaim_stale(self) -> None:
        """回收**已确认退出**的残留锁；删除这一步只有一个赢家.

        为什么不能只"复查一次 owner"（2026-10-04 复核 P1-1 之二）：B 判定旧 owner 已
        退出后可能被调度挂起，A 期间清掉旧锁并另建新锁，B 醒来照旧判断去删——删的是
        **A 的新锁**，于是两人同时持锁。门内**重新**读一次 owner 才能关掉这个窗口：
        门是 mkdir 出来的，同一时刻只有一个执行者能进。
        """
        gate = self.lock_dir.with_name(self.lock_dir.name + RECLAIM_SUFFIX)
        try:
            gate.mkdir()
        except FileExistsError:
            held, why = owner_status(gate)
            if held:
                raise BusyError(
                    f"另一个执行者正在回收残留锁（{why}）：{self.lock_dir}",
                    lock_dir=str(self.lock_dir), reason="reclaim_in_progress") from None
            _remove_tree(gate)          # 回收者自己崩了：门可复用（同一判据）
            try:
                gate.mkdir()
            except FileExistsError:
                raise BusyError("回收门争用", lock_dir=str(self.lock_dir),
                                reason="reclaim_in_progress") from None
        try:
            _write_owner(gate, purpose="reclaim")
            alive, why = self.owner_alive()      # **门内**复核：可能已被他人重新持有
            if alive:
                owner = self.read_owner() or {}
                raise BusyError(
                    f"锁在回收门内被重新持有（{why}）：{self.lock_dir}",
                    lock_dir=str(self.lock_dir), owner_pid=owner.get("pid"), reason=why)
            self._clear_stale(self.lock_dir)
        finally:
            _remove_tree(gate)

    def _clear_stale(self, target: Path | None = None) -> None:
        _remove_tree(Path(target) if target is not None else self.lock_dir)

    def release(self) -> None:
        if not self._held:
            return
        owner = self.read_owner() or {}
        if owner.get("token") != self._token:
            # 锁已被他人接管（不应发生）：拒绝误删
            self._held = False
            return
        self._clear_stale()
        self._held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


# ---------------------------------------------------------------- attempt 目录与输入冻结

def attempt_dir(package: str, experiment_id: str, attempt_id: str,
                chapter: int | None = None) -> Path:
    validate_experiment_id(experiment_id)
    assert_attempt_id(attempt_id)
    return enhancement_package_dir(package, experiment_id, chapter) / ATTEMPTS_DIRNAME / attempt_id


def package_root(package: str, experiment_id: str, chapter: int | None = None) -> Path:
    validate_experiment_id(experiment_id)
    return enhancement_package_dir(package, experiment_id, chapter)


def freeze_inputs(attempt: Path, payload: dict) -> Path:
    """把本次事实输入写入 ``input.json``（两阶段原子写）。返回写入路径。"""
    attempt.mkdir(parents=True, exist_ok=True)
    target = attempt / INPUT_FILENAME
    commit_json_swap(target, prepare_json_swap(target, payload, ensure_ascii=False, indent=2))
    return target


def read_inputs(attempt: Path) -> dict | None:
    p = Path(attempt) / INPUT_FILENAME
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 事件与 lifecycle 投影

def _events_path(attempt: Path) -> Path:
    return Path(attempt) / EVENTS_FILENAME


def split_event_tail(raw: str) -> tuple[list[str], str, str]:
    """把事件文件原文切成 ``(已确认整行, 尾部原文, 结构问题)``.

    结构问题取值：
    - ``""``：全部确认（每行都以换行收尾）；
    - ``"unterminated"``：末行是**完整 JSON 但缺收尾换行**——落盘未确认（一次写一条是
      ``json + \n``），直接追加会与下一条粘成一行（2026-10-04 复核 P1-3 之二）；
    - ``"partial"``：末行是半截 JSON；
    - ``"middle_break"``：断行之后仍能解析出事件 ⇒ 真损坏，拒绝自动修复。
    """
    lines = raw.split("\n")
    trailing = raw.endswith("\n")
    if trailing:
        lines = lines[:-1]
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            ok = isinstance(json.loads(line), dict)
        except Exception:
            ok = False
        last = (i == len(lines) - 1)
        if ok and (trailing or not last):
            continue
        if ok:
            return lines[:i], line, "unterminated"
        if trailing or not last:
            return lines[:i], "", "middle_break"
        return lines[:i], line, "partial"
    return lines, "", ""


def read_events_strict(attempt: Path) -> tuple[list[dict], str | None]:
    """严格读事件：区分 不在 / 读不动 / 断行（尾部不完整保留原始证据）。

    Returns: ``(events, tail_problem)``；``tail_problem`` 非空表示最后一行
    不完整（无换行或 JSON 坏），已记录的完整事件仍照常返回。
    """
    p = _events_path(attempt)
    if not p.exists():
        return [], None
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw:
        return [], None
    # 判据与修复共用一份（`split_event_tail`）：否则"读得出来"和"能追加"会各说各话
    confirmed, _tail_text, kind = split_event_tail(raw)
    events: list[dict] = []
    for i, line in enumerate(confirmed):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            return events, f"broken_line_{i + 1}"
    if kind == "middle_break":
        return events, f"broken_line_{len(events) + 1}"
    if kind in ("unterminated", "partial"):
        return events, f"tail_incomplete:line_{len(events) + 1}"
    return events, None


def repair_event_tail(attempt: Path) -> dict | None:
    """恢复：把事件文件截到**最后确认点**，残片原文另存证据（不删原文）。

    只处理**尾部**残片；中间断链是数据损坏，拒绝自动修复（``events_broken``）。
    返回修复记录（``None`` = 无需修复）。调用方须持锁。两阶段写：先写满临时文件
    并 fsync，再 ``os.replace``。
    """
    attempt = Path(attempt)
    p = _events_path(attempt)
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8", errors="replace")
    confirmed, fragment, problem = split_event_tail(raw)
    if problem == "middle_break":
        raise EnhancementError("events_broken", "recover",
                               "事件文件中间断链（断行之后仍有完整事件），拒绝自动修复",
                               attempt_id=attempt.name)
    if not problem:
        return None
    evidence = attempt / f"{EVENTS_FILENAME}.tail.{time.time_ns()}.fragment"
    evidence.write_text(fragment, encoding="utf-8")
    body = "".join(line + "\n" for line in confirmed)
    if problem == "unterminated":
        # 事件本身是完整的，只是没被换行收尾 ⇒ 补上换行（**不丢事件**），
        # 仍把原文留成证据以便复核
        body += fragment + "\n"
    tmp = p.with_name(p.name + ".repair.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return {"kind": problem,
            "fragment_bytes": len(fragment.encode("utf-8")),
            "evidence_path": str(evidence),
            "confirmed_events": len(confirmed) + (1 if problem == "unterminated" else 0)}


def append_event(attempt: Path, kind: str, *, before=None, after=None, **fields) -> dict:
    """锁内追加事件（seq 递增、flush + fsync 后才算确认）。调用方须持锁。

    **尾部残片一律拒绝追加**：追加写入会与残片粘成同一行，新事件随即读不出来
    （2026-10-03 复核实测：追加返回成功，随后 `task_id` 读不回）。恢复路径先调
    :func:`repair_event_tail` 截到最后确认点，再追加。
    """
    events, tail = read_events_strict(attempt)
    if tail and tail.startswith("broken_line"):
        raise EnhancementError("events_broken", "events",
                               f"事件文件中间断链，拒绝追加：{tail}", attempt_id=attempt.name)
    if tail:
        raise EnhancementError("events_tail_incomplete", "events",
                               f"事件尾部不完整（{tail}），拒绝追加——追加会与残片粘成"
                               "一行并让新事件读不回来；先走恢复修尾到最后确认点",
                               attempt_id=attempt.name)
    seq = (max((int(e.get("seq") or 0) for e in events), default=0) + 1)
    event = {
        "schema_version": SCHEMA_EVENT,
        "seq": seq,
        "kind": kind,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "before": before,
        "after": after,
    }
    event.update(fields)
    attempt.mkdir(parents=True, exist_ok=True)
    with open(_events_path(attempt), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return event


def write_lifecycle(attempt: Path, projection: dict) -> Path:
    """原子更新 lifecycle 投影（与事件同源；调用方须持锁）。"""
    target = Path(attempt) / LIFECYCLE_FILENAME
    payload = dict(projection)
    payload.setdefault("schema_version", SCHEMA_LIFECYCLE)
    payload["projected_from_event_seq"] = payload.get("projected_from_event_seq")
    commit_json_swap(target, prepare_json_swap(target, payload, ensure_ascii=False, indent=2))
    return target


def read_lifecycle(attempt: Path) -> dict | None:
    p = Path(attempt) / LIFECYCLE_FILENAME
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def validate_event_chain(events: list[dict], *, initial_state: str,
                         transitions: dict, attempt_id: str = "") -> str:
    """事件链结构校验：``seq`` 必须 1..N 连续，状态迁移必须合法。返回终态。

    两条都**逐条**核（2026-10-03 复核 P1-3：旧实现既接受跳号 ``seq 1→9``，也接受
    非法状态链 ``draft→accepted``，于是恢复会把损坏的历史当权威继续跑）。
    """
    expected = 1
    state = initial_state
    for e in events:
        seq = e.get("seq")
        if not isinstance(seq, int) or seq != expected:
            raise EnhancementError("events_seq_gap", "recover",
                                   f"事件 seq 不连续：期望 {expected}，实得 {seq!r}",
                                   attempt_id=attempt_id, field_path="seq")
        expected += 1
        if not e.get("kind"):
            raise EnhancementError("event_kind_missing", "recover",
                                   f"事件 seq={seq} 缺 kind", attempt_id=attempt_id,
                                   field_path="kind")
        after = e.get("after")
        if isinstance(after, dict) and after.get("state"):
            to_state = str(after["state"])
            if to_state != state and state not in (transitions.get(to_state) or set()):
                raise EnhancementError(
                    "illegal_state_chain", "recover",
                    f"事件 seq={seq} 的非法状态迁移 {state} → {to_state}"
                    f"（允许来源: {sorted(transitions.get(to_state) or set())}）",
                    attempt_id=attempt_id, field_path="state")
            state = to_state
    return state


def reconcile_lifecycle(attempt: Path, *, initial_state: str = "draft",
                        transitions: dict) -> dict:
    """恢复：把 lifecycle 投影对齐到事件历史。

    规则（计划 §F2/F3）：
    - 尾部不完整事件 ⇒ **保留残片原文为证据**并截到最后确认点（不删原文），再继续；
    - ``seq`` 跳号 / 状态链非法 / 事件中间断链 ⇒ 拒绝自动续跑；
    - 投影**领先**（seq 大于事件最大 seq）⇒ 拒绝自动续跑；
    - 投影**落后** ⇒ 按合法事件补投影。

    ``transitions``（目标状态 → 允许来源集合）**必填**：状态词汇表按包不同
    （video 是 draft→…→accepted，tts 是 draft→frozen→synthesized→accepted），
    没有可猜的默认表——猜错要么放行非法链，要么误拒合法链。
    """
    repaired = repair_event_tail(attempt)
    events, tail = read_events_strict(attempt)
    if tail and tail.startswith("broken_line"):
        raise EnhancementError("events_broken", "recover",
                               f"事件中间断链，拒绝自动续跑：{tail}", attempt_id=attempt.name)
    state = validate_event_chain(events, initial_state=initial_state,
                                 transitions=transitions, attempt_id=attempt.name)
    last_seq = max((int(e.get("seq") or 0) for e in events), default=0)
    projection = read_lifecycle(attempt)
    if projection is not None:
        proj_seq = int(projection.get("projected_from_event_seq") or 0)
        if proj_seq > last_seq:
            raise EnhancementError("projection_ahead", "recover",
                                   f"lifecycle 投影领先事件历史（{proj_seq} > {last_seq}），拒绝自动续跑",
                                   attempt_id=attempt.name)
    new_projection = dict(projection or {})
    new_projection.update({"state": state, "projected_from_event_seq": last_seq,
                           "attempt_id": attempt.name})
    new_projection.pop("tail_incomplete", None)
    if repaired:
        new_projection["tail_repaired"] = repaired
    write_lifecycle(attempt, new_projection)
    return new_projection


# ---------------------------------------------------------------- 不可变 manifest

def seal_manifest(attempt: Path, payload: dict) -> dict:
    """一次写成不可变 ``manifest.json``（write-once；已存在即拒绝改写）。"""
    target = Path(attempt) / MANIFEST_FILENAME
    if target.exists():
        raise EnhancementError("manifest_immutable", "manifest",
                               f"manifest 已存在，不允许改写: {target}",
                               attempt_id=attempt.name)
    payload = dict(payload)
    payload.setdefault("schema_version", SCHEMA_ATTEMPT_MANIFEST)
    payload["attempt_id"] = attempt.name
    commit_json_swap(target, prepare_json_swap(target, payload, ensure_ascii=False, indent=2))
    sealed = json.loads(target.read_text(encoding="utf-8"))
    sealed["manifest_sha256"] = sha256_file(target)
    return sealed


def read_manifest(attempt: Path) -> dict | None:
    p = Path(attempt) / MANIFEST_FILENAME
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def artifact_record(path: Path, *, role: str | None = None, media: dict | None = None) -> dict:
    p = Path(path)
    if not p.is_file():
        raise EnhancementError("artifact_missing", "manifest",
                               f"产物不存在，拒绝入 manifest: {p}", exit_code=EXIT_REJECTED)
    return {"path": str(p.resolve()), "sha256": sha256_file(p),
            "bytes": p.stat().st_size, "role": role, "media": media}


# ---------------------------------------------------------------- accepted 指针

def accepted_path(package_root: Path) -> Path:
    return Path(package_root) / ACCEPTED_FILENAME


def read_accepted(package_root: Path) -> dict | None:
    p = accepted_path(package_root)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def commit_accepted(package_root: Path, *, attempt_id: str, manifest_path: Path,
                    manifest_sha256: str, input_key: str, deadline=None) -> dict:
    """两阶段替换 accepted 指针，并在替换后**读回完整关系**。

    - 替换前再核一次 deadline（若调用方给了 ``deadline``，需提供 ``expired()``）；
      过点即放弃（丢弃临时文件，盘上原指针一字节不改）。
    - 替换后读回；读回失败 ⇒ ``CommitUnconfirmedError``（退出码 6），
      replacement_performed=True / confirmed=False —— 不声称旧值仍在，也不盲目回滚。
    """
    root = Path(package_root)
    root.mkdir(parents=True, exist_ok=True)
    pointer = {
        "schema_version": SCHEMA_ACCEPTED,
        "attempt_id": assert_attempt_id(attempt_id),
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": str(manifest_sha256),
        "input_key": str(input_key),
        "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    target = accepted_path(root)
    tmp = prepare_json_swap(target, pointer, ensure_ascii=False, indent=2)
    if deadline is not None and getattr(deadline, "expired", None) and deadline.expired():
        discard_json_swap(tmp)
        raise EnhancementError("deadline_exhausted", "commit",
                               "替换前复核截止已过，放弃提交（原指针未改）",
                               exit_code=EXIT_REJECTED, attempt_id=attempt_id)
    try:
        commit_json_swap(target, tmp)
    except Exception as exc:
        raise EnhancementError("commit_failed_before_replace", "commit",
                               f"替换失败，原 accepted 指针保持不变: {exc}",
                               attempt_id=attempt_id, replacement_performed=False) from exc
    # 读回确认
    try:
        back = json.loads(target.read_text(encoding="utf-8"))
        ok = (back.get("attempt_id") == pointer["attempt_id"]
              and back.get("manifest_sha256") == pointer["manifest_sha256"]
              and Path(back.get("manifest_path", "")).is_file()
              and sha256_file(Path(back["manifest_path"])) == pointer["manifest_sha256"])
    except Exception as exc:
        raise CommitUnconfirmedError(f"替换后读回失败（不得声称旧值仍在）: {exc}",
                                     attempt_id=attempt_id) from exc
    if not ok:
        raise CommitUnconfirmedError("替换后读回的关系不一致（manifest 摘要/身份不符）",
                                     attempt_id=attempt_id)
    return back


def verify_accepted(package_root: Path, *, required_checks: tuple = (),
                    expect_input_key: str | None = None) -> dict:
    """读者侧完整核验：指针 → manifest → 全部产物 → 来源关系 → 自身 required_checks。

    任一步不满足即抛 ``EnhancementError``（rejected）；**不**凭文件存在或单条
    signature 采纳。
    """
    pointer = read_accepted(package_root)
    if pointer is None:
        raise EnhancementError("accepted_missing", "accept",
                               f"accepted 指针不存在: {accepted_path(package_root)}",
                               exit_code=EXIT_REJECTED)
    if pointer.get("schema_version") != SCHEMA_ACCEPTED:
        raise EnhancementError("accepted_schema", "accept",
                               f"accepted 指针 schema 不符: {pointer.get('schema_version')!r}",
                               exit_code=EXIT_REJECTED)
    if expect_input_key is not None and pointer.get("input_key") != expect_input_key:
        raise EnhancementError("accepted_input_key_mismatch", "accept",
                               f"accepted 的 input_key 与期望不符: {pointer.get('input_key')} != {expect_input_key}",
                               exit_code=EXIT_REJECTED, field_path="input_key")
    manifest_path = Path(pointer.get("manifest_path", ""))
    if not manifest_path.is_file():
        raise EnhancementError("accepted_manifest_missing", "accept",
                               f"指针指向的 manifest 不存在: {manifest_path}",
                               exit_code=EXIT_REJECTED)
    actual_sha = sha256_file(manifest_path)
    if actual_sha != pointer.get("manifest_sha256"):
        raise EnhancementError("accepted_manifest_digest_mismatch", "accept",
                               f"manifest 摘要与指针不符（可能被改写）: {actual_sha[:12]} != {str(pointer.get('manifest_sha256'))[:12]}",
                               exit_code=EXIT_REJECTED)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("attempt_id") != pointer.get("attempt_id"):
        raise EnhancementError("accepted_identity_mismatch", "accept",
                               "manifest 与指针的 attempt 身份不一致",
                               exit_code=EXIT_REJECTED)
    # 指针的输入身份必须**绑在 manifest 自己记的请求身份上**：否则可以把 B 请求的
    # 指针配 A 请求的 manifest（attempt_id 相同即可），输入 key 这一层就白设了。
    # 缺键**不再跳过**（2026-10-04 复核 P1-2 之二）：缺了就没有绑定，等于没有核。
    recorded_key = manifest.get("request_key") or manifest.get("input_key")
    if not recorded_key:
        raise EnhancementError(
            "accepted_input_key_missing", "accept",
            "manifest 未记录请求身份（request_key/input_key）：accepted 指针绑不到"
            "这一次请求上，无法判断它属于哪一份输入",
            exit_code=EXIT_REJECTED, field_path="request_key")
    if str(recorded_key) != str(pointer.get("input_key") or ""):
        raise EnhancementError(
            "accepted_input_key_mismatch", "accept",
            f"accepted 的 input_key 与 manifest 记录的请求身份不符: "
            f"{pointer.get('input_key')} != {recorded_key}",
            exit_code=EXIT_REJECTED, field_path="input_key")
    manifest_status = str(manifest.get("status") or "")
    if manifest_status in REJECTING_MANIFEST_STATUSES:
        raise EnhancementError(
            "accepted_manifest_rejected", "accept",
            f"被指针指名的 manifest 自报状态 {manifest_status!r}（非可消费候选）",
            exit_code=EXIT_REJECTED, field_path="status")
    artifacts = manifest.get("artifacts") or []
    if not artifacts:
        raise EnhancementError("accepted_no_artifacts", "accept",
                               "manifest 未登记任何产物", exit_code=EXIT_REJECTED)
    for rec in artifacts:
        p = Path(rec.get("path", ""))
        if not p.is_file():
            raise EnhancementError("accepted_artifact_missing", "accept",
                                   f"产物缺失: {p}", exit_code=EXIT_REJECTED)
        if sha256_file(p) != rec.get("sha256"):
            raise EnhancementError("accepted_artifact_digest_mismatch", "accept",
                                   f"产物摘要不符（可能被改写）: {p}", exit_code=EXIT_REJECTED)
    checks = {str(c.get("name")): str(c.get("status")) for c in (manifest.get("checks") or [])}
    unmet = [name for name in required_checks if checks.get(name) not in ("pass", "completed", "ok")]
    if unmet:
        raise EnhancementError("required_checks_unmet", "accept",
                               f"required_checks 未满足: {unmet}（当前: {checks}）",
                               exit_code=EXIT_REJECTED, residual_issues=unmet)
    return {"pointer": pointer, "manifest": manifest, "manifest_sha256": actual_sha}


# ---------------------------------------------------------------- 清理

def cleanup_attempt_tmp(attempt: Path) -> list[str]:
    """只清理**本 attempt 目录内**的临时文件（``*.tmp``）。返回删除清单。"""
    removed: list[str] = []
    for p in sorted(Path(attempt).rglob("*.tmp")):
        try:
            p.unlink()
            removed.append(str(p))
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- 候选执行上下文

@dataclass
class CandidateContext:
    """一次候选执行的路径与身份上下文（§V1 ``ExecutionContext`` 的落盘侧）。

    所有候选产物都必须落在 ``attempt_dir`` 内；任何试图写生产路径的调用会被
    :meth:`assert_path` 拦下。``log_dir`` 与 ``attempt_dir`` 同名实现，
    ``execution_log_file`` 因此可直接消费本对象。
    """

    experiment_id: str
    package: str
    attempt_id: str
    chapter: int | None = None
    #: 来源身份（§F4）：采纳 manifest 必须能回答"哪一章、哪一稿、谁产生的"。
    #: 生产者负责填；缺失时采纳出口**拒收**，不写空身份、不用 `fixture` 兜底。
    scope: dict = field(default_factory=dict)
    script_revision: str = ""
    source: str = ""

    def __post_init__(self):
        validate_experiment_id(self.experiment_id)
        assert_attempt_id(self.attempt_id)
        if self.package not in ENHANCEMENT_PACKAGES:
            raise EnhancementError("unknown_package", "context",
                                   f"未知包名: {self.package!r}",
                                   exit_code=EXIT_INVALID_INPUT, field_path="package")

    # -- 路径 --
    @property
    def package_root(self) -> Path:
        return package_root(self.package, self.experiment_id, self.chapter)

    @property
    def attempt_dir(self) -> Path:
        return attempt_dir(self.package, self.experiment_id, self.attempt_id, self.chapter)

    @property
    def outputs_dir(self) -> Path:
        return self.attempt_dir / "outputs"

    @property
    def assets_dir(self) -> Path:
        return self.attempt_dir / "assets"

    @property
    def log_dir(self) -> Path:
        return self.attempt_dir / "logs"

    @property
    def candidate_path(self) -> Path:
        return self.outputs_dir / "candidate.mp4"

    def prepare(self) -> "CandidateContext":
        for d in (self.outputs_dir, self.assets_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def shot_dir(self, idx: int) -> Path:
        return self.attempt_dir / "shots" / f"shot_{int(idx):02d}"

    # -- 守卫 --
    def assert_path(self, path, *, inputs: tuple = (), field_path: str = "candidate_path",
                    must_exist: bool = False) -> Path:
        return assert_candidate_path(path, experiment_root=self.attempt_dir, inputs=inputs,
                                     formal=production_paths(self.chapter),
                                     field_path=field_path, must_exist=must_exist)

    def lock(self, purpose: str = "") -> "AttemptLock":
        return AttemptLock(self.attempt_dir / LOCK_DIRNAME,
                           purpose=purpose or f"{self.package}:{self.attempt_id}")


__all__ = [name for name in dir() if not name.startswith("_")]
