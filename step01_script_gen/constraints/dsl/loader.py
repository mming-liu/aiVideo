"""约束 DSL 加载器（v6.42 PoC）.

职责：
  1. 从 YAML 文件读规则
  2. 验证规则 schema（name/severity/when/then 必填）
  3. 动态生成 ConstraintRule 子类（包装解释器）
  4. 注册到 ALL_CONSTRAINTS

用法：
    from step01_script_gen.constraints.dsl.loader import load_rules
    load_rules("step01_script_gen/constraints/rules/")
    # 现在 ALL_CONSTRAINTS 多了 N 条 ConstraintRule

新增规则流程（无需 Python 代码）：
    1. 在 rules/ 下加 .yaml 文件
    2. 调用 load_rules() 或启动时自动调用
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# 尝试 import yaml，未装则给明确错误
try:
    import yaml  # type: ignore
except ImportError as _e:  # pragma: no cover
    yaml = None
    _YAML_IMPORT_ERROR = _e
else:
    _YAML_IMPORT_ERROR = None

from .interpreter import _eval_expr, _exec_action
from ..base import Constraint
from ..types import Severity
from ..engine import ALL_CONSTRAINTS


_RULE_EXT = ".yaml"
_RULE_YML_EXT = ".yml"


def _parse_severity(s: str) -> Severity:
    s = (s or "").strip().lower()
    if s == "hard":
        return Severity.HARD
    return Severity.WARN


def _validate_rule(rule: dict, src: str) -> Optional[str]:
    """校验规则 schema. 返回错误信息或 None."""
    for k in ("name", "when", "then"):
        if k not in rule:
            return f"{src}: missing required field '{k}'"
    if not isinstance(rule["when"], dict):
        return f"{src}: 'when' must be a dict"
    if not isinstance(rule["then"], dict):
        return f"{src}: 'then' must be a dict"
    if "op" not in rule["when"]:
        return f"{src}: 'when.op' required"
    if "action" not in rule["then"]:
        return f"{src}: 'then.action' required"
    return None


def _build_constraint_class(rule: dict) -> type:
    """从 rule dict 动态生成 ConstraintRule 子类."""
    rule_name = rule["name"]
    severity = _parse_severity(rule.get("severity", "warn"))
    fields = tuple(rule.get("fields", ()))
    repair_marker = rule.get("repair_marker")
    skip_hook = bool(rule.get("skip_hook", False))
    when_expr = rule["when"]
    then_action = rule["then"]

    def _apply(self, ctx) -> list:
        repairs: list = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not _eval_expr(when_expr, shot):
                continue
            if self._already_repaired(shot):
                continue
            r = _exec_action(then_action, shot, self.name, idx)
            if r is not None:
                repairs.append(r)
        return repairs

    # 用 type() 直接构造（避开 Constraint 的 __init_subclass__ / abstract 检查顺序问题）
    attrs: dict = {
        "name": rule_name,
        "severity": severity,
        "fields": fields,
        "apply": _apply,
        "__module__": __name__,
        "__qualname__": f"ConstraintRule_{rule_name}",
    }
    if repair_marker is not None:
        attrs["repair_marker"] = repair_marker
    if skip_hook:
        attrs["skip_hook"] = True
    cls = type(f"ConstraintRule_{rule_name}", (Constraint,), attrs)
    return cls


def load_rule_file(path: str) -> int:
    """从单个 YAML 文件加载规则. 返回新增的 constraint 数（0 或 1）.

    Raises:
        RuntimeError: PyYAML 未装 / 规则 schema 错误
    """
    if yaml is None:
        raise RuntimeError(
            f"PyYAML 未安装，无法加载 YAML 规则: {_YAML_IMPORT_ERROR}"
        )
    src = path
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return 0
    # 支持单条规则或多条（list）
    rules = data if isinstance(data, list) else [data]
    added = 0
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        err = _validate_rule(rule, src)
        if err:
            raise RuntimeError(f"规则校验失败: {err}")
        # 已存在同名规则不重复添加
        if any(c.name == rule["name"] for c in ALL_CONSTRAINTS):
            continue
        cls = _build_constraint_class(rule)
        ALL_CONSTRAINTS.append(cls())
        added += 1
    return added


def load_rules(directory: str) -> int:
    """从目录加载所有 .yaml / .yml 规则文件. 返回新增总数.

    Args:
        directory: 包含 .yaml / .yml 的目录路径
    """
    if not os.path.isdir(directory):
        return 0
    total = 0
    for fn in sorted(os.listdir(directory)):
        if not (fn.endswith(_RULE_EXT) or fn.endswith(_RULE_YML_EXT)):
            continue
        path = os.path.join(directory, fn)
        total += load_rule_file(path)
    return total


def auto_load() -> int:
    """自动加载同包 rules/ 目录. 返回新增总数.

    等价于：load_rules(os.path.join(os.path.dirname(__file__), '..', 'rules'))
    """
    here = os.path.dirname(os.path.abspath(__file__))
    rules_dir = os.path.normpath(os.path.join(here, "..", "rules"))
    return load_rules(rules_dir)
