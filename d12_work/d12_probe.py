"""离线核对：候选计划在本轮代码下的确定性状态与核验提示形状（零网络、零模型调用）."""
from __future__ import annotations

import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'output/step01_ab/20261018T135622'
D9 = EXP / 'd9_feasibility'
NOVEL = (D9 / 'input' / 'novel.txt').read_text(encoding='utf-8')

from shared import presentation_contract as pc          # noqa: E402
from step01_script_gen import outline as o             # noqa: E402
from step01_script_gen import plan_feasibility as pf   # noqa: E402
from step01_script_gen import content_verify as cvs    # noqa: E402

OUT = io.StringIO()
def say(*a):
    print(*a, file=OUT)

CASES = {
    'r6': EXP / 'd11_run/handfix_r6/final_plan.json',
    'cand9': EXP / 'd11_run/real_slot_f08/candidate_plan.json',
}

for name, path in CASES.items():
    plan = json.loads(path.read_text(encoding='utf-8'))
    plan.pop('plan_verification', None)
    plan.pop('verification_rejected', None)
    say('=' * 70)
    say(name, path.name)
    view, bad = pc.view_from_plan(plan)
    say(' view problems: %s' % ([(p.get('code'), p.get('path')) for p in (bad or [])][:6],))
    if view is None:
        continue
    codes = [p.get('code') for p in (o.plan_acceptance_problems(plan, NOVEL) or [])]
    say(' acceptance codes: %s' % (codes or '0',))
    units = [(u.get('uid'), str(u.get('text') or ''), 'dialogue') for u in view.get('dialogue_units') or []]
    units += [(u.get('uid'), str(u.get('text') or ''), 'narration') for u in view.get('narration_units') or []]
    durs = [(pf._estimate_duration(t), uid, kind) for uid, t, kind in units]
    durs.sort(reverse=True)
    say(' beats=%d facts=%d voices=%d' % (len(view['beats']), len(view['frozen_facts']), len(units)))
    say(' total voice sec=%.2f  max line=%.2f (%s)' % (sum(d for d, _, _ in durs), durs[0][0], durs[0][1]))
    say(' longest 4: %s' % ([(round(d, 2), u) for d, u, _ in durs[:4]],))
    for b in view['fact_bindings']:
        say('  %s -> %s' % (b['fact_uid'], [(c.get('kind'), pc.ref_uid(c)) for c in b['carriers']]))
    prompt = cvs.build_verification_prompt(view, novel_text=NOVEL, novel_digest='x', novel_version='')
    for fu in ('f02', 'f09'):
        marker = '## 事实 %s' % fu
        if marker not in prompt:
            continue
        block = prompt.split(marker, 1)[1]
        block = block.split('\n## 事实 ', 1)[0]
        say(' - prompt %s -' % fu)
        say(block.strip()[:700])

(ROOT / 'd12_work/probe.out.txt').write_text(OUT.getvalue(), encoding='utf-8')
print('written')
