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
from step01_script_gen.weapon_states import asserted_marker, prop_state_owner


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
            return str((shot.get("keyframes") or [])[int(frame_id[2:])].get("desc") or "")
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
#
# 2026-09-27（用户裁决 2）：把「**正在**离开画面」与「**已经**离开画面」分开。
#
# 判别样本是冻结稿 shot 12：FF「庄仆们转身退下，张十三站在船头，目送他们」、
# kf0「张十三站在船头，目送庄仆们退下，庄仆们边走边哄笑」——人物**此刻仍在画面
# 里**（边走边哄笑）；LF 才写「庄仆们消失在夜色中」。旧判据把裸词「退下」当已
# 离场，于是审计（`OFFSCREEN_ACTIVE_STATE@12`）要求把 FF/kf0 的 `on_screen`
# 改成 true 时，本检查立刻判出新阻塞 → `shot_12` 整组回退 → 该镜两轮都修不掉。
#
# 现在：只有**完成态**才算已离开画面——
#   · 绝对形式（`_EXIT_DONE`）：已离开/不见踪影/沉入水底 …；
#   · 完成标记形式（`_EXIT_DONE_RE`）：`已|已经 + 动词`、`动词 + 了/完毕`、
#     `消失在/于 …`。
# 裸动词（`_EXIT_IN_PROGRESS`）只说明「正在离开」，**不算**离场，也就不再与
# `on_screen=true` 冲突。
_EXIT_DONE = (
    "已走远", "已经走远", "走远了", "已远去", "已离去", "已离开", "离开了",
    "已退场", "已离场", "已消失", "不见踪影", "已散去",
    # v6.115（审核）：「已倒下 / 倒地不起」**不是离场**——倒地的人仍在画面里，
    # 把它当离场词会把「尚在画面的倒地人物」误判成缺席（与 `frame_presence_consistency`
    # 的语义正好相反）。离场 = 离开画面，不是失去行动能力。
    "已没入", "已沉入", "沉入水底", "沉入水中",
    "已跑远",
)

#: 进行中的离场动作词（**单一来源**：下面的完成态正则由它拼出）。单用它们只说明
#: **正在**离开画面（人还在画面里），必须与完成标记同现才算「已离开画面」。
_EXIT_IN_PROGRESS = ("走远", "远去", "离去", "离开", "退下", "退场", "离场",
                     "散去", "消失")

_EXIT_DONE_RE = re.compile(
    r"(?:已|已经)\s*(?:" + "|".join(_EXIT_IN_PROGRESS) + r")"
    r"|(?:" + "|".join(_EXIT_IN_PROGRESS) + r")\s*(?:了|完毕|干净)"
    r"|消失\s*(?:在|于)"
)


def _exit_is_completed(clause: str) -> bool:
    """该分句是否陈述「**已经**离开画面」（完成态）.

    裸的「正在离开」动词（`_EXIT_IN_PROGRESS`）不算——判别样本 shot 12：
    FF/kf0「转身退下，边走边哄笑」人物仍在画面里；LF「消失在夜色中」才是已离开。
    """
    if any(m in clause for m in _EXIT_DONE):
        return True
    return bool(_EXIT_DONE_RE.search(clause))


def _completed_exit_subjects(clause: str, candidates: list[str]) -> set[str]:
    """只绑定紧邻完成态谓词的显式主体，不把宾语、观察者或衣物归为离场人。"""
    names = sorted((n for n in candidates if n), key=len, reverse=True)
    if not names:
        return set()
    name_pattern = "(?:" + "|".join(re.escape(n) for n in names) + ")"
    subjects = name_pattern + r"们?(?:\s*(?:和|与|及|、)\s*" + name_pattern + r"们?)*"
    predicate = "(?:" + "|".join(re.escape(m) for m in _EXIT_DONE) + "|" + _EXIT_DONE_RE.pattern + ")"
    pattern = re.compile(r"(?P<subjects>" + subjects + r")(?:的?身影)?\s*(?:均|都)?\s*" + predicate)
    return {name for match in pattern.finditer(clause)
            for name in re.findall(name_pattern, match.group("subjects"))}


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
            # 只有**完成态**（已离开画面）才算离场；「正在退下」的人物仍在画面里，
            # 与 `on_screen=true` 不矛盾（2026-09-27 用户裁决 2，判别样本见
            # `_EXIT_DONE` 上方注释）。
            if not _exit_is_completed(clause):
                continue
            for raw in _completed_exit_subjects(clause, candidates):
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
                           if asserted_marker(c, _RELEASE_MARKERS)]
        if not release_clauses:
            continue
        for entry in entries:
            props = [str(p).strip() for p in (entry.get("props") or [])
                     if str(p).strip()]
            if not props:
                continue
            # 释放句里必须**点名**该道具——「他放下手里的东西」不含道具名时不判，
            # 那属于开放语义，交 final audit。
            names = list({_norm_name(e.get("name")) for e in entries}
                         | {_norm_name(e.get("name")) for e in shot.get("char_layout") or []
                            if isinstance(e, dict)})
            bad = [p for p in props if any(p in c for c in release_clauses)
                   and prop_state_owner(text, p, names,
                       [_norm_name(e.get("name")) for e in entries
                        if p in (e.get("props") or [])], _RELEASE_MARKERS)
                   == _norm_name(entry.get("name"))]
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


def result_action_evidence_groups(clause: str) -> tuple:
    """证据分句命中的**结果动作组**（每组 = 可互相替代的若干表述）.

    单源 `_RESULT_ACTIONS`——不另建词表，也不引入新阈值。

    为什么要有这一层：`result_action_support` 的 evidence 是**含结果动词的整个
    分句**（见 `_result_action_issues` 里的 `clause`），逐字要求它留存等于禁止
    任何改写。2026-09-29 实测（第一章第 3 轮）：审计自己的 `minimal_fix` 明令把
    「寒光短刃刺中杨旭心口」改写为「短刃已刺入心口，刺客推臂后踞蹲持刃」，照办的
    修复被逐字闸门判成「删证据」→ 按目标回退 → 下一轮再报同一条，三轮空转在
    同一个镜头上。

    判据取向：`result_action_support` 的触发条件是「LF 里出现某组某表述」——
    要让检测器闭嘴就**必须**把这组表述从 LF 里清掉。所以「该组一个表述都不剩」
    正好等价于「让检测器闭嘴」，而「还留着一个同义表述」就是合法改写。

    **必须逐组判定**（2026-09-29 复核反例）：`_clauses` 只在 `，。；！？` 切分，
    所以同一分句可以带两个动作（「刺中杨旭心口、踹倒听香」）。把命中的组**合并**
    成一次 `any()` 会让「踹倒整段被删掉」蒙混过关——合并是错的，调用方必须对
    返回的**每一组**分别要求「还剩至少一个表述」。

    **未覆盖（同一复核指出，本函数不声称覆盖）**：**当事角色/受事对象的替换**。
    「刺中杨旭心口」改成「刺中听香心口」在本口径下**通过**——动作组词汇都在。
    确定性层没有「同一事件的对象身份」可核验依据；而语义侧（审计）自己会明令
    改写当事表述（实测第 3/4 轮要求把「神秘人」统一称「刺客」、把受事名略去），
    一刀切要求人名留存会把审计的正解重新判死。要拦「换人」须另行定义可核验的
    依据（原文/计划的当事角色），**不在本闸门范围内**——缺口见
    `tests/step01_script_gen/test_cross_field_checks.py` 的 xfail 反例。

    返回 `()` = 该分句里没有可识别的结果动作 → 调用方**必须**退回逐字口径
    （不能因为「认不出」就放行）。
    """
    return tuple(group for group in _RESULT_ACTIONS
                 if any(w in clause for w in group))


# ---------------------------------------------------------------------------
# 5. weapon_hand_conflict (2026-11-06)
# ---------------------------------------------------------------------------
#: 明确的「手别 + 持握/挥动动作」写法。只认这一类，开放语义交给审计。
_HAND_HOLD_RE = re.compile(r"(左|右)手[^，。；！？]{0,8}?(?:持|握|拿|攥|扬|举|挥|提|刺|捅|扎|抵|顶|架|按)")
#: 显式换手动作——出现即说明左右手变化是**剧情动作**，不判矛盾。
_HAND_SWITCH = ("换手", "交到左", "交到右", "换到左", "换到右",
                "改用左", "改用右", "递到左", "递到右", "从左手", "从右手")


def _weapon_hand_issues(shot: dict, shot_id) -> list[Issue]:
    """同镜**同一武器**被写成「左手…」又写成「右手…」的跨字段矛盾（纯检测）.

    审计（model_audit_STATE_CHAIN_CONFLICT）能看出来，确定性层此前看不见：实测
    f6 的 ff_desc/关键帧写「左手短刃」，motion_desc 写「右手寒光短刃扬起」，两条
    口径互相矛盾，生成时模型只能随机选一个。

    判据（刻意收窄，避免误报）：
      - 同一**子句**里同时出现「手别词 + 持握动作」与**同一个武器词**；
      - 左右两种手别指向**同一武器**才判矛盾（左手持箫 + 右手持剑不是矛盾）；
      - `motion_desc` 里出现显式换手动作时整镜放行（剧情真的换了手）。

    只报告 + 给**成对授权目标**（冲突涉及的全部字段），不写任何字段。
    """
    from step01_script_gen.prompt_semantic.registry import WEAPON_KEYWORDS

    texts = [("ff_desc", shot.get("ff_desc"))]
    texts += [(f"keyframes.{i}.desc", (kf or {}).get("desc"))
              for i, kf in enumerate(shot.get("keyframes") or []) if isinstance(kf, dict)]
    texts += [("motion_desc", shot.get("motion_desc")), ("lf_desc", shot.get("lf_desc"))]
    motion = str(shot.get("motion_desc") or "")
    if any(mark in motion for mark in _HAND_SWITCH):
        return []

    by_weapon: dict = {}
    for field, raw in texts:
        text = str(raw or "")
        if not text:
            continue
        for clause in _clauses(text):
            weapons = [w for w in WEAPON_KEYWORDS if w and w in clause]
            if not weapons:
                continue
            for match in _HAND_HOLD_RE.finditer(clause):
                hand = match.group(1)
                for weapon in weapons:
                    by_weapon.setdefault(weapon, {"左": [], "右": []})[hand].append((field, clause))

    conflicts = []
    for weapon, sides in by_weapon.items():
        if sides["左"] and sides["右"]:
            conflicts.append((weapon, sides["左"], sides["右"]))
    if not conflicts:
        return []

    # 同一镜只出**一条**：不同武器词可能落在同一批子句里（「短刃」与「刀」同时命中），
    # 逐词各报一条会让同一次修复在阻塞表里重复计票。授权面取并集。
    targets: list = []
    for _weapon, left, right in conflicts:
        for field, _clause in left + right:
            item = {"shot_id": shot_id, "field_path": field}
            if item not in targets:
                targets.append(item)
    weapons = "、".join(w for w, _l, _r in conflicts)
    left0, right0 = conflicts[0][1][0], conflicts[0][2][0]
    return [Issue(
        code="weapon_hand_conflict", severity="visual_risk",
        message=(f"同一镜内「{weapons}」被写成两种手别："
                 f"左手见 {left0[0]}「{left0[1][:28]}」，"
                 f"右手见 {right0[0]}「{right0[1][:28]}」；"
                 f"motion_desc 没有换手动作，两条口径无法同时执行"),
        shot_id=shot_id, frame_id="", entity_id="",
        field_path=left0[0],
        evidence=(left0[1][:100] + " / " + right0[1][:100]),
        repair_targets=targets, repairable=True,
    )]


# ---------------------------------------------------------------------------
# 6. 跨镜光线/时段倒流（M2.2，2026-11-09）
# ---------------------------------------------------------------------------
#: 明确的时间段词 → 单调序号。只收**确定性**时段词；「天色渐暗」这类过程
#: 描述不排时间，避免把合法过渡判成倒流。
_TIME_OF_DAY = (
    (0, ("清晨", "黎明", "拂晓", "破晓", "日出", "早晨", "晨光", "朝阳")),
    (1, ("上午", "日上三竿")),
    (2, ("正午", "中午", "晌午", "烈日当空")),
    (3, ("下午", "午后")),
    (4, ("黄昏", "傍晚", "日落", "夕阳", "夕照", "晚霞", "暮色")),
    (5, ("夜晚", "入夜", "夜色", "月光", "月色", "星夜", "深夜", "半夜",
         "烛光", "灯火")),
)
#: 显式时间跳跃/回忆标记：出现即说明相邻镜不是连续时间，不做倒流判定。
_TIME_JUMP_MARKERS = (
    "回忆", "闪回", "倒叙", "数日前", "数年前", "次日", "翌日", "第二天",
    "隔日", "多年后", "三日后", "半个月后", "一个月后", "时间流逝",
    "与此同时", "同一时间", "另一处", "转场",
)
#: 日/夜光线的互斥族（只判这两族，晨昏之间的连续变化不判矛盾）。
_DAYLIGHT_WORDS = ("阳光", "日光", "白昼", "天光大亮", "烈日", "晴空", "日照")
_NIGHT_WORDS = ("月光", "月色", "夜色", "星夜", "灯火", "烛光", "漆黑", "夜幕")
_STATE_RESET_MARKERS = (
    "已起身", "被扶起", "整理后", "换过", "重新站起", "伤已包扎", "卸下",
    "放下", "交出", "丢弃", "方才起身", "这才站起",
)
#: 相邻镜状态衔接：上镜 LF 终态 → 下镜 FF 初态不得无依据复原。
_STATE_RESET_PAIRS = (
    (("倒地", "倒伏", "仰倒", "僵卧", "伏地", "倒地不起"),
     ("站立", "站起", "起身", "端坐", "跪坐", "跪立", "跃起")),
    (("已无神", "双目无神", "气绝", "身亡", "死去"),
     ("睁眼", "睁目", "开口", "说话", "站起", "起身")),
    (("浑身湿透", "湿透", "浸透", "衣袍滴水"),
     ("干爽", "衣袍干燥", "滴水未沾")),
    (("空手", "手中无物", "赤手空拳", "两手空空"),
     ("持刀", "握刀", "持剑", "握剑", "手持", "提着", "攥着")),
)


def _time_ordinal(text: str) -> int | None:
    """该文本里最先出现的**明确时段**序号；没有则 None."""
    text = str(text or "")
    best = None
    for order, words in _TIME_OF_DAY:
        for word in words:
            pos = text.find(word)
            if pos < 0:
                continue
            if best is None or pos < best[0]:
                best = (pos, order)
    return None if best is None else best[1]


def _light_family(text: str) -> str:
    text = str(text or "")
    if any(w in text for w in _NIGHT_WORDS):
        return "night"
    if any(w in text for w in _DAYLIGHT_WORDS):
        return "day"
    return ""


def _cross_shot_time_issues(script: dict) -> list[Issue]:
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    issues: list[Issue] = []
    for prev, curr in zip(shots, shots[1:]):
        if str(prev.get("scene") or "") != str(curr.get("scene") or ""):
            continue
        if str(prev.get("location") or "") != str(curr.get("location") or ""):
            continue
        prev_text = str(prev.get("lf_desc") or prev.get("ff_desc") or "")
        curr_text = str(curr.get("ff_desc") or curr.get("lf_desc") or "")
        if not prev_text or not curr_text:
            continue
        if any(mark in curr_text for mark in _TIME_JUMP_MARKERS):
            continue
        prev_t, curr_t = _time_ordinal(prev_text), _time_ordinal(curr_text)
        if prev_t is not None and curr_t is not None and curr_t < prev_t:
            issues.append(Issue(
                code="cross_shot_time_reversal", severity="advisory",
                message=(f"相邻镜同一场景时段倒流：上镜 LF 时段序号 {prev_t}，"
                         f"本镜 FF 时段序号 {curr_t}（无回忆/时间跳跃依据）"),
                shot_id=curr.get("index"), frame_id="ff",
                field_path="ff_desc", evidence=curr_text[:60],
                repairable=False,
            ))
            continue
        prev_l, curr_l = _light_family(prev_text), _light_family(curr_text)
        if {prev_l, curr_l} == {"day", "night"}:
            issues.append(Issue(
                code="cross_shot_lighting_conflict", severity="advisory",
                message=(f"相邻镜同一场景光线族冲突：上镜 LF={prev_l}，本镜 FF={curr_l}"
                         f"（无时间过渡/回忆依据）"),
                shot_id=curr.get("index"), frame_id="ff",
                field_path="ff_desc", evidence=curr_text[:60],
                repairable=False,
            ))
    return issues


def _cross_shot_state_issues(script: dict) -> list[Issue]:
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    issues: list[Issue] = []
    for prev, curr in zip(shots, shots[1:]):
        if str(prev.get("scene") or "") != str(curr.get("scene") or ""):
            continue
        if str(prev.get("location") or "") != str(curr.get("location") or ""):
            continue
        lf = str(prev.get("lf_desc") or "")
        ff = str(curr.get("ff_desc") or "")
        if not lf or not ff:
            continue
        if any(mark in ff for mark in _STATE_RESET_MARKERS):
            continue
        for before, after in _STATE_RESET_PAIRS:
            hit_before = next((w for w in before if w in lf), "")
            hit_after = next((w for w in after if w in ff), "")
            if hit_before and hit_after:
                issues.append(Issue(
                    code="adjacent_state_break", severity="advisory",
                    message=(f"邻镜状态衔接无过渡：上镜 LF 为「{hit_before}」，"
                             f"本镜 FF 直接「{hit_after}」且无起身/整理/换手依据"),
                    shot_id=curr.get("index"), frame_id="ff",
                    field_path="ff_desc", evidence=ff[:60],
                    repairable=False,
                ))
                break
    return issues


# ---------------------------------------------------------------------------
# 7. 镜内运镜冲突（M2.2）
# ---------------------------------------------------------------------------
_CAMERA_OPPOSITES = (
    (("推近", "推进", "推镜", "向前推"), ("拉远", "拉后", "拉镜", "向后拉")),
    (("左摇", "向左摇", "左移", "向左移", "向左横移"),
     ("右摇", "向右摇", "右移", "向右移", "向右横移")),
    (("上升", "升镜", "升高", "向上摇"), ("下降", "降镜", "降低", "向下摇")),
    (("顺时针环绕", "顺旋"), ("逆时针环绕", "逆旋")),
)
#: 分段标记：同一镜内分段运镜是合法的，不判冲突。必须用**显式分段句式**
#: （先…再… / 前N秒 / 随后），不能用裸「前/后/秒」——它们会命中「向前推」
#: 「镜头后拉」这类正常运镜词，把真冲突全部跳过。
_CAMERA_SEGMENT_RE = re.compile(
    r"(?:先|再|随后|然后|接着|之后|随即|片刻后|紧接着|第[一二三四]段|前段|后段"
    r"|前\s*\d+(?:\.\d+)?\s*秒|后\s*\d+(?:\.\d+)?\s*秒)")
_FIXED_CAMERA = ("固定机位", "固定镜头", "机位固定", "静止镜头", "镜头固定")


def _camera_conflict_issues(shot: dict, shot_id) -> list[Issue]:
    camera = str(shot.get("camera_movement") or "")
    motion = str(shot.get("motion_desc") or "")
    text = (camera + "\n" + motion).strip()
    if not text:
        return []
    issues: list[Issue] = []
    for left, right in _CAMERA_OPPOSITES:
        l_hit = next((w for w in left if w in text), "")
        r_hit = next((w for w in right if w in text), "")
        if not (l_hit and r_hit):
            continue
        # 同一镜显式写「先…再…」是合法分段运镜；只有两侧都出现在**同一连续
        # 指令段**（无分段标记）时才是冲突。
        if _CAMERA_SEGMENT_RE.search(motion):
            continue
        l_in_camera = any(w in camera for w in left)
        r_in_camera = any(w in camera for w in right)
        evidence = camera if (l_in_camera and r_in_camera) else motion
        issues.append(Issue(
            code="intra_shot_camera_conflict", severity="visual_risk",
            message=(f"同一镜内运镜互斥：「{l_hit}」与「{r_hit}」同时出现，"
                     f"且 motion_desc 没有先/再分段，模型无法同时执行"),
            shot_id=shot_id, frame_id="",
            field_path="camera_movement" if evidence is camera else "motion_desc",
            evidence=evidence[:100], repairable=True,
        ))
        break
    fixed = next((w for w in _FIXED_CAMERA if w in camera), "")
    if fixed:
        move = next((w for w in _CAMERA_OPPOSITES[0][0] + _CAMERA_OPPOSITES[0][1]
                     + _CAMERA_OPPOSITES[1][0] + _CAMERA_OPPOSITES[1][1]
                     + ("跟拍", "环绕", "摇镜", "俯冲", "拉升") if w in motion), "")
        if move and not _CAMERA_SEGMENT_RE.search(motion):
            issues.append(Issue(
                code="intra_shot_camera_conflict", severity="visual_risk",
                message=(f"camera_movement 为「{fixed}」，但 motion_desc 要求「{move}」；"
                         f"固定机位与运动镜头互斥"),
                shot_id=shot_id, frame_id="",
                field_path="camera_movement", evidence=f"{fixed} / {move}",
                repairable=True,
            ))
    return issues


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
CROSS_FIELD_CODES = (
    "frame_presence_consistency",
    "prop_possession_consistency",
    "frame_stage_pose_consistency",
    "result_action_support",
    "weapon_hand_conflict",
    "cross_shot_time_reversal",
    "cross_shot_lighting_conflict",
    "adjacent_state_break",
    "intra_shot_camera_conflict",
)

CHECKS = (
    _presence_issues,
    _possession_issues,
    _stage_pose_issues,
    _result_action_issues,
    _weapon_hand_issues,
    _camera_conflict_issues,
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
    # M2.2：跨镜检查需要**相邻镜对**，不能放进逐镜检查表。失败同样计入
    # `failures`（fail-closed），不能因为异常而静默跳过跨镜检测。
    for check in (_cross_shot_time_issues, _cross_shot_state_issues):
        try:
            issues.extend(check(script))
        except Exception as exc:
            failures.append("%s: %s: %s" % (getattr(check, "__name__", "check"),
                                            type(exc).__name__, exc))
    return issues, failures


__all__ = [
    "CROSS_FIELD_CODES",
    "cross_field_issues",
    "frame_entries",
    "frame_ids",
    "frame_text",
]
