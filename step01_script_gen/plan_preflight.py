# -*- coding: utf-8 -*-
"""R3-3：核验前的规划入口预检（原子性 + 声音/节拍闭环；影子）.

不接 `main.py` 生产主门；供离线回放/实验运行器在**发内容核验之前**调用。
"""
from __future__ import annotations

from step01_script_gen.fact_atomicity import atomicity_problems
from step01_script_gen.voice_carrier_closure import closure

VERSION = "plan_preflight.v1"


def preflight_problems(plan, novel_text="") -> dict:
    atomic = atomicity_problems(plan, novel_text)
    cl = closure(plan, novel_text)
    carrier = []
    for p in cl.get("problems") or []:
        q = dict(p)
        q["preflight_stage"] = "carrier_closure"
        carrier.append(q)
    for p in atomic:
        p["preflight_stage"] = "fact_atomicity"
    problems = atomic + carrier
    return {
        "version": VERSION,
        "passed": not problems,
        "problems": problems,
        "first_rejection": problems[0] if problems else None,
        "atomicity_problems": atomic,
        "carrier_problems": carrier,
        "closure": cl,
    }


__all__ = ["VERSION", "preflight_problems"]
