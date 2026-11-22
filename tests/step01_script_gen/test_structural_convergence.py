# -*- coding: utf-8 -*-
"""结构收敛路径（二次审核发现 1，P0）.

实测：镜头 10/11/13 的台词最低需求 13.7 / 14.4 / 20.0s 都超过单镜 8s 上限，镜头 5
只有 3.0s。代码行为是「一次生成完整镜头对象 → 发现结构不合法 → **只报告**」：
`timecode` 只打 `[SPLIT-BLOCKED]`，`_regen_over_limit` 只管镜头总数与全片总时长，
字段级修复轮修不了切分问题。于是必然带着 BLOCK 落盘。

本文件锁住新补的两条路径：
  1. 候选选择把「结构不合法」当**拒收条件**（有预算就重取，没预算明确 BLOCK）；
  2. `_regen_structural_over_limit` 把结构问题点名反馈给 LLM **重做 shot-plan**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# v6.131（2026-09-25 收口）：生成前计划门挂在**候选扇出前**（`_generate_script_candidates`
# 入口），缺计划/计划不可行即停。因此凡是要驱动候选扇出机制的用例，都必须给它一份
# **真实可验算**的计划（节拍 3 个/下限 9s、对白 1 句逐字来自下面的原文 → 可行）。
_SOURCE = "老陈说：这船今日不出港。舱里还有两箱货，天黑前必须走。"
from step01_script_gen import plan_feasibility as pf

_PLAN = {"main_events": ["老陈说船不出港", "舱里还有两箱货", "天黑前必须走"],
         "climax": "老陈说船不出港",
         "causal_chain": ["老陈说船不出港→舱里还有两箱货→天黑前必须走"],
         "required_facts": ["老陈说船今日不出港｜原文证据：“老陈说：这船今日不出港。”"
                            "｜承载：B1/台词1"],
         "shot_beats": ["节拍：老陈说船今日不出港｜本镜信息目的：交代｜原文依据：“老陈说："
                        "这船今日不出港。”｜载体：台词1",
                        "节拍：舱里还有两箱货｜本镜信息目的：交代｜原文依据：“舱里还有两箱货”"
                        "｜载体：画面",
                        "节拍：天黑前必须走｜本镜信息目的：推进｜原文依据：“天黑前必须走”"
                        "｜载体：画面"],
         "dialogue_lines": ["这船今日不出港"],
         "narration_lines": [], "hook_spoken": [], "hook_carrier": ""}

#: 计划身份（`plan_id`）与逐镜 `plan_refs`：**承载契约下的新生成稿必须声明**
#: （v2 起：带「载体：/承载：」的计划要求逐镜履约声明，否则候选层事件契约整稿拒收）。
#: 夹具因此要给 mock 稿补上——与本文件既有先例（夹具带真实计划、mock 稿带计划台词）同源。
_PLAN_ID = pf.plan_identity(_PLAN)


def _refs(fn):
    """把 mock 的解析结果补成**契约完整**的新稿（`plan_id` + 逐镜 `plan_refs`）.

    节拍声明按镜头序**非降**分配：第 k 镜声明第 k 个节拍，之后的镜头都声明最后一条
    （覆盖每个节拍、又不制造顺序回退）；台词只在首镜声明一次。
    """
    _seq = list(range(1, len(_PLAN["shot_beats"]) + 1)) or [1]

    def _w(raw, *a, **k):
        k.pop("plan", None)
        _s = fn(raw, *a, **k)
        if not isinstance(_s, dict):
            return _s
        _s.setdefault("plan_id", _PLAN_ID)
        _shots = [x for x in (_s.get("shots") or []) if isinstance(x, dict)]
        for _i, _sh in enumerate(_shots):
            if len(_shots) == 1:
                _sh.setdefault("plan_refs", {"beats": list(_seq), "lines": [1]})
            else:
                _b = _seq[min(_i, len(_seq) - 1)]
                _sh.setdefault("plan_refs", {"beats": [_b], "lines": [1] if _i == 0 else []})
        return _s
    return _w


#: 计划门会**收窄** `dialogue_lines`（核验通过的那一组），上面那句在原文里逐字存在


def _shot(index=1, narr_len=0, dur=4.0):
    narr = ("张十三道：" + "你最好想清楚再开口。" * narr_len) if narr_len else ""
    return {"index": index, "duration_sec": dur, "duration": dur,
            "narration_segment": narr, "subtitle": narr,
            "motion_desc": "0-2秒：开口；2-4秒：收势",
            "prompt": "中景", "ff_desc": "f", "lf_desc": "l",
            # v6.99：fixture 必须自带与 motion 段数一致的 KF——否则新稿会因为
            # 「0 个 kf / 2 段」引入新的 blocking 身份，被 `_blocking_not_degraded`
            # 正确拒绝，用例就测不到「采纳改善稿」这条路径了。
            "keyframes": [{"desc": "开口", "t_sec": 1.0, "stage": "approach"},
                          {"desc": "收势", "t_sec": 3.0, "stage": "after"}],
            # 与 KF 数一致（否则 `kf_layout_count_mismatch` 会引入新 blocking 身份）
            "frame_layouts": {"ff": [], "lf": [], "kfs": [[], []]}}


def _split_narration(narr, parts):
    """把台词按原文**连续切成 `parts` 段**——真实「重做 shot-plan」的字面量约束.

    v6.98：`_content_degradation` 现在会核对**台词清单**（旧稿说过的每一句必须仍在
    候选里）。合成 fixture 若拿「重复句 ×N」当台词、又让新稿每镜只留 1 份，
    新稿就把旧稿的台词**整句丢了**——那是 fixture 不真实，不是判据过严：
    真实路径的 prompt 明确要求「台词按原文**连续切分**，禁止改写、禁止增删字」。
    """
    n = len(narr) // parts
    out = []
    for i in range(parts):
        out.append(narr[i * n: (i + 1) * n] if i < parts - 1 else narr[i * n:])
    return out


def _script(shots):
    return {"shots": shots, "hook": None, "fps": 24, "characters": [],
            "total_duration": sum(s["duration_sec"] for s in shots)}


# ------------------------------------------------------------ 结构问题识别

def test_structural_issues_only_include_the_two_structural_codes():
    from step01_script_gen.main import _structural_shot_issues

    over = _script([_shot(1, narr_len=10, dur=8.0)])       # 台词念不完
    assert [i.code for i in _structural_shot_issues(over)] == ["shot_content_over_cap"]

    short = _script([_shot(1, dur=3.0), _shot(2, dur=6.0)])
    assert [i.code for i in _structural_shot_issues(short)] == ["shot_duration_below_min"]

    clean = _script([_shot(1, narr_len=1, dur=5.0), _shot(2, narr_len=1, dur=5.0)])
    assert _structural_shot_issues(clean) == []


# ------------------------------------------------------- 结构收敛轮

def _patch_regen(monkeypatch, new_script, *, fail=False):
    """mock 掉模型调用。

    v6.89 起 `_regen_structural_over_limit` **先**发一次「单镜定向拆分」请求
    （`SPLIT_SHOT_SCHEMA`），再走整脚本 regen。本文件测的是**整脚本那条路**，
    所以按 schema 分派：拆分请求一律返回空 `sub_shots`（=被拒，不影响后续），
    `calls["n"]` 只统计整脚本 regen 的次数（既有断言因此保持不变）。
    """
    import step01_script_gen.main as m

    calls = {"n": 0, "prompt": "", "split_n": 0}

    class _R:
        success = not fail
        error = "" if not fail else "boom"
        text = json.dumps(new_script, ensure_ascii=False)

    class _R_split:
        success = True
        error = ""
        text = json.dumps({"sub_shots": []}, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            if (req.json_schema or {}).get("name") == "split_over_cap_shot":
                calls["split_n"] += 1
                return _R_split()
            calls["n"] += 1
            calls["prompt"] = req.user_prompt
            calls["req"] = req          # v6.101：超时等请求级参数也要能断言
            return _R()

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "build_regen_feedback", lambda *a, **k: "")
    monkeypatch.setattr(m, "_compute_timecodes", lambda s: None)
    monkeypatch.setattr(m, "parse_response", _refs(lambda raw, novel_text="": json.loads(raw)))
    return calls


def test_clean_script_never_calls_the_llm(monkeypatch):
    import step01_script_gen.main as m

    calls = _patch_regen(monkeypatch, _script([_shot(1)]))
    script = _script([_shot(1, narr_len=1, dur=5.0), _shot(2, narr_len=1, dur=5.0)])
    assert m._regen_structural_over_limit(script, "novel") is script
    assert calls["n"] == 0, "结构合法时不得调模型"


def test_structural_regen_adopts_clean_new_plan(monkeypatch):
    import step01_script_gen.main as m

    # 旧稿：1 镜、台词念不完（115 字 ≈ 28.8s > 12s 上限）
    src = _script([_shot(1, narr_len=10, dur=8.0)])
    _narr = src["shots"][0]["narration_segment"]
    # 新稿：按原文**连续切分**成 4 镜，每镜 10s（内容需求 ≈7.2s ≤ 10.0，结构合法）
    fixed = _script([])
    fixed["shots"] = []
    for i, part in enumerate(_split_narration(_narr, 4), 1):
        sh = _shot(i, dur=10.0)
        sh["narration_segment"] = part
        sh["subtitle"] = part
        fixed["shots"].append(sh)
    calls = _patch_regen(monkeypatch, fixed)
    out = m._regen_structural_over_limit(src, "novel")
    assert calls["n"] == 1
    assert len(out["shots"]) == 4, "应采纳拆分后的新稿"
    # 反馈必须点名镜头并明确要求「重做 shot-plan」
    assert "重做 shot-plan" in calls["prompt"]
    assert "镜头1" in calls["prompt"]


def test_structural_regen_rejects_clean_plan_that_drops_content(monkeypatch):
    """**结构问题 1 → 0，但候选丢了台词 → 必须拒绝**（审核 P0-1 的对抗反例）.

    修法前的漏洞：`if not new_issues: return new_script` 走**快速返回**，完整性/内容
    门只在 `0 < M < N` 时才跑——于是「修得最干净的那次重生」恰好拥有最弱的验收，
    删掉剧情只要「结构干净」就被采纳。本用例锁住「目标指标 = 0 也必须过统一验收」。
    """
    import step01_script_gen.main as m

    clean_but_empty = _script([_shot(1, narr_len=0, dur=5.0),
                               _shot(2, narr_len=0, dur=5.0)])
    calls = _patch_regen(monkeypatch, clean_but_empty)
    src = _script([_shot(1, narr_len=10, dur=8.0)])
    out = m._regen_structural_over_limit(src, "novel")

    assert calls["n"] == 1
    assert out is src, "结构归零但内容退化（台词全丢）不得采纳"
    assert len(out["shots"]) == 1


def test_structural_regen_keeps_original_when_new_plan_not_better(monkeypatch):
    import step01_script_gen.main as m

    calls = _patch_regen(monkeypatch, _script([_shot(1, narr_len=10, dur=8.0)]))
    script = _script([_shot(1, narr_len=10, dur=8.0)])
    out = m._regen_structural_over_limit(script, "novel")
    assert out is script, "新稿同样非法 → 保留原稿"
    assert calls["n"] == 1


def test_structural_regen_failure_keeps_original(monkeypatch):
    import step01_script_gen.main as m

    calls = _patch_regen(monkeypatch, {}, fail=True)
    script = _script([_shot(1, narr_len=10, dur=8.0)])
    assert m._regen_structural_over_limit(script, "novel") is script
    assert calls["n"] == 1


def test_structural_regen_bad_json_keeps_original(monkeypatch):
    import step01_script_gen.main as m

    def _boom(raw, novel_text=""):
        raise ValueError("bad json")

    calls = _patch_regen(monkeypatch, {})
    monkeypatch.setattr(m, "parse_response", _refs(_boom))
    script = _script([_shot(1, narr_len=10, dur=8.0)])
    assert m._regen_structural_over_limit(script, "novel") is script
    assert calls["n"] == 1


# ------------------------------------------------- 候选择收

def test_structurally_invalid_candidate_is_refetched(monkeypatch):
    """结构不合法 + 还有预算 → **拒收并重取**（不是先收下再在末尾抱怨）."""
    import step01_script_gen.main as m
    import shared.config as cfg

    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRIES", 3)
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRY_SLEEP_SEC", 0)

    seq = [False, True, True]      # 第 1 个候选结构非法，第 2 个合法

    def _gen(novel, *a, **kw):
        return "raw"

    monkeypatch.setattr(m, "gen_spy_script", _gen)
    monkeypatch.setattr(m, "parse_response",
                        # v6.135：mock 稿带计划台词（台词完整性硬门），空稿会被判破损
                        _refs(lambda raw, novel_text="": {"shots": [{"index": 1, "n": 1,
                            "narration_segment": "老陈说：这船今日不出港",
                            "ff_desc": "f", "lf_desc": "l", "prompt": "p",
                            "duration_sec": 3.0}]}))
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 50, "detail": "m"})
    monkeypatch.setattr(m, "_compute_timecodes", lambda s: None)
    state = {"i": 0}

    def _summary(script, novel_text=""):
        ok = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return {"quality_checked": True, "blocking_risk_count": 0 if ok else 1,
                "integrity_ok": True, "integrity_issues": [],
                "structural_ok": ok, "structural_issues": [] if ok else ["超载"]}

    monkeypatch.setattr(m, "_candidate_quality_summary", _summary)

    cands = m._generate_script_candidates(_SOURCE, None, None, _PLAN)
    assert state["i"] >= 2, "结构非法的候选必须触发重取"
    assert cands and cands[0][0].get("structural_ok") is True


def test_structural_regen_adopts_fewer_violations_even_when_content_rank_is_worse(monkeypatch):
    """结构非法数**严格变少**必须采纳，即使整体质量秩更差.

    2026-09-21 22:20:04 实测回归：原判据是
    `len(new) < len(old) and new_rank <= old_rank`，而 `_quality_rank` 把内容分与
    **全部** blocking 计数混进同一个元组。一份把非法镜头 **7 → 5** 的新稿因为内容项
    略多被判成「未改善」丢弃，本轮真实运行因此带着 5 个 `shot_content_over_cap`
    直接进 hard。结构轮存在的唯一目的就是收敛结构，判据却不由结构主导。
    """
    import step01_script_gen.main as m

    calls = _patch_regen(monkeypatch, _script([_shot(1, narr_len=10, dur=8.0)]))
    monkeypatch.setattr(m, "_score_script", lambda s, t="": {"total": 50.0})
    # 新稿（1 镜）在质量秩上「更差」——blocking 计数远高于原稿（2 镜）
    monkeypatch.setattr(
        m, "_candidate_quality_summary",
        lambda s, t="": {"integrity_ok": True, "integrity_count": 0,
                        "blocking_risk_count": 99 if len(s["shots"]) == 1 else 0})
    # 原稿 2 个结构问题：镜头 1 超 cap + 镜头 2（非末镜）低于下限
    script = _script([_shot(1, narr_len=10, dur=8.0), _shot(2, dur=3.0),
                      _shot(3, narr_len=1, dur=5.0)])
    out = m._regen_structural_over_limit(script, "novel", max_attempts=1)
    assert calls["n"] == 1
    assert out is not script, "结构非法数 2 → 1 必须采纳，不得因内容秩更差而丢弃"
    assert [s["index"] for s in out["shots"]] == [1]


def test_structural_regen_refuses_draft_that_introduces_integrity_anomaly(monkeypatch):
    """唯一附加守卫：结构改善但**引入完整性异常**（硬正确性问题）→ 保留原版.

    「内容分低一点」与「内容崩坏」不是一回事：前者交随后的定向修复轮，
    后者（状态回退/姿态互斥/时长预算等）必须当场拒绝。
    """
    import step01_script_gen.main as m

    _patch_regen(monkeypatch, _script([_shot(1, narr_len=10, dur=8.0)]))
    monkeypatch.setattr(
        m, "_candidate_quality_summary",
        lambda s, t="": ({"integrity_ok": False, "integrity_count": 2,
                          "integrity_issues": ["状态回退"]}
                        if len(s["shots"]) == 1
                        else {"integrity_ok": True, "integrity_count": 0}))
    script = _script([_shot(1, narr_len=10, dur=8.0), _shot(2, dur=3.0),
                      _shot(3, narr_len=1, dur=5.0)])
    out = m._regen_structural_over_limit(script, "novel", max_attempts=1)
    assert out is script, "结构改善但引入完整性异常 → 保留原版"


def test_kf_event_window_mismatch_is_repair_eligible():
    """`kf_event_window_mismatch` 必须进定向修复轮，且字段授权用 **canonical** 形态.

    2026-09-21 22:22 实测：该 code 是最大阻塞桶（18 → 9 条），却既不在
    `_REPAIR_PRIORITY_CATEGORIES`、也不是 `STRUCTURAL_SHOT_CODES` → 没有任何收敛
    路径，只能一路报告到 BLOCK。它构造的 issue 本来就有 `shot_id` /
    `field_path="keyframes.desc"` / `repairable=True`，与同族的
    `kf_time_window_alignment` 是同一字段、同一类缺陷、同一套修法。

    另外它的 `field_path` 写作 `keyframes.desc`（无下标），而 canonical diff 出来的是
    `keyframes.*.desc`——`path_allowed` 不支持「父字段反向包含子路径」，所以单靠
    `field_path` 授权必然被拒，必须在 code 表里显式登记规范形态。
    """
    from step01_script_gen.main import _REPAIR_PRIORITY_CATEGORIES
    from step01_script_gen.repair_fields import allowed_repair_fields, check_replacement_authorized
    from step01_script_gen.quality_issues import Issue

    assert "kf_event_window_mismatch" in _REPAIR_PRIORITY_CATEGORIES

    issue = Issue(code="kf_event_window_mismatch", severity="visual_risk",
                  message="kf1 位于 0-2s，但内容更匹配 2-4s 的事件",
                  shot_id=1, frame_id="kf1", field_path="keyframes.desc",
                  repairable=True)
    allowed = allowed_repair_fields([], [issue])
    assert "keyframes.*.desc" in allowed

    before = {"keyframes": [{"t_sec": 1.0, "stage": "pre", "desc": "旧描述"}]}
    after = {"keyframes": [{"t_sec": 1.0, "stage": "pre", "desc": "新描述"}]}
    assert check_replacement_authorized(before, after, allowed) == ""
    # 未授权路径仍被拒
    after2 = dict(after, motion_desc="顺手改了动作")
    assert check_replacement_authorized(before, after2, allowed) != ""


def test_structural_regen_adopts_when_integrity_is_not_degraded_but_dirty(monkeypatch):
    """原稿带完整性异常时，结构改善的稿子不得因「不干净」被拒（v6.87）.

    2026-09-21 23:17 实测回归：
    ```
    [STRUCT] 新稿结构改善（5 → 4）但引入完整性异常，保留原版：['正片总时长 92.2s > 90s…',
                                                                '同帧出现刀尖但没有明确持刀者']
    ```
    两个候选摘要全标「完整性异常」——**原稿本身就不干净**，所以布尔判据
    `integrity_ok is False` 恒为真，结构轮永远无法采纳任何稿子。
    用户文档里 B2 的用词本来就是「完整性**不退化**」。
    """
    import step01_script_gen.main as m

    _patch_regen(monkeypatch, _script([_shot(1, narr_len=10, dur=8.0)]))
    monkeypatch.setattr(
        m, "_candidate_quality_summary",
        lambda s, t="": {"integrity_ok": False, "integrity_count": 2,
                        "integrity_issues": ["正片总时长 92.2s > 90s"]})
    script = _script([_shot(1, narr_len=10, dur=8.0), _shot(2, dur=3.0),
                      _shot(3, narr_len=1, dur=5.0)])
    out = m._regen_structural_over_limit(script, "novel", max_attempts=1)
    assert out is not script, "结构 2 → 1 且完整性未退化（2 → 2）→ 必须采纳"


def test_structural_regen_refuses_when_integrity_actually_degrades(monkeypatch):
    """完整性异常条数真的增加 → 拒绝（判据不是「无条件放行」）."""
    import step01_script_gen.main as m

    _patch_regen(monkeypatch, _script([_shot(1, narr_len=10, dur=8.0)]))
    monkeypatch.setattr(
        m, "_candidate_quality_summary",
        lambda s, t="": ({"integrity_ok": False, "integrity_count": 4,
                          "integrity_issues": ["a", "b", "c", "d"]}
                        if len(s["shots"]) == 1
                        else {"integrity_ok": False, "integrity_count": 1,
                              "integrity_issues": ["x"]}))
    script = _script([_shot(1, narr_len=10, dur=8.0), _shot(2, dur=3.0),
                      _shot(3, narr_len=1, dur=5.0)])
    out = m._regen_structural_over_limit(script, "novel", max_attempts=1)
    assert out is script, "完整性异常 1 → 4 条（退化）→ 保留原版"


# ===========================================================================
# v6.101（2026-09-23 第十四次重跑实测后的三项修复）
# ===========================================================================

def test_structural_regen_uses_full_script_timeout(monkeypatch):
    """结构收敛轮是**全脚本级**请求 → 必须用 `_full_script_timeout_sec()`（300s）.

    第十四次实测：它一直用**修复轮**的 180s → `Agent Plan 客户端超时 (180s)`，
    整轮作废、连候选都没拿到（`_accept_regen_candidate` 因此从未被走到）。
    """
    import step01_script_gen.main as m

    calls = _patch_regen(monkeypatch, _script([_shot(1, narr_len=1, dur=10.0)]))
    script = _script([_shot(1, narr_len=10, dur=8.0)])
    m._regen_structural_over_limit(script, "novel")

    assert calls["n"] >= 1, "本轮必须发出整脚本重生请求"
    assert calls["req"].timeout_sec == m._full_script_timeout_sec(), \
        "结构收敛轮的超时必须与 GAP 重生同档（全脚本级），不能用修复轮的 180s"


def test_opening_empty_overload_is_a_structural_target(monkeypatch):
    """开篇空镜过多 → 必须出现在**结构收敛轮的目标集合**里（v6.101）.

    两个空镜约束 v6.99 起 report-only（代码不再删镜头），删除诉求「交结构 regen」
    ——但收敛轮此前只读 `_shot_duration_issues`，看不见空镜问题，这条诉求等于没落地。
    """
    from step01_script_gen.main import _structural_feedback, _structural_shot_issues

    def _empty(i):
        return {"index": i, "duration": 5.0, "duration_sec": 5.0,
                "main_subject": "空镜·河面", "prompt": "环境空镜",
                "ff_desc": "河面开阔", "lf_desc": "水面平静",
                "motion_desc": "0-2秒：水波；2-5秒：芦苇", "narration_segment": "",
                "subtitle": "", "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}

    def _char(i):
        return {"index": i, "duration": 5.0, "duration_sec": 5.0,
                "main_subject": "杨旭", "prompt": "[中景] 杨旭立在船头",
                "ff_desc": "杨旭立在船头", "lf_desc": "杨旭抬手",
                "motion_desc": "0-2秒：抬手；2-5秒：收势", "narration_segment": "",
                "subtitle": "", "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}

    script = _script([_empty(1), _empty(2), _empty(3)] + [_char(i) for i in range(4, 9)])
    issues = _structural_shot_issues(script)
    assert any(i.code == "opening_empty_shot_overload" for i in issues), \
        [i.code for i in issues]
    # 非阻塞：advisory 不进 blocking，门禁不会因此多拦一类
    assert all(i.severity != "hard" for i in issues)
    # 反馈里要给出修法（合并/替换成带主体的建立镜，而不是删镜头）
    fb = _structural_feedback(issues)
    assert "开篇空镜过多" in fb and "不是**让你删镜头" in fb


def test_opening_empty_within_limit_is_not_a_target():
    from step01_script_gen.main import _structural_shot_issues

    def _empty(i):
        return {"index": i, "duration": 5.0, "duration_sec": 5.0, "main_subject": "空镜·河面",
                "prompt": "环境空镜", "ff_desc": "河面开阔", "lf_desc": "水面平静",
                "motion_desc": "0-5秒：水波", "narration_segment": "", "subtitle": "",
                "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}

    def _char(i):
        return {"index": i, "duration": 5.0, "duration_sec": 5.0, "main_subject": "杨旭",
                "prompt": "[中景] 杨旭", "ff_desc": "杨旭", "lf_desc": "杨旭",
                "motion_desc": "0-5秒：抬手", "narration_segment": "", "subtitle": "",
                "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}

    # 前 3 镜只有 1 个空镜（=上限）→ 不该成为目标
    script = _script([_empty(1), _char(2), _char(3)] + [_char(i) for i in range(4, 9)])
    assert not [i for i in _structural_shot_issues(script)
                if i.code == "opening_empty_shot_overload"]
