"""原生 DeepSeek 未显式指定模型时使用规范的 Flash 模型名。"""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def test_default_native_deepseek_model_without_override():
    env = os.environ.copy()
    env.pop("DEEPSEEK_MODEL", None)
    result = subprocess.run(
        [sys.executable, "-c", "from _text_gen.config import config; print(config.DEEPSEEK_MODEL)"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "deepseek-flash"
