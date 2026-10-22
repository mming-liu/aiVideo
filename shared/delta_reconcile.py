"""`plan_revision.delta.v1` 的确定性收尾 —— **R0 + R1'（2026-10-21 收紧）**.

启用两条，都要求**唯一解**，不满足就保持原值、交统一验收拒收，并记 `skipped` 原因：

**R0：修引用身份拼写**（漏写 `new:` 前缀）。只改引用里的 UID，不改正文、来源、
说话人、实体数量、顺序或事实主张。六条件：
1. 本轮声明：`new:X` 出现在**本轮 delta 对应集合**的 `upsert` 里；
2. 旧身份冲突：基线与合并后集合都**不存在** `X`；
3. 类型：引用的声音类型与声明集合一致（narration ↔ narration_units 等）；
4. 唯一性：只有一个符合条件的 `new:X`；
5. 形状：引用本身是 `{kind, uid}` 形状的字典（不借机清洗其他非法字段）；
6. 范围：只替换 UID。

**R1'：基线权威的逐字重复归并**。模型常把一句已有声音"重新写一遍"成新单元
（正文逐字相同、换了 uid），再把节拍/载体改指新单元——旧单元因此无人播放，
整份响应被 `view_voice_unit_unplaced` 原子拒收。R1' 把这类**新增的重复单元**
并回基线身份：改引用、删重复，播放文本与类型都以基线为准。

七条件（任一不满足 ⇒ 该单元不动）：
1. 本轮声明：`X` 出现在本轮 `upsert` 的单元里；
2. 确为新增：`X` **不在基线**（基线已有的 uid 是正常 upsert，不是重复）；
3. 正文逐字相等（只归一空白，不做相似度）；
4. 基线唯一解：基线里正文相同的单元**恰好一个** `Y`（跨两个集合一起数）；
5. 来源不冲突：两侧都有 `source.text` 时必须逐字相等（缺一侧则以上条为准）；
6. 引用可全改指：节拍 `voice_ref`、事实载体、钩子载体、钩子 `spoken_uids`
   全部能改指 `Y`（`spoken_uids` 只接受对白目标），且改后**无残余引用**；
7. 无内容损失：只删 `X` 这个重复项，`Y` 原样保留并被引用。

与已停用的 **R1/R1b/R2** 的区别：R1 依"正文可由若干单元拼接"删孤儿（会丢内容、
无说话人证据）；R1b/R2 在**本轮新单元之间**换类型/迁载体。R1' 只做"同一段正文
在基线里已有唯一身份"的归并，因此跨集合的重复也以**基线已定的类型**为准，
不靠相似度、不新增声音、不改变任何播放文本。
`diagnose_splits()` 仍只产出诊断，不改候选。
"""
from __future__ import annotations

import re

from . import presentation_contract as pc

TEMP = pc.DELTA_TEMP_UID_PREFIX
_COLL = {pc.KIND_NARRATION: "narration_units", pc.KIND_DIALOGUE: "dialogue_units"}


def _uid(ref) -> str:
    if isinstance(ref, dict):
        return str(ref.get("uid") or ref.get("beat_uid") or ref.get("card_id") or "")
    return ""


def _units(view) -> dict:
    out = {}
    for kind, key in ((pc.KIND_NARRATION, "narration_units"),
                      (pc.KIND_DIALOGUE, "dialogue_units")):
        for u in (view.get(key) or []):
            if isinstance(u, dict):
                out[_uid(u)] = kind
    return out


def _declared(delta, kind) -> set:
    ups = (delta or {}).get("upsert") or {}
    if not isinstance(ups, dict):
        return set()
    return {str(u.get("uid")) for u in (ups.get(_COLL[kind]) or []) if isinstance(u, dict)}


def _deleted_uids(delta) -> set:
    """本轮 `delete` 明确删除的实体 uid（各集合合并计数）。

    R1' 的目标**必须排除**这些：基线里有、但本轮要删的单元在应用后不存在，
    把引用改指过去就把"重复"换成了"悬空"（2026-10-21 实跑抓到：改指到 `d01`，
    而同一份 delta 的 `delete.dialogue_units` 里就有 `d01`——整份响应因此被拒，
    却看起来像"模型删了还在引用的单元"）。
    """
    out = set()
    dele = (delta or {}).get("delete") if isinstance(delta, dict) else None
    if not isinstance(dele, dict):
        return out
    for v in dele.values():
        if isinstance(v, list):
            out.update(str(x) for x in v if isinstance(x, str) and x)
    return out


def _norm_text(text) -> str:
    """正文比较口径：**只归一空白**（换行/空格/制表），不做相似度、不做标点等价。"""
    return re.sub(r"\s+", "", str(text or ""))


def _source_text(unit) -> str:
    src = unit.get("source") if isinstance(unit, dict) else None
    return str(src.get("text") or "") if isinstance(src, dict) else ""


def _unit_index(view) -> dict:
    """`uid → {kind, text, source_text}`（两个集合一起收；uid 冲突时后者不覆盖前者）。"""
    out = {}
    for kind, key in ((pc.KIND_NARRATION, "narration_units"),
                      (pc.KIND_DIALOGUE, "dialogue_units")):
        for u in (view.get(key) or []) if isinstance(view, dict) else []:
            if not isinstance(u, dict):
                continue
            uid = u.get("uid")
            if isinstance(uid, str) and uid and uid not in out:
                out[uid] = {"kind": kind, "text": u.get("text"),
                            "source_text": _source_text(u)}
    return out


def _declared_units(delta) -> dict:
    """本轮 `upsert` 声明的单元（`uid → {kind, text, source_text}`）。"""
    out = {}
    ups = (delta or {}).get("upsert") if isinstance(delta, dict) else None
    if not isinstance(ups, dict):
        return out
    for kind, key in ((pc.KIND_NARRATION, "narration_units"),
                      (pc.KIND_DIALOGUE, "dialogue_units")):
        for u in (ups.get(key) or []):
            if not isinstance(u, dict):
                continue
            uid = u.get("uid")
            if isinstance(uid, str) and uid and uid not in out:
                out[uid] = {"kind": kind, "text": u.get("text"),
                            "source_text": _source_text(u)}
    return out


def _redirect_ops(view, x: str, y: str, ykind: str, xkind: str = "") -> tuple:
    """列出把 `x` 的**全部**引用改指 `y` 的操作；有改不了的引用 ⇒ `(None, 原因)`。

    引用自带的类型必须等于**被替换单元 `x` 的类型**（`xkind`）；改指后统一写成
    基线目标 `y` 的类型（`ykind`）。跨集合时类型会随之改写——那不是"猜类型"，
    而是把本轮新增的重复单元并回基线已定的身份，测试与报告里单独记 `R1p_type_restored`。

    只处理契约里的四个引用位置；出现改不了的引用（形状非法、引用类型与 `x` 不符、
    钩子 `spoken_uids` 指向旁白、视图 `order` 仍提到 `x`）就整条放弃——不在
    "还剩一处指向 x"的情况下删单元，那只会把孤儿换成悬空。
    """
    ops, why = [], ""
    _xkind = xkind or ykind

    def _mismatch(found_kind, where):
        nonlocal why
        why = "%s 指向 %s，类型=%r 与 %s 的类型 %r 不一致 ⇒ 不能改指" % (
            where, x, found_kind, x, _xkind)

    for i, b in enumerate(view.get("beats") or []):
        if not isinstance(b, dict):
            continue
        ref = b.get("voice_ref")
        if _uid(ref) != x:
            continue
        if not _sound_ref(ref):
            why = "beats[%s].voice_ref 指向 %s 但形状非法 ⇒ 不能改指" % (b.get("uid"), x)
            continue
        if str(ref.get("kind") or "") != _xkind:
            _mismatch(ref.get("kind"), "beats[%s].voice_ref" % b.get("uid"))
            continue
        ops.append((b, "voice_ref", None, {"kind": ykind, "uid": y}))

    def _carriers(holder, where):
        nonlocal why
        carriers = holder.get("carriers")
        if not isinstance(carriers, list):
            return
        for i, c in enumerate(carriers):
            if _uid(c) != x:
                continue
            if not _sound_ref(c):
                why = "%s.carriers[%d] 指向 %s 但形状非法 ⇒ 不能改指" % (where, i, x)
                continue
            if str(c.get("kind") or "") != _xkind:
                _mismatch(c.get("kind"), "%s.carriers[%d]" % (where, i))
                continue
            ops.append((carriers, i, None, {"kind": ykind, "uid": y}))

    for i, fb in enumerate(view.get("fact_bindings") or []):
        if isinstance(fb, dict):
            _carriers(fb, "fact_bindings[%s]" % fb.get("fact_uid"))
    hk = view.get("hook_bindings")
    if isinstance(hk, dict):
        _carriers(hk, "hook_bindings")
        spoken = hk.get("spoken_uids")
        if isinstance(spoken, list):
            for i, uid in enumerate(spoken):
                if uid != x:
                    continue
                if ykind != pc.KIND_DIALOGUE:
                    why = "hook_bindings.spoken_uids 指向对白，基线目标 %s 是旁白" % y
                    continue
                ops.append((spoken, i, None, y))

    # 视图里若带 `order`（或别的容器）仍提到 x，就没有全改指 ⇒ 放弃
    order = view.get("order")
    if isinstance(order, dict):
        for key, seq in order.items():
            if isinstance(seq, list) and x in seq:
                why = "视图 order[%s] 仍引用 %s ⇒ 不能删" % (key, x)
    elif isinstance(order, list) and x in order:
        why = "视图 order 仍引用 %s ⇒ 不能删" % x
    return (None, why) if why else (ops, "")


def _ref_uids(view) -> set:
    """视图里**所有**被引用的声音 uid（节拍/事实载体/钩子载体/钩子台词）。"""
    out = set()
    for b in (view.get("beats") or []):
        if isinstance(b, dict):
            u = _uid(b.get("voice_ref"))
            if u:
                out.add(u)
    for fb in (view.get("fact_bindings") or []):
        if isinstance(fb, dict):
            for c in (fb.get("carriers") or []):
                u = _uid(c)
                if u:
                    out.add(u)
    hk = view.get("hook_bindings")
    if isinstance(hk, dict):
        for c in (hk.get("carriers") or []):
            u = _uid(c)
            if u:
                out.add(u)
        for u in (hk.get("spoken_uids") or []):
            if isinstance(u, str) and u:
                out.add(u)
    return out


def _order_mentions(view, uid: str) -> bool:
    order = view.get("order")
    if isinstance(order, dict):
        return any(isinstance(seq, list) and uid in seq for seq in order.values())
    return isinstance(order, list) and uid in order


def _drop_covered_orphans(view: dict, *, declared=(), base_view=None, deleted=()) -> tuple:
    """R1''：删掉**零引用**、且正文被本轮某个已承载单元逐字包含的**同类型**单元。

    与 R1'a 的区别：这里不"改指"，所以**不改变任何播放文本**。被删单元没人播、
    也没有事实/钩子引用；它的正文完整出现在一个正在播的新单元里，同类型 ⇒ 声音
    身份不变、内容不丢。跨类型（例如对白被写成旁白）一律不删——那会换掉说话人。
    """
    notes: list = []
    base_units = _unit_index(base_view or {})
    if not base_units:
        return view, notes
    declared_units = {k: v for k, v in _declared_units({"upsert": declared}).items()
                      if k not in set(deleted or ())}
    if not declared_units:
        return view, notes
    refs = _ref_uids(view)
    for kind, key in ((pc.KIND_NARRATION, "narration_units"),
                      (pc.KIND_DIALOGUE, "dialogue_units")):
        keep = []
        for u in (view.get(key) or []):
            if not isinstance(u, dict):
                keep.append(u)
                continue
            uid = u.get("uid")
            info = base_units.get(uid) if isinstance(uid, str) else None
            text = _norm_text(u.get("text"))
            if (info is None or info["kind"] != kind or uid in refs
                    or uid in declared_units or len(text) < 6
                    or _order_mentions(view, uid)):
                keep.append(u)
                continue
            covers = [x for x, xi in declared_units.items()
                      if xi["kind"] == kind and x in refs
                      and text in _norm_text(xi["text"])]
            if len(covers) != 1:
                notes.append({"rule": "R1pp_skipped", "uid": uid,
                              "why": "本轮同类型已承载单元中包含该正文的 %d 个（需恰好 1 个）"
                                     % len(covers)})
                keep.append(u)
                continue
            notes.append({"rule": "R1pp_covered_orphan", "uid": uid, "by": covers[0],
                          "detail": "%s 零引用且正文被已承载的 %s 完整包含 ⇒ 删除孤儿"
                                    % (uid, covers[0])})
        view[key] = keep
    return view, notes


def _dedupe_exact(view: dict, *, base_view, delta) -> tuple:
    """R1'：把本轮新增的**逐字重复**单元并回基线身份（改引用 + 删重复）。"""
    notes: list = []
    if not isinstance(base_view, dict) or not isinstance(delta, dict):
        return view, notes
    base_units = _unit_index(base_view)
    if not base_units:
        return view, notes
    # **目标必须是应用后仍然存在的基线单元**：本轮 `delete` 里的 uid 不能当目标，
    # 否则改指 = 制造悬空（实跑抓到过）。
    for _d in _deleted_uids(delta):
        base_units.pop(_d, None)
    if not base_units:
        return view, notes
    declared = _declared_units(delta)
    present = _unit_index(view)
    by_text: dict = {}
    for y, info in base_units.items():
        n = _norm_text(info["text"])
        if n:
            by_text.setdefault(n, []).append((y, info))

    for x, info in declared.items():
        if x in base_units:                          # 条件 2：不是本轮新增
            continue
        if x not in present:                         # 已归并过（幂等：不再重复记事）
            continue
        n = _norm_text(info["text"])
        if not n:                                    # 条件 3：空正文不参与
            continue
        hits = by_text.get(n) or []
        if len(hits) != 1:                           # 条件 4：唯一解
            notes.append({"rule": "R1p_skipped", "uid": x,
                          "why": "基线里正文相同的单元 %d 个（需恰好 1 个）" % len(hits)})
            continue
        y, yinfo = hits[0]
        if (info["source_text"] and yinfo["source_text"]
                and _norm_text(info["source_text"]) != _norm_text(yinfo["source_text"])):
            notes.append({"rule": "R1p_skipped", "uid": x,
                          "why": "来源文本与基线 %s 不一致" % y})   # 条件 5
            continue
        ops, why = _redirect_ops(view, x, y, yinfo["kind"], info["kind"])
        if ops is None:                              # 条件 6：引用必须能全改指
            notes.append({"rule": "R1p_skipped", "uid": x, "why": why})
            continue
        for holder, key, idx, value in ops:
            if idx is None:
                holder[key] = value
            else:
                holder[idx] = value
        key = ("narration_units" if info["kind"] == pc.KIND_NARRATION
               else "dialogue_units")
        view[key] = [u for u in (view.get(key) or [])
                     if not (isinstance(u, dict) and u.get("uid") == x)]
        notes.append({"rule": ("R1p_exact_duplicate" if info["kind"] == yinfo["kind"]
                               else "R1p_type_restored"),
                      "uid": x, "to": y, "kind": yinfo["kind"], "from_kind": info["kind"],
                      "refs": len(ops), "detail": "%s 逐字重复基线 %s ⇒ 改指并删除"
                                                  % (x, y)})
    return view, notes


def _sound_ref(ref) -> bool:
    """声音引用**只接受** `{kind, uid}`：kind 合法、uid 为非空字符串、无多余字段。"""
    if not isinstance(ref, dict) or set(ref) != {"kind", "uid"}:
        return False
    kind = ref.get("kind")
    uid = ref.get("uid")
    return kind in _COLL and isinstance(uid, str) and bool(uid.strip())


def _plan_fix(uid, kind_hint, *, existing, base_ids, declared_n, declared_d, notes, where):
    """返回修正后的 uid 或原值；所有拒绝理由写进 notes（不改判据）。"""
    if not uid:
        return uid
    if uid in existing or uid in base_ids:
        return uid                                   # 条件 2：不是临时身份
    cands = []
    if uid in declared_n:
        cands.append(pc.KIND_NARRATION)
    if uid in declared_d:
        cands.append(pc.KIND_DIALOGUE)
    if len(cands) != 1:                              # 条件 1/4
        notes.append({"rule": "R0_skipped", "uid": uid, "where": where,
                      "why": "本轮未声明或无唯一候选（候选数=%d）" % len(cands)})
        return uid
    kind = cands[0]
    if kind_hint and kind_hint != kind:              # 条件 3
        notes.append({"rule": "R0_skipped", "uid": uid, "where": where,
                      "why": "类型不一致：引用=%s 声明=%s" % (kind_hint, kind)})
        return uid
    notes.append({"rule": "R0_temp_prefix", "uid": uid, "where": where,
                  "detail": "%s → %s%s" % (where, TEMP, uid)})
    return TEMP + uid


def reconcile(view: dict, *, base_view: dict | None = None, delta: dict | None = None) -> tuple:
    import copy

    v = copy.deepcopy(view)
    notes: list = []
    existing = set(_units(v))
    base_ids = set(_units(base_view or {}))
    declared_n = {u[len(TEMP):] for u in _declared(delta, pc.KIND_NARRATION) if u.startswith(TEMP)}
    declared_d = {u[len(TEMP):] for u in _declared(delta, pc.KIND_DIALOGUE) if u.startswith(TEMP)}

    # ---- 声音引用（节拍 voice_ref）----
    for b in v.get("beats") or []:
        ref = b.get("voice_ref")
        if not _sound_ref(ref):                       # 非法形状：原样保留
            continue
        kind = str(ref.get("kind") or "")
        new_uid = _plan_fix(_uid(ref), kind, existing=existing, base_ids=base_ids,
                            declared_n=declared_n, declared_d=declared_d, notes=notes,
                            where="beats[%s].voice_ref" % _uid(b))
        if new_uid != _uid(ref):
            b["voice_ref"] = dict(ref, uid=new_uid)

    # ---- 事实载体（列表形状）----
    for fb in v.get("fact_bindings") or []:
        if not isinstance(fb, dict):
            continue
        out = []
        for c in (fb.get("carriers") or []):
            if not _sound_ref(c):                     # 含 beat_uid/card_id 混入：原样保留
                out.append(c)
                continue
            old = _uid(c)
            new_uid = _plan_fix(old, str(c.get("kind") or ""), existing=existing,
                                base_ids=base_ids, declared_n=declared_n, declared_d=declared_d,
                                notes=notes,
                                where="fact_bindings[%s]" % str(fb.get("fact_uid")))
            out.append(dict(c, uid=new_uid) if new_uid != old else c)
        fb["carriers"] = out

    # ---- 钩子（**字典形状**：spoken_uids 是**对白**引用，carriers 走声音引用形状）----
    hk = v.get("hook_bindings")
    if isinstance(hk, dict):
        spoken = hk.get("spoken_uids")
        if isinstance(spoken, list):
            fixed_spoken = []
            for uid in spoken:
                if not isinstance(uid, str) or not uid.strip():   # 不做字符串强转
                    fixed_spoken.append(uid)
                    continue
                nu = _plan_fix(uid, pc.KIND_DIALOGUE, existing=existing, base_ids=base_ids,
                               declared_n=declared_n, declared_d=declared_d, notes=notes,
                               where="hook_bindings.spoken_uids")
                fixed_spoken.append(nu)
            hk["spoken_uids"] = fixed_spoken
        carriers = hk.get("carriers")
        if isinstance(carriers, list):
            out = []
            for c in carriers:
                if not _sound_ref(c):
                    out.append(c)
                    continue
                old = _uid(c)
                nu = _plan_fix(old, str(c.get("kind") or ""), existing=existing,
                               base_ids=base_ids, declared_n=declared_n, declared_d=declared_d,
                               notes=notes, where="hook_bindings.carriers")
                out.append(dict(c, uid=nu) if nu != old else c)
            hk["carriers"] = out
    elif isinstance(hk, list):
        notes.append({"rule": "R0_skipped", "uid": "", "where": "hook_bindings",
                      "why": "hook_bindings 是列表形状（非契约形状）⇒ 不处理"})

    # ---- R1'：基线权威的重复归并（R1'a 逐字改指 / R1'' 删被覆盖的零引用孤儿）----
    # 必须放在 R0 之后：R0 可能刚把引用补成 `new:X`，这时才看得见"新增单元重复基线"。
    v, _dup_notes = _dedupe_exact(v, base_view=base_view, delta=delta)
    notes.extend(_dup_notes)
    v, _orphan_notes = _drop_covered_orphans(
        v, declared=(delta or {}).get("upsert") or {}, base_view=base_view,
        deleted=_deleted_uids(delta))
    notes.extend(_orphan_notes)

    return v, notes


def diagnose_splits(view: dict) -> list:
    """**只诊断，不修改**：列出"无人承载、但正文等于若干已承载单元按播放顺序拼接"的单元。

    这些是历史 R1 会删除的对象；现在它们一律保持原样并继续拒收，
    由生产者提供可信的替代关系（同类型/说话人/来源）后再评估。
    """
    units = {}
    for key in ("narration_units", "dialogue_units"):
        for u in (view.get(key) or []):
            if isinstance(u, dict):
                units[_uid(u)] = (key, str(u.get("text") or ""))
    played = [(_uid(b.get("voice_ref") or {})) for b in (view.get("beats") or [])]
    placed = set(played) & set(units)
    out = []
    for uid, (key, text) in units.items():
        if uid in placed or not text.strip():
            continue
        target, hits = text.strip(), []

        def walk(i, acc, used):
            if len(hits) > 1:
                return
            if acc == target and len(used) >= 2:
                hits.append(list(used))
                return
            if len(acc) >= len(target) or i >= len(played):
                return
            for j in range(i, len(played)):
                u2 = played[j]
                if u2 in used or u2 not in placed:
                    continue
                walk(j + 1, acc + str(units[u2][1]).strip(), used + [u2])

        walk(0, "", [])
        if hits:
            out.append({"uid": uid, "collection": key, "candidates": hits,
                        "note": "仅诊断：缺可信替代关系 ⇒ 保持拒收、不删除"})
    return out
