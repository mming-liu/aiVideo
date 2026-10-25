import copy
import json
import pytest
from step01_script_gen import main as m
from tests.step01_script_gen.test_repair_targets import (a3_commit_env as commit_env,
    _a3_chain_script as _layout_script, _a3_has_phantom as _has_phantom,
    _A3ChainService as _RepairSvc)
from tests.step01_script_gen.test_step01_commit import _audit_payload, _fake_audit
from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

@pytest.mark.parametrize("sid", ["16", "37"])
def test_valid_example_preserves_real_objects_and_bare_ids(sid):
    shot={"index":int(sid),"prompt":"人物\"静坐\"", "frame_layouts":{"ff":[{"props":[]}]}}
    source={"shot_"+sid:shot,"hook":{"prompt":"开场"},"shot_99":{"prompt":"未授权"}}
    before=copy.deepcopy(source)
    answer=json.loads(m._repair_answer_example(source,["shot_"+sid,"hook"]))
    assert answer=={"shot_replacements":[{"shot_id":sid,"shot":shot}],"hook_replacement":{"shot_id":"hook","shot":source["hook"]}}
    assert source==before

def test_actual_repair_request_consumes_serialized_example(commit_env,monkeypatch):
    holder={};captured=[]
    class Service(_RepairSvc):
        def generate(self,req):
            captured.append(req)
            return super().generate(req)
    def audit(current,n):
        holder["script"]=copy.deepcopy(current)
        sid=str(current["shots"][0]["index"])
        issues=[] if not _has_phantom(current) else [{"code":"model_audit_LAYOUT_PHANTOM_ENTITY","severity":"visual_risk","shot_id":sid,"field_path":"ff_desc","evidence":"小厮","message":"删除多余条目","repair_targets":[{"shot_id":sid,"field_path":"frame_layouts.kfs[0]"}]}]
        return _audit_payload(current,issues=issues,scope=m.STEP01_AUDIT_SCOPE,fingerprint=RULE_SOURCE_FINGERPRINT)
    _fake_audit(monkeypatch,audit)
    monkeypatch.setattr(m,"create_service",lambda *a,**k:Service(holder))
    _,summary=m._commit_step01_outputs(_layout_script(),"")
    assert summary["status"]=="COMMITTED" and captured
    for req in captured:
        if req.user_inputs:
            task=req.user_inputs[-1]["content"]
            assert "...完整镜头" not in task
            start=task.index("格式：")+len("格式：")
            example=json.JSONDecoder().raw_decode(task[start:])[0]
            assert example["shot_replacements"][0]["updates"]
            assert not example["shot_replacements"][0]["shot_id"].startswith("shot_")
