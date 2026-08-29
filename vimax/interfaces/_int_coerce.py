"""LLM JSON 输出的 int 字段兜底

Pydantic 2.13 的 int 字段默认拒绝：
  - 带小数部分的 float（10.5 / 9.9）
  - 非空字符串（"10" / "10.0"）
但 LLM 经常把 idx/index 输出成 10.0 / 10.5 / "10"，落到这些字段就崩。

本模块提供 schema 边界的兜底转换：
  - int / 整型 float / 整型字符串 → 正常转 int
  - 带小数 float → 截断到 int（idx 是离散编号，10.5 必是幻觉）
  - None / bool → 原样
  - 真垃圾（"abc"）→ 原样抛回 Pydantic 报清晰错误

不写"模型校准"逻辑：idx/index 是桥接 LLM 离散编号，截断是合理的兜底。
"""
from __future__ import annotations
from typing import Any, List


def coerce_int(v: Any) -> Any:
    """单值 int 兜底。用于 int / Optional[int] 字段的 mode='before' validator。"""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)  # 整型和小数都截断：10.0 → 10，10.5 → 10
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return v
        try:
            f = float(s)
        except ValueError:
            return v  # 真垃圾 → 让 Pydantic 报 ValidationError
        return int(f)
    return v


def coerce_int_list(v: Any) -> Any:
    """List[int] 兜底。逐元素过 coerce_int,非 list 原样返回。"""
    if v is None:
        return v
    if not isinstance(v, list):
        return v
    return [coerce_int(x) for x in v]
