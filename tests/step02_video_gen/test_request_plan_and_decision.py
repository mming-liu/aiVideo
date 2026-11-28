"""V 包契约测试：请求冻结/key 事实边界 与 纯函数裁决。"""
from pathlib import Path

import pytest

from step02_video_gen.request_plan import (FrozenVideoRequest, ReferenceBinding,
                                           build_frozen_request, validate_frozen_request)
from step02_video_gen.reroll_decision import (DECISION_ACCEPT, DECISION_POSTPROCESS,
                                              DECISION_REVISE_INPUTS, DECISION_STOP,
                                              DECISION_UNVERIFIED, VideoCheckReport,
                                              VideoIssue, decide)


class _Req:
    """最小替身：形状对齐 _video_gen.models.VideoGenerationRequest 的消费字段。"""

    def __init__(self, prompt="p", image_path=None, reference_paths=None,
                 duration=6, size="720x1280", seed=None):
        self.prompt = prompt
        self.clip_index = 1
        self.duration = duration
        self.size = size
        self.seed = seed
        self.negative_prompt = ""
        self.camera_movement = ""
        self.image_path = image_path
        self.last_frame_image_path = None
        self.extra_keyframe_paths = []
        self.reference_paths = list(reference_paths or [])


def _shot():
    return {"index": 1, "shot_id": "shot-1", "duration_sec": 6.0, "source_spans": []}


def test_request_key_uses_bytes_not_paths(tmp_path):
    a = tmp_path / "a.png"
    a.write_bytes(b"img-A")
    b_dir = tmp_path / "other"
    b_dir.mkdir()
    b = b_dir / "b.png"
    b.write_bytes(b"img-A")          # 同内容、不同路径

    f1 = build_frozen_request(_Req(image_path=str(a)), shot=_shot(), scope={"chapter": 1},
                              script_revision="rev1", provider="stub", model_id="m1")
    f2 = build_frozen_request(_Req(image_path=str(b)), shot=_shot(), scope={"chapter": 1},
                              script_revision="rev1", provider="stub", model_id="m1")
    assert f1.request_key == f2.request_key          # 素材摘要进 key，路径不进

    b.write_bytes(b"img-B")                          # 同路径、不同字节
    f3 = build_frozen_request(_Req(image_path=str(b)), shot=_shot(), scope={"chapter": 1},
                              script_revision="rev1", provider="stub", model_id="m1")
    assert f3.request_key != f2.request_key

    # 生成事实变化 ⇒ key 变化
    f4 = build_frozen_request(_Req(prompt="p2", image_path=str(a)), shot=_shot(),
                              scope={"chapter": 1}, script_revision="rev1", provider="stub",
                              model_id="m1")
    f5 = build_frozen_request(_Req(image_path=str(a), seed=7), shot=_shot(), scope={"chapter": 1},
                              script_revision="rev1", provider="stub", model_id="m1")
    f6 = build_frozen_request(_Req(image_path=str(a)), shot=_shot(), scope={"chapter": 1},
                              script_revision="rev1", provider="stub", model_id="m2")
    assert len({f1.request_key, f4.request_key, f5.request_key, f6.request_key}) == 4

    # key_facts 不含素材路径与本地输出路径
    facts = f1.key_facts()
    assert "path" not in facts["references"][0]
    assert set(facts["references"][0]) == {"ref_id", "role", "sha256", "bindings", "inherit", "exclude"}


def test_reference_bindings_shape(tmp_path):
    img = tmp_path / "i.png"; img.write_bytes(b"x")
    ref = tmp_path / "r.png"; ref.write_bytes(b"y")
    req = _Req(image_path=str(img), reference_paths=[str(ref)])
    f = build_frozen_request(req, shot=_shot(), scope={}, script_revision="r",
                             provider="stub", model_id="m")
    assert [r.role for r in f.references] == ["first_frame", "reference"]
    assert all(r.sha256 for r in f.references)


def test_validate_reports_structured_problems(tmp_path):
    f = FrozenVideoRequest(request_id="r1", provider="stub", model_id="m", mode="image",
                           prompt="")
    problems = {p["code"] for p in validate_frozen_request(f)}
    assert "prompt_empty" in problems

    f2 = FrozenVideoRequest(request_id="r2", provider="stub", model_id="m", mode="image",
                            prompt="ok")
    f2.references = [
        ReferenceBinding(ref_id="r1", role="first_frame", path=str(tmp_path / "nope.png")),
        ReferenceBinding(ref_id="r2", role="weird_role", path=None),
    ]
    got = {(p["code"], p["field_path"]) for p in validate_frozen_request(f2, max_references=1)}
    assert ("reference_missing", "references[r1].path") in got
    assert ("unknown_role", "references[r2].role") in got
    assert ("reference_capacity", "references") in got
    assert all(p["needed_change"] for p in validate_frozen_request(f2, max_references=1))


# ---------------------------------------------------------------- 裁决

def test_decide_accept_unverified_and_postprocess():
    ok = VideoCheckReport(media_status="pass", semantic_status="pass")
    assert decide(ok).kind == DECISION_ACCEPT

    unknown = VideoCheckReport(media_status="pass", semantic_status="unknown")
    d = decide(unknown)
    assert d.kind == DECISION_UNVERIFIED and "unverified" in d.reasons[0]

    post = VideoCheckReport(media_status="pass", semantic_status="fail",
                            issues=[VideoIssue("i1", "subtitle"), VideoIssue("i2", "encoding")])
    assert decide(post).kind == DECISION_POSTPROCESS


def test_decide_contract_beats_postprocess_and_returns_required_change():
    rep = VideoCheckReport(media_status="pass", semantic_status="fail", issues=[
        VideoIssue("i1", "voice_identity", field_paths=["shots[3].narration_segment"],
                   required_change="换回角色音色"),
        VideoIssue("i2", "subtitle"),
    ])
    d = decide(rep)
    assert d.kind == DECISION_REVISE_INPUTS
    assert d.required_changes[0]["field_paths"] == ["shots[3].narration_segment"]
    assert d.residual == ["i2"]                       # 后处理项作为残余列出


def test_decide_media_without_evidence_is_unverified():
    rep = VideoCheckReport(media_status="fail", semantic_status="pass",
                           issues=[VideoIssue("i1", "black", time_range=(1.0, 2.0))])
    assert decide(rep).kind == DECISION_UNVERIFIED     # 无 observed/证据 ⇒ 不判生成错误
    rep2 = VideoCheckReport(media_status="fail", semantic_status="pass", issues=[
        VideoIssue("i1", "black", time_range=(1.0, 2.0), observed="整段纯黑",
                   evidence_paths=["/tmp/frame_12.png"])])
    assert decide(rep2).kind == DECISION_REVISE_INPUTS


def test_decide_stops_on_same_key_same_evidence():
    rep = VideoCheckReport(media_status="pass", semantic_status="fail",
                           issues=[VideoIssue("i1", "action", expected="拔剑", observed="空手")])
    first = decide(rep, request={"request_key": "K"})
    assert first.kind == DECISION_REVISE_INPUTS
    hist = [{"request_key": "K", "fingerprints": [i.fingerprint() for i in rep.issues]}]
    second = decide(rep, request={"request_key": "K"}, history=hist)
    assert second.kind == DECISION_STOP and second.stalled
    assert "故事不可满足" not in " ".join(second.reasons)


def test_provider_material_roles_freeze_and_pass_preflight(tmp_path):
    """P1-4：走**provider 的角色词汇**产出的素材，冻结后必须能过预检.

    反例：Seedance provider 的 `content_plan` 用 `reference_image`，冻结时原样搬进
    `references[].role`，预检按 `KNOWN_ROLES` 判 `unknown_role` ⇒ **合法请求在 POST
    前被拒**。同时冻结的参数必须取 prepared 的**实际值**（时长 10 而非请求里的 6）。
    """
    from _video_gen.models import MaterialRef, PreparedVideoRequest
    from step02_video_gen.request_plan import build_frozen_request, validate_frozen_request

    media = tmp_path / "ref.png"
    media.write_bytes(b"png-bytes")
    from shared.enhancement_io import sha256_file
    prepared = PreparedVideoRequest(
        request_id="req-1", provider="volcengine", model_id="seedance-x", mode="r2v",
        prompt="生效文本", parameters={"duration": 10, "resolution": "720x1280",
                                       "generate_audio": False},
        materials=[MaterialRef(role="reference_image", kind="image",
                               source_path=str(media), sha256=sha256_file(media),
                               bytes=media.stat().st_size, ref_id="r1")])

    class _Req:
        prompt = "原始文本"
        duration = 6
        size = "1080x1920"
        seed = None
        negative_prompt = ""
        camera_movement = None
        image_path = None
        last_frame_image_path = None
        extra_keyframe_paths = None
        reference_paths = None

    frozen = build_frozen_request(_Req(), shot={"shot_id": "shot-1", "index": 1},
                                  scope={"chapter": 1}, script_revision="r1",
                                  provider="volcengine", model_id="seedance-x",
                                  prepared=prepared)
    assert frozen.references and frozen.references[0].role == "reference"
    assert validate_frozen_request(frozen) == []          # 预检通过（不再 unknown_role）
    # 冻结的是**实际**参数：时长取 prepared 的 10，不是请求里的 6
    assert frozen.parameters["duration"] == 10
    assert frozen.prompt == "生效文本"
