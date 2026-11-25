# -*- coding: utf-8 -*-
"""S21/S22：`compile_reallocated_plan` 的匿名自足夹具（两种不同内容结构）.

覆盖：合法重分配（替换/删除声音、调整承载、局部补画面）→ 投影/验收/预算通过；
错说话人、改冻结事实、缺承载、悬空引用、旧身份、旧核验结果六类拒收。
夹具全部为匿名最小情节，只依赖受控代码与原文常量，不读 `.tmp`/生产产物/网络。
"""
from __future__ import annotations

import copy

import pytest

from shared import presentation_contract as pc
from step01_script_gen import lightweight_plan as lw
from step01_script_gen.outline import plan_acceptance_problems

# ---- 夹具 A：纯对白结构（4 条承载声音 + 2 条无事实声音）----
NOVEL_A = ("镇外的石桥边有一间茶棚，棚下只摆着两张桌子。\n"
           "掌柜姓周，伙计姓陈，两人守着冷清的生意。\n"
           "周掌柜压低声音道：“货已经在昨夜换了地方，谁也找不到。”\n"
           "陈伙计皱眉道：“官府的人今早来过，问了桥下那条船。”\n"
           "周掌柜摆手道：“船上的痕迹我昨夜就抹了，他们问不出什么。”\n"
           "陈伙计叹道：“那就只剩等消息了。”\n"
           "周掌柜又说：“这茶棚的生意，一年不如一年。”\n"
           "陈伙计笑道：“今天的风倒是凉快。”\n")
L_A1 = "货已经在昨夜换了地方，谁也找不到。"
L_A2 = "官府的人今早来过，问了桥下那条船。"
L_A3 = "船上的痕迹我昨夜就抹了，他们问不出什么。"
L_A4 = "那就只剩等消息了。"

# ---- 夹具 B：旁白 + 对白混合、以画面承接（不同事件/人物/载体分布）----
NOVEL_B = ("雨停时，渡口的木船还拴在桩上。船夫蹲在船头补网，一言不发。\n"
           "岸边的妇人低声道：“昨夜那盏灯，熄得比往常早。”\n"
           "船夫抬头道：“灯油尽了，自然就熄了。”\n"
           "船夫又补了一句：“网补好了就该走了。”\n")
L_B1 = "昨夜那盏灯，熄得比往常早。"
L_B2 = "灯油尽了，自然就熄了。"
L_B3 = "雨停时，渡口的木船还拴在桩上。"


def _beat(text, src, kind="none", vt="", sp=""):
    return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
            "voice_text": vt, "speaker": sp}


def _answer_a():
    return {
        "characters": ["周掌柜", "陈伙计"], "emotional_arc": "警惕",
        "climax": "抹去痕迹", "end_hook": "等消息", "omittable": [],
        "events": [
            {"summary": "两人在茶棚密谈", "beats": [
                _beat("周掌柜压低声音说话", "周掌柜压低声音道", "dialogue", L_A1, "周掌柜"),
                _beat("陈伙计皱眉回话", "陈伙计皱眉道", "dialogue", L_A2, "陈伙计")]},
            {"summary": "周掌柜交代已抹痕迹", "beats": [
                _beat("周掌柜摆手", "周掌柜摆手道", "dialogue", L_A3, "周掌柜"),
                _beat("陈伙计叹气", "陈伙计叹道", "dialogue", L_A4, "陈伙计")]},
            {"summary": "闲话两句各自收声", "beats": [
                _beat("周掌柜说生意一年不如一年", "周掌柜又说", "dialogue",
                      "这茶棚的生意，一年不如一年。", "周掌柜"),
                _beat("陈伙计笑说风凉快", "陈伙计笑道", "dialogue",
                      "今天的风倒是凉快。", "陈伙计"),
                _beat("棚外无人经过", "棚下只摆着两张桌子。")]},
        ],
        "facts": [
            {"claim": "周掌柜说货昨夜换了地方，谁也找不到", "evidence": L_A1,
             "event": 1, "beat": 1, "carrier": "voice"},
            {"claim": "陈伙计说官府今早来问过桥下的船", "evidence": L_A2,
             "event": 1, "beat": 2, "carrier": "voice"},
            {"claim": "周掌柜说船上的痕迹昨夜已抹掉", "evidence": L_A3,
             "event": 2, "beat": 1, "carrier": "voice"},
            {"claim": "陈伙计说只剩等消息", "evidence": L_A4,
             "event": 2, "beat": 2, "carrier": "voice"},
        ],
    }


def _answer_b():
    return {
        "characters": ["妇人", "船夫"], "emotional_arc": "平静", "climax": "灯熄",
        "end_hook": "补网", "omittable": [],
        "events": [
            {"summary": "雨停后的渡口", "beats": [
                _beat("渡口木船拴在桩上", "雨停时，渡口的木船还拴在桩上。"),
                _beat("妇人的低语", "岸边的妇人低声道", "dialogue", L_B1, "妇人")]},
            {"summary": "船夫解释灯灭", "beats": [
                _beat("船夫抬头", "船夫抬头道", "dialogue", L_B2, "船夫"),
                _beat("船夫补一句", "船夫又补了一句", "dialogue",
                      "网补好了就该走了。", "船夫")]},
            {"summary": "船夫低头补网", "beats": [
                _beat("船夫蹲在船头继续补网", "船夫蹲在船头补网，一言不发。")]},
        ],
        "facts": [
            {"claim": "妇人说昨夜那盏灯熄得比往常早", "evidence": L_B1,
             "event": 1, "beat": 2, "carrier": "voice"},
            {"claim": "船夫说灯油尽了所以灯熄了", "evidence": L_B2,
             "event": 2, "beat": 1, "carrier": "voice"},
            {"claim": "雨停时渡口的木船拴在桩上", "evidence": L_B3,
             "event": 1, "beat": 1, "carrier": "visual"},
        ],
    }


def _base(novel, answer):
    plan, probs = lw.build_plan(answer, novel)
    assert plan is not None and probs == [], probs
    return plan


def _view(base_plan, spec):
    """在基线视图上替换声音/承载（其余冻结块原样复用）."""
    base_view, probs = pc.view_from_plan(base_plan)
    assert base_view is not None and probs == [], probs
    beats = []
    for i, (event_no, text, voice) in enumerate(spec["beats"], 1):
        uid = "b%02d" % i
        beats.append({"uid": uid, "text": text, "purpose": "推进", "evidence": "",
                      "source": {}, "event_uids": [base_view["frozen_events"][event_no - 1]],
                      "voice_ref": ({"kind": "dialogue", "uid": voice} if voice else
                                    {"kind": "visual", "uid": uid})})
    units = [{"uid": uid, "text": text, "source": {"text": text}}
             for uid, _spk, text in spec["voices"]]
    binds = [{"fact_uid": f["uid"],
              "carriers": [{"kind": k, "uid": (v if k == "dialogue" else "b%02d" % b)}
                           for b, k, v in spec["carriers"][f["uid"]]]}
             for f in base_view["frozen_facts"]]
    return {
        "schema": base_view["schema"], "mode": base_view["mode"],
        "event_binding": base_view.get("event_binding") or "unknown",
        "frozen_facts": base_view["frozen_facts"], "frozen_events": base_view["frozen_events"],
        "beats": beats, "dialogue_units": units, "narration_units": [],
        "screen_cards": [], "fact_bindings": binds,
        "hook_bindings": base_view.get("hook_bindings") or {"spoken_uids": [], "carriers": []},
        "evidence_updates": [], "plan_basis": base_view["plan_basis"],
        "carrier_capacity": max(len(x) for x in spec["carriers"].values()),
        "voice_speakers": {uid: spk for uid, spk, _t in spec["voices"]},
    }


SPEC_A = {
    "beats": [
        (1, "周掌柜伏在桌边，压低声音，眼睛盯着门口。", "d01"),
        (1, "陈伙计皱眉，手指在桌面上敲了一下。", "d02"),
        (2, "周掌柜摆手，袖口沾着水渍。", "d03"),
        (2, "陈伙计长叹一口气，望向棚外。", None),
    ],
    "voices": [("d01", "周掌柜", L_A1), ("d02", "陈伙计", L_A2),
               ("d03", "周掌柜", L_A3)],
    "carriers": {"f01": [(1, "dialogue", "d01")], "f02": [(2, "dialogue", "d02")],
                 "f03": [(3, "dialogue", "d03")], "f04": [(4, "visual", None)]},
}

SPEC_B = {
    "beats": [
        (1, "雨停后的渡口，木船拴在桩上，船夫蹲在船头补网。", None),
        (1, "妇人站在岸边，低声开口。", "d01"),
        (2, "船夫抬头，手上还捏着网线。", "d02"),
    ],
    "voices": [("d01", "妇人", L_B1), ("d02", "船夫", L_B2)],
    "carriers": {"f01": [(2, "dialogue", "d01")], "f02": [(3, "dialogue", "d02")],
                 "f03": [(1, "visual", None)]},
}


def _compile(novel, answer, spec, *, base_plan=None, novel_text=None):
    base = base_plan if base_plan is not None else _base(novel, answer)
    return lw.compile_reallocated_plan(base, _view(base, spec),
                                       novel_text=novel_text if novel_text is not None else novel)


@pytest.mark.parametrize("novel,answer,spec", [(NOVEL_A, _answer_a(), SPEC_A),
                                               (NOVEL_B, _answer_b(), SPEC_B)])
def test_compile_accepts_legal_reallocation(novel, answer, spec):
    base = _base(novel, answer)
    cand, probs, changes = lw.compile_reallocated_plan(base, _view(base, spec),
                                                       novel_text=novel)
    assert probs == [], probs
    assert cand is not None
    assert plan_acceptance_problems(cand, novel_text=novel) == []
    # 冻结内容保留
    from step01_script_gen.plan_feasibility import fact_evidence_text, strip_fact_carrier

    def _claims(plan):
        return [strip_fact_carrier(f).split("｜原文证据")[0] for f in plan["required_facts"]]

    assert _claims(cand) == _claims(base)
    assert [fact_evidence_text(f) for f in cand["required_facts"]] == \
        [fact_evidence_text(f) for f in base["required_facts"]]
    assert (cand.get("source_splits") or None) == (base.get("source_splits") or None)
    # 派生字段重建：声音条数/编号/载体标签/时长摘要
    assert len(cand["dialogue_lines"]) == len(spec["voices"])
    assert [u["echo_text"] for u in cand["voice_units"]] == cand["dialogue_lines"]
    assert all("B0/" not in f for f in cand["required_facts"])
    assert cand["time_budget"].startswith("台词约")
    # 旧核验失效
    assert cand["lightweight_plan"]["content_verification"] == "not_run"
    # 前后对照可读
    assert changes["accepted"] is True
    assert changes["voices"]["after_lines"] == len(spec["voices"])
    assert changes["voices"]["after_lines"] < changes["voices"]["before_lines"]
    # 投影回读是固定点
    view, vp = pc.view_from_plan(cand)
    assert vp == [] and pc.plan_from_view(view) == cand


def test_compile_rejects_wrong_speaker():
    bad = _view(_base(NOVEL_A, _answer_a()), SPEC_A)
    bad["voice_speakers"]["d01"] = "陈伙计"
    cand, probs, _ = lw.compile_reallocated_plan(_base(NOVEL_A, _answer_a()), bad,
                                                 novel_text=NOVEL_A)
    assert cand is None
    assert [p["code"] for p in probs] == ["realloc_speaker_attribution"]


def test_compile_rejects_frozen_fact_change():
    base = _base(NOVEL_A, _answer_a())
    bad = _view(base, SPEC_A)
    bad["frozen_facts"][0]["claim"] = "改过的说法"
    cand, probs, _ = lw.compile_reallocated_plan(base, bad, novel_text=NOVEL_A)
    assert cand is None
    assert "realloc_frozen_changed" in [p["code"] for p in probs]


def test_compile_rejects_missing_carrier():
    base = _base(NOVEL_A, _answer_a())
    bad = _view(base, SPEC_A)
    bad["fact_bindings"][0]["carriers"] = []
    cand, probs, _ = lw.compile_reallocated_plan(base, bad, novel_text=NOVEL_A)
    assert cand is None
    assert "carrier_fact_missing" in [p["code"] for p in probs]


def test_compile_rejects_dangling_ref():
    base = _base(NOVEL_A, _answer_a())
    bad = _view(base, SPEC_A)
    bad["fact_bindings"][0]["carriers"].append({"kind": "visual", "uid": "b99"})
    cand, probs, _ = lw.compile_reallocated_plan(base, bad, novel_text=NOVEL_A)
    assert cand is None
    assert "carrier_fact_beat_missing" in [p["code"] for p in probs]


def test_compile_rejects_stale_identity():
    base = _base(NOVEL_A, _answer_a())
    cand, probs, _ = lw.compile_reallocated_plan(base, _view(base, SPEC_A),
                                                 novel_text=NOVEL_B)
    assert cand is None
    assert "realloc_base_identity_invalid" in [p["code"] for p in probs]


def test_compile_does_not_inherit_old_verification():
    base = copy.deepcopy(_base(NOVEL_A, _answer_a()))
    base["plan_verification"] = {"status": "ok", "facts": [{"verdict": "supported"}]}
    cand, probs, _ = lw.compile_reallocated_plan(base, _view(base, SPEC_A),
                                                 novel_text=NOVEL_A)
    assert probs == [] and cand is not None
    assert "plan_verification" not in cand
    assert cand["lightweight_plan"]["content_verification"] == "not_run"
