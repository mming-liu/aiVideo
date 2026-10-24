"""Legacy planning uses semantic verdicts; evidence is lossless metadata."""
import copy
import json
from types import SimpleNamespace

import pytest

from shared.fact_metadata import (
    LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION, split_claim_evidence,
)


@pytest.mark.parametrize("wrapped", [
    '“甲大喊：“救命啊！”随后跑开。”',
    '"甲大喊："救命啊！"随后跑开。"',
    '「甲大喊：“救命啊！”随后跑开。」',
    '甲大喊：“救命啊！”随后跑开。',
])
def test_evidence_preserves_nested_quotes(wrapped):
    expected = wrapped[1:-1] if wrapped[0] in '“"「' else wrapped
    fact = '甲“呼救”｜原文证据：' + wrapped + '｜承载：B1/台词1'
    from shared.presentation_contract import _split_claim_evidence
    from step01_script_gen.plan_feasibility import fact_evidence_text
    assert split_claim_evidence(fact) == ('甲“呼救”', expected)
    assert _split_claim_evidence(fact) == ('甲“呼救”', expected)
    assert fact_evidence_text(fact) == expected


def plan_for_narration():
    return {
        'main_events': ['周明辞职', '周明离开仓库', '仓库仍在营业'], 'climax': '周明离开仓库',
        'required_facts': ['周明去年辞职。｜原文证据：“仓库管理员周明去年辞职。仓库仍旧正常营业。”｜承载：B1/旁白1'],
        'causal_chain': ['周明辞职后离开仓库'],
        'shot_beats': ['节拍：周明收拾东西｜载体：旁白1',
                       '节拍：周明告别同事｜载体：台词1',
                       '节拍：工人继续工作｜载体：画面'],
        'dialogue_lines': ['再见。'], 'omittable': [], 'hook_spoken': [],
        'time_budget': '动作约75秒', 'characters': ['周明'],
        'emotional_arc': '平静', 'key_scenes': ['仓库'],
        'character_motives': ['周明：离职'], 'end_hook': '仓库仍在营业',
        'hook_carrier': '',
        'narration_lines': [{'text': '仓库管理员周明去年辞职。',
                             'source': '仓库管理员周明去年辞职。', 'fact_refs': [1]}],
        LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION,
    }


NOVEL = '仓库管理员周明去年辞职。仓库仍旧正常营业。周明说：“再见。”'


def wire_body(body,request):
    """Explicit fixture citation selection for the production reference transport."""
    if 'source_references.v1' not in request.user_prompt:
        return body
    import hashlib
    body=copy.deepcopy(body)
    ref='src_'+hashlib.sha256(NOVEL.encode('utf-8')).hexdigest()[:16]+'_0'
    rows=list(body.get('source_support') or [])
    rows.extend(body[key] for key in ('coherence','source_preservation') if isinstance(body.get(key),dict))
    for row in rows:
        if isinstance(row,dict) and isinstance(row.get('evidence_spans'),list):
            row['evidence_spans']=[ref if span==NOVEL else span for span in row['evidence_spans']]
    return body


@pytest.mark.parametrize('label', ['旁白：', '画外音：', 'Narrator:'])
def test_declared_narration_is_not_extra_dialogue(label):
    from step01_script_gen import plan_feasibility as pf
    plan = plan_for_narration()
    script = {'shots': [
        {'index': 1, 'plan_refs': {'beats': [1], 'lines': []},
         'narration_segment': label + plan['narration_lines'][0]['text']},
        {'index': 2, 'plan_refs': {'beats': [2], 'lines': [1]}, 'narration_segment': '周明：再见。'},
        {'index': 3, 'plan_refs': {'beats': [3], 'lines': []}, 'narration_segment': ''}]}
    assert pf.check_script_dialogue_integrity(script, plan) == (True, [])
    pf.bind_plan_identity(script, plan)
    assert pf.check_event_contract(script, plan, require_declaration=True)[0]
    # Neither a narrator label nor a plan-text prefix authorizes new speech.
    for text in (label + '天气真好。', label + plan['narration_lines'][0]['text'] + '快来帮忙。'):
        bad = copy.deepcopy(script)
        bad['shots'][0]['narration_segment'] = text
        assert any(p['reason'] == 'extra_dialogue' for p in pf.check_script_dialogue_integrity(bad, plan)[1])
    wrong = copy.deepcopy(script)
    wrong['shots'][0]['plan_refs']['beats'] = [3]
    assert any(p['reason'] == 'extra_dialogue' for p in pf.check_script_dialogue_integrity(wrong, plan)[1])


def test_legacy_delta_wire_preserves_single_carrier_contract():
    from step01_script_gen import outline
    from shared.presentation_contract import view_from_plan, delta_request
    view, bad = view_from_plan(plan_for_narration())
    assert not bad
    request = delta_request(view)
    schema = outline.replan_delta_schema_for_request(request)
    carriers = schema['schema']['properties']['fact_bindings']['items']['properties']['carriers']
    assert carriers['maxItems'] == 1
    hybrid = copy.deepcopy(request)
    hybrid['view']['mode'] = 'screen_hybrid'
    hybrid_schema = outline.replan_delta_schema_for_request(hybrid)
    assert 'maxItems' not in hybrid_schema['schema']['properties']['fact_bindings']['items']['properties']['carriers']


def test_verification_prompt_distinguishes_thought_from_spoken_quote():
    from step01_script_gen.content_verify import build_verification_prompt
    from shared.presentation_contract import view_from_plan
    view, bad = view_from_plan(plan_for_narration())
    assert not bad
    prompt = build_verification_prompt(view, novel_text=NOVEL)
    assert '明确未说出口的思想、内心独白属于 novel_narration' in prompt
    assert '明确说出口的话属于 novel_speech' in prompt


def narration_response(*, claim='周明去年辞职。', verdict='supported',
                       text='仓库管理员周明去年辞职。', source_kind='novel_narration'):
    """Explicit fixture verdicts, not a lexical oracle or model simulation."""
    supported = verdict == 'supported'
    return {
        'coherence': {'beat_uids': ['b01','b02','b03'], 'verdict': 'supported',
                      'dependencies': [{'beat_uid':uid,'requires_before':[], 'reason':'显式无依赖夹具'}
                                       for uid in ['b01','b02','b03']],
                      'conflict_beats': [], 'evidence_spans': [NOVEL],
                      'reason': '夹具指定：辞职、告别与继续营业无前提冲突'},
        'source_support': [{'fact_uid': 'f01', 'verdict': verdict,
                            'evidence_spans': [NOVEL] if supported else [],
                            'reason': '' if supported else '主体或命题不成立'}],
        'facts': [{'fact_uid': 'f01', 'verdict': verdict,
                   'support_spans': [text] if supported else [],
                   'missing': '' if supported else '没有表达完整主张',
                   'suggested_carriers': [],
                   'obligations': [{'obligation_id': 'claim', 'text': claim,
                                    'verdict': verdict,
                                    'evidence': [{'carrier_uid': 'n01', 'exhibitor_span': text}] if supported else [],
                                    'missing': '' if supported else '没有表达完整主张',
                                    'reason': ''}]}],
        'voice_units': [{'unit_uid': 'd01', 'verdict': 'supported',
                         'source_kind': 'novel_speech', 'speaker': '周明',
                         'echo_text': '再见。', 'reason': ''},
                        {'unit_uid': 'n01', 'verdict': 'supported',
                         'source_kind': source_kind, 'speaker': '',
                         'echo_text': text, 'reason': ''}],
    }


def verify_through_service(monkeypatch, plan, response):
    from step01_script_gen import outline, content_verify
    from shared.presentation_contract import view_from_plan
    from shared.content_verification import VerificationController
    from _text_gen.models import TextGenerationResult
    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(
        generate=lambda request: TextGenerationResult(success=True, text=json.dumps(response, ensure_ascii=False))))
    identity = content_verify.novel_identity(NOVEL)
    controller = VerificationController(
        verify=lambda view, digest: content_verify.verify_view_with_service(
            view, digest, novel_text=NOVEL, novel_digest=identity['digest']),
        novel_digest=identity['digest'])
    assert view_from_plan(plan)[0] is not None
    return content_verify.attach_verification(plan, controller)


def test_legal_narration_through_real_parser_and_pregeneration(monkeypatch, tmp_path):
    from step01_script_gen import outline, plan_feasibility as pf
    plan = verify_through_service(monkeypatch, plan_for_narration(), narration_response())
    assert outline.plan_acceptance_problems(plan, NOVEL) == []
    pf.check_plan_before_generation(plan, chapter_dir=tmp_path, novel_text=NOVEL)
    # Additional source context is not an additional frozen claim.
    assert '仓库仍旧正常营业' not in plan['narration_lines'][0]['text']


@pytest.mark.parametrize('claim', ['李明去年辞职。', '周明去年没有辞职。'])
def test_wrong_subject_or_negation_cannot_pass(monkeypatch, tmp_path, claim):
    from step01_script_gen import outline, plan_feasibility as pf
    plan = plan_for_narration()
    plan['required_facts'][0] = claim + '｜' + plan['required_facts'][0].split('｜', 1)[1]
    plan = verify_through_service(monkeypatch, plan, narration_response(claim=claim, verdict='contradicted'))
    assert 'fact_basis_invalid' in [p['code'] for p in outline.plan_acceptance_problems(plan, NOVEL)]
    with pytest.raises(pf.PlanContractInvalidError):
        pf.check_plan_before_generation(plan, chapter_dir=tmp_path, novel_text=NOVEL)


def test_missing_action_rejected_and_missing_record_fails_closed(monkeypatch):
    from step01_script_gen import outline
    plan = plan_for_narration()
    plan['narration_lines'][0]['text'] = '仓库管理员周明'
    checked = verify_through_service(monkeypatch, plan, narration_response(verdict='partial', text='仓库管理员周明'))
    assert outline.plan_acceptance_problems(checked, NOVEL)
    assert 'verify_record_missing' in [p['code'] for p in outline.plan_acceptance_problems(plan, NOVEL)]


def test_record_cannot_follow_changed_candidate_or_novel(monkeypatch):
    from step01_script_gen import outline
    plan = verify_through_service(monkeypatch, plan_for_narration(), narration_response())
    changed = copy.deepcopy(plan)
    changed['required_facts'][0] = changed['required_facts'][0].replace('周明', '李明')
    assert 'verify_digest_mismatch' in [p['code'] for p in outline.plan_acceptance_problems(changed, NOVEL)]
    assert 'verify_novel_digest_mismatch' in [p['code'] for p in outline.plan_acceptance_problems(plan, NOVEL + '次年复职。')]


def test_narration_cannot_use_a_dialogue_source_verdict(monkeypatch):
    from step01_script_gen import outline
    checked = verify_through_service(monkeypatch, plan_for_narration(), narration_response(source_kind='novel_speech'))
    assert 'verify_voice_not_speech' in [p['code'] for p in outline.plan_acceptance_problems(checked, NOVEL)]


@pytest.mark.parametrize('policy_field',[LEGACY_ACCEPTANCE_FIELD,'plan_coherence_version'])
def test_generation_and_cache_require_current_semantic_policy(monkeypatch, tmp_path, policy_field):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    calls = []
    raw = plan_for_narration()
    raw.pop(LEGACY_ACCEPTANCE_FIELD)

    def generate(request):
        name = request.json_schema['name']
        calls.append(name)
        body = narration_response() if name == 'content_verification' else raw
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE', 'legacy_audio')
    cache = tmp_path / 'outline.json'
    first = outline.generate_outline_result(NOVEL, cache_path=cache)
    assert first['executable'], first['acceptance']
    assert calls.count('content_verification') == 1
    assert first['plan'][LEGACY_ACCEPTANCE_FIELD] == LEGACY_ACCEPTANCE_VERSION
    calls.clear()
    second = outline.generate_outline_result(NOVEL, cache_path=cache)
    assert second['executable'] and second['cache']['hit']
    assert second['cache']['verification_reused']
    assert calls == []
    # Even a matching outer cache key cannot grant the new policy to old content.
    stored = json.loads(cache.read_text(encoding='utf-8'))
    stored['outline'].pop(policy_field)
    cache.write_text(json.dumps(stored, ensure_ascii=False), encoding='utf-8')
    third = outline.generate_outline_result(NOVEL, cache_path=cache)
    assert third['executable'] and not third['cache']['hit']
    assert calls.count('content_verification') == 1


def test_full_revision_cannot_remove_local_acceptance_policy():
    from step01_script_gen.outline import revise_plan_full
    from step01_script_gen.plan_coherence import mark_plan,FIELD,VERSION
    baseline = mark_plan(plan_for_narration())
    candidate = copy.deepcopy(baseline)
    candidate.pop(LEGACY_ACCEPTANCE_FIELD)
    candidate.pop(FIELD)
    result, why, _ = revise_plan_full(baseline, [], NOVEL, response=candidate)
    assert result is not None, why
    assert result[LEGACY_ACCEPTANCE_FIELD] == LEGACY_ACCEPTANCE_VERSION
    assert result[FIELD] == VERSION


def test_legal_delta_repairs_missing_action_and_keeps_verification(monkeypatch, tmp_path):
    from step01_script_gen import outline, content_verify, plan_feasibility as pf
    from shared.presentation_contract import view_from_plan, view_digest, fact_basis_digest
    from shared.content_verification import VerificationController
    from _text_gen.models import TextGenerationResult
    baseline = plan_for_narration()
    baseline['narration_lines'][0]['text'] = '仓库管理员周明'
    view, bad = view_from_plan(baseline)
    assert not bad
    unit = dict(view['narration_units'][0], text='仓库管理员周明去年辞职。')
    delta = {'schema': 'plan_revision.delta.v1', 'rule_version': 'plan_revision.delta.v1',
             'base_plan_hash': view_digest(view), 'fact_basis_hash': fact_basis_digest(view),
             'upsert': {'narration_units': [unit]}}
    requests = []

    def generate(request):
        requests.append(request)
        body = narration_response() if request.json_schema['name'] == 'content_verification' else delta
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    identity = content_verify.novel_identity(NOVEL)
    ctl = VerificationController(
        verify=lambda candidate, digest: content_verify.verify_view_with_service(
            candidate, digest, novel_text=NOVEL, novel_digest=identity['digest']),
        novel_digest=identity['digest'])
    corrected, why, _, evidence = outline._replan_by_view(
        baseline, [], NOVEL, None, evidence={}, mode='legacy_audio',
        protocol=outline.ROUTE_VIEW_DELTA, verification=ctl)
    assert corrected is not None, why
    assert evidence['residuals'] == 0
    assert corrected[LEGACY_ACCEPTANCE_FIELD] == LEGACY_ACCEPTANCE_VERSION
    assert corrected['required_facts'] == baseline['required_facts']
    assert corrected['narration_lines'][0]['text'] == '仓库管理员周明去年辞职。'
    assert baseline['narration_lines'][0]['text'] == '仓库管理员周明'
    assert outline.plan_acceptance_problems(corrected, NOVEL) == []
    pf.check_plan_before_generation(corrected, chapter_dir=tmp_path, novel_text=NOVEL)
    assert len(requests) == 2


@pytest.mark.parametrize('claim,dialogue,novel', [
    ('林雨惊恐呼救。', '救命啊！', '林雨惊恐地大喊：“救命啊！”旁边有人奔跑。'),
    ('陈启威胁林雨，如果报官就脱不了身。', '你敢报官，就休想脱身。',
     '陈启转身向林雨说：“你敢报官，就休想脱身。”他随后走出屋子。'),
])
def test_short_dialogue_does_not_have_to_repeat_all_source_context(monkeypatch, tmp_path, claim, dialogue, novel):
    from step01_script_gen import outline, content_verify, plan_feasibility as pf
    from shared.content_verification import VerificationController
    from _text_gen.models import TextGenerationResult
    plan = plan_for_narration()
    plan['required_facts'] = [claim + '｜原文证据：“' + novel + '”｜承载：B1/台词1']
    plan['narration_lines'] = []
    plan['dialogue_lines'] = [dialogue]
    plan['shot_beats'][0] = '节拍：林雨听到声音｜载体：台词1'
    plan['shot_beats'][1] = '节拍：门外有人走过｜载体：画面'
    raw = narration_response(claim=claim)
    raw['source_support'][0]['evidence_spans'] = [novel]
    raw['facts'][0]['support_spans'] = [dialogue]
    raw['facts'][0]['obligations'][0]['evidence'] = [{'carrier_uid': 'd01', 'exhibitor_span': dialogue}]
    raw['voice_units'] = [{'unit_uid': 'd01', 'verdict': 'supported', 'source_kind': 'novel_speech',
                           'speaker': '林雨' if dialogue == '救命啊！' else '陈启',
                           'echo_text': dialogue, 'reason': ''}]
    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=lambda req:
        TextGenerationResult(success=True, text=json.dumps(raw, ensure_ascii=False))))
    digest = content_verify.novel_identity(novel)['digest']
    ctl = VerificationController(verify=lambda view, identity: content_verify.verify_view_with_service(
        view, identity, novel_text=novel, novel_digest=digest), novel_digest=digest)
    checked = content_verify.attach_verification(plan, ctl)
    assert outline.plan_acceptance_problems(checked, novel) == []
    pf.check_plan_before_generation(checked, chapter_dir=tmp_path, novel_text=novel)


@pytest.mark.parametrize('version', [None, '', 'unknown'])
def test_unknown_acceptance_version_is_rejected(version):
    from step01_script_gen.outline import plan_acceptance_problems
    plan = plan_for_narration()
    plan[LEGACY_ACCEPTANCE_FIELD] = version
    assert 'planning_acceptance_version_invalid' in [p['code'] for p in plan_acceptance_problems(plan, NOVEL)]


@pytest.mark.parametrize('body', ['B6: 画面：周明离开仓库', '画面：周明离开仓库', '节拍：  '])
def test_unreadable_body_cannot_be_projected_to_empty_text(body):
    from shared.presentation_contract import view_from_plan
    from step01_script_gen.outline import outline_structure_problems
    plan = plan_for_narration()
    plan['shot_beats'][0] = body + '｜本镜信息目的：周明辞职｜载体：旁白1'
    assert 'view_beat_text_blank' in [p['code'] for p in outline_structure_problems(plan)]
    view, bad = view_from_plan(plan)
    assert view is None
    assert 'view_beat_text_blank' in [p['code'] for p in bad]
    assert plan['shot_beats'][0].startswith(body)


def test_delta_cannot_erase_a_beat_body():
    from shared.presentation_contract import (view_from_plan, view_digest, fact_basis_digest,
                                              apply_delta_view)
    plan = plan_for_narration()
    view, bad = view_from_plan(plan)
    assert not bad
    delta = {'schema': 'plan_revision.delta.v1', 'rule_version': 'plan_revision.delta.v1',
             'base_plan_hash': view_digest(view), 'fact_basis_hash': fact_basis_digest(view),
             'upsert': {'beats': [dict(view['beats'][0], text='')]}}
    changed, bad, result = apply_delta_view(view, delta, mode='legacy_audio')
    assert changed is None and result['kind'] == 'illegal'
    assert 'view_beat_text_blank' in [p['code'] for p in bad]
    assert view['beats'][0]['text'] == '周明收拾东西'


def test_first_version_format_error_is_returned_before_verification(monkeypatch, tmp_path):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    bad = plan_for_narration()
    bad.pop(LEGACY_ACCEPTANCE_FIELD)
    bad['shot_beats'][0] = 'B18: 画面：周明收拾东西｜载体：旁白1'
    good = plan_for_narration()
    good.pop(LEGACY_ACCEPTANCE_FIELD)
    initial = [bad, good]
    calls = []

    def generate(request):
        name = request.json_schema['name']
        calls.append((name, request.user_prompt))
        body = narration_response() if name == 'content_verification' else initial.pop(0)
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE', 'legacy_audio')
    result = outline.generate_outline_result(NOVEL, cache_path=tmp_path / 'outline.json')
    assert result['executable'], result['acceptance']
    assert [name for name, _ in calls] == ['chapter_outline', 'chapter_outline', 'content_verification']
    assert 'shot_beats[0]' in calls[1][1]
    assert '节拍：正文' in calls[1][1]


@pytest.mark.parametrize('first_failure', ['source_span', 'truncated', 'bad_shape'])
def test_verifier_protocol_correction_keeps_candidate_and_uses_shared_budget(monkeypatch, tmp_path, first_failure):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    calls = []
    verification_calls = []
    raw = plan_for_narration()
    raw.pop(LEGACY_ACCEPTANCE_FIELD)

    def generate(request):
        name = request.json_schema['name']
        calls.append(name)
        if name != 'content_verification':
            body = raw
        else:
            verification_calls.append(request)
            body = narration_response()
            if len(verification_calls) == 1:
                if first_failure == 'truncated':
                    return TextGenerationResult(success=False, truncated=True,
                                                error='max_output_tokens', text='{"facts":[')
                if first_failure == 'bad_shape':
                    body = {'facts': []}
                else:
                    body['source_support'][0]['evidence_spans'] = ['原文没有的句子']
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE', 'legacy_audio')
    result = outline.generate_outline_result(NOVEL, cache_path=tmp_path / 'outline.json')
    assert result['executable'], result['acceptance']
    assert calls == ['chapter_outline', 'content_verification', 'content_verification']
    assert result['verification']['used'] == 2
    assert len(result['verification']['corrections']) == 1
    assert result['plan']['required_facts'] == raw['required_facts']
    assert result['plan']['narration_lines'] == raw['narration_lines']
    assert result['revision']['calls'] == 0
    assert '候选、原文、主张和待核义务完全不变' in verification_calls[1].user_prompt


def test_bad_verification_exhausts_four_calls_without_rewriting_plan(monkeypatch, tmp_path):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    calls = []
    raw = plan_for_narration()
    raw.pop(LEGACY_ACCEPTANCE_FIELD)

    def generate(request):
        name = request.json_schema['name']
        calls.append(name)
        body = raw if name == 'chapter_outline' else narration_response()
        if name == 'content_verification':
            body['source_support'][0]['evidence_spans'] = ['不存在的原文']
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE', 'legacy_audio')
    cache = tmp_path / 'outline.json'
    result = outline.generate_outline_result(NOVEL, cache_path=cache)
    assert not result['executable']
    assert calls == ['chapter_outline'] + ['content_verification'] * 4
    assert result['revision']['stop_reason'] == 'verification_failed'
    assert result['revision']['calls'] == 0
    assert result['verification']['used'] == 4
    assert not cache.exists()
    assert result['plan']['required_facts'] == raw['required_facts']


def test_negative_semantic_verdict_does_not_trigger_protocol_correction(monkeypatch):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    calls = []

    def generate(request):
        name = request.json_schema['name']
        calls.append(name)
        body = plan_for_narration() if name == 'chapter_outline' else narration_response(verdict='partial')
        return TextGenerationResult(success=True, text=json.dumps(wire_body(body,request), ensure_ascii=False))

    monkeypatch.setattr(outline, 'create_service', lambda: SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE', 'legacy_audio')
    result = outline.generate_outline_result(NOVEL, max_revision_calls=0)
    assert not result['executable']
    assert calls == ['chapter_outline', 'content_verification']
    assert result['verification']['corrections'] == []
    assert result['plan']['plan_verification']['status'] == 'ok'


def test_distinct_protocol_feedback_keeps_existing_four_call_budget(monkeypatch,tmp_path):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    sent=[]
    def generate(request):
        if request.json_schema['name']=='chapter_outline':
            body=plan_for_narration()
        else:
            sent.append(request)
            body=narration_response()
            body['source_support'][0]['evidence_spans']=['src_unknown_'+str(len(sent))]
        return TextGenerationResult(success=True,text=json.dumps(wire_body(body,request),ensure_ascii=False))
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE','legacy_audio')
    result=outline.generate_outline_result(NOVEL,cache_path=tmp_path/'outline.json')
    assert not result['executable'] and len(sent)==4
    assert result['verification']['used']==4
    # T2：失败记录不挂成合法 `plan_verification`；它只作诊断留在 `verification_rejected`。
    assert 'plan_verification' not in result['plan']
    assert 'correction_stop' not in result['plan']['verification_rejected']


def test_same_protocol_failure_does_not_prevent_later_valid_response_within_budget(monkeypatch,tmp_path):
    from step01_script_gen import outline
    from _text_gen.models import TextGenerationResult
    sent=[]
    def generate(request):
        body=plan_for_narration()
        if request.json_schema['name']=='content_verification':
            sent.append(request)
            body=narration_response()
            if len(sent)<3:
                body['source_support'][0]['evidence_spans']=['src_same_unknown']
        return TextGenerationResult(success=True,text=json.dumps(wire_body(body,request),ensure_ascii=False))
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=generate))
    monkeypatch.setenv('STEP01_PRESENTATION_MODE','legacy_audio')
    result=outline.generate_outline_result(NOVEL,cache_path=tmp_path/'outline.json')
    assert result['executable'] and len(sent)==3
    assert result['verification']['used']==3 and result['revision']['calls']==0


def test_protocol_correction_does_not_send_after_deadline():
    from shared.content_verification import VerificationController
    from shared.presentation_contract import view_from_plan
    from step01_script_gen.content_verify import parse_verification_response
    now = [0.0]
    sent = []
    record, bad = parse_verification_response(json.dumps(narration_response(), ensure_ascii=False))
    assert not bad

    def verify(view, digest):
        sent.append(digest)
        return record

    def validate(record, view, digest):
        now[0] = 11.0
        return [{'code': 'verify_source_span_not_in_novel'}]

    ctl = VerificationController(verify=verify, validate_record=validate,
                                 repair=lambda *args: pytest.fail('deadline forbids correction'),
                                 clock=lambda: now[0], deadline_at=10.0)
    view, bad = view_from_plan(plan_for_narration())
    assert not bad
    result = ctl.record_for(view, 'candidate')
    assert result['status'] == 'bad_response'
    assert len(sent) == 1 and ctl.used == 1


@pytest.mark.parametrize('extra,code', [
    ('节拍：另一段动作', 'view_beat_segment_duplicate'),
    ('原文依据：另一段出处｜原文依据：第三段出处', 'view_beat_segment_duplicate'),
    ('未识别正文：另一段动作', 'view_beat_segment_unrepresentable'),
])
def test_projection_does_not_discard_extra_beat_content(extra, code):
    from shared.presentation_contract import view_from_plan
    plan = plan_for_narration()
    plan['shot_beats'][0] += '｜' + extra
    view, bad = view_from_plan(plan)
    assert view is None
    assert code in [p['code'] for p in bad]
