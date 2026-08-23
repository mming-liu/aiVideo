"""约束 DSL 解释器（v6.42 PoC）.

支持的 when 表达式（按 op 分发）:
  - contains_any     字段含 keywords 任一
  - contains_all     字段含 keywords 全部
  - not_contains_any 字段不含 keywords 任一
  - matches          字段匹配正则
  - not_matches      字段不匹配正则
  - field_eq         字段 == value
  - field_ne         字段 != value
  - not              取反（expr 必须是 dict）
  - and              全部满足（exprs 必须是 list）
  - or               任一满足

支持的 then 动作:
  - append            追加 text 到 target 字段末尾
  - rewrite           重写 target 字段为 text
  - set_field         设置 target 字段为 value
  - strip_patterns    从 target 字段剥离命中 patterns 的所有片段

设计原则：
  - 表达式只读不写（不破坏字段）
  - 动作写入字段并返回 RepairRecord
  - 字段值都按 str 处理，非 str 视为空串
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Optional


# ============================================================================
# 表达式求值（无副作用，返回 bool）
# ============================================================================

def _get_str(shot: dict, field: str) -> str:
    """取字段的字符串值，非 str 视为空串."""
    v = shot.get(field)
    if isinstance(v, str):
        return v
    return ""


def _eval_expr(expr: Any, shot: dict) -> bool:
    """求值一个 when 表达式.

    Args:
        expr: 表达式 dict（如 {"op": "contains_any", ...}）或 dict 列表（and/or）
        shot: shot dict

    Returns:
        bool
    """
    if not isinstance(expr, dict):
        return False
    op = expr.get("op")
    if op == "contains_any":
        f = expr.get("field", "")
        kws = expr.get("keywords", [])
        text = _get_str(shot, f)
        return any(k in text for k in kws)
    if op == "contains_all":
        f = expr.get("field", "")
        kws = expr.get("keywords", [])
        text = _get_str(shot, f)
        return all(k in text for k in kws)
    if op == "not_contains_any":
        f = expr.get("field", "")
        kws = expr.get("keywords", [])
        text = _get_str(shot, f)
        return not any(k in text for k in kws)
    if op == "matches":
        f = expr.get("field", "")
        pat = expr.get("pattern", "")
        text = _get_str(shot, f)
        try:
            return bool(re.search(pat, text))
        except re.error:
            return False
    if op == "not_matches":
        f = expr.get("field", "")
        pat = expr.get("pattern", "")
        text = _get_str(shot, f)
        try:
            return not re.search(pat, text)
        except re.error:
            return True
    if op == "field_eq":
        f = expr.get("field", "")
        v = expr.get("value")
        return shot.get(f) == v
    if op == "field_ne":
        f = expr.get("field", "")
        v = expr.get("value")
        return shot.get(f) != v
    if op == "not":
        sub = expr.get("expr", {})
        return not _eval_expr(sub, shot)
    if op == "and":
        exprs = expr.get("exprs", [])
        return all(_eval_expr(e, shot) for e in exprs)
    if op == "or":
        exprs = expr.get("exprs", [])
        return any(_eval_expr(e, shot) for e in exprs)
    # 未知 op：保守返回 False
    return False


# ============================================================================
# 动作执行（有副作用，返回 RepairRecord 或 None）
# ============================================================================

def _exec_action(action: dict, shot: dict, constraint_name: str, shot_index) -> Optional[Any]:
    """执行一个 then 动作.

    Returns:
        RepairRecord（如有修改）或 None（如 target 不存在 / value 不变）
    """
    # 延迟导入避免循环（interpreter 在 dsl/ 子包，types 在父包）
    # 用 importlib 走绝对路径导入，避开 sys.path 缓存
    import importlib.util as _ilu
    _here = os.path.dirname(os.path.abspath(__file__))
    _types_path = os.path.join(os.path.dirname(_here), "types.py")
    if "step01_script_gen.constraints._types_mod" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "step01_script_gen.constraints._types_mod", _types_path
        )
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["step01_script_gen.constraints._types_mod"] = _mod
        _spec.loader.exec_module(_mod)  # type: ignore
    RepairRecord = sys.modules["step01_script_gen.constraints._types_mod"].RepairRecord

    if not isinstance(action, dict):
        return None
    op = action.get("action")
    target = action.get("target", "")
    if op == "append":
        text = action.get("text", "")
        cur = shot.get(target)
        if not isinstance(cur, str):
            return None
        if text in cur:
            return None  # 幂等：已含
        before = cur
        # 智能拼接：剥掉尾部标点再加
        stripped = cur.rstrip("。.，,;；")
        new_val = stripped + text
        shot[target] = new_val
        return RepairRecord(
            constraint_name=constraint_name,
            shot_index=shot_index,
            action="append",
            target=target,
            before=before,
            after=new_val,
        )
    if op == "rewrite":
        text = action.get("text", "")
        cur = shot.get(target)
        if cur == text:
            return None
        before = cur if isinstance(cur, str) else ""
        shot[target] = text
        return RepairRecord(
            constraint_name=constraint_name,
            shot_index=shot_index,
            action="rewrite",
            target=target,
            before=before,
            after=text,
        )
    if op == "set_field":
        value = action.get("value")
        cur = shot.get(target)
        if cur == value:
            return None
        before = cur if isinstance(cur, str) else ""
        shot[target] = value
        return RepairRecord(
            constraint_name=constraint_name,
            shot_index=shot_index,
            action="set_field",
            target=target,
            before=before,
            after=str(value) if value is not None else "",
        )
    if op == "strip_patterns":
        patterns = action.get("patterns", [])
        cur = shot.get(target)
        if not isinstance(cur, str) or not cur:
            return None
        before = cur
        new_val = cur
        for pat in patterns:
            try:
                new_val = re.sub(pat, "", new_val)
            except re.error:
                continue
        # 清理孤立括号和多余标点
        if new_val.count("（") != new_val.count("）"):
            new_val = re.sub(r"[（）]", "", new_val)
        new_val = re.sub(r"[，,。]{2,}", "。", new_val)
        new_val = new_val.strip("。，, ") + ("。" if new_val.strip() else "")
        if new_val == before:
            return None
        shot[target] = new_val
        return RepairRecord(
            constraint_name=constraint_name,
            shot_index=shot_index,
            action="rewrite",
            target=target,
            before=before,
            after=new_val,
        )
    return None
