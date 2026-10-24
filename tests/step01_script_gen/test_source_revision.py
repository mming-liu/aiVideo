import copy
import json

import pytest

from step01_script_gen import source_revision as s, plan_coherence as c
from tests.step01_script_gen.test_legacy_semantic import (
    NOVEL,plan_for_narration,narration_response,verify_through_service)
from shared.presentation_contract import view_from_plan
from shared.content_verification import plan_digest_of

pytestmark=pytest.mark.v2_request


def baseline(monkeypatch):
    plan=c.mark_plan(plan_for_narration())
    plan['required_facts'][0]=plan['required_facts'][0].replace('周明去年辞职。','周明去年主动辞职。',1)
    raw=narration_response(claim='周明去年主动辞职。',verdict='partial')
    return verify_through_service(monkeypatch,plan,raw)


def response():
    return {'fact_updates':[{'fact_uid':'f01','claim':'周明去年辞职。','evidence':NOVEL}],
            'core_updates':{}}


def apply(plan,raw):
    from step01_script_gen.content_verify import novel_identity
    return s.apply_response(plan,raw,novel_text=NOVEL,novel_digest=novel_identity(NOVEL)['digest'])


def preservation(*,verdict='supported'):
    return {'target_uids':['f01'],'verdict':verdict,'evidence_spans':[NOVEL],
            'reason':'保留辞职、主体和时间，仅移除原文未确定的主动性'}


def test_original_producer_can_correct_only_ungrounded_claim(monkeypatch):
    plan=baseline(monkeypatch);before=copy.deepcopy(plan)
    candidate,bad=apply(plan,response())
    assert not bad and candidate is not None
    assert plan==before
    assert candidate['required_facts'][0].startswith('周明去年辞职。｜')
    assert candidate['source_revision_context']['targets']==['f01']
    assert candidate['main_events']==plan['main_events']
    assert 'plan_verification' not in candidate
    assert plan_digest_of(candidate)!=plan_digest_of(plan)
    view,bad=view_from_plan(candidate)
    assert not bad and not s.admission_problems(view)
    assert s.problems({},view)[0]['code']=='verify_source_preservation_record'
    assert not s.problems({'source_preservation':preservation()},view,novel_text=NOVEL)


@pytest.mark.parametrize('mutate',[
    lambda p:p['fact_updates'].clear(),
    lambda p:p['fact_updates'][0].update(fact_uid='f02'),
    lambda p:p['fact_updates'][0].update(claim=' '),
    lambda p:p['fact_updates'][0].update(evidence='原文没有的证据'),
    lambda p:p['core_updates'].update(dialogue_lines=[]),
    lambda p:p['core_updates'].update(main_events=None),
])
def test_invalid_source_response_never_changes_baseline(monkeypatch,mutate):
    plan=baseline(monkeypatch);before=copy.deepcopy(plan);raw=response();mutate(raw)
    candidate,bad=apply(plan,raw)
    assert candidate is None and bad and plan==before


@pytest.mark.parametrize('mutate',[
    lambda p:p.pop('plan_verification'),
    lambda p:p['plan_verification'].update(plan_digest='wrong'),
    lambda p:p['plan_verification'].update(novel_digest='wrong'),
    lambda p:p['plan_verification'].update(rule_version='old'),
    lambda p:p['plan_verification'].pop('rule_version'),
    lambda p:p['plan_verification']['source_support'].append(copy.deepcopy(p['plan_verification']['source_support'][0])),
    lambda p:p['plan_verification']['source_support'][0].update(verdict='supported'),
])
def test_no_current_source_rejection_means_no_reopening(monkeypatch,mutate):
    plan=baseline(monkeypatch);mutate(plan)
    candidate,bad=apply(plan,response())
    assert candidate is None and bad[0]['code']=='source_revision_unauthorized'


@pytest.mark.parametrize('value',[
    None,{}, {'target_uids':['f02'],'verdict':'supported','evidence_spans':[NOVEL],'reason':'错身份'},
    {'target_uids':['f01'],'verdict':'supported','evidence_spans':['假原文'],'reason':'假出处'},
])
def test_preservation_record_cannot_be_missing_or_foreign(monkeypatch,value):
    candidate,bad=apply(baseline(monkeypatch),response());assert not bad
    view,_=view_from_plan(candidate)
    bad=s.problems({'source_preservation':value},view,novel_text=NOVEL)
    assert bad and bad[0]['code'] in s.PROTOCOL_CODES


def test_dropping_supported_core_is_a_semantic_rejection(monkeypatch):
    candidate,bad=apply(baseline(monkeypatch),response());assert not bad
    view,_=view_from_plan(candidate)
    bad=s.problems({'source_preservation':preservation(verdict='partial')},view,novel_text=NOVEL)
    assert [p['code'] for p in bad]==['source_preservation_failed']
    assert not s.protocol_problems({'source_preservation':preservation(verdict='partial')},view)


def test_source_revision_through_actual_planner_and_verification_entry(monkeypatch):
    from types import SimpleNamespace
    from _text_gen.models import TextGenerationResult
    from step01_script_gen import outline, content_verify
    from step01_script_gen.plan_revision import RevisionBudget
    from shared import content_verification as cv
    plan=baseline(monkeypatch)
    issues=outline.plan_acceptance_problems(plan,NOVEL)
    assert any(p['code']=='fact_basis_invalid' for p in issues)
    requests=[]
    def generate(request):
        requests.append(request)
        from step01_script_gen.source_references import catalog_for
        raw=response();raw['fact_updates'][0]['evidence']=list(catalog_for(NOVEL))
        return TextGenerationResult(success=True,text=json.dumps(raw,ensure_ascii=False))
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=generate))
    def verify(view,digest):
        raw=narration_response()
        raw['source_preservation']=preservation()
        return dict(raw,schema=cv.SCHEMA_VERIFICATION,status='ok')
    controller=cv.VerificationController(verify=verify,require_coherence=True,
        novel_digest=content_verify.novel_identity(NOVEL)['digest'])
    result=outline.revise_plan_once(plan,issues,RevisionBudget(max_calls=1,deadline_sec=60),
        novel_text=NOVEL,verification=controller)
    assert result['kind']=='applied',result
    assert len(requests)==1 and NOVEL in requests[0].user_prompt
    assert not outline.plan_acceptance_problems(result['plan'],NOVEL)
    assert result['plan']['plan_verification']['plan_digest']==plan_digest_of(result['plan'])


def test_source_producer_transport_failure_keeps_baseline(monkeypatch):
    from types import SimpleNamespace
    from _text_gen.models import TextGenerationResult
    from step01_script_gen import outline
    from step01_script_gen.plan_revision import RevisionBudget
    plan=baseline(monkeypatch);before=copy.deepcopy(plan)
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(
        generate=lambda request:TextGenerationResult(success=False,error='incomplete response')))
    result=outline.revise_plan_once(plan,outline.plan_acceptance_problems(plan,NOVEL),
        RevisionBudget(max_calls=1,deadline_sec=60),novel_text=NOVEL)
    assert result['kind']=='call_failed' and plan==before
    assert result['evidence']['applied'] is None


def test_full_plan_replacement_cannot_drop_source_preservation_policy(monkeypatch):
    from step01_script_gen import outline
    candidate,bad=apply(baseline(monkeypatch),response());assert not bad
    replacement=copy.deepcopy(candidate)
    replacement.pop(s.FIELD)
    outline._preserve_acceptance_policy(candidate,replacement)
    assert replacement[s.FIELD]==candidate[s.FIELD]
    assert replacement[s.FIELD] is not candidate[s.FIELD]


def test_current_novel_mismatch_does_not_send_source_revision(monkeypatch):
    from types import SimpleNamespace
    from step01_script_gen import outline
    from step01_script_gen.plan_revision import RevisionBudget
    plan=baseline(monkeypatch);sent=[]
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(
        generate=lambda request:sent.append(request)))
    result=outline.revise_plan_once(plan,outline.plan_acceptance_problems(plan,NOVEL),
        RevisionBudget(max_calls=1,deadline_sec=60),novel_text='另一章正文')
    assert result['kind']=='verification_failed' and not sent


def test_later_source_rejection_expands_targets_without_losing_original_baseline(monkeypatch):
    plan=c.mark_plan(plan_for_narration())
    plan['required_facts'][0]=plan['required_facts'][0].replace('去年辞职','去年主动辞职',1)
    plan['required_facts'].append('仓库仍旧正常营业。｜原文证据：“仓库仍旧正常营业。”｜承载：B3/画面')
    plan['shot_beats'][2]='节拍：仓库仍旧正常营业，工人继续工作｜载体：画面'
    def raw_record(first,second):
        raw=narration_response(claim=first,verdict='partial')
        raw['source_support'].append({'fact_uid':'f02','verdict':second,
            'evidence_spans':[NOVEL] if second=='supported' else [],'reason':'显式来源裁决夹具'})
        fact=copy.deepcopy(raw['facts'][0]);fact.update(fact_uid='f02')
        fact['obligations'][0]['text']='仓库仍旧正常营业。'
        raw['facts'].append(fact)
        return raw
    plan=verify_through_service(monkeypatch,plan,raw_record('周明去年主动辞职。','supported'))
    candidate,bad=apply(plan,response());assert not bad
    original=copy.deepcopy(candidate[s.FIELD])
    raw=raw_record('周明去年辞职。','partial');raw['source_preservation']=preservation()
    candidate=verify_through_service(monkeypatch,candidate,raw)
    assert s.eligible_targets(candidate)==['f01','f02']
    updates=response();updates['fact_updates'].append({
        'fact_uid':'f02','claim':'仓库仍正常营业。','evidence':'仓库仍旧正常营业。'})
    revised,bad=apply(candidate,updates)
    assert not bad,bad
    assert revised[s.FIELD]['targets']==['f01','f02']
    assert revised[s.FIELD]['baseline_facts']==original['baseline_facts']
    assert revised[s.FIELD]['base_digest']==original['base_digest']


def test_optional_null_core_field_does_not_prevent_source_correction(monkeypatch):
    plan=c.mark_plan(plan_for_narration());plan['end_hook']=None
    plan['required_facts'][0]=plan['required_facts'][0].replace('去年辞职','去年主动辞职',1)
    plan=verify_through_service(monkeypatch,plan,narration_response(
        claim='周明去年主动辞职。',verdict='partial'))
    candidate,bad=apply(plan,response())
    assert not bad and candidate['end_hook'] is None
    assert candidate[s.FIELD]['baseline_core']['end_hook'] is None


@pytest.mark.parametrize('evidence',[NOVEL,[],['src_foreign_0'],[None]])
def test_original_producer_rejects_bad_source_reference_without_applying(monkeypatch,evidence):
    from types import SimpleNamespace
    from _text_gen.models import TextGenerationResult
    from step01_script_gen import outline
    from step01_script_gen.plan_revision import RevisionBudget
    plan=baseline(monkeypatch);before=copy.deepcopy(plan)
    raw=response();raw['fact_updates'][0]['evidence']=evidence
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=lambda request:
        TextGenerationResult(success=True,text=json.dumps(raw,ensure_ascii=False))))
    result=outline.revise_plan_once(plan,outline.plan_acceptance_problems(plan,NOVEL),
        RevisionBudget(max_calls=1,deadline_sec=60),novel_text=NOVEL)
    assert result['kind']=='illegal' and plan==before
    assert result['evidence']['applied'] is False


def test_stale_source_record_does_not_fall_back_to_another_revision_protocol(monkeypatch):
    from types import SimpleNamespace
    from step01_script_gen import outline
    from step01_script_gen.plan_revision import RevisionBudget
    plan=baseline(monkeypatch)
    issues=outline.plan_acceptance_problems(plan,NOVEL)
    plan['plan_verification']['rule_version']='old'
    sent=[]
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=lambda request:sent.append(request)))
    result=outline.revise_plan_once(plan,issues,RevisionBudget(max_calls=1,deadline_sec=60),novel_text=NOVEL)
    assert result['kind']=='verification_failed' and not sent


# ---- T3：来源修正授权与「复合事实必须拆」的规格冲突（版本化）----
#
# 互斥链：`outline.py` 要求复合事实各自成条；核验上下文每条事实只挂一条载体；
# 而 v1 `admission_problems` 要求事实 uid 集合与基线完全一致 ⇒ 拆不了。
# 版本化只放开 **targets 域内** 的拆分：父 uid 换成 ≥2 个 `父uid.后缀` 子 uid，
# 子 uid 继承父授权；非 targets 事实的身份与主张仍必须原样保留。v1 行为不变。

BASELINE_FACTS=(('f01','甲离开。'),('f02','乙辞职并离开。'),('f03','丙留下。'))
SPLIT_TARGET_FACTS=(('f01','甲离开。'),('f02.1','乙辞职。'),
                    ('f02.2','乙离开。'),('f03','丙留下。'))


def revision_view(version,facts,targets=('f02',),baseline=BASELINE_FACTS):
    ctx={'version':version,'novel_digest':'a'*64,'base_digest':'b'*64,
         'targets':list(targets),
         'baseline_facts':[{'uid':u,'claim':c} for u,c in baseline],
         'baseline_core':{key:'' for key in s.CORE_FIELDS}}
    return {'plan_basis':{s.FIELD:ctx},
            'frozen_facts':[{'uid':u,'claim':c,'evidence':''} for u,c in facts]}


def test_v1_source_revision_still_rejects_a_fact_split():
    """v1 语义原样保留：只要 uid 集合变了就拒（这是 T3 的互斥固定点）。"""
    bad=s.admission_problems(revision_view(s.VERSION,SPLIT_TARGET_FACTS))
    assert [p['code'] for p in bad]==['verify_source_revision_context']


def test_v2_source_revision_allows_split_of_an_authorized_target():
    """v2：targets 域内一条事实拆成 ≥2 个 `父uid.后缀` 子 uid 应被准入。"""
    assert s.VERSION_V2!=s.VERSION
    assert not s.admission_problems(revision_view(s.VERSION_V2,SPLIT_TARGET_FACTS))


def test_v2_split_still_needs_the_independent_preservation_review():
    """拆分不豁免来源保留核验：记录仍按**父 targets** 同序回带。"""
    view=revision_view(s.VERSION_V2,SPLIT_TARGET_FACTS)
    novel='甲离开。乙辞职并离开。丙留下。'
    record={'source_preservation':{'target_uids':['f02'],'verdict':'supported',
                                   'evidence_spans':[novel],
                                   'reason':'只拆开已授权事实，未触碰其他主张'}}
    assert not s.problems(record,view,novel_text=novel)
    partial=copy.deepcopy(record)
    partial['source_preservation']['verdict']='partial'
    bad=s.problems(partial,view,novel_text=novel)
    assert [p['code'] for p in bad]==['source_preservation_failed']


@pytest.mark.parametrize('facts,targets',[
    # ⚠️ handfix_r5 的真实形态还包含 f09（不在 targets）被拆；v2 仍拒绝这条。
    # 这是产品标准决定（选 a 还是 b），本测试不偷偷放宽授权。
    # 未授权事实被拆
    ((('f01','甲离开。'),('f02.1','乙辞职。'),('f02.2','乙离开。'),
      ('f03.1','丙留下。'),('f03.2','丙继续留下。')),('f02',)),
    # 未授权事实的主张被改写（身份没变也不行）
    ((('f01','甲走了。'),('f02.1','乙辞职。'),('f02.2','乙离开。'),
      ('f03','丙留下。')),('f02',)),
    # 授权事实被删除，没有任何子 uid
    ((('f01','甲离开。'),('f03','丙留下。')),('f02',)),
    # 只换成一个子 uid（改名不是拆分）
    ((('f01','甲离开。'),('f02.1','乙辞职并离开。'),('f03','丙留下。')),('f02',)),
    # 子 uid 找不到授权父
    ((('f01','甲离开。'),('f02.1','乙辞职。'),('f02.2','乙离开。'),
      ('f03','丙留下。'),('f99.1','戊到场。')),('f02',)),
    # 子 uid 的后缀为空
    ((('f01','甲离开。'),('f02.','乙辞职。'),('f02.2','乙离开。'),
      ('f03','丙留下。')),('f02',)),
])
def test_v2_still_rejects_identity_changes_outside_the_authorized_split(facts,targets):
    bad=s.admission_problems(revision_view(s.VERSION_V2,facts,targets))
    assert bad and bad[0]['code']=='verify_source_revision_context'


def test_v2_still_allows_a_plain_correction_inside_targets():
    """不拆分时 v2 与 v1 一样：targets 内可改主张，其他事实不动。"""
    facts=(('f01','甲离开。'),('f02','乙离开。'),('f03','丙留下。'))
    assert not s.admission_problems(revision_view(s.VERSION_V2,facts))
    assert not s.admission_problems(revision_view(s.VERSION,facts))


def test_unknown_source_revision_version_is_rejected():
    bad=s.admission_problems(revision_view('source_revision.v9',SPLIT_TARGET_FACTS))
    assert [p['code'] for p in bad]==['verify_source_revision_context']
