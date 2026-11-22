# -*- coding: utf-8 -*-
"""M04（2026-11-22）：后处理不得把同一段资产 / 外观描述复制到多处（质量门 C6 判 duplicate_injection）.

S11 40 轮：17 份提交门 BLOCK 中 16 份含 duplicate_injection，来源是两个后处理写入点：

1. v6_43 规则 1：把「小舟」整词替换成 scene_objects 完整规格，并对 prompt/ff/lf/motion
   四个字段各写一遍 → 跨字段 ≥24 字整块重复。规则已删除；尺寸由真正的消费者
   （Step02a `_inject_object_size_anchor`）按 scene_objects 自行注入。
2. role_anchor：正文已写了某角色的外观段，仍前置一份 `[名: 外观]` → 同字段重复。

样本全部匿名，非船承载空间用「车厢」验证机制与作品、物体类别无关。
"""
from __future__ import annotations

import copy
import json

from shared.script_preflight import check_duplicate_injection
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
from step01_script_gen.constraints.v6_43_prompt_enforcement import V643PromptEnforcementConstraint

_BOAT_SPEC = {"湖心小舟": {"category": "船", "subtype": "平底敞口小舟",
                          "length_m": 4.5, "width_m": 1.2, "capacity": "可容2人"}}


def _ctx(shots, *, scene_objects=None, characters=None):
    script = {"shots": shots, "characters": characters or [], "scene_objects": scene_objects or {}}
    return ConstraintContext.from_script(script)


def test_scene_object_spec_is_not_copied_into_every_field():
    shot = {"index": 1, "location": "湖心小舟",
            "prompt": "中景，角色甲坐在小舟船头吹奏",
            "ff_desc": "角色甲坐在小舟船头，竹管横在胸前",
            "lf_desc": "角色甲坐在小舟船头，竹管抵唇",
            "motion_desc": "0-3秒：角色甲在小舟上抬起竹管"}
    before = copy.deepcopy(shot)
    V643PromptEnforcementConstraint().apply(_ctx([shot], scene_objects=_BOAT_SPEC))
    for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
        assert shot[field] == before[field], f"{field} 不得被改写成带规格的资产描述"
    assert check_duplicate_injection(shot) == []


def test_size_still_reaches_the_frame_prompt_consumer(tmp_path, monkeypatch):
    """尺寸只由 Step02a 消费端按 scene_objects 注入一次；非船承载空间同样生效."""
    import shared.paths as paths
    from shared.vimax_bridge import _inject_object_size_anchor

    script = {"scene_objects": {"驿道车厢": {"category": "车", "subtype": "双辕车厢",
                                            "length_m": 3.0, "width_m": 1.5}},
              "shots": []}
    path = tmp_path / "script_result.json"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "script_file", lambda *a, **k: path)
    shot = {"location": "驿道车厢", "ff_desc": "角色乙坐在车厢里", "lf_desc": "", "prompt": ""}
    out = _inject_object_size_anchor(shot, "角色乙坐在车厢里")
    assert "长3.0米" in out and "宽1.5米" in out
    assert out.count("长3.0米") == 1
    assert _inject_object_size_anchor(shot, out) == out, "消费端注入幂等"


_CHARS = [{"identifier": "角色丙", "static_features": "脸型瘦削，细长丹凤眼，眼神机警，黑发束髻，青衣小帽"}]


def _role_shot(prompt):
    return {"index": 1, "main_subject": "角色丙", "prompt": prompt,
            "frame_layouts": {"ff": [{"name": "角色丙"}], "lf": []}}


def test_role_anchor_does_not_repeat_an_appearance_already_in_the_prompt():
    shot = _role_shot("中景，角色丙脸型瘦削，细长丹凤眼，眼神机警，黑发束髻，青衣小帽，自岸边跃上船梢")
    before = shot["prompt"]
    RoleAnchorConstraint().apply(_ctx([shot], characters=_CHARS))
    assert shot["prompt"] == before
    assert check_duplicate_injection(shot) == []


def test_role_anchor_still_adds_a_missing_appearance():
    shot = _role_shot("中景，角色丙自岸边跃上船梢")
    RoleAnchorConstraint().apply(_ctx([shot], characters=_CHARS))
    assert shot["prompt"].startswith("[角色丙: 脸型瘦削")
    assert check_duplicate_injection(shot) == []
