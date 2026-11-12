"""S04：局部修订的**关联检查面**与**回读**（2026-11-11）.

证明的是确定性部分：改某一字段时，哪些同帧字段必须一起核对；回读能如实报出
「关联字段跟着变了 / 没变」。语义是否真的修好由重验与提交点审计判定，本文件不代替。
"""
from __future__ import annotations

from step01_script_gen import repair_fields as rf


def _shot(**kw) -> dict:
    base = {
        "index": 1, "duration_sec": 5.0,
        "ff_desc": "甲右手提壶站在案边", "lf_desc": "甲把壶放在案上",
        "motion_desc": "甲放下壶", "prompt": "[中景] 甲放下壶",
        "keyframes": [{"desc": "壶离案三寸", "stage": "approach", "t_sec": 1.5},
                      {"desc": "壶触案面", "stage": "climax", "t_sec": 3.0}],
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True, "position": "案边",
                    "pose": "提壶", "gaze_target": "案面", "props": ["酒壶"]}],
            "lf": [{"name": "甲", "on_screen": True, "position": "案边",
                    "pose": "垂手", "gaze_target": "案面", "props": []}],
            "kfs": [[{"name": "甲", "on_screen": True, "position": "案边",
                      "pose": "俯身", "gaze_target": "案面", "props": ["酒壶"]}],
                    [{"name": "甲", "on_screen": True, "position": "案边",
                      "pose": "松手", "gaze_target": "案面", "props": []}]],
        },
    }
    base.update(kw)
    return base


def test_ff_desc_pairs_with_ff_layout():
    out = rf.related_check_fields(["ff_desc"], _shot())
    assert out["ff_desc"] == ["frame_layouts.ff"]


def test_keyframe_desc_pairs_with_kf_layout_and_stage():
    out = rf.related_check_fields(["keyframes[1].desc"], _shot())
    assert "frame_layouts.kfs[1]" in out["keyframes[1].desc"]
    assert "keyframes[1].stage" in out["keyframes[1].desc"]


def test_layout_pairs_with_its_desc_field():
    out = rf.related_check_fields(["frame_layouts.lf"], _shot())
    assert out["frame_layouts.lf"] == ["lf_desc"]


def test_props_paths_pair_with_desc_field():
    out = rf.related_check_fields(["frame_layouts.ff.0.props"], _shot())
    assert out["frame_layouts.ff.0.props"] == ["ff_desc"]


def test_dead_related_paths_are_not_returned():
    """关联字段在该镜不存在时不给死路径（否则回读永远是「未变」）."""
    shot = _shot()
    shot.pop("frame_layouts")
    assert rf.related_check_fields(["ff_desc"], shot) == {}


def test_related_scope_never_contains_the_target_itself():
    shot = _shot()
    out = rf.related_check_fields(
        ["ff_desc", "lf_desc", "keyframes[0].desc", "keyframes[0].stage"], shot)
    for target, related in out.items():
        assert target not in related


def test_readback_reports_changed_and_unchanged():
    before = _shot()
    after = _shot(ff_desc="甲空手站在案边")
    after["frame_layouts"]["ff"][0]["props"] = []
    related = rf.related_check_fields(["ff_desc"], before)
    rb = rf.related_readback(before, after, related)
    assert rb["checked"] == 1
    assert rb["changed"] == 1
    assert rb["rows"][0]["field"] == "frame_layouts.ff"


def test_readback_detects_half_repair():
    """只改描述、不改同帧布局 → 回读必须显示关联字段「未变」（半条修复可见）."""
    before = _shot()
    after = _shot(ff_desc="甲空手站在案边")
    related = rf.related_check_fields(["ff_desc"], before)
    rb = rf.related_readback(before, after, related)
    assert rb["changed"] == 0
    assert rb["unchanged"] == 1


def test_main_exposes_flat_related_scope(tmp_path, monkeypatch):
    import step01_script_gen.main as m

    shot = _shot()
    flat = m._related_check_fields_for("shot_1", {"ff_desc"}, {"shot_1": shot})
    assert flat == ["frame_layouts.ff"]
    # 镜头不存在 → 空表（不阻塞修复）
    assert m._related_check_fields_for("shot_9", {"ff_desc"}, {"shot_1": shot}) == []


def test_round_records_related_readback_in_ledger(monkeypatch):
    """S04：关联字段回读进**结构化台账**（不只是日志）.

    模型只改 `ff_desc`、没动同帧布局 → 台账必须显示 checked>=1 / changed==0，
    并点出未变的关联字段。这样「半条修复」在 `step01_repair_rounds.json` 里可见，
    不必靠人翻日志猜。
    """
    from step01_script_gen.quality_issues import Issue
    from tests.step01_script_gen.test_linked_group_capacity import _Harness
    from tests.step01_script_gen.test_repair_targets import _complete_shot

    MARKER = "灯盏碎在甲板"
    base = _complete_shot(4)
    base["ff_desc"] = "张十三举灯盏站立，灯盏完好"
    script = {"shots": [base], "hook": None, "fps": 24, "total_duration": 6.0}

    def _warnings_fn(_script, _novel):
        shots = {str(x.get("index")): x for x in (_script.get("shots") or [])}
        issues, ws = [], {}
        if MARKER not in str((shots.get("4") or {}).get("ff_desc") or ""):
            issues.append({"code": "prop_state_static", "severity": "visual_risk",
                           "shot_id": "4", "frame_id": "ff", "entity_id": "",
                           "field_path": "ff_desc", "message": "首帧道具状态不符"})
            ws["shot_4"] = ["[prop_state_static] 首帧道具状态不符"]
        return {"_has_hard": True, "shots": ws, "issues": issues}

    fixed = _complete_shot(4)
    fixed["ff_desc"] = "张十三举灯盏站立，" + MARKER   # 授权字段：改描述
    # 同时**越权**改了同帧布局（未授权）——四栏台账必须把它记成「请求了但被回填」
    fixed["frame_layouts"]["ff"][0]["props"] = ["碎灯盏"]

    issue = Issue(code="model_audit_PROP_STATE", severity="visual_risk",
                  message="首帧道具状态不符", shot_id="4", frame_id="ff",
                  entity_id="张十三", field_path="ff_desc", evidence="灯盏完好",
                  repair_targets=[{"shot_id": "4", "field_path": "ff_desc"}],
                  repairable=True)
    h = _Harness(monkeypatch, audit_issues=[issue], indexes=(4,),
                 warnings_fn=_warnings_fn, script=script,
                 replacements=[{"shot_id": "4", "shot": fixed}])
    h.run()

    assert h.diag, h.logs[:12]
    rb = h.diag[-1].get("related_readback") or {}
    assert rb.get("checked", 0) >= 1, rb
    assert rb.get("changed") == 0, rb
    row = (rb.get("targets") or [{}])[0]
    # 四栏：模型请求 / 实际应用 / 越权回填 / 投影后残余
    assert rb.get("requested_basis") == "shot_replacements[].shot", rb
    assert row.get("target_changed") is True, row
    assert any("frame_layouts.ff" in p for p in row.get("requested") or []), row
    assert row.get("applied") == [], row
    assert any("frame_layouts.ff" in p for p in row.get("reverted") or []), row
    assert "residual_after_projection" in rb, rb
    residual = rb["residual_after_projection"]
    assert not residual.get("error"), residual
    unchanged = [f for t in rb.get("targets") or [] for f in t.get("unchanged_fields") or []]
    assert any("frame_layouts.ff" in f for f in unchanged), unchanged
    # 「目标改了、关联字段没跟着改」＝半条修复，必须单独列出来（消费方不必自己推）
    assert rb.get("half_repaired"), rb
    assert any("frame_layouts.ff" in f
               for h2 in rb["half_repaired"] for f in h2["fields"]), rb["half_repaired"]
