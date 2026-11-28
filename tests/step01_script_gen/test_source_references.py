import copy
import json
from types import SimpleNamespace

import pytest

from step01_script_gen import content_verify as cv,source_references as sr,plan_coherence as c
from tests.step01_script_gen.test_legacy_semantic import NOVEL,plan_for_narration,narration_response,wire_body

pytestmark=pytest.mark.v2_request


def test_catalog_preserves_punctuation_and_duplicate_source_locations():
    novel='  “你好。”\r\n\r\n他说：“再见。”\n  “你好。”'
    catalog=sr.catalog_for(novel)
    assert list(catalog.values())==['  “你好。”','他说：“再见。”','  “你好。”']
    assert len(catalog)==3 and catalog==sr.catalog_for(novel)
    refs=list(catalog)
    assert sr.continuous_evidence([refs[0],refs[1]],catalog,novel)=='  “你好。”\r\n\r\n他说：“再见。”'
    assert not set(catalog)&set(sr.catalog_for(novel+'别的内容'))


@pytest.mark.parametrize('refs',[['原文文本'],['src_unknown_0'],[None],{},None])
def test_foreign_literal_or_malformed_reference_never_becomes_a_quote(refs):
    raw=narration_response();raw['source_support'][0]['evidence_spans']=refs
    rec,bad=cv.parse_verification_response(json.dumps(raw,ensure_ascii=False),source_catalog=sr.catalog_for(NOVEL))
    assert rec is None and bad


def test_service_parser_expands_source_fields_and_leaves_display_evidence_literal(monkeypatch):
    from step01_script_gen import outline
    from shared.presentation_contract import view_from_plan
    from shared.content_verification import VerificationController
    requests=[]
    raw=narration_response()
    def generate(request):
        requests.append(request)
        return SimpleNamespace(success=True,text=json.dumps(wire_body(raw,request),ensure_ascii=False),call_id='fixture-ref')
    monkeypatch.setattr(outline,'create_service',lambda:SimpleNamespace(generate=generate))
    identity=cv.novel_identity(NOVEL)
    controller=VerificationController(verify=lambda view,digest:cv.verify_view_with_service(
        view,digest,novel_text=NOVEL,novel_digest=identity['digest'],source_references=True),
        novel_digest=identity['digest'],require_coherence=True)
    plan=cv.attach_verification(c.mark_plan(plan_for_narration()),controller)
    assert controller.used==1 and not outline.plan_acceptance_problems(plan,NOVEL)
    record=plan['plan_verification']
    assert record['source_support'][0]['evidence_spans']==[NOVEL]
    assert record['coherence']['evidence_spans']==[NOVEL]
    assert record['facts']==raw['facts'] and record['voice_units']==raw['voice_units']
    assert record['_source_reference_evidence']['version']==sr.VERSION
    assert all(key in requests[0].user_prompt for key in sr.catalog_for(NOVEL))


def test_preservation_and_coherence_use_the_same_strict_source_catalog():
    catalog=sr.catalog_for(NOVEL);ref=next(iter(catalog))
    raw={'source_support':[{'evidence_spans':[ref]}],
         'coherence':{'evidence_spans':[ref]},'source_preservation':{'evidence_spans':[ref]},
         'facts':[{'support_spans':[ref]}]}
    before=copy.deepcopy(raw);expanded,bad=sr.expand(raw,catalog)
    assert not bad and raw==before
    assert expanded['source_preservation']['evidence_spans']==[NOVEL]
    assert expanded['facts']==raw['facts']
    raw['source_preservation']['evidence_spans']=['foreign']
    assert sr.expand(raw,catalog)[0] is None


def test_model_cannot_self_report_reference_transport_receipt():
    raw=narration_response();raw['_source_reference_evidence']={'version':'forged'}
    record,bad=cv.parse_verification_response(json.dumps(raw,ensure_ascii=False))
    assert not bad and '_source_reference_evidence' not in record
