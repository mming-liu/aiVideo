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
        issues=[] if not _has_phantom(current) else [{"code":"model_audit_LAYOUT_PHANTOM_ENTITY","severity":"visual_risk","shot_id":sid,"entity_id":"小厮","field_path":"ff_desc","evidence":"小厮","message":"删除多余条目","repair_targets":[{"shot_id":sid,"field_path":"frame_layouts.kfs[0]"}]}]
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


# ---------------------------------------------------------------------------
# M02（2026-11-15）：请求表达严格匹配逐目标授权（零模型 / 零网络）
# ---------------------------------------------------------------------------
import inspect as _inspect  # noqa: E402
from step01_script_gen.repair_authorization import (  # noqa: E402
    render_authorization_guidance as _guidance,
    repair_authorization_spec as _auth_spec)
from step01_script_gen.repair_fields import (  # noqa: E402
    apply_scoped_field_updates as _apply_updates, path_allowed as _allowed)
from tests.verification_helpers import step01_getsource  # noqa: E402


def _m02_shot():
    return {
        "index": "5", "duration": 8.0, "duration_sec": 8.0,
        "narration_segment": "旁白原文", "subtitle": "字幕原文",
        "prompt": "中景", "ff_desc": "起始画面", "lf_desc": "结束画面",
        "motion_desc": "0-3秒：第一段；3-8秒：第二段",
        "keyframes": [
            {"t_sec": 1.0, "desc": "第一段", "stage": "pre"},
            {"t_sec": 3.0, "desc": "第二段", "stage": "approach"}],
        "frame_layouts": {
            "ff": [{"name": "甲", "pose": "站立", "props": []}],
            "lf": [{"name": "甲", "pose": "坐姿", "props": []}],
            "kfs": [
                [{"name": "甲", "pose": "站立", "props": []}],
                [{"name": "甲", "pose": "坐姿", "props": []}],
            ]},
        "temporal_plan": {"events": []},
    }


def _m02_example(allowed, *, compact=True):
    shot = _m02_shot()
    raw = m._repair_answer_example(
        {"shot_5": shot}, {"shot_5"}, {"shot_5": set(allowed)},
        compact=compact)
    return shot, json.loads(raw)["shot_replacements"][0]


def test_m02_precise_leaf_paths_are_not_promoted_to_parent_field():
    spec = _auth_spec(_m02_shot(),
                      {"frame_layouts.kfs.0.0.pose", "ff_desc"})
    assert spec["writable_paths"] == ["ff_desc", "frame_layouts.kfs.0.0.pose"]
    assert "frame_layouts" not in spec["writable_paths"]
    assert spec["keyframe_additions_allowed"] is False


def test_m02_wildcard_paths_expand_to_applicable_concrete_paths():
    spec = _auth_spec(_m02_shot(),
                      {"keyframes.*.desc", "frame_layouts.lf.*.props"})
    assert spec["writable_paths"] == [
        "frame_layouts.lf.0.props", "keyframes.0.desc", "keyframes.1.desc"]
    assert all("*" not in p for p in spec["writable_paths"])


def test_m02_placeholders_match_current_value_types():
    spec = _auth_spec(_m02_shot(), {"frame_layouts", "duration"})
    fl = spec["placeholders"]["frame_layouts"]
    assert isinstance(fl, dict), "frame_layouts 是对象，不是列表"
    assert set(fl) >= {"ff", "lf", "kfs"}
    assert isinstance(fl["kfs"], list)
    assert isinstance(spec["placeholders"]["duration"], float)


def test_m02_frame_addition_permission_uses_authoritative_check():
    leaf = _auth_spec(_m02_shot(),
                      {"frame_layouts.kfs.1", "frame_layouts.kfs.0.0.props"})
    assert leaf["keyframe_additions_allowed"] is False
    whole = _auth_spec(_m02_shot(), {"keyframes", "frame_layouts.kfs"})
    assert whole["keyframe_additions_allowed"] is True
    assert not any(p in ("keyframes", "frame_layouts.kfs")
                   for p in whole["writable_paths"])


def test_m02_readonly_related_fields_are_listed_separately():
    spec = _auth_spec(_m02_shot(), {"ff_desc"})
    assert "keyframes" in spec["read_only_related"]
    assert "frame_layouts.kfs" in spec["read_only_related"]
    assert "ff_desc" not in spec["read_only_related"]


def test_m02_compact_example_updates_pass_real_application_entry():
    allowed = {"frame_layouts.kfs.0.0.pose", "ff_desc", "keyframes.*.desc"}
    shot, repl = _m02_example(allowed)
    assert "shot" not in repl, "compact 示例不得抄整份镜头"
    paths = [u["field_path"] for u in repl["updates"]]
    assert all("*" not in p for p in paths)
    assert all(_allowed(p, allowed) for p in paths)
    assert "keyframe_additions" not in repl
    new_shot, why = _apply_updates(shot, repl["updates"], allowed=allowed)
    assert new_shot is not None, why
    assert why == ""


def test_m02_whole_table_placeholder_has_correct_type():
    shot, repl = _m02_example({"frame_layouts"})
    assert [u["field_path"] for u in repl["updates"]] == ["frame_layouts"]
    assert isinstance(repl["updates"][0]["value"], dict)
    new_shot, why = _apply_updates(shot, repl["updates"],
                                   allowed={"frame_layouts"})
    assert new_shot is not None, why


def test_m02_paired_additions_only_when_authorized():
    _, leaf = _m02_example({"frame_layouts.kfs.0.0.props"})
    assert "keyframe_additions" not in leaf
    _, whole = _m02_example({"keyframes", "frame_layouts.kfs"})
    assert "keyframe_additions" in whole
    assert set(whole["keyframe_additions"][0]) == {"frame", "layout"}
    assert "updates" not in whole, "补帧授权下不诱导整表重写 kfs"


def test_m02_leaf_authorization_still_rejects_whole_table_answer():
    new_shot, why = _apply_updates(
        _m02_shot(),
        [{"field_path": "frame_layouts",
          "value": {"ff": [], "lf": [], "kfs": []}}],
        allowed={"frame_layouts.kfs.0.0.pose"})
    assert new_shot is None
    assert "越权" in why, why


def test_m02_full_shot_compatibility_is_preserved():
    raw = m._repair_answer_example({"shot_5": _m02_shot()}, {"shot_5"})
    repl = json.loads(raw)["shot_replacements"][0]
    assert "shot" in repl and repl["shot"]["index"] == "5"


def test_m02_both_request_sites_render_the_same_guidance():
    src = step01_getsource(m._repair_shot_warnings)
    assert src.count('f"{_auth_guidance}"') == 2, "两处请求构造都要带授权说明"
    assert "_auth_spec_by_target" in src
    g = _guidance({"shot_5": _auth_spec(_m02_shot(), {"ff_desc"})})
    assert "允许修改" in g and "必须读取用于对齐" in g
    assert "未授权补帧" in g
    g2 = _guidance({"shot_5": _auth_spec(_m02_shot(), {"keyframes"})})
    assert "允许补帧" in g2 and "成对形状" in g2
