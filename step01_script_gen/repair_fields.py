# -*- coding: utf-8 -*-
"""修复授权：把「完整镜头事务」从「整个对象都可以改」收紧到「只改被问题点名的字段」.

对应 `docs/step01-temporal-writers-rereview-2026-09-20.md` **发现 2（P0）**。

## 问题

`_apply_shot_replacements` 收的是**完整镜头对象**——这解决了「字段级补丁会让结构
字段互相不一致」的老问题，但把「完整对象传输」误当成了「完整对象都可以修改」。
运行时只挡住一份**固定**的 mask 字段（index/旁白/字幕/场景/地点/variation_type），
于是「本轮只要求修 prompt」时，replacement 仍可同时改写 `motion_desc`、
`temporal_plan`、`keyframes`、`ff/lf` 和 `frame_layouts`；只要固定 mask 未动、
未新增可识别 blocker，事务就通过。`temporal_plan` 进 revision 只能证明变化被
哈希记录，不能证明变化得到授权。

## 契约

1. **允许集合来自问题本身**：每个 target 的允许字段路径由它的 deterministic
   warning / audit issue 推导（`allowed_repair_fields`）。**推不出字段路径的问题
   不得走定向修复**——那类镜头交由整镜 regen，不在本模块兜底。
   例外：`result_action_support` / `kf_segment_missing` 这类**只能靠增删
   KF 修**的问题，授权面包含 **KF 列表本身**（`keyframes` / `frame_layouts.kfs`）
   ——详见 `_KF_STRUCTURAL_CODES`；配套确定性验收见 `kf_structural_guard`。
   （`kf_time_window_alignment` 2026-09-27 移出该列：它的缺段检测已并入
   `kf_segment_missing`，剩下的越窗/顺序只改叶子。）
2. **canonical 路径 diff**：replacement 与原镜做路径级比较，除允许路径与纯派生
   字段（`duration_sec` / `timecode`，由 `write_timeline_from_durations` 统一生成）
   外，任何差异整批拒绝。
3. **迁移期默认锁定**：`temporal_plan` / `motion_desc` / `keyframes` 与时长字段
   默认不可改，只有对应的时间问题**明确点名**它们时才放开。

## 为什么是「必要判据」而不是「充分判据」

本模块只回答「这次改动**有没有得到授权**」，不回答「改后的内容对不对」——后者仍由
`_validate_replacement_shot` 的结构校验、`_final_field_mask_violations` 的锁定
字段比较、逐镜 revision gate 与 quality gate 负责。授权判据刻意保持确定性：宁可
拒绝一次合法修复（行为是保留原镜 + 记录原因 + 交完整 regen），也不放行一次越权改写。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 迁移期默认**锁定**的字段（只有被问题明确点名才放开）
# ---------------------------------------------------------------------------
# 时间类：发现 2 实施方案 #3 —— 这些字段驱动拆镜/时长/KF 归属，默认锁死。
DEFAULT_LOCKED_FIELDS: tuple = (
    "temporal_plan", "motion_desc", "keyframes", "duration",
    "index", "narration_segment", "subtitle", "scene", "location", "variation_type",
)

# 纯派生字段：由 `timecode.write_timeline_from_durations` 从 `duration` 统一生成，
# 模型给的值不参与比较（发现 9 #2：不能两种口径并存）。
DERIVED_FIELDS: tuple = ("duration_sec", "timecode")


def _norm(path: str) -> str:
    """把 `keyframes[0].desc` / `keyframes.0.desc` 归一为 `keyframes.*.desc`."""
    p = str(path or "").strip()
    p = re.sub(r"\[(\d+)\]", r".\1", p)
    p = re.sub(r"\.\d+(?=\.|$)", ".*", p)
    return p


def path_allowed(path: str, allowed) -> bool:
    """`path` 是否落在允许集合内（支持父字段与 `*` 通配）.

    v6.119：允许条目**点名了具体下标**时（如 `keyframes.2.desc`），只匹配该下标
    ——「点名 kf2」不再等于「授权同镜所有 kf」。不含下标的条目行为**逐字不变**。
    """
    p = _norm(path)
    if not p:
        return True
    p_exact = _norm_keep_index(path)
    for a_raw in allowed:
        a_raw = str(a_raw or "").strip()
        if not a_raw:
            continue
        if _has_explicit_index(a_raw):
            a_exact = _norm_keep_index(a_raw)
            if p_exact == a_exact or p_exact.startswith(a_exact + "."):
                return True
            continue
        a = _norm(a_raw)
        if not a:
            continue
        if p == a or p.startswith(a + "."):
            return True
        if "*" in a:
            rx = "^" + re.escape(a).replace(r"\*", "[^.]+") + r"(\..*)?$"
            if re.match(rx, p):
                return True
    return False


# ---------------------------------------------------------------------------
# warning 文本 → 允许字段
# ---------------------------------------------------------------------------
# 顺序无关（取并集）。ASCII 关键词按「整词」匹配，避免 FF 命中随机子串。
_REPAIR_FIELD_KEYWORDS: tuple = (
    # ---- 时间类：只有被点名才放开（发现 2 实施方案 #3）----
    (("时长", "duration", "镜长"), ("duration",)),
    (("t_sec", "物理时刻", "时刻", "降序", "同刻", "堆叠", "时间窗", "时段",
      "时序", "倒装"), ("keyframes.*.t_sec",)),
    # ---- 关键帧文本 ----
    (("关键帧", "kf", "定格帧", "中间帧", "帧描述"), ("keyframes.*.desc",)),
    (("stage", "阶段标签"), ("keyframes.*.stage",)),
    # ---- 布局事实 ----
    (("frame_layouts", "布局", "站位", "在画", "画左", "画右", "画中", "视线",
      "gaze", "on_screen", "在场"), ("frame_layouts",)),
    (("char_layout", "角色布局"), ("char_layout",)),
    # ---- 画面文本 ----
    (("ff_desc",), ("ff_desc",)),
    (("lf_desc",), ("lf_desc",)),
    (("FF", "LF", "首帧", "尾帧", "ff", "lf"), ("ff_desc", "lf_desc")),
    (("prompt", "提示词"), ("prompt",)),
    (("motion_desc", "动作分段", "动作描", "motion"), ("motion_desc",)),
    # ---- 事件表 ----
    (("temporal_plan", "事件表", "事件窗口"), ("temporal_plan",)),
)

# 结构化 issue 的 code → 允许字段（`code` 命中时优先用它，比文本关键词准）
_REPAIR_CODE_FIELDS: dict = {
    "kf_time_out_of_shot": ("keyframes.*.t_sec",),
    "kf_time_missing": ("keyframes.*.t_sec",),
    "kf_time_not_strict": ("keyframes.*.t_sec",),
    "kf_time_too_close": ("keyframes.*.t_sec",),
    "kf_event_duplicate": ("keyframes.*.desc",),
    "kf_temporal_consistency": ("keyframes.*.t_sec", "keyframes.*.desc",
                                "keyframes.*.stage"),
    "kf_time_window_alignment": ("keyframes.*.t_sec", "keyframes.*.desc",
                                 "keyframes.*.stage"),
    "kf_frame_redundancy": ("keyframes.*.desc",),
    # v6.84 续（2026-09-21）：本 code 的 `field_path` 写作 `keyframes.desc`
    # （无下标），而 canonical diff 出来的是 `keyframes.*.desc` ——`path_allowed`
    # 不支持「父字段反向包含子路径」，所以单靠 `field_path` 授权必然被拒。
    # 显式登记规范形态，与同族的 `kf_time_window_alignment` 对齐。
    "kf_event_window_mismatch": ("keyframes.*.desc",),
    # v6.143（2026-09-27）：缺段只能靠**新增帧**修——列表增长 + 同序号
    # `frame_layouts.kfs` 条目，属联动修改（帧/时间/布局一起动才自洽）。
    "kf_segment_missing": ("keyframes.*.t_sec", "keyframes.*.desc",
                           "keyframes.*.stage", "frame_layouts"),
    "duration_two_calibers": ("duration",),
    "timecode_span_mismatch": ("duration",),
    "time_field_audit_write": ("duration",),
    "ff_lf_redundancy": ("ff_desc", "lf_desc"),
    "cross_shot_continuity": ("lf_desc",),
    "ff_desc_length": ("ff_desc",),
    "prompt_length_budget": ("prompt",),
    "text_integrity": ("prompt", "ff_desc", "lf_desc", "motion_desc"),
    "frame_layouts_required": ("frame_layouts",),
    "gaze_state_conflict": ("frame_layouts",),
    "weapon_holder": ("prompt", "ff_desc", "lf_desc"),
    "person_boat_position": ("prompt", "ff_desc", "lf_desc", "frame_layouts"),
    "multi_person_staging": ("prompt", "ff_desc", "lf_desc", "frame_layouts"),
    "shot_duration": ("duration",),
    # v6.103（2026-09-23 总计划阶段 2）：四类跨字段矛盾的**最小**授权面。
    #
    # 前两条**刻意留空**：它们要改的是「被点名的那一帧的 frame_layouts 条目」，
    # 而「哪一帧 + 哪个叶子」只写在 issue 的 `field_path`（如
    # `frame_layouts.lf.*.on_screen`）里，code 级映射写不出帧号与叶子。留空 +
    # 「结构化 issue 自带精确路径时不再解析文案关键词」→ 授权恰好落在那个叶子上，
    # 同帧其它叶子、其他帧、旁白与角色身份都不在授权内（总计划 §2.3「禁止顺带修改」）。
    "frame_presence_consistency": (),
    "prop_possession_consistency": (),
    # 「对应 frame 的 pose；必要时对应 desc」（总计划 §2.3 授权表）。
    "frame_stage_pose_consistency": ("lf_desc",),
    # motion_desc 与相关 KF；LF 结果事实、旁白、无关事件不在授权内。
    "result_action_support": ("motion_desc", "keyframes.*.desc",
                              "keyframes.*.stage", "keyframes.*.t_sec"),
    # v6.124（用户口径 (a) 窄版）：kf 描述明确可见的人物缺布局条目。
    # **刻意无 code 级映射**（v6.124.1，用户 2026-09-25 核对）：本检测器自己声明
    # 精确到帧的 `repair_targets`（见 `quality_issues._kf_person_visibility_issues`），
    # 成对模式**只**认那些条目。留一条 `keyframes.*.desc` 这样的通配映射，等于给
    # 「没有成对目标的同一 code」保留一个更宽的授权面，只会静默放宽——与「宁可
    # 拒绝一次合法修复」相反。推不出字段路径时按既有口径：该目标本轮不进定向修复。
}

# ---------------------------------------------------------------------------
# v6.104（2026-09-24 总计划 §1.1）：**必须增删 KF 才能修**的问题，授权面必须包含
# 「KF 列表本身」。
#
# 为什么：`merge_authorized` 对列表的规则是「列表路径**本身**未被授权 → 长度变化
# 一律回填原值」，而 kf 类授权此前**只有** `keyframes.*.desc/stage/t_sec` 这些叶子
# 路径。于是模型按修复 prompt 决策树（「报『kf 时段缺段』→ 按 motion_desc 时段表补
# 1 帧定格」）给出的**补帧正解**被静默回填：
#
#     实测（确定性验证）：
#       allowed = {keyframes.*.t_sec, keyframes.*.desc, keyframes.*.stage}
#       模型返回 3 个 kf → 合并后 len=2（整表回填），日志「未授权路径已回填 ['keyframes']」
#
# 后果：`kf_time_window_alignment`（检测 1：kfs 数 < 非空时段数）在旧授权面下
# **结构性不可解**——三轮真实重跑都停在 `resolved=0` → BLOCK，还被误记成「模型补帧
# 能力不足」。计划 §1.1 写的「默认锁定原 motion_desc，只允许**新增或调整**相关 KF」
# 从未落地，就是这一条。
#
# 授权列表 = 允许增删 KF，代价由配套的确定性验收补回（`kf_structural_guard`）：
#   ① KF 必须落在镜内且按时段单调；② `motion_desc` 的原事件文本必须仍在、顺序不变。
_KF_STRUCTURAL_CODES: tuple = (
    "result_action_support",      # LF 结果动作在 motion 里缺过程段 → 补过程段 + 补帧
    # 2026-09-27（用户裁决 3）：`kf_time_window_alignment` **已移出本列**。
    #
    # 它此前凭「检测 1：kfs 数 < 非空时段数 → 只能补帧」拿结构权限；而检测 1
    # 现在移出了该类别（缺段由 `kf_segment_missing` 单独阻塞，见
    # `validation._check_kf_time_window_alignment` 的 docstring）。本类别剩下的
    # 两类——越窗（orphan KF）与时段倒装——**都只需改叶子**（`keyframes.*.t_sec`
    # / `.desc`），不需要增删整张 KF 列表。继续挂在结构码上等于给同一 code 留一个
    # 更宽的授权面（可增删帧而问题并不需要），与总计划 §2.3「禁止顺带修改」相反。
    # 叶子授权仍由 `_REPAIR_CODE_FIELDS["kf_time_window_alignment"]` 给出。
    #
    # 2026-09-27（复核 1-1）：`kf_segment_missing` 同理——「某明确动作时段没有任何
    # 帧归属它」只能靠**新增帧**修，而成对目标是列表级（`keyframes` +
    # `frame_layouts.kfs`）。不登记在本列时，`validate_repair_targets(..., structural
    # =False)` 会把这两条目标全判成「不是该镜头的真实字段路径」→ 整条 issue 被跳过
    # → **质量门报得出、修复轮拿不到任何授权**（resolved=0 的结构性死路）。
    # 代价与 `result_action_support` 相同：授权 = 允许增删 KF，由
    # `kf_structural_guard`（镜内 + 单调 + motion 事件不丢）与「新增阻塞即回退」补回；
    # 布局没跟上会引入 `kf_layout_count_mismatch`（blocking）→ 整组回退。
    "kf_segment_missing",
    # ⚠️ v6.124.1（用户 2026-09-25 核对后收窄）：`kf_person_missing_in_layout`
    # **不在**此列。它的修法之一是「给 `frame_layouts.kfs[i]` 补一个角色条目」，
    # 曾据此按结构码授权「KF 列表本身」——但列表级授权同时放开了 `keyframes` 与
    # `frame_layouts.kfs` 的**整张列表**：用户用两帧最小样例复现，只报 kf0 缺人时
    # kf1 的描述与布局同样可改，超出窄检测器的本意。
    # 正解：该检测器自带**精确到帧**的成对目标（`keyframes[i].desc` +
    # `frame_layouts.kfs[i]`）。子列表下标被点名时，`merge_authorized` 是整棵子树
    # 替换（含列表长度变化）——补条目照样能落地，而**相邻帧不受影响**。
    # 代价：不再授权增删 `keyframes`（原本就不需要），`kf_structural_guard` 因此
    # 对本 code 不适用（闸门按 `'keyframes' in allowed` 判定，见
    # `main._kf_structural_violations`）——这是收窄，不是放松。
)

# 结构级路径：**列表本身**（不是叶子）。授权它们 = 允许改变列表长度。
_KF_STRUCTURAL_PATHS: tuple = ("keyframes", "frame_layouts.kfs")

# 文案回退（结构化 code 缺失时才走）里的「缺帧」信号。刻意收窄——宽泛关键词会
# 把「增删 KF」的权限发给无关镜头，而本模块的口径是「宁可拒绝一次合法修复」。
_KF_STRUCTURAL_KEYWORDS: tuple = ("缺段", "缺帧", "时段覆盖", "每段至少 1 帧",
                                  "每段生成 1 帧", "无任何 kf")

# 「矛盾**两侧**都可修」的 code（v6.103，2026-09-23 真实重跑实测）。
#
# 在场/持物这两类矛盾各有两侧：**帧描述**（ff/kf/lf desc）与 **frame_layouts
# 条目**。哪一侧是错的**不是确定性层能判的**——总计划 §1.2 的优先级把原文放在
# 最高位，只有拿到本镜原文才能定方向。实测（第二十次重跑）：
#
#   shot 2：ff_desc 写「钓杆已脱手垂湖面，酒壶已脱手」，而 frame_layouts.ff
#   仍列 props=[钓杆, 酒壶]；**原文**是「左手钓杆垂在湖面，右手提着一只酒壶」
#   —— 错的是描述（脱手写早了），不是布局。只授权布局侧时，模型给出的正解
#   （改 ff_desc）被 `merge_authorized` 回填，修复轮 resolved=0 → 只能 BLOCK。
#
# 所以这两个 code 额外授权**同帧的描述字段**：方向交给模型按原文切片决定，确定性
# 层只负责「矛盾必须消失」（修复后重跑同一检测）与「不许越权改别处」。
# **证据保留闸门只对 `result_action_support` 生效**（那一条的方向是计划写死的：
# 保留 LF 结果、补 motion 过程），在场/持物两条不设该闸门——否则会把原文支持的
# 正解判成「删证据」。
_PAIRED_FRAME_DESC_CODES = (
    "frame_presence_consistency",
    "prop_possession_consistency",
)


def _audit_props_paths(fp: str, frame_id: str) -> tuple:
    """审计 `field_path` 含 `props` 时的**两侧**授权（v6.113）.

    审计给出的路径有时是「描述字段 + `.props`」这种**不在 shot schema 里的组合路径**
    （实测 `ff_desc.props`，意思是「ff_desc 提到的道具与布局不一致」）。它既不等于
    任何真实字段、也无法前缀匹配任何字段，于是授权集成了**一条死路径**：

      实测（第三十次真实重跑）：
        [REPAIR] shot 2: 未授权路径已回填 4 处:
                 ['core_keywords.3','core_keywords.4','frame_layouts.ff.0.props','prompt']
      模型**想**去补 `frame_layouts.ff.0.props`（布局里没列 ff_desc 提到的钓杆/酒壶），
      但按 `ff_desc.props` 推不出这条路径 → 回填 → 矛盾依旧 → 审计每轮重报同一条，
      两轮预算耗尽 → BLOCK。

    道具状态是**两侧**的事（描述与布局条目谁错，由原文切片决定；与
    `_PAIRED_FRAME_DESC_CODES` 同一原则），所以两侧都授权。
    """
    out: list = []
    base = _norm(str(fp or "").split(".")[0].strip())
    if base in ("ff_desc", "lf_desc", "motion_desc", "prompt"):
        out.append(base)
    fid = str(frame_id or "")
    if fid == "ff":
        out.append("frame_layouts.ff.*.props")
    elif fid == "lf":
        out.append("frame_layouts.lf.*.props")
    elif fid.startswith("kf"):
        out.append("frame_layouts.kfs.*.*.props")
    else:
        # 帧未知 → 三处都放开（矛盾可能落在任意帧；收窄会重演「死路径」）
        out.extend(("frame_layouts.ff.*.props", "frame_layouts.lf.*.props",
                    "frame_layouts.kfs.*.*.props"))
    return tuple(out)


def _desc_path_for_frame(frame_id: str) -> str:
    """该帧对应的**描述字段**路径（与 `cross_field_checks.frame_text` 同源）."""
    fid = str(frame_id or "")
    if fid == "ff":
        return "ff_desc"
    if fid == "lf":
        return "lf_desc"
    if fid.startswith("kf"):
        return "keyframes.*.desc"
    return ""


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z_]+")


def _narrow(allowed: set) -> set:
    """丢掉被更具体路径覆盖的**宽前缀**（v6.103）.

    `frame_layouts`（宽）与 `frame_layouts.lf`（具体）同时出现在授权集里时，
    宽的那条会让模型获得「改这一镜**所有帧**布局」的权限——而跨字段 issue
    的语义是「只改被点名的那一帧」。收窄后授权恰好落在具体路径上。

    只在**存在更具体后代**时丢前缀，所以 `{frame_layouts}`、`{ff_desc, lf_desc}`、
    `{keyframes.*.t_sec, keyframes.*.desc}` 这类同层集合一个都不动。
    收窄的代价是可能拒绝一次「本来合法但要改宽字段」的修复——按本模块
    「宁可拒绝一次合法修复」的既定口径，这个方向是安全的。
    """
    return {p for p in allowed
            if not any(q != p and q.startswith(p + ".") for q in allowed)}


def _with_kf_structural(allowed: set, structural: bool) -> set:
    """把「KF 列表本身」并入授权集（v6.104，总计划 §1.1）.

    必须在 `_narrow` **之后**调用：`_narrow` 丢的是「有更具体后代的宽前缀」，
    而 `keyframes` 正是 `keyframes.*.desc` 的宽前缀——先并进去会被它丢掉。

    并进来之后反过来丢掉**结构级路径的后代**：列表被授权时叶子已被包含，
    留在集合里只会让 REPAIR FEEDBACK 的 `allowed_fields` 与日志更难看懂。
    """
    if not structural:
        return allowed
    out = {p for p in allowed
           if not any(p == s or p.startswith(s + ".") for s in _KF_STRUCTURAL_PATHS)}
    out.update(_KF_STRUCTURAL_PATHS)
    return out


def _text_hits(text: str, keyword: str) -> bool:
    t = str(text or "")
    if not t:
        return False
    if keyword.isascii():
        for m in _ASCII_TOKEN_RE.finditer(t):
            if m.group(0) == keyword:
                return True
        return False
    return keyword in t


def _fields_from_text(text: str) -> set:
    out = set()
    for keywords, fields in _REPAIR_FIELD_KEYWORDS:
        if any(_text_hits(text, kw) for kw in keywords):
            out.update(fields)
    return out


def _is_model_audit_code(code) -> bool:
    """该 issue 是否来自模型审计（`run_same_model_audit_with_status` 统一加前缀）.

    用途：模型审计的成对目标是**契约的一部分**，缺/空/无效一律不授权、不退回旧的
    定位字段推导（用户口径：**不必兼容旧模型审计产物**——审计版本已变，旧结论本就
    不可复用）。确定性质量门的 issue 没有这个契约，仍走原有推导路径。
    """
    return str(code or "").startswith("model_audit_")


def _is_structural_code(code) -> bool:
    """「必须增删 KF 才能修」的问题码判据（**前缀感知**）.

    模型审计的 code 统一带 `model_audit_` 前缀（见 `_is_model_audit_code`），直接与
    `_KF_STRUCTURAL_CODES`（小写裸名）比对**必然不命中**。这里剥掉前缀后精确比对，
    不做大小写折叠——宽匹配会把「增删 KF」的权限发给不该拿的镜头。
    """
    c = str(code or "")
    if c.startswith("model_audit_"):
        c = c[len("model_audit_"):]
    return c in _KF_STRUCTURAL_CODES


# `shot5` / `shot_5` / `shot-5` / `shot 5` —— 同一件事的写法差异，一律归一到 `shot_5`。
_SHOT_ALIAS_RE = re.compile(r"^shot[\s_\-]*([0-9]+)$", re.IGNORECASE)


def _is_canonical_shot_id(s: str) -> bool:
    """规范写法 = 稿件里的镜头序号裸数字（`"5"`）；钩子写 `"0"` 或 `"hook"`."""
    return s in ("0", "hook") or (s.isdigit() and s == str(int(s)))


def _shot_index_of(shot_id) -> str:
    """任何来源的 `shot_id` → **镜头序号裸串**（钩子 → `"hook"`）；指不明时原样返回.

    v6.120（2026-09-25 真实重跑取证）：同一份提示词、temperature=0.0，提交第 1 轮的
    审计写 `"5"`，第 2 轮写 `"shot5"`（`step01_audit_rounds.json` 逐轮实拍）。旧实现
    只会拼出 `shot_shot5` → `validate_repair_targets` 判「该镜头不存在于本稿」→ 5 条
    blocking **整条拒绝** → 提交点契约门 BLOCK，两条修复轮一次没花。

    为什么这层归一不是「猜授权」：本项目的镜头身份恒为 `index` 十进制数（`main` 建
    `shots_by_id`、`_issue_target_shot` 查镜头都只有一个写法），**`shot_shot5` 不可能是
    任何合法镜头**——`shot5 ≡ shot 5` 是纯记法差异，映射双射、不改变指向；与「从
    `minimal_fix` 文案里反推字段路径」（那是猜，已否决）是两件事。既有同取向先例：
    `main._norm_target_id`（v6.84）已在**修复响应**一侧宽容 `shot_10` vs `10`。

    仍然 fail-closed：`shot_5.1` / `shot_x` / `第5镜` 这类**真指不明**的写法原样返回，
    调用方拼出的 target 名不存在 → 整条拒绝。
    """
    s = str(shot_id if shot_id is not None else "").strip()
    if s.lower() in ("hook", "0"):
        return "hook"
    if s.isdigit():
        return str(int(s))
    _m = _SHOT_ALIAS_RE.match(s)
    if _m:
        return str(int(_m.group(1)))
    return s


def _target_id_of(shot_id) -> str:
    """任何来源的 `shot_id` → 事务 target 名（`shot_5` / `hook`）——**唯一**口径."""
    _idx = _shot_index_of(shot_id)
    return "hook" if _idx == "hook" else f"shot_{_idx}"


def shot_id_aliases(records) -> list:
    """审计条目里**非规范镜头号写法**的归一记录（`"shot5" → "shot_5"`），供日志观测.

    归一本身发生在 `_target_id_of`（双射，指向不变）；这里只负责让它**可见**——
    「模型换了一种记法」属于输入形态变化，不该静默发生（准则 7：宁可显式暴露）。
    只做观测，不参与任何判定。
    """
    out: list = []
    seen: set = set()
    for rec in (records or []):
        if not isinstance(rec, dict):
            continue
        raws = [rec.get("shot_id")]
        for pair in (rec.get("repair_targets") or []):
            if isinstance(pair, dict):
                raws.append(pair.get("shot_id"))
        for raw in raws:
            s = str(raw if raw is not None else "").strip()
            if not s or _is_canonical_shot_id(s):
                continue
            note = f"{s}→{_target_id_of(s)}"
            if note not in seen:
                seen.add(note)
                out.append(note)
    return out


def _norm_keep_index(path: str) -> str:
    """`keyframes[2].desc` → `keyframes.2.desc`（**保留**显式下标，不折叠成 `*`）.

    与 `_norm` 的分工：`_norm` 是**授权匹配**用的通配形态（点名字段种类）；
    本函数是**存在性校验**与**下标精确授权**用的形态（点名字段种类 + 具体下标）。
    """
    return re.sub(r"\[(\d+)\]", r".\1", str(path or "").strip())


def _has_explicit_index(path: str) -> bool:
    """该路径是否点名了具体下标（如 `keyframes.2.desc`）."""
    return any(seg.isdigit() for seg in _norm_keep_index(path).split("."))


def _path_exists_in_shot(path: str, shot: dict) -> bool:
    """逐段走进镜头对象：字典按下标键、列表要求**真实存在**的下标.

    为什么不能用 `flatten_paths` + 归一比对（v1 实现，2026-09-25 实测的漏洞）：
    `flatten_paths` 的键是**通配**形态（`keyframes.*.desc`），而归一比对本就把
    `[2]` 折成 `*` —— 于是 `keyframes[99].desc`（该镜只有 3 个 kf）、
    `frame_layouts.kfs[9][0].props` 都**通过校验**，凭空给出一条不存在的目标。
    """
    cur = shot
    for _seg in _norm_keep_index(path).split("."):
        if not _seg:
            return False
        if isinstance(cur, dict):
            if _seg not in cur:
                return False
            cur = cur[_seg]
        elif isinstance(cur, list):
            if not _seg.isdigit():
                return False
            _i = int(_seg)
            if _i < 0 or _i >= len(cur):
                return False
            cur = cur[_i]
        else:
            return False
    return True


def _is_real_leaf(path: str, shot: dict, *, structural: bool = False) -> bool:
    """`path` 是否是**该镜头对象里真实存在**的字段路径（**精确**匹配，非前缀）.

    判据用 `flatten_paths(shot)`——授权路径必须能在这份镜头数据里找到对应项，
    否则它是一条**死路径**：进得了授权集，`canonical_path_diff` 里永远没有匹配项。

    ⚠️ 必须**逐段走进对象**，不能复用 `path_allowed`（授权用的前缀语义，会让
    `ff_desc.props` 被当成 `ff_desc` 的子路径），也不能只用 `flatten_paths` 做
    归一比对（通配形态会把 `keyframes[99].desc` 也放过）。

    通配符例外：模型显式写了 `*`（如 `keyframes[*].desc`）时退回**形状**校验——
    那是它自己声明的通配授权，不存在「下标指错」的问题；此时仍要求该形状在该镜里
    存在（`flatten_paths` 归一比对）。

    结构级列表路径（`keyframes` / `frame_layouts.kfs`）只在 `structural=True` 时放行
    （授权它们 = 允许改列表长度，属 `_KF_STRUCTURAL_CODES` 的配套能力）。
    """
    if not path:
        return False
    _p_exact = _norm_keep_index(path)
    _is_structural_path = any(_p_exact == _norm_keep_index(s)
                              for s in _KF_STRUCTURAL_PATHS)
    if _is_structural_path:
        return bool(structural) and _path_exists_in_shot(_p_exact, shot)
    if "*" in str(path):
        # 模型显式通配：只校形状（下标不存在的问题由它自己承担）
        return _norm(path) in {_norm(k) for k in flatten_paths(shot)}
    return _path_exists_in_shot(_p_exact, shot)


def validate_repair_targets(pairs, shots_by_id: dict, *, structural: bool = False) -> tuple:
    """逐项校验 `repair_targets`：**镜头存在** + `field_path` 是该镜头的**真实字段路径**.

    Returns `(valid, rejected)`：
      - `valid`：`[{"shot_id","field_path"}]`，顺序保留；
      - `rejected`：`["<target>/<path>（原因）"]`，供调用方**显式**说明为什么没授权。

    v6.119（2026-09-25 用户决定）：「只写**该镜头里真实存在**的字段」是成对目标的
    硬约束。校验不通过**不做任何回退猜测**（不从 `minimal_fix` 反推路径），直接剔除；
    一条 issue 的条目全被剔除时该 issue **不贡献任何授权**——与本模块既有的
    「宁可拒绝一次合法修复，也不放行一次越权改写」同一条口径。
    """
    valid: list = []
    rejected: list = []
    for item in (pairs or []):
        if not isinstance(item, dict):
            rejected.append(f"{item!r}（不是成对对象）")
            continue
        sid = str(item.get("shot_id") or "").strip()
        fp = str(item.get("field_path") or "").strip()
        tid = _target_id_of(sid)
        shot = (shots_by_id or {}).get(tid)
        if not sid:
            rejected.append(f"<?>/{fp or '<空>'}（缺 shot_id）")
        elif shot is None:
            rejected.append(f"{tid}/{fp or '<空>'}（该镜头不存在于本稿）")
        elif not fp:
            rejected.append(f"{tid}/<空>（缺 field_path）")
        elif not _is_real_leaf(fp, shot, structural=structural):
            rejected.append(f"{tid}/{fp}（不是该镜头的真实字段路径）")
        else:
            valid.append({"shot_id": sid, "field_path": fp})
    return valid, rejected


def allowed_repair_fields(warnings, audit_issues=None, *,
                          target_shot_id: str | None = None,
                          shots_by_id: dict | None = None) -> set:
    """从 warnings + audit issues 推导允许修改的镜头字段路径集合.

    Args:
        warnings: 本轮该 target 的告警文本列表（deterministic checker 输出）
        audit_issues: 该 target 相关的 `Issue` 对象列表（可选）
        target_shot_id: 本 target 的 target 名（`shot_5` / `hook`）。给了它才启用
            **成对目标模式**（v6.119）；不给 = 旧行为（逐字不变，其他调用方不受影响）。
        shots_by_id: `{target 名: 镜头对象}`，成对模式校验路径真实性用。

    **成对目标模式**（v6.119，2026-09-25 用户决定）：issue 带 `repair_targets` 时，
    **只**认成对目标里属于本镜的那些 `field_path`——`code→字段`表、`field_path`、
    文案关键词**全部不参与**。理由：一条问题往往要跨镜联动修改（同一事实在
    shot5 与 shot7-9 的 `ff_desc` 里互相矛盾），而「问题定位在哪」与「要改哪里」
    是两件事；拿定位字段当授权面会把整体缺陷切成半条（实测 shot 5：正解要改
    `keyframes[2].desc` + `keyframes[2].stage` + `frame_layouts.kfs.2.0.props`
    三处，只授权第一处 → 两处被回填 → 半条修复引入新阻塞 → 整镜回退、缺陷零进展）。
    issue **没有** `repair_targets` 时（旧审计产物 / 模型写空数组）仍走原有推导。

    例外：`_KF_STRUCTURAL_CODES` 仍会并入**结构级列表路径**（`keyframes` /
    `frame_layouts.kfs`）——「必须增删 KF 才能修」的问题在叶子路径下结构性不可解
    （见 `_KF_STRUCTURAL_CODES` 的说明），这不是「顺带改字段」而是同一个修复动作。

    Returns:
        允许路径集合（已归一，如 `keyframes.*.t_sec`）。**空集表示「没有明确
        字段路径」**——调用方应据此拒绝该镜的定向修复，改走整镜 regen。
    """
    allowed = set()
    # v6.119：成对目标派生的路径**保留下标**，单独收在这里 —— 它只授权点名的那个
    # 下标（`keyframes.2.desc` 不覆盖 kf0/kf1）。旧路径（code 表 / field_path /
    # 文案关键词）仍进 `allowed` 走通配形态，行为与之前逐字一致。
    allowed_exact: set = set()
    # v6.104：本轮是否出现「必须增删 KF 才能修」的问题（决定是否授权 KF 列表本身）
    structural = False
    _pairs_mode = target_shot_id is not None and shots_by_id is not None
    for issue in audit_issues or []:
        code = str(getattr(issue, "code", "") or "")
        fp = str(getattr(issue, "field_path", "") or "")
        # ---- v6.119：成对目标模式（只认成对，不看 code/定位字段/文案）----
        # 三态（`Issue.repair_targets`）：
        #   None → 模型审计：契约未遵守 → **不授权**（提交点显式 BLOCK）；
        #          确定性 issue：走下面的兼容路径；
        #   []   → **显式**「无修复目标」→ 不授权（含**不**授予结构码的 KF 列表能力，
        #          否则「模型说这条不修」会经由 code 又拿回一个更宽的授权面）；
        #   非空 → **任一条无效则整条拒绝**（不留半组继续修）；全部有效时只认属于
        #          本镜的那些。
        _declared = getattr(issue, "repair_targets", None)
        if _pairs_mode and (_is_model_audit_code(code) or _declared is not None):
            if not _declared:
                continue
            _valid, _rejected = validate_repair_targets(
                _declared, shots_by_id, structural=_is_structural_code(code))
            if _rejected:
                # 联动问题只要有一条目标无效，就拒绝**整条**——半组修出来的东西
                # 自相矛盾（shot 5 实测就是这么被回退的）
                continue
            if _valid and _is_structural_code(code):
                # 「必须增删 KF 才能修」的配套结构能力（见 `_KF_STRUCTURAL_CODES`）：
                # 只在**确有**有效成对目标时给出，且随后由 `kf_structural_guard` 验收
                structural = True
            for _p in _valid:
                if _target_id_of(_p["shot_id"]) == target_shot_id:
                    # **保留下标**（`keyframes.2.desc`）——点名 kf2 就只授权 kf2。
                    # 旧的 `code`/`field_path`/文案关键词路径仍走通配形态（零变化）。
                    allowed_exact.add(_norm_keep_index(_p["field_path"]))
            continue
        if _is_structural_code(code):
            structural = True
        allowed.update(_REPAIR_CODE_FIELDS.get(code, ()))
        if fp and fp != "global":
            allowed.add(fp)
            # v6.113：审计路径含 props → 布局条目的 props 同样授权（两侧，见上）
            if "props" in fp.lower():
                _pp = _audit_props_paths(fp, getattr(issue, "frame_id", ""))
                allowed.update(_pp)
                if _pp and fp not in _pp:
                    # 组合路径（`ff_desc.props`）**本身不是 schema 路径**，留着它只有
                    # 一个后果：`_narrow` 会把真正的 `ff_desc` 当成「有更具体后代的
                    # 宽前缀」丢掉——授权反而更窄。映射已经覆盖它（布局侧由
                    # `frame_layouts.<frame>.*.props` 覆盖），所以丢掉。
                    # ⚠️ 仅当映射**不含** `fp` 时才丢：确定性检测器给的就是真实路径
                    # （`frame_layouts.ff.*.props`），映射产出同一条时丢弃会把授权删空。
                    allowed.discard(fp)
            if code in _PAIRED_FRAME_DESC_CODES:
                # 矛盾的另一侧：同帧描述（方向由原文决定，见上）
                _paired = _desc_path_for_frame(getattr(issue, "frame_id", ""))
                if _paired:
                    allowed.add(_paired)
            # v6.103：结构化 issue **自带精确字段路径**时不再从文案里解析关键词。
            # 文案是给人看的——「关键帧2 描述称…」里的「关键帧」会把授权面撑到
            # `keyframes.*.desc`，而这条 issue 真正要改的是 `frame_layouts.*` 上的
            # 一个叶子。总计划 §2.3 明确要求「禁止顺带修改其他帧」，所以具体路径
            # 优先于文案关键词（与本模块「code 命中时优先用它，比文本关键词准」
            # 同一条原则）。
            continue
        allowed.update(_fields_from_text(str(getattr(issue, "message", "") or "")))
    for w in warnings or []:
        text = str(w)
        if any(_text_hits(text, kw) for kw in _KF_STRUCTURAL_KEYWORDS):
            structural = True
        allowed.update(_fields_from_text(text))
    return (_with_kf_structural(_narrow({_norm(a) for a in allowed if a}), structural)
            | {a for a in allowed_exact if a})


# ---------------------------------------------------------------------------
# canonical 路径 diff
# ---------------------------------------------------------------------------
class _Missing:
    def __repr__(self):  # pragma: no cover - 仅诊断
        return "<missing>"


_MISSING = _Missing()


def flatten_paths(obj, prefix: str = "") -> dict:
    """把嵌套 dict/list 拍平成 `{规范路径: 值}`（list 元素用 `*` 归一）."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = prefix + "." + str(k) if prefix else str(k)
            out.update(flatten_paths(v, key))
    elif isinstance(obj, list):
        # 含结构元素的列表按元素拍平并记录长度；纯标量列表整体比较
        if any(isinstance(x, (dict, list)) for x in obj):
            for x in obj:
                out.update(flatten_paths(x, prefix + ".*" if prefix else "*"))
            out[(prefix or "") + ".__len__"] = len(obj)
        else:
            out[prefix] = list(obj)
    else:
        out[prefix] = obj
    return out


def canonical_path_diff(before, after, *, ignore=DERIVED_FIELDS,
                        tol: float = 0.011) -> list:
    """两个镜头对象之间的**规范路径差异**（浮点用容差比较）.

    Args:
        ignore: 允许差异的派生字段前缀（默认 `duration_sec` / `timecode`）
        tol: 数值容差——时长会经 `_align_frame_duration` 定点到 2 位小数，
             不该把定点差当成「改了时长」
    """
    a, b = flatten_paths(before), flatten_paths(after)
    out = []
    for path in sorted(set(a) | set(b)):
        if any(path == ig or path.startswith(ig + ".") for ig in ignore):
            continue
        va, vb = a.get(path, _MISSING), b.get(path, _MISSING)
        if va is _MISSING or vb is _MISSING:
            out.append(path)
            continue
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) \
                and not isinstance(va, bool) and not isinstance(vb, bool):
            if abs(float(va) - float(vb)) <= tol:
                continue
        if va != vb:
            out.append(path)
    return out


def _same_value(a, b) -> bool:
    """值是否等价（浮点用 0.011 容差，与 `canonical_path_diff` 同口径）."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float))             and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 0.011
    return a == b


def merge_authorized(target: dict, new: dict, allowed) -> tuple:
    """只保留 `new` 在**授权路径**上的改动，其余一律取 `target` 的原值.

    返回 `(合并后的完整对象, 被回填的路径列表)`。

    v6.91（用户决定，2026-09-22）：把「未授权路径 = 原样」从**判据**变成**构造保证**。

    为什么需要：replacement 契约要求返回**完整**镜头对象（24 字段全必填，schema 层
    强约束），而授权只覆盖其中少数几个路径。模型把未授权字段的措辞写得略有不同
    （哪怕同义）→ `check_replacement_authorized` 判越权 → **整条**替换被拒。
    实测 2026-09-22 07:39：3/3 目标因此被拒，修复轮产出 0；而修复 prompt 已经两处
    写了「镜头内未被 warn 授权的字段逐字保留」，模型不遵守（与发现 14 同类）。

    安全性：合并后与 `target` 的差异**只**落在授权路径内——正是
    `check_replacement_authorized` 要求的那条判据，由构造成立。原判据保留为
    **纵深防御**（它能抓到本函数自身的 bug）。

    规则：
      - 路径被授权 → 用 `new` 的值（整棵子树，含为修 kf 缺段而增删列表元素的情况）；
      - 路径未被授权但**有后代被授权** → 递归下去，逐叶子判定；
      - 否则 → 用 `target` 的值；
      - `new` 缺字段 → 用 `target` 的（结构完整性兜底）；
      - 列表：整表被授权 → 用 `new` 的；长度变了但未授权 → 回填 `target` 的
        （长度变化是结构改动，不在字段级授权范围内）。
    """
    import copy as _c

    def _note(echoed: list, path: str, changed: bool) -> None:
        """只记**确实被改过**的路径——否则「值本来就相同」的字段会塞满日志
        （实测 `index` 这类恒定字段每次都被记，日志失真）。"""
        if changed:
            echoed.append(path)

    def _walk(t, n, prefix: str, echoed: list):
        if isinstance(t, dict):
            if not isinstance(n, dict):
                _note(echoed, prefix, True)
                return _c.deepcopy(t)
            out = {}
            for k, tv in t.items():
                cp = f"{prefix}.{k}" if prefix else str(k)
                if k not in n:
                    _note(echoed, cp, True)
                    out[k] = _c.deepcopy(tv)
                else:
                    out[k] = _walk(tv, n[k], cp, echoed)
            for k in n:                      # new 多出的键 = 未授权新增 → 丢弃
                if k not in t:
                    _note(echoed, f"{prefix}.{k}" if prefix else str(k), True)
            return out
        if isinstance(t, list):
            if not isinstance(n, list):
                _note(echoed, prefix, True)
                return _c.deepcopy(t)
            if path_allowed(prefix, allowed):
                return _c.deepcopy(n)
            if len(t) != len(n):
                _note(echoed, prefix, True)  # 长度变化 = 结构改动，未授权 → 回填
                return _c.deepcopy(t)
            return [_walk(tv, nv, f"{prefix}.{i}", echoed)
                    for i, (tv, nv) in enumerate(zip(t, n))]
        if path_allowed(prefix, allowed):
            return _c.deepcopy(n)
        _note(echoed, prefix, not _same_value(t, n))
        return _c.deepcopy(t)

    echoed: list = []
    merged = _walk(target, new, "", echoed)
    return merged, echoed


def check_replacement_authorized(before: dict, after: dict, allowed) -> str:
    """replacement 是否**只**改了被授权的字段；返回空串=通过，否则返回拒绝原因.

    `allowed` 为空集时按「只允许纯派生字段变化」处理——即等同于拒绝一切内容改动
    （调用方应先判定「无明确字段路径 ⇒ 不走定向修复」）。
    """
    allowed = {_norm(x) for x in (allowed or set())}
    bad = [p for p in canonical_path_diff(before, after)
           if not path_allowed(p, allowed)]
    if not bad:
        return ""
    return "越权改动（本轮只授权 %s）：%s" % (sorted(allowed) or ["（无）"], bad[:6])
