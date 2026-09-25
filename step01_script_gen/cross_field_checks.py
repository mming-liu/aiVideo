# -*- coding: utf-8 -*-
"""跨字段一致性检查（v6.103，2026-09-23 总计划阶段 2）.

`docs/step01-and-awesome-seedance-master-plan-2026-09-23.md` §2.3 记录的四条真实
blocking（目录 12）此前**只有 model audit 能发现**：结构化质量层看不见「描述说
已离场、布局仍在场」这类跨字段矛盾，于是模型审计报 blocking，而确定性层
`PASS`——两条口径互相矛盾，谁也无法驱动修复。

本模块给这四类矛盾建立**纯检测**（不改写任何字段）：

  1. `frame_presence_consistency`    描述称已离场，frame_layouts 仍 on_screen=true
  2. `prop_possession_consistency`   描述称已脱手，frame_layouts.props 仍持有
  3. `frame_stage_pose_consistency`  LF 是结果帧，pose 仍是过程态
  4. `result_action_support`         LF 的结果动作在 motion_desc 里没有过程段

返回 `(issues, failures)`：`failures` 是「检测器抛异常」的清单。**不得丢弃**
——四类矛盾只有本模块能确定性看出来，静默跳过等于让质量门 fail-open
（v6.111，审核 P1-2）。

设计约束（与总计划 §2.3 一致）：

  - **只做高置信结构关系**。开放中文语义（含糊的「似乎离开了」）交给 final
    audit，不在这里宽泛匹配关键词；
  - 检测器**不写任何字段**——修复走字段授权事务（`repair_fields`）；
  - issue 身份 = `(code, shot_id, frame_id, entity_id, field_path)`，**不含文案**
    ——措辞变化不能让同一条 issue 变成新问题（`_blocking_record_key` 同口径）。

## 与既有检查的分工（有意互补，不替换）

  - `_onscreen_prop_issues` 查的是**反方向**：`on_screen=false` 却仍要求动作。
    本模块的 #1 查「描述已离场、布局说在场」；
  - `_prop_release_owner_issues` 查「脱手但归属不明（多个持有者）」。本模块的
    #2 查「脱手但布局仍列该道具」；
  - `_pose_conflicts` 查同帧姿态自相矛盾（坐+躺）。本模块的 #3 查**帧阶段**
    与姿态的矛盾（结果帧里写着过程态）；
  - #4 是全新的：LF 结果动作必须在 `motion_desc`（动作时序事实源，总计划 §1.2）
    里有对应过程段。
"""
from __future__ import annotations

import re

from shared.alias_map import CHARACTER_ALIASES, apply_char_alias
from step01_script_gen.quality_issues import Issue


# ---------------------------------------------------------------------------
# 帧访问
# ---------------------------------------------------------------------------
def _sorted_kfs(shot: dict) -> list[dict]:
    kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    kfs.sort(key=lambda k: float(k.get("t_sec") or k.get("time_ratio") or 0))
    return kfs


def frame_ids(shot: dict) -> list[str]:
    """镜头内的帧标识序列：`ff` → `kf0..kfN` → `lf`（与 `_frame_texts` 同序）."""
    out = ["ff"]
    out.extend(f"kf{i}" for i in range(len(_sorted_kfs(shot))))
    out.append("lf")
    return out


def frame_text(shot: dict, frame_id: str) -> str:
    """该帧对应的描述文本（ff_desc / keyframes[i].desc / lf_desc）."""
    if frame_id == "ff":
        return str(shot.get("ff_desc") or "")
    if frame_id == "lf":
        return str(shot.get("lf_desc") or "")
    if frame_id.startswith("kf"):
        try:
            return str(_sorted_kfs(shot)[int(frame_id[2:])].get("desc") or "")
        except (IndexError, ValueError):
            return ""
    return ""


def frame_entries(shot: dict, frame_id: str) -> list[dict]:
    """该帧的 `frame_layouts` 条目列表（缺失/结构不符 → 空表）."""
    layouts = shot.get("frame_layouts")
    if not isinstance(layouts, dict):
        return []
    if frame_id.startswith("kf"):
        try:
            bucket = (layouts.get("kfs") or [])[int(frame_id[2:])]
        except (IndexError, ValueError, TypeError):
            return []
    else:
        bucket = layouts.get(frame_id)
    if not isinstance(bucket, list):
        return []
    return [x for x in bucket if isinstance(x, dict)]


def _layout_field_path(frame_id: str, leaf: str) -> str:
    """该帧 `frame_layouts` 条目的**精确叶子路径**（授权层用）.

    写成叶子（`frame_layouts.lf.*.on_screen`）而不是整帧桶
    （`frame_layouts.lf`）：`repair_fields._narrow()` 会丢掉「被更具体路径覆盖的
    宽前缀」，而模型审计给的是精确叶子——两者并集后若只剩叶子，模型就改不动
    同帧的其它叶子（例如修「仍在场」必须动 `on_screen`，而审计只点了 `pose`）。
    `*` 与 `flatten_paths` 的列表归一写法一致，`path_allowed` 支持。
    """
    if frame_id.startswith("kf"):
        # ⚠️ `frame_layouts.kfs` 是**列表的列表**（每个 kf 一个条目列表），
        # 规范化后是 `frame_layouts.kfs.*.*.<leaf>` —— 两个通配层级。
        # 只写一个 `*` 时 `path_allowed` 永远不匹配，模型对该帧的修复会被
        # `merge_authorized` 当作越权回填（真实运行实测到过：
        # `shot 1: 未授权路径已回填 ['frame_layouts.kfs.2.1.on_screen']`）。
        return f"frame_layouts.kfs.*.*.{leaf}"
    return f"frame_layouts.{frame_id}.*.{leaf}"


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[，。；！？]", text or "") if c.strip()]


def _norm_name(value) -> str:
    """布局里的角色名归一（`apply_char_alias` 幂等，与 char_layout 折叠同向）."""
    return apply_char_alias(str(value or "").strip())


# ---------------------------------------------------------------------------
# 1. frame_presence_consistency
# ---------------------------------------------------------------------------
# 离场/缺席标记。刻意**不含**「远处」「可见」「田埂」这类在场证据词——
# 反例「老农仍在远处田埂可见」不得被误判为已离场。
_EXIT_MARKERS = (
    "已走远", "已经走远", "走远了", "已远去", "已离去", "已离开", "离开了",
    "已退场", "退场", "已离场", "离场", "已消失", "不见踪影",
    # v6.115（审核）：「已倒下 / 倒地不起」**不是离场**——倒地的人仍在画面里，
    # 把它当离场词会把「尚在画面的倒地人物」误判成缺席（与 `frame_presence_consistency`
    # 的语义正好相反）。离场 = 离开画面，不是失去行动能力。
    "已没入", "已沉入", "沉入水底", "沉入水中",
    "已死", "已跑远", "已散去", "散去", "退下",
)


def _presence_issues(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    for frame_id in frame_ids(shot):
        entries = frame_entries(shot, frame_id)
        if not entries:
            continue
        names = {_norm_name(e.get("name")) for e in entries if e.get("name")}
        if not names:
            continue
        text = frame_text(shot, frame_id)
        if not text:
            continue
        # 别名表把「老农」折叠到布局里的「群演」——描述与布局用的是两个词，
        # 不折叠就永远对不上（目录 12 shot 1 LF 正是这个形态）。**匹配用原文名，
        # 归属判定用折叠名**：布局里存的是折叠后的规范名。
        raw_names = [str(e.get("name") or "").strip() for e in entries
                     if e.get("name")]
        candidates = list(dict.fromkeys(raw_names + list(CHARACTER_ALIASES)))
        for clause in _clauses(text):
            if not any(m in clause for m in _EXIT_MARKERS):
                continue
            for raw in candidates:
                if raw not in clause:
                    continue
                hit = _norm_name(raw)
                if hit not in names:
                    continue
                entry = next((e for e in entries
                              if _norm_name(e.get("name")) == hit), None)
                if entry is None or entry.get("on_screen") is False:
                    continue
                issues.append(Issue(
                    code="frame_presence_consistency", severity="visual_risk",
                    message=(f"{frame_id} 描述称「{clause}」，"
                             f"但布局中 {hit} 仍标记 on_screen=true"),
                    shot_id=shot_id, frame_id=frame_id, entity_id=hit,
                    field_path=_layout_field_path(frame_id, "on_screen"),
                    evidence=clause, repairable=True,
                ))
    return issues


# ---------------------------------------------------------------------------
# 2. prop_possession_consistency
# ---------------------------------------------------------------------------
# 释放标记。反例「手边放着酒壶」不含任何释放词——不得误判。
_RELEASE_MARKERS = (
    "已脱手", "脱手", "滑落", "掉落", "坠入", "坠地", "放下", "松开",
    "丢了", "掷出", "抛出", "撒手", "已空",
)


def _possession_issues(shot: dict, shot_id) -> list[Issue]:
    issues: list[Issue] = []
    for frame_id in frame_ids(shot):
        entries = frame_entries(shot, frame_id)
        if not entries:
            continue
        text = frame_text(shot, frame_id)
        if not text:
            continue
        release_clauses = [c for c in _clauses(text)
                           if any(m in c for m in _RELEASE_MARKERS)]
        if not release_clauses:
            continue
        for entry in entries:
            props = [str(p).strip() for p in (entry.get("props") or [])
                     if str(p).strip()]
            if not props:
                continue
            # 释放句里必须**点名**该道具——「他放下手里的东西」不含道具名时不判，
            # 那属于开放语义，交 final audit。
            bad = [p for p in props if any(p in c for c in release_clauses)]
            if not bad:
                continue
            clause = next(c for c in release_clauses if any(p in c for p in bad))
            issues.append(Issue(
                code="prop_possession_consistency", severity="visual_risk",
                message=(f"{frame_id} 描述称「{clause}」，"
                         f"但布局中 {_norm_name(entry.get('name'))} 仍持有 "
                         f"{'/'.join(bad)}"),
                shot_id=shot_id, frame_id=frame_id,
                entity_id=_norm_name(entry.get("name")),
                field_path=_layout_field_path(frame_id, "props"),
                evidence=clause, repairable=True,
            ))
    return issues


# ---------------------------------------------------------------------------
# 3. frame_stage_pose_consistency
# ---------------------------------------------------------------------------
# LF 是**结果帧**：pose 落在这些过程态词上，且同帧描述没有任何结果证据
# （落在/站定/已…）→ 阶段错误。
_PROCESS_POSE_MARKERS = (
    "纵身", "跃起", "腾空", "凌空", "半空", "飞掠", "扑向", "冲出",
    "冲上", "跃向", "起跳", "扑出", "窜出", "疾冲",
)
# 结果证据词：出现任一即认为该帧描述已交代结果态，不再判过程态。
_RESULT_MARKERS = (
    "落在", "落地", "站定", "稳住", "停住", "已落", "已到", "已站",
    "卧", "倒地", "跪", "坐", "躺", "伏", "立定", "稳稳", "踩在", "停在",
)


def _stage_pose_issues(shot: dict, shot_id) -> list[Issue]:
    frame_id = "lf"
    entries = frame_entries(shot, frame_id)
    if not entries:
        return []
    text = frame_text(shot, frame_id)
    if not text:
        return []
    issues: list[Issue] = []
    clauses = _clauses(text)
    for entry in entries:
        pose = str(entry.get("pose") or "")
        hit = next((m for m in _PROCESS_POSE_MARKERS if m in pose), "")
        if not hit:
            continue
        name = _norm_name(entry.get("name"))
        # 结果证据只在**该角色自己的分句**里找：同帧别的人「仰卧」不能证明
        # 这个角色的落点（目录 12 shot 5 LF 的「杨旭仰卧」就是这么把
        # 张十三的「纵身跃起」放过去的）。
        scoped = " ".join(c for c in clauses if name and name in c) or text
        if any(m in scoped for m in _RESULT_MARKERS):
            continue
        clause = next((c for c in clauses if name and name in c),
                      clauses[-1] if clauses else text)
        issues.append(Issue(
            code="frame_stage_pose_consistency", severity="visual_risk",
            message=(f"lf 为结果帧，但 {name or '角色'} 的 pose「{pose}」仍是过程态"
                     f"（{hit}），且描述「{clause}」未给出结果态"),
            shot_id=shot_id, frame_id=frame_id, entity_id=name,
            field_path=_layout_field_path(frame_id, "pose"),
            evidence=pose, repairable=True,
        ))
    return issues


# ---------------------------------------------------------------------------
# 4. result_action_support
# ---------------------------------------------------------------------------
# 每组是**同一个动作的若干表述**（同义/近义）。判据：LF 里出现了某组，
# 而 `motion_desc` 里该组一个表述都没有 → 时间线缺这段过程。
#
# 用「组」而不是「词」的原因：只按词匹配会把「耳光扇在脸上」判成未支撑——
# motion 写「扇出耳光」时动作明明在，只是**结果句式不同**（「扇在…脸上」是结果
# 语序）。总计划的要求是「LF 的结果动作必须在 motion 里有对应过程段」，
# 对应的是**动作**，不是措辞。
#
# 且只用多字、动作明确的表述，避免「推/拉/按」这类高频单字在无动作语境里误报。
_RESULT_ACTIONS: tuple = (
    ("耳光", "掌掴", "掴", "扇在", "扇了", "一掌"),
    ("一脚", "踹", "踢"),
    ("挥拳", "出拳", "一拳"),
    ("猛推", "推搡", "推向"),
    ("拽住", "揪住", "抓住", "扯住"),
    ("掐住", "勒住"),
    ("夺过", "抢夺"),
    ("摔在", "摔向", "掷出"),
    ("撞上", "撞在", "撞向"),
    ("刺入", "刺进", "刺中"),
    ("劈下", "砍下", "劈向"),
    ("搂住", "抱住", "抱起"),
    ("按倒", "按住", "压下"),
    ("击飞", "砸在", "砸向"),
)


def _result_action_issues(shot: dict, shot_id) -> list[Issue]:
    text = frame_text(shot, "lf")
    if not text:
        return []
    motion = str(shot.get("motion_desc") or "")
    kf_text = " ".join(frame_text(shot, f) for f in frame_ids(shot)
                       if f.startswith("kf"))
    clauses = _clauses(text)
    missing: list[str] = []
    seen: set[str] = set()
    for group in _RESULT_ACTIONS:
        if not any(w in text for w in group):
            continue
        if any(w in motion for w in group):
            continue
        action = next(w for w in group if w in text)
        clause = next((c for c in clauses if action in c), text[-80:])
        if clause in seen:
            continue
        seen.add(clause)
        missing.append(clause)
    if not missing:
        return []
    # **一帧一条 issue**（不是一词一条）：同一句结果里的「反手一掌 / 耳光扇在
    # 脸上」是同一个动作链，拆成多条只会虚增 blocking 计数，而
    # `_blocking_record_key` 本来就不含文案——多条同 key 的 issue 在账本里
    # 仍是一条。这里在**计数口径**上就对齐它。
    joined = " ".join(missing)
    covered = any(any(w in kf_text for w in group)
                  for group in _RESULT_ACTIONS if any(w in joined for w in group))
    return [Issue(
        code="result_action_support", severity="visual_risk",
        message=("lf 的结果动作「" + "」「".join(missing)
                 + "」在 motion_desc 中没有对应过程段"
                 + ("（KF 描述里有，但时间线上没有这段过程）" if covered
                    else "（KF 描述里也没有）")),
        shot_id=shot_id, frame_id="lf", entity_id="",
        field_path="motion_desc",
        evidence=" | ".join(missing), repairable=True,
    )]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
CROSS_FIELD_CODES = (
    "frame_presence_consistency",
    "prop_possession_consistency",
    "frame_stage_pose_consistency",
    "result_action_support",
)

CHECKS = (
    _presence_issues,
    _possession_issues,
    _stage_pose_issues,
    _result_action_issues,
)


def cross_field_issues(script: dict) -> tuple:
    """对整个脚本（含 hook）跑四类跨字段检查，返回 Issue 列表（不改写任何字段）."""
    issues: list[Issue] = []
    targets: list[tuple] = []
    hook = script.get("hook")
    if isinstance(hook, dict):
        targets.append(("hook", hook))
    for i, shot in enumerate(script.get("shots") or []):
        if isinstance(shot, dict):
            targets.append((shot.get("index", i + 1), shot))
    failures: list = []
    for shot_id, shot in targets:
        for check in CHECKS:
            try:
                issues.extend(check(shot, shot_id))
            except Exception as exc:
                # v6.111（审核 P1-2）：**不得静默放行**。
                #
                # 原实现 `except: continue` 的注释写的是「它只是补充信号」——但
                # 四类跨字段矛盾**只有这里**能确定性地看出来（总计划阶段 2 的
                # 全部意义）。任何新数据形态或本模块的回归都会让该镜头**跳过检测**，
                # 而质量门照样可能 PASS：fail-open。
                # 现在把失败如实上报，由 `collect_structured_issues` 置
                # `checker_failed=True` 并产生 blocking，门禁 fail-closed。
                failures.append(
                    "%s@shot%s: %s: %s" % (getattr(check, "__name__", "check"),
                                           shot_id, type(exc).__name__, exc))
    return issues, failures


__all__ = [
    "CROSS_FIELD_CODES",
    "cross_field_issues",
    "frame_entries",
    "frame_ids",
    "frame_text",
]
