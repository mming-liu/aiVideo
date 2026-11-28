import copy

import pytest

from shared import presentation_contract as pc
from step01_script_gen import plan_content_repair as pcr
from tests.step01_script_gen.test_legacy_semantic import plan_for_narration,NOVEL

pytestmark=pytest.mark.v2_request


@pytest.mark.parametrize('mode,carrier',[
    (pc.MODE_LEGACY,'B1/旁白1'),(pc.MODE_LEGACY,'B2/台词1'),(pc.MODE_LEGACY,'B3/画面'),
    (pc.MODE_HYBRID,'B2/台词1'),(pc.MODE_HYBRID,'B3/画面')])
@pytest.mark.parametrize('action',[pcr.ACT_CREATE,pcr.ACT_BIND,pcr.ACT_REWRITE])
def test_selected_visual_binding_round_trips_under_each_representation(mode,carrier,action):
    plan=plan_for_narration()
    plan['shot_beats'].append('节拍：空仓门外景｜载体：画面')
    plan['required_facts'][0]=plan['required_facts'][0].rsplit('承载：',1)[0]+'承载：'+carrier
    view,bad=pc.view_from_plan(plan);assert not bad
    if mode==pc.MODE_HYBRID:
        view['mode']=mode
        view['narration_units']=[]
        view['beats'][0]['voice_ref']={'kind':pc.KIND_VISUAL,'beat_uid':'b01'}
        view['event_binding']='declared'
        for beat,event in zip(view['beats'],view['frozen_events']):
            beat['event_uids']=[event]
        view,bad=pc.view_from_plan(pc.plan_from_view(view),mode=mode);assert not bad
    before=copy.deepcopy(view)
    found=pcr.content_slots(view,[{'code':'verify_fact_unsupported','uid':'f01'}])
    assert found['slots']
    slots=found['slots'];request=pcr.slot_request(view,slots)
    assert request['binding_policy']==pc.fact_binding_policy(view)
    item={'slot_id':slots[0]['slot_id'],'action':action}
    if action==pcr.ACT_CREATE:
        item.update(text='周明在仓库收拾辞职物品。',insert_after_uid='b01',event_index=0)
    elif action==pcr.ACT_REWRITE:
        item.update(text='周明在仓库收拾辞职物品。',target_uid='b04')
    else:
        item['target_uid']='b04'
    answer={'schema':pcr.SLOT_ANSWER_SCHEMA,'answers':[item]}
    assert not pcr.answer_problems(answer,slots)
    delta,bad=pcr.compile_delta(view,slots,answer,novel_text=NOVEL)
    assert not bad and view==before
    carriers=delta['fact_bindings'][0]['carriers']
    assert len(carriers)==(1 if mode==pc.MODE_LEGACY else 2)
    if mode==pc.MODE_HYBRID:
        assert carriers[0]==view['fact_bindings'][0]['carriers'][0]
    candidate,bad,state=pc.apply_delta_view(view,delta,accept=lambda current:[])
    assert state['kind']=='applied' and not bad,(state,bad)
    restored,bad=pc.view_from_plan(pc.plan_from_view(candidate),mode=mode)
    assert not bad
    assert len(restored['fact_bindings'][0]['carriers'])==len(carriers)
    assert restored['dialogue_units']==view['dialogue_units']
    assert [u['text'] for u in restored['narration_units']]==[u['text'] for u in view['narration_units']]
    assert ('替换该事实原有绑定' in pcr.slot_prompt(request))==(mode==pc.MODE_LEGACY)


def test_legacy_slot_offers_voice_beat_as_a_reusable_visual_target():
    """2026-10-18 契约变更（用户裁决"允许同镜声画共存"）：承声节拍也是可选画面目标。

    改动前此处 `target_uids==[]`、只能新建；现在应能**复用**该节拍，且不丢旁白。
    """
    plan=plan_for_narration();plan['shot_beats']=plan['shot_beats'][:2]
    view,bad=pc.view_from_plan(plan);assert not bad
    slots=pcr.content_slots(view,[{'code':'verify_fact_unsupported','uid':'f01'}])['slots']
    assert pcr.ACT_REWRITE in slots[0]['allowed_actions']
    assert 'b01' in [str(x) for x in slots[0]['target_uids']]
    answer={'schema':pcr.SLOT_ANSWER_SCHEMA,'answers':[{'slot_id':slots[0]['slot_id'],
        'action':pcr.ACT_REWRITE,'target_uid':'b01','text':'周明收拾辞职物品。'}]}
    delta,bad=pcr.compile_delta(view,slots,answer,novel_text=NOVEL);assert not bad
    assert not [b for b in (delta.get('upsert') or {}).get('beats') or []
                if str(b.get('uid') or '').startswith('new:')]
    candidate,bad,state=pc.apply_delta_view(view,delta,accept=lambda current:[])
    assert not bad and state['kind']=='applied'
    assert candidate['narration_units']==view['narration_units']


def test_legacy_visual_on_voice_beat_applies_and_keeps_the_voice():
    """2026-10-18 契约变更：一镜可同时承声与承载事实画面（声音归属与事实承载分别建模）。"""
    view,bad=pc.view_from_plan(plan_for_narration());assert not bad
    sent=[]
    delta={'schema':pc.DELTA_SCHEMA,'rule_version':pc.DELTA_RULE_VERSION,
        'base_plan_hash':pc.view_digest(view),'fact_basis_hash':pc.fact_basis_digest(view),
        'fact_bindings':[{'fact_uid':'f01','carriers':[{'kind':pc.KIND_VISUAL,'beat_uid':'b01'}]}]}
    candidate,bad,state=pc.apply_delta_view(view,delta,accept=lambda current:sent.append(current) or [])
    assert state['kind']=='applied' and not bad and len(sent)==1,(state,bad)
    assert candidate['narration_units']==view['narration_units']
    restored,bad=pc.view_from_plan(pc.plan_from_view(candidate));assert not bad
    carriers=[c for b in restored['fact_bindings'] if b['fact_uid']=='f01'
              for c in b['carriers']]
    assert any(pc.ref_uid(c)=='b01' for c in carriers)
    assert any(str((b.get('voice_ref') or {}).get('uid'))=='n01'
               for b in restored['beats'] if b['uid']=='b01')


def test_actual_planner_accepts_voice_beat_choice_at_answer_contract(monkeypatch):
    import json
    from types import SimpleNamespace
    from _text_gen.models import TextGenerationResult
    from step01_script_gen import outline
    from step01_script_gen.plan_revision import RevisionBudget
    from tests.step01_script_gen.test_legacy_semantic import narration_response,verify_through_service
    raw=narration_response(verdict='partial')
    raw['source_support'][0].update(verdict='supported',evidence_spans=[NOVEL])
    plan=verify_through_service(monkeypatch,plan_for_narration(),raw)
    answer={'schema':pcr.SLOT_ANSWER_SCHEMA,'answers':[{'slot_id':'slot:fact:f01',
        'action':pcr.ACT_REWRITE,'target_uid':'b01','text':'周明收拾辞职物品。'}]}
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=lambda request:
        TextGenerationResult(success=True,text=json.dumps(answer,ensure_ascii=False))))
    result=outline.revise_plan_once(plan,outline.plan_acceptance_problems(plan,NOVEL),
        RevisionBudget(max_calls=1,deadline_sec=60),novel_text=NOVEL)
    assert result['kind']!='illegal'
    assert result.get('evidence',{}).get('failure_stage')!='answer_contract'


@pytest.mark.parametrize('change',['remove_last_voice_placement'])
def test_other_legacy_channel_conflicts_fail_before_acceptance(change):
    view,bad=pc.view_from_plan(plan_for_narration());assert not bad
    delta={'schema':pc.DELTA_SCHEMA,'rule_version':pc.DELTA_RULE_VERSION,
        'base_plan_hash':pc.view_digest(view),'fact_basis_hash':pc.fact_basis_digest(view)}
    if change=='hook_visual_on_voice':
        delta['hook_bindings']={'carriers':[{'kind':pc.KIND_VISUAL,'beat_uid':'b01'}],'spoken_uids':[]}
        expected='view_binding_legacy_visual_mismatch'
    else:
        beat=copy.deepcopy(view['beats'][0]);beat['voice_ref']={'kind':pc.KIND_VISUAL,'beat_uid':'b01'}
        delta['upsert']={'beats':[beat]}
        expected='view_voice_unit_unplaced'
    accepted=[]
    candidate,bad,state=pc.apply_delta_view(view,delta,accept=lambda current:accepted.append(current) or [])
    assert state['kind']=='illegal' and not accepted
    assert expected in [p['code'] for p in bad]


def test_slot_request_includes_unslotted_facts_on_rewritable_targets():
    plan=plan_for_narration()
    plan['required_facts'].append('仓库仍旧正常营业。｜原文证据：“仓库仍旧正常营业。”｜承载：B3/画面')
    view,bad=pc.view_from_plan(plan);assert not bad
    slots=pcr.content_slots(view,[{'code':'verify_fact_unsupported','uid':'f01'}])['slots']
    request=pcr.slot_request(view,slots)
    assert [fact['uid'] for fact in request['protected_facts']]==['f02']
    assert request['protected_facts'][0]['claim']=='仓库仍旧正常营业。'
    assert request['protected_facts'][0]['carriers']==view['fact_bindings'][1]['carriers']


def test_legacy_hook_visual_on_voice_beat_applies_and_keeps_the_voice():
    """与 fact 同一契约：钩子的画面也可落在承声节拍上，声音不被挤掉。"""
    view,bad=pc.view_from_plan(plan_for_narration());assert not bad
    sent=[]
    delta={'schema':pc.DELTA_SCHEMA,'rule_version':pc.DELTA_RULE_VERSION,
        'base_plan_hash':pc.view_digest(view),'fact_basis_hash':pc.fact_basis_digest(view),
        'hook_bindings':{'carriers':[{'kind':pc.KIND_VISUAL,'beat_uid':'b01'}],'spoken_uids':[]}}
    candidate,bad,state=pc.apply_delta_view(view,delta,accept=lambda current:sent.append(current) or [])
    assert state['kind']=='applied' and not bad and len(sent)==1,(state,bad)
    assert candidate['narration_units']==view['narration_units']
