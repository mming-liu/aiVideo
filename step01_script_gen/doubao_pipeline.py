"""doubao_pipeline - doubao 专用分镜生成/修复/GAP 重生流水线（v6.60+, 2026-09-05）.

设计原则:
  - 与 deepseek 路径 _regen_over_limit / _repair_shot_warnings / GAP 重生 **接口同构**，
    内部走 doubao 专用 prompt
  - 通过 main.py 条件 import 挂载: TEXT_PROVIDER=agent_plan 时启用，deepseek 零影响
  - 退出 doubao 模式只需 unset TEXT_PROVIDER，代码完全 revert
  - 修复/GAP 重生函数都是 **同接口、同返回类型**（dict），方便 main.py 调用点改 if/else
  - 温度参数：保留 models.py dataclass 默认 0.05（保 deepseek 路径零修改），本模块所有 service.generate() 调用显式传 temperature=0.2 覆盖

导出函数:
  - doubao_regen_over_limit(script, novel_text, bank, objects_bank, max_attempts=None)
  - doubao_repair_shot_warnings(script, novel_text, bank, objects_bank)
  - doubao_gap_regen(script, novel_text, validation_result, bank, objects_bank, ...)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from _text_gen import create_service, TextGenerationRequest
from _text_gen.deepseek_schema import SCRIPT_SCHEMA
from .prompts import DOUBAO_SYSTEM_PROMPT
from .validation import parse_response
from .doubao_repair_prompts import (
    build_doubao_repair_prompt,
    build_doubao_gap_regen_prompt,
    build_doubao_regen_over_limit_prompt,
)
from .prompts import _build_regen_iron_rules
from .gap_analysis import validate_script_gap, generate_gap_report
from shared.config import SHOT_COUNT_MIN, SHOT_COUNT_MAX, GAP_MAX_REGEN_ATTEMPTS
from _text_gen.config import TEXT_PROVIDER_DEFAULT


def _strip_markdown_fences(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw


def _is_doubao_enabled() -> bool:
    """安全检查：TEXT_PROVIDER=agent_plan 时才允许调用。deepseek 路径不会走到这里。"""
    return (os.environ.get("TEXT_PROVIDER") or TEXT_PROVIDER_DEFAULT).strip().lower() == "agent_plan"

def doubao_regen_over_limit(script, novel_text, bank=None, objects_bank=None, max_attempts=None):
    """doubao 专用 _regen_over_limit（与 deepseek 同接口）.

    max_attempts 默认单源 shared.config.GAP_MAX_REGEN_ATTEMPTS（None=取配置值）。

    Returns: 修复后的 script dict；失败回退原 script.
    """
    if not _is_doubao_enabled():
        return script
    if max_attempts is None:
        max_attempts = GAP_MAX_REGEN_ATTEMPTS

    for attempt in range(1, max_attempts + 1):
        prompt = build_doubao_regen_over_limit_prompt(
            novel_text=novel_text,
            bank=bank,
            objects_bank=objects_bank,
            shot_count_min=SHOT_COUNT_MIN,
            shot_count_max=SHOT_COUNT_MAX,
        )
        service = create_service()
        req = TextGenerationRequest(
            system_prompt=DOUBAO_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.2,
            json_schema=SCRIPT_SCHEMA,
        )
        result = service.generate(req)
        if not result.success:
            continue
        try:
            raw = _strip_markdown_fences(result.text)
            new_script = parse_response(raw, novel_text=novel_text)
            return new_script
        except (ValueError, json.JSONDecodeError):
            continue
    return script

def doubao_repair_shot_warnings(script, novel_text, bank=None, objects_bank=None):
    """doubao 专用 _repair_shot_warnings（与 deepseek 同接口）.

    Returns: 修复后的 script dict；失败回退原 script.
    """
    if not _is_doubao_enabled():
        return script

    # 收集现有 warnings（仅作为提示，实际提示词内已含硬规则清单）
    warnings = []
    shots = script.get("shots") or []
    for idx, s in enumerate(shots, 1):
        ms = (s.get("main_subject") or "").strip()
        if any(k in ms for k in ("对白", "质问", "叫道", "对", "说", "答", "问", "喊", "吼", "低声", "怒道", "厉声")):
            narr = (s.get("narration_segment") or "").strip()
            sub = (s.get("subtitle") or "").strip()
            if not narr and not sub:
                warnings.append({"shot_index": idx, "message": "对白场景但 narration_segment/subtitle 双空"})

    prompt = build_doubao_repair_prompt(
        shots=shots,
        warnings=warnings,
        novel_text=novel_text,
        bank=bank,
        objects_bank=objects_bank,
    )
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=DOUBAO_SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=0.2,
        json_schema=SCRIPT_SCHEMA,
    )
    result = service.generate(req)
    if not result.success:
        return script
    try:
        raw = _strip_markdown_fences(result.text)
        new_script = parse_response(raw, novel_text=novel_text)
        return new_script
    except (ValueError, json.JSONDecodeError):
        return script

def doubao_gap_regen(script, novel_text, validation_result, bank=None, objects_bank=None, *, iron_rules="", checklist_feedback=""):
    """doubao 专用 GAP 重生（与 main.py:1551-1634 深度求同构）.

    Returns: (new_script, new_validation) 元组；失败回退 (script, validation_result).
    """
    if not _is_doubao_enabled():
        return script, validation_result

    prompt = build_doubao_gap_regen_prompt(
        validation_result=validation_result,
        novel_text=novel_text,
        bank=bank,
        objects_bank=objects_bank,
        iron_rules=iron_rules or _build_regen_iron_rules(),
        checklist_feedback=checklist_feedback,
    )
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=DOUBAO_SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=0.2,
        json_schema=SCRIPT_SCHEMA,
    )
    result = service.generate(req)
    if not result.success:
        return script, validation_result
    try:
        raw = _strip_markdown_fences(result.text)
        new_script = parse_response(raw, novel_text=novel_text)
    except (ValueError, json.JSONDecodeError):
        return script, validation_result

    new_validation = validate_script_gap(new_script, novel_text)
    return new_script, new_validation


# 显式模块注册入口（main.py 可选调）: 当前实现是模块 import 时即挂载, 无副作用
def register_doubao_pipeline() -> None:
    """main.py 走 agent_plan 时调，作为显式入口点.

    保留此函数是为了未来不需改 main.py 即可添加副作用（如清缓存、打日志等）.
    """
    pass