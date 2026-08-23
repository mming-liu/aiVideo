"""HARD 违例 → regen 触发器（v6.42 PoC）.

apply_constraints 跑完后，如果有 HARD severity 未解决的违例（如 axis_jump /
runtime_extend），把违例信息打成"修复反馈 prompt"，调用 LLM 局部 regen。

设计原则（v6.42 PoC）：
  - opt-in: REGEN_AUTO_TRIGGER=1 才启用
  - 只读 violations_remaining 列表，不修改 shot
  - 输出 regen prompt（同时打 INFO 日志，便于人审）
  - 调用 LLM 同 TextGenerationRequest，复用 provider 切换
  - LLM 失败不抛（与 self_review 一致）

完整接入 step01 的 regen loop 是后续工作（需要把 regen 出来的 shot 合并回
原 script + 触发 _validate_script 复检）。本 PoC 只生成 regen prompt 并记录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .types import RepairReport, Severity, Violation


def _format_violations_for_regen(violations: List[Violation]) -> str:
    """把违例列表格式化为 LLM 友好的 regen 反馈 prompt."""
    lines: List[str] = []
    lines.append("# ★ 硬约束未通过（v6.42 触发 regen）")
    lines.append("")
    lines.append("以下镜头的硬约束未通过修复兜底，请重新生成这些镜头：")
    lines.append("")
    # 按 shot_index 分组
    by_shot: dict = {}
    for v in violations:
        idx = v.shot_index if v.shot_index is not None else -1
        by_shot.setdefault(idx, []).append(v)
    for idx in sorted(by_shot.keys()):
        v_list = by_shot[idx]
        if idx < 0:
            label = "(script-level)"
        else:
            label = f"shot {idx}"
        lines.append(f"## {label}")
        for v in v_list:
            lines.append(f"  - [{v.constraint_name}] {v.message}")
        lines.append("")
    lines.append("修复要求：")
    lines.append("  1. 保持原分镜的剧情/动作不变")
    lines.append("  2. 修正上述违例指向的问题")
    lines.append("  3. 修正后输出整个 script（不只是违例镜头）")
    return "\n".join(lines)


def build_regen_prompt(violations: List[Violation]) -> str:
    """构造 regen 反馈 prompt. 仅 hard_unresolved 的会被传进来.

    Args:
        violations: HARD severity 的未解决违例

    Returns:
        LLM 友好的 prompt 字符串
    """
    if not violations:
        return ""
    return _format_violations_for_regen(violations)


def trigger_regen_if_needed(
    report: RepairReport,
    *,
    auto_call_llm: Optional[bool] = None,
) -> Optional[str]:
    """根据 report 决定是否触发 regen. 返回 LLM 输出或 None.

    Args:
        report: apply_constraints 返回的 RepairReport
        auto_call_llm: True=调 LLM, False=只打日志不调, None=读 env

    Returns:
        LLM 输出（auto_call_llm=True 时）或 None

    Env:
        REGEN_AUTO_TRIGGER=1: 启用 LLM 调用
    """
    hard_unresolved = report.hard_unresolved if hasattr(report, "hard_unresolved") else []
    if not hard_unresolved:
        return None

    prompt = build_regen_prompt(hard_unresolved)
    if not prompt:
        return None

    # 日志：报告触发信息
    try:
        from shared.tee_logger import get_logger
        log = get_logger()
        log.warn(
            f"[REGEN] 触发局部 regen: {len(hard_unresolved)} 条 HARD 违例"
        )
        for v in hard_unresolved[:5]:
            log.warn(
                f"  - {v.constraint_name}@shot{v.shot_index}: {v.message}"
            )
        if len(hard_unresolved) > 5:
            log.warn(f"  ... 还有 {len(hard_unresolved) - 5} 条")
        log.info(f"[REGEN] 反馈 prompt（前 500 字）:\n{prompt[:500]}")
    except Exception:
        pass

    if auto_call_llm is None:
        auto_call_llm = os.getenv("REGEN_AUTO_TRIGGER", "0").strip() == "1"
    if not auto_call_llm:
        return None

    # 调用 LLM
    try:
        from _text_gen import create_service, TextGenerationRequest
        service = create_service()
        req = TextGenerationRequest(
            system_prompt=(
                "你是短视频分镜脚本的修复员。用户提供了一段分镜脚本和违例清单，"
                "请重新生成完整脚本（保持剧情/动作/镜头数），只修正违例指向的问题。"
                "输出必须是合法 JSON：{\"shots\": [...], \"hook\": {...}, \"characters\": [...]}"
            ),
            user_prompt=prompt,
            max_tokens=8000,
            temperature=0.1,
            top_p=0.3,
        )
        result = service.generate(req)
        if result and result.success and result.text:
            return result.text
    except Exception as e:
        try:
            from shared.tee_logger import get_logger
            get_logger().warn(f"[REGEN] LLM 调用失败: {e}")
        except Exception:
            pass
    return None


# ============================================================================
# v6.42: 闭环 - 解析 regen 输出 + merge 回原 script
# ============================================================================

import json
import re as _re


def _safe_parse_regen_json(text: str) -> Optional[dict]:
    """解析 regen LLM 输出为 script dict. 失败返回 None.

    复用 parse_response 的兜底思路：先 safe_parse，再退到 {…} 区间.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    # 先尝试 safe_parse_json_object
    try:
        from shared.utils.json_repair import safe_parse_json_object
        result = safe_parse_json_object(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # 兜底：找 {…} 区间 + 修中文标点
    try:
        s = text.find("{")
        e = text.rfind("}")
        if s < 0 or e < 0 or e <= s:
            return None
        candidate = text[s:e + 1]
        # 修复常见错误
        candidate = _re.sub(r",\s*}", "}", candidate)
        candidate = _re.sub(r",\s*]", "]", candidate)
        return json.loads(candidate)
    except Exception:
        return None


def merge_regen_into_script(
    original_script: dict,
    regen_output: dict,
    *,
    target_shot_indices: Optional[List[int]] = None,
) -> dict:
    """把 regen 生成的 shot 合并回原 script.

    Args:
        original_script: 完整原 script
        regen_output: LLM regen 出来的 dict（含 shots/hook/characters）
        target_shot_indices: 仅合并这些 index 的 shot；None=合并所有

    Returns:
        修改后的 script dict（原 dict 也被 in-place 修改；为方便链式也返回）

    合并规则：
      - 按 shot.index 匹配
      - 若 regen 中无对应 index → 保留原 shot
      - 保留原 script 顶层字段（meta/render_style 等），只覆盖 shots/hook/characters
      - 顶层 _regen_applied 标记 = True
    """
    if not isinstance(regen_output, dict):
        return original_script
    if not isinstance(original_script, dict):
        return original_script

    new_shots_data = regen_output.get("shots")
    actually_merged: list = []
    if isinstance(new_shots_data, list) and new_shots_data:
        # 建索引：regen_shots[i].index → shot dict
        regen_by_index: dict = {}
        for s in new_shots_data:
            if not isinstance(s, dict):
                continue
            idx = s.get("index")
            if idx is not None:
                regen_by_index[idx] = s

        # 合并 shots
        original_shots = original_script.get("shots", [])
        if not isinstance(original_shots, list):
            original_shots = []

        for i, orig_shot in enumerate(original_shots):
            if not isinstance(orig_shot, dict):
                continue
            idx = orig_shot.get("index", i)
            if target_shot_indices is not None and idx not in target_shot_indices:
                continue
            if idx in regen_by_index:
                # in-place 替换：保留 orig 的 key（避免 regen 缺字段）
                new_shot = regen_by_index[idx]
                # 先备份原 dict 的所有 key
                preserved = dict(orig_shot)
                preserved.update(new_shot)
                original_shots[i] = preserved
                actually_merged.append(idx)

    # 合并 hook（如果有）
    if "hook" in regen_output and regen_output["hook"] is not None:
        original_script["hook"] = regen_output["hook"]

    # 合并 characters（如果有，按 ident 去重）
    new_chars = regen_output.get("characters")
    if isinstance(new_chars, list) and new_chars:
        existing_by_ident = {
            c.get("ident"): c for c in original_script.get("characters", [])
            if isinstance(c, dict) and c.get("ident")
        }
        for c in new_chars:
            if not isinstance(c, dict):
                continue
            ident = c.get("ident")
            if ident and ident in existing_by_ident:
                existing_by_ident[ident].update(c)
            else:
                original_script.setdefault("characters", []).append(c)

    # 标记
    original_script["_regen_applied"] = True
    original_script["_regen_shot_indices"] = actually_merged

    return original_script


def trigger_regen_and_merge(
    script: dict,
    report,
    *,
    auto_call_llm: Optional[bool] = None,
) -> dict:
    """regen 一体化：调 LLM → 解析 → merge → re-fix 一次.

    Args:
        script: 原 script
        report: apply_constraints 返回的 RepairReport
        auto_call_llm: True=调 LLM, False=只生 prompt, None=读 env

    Returns:
        修改后的 script（如 LLM 失败或解析失败，返回原 script）
    """
    hard_unresolved = report.hard_unresolved if hasattr(report, "hard_unresolved") else []
    if not hard_unresolved:
        return script

    # 收集需要 regen 的 shot indices
    target_indices = sorted({
        v.shot_index for v in hard_unresolved
        if v.shot_index is not None and v.shot_index >= 0
    })

    # 生成 prompt
    prompt = build_regen_prompt(hard_unresolved)
    if not prompt:
        return script

    if auto_call_llm is None:
        auto_call_llm = os.getenv("REGEN_AUTO_TRIGGER", "0").strip() == "1"
    if not auto_call_llm:
        return script

    # 调 LLM
    llm_text = trigger_regen_if_needed(report, auto_call_llm=True)
    if not llm_text:
        return script

    # 解析
    regen_output = _safe_parse_regen_json(llm_text)
    if regen_output is None:
        try:
            from shared.tee_logger import get_logger
            get_logger().warn("[REGEN] LLM 输出 JSON 解析失败，保留原版")
        except Exception:
            pass
        return script

    # merge
    try:
        from shared.tee_logger import get_logger
        get_logger().info(
            f"[REGEN] merge 回原 script: target_shots={target_indices}, "
            f"regen_shots={len(regen_output.get('shots', []))}"
        )
    except Exception:
        pass

    merged = merge_regen_into_script(
        script, regen_output, target_shot_indices=target_indices,
    )
    return merged

