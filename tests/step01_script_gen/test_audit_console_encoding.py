"""入口的 UTF-8 输出：GBK 控制台下含 `⇒` 的文案不得把退出码变成 1（2026-10-29 实测）."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_entry_makes_gbk_console_safe_for_arrow_text():
    code = (
        "import sys, tools.audit_frozen_candidate  # noqa: F401\n"
        "print('墙钟上限 300.000s 内未返回 ⇒ 按失败处置；')\n"
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert proc.returncode == 0, (proc.returncode, proc.stdout[-300:], proc.stderr[-500:])
