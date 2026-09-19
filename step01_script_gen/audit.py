"""Same-model independent audit for a generated Step01 script.

The audit gets a fresh context and must return structured, evidence-based
issues.  Its findings are advisory inputs to targeted repair; deterministic
checks remain the final authority.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from _text_gen import TextGenerationRequest, create_service
from step01_script_gen.quality_issues import Issue


AUDIT_SCHEMA = {
    "name": "script_audit",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "severity": {"type": "string", "enum": ["blocking", "warning", "info"]},
                        "shot_id": {"type": "string"},
                        "frame_id": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "field_path": {"type": "string"},
                        "evidence": {"type": "string"},
                        "impact": {"type": "string"},
                        "minimal_fix": {"type": "string"},
                        "stage": {"type": "string", "enum": ["pre", "process", "result"]},
                    },
                    "required": ["code", "severity", "shot_id", "frame_id", "entity_id",
                                 "field_path", "evidence", "impact", "minimal_fix"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["issues"],
        "additionalProperties": False,
    },
}


_AUDIT_SYSTEM = """你是独立分镜审查员。不要证明脚本优秀，只找可验证的问题。
只输出 JSON。最多返回 20 条问题；每条 evidence/impact/minimal_fix 尽量控制在 80 字以内。每个问题必须给出镜头、字段、证据和最小修复建议。程序检查是最终权威。

帧职责边界（必须遵守）：
- frame_id=ff：只审「动作开始前的准备态」。不得要求 ff 已经完成 motion_desc 中的过程动作或 lf_desc 中的结果动作。
- frame_id=kfN：只审该关键帧应呈现的中间过程态。
- frame_id=lf：只审动作结束后的结果态。
- 如果后续动作缺失，field_path 应指向 motion_desc 或对应 kf/lf，不得要求把它补进 ff。
- severity 必须填写 blocking/warning/info；impact 只写影响，不要把 blocking/warning 写进 impact。
- 可选 stage 必须填写 pre/process/result，与 frame_id 职责一致。
"""


def _compact_script(script: dict) -> dict:
    shots = []
    for s in script.get("shots") or []:
        if not isinstance(s, dict):
            continue
        shots.append({
            "index": s.get("index"),
            "duration_sec": s.get("duration_sec"),
            "main_subject": s.get("main_subject"),
            "ff_desc": s.get("ff_desc"),
            "lf_desc": s.get("lf_desc"),
            "motion_desc": s.get("motion_desc"),
            "narration_segment": s.get("narration_segment"),
            "frame_layouts": s.get("frame_layouts"),
            "keyframes": s.get("keyframes"),
        })
    return {"hook": script.get("hook"), "shots": shots,
            "total_duration": script.get("total_duration")}


def build_audit_prompt(novel_text: str, plan: dict | None, script: dict,
                       audit_focus: list[str] | None = None,
                       metadata: dict | None = None) -> str:
    focus_lines = "\n".join(f"- {item}" for item in (audit_focus or [])) or "- 无额外定向项"
    metadata_text = json.dumps(metadata or {}, ensure_ascii=False, indent=2)
    return f"""# 审查元数据
{metadata_text}

# 定向审查项
{focus_lines}

# 原文
{(novel_text or '')[:12000]}

# 故事规划
{json.dumps(plan or {}, ensure_ascii=False)}

# 当前分镜
{json.dumps(_compact_script(script), ensure_ascii=False)}

# 审查规则
重点检查：
1. 观众是否知道人物为什么这样做；
2. 是否省略理解剧情必需的信息；
3. 首帧→动作→关键帧→尾帧是否成立；
4. 哪些镜头重复表达同一信息；
5. 钩子和结尾是否真的来自当前故事。

帧职责：
- ff 只检查动作开始前准备态；
- kf 只检查对应中间过程；
- lf 只检查结果态；
- 后续动作缺失必须指向 motion_desc/对应 kf/lf，禁止要求 ff 提前完成过程或结果动作。

每个问题必须给 shot_id、frame_id、entity_id、field_path、evidence、impact、minimal_fix。
严重级别：blocking=确定影响剧情/可执行性；warning=可能影响；info=优化建议。
"""


def _parse_issues_with_status(text: str) -> tuple[list[dict], str]:
    """Parse audit output and distinguish empty from malformed JSON."""
    raw = (text or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return [], "parse_failed"
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            return [], "parse_failed"
    if isinstance(data, list):
        issues = data
    elif isinstance(data, dict):
        issues = data.get("issues")
    else:
        issues = None
    if not isinstance(issues, list):
        return [], "parse_failed"
    return [x for x in issues if isinstance(x, dict)], "ok"


def _parse_issues(text: str) -> list[dict]:
    """Backward-compatible list-only parser."""
    return _parse_issues_with_status(text)[0]


@dataclass
class AuditResult:
    status: str  # ok | request_failed | parse_failed
    issues: list[Issue] = field(default_factory=list)
    error: str = ""
    raw_text: str = ""


def run_same_model_audit_with_status(
    novel_text: str,
    plan: dict | None,
    script: dict,
    *,
    model: str | None = None,
    timeout_sec: int = 180,
    audit_focus: list[str] | None = None,
    metadata: dict | None = None,
) -> AuditResult:
    """Run audit while preserving failure state for logs and repair acceptance."""
    service = create_service()
    import os as _os
    _audit_max_tokens = int(_os.getenv("AUDIT_MAX_OUTPUT_TOKENS", "16000") or "16000")
    req = TextGenerationRequest(
        system_prompt=_AUDIT_SYSTEM,
        user_prompt=build_audit_prompt(novel_text, plan, script, audit_focus=audit_focus, metadata=metadata),
        max_tokens=_audit_max_tokens,
        temperature=0.1,
        json_schema=AUDIT_SCHEMA,
        model=model,
        timeout_sec=timeout_sec,
    )
    result = service.generate(req)
    if not result.success:
        return AuditResult("request_failed", [], str(getattr(result, "error", "") or ""), str(getattr(result, "text", "") or ""))
    parsed, status = _parse_issues_with_status(result.text)
    if status != "ok":
        return AuditResult(status, [], "audit output is not valid JSON or lacks issues[]", result.text)
    out: list[Issue] = []
    for d in parsed:
        sev_raw = str(d.get("severity") or "warning").strip().lower()
        impact_raw = str(d.get("impact") or "").strip().lower()
        # Model occasionally writes "blocking" into impact while leaving
        # severity=warning.  Treat that as blocking; it is not safe to
        # silently downgrade an explicit blocking claim.
        if impact_raw.startswith("blocking") or sev_raw == "blocking":
            severity = "visual_risk"
        else:
            severity = "advisory"
        _sid = d.get("shot_id")
        shot_id = str(_sid).strip() if _sid is not None and str(_sid).strip() else None
        out.append(Issue(
            code=f"model_audit_{d.get('code') or 'issue'}",
            severity=severity,
            message=f"{d.get('impact') or ''}；建议：{d.get('minimal_fix') or ''}",
            shot_id=shot_id,
            frame_id=str(d.get("frame_id") or ""),
            entity_id=str(d.get("entity_id") or ""),
            field_path=str(d.get("field_path") or ""),
            evidence=str(d.get("evidence") or ""),
            repairable=True,
        ))
    return AuditResult("ok", out)


def run_same_model_audit(novel_text: str, plan: dict | None, script: dict,
                         *, model: str | None = None,
                         timeout_sec: int = 180) -> list[Issue]:
    """Backward-compatible list-returning wrapper."""
    return run_same_model_audit_with_status(
        novel_text, plan, script, model=model, timeout_sec=timeout_sec
    ).issues


__all__ = [
    "AUDIT_SCHEMA", "AuditResult", "build_audit_prompt",
    "run_same_model_audit", "run_same_model_audit_with_status",
]
