# -*- coding: utf-8 -*-
"""`shared.vimax_bridge` 拆分出的 ledger_and_errors 部分（2026-11-28 O11）.

自动迁移：符号级依赖闭包已核；父模块 100% re-export 保持
`shared.vimax_bridge.<name>` 调用与 monkeypatch 语义不变。
"""
from __future__ import annotations

# 从父模块整体复制的导入表：迁移代码的自由名全部覆盖；未用到的导入
# 不参与 lint 计数（拆分不改行为，也不引入新的 F401 噪声）。
# ruff: noqa: F401
import asyncio
import fnmatch
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from vimax.interfaces import (
    Camera,
    CharacterInScene,
    ShotBriefDescription,
    ShotDescription,
)
from shared.character_bank import CharacterBank, StyleBank
from shared.paths import (
    shot_work_dir,
    FIRST_FRAME_FILENAME,
    LAST_FRAME_FILENAME,
    KEYFRAME_FILENAME_FMT,
)
import hashlib
from shared.style_prefix import (style_prefix_for_canvas, frame_canvas_ratio, sanitize_prompt,
                                 ensure_prefix, is_empty_scene, add_image_strict_constraints,
                                 preprocess_prompt_for_image)
from shared.style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT, image_lock_prompt
from step01_script_gen.prompt_semantic import (
    THREAT_WORDS_ENLARGED as _THREAT_WORDS_ENLARGED,
    THREAT_VISIBILITY_KEYWORDS as _THREAT_VISIBILITY_KEYWORDS,
    THREAT_CORE_IDENTIFIERS as _THREAT_CORE_IDENTIFIERS,
)
from step01_script_gen.prompt_semantic import WARDROBE_WORDS as _COSTUME_WORDS
from step01_script_gen.prompt_semantic import (
    HAIR_STATE_WORDS as _HAIR_STATE_WORDS,
    HAIR_STATIC_PHRASES as _HAIR_STATIC_PHRASES,
    ANIMAL_FACE_KEYWORDS as _ANIMAL_FACE_KEYWORDS,
    NO_ANIMAL_FEATURES_GUARD as _NO_ANIMAL_FEATURES_GUARD,
    ANIMAL_METAPHOR_FACE_REPLACEMENTS as _ANIMAL_METAPHOR_FACE_REPLACEMENTS,
)
from shared.content_safety import (
    WEAPON_WORDS,
    strip_action_result as _safety_strip_action_result,
)
from shared.split_similarity import (
    KF_FF_SIM_THRESHOLD as _KF_FF_SIM_THRESHOLD,
    KF_FF_ANCHOR_EXEMPT_STAGES as _KF_FF_ANCHOR_EXEMPT_STAGES,
    text_sim_ratio as _text_sim_ratio,
)


__all__ = [
    'FAILURE_INFRA_UNAVAILABLE',
    'FAILURE_MISSING_ASSET',
    'FAILURE_QC_DISPUTE',
    'FAILURE_REQUEST_CONFLICT',
    'FAILURE_STOCHASTIC_MISS',
    '_NON_RETRYABLE_FAILURES',
    '_check_frame_contract_preflight',
    '_classify_generation_failure',
    '_resolve_ledger_qc_verdict',
]


FAILURE_REQUEST_CONFLICT = "request_conflict"

FAILURE_MISSING_ASSET = "missing_asset"

FAILURE_QC_DISPUTE = "qc_dispute"

FAILURE_STOCHASTIC_MISS = "stochastic_miss"

FAILURE_INFRA_UNAVAILABLE = "infra_unavailable"

_NON_RETRYABLE_FAILURES = (
    FAILURE_REQUEST_CONFLICT,
    FAILURE_MISSING_ASSET,
    FAILURE_QC_DISPUTE,
    FAILURE_INFRA_UNAVAILABLE,
)

def _classify_generation_failure(
    exc: BaseException | None = None,
    qc_result=None,
    qc_critical: bool = False,
) -> str:
    """Classify Step02a frame failure for P1-5 stop-loss routing."""
    if exc is not None:
        msg = str(exc or "")
        if isinstance(exc, FileNotFoundError) or any(
            tok in msg for tok in (
                "参考图缺失", "参考图不存在", "portrait_file_missing",
                "读取/编码失败", "参考图在发送前缺失",
            )
        ):
            return FAILURE_MISSING_ASSET
        if isinstance(exc, ValueError) or type(exc).__name__ == "PromptContradictionError" or any(
            tok in msg for tok in (
                "帧契约", "矛盾", "篡改", "契约摘要不一致",
                "参考图数量", "参考图超限", "无效分辨率",
                "ambiguous_character_alias", "frame_contract",
            )
        ):
            return FAILURE_REQUEST_CONFLICT
        if any(tok in msg for tok in (
            "qc_call_failed", "QC 异常", "质检失败",
            "达到尝试上限", "额度耗尽", "[SOFT-SKIP]",
        )):
            return FAILURE_QC_DISPUTE
        return FAILURE_REQUEST_CONFLICT
    if qc_result is not None and not getattr(qc_result, "ok", True):
        return FAILURE_QC_DISPUTE
    if qc_critical:
        return FAILURE_STOCHASTIC_MISS
    return "none"

def _resolve_ledger_qc_verdict(
    frame_path: str | Path,
    shot: dict,
    frame_kind: str,
    characters: list | None,
    *,
    qc_critical: bool,
    qc_last=None,
    kf_index: int | None = None,
) -> tuple[str, str, str]:
    """Derive (defect_class, qc_verdict, detail) from authoritative qc_state()."""
    if qc_last is not None and not getattr(qc_last, "ok", True):
        return FAILURE_QC_DISPUTE, "qc_call_failed", str(getattr(qc_last, "error", ""))
    try:
        from shared.image_qc import build_qc_frame_meta, qc_state
        meta = build_qc_frame_meta(
            shot, frame_kind, characters, kf_index=kf_index, frame_path=frame_path,
        )
        state = qc_state(Path(frame_path), expected_frame=meta)
        status = str(state.get("status") or "")
        verdict_detail = str((state.get("data") or {}).get("verdict_detail") or "")
        if status == "qc_call_failed":
            return FAILURE_QC_DISPUTE, "qc_call_failed", str((state.get("data") or {}).get("error") or "")
        if status == "qc_rejected" or qc_critical:
            return FAILURE_STOCHASTIC_MISS, "qc_rejected", verdict_detail or "critical_reject"
        if status == "qc_passed":
            return "none", "qc_passed", verdict_detail or "clean_pass"
        if status:
            return "none", status, verdict_detail
    except Exception:
        pass
    if qc_critical:
        return FAILURE_STOCHASTIC_MISS, "qc_rejected", "critical_reject"
    return "none", "qc_passed", "clean_pass"

def _check_frame_contract_preflight(
    shot: dict,
    frame_kind: str,
    characters: list | None,
    *,
    kf_index: int | None = None,
) -> dict | None:
    """Block invalid/contradictory frame contracts before dispatching any image POST."""
    try:
        from shared.prompt_facts import project_frame_facts
        proj = project_frame_facts(shot, frame_kind, characters, kf_index=kf_index)
        if proj.get("spec_status") == "invalid":
            return {
                "code": FAILURE_REQUEST_CONFLICT,
                "issues": proj.get("spec_issues") or [],
            }
    except Exception:
        return None
    return None

