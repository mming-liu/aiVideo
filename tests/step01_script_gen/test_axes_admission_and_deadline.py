"""Controller admission and deadline checks across the actual two-axis path."""
import copy
import time
import pytest
from shared import content_verification as cv
from step01_script_gen import content_verify as producer, outline
from tests.step01_script_gen import test_screen_hybrid_components_chain as T
from _text_gen.models import TextGenerationResult

pytestmark = pytest.mark.v2_request


def sample():
    plan = T._with_context(T._components_plan())
    view = T._view_of(plan)
    return plan, view, T._valid_combined(plan, view, T._components_table())


@pytest.mark.parametrize("field,value", [("plan_digest", "foreign"), ("novel_digest", "foreign"),
    ("rule_version", "old"), ("obligation_basis", "foreign"), ("facts", 1)])
def test_controller_rejects_foreign_or_malformed_record_without_restamping(field, value):
    plan, view, record = sample()
    record[field] = value
    original = copy.deepcopy(record)
    ctl = cv.VerificationController(verify=None, novel_digest=T._novel_dig())
    result = ctl.record_axes_for(view, cv.plan_digest_of(plan),
        verify_axes=lambda *a, **kw: {"sent":2,"record":record,"problems":[]})
    assert result["status"] != "ok"
    assert ctl.used == 2 and record == original
    assert ctl.seed(cv.plan_digest_of(plan), record, plan=plan) is False


@pytest.mark.parametrize("ids", [("", "display"), ("source", "  "), ("same", "same")])
def test_bad_axis_ids_are_rejected_at_both_producer_and_seed(monkeypatch, ids):
    plan, view, rec = sample()
    rec["axes"].update(source_call_id=ids[0], display_call_id=ids[1])
    ctl = cv.VerificationController(verify=None, novel_digest=T._novel_dig())
    assert ctl.seed(cv.plan_digest_of(plan), rec, plan=plan) is False
    assert cv.record_shape_problems(rec)


def test_initialization_crossing_deadline_does_not_send(monkeypatch):
    _, view, _ = sample()
    now = [10.0]
    sends = []
    monkeypatch.setattr(time,"monotonic",lambda:now[0])
    class Service:
        def generate(self, req):
            sends.append(req)
            return TextGenerationResult(success=False,error="unexpected")
    def create():
        now[0] = 20.0
        return Service()
    monkeypatch.setattr(outline,"create_service",create)
    out = producer.verify_view_axes_with_service(view,cv.plan_digest_of(view),
        decomposition=T._components_table(),novel_text=T._SRC,novel_digest=T._novel_dig(),
        deadline_monotonic=15.0)
    assert out["sent"] == 0 and sends == []
    assert out["problems"][0]["code"] == "verify_deadline"


def test_controller_deadline_reaches_axis_send_boundary(monkeypatch):
    plan, view, _ = sample()
    now = [10.0]
    sends = []
    monkeypatch.setattr(time,"monotonic",lambda:now[0])
    class Service:
        def generate(self, req):
            sends.append(req)
            now[0] = 20.0
            source,_ = T._axis_records(view,T._components_table(),plan)
            import json
            return TextGenerationResult(success=True,text=json.dumps(source),call_id="source")
    monkeypatch.setattr(outline,"create_service",lambda:Service())
    def axes(v,d,**kw):
        return producer.verify_view_axes_with_service(v,d,novel_text=T._SRC,
            novel_digest=T._novel_dig(),**kw)
    ctl = cv.VerificationController(verify=None,novel_digest=T._novel_dig(),deadline_at=15.0)
    result = ctl.record_axes_for(view,cv.plan_digest_of(plan),verify_axes=axes)
    assert len(sends) == ctl.used == 1
    assert sends[0].deadline_monotonic == 15.0
    assert result["status"] != "ok" and result["code"] == "verify_deadline", result


@pytest.mark.parametrize("tokens", [None, "bad", 0, -1, True, 1.5])
def test_invalid_display_parameters_reject_before_source_send(monkeypatch, tokens):
    plan, view, _ = sample()
    creates = []
    def create():
        creates.append(1)
        raise RuntimeError("unexpected initialization")
    monkeypatch.setattr(outline,"create_service",create)
    out = producer.verify_view_axes_with_service(view,cv.plan_digest_of(plan),
        decomposition=T._components_table(),novel_text=T._SRC,novel_digest=T._novel_dig(),
        display_max_tokens=tokens)
    assert creates == [] and out["sent"] == 0
    assert out["problems"][0]["code"] == "verify_input_rejected"


@pytest.mark.parametrize("changes", [{"axes":None}, {"sent":0}, {"sent":1}])
def test_success_requires_two_axis_evidence_and_two_sends(changes):
    plan, view, record = sample()
    output = {"sent":2,"record":record,"problems":[]}
    if "axes" in changes:
        record.pop("axes")
    else:
        output.update(changes)
    ctl=cv.VerificationController(verify=None,novel_digest=T._novel_dig())
    result=ctl.record_axes_for(view,cv.plan_digest_of(plan),verify_axes=lambda *a,**kw:output)
    assert result["status"] != "ok"


@pytest.mark.parametrize("field,value", [("novel_digest","foreign"),("facts",1)])
def test_reused_record_cannot_bypass_admission_after_mutation(field,value):
    plan,view,record=sample()
    ctl=cv.VerificationController(verify=None,novel_digest=T._novel_dig())
    digest=cv.plan_digest_of(plan)
    assert ctl.seed(digest,record,plan=plan)
    ctl.records[digest][field]=value
    result=ctl.record_axes_for(view,digest,verify_axes=lambda *a,**kw:pytest.fail("rejected record must not send"))
    assert result["status"] != "ok" and ctl.used == 0


def test_table_override_cannot_replace_admitted_context():
    plan,view,_=sample()
    ctl=cv.VerificationController(verify=None,novel_digest=T._novel_dig())
    result=ctl.record_axes_for(view,cv.plan_digest_of(plan),decomposition={},
        verify_axes=lambda *a,**kw:pytest.fail("table mismatch must not send"))
    assert result["status"] != "ok" and ctl.used == 0
    assert result["code"] == "verify_input_rejected"


@pytest.mark.parametrize("record", [True,1,"unexpected",["unexpected"]])
def test_non_object_producer_record_is_rejected_without_crashing(record):
    plan,view,_=sample()
    ctl=cv.VerificationController(verify=None,novel_digest=T._novel_dig())
    result=ctl.record_axes_for(view,cv.plan_digest_of(plan),
        verify_axes=lambda *a,**kw:{"sent":2,"record":record})
    assert result["status"] != "ok" and ctl.used == 2


@pytest.mark.parametrize("args", [{"model":1},{"source_model":"  "},{"display_model":[]},
                                  {"remaining":True},{"remaining":"bad"}])
def test_invalid_model_or_capacity_parameters_are_zero_send(monkeypatch,args):
    plan,view,_=sample()
    monkeypatch.setattr(outline,"create_service",lambda:pytest.fail("invalid input must not initialize"))
    result=producer.verify_view_axes_with_service(view,cv.plan_digest_of(plan),
        decomposition=T._components_table(),novel_text=T._SRC,novel_digest=T._novel_dig(),**args)
    assert result["sent"] == 0 and result["problems"][0]["code"] == "verify_input_rejected"


@pytest.mark.parametrize("ids", [("", "display"), ("source", "  "), ("same", "same")])
def test_actual_producer_rejects_bad_physical_axis_ids(monkeypatch,ids):
    plan,view,_=sample()
    records=T._axis_records(view,T._components_table(),plan)
    sends=[]
    class Service:
        def generate(self,req):
            import json
            index=len(sends)
            sends.append(req)
            return TextGenerationResult(success=True,text=json.dumps(records[index]),call_id=ids[index])
    monkeypatch.setattr(outline,"create_service",lambda:Service())
    result=producer.verify_view_axes_with_service(view,cv.plan_digest_of(plan),
        decomposition=T._components_table(),novel_text=T._SRC,novel_digest=T._novel_dig())
    assert result["record"] is None and result["sent"] == len(sends) == 2
    assert any(p["code"].startswith("verify_axes_call_id") for p in result["problems"])


def test_explicit_null_axes_is_not_legacy_absence():
    plan,view,record=sample()
    record["axes"]=None
    ctl=cv.VerificationController(verify=None,novel_digest=T._novel_dig())
    assert not ctl.seed(cv.plan_digest_of(plan),record,plan=plan)
    assert cv.axes_problems(record)
