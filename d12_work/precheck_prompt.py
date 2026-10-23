"""核验前离线自检：准入、提示块、摘要（零网络）."""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'output/step01_ab/20261018T135622'
NOVEL = (EXP / 'd9_feasibility/input/novel.txt').read_text(encoding='utf-8')
PLAN = json.loads((ROOT / 'd12_work/candidate_content.json').read_text(encoding='utf-8'))

from shared import presentation_contract as pc          # noqa: E402
from shared import content_verification as cv          # noqa: E402
from step01_script_gen import content_verify as cvs    # noqa: E402
from step01_script_gen.source_references import catalog_for  # noqa: E402

view, bad = pc.view_from_plan(PLAN)
print('view bad:', [(p.get('code'), p.get('path')) for p in (bad or [])])
print('admission:', [(p.get('code'), p.get('path')) for p in cvs._request_admission(view)] or '0')
print('digest:', cv.plan_digest_of(PLAN))
print('novel digest:', hashlib.sha256(NOVEL.encode('utf-8')).hexdigest())

prompt = cvs.build_verification_prompt(view, novel_text=NOVEL, novel_digest='x', novel_version='',
                                       source_catalog=catalog_for(NOVEL))
blocks = prompt.split('## 事实 ')
for b in blocks[1:]:
    fu = b.split('主张', 1)[0].strip()
    if fu in ('f02', 'f03', 'f04', 'f10', 'f11', 'f12', 'f13'):
        body = b.split('\n## 事实 ')[0]
        body = body.split('实际展示正文（**只有这些**是观众能看到的）：')[-1]
        print('--- %s ---%s' % (fu, body.strip()[:400]))
if '# 声音单位' in prompt:
    tail = prompt.split('# 声音单位', 1)[1].split('# 判定规则', 1)[0]
    print('--- voices ---' + tail[:900])
