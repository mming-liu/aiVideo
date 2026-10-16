"""Transport references to exact source paragraphs; no fuzzy quote repair."""
from __future__ import annotations

import copy
import hashlib
import re

VERSION = 'source_references.v1'


def catalog_for(novel_text):
    from shared.config import PROMPT_MAX_CHARS
    text=str(novel_text or '')
    identity=hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    return {f'src_{identity}_{match.start()}':match.group(0)
            for match in re.finditer(r'[^\r\n]+',text[:PROMPT_MAX_CHARS])
            if match.group(0).strip()}


def render(catalog):
    return '\n'.join(f'[{key}] {value}' for key,value in catalog.items())


def instruction(catalog):
    return ('\n\n# 原文引用传输格式 '+VERSION+'\n'
        '以上是展开后的逻辑核验记录要求。本次实际输出中，所有 evidence_spans 数组'
        '（source_support、coherence、source_preservation）只填写上方 src_ 开头的原文片段编号，'
        '不能抄写或改写原文文本。你负责选择确实支持完整主张的片段，'
        '代码负责把编号展开为该片段的逐字原文；“逐字证据”约束由展开后的记录继续核验。'
        '需要不连续证据就选择多个编号，不得编造编号。'
        'support_spans、exhibitor_span、echo_text 和义务正文仍按各自的原样回带规则，'
        '不能填原文编号。所有语义判断保持不变。')


def expand(record,catalog):
    """Expand only source citation fields; malformed or foreign refs fail closed."""
    if not isinstance(record,dict):
        return record,[]
    out=copy.deepcopy(record)
    bindings=[]
    locations=[]
    rows=out.get('source_support')
    if isinstance(rows,list):
        locations.extend((row,f'source_support[{i}]') for i,row in enumerate(rows) if isinstance(row,dict))
    for key in ('coherence','source_preservation'):
        if isinstance(out.get(key),dict):
            locations.append((out[key],key))
    for row,path in locations:
        refs=row.get('evidence_spans')
        if not isinstance(refs,list):
            continue  # The shared record shape validator owns type errors.
        if any(not isinstance(ref,str) or ref not in catalog for ref in refs):
            return None,[{'code':'verify_source_reference_invalid','kind':'verification',
                          'uid':'','ref':'','path':path+'.evidence_spans',
                          'reason':'来源引用须使用本次原文目录中的片段编号，不接受文本或其他原文的编号','fix':''}]
        bindings.append({'path':path+'.evidence_spans','refs':list(refs)})
        row['evidence_spans']=[catalog[ref] for ref in refs]
    out['_source_reference_evidence']={'version':VERSION,'bindings':bindings}
    return out,[]


def continuous_evidence(refs,catalog,novel_text):
    """The smallest literal source interval containing the selected paragraphs."""
    if (not isinstance(refs,list) or not refs
            or any(not isinstance(ref,str) or ref not in catalog for ref in refs)):
        raise ValueError('evidence 必须选择本次原文目录中的非空编号数组')
    starts=[int(ref.rsplit('_',1)[1]) for ref in refs]
    ends=[start+len(catalog[ref]) for start,ref in zip(starts,refs)]
    return novel_text[min(starts):max(ends)]
