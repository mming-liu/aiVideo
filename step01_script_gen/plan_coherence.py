"""Versioned whole-plan coherence evidence; semantic decisions belong to the judge."""
from __future__ import annotations

import json

FIELD = 'plan_coherence_version'
VERSION = 'plan_coherence.v3'
PROTOCOL_CODES = {'verify_coherence_record', 'verify_coherence_identity',
                  'verify_coherence_evidence'}
SCHEMA = {
    'type': 'object',
    'properties': {
        # ⚠️ 2026-10：契约（`shape_problems`）要求这些字符串**非空白**，schema 却只写
        # `type: string` ⇒ 模型按 schema 合法输出空串，却被契约判 `verify_coherence_record`
        # 而整份核验失败（实测：真实运行 3 次纠正都没救回来）。
        # 把要求抬进 schema 本身，模型才有据可依。
        'beat_uids': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
        'verdict': {'type': 'string', 'enum': ['supported','partial','contradicted','unknown']},
        'conflict_beats': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
        'evidence_spans': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
        'reason': {'type': 'string', 'minLength': 1},
        'dependencies': {'type': 'array', 'items': {'type': 'object',
            'properties': {'beat_uid': {'type': 'string', 'minLength': 1},
                'requires_before': {'type': 'array',
                                    'items': {'type': 'string', 'minLength': 1}},
                'reason': {'type': 'string', 'minLength': 1}},
            'required': ['beat_uid','requires_before','reason'],
            'additionalProperties': False}},
    },
    'required': ['beat_uids','verdict','conflict_beats','evidence_spans','reason','dependencies'],
    'additionalProperties': False,
}


def mark_plan(plan):
    return dict(plan, **{FIELD: VERSION})


def required(view):
    return FIELD in (view.get('plan_basis') or {})


def admission_problems(view):
    if required(view) and (view.get('plan_basis') or {}).get(FIELD) != VERSION:
        return [_problem('verify_coherence_record','未知或空白的计划整体一致性协议版本')]
    return []


def _problem(code, reason, uid=''):
    return {'code':code, 'kind':'beat' if code=='verify_plan_coherence' else 'verification',
            'uid':uid,'ref':uid,'path':'beats',
            'reason':reason+'；核对完整先后依赖，用既有 order/upsert 修订并保留核心事实与台词。',
            'fix':''}


def shape_problems(value):
    if not isinstance(value,dict) or set(value)!=set(SCHEMA['required']):
        return [_problem('verify_coherence_record','coherence 必须完整提供且仅提供声明的字段')]
    for key in ('beat_uids','conflict_beats','evidence_spans'):
        if not isinstance(value[key],list) or any(not isinstance(s,str) or not s.strip() for s in value[key]):
            return [_problem('verify_coherence_record',f'coherence.{key} 必须为非空白字符串数组（允许空数组）')]
    if (value['verdict'] not in SCHEMA['properties']['verdict']['enum']
            or not isinstance(value['reason'],str) or not value['reason'].strip()):
        return [_problem('verify_coherence_record','coherence.verdict 非法或 reason 为空白')]
    if not isinstance(value['dependencies'],list):
        return [_problem('verify_coherence_record','dependencies 必须逐节拍提供')]
    for _i, row in enumerate(value['dependencies']):
        if (not isinstance(row,dict) or set(row)!={'beat_uid','requires_before','reason'}
                or not isinstance(row['beat_uid'],str) or not row['beat_uid'].strip()
                or not isinstance(row['reason'],str) or not row['reason'].strip()
                or not isinstance(row['requires_before'],list)
                or any(not isinstance(s,str) or not s.strip() for s in row['requires_before'])):
            # ⚠️ 2026-10：拒收必须**点名是哪一条、哪个字段**。旧文案只说"dependencies 条目
            # 必须…非空原因"，模型在真实运行里连续纠正都定位不到（7 份记录里 2 份带着空
            # `reason` 反复被拒、最后额度耗尽）。这里带上索引、节拍身份与具体字段。
            _uid = str(row.get('beat_uid') or '') if isinstance(row, dict) else ''
            if not isinstance(row, dict):
                _what = '条目不是对象'
            elif set(row) != {'beat_uid','requires_before','reason'}:
                _what = '字段集必须恰好是 beat_uid/requires_before/reason，实际是 %s' % (
                    sorted(row),)
            elif not str(row.get('beat_uid') or '').strip():
                _what = 'beat_uid 为空白'
            elif not str(row.get('reason') or '').strip():
                _what = 'reason 为空白（必须写清主体在场/状态承接/因果前提）'
            elif (not isinstance(row.get('requires_before'), list)
                    or any(not isinstance(s, str) or not s.strip()
                           for s in row.get('requires_before') or [])):
                _what = 'requires_before 必须是**非空白字符串**数组（没有先行关系就给空数组）'
            else:
                _what = '条目不合法'
            return [_problem('verify_coherence_record',
                             'dependencies[%d]（节拍 %s）不合格：%s'
                             % (_i, _uid or '?', _what), _uid)]
    return []


def problems(record,view,novel_text=None):
    if not required(view):
        return []
    invalid=admission_problems(view)
    if invalid:
        return invalid
    value=record.get('coherence')
    bad=shape_problems(value)
    if bad:
        return bad
    want=[b['uid'] for b in view['beats']]
    conflicts=value['conflict_beats']
    if (value['beat_uids']!=want or len(conflicts)!=len(set(conflicts))
            or not set(conflicts).issubset(want)):
        return [_problem('verify_coherence_identity','coherence 必须回带完整有序 beat_uids；冲突身份必须属于本候选且不重复')]
    rows=value['dependencies']
    if ([r['beat_uid'] for r in rows]!=want or any(
            len(r['requires_before'])!=len(set(r['requires_before']))
            or not set(r['requires_before']).issubset(set(want)-{r['beat_uid']}) for r in rows)):
        return [_problem('verify_coherence_identity','dependencies 必须覆盖完整当前顺序，先行身份属于本候选、不得重复或引用自身')]
    supported=value['verdict']=='supported'
    if supported and conflicts:
        return [_problem('verify_coherence_record','supported 不得同时声明冲突节拍')]
    if not supported and (not conflicts or not value['evidence_spans']):
        return [_problem('verify_coherence_record','负面结论须指出冲突节拍及原文证据')]
    if novel_text is not None and any(span not in novel_text for span in value['evidence_spans']):
        return [_problem('verify_coherence_evidence','coherence.evidence_spans 必须逐字出自本次原文')]
    positions={uid:i for i,uid in enumerate(want)}
    dependency_conflicts=[]
    for row in rows:
        late=[uid for uid in row['requires_before'] if positions[uid]>=positions[row['beat_uid']]]
        if late:
            dependency_conflicts.append(_problem('verify_plan_coherence',
                f"{row['beat_uid']} 必须在 {','.join(late)} 后呈现，但当前顺序相反：{row['reason']}",
                row['beat_uid']))
    if dependency_conflicts:
        return dependency_conflicts
    if not supported:
        return [_problem('verify_plan_coherence',value['reason'],conflicts[0])]
    return []


def protocol_problems(record, view):
    return [p for p in problems(record, view) if p['code'] in PROTOCOL_CODES]


def prompt(view):
    if not required(view):
        return ''
    units={u['uid']:u['text'] for key in ('dialogue_units','narration_units') for u in view.get(key,[])}
    rows=[]
    for beat in view['beats']:
        rows.append({key:beat.get(key) for key in ('uid','text','voice_ref')})
    return ('\n\n# 必答：整份计划的前后依赖（coherence）\n'
        '下面是完整有序节拍及声音正文。信息目的等声明不能代替实际动作与台词。独立核对时间、因果、主体在场和问答前提；'
        '逐条事实正确不代表整份计划可执行。缺少必要前提或先后颠倒须判负；'
        '明确且可理解的回忆/倒叙允许，不要求按原文字面位置机械排序，不增加无关原文细节。'
        '正常的时间跳转、场景切换及合理可推知的过渡动作可以省略；'
        '不能仅因未逐项展示原文操作而判负。只有已呈现内容存在不可兼容的先后、'
        '状态或主体关系，或缺失使核心事件及人物关系无法理解的必要前提，才构成冲突。'
        '将整体结论写入额外顶层 coherence 对象，字段严格如下：'
        +json.dumps(SCHEMA,ensure_ascii=False)+'\n'
        'beat_uids 原样回带完整实际顺序；supported 时 conflict_beats=[]；'
        '非 supported 必须指出冲突身份、原文逐字 evidence_spans 与具体关系原因。'
        'dependencies 必须按实际顺序逐节拍列全；每条独立判断该节拍成立所必需、'
        '且本计划已有明确呈现的先行节拍，填 requires_before，并解释主体、状态或因果关系。'
        '⚠️ 每条的 `reason` 必须是**非空白**的具体关系说明（写清是主体在场、状态承接还是因果前提），'
        '**不能是空串**——空串会被判为记录不合格，整份核验作废。'
        '不能因为前提被排在后面就省略这条依赖；顺序比较由代码完成，整体 supported 不能覆盖依赖冲突。'
        '没有这种必要先行关系填空数组；普通过渡动作不制造依赖，合法倒叙以观众能理解的叙事前提为准。'
        '\n完整节拍：'+json.dumps(rows,ensure_ascii=False)+'\n声音正文：'
        +json.dumps(units,ensure_ascii=False)
        # 2026-10：少样本。真实运行里模型反复交空 `reason`，被契约判记录不合格后
        # 整份核验作废、额度耗尽。给一份**填好的**样例，比再加一条禁令有效。
        +'\n\n合格样例（照这个形状，reason 一律写实、不得空）：\n'
        +json.dumps({'beat_uids': ['b01','b02','b03'],
                     'verdict': 'supported',
                     'conflict_beats': [],
                     'evidence_spans': [],
                     'reason': '三条节拍按相遇→表明身份→交代去向推进，主体在场与因果前提完整',
                     'dependencies': [
                         {'beat_uid': 'b01','requires_before': [],
                          'reason': '开场镜头，无先行依赖'},
                         {'beat_uid': 'b02','requires_before': ['b01'],
                          'reason': '承接 b01 的相遇，人物在场后才有这句话'},
                         {'beat_uid': 'b03','requires_before': ['b02'],
                          'reason': '以 b02 交代的身份为前提下达去向'}]},
                    ensure_ascii=False))
