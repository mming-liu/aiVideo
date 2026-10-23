"""交付封装：把第二次核验记录挂到候选上，落 final_plan.json，并核对 0 问题.

环境变量：D12_PLAN（候选）、D12_REC（核验记录）、D12_OUT（产物路径）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'output/step01_ab/20261018T135622'
NOVEL = (EXP / 'd9_feasibility' / 'input' / 'novel.txt').read_text(encoding='utf-8')
PLAN = json.loads(pathlib.Path(os.environ.get('D12_PLAN') or
                               (ROOT / 'd12_work/candidate_content_v15.json')
                               ).read_text(encoding='utf-8'))
REC = json.loads(pathlib.Path(os.environ.get('D12_REC') or
                              (ROOT / 'd12_work/run/verify_4/verification_record.json')
                              ).read_text(encoding='utf-8'))

from step01_script_gen import outline as o   # noqa: E402

final = dict(PLAN, plan_verification=REC)
probs = o.plan_acceptance_problems(final, NOVEL) or []
print('final acceptance:', [(p.get('code'), p.get('uid')) for p in probs] or '0')
if probs:
    raise SystemExit(1)
out = pathlib.Path(os.environ.get('D12_OUT') or (ROOT / 'd12_work/final_plan.json'))
out.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding='utf-8')
print('written', out)
