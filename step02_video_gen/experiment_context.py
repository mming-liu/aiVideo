# -*- coding: utf-8 -*-
"""step02 报告 / 实验 manifest 的可追踪上下文字段（总计划阶段 7 / 工作包 E）.

目的（非 step01 吸收计划 §7.1）：让**静态 prompt 检查**、**真实视频 QC** 与
**人工 A/B 评分**能关联到同一次实验，而不改动 step01 的质量门。

## 契约（§7.2 / §7.4）

  - 这些字段**只**进入 step02 报告与实验 manifest，**绝不**回写
    `script_result.json`、**绝不**改变 script revision；
  - 没有 `experiment_id` 时，生产报告保持**兼容**（字段为可选增量，老报告读取
    不报错）；
  - 人工评分缺失 **不**判 BLOCK（主观项不能变成生产阻断条件）；
  - 上游 semantic gate 阻塞时只能报 `upstream_blocked`——**本地 prompt 检查的
    PASS 不得覆盖它**；
  - 额度口径：分别统计 `api_generated_images` 与 `local_derived_images`，
    禁止再用「输出 PNG 总数」估算费用（目录 12 已确认 `ref_sheet_*.png` 是本地
    PIL 拼接，不消耗额度）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path


def reference_policy_version() -> str:
    """r2v 参考图策略的版本指纹（策略文案一变，指纹就变）."""
    try:
        from step02_video_gen.prompt_builder import (
            _MULTI_CHAR_ISOLATION, _R2V_KIND_INHERIT_EXCLUDE, r2v_policy_mode)
    except Exception:
        return ""
    # v6.111：模式也要进指纹——否则 A/B 两组（plain / inherit_exclude）的报告
    # 指纹相同，无法从报告反推「这条 prompt 是哪一组」。
    blob = json.dumps(
        {"table": {k: list(v) for k, v in sorted(_R2V_KIND_INHERIT_EXCLUDE.items())},
         "mode": r2v_policy_mode(),
         "isolation": _MULTI_CHAR_ISOLATION},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def upstream_status(chapter_dir_path: Path, script: dict) -> dict:
    """上游 step01 的准入状态（revision / blocking 数 / manifest 状态）."""
    out = {"upstream_gate_status": "upstream_blocked",
           "upstream_audit_revision": "",
           "upstream_blocking_count": None}
    try:
        # v6.111（审核 P2-6）：**两道门都要过**才算 eligible。只验 manifest 时
        # 「三份产物摘要一致」成立但 quality/audit 未必 PASS，会把不 eligible 的
        # 上游标成 eligible。与真实生成走同一对检查（`require_step01_upstream`）。
        from shared.quality_gate import require_step01_upstream
        manifest = require_step01_upstream(
            Path(chapter_dir_path), script, stage="step01")
        out["upstream_gate_status"] = "eligible"
        out["upstream_audit_revision"] = str(manifest.get("script_revision") or "")
        out["upstream_blocking_count"] = int(manifest.get("blocking_count") or 0)
        return out
    except Exception:
        pass
    # gate 未通过时仍尽量给出审计 revision 与 blocking 数（诊断用）
    try:
        audit = json.loads(
            (Path(chapter_dir_path) / "step01_audit.json").read_text(encoding="utf-8"))
        out["upstream_audit_revision"] = str(audit.get("input_revision") or "")
        out["upstream_blocking_count"] = len([
            i for i in (audit.get("issues") or [])
            if str(i.get("severity") or "") in ("hard", "visual_risk", "blocking")])
    except Exception:
        pass
    return out


def _shot_prompt_digest(shot: dict) -> str:
    """单镜**最终提交 prompt** 的摘要（不是 step01 的聚合 prompt）."""
    try:
        from shared.model_caps import active_video_is_seedance
        from step02_video_gen.prompt_builder import _build_seedance_concise_prompt
        if active_video_is_seedance():
            text = _build_seedance_concise_prompt(shot)
        else:
            text = str(shot.get("prompt") or "")
    except Exception:
        text = str(shot.get("prompt") or "")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def reference_manifest_for(shot: dict, characters: list) -> list:
    """该镜实际会提交的 r2v 参考图（非 r2v 模式返回空表）.

    只读：`_collect_r2v_references` 读注册表与资产库，不改任何文件。
    """
    if os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower() != "r2v":
        return []
    try:
        from step02_video_gen.prompt_builder import (
            _classify_reference_kind, _collect_r2v_references,
            _load_character_registry)
        registry = _load_character_registry()
        refs = _collect_r2v_references(shot, characters=characters or [],
                                       registry=registry)
        out = []
        for i, p in enumerate(refs, start=1):
            out.append({
                "index": i,
                "path": str(p),
                "name": Path(str(p)).name,
                "kind": _classify_reference_kind(str(p), shot, registry,
                                                 characters or []),
            })
        return out
    except Exception:
        return []


def build_step02_context(script: dict, *, chapter_dir_path: Path | None = None,
                         experiment_id: str | None = None,
                         prompt_variant: str | None = None,
                         manual_score_status: str | None = None,
                         api_generated_images: int | None = None,
                         local_derived_images: int | None = None,
                         shots: list | None = None) -> dict:
    """构造 step02 报告 / 实验 manifest 的上下文字段.

    **只读**：内部对 script 做深拷贝，绝不回写、绝不改 revision。
    """
    work = copy.deepcopy(script)
    characters = list(work.get("characters") or [])
    units = []
    if isinstance(work.get("hook"), dict):
        units.append(work["hook"])
    units.extend([s for s in (work.get("shots") or []) if isinstance(s, dict)])
    if shots:
        wanted = {str(x) for x in shots}
        units = [u for u in units if str(u.get("index")) in wanted]

    chapter = Path(chapter_dir_path) if chapter_dir_path else None
    ctx = {
        "experiment_id": experiment_id,
        "prompt_variant": prompt_variant,
        "video_provider": os.getenv("VIDEO_PROVIDER", ""),
        "video_model": _active_video_model(),
        "reference_mode": os.getenv("VIDEO_REFERENCE_MODE", ""),
        "reference_policy_version": reference_policy_version(),
        # 主观评分：缺失时为 None——**不得**据此判 BLOCK（§7.4）
        "manual_score_status": manual_score_status or None,
        "prompt_digests": {str(u.get("index")): _shot_prompt_digest(u) for u in units},
        "reference_manifest": {str(u.get("index")): reference_manifest_for(u, characters)
                               for u in units},
        # 额度口径分开记（§2.3）：本地 PIL 派生不计入 API 张数
        "api_generated_images": (None if api_generated_images is None
                                 else int(api_generated_images)),
        "local_derived_images": (None if local_derived_images is None
                                 else int(local_derived_images)),
    }
    if chapter is not None:
        ctx.update(upstream_status(chapter, work))
    else:
        ctx.update({"upstream_gate_status": "upstream_blocked",
                    "upstream_audit_revision": "",
                    "upstream_blocking_count": None})
    return ctx


def _active_video_model() -> str:
    try:
        from shared.model_caps import active_video_model
        return str(active_video_model() or "")
    except Exception:
        return ""


def apply_upstream_override(payload: dict) -> dict:
    """上游阻塞时把报告的结论**降级**为 `upstream_blocked`.

    本地 prompt 检查的 PASS 不能覆盖上游 gate（§7.4 最后一条）。只在已声明
    `upstream_gate_status` 且不是 eligible 时生效——没有该字段的老报告不受影响。
    """
    if str(payload.get("upstream_gate_status") or "") == "eligible":
        return payload
    if "upstream_gate_status" not in payload:
        return payload
    payload["local_prompt_status"] = payload.get("status")
    payload["status"] = "upstream_blocked"
    # 上游阻塞不是 step02 的本地阻断条件：不改 blocking 语义（VIDEO_QC 软失败
    # 策略保持不变），只把结论标成「不可据此生成」。
    return payload


__all__ = [
    "apply_upstream_override",
    "build_step02_context",
    "reference_manifest_for",
    "reference_policy_version",
    "upstream_status",
]
