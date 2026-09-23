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
    """`path` 是否落在允许集合内（支持父字段与 `*` 通配）."""
    p = _norm(path)
    if not p:
        return True
    for a in allowed:
        a = _norm(a)
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
}

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z_]+")


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


def allowed_repair_fields(warnings, audit_issues=None) -> set:
    """从 warnings + audit issues 推导允许修改的镜头字段路径集合.

    Args:
        warnings: 本轮该 target 的告警文本列表（deterministic checker 输出）
        audit_issues: 同 target 的 `Issue` 对象列表（可选）

    Returns:
        允许路径集合（已归一，如 `keyframes.*.t_sec`）。**空集表示「没有明确
        字段路径」**——调用方应据此拒绝该镜的定向修复，改走整镜 regen。
    """
    allowed = set()
    for issue in audit_issues or []:
        code = str(getattr(issue, "code", "") or "")
        fp = str(getattr(issue, "field_path", "") or "")
        allowed.update(_REPAIR_CODE_FIELDS.get(code, ()))
        if fp and fp != "global":
            allowed.add(fp)
        allowed.update(_fields_from_text(str(getattr(issue, "message", "") or "")))
    for w in warnings or []:
        allowed.update(_fields_from_text(str(w)))
    return {_norm(a) for a in allowed if a}


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
