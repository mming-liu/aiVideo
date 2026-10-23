"""单次真实核验驱动（等价于 d11_run2.py 的 verify 阶段；只把输出根换到可写目录）.

用法：py -3.14 d12_work/verify_once.py <case>
- 输入：d12_work/candidate_content.json（固定候选，摘要打印进 run_log）
- 输出：d12_work/run/<case>/（calls/send_01.json、calls/verify_archive.jsonl、
  verification_record.json 或 .rejected.json、run_log.json）
- cap=1：本进程最多一次物理发送；两次核验 = 两个独立进程/独立目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path('.').resolve()
EXP = ROOT / 'output/step01_ab/20261018T135622'
CASE = sys.argv[1] if len(sys.argv) > 1 else 'verify_1'
RUN = ROOT / 'd12_work/run' / CASE
(RUN / 'calls').mkdir(parents=True, exist_ok=True)
(RUN / 'output').mkdir(parents=True, exist_ok=True)

os.environ.update({
    'OUTPUT_BASE': str(RUN / 'output'),
    'NOVEL_NAME': '锦衣夜行', 'BOOK_INDEX': '1', 'BOOK_NAME': '杀青州', 'NOVEL_CHAPTER': '1',
    'STEP01_LOG_PATH': str(RUN / 'calls' / 'step01.log'),
    'CONTENT_VERIFY_MAX_TOKENS': '16000',
})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXP / 'tools'))

TARGET = 'deepseek-v4.1-flash'
CAP = 1
DEADLINE = 600.0
PLAN_PATH = pathlib.Path(os.environ.get('D12_PLAN') or (ROOT / 'd12_work/candidate_content.json'))
PLAN = json.loads(PLAN_PATH.read_text(encoding='utf-8'))
NOVEL = (EXP / 'd9_feasibility' / 'input' / 'novel.txt').read_text(encoding='utf-8')
NDIG = hashlib.sha256(NOVEL.encode('utf-8')).hexdigest()

from _text_gen import config as _cfg                     # noqa: E402
from shared import config as _scfg                       # noqa: E402
for _a in ('AGENT_PLAN_TEXT_MODEL', 'DEEPSEEK_MODEL'):
    if hasattr(_cfg, _a):
        setattr(_cfg, _a, TARGET)
_scfg.SCRIPT_CANDIDATE_MODELS = [TARGET]

import requests                                          # noqa: E402
import guard_core                                        # noqa: E402
from shared import presentation_contract as pc           # noqa: E402
from shared.content_verification import (VerificationController,   # noqa: E402
                                        record_protocol_problems, plan_digest_of)
from step01_script_gen import outline as o               # noqa: E402
from step01_script_gen import content_verify as cv       # noqa: E402
from step01_script_gen import plan_feasibility as pf     # noqa: E402

REAL_POST = requests.post
log = {'stage': 'verify', 'case': CASE, 'cap': CAP, 'target': TARGET,
       'plan_path': str(PLAN_PATH),
       'plan_sha16': hashlib.sha256(json.dumps(PLAN, ensure_ascii=False, sort_keys=True)
                                    .encode('utf-8')).hexdigest()[:16],
       'novel_sha': NDIG}

view, bad = pc.view_from_plan(PLAN)
if bad:
    raise SystemExit('视图不可用：%s' % [(p.get('code'), p.get('path')) for p in bad])
digest = plan_digest_of(PLAN)
log['plan_digest'] = digest

# 确定性门控（零发送）
codes = [p.get('code') for p in (o.plan_acceptance_problems(PLAN, NOVEL) or [])]
bad_codes = [c for c in codes if c not in ('verify_record_missing', 'verify_bad_response')]
units = [str(u.get('text') or '') for u in view.get('dialogue_units') or []]
units += [str(u.get('text') or '') for u in view.get('narration_units') or []]
lower = round(sum(float(pf._estimate_duration(t)) for t in units), 2)
log['deterministic'] = {'codes': codes, 'gate': bad_codes, 'voice_lower_bound': lower,
                        'beats': len(view['beats'])}
if bad_codes:
    log['verify'] = {'skipped': True, 'reason': '确定性门控未通过'}
    (RUN / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
    raise SystemExit('门控未通过：%s' % bad_codes)

G = guard_core.Guard(REAL_POST, cap=CAP, deadline_sec=DEADLINE, target=TARGET,
                     quotas=(50000, 16000), log_dir=RUN / 'calls')


def _verify(view_obj, plan_digest):
    return cv.verify_view_with_service(
        view_obj, plan_digest, novel_text=NOVEL, novel_digest=NDIG,
        max_tokens=16000, source_references=True, reasoning_effort='none',
        deadline_monotonic=time.monotonic() + 600,
        archive_path=str(RUN / 'calls' / 'verify_archive.jsonl'))


ctl = VerificationController(
    verify=_verify, require_coherence=True,
    validate_record=lambda r, v, d: record_protocol_problems(
        r, v, plan_digest=d, novel_text=NOVEL, novel_digest=NDIG),
    rule_version=getattr(o, 'VERIFY_RULE_VERSION', None), novel_digest=NDIG)
requests.post = G
try:
    rec = ctl.record_for(view, digest)
finally:
    requests.post = REAL_POST

log['verify'] = {'transmitted': G.transmitted, 'attempts': G.attempts,
                 'blocked': G.blocked, 'status': (rec or {}).get('status')}
_delivery = cv.deliver_verification_record(rec, run_dir=RUN, log=log, transmitted=G.transmitted)
if _delivery['accepted']:
    probs = o.plan_acceptance_problems(dict(PLAN, plan_verification=rec), NOVEL) or []
    log['final_acceptance'] = {'codes': [p.get('code') for p in probs],
                               'details': [(p.get('code'), p.get('uid')) for p in probs]}
    print('最终验收：%s' % ([p.get('code') for p in probs] or '0 问题'))
else:
    probs = o.plan_acceptance_problems(PLAN, NOVEL) or []
    log['final_acceptance'] = {'codes': [p.get('code') for p in probs],
                               'rejected_status': _delivery['rejected_status'],
                               'rejected_code': _delivery['rejected_code']}
    print('核验记录被拒：%s' % _delivery['rejected_status'])

log['totals'] = {'attempts': G.attempts, 'transmitted': G.transmitted, 'blocked': G.blocked}
archive = sorted((RUN / 'calls').glob('send_*.json'))
checks = []
if G.transmitted and not archive:
    checks.append('已传输但未归档')
if G.transmitted and len(archive) < G.transmitted:
    checks.append('归档数 < 已传输')
if G.archive_errors:
    checks.append('归档失败：%s' % '; '.join(G.archive_errors))
log['self_check'] = {'ok': not checks, 'problems': checks, 'archived': [p.name for p in archive]}
(RUN / 'run_log.json').write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
print('case=%s 尝试=%d 已传输=%d 阻断=%d 归档=%d 自检=%s' % (
    CASE, G.attempts, G.transmitted, G.blocked, len(archive),
    'PASS' if not checks else '; '.join(checks)))
raise SystemExit(0 if not checks else 1)
