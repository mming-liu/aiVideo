# -*- coding: utf-8 -*-
"""单镜定向拆分（发现 14 选项 2，v6.89，2026-09-22）.

## 为什么需要它

`shot_content_over_cap` 是四轮重跑里**唯一没被动过**的桶（5 条，台词需求
9.8 / 12.5 / 14.7 / 8.8 / 20.2s）。prompt 侧的显式许可（B2.1 / R1 旁注 / main.md 2c）
**已被证伪**：2026-09-22 07:11 三个候选行为与加规则前逐字一致。

根因是「**单条**原文台词本身超长」——原文里那是**一整句、一整对引号**（89 字 →
20.2s），只能把同一条台词切成两段分给两个镜头，而模型稳定地不切。

## 与当年被禁止的「复制字段拆镜」的区别

那时是**代码复制**父镜字段造子镜 → 产出 ff==lf 的空壳；这里是**模型重新生成**
每个子镜的完整对象，`SPLIT_SHOT_SCHEMA` 复用 `_SHOT`（24 字段全必填），
子镜在 schema 层就不可能是空壳。

## 本文件锁住的不变量

1. 台词**逐字连续**（拼回来与父镜相等）——这是「拆」与「改写」的分界；
2. 子镜**容量合格**由质量门本体裁决（不复制判据）；
3. 只有被点名的那一镜被改，其它镜头逐字节不动。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 父镜台词：6 个分句 ≈ 60 字 → 估时 >8s（超载）；按**句末**切成 3 段各 2 分句 → 各 ≈6s
# v6.136（A+B 统一判据）：合法切点 = 句末边界（。！？…；）——分句必须以句号结尾，
# 逗号结尾的分句之间不可切（句中碎片 = 终稿台词门 cross_shot_split）
_C1 = "你最好想清楚再开口。"
_C2 = "公子爷的事你担待不起。"
_C3 = "免得惹祸上身才是。"
_PARENT_NARR = "张十三道：" + _C1 * 2 + _C2 * 2 + _C3 * 2
# 三段**互不相同**——否则「倒序」与「正序」拼出来一样，顺序判据测不出来
_SUB_NARRS = ["张十三道：" + _C1 * 2, "张十三道：" + _C2 * 2, "张十三道：" + _C3 * 2]
_SUB_NARR = _SUB_NARRS[0]


# 每个子镜配一组**互不相同**的动作/构图——`adjacent_duplicate_visuals` 的判据是
# `jaccard_bigram(motion_desc + prompt + lf_desc) >= 0.85`（同地点族 + 同说话人），
# 所以「子镜互不近似」必须是 fixture 的一部分，不能用同一段文本冒充多个子镜。
_DISTINCT = [
    ("0-2秒：他抬手示意；2-4秒：他垂手", "近景，抬手", "船头，张十三抬手"),
    ("0-2秒：他转身背对；2-4秒：他迈步", "中景，转身", "船头，张十三转身"),
    ("0-2秒：他低头沉思；2-4秒：他抬眼", "远景，低头", "船头，张十三低头"),
    ("0-2秒：他按住船舷；2-4秒：他松开", "特写，按舷", "船头，张十三按舷"),
]


def _frames_for(motion, dur):
    """按时段中点给每段一帧——夹具**帧合规**（v6.143 P3 起结构轮把缺帧算结构问题）."""
    from step01_script_gen.kf_coverage import segments as _segments

    return [{"t_sec": round((a + b) / 2, 3), "stage": "approach",
             "desc": str(body or "定格")}
            for a, b, body in _segments(motion, dur)]


def _shot(index, narration="", dur=5.0, *, motion=None, prompt=None, lf=None):
    _motion = motion or "0-2秒：开口；2-4秒：收势"
    _kfs = _frames_for(_motion, dur)
    return {"index": index, "duration": dur, "duration_sec": dur,
            "narration_segment": narration, "subtitle": narration,
            "scene": "照月湾", "location": "照月湾船头",
            "motion_desc": _motion,
            "prompt": prompt or "中景",
            "ff_desc": "船头，张十三直立", "lf_desc": lf or "船头，张十三垂手",
            "core_keywords": ["船头"], "keyframes": _kfs,
            "frame_layouts": {"ff": [], "lf": [], "kfs": [[] for _ in _kfs]}}


def _compliant_subs(shot, cap=None):
    """按**切分器本身**产出的分段构造合规子镜（模拟一个守规矩的模型）.

    v6.93：段数随生效 cap 变化（cap 8 → 12 后 15.3s 的台词从 3 段变 2 段），
    所以子镜数**不能写死**——直接调用 `_deterministic_narration_cuts` 得到分段，
    再为每段配一组互不相同的动作/构图。这同时让 mock 更诚实：
    真实模型被告知「必须恰好用这些分段」，这里模拟它照做。
    """
    segs = _segments_for(shot, cap)
    return [dict(_shot(i, t, dur=5.0, motion=m, prompt=p, lf=l))
            for i, (t, (m, p, l)) in enumerate(zip(segs, _DISTINCT), 1)]


def _segments_for(shot, cap=None):
    """被点名镜头在**生效 cap** 下的确定性分段（段数随 cap 变，不能写死）."""
    from step01_script_gen.main import _deterministic_narration_cuts, _shot_cap

    segs = _deterministic_narration_cuts(shot["narration_segment"], cap or _shot_cap())
    assert segs, "fixture 台词必须能被确定性切分"
    return segs


def _dup_subs(shot, cap=None):
    """分段正确但**视觉全同**的子镜——测 ③ 的「视觉重复 → 带反馈重试」."""
    return [dict(_shot(i, t, dur=5.0))
            for i, t in enumerate(_segments_for(shot, cap), 1)]


def _no_derived(shot):
    """去掉派生字段（`timecode` 由 `write_timeline_from_durations` 生成）."""
    return {k: v for k, v in shot.items() if k != "timecode"}


def _over_cap_script():
    """一个超载镜（index=10）+ 两个正常镜，用于验证「只动被点名那一镜」."""
    return {"shots": [_shot(9), _shot(10, _PARENT_NARR, dur=8.0), _shot(11)],
            "hook": None, "fps": 24, "characters": [],
            "total_duration": 18.0}


def _sub_shots(n=3):
    """n 个**文本互不相同**的子镜（拼起来 = 父镜台词）."""
    if n == 3:
        texts = list(_SUB_NARRS)
    elif n == 2:
        texts = ["张十三道：" + _C1 * 2 + _C2 * 2, "张十三道：" + _C3 * 2]
    else:
        texts = ["张十三道：" + _C1 * 2 * n]
    return [dict(_shot(i, t, dur=5.0, motion=m, prompt=p, lf=l))
            for i, (t, (m, p, l)) in enumerate(zip(texts, _DISTINCT), 1)]


# ------------------------------------------------------------ 台词逐字连续

def test_narration_body_strips_prefix_and_folds_punctuation():
    from step01_script_gen.main import _narration_body

    assert _narration_body("张十三道：你最好想清楚再开口。") == "你最好想清楚再开口"
    # 省略号折叠：原文 `……` 与切开后拼回的 `…`+`…` 必须归一到同一形态
    assert _narration_body("听香道：不……知道") == _narration_body("听香道：不…知道")


def test_narration_continuous_accepts_verbatim_split():
    from step01_script_gen.main import _narration_continuous

    ok, why = _narration_continuous(_PARENT_NARR, _sub_shots(3))
    assert ok, why


def test_narration_continuous_rejects_dropped_chars():
    """少字 = 删台词 → 这是「改写」，必须拒."""
    from step01_script_gen.main import _narration_continuous

    subs = _sub_shots(3)
    subs[1]["narration_segment"] = _SUB_NARR[:-2]          # 少 2 个字
    ok, why = _narration_continuous(_PARENT_NARR, subs)
    assert not ok and "逐字" in why, why


def test_narration_continuous_rejects_added_or_reordered():
    from step01_script_gen.main import _narration_continuous

    subs = _sub_shots(3)
    subs[2]["narration_segment"] = _SUB_NARRS[2] + _C1 * 2     # 多一段
    assert not _narration_continuous(_PARENT_NARR, subs)[0]

    rev = list(reversed(_sub_shots(3)))
    ok, why = _narration_continuous(_PARENT_NARR, rev)
    assert not ok and "顺序" in why, why


def test_narration_continuous_ignores_silent_sub_shots():
    """无台词子镜（纯反应镜）合法：空正文对拼接不贡献，不算断链."""
    from step01_script_gen.main import _narration_continuous

    subs = _sub_shots(3) + [dict(_shot(4, "", dur=4.0))]
    # 拼接仍等于父镜（空段贡献 0 字）
    assert _narration_continuous(_PARENT_NARR, subs)[0]


# ---------------------------------------------------- 容量判据走质量门本体

def test_split_within_cap_uses_the_gate_itself():
    """子镜合格性由 `_shot_duration_issues` 裁决，不复制判据."""
    from step01_script_gen.main import _content_need, _split_within_cap

    subs = _sub_shots(3)
    for s in subs:
        s["duration"] = s["duration_sec"] = _content_need(s)
    assert _split_within_cap(subs)[0], "三段各 ≈5s 应当合格"


def test_split_within_cap_rejects_still_over_cap_sub():
    """某子镜仍装不下 → 拒（否则等于把超载藏进子镜里）."""
    from step01_script_gen.main import _split_within_cap

    subs = _sub_shots(2)
    subs[0]["narration_segment"] = _PARENT_NARR                    # 仍超载
    subs[0]["duration"] = subs[0]["duration_sec"] = 8.0
    ok, why = _split_within_cap(subs)
    assert not ok and "超过单镜上限" in why, why


def test_split_within_cap_enforces_non_last_floor():
    """末位子镜也要受 >=3.5s 检查（探针追加占位镜的意义）."""
    from step01_script_gen.main import _split_within_cap

    subs = _sub_shots(3)
    subs[2]["duration"] = subs[2]["duration_sec"] = 3.0
    ok, why = _split_within_cap(subs)
    assert not ok and "下限" in why, why


def test_content_need_matches_the_quality_gate_value():
    """`_content_need` 与质量门 `shot_content_over_cap` 的估值必须一致.

    它只用于**给子镜分配时长**（接受与否由质量门裁决），但两者若漂移，
    分配出来的时长就会系统性偏小/偏大，所以用回归钉住。
    """
    from step01_script_gen.main import _content_need
    from step01_script_gen.quality_issues import _shot_duration_issues

    for shot in (_shot(1, _PARENT_NARR, 8.0), _shot(2, "张十三道：住嘴", 5.0),
                 _shot(3, "", 5.0)):
        issues = [i for i in _shot_duration_issues({"shots": [shot]})
                  if i.code == "shot_content_over_cap"]
        if not issues:
            continue
        gate_need = float(issues[0].evidence.split("need=")[1].split()[0])
        assert abs(_content_need(shot) - gate_need) < 0.01, shot["index"]


# ------------------------------------------------------- 端到端：拆分与拒绝

def _patch_split_model(monkeypatch, subs, *, fail=False):
    import step01_script_gen.main as m

    calls = {"n": 0, "prompt": ""}

    class _R:
        success = not fail
        error = "" if not fail else "boom"
        text = json.dumps({"sub_shots": subs}, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            calls["n"] += 1
            calls["prompt"] = req.user_prompt
            return _R()

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_repair_timeout_sec", lambda: 30)
    return calls


def test_scoped_split_splices_and_leaves_other_shots_untouched(monkeypatch):
    import step01_script_gen.main as m

    script = _over_cap_script()
    others_before = [copy.deepcopy(script["shots"][0]), copy.deepcopy(script["shots"][2])]
    subs = _compliant_subs(script["shots"][1])
    calls = _patch_split_model(monkeypatch, subs)

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)

    assert calls["n"] == 1, "只对被点名的那一镜发一次请求"
    assert len(out["shots"]) == 2 + len(subs), "1 个超载镜 → 按分段数个子镜"
    # 其它镜头逐字节不动
    assert _no_derived(out["shots"][0]) == _no_derived(others_before[0])
    assert _no_derived(out["shots"][-1]) == _no_derived(others_before[1])
    # 子镜 index 唯一（阶段 1 的 shot_index_renumber 之后会归一成整数）
    idxs = [s["index"] for s in out["shots"][1:1 + len(subs)]]
    # 子镜 index 唯一且落在父镜与下一镜之间（阶段 1 的 shot_index_renumber 之后归一成整数）
    assert len(set(idxs)) == len(subs) and all(10 < i < 11 for i in idxs), idxs
    # 超载已消
    assert not [i for i in m._structural_shot_issues(out)
                if i.code == "shot_content_over_cap"]
    # 提示词必须讲清「这不是改写台词」，并**直接给出分段**
    assert "不是改写台词" in calls["prompt"]
    assert "逐字照用，不得改动" in calls["prompt"]
    assert "子镜1 的 narration_segment" in calls["prompt"]


def test_scoped_split_duration_is_derived_from_content(monkeypatch):
    """子镜时长由**内容需求**派生，不是照抄模型给的值."""
    import step01_script_gen.main as m

    subs = _compliant_subs(_over_cap_script()["shots"][1])
    for s in subs:
        s["duration"] = s["duration_sec"] = 7.9      # 模型写了个偏大的值
    _patch_split_model(monkeypatch, subs)

    out = m._scoped_split_over_cap_shots(_over_cap_script(), "原文", model=None)
    for s in out["shots"][1:1 + len(subs)]:
        # 「由内容派生」= 等于自己的内容需求（按帧对齐），而不是照抄模型的 7.9。
        # v6.93：不能再断言「< 7.9」——cap 8 → 12 后每段的真实需求本来就 >7.9。
        assert s["duration"] != 7.9, "不得照抄模型给的 duration"
        assert abs(s["duration"] - m._content_need(s)) < 0.06,             (s["duration"], m._content_need(s))
        assert s["duration"] >= 3.5


def test_scoped_split_rejected_on_non_verbatim_narration(monkeypatch):
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    subs = _sub_shots(3)
    subs[0]["narration_segment"] = "张十三道：我改写了台词"      # 不连续
    _patch_split_model(monkeypatch, subs)

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    assert [_no_derived(x) for x in out["shots"]] ==         [_no_derived(x) for x in before["shots"]], "台词被改写 → 整镜保持原样"


def test_scoped_split_rejected_on_single_sub_shot(monkeypatch):
    """只返回 1 个子镜 = 没拆，拒."""
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    _patch_split_model(monkeypatch, _sub_shots(1))

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    assert [_no_derived(x) for x in out["shots"]] ==         [_no_derived(x) for x in before["shots"]]


def test_scoped_split_rejected_when_model_fails(monkeypatch):
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    _patch_split_model(monkeypatch, _sub_shots(3), fail=True)

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    assert [_no_derived(x) for x in out["shots"]] ==         [_no_derived(x) for x in before["shots"]]


def test_scoped_split_skips_when_nothing_over_cap(monkeypatch):
    import step01_script_gen.main as m

    calls = _patch_split_model(monkeypatch, _sub_shots(3))
    script = {"shots": [_shot(1), _shot(2)], "hook": None, "fps": 24,
              "characters": [], "total_duration": 10.0}
    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    assert out is script and calls["n"] == 0, "无超载时不得调模型"


def test_structural_regen_prefers_scoped_split_over_whole_script_regen(monkeypatch):
    """结构收敛轮**先**走定向拆分；拆干净了就不该再发整脚本重生成."""
    import step01_script_gen.main as m

    whole = {"n": 0}
    split_calls = {"n": 0}
    _subs = _compliant_subs(_over_cap_script()["shots"][1])

    class _R:
        success = True
        error = ""
        text = json.dumps({"sub_shots": _subs}, ensure_ascii=False)

    class _Svc:
        """同一个 service 要服务两类请求：拆分（本次要走的）与整脚本 regen."""

        def generate(self, req):
            _name = (req.json_schema or {}).get("name", "")
            if _name == "split_over_cap_shot":
                split_calls["n"] += 1
                return _R()
            whole["n"] += 1
            raise AssertionError("拆干净了不该再走整脚本 regen")

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_repair_timeout_sec", lambda: 30)
    script = _over_cap_script()
    out = m._regen_structural_over_limit(script, "原文", max_attempts=1)

    assert split_calls["n"] == 1
    assert whole["n"] == 0, "定向拆分已消掉超载，整脚本 regen 不应被调用"
    assert len(out["shots"]) == 2 + len(_subs)


# --------------------------------------------- 子镜近似重复判据（v6.89 续）

def test_split_has_duplicate_pair_uses_the_repo_wide_detector():
    """判据复用 `find_adjacent_duplicate_pairs`，阈值不复制.

    该检测器**会跳过拆镜子镜**（半整数 index / `_split_from`），而子镜此刻正是
    半整数——所以判据必须先用临时整数 index 复制再判定，否则永远不触发。
    """
    from step01_script_gen.main import _split_has_duplicate_pair

    distinct = _sub_shots(3)
    assert _split_has_duplicate_pair(distinct)[0], "互不相同的子镜应当通过"

    same = [dict(_shot(i, t, dur=5.0)) for i, t in enumerate(_SUB_NARRS, 1)]
    ok, why = _split_has_duplicate_pair(same)
    assert not ok and "视觉重复" in why, why


def test_scoped_split_rejected_when_sub_shots_repeat_the_same_action(monkeypatch):
    """子镜相邻视觉重复 → 整条拆分被拒.

    v6.89 续（2026-09-22 07:40 实测）：拆分第一次真实生效，就在最终门带出
    `镜头11/12 相邻且视觉重复（相似度 89%）`——两条子镜来自同一父镜，模型把同一个
    动作写了两遍。拆分 prompt 里写了「禁止把同一个动作在两个子镜里各演一遍」，
    模型不遵守（与发现 14 同类），所以做成**确定性采纳判据**。
    """
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    # 必须用**分段正确、视觉全同**的 fixture——写死段数会因「段数不符」提前返回，
    # 那样这条测试就测不到「视觉重复」这个判据（v6.94 自查发现的测错风险）。
    same = _dup_subs(script["shots"][1])
    _patch_split_model(monkeypatch, same)

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    assert [_no_derived(x) for x in out["shots"]] ==         [_no_derived(x) for x in before["shots"]], "视觉重复的子镜 → 整条拆分作废"


# ------------------------------------- 确定性切点（v6.92：切点不交给模型）

def test_deterministic_cuts_are_sentence_ended_and_within_cap():
    """切点由 `(台词文本, 上限)` 唯一决定：各段 ≤ 上限、**各段句末收尾**（v6.136）.

    交给模型只会得到三种失败（实测采纳率 1/4-2/4）：「不均衡」（子镜仍 11.9s）、
    「只回一段」（20.2s 那条直接拒绝拆）、「补字」（原 76 字拼回 120 字）。
    「台词逐字连续」是唯一语义约束 → **切在哪是纯函数**；v6.136 起合法切点
    收窄为句末边界——同段数下「最长段最短」仍优先，但句末点稀疏时**合法优先
    于均衡**（09-26 实测：64 字口吃台词切 3 段 5.85/5.85/3.75，不均衡但合法）。
    """
    from step01_script_gen.main import _deterministic_narration_cuts
    from step01_script_gen.timecode import _estimate_duration
    from step01_script_gen.main import _narration_body

    # 真实产物里那条 64 字口吃台词（09-26 三次实跑的实损形态）
    narr = ("听香道：不…不知道，那人…那人一下子从水里跳出来，就…就杀了公子，"
            "然后又…又跳进水里不见了，奴家…奴家连他是男是女都没看清…")
    parts = _deterministic_narration_cuts(narr, 8.0)
    assert parts and 2 <= len(parts) <= 4, parts
    for p in parts:
        assert _estimate_duration(p) <= 8.01, (p, _estimate_duration(p))
        assert _narration_body(p)[-1] in "。！？…；",             "每段必须以句末字符收尾（句中碎片 = 终稿门 cross_shot_split）: %r" % p
    # 逐字连续：拼回来等于原文（去前缀/标点后）
    assert "".join(_narration_body(p) for p in parts) == _narration_body(narr)


def test_deterministic_cuts_refuse_when_parts_exceed_the_ceiling():
    """需求段数 > 4（schema 上限）→ None，交整脚本 regen.

    这是**真正**切不出的情形：40s 的台词在 8s 上限下需要 5 段，超过 4 段上限。
    """
    from step01_script_gen.main import _deterministic_narration_cuts

    narr = "张十三道：" + "你最好想清楚再开口，" * 16      # ≈40s 需求 → 需 5 段
    assert _deterministic_narration_cuts(narr, 8.0) is None


def test_deterministic_cuts_refuse_without_sentence_end_bounds():
    """无句末边界（无标点/只有逗号）→ None，**等字长兜底已废除**（v6.136 A+B）.

    旧取舍（等字长兜底）产出的句中碎片正是终稿台词门 `cross_shot_split` 判破损、
    审计报"同一句被截成两半"的形态——"拆分成功、终稿失败"的直接机制（09-26 三次
    实跑复现）。统一判据后：无句末合法切法 → 在调用拆分模型**之前**停下（None），
    交整脚本 regen／规划层重选（规划层可表示性检查会提前拦住这类台词）。
    """
    from step01_script_gen.main import _deterministic_narration_cuts

    assert _deterministic_narration_cuts("张十三道：" + "你" * 60, 8.0) is None
    # 只有逗号（09-26 实损的 84 字台词同型：单句超长、句内只有逗号）同样拒绝
    assert _deterministic_narration_cuts("张十三道：" + "你最好想清楚再开口，" * 6, 8.0) is None


def test_deterministic_cuts_never_split_off_a_half_sentence():
    """切点必须落在**句末**之后（v6.136：句中碎片不可读，也是审计 truncated 形态）."""
    from step01_script_gen.main import _deterministic_narration_cuts, _narration_body

    narr = "张十三道：" + "你最好想清楚再开口。" * 6
    for seg in _deterministic_narration_cuts(narr, 8.0):
        # 注意用原文末字符断言（_narration_body 会把标点折叠掉）
        assert seg.rstrip()[-1] in "。！？…；", seg


def test_cut_search_finds_feasible_solution_with_sparse_sentence_bounds():
    """句末切点**稀疏且不均匀**时仍必须找到可行解（v6.136：合法优先于均衡）.

    v6.92 修（2026-09-22）：可用标点位置不均匀，只试「均衡切点」会把可行解误判成
    "无法切分"。v6.136 起合法切点收窄为句末——切点更稀疏，「先可行、再均衡」的
    优先级不变。单句超长（句内只有逗号，如 09-26 实损的 84 字台词）则**没有**
    合法解 → None，由规划层可表示性检查在生成前拦住（见
    test_deterministic_cuts_refuse_without_sentence_end_bounds）。
    """
    from step01_script_gen.main import _deterministic_narration_cuts
    from step01_script_gen.timecode import _estimate_duration
    from step01_script_gen.main import _narration_body

    narr = ("听香道：不…不知道，那人…那人一下子从水里跳出来，就…就杀了公子，"
            "然后又…又跳进水里不见了，奴家…奴家连他是男是女都没看清…")
    parts = _deterministic_narration_cuts(narr, 8.0)
    assert parts, "省略号句末边界存在 ⇒ 必须能切（09-26 实损台词的可保留依据）"
    assert len(parts) <= 4
    for p in parts:
        assert _estimate_duration(p) <= 8.01, (p, _estimate_duration(p))
        assert _narration_body(p)[-1] in "。！？…；", p
    # 逐字连续
    assert "".join(_narration_body(p) for p in parts) == _narration_body(narr)


def test_cut_search_keeps_segments_balanced_when_both_are_feasible():
    """可行解里挑**最长段最短**的那个（均衡是第二优先级，不是被放弃）."""
    from step01_script_gen.main import _deterministic_narration_cuts
    from step01_script_gen.timecode import _estimate_duration

    parts = _deterministic_narration_cuts("张十三道：" + "你最好想清楚再开口。" * 6, 8.0)
    d = [_estimate_duration(p) for p in parts]
    assert max(d) - min(d) < 1.5, d


# --------------------------------------------------- ③ 带反馈重试（v6.94）

def _patch_split_sequence(monkeypatch, responses):
    """按调用**次序**依次返回 responses（测「首次重复 → 带反馈重试」用）."""
    import step01_script_gen.main as m

    calls = {"n": 0, "prompts": []}

    class _R:
        success = True
        error = ""
        def __init__(self, subs):
            self.text = json.dumps({"sub_shots": subs}, ensure_ascii=False)

    class _Svc:
        def generate(self, req):
            i = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            calls["prompts"].append(req.user_prompt)
            return _R(responses[i])

    monkeypatch.setattr(m, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    monkeypatch.setattr(m, "_repair_timeout_sec", lambda: 30)
    return calls


def test_split_retries_once_with_feedback_on_visual_duplication(monkeypatch):
    """视觉重复 → **带反馈重试一次**，第二次通过则采纳（v6.94 ③）.

    prompt 里已写明「相邻子镜必须换机位/景别」，模型仍照抄（第九次实测相似度 98%）
    ——与发现 14 同型：**prompt 说了、模型不做**。所以把「你的子镜1/2 相似度 98%」
    这个**具体失败事实**回喂，而不是再泛泛要求一次。
    """
    import step01_script_gen.main as m

    script = _over_cap_script()
    dup = _dup_subs(script["shots"][1])                  # 分段正确、视觉全同
    fixed = _compliant_subs(script["shots"][1])          # 分段正确、机位/景别不同
    calls = _patch_split_sequence(monkeypatch, [dup, fixed])

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)

    assert calls["n"] == 2, "视觉重复必须带反馈重试一次"
    assert "上一次尝试被拒" in calls["prompts"][1]
    assert "视觉重复" in calls["prompts"][1], "重试 prompt 要说清被拒的具体原因"
    assert len(out["shots"]) == 2 + len(fixed), "重试通过后应采纳该拆分"


def test_split_gives_up_after_single_retry(monkeypatch):
    """重试后仍重复 → 放弃（不无限重试），且整镜保持原样."""
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    dup = _dup_subs(script["shots"][1])
    calls = _patch_split_sequence(monkeypatch, [dup, dup])

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)

    assert calls["n"] == 2, "只重试一次"
    assert [_no_derived(x) for x in out["shots"]] == \
        [_no_derived(x) for x in before["shots"]]


# ------------------------------------- ② 子镜时长覆盖自己的 KF（v6.95）

def test_split_sub_shot_duration_covers_its_own_keyframes(monkeypatch):
    """子镜时长必须覆盖自己的最大 KF 时刻（同发现 18 的钩子）.

    第十次实测：`shot_content_over_cap` 归零，但新暴露 `kf_time_out_of_shot` × 3——
    子镜的 `keyframes[].t_sec` 是模型按**自己以为的时长**写的，而子镜时长由**内容需求**
    派生，两者没对账，于是有帧落在镜长之外。
    """
    import step01_script_gen.main as m

    script = _over_cap_script()
    subs = _compliant_subs(script["shots"][1])
    for s in subs:
        # KF 写在一个**比内容需求更长**、但仍在 cap 内的时刻
        s["keyframes"] = [{"desc": "抬手", "stage": "pre", "t_sec": 9.5}]
    _patch_split_model(monkeypatch, subs)

    out = m._scoped_split_over_cap_shots(script, "原文", model=None)
    adopted = out["shots"][1:1 + len(subs)]
    assert len(adopted) == len(subs), "该拆分应被采纳"
    for s in adopted:
        assert s["duration"] >= 9.5, \
            f"时长必须覆盖自己的 KF 时刻，实得 {s['duration']}"


def test_split_retries_when_keyframe_exceeds_the_cap(monkeypatch):
    """KF 时刻超出单镜上限 → 不可覆盖 → 带反馈重试（不是静默留一个越界帧）."""
    import step01_script_gen.main as m

    script = _over_cap_script()
    from step01_script_gen.main import _shot_cap
    bad = _compliant_subs(script["shots"][1])
    for s in bad:
        s["keyframes"] = [{"desc": "抬手", "stage": "pre",
                           "t_sec": _shot_cap() + 3.0}]        # 超过 cap，覆盖不下
    good = _compliant_subs(script["shots"][1])
    for s in good:
        s["keyframes"] = [{"desc": "抬手", "stage": "pre", "t_sec": 5.0}]

    calls = _patch_split_sequence(monkeypatch, [bad, good])
    out = m._scoped_split_over_cap_shots(script, "原文", model=None)

    assert calls["n"] == 2, "KF 超出上限应带反馈重试一次"
    assert "KF" in calls["prompts"][1], "重试 prompt 要点明 KF 问题"
    assert len(out["shots"]) == 2 + len(good), "重试通过后应采纳"


def test_split_gives_up_when_keyframe_still_exceeds_the_cap(monkeypatch):
    import step01_script_gen.main as m

    script = _over_cap_script()
    before = copy.deepcopy(script)
    from step01_script_gen.main import _shot_cap
    bad = _compliant_subs(script["shots"][1])
    for s in bad:
        s["keyframes"] = [{"desc": "抬手", "stage": "pre", "t_sec": _shot_cap() + 3.0}]

    calls = _patch_split_sequence(monkeypatch, [bad, bad])
    out = m._scoped_split_over_cap_shots(script, "原文", model=None)

    assert calls["n"] == 2
    assert [_no_derived(x) for x in out["shots"]] == \
        [_no_derived(x) for x in before["shots"]], "重试后仍不合法 → 整镜保持原样"
