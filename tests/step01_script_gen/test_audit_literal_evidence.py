from types import SimpleNamespace
import pytest
from _text_gen.models import TextGenerationResult
from step01_script_gen import audit, main
from tools.probe_isolation import isolated_probe


def test_actual_audit_request_states_the_literal_witness_contract(monkeypatch,tmp_path):
    sent=[]
    class Service:
        def generate(self,req):
            sent.append(req)
            return TextGenerationResult(success=True,text='{"issues":[]}')
    monkeypatch.setattr(audit,"create_service",lambda *a,**kw:Service())
    with isolated_probe(root=tmp_path,label="literal-evidence",block_transport=True):
        result=audit.run_same_model_audit_with_status("novel",{}, {"shots":[]})
    assert result.status=="ok" and len(sent)==1
    assert "逐字连续出现的缺陷片段" in sent[0].system_prompt
    assert "不加引号外壳、字段名前缀或解释" in sent[0].system_prompt

@pytest.mark.parametrize("fragment",["duplicated repeated corrupted phrase","连续重复且不通顺的错误句子"])
def test_literal_witness_is_real_progress_after_a_previous_attempt(fragment):
    issue=SimpleNamespace(code="model_audit_TEXT_CORRUPTION",shot_id="7",field_path="prompt",evidence=fragment)
    before={"shots":[{"index":7,"prompt":"prefix "+fragment+" suffix"}]}
    after={"shots":[{"index":7,"prompt":"prefix coherent replacement suffix"}]}
    attempted={main._audit_issue_identity(issue)}
    assert main._audit_issue_newly_resolved(after,before,issue,attempted)
    assert not main._audit_issue_newly_resolved(before,before,issue,attempted)
    assert not main._audit_issue_newly_resolved(after,after,issue,attempted)
    issue.evidence="The prompt contains "+fragment+" and that is defective"
    assert not main._audit_issue_newly_resolved(after,before,issue,attempted)
