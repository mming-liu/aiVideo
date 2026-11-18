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
* **暂停态单独落盘** `snapshots/_paused.json`（`write_paused`/`read_paused`），
  不进四态索引，`latest()` 的正常语义不被污染；
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

#: 暂停态（T1，2026-11-18）：超时/单阶段 SLA 触发时，把**最近完成的 CP**
#: 如实记为「停在哪个阶段边界」。命名与四态同风格，但**单独文件 + 单独读接口**，
#: 绝不写进 `_checkpoints.json`——否则 `latest()` 的正常四态语义会被暂停态污染。
PAUSED_AT_CP1_PLAN_VERIFIED = "PAUSED_AT_CP1_PLAN_VERIFIED"
PAUSED_AT_CP2_CANDIDATE_GENERATED = "PAUSED_AT_CP2_CANDIDATE_GENERATED"
PAUSED_AT_CP3_INITIAL_AUDIT_DONE = "PAUSED_AT_CP3_INITIAL_AUDIT_DONE"
PAUSED_AT_CP4_REPAIR_ROUND_K_DONE = "PAUSED_AT_CP4_REPAIR_ROUND_K_DONE"

PAUSED_STAGES = (PAUSED_AT_CP1_PLAN_VERIFIED, PAUSED_AT_CP2_CANDIDATE_GENERATED,
                 PAUSED_AT_CP3_INITIAL_AUDIT_DONE, PAUSED_AT_CP4_REPAIR_ROUND_K_DONE)

_PAUSED_BY_CP = {
    CP1_PLAN_VERIFIED: PAUSED_AT_CP1_PLAN_VERIFIED,
    CP2_CANDIDATE_GENERATED: PAUSED_AT_CP2_CANDIDATE_GENERATED,
    CP3_INITIAL_AUDIT_DONE: PAUSED_AT_CP3_INITIAL_AUDIT_DONE,
    CP4_REPAIR_ROUND_K_DONE: PAUSED_AT_CP4_REPAIR_ROUND_K_DONE,
}
_CP_BY_PAUSED = {v: k for k, v in _PAUSED_BY_CP.items()}
#: 恢复起点标签（`RESUME_FROM_CP3`）——与四态一一对应，只用于人读决策。
RESUME_FROM_BY_CP = {
    CP1_PLAN_VERIFIED: "CP1",
    CP2_CANDIDATE_GENERATED: "CP2",
    CP3_INITIAL_AUDIT_DONE: "CP3",
    CP4_REPAIR_ROUND_K_DONE: "CP4",
}

INDEX_NAME = "_checkpoints.json"
#: 暂停态单独落盘：`snapshots/_paused.json`（原子替换，只保留最近一次暂停）。
PAUSED_NAME = "_paused.json"


class CheckpointError(RuntimeError):
    """检查点内容非法（损坏 / 字段缺失 / 状态不自洽）——读取端应 fail-closed."""


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
    """最近写入的检查点（按索引里的 `written_at`，不按 mtime 猜）.

    只读 `_checkpoints.json`（四态索引）；暂停态在 `_paused.json`，不会出现在这里。
    """
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


def _normalize_cp_stage(stage: str) -> str:
    """把四态 / 暂停态 / CP4_rNN 归一成四态之一（非法 → CheckpointError）."""
    name = str(stage or "").strip()
    if name in STAGES:
        return name
    if name in _CP_BY_PAUSED:
        return _CP_BY_PAUSED[name]
    if name.startswith(CP4_REPAIR_ROUND_K_DONE + "_r"):
        suffix = name[len(CP4_REPAIR_ROUND_K_DONE) + 2:]
        if suffix.isdigit():
            return CP4_REPAIR_ROUND_K_DONE
    raise CheckpointError("非法检查点阶段: %r" % (stage,))


def paused_stage_for(stage: str) -> str:
    """四态 → 对应暂停态名（如 CP3 → PAUSED_AT_CP3_INITIAL_AUDIT_DONE）."""
    return _PAUSED_BY_CP[_normalize_cp_stage(stage)]


def paused_path(chapter_dir) -> Path:
    return _snap_dir(chapter_dir) / PAUSED_NAME


def write_paused(chapter_dir, stage: str, *, run_id: str = "", revision: str = "",
                 reason: str = "", extra: dict | None = None, script=None,
                 log=None) -> dict:
    """原子写「停在某个 CP 边界」的暂停态；返回记录（失败也返回，**不抛**）.

    暂停态是保护性副作用：超时/预算不足时**不能反过来打断 BLOCK 结论**。
    与 `write()` 的关键区别：只写 `snapshots/_paused.json`，**不碰**
    `_checkpoints.json`，因此 `latest()` 仍只看到正常四态。

    `stage` 可以是四态（推荐）、暂停态或 `CP4..._rNN`；`revision` 必须给出，
    或由 `script` 计算——「暂停在哪份稿上」不能留空。
    """
    rec: dict = {"paused": True, "stage": "", "checkpoint_stage": str(stage),
                 "resume_from": "", "run_id": str(run_id or ""),
                 "revision": str(revision or ""), "reason": str(reason or ""),
                 "path": "", "written": False, "error": "",
                 # 微秒精度：同秒内多次超时也能按时间排序
                 "written_at": time.strftime("%Y-%m-%dT%H:%M:%S") + ".%06d"
                 % (int(time.time() * 1e6) % 1000000)}
    try:
        cp_stage = _normalize_cp_stage(stage)
        rec["checkpoint_stage"] = cp_stage
        rec["stage"] = _PAUSED_BY_CP[cp_stage]
        rec["resume_from"] = RESUME_FROM_BY_CP[cp_stage]
        if script is not None:
            from shared.quality_gate import compute_script_revision as _csr
            if not isinstance(script, dict):
                raise CheckpointError("script 不是对象，无法计算 revision")
            rec["revision"] = _csr(script)
        if not rec["revision"]:
            raise CheckpointError("PAUSED 记录缺少 revision（不知道停在哪份稿上）")

        from shared.json_io import atomic_dump_json as _adj
        path = paused_path(chapter_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 落盘记录必须自证「这份暂停态确实写成了」：先写 written=true/path，
        # 失败时返回的 rec 仍保持 written=false（不把失败伪装成成功）。
        payload = {**rec, "path": str(path), "written": True,
                   "extra": extra or {}}
        _adj(path, payload, ensure_ascii=False, indent=2)
        back = json.loads(path.read_text(encoding="utf-8"))
        if (back.get("stage") != rec["stage"]
                or back.get("revision") != rec["revision"]):
            raise RuntimeError("PAUSED 读回不一致")
        rec.update({"path": str(path), "written": True})
    except Exception as _e:                       # noqa: BLE001
        rec["error"] = "%s: %s" % (type(_e).__name__, _e)
        try:
            if log is not None:
                log.warn("[CHECKPOINT] PAUSED 写入失败（不打断超时结论）: %s" % _e)
        except Exception:
            pass
    return rec


def read_paused(chapter_dir) -> dict | None:
    """读暂停态；**缺失 → None**，存在但非法 → `CheckpointError`（fail-closed）.

    缺失与损坏必须分开：缺失是「没有暂停」，损坏是「有暂停但读不懂」——
    后者不能悄悄当成没有，否则恢复会从错误起点继续。
    """
    path = paused_path(chapter_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as _e:                       # noqa: BLE001
        raise CheckpointError("PAUSED 检查点不可解析: %s: %s"
                              % (type(_e).__name__, _e))
    if not isinstance(data, dict):
        raise CheckpointError("PAUSED 检查点不是对象")
    if data.get("paused") is not True:
        raise CheckpointError("PAUSED 检查点缺少 paused=true")
    if data.get("written") is not True:
        raise CheckpointError("PAUSED 检查点未标记 written=true")
    if not str(data.get("path") or ""):
        raise CheckpointError("PAUSED 检查点缺少 path")
    stage = str(data.get("stage") or "")
    cp_stage = str(data.get("checkpoint_stage") or "")
    if stage not in PAUSED_STAGES:
        raise CheckpointError("PAUSED 检查点 stage 非法: %r" % (stage,))
    if cp_stage not in STAGES:
        raise CheckpointError("PAUSED 检查点 checkpoint_stage 非法: %r" % (cp_stage,))
    if _PAUSED_BY_CP.get(cp_stage) != stage:
        raise CheckpointError("PAUSED 检查点 stage 与 checkpoint_stage 不一致")
    if str(data.get("resume_from") or "") != RESUME_FROM_BY_CP[cp_stage]:
        raise CheckpointError("PAUSED 检查点 resume_from 非法: %r"
                              % (data.get("resume_from"),))
    if not str(data.get("written_at") or ""):
        raise CheckpointError("PAUSED 检查点缺少 written_at")
    if not isinstance(data.get("revision"), str) or not data["revision"]:
        raise CheckpointError("PAUSED 检查点缺少 revision")
    return data


__all__ = ["CP1_PLAN_VERIFIED", "CP2_CANDIDATE_GENERATED", "CP3_INITIAL_AUDIT_DONE",
           "CP4_REPAIR_ROUND_K_DONE", "PAUSED_AT_CP1_PLAN_VERIFIED",
           "PAUSED_AT_CP2_CANDIDATE_GENERATED", "PAUSED_AT_CP3_INITIAL_AUDIT_DONE",
           "PAUSED_AT_CP4_REPAIR_ROUND_K_DONE", "PAUSED_STAGES", "PAUSED_NAME",
           "RESUME_FROM_BY_CP", "CheckpointError", "INDEX_NAME", "STAGES",
           "checkpoint_path", "latest", "paused_path", "paused_stage_for", "read",
           "read_paused", "write", "write_paused"]
