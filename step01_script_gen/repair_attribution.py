# -*- coding: utf-8 -*-
"""M01：授权路径的**四阶段归因**（模型写入 / 后处理 writer / 事务回滚 / 保留）.

背景（run j 实测，2026-11-15）：诊断台账的 `postprocess_reverted` 在
`_repair_acceptance_decision` **之后**才计算，而拒收分支已经执行
``script = _round_before_script``（整轮回退）。于是该字段比较的是
「合并后快照 vs 已回到基线的稿子」，把模型写入、后处理改写和事务回滚
三种不同来源的差异混成一个数（run j 记为 shot_4 的 36 个字段）。

本模块提供确定性的阶段归属。它**只记事实、不改任何接受判据**：

* 阶段 A = 授权合并后、后处理前（只含模型写入）
* 阶段 B = 所有后处理结束、事务裁决前（`freeze_reverify` 的同一份稿）
* 阶段 C = 按目标回退后（若发生）
* 阶段 D = 最终返回稿（整轮回退则为基线）

`postprocess_reverted` 固定比较 A→B；事务回滚另记 B→D。
逐 writer 快照用于定位「首次把 A 的值改掉」的那个约束，没有这条证据
就不能把差异归因到具体 writer。

口径与 `main._flatten_exact_paths` / `repair_fields.path_allowed` 一致；
值用稳定 JSON 的 SHA-256 前 16 位作指纹，避免把整段文本塞进台账。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from step01_script_gen.repair_fields import path_allowed, shot_by_target

_MISSING = "<missing>"
_STAGE_KEYS = ("baseline", "merged", "postprocess", "post_rollback", "final")


def flatten_exact_paths(obj: Any, prefix: str = "") -> dict:
    """展平为精确路径 → 值（与 `main._flatten_exact_paths` 同口径）."""
    out: dict = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = "%s.%s" % (prefix, key) if prefix else str(key)
            out.update(flatten_exact_paths(value, key_path))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out.update(flatten_exact_paths(value, "%s.%d" % (prefix, i)))
    elif prefix:
        out[prefix] = obj
    return out


def _stable(value: Any) -> str:
    if value is _MISSING:
        return _MISSING
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001 - 只用于诊断展示，不能让观测抛异常
        return repr(value)


def value_fingerprint(value: Any) -> str:
    """值指纹（缺失是独立哨兵，不能与空字符串/None 混淆）."""
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()[:16]


def authorized_path_snapshot(script, allowed_by_target) -> dict:
    """{target: {精确路径: 值指纹}}，只取本轮授权且当前存在的路径."""
    out: dict = {}
    if not isinstance(script, dict):
        return out
    for tid, allowed in (allowed_by_target or {}).items():
        if not allowed:
            continue
        shot = shot_by_target(script, tid)
        if not isinstance(shot, dict):
            continue
        flat = flatten_exact_paths(shot)
        out[str(tid)] = {
            p: value_fingerprint(v) for p, v in flat.items()
            if path_allowed(p, allowed)
        }
    return out


def changed_paths(before: dict, after: dict) -> dict:
    """两个授权路径快照之间「值变了 / 路径出现或消失」的清单."""
    out: dict = {}
    for tid in sorted(set(before or {}) | set(after or {})):
        b = (before or {}).get(tid) or {}
        a = (after or {}).get(tid) or {}
        diff = sorted(p for p in set(b) | set(a)
                      if b.get(p, _MISSING) != a.get(p, _MISSING))
        if diff:
            out[tid] = diff
    return out


def _preview(script, tid: str, path: str) -> str:
    """短值展示（长文本截断），仅用于人读，不参与任何判据."""
    if not isinstance(script, dict):
        return _MISSING
    shot = shot_by_target(script, tid)
    if not isinstance(shot, dict):
        return _MISSING
    flat = flatten_exact_paths(shot)
    if path not in flat:
        return _MISSING
    s = _stable(flat[path])
    return s if len(s) <= 120 else s[:117] + "..."


def attribute_field_loss(*, snapshots: dict, writer_nodes=(),
                         preview_scripts: dict | None = None,
                         allowed_by_target=None) -> dict:
    """逐字段给出可核验的阶段归属.

    `snapshots` 为 ``{stage: authorized_path_snapshot(...)}``，至少含
    ``baseline`` / ``merged`` / ``postprocess`` / ``final``；``post_rollback``
    可选。`writer_nodes` 是账本节点列表，节点里的 ``authorized_paths`` 由
    `RepairEffectLedger._freeze` 逐 writer 冻结。

    返回 ``fields``（逐目标逐路径）与 ``summary``（计数）。每条记录给的是
    **独立布尔事实**而非单一结论：writer 改过与事务回滚可以同时为真，
    这样不会为了归类而丢失证据。
    """
    s0 = snapshots.get("baseline") or {}
    s1 = snapshots.get("merged") or {}
    s2 = snapshots.get("postprocess") or {}
    s3 = snapshots.get("final") or {}
    sc = snapshots.get("post_rollback") or s2
    # 只认 writer 节点（NODE_WRITER 的字面量；本模块不 import repair_effects，
    # 否则与账本形成循环依赖）。`reverify` 节点就是阶段 B 本身，不能算成
    # 「首次改动的 writer」，否则归因会永远指向最后一个观测点。
    writers = [(str(n.get("writer") or n.get("node") or "?"),
                n.get("authorized_paths") or {})
               for n in (writer_nodes or ())
               if str(n.get("node") or "") == "writer"]

    fields: dict = {}
    summary = {"model_written": 0, "writer_changed": 0,
               "writer_reverted": 0, "transaction_rolled_back": 0,
               "writer_changed_and_rolled_back": 0,
               "kept": 0, "postprocess_only": 0, "total": 0}
    previews = preview_scripts or {}
    for tid in sorted(set(s0) | set(s1) | set(s2) | set(sc) | set(s3)):
        all_paths = set()
        for snap in (s0, s1, s2, sc, s3):
            all_paths |= set((snap or {}).get(tid) or {})
        for _wname, wsnap in writers:
            all_paths |= set((wsnap or {}).get(tid) or {})
        for path in sorted(all_paths):
            def _v(snap):
                return ((snap or {}).get(tid) or {}).get(path, _MISSING)

            v0, v1, v2, vc, v3 = _v(s0), _v(s1), _v(s2), _v(sc), _v(s3)
            model_written = v1 != v0
            writer_changed = v2 != v1
            rolled_back = v3 != v2
            if not (model_written or writer_changed or rolled_back or v3 != v0):
                continue

            first_writer = None
            for wname, wsnap in writers:
                wv = ((wsnap or {}).get(tid) or {}).get(path, _MISSING)
                if wv != v1:
                    first_writer = wname
                    break

            if rolled_back:
                stage = "transaction_rollback"
                summary["transaction_rolled_back"] += 1
            elif writer_changed and model_written:
                # writer 把**模型写入的值**改掉才算「抵消」；模型没写过的路径
                # 被 writer 改动只是后处理自身的写入，不能冒称修复被撤销。
                stage = "writer_reverted"
                summary["writer_reverted"] += 1
            elif model_written:
                stage = "kept"
                summary["kept"] += 1
            else:
                stage = "postprocess_only"
                summary["postprocess_only"] += 1
            if model_written:
                summary["model_written"] += 1
            if writer_changed:
                summary["writer_changed"] += 1
            if writer_changed and rolled_back:
                summary["writer_changed_and_rolled_back"] += 1
            summary["total"] += 1
            # `stage` 是主归因（便于排序/汇总）；`stages` 是**全部成立的事实**。
            # 一个字段可以既被 writer 改过、又被整轮回退抹掉，单看 stage 会丢掉
            # 前半段证据，M01 的 36 字段里就有这种复合情形。
            stages = []
            if model_written:
                stages.append("model_written")
            if writer_changed:
                stages.append("writer_changed")
            if rolled_back:
                stages.append("transaction_rolled_back")
            if not stages:
                stages.append("unchanged")

            rec = {
                "stage": stage,
                "stages": stages,
                "model_written": bool(model_written),
                "writer_changed": bool(writer_changed),
                "first_writer": first_writer,
                "transaction_rolled_back": bool(rolled_back),
                "post_rollback_differs": bool(vc != v2),
                "digests": {"baseline": v0, "merged": v1,
                            "postprocess": v2, "post_rollback": vc,
                            "final": v3},
            }
            if previews:
                rec["values"] = {
                    stage_key: _preview(script, tid, path)
                    for stage_key, script in previews.items()
                    if stage_key in _STAGE_KEYS
                }
            fields.setdefault(tid, {})[path] = rec
    return {"fields": fields, "summary": summary}
