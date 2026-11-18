# -*- coding: utf-8 -*-
"""修订/核验提示词的三问自检（2026-11-18 R7）.

生产规则与匿名样本都不写死本章人名、镜号或原句；这里只提供通用问题文本。
"""
from __future__ import annotations

REVISION_SELF_CHECK_QUESTIONS = (
    "1. 摘句是否保留了决定句意的条件、否定及其所指？",
    "2. 观众通过哪一句、哪位说话人或哪个既有声音载体，听懂角色改变行动的动机与威胁？",
    "3. 末镜呈现的出发地、移动方向与目的地，分别有何原文依据？",
)


def render_revision_questions() -> str:
    return ("\n\n# 修订前自检三问（逐项回答后再落笔）\n"
            + "\n".join("- " + q for q in REVISION_SELF_CHECK_QUESTIONS) + "\n")
