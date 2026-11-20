# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.repair_accept —— 问题身份、已解决判定、修复采纳判断.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations


from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _blocking_record_key(rec: dict) -> tuple:
    """Identity used to compare before/after repair outcomes.

    The audit contract is intentionally locator-based: code + shot + frame +
    entity + field.  Message wording is not identity and must never make an
    unchanged issue look like a newly introduced blocker.
    """
    return (
        str(rec.get("code") or ""),
        str(rec.get("shot_id") if rec.get("shot_id") is not None else ""),
        str(rec.get("frame_id") or ""),
        str(rec.get("entity_id") or ""),
        str(rec.get("field_path") or ""),
    )


def _reindexed_kf_shots(script_before: dict | None,
                        script_after: dict | None) -> set[str]:
    """本轮 KF 列表长度发生变化的镜头集合（v6.104.1）.

    为什么需要：kf 类 issue 的定位符里带**位置序号**（`frame_id="kf1"`）。修复轮
    给某镜**新增一帧**之后，该镜后续帧的序号整体后移——同一个缺陷换了 key，
    于是 `after_keys - before_keys` 把它记成「新阻塞」，`before_keys - after_keys`
    又把旧 key 记成「已解决」。一减一增都是假的，而这条路径**只有补帧才会走到**，
    正是 v6.104 新放开的修复方式。

    实测（第二十五次重跑）：shot 9 帧数 2→4，`resolved=4` 的一轮被 `new=2`
    整轮回退。所以「帧数变了的镜头」在身份比较里必须**丢掉位置序号**。
    """
    def _counts(script):
        out = {}
        if not isinstance(script, dict):
            return out
        for s in list(script.get("shots") or []) + ([script["hook"]]
                                                    if isinstance(script.get("hook"), dict)
                                                    else []):
            if isinstance(s, dict):
                out[str(s.get("index"))] = len(s.get("keyframes") or [])
        return out

    before, after = _counts(script_before), _counts(script_after)
    return {sid for sid in set(before) | set(after)
            if before.get(sid, -1) != after.get(sid, -1)}


def _blocking_record_keys(state: dict | None,
                          reindexed: set[str] | None = None) -> set[tuple]:
    keys: set[tuple] = set()
    for rec in (state or {}).get("issues") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("severity") or "") not in ("hard", "visual_risk"):
            continue
        key = list(_blocking_record_key(rec))
        # 该镜本轮帧数变了 → 位置序号不再是稳定定位符，降级为「该镜的该类问题」
        if (reindexed and str(rec.get("frame_id") or "").startswith("kf")
                and str(rec.get("shot_id")) in reindexed):
            key[2] = "*"
        keys.add(tuple(key))
    return keys


#: audit 问题代码 → **跨轮稳定 family**（v6.139，修复 2）。同一语义问题跨轮换名
#: 是实测常态（FUTURE_STATE → FUTURE_STATE_IN_FF → result_shown_in_ff →
#: FUTURE_STATE_IN_PRE_FRAME，2026-09-26 20:49 run 四轮四名）——按 (shot, family)
#: 归并才能比对「同一问题」，这是触达兜底单次记账与跨轮身份比对的事实源。
#: 匹配子串、大小写不敏感、具体在前；都不命中 → family = 代码本身（保守不归并）。
_AUDIT_FAMILY_PATTERNS: tuple = (
    # 2026-11-15（run 20261115T140440 实测）：`MISSING_CAUSAL_EVENT` →
    # `ACTION_CAUSALITY_GAP` 同镜换名被 G1 判「未再报出」。causal 族必须在
    # `missing` 之前——前者同时含两个关键字，族归并取首个命中。
    ("causality_gap", ("causal",)),
    ("future_state_in_frame", ("future_state", "result_shown", "_in_ff",
                               "_in_pre", "premature", "state_in_")),
    ("state_contradiction", ("contradict", "inconsistency", "mismatch",
                             "conflict", "garbled", "state_chain")),
    ("missing_beat", ("missing",)),
    ("prop_state", ("prop",)),
    ("text_truncation", ("truncat", "text_error", "garbled_text")),
    ("layout_gap", ("layout", "person_missing", "frame_duty",
                    "frame_role", "duty")),
    ("temporal_window", ("time_window", "temporal", "kf_")),
)


def _issue_family(code) -> str:
    """问题代码 → **跨轮稳定 family**（审计条目与确定性检查共用同一份归并表）.

    同一语义问题跨轮换名是实测常态（`FUTURE_STATE` → `FUTURE_STATE_IN_FF` →
    `result_shown_in_ff`，2026-09-26 20:49 run 四轮四名）。G1 的独立关闭依据要
    在「审计条目」与「确定性检查记录」之间比对同一问题，必须共用这一份归并。
    """
    text = str(code or "")
    lower = text.lower()
    family = text[len("model_audit_"):] if lower.startswith("model_audit_") else text
    for fam, keys in _AUDIT_FAMILY_PATTERNS:
        if any(k in lower for k in keys):
            return fam
    return family


def _audit_issue_identity(issue) -> tuple:
    """audit 问题的**跨轮稳定身份** `(shot, family)`.

    family 按代码子串归并（见 `_AUDIT_FAMILY_PATTERNS`）；`shot` 经
    `_issue_shot_index` 归一（`shot5`/`5` 同身份）。措辞漂移不影响身份；
    同镜同族的两个不同问题会被视为同一身份——对「兜底记账只记一次」这是
    保守方向（少记功绩），可接受。
    """
    return (str(_issue_shot_index(issue)),
            _issue_family(getattr(issue, "code", "")))


def _record_family_identity(rec: dict) -> tuple:
    """确定性质量门记录 → `(镜头, family)`；与 `_audit_issue_identity` 同口径.

    G1 用它回答「上一轮报出的审计问题，有没有一条**同一问题**的确定性检查
    曾经报出、本轮已经干净」——这是独立于「模型动过手」的关闭依据。
    """
    from step01_script_gen.repair_fields import _shot_index_of
    return (str(_shot_index_of((rec or {}).get("shot_id"))),
            _issue_family((rec or {}).get("code")))


def _layout_entity_names(script: dict, issue) -> set:
    """该 issue 点名的镜头/帧里，`frame_layouts` 出现过的实体名集合."""
    shot = _issue_target_shot(script, issue)
    if not isinstance(shot, dict):
        return set()
    fl = shot.get("frame_layouts")
    if not isinstance(fl, dict):
        return set()
    fid = str(getattr(issue, "frame_id", "") or "")
    frames: list = []
    if fid.startswith("kf"):
        try:
            idx = int(fid[2:])
        except ValueError:
            idx = -1
        kfs = fl.get("kfs")
        if isinstance(kfs, list):
            frames = [kfs[idx]] if 0 <= idx < len(kfs) else list(kfs)
    elif fid in ("ff", "lf"):
        frames = [fl.get(fid)]
    else:
        frames = [fl.get("ff"), fl.get("lf")] + list(fl.get("kfs") or [])
    names: set = set()
    for frame in frames:
        for entry in frame or []:
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if name:
                    names.add(name)
    return names


def _audit_issue_structurally_removed(before_script, after_script, issue) -> bool:
    """问题点名的实体**修复前在目标布局里、修复后消失**（确定性内容事实）.

    这是独立于「模型动过手」的关闭依据：`entity_id` 是问题自己声明的实体，
    `frame_layouts` 是结构化事实源，出现→消失的转变由代码核对。修复前就不存在
    的实体不适用（那可能是「缺失」类问题，不能反过来当成已解决）。
    """
    name = str(getattr(issue, "entity_id", "") or "").strip()
    if not name or not isinstance(before_script, dict):
        return False
    return (name in _layout_entity_names(before_script, issue)
            and name not in _layout_entity_names(after_script, issue))


def _g1_unclosed_issues(prev_issues, now_ids, script, *,
                        prev_det_families, now_det_families,
                        det_checked: bool, prev_script=None) -> list:
    """G1：上一轮 blocking 本轮沉默、且没有确定性关闭依据的条目（纯函数）.

    关闭依据只有三类，都必须独立于「模型动过手」：
      (a) 判据证据已不在当前稿件中（`_audit_issue_resolved`）；
      (b) **同一 (镜头, family) 的确定性检查**上一轮报出、本轮干净，且两侧都
          真的跑完（`det_checked`）；
      (c) 问题点名的实体在修复前的目标布局里存在、修复后消失
          （`_audit_issue_structurally_removed`）——结构化内容事实。

    ⚠️ 这里**刻意不接受**「已尝试 / 字段触达」集合：那只证明模型动过手、且
    那一轮验收（本身可被触达兜底满足）通过，不证明问题已修复。2026-11-14
    复核给出的反例正是「改字段 → 修复轮接受 → 下轮审计沉默」被旧豁免放行。
    """
    now = set(now_ids or ())
    prev_det = set(prev_det_families or ())
    now_det = set(now_det_families or ())
    out: list = []
    for issue in prev_issues or ():
        if not _mrt.is_blocking(issue):
            continue
        ident = _audit_issue_identity(issue)
        if ident in now:
            continue
        if _audit_issue_resolved(script, issue):
            continue
        if det_checked and ident in prev_det and ident not in now_det:
            continue
        if _audit_issue_structurally_removed(prev_script, script, issue):
            continue
        out.append(issue)
    return out


def _audit_evidence_text(issue) -> str | None:
    """既有消失判据的最短证据要求；不足长度表示不可判，不表示未修复。"""
    evidence = str(getattr(issue, "evidence", "") or "").strip()
    return evidence if len(evidence) >= 8 else None


def _audit_paired_fields(script: dict | None, issue) -> dict:
    """已声明目标的实际授权投影；缺席维持旧路径，空/非法声明不回退。"""
    from step01_script_gen.repair_fields import allowed_repair_fields, _target_id_of
    if not isinstance(script, dict):
        return {}
    shots = {_target_id_of(s.get("index")): s for s in script.get("shots") or []
             if isinstance(s, dict)}
    if isinstance(script.get("hook"), dict):
        shots["hook"] = script["hook"]
    return {tid: allowed_repair_fields([], [issue], target_shot_id=tid, shots_by_id=shots)
            for tid in shots}


def _declared_repair_paths(issues, tid: str) -> set:
    """本轮**声明**给该目标的字段路径（`repair_targets` 里属于本镜的那些）.

    M02（2026-11-12）：与 `allowed_repair_fields` 的最终授权面**分开记账**——
    两者的差集就是「本轮由 code 表 / 关联推导 / 同帧实体互授扩出来的可改字段」。
    台账要能回答「为什么这一轮允许改这些字段」，而不是只能从提示词正文反推。
    声明缺席（`repair_targets is None`）时返回空集：它表示「没有声明」，
    与「显式声明为空」的区别由 `allowed_repair_fields` 的三态语义负责。
    """
    from step01_script_gen.repair_fields import _norm_keep_index, _target_id_of
    out: set = set()
    for issue in issues or []:
        for pair in (getattr(issue, "repair_targets", None) or []):
            if not isinstance(pair, dict):
                continue
            if _target_id_of(str(pair.get("shot_id") or "")) != tid:
                continue
            path = _norm_keep_index(str(pair.get("field_path") or "").strip())
            if path:
                out.add(path)
    return out


def _flatten_exact_paths(obj, prefix: str = "") -> dict:
    """**保留下标**的扁平化（`frame_layouts.ff.0.props`），用于逐路径比较.

    `repair_fields.flatten_paths` 把列表下标归一成 `*`（适合做「同类叶子」统计），
    但授权面记的是**精确下标**（`frame_layouts.ff.0.props`），两者匹配不上。复核
    要求「布局列表也纳入投影后差异检查」，所以这里另给一份保留下标的实现。
    """
    out: dict = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = "%s.%s" % (prefix, key) if prefix else str(key)
            out.update(_flatten_exact_paths(value, key_path))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out.update(_flatten_exact_paths(value, "%s.%d" % (prefix, i)))
    else:
        out[prefix] = obj
    return out


def _authorized_changes_lost(merged: "dict | None", after: dict,
                             allowed_by_target: dict | None) -> dict:
    """合并后**已授权**的改动被后处理改回/改写的清单（M02）.

    `merged` 是「合并后、后处理前」的快照（只含 LLM 的写入），`after` 是本轮结束时的
    稿件。逐目标、逐授权路径比较两者的取值：凡授权路径上的值被后处理改动，都记进来。

    ⚠️ 这里**只记账、不改变接受判据**。接受与否仍由
    `_repair_acceptance_decision` 的既有闸门决定；把「后处理抵消」直接升级成拒绝，
    等于在没有判别证据的情况下改准入语义（须走 M09 的正式门禁裁决）。
    已知适用面：`prompt` 这类会被后处理 writer 重写的聚合字段，以及
    `frame_layouts.*` 这类会被约束层重建的列表——2026-11-12 复核要求两者都覆盖。
    """
    from step01_script_gen.repair_fields import path_allowed, shot_by_target
    out: dict = {}
    for tid, allowed in (allowed_by_target or {}).items():
        if not allowed:
            continue
        _b = shot_by_target(merged, tid) if isinstance(merged, dict) else None
        _a = shot_by_target(after, tid)
        if not isinstance(_b, dict) or not isinstance(_a, dict):
            continue
        _fb, _fa = _flatten_exact_paths(_b), _flatten_exact_paths(_a)
        _lost = sorted(p for p, v in _fb.items()
                       if path_allowed(p, allowed) and _fa.get(p) != v)
        if _lost:
            out[str(tid)] = _lost
    return out


def _audit_issue_newly_resolved(script_after: dict, script_before: dict | None,
                                issue: _mrt.Issue, attempted: set | None = None) -> bool:
    """该 audit issue 是否**在本轮内新解决**（v6.102）.

    `_audit_issue_resolved()` 问的是「evidence **现在**还在不在稿子里」——它是
    **幂等**的：第 1 轮清掉之后永远为真。第十六次修复前的实测（第十五次）：

    ```text
    第 2 轮: before=10+audit2 after=10 resolved=0 audit_resolved=2
    第 3 轮: before=10+audit2 after=10 resolved=0 audit_resolved=2   ← 数字一模一样
    ```

    两轮 `resolved=0`，却都靠这个**陈旧的 2** 通过验收（接受条件里
    `audit_resolved > 0` 即可），于是同一批 4 镜被反复改写两次、白烧 2 次 LLM 调用
    而没有任何可测量的收益。

    加一个基线口径：**基线里已经「解决」的不算本轮功绩**——只有基线里 evidence
    仍在、本轮之后才消失的，才算新解决。
    """
    if script_before is None:
        return _audit_issue_resolved(script_after, issue)
    # 发现位置不等于修法位置：上游补建立、同镜补另一侧时应保留来源证据。
    # 这种授权路径只能给一次触达机会，不能冒充已解决；必须再次完整审计。
    from step01_script_gen.repair_fields import path_allowed, _target_id_of
    declared = getattr(issue, "repair_targets", None)
    observation_allowed = True
    if declared is not None:
        fields = _audit_paired_fields(script_before, issue)
        observation_allowed = path_allowed(str(getattr(issue, "field_path", "") or ""),
                                          fields.get(_target_id_of(issue.shot_id), set()))
    if (not observation_allowed or _audit_evidence_text(issue) is None
            or _audit_issue_resolved(script_before, issue)):
        # v6.108（2026-09-24 第三十次重跑实测）：证据在**改前稿子里就找不到**
        # → 这个判据没有区分度（自由文本证据），于是「本轮新解决」永远为 False，
        # 审计类阻塞**结构性无法被修复轮证明解决**：
        #
        #   实测：提交点确定性阻塞已归零、只剩 1 条审计问题（shot 2 ff_desc 道具
        #   状态与原文不符）。模型按 issue 点名的字段改了 ff_desc、没有引入新阻塞，
        #   却因 `audit_resolved=0` 被判 `no_target_resolved` → 整轮回退 → BLOCK。
        #
        # 兜底口径：改前不可判 → 用「本轮是否动过该 issue 点名的字段」计一次
        # 已解决。**这不是放宽最终门禁**：提交循环每轮都会重跑全量审计，
        # `blocking = 审计 ∪ 确定性` 必须为空才可能提交；改得不对下一轮会再次报出来。
        # v6.139（修复 2，九审根因 2）：兜底记账**每个问题身份只记一次**——同一问题
        # 靠「动过字段」反复通过验收 = 把尝试当进展（18:46 run 台账第 21 条
        # accepted/resolved=0 实锤；触达证明尝试过，不证明语义已解决）。已尝试
        # 未被后续审计核实的身份，第二轮起不再给兜底分——要么真解决（证据判据
        # 不受影响），要么如实 no_target_resolved。
        if attempted and _audit_issue_identity(issue) in attempted:
            return False
        return _audit_issue_field_touched(script_before, script_after, issue)
    return _audit_issue_resolved(script_after, issue)


def _issue_shot_index(issue) -> str:
    """issue 的 `shot_id` → 镜头身份：序号裸串、钩子的 `"hook"`，或**不可解析的原串**.

    「可解析」= `"hook"` 或纯数字串；空串与 `shot_x` / `第5镜` 这类**指不明**的写法
    原样返回（保留原值便于日志回查），调用方必须按「定位不到」处理——
    `_issue_target_shot` 返回 None、`_audit_issue_resolved` 判「无法证明已解决」。

    v6.120：模型审计的 `shot_id` 记法不保证是裸数字（实测 `"shot5"`），这里必须与
    `_target_id_of` 同口径——否则「按 index 找镜头」的每一处都会找不到（`shot5`
    匹配不上 `index: 5`），进而把 issue 误判成「目标镜头已被结构补丁删除」。
    """
    from step01_script_gen.repair_fields import _shot_index_of
    return _shot_index_of(getattr(issue, "shot_id", ""))


def _issue_target_shot(script: dict, issue) -> "dict | None":
    """该 issue 点名的镜头对象（hook 用 `hook`，其余按 index）；**找不回来就是 None**.

    v6.120：`shot_id` 为空或**指不明**（`""` / `shot_x` / `第5镜`）时返回 None——
    调用方必须把它读成「无法定位」，不得读成「这个镜头不存在于稿中」。
    """
    if not isinstance(script, dict):
        return None
    _idx = _issue_shot_index(issue)
    if _idx == "hook":
        return script.get("hook") if isinstance(script.get("hook"), dict) else None
    if not _idx.isdigit():
        return None
    for item in script.get("shots") or []:
        if isinstance(item, dict) and str(item.get("index")) == _idx:
            return item
    return None


def _frame_layout_of(shot: "dict | None", frame_id: str):
    """该**镜头**里这一帧在 `frame_layouts` 的条目（kf 用序号，越界回退整表）."""
    fl = shot.get("frame_layouts") if isinstance(shot, dict) else None
    if not isinstance(fl, dict):
        return None
    fid = str(frame_id or "")
    if fid == "ff":
        return fl.get("ff")
    if fid == "lf":
        return fl.get("lf")
    if fid.startswith("kf"):
        try:
            i = int(fid[2:])
        except ValueError:
            return fl.get("kfs")
        kfs = fl.get("kfs")
        if isinstance(kfs, list) and 0 <= i < len(kfs):
            return kfs[i]
        return kfs
    return fl


def _audit_issue_field_touched(before_script: "dict | None", after_script: dict,
                               issue) -> bool:
    """本轮是否**改动了该 issue 点名的字段**（v6.108）.

    用于「证据判据没有区分度」时的兜底——见 `_audit_issue_newly_resolved`。
    只回答「模型有没有按 issue 指的地方动手」，不回答「改得对不对」；后者由
    下一轮全量审计裁决（提交循环每轮都会重跑审计）。
    """
    if getattr(issue, "repair_targets", None) is not None:
        from step01_script_gen.repair_fields import merge_authorized, shot_by_target
        for tid, allowed in _audit_paired_fields(before_script, issue).items():
            before = shot_by_target(before_script, tid)
            after = shot_by_target(after_script, tid)
            if not allowed or not isinstance(before, dict) or not isinstance(after, dict):
                continue
            projected, _ = merge_authorized(before, after, allowed)
            if projected != before:
                return True
        return False
    fp = str(getattr(issue, "field_path", "") or "")
    root = fp.replace("[", ".").split(".")[0].strip() if fp else ""
    # v6.113：审计的 field_path 语法很活——`props` / `ff_desc.props` /
    # `frame_layouts.ff[0].props` 都出现过。**裸的布局叶子名**（`props`/`pose`…）
    # 此前不在识别范围内 → 模型明明改了那一帧的布局，却被判「没动过」→
    # `audit_resolved=0` → 整轮回退（第三十一次重跑：shot 2 `props` / shot 5 `props`）。
    _LEAF_ROOTS = ("props", "pose", "position", "gaze_target", "on_screen",
                   "facing", "surface", "name")
    if root in _LEAF_ROOTS:
        fid = str(getattr(issue, "frame_id", "") or "")
        _b = _issue_target_shot(before_script, issue)
        _a = _issue_target_shot(after_script, issue)
        if not isinstance(_b, dict) or not isinstance(_a, dict):
            return False
        return _frame_layout_of(_b, fid) != _frame_layout_of(_a, fid)
    if root not in ("prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone",
                    "keyframes", "frame_layouts"):
        return False
    b = _issue_target_shot(before_script, issue)
    a = _issue_target_shot(after_script, issue)
    if not isinstance(b, dict) or not isinstance(a, dict):
        return False
    if root == "keyframes":
        # 帧类 issue：帧序号可能因补帧整体后移，所以按「该帧的 desc 集合是否变化」
        # 兜底，而不是死扣下标。
        fid = str(getattr(issue, "frame_id", "") or "")
        try:
            idx = int(fid[2:]) if fid.startswith("kf") else -1
        except ValueError:
            idx = -1
        bd = [str(k.get("desc") or "") for k in (b.get("keyframes") or [])
              if isinstance(k, dict)]
        ad = [str(k.get("desc") or "") for k in (a.get("keyframes") or [])
              if isinstance(k, dict)]
        if 0 <= idx < len(bd) and 0 <= idx < len(ad):
            return bd[idx] != ad[idx]
        return bd != ad
    if root == "frame_layouts":
        return b.get("frame_layouts") != a.get("frame_layouts")
    return str(b.get(root) or "") != str(a.get(root) or "")


def _audit_issue_resolved(script: dict, issue: _mrt.Issue) -> bool:
    """Best-effort verification for an audit-only issue without a second LLM call.

    An audit issue is considered resolved only when its original evidence is no
    longer present in the targeted text.  This is deliberately conservative:
    unverifiable audit issues cannot justify keeping a patch.

    ⚠️ 只在证据**确实出现在改前文本里**时才有区分度——见
    `_audit_issue_newly_resolved`：审计证据常常是自由文本描述（「原文X，但
    ff_desc 写 Y，与原文不符」），它永远不会字面出现在稿子里，此时本函数对
    改前改后都返回 True，等于没有判据。
    """
    evidence = _audit_evidence_text(issue)
    if evidence is None:
        return False
    # v6.120（用户口径）：`shot_id` 为空或**指不明**时，这条 issue 的解决与否
    # **无法证明**（返回 False）。旧实现会走到下面的「镜头已被结构补丁删除」分支
    # 返回 True —— 把「定位不到」当成「已解决」，与「不可验证的审计问题不能用来
    # 保住一轮改动」的既定口径相反（见 docstring）。注意：不是本次 BLOCK 的成因
    # （正式入口传的是修前稿，改前改后同为 True → `newly_resolved` 仍为 False），
    # 这里补的是「不静默伪造结论」这一条。
    _sid = _issue_shot_index(issue)
    if not (_sid == "hook" or _sid.isdigit()):
        return False
    shot = _issue_target_shot(script, issue)
    if shot is None and not any(
            isinstance(s, dict) and str(s.get("index")) == _sid
            for s in (script.get("shots") or [])):
        return True  # target shot was removed by a structural patch

    texts: list[str] = []
    field_path = str(getattr(issue, "field_path", "") or "")
    if field_path in ("prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone"):
        texts.append(str(shot.get(field_path) or ""))
    else:
        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            texts.append(str(shot.get(field) or ""))
        for kf in shot.get("keyframes") or []:
            if isinstance(kf, dict):
                texts.append(str(kf.get("desc") or ""))
    return not any(evidence in text for text in texts)


# v6.103（总计划阶段 3）：跨字段 issue 的**判据证据**必须留在高优先字段里。
#
# 优先级（总计划 §1.2）：原文/明确旁白 > motion_desc/temporal_plan >
# ff/kf/lf 描述 > frame_layouts > 聚合 prompt。四条跨字段 issue 里，判据证据都
# 落在**比待修字段更高优先**的那一层：
#
#   - 在场/持物：证据在**帧描述**里（布局是更低的派生层）→ 正确修法是改布局；
#   - 结果动作支撑：证据在 **lf_desc** 里（motion 才是要补的那层）→ 正确修法是
#     给 motion 加过程段；
#
# 于是「把描述里那句话删掉」能让检测器立刻闭嘴 —— 那不是修复，是**改写检测
# 输入**，正是总计划 §阶段 3 点名的失败模式（「模型只改写措辞就可能骗过字符串
# 判据」）。`frame_stage_pose_consistency` 不在表里：它的证据是**布局**里的姿势，
# 属于低优先字段，改掉它正是合法修复方向。
#
# ⚠️ v6.103 修（真实重跑实测）：闸门**只**对 `result_action_support` 生效。
# 在场/持物这两类的「哪一侧是错的」由**原文**决定（§1.2 的优先级最高位），
# 确定性层判不了：第二十次重跑里 shot 2 的正解就是改 `ff_desc`（原文写「左手
# 钓杆垂在湖面」），而闸门会把这条正解判成「删证据」→ 整轮 resolved=0 → BLOCK。
# 那两类的方向交给模型按原文切片决定，确定性层只保证「矛盾必须消失」
# （修复后重跑同一检测）+「不许越权改别处」。
# v6.144（2026-09-29，用户批准改判据）：口径从**逐字**改为**事件级**。
# 逐字口径实测把审计自己要求的改写判成「删证据」：第一章第 3 轮的 `minimal_fix`
# 明令把结果句改写为同义表述（「刺中」→「刺入」），照办的修复被按目标回退，
# 下一轮审计再报同一条，三轮空转在同一个镜头上。事件级只拦「这句的结果动作没了」。
_CROSS_FIELD_EVIDENCE_GUARD = {
    "result_action_support": "lf",
}

#: 按 code 走**事件级**口径的清单。事件级 = 「同一结果动作组里任一表述仍在」
#: 即通过（措辞可变）；只有该组**一个表述都不剩**才算「删证据」——那正是让
#: `result_action_support` 闭嘴所需的唯一动作，所以拦它不会误伤合法改写。
#: v6.145（2026-09-29 复核反例）：**逐组**判定——同一分句可命中多组
#: （`_clauses` 不切「、」），并成一次 `any()` 会漏掉「第二个动作整段被删」。
#: 不在表里的 code 只能逐字比：认不出动作**不放行**（准则 7）。
_CROSS_FIELD_EVIDENCE_IDENTITY_CODES = {"result_action_support"}


def _cross_field_evidence_removed(before_state: dict | None, script_after: dict,
                                  picked: set) -> list[str]:
    """跨字段 issue 判据证据在修复后**从高优先字段消失**的清单（空 = 通过）.

    只检查被点名镜头；证据按 `" | "` 拆分后**逐段**判定，两条口径按 code 选：

    - **事件级**（`_CROSS_FIELD_EVIDENCE_IDENTITY_CODES`，v6.144/.145）：证据是
      「含结果动词的整个分句」，要求**每个命中的结果动作组都还剩一个表述**
      （词表单源 `cross_field_checks._RESULT_ACTIONS`）。措辞可变——审计自己的
      `minimal_fix` 就可能明令改写；但**当事角色的替换不在覆盖内**（见
      `result_action_evidence_groups` 的未覆盖声明）；
    - **逐字**（其余 code，或分句里认不出结果动作时）：证据必须原样仍在。
      「认不出」是退回严口径的理由，**不是**放行的理由。
    """
    from step01_script_gen.cross_field_checks import (
        frame_text, result_action_evidence_groups)

    by_key: dict = {}
    for s in script_after.get("shots") or []:
        if isinstance(s, dict):
            by_key[str(s.get("index"))] = s
    if isinstance(script_after.get("hook"), dict):
        by_key["hook"] = script_after["hook"]

    removed: list[str] = []
    for rec in (before_state or {}).get("issues") or []:
        if not isinstance(rec, dict):
            continue
        mode = _CROSS_FIELD_EVIDENCE_GUARD.get(str(rec.get("code") or ""))
        if mode is None:
            continue
        sid = str(rec.get("shot_id") if rec.get("shot_id") is not None else "")
        tid = "hook" if sid in ("0", "hook") else f"shot_{sid}"
        if tid not in picked:
            continue
        shot = by_key.get("hook" if tid == "hook" else sid)
        if not isinstance(shot, dict):
            continue
        frame_id = "lf" if mode == "lf" else str(rec.get("frame_id") or "")
        if not frame_id:
            continue
        text = frame_text(shot, frame_id)
        _code = str(rec.get("code") or "")
        _event_level = _code in _CROSS_FIELD_EVIDENCE_IDENTITY_CODES
        for part in str(rec.get("evidence") or "").split(" | "):
            part = part.strip()
            if not part:
                continue
            _groups = (result_action_evidence_groups(part)
                       if _event_level else ())
            if _groups:
                # **逐组**要求还剩一个表述——并成一次 any() 会让同一分句里的
                # 第二个动作整段被删也判通过（复核反例）。
                _lost = [g for g in _groups if not any(w in text for w in g)]
                if _lost:
                    removed.append(
                        f"{tid}.{frame_id} 结果动作被删除（{_code}）："
                        f"「{part[:40]}」（%d/%d 个动作组无残留）"
                        % (len(_lost), len(_groups))
                    )
            elif part not in text:
                removed.append(
                    f"{tid}.{frame_id} 判据证据被删除（{_code}）：「{part[:40]}」"
                )
    return removed


# v6.104（2026-09-24 总计划 §1.1）：本条修复轮**授权了 KF 列表本身**的镜头，
# 必须通过 `kf_structural_guard` 的两条确定性验收（KF 单调在镜内 / motion 原事件
# 文本仍在、顺序不变）。
#
# 为什么闸门挂在这里而不是 `_validate_replacement_shot`：授权列表只发生在
# 「必须增删 KF 才能修」的问题上，普通修复轮不适用——把单调性检查加到全局
# 校验里，会把「本来就乱序但从未被点名的镜头」也判成越权，重现本仓反复出现过的
# 「假拒绝 → resolved=0」形态。范围收在「本轮拿到列表授权」的镜头上。
def _neighbor_motion_window(script: dict, tid: str, novel_text: str):
    """本镜没有台词/旁白时的**邻近原文窗口**（±1~2 镜，再外扩 600 字）.

    实测（2026-11-06 f6 刺杀、f8 抓船舷）：纯动作镜头的 `narration_segment` 为空，
    `shot_source_window_raw` 只能回落整章，于是 `narrow_motion_patch` 一律合回基线——
    这类镜头的 motion 修复**结构性不可达**。相邻镜往往描写同一段连续动作，用它定位
    出的窗口仍由同一套判据复核（同组动作 + 施事/受事），只扩大可核对范围，不放松判据。
    """
    from step01_script_gen.validation import shot_source_window_raw

    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    idx = None
    for i, s in enumerate(shots):
        if "shot_%s" % s.get("index") == tid:
            idx = i
            break
    if idx is None:
        return "", False
    raw = str(novel_text or "")
    for off in (1, -1, 2, -2):
        j = idx + off
        if not (0 <= j < len(shots)):
            continue
        w, strict = shot_source_window_raw(shots[j], novel_text)
        if not (strict and w):
            continue
        pos = raw.find(w)
        if pos < 0:
            return w, True
        return raw[max(0, pos - 600): min(len(raw), pos + len(w) + 600)], True
    return "", False


def _kf_structural_violations(before_script: dict, after_script: dict,
                              allowed_by_target: dict | None,
                              picked: set, novel_text: str = "") -> list[str]:
    from step01_script_gen.kf_structural_guard import (
        kf_structural_violations, narrow_motion_patch)
    from step01_script_gen.validation import shot_source_window_raw

    def _shot(script: dict, tid: str):
        if tid == "hook":
            return script.get("hook") if isinstance(script.get("hook"), dict) else None
        sid = str(tid)[5:] if str(tid).startswith("shot_") else str(tid)
        for s in script.get("shots") or []:
            if isinstance(s, dict) and str(s.get("index")) == sid:
                return s
        return None

    # v6.146：**名册**（本脚本自己的角色名 + 布局里的名字）——纠错判据用它查
    # 「施事/受事是否被换掉」。取自脚本自身，不写死任何具体角色名。
    _canonical: set = set()
    for _c in (before_script.get("characters") or []):
        if isinstance(_c, dict):
            _nm = str(_c.get("identifier") or "").strip()
            if _nm:
                _canonical.add(_nm)     # 正名：出处关系校验要求「同句含全部当事角色」
    # 2026-11-19 第 1 包 M03：删除未使用的布局名册收集——它写入从未定义的
    # `_roster`（20a6bac 起），布局节点带 `name` 时整个守卫以 NameError 崩溃。

    out: list[str] = []
    for tid in picked or ():
        allowed = (allowed_by_target or {}).get(tid) or ()
        _allowed_set = {str(a) for a in allowed}
        if "keyframes" not in _allowed_set:
            continue                      # 本轮未授权改 KF 列表 → 不适用
        before, after = _shot(before_script, tid), _shot(after_script, tid)
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        # v6.149（复核收窄）：本轮**授权改 motion_desc** 时，由**代码**做范围收窄——
        # `narrow_motion_patch` 只采纳通过「同一原文事件里的动作 + 施事/受事」校验的
        # 时段，其余时段**按基线原样合回**（改错了不会被采纳，而不是把整轮判死）。
        # 合回后本函数不再叠加逐字事件保全（`skip_motion`），否则会把刚采纳的时段
        # 判成「原事件文本丢失」。
        _editable_motion = any(a == "motion_desc" or a.startswith("motion_desc.")
                               for a in _allowed_set)
        _skip_motion = False
        if _editable_motion:
            _window, _strict = shot_source_window_raw(before, novel_text)
            if not _strict:
                # 2026-11-06：无台词镜头用邻近镜的严格窗口兜底（仍过同一关系判据）
                _nw, _nstrict = _neighbor_motion_window(before_script, tid, novel_text)
                if _nstrict:
                    _window, _strict = _nw, True
            _new_motion, _notes = narrow_motion_patch(
                before, after, canonical=_canonical, novel_window=_window,
                window_strict=_strict)
            if _new_motion is None:
                # 结构不可安全合并（段数变了/解析不出）→ 按违例处理，不放行
                out.extend("%s: %s" % (tid, m) for m in _notes)
            else:
                for _n in _notes:
                    _mrt._logger.info("[REPAIR] %s 动作范围收窄: %s" % (tid, _n))
                if _new_motion != str(after.get("motion_desc") or ""):
                    # `after` 就是 `after_script` 里的那个镜头对象 → 原地写回即生效
                    after["motion_desc"] = _new_motion
                _skip_motion = True
        out.extend("%s: %s" % (tid, m) for m in kf_structural_violations(
            before, after, skip_motion=_skip_motion))
    return out


def _g7_merged_state(merged_script, novel_text) -> "dict | None":
    """G7：算「合并后、后处理前」的确定性 state，供确定性 target-loss 检查.

    `None` 只表示**没有合并快照**（调用方未提供，向后兼容）；收集器**失败**
    返回带 `checker_failed=True` 的状态，由 `_repair_acceptance_decision` 判
    `postprocess_check_failed` 拒收——"检查没做完"不能当成"没有 target-loss"。
    """
    if not isinstance(merged_script, dict):
        return None
    try:
        return _msc._collect_repair_warnings(merged_script, novel_text)
    except Exception as exc:
        _mrt._logger.warn("[REPAIR] G7 合并后确定性状态计算失败（本轮拒收）: %s" % exc)
        return {"issues": [], "checker_failed": True,
                "error": "merged_state_failed: %s" % exc}


def _repair_acceptance_decision(before_state: dict | None,
                                after_state: dict | None,
                                audit_issues: list[_mrt.Issue] | None,
                                script_after: dict,
                                locked_changed: bool,
                                script_before: dict | None = None,
                                non_target_changed: list | None = None,
                                locked_by_llm_changed: list | None = None,
                                evidence_removed: list | None = None,
                                kf_structural_violations: list | None = None,
                                layout_structural_violations: list | None = None,
                                audit_attempted: set | None = None,
                                merged_script: dict | None = None,
                                merged_state: dict | None = None) -> dict:
    """Pure acceptance decision for one targeted-repair round.

    Required conditions:
      * both checker runs completed successfully;
      * no new blocking issue identity appears;
      * locked fields are unchanged;
      * **未被点名的镜头语义 revision 完全不变**（v6.77 第四阶段新增）——
        修复轮是「镜头事务」，只许动被点名的镜头；
      * **LLM 未改动被点名镜头的锁定字段**（v6.87：基线取「合并后、后处理前」，
        只含 LLM 的改动；后处理是管线自己的确定性归一化，不算越权）；
      * **跨字段 issue 的判据证据没被删掉**（v6.103，总计划阶段 3）——
        删证据能让检测器闭嘴，但那是改写检测输入，不是修复；
      * **增删 KF 的结构修复通过了 §1.1 验收**（v6.104）——KF 落在镜内且按时段
        单调、`motion_desc` 的原事件文本仍在且顺序不变（授权 KF 列表的配套代价）；
      * **G7 的合并后确定性检查真的跑完**（`merged_state["checker_failed"]` 为假）——
        检查未完成按 `postprocess_check_failed` 拒收，不静默跳过 target-loss；
      * at least one target issue (deterministic or audit) is verifiably gone.
    """
    # v6.104.1：帧数变了的镜头，kf 类 issue 按「该镜的该类问题」比较（位置序号
    # 在补帧后会整体后移，逐位置比较会把同一缺陷记成「一减一增」的假信号）。
    _reindexed = _reindexed_kf_shots(script_before, script_after)
    before_keys = _blocking_record_keys(before_state, _reindexed)
    after_keys = _blocking_record_keys(after_state, _reindexed)
    resolved = before_keys - after_keys
    new_blocking = after_keys - before_keys
    checker_failed = bool((before_state or {}).get("checker_failed")
                          or (after_state or {}).get("checker_failed"))
    # 2026-11-14 复核 P2：G7 的**合并后**检查没跑完（收集器异常/内部 checker_failed）
    # 是独立拒收状态——旧实现返回 None 后静默跳过确定性 target-loss，等于把
    # "检查未完成"当成"没有丢失"。
    merged_checker_failed = bool(isinstance(merged_state, dict)
                                 and merged_state.get("checker_failed"))
    audit_targets = [i for i in (audit_issues or []) if _mrt.is_blocking(i)]
    # v6.102：audit 判据必须是「**本轮新**解决」——`_audit_issue_resolved` 本身幂等，
    # 拿它当轮内功绩会让空转轮次靠陈旧计数通过验收。
    # v6.139：`audit_attempted` = 本 run 已尝试过（触达兜底记过账）的问题身份集——
    # 同一身份不再给兜底分（见 `_audit_issue_newly_resolved`）。
    _touched_ids: list = []
    audit_field_touched = 0
    for i in audit_targets:
        if _audit_issue_newly_resolved(script_after, script_before, i,
                                       attempted=audit_attempted):
            audit_field_touched += 1
            _touched_ids.append(_audit_issue_identity(i))
    has_targets = bool(before_keys or audit_targets)
    _non_target = list(non_target_changed or [])
    # v6.87：LLM 对锁定字段的写入与「非目标镜头被改动」是**两个不同的闸门**，
    # 分开命名——v6.83 起两者被并在一个列表里、统一报 `non_target_changed`，
    # 结果日志永远指不清是哪一道拦下的（三次运行都是这个 reason，无从定位）。
    _locked_by_llm = list(locked_by_llm_changed or [])
    # v6.103：证据被删是**独立闸门**——它和「新增 blocking」「越权」都是不同性质的
    # 失败，混进任何一条都会让日志指不清方向。
    _evidence_removed = list(evidence_removed or [])
    # v6.104：授权 KF 列表之后，增删必须仍然满足结构验收（独立闸门，理由同上）。
    _kf_struct = list(kf_structural_violations or [])
    # v6.154（复核之二十 P1）：点名**帧子列表**（增删布局条目）之后，只允许纯增删——
    # 保留条目逐字不变、相对顺序不变、非点名帧不动。独立闸门，理由同上：
    # 与「KF 结构」「证据被删」「越权」混在一起会让日志指不清方向。
    _layout_struct = list(layout_structural_violations or [])
    # 2026-11-14 S06 G7（批准）：**目标丢失（target-loss）**闸门——合并后目标已达成、
    # 后处理后又丢了，说明管线自己的确定性步骤把已授权的修复抵消掉。只按
    # 「目标从达成变为未达成」拒收；不按「存在任何非目标残余」一刀切（那会误拒
    # R1 类合法等价派生，如全角 `｜载体：` 规范化）。
    _post_merge_target_lost: list = []
    if isinstance(merged_script, dict):
        for _i in audit_targets:
            try:
                if (_audit_issue_resolved(merged_script, _i)
                        and not _audit_issue_resolved(script_after, _i)):
                    _post_merge_target_lost.append(_audit_issue_identity(_i))
            except Exception:
                continue
    # 2026-11-14 P1/G7（覆盖确定性目标）：审计目标之外，**确定性 blocking** 也要按
    # 「合并后已达成、后处理后丢失」检查。只要 merged 的确定性 state 里该 key 已
    # 不出现（确定性复核通过），而最终 script_after 的 state 里又出现，就是管线
    # 自己的确定性步骤把已授权修复抵消掉，与审计目标同等拒收。
    if isinstance(merged_state, dict) and before_keys:
        _merged_keys = _blocking_record_keys(merged_state, _reindexed)
        for _k in sorted((before_keys - _merged_keys) & after_keys):
            if _k not in _post_merge_target_lost:
                _post_merge_target_lost.append(_k)
    accepted = (
        not checker_failed
        and not merged_checker_failed
        and not new_blocking
        and not locked_changed
        and not _non_target
        and not _locked_by_llm
        and not _evidence_removed
        and not _kf_struct
        and not _layout_struct
        and not _post_merge_target_lost
        and (not has_targets or bool(resolved) or audit_field_touched > 0)
    )
    reason = ""
    if checker_failed:
        reason = "checker_failed"
    elif merged_checker_failed:
        reason = "postprocess_check_failed"
    elif new_blocking:
        reason = "new_blocking"
    elif _post_merge_target_lost:
        reason = "postprocess_target_lost"
    elif _evidence_removed:
        reason = "evidence_removed"
    elif _kf_struct:
        reason = "kf_structure_invalid"
    elif _layout_struct:
        reason = "layout_structure_invalid"
    elif locked_changed:
        reason = "locked_changed"
    elif _locked_by_llm:
        reason = "locked_field_changed"
    elif _non_target:
        reason = "non_target_changed"
    elif has_targets and not resolved and audit_field_touched <= 0:
        reason = "no_target_resolved"
    return {
        "accepted": accepted,
        "reason": reason,
        "before": len(before_keys),
        "after": len(after_keys),
        "resolved": len(resolved),
        "new_blocking": len(new_blocking),
        # 诊断（v6.104.1）：被拒时必须说清「新增了什么」——此前只有计数，
        # 排查只能靠复现整轮运行（且日志行会被 581 字符截断）。
        # v6.119：**不截断**。此前是 `[:6]`，而这份 keys 不只用于日志——`_new_blocking_targets`
        # 用它定位「肇事目标」，截断会让按目标回退**漏掉**目标（正确性问题），
        # 台账也因此永远追不全。日志侧按需取前 N 条并显式标出「另 M 条」。
        "new_blocking_keys": sorted(new_blocking),
        "resolved_keys": sorted(resolved)[:6],
        "reindexed_kf_shots": sorted(_reindexed),
        "audit_targets": len(audit_targets),
        # v6.115（审核）：**如实命名**——自由文本证据下它只证明「点名字段被改过」，
        # 不证明矛盾消失。真实解决数以**修复后的审计复核**为准，不得用它推导
        # 「每轮净修 N 条」（那会高估修复效率）。
        "audit_field_touched": audit_field_touched,
        # v6.139：本轮靠兜底记账的问题身份（shot, family）——调用方据此维护
        # 「已尝试未核实」集合，同一身份第二轮起不再给兜底分。
        "audit_touched_ids": _touched_ids,
        "checker_failed": checker_failed,
        # 2026-11-14 复核 P2：G7 合并后检查未完成（独立于 before/after 的 checker_failed）。
        "merged_checker_failed": merged_checker_failed,
        "locked_changed": bool(locked_changed),
        "non_target_changed": _non_target,
        "locked_by_llm_changed": _locked_by_llm,
        "evidence_removed": _evidence_removed,
        "kf_structural_violations": _kf_struct,
        "layout_structural_violations": _layout_struct,
        # 2026-11-14 S06 G7：后处理把已达成的目标改回/改写的身份（target-loss）。
        "postprocess_target_lost": _post_merge_target_lost,
    }


# v6.84（二次审核发现 3 实施 #4）：每轮定向修复的镜头数。
# 6 个完整镜头对象 + 上下文会顶到 8000 token 上限（本次实测 `finish_reason=length`
# 整份丢弃）；按 3 镜分批，靠**批数**而不是靠抬高 token 上限来解决上下文体积。
# 每轮名下的镜头数（v6.101：3 → 4）。配套把 `REPAIR_MAX_OUTPUT_TOKENS`
# 从 8000 抬到 16000——4 个完整镜头对象 + 上下文按实测约 5-7k token，
# 16k 有 2 倍余量；**不靠抬 token 掩盖上下文体积**这条原则仍然成立，
# 所以批量只加到 4，不是 6/8。
_REPAIR_MAX_SHOTS_PER_ROUND = 4

# v6.122（用户决定 B，2026-09-25）：**联动组的专用上限**。
#
# `_REPAIR_MAX_SHOTS_PER_ROUND`（4）继续管**互不相关**的目标（它约束的是 prompt
# 体积）。但一个**原子联动组**既不能被拆（拆 = 半条修复，正是 shot 5 被整镜回退的
# 成因），也不该被容量规则毙掉——毙掉它等于**结构性无法提交**：同一份稿的审计每轮
# 都会声明同一组，而这条问题能力上可修。
#
# 实测 run 20260925T145218-32008：一条跨镜问题声明 5 镜（4 镜 prompt + 1 镜 ff_desc，
# 已按 audited revision 逐字核实**全部必要**），当时上限 4 → 组永远进不了本轮 →
# 只剩 BLOCK 一条路。
#
# 6 的来历：4 个完整镜头对象实测 5-7k token，输出上限 16k；6 镜约 8-10k，仍在 16k
# 内——**不靠抬 token 预算掩盖上下文体积**这条原则不变。超过 6 的组属「一次改不完」
# → 调用方**立即 BLOCK**，不再空耗修复轮与审计调用。
_REPAIR_MAX_SHOTS_PER_GROUP = 6
