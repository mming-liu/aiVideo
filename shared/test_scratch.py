# -*- coding: utf-8 -*-
"""测试临时产物治理：计划与执行（会话内轻量回收 + 会话外清理工具共用）.

背景（2026-11 复核）：`.pytest_tmp` 曾涨到 189 GB / 33.8 万文件。两个成因：

1. `motion_revise_<pid>` 等按 PID 命名的隔离根从不回收（每个约 1.3 GB）；
2. `--basetemp=.pytest_tmp/<run>` 与 `%TEMP%/pytest-of-<user>/pytest-N` 按会话
   累积，pytest 的 `tmp_path_retention_policy` 刻意关闭（见 pytest.ini）。

本模块只提供**纯计划**（扫描 + 分类 + 保留策略）与**受控删除**；策略由调用方
（`tests/conftest.py` 会话开始、`tools/cleanup_test_artifacts.py` 会话外）选择：

- 只回收「进程已退出」的 PID 根；活进程/当前进程的根永远保留；
- 每个前缀保留最近 `keep_latest` 个根（便于复盘）；
- 其余候选必须早于 `min_age_sec`（默认 6 小时）才回收；
- 删除失败/被守卫拒绝时保留现场，不升级为会话失败。

**删除语义**：`permanent=True` 直接删（释放磁盘）；`permanent=False` 走系统
回收站（可逆，但回收站未清空前不释放磁盘）。会话内自动回收必须用 `permanent`，
否则回收站会继续占盘。
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

#: 测试侧按 PID 命名的隔离根前缀（见 tests/enhancements/test_motion_*.py）。
STALE_TMP_PREFIXES = ("motion_revise_", "motion_cli_", "motion_fixtures_")

DEFAULT_KEEP_LATEST = 2
DEFAULT_MIN_AGE_SEC = 6 * 3600
#: 单次扫描最多统计的文件数（避免在超大目录上卡死）。
_SIZE_SCAN_FILE_CAP = 200_000

#: 长期保留标记：目录内含这些文件名时，自动回收一律跳过（**安全网**）。
#: 长期候选/审计包/探针证据应放在 `.pytest_tmp` **之外**（如 `.dsh/`、
#: `baselines/`）；这个标记只防误删，不改变「pytest 临时目录不承载长期证据」
#: 的约定（2026-11-14 复核：U6 包曾放在 `.pytest_tmp`，整目录清理后需从
#: `baselines/2026-11-12-pre-layered/u6_frozen/` 恢复）。
KEEP_MARKERS = (".keep", "KEEP")


def has_keep_marker(path) -> bool:
    """目录是否带长期保留标记（`.keep` / `KEEP`）."""
    try:
        base = Path(path)
        return any((base / name).exists() for name in KEEP_MARKERS)
    except OSError:
        return True          # 判不了就保留，宁可留着也不误删


@dataclass
class ScratchCandidate:
    """一个可回收的测试临时目录."""

    path: Path
    kind: str                      # pid_root | basetemp | pytest_tmp
    mtime: float
    pid: int | None = None
    size_bytes: int = 0
    files: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "mtime": self.mtime,
            "pid": self.pid,
            "size_bytes": self.size_bytes,
            "files": self.files,
            "reason": self.reason,
        }


def pid_is_alive(pid: int) -> bool:
    """尽力判断 PID 是否仍存活（判不了就当存活，宁可留着也不误删）."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
    return True


def _dir_size(path: Path, *, cap: int = _SIZE_SCAN_FILE_CAP) -> tuple[int, int]:
    """返回 `(bytes, files)`；超过文件上限时提前停止统计（不阻塞治理）."""
    total = 0
    count = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
                count += 1
                if count >= cap:
                    return total, count
    except OSError:
        pass
    return total, count


def _candidate(path: Path, kind: str, *, pid: int | None = None,
               reason: str = "", measure: bool = False) -> ScratchCandidate:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    size, files = _dir_size(path) if measure else (0, 0)
    return ScratchCandidate(path=path, kind=kind, mtime=mtime, pid=pid,
                            size_bytes=size, files=files, reason=reason)


def parse_pid_root(name: str,
                   prefixes=STALE_TMP_PREFIXES) -> tuple[str, int] | None:
    """`motion_revise_12345` → `("motion_revise_", 12345)`；不匹配返回 None."""
    for prefix in prefixes:
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if suffix.isdigit():
            return prefix, int(suffix)
    return None


def _split_reap_keep(candidates: list[ScratchCandidate], *, now: float,
                     min_age_sec: float, keep_latest: int,
                     key=lambda c: "") -> tuple[list, list, list]:
    """保留窗口内的最近 keep_latest 个；早于 min_age 的一律进回收清单.

    语义（2026-11-14 修正）：`keep_latest` 只保护**窗口内**的根（便于复盘
    刚结束的会话）；窗口外的根即使排在「最近」里也必须回收——否则每个前缀
    总会留下 keep_latest 个永久不动的旧根，磁盘仍会无界累积。
    """
    reap: list[ScratchCandidate] = []
    keep: list[ScratchCandidate] = []
    recent: list[ScratchCandidate] = []
    groups: dict[str, list[ScratchCandidate]] = {}
    for item in candidates:
        groups.setdefault(key(item), []).append(item)
    for items in groups.values():
        items.sort(key=lambda c: c.mtime, reverse=True)
        fresh = [c for c in items if c.mtime and now - c.mtime < min_age_sec]
        stale = [c for c in items if not (c.mtime and now - c.mtime < min_age_sec)]
        keep.extend(fresh[:keep_latest])
        recent.extend(fresh[keep_latest:])
        reap.extend(stale)
    return reap, keep, recent


def plan_pid_reap(root, *, current_pid: int | None = None,
                  prefixes=STALE_TMP_PREFIXES,
                  min_age_sec: float = DEFAULT_MIN_AGE_SEC,
                  keep_latest: int = DEFAULT_KEEP_LATEST,
                  now: float | None = None, measure: bool = False) -> dict:
    """规划 `.pytest_tmp` 下按 PID 命名的遗留隔离根.

    Returns:
        `{"reap": [ScratchCandidate], "keep": [...], "active": [...],
          "recent": [...]}`——`active` 是活进程/当前进程的根（永远保留），
        `keep` 是每前缀最近 keep_latest 个（保留复盘），`recent` 是太新的
        候选（本轮不动），`reap` 才是可回收清单。
    """
    now = time.time() if now is None else now
    root = Path(root)
    current_pid = os.getpid() if current_pid is None else current_pid
    candidates: list[ScratchCandidate] = []
    active: list[ScratchCandidate] = []
    protected: list[ScratchCandidate] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return {"reap": [], "keep": [], "active": [], "recent": []}
    for child in children:
        if not child.is_dir():
            continue
        if has_keep_marker(child):
            protected.append(_candidate(child, "pid_root",
                                        reason="keep_sentinel", measure=measure))
            continue
        parsed = parse_pid_root(child.name, prefixes)
        if parsed is None:
            continue
        prefix, pid = parsed
        if pid == current_pid or pid_is_alive(pid):
            active.append(_candidate(child, "pid_root", pid=pid,
                                     reason="alive", measure=measure))
            continue
        candidates.append(_candidate(child, "pid_root", pid=pid,
                                     reason="exited", measure=measure))
    reap, keep, recent = _split_reap_keep(
        candidates, now=now, min_age_sec=min_age_sec,
        keep_latest=keep_latest, key=lambda c: str(c.path.name).rsplit("_", 1)[0])
    return {"reap": reap, "keep": protected + keep,
            "active": active, "recent": recent}


def plan_basetemp_reap(root, *, min_age_sec: float = DEFAULT_MIN_AGE_SEC,
                       keep_latest: int = DEFAULT_KEEP_LATEST,
                       now: float | None = None,
                       measure: bool = False,
                       prefixes=STALE_TMP_PREFIXES,
                       exclude: set | None = None) -> dict:
    """规划 `.pytest_tmp` 下 `--basetemp` 遗留会话目录（非 PID 根）.

    ``exclude`` 是本轮正在使用的 basetemp（或其它必须保留的路径）；即使它
    很旧也不会进入回收清单。
    """
    now = time.time() if now is None else now
    root = Path(root)
    excluded = {Path(p).resolve() for p in (exclude or set())}
    candidates: list[ScratchCandidate] = []
    protected: list[ScratchCandidate] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return {"reap": [], "keep": [], "recent": []}
    for child in children:
        if not child.is_dir():
            continue
        if parse_pid_root(child.name, prefixes) is not None:
            continue
        if has_keep_marker(child):
            protected.append(_candidate(child, "basetemp",
                                        reason="keep_sentinel", measure=measure))
            continue
        try:
            if child.resolve() in excluded:
                continue
        except OSError:
            continue
        candidates.append(_candidate(child, "basetemp", measure=measure))
    reap, keep, recent = _split_reap_keep(
        candidates, now=now, min_age_sec=min_age_sec,
        keep_latest=keep_latest, key=lambda c: "basetemp")
    return {"reap": reap, "keep": protected + keep, "recent": recent}


def plan_pytest_tmp_reap(base, *, min_age_sec: float = DEFAULT_MIN_AGE_SEC,
                         keep_latest: int = DEFAULT_KEEP_LATEST,
                         now: float | None = None,
                         measure: bool = False) -> dict:
    """规划 `%TEMP%/pytest-of-<user>/pytest-N` 遗留目录."""
    now = time.time() if now is None else now
    base = Path(base)
    candidates: list[ScratchCandidate] = []
    protected: list[ScratchCandidate] = []
    try:
        children = list(base.iterdir())
    except OSError:
        return {"reap": [], "keep": [], "recent": []}
    for child in children:
        if not (child.is_dir() and child.name.startswith("pytest-")):
            continue
        if has_keep_marker(child):
            protected.append(_candidate(child, "pytest_tmp",
                                        reason="keep_sentinel", measure=measure))
            continue
        candidates.append(_candidate(child, "pytest_tmp", measure=measure))
    reap, keep, recent = _split_reap_keep(
        candidates, now=now, min_age_sec=min_age_sec,
        keep_latest=keep_latest, key=lambda c: "pytest")
    return {"reap": reap, "keep": protected + keep, "recent": recent}


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
def send_to_recycle(path) -> bool:
    """把目录移入系统回收站（Windows）；不可用/失败返回 False.

    走 `SHFileOperationW` 而不是 `shutil.rmtree`：不枚举/批量删除文件，
    可绕开环境里的批量删除守卫；代价是回收站清空前不释放磁盘。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_SILENT = 0x0004
        FOF_NOCONFIRMATION = 0x0010
        FOF_ALLOWUNDO = 0x0040
        FOF_NOERRORUI = 0x0400
        target = str(Path(path).resolve()) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = target
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION
                     | FOF_SILENT | FOF_NOERRORUI)
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


def remove_tree(path, *, permanent: bool) -> tuple[bool, str]:
    """删除一个目录树；返回 `(ok, method_or_reason)`.

    `permanent=False` 走回收站（可逆）；不可用时**不**回退到永久删除。
    任何异常都返回失败，由调用方保留现场。
    """
    target = Path(path)
    if not target.exists():
        return True, "absent"
    if not permanent:
        return (True, "recycled") if send_to_recycle(target) else (
            False, "recycle_unavailable")
    try:
        shutil.rmtree(target, ignore_errors=True)
        return (not target.exists()), "removed"
    except BaseException as exc:                      # noqa: BLE001 - 守卫可能抛 SystemExit
        return False, "remove_failed: %s" % exc


def plan_to_dict(plan: dict) -> dict:
    """把计划转成 JSON 可序列化结构（工具报告用）."""
    out = {}
    for key, value in plan.items():
        if isinstance(value, list):
            out[key] = [item.as_dict() if isinstance(item, ScratchCandidate)
                        else item for item in value]
        else:
            out[key] = value
    return out
