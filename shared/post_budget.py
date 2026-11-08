# -*- coding: utf-8 -*-
"""图片/视频 POST 的跨进程预算与额度熔断（2026-11-08 WP7-Prep）.

与 Step01 的 shared/send_budget.py 同构，但面向 POST 次数：

- 只有显式设置 *_BUDGET_TOTAL > 0 才启用；未设置时完全无行为变化。
- 账本落在章节目录（或 *_BUDGET_FILE），每次发送前原子检查并 +1；
  进程内 threading 锁 + 跨进程文件锁（Windows msvcrt / POSIX fcntl）。
- 文件损坏/不可读 ⇒ 拒绝发送（fail-closed）。
- 上游明确返回额度耗尽（403 SetLimitExceeded / QuotaExceeded）时写入熔断标记，
  重启后仍拒绝发送；需显式 reset 或人工删账本。
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

_KINDS = {
    "image": ("STEP02A_IMAGE_BUDGET_FILE", "STEP02A_IMAGE_BUDGET_TOTAL",
              "step02a_image_budget.json"),
    "video": ("STEP02B_VIDEO_BUDGET_FILE", "STEP02B_VIDEO_BUDGET_TOTAL",
              "step02b_video_budget.json"),
}

_LOCK = threading.Lock()
_QUOTA: dict[str, str] = {}
_QUOTA_ERROR_MARKERS = (
    "setlimitexceeded", "quotaexceeded", "quota exceeded", "quota_exceeded",
    "额度耗尽", "额度不足", "配额不足", "配额耗尽",
)


class PostBudgetExhausted(RuntimeError):
    """预算/额度已耗尽：调用方必须在发送前停止."""


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _config(kind: str):
    """返回 (ledger_path | None, limit)；未启用返回 (None, 0)."""
    try:
        env_file, env_total, default_name = _KINDS[kind]
    except KeyError as exc:
        raise ValueError("未知预算类型: %r" % kind) from exc
    limit = _int(os.getenv(env_total), 0)
    if limit <= 0:
        return None, 0
    raw = (os.getenv(env_file) or "").strip()
    if raw:
        return Path(raw), limit
    from shared.paths import chapter_dir
    return Path(chapter_dir()) / default_name, limit


def _read(path: Path, kind: str, expected_limit: int) -> dict:
    """读取并严格校验账本；缺失返回初始状态；字段损坏/身份不符一律拒绝.

    不允许 used 缺失/负数/字符串被当成 0（那会静默恢复额度），也不允许
    账本 limit 与本次 TOTAL 不一致（那会在恢复时静默扩大/缩小预算）。
    """
    if not path.exists():
        return {"kind": kind, "used": 0, "limit": int(expected_limit),
                "quota_exhausted": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PostBudgetExhausted("预算账本不可读（拒绝发送）: %s" % exc) from exc
    if not isinstance(data, dict):
        raise PostBudgetExhausted("预算账本不是 JSON 对象（拒绝发送）: %s" % path)
    if data.get("kind") != kind:
        raise PostBudgetExhausted(
            "预算账本身份不符：kind=%r，期望 %r（拒绝发送）"
            % (data.get("kind"), kind))
    used = data.get("used")
    if isinstance(used, bool) or not isinstance(used, int) or used < 0:
        raise PostBudgetExhausted(
            "预算账本 used 非法：%r（拒绝发送，禁止按 0 重置）" % (used,))
    limit = data.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise PostBudgetExhausted(
            "预算账本 limit 非法：%r（拒绝发送）" % (limit,))
    if int(expected_limit) and limit != int(expected_limit):
        raise PostBudgetExhausted(
            "预算身份不一致：账本 limit=%d，本次 TOTAL=%d；"
            "不得静默扩大/重置，如需改预算请显式 reset"
            % (limit, int(expected_limit)))
    quota = data.get("quota_exhausted", "")
    if quota not in (None, "") and not isinstance(quota, str):
        raise PostBudgetExhausted("预算账本 quota_exhausted 非法（拒绝发送）")
    data["quota_exhausted"] = str(quota or "")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _file_lock(path: Path):
    """跨进程排他锁：锁文件与账本同目录."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def acquire(kind: str, amount: int = 1) -> int:
    """发送前原子扣减；超限/熔断抛 PostBudgetExhausted；返回已用次数."""
    amount = max(1, int(amount))
    path, limit = _config(kind)
    if path is None:
        if _QUOTA.get(kind):
            raise PostBudgetExhausted("额度已熔断（%s）：%s" % (kind, _QUOTA[kind]))
        return 0
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        quota = data["quota_exhausted"]
        if quota:
            _QUOTA[kind] = quota
            raise PostBudgetExhausted("额度已熔断（%s）：%s" % (kind, quota))
        used = data["used"]
        effective = data["limit"]
        if used + amount > effective:
            raise PostBudgetExhausted(
                "POST 预算耗尽（%s）：已用 %d/%d" % (kind, used, effective))
        used += amount
        data.update({"kind": kind, "used": used, "limit": effective,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        data.pop("quota_exhausted", None)
        _write(path, data)
        return used


def mark_quota_exhausted(kind: str, message: str) -> None:
    """记录上游额度耗尽；有账本时落盘，重启后仍拒绝发送."""
    text = str(message or "quota exhausted")[:500]
    _QUOTA[kind] = text
    path, _limit = _config(kind)
    if path is None:
        return
    with _LOCK, _file_lock(path):
        data = _read(path, kind, _limit)
        data.update({"kind": kind, "quota_exhausted": text,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        _write(path, data)


def quota_exhausted(kind: str) -> str:
    """返回熔断原因（空=未熔断）；账本损坏按熔断处理（fail-closed）."""
    if _QUOTA.get(kind):
        return _QUOTA[kind]
    path, _limit = _config(kind)
    if path is None:
        return ""
    try:
        with _LOCK, _file_lock(path):
            data = _read(path, kind, _limit)
    except PostBudgetExhausted as exc:
        return str(exc)
    text = str(data.get("quota_exhausted") or "")
    if text:
        _QUOTA[kind] = text
    return text


def usage(kind: str) -> dict:
    """读取当前账本（诊断/报告用）；未启用返回 {}."""
    path, limit = _config(kind)
    if path is None:
        return {}
    try:
        with _LOCK, _file_lock(path):
            data = _read(path, kind, limit)
    except PostBudgetExhausted as exc:
        return {"error": str(exc), "path": str(path), "limit": limit}
    data.setdefault("limit", limit)
    data["path"] = str(path)
    return data


def clear_quota(kind: str) -> None:
    """清除额度熔断标记但保留已用次数（账户限额调整后恢复用）."""
    _QUOTA.pop(kind, None)
    path, limit = _config(kind)
    if path is None:
        return
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        data.pop("quota_exhausted", None)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(path, data)


def reset(kind=None) -> None:
    """测试/人工重置：清内存标记并删除账本（含锁文件）."""
    kinds = [kind] if kind else list(_KINDS)
    for item in kinds:
        _QUOTA.pop(item, None)
        path, _limit = _config(item)
        if path is None:
            continue
        for target in (path, path.with_suffix(path.suffix + '.lock'),
                       path.with_suffix(path.suffix + '.tmp')):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


def is_quota_error(text) -> bool:
    """判断上游错误是否明确表示账户额度/配额耗尽."""
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _QUOTA_ERROR_MARKERS)


__all__ = ["PostBudgetExhausted", "acquire", "clear_quota", "is_quota_error",
           "mark_quota_exhausted", "quota_exhausted", "reset", "usage"]
