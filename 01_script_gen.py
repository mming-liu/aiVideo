"""01 分镜脚本生成 — (已弃用，重定向到 step01_script_gen.main)

保留此文件作为向后兼容的包装器。所有逻辑已迁移到 step01_script_gen/ 包中。
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
CHARACTER_EXTRACTION_ENABLED = os.getenv("CHARACTER_EXTRACTION_ENABLED", "1").strip() == "1"
SECTIONS_PER_CHAPTER = int(os.getenv("SECTIONS_PER_CHAPTER", "1"))
SHOT_COUNT_MIN = int(os.getenv("SHOT_COUNT_MIN", "4"))
SHOT_COUNT_MAX = int(os.getenv("SHOT_COUNT_MAX", "20"))
COMPLIANCE_STRICT = os.getenv("COMPLIANCE_STRICT", "0").strip() == "1"
COMPLIANCE_AUTO_FIX = os.getenv("COMPLIANCE_AUTO_FIX", "1").strip() == "1"

print("[DEPRECATED] 01_script_gen.py 已弃用，使用 step01_script_gen.main 替代", file=sys.stderr)

if __name__ == "__main__":
    from step01_script_gen.main import main
    main()
