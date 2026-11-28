import pytest

from step01_script_gen.quality_issues import _onscreen_prop_issues, parse_shot_ref
from step01_script_gen.constraints.weapon_state_consistency import _repair_text
from step01_script_gen.validation import _check_weapon_holder


@pytest.mark.parametrize('text', [
    '甲手持短刀，刀锋没入乙心口，甲仍持刀。',
    '甲手持短刀，乙的短刀已脱手。',
    '甲手持短刀，甲的剑已脱手。',
    '甲手持短刀，甲的短刀并未脱手。',
])
def test_legal_weapon_relations_are_not_release_conflicts(text):
    shot={'ff_desc':text,'char_layout':[{'name':'甲'},{'name':'乙'}],
          'frame_layouts':{'ff':[]}}
    assert not [i for i in _onscreen_prop_issues(shot,7) if i.code=='weapon_state_conflict']
    assert not _repair_text(text,'',['甲','乙'],time_sec=0,label='ff_desc')[1]


def test_same_owner_same_weapon_explicit_hold_and_release_conflicts():
    text='甲手持短刀，甲的短刀已脱手。'
    shot={'ff_desc':text,'char_layout':[{'name':'甲'}],'frame_layouts':{'ff':[]}}
    assert any(i.code=='weapon_state_conflict' for i in _onscreen_prop_issues(shot,7))
    assert _repair_text(text,'',['甲'],time_sec=0,label='ff_desc')[1]


@pytest.mark.parametrize('name', ['林远','阿列克谢','Alex'])
def test_named_wield_action_has_holder(name):
    text=f'{name}坐车夫座扬鞭，车轮扬起细尘。'
    assert not _check_weapon_holder([{'index':23,'ff_desc':text}])['issues']


@pytest.mark.parametrize('label', ['shot9','shot 23','shot13.5','镜头9'])
def test_checklist_shot_labels_keep_identity(label):
    expected=label.replace('shot','').replace('镜头','').strip()
    assert parse_shot_ref(f'{label}: temporal_plan 引文越界')==(expected,[])


@pytest.mark.parametrize('text', ['甲的短刀并未脱手。','乙的短刀已脱手。'])
def test_layout_release_does_not_borrow_negated_or_other_actor_state(text):
    from step01_script_gen.cross_field_checks import _possession_issues
    from step01_script_gen.constraints.weapon_state_consistency import _release_owner
    from step01_script_gen.constraints.kf_event_order import _prop_released_for
    shot={'ff_desc':text,'char_layout':[{'name':'甲'},{'name':'乙'}],
          'frame_layouts':{'ff':[{'name':'甲','props':['短刀']}]}}
    assert not _possession_issues(shot,7)
    assert _release_owner(text,'短刀',['甲','乙'],['甲'])!='甲'
    assert not _prop_released_for(text,'短刀','甲',['甲','乙'],['甲'])


def test_partial_prop_characters_do_not_establish_release():
    from step01_script_gen.constraints.weapon_state_consistency import _release_owner
    from step01_script_gen.constraints.kf_event_order import _prop_released_for
    text='甲低头，绳索脱手。'
    assert _release_owner(text,'缰绳',['甲'],['甲'])==''
    assert not _prop_released_for(text,'缰绳','甲',['甲'],['甲'])


def test_frame_identity_does_not_sort_text_without_layout():
    from step01_script_gen.quality_issues import _frame_texts
    from step01_script_gen.cross_field_checks import frame_text
    shot={'keyframes':[{'t_sec':3,'desc':'甲的短刀已脱手'},
                       {'t_sec':1,'desc':'甲手持短刀'}]}
    assert dict(_frame_texts(shot))['kf0']=='甲的短刀已脱手'
    assert frame_text(shot,'kf0')=='甲的短刀已脱手'


def test_orphan_weapon_keeps_exact_frame_repair_capability():
    from step01_script_gen.quality_issues import collect_structured_issues
    from step01_script_gen.repair_fields import allowed_repair_fields, path_allowed
    shot={'index':23,'keyframes':[{'desc':'一把匕首悬在半空','t_sec':1}],
          'frame_layouts':{'kfs':[[]]}}
    checked=_check_weapon_holder([shot])
    issues,failed,_=collect_structured_issues({'shots':[shot]},set(),set(),
        checklist_runner=lambda _: {'results':{'weapon_holder':checked}})
    target=[i for i in issues if i.code=='weapon_holder']
    assert not failed and len(target)==1
    assert target[0].shot_id==23 and target[0].frame_id=='kf0'
    allowed=allowed_repair_fields([],target,target_shot_id='shot_23',
                                  shots_by_id={'shot_23':shot})
    assert path_allowed('keyframes[0].desc',allowed)
    assert not path_allowed('ff_desc',allowed)
    assert not path_allowed('keyframes[1].desc',allowed)


def test_temporal_blocker_reaches_actual_repair_selection(monkeypatch):
    from step01_script_gen import main as m
    from step01_script_gen.repair_fields import allowed_repair_fields, path_allowed
    shot={'index':23,'temporal_plan':{'events':[{'source_quote':'引文'}]}}
    monkeypatch.setattr(m,'_run_prompt_checklist',lambda _: {'results':{
        'temporal_plan':{'issues':['shot23: temporal_plan 引文越界'],'warnings':[]}}})
    state=m._collect_repair_warnings({'shots':[shot]},'')
    assert state['shots']['shot_23']==['shot23: temporal_plan 引文越界']
    assert all('temporal_plan' not in message for message in state['shots'].get('global',[]))
    issue=m.Issue(**next(r for r in state['issues'] if r['code']=='temporal_plan'))
    allowed=allowed_repair_fields([], [issue], target_shot_id='shot_23',
                                  shots_by_id={'shot_23':shot})
    assert path_allowed('temporal_plan.events[0].source_quote',allowed)
    assert not path_allowed('narration_segment',allowed)


@pytest.mark.parametrize('text,released',[
    ('甲没有拖走钓杆',False),('甲未拖着钓杆离去',False),
    ('甲拖走钓杆',True),('甲拖着钓杆离去',True),
    ('甲的钓杆未脱手仍握着',False),
])
def test_prop_anchor_motion_fallback_preserves_negation(text,released):
    from step01_script_gen import prop_state_anchor as p
    script={'shots':[{'index':23,'ff_desc':text}]}
    assert bool(p.release_frames(script,'钓杆',{})) == released
    if not released:
        assert p._holding_clause(text,{'钓杆'})==text
