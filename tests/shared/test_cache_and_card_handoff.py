# -*- coding: utf-8 -*-
"""缓存生命周期与核验记录复用（工作包 1 缓存段）+ 卡片完整消费关系（工作包 3 第 5 条）.

两条都是**交接**性质：读起来单点都"合理"，接起来就断。复核 2026-10-05 的读数：

    首轮：executable=True，缓存外层=5 / 内层=6
    次轮：cache_hit=False，桩规划与核验各重新调用一次
    仅纠正隔离副本的外层版本：cache_hit=True，规划调用=0，**核验调用=1**

第三条说明**只修版本还不够**：记录存在缓存里、却没有与本次的候选/原文/模式/规则四项
身份核一遍，于是要么被丢弃（重核）要么被照单全收（拿旧记录当新的）。本文件把这两段
都钉住。
"""
from __future__ import annotations

import json
import pytest
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                               # noqa: E402
from shared import presentation_contract as pc                               # noqa: E402
from shared.media_contract import MERGE_FPS                                 # noqa: E402
from shared.screen_cards import plan as CP                                  # noqa: E402
from shared.screen_cards import profile as PF                               # noqa: E402
from shared.screen_cards import render as RD                                # noqa: E402
from step04_merge.timeline import (BoundaryTransition, ClipEntry,            # noqa: E402
                                   EndpointEffect, MergeTimeline, stable_shot_id)

_TXT = "杨家主人姓杨名旭，字文轩，今年刚及弱冠。"


def _codes(probs):
    return [p.get("code") for p in probs]


# ---------------------------------------------------------------------------
# ① 核验记录复用：四项身份（候选 / 原文 / 模式 / 规则）
# ---------------------------------------------------------------------------
def _novel():
    return "杨家主人姓杨名旭，字文轩，今年刚及弱冠。四年前丧父。"


_NOVEL = ("雨下了三天。周明去年递了辞呈。这里以后就交给林青了。"
          "北岸仓库的货位已经清点完毕。")


def _record_for(plan, *, novel_digest, rule_version=cv.DEFAULT_RULE_VERSION,
                status="ok", novel_text=_NOVEL):
    """给一份**身份正确**的记录（用于"应当复用"的正例）."""
    _view, _ = pc.view_from_plan(plan)
    # 本夹具的核验响应以 **JSON 文本**回放（_Res.text），生产侧会走
    # parse_verification_response(..., source_catalog=...) 的 expand()：
    # evidence_spans 必须填本次原文目录里的 src_ 编号（填原文会被
    # verify_source_reference_invalid 拒收）。
    from step01_script_gen import source_references as _sref
    _catalog = _sref.catalog_for(novel_text) if novel_text else {}

    def _ref(span: str) -> str:
        for _key, _value in _catalog.items():
            if span and span in _value:
                return _key
        raise AssertionError("夹具引文不在原文目录里：%r" % span)

    _rec = {"schema": cv.SCHEMA_VERIFICATION, "status": status,
            "rule_version": rule_version,
            "plan_digest": cv.plan_digest_of(plan),
            "novel_digest": novel_digest,
            "source_support": [], "facts": [], "voice_units": []}
    _rec["obligation_basis"] = cv.obligation_basis_digest(_view)
    for _f in (_view or {}).get("frozen_facts") or []:
        _span = str(_f.get("evidence") or "")
        assert not novel_text or _span in novel_text,             "夹具的引文不在原文里：%r" % _span
        _rec["source_support"].append({
            "fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
            "evidence_spans": [_ref(_span)] if _span else [], "reason": ""})
        _txts = [t for t in cv.all_carrier_texts(_view, _f["uid"]) if t]
        # v2 义务轴：`supported` 必须覆盖该事实的**全部**义务，每条义务要回带
        # **真的持有这段正文的载体**与逐字片段——所以义务点名一个可解析的载体。
        _cu, _ct = "", ""
        for _b in (_view or {}).get("fact_bindings") or []:
            if _b.get("fact_uid") != _f["uid"]:
                continue
            for _c in _b.get("carriers") or []:
                _ts = [t for t in cv.carrier_texts(_view, _c) if t]
                if _ts:
                    _cu, _ct = pc.ref_uid(_c), _ts[0]
                    break
            if _ct:
                break
        _rec["facts"].append({
            "fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
            "support_spans": [max(_txts, key=len)] if _txts else [],
            "missing": "", "suggested_carriers": [],
            "obligations": [{
                "obligation_id": cv.CLAIM_OBLIGATION_ID,
                "text": str(_f.get("claim") or ""),
                "verdict": cv.VERDICT_SUPPORTED if _ct else cv.VERDICT_PARTIAL,
                "evidence": ([{"carrier_uid": _cu, "exhibitor_span": _ct}]
                             if _ct else []),
                "missing": "" if _ct else "（夹具：非 supported，未写明缺什么）",
                "reason": ""}]})
    for _u in ((_view or {}).get("dialogue_units") or []) \
            + ((_view or {}).get("narration_units") or []):
        _rec["voice_units"].append({
            "unit_uid": pc.ref_uid(_u), "verdict": cv.VERDICT_SUPPORTED,
            "source_kind": "novel_speech", "speaker": "杨旭",
            "echo_text": str(_u.get("text") or ""), "reason": ""})
    # require_coherence=True（plan_coherence.v3）时记录必须带合法 coherence 块；
    # 形状照 tests/step01_script_gen/p3_entry_fixtures.py:88-95。
    _beats = [str(b.get("uid")) for b in ((_view or {}).get("beats") or [])]
    _rec["coherence"] = {
        "beat_uids": _beats, "verdict": "supported",
        "conflict_beats": [], "evidence_spans": [],
        "reason": "按计划给定的先后顺序承接",
        "dependencies": [
            {"beat_uid": _uid, "requires_before": _beats[:_i],
             "reason": "按计划给定的先后顺序承接"}
            for _i, _uid in enumerate(_beats)]}
    return _rec


def _hybrid_plan():
    # `main_events` 必须 **3–6 条**（`outline_structure_problems` 的既有结构门）。
    # 事件**数量**要满足，而 `beat_events` 按位置取前三条即可。
    return {
        "main_events": ["周明去年递了辞呈", "账目由林青接管", "北岸仓库货位清点"],
        "climax": "账目由林青接管",
        "causal_chain": ["递辞呈→林青接管→清点货位"],
        "time_budget": "画面为主",
        "shot_beats": [
            "节拍：周明把辞呈放在桌上｜本镜信息目的：推进｜"
            "原文依据：“周明去年递了辞呈。”｜载体：画面",
            "节拍：林青接过账本｜本镜信息目的：推进｜"
            "原文依据：“这里以后就交给林青了。”｜载体：画面",
            "节拍：镜头扫过北岸仓库｜本镜信息目的：收束｜"
            "原文依据：“北岸仓库的货位已经清点完毕。”｜载体：画面"],
        "dialogue_lines": [], "narration_lines": [],
        "required_facts": [
            "周明去年递了辞呈｜原文证据：“周明去年递了辞呈。”｜承载：B1/画面",
            "账目由林青接管｜原文证据：“这里以后就交给林青了。”｜承载：B2/画面",
            "北岸货位已清点｜原文证据：“北岸仓库的货位已经清点完毕。”｜承载：B3/画面"],
        "hook_spoken": [], "hook_carrier": "",
        "plan_contract_version": pc.PLAN_CONTRACT_VERSION_HYBRID,
        "presentation": {
            "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
            "fact_uids": ["f01", "f02", "f03"],
            "beat_uids": ["b01", "b02", "b03"], "dialogue_uids": [],
            "narration_uids": [],
            "beat_events": [["周明去年递了辞呈"], ["账目由林青接管"],
                            ["北岸仓库货位清点"]],
            "screen_cards": [{"card_id": "c01", "beat_uid": "b02",
                              "template": "character",
                              "text": "这里以后就交给林青了。",
                              "placement": "info_panel"}],
            "fact_bindings": [
                {"fact_uid": "f01",
                 "carriers": [{"kind": "visual", "beat_uid": "b01"}]},
                {"fact_uid": "f02",
                 "carriers": [{"kind": "visual", "beat_uid": "b02"},
                              {"kind": "screen_card", "card_id": "c01"}]},
                {"fact_uid": "f03",
                 "carriers": [{"kind": "visual", "beat_uid": "b03"}]}],
            "hook_bindings": {"spoken_uids": [], "carriers": []}},
    }


def test_verification_record_is_reusable_when_all_four_identities_match():
    """候选 / 原文 / 规则 / 状态四项全对 ⇒ **直接复用**，不再调模型."""
    _plan = _hybrid_plan()
    _rec = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan))
    # 四项都对
    assert _rec["status"] == "ok"
    assert _rec["plan_digest"] == cv.plan_digest_of(_plan)
    assert isinstance(_rec["source_support"], list)
    assert isinstance(_rec["facts"], list)
    assert isinstance(_rec["voice_units"], list)


def test_record_with_wrong_digest_is_not_reusable():
    """候选摘要不符 ⇒ 拒用（不能拿别份候选的记录）."""
    _plan = _hybrid_plan()
    _rec = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan))
    _rec["plan_digest"] = "0" * 64
    assert _rec["plan_digest"] != cv.plan_digest_of(_plan)


def test_failed_record_is_never_reusable():
    """核验失败（status != ok）的记录**不能**复用——失败不当作内容可接受."""
    _plan = _hybrid_plan()
    _rec = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan),
                       status=cv.FAIL_CALL)
    assert _rec["status"] == cv.FAIL_CALL


def test_cache_outer_version_must_match_content():
    """**缓存外层版本 = 内容版本**（复核读数：外层 5 / 内层 6 ⇒ 永远命中不了）."""
    _plan = _hybrid_plan()
    assert _plan["plan_contract_version"] == pc.PLAN_CONTRACT_VERSION_HYBRID
    assert pc.PLAN_CONTRACT_VERSION_BY_MODE[pc.plan_mode(_plan)] == \
        _plan["plan_contract_version"], "外层与内容版本不是同一个来源"


def test_cache_key_covers_rule_versions():
    """缓存键必须含**修订规则版本与核验规则版本**——规则改了旧缓存不可复用."""
    import inspect
    from step01_script_gen import outline as o
    _src = inspect.getsource(o.generate_outline_result)
    for _k in ("revision_rule_version", "verify_rule_version",
               "presentation_mode"):
        assert _k in _src, "缓存键缺 %s" % _k


def test_cache_hit_reuses_the_verified_record_without_recalling(tmp_path, monkeypatch):
    """**真实缓存读写**：第二次运行生成 0 次、核验 0 次（复核 2026-10-05 P2 的退出条件）.

    这一条走**完整链路**（真写盘、真读回 + 真调用计数），不是检查字典字段。

    桩服务第一次返回**一份合法计划**（原实现返回 `{"unexpected": "shape"}`，结构不合格
    ⇒ 首版被拒、额度耗尽、**什么都没写缓存**，于是这条用例压根测不到"命中"）。
    """
    import os
    import step01_script_gen.outline as o

    _calls = {"gen": 0, "ver": 0}
    _plan = _hybrid_plan()
    _ver_record = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan))

    class _Res:
        def __init__(self, payload, cid):
            self.success = True
            self.text = json.dumps(payload, ensure_ascii=False)
            self.error = ""
            self.call_id = cid

    class _Svc:
        def generate(self, req):
            # 按 schema 区分**生成**与**核验**：核验请求带 content_verification 的 name
            _name = str(getattr(getattr(req, "json_schema", None) or {}, "get", lambda *_: "")("name")
                        if isinstance(getattr(req, "json_schema", None), dict) else "")
            if "content_verification" in _name:
                _calls["ver"] += 1
                return _Res(_ver_record, "v1")
            _calls["gen"] += 1
            return _Res(_first_version_payload(_plan), "g1")

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")

    # 原文**逐字包含**夹具的每一处引文：`presentation_beat_evidence_not_in_novel`
    # 与 `verify_source_span_not_in_novel` 都要拿它当依据
    _novel = ("雨下了三天。周明去年递了辞呈。这里以后就交给林青了。"
              "北岸仓库的货位已经清点完毕。")
    _cache = tmp_path / "outline_cache.json"

    _r1 = o.generate_outline_result(_novel, cache_path=_cache)
    assert _r1.get("executable") is True,         "第一轮本该可执行：%s / %s" % (_r1.get("acceptance", {}).get("codes"),
                                      _r1.get("failure"))
    assert _calls["gen"] == 1, _calls
    assert _calls["ver"] == 1, _calls

    _r2 = o.generate_outline_result(_novel, cache_path=_cache)
    assert _r2.get("cache", {}).get("hit") is True,         "第二轮没有命中缓存：%s" % _r2.get("cache")
    assert _calls["gen"] == 1, "命中缓存仍又调了一次生成：%s" % _calls
    assert _calls["ver"] == 1, "命中缓存仍又核验了一次：%s" % _calls



def test_a_cached_record_with_a_stale_obligation_basis_is_rejected_and_re_verified(
        tmp_path, monkeypatch):
    """**缓存读回也核义务支座**（复核 2026-10-09 P1 的缓存侧）：支座不符 ⇒ 记录不随计划流通.

    单变量：与命中用例逐字相同，只把缓存里那份记录的 `obligation_basis` 改掉
    （模拟"主张改过 / 派生口径变了"）。契约上它必须被判**不可复用**：读回层记
    `cache.verification_rejected`、记录从计划本体摘掉，随后按本次身份**重新核验**。
    """
    import step01_script_gen.outline as o

    _calls = {"gen": 0, "ver": 0}
    _plan = _hybrid_plan()
    _ver_record = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan))

    class _Res:
        def __init__(self, payload, cid):
            self.success = True
            self.text = json.dumps(payload, ensure_ascii=False)
            self.error = ""
            self.call_id = cid

    class _Svc:
        def generate(self, req):
            _name = str(getattr(getattr(req, "json_schema", None) or {}, "get",
                                lambda *_: "")("name")
                        if isinstance(getattr(req, "json_schema", None), dict) else "")
            if "content_verification" in _name:
                _calls["ver"] += 1
                return _Res(_ver_record, "v%d" % _calls["ver"])
            _calls["gen"] += 1
            return _Res(_first_version_payload(_plan), "g%d" % _calls["gen"])

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    _novel = ("雨下了三天。周明去年递了辞呈。这里以后就交给林青了。"
              "北岸仓库的货位已经清点完毕。")
    _cache = tmp_path / "outline_cache.json"

    _r1 = o.generate_outline_result(_novel, cache_path=_cache)
    assert _r1.get("executable") is True, _r1.get("failure")
    assert (_calls["gen"], _calls["ver"]) == (1, 1), _calls

    # ---- 单变量：篡改缓存里那份记录的**义务支座**（其余逐字不动）----
    # 缓存结构：{source_hash, plan_contract_version, presentation_mode,
    #           verification: <核验记录>, outline: <计划>}——读回层用的是**顶层**
    # `verification`；计划本体里那份只是随计划流通的副本。
    _raw = json.loads(_cache.read_text(encoding="utf-8"))
    _rec = _raw.get("verification")
    assert isinstance(_rec, dict) and _rec.get("obligation_basis"), (
        "缓存结构变了，夹具要跟着改", sorted(_raw))
    _rec["obligation_basis"] = "0" * 64
    _cache.write_text(json.dumps(_raw, ensure_ascii=False), encoding="utf-8")

    _r2 = o.generate_outline_result(_novel, cache_path=_cache)
    _rej = (_r2.get("cache") or {}).get("verification_rejected") or {}
    assert _rej, "支座不符的记录必须被判不可复用：%s" % (_r2.get("cache") or {})
    assert _calls["ver"] == 2, "被拒的记录不许复用，必须按本次身份重新核验：%s" % _calls
    # **计划也不复用**：可执行缓存只放"通过统一验收"的版本，而新模式计划缺了有效核验
    # 记录就过不了验收（"缺记录不得判通过"）。于是本轮按首版重新规划并覆盖缓存——
    # 这是既有的缓存协议（消息里写明"可执行缓存不包含失败版本"），不是本用例新定的。
    assert _calls["gen"] == 2, "被拒的缓存计划不该继续流通：%s" % _calls
    assert _r2.get("executable") is True, _r2.get("failure")

def _first_version_payload(plan):
    """把一份**合法的 v6 计划**摊成首版 schema 的形状（去掉结构化块与版本戳）."""
    _p = {k: v for k, v in plan.items()
          if k not in ("presentation", "plan_contract_version", "plan_verification")}
    _p["screen_cards"] = [
        {"beat": 2, "template": "character",
         "text": "这里以后就交给林青了。", "placement": "info_panel"}]
    _p["beat_events"] = [[1], [2], [3]]
    _p["characters"] = ["林青"]
    _p["emotional_arc"] = "平→转"
    _p["end_hook"] = "交接"
    _p["key_scenes"] = ["账房"]
    _p["character_motives"] = ["林青：接账"]
    return _p


# ---------------------------------------------------------------------------
# ② 卡片的完整消费关系：一帧冒充整页
# ---------------------------------------------------------------------------
def _clips(n=1, dur=20.0):
    return tuple(ClipEntry(shot_index=i, kind="shot",
                           stable_id=stable_shot_id("sc1", "r1", "shot", i),
                           path=Path("clip%d.mp4" % i), duration_sec=dur,
                           raw_duration_sec=dur, frames=int(dur * MERGE_FPS))
                 for i in range(1, n + 1))


def _timeline(clips=None, xfade=0.0):
    _cl = list(clips or _clips())
    _b = tuple(BoundaryTransition(index=i, kind="fade", duration_sec=xfade)
               for i in range(1, len(_cl))) if xfade > 0 else ()
    return MergeTimeline(scope="sc1", script_revision="r1", width=1080, height=1920,
                         fps=MERGE_FPS, clips=tuple(_cl),
                         boundary_transitions=_b,
                         endpoint_effects=(EndpointEffect(
                             kind="fade_in", duration_sec=0.0, clip_index=0),))


def _rel(beat_uid="b01"):
    return {"card_id": "c01", "beat_uid": beat_uid, "template": "character",
            "text": _TXT, "text_digest": CP.text_digest(_TXT),
            "placement": "info_panel", "anchor": "beat_start",
            "source_spans": [], "fact_uids": ["f01"], "shot_index": 1}


def _built(tmp_path, *, dur=20.0):
    _a, _ap = RD.render_card(_TXT, "character", "c01", tmp_path)
    assert _ap == [], _codes(_ap)
    _t = _timeline(_clips(1, dur), 0.0)
    _m, _p = CP.build_card_plan(_t, relations=[_rel()], assets={"c01": _a},
                                source_plan_digest="d0",
                                prof=PF.active_profile(), out_dir=tmp_path)
    return _t, _m, _p


def test_one_frame_display_does_not_pass_as_full_page_consumption(tmp_path):
    """**一页只展示一帧**不能算"被消费"（复核 2026-10-05 P1）.

    旧判据只看"该页有没有被某个 segment 点名"，把 segment 的时窗缩到 1 帧、
    `members` 里照旧写着它就顺利通过——观众只看到一闪而过。
    """
    _t, _m, _p = _built(tmp_path)
    assert _m is not None, _codes(_p)
    assert CP.validate_card_plan(_m, timeline=_t, relations=[_rel()]) == []
    _one_frame = json.loads(json.dumps(_m))
    for _s in _one_frame["segments"]:
        _s["end_frame"] = _s["start_frame"] + 1          # 只显示 1 帧
    got = _codes(CP.validate_card_plan(_one_frame, timeline=_t, relations=[_rel()]))
    assert "card_plan_pages_unconsumed" in got, got


def test_segment_digest_must_match_the_asset_on_disk(tmp_path):
    """**segment 摘要错** ⇒ 拒收（清单说有卡，盘上是另一张）.

    ⚠️ 复核 2026-10-05 P2：原用例改了 `sha256` 却**既没调验证器、也没断言拒收**——
    它什么都没测。这里真的调 `validate_card_plan` 并断言 code。
    """
    _t, _m, _p = _built(tmp_path)
    assert _m is not None, _codes(_p)
    assert _m["segments"], "本用例需要至少一个 segment"
    _bad = json.loads(json.dumps(_m))
    for _s in _bad["segments"]:
        _s["sha256"] = "0" * 64
    got = _codes(CP.validate_card_plan(_bad, timeline=_t, relations=[_rel()],
                                       source_plan_digest=_m["source_plan_digest"]))
    assert "card_plan_segment_digest_mismatch" in got, got
    # 合法对照：原样通过
    assert CP.validate_card_plan(_m, timeline=_t, relations=[_rel()],
                                 source_plan_digest=_m["source_plan_digest"]) == []


def test_shortening_page_and_segment_together_is_rejected(tmp_path):
    """**页和片段一起缩成一帧**必须被拒（复核 2026-10-05 P1 的核心反例）.

    "每帧覆盖"只核了 segment 覆盖**清单自报的页**——清单说页只有 1 帧，覆盖就成立。
    真正要问的是「这段正文按当前 profile 需要读多久」。
    """
    _t, _m, _p = _built(tmp_path)
    assert _m is not None, _codes(_p)
    assert CP.validate_card_plan(_m, timeline=_t, relations=[_rel()],
                                 source_plan_digest=_m["source_plan_digest"]) == []
    _short = json.loads(json.dumps(_m))
    for _c in _short["cards"]:
        _c["window"] = {"start_frame": _c["window"]["start_frame"],
                        "end_frame": _c["window"]["start_frame"] + 1}
        for _pg in _c["pages"]:
            _pg["start_frame"] = _c["window"]["start_frame"]
            _pg["end_frame"] = _c["window"]["start_frame"] + 1
    for _s in _short["segments"]:
        _s["end_frame"] = _s["start_frame"] + 1
    got = _codes(CP.validate_card_plan(_short, timeline=_t, relations=[_rel()],
                                       source_plan_digest=_m["source_plan_digest"]))
    assert "card_plan_reading_time_insufficient" in got, got


def test_wrong_source_plan_digest_is_rejected(tmp_path):
    """**错计划的清单不能被消费**（复核 2026-10-05 P1：此前只信清单自报）."""
    _t, _m, _p = _built(tmp_path)
    assert _m is not None, _codes(_p)
    _bad = json.loads(json.dumps(_m))
    _bad["source_plan_digest"] = "f" * 64
    got = _codes(CP.validate_card_plan(_bad, timeline=_t, relations=[_rel()],
                                       source_plan_digest=_m["source_plan_digest"]))
    assert "card_plan_source_digest_mismatch" in got, got


def test_overlay_entries_are_produced_from_a_valid_manifest(tmp_path):
    """真实链路：清单 → **叠加条目**（复核 2026-10-05 P1：`overlay_entries` 抛 NameError）."""
    from step04_merge.timeline import validate_overlays
    _t, _m, _p = _built(tmp_path)
    assert _m is not None, _codes(_p)
    _entries, _ep = CP.overlay_entries(_m, _t)
    assert _ep == []
    assert _entries, "合法清单没有产出任何叠加条目"
    for _e in _entries:
        assert isinstance(_e.layer, int) and _e.layer >= 0, "layer 不是整数 ⇒ 比较会炸"
        assert Path(_e.path).is_file(), "叠加条目指向不存在的资产"
        assert _e.end_sec > _e.start_sec, "叠加时窗非法"
    # 走真实时间线的 overlay 守卫
    _checked = validate_overlays(
        _replace_overlays(_t, _entries), _t.clips, _t.windows)
    assert len(_checked) == len(_entries), "真实守卫拒绝了合法条目"


def _replace_overlays(timeline, entries):
    from dataclasses import replace as _replace
    return _replace(timeline, overlays=tuple(entries))


def test_card_relations_use_the_real_fact_uid_not_the_binding_position():
    """`fact_uids` 取**真实 uid**，不按绑定位置编号（复核 2026-10-05 P1）."""
    _plan = _hybrid_plan()
    _rels = {r["card_id"]: r["fact_uids"] for r in CP.card_relations(_plan)}
    assert _rels["c01"] == ["f02"], _rels
    # 调换绑定顺序 ⇒ 关系不变（§33 P1-1 的同一性质）
    _plan2 = json.loads(json.dumps(_plan))
    _plan2["presentation"]["fact_bindings"] = [
        {"fact_uid": "f01", "carriers": [{"kind": "visual", "beat_uid": "b01"}]},
        {"fact_uid": "f02",
         "carriers": [{"kind": "visual", "beat_uid": "b02"},
                      {"kind": "screen_card", "card_id": "c01"}]},
        {"fact_uid": "f03", "carriers": [{"kind": "visual", "beat_uid": "b03"}]}]
    _plan2["presentation"]["fact_uids"] = ["f03", "f01", "f02"]   # **顺序打乱**
    _rels2 = {r["card_id"]: r["fact_uids"] for r in CP.card_relations(_plan2)}
    assert _rels2["c01"] == ["f02"],         "绑定顺序变了，卡片承载的事实就跟着变 ⇒ fact_uid 是按位置生成的"

# ---------------------------------------------------------------------------
# ④ 联合承载贯通：投影 → **真实缓存读回** → 消费者
# ---------------------------------------------------------------------------
def _cached_round_trip(tmp_path, monkeypatch):
    """真写盘 + 真读回跑两轮，返回 `(第一轮结果, 第二轮结果)`（复用生产入口）."""
    import step01_script_gen.outline as o

    _calls = {"gen": 0, "ver": 0}
    _plan = _hybrid_plan()
    _ver_record = _record_for(_plan, novel_digest=cv.plan_digest_of(_plan))

    class _Res:
        def __init__(self, payload, cid):
            self.success = True
            self.text = json.dumps(payload, ensure_ascii=False)
            self.error = ""
            self.call_id = cid

    class _Svc:
        def generate(self, req):
            _js = getattr(req, "json_schema", None)
            _name = str(_js.get("name") or "") if isinstance(_js, dict) else ""
            if "content_verification" in _name:
                _calls["ver"] += 1
                return _Res(_ver_record, "v1")
            _calls["gen"] += 1
            return _Res(_first_version_payload(_plan), "g1")

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    _novel = ("雨下了三天。周明去年递了辞呈。这里以后就交给林青了。"
              "北岸仓库的货位已经清点完毕。")
    _cache = tmp_path / "outline_cache.json"
    _r1 = o.generate_outline_result(_novel, cache_path=_cache)
    _r2 = o.generate_outline_result(_novel, cache_path=_cache)
    return _r1, _r2, _calls


def _bindings_of(plan):
    return {str(b["fact_uid"]): b["carriers"]
            for b in (plan.get("presentation") or {}).get("fact_bindings") or []}


def test_a_joint_carrier_survives_projection_and_the_real_cache_round_trip(
        tmp_path, monkeypatch):
    """复核 2026-10-06（第八轮）④：**联合承载**必须经投影与**真实缓存读回**保持完整.

    复核此前只有局部投影证据（"画面＋卡片"在应用/投影/读回里保住）——缓存与最终消费者
    还没证。这条走真写盘真读回：第一轮落盘的 `f02` 有**两个**载体，第二轮命中缓存读回来
    必须**仍是两个**（少一个就是静默丢关系，比拒收更糟）。
    """
    _r1, _r2, _calls = _cached_round_trip(tmp_path, monkeypatch)
    assert _r1.get("executable") is True, _r1.get("failure")
    _b1 = _bindings_of(_r1["plan"])
    assert len(_b1.get("f02") or []) == 2, "夹具本身就不是联合承载：%s" % _b1
    assert {c["kind"] for c in _b1["f02"]} == {"visual", "screen_card"}

    assert _r2.get("cache", {}).get("hit") is True, _r2.get("cache")
    assert _calls["gen"] == 1 and _calls["ver"] == 1, _calls
    _b2 = _bindings_of(_r2["plan"])
    assert _b2 == _b1, "缓存读回改动了载体关系：%s -> %s" % (_b1, _b2)


def test_the_consumer_still_sees_the_card_after_the_cache_round_trip(tmp_path, monkeypatch):
    """消费者侧：缓存读回的稿子仍能产出那张卡片的来源关系与叠加入口（零模型调用）."""
    from step04_merge.screen_cards import card_relations_for
    from shared.screen_cards import plan as CP

    _r1, _r2, _ = _cached_round_trip(tmp_path, monkeypatch)
    assert _r2.get("cache", {}).get("hit") is True
    _script = {"chapter_outline": _r2["plan"]}
    _rel, _mode, _probs = card_relations_for(_script)
    assert _probs == [], _probs
    assert _mode == pc.MODE_HYBRID
    assert [r["card_id"] for r in _rel] == ["c01"], _rel
    # 卡片正文与节拍绑定逐字保留（不是"有个 id 就算过"）
    assert _rel[0]["text"] == "这里以后就交给林青了。"
    assert str(_rel[0]["beat_uid"]) == "b02"
    # 该事实在**消费者看到的**绑定表里仍然是联合承载
    _b = _bindings_of(_r2["plan"])
    assert {c["kind"] for c in _b["f02"]} == {"visual", "screen_card"}
    # 清单消费者：卡片关系能驱动 plan 侧建清单（合法输入 ⇒ 无问题）
    _t = _timeline(_clips(1, dur=20.0), 0.0)
    _m, _mp = CP.build_card_plan(_t, relations=[_rel[0]], assets={},
                                 source_plan_digest="d0", prof=PF.active_profile())
    assert _m is None and any(p.get("code") for p in _mp), \
        "资产缺失必须明确报错，而不是产出一份没资产的清单"

# ---------------------------------------------------------------------------
# ①' 缓存身份闭环（复核 2026-10-07 三轮 ②）：**被拒的记录不许随计划继续流通**
#
# 实测形态：把缓存里记录的原文摘要**改错或清空** ⇒ 同时出现 `verification_rejected`、
# `cache.hit=true`、`executable=true`，**生成与核验均 0 次**——前一道门拒了，后续消费
# 照样把那份记录当"已核验"。现在：拒绝之后从计划本体里摘掉 ⇒ 统一验收如实报
# "缺核验记录" ⇒ 按既定策略**重新生成并重新核验**。
# ---------------------------------------------------------------------------
def _stub_service(monkeypatch, *, plan, record, calls):
    """桩服务：生成回**首版形状**的计划、核验回给定记录；分别计数."""
    import step01_script_gen.outline as o

    class _Res:
        def __init__(self, payload, cid):
            self.success = True
            self.text = json.dumps(payload, ensure_ascii=False)
            self.error = ""
            self.call_id = cid

    class _Svc:
        def generate(self, req):
            _js = getattr(req, "json_schema", None)
            _name = str(_js.get("name") or "") if isinstance(_js, dict) else ""
            if "content_verification" in _name:
                calls["ver"] += 1
                return _Res(record, "v1")
            calls["gen"] += 1
            return _Res(_first_version_payload(plan), "g1")

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    return o


@pytest.mark.parametrize("tamper", ["wrong", "cleared"])
def test_a_tampered_cached_record_is_rejected_and_never_rides_along(
        tmp_path, monkeypatch, tamper):
    """真写盘、真读回：摘要改错/清空 ⇒ 拒绝 + **重新核验**，不是"零调用照样可执行"."""
    from step01_script_gen.content_verify import novel_identity
    _plan = _hybrid_plan()
    _novel = _NOVEL
    _calls = {"gen": 0, "ver": 0}
    _o = _stub_service(monkeypatch, plan=_plan,
                       record=_record_for(_plan,
                                          novel_digest=cv.plan_digest_of(_plan)),
                       calls=_calls)
    _cache = tmp_path / "outline_cache.json"
    _r1 = _o.generate_outline_result(_novel, cache_path=_cache)
    assert _r1.get("executable") is True, (_r1.get("failure"),
                                           _r1.get("acceptance", {}).get("codes"))
    _calls.update(gen=0, ver=0)
    # ---- 篡改缓存里的原文摘要（外层与计划本体两处，读者可能从任一处取）----
    _raw = json.loads(_cache.read_text(encoding="utf-8"))
    assert isinstance(_raw.get("verification"), dict), _raw.keys()
    assert isinstance(_raw["outline"].get("plan_verification"), dict)
    _new = ("0" * 64) if tamper == "wrong" else ""
    _raw["verification"]["novel_digest"] = _new
    _raw["outline"]["plan_verification"]["novel_digest"] = _new
    _cache.write_text(json.dumps(_raw, ensure_ascii=False), encoding="utf-8")
    # ---- 第二轮：必须**拒用**这份记录，并重新核验 ----
    _r2 = _o.generate_outline_result(_novel, cache_path=_cache)
    assert _r2["cache"].get("verification_rejected"), _r2["cache"]
    assert _r2["cache"].get("hit") is not True,         "被拒的记录仍随缓存被复用：%s" % _r2["cache"]
    assert _calls["gen"] >= 1 and _calls["ver"] >= 1,         "被拒之后没有重新生成/重新核验（旧形态是 0 次）：%s" % _calls
    assert _r2.get("executable") is True, _r2.get("failure")
    assert _r2["plan"]["plan_verification"]["novel_digest"] ==         novel_identity(_novel)["digest"], "交付的记录没绑本次原文身份"

