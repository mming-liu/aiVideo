# -*- coding: utf-8 -*-
"""shared.screen_cards.plan —— `screen_card_plan.v1`：卡片**清单**与**同镜叠加分段**.

主专题 §6.2 的规则逐条落实：

- **不冒用 `motion_overlay_plan.v1`**：卡片清单是自己的 schema，消费者按 schema 分派；
- 清单含 scope、script revision、来源计划摘要、正文摘要、字体/资产摘要、**稳定镜头身份**、
  局部时窗及**实际分页映射**；
- **局部时间只转换为绝对时间一次**：`abs = 窗口起点 + 局部偏移`；
- 帧率与量化复用 `shared.media_contract`；**所有区间用整数帧半开区间**，秒数只在 FFmpeg
  边界转换；
- 卡片只用目标镜头**完全可见**的窗口：排除首淡入、末淡出及**交叉转场重叠区**；
  可用窗口按**最终时间线**算，不能按 `sum(clip.duration)` 累加；
- 延续卡片拆成镜头内片段，**转场区不显示**，阅读时间只计**实际可见帧**，不把不可见的
  间隙算进去；单页要连续阅读就必须在一个连续可用窗装下，**不跨间隙凑时长**；
- **全时间线**同时出现的卡片统一分段合成（不只做同镜局部检查）；
- 旧动效与新卡片在同镜冲突 ⇒ **明确拒收并报告**，不静默覆盖。
"""
from __future__ import annotations

import hashlib
import json
import os

from shared.media_contract import MERGE_FPS, quantize_frame_count

from . import profile as _profile
from .layout import LayoutError, load_font, wrap_lines

SCHEMA_CARD_PLAN = "screen_card_plan.v1"

#: 默认卡位。
DEFAULT_PLACEMENT = _profile.PLACEMENT_UPPER


def _digest(obj) -> str:
    _t = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    default=str)
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


def _p(code, reason, path="", **extra):
    _d = {"code": code, "kind": "card", "path": path, "reason": reason,
          "detail": reason}
    _d.update(extra)
    return _d


def text_digest(text) -> str:
    """卡片**正文摘要**（进关系、清单与校验；字面相同才算同一段正文）."""
    return _digest(str(text or ""))


# ---------------------------------------------------------------------------
# ① Step01 侧的**来源关系**：卡片挂在哪条节拍上、承载哪些事实
# ---------------------------------------------------------------------------
def card_relations(plan, *, problems=None) -> list:
    """计划 → 卡片来源关系（`presentation.screen_cards` + `fact_bindings` 反查）.

    每条：`card_id / beat_uid / template / text / placement / anchor / source_spans /
    fact_uids`。**只读计划**，不猜、不补。

    ⚠️ `fact_uids` 取绑定里的**真实 `fact_uid`**，**不按绑定数组位置重新编号**
    （复核 2026-10-05 P1）：`fact_bindings` 的排列不携带含义（§33 P1-1 已经为此修过
    一次），按位置造 `f%02d` 会让"调换两行绑定"变成"卡片承载了另一条事实"——卡片清单
    会核错绑定而不自知。

    ⚠️ **消费准入先跑**（复核 2026-10-06 第十二轮 ①）：`for _c in (...)` 与
    `(_car or {}).get(...)` 这类写法假定"成员是对象、`carriers` 是数组"，坏输入直接抛
    `TypeError` / `AttributeError`。这里**不逐处加 `isinstance` 后 `continue`**（那是
    **静默丢关系**），而是在入口用**与视图共用的**那一份准入检查一次；不过就**返回空
    关系**并把问题交给调用方（准备阶段转 `ScreenCardError`，见
    `step04_merge.screen_cards.card_relations_for`）。
    """
    from shared.presentation_contract import view_admission_problems
    _pres = (plan or {}).get("presentation")
    if not isinstance(_pres, dict):
        return []
    _adm = view_admission_problems(
        {"screen_cards": _pres.get("screen_cards"),
         "fact_bindings": _pres.get("fact_bindings")})
    if _adm:
        if problems is not None:
            problems.extend(_adm)
        return []
    _out = []
    for _c in (_pres.get("screen_cards") or []):
        _cid = str(_c.get("card_id") or _c.get("uid") or "")
        _facts = []
        for _b in (_pres.get("fact_bindings") or []):
            _fu = str(_b.get("fact_uid") or "")
            for _car in (_b.get("carriers") or []):
                if str(_car.get("kind")) == "screen_card" \
                        and str(_car.get("card_id") or _car.get("uid")) == _cid:
                    _facts.append(_fu)
        _out.append({
            "card_id": _cid,
            "beat_uid": str(_c.get("beat_uid") or ""),
            "template": str(_c.get("template") or ""),
            "text": str(_c.get("text") or ""),
            "text_digest": text_digest(_c.get("text") or ""),
            "placement": str(_c.get("placement") or DEFAULT_PLACEMENT),
            "anchor": str(_c.get("anchor") or "beat_start"),
            "source_spans": [dict(x) for x in (_c.get("source_spans") or [])
                             if isinstance(x, dict)],
            "fact_uids": sorted({x for x in _facts if x}),
        })
    return _out


# ---------------------------------------------------------------------------
# ② 可用窗口：排除淡入/淡出与交叉转场重叠（整数帧半开区间）
# ---------------------------------------------------------------------------
def available_windows(timeline) -> list:
    """每镜的**可用展示窗口**（整数帧 `[start, end)`）——按**最终时间线**算.

    排除三样：首镜淡入、末镜淡出、与相邻镜的**交叉转场重叠区**。转场区的画面是两镜
    混合的，卡片在那里既不清楚也会被下一条覆盖。
    """
    from step04_merge.timeline import compute_windows
    _fps = int(getattr(timeline, "fps", MERGE_FPS) or MERGE_FPS)
    _wins = compute_windows(timeline.clips, timeline.boundary_transitions)
    _by_index = {int(b.index): b for b in timeline.boundary_transitions}
    _eff = {}
    for _e in timeline.endpoint_effects:
        _eff[(int(_e.clip_index), str(_e.kind))] = float(_e.duration_sec)

    def _pos(sec) -> int:
        """**时间位置** → 整数帧。位置**允许 0**（整条时间线的起点就是 0）。

        ⚠️ 这里不能直接用 `media_contract.quantize_frame_count`：那是**时长**函数，
        规定"不足 1 帧即非法"，于是**第一段镜头的起点 0 秒被它判成非法**（复核
        2026-10-05 P1）。位置与时长是**两件事**：位置可以为 0 且任何非负值合法；
        时长才必须为正。帧率与量化公式共用同一个 `floor(x*fps+0.5)`。
        """
        import math
        return int(math.floor(max(0.0, float(sec)) * _fps + 0.5))

    def _dur(sec) -> int:
        """**时长** → 整数帧（必须 ≥1 帧；0/负数是**非法时长**，走既有异常出口）."""
        return quantize_frame_count(sec, _fps)

    _out = []
    for _i, _w in enumerate(_wins):
        _s = _pos(_w.start_sec)
        _e = _pos(_w.start_sec) + _dur(_w.duration_sec)
        # 转场重叠：入边（与本镜前一条的重叠）与出边（与本镜后一条的重叠）
        _tin = _by_index.get(_i)
        if _tin is not None and float(_tin.duration_sec) > 0:
            _s = _pos(_w.start_sec + float(_tin.duration_sec))
        _tout = _by_index.get(_i + 1)
        if _tout is not None and float(_tout.duration_sec) > 0:
            _e = _pos(_wins[_i + 1].start_sec)
        # 端点效果：首淡入 / 末淡出
        _fi = _eff.get((_i, "fade_in"))
        if _fi:
            _s = max(_s, _pos(_w.start_sec + _fi))
        _fo = _eff.get((_i, "fade_out"))
        if _fo:
            _e = min(_e, _pos(_w.end_sec - _fo))
        _out.append({"clip_index": _i, "start_frame": int(_s),
                     "end_frame": int(max(_s, _e))})
    return _out


# ---------------------------------------------------------------------------
# ③ 建清单：卡位分组 → 时序切分 → 分页 → 资产
# ---------------------------------------------------------------------------
def _distribute(total: int, weights) -> list:
    """把 `total` 帧按 `weights` 分给各页：**每页 ≥1 且总和恰好 `total`**.

    原型是「先各自 `max(1, round(...))`、最后一页兜底」——四舍五入会把前面几页推高，
    最后一页的兜底随后变成 `max(1, 负数)`=1，于是**总帧数超过**分到的窗口，最后一页
    的 `end_frame` 越出卡片窗口（校验时报 `card_plan_page_out_of_window`，而真正的原因
    在这里）。改成按权重分配后**逐项修正**到恰好。
    """
    _k = len(weights)
    if _k == 0:
        return []
    _w = [max(1, int(x)) for x in weights]
    _s = float(sum(_w))
    _out = [max(1, int(total * x / _s)) for x in _w]
    _diff = int(total) - sum(_out)
    _i = 0
    while _diff != 0 and _i < 16 * _k:
        _j = _i % _k
        if _diff > 0:
            _out[_j] += 1
            _diff -= 1
        elif _out[_j] > 1:
            _out[_j] -= 1
            _diff += 1
        _i += 1
    return _out


def _required_frames(text, placement, prof) -> int:
    """一条卡**至少要连续可见多少帧**才能读完（一个连续可用窗里装下）.

    排不下返 `-1`（**不是**抛异常）：`load_font`（缺字体）与 `slot_rect`（卡位非法）
    抛的是 `LayoutError`/`ValueError`，在这里转成"这条卡排不下"，让调用方按同一个
    出口报结构化问题——清单生成器不许在预检阶段抛 traceback。
    """
    from .layout import LayoutError, layout_checked
    try:
        _rect = _profile.slot_rect(prof, placement)
    except ValueError:
        return -1
    try:
        _pages, _probs = layout_checked(text, prof, rect=_rect)
    except LayoutError:
        return -1
    if _probs:
        return -1
    return sum(int(x.frames) for x in _pages)


def build_card_plan(timeline, *, relations, assets=None, source_plan_digest="",
                    script_revision=None, scope=None, prof=None,
                    out_dir=None) -> tuple:
    """按最终时间线把卡片**排进**可用窗口 → `(manifest, problems)`.

    `assets`：`{card_id: [CardAsset, ...]}`（已按页渲染好的资产，见 `render.render_card`）。
    **省略/`None`/空表 = 预检模式**：照样产出清单与页时窗，只是不带资产路径，也不核页数
    ——用来回答"这份计划排得下吗"。给了资产就**逐张核页数与摘要**，页数不符判
    `card_asset_page_mismatch`（资产与正文/字体/profile 不是同一版，必须重建）。

    切分规则（确定性、可复算）：

    1. 同一镜、同一**卡位**的卡片按节拍顺序**顺序切分**该卡位的可用窗口；
    2. 每张卡把自己那份按**页序**切分（页是必须连续阅读的单元；帧数**恰好**分完）；
    3. 任何一张卡分到的连续帧数 < 它需要的最少阅读帧数 ⇒
       `presentation_window_insufficient`（**不自动加时、不慢放、不截字**）。
    """
    _prof = prof or _profile.active_profile()
    _fps = int(getattr(timeline, "fps", MERGE_FPS) or MERGE_FPS)
    _scope = str(scope or getattr(timeline, "scope", "") or "")
    _rev = str(script_revision or getattr(timeline, "script_revision", "") or "")
    _problems: list = []

    _clips = list(getattr(timeline, "clips", ()) or ())
    _by_id = {(str(c.kind), int(c.shot_index)): c for c in _clips}
    _wins = available_windows(timeline)

    # 卡位 → 可用窗口（同一镜同卡位共用一段）
    _slots: dict = {}
    _resolved: list = []
    for _r in relations:
        _need = _required_frames(_r["text"], _r["placement"], _prof)
        if _need < 0:
            _problems.append(_p("card_layout_overflow",
                                "卡片 %s 排不下（卡位 %s）：正文过长或该卡位太矮"
                                % (_r["card_id"], _r["placement"]),
                                "presentation.screen_cards[%s]" % _r["card_id"]))
            continue
        # 节拍 → 镜头：`beat_uid` 先映射到**当前版本节拍编号**，再经 plan_refs 找到镜头。
        # 这一步需要稿件；离线清单生成由调用方给出 `shot_of_beat`。
        _shot = _r.get("shot_index")
        if _shot is None:
            _problems.append(_p("card_beat_unbound",
                                "卡片 %s 还没有绑定到镜头（节拍 %s → 镜头映射缺失）"
                                % (_r["card_id"], _r.get("beat_uid")),
                                "presentation.screen_cards[%s]" % _r["card_id"]))
            continue
        _key = ("hook", 0) if int(_shot) == 0 and ("hook", 0) in _by_id else ("shot", int(_shot))
        _clip = _by_id.get(_key)
        if _clip is None:
            _problems.append(_p("card_shot_not_found",
                                "卡片 %s 指向不存在的镜头 shot_index=%s"
                                % (_r["card_id"], _shot),
                                "presentation.screen_cards[%s]" % _r["card_id"]))
            continue
        _r = dict(_r, clip_index=_clips.index(_clip),
                  stable_shot_id=str(_clip.stable_id), kind=str(_clip.kind),
                  shot_index=int(_clip.shot_index), need_frames=int(_need))
        _resolved.append(_r)
        _slots.setdefault((_r["clip_index"], _r["placement"]), []).append(_r)

    # **展示预算在合计这一层判**：`reading_budget_sec` 是**整章所有卡合计**的上限，
    # 单卡那层不核（拿章级预算当卡级判据会把一张长卡误判成"超预算"）。
    _reading_total = sum(int(x["need_frames"]) for x in _resolved) / float(_fps)
    if _reading_total > float(_prof.reading_budget_sec):
        _problems.append(_p(
            "presentation_reading_budget_exceeded",
            "文字卡合计阅读需求 %.2fs 超过展示预算 %.2fs（%d 张卡）——拆页、转移信息或"
            "调整镜头；**不自动加时、慢放、截字或减少核心事实**"
            % (_reading_total, _prof.reading_budget_sec, len(_resolved)),
            "presentation.screen_cards",
            required_sec=round(_reading_total, 3),
            budget_sec=float(_prof.reading_budget_sec), cards=len(_resolved)))

    _cards: list = []
    for (_ci, _pl), _group in sorted(_slots.items(), key=lambda x: (x[0][0], x[0][1])):
        _win = _wins[_ci]
        _lo, _hi = int(_win["start_frame"]), int(_win["end_frame"])
        _span = max(0, _hi - _lo)
        _need_total = sum(int(x["need_frames"]) for x in _group)
        if _span < _need_total:
            for _r in _group:
                _problems.append(_p(
                    "presentation_window_insufficient",
                    "卡片 %s 的实际可用窗口不够：该镜卡位 %s 有 %d 张卡共需 %d 帧，"
                    "实际可用只有 %d 帧（镜头 %s，revision %s）——拆页、转移信息或调整"
                    "镜头；**不自动加时、不慢放、不截字**"
                    % (_r["card_id"], _pl, len(_group), _need_total, _span,
                       _r["stable_shot_id"], _rev),
                    "presentation.screen_cards[%s]" % _r["card_id"],
                    card_id=_r["card_id"], shot_id=_r["stable_shot_id"],
                    required_frames=int(_need_total), available_frames=int(_span),
                    source_revision=_rev))
            continue
        # 顺序切分：每张卡拿到一段**连续**可用窗
        _cur = _lo
        for _r in _group:
            _n = int(_r["need_frames"])
            _cs, _ce = _cur, _cur + _n
            _cur = _ce
            _pages = _paginate_for_plan(_r["text"], _r["placement"], _prof)
            if _pages is None:
                _problems.append(_p("card_layout_overflow",
                                    "卡片 %s 排不下" % _r["card_id"],
                                    "presentation.screen_cards[%s]" % _r["card_id"]))
                continue
            # 按**页序**切分这张卡的那段连续窗（帧数**恰好**分完，不多不少）
            _pframes = _distribute(_n, [int(x.frames) for x in _pages])
            _pt = _cs
            _page_recs = []
            for _x, _f in zip(_pages, _pframes):
                _page_recs.append({"page_index": int(_x.index),
                                   "text_start": int(_x.start),
                                   "text_end": int(_x.end),
                                   "start_frame": int(_pt), "end_frame": int(_pt + _f)})
                _pt += _f
            _card_assets = list((assets or {}).get(_r["card_id"]) or [])
            if _card_assets and len(_card_assets) != len(_page_recs):
                # 只有**真的给了资产**才核页数；`assets=None`/空表 = 预检模式
                #（"这份计划排得下吗"），此时不产出资产路径也不该被判页数不符。
                _problems.append(_p(
                    "card_asset_page_mismatch",
                    "卡片 %s 的资产有 %d 页而排布算出 %d 页——资产与正文/字体/profile "
                    "不是同一版，必须重建" % (_r["card_id"], len(_card_assets),
                                              len(_page_recs)),
                    "presentation.screen_cards[%s]" % _r["card_id"]))
                continue
            for _pr, _pr_a in zip(_page_recs, _card_assets):
                _pr["asset"] = str(getattr(_pr_a, "path", ""))
                _pr["sha256"] = str(getattr(_pr_a, "sha256", ""))
                _pr["placement"] = _r["placement"]
                _pr["card_id"] = _r["card_id"]
            _cards.append({
                "card_id": _r["card_id"], "beat_uid": _r["beat_uid"],
                "template": _r["template"], "placement": _r["placement"],
                "text": _r["text"], "text_digest": _r["text_digest"],
                "fact_uids": list(_r["fact_uids"]),
                "source_spans": list(_r["source_spans"]),
                "shot_index": int(_r["shot_index"]), "kind": _r["kind"],
                "stable_shot_id": _r["stable_shot_id"],
                "window": {"start_frame": int(_cs), "end_frame": int(_ce)},
                "pages": _page_recs,
            })

    if _problems:
        return None, _problems

    _manifest = {
        "schema": SCHEMA_CARD_PLAN, "scope": _scope, "script_revision": _rev,
        "fps": _fps, "source_plan_digest": str(source_plan_digest or ""),
        "profile": _profile.profile_manifest(_prof),
        "cards": _cards,
        "segments": [],
    }
    _segs, _sp = segment_overlays(_manifest, timeline, out_dir=out_dir, prof=_prof)
    if _sp:
        return None, _sp
    _manifest["segments"] = _segs
    return _manifest, []


def _paginate_for_plan(text, placement, prof):
    """排不出页表就返 `None`（**不抛**）——调用方按同一个出口报结构化问题."""
    from .layout import LayoutError, layout_checked
    try:
        _rect = _profile.slot_rect(prof, placement)
    except ValueError:
        return None
    try:
        _pages, _probs = layout_checked(text, prof, rect=_rect)
    except LayoutError:
        return None
    return None if _probs else _pages


# ---------------------------------------------------------------------------
# ④ 全时间线分段合成：同一时刻同时在场的卡片合成一张透明图
# ---------------------------------------------------------------------------
def segment_overlays(manifest, timeline=None, *, out_dir=None, prof=None) -> tuple:
    """把**全时间线**的卡片页按同时在场关系分段 → `(segments, problems)`.

    每个基本段内"同时在场的页集合"不变；相邻同集合的段合并成一段；每段合成**一张**透明
    图并输出一个不重叠的叠加条目。**同一段内两张卡必须占不同卡位**，否则明确拒收
    （不静默叠着画）。
    """
    from PIL import Image
    _prof = prof or _profile.active_profile()
    _segs: list = []
    _problems: list = []
    _spans = []
    for _c in manifest.get("cards") or []:
        for _pg in _c.get("pages") or []:
            _spans.append((int(_pg["start_frame"]), int(_pg["end_frame"]),
                           "%s#%d" % (_c["card_id"], _pg["page_index"]), _c, _pg))
    if not _spans:
        return [], []
    _bounds = sorted({x[0] for x in _spans} | {x[1] for x in _spans})
    _raw: list = []
    for _i in range(len(_bounds) - 1):
        _a, _b = _bounds[_i], _bounds[_i + 1]
        if _b <= _a:
            continue
        _live = sorted({x[2] for x in _spans if x[0] <= _a and x[1] >= _b})
        if not _live:
            continue
        if _raw and _raw[-1]["members"] == _live and _raw[-1]["end_frame"] == _a:
            _raw[-1]["end_frame"] = _b
        else:
            _raw.append({"start_frame": _a, "end_frame": _b, "members": _live})
    _by_key = {"%s#%d" % (c["card_id"], p["page_index"]): (c, p)
               for c in (manifest.get("cards") or []) for p in (c.get("pages") or [])}
    for _s in _raw:
        _slots = {}
        _bad = False
        for _k in _s["members"]:
            _c, _pg = _by_key[_k]
            _pl = str(_c.get("placement") or DEFAULT_PLACEMENT)
            if _pl in _slots:
                _problems.append(_p(
                    "card_slot_conflict",
                    "同一时刻有两张卡占同一个卡位 %s（%s 与 %s，帧 %d..%d）——同镜并显"
                    "必须用不同卡位，**不静默叠着画**"
                    % (_pl, _slots[_pl], _k, _s["start_frame"], _s["end_frame"]),
                    "segments"))
                _bad = True
            _slots[_pl] = _k
        if _bad:
            continue
        _comps = [_by_key[_k][1] for _k in _s["members"]]
        _entry = {"start_frame": int(_s["start_frame"]),
                  "end_frame": int(_s["end_frame"]),
                  "members": list(_s["members"]),
                  "placement": sorted(_slots),
                  "asset": "", "sha256": ""}
        if out_dir is not None and all(x.get("asset") for x in _comps):
            _img = None
            for _x in _comps:
                try:
                    _im = Image.open(_x["asset"]).convert("RGBA")
                except OSError as exc:
                    _problems.append(_p("card_asset_unreadable",
                                        "卡片资产读不动（%s）：%s" % (_x["asset"], exc)))
                    _img = None
                    break
                _img = _im if _img is None else Image.alpha_composite(_img, _im)
            if _problems:
                continue
            import os
            _pdir = os.path.join(str(out_dir), "segments")
            os.makedirs(_pdir, exist_ok=True)
            _path = os.path.join(_pdir, "seg_%04d_%04d.png"
                                 % (_entry["start_frame"], _entry["end_frame"]))
            _img.save(_path, "PNG")
            _entry["asset"] = _path
            _entry["sha256"] = _file_sha256(_path)
        _segs.append(_entry)

    # 与旧动效的同镜冲突：**明确拒收**，不静默覆盖
    if timeline is not None:
        _ovl = []
        for _o in (getattr(timeline, "overlays", ()) or ()):
            _at = _stable_index(timeline, _o)
            if _at is not None:
                _ovl.append((_at, float(_o.start_sec), float(_o.end_sec),
                             str(_o.overlay_id)))
        _fps = int(getattr(timeline, "fps", MERGE_FPS) or MERGE_FPS)
        for _s in _segs:
            for _ci, _a, _b, _oid in _ovl:
                _sa = _s["start_frame"] / float(_fps)
                _sb = _s["end_frame"] / float(_fps)
                if _ci < 0:
                    continue
                if min(_sb, _b) - max(_sa, _a) > (1.0 / _fps):
                    _problems.append(_p(
                        "card_motion_overlay_conflict",
                        "卡片段（帧 %d..%d）与旧动效 %s 在同一镜头的同一时间段重叠——"
                        "两套叠加编排不能静默覆盖，请择一" % (_s["start_frame"],
                                                              _s["end_frame"], _oid),
                        "segments"))
    return ([_s for _s in _segs], _problems)


def _stable_index(timeline, overlay):
    """旧动效条目 → 它落在第几个 clip（按稳定身份匹配，不按编号猜）."""
    for _i, _c in enumerate(getattr(timeline, "clips", ()) or ()):
        if str(_c.stable_id) == str(getattr(overlay, "stable_shot_id", "")):
            return _i
    return -1


def _file_sha256(path) -> str:
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _c in iter(lambda: _f.read(1 << 16), b""):
            _h.update(_c)
    return _h.hexdigest()


# ---------------------------------------------------------------------------
# ⑤ 清单校验与叠加条目导出
# ---------------------------------------------------------------------------
def validate_card_plan(obj, *, timeline=None, relations=None, prof=None,
                      source_plan_digest=None) -> list:
    """清单校验：schema / **身份** / 正文摘要 / 分页映射 / 时窗 / 资产摘要 /
    **完整消费关系**（工作包 3 第 5 条）.

    外部传入的清单**逐项核当前稿**（card_id / 正文 / 绑定 / profile），不允许手写清单
    替换内容。**自动生成与外部清单共用这一个函数**——此前只有自动生成那条路有完整判据，
    外部清单走的是"局部核"，于是**错章、错稿、错计划的清单能通过**，
    而 `segments=[]`（有卡片页面却没有叠加条目）也能通过，最终生成**零个 overlay**：
    成片里一张卡都没有，而清单校验说通过。

    身份四项缺一不可（scope / script_revision / source_plan_digest / profile），
    **每页必须有消费路径**（`pages` → `segments` → overlay）。
    """
    _problems: list = []
    if not isinstance(obj, dict):
        return [_p("card_plan_not_object", "卡片清单不是 JSON 对象")]
    if obj.get("schema") != SCHEMA_CARD_PLAN:
        return [_p("card_plan_schema_unknown",
                   "未知的卡片清单 schema：%r（期望 %s）"
                   % (obj.get("schema"), SCHEMA_CARD_PLAN), "schema")]
    _fps = int(obj.get("fps") or 0)
    if _fps != MERGE_FPS:
        _problems.append(_p("card_plan_fps_mismatch",
                            "清单 fps=%r 与合并帧率 %d 不符" % (obj.get("fps"), MERGE_FPS),
                            "fps"))
    # ---- **身份**：错章 / 错稿 / 错计划一律拒收（此前完全没核）----
    if timeline is not None:
        _scope = str(getattr(timeline, "scope", "") or "")
        _rev = str(getattr(timeline, "script_revision", "") or "")
        if _scope and str(obj.get("scope") or "") != _scope:
            _problems.append(_p("card_plan_scope_mismatch",
                                "清单的 scope=%r 与本次合成的 scope=%r 不符——**错章的"
                                "清单不能被消费**"
                                % (obj.get("scope"), _scope), "scope"))
        if _rev and str(obj.get("script_revision") or "") != _rev:
            _problems.append(_p("card_plan_revision_mismatch",
                                "清单的 script_revision=%r 与本次稿件的 %r 不符——"
                                "**错稿的清单不能被消费**（稿件换版后镜头窗口已变）"
                                % (obj.get("script_revision"), _rev), "script_revision"))
    # ---- **来源计划身份**：与当前稿自己的计划摘要核（此前只信清单自报）----
    if source_plan_digest is not None:
        _got_digest = str(obj.get("source_plan_digest") or "")
        if _got_digest != str(source_plan_digest):
            _problems.append(_p(
                "card_plan_source_digest_mismatch",
                "清单的来源计划摘要 %r 与当前稿的 %r 不符——**错计划的清单不能被消费**"
                % (_got_digest[:12], str(source_plan_digest)[:12]),
                "source_plan_digest"))
    _pm = obj.get("profile") or {}
    _want = _profile.profile_manifest(prof or _profile.active_profile())
    if str(_pm.get("profile_digest") or "") != str(_want.get("profile_digest") or ""):
        _problems.append(_p("card_plan_profile_mismatch",
                            "清单的显示 profile 与当前生效值不符——样式/字体/字号变了"
                            "就得重建卡片，不能沿用旧资产", "profile"))
    if relations is not None:
        _want_cards = {r["card_id"]: r for r in relations}
        _got = {str(c.get("card_id")): c for c in (obj.get("cards") or [])}
        if sorted(_want_cards) != sorted(_got):
            _problems.append(_p("card_plan_cards_mismatch",
                                "清单的卡片集合与当前稿不符（稿 %s；清单 %s）"
                                % (sorted(_want_cards), sorted(_got)), "cards"))
        for _cid in sorted(set(_want_cards) & set(_got)):
            _w, _g = _want_cards[_cid], _got[_cid]
            if _digest(str(_g.get("text") or "")) != _w["text_digest"]:
                _problems.append(_p("card_plan_text_mismatch",
                                    "卡片 %s 的正文与当前稿不符——清单不能替换内容" % _cid,
                                    "cards[%s].text" % _cid))
            if str(_g.get("beat_uid") or "") != _w["beat_uid"]:
                _problems.append(_p("card_plan_binding_mismatch",
                                    "卡片 %s 的节拍绑定与当前稿不符（稿 %s；清单 %s）"
                                    % (_cid, _w["beat_uid"], _g.get("beat_uid")),
                                    "cards[%s].beat_uid" % _cid))
    for _c in (obj.get("cards") or []):
        _cid = str(_c.get("card_id") or "")
        _text = str(_c.get("text") or "")
        _placement = str(_c.get("placement") or DEFAULT_PLACEMENT)
        _pages = list(_c.get("pages") or [])
        if not _pages:
            _problems.append(_p("card_plan_pages_missing",
                                "卡片 %s 没有任何分页记录" % _cid, "cards[%s].pages" % _cid))
            continue
        # **分页映射必须拼回正文**（身份与内容一致的唯一判据）
        if "".join(_text[int(x.get("text_start") or 0):int(x.get("text_end") or 0)]
                   for x in _pages) != _text:
            _problems.append(_p("card_plan_pages_not_reassemblable",
                                "卡片 %s 的分页按序拼回不等于正文——分页只许切分、"
                                "不许改动正文" % _cid, "cards[%s].pages" % _cid))

        # ---- **阅读时长按正文与生效 profile 重算**（工作包 2 卡片统一准入）----
        #
        # 复核 2026-10-05 P1：把**页和片段一起**缩到 1 帧仍然通过，原因是"每帧覆盖"
        # 只核了 segment 覆盖了**清单自报的页**——清单说页只有 1 帧，覆盖就成立。
        # 真正要问的是「这段正文按**当前 profile** 需要读多久」。那是**代码算得出来**的：
        # 字符数 → 阅读帧数。清单自称的时窗不作为依据。
        try:
            _need = _required_frames(_text, _placement, prof or _profile.active_profile())
        except Exception:                                              # noqa: BLE001
            _need = -1
        _window = _c.get("window") or {}
        _span = int(_window.get("end_frame") or 0) - int(_window.get("start_frame") or 0)
        if _need < 0:
            _problems.append(_p(
                "card_plan_layout_overflow",
                "卡片 %s 按当前生效 profile **排不下**（卡位 %s）——布局重算失败"
                % (_cid, _placement), "cards[%s]" % _cid))
        elif _span < _need:
            _problems.append(_p(
                "card_plan_reading_time_insufficient",
                "卡片 %s 分到的展示时长 %d 帧，按当前 profile 读完这段正文至少要 %d 帧"
                "（少了 %d 帧）——**页和片段一起缩短**骗不过这一道；拆页、转移信息或"
                "调整镜头，**不截正文、不偷偷延长视频**"
                % (_cid, _span, _need, _need - _span),
                "cards[%s].window" % _cid, card_id=_cid,
                required_frames=int(_need), available_frames=int(_span)))

        _w = _c.get("window") or {}
        _lo, _hi = int(_w.get("start_frame") or 0), int(_w.get("end_frame") or 0)
        if _hi <= _lo:
            _problems.append(_p("card_plan_window_invalid",
                                "卡片 %s 的时窗非法（%d..%d）" % (_cid, _lo, _hi),
                                "cards[%s].window" % _cid))
        _prev = None
        for _pg in sorted(_pages, key=lambda x: int(x.get("page_index") or 0)):
            _a, _b = int(_pg.get("start_frame") or 0), int(_pg.get("end_frame") or 0)
            if _a < _lo or _b > _hi or _b <= _a:
                _problems.append(_p("card_plan_page_out_of_window",
                                    "卡片 %s 第 %s 页的时窗（%d..%d）越出卡片窗口（%d..%d）"
                                    % (_cid, _pg.get("page_index"), _a, _b, _lo, _hi),
                                    "cards[%s].pages" % _cid))
            if _prev is not None and _a < _prev:
                _problems.append(_p("card_plan_pages_overlap",
                                    "卡片 %s 的分页时窗重叠（%d < %d）" % (_cid, _a, _prev),
                                    "cards[%s].pages" % _cid))
            _prev = _b
        for _pg in _pages:
            _ap = str(_pg.get("asset") or "")
            _sha = str(_pg.get("sha256") or "")
            if not _ap:
                _problems.append(_p("card_plan_asset_missing",
                                    "卡片 %s 第 %s 页没有资产路径" % (_cid, _pg.get("page_index")),
                                    "cards[%s].pages" % _cid))
                continue
            import os
            if not os.path.isfile(_ap):
                _problems.append(_p("card_plan_asset_not_found",
                                    "卡片资产不存在：%s" % _ap,
                                    "cards[%s].pages" % _cid))
            elif _sha and _file_sha256(_ap) != _sha:
                _problems.append(_p("card_plan_asset_digest_mismatch",
                                    "卡片资产 %s 的摘要与清单不符——旧资产不能被消费"
                                    % _ap, "cards[%s].pages" % _cid))
    # 段落之间不得重叠（全局守卫）
    _segs = sorted((obj.get("segments") or []), key=lambda s: int(s.get("start_frame") or 0))
    for _a, _b in zip(_segs, _segs[1:]):
        if int(_b.get("start_frame") or 0) < int(_a.get("end_frame") or 0):
            _problems.append(_p("card_plan_segments_overlap",
                                "卡片段在时间线上重叠（%s 与 %s）"
                                % (_a.get("start_frame"), _b.get("start_frame")),
                                "segments"))

    # ---- **完整消费关系**：每页都必须**整段**被叠加条目覆盖（工作包 3 第 5 条）----
    #
    # ⚠️ 「被 segment 点名」只是**必要条件**（复核 2026-10-05 P1）：把一页的 segment
    # 时窗缩到 1 帧、`members` 里仍然写着它，旧校验照样通过，后续 overlay 守卫也不拦
    # ——于是观众只看到一闪而过的一帧，而清单说"这一页被消费了"。所以这里核的是
    # **完整可见时间**：该页的每一帧都必须落在某个 segment 的区间内。
    _page_spans = {}
    for _c in (obj.get("cards") or []):
        for _pg in (list(_c.get("pages") or [])):
            _page_spans["%s#%s" % (str(_c.get("card_id")),
                                    str(_pg.get("page_index")))] = (
                int(_pg.get("start_frame") or 0), int(_pg.get("end_frame") or 0))
    _seg_spans: dict = {}
    for _s in _segs:
        _sa, _sb = int(_s.get("start_frame") or 0), int(_s.get("end_frame") or 0)
        for _m in (_s.get("members") or []):
            _seg_spans.setdefault(str(_m), []).append((_sa, _sb))
    _orphan = []
    for _key, (_pa, _pb) in _page_spans.items():
        _hits = sorted(_seg_spans.get(_key) or [])
        if not _hits:
            _orphan.append(_key)
            continue
        # 逐帧核覆盖：把该页切成若干采样点，每个都必须落在某个 segment 内
        _covered_frames = [False] * max(0, _pb - _pa)
        for _sa, _sb in _hits:
            for _f in range(max(_pa, _sa), min(_pb, _sb)):
                _i = _f - _pa
                if 0 <= _i < len(_covered_frames):
                    _covered_frames[_i] = True
        if _covered_frames and not all(_covered_frames):
            _shown = sum(1 for x in _covered_frames if x)
            _orphan.append("%s（只被消费 %d/%d 帧）"
                           % (_key, _shown, len(_covered_frames)))
    if _orphan:
        _problems.append(_p(
            "card_plan_pages_unconsumed",
            "这些卡片页没有被叠加条目**完整**覆盖：%s——清单说有卡、成片里却看不到"
            "（或只闪过一帧）" % _orphan[:8],
            "segments"))
    for _s in _segs:
        if int(_s.get("end_frame") or 0) <= int(_s.get("start_frame") or 0):
            _problems.append(_p("card_plan_segment_window_invalid",
                                "卡片段时窗非法（%s..%s）"
                                % (_s.get("start_frame"), _s.get("end_frame")),
                                "segments"))
        if not str(_s.get("asset") or ""):
            _problems.append(_p("card_plan_segment_asset_missing",
                                "卡片段（%s..%s）没有合成资产——分段合成产物缺失"
                                % (_s.get("start_frame"), _s.get("end_frame")), "segments"))
        elif not os.path.isfile(str(_s.get("asset"))):
            _problems.append(_p("card_plan_segment_asset_not_found",
                                "卡片段资产不存在：%s" % _s.get("asset"), "segments"))
        elif not str(_s.get("sha256") or ""):
            _problems.append(_p(
                "card_plan_segment_digest_missing",
                "卡片段资产 %s 没有摘要——**声明与实际一致**核不了，"
                "清单不许只给路径" % _s.get("asset"), "segments"))
        elif _file_sha256(str(_s.get("asset"))) != str(_s.get("sha256")):
            # 复核 2026-10-05 P1：改 segment 的 sha256 此前能通过——这里拿清单**自称**的
            # 摘要比资产**路径**，没有重算文件摘要，于是"盘上是另一张、清单说不是"照样过。
            _problems.append(_p(
                "card_plan_segment_digest_mismatch",
                "卡片段资产 %s 的实际摘要与清单不符——**实际合成资产与声明不一致**"
                % _s.get("asset"), "segments"))
    # 片段时窗必须落在它所属镜头的**可用窗口**里（不是落在别的镜头上）
    if timeline is not None:
        _wins = {int(w["clip_index"]): w for w in available_windows(timeline)}
        _card_of = {str(c.get("card_id")): c for c in (obj.get("cards") or [])}
        for _s in _segs:
            _members = [str(x) for x in (_s.get("members") or [])]
            if not _members:
                continue
            _cid = _members[0].split("#", 1)[0]
            _card = _card_of.get(_cid)
            if _card is None:
                continue
            _win = _wins.get(_cidx_of_clip(timeline, str(_card.get("stable_shot_id") or "")))
            if _win is None:
                continue
            if (int(_s.get("start_frame") or 0) < int(_win["start_frame"])
                    or int(_s.get("end_frame") or 0) > int(_win["end_frame"])):
                _problems.append(_p(
                    "card_plan_segment_out_of_window",
                    "卡片段（%s..%s）落在镜头 %s 的**可用窗口**之外（%d..%d）——"
                    "它会显示在淡入淡出或转场重叠区上"
                    % (_s.get("start_frame"), _s.get("end_frame"),
                       _card.get("stable_shot_id"), _win["start_frame"],
                       _win["end_frame"]),
                    "segments"))
    return _problems


def _cidx_of_clip(timeline, stable_shot_id) -> int | None:
    """稳定镜头身份 → 它在 `clips` 里的下标；找不到返回 `None`（不猜）."""
    for _i, _c in enumerate(getattr(timeline, "clips", ()) or ()):
        if str(getattr(_c, "stable_id", "")) == str(stable_shot_id):
            return _i
    return None


def overlay_entries(manifest, timeline=None) -> tuple:
    """清单 → `step04_merge.timeline.OverlayEntry` 元组（绝对秒只在**这里**转换一次）."""
    from pathlib import Path
    from step04_merge.timeline import OverlayEntry
    _fps = int(manifest.get("fps") or MERGE_FPS)
    _by_index = {}
    for _c in (manifest.get("cards") or []):
        _by_index.setdefault((str(_c.get("kind") or "shot"), int(_c.get("shot_index") or 0)),
                             str(_c.get("stable_shot_id") or ""))
    _out = []
    for _i, _s in enumerate(manifest.get("segments") or []):
        _a, _b = int(_s["start_frame"]), int(_s["end_frame"])
        _members = list(_s.get("members") or [])
        if not _members:
            continue
        if _b <= _a:
            # 时窗非法的片段**不产叠加条目**（准入已报过；这里不把坏条目交给下游）
            continue
        _owner = _members[0].split("#", 1)[0]
        _card = next((c for c in (manifest.get("cards") or [])
                      if str(c.get("card_id")) == _owner), None)
        if _card is None:
            continue
        _out.append(OverlayEntry(
            overlay_id="card_seg_%04d" % _i,
            stable_shot_id=str(_card.get("stable_shot_id") or ""),
            shot_index=int(_card.get("shot_index") or 0),
            kind="image", path=Path(str(_s.get("asset") or "")),
            start_sec=_a / float(_fps), end_sec=_b / float(_fps),
            # ⚠️ `layer` 取**片段序号** `_i`，不是不存在的 `i`（复核 2026-10-05 P1：
            # 真实绘制与清单生成都成功，**消费者这一步抛 `NameError`**）。
            # 层序必须与 `validate_overlays` 的排序键一致（`layer` 递增即越靠上）。
            layer=_i, spec_id="screen_card", fps=_fps,
            sha256=str(_s.get("sha256") or "")))
    return tuple(_out), []


__all__ = ["SCHEMA_CARD_PLAN", "DEFAULT_PLACEMENT", "card_relations",
           "available_windows", "build_card_plan", "segment_overlays",
           "validate_card_plan", "overlay_entries"]
