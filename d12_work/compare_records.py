"""比较两次核验记录（同一候选、同一摘要）：逐事实、逐声音、验收码."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'output/step01_ab/20261018T135622'
NOVEL = (EXP / 'd9_feasibility/input/novel.txt').read_text(encoding='utf-8')
PLAN = json.loads((ROOT / 'd12_work/candidate_content.json').read_text(encoding='utf-8'))

from step01_script_gen import outline as o   # noqa: E402

recs = {}
for case in ('verify_1', 'verify_2'):
    recs[case] = json.loads((ROOT / 'd12_work/run' / case / 'verification_record.json')
                            .read_text(encoding='utf-8'))
print('digests equal:', len({r['plan_digest'] for r in recs.values()}) == 1,
      recs['verify_1']['plan_digest'])
print('rules:', {r['rule_version'] for r in recs.values()},
      'status:', {r['status'] for r in recs.values()})
f1 = {x['fact_uid']: x['verdict'] for x in recs['verify_1']['facts']}
f2 = {x['fact_uid']: x['verdict'] for x in recs['verify_2']['facts']}
v1 = {x['unit_uid']: (x['verdict'], x['source_kind'], x['speaker']) for x in recs['verify_1']['voice_units']}
v2 = {x['unit_uid']: (x['verdict'], x['source_kind'], x['speaker']) for x in recs['verify_2']['voice_units']}
print('fact flips:', {k: (f1.get(k), f2.get(k)) for k in set(f1) | set(f2) if f1.get(k) != f2.get(k)} or 'none')
print('voice flips:', {k: (v1.get(k), v2.get(k)) for k in set(v1) | set(v2) if v1.get(k) != v2.get(k)} or 'none')
print('fact verdicts pass1:', sorted(set(f1.values())), 'pass2:', sorted(set(f2.values())))
print('voice kinds pass1:', sorted({x[1] for x in v1.values()}), 'pass2:', sorted({x[1] for x in v2.values()}))
for case, rec in recs.items():
    probs = o.plan_acceptance_problems(dict(PLAN, plan_verification=rec), NOVEL) or []
    print(case, 'acceptance:', [(p.get('code'), p.get('uid')) for p in probs] or '0')
# 记录里的调用证据摘要
for case, rec in recs.items():
    ev = rec.get('_call_evidence') or {}
    print(case, 'call_id=', rec.get('call_id'), 'usage=',
          (ev.get('usage') or {}))
