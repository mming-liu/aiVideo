# -*- coding: utf-8 -*-
"""P0 优化与分镜窄字段补丁（Scoped Field Patch）回归测试（无网络）."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.utils.json_repair import (  # noqa: E402
    _strip_structural_trailing_commas,
    safe_parse_json,
)
from step01_script_gen.quality_issues import Issue  # noqa: E402
from step01_script_gen.repair_fields import (  # noqa: E402
    allowed_repair_fields,
    apply_scoped_field_updates,
)
from step02_video_gen.prompt_builder import (  # noqa: E402
    _build_seedance_concise_prompt,
    _make_request,
    derive_attempt_seed,
)
import step02_video_gen.submit_poll as sp  # noqa: E402


def _make_shot(index: int = 5) -> dict:
    return {
        "index": index,
        "duration": 6.0,
        "duration_sec": 6.0,
        "scene": "湖面小舟",
        "location": "湖心",
        "variation_type": "medium",
        "main_subject": "旅人甲",
        "camera_movement": "固定镜头",
        "ff_desc": "旅人甲仰面朝上双目紧闭躺在船头",
        "lf_desc": "守卫乙已丢下灯盏，纵身跃上船梢",
        "motion_desc": "0-2秒：旅人甲仰卧饮酒；2-4秒：守卫乙丢下灯盏；4-6秒：守卫乙纵身跃上船梢。",
        "prompt": "古风写实，湖面小舟，旅人甲仰卧饮酒，守卫乙跃上船梢。",
        "narration_segment": "守卫乙道：“凶手已经走远了。”",
        "subtitle": "凶手已经走远了。",
        "keyframes": [
            {"t_sec": 1.0, "desc": "旅人甲仰卧饮酒", "stage": "pre"},
            {"t_sec": 3.5, "desc": "守卫乙丢灯盏纵身跃起", "stage": "climax"},
            {"t_sec": 4.5, "desc": "守卫乙举灯盏站立倾听", "stage": "approach"},
        ],
        "frame_layouts": {
            "ff": [{"name": "旅人甲", "on_screen": True, "position": "画中", "pose": "仰卧", "props": ["酒壶"]}],
            "lf": [{"name": "守卫乙", "on_screen": True, "position": "画右", "pose": "跃上船梢", "props": []}],
            "kfs": [
                [{"name": "旅人甲", "on_screen": True, "position": "画中", "pose": "举杯", "props": ["酒壶"]}],
                [{"name": "守卫乙", "on_screen": True, "position": "画右", "pose": "脱手", "props": []}],
                [{"name": "守卫乙", "on_screen": True, "position": "画右", "pose": "站立倾听", "props": ["灯盏"]}],
            ],
        },
    }


class TestJsonRepairTrailingCommas(unittest.TestCase):
    def test_structural_trailing_commas_removed_without_corrupting_string_literals(self):
        raw = '{"dialogue": "他说：\\",}\\"，然后转身[,]", "items": [1, 2, ], "meta": {"ok": true, } }'
        repaired = _strip_structural_trailing_commas(raw)
        parsed = safe_parse_json(raw)
        self.assertEqual(parsed["dialogue"], '他说：",}"，然后转身[,]')
        self.assertEqual(parsed["items"], [1, 2])
        self.assertEqual(parsed["meta"], {"ok": True})
        self.assertIn('\\",}\\"', repaired)
        self.assertIn("[,]", repaired)


class TestScopedFieldPatch(unittest.TestCase):
    def test_apply_scoped_field_updates_dict_and_list_forms(self):
        shot = _make_shot(5)
        allowed = {"keyframes.2.desc", "keyframes.2.stage", "frame_layouts.kfs.2.0.props"}

        patched, err = apply_scoped_field_updates(
            shot,
            {
                "keyframes[2].desc": "守卫乙已在船梢稳住身形倾听",
                "keyframes[2].stage": "after",
                "frame_layouts.kfs.2.0.props": [],
            },
            allowed=allowed,
        )
        self.assertEqual(err, "")
        self.assertIsNotNone(patched)
        self.assertEqual(patched["keyframes"][2]["desc"], "守卫乙已在船梢稳住身形倾听")
        self.assertEqual(patched["keyframes"][2]["stage"], "after")
        self.assertEqual(patched["frame_layouts"]["kfs"][2][0]["props"], [])
        # Locked and sibling fields remain untouched
        self.assertEqual(patched["narration_segment"], shot["narration_segment"])
        self.assertEqual(patched["keyframes"][0], shot["keyframes"][0])

        # List form
        patched2, err2 = apply_scoped_field_updates(
            shot,
            [
                {"field_path": "keyframes.2.desc", "value": "守卫乙落定倾听"},
                {"field_path": "keyframes.2.stage", "value": "after"},
            ],
            allowed=allowed,
        )
        self.assertEqual(err2, "")
        self.assertEqual(patched2["keyframes"][2]["desc"], "守卫乙落定倾听")

    def test_apply_scoped_field_updates_rejects_unauthorized_path(self):
        shot = _make_shot(5)
        allowed = {"keyframes.2.desc"}
        patched, err = apply_scoped_field_updates(
            shot,
            {
                "keyframes[2].desc": "合法修改",
                "narration_segment": "偷改旁白",
            },
            allowed=allowed,
        )
        self.assertIsNone(patched)
        self.assertIn("越权字段补丁", err)
        self.assertIn("narration_segment", err)

    def test_apply_scoped_field_updates_syncs_duration_sec(self):
        shot = _make_shot(5)
        patched, err = apply_scoped_field_updates(
            shot,
            {"duration": 8.0},
            allowed={"duration"},
        )
        self.assertEqual(err, "")
        self.assertEqual(patched["duration"], 8.0)
        self.assertEqual(patched["duration_sec"], 8.0)

    def test_main_apply_shot_replacements_accepts_scoped_updates(self):
        import step01_script_gen.main as m

        shot5 = _make_shot(5)
        script = {"shots": [shot5], "characters": []}
        issue = Issue(
            code="model_audit_KF_ORDER_PROP_CONFLICT",
            severity="visual_risk",
            message="kf2 灯盏在丢后又出现",
            shot_id="5",
            frame_id="kf2",
            field_path="keyframes[2].desc",
            repair_targets=[
                {"shot_id": "5", "field_path": "keyframes[2].desc"},
                {"shot_id": "5", "field_path": "keyframes[2].stage"},
                {"shot_id": "5", "field_path": "frame_layouts.kfs.2.0.props"},
            ],
        )
        allowed = allowed_repair_fields([], [issue], target_shot_id="shot_5", shots_by_id={"shot_5": shot5})

        merged_script, count, rejected = m._apply_shot_replacements(
            script,
            replacements=[
                {
                    "shot_id": "5",
                    "updates": {
                        "keyframes[2].desc": "守卫乙空手立于船梢倾听动静",
                        "keyframes[2].stage": "after",
                        "frame_layouts.kfs.2.0.props": [],
                    },
                }
            ],
            picked={"shot_5"},
            allowed_by_target={"shot_5": allowed},
            expected_targets={"5"},
        )
        self.assertEqual(rejected, [])
        self.assertEqual(count, 1)
        out_shot = merged_script["shots"][0]
        self.assertEqual(out_shot["keyframes"][2]["desc"], "守卫乙空手立于船梢倾听动静")
        self.assertEqual(out_shot["keyframes"][2]["stage"], "after")
        self.assertEqual(out_shot["frame_layouts"]["kfs"][2][0]["props"], [])
        self.assertEqual(out_shot["narration_segment"], shot5["narration_segment"])
    def test_main_apply_shot_replacements_accepts_updates_plus_keyframe_additions(self):
        """同一镜可以「补一处字段 + 补一帧」：两件事互不替代（2026-11-06）。

        实测 f13 既缺一段动作帧、尾帧布局又漏了被抬的人；旧 `_stage` 只允许一种
        形态，模型只能二选一，另一半永远修不掉。
        """
        import step01_script_gen.main as m

        shot5 = _make_shot(5)
        script = {"shots": [shot5], "characters": []}
        merged, count, rejected = m._apply_shot_replacements(
            script,
            replacements=[{
                "shot_id": "5",
                "updates": {"lf_desc": "旅人甲仍仰卧船头，守卫乙扣住船梢稳住身形"},
                "keyframe_additions": [{
                    "frame": {"t_sec": 5.0, "desc": "守卫乙扣住船梢稳住身形", "stage": "after"},
                    "layout": [{"name": "守卫乙", "on_screen": True, "position": "画右",
                                "pose": "扣住船梢", "props": []}],
                }],
            }],
            picked={"shot_5"},
            allowed_by_target={"shot_5": {"lf_desc", "keyframes", "frame_layouts.kfs"}},
            expected_targets={"5"},
        )
        self.assertEqual(rejected, [])
        self.assertEqual(count, 1)
        out = merged["shots"][0]
        self.assertTrue(out["lf_desc"].startswith("旅人甲仍仰卧船头"))
        self.assertEqual(len(out["keyframes"]), 4)
        self.assertEqual(len(out["frame_layouts"]["kfs"]), 4)
        self.assertEqual(out["keyframes"][3]["desc"], "守卫乙扣住船梢稳住身形")

    def test_repair_example_prefers_keyframe_additions_over_whole_kf_table(self):
        """授权面含整个 `keyframes` 时，示例必须给成对补帧，不得诱导整表覆盖。"""
        import json as _json
        import step01_script_gen.main as m

        shot = _make_shot(5)
        example = _json.loads(m._repair_answer_example(
            {"shot_5": shot}, ["shot_5"],
            {"shot_5": {"keyframes", "frame_layouts.kfs", "lf_desc"}}))
        item = example["shot_replacements"][0]
        self.assertIn("keyframe_additions", item)
        self.assertEqual({p["field_path"] for p in item.get("updates", [])}, {"lf_desc"})
        frame, layout = item["keyframe_additions"][0]["frame"], item["keyframe_additions"][0]["layout"]
        self.assertIn("t_sec", frame)
        self.assertTrue(layout and "name" in layout[0])




def test_hand_only_motion_correction_is_accepted_without_source_window():
    """只换「左手/右手」的 motion 改写不该被「整章窗口」整段合回（2026-11-06）.

    纯动作镜头没有 narration_segment，window_strict 恒为 False；f6 只需把手别对齐
    本镜 ff_desc/关键帧。旧实现一律合回 → 该条永远修不掉。
    """
    from step01_script_gen.kf_structural_guard import narrow_motion_patch

    before = {"index": 6, "duration_sec": 6,
              "motion_desc": "0-2秒：甲按住乙；2-4秒：右手短刃扬起。",
              "keyframes": [], "frame_layouts": {"kfs": []}}
    after = copy.deepcopy(before)
    after["motion_desc"] = "0-2秒：甲按住乙；2-4秒：左手短刃扬起。"
    out, notes = narrow_motion_patch(before, after, canonical={"甲", "乙"},
                                    novel_window="", window_strict=False)
    assert out is not None, notes
    assert "左手" in out and "右手" not in out


def test_strong_own_score_is_not_flagged_as_window_mismatch():
    """本段已够像（>=0.55）时，不因邻段公共词密度更高判错位（2026-11-06）."""
    from step01_script_gen.kf_coverage import KfSlot

    borderline = KfSlot(index=1, desc="x", t_sec=3.0, own=1, best=0, scores=[0.78, 0.60])
    assert borderline.mismatched is False
    # 对照：本段明显不像时仍按原判据报错位
    weak = KfSlot(index=1, desc="x", t_sec=3.0, own=1, best=0, scores=[0.80, 0.30])
    assert weak.mismatched is True







def test_normalize_plan_refs_keeps_ambiguous_substring_utterances():
    """子串命中 ≠ 承载：两条台词有包含关系但表达不同意图时不得自动补引用.

    2026-11-07 复核 P1 反例：D1「先关门。」、D2「我说先关门，但你不要照做。」；
    镜头 1 说 D1、镜头 2 说 D2。归一化若按子串给镜头 2 补 D1，会凭空造出
    lines_order_violation（真实 run21 的第二份候选就是这样被改坏的）。
    """
    from step01_script_gen.plan_feasibility import normalize_plan_refs

    plan = {"dialogue_lines": ["先关门。", "我说先关门，但你不要照做。"],
            "shot_beats": ["节拍：甲关门。｜载体：台词1", "节拍：乙提醒。｜载体：台词2"]}
    script = {"shots": [
        {"index": 1, "narration_segment": "甲：先关门。",
         "plan_refs": {"beats": [1], "lines": [1]}},
        {"index": 2, "narration_segment": "乙：我说先关门，但你不要照做。",
         "plan_refs": {"beats": [2], "lines": [2]}},
    ]}
    assert normalize_plan_refs(script, plan) == 0
    assert script["shots"][1]["plan_refs"] == {"beats": [2], "lines": [2]}


def test_normalize_plan_refs_fixes_unambiguous_missing_beat():
    """无歧义时仍要补齐：本镜确实说了该台词、却没声明承载它的节拍 ⇒ 补引用.

    2026-11-07 复核 P1 反例：D1「先关门。」、D2「我说先关门，但你不要照做。」；
    镜头 1 说 D1、镜头 2 说 D2。归一化若按子串给镜头 2 补 D1，会凭空造出
    lines_order_violation（真实 run21 的第二份候选就是这样被改坏的）。
    """
    from step01_script_gen.plan_feasibility import normalize_plan_refs

    plan = {"dialogue_lines": ["先关门。"],
            "shot_beats": ["节拍：甲关门。｜载体：台词1"]}
    script = {"shots": [
        {"index": 1, "narration_segment": "甲：先关门。",
         "plan_refs": {"beats": [], "lines": []}},
    ]}
    assert normalize_plan_refs(script, plan) >= 1
    assert script["shots"][0]["plan_refs"] == {"beats": [1], "lines": [1]}




def test_normalize_plan_refs_is_idempotent():
    """归一化必须幂等：第二次调用产生零变更（S03 验收）."""
    from step01_script_gen.plan_feasibility import normalize_plan_refs

    plan = {"dialogue_lines": ["先关门。"],
            "shot_beats": ["节拍：甲关门。｜载体：台词1"]}
    script = {"shots": [{"index": 1, "narration_segment": "甲：先关门。",
                         "plan_refs": {"beats": [], "lines": []}}]}
    assert normalize_plan_refs(script, plan) >= 1
    assert normalize_plan_refs(script, plan) == 0


def test_voice_source_spans_offer_the_dialogue_body(monkeypatch):
    """证据是带归属语的整句时，台词本体也要作为来源片段（S02 验收）."""
    from step01_script_gen import plan_content_repair as pcr

    span = "乙沉住了气，缓缓说道：“先关门，再关窗。”"
    monkeypatch.setattr(pcr, "source_spans_by_fact", lambda _r: {"f1": [span]})
    view = {"frozen_facts": [{"uid": "f1", "claim": "甲吩咐", "evidence": span}]}
    spans = pcr._voice_source_spans(view, "f1", {"_": 1}, span, admitted=True)
    assert span in spans
    assert "先关门，再关窗。" in spans

def test_dialogue_body_is_offered_as_voice_source_and_reuse_target():
    """带归属语的整句要能引出「台词本体」：既作来源片段，也作复用目标（2026-11-07）。"""
    from step01_script_gen.plan_content_repair import _dialogue_bodies, _voice_targets

    span = "乙沉住了气，缓缓说道：“先关门，再关窗。”"
    assert _dialogue_bodies(span) == ["先关门，再关窗。"]
    assert _dialogue_bodies("没有引号的叙述句") == []
    view = {"frozen_facts": [{"uid": "f1", "claim": "甲吩咐关门",
                             "evidence": span}],
            "dialogue_units": [{"uid": "d01", "text": "先关门，再关窗。"}],
            "narration_units": []}
    targets = _voice_targets(view, "f1")
    assert {"kind": "dialogue", "uid": "d01"} in targets

def test_plan_dialogue_lines_are_never_rewritten():
    """计划台词读取只做清洗，**不得**用子串关系替换文本（2026-11-07 复核 P1）.

    反例：D1「明天必须离开这里。」与 D2「你说明天必须离开这里，但我不同意。」
    是两条独立主张；把 D1 换成 D2 会改写主张，并连带影响提示、预算与声音条目。
    子串关系不能证明两条声音身份相同——重复条目应由计划层按声音身份处理。
    """
    from step01_script_gen.plan_feasibility import plan_dialogue_lines

    d1 = "明天必须离开这里。"
    d2 = "你说明天必须离开这里，但我不同意。"
    assert plan_dialogue_lines({"dialogue_lines": [d1, d2]}) == [d1, d2]



def test_partial_quoted_dialogue_is_rejected():
    """引号内片段不得当成完整台词（2026-11-07 复核 P1）.

    计划「甲说：“先关门。”随后又说：“不要点灯。”」时，只写出第一句的稿子必须判破损。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["甲说：“先关门。”随后又说：“不要点灯。”"]}
    script = {"shots": [{"index": 1, "narration_segment": "甲：先关门。",
                         "subtitle": "甲：先关门。"}]}
    ok, broken = check_script_dialogue_integrity(script, plan)
    assert ok is False, broken


def test_dialogue_split_by_empty_shot_is_rejected():
    """跨镜拼回的台词必须落在连续镜头上（2026-11-07 复核 P1）.

    三镜承载「先关门。再关窗。」而中间镜旁白为空时，声音被空镜切断——
    旧实现把空镜从单元序列里滤掉，拼接照样成立。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    def _shot(i, text):
        return {"index": i, "narration_segment": text, "subtitle": text}

    plan = {"dialogue_lines": ["先关门。再关窗。"]}
    gap = {"shots": [_shot(1, "甲：先关门。"), _shot(2, ""), _shot(3, "甲：再关窗。")]}
    ok, broken = check_script_dialogue_integrity(gap, plan)
    assert ok is False
    assert any(b.get("reason") == "cross_shot_gap" for b in broken), broken
    adj = {"shots": [_shot(1, "甲：先关门。"), _shot(2, "甲：再关窗。")]}
    assert check_script_dialogue_integrity(adj, plan)[0] is True

def test_dialogue_gate_accepts_plan_line_with_attribution_prefix():
    """计划台词带「某某沉住了气，漫声说道：…」归属语时，仍按引号内台词本体匹配.

    2026-11-07 run15 实测：归属语含逗号 → `_split_speaker_prefix` 不把它当前缀
    （这是对「且慢，我告诉你：…」的防误切），整句成为 needle；剧本只写说的话就判
    not_found。补一条「引号内正文」形态，两侧仍过同一套归一。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["甲沉住了气，漫声说道：“乙方才那句话，谁听见了？”"]}
    script = {"shots": [{"index": 1, "narration_segment": "乙方才那句话，谁听见了？",
                         "subtitle": "乙方才那句话，谁听见了？"}]}
    ok, broken = check_script_dialogue_integrity(script, plan)
    assert ok, broken

class TestRepairFieldAuthorizationNarrowing(unittest.TestCase):
    def test_kf_segment_missing_no_longer_authorizes_whole_frame_layouts(self):
        from step01_script_gen.repair_fields import _REPAIR_CODE_FIELDS

        fields = _REPAIR_CODE_FIELDS["kf_segment_missing"]
        self.assertIn("frame_layouts.kfs", fields)
        self.assertNotIn("frame_layouts", fields)
        self.assertEqual(_REPAIR_CODE_FIELDS["weapon_hand_conflict"], ())


class TestVideoGenP0Optimizations(unittest.TestCase):
    def test_derive_attempt_seed_offsets_on_qc_retry(self):
        base = 12345678
        self.assertEqual(derive_attempt_seed(base, 0), base)
        s1 = derive_attempt_seed(base, 1)
        s2 = derive_attempt_seed(base, 2)
        self.assertNotEqual(s1, base)
        self.assertNotEqual(s2, base)
        self.assertNotEqual(s1, s2)
        # Deterministic across calls
        self.assertEqual(derive_attempt_seed(base, 1), s1)

        shot = _make_shot(5)
        req0 = _make_request(None, "测试 prompt", [], copy.deepcopy(shot), retry_attempt=0)
        req1 = _make_request(None, "测试 prompt", [], copy.deepcopy(shot), retry_attempt=1)
        self.assertIsNotNone(req0.seed)
        self.assertNotEqual(req0.seed, req1.seed)

    def test_seedance_concise_prompt_normalizes_ff_and_lf_desc(self):
        shot = _make_shot(5)
        shot["ff_desc"] = "旅人甲正在缓慢地举起酒壶一边喝酒一边转头看向湖面"
        prompt = _build_seedance_concise_prompt(shot, characters=[])
        self.assertIn("【视频起点】", prompt)
        self.assertEqual(shot["ff_desc"], shot["ff_desc"].strip())

    def test_get_shot_hash_tracks_t_sec_changes(self):
        shot1 = _make_shot(5)
        shot2 = copy.deepcopy(shot1)
        shot2["keyframes"][1]["t_sec"] = 4.1  # Changed t_sec without time_ratio
        script = {"characters": []}
        h1 = sp._get_shot_hash(shot1, script)
        h2 = sp._get_shot_hash(shot2, script)
        self.assertNotEqual(h1, h2, "修改 keyframes[i].t_sec 必须使视频缓存哈希失效")

    def test_adaptive_polling_defaults(self):
        self.assertLessEqual(sp.INITIAL_POLL_DELAY, 30)
        self.assertLessEqual(sp.POLL_BASE_INTERVAL, 30)
        self.assertLessEqual(sp.POLL_MAX_INTERVAL, 60)


if __name__ == "__main__":
    unittest.main()


import asyncio
import json
import pytest


def test_duration_patch_survives_the_real_merge():
    from step01_script_gen import main as m
    shot = _make_shot()
    result, count, rejected = m._apply_shot_replacements(
        {"shots": [shot]}, [{"shot_id": "5", "updates": {"duration": 8.0}}],
        {"shot_5"}, allowed_by_target={"shot_5": {"duration"}}, expected_targets={"5"})
    assert rejected == [] and count == 1
    assert result["shots"][0]["duration"] == result["shots"][0]["duration_sec"] == 8.0
    assert shot["duration"] == 6.0


@pytest.mark.parametrize("updates", [
    [{"field_path": "ff_desc", "value": "new", "authority": True}],
    [{"field_path": "keyframes[0].desc", "value": "first"},
     {"field_path": "keyframes.0.desc", "value": "second"}],
])
def test_ambiguous_or_unknown_patch_fields_are_rejected(updates):
    result, reason = apply_scoped_field_updates(_make_shot(), updates, allowed={"ff_desc", "keyframes.0.desc"})
    assert result is None and reason


def test_patch_requires_explicit_authorization():
    result, reason = apply_scoped_field_updates(_make_shot(), {"ff_desc": "new"})
    assert result is None and reason


def test_two_replacement_forms_cannot_hide_an_unauthorized_update():
    from step01_script_gen import main as m
    shot = _make_shot()
    _, count, rejected = m._apply_shot_replacements(
        {"shots": [shot]}, [{"shot_id": "5", "shot": shot,
         "updates": {"narration_segment": "changed"}}], {"shot_5"},
        allowed_by_target={"shot_5": {"ff_desc"}}, expected_targets={"5"})
    assert count == 0 and rejected


def test_repair_request_schema_exposes_both_compact_forms():
    from _text_gen.deepseek_schema import REPAIR_SHOT_REPLACEMENT_SCHEMA
    schema = REPAIR_SHOT_REPLACEMENT_SCHEMA["schema"]
    branches = schema["properties"]["shot_replacements"]["items"]["oneOf"]
    keys = [set(branch["properties"]) for branch in branches]
    assert {"shot_id", "shot"} in keys
    assert {"shot_id", "updates"} in keys
    assert {"shot_id", "keyframe_additions"} in keys


def test_scoped_prompt_example_uses_only_authorized_current_values():
    from step01_script_gen import main as m
    shot = _make_shot()
    example = json.loads(m._repair_answer_example({"shot_5": shot}, ["shot_5"],
                         {"shot_5": {"keyframes.2.desc", "keyframes.2.stage"}}))
    item = example["shot_replacements"][0]
    assert "shot" not in item
    assert {p["field_path"] for p in item["updates"]} == {"keyframes.2.desc", "keyframes.2.stage"}


def test_seedance_normalizers_are_consumed_without_mutating_input(monkeypatch):
    from shared import style_prefix
    shot = _make_shot()
    before = copy.deepcopy(shot)
    seen = []
    def normalize_ff(text, current, characters):
        seen.append(("ff", text, characters))
        return "anonymous start"
    def normalize_lf(text, current, characters):
        seen.append(("lf", text, characters))
        return "anonymous end"
    monkeypatch.setattr(style_prefix, "normalize_ff_desc", normalize_ff)
    monkeypatch.setattr(style_prefix, "normalize_lf_desc", normalize_lf)
    chars = [{"name": "role A"}]
    prompt = _build_seedance_concise_prompt(shot, characters=chars)
    assert "anonymous start" in prompt and "anonymous end" in prompt
    assert [x[0] for x in seen] == ["ff", "lf"]
    assert all(x[2] is chars for x in seen)
    assert shot == before


def test_qc_counter_is_idempotent_for_the_same_failed_task(tmp_path):
    sp._record_qc_failure(tmp_path, "task-a")
    sp._record_qc_failure(tmp_path, "task-a")
    assert sp._read_qc_retry_state(tmp_path)["count"] == 1
    sp._record_qc_failure(tmp_path, "task-b")
    assert sp._read_qc_retry_state(tmp_path)["count"] == 2


@pytest.mark.parametrize("body", ["truncated", "-1", "true", '{"count": "2"}'])
def test_invalid_qc_counter_blocks_new_submission(tmp_path, monkeypatch, body):
    (tmp_path / "qc_fail_count.txt").write_text(body, encoding="utf-8")
    class Adapter:
        async def submit_task(self, req):
            raise AssertionError("must not submit")
    monkeypatch.setattr(sp, "_build_prompt", lambda *a, **k: "prompt", raising=False)
    result = asyncio.run(sp._submit_video(_make_shot(), tmp_path, tmp_path / "video.mp4", [],
                        "hash", Adapter(), asyncio.Semaphore(1), {"characters": []}, 5))
    assert result is None


def test_qc_counter_atomic_write_failure_preserves_old_state(tmp_path, monkeypatch):
    import shared.json_io as json_io
    sp._record_qc_failure(tmp_path, "task-a")
    before = (tmp_path / "qc_fail_count.txt").read_bytes()
    def fail(*a, **k):
        raise OSError("disk unavailable")
    monkeypatch.setattr(json_io, "atomic_dump_json", fail)
    with pytest.raises(OSError):
        sp._record_qc_failure(tmp_path, "task-b")
    assert (tmp_path / "qc_fail_count.txt").read_bytes() == before


from tests.step01_script_gen.test_repair_targets import a3_commit_env


def test_actual_repair_request_and_commit_consume_scoped_updates(a3_commit_env, monkeypatch):
    from step01_script_gen import main as m
    from tests.step01_script_gen.test_repair_targets import _a3_patch, _a3_chain_script, _a3_has_phantom
    holder, service = _a3_patch(monkeypatch)
    original_text = service._repair_text
    def scoped_text():
        body = json.loads(original_text())
        item = body["shot_replacements"][0]
        return json.dumps({"shot_replacements": [{"shot_id": item["shot_id"],
                          "updates": {"frame_layouts.kfs.0": item["shot"]["frame_layouts"]["kfs"][0]}}]}, ensure_ascii=False)
    monkeypatch.setattr(service, "_repair_text", scoped_text)
    original_generate = service.generate
    seen = []
    def generate(request):
        if (request.json_schema or {}).get("name") == "repair_shot_replacements":
            seen.append(request)
        return original_generate(request)
    monkeypatch.setattr(service, "generate", generate)
    result, summary = m._commit_step01_outputs(_a3_chain_script(), "")
    assert summary["status"] == "COMMITTED" and not _a3_has_phantom(result)
    assert len(seen) == 1
    for text in [seen[0].user_prompt] + [x["content"] for x in seen[0].user_inputs or [] if "格式：" in x["content"]]:
        if "格式：" in text:
            example = json.JSONDecoder().raw_decode(text.split("格式：", 1)[1])[0]
            assert "updates" in example["shot_replacements"][0]
    assert (a3_commit_env / "step01_commit.json").is_file()


def test_submission_and_frozen_request_use_persisted_retry_seed(tmp_path, monkeypatch):
    from step02_video_gen import prompt_builder as pb
    sp._record_qc_failure(tmp_path, "failed-task")
    shot = _make_shot()
    script = {"characters": [], "shots": [shot]}
    monkeypatch.setattr(pb, "_build_prompt", lambda *a, **k: "prompt")
    from tests.enhancements.video_stub_provider import StubVideoService

    class CapturingService(StubVideoService):
        def __init__(self):
            super().__init__()
            self.requests = []

        def prepare_request(self, req, context=None):
            self.requests.append(req)
            return super().prepare_request(req, context)

    class Adapter:
        def __init__(self):
            self.service = CapturingService()

    adapter = Adapter()
    async def poll(*a, **k):
        return True
    monkeypatch.setattr(sp, "_poll_video", poll)
    async def run():
        task = await sp._submit_video(shot, tmp_path, tmp_path / "video.mp4", [], "hash",
                                     adapter, asyncio.Semaphore(1), script, 5)
        assert await task is True
    asyncio.run(run())
    frozen, request = sp.build_frozen_for_shot(shot=shot, script=script, video_adapter=None,
        frame_paths=[], mode="A", shot_dir=tmp_path, prompt="prompt")
    base_request = pb._make_request(None, "prompt", [], shot)
    assert adapter.service.requests[0].seed == request.seed != base_request.seed
    assert sp._read_qc_retry_state(tmp_path)["count"] == 1


@pytest.mark.parametrize("updates", [{"ff_desc": None}, {"duration": True}, {"keyframes": "wrong type"}])
def test_invalid_value_types_are_rejected_before_merge(updates):
    result, reason = apply_scoped_field_updates(_make_shot(), updates, allowed=set(updates))
    assert result is None and reason


@pytest.mark.parametrize("persist_fails", [False, True])
def test_production_poll_records_retry_before_deleting_failed_video(tmp_path, monkeypatch, persist_fails):
    from types import SimpleNamespace
    video = tmp_path / "video.mp4"
    video.write_bytes(b"rejected video")
    (tmp_path / "prompt_hash.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "_qc_video_product_result", lambda *a, **k:
                        SimpleNamespace(status="failed", reason="fixture rejection", checks=[]))
    if persist_fails:
        def fail(*a, **k):
            raise OSError("disk unavailable")
        monkeypatch.setattr(sp, "_record_qc_failure", fail)
    class Adapter:
        async def fetch_result(self, req, task_id):
            return SimpleNamespace(success=True, save_path=str(video))
        async def submit_task(self, req):
            raise AssertionError("QC must not resubmit")
    async def run():
        return await sp._poll_video(Adapter(), asyncio.Semaphore(1), SimpleNamespace(duration=6),
                                    "failed-task", 5, tmp_path, video, 1, "hash")
    assert asyncio.run(run()) is False
    assert not (tmp_path / "prompt_hash.txt").exists()
    if persist_fails:
        assert video.exists()
        assert not (tmp_path / "qc_fail_count.txt").exists()
    else:
        assert not video.exists()
        assert sp._read_qc_retry_state(tmp_path)["count"] == 1


@pytest.mark.parametrize("invalid_shot", [False, 42, "invalid"])
def test_non_object_full_replacement_is_not_counted_as_applied(invalid_shot):
    from step01_script_gen import main as m
    _, count, rejected = m._apply_shot_replacements(
        {"shots": [_make_shot()]}, [{"shot_id": "5", "shot": invalid_shot}],
        {"shot_5"}, allowed_by_target={"shot_5": {"ff_desc"}})
    assert count == 0 and rejected
