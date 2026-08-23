"""约束 DSL 子包（v6.42 PoC）.

提供：
  - interpreter: 表达式求值 + 动作执行
  - loader: 从 YAML 自动加载 + 注册到 ALL_CONSTRAINTS

新增规则流程（零 Python 代码）：
  1. 在 step01_script_gen/constraints/rules/ 加 .yaml 文件
  2. 启动时调 auto_load() 或在需要时调 load_rules(path)
"""
from .interpreter import _eval_expr, _exec_action
from .loader import load_rule_file, load_rules, auto_load

__all__ = [
    "_eval_expr",
    "_exec_action",
    "load_rule_file",
    "load_rules",
    "auto_load",
]
