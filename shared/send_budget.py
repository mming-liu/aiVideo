# -*- coding: utf-8 -*-
"""**跨恢复的发送预算**（本包 §5，2026-11-07）.

问题：提交/恢复入口每一轮都能重新发审计与修复，重启进程就等于把额度清零。
计划给的硬约束是"本包 12 次实际发送（<=6 修复 + <=6 审计），重试计入所属类别"。

这里把计数落在一个 JSON 文件里（章节目录），由**发送前**的统一入口读写：

  · 进程内：模块级锁，避免多线程竞争；
  · 进程间：由 Step01 的章节锁保证一次只有一个 runner（本模块不重复加锁）；
  · 每次允许发送前 +1，拒绝时**不**写入——被拒的请求不计入；
  · 文件损坏/不可读 ⇒ 拒绝发送（宁可停下，也不在无记录的情况下烧额度）。

未设置 STEP01_SEND_BUDGET_FILE 时完全不生效（测试与其它阶段零行为变化）。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ENV_FILE = "STEP01_SEND_BUDGET_FILE"
ENV_CAP_SENDS = "STEP01_SEND_BUDGET_TOTAL"
ENV_CAP_REPAIRS = "STEP01_SEND_BUDGET_REPAIRS"
ENV_CAP_AUDITS = "STEP01_SEND_BUDGET_AUDITS"

DEFAULT_CAP_SENDS = 12
DEFAULT_CAP_REPAIRS = 6
DEFAULT_CAP_AUDITS = 6

_KIND_BY_SCHEMA = {
    "repair_shot_replacements": "repairs",
    "script_audit": "audits",
}

_LOCK = threading.Lock()


class BudgetExhausted(RuntimeError):
    """预算已耗尽：调用方必须停止新增发送并保存可恢复状态."""


def _int_env(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default


def kind_of(schema) -> str:
    return _KIND_BY_SCHEMA.get(str(schema or ""), "others")


def _state_path():
    raw = (os.getenv(ENV_FILE) or "").strip()
    return Path(raw) if raw else None

def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("预算文件不是对象")
        return data
    except Exception as exc:                                     # noqa: BLE001
        raise BudgetExhausted("发送预算文件不可读（拒绝发送）: %s" % exc) from exc


def _save(path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def allow(schema) -> None:
    """允许发送则计数 +1；否则抛 BudgetExhausted（不写入）."""
    path = _state_path()
    if path is None:
        return
    kind = kind_of(schema)
    with _LOCK:
        data = _load(path)
        caps = {"sends": _int_env(ENV_CAP_SENDS, DEFAULT_CAP_SENDS),
                "repairs": _int_env(ENV_CAP_REPAIRS, DEFAULT_CAP_REPAIRS),
                "audits": _int_env(ENV_CAP_AUDITS, DEFAULT_CAP_AUDITS)}
        used = {k: int(data.get(k) or 0) for k in ("sends", "repairs", "audits")}
        if used["sends"] >= caps["sends"]:
            raise BudgetExhausted("发送预算耗尽：已用 %d/%d（本包上限）"
                                  % (used["sends"], caps["sends"]))
        if kind in ("repairs", "audits") and used[kind] >= caps[kind]:
            raise BudgetExhausted("%s 预算耗尽：已用 %d/%d"
                                  % (kind, used[kind], caps[kind]))
        used["sends"] += 1
        if kind in ("repairs", "audits"):
            used[kind] += 1
        data.update(used)
        data["last_schema"] = str(schema or "")
        _save(path, data)


def snapshot() -> dict:
    path = _state_path()
    if path is None or not path.exists():
        return {}
    try:
        return _load(path)
    except BudgetExhausted:
        return {}


__all__ = ["BudgetExhausted", "ENV_FILE", "allow", "kind_of", "snapshot"]

