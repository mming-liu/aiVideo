# -*- coding: utf-8 -*-
"""step01_script_gen.orchestrator —— Step 01 编排实现（2026-11-19 自 main.py 按职责拆出）.

入口仍是 `step01_script_gen/main.py`。**不要**绕过它直接导入本包的子模块：
`runtime` 在导入期计算章节目录、日志与运行上下文，必须在 `main.py` 的
运行范围 bootstrap（`--chapter/--book/--book-name`）之后才能导入。

本文件刻意不导入任何子模块。
"""

#: 子模块的依赖顺序（每个模块只依赖排在它前面的模块；tools/split_step01_main.py 与
#: tests 的静态守卫共用这份顺序）。
MODULE_ORDER = (
    "runtime", "scoring", "regen", "repair_apply", "final_audit", "repair_accept",
    "finalize", "repair_targets", "repair_round", "persist", "blocked_ledger",
    "generation", "commit", "best_guard",
)
