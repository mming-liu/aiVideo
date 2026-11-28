import copy
import json
from types import SimpleNamespace

import pytest

from step01_script_gen import plan_coherence as c
from tests.step01_script_gen.test_legacy_semantic import (
    NOVEL, plan_for_narration, narration_response, verify_through_service)
from shared.presentation_contract import view_from_plan

pytestmark=pytest.mark.v2_request


def verdict(view, *, supported=True):
    return {'beat_uids':[b['uid'] for b in view['beats']],
            'dependencies':[{'beat_uid':b['uid'],'requires_before':[],
                             'reason':'显式夹具：无必要先行依赖'} for b in view['beats']],
            'verdict':'supported' if supported else 'contradicted',
            'conflict_beats':[] if supported else [view['beats'][1]['uid']],
            'evidence_spans':[NOVEL], 'reason':'时间、主体与前提成立' if supported else '回答先于提问者到场'}


def test_all_inverted_dependencies_are_returned_together():
    view,_=view_from_plan(c.mark_plan(plan_for_narration()))
    raw=verdict(view)
    raw['dependencies'][0]['requires_before']=['b03']
    raw['dependencies'][1]['requires_before']=['b03']
    bad=c.problems({'coherence':raw},view,NOVEL)
    assert [(p['code'],p['uid']) for p in bad]==[
        ('verify_plan_coherence','b01'),('verify_plan_coherence','b02')]


def test_coherence_action_table_does_not_mix_declared_purpose_or_source_into_action():
    view,_=view_from_plan(c.mark_plan(plan_for_narration()))
    view['beats'][0].update(purpose='只是假定目的XYZ',evidence='只是假定出处ABC',event_uids=['只是假定归属DEF'])
    prompt=c.prompt(view)
    assert all(text not in prompt for text in ('只是假定目的XYZ','只是假定出处ABC','只是假定归属DEF'))
    assert view['beats'][0]['text'] in prompt


@pytest.mark.parametrize('key,value',[
    ('verdict',None),('reason',' '),('beat_uids',None),('conflict_beats',['unknown']),
    ('beat_uids',['b02','b01','b03']),('evidence_spans',['不存在的原文']),
])
def test_bad_coherence_record_is_protocol_error(key,value):
    view,_=view_from_plan(c.mark_plan(plan_for_narration()))
    raw=verdict(view);raw[key]=value
    assert c.problems({'coherence':raw},view,NOVEL)
    assert all(p['code'] in c.PROTOCOL_CODES for p in c.problems({'coherence':raw},view,NOVEL))


@pytest.mark.parametrize('version',[None,'','unknown'])
def test_unknown_version_rejected_on_view_admission(version):
    plan=c.mark_plan(plan_for_narration());plan[c.FIELD]=version
    from shared.presentation_contract import view_admission_problems
    view,bad=view_from_plan(plan)
    bad=bad or view_admission_problems(view)
    assert bad and any(p['code']=='verify_coherence_record' for p in bad)


@pytest.mark.parametrize('supported',[True,False])
def test_actual_service_parser_and_plan_gate_keep_coherence(monkeypatch,supported):
    from step01_script_gen import outline
    plan=c.mark_plan(plan_for_narration());view,_=view_from_plan(plan)
    raw=narration_response();raw['coherence']=verdict(view,supported=supported)
    checked=verify_through_service(monkeypatch,plan,raw)
    issues=outline.plan_acceptance_problems(checked,NOVEL)
    assert checked['plan_verification']['coherence']==raw['coherence']
    # S10（2026-11-21 用户裁决·方案1）：coherence 轴整体降为 advisory ⇒ 单次负面
    # 不再进硬门；记录仍保留真实结论，并带 stability.advisory_coherence 标记。
    _codes=[p['code'] for p in issues]
    assert 'verify_plan_coherence' not in _codes
    assert not _codes
    _st=checked['plan_verification'].get('stability') or {}
    assert _st.get('advisory_coherence') is True, _st


def test_prompt_contains_complete_order_and_real_actions():
    from step01_script_gen.content_verify import build_verification_prompt
    view,_=view_from_plan(c.mark_plan(plan_for_narration()))
    text=build_verification_prompt(view,novel_text=NOVEL)
    assert '整份计划的前后依赖' in text and '回忆/倒叙允许' in text
    assert '合理可推知的过渡动作可以省略' in text
    assert '不能仅因未逐项展示原文操作而判负' in text
    assert all(b['uid'] in text and b['text'] in text for b in view['beats'])


@pytest.mark.parametrize('value',[None,{},''])
def test_missing_or_null_coherence_cannot_be_reused(monkeypatch,value):
    from shared import content_verification as cv
    from step01_script_gen import content_verify
    plan=c.mark_plan(plan_for_narration());view,_=view_from_plan(plan)
    checked=verify_through_service(monkeypatch,plan,narration_response())
    record=copy.deepcopy(checked['plan_verification'])
    if value is None:
        record.pop('coherence')
    else:
        record['coherence']=value
    assert not cv.record_basis_matches(record,plan)
    ctl=cv.VerificationController(verify=lambda *a:None,
        novel_digest=content_verify.novel_identity(NOVEL)['digest'])
    assert not ctl.seed(record['plan_digest'],record,view=view)
    assert not ctl.seed(record['plan_digest'],record,plan=plan)
    assert not ctl.records


def test_hybrid_plan_uses_same_whole_plan_record_contract():
    from tests.step01_script_gen.test_plan_content_slots import _fixture
    from shared.content_verification import record_protocol_problems
    fixture=_fixture();plan=c.mark_plan(fixture['plan']);view,bad=view_from_plan(plan)
    assert not bad
    record=copy.deepcopy(plan['plan_verification'])
    assert c.protocol_problems(record,view)
    record['coherence']={'beat_uids':[b['uid'] for b in view['beats']],
        'dependencies':[{'beat_uid':b['uid'],'requires_before':[],
                         'reason':'显式夹具：无依赖'} for b in view['beats']],
        'verdict':'supported','conflict_beats':[], 'evidence_spans':[],
        'reason':'显式夹具：整体顺序通过；事实承载负面保留'}
    assert not c.protocol_problems(record,view)
    record['coherence']['beat_uids'].reverse()
    assert c.protocol_problems(record,view)[0]['code']=='verify_coherence_identity'


@pytest.mark.parametrize('uids',[['b01','b02','b03'],['arrival','answer','departure']])
def test_code_rejects_inverted_dependency_even_if_judge_says_supported(uids):
    view,_=view_from_plan(c.mark_plan(plan_for_narration()))
    for beat,uid in zip(view['beats'],uids):beat['uid']=uid
    raw=verdict(view)
    raw['dependencies'][0]['requires_before']=[uids[1]]
    raw['dependencies'][0]['reason']='本动作需要另一主体先到场'
    issues=c.problems({'coherence':raw},view,NOVEL)
    assert len(issues)==1 and issues[0]['code']=='verify_plan_coherence'
    assert issues[0]['uid']==uids[0]
    raw['dependencies'][0]['requires_before']=[]
    raw['dependencies'][1]['requires_before']=[uids[0]]
    assert not c.problems({'coherence':raw},view,NOVEL)


@pytest.mark.parametrize('rows',[
    None,[],[{'beat_uid':'b01','requires_before':[],'reason':'无'}],
    [{'beat_uid':'b01','requires_before':['b01'],'reason':'自引'}],
])
def test_dependency_table_cannot_be_missing_partial_or_self_referential(rows):
    view,_=view_from_plan(c.mark_plan(plan_for_narration()));raw=verdict(view)
    raw['dependencies']=rows
    assert c.protocol_problems({'coherence':raw},view)


@pytest.mark.parametrize('by_dependency',[False,True])
def test_actual_planner_reorders_and_reverifies_candidate(monkeypatch,by_dependency):
    from shared import content_verification as cv, presentation_contract as pc
    from step01_script_gen import outline,content_verify
    from step01_script_gen.plan_revision import RevisionBudget
    from _text_gen.models import TextGenerationResult
    plan=c.mark_plan(plan_for_narration());view,_=view_from_plan(plan)
    original_response=narration_response();original_response['coherence']=verdict(view,supported=by_dependency)
    if by_dependency:
        original_response['coherence']['dependencies'][0]['requires_before']=['b03']
    plan=verify_through_service(monkeypatch,plan,original_response)
    # S10（2026-11-21 用户裁决·方案1）：coherence 轴降为 advisory，验收不再硬阻断；
    # 但记录里的负面结论/依赖冲突仍可**显式**取出交修订——本用例继续验证"拿到这条
    # 问题后，真实修订入口会重排并在新候选上重验"。
    assert 'verify_plan_coherence' not in [
        p['code'] for p in outline.plan_acceptance_problems(plan,NOVEL)]
    issues=c.problems(plan['plan_verification'],view,NOVEL)
    assert [p['code'] for p in issues]==['verify_plan_coherence']
    requests=[];verified=[]
    def generate(request):
        requests.append(request)
        delta={'schema':'plan_revision.delta.v1','rule_version':'plan_revision.delta.v1',
               'base_plan_hash':pc.view_digest(view),'fact_basis_hash':pc.fact_basis_digest(view),
               'order':{'beats':['b03','b01','b02']}}
        return TextGenerationResult(success=True,text=json.dumps(delta))
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=generate))
    def verify(new_view,digest):
        verified.append((copy.deepcopy(new_view),digest))
        response=narration_response();response['coherence']=verdict(new_view)
        if by_dependency:
            response['coherence']['dependencies'][1]['requires_before']=['b01']
        return dict(response,schema=cv.SCHEMA_VERIFICATION,status='ok')
    controller=cv.VerificationController(verify=verify,require_coherence=True,
                novel_digest=content_verify.novel_identity(NOVEL)['digest'],
                stability_confirmation=True)
    result=outline.revise_plan_once(plan,issues,RevisionBudget(max_calls=1,deadline_sec=60),
                                    novel_text=NOVEL,verification=controller)
    assert result['kind']=='applied',result
    after=result['plan']
    assert len(requests)==1 and len(verified)==1
    assert [b['text'] for b in verified[0][0]['beats']]==[
        view['beats'][i]['text'] for i in (2,0,1)]
    assert after['plan_revision']['mapping']['b03']=='b03'
    assert 'B2/旁白1' in after['required_facts'][0]
    assert not outline.plan_acceptance_problems(after,NOVEL)
    assert after['plan_verification']['plan_digest']==cv.plan_digest_of(after)
