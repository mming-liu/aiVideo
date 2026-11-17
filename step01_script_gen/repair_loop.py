# -*- coding: utf-8 -*-
"""M1：修复轮额度/落盘辅助（2026-11-18）.

从 ``step01_script_gen/main.py`` 拆出的成对额度预检；供提交循环在发起修复前
判断「一次修复 + 一次修后复审」两层额度是否都够。只读，不发送。
"""
from __future__ import annotations


def paired_repair_budget() -> dict:
    """**成对额度预检读数**（M2.2b，2026-11-17 补双层）.

    「一次修复 + 一次修后复审」这一对，逻辑调用层与网络尝试层都要够：
    各阶段 ≥1，且全局 ≥2。未安装 ``OutboundGuard`` 时全部读数为 ``None``
    （= 不限）、``short=False``，未接管预算的调用方行为不变。
    """
    rec = {"remaining_logical_repair": None, "remaining_logical_audit": None,
           "remaining_logical_total": None,
           "remaining_attempts_repair": None, "remaining_attempts_audit": None,
           "remaining_attempts_total": None,
           "short": False, "short_on": [],
           "required": {"repair": 1, "audit": 1}}
    try:
        from shared.outbound_guard import remaining_logical as _rem_logical
        from shared.outbound_guard import remaining_attempts as _rem_attempts
        _lr, _la, _lg = _rem_logical("repair"), _rem_logical("audit"), _rem_logical()
        _ar, _aa, _ag = (_rem_attempts("repair"), _rem_attempts("audit"),
                         _rem_attempts())
    except Exception:                              # noqa: BLE001
        return rec
    rec.update({
        "remaining_logical_repair": _lr, "remaining_logical_audit": _la,
        "remaining_logical_total": _lg,
        "remaining_attempts_repair": _ar, "remaining_attempts_audit": _aa,
        "remaining_attempts_total": _ag,
    })
    short_on = []
    if _lr is not None and _lr < 1:
        short_on.append("logical_repair")
    if _la is not None and _la < 1:
        short_on.append("logical_audit")
    if _lg is not None and _lg < 2:
        short_on.append("logical_total")
    if _ar is not None and _ar < 1:
        short_on.append("attempts_repair")
    if _aa is not None and _aa < 1:
        short_on.append("attempts_audit")
    if _ag is not None and _ag < 2:
        short_on.append("attempts_total")
    rec["short_on"] = short_on
    rec["short"] = bool(short_on)
    return rec
