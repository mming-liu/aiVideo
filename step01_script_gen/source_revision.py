"""Original-producer correction of ungrounded claims, with independent preservation review."""
from __future__ import annotations

import copy
import json
import re

FIELD = 'source_revision_context'
VERSION = 'source_revision.v1'
#: v2：只放开 **targets 域内** 的事实拆分（父 uid → ≥2 个 `父uid.后缀` 子 uid）。
#: 子 uid 继承父授权；非 targets 事实的 uid 与主张仍必须原样保留。v1 行为不变。
VERSION_V2 = 'source_revision.v2'
SUPPORTED_VERSIONS = (VERSION, VERSION_V2)
PROTOCOL_CODES = {'verify_source_revision_context', 'verify_source_preservation_record',
                  'verify_source_preservation_identity', 'verify_source_preservation_evidence',
                  'verify_fact_split_context'}
CORE_FIELDS = ('main_events', 'climax', 'causal_chain', 'end_hook', 'character_motives')
PRESERVATION_SCHEMA = {'type':'object', 'properties':{
    'target_uids': {'type':'array','items':{'type':'string'}},
    'verdict': {'type':'string','enum':['supported','partial','contradicted','unknown']},
    'evidence_spans': {'type':'array','items':{'type':'string'}},
    'reason': {'type':'string'}},
    'required':['target_uids','verdict','evidence_spans','reason'], 'additionalProperties':False}


def problem(code, reason, uid=''):
    return {'code':code,'kind':'fact' if uid else 'verification','uid':uid,'ref':uid,
            'path':'required_facts','reason':reason,'fix':''}


def _split_child_parent(uid, targets):
    """`uid` 所属的授权 target 父 uid（按最长前缀；无命中返回 None）."""
    hits=[t for t in targets if uid.startswith(t+'.')]
    return max(hits,key=len) if hits else None


def _v2_identity_reason(baseline, current, targets):
    """v2 身份判据：返回第一处违规原因；`None` 表示合法.

    允许的唯一增删是 **targets 内** 的一条事实被替换成 ≥2 个
    `父uid.后缀` 子 uid。授权事实本身可以保留并改主张（不拆分时与 v1 相同）；
    非 targets 事实的 uid 与主张必须逐一原样保留——来源修正授权仍然有效。
    """
    if not set(targets).issubset(baseline):
        return '来源修正 targets 必须来自基线事实'
    children={}
    for uid in current:
        if uid in baseline:
            continue
        parent=_split_child_parent(uid,targets)
        if parent is None:
            return '新增事实 %r 不在任何授权事实的拆分域内' % uid
        suffix=uid[len(parent)+1:]
        if not suffix or not re.fullmatch(r'[A-Za-z0-9_]+',suffix):
            return ('子 uid %r 必须写成 `父uid.后缀`（后缀限字母、数字、下划线），'
                    '且不得再嵌套点号' % uid)
        if len([t for t in targets if uid.startswith(t+'.')])>1:
            return '子 uid %r 同时落在多个授权父之下，拆分归属有歧义' % uid
        children.setdefault(parent,[]).append(uid)
    for uid,claim in baseline.items():
        if uid in targets:
            if uid in current:
                if children.get(uid):
                    return '授权事实 %r 不能既保留又拆分成子 uid' % uid
            elif len(children.get(uid) or [])<2:
                return '授权事实 %r 被删除或没有拆成 ≥2 个子 uid' % uid
        else:
            if uid not in current:
                return '未授权事实 %r 被删除' % uid
            if children.get(uid):
                return '未授权事实 %r 不得拆分' % uid
            if current[uid]!=claim:
                return '未授权事实 %r 的主张被改写' % uid
    return None


def context(view):
    return (view.get('plan_basis') or {}).get(FIELD)


def admission_problems(view):
    if FIELD not in (view.get('plan_basis') or {}):
        return []
    ctx=context(view)
    if (not isinstance(ctx,dict) or set(ctx)!={'version','novel_digest','base_digest',
                                             'targets','baseline_facts','baseline_core'}
            or ctx.get('version') not in SUPPORTED_VERSIONS
            or any(not isinstance(ctx.get(k),str) or not re.fullmatch(r'[0-9a-f]{64}',ctx[k])
                   for k in ('novel_digest','base_digest'))
            or not isinstance(ctx.get('targets'),list) or not ctx['targets']
            or any(not isinstance(u,str) or not u.strip() for u in ctx['targets'])
            or len(set(ctx['targets']))!=len(ctx['targets'])
            or not isinstance(ctx.get('baseline_facts'),list)
            or not isinstance(ctx.get('baseline_core'),dict)
            or set(ctx['baseline_core'])!=set(CORE_FIELDS)):
        return [problem('verify_source_revision_context','来源修正上下文缺失、形状非法或版本/身份无效')]
    old=ctx['baseline_facts']
    if any((value is not None and not isinstance(value,(str,list)))
           or (isinstance(value,list) and any(not isinstance(item,str) for item in value))
           for value in ctx['baseline_core'].values()):
        return [problem('verify_source_revision_context','来源修正基线核心字段类型非法')]
    if any(not isinstance(f,dict) or set(f)!={'uid','claim'}
           or any(not isinstance(f[k],str) or not f[k].strip() for k in ('uid','claim')) for f in old):
        return [problem('verify_source_revision_context','来源修正基线事实必须完整保留身份与正文')]
    baseline={f['uid']:f['claim'] for f in old}
    # **拆分账本准入扩展**：先按已校验的父子映射把子事实折回父义务，再跑既有 v1/v2。
    # 折叠只改读法，不改 baseline_facts / targets / source_preservation。
    from shared import fact_split
    collapsed, split_problems = fact_split.collapse(view)
    if split_problems or collapsed is None:
        _why='；'.join(str((p or {}).get('reason') or '') for p in (split_problems or [])[:2])
        return [problem('verify_fact_split_context',
                        '来源修正准入需要可读的拆分账本：%s' % (_why or '账本不可读'))]
    current=collapsed
    split_ctx=fact_split.context(view)
    for _entry in (split_ctx or {}).get('splits') or []:
        if not isinstance(_entry,dict) or _entry.get('baseline_claim') is None:
            continue
        _parent=str(_entry.get('parent_uid') or '')
        if (_parent not in ctx['targets']
                and str(_entry.get('parent_claim') or '')!=str(_entry.get('baseline_claim') or '')):
            return [problem('verify_fact_split_context',
                            '非目标事实 %r 的父主张与基线不一致——拆分不授权改父主张' % _parent,
                            _parent)]
    if len(baseline)!=len(old):
        return [problem('verify_source_revision_context','来源修正不得增删事实、改身份或改写未授权的已成立主张')]
    if ctx['version']==VERSION_V2:
        reason=_v2_identity_reason(baseline,current,ctx['targets'])
        if reason:
            return [problem('verify_source_revision_context',reason)]
        return []
    if (set(baseline)!=set(current)
            or not set(ctx['targets']).issubset(baseline)
            or any(current[u]!=claim for u,claim in baseline.items() if u not in ctx['targets'])):
        return [problem('verify_source_revision_context','来源修正不得增删事实、改身份或改写未授权的已成立主张')]
    return []


def shape_problems(value):
    if (not isinstance(value,dict) or set(value)!=set(PRESERVATION_SCHEMA['required'])
            or value.get('verdict') not in PRESERVATION_SCHEMA['properties']['verdict']['enum']
            or not isinstance(value.get('reason'),str) or not value['reason'].strip()
            or any(not isinstance(value.get(k),list)
                   or any(not isinstance(s,str) or not s.strip() for s in value[k])
                   for k in ('target_uids','evidence_spans'))):
        return [problem('verify_source_preservation_record','来源修正必须有独立、完整且非空原因的核心保留核验')]
    return []


def problems(record, view, *, novel_text=None, novel_digest=None):
    if FIELD not in (view.get('plan_basis') or {}):
        return []
    bad=admission_problems(view)
    if bad:
        return bad
    ctx=context(view)
    if novel_digest is not None and ctx['novel_digest']!=novel_digest:
        return [problem('verify_source_revision_context','来源修正属于其他原文身份')]
    value=record.get('source_preservation')
    bad=shape_problems(value)
    if bad:
        return bad
    if value['target_uids']!=ctx['targets']:
        return [problem('verify_source_preservation_identity','来源保留核验必须回带完整、同序的授权事实身份')]
    if novel_text is not None and any(s not in novel_text for s in value['evidence_spans']):
        return [problem('verify_source_preservation_evidence','保留核验的证据必须逐字来自本次原文')]
    if value['verdict']!='supported':
        return [problem('source_preservation_failed',value['reason'],ctx['targets'][0])]
    if not value['evidence_spans']:
        return [problem('verify_source_preservation_evidence','核心保留通过须给出原文证据')]
    return []


def protocol_problems(record,view):
    return [p for p in problems(record,view) if p['code'] in PROTOCOL_CODES]


def prompt(view):
    if FIELD not in (view.get('plan_basis') or {}):
        return ''
    ctx=context(view)
    current={k:(view.get('plan_basis') or {}).get(k) for k in CORE_FIELDS}
    current['main_events']=view.get('frozen_events')
    return ('\n\n# 来源主张修正：必须独立核验核心保留\n'
        '原生产者获准纠正仅 targets 中来源不成立的主张，不能删除原文真实支持的核心动作、主体、'
        '否定、条件及因果关系来换通过，也不能在关联事件或其他摘要留下同一无来源推断。'
        '比较基线、当前完整主张和当前事件，结合全文核对：只去除/修正无原文支持部分，'
        '其余原文核心内容是否仍完整保留。不是要求保留已被证明无来源的推断。'
        '核验员不得自行修改计划；负面要具体说明被丢掉、换主体或残留错误推断的关系。'
        '在额外顶层 source_preservation 回答，严格使用以下 schema：'
        +json.dumps(PRESERVATION_SCHEMA,ensure_ascii=False)+'\n基线及授权：'
        +json.dumps(ctx,ensure_ascii=False)+'\n当前核心摘要：'+json.dumps(current,ensure_ascii=False)
        +'\n当前事实采用前面逐项给出的完整冻结主张。')


def eligible_targets(plan):
    """Only a current, valid source verdict can open the producer correction path."""
    from shared.content_verification import (plan_digest_of, record_basis_matches, record_shape_problems,
                                             DEFAULT_RULE_VERSION, record_protocol_problems)
    record=plan.get('plan_verification')
    if (not isinstance(record,dict) or record.get('status')!='ok'
            or record.get('rule_version')!=DEFAULT_RULE_VERSION
            or record.get('plan_digest')!=plan_digest_of(plan)
            or record_shape_problems(record)
            or not record_basis_matches(record,plan)):
        return []
    from shared.presentation_contract import view_from_plan
    view,bad=view_from_plan(plan)
    if bad:
        return []
    if record_protocol_problems(record,view,plan_digest=plan_digest_of(plan),
                                novel_text=None,novel_digest=record.get('novel_digest')):
        return []
    negative={r['fact_uid'] for r in record.get('source_support',[])
              if r.get('verdict') in ('partial','contradicted','unknown')}
    if context(view):
        negative.update(context(view)['targets'])
    return [f['uid'] for f in view['frozen_facts'] if f['uid'] in negative]


def apply_response(plan, response, *, novel_text, novel_digest):
    """A source transaction keeps fact identities and non-target claims intact."""
    from shared.presentation_contract import view_from_plan
    from shared.content_verification import plan_digest_of
    from step01_script_gen.content_verify import novel_identity
    view,bad=view_from_plan(plan)
    targets=eligible_targets(plan)
    if (bad or not targets or novel_identity(novel_text)['digest']!=novel_digest
            or plan.get('plan_verification',{}).get('novel_digest')!=novel_digest):
        return None,[problem('source_revision_unauthorized','没有可独立核对的当前来源负面结论')]
    from shared.content_verification import record_protocol_problems
    invalid=record_protocol_problems(plan['plan_verification'],view,
        plan_digest=plan_digest_of(plan),novel_text=novel_text,novel_digest=novel_digest)
    if invalid:
        return None,[problem('source_revision_unauthorized','来源负面记录尚有协议或出处错误，须先纠正核验记录')]
    if (not isinstance(response,dict) or set(response)!={'fact_updates','core_updates'}
            or not isinstance(response['fact_updates'],list)
            or not isinstance(response['core_updates'],dict)
            or not set(response['core_updates']).issubset(CORE_FIELDS)):
        return None,[problem('source_revision_response','来源生产者须返回 fact_updates 与受限 core_updates')]
    rows=response['fact_updates']
    if (any(not isinstance(r,dict) or set(r)!={'fact_uid','claim','evidence'}
            or any(not isinstance(r[k],str) or not r[k].strip() for k in ('fact_uid','claim','evidence'))
            or '｜' in r['claim'] or '｜' in r['evidence'] or r['evidence'] not in novel_text for r in rows)
            or [r['fact_uid'] for r in rows]!=targets):
        return None,[problem('source_revision_response','修正表必须完整同序覆盖授权身份、主张非空且证据逐字来自原文')]
    for key,value in response['core_updates'].items():
        old=plan.get(key)
        if ((isinstance(old,list) and (not isinstance(value,list)
                or any(not isinstance(s,str) or not s.strip() for s in value)))
                or (isinstance(old,str) and (not isinstance(value,str) or not value.strip()))
                or (old is None and value is not None)):
            return None,[problem('source_revision_response','核心摘要修正字段类型非法或空白：'+key)]
    candidate=copy.deepcopy(plan)
    updates={r['fact_uid']:r for r in rows}
    for i,fact in enumerate(view['frozen_facts']):
        if fact['uid'] not in updates:
            continue
        row=updates[fact['uid']]
        old=str(candidate['required_facts'][i])
        suffix=[part for part in old.split('｜')[1:] if not part.strip().startswith('原文证据：')]
        candidate['required_facts'][i]='｜'.join([row['claim'],'原文证据：“'+row['evidence']+'”']+suffix)
    candidate.update(copy.deepcopy(response['core_updates']))
    candidate[FIELD]=copy.deepcopy(context(view)) if context(view) else {
        'version':VERSION,'novel_digest':novel_digest,'base_digest':plan_digest_of(plan),
        'targets':targets,'baseline_facts':[{'uid':f['uid'],'claim':f['claim']} for f in view['frozen_facts']],
        'baseline_core':{k:copy.deepcopy(plan.get(k,[] if k in ('main_events','causal_chain','character_motives') else '')) for k in CORE_FIELDS}}
    candidate[FIELD]['targets']=targets
    for key in ('plan_verification','verification_rejected','verification_context'):
        candidate.pop(key,None)
    candidate_view,bad=view_from_plan(candidate)
    bad=bad or admission_problems(candidate_view)
    return (None,bad) if bad else (candidate,[])


def revise_with_service(plan, issues, *, novel_text, service, model=None,
                        timeout_sec=None, deadline_monotonic=None, verification=None):
    """One original-producer call; the existing controller owns budget and acceptance."""
    from _text_gen import TextGenerationRequest
    from step01_script_gen.outline import _parse_outline_json, _outline_max_tokens
    from shared.call_ledger import call_id_of
    targets=eligible_targets(plan)
    evidence={'route':'source_basis_revision','targets':targets,'applied':None}
    from step01_script_gen.content_verify import novel_identity
    novel_digest=novel_identity(novel_text)['digest']
    if not targets or plan.get('plan_verification',{}).get('novel_digest')!=novel_digest:
        evidence.update(applied=False,no_model_call=True)
        return {'kind':'verification_failed','detail':'当前原文与来源负面记录身份不匹配',
                'call_id':'','evidence':evidence}
    from shared.presentation_contract import view_from_plan
    from shared.content_verification import plan_digest_of,record_protocol_problems
    view,bad=view_from_plan(plan)
    bad=bad or record_protocol_problems(plan['plan_verification'],view,
        plan_digest=plan_digest_of(plan),novel_text=novel_text,novel_digest=novel_digest)
    if bad:
        evidence.update(applied=False,no_model_call=True)
        return {'kind':'verification_failed','detail':'来源核验记录尚有协议或出处错误，须先纠正核验',
                'call_id':'','evidence':evidence}
    from step01_script_gen.source_references import catalog_for,render,continuous_evidence
    source_catalog=catalog_for(novel_text)
    prompt_text=('你是本计划的原规划生产者。来源核验发现部分首版主张加入了原文不支持的限定。'
        '仅修正授权事实的无来源部分，保留原文真实支持的核心事件、主体、否定、条件、因果。'
        'fact_updates 必须按授权身份同序完整返回，每项 fact_uid、claim、evidence；'
        'evidence 为下方 src_ 原文片段编号的非空数组，代码将所选片段的最短连续原文区间展开为逐字证据。'
        '不抄写或改写原文，不编造编号。不得增删事实或改变其他事实。'
        'core_updates 仅在核心摘要含同一无来源推断时纠正，对象可含 '
        +', '.join(CORE_FIELDS)+'，保持原字段类型，其余不返回。'
        # ⚠️ 2026-10：实测模型只回 {"fact_updates": [...]}、把 `core_updates` 整个漏掉
        # （提供方没强制 schema 的 `required`），于是被整份拒收。这里写成不可漏的硬要求。
        '`core_updates` **必须出现**：没有核心修法就写空对象 `{}`；'
        '漏掉这个键会被判响应违约、整份拒收。'
        '只输出 JSON 对象 {"fact_updates": [...], "core_updates": {}}，不要输出别的顶层键。'
        # 2026-10：少样本。真实运行里模型漏 `core_updates` 整份被拒；给一份骨架样例，
        # 比再加一条禁令有效（编号用占位符，**不要**照抄示例里的假编号）。
        '\n合格样例（形状照抄；evidence 填上面真实存在的 src_ 编号，**不要**写示例里的占位符）：'
        '\n{"fact_updates": [{"fact_uid": "F1", "claim": "修正后的完整主张",'
        ' "evidence": ["src_<原文摘要>_<偏移>"]}], "core_updates": {}}'
        '\n注意：fact_updates 必须**按授权身份同序**覆盖全部授权事实；core_updates 即使不改也**必须写 {}**。'
        '\n授权身份：'+json.dumps(targets,ensure_ascii=False)
        +'\n本轮核验与问题：'+json.dumps({'record':plan.get('plan_verification'),'issues':issues},ensure_ascii=False)
        +'\n完整基线计划：'+json.dumps(plan,ensure_ascii=False)+'\n完整原文（编号用于引用）：'+render(source_catalog))
    schema={'type':'object','properties':{
        'fact_updates':{'type':'array','items':{'type':'object','properties':{
            key:({'type':'array','items':{'type':'string'}} if key=='evidence'
                 else {'type':'string'}) for key in ('fact_uid','claim','evidence')},
            'required':['fact_uid','claim','evidence'],'additionalProperties':False}},
        'core_updates':{'type':'object','properties':{
            key:({'type':'array','items':{'type':'string'}} if isinstance(plan.get(key),list)
                 else {'type':'null'} if plan.get(key) is None else {'type':'string'})
            for key in CORE_FIELDS},'additionalProperties':False}},
        'required':['fact_updates','core_updates'],'additionalProperties':False}
    try:
        result=service.generate(TextGenerationRequest(
            system_prompt='你负责忠实于原文的来源修正，只输出 JSON。',user_prompt=prompt_text,
            max_tokens=_outline_max_tokens(),temperature=0.3,model=model,
            json_schema={'name':'source_basis_revision','strict':True,'schema':schema},
            timeout_sec=timeout_sec,deadline_monotonic=deadline_monotonic))
    except Exception as exc:
        return {'kind':'call_failed','detail':str(exc),'call_id':call_id_of(exc),'evidence':evidence}
    call_id=str(getattr(result,'call_id','') or '')
    if not result.success:
        return {'kind':'call_failed','detail':str(result.error),'call_id':call_id,'evidence':evidence}
    response=_parse_outline_json(result.text)
    evidence['response']=copy.deepcopy(response)
    if isinstance(response,dict) and isinstance(response.get('fact_updates'),list):
        try:
            for row in response['fact_updates']:
                if isinstance(row,dict):
                    row['evidence']=continuous_evidence(row.get('evidence'),source_catalog,novel_text)
        except ValueError as exc:
            evidence['applied']=False
            return {'kind':'illegal','detail':str(exc),'call_id':call_id,'evidence':evidence}
    candidate,bad=apply_response(plan,response,novel_text=novel_text,
                               novel_digest=novel_digest)
    evidence['applied']=candidate is not None
    if bad:
        return {'kind':'illegal','detail':json.dumps(bad,ensure_ascii=False),'call_id':call_id,'evidence':evidence}
    if verification is not None:
        from step01_script_gen.content_verify import attach_verification
        candidate=attach_verification(candidate,verification)
    return {'kind':'applied','plan':candidate,'detail':'source_basis_revision',
            'call_id':call_id,'evidence':evidence}
