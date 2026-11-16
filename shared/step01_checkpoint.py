# -*- coding: utf-8 -*-
"""Step 01 阶段级原子检查点（M2.3 / 计划 CP1–CP4）.

## 为什么

旧口径用「全局 1200s 减已用时间 < 840s」一刀切：初审一旦花掉 430s，哪怕只差
250s 就能跑完修复，也会直接 `commit_blocked` 退出，**已完成的生成与初审全部作废**。
检查点把进度变成**可恢复的数据**：每到一个阶段边界就原子落盘，下一次从最近一个
检查点续跑，零重复生成、零重复初审。

## 阶段

| 检查点 | 含义 |
|---|---|
| `CP1_PLAN_VERIFIED` | 大纲与顺序投影核验完成（可进入生成） |
| `CP2_CANDIDATE_GENERATED` | 正片/钩子生成并完成确定性 finalize |
| `CP3_INITIAL_AUDIT_DONE` | 首轮审计完成（`step01_audit_rounds.json` 已落盘） |
| `CP4_REPAIR_ROUND_K_DONE` | 第 k 轮受限修复与复审完成 |

## 契约

* 文件名固定：`snapshots/cp_<stage>.json`（CP4 带 `_r<k>` 后缀），便于直接定位；
* 另写索引 `snapshots/_checkpoints.json`：`{stage: {path, revision, written_at}}`，
  指针式读取不需要猜"最新目录"；
* **写入失败只告警**（检查点是保护性副作用，不能反过来打断提交）；
* 读回校验 `compute_script_revision` 一致，避免"写盘成功但内容不是这份稿"。

本模块不判断"该不该续跑"，只负责**如实记录**每个阶段边界上有什么。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CP1_PLAN_VERIFIED = "CP1_PLAN_VERIFIED"
CP2_CANDIDATE_GENERATED = "CP2_CANDIDATE_GENERATED"
CP3_INITIAL_AUDIT_DONE = "CP3_INITIAL_AUDIT_DONE"
CP4_REPAIR_ROUND_K_DONE = "CP4_REPAIR_ROUND_K_DONE"

STAGES = (CP1_PLAN_VERIFIED, CP2_CANDIDATE_GENERATED,
          CP3_INITIAL_AUDIT_DONE, CP4_REPAIR_ROUND_K_DONE)

INDEX_NAME = "_checkpoints.json"


def _snap_dir(chapter_dir) -> Path:
    return Path(chapter_dir) / "snapshots"


def checkpoint_path(chapter_dir, stage: str, *, round_no: int | None = None) -> Path:
    name = str(stage)
    if stage == CP4_REPAIR_ROUND_K_DONE and round_no is not None:
        name = "%s_r%02d" % (name, int(round_no))
    return _snap_dir(chapter_dir) / ("cp_%s.json" % name)


def write(chapter_dir, stage: str, *, script=None, audit=None,
          extra: dict | None = None, round_no: int | None = None,
          log=None) -> dict:
    """原子写一个阶段检查点；返回记录（失败也返回，含 `error`，**不抛**）."""
    rec: dict = {"stage": str(stage), "path": "", "revision": "", "written": False,
                 # 微秒精度：同一秒内连写两个检查点时，索引里的"最近"仍可判定
                 "written_at": time.strftime("%Y-%m-%dT%H:%M:%S") + ".%06d"
                 % (int(time.time() * 1e6) % 1000000), "error": ""}
    try:
        from shared.json_io import atomic_dump_json as _adj
        from shared.quality_gate import compute_script_revision as _csr

        snap = _snap_dir(chapter_dir)
        snap.mkdir(parents=True, exist_ok=True)
        path = checkpoint_path(chapter_dir, stage, round_no=round_no)
        payload = {
            "stage": str(stage),
            "round": round_no,
            "written_at": rec["written_at"],
            "script": script,
            "audit": audit,
            "extra": extra or {},
        }
        _adj(path, payload, ensure_ascii=False, indent=2)
        if isinstance(script, dict):
            rec["revision"] = _csr(script)
            back = json.loads(path.read_text(encoding="utf-8"))
            if _csr(back.get("script") or {}) != rec["revision"]:
                raise RuntimeError("检查点读回 revision 不一致")
        rec.update({"path": str(path), "written": True})

        # 索引：指针式，不依赖"最新目录"或 mtime
        idx_path = snap / INDEX_NAME
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if not isinstance(idx, dict):
                idx = {}
        except Exception:
            idx = {}
        idx[str(stage) if round_no is None
            else "%s_r%02d" % (str(stage), int(round_no))] = {
                "path": rec["path"], "revision": rec["revision"],
                "written_at": rec["written_at"], "round": round_no}
        _adj(idx_path, idx, ensure_ascii=False, indent=2)
    except Exception as _e:                       # noqa: BLE001
        rec["error"] = "%s: %s" % (type(_e).__name__, _e)
        try:
            if log is not None:
                log.warn("[CHECKPOINT] %s 写入失败（不打断提交）: %s" % (stage, _e))
        except Exception:
            pass
    return rec


def read(chapter_dir, stage: str, *, round_no: int | None = None) -> dict | None:
    """读一个阶段检查点（不存在/不可解析 → None，不猜内容）."""
    path = checkpoint_path(chapter_dir, stage, round_no=round_no)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def latest(chapter_dir) -> dict | None:
    """最近写入的检查点（按索引里的 `written_at`，不按 mtime 猜）."""
    idx_path = _snap_dir(chapter_dir) / INDEX_NAME
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(idx, dict) or not idx:
        return None
    key, rec = max(idx.items(),
                   key=lambda kv: str((kv[1] or {}).get("written_at") or ""))
    return {"key": key, **(rec or {})}


__all__ = ["CP1_PLAN_VERIFIED", "CP2_CANDIDATE_GENERATED", "CP3_INITIAL_AUDIT_DONE",
           "CP4_REPAIR_ROUND_K_DONE", "INDEX_NAME", "STAGES", "checkpoint_path",
           "latest", "read", "write"]
