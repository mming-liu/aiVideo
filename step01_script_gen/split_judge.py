# -*- coding: utf-8 -*-
"""P3-B：受控 M 判定（父主张是否被全部子主张完整保留）.

只负责提示/解析/代码盖章；调用由注入的 `call` 完成，本模块不建 service、不发送。
"""
from __future__ import annotations

import hashlib
import json

from shared import fact_split as fs

M_SCHEMA = {
    "name": "fact_split_m.v1",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["verdict", "missing", "reason"],
        "properties": {
            "verdict": {"type": "string",
                        "enum": ["supported", "partial", "contradicted", "unknown"]},
            "missing": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
}

M_SYSTEM_PROMPT = ("你是严格的事实拆分完整性判定器。只判断子主张合起来是否完整保留"
                   "父主张的主体、受事、否定、条件、时间、因果与限定语。"
                   "只输出约定的 JSON，不要解释。")


def call_split_m(prompt, *, model=None, deadline_monotonic=None,
                 timeout_sec=None) -> tuple:
    """生产 M 调用：一次 `generate`，返回 `(raw_text, provider_call_id)`；不重试."""
    from _text_gen.config import config
    from _text_gen.models import TextGenerationRequest
    from step01_script_gen.outline import create_service
    _req = TextGenerationRequest(
        system_prompt=M_SYSTEM_PROMPT, user_prompt=prompt,
        max_tokens=config.SPLIT_M_MAX_TOKENS, temperature=0.0,
        json_schema=M_SCHEMA, model=model,
        deadline_monotonic=deadline_monotonic, timeout_sec=timeout_sec)
    _res = create_service().generate(_req)
    if not _res.success:
        raise RuntimeError("M 调用失败：%s" % str(getattr(_res, "error", "") or ""))
    return str(getattr(_res, "text", "") or ""), str(getattr(_res, "call_id", "") or "")


def build_m_prompt(parent_claim, children, source_context=None) -> str:
    _kids = [{"uid": str(c.get("uid") or ""), "claim": str(c.get("claim") or "")}
             for c in (children or []) if isinstance(c, dict)]
    _lines = [
        "# 事实拆分完整性判定（M）",
        "只判断：**全部子主张合起来是否完整保留父主张**。",
        "必须逐项核对主体、受事、否定、条件、时间、因果和限定语；"
        "子主张数量正确、关键词重合或某一条 supported 都不能代替完整保留。",
        "父主张（冻结，不改写）：%s" % str(parent_claim or ""),
        "子主张：",
    ]
    for _k in _kids:
        _lines.append("- %s：%s" % (_k["uid"], _k["claim"]))
    if source_context:
        _lines.append("来源上下文：%s" % json.dumps(source_context, ensure_ascii=False))
    _lines += [
        "",
        "只输出一个 JSON 对象，**只能**有 verdict/missing/reason 三个键：",
        '{"verdict": "supported|partial|contradicted|unknown",'
        ' "missing": "", "reason": ""}',
        "verdict 只能由你根据上述父/子主张判断；不要输出任何 uid、摘要或调用信息。",
    ]
    return chr(10).join(_lines)


def parse_m_response(raw) -> tuple:
    """严格解析 M 响应；返回 `(data|None, problems)`."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception as exc:                                       # noqa: BLE001
            return None, [{"code": "split_m_response_invalid",
                           "reason": "M 响应不是 JSON：%s" % exc}]
    if not isinstance(raw, dict):
        return None, [{"code": "split_m_response_invalid",
                       "reason": "M 响应必须是对象"}]
    _bad = sorted(set(raw) - {"verdict", "missing", "reason"})
    if _bad:
        return None, [{"code": "split_m_unknown_field",
                       "reason": "M 响应有未知字段：%s" % _bad}]
    if str(raw.get("verdict") or "") not in (
            "supported", "partial", "contradicted", "unknown"):
        return None, [{"code": "split_m_verdict_invalid",
                       "reason": "M verdict 非法：%r" % raw.get("verdict")}]
    for _k in ("missing", "reason"):
        if not isinstance(raw.get(_k), str):
            return None, [{"code": "split_m_field_type",
                           "reason": "M 的 %s 必须是字符串" % _k}]
    return {"verdict": str(raw["verdict"]), "missing": raw["missing"],
            "reason": raw["reason"]}, []


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()


def judge_split(parent_claim, children, *, call, call_id="", model=None) -> dict:
    """调用注入的 `call(prompt)`，解析并由代码盖章；不在此处写账本.

    `call` 可以返回原始响应字符串，也可以返回 `(raw, provider_call_id)`；后者且
    `call_id` 为空时，用 provider id 作为账本盖章的 `call_id`。
    """
    _prompt = build_m_prompt(parent_claim, children)
    _raw = call(_prompt)
    _provider_cid = ""
    if isinstance(_raw, tuple) and len(_raw) == 2:
        _raw, _provider_cid = _raw
    _data, _problems = parse_m_response(_raw)
    if _data is None:
        return {"ok": False, "problems": _problems, "judgment": None}
    _judgment = {
        "mode": "model",
        "verdict": _data["verdict"],
        "missing": _data["missing"],
        "reason": _data["reason"],
        "inputs_digest": fs.inputs_digest(parent_claim, children),
        "call_id": str(call_id or _provider_cid or ""),
        "prompt_digest": _digest(_prompt),
        "response_digest": _digest(_raw),
    }
    return {"ok": True, "problems": [], "judgment": _judgment}


def m_judgment_problems(entry) -> list:
    """账本判定的调用证据检查；不把自报 supported 当已执行."""
    _j = (entry or {}).get("judgment") if isinstance(entry, dict) else None
    if not isinstance(_j, dict):
        return [{"code": "split_m_judgment_missing",
                 "reason": "拆分账本缺 M judgment"}]
    _p = []
    if str(_j.get("mode") or "") != "model":
        _p.append({"code": "split_m_mode_invalid",
                   "reason": "M judgment.mode 必须是 model"})
    if not str(_j.get("call_id") or "").strip():
        _p.append({"code": "split_m_call_missing",
                   "reason": "M judgment 缺 call_id"})
    for _k in ("prompt_digest", "response_digest"):
        _v = str(_j.get(_k) or "")
        if len(_v) != 64 or any(ch not in "0123456789abcdef" for ch in _v):
            _p.append({"code": "split_m_digest_invalid",
                       "reason": "M judgment.%s 不是 sha256" % _k})
    if str(_j.get("inputs_digest") or "") != fs.inputs_digest(
            (entry or {}).get("parent_claim"), (entry or {}).get("children")):
        _p.append({"code": "split_m_inputs_mismatch",
                   "reason": "M judgment.inputs_digest 与父/子主张不一致"})
    if str(_j.get("verdict") or "") != "supported":
        _p.append({"code": "split_m_not_supported",
                   "reason": "M 判定不是 supported"})
    return _p


__all__ = ["M_SCHEMA", "M_SYSTEM_PROMPT", "build_m_prompt", "call_split_m",
           "judge_split", "m_judgment_problems", "parse_m_response"]
