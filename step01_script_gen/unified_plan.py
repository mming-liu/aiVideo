# -*- coding: utf-8 -*-
"""共同规划入口（S27/S29，2026-11-25）.

`generate_unified_outline_result()` 是新的生产入口：它把两种计划来源收进**同一份
结果合同**，并让统一验收、内容核验、缓存身份和下游消费只认这一份结果。

两种来源：

- `lightweight`（统一模式默认）：紧凑事件/节拍 + 显式声音归属 + 声画承载；
  生成后接原版的 `VerificationController`，核验通过才把计划块标为 `verified`。
- `legacy`：原版完整规划（因果、人物动机、情绪弧线、资产需求、核验记录）。

本模块**不复制**两套生成器：来源仍由 `lightweight_plan` / `outline` 产出；这里只做
共同合同、核验接线、身份检查和结果归一。模型决定内容与声画承载；编号、引用、
声音单位与时间读数仍由既有投影/估时入口派生。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

UNIFIED_CONTRACT_VERSION = "unified_plan.v1"
UNIFIED_SOURCE_ENV = "STEP01_PLAN_SOURCE"
UNIFIED_CACHE_NAME = "chapter_outline.unified.cache.json"
SOURCES = ("lightweight", "legacy")


def plan_source_setting(explicit: str | None = None) -> str:
    """本次统一入口的计划来源；未知值在发送前失败."""
    value = str(explicit if explicit is not None else
                os.getenv(UNIFIED_SOURCE_ENV) or "lightweight").strip().lower()
    if value not in SOURCES:
        raise ValueError("%s=%r（合法：%s）" % (UNIFIED_SOURCE_ENV, value, list(SOURCES)))
    return value


def unified_cache_path(cache_path) -> Path | None:
    """统一缓存单独成文件：旧 legacy/lightweight 读取方永远不会读到它."""
    return None if cache_path is None else Path(cache_path).with_name(UNIFIED_CACHE_NAME)


def _unified_key(novel_text: str, source: str, model) -> str:
    from _text_gen.config import TextGenCfg
    from shared.config import duration_band, voice_allocation_limits
    from shared.content_verification import DEFAULT_RULE_VERSION
    from step01_script_gen.lightweight_plan import CONTRACT_VERSION
    from step01_script_gen.outline import VERIFY_RULE_VERSION
    from step01_script_gen.plan_coherence import VERSION as COHERENCE_VERSION
    payload = {
        "contract": UNIFIED_CONTRACT_VERSION, "source": source,
        "novel_sha256": hashlib.sha256(str(novel_text or "").encode("utf-8")).hexdigest(),
        "provider": TextGenCfg.PROVIDER,
        "model": model or TextGenCfg.default_text_model(),
        "duration_band": duration_band(),
        "voice_limits": voice_allocation_limits(),
        "lightweight_contract": CONTRACT_VERSION,
        "verify_rule_version": VERIFY_RULE_VERSION,
        "verification_default_rule": DEFAULT_RULE_VERSION,
        "coherence_version": COHERENCE_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def _read_cache(path, key: str, novel_text: str):
    from step01_script_gen.outline import plan_acceptance_problems
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("source_hash") != key:
        return None
    plan = data.get("outline")
    if not isinstance(plan, dict):
        return None
    block = plan.get("lightweight_plan")
    if not isinstance(block, dict) or block.get("content_verification") != "verified":
        return None
    if not isinstance(plan.get("plan_verification"), dict):
        return None
    if plan_acceptance_problems(plan, novel_text):
        return None
    return plan


def _write_cache(path, key: str, plan: dict) -> str:
    from shared.json_io import atomic_dump_json
    try:
        atomic_dump_json(Path(path), {"source_hash": key,
                                      "contract": UNIFIED_CONTRACT_VERSION,
                                      "outline": plan}, ensure_ascii=False, indent=1)
        return ""
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, exc)


def _attach_verification(plan, novel_text, *, model=None, deadline_monotonic=None,
                         verify=None, max_verifications=None):
    """轻量计划 → 原版 `VerificationController` → 记录 + 统一验收.

    内容变化后旧记录一律不继承：调用方只在没有 `verified` 身份时进来；成功后把
    `lightweight_plan.content_verification` 从 `not_run` 改为 `verified`。
    """
    from shared.content_verification import (VerificationController, plan_digest_of,
                                             record_protocol_problems)
    from shared.presentation_contract import view_from_plan
    from step01_script_gen import content_verify as cv
    from step01_script_gen.outline import VERIFY_RULE_VERSION, plan_acceptance_problems
    import copy

    view, view_bad = view_from_plan(copy.deepcopy(plan))
    if view_bad or view is None:
        return None, list(view_bad or []), None
    novel_id = cv.novel_identity(novel_text)
    digest = plan_digest_of(plan)

    def _verify(view_obj, plan_digest):
        if verify is not None:
            return verify(view_obj, plan_digest)
        return cv.verify_view_with_service(
            view_obj, plan_digest, model=model, deadline_monotonic=deadline_monotonic,
            novel_text=novel_text, novel_digest=novel_id["digest"],
            novel_version=novel_id["version"], source_references=True)

    def _validate(record, view_obj, plan_digest):
        return record_protocol_problems(record, view_obj, plan_digest=plan_digest,
                                        novel_text=novel_text,
                                        novel_digest=novel_id["digest"])

    kw = ({"max_verifications": int(max_verifications)}
          if max_verifications is not None else {})
    controller = VerificationController(
        verify=_verify, require_coherence=True, validate_record=_validate,
        deadline_at=deadline_monotonic, rule_version=VERIFY_RULE_VERSION,
        stability_confirmation=True, novel_digest=novel_id["digest"], **kw)
    record = controller.record_for(view, digest)
    if not isinstance(record, dict) or str(record.get("status") or "") != "ok":
        problems = list((record or {}).get("problems") or [])
        if not problems:
            problems = [{"code": str((record or {}).get("code") or "verification_failed"),
                         "path": "plan_verification",
                         "reason": str((record or {}).get("error") or "内容核验未通过")}]
        return None, problems, controller.as_dict()

    candidate = copy.deepcopy(plan)
    candidate["plan_verification"] = record
    block = candidate.get("lightweight_plan")
    if not isinstance(block, dict):
        return None, [{"code": "unified_identity_missing", "path": "lightweight_plan",
                       "reason": "统一入口要求轻量计划身份块"}], controller.as_dict()
    block["content_verification"] = "verified"
    block["verification_rule_version"] = VERIFY_RULE_VERSION
    problems = list(plan_acceptance_problems(candidate, novel_text) or [])
    return (candidate if not problems else None), problems, controller.as_dict()


def generate_unified_outline_result(
    novel_text: str, model=None, cache_path=None, *, source: str | None = None,
    deadline_monotonic=None, run_deadline_at=None, verify=None,
    max_verifications: int | None = None,
) -> dict:
    """共同规划入口；返回沿用 `generate_outline_result` 的结构化结果.

    `source` 缺省读 `STEP01_PLAN_SOURCE`（默认 lightweight）。`legacy` 来源直接复用
    原版入口（它已含核验与修订）；`lightweight` 来源生成后必须经
    `VerificationController`，核验记录与 `verified` 身份都成立才 `executable=True`。
    """
    source = plan_source_setting(source)
    deadlines = [float(x) for x in (deadline_monotonic, run_deadline_at)
                 if x is not None]
    deadline = min(deadlines) if deadlines else None
    if source == "legacy":
        from step01_script_gen.outline import generate_outline_result
        result = generate_outline_result(
            novel_text, model=model, cache_path=cache_path,
            deadline_monotonic=deadline, run_deadline_at=deadline)
        result["plan_source"] = "legacy"
        result["unified_contract"] = UNIFIED_CONTRACT_VERSION
        return result

    from step01_script_gen.lightweight_plan import (
        generate_lightweight_outline_result)
    from step01_script_gen.outline import _fail, _outline_result_skeleton
    path = unified_cache_path(cache_path)
    key = _unified_key(novel_text, source, model)
    if path is not None:
        hit = _read_cache(path, key, novel_text)
        if hit is not None:
            result = _outline_result_skeleton("cache")
            result.update(plan=hit, executable=True, failure=None, plan_source="lightweight",
                          unified_contract=UNIFIED_CONTRACT_VERSION)
            result["cache"]["hit"] = True
            result["cache"]["unified_hit"] = True
            result["cache"]["key"] = key
            result["acceptance"] = {"problems": [], "codes": [], "residual_class": "none",
                                    "verdict": "ok"}
            return result

    result = generate_lightweight_outline_result(
        novel_text, model=model, cache_path=cache_path,
        deadline_monotonic=deadline, run_deadline_at=deadline)
    result["plan_source"] = "lightweight"
    result["unified_contract"] = UNIFIED_CONTRACT_VERSION
    result["cache"]["unified_key"] = key
    if not result.get("executable") or not isinstance(result.get("plan"), dict):
        return result

    plan = result["plan"]
    if not isinstance(plan.get("plan_verification"), dict) or not plan.get("plan_verification"):
        verified, problems, meter = _attach_verification(
            plan, novel_text, model=model, deadline_monotonic=deadline,
            verify=verify, max_verifications=max_verifications)
        result["verification"] = meter
        if verified is None:
            result["plan"] = plan
            return _fail(result, "verification_failed",
                         "统一入口的内容核验未通过：%s"
                         % "；".join(str((p or {}).get("code") or "") for p in problems[:4]),
                         problems=problems)
        plan = verified
        result["plan"] = plan
        result["acceptance"] = {"problems": [], "codes": [], "residual_class": "none",
                                "verdict": "ok"}
        result["executable"] = True
        result["failure"] = None
        result["revision"]["stop_reason"] = "ok"

    # 核验后必须再跑一次完整验收（记录身份/正文/预算都可能与核验前不同）。
    from step01_script_gen.outline import plan_acceptance_problems
    problems = list(plan_acceptance_problems(plan, novel_text) or [])
    if problems:
        result["acceptance"] = {"problems": problems,
                                "codes": sorted({str(p.get("code") or "") for p in problems}),
                                "residual_class": "content", "verdict": "blocked"}
        return _fail(result, "unified_acceptance_failed",
                     "统一验收未通过：%s"
                     % "；".join(str(p.get("code") or "") for p in problems[:4]),
                     problems=problems)
    _block = plan.get("lightweight_plan") if isinstance(plan, dict) else None
    if (path is not None and isinstance(_block, dict)
            and _block.get("content_verification") == "verified"
            and isinstance(plan.get("plan_verification"), dict)
            and plan.get("plan_verification")):
        error = _write_cache(path, key, plan)
        result["cache"]["unified_written"] = not error
        result["cache"]["unified_write_error"] = error or None
    return result


__all__ = ["UNIFIED_CONTRACT_VERSION", "UNIFIED_SOURCE_ENV", "UNIFIED_CACHE_NAME",
           "SOURCES", "plan_source_setting", "unified_cache_path",
           "generate_unified_outline_result"]
