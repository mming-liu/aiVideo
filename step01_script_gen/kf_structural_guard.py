# -*- coding: utf-8 -*-
"""KF 结构修复的确定性验收闸门（v6.104，2026-09-24 总计划 §1.1）.

## 为什么需要

`repair_fields` 从 v6.104 起，对「必须**增删 KF**才能修」的问题（时段覆盖缺段 /
LF 结果动作缺过程段）授权 **KF 列表本身**（`keyframes` / `frame_layouts.kfs`）。
不授权列表时，`merge_authorized` 的规则是「列表路径未被授权 → 长度变化一律回填
原值」，于是模型按修复 prompt 决策树给出的正解
（「报『kf 时段缺段』→ 按 motion_desc 时段表补 1 帧定格」）被**静默回填**：

    实测（第二十三次重跑前的确定性验证）：
      allowed = {keyframes.*.t_sec, keyframes.*.desc, keyframes.*.stage}
      模型返回 3 个 kf → 合并后 len=2（整表被回填）

后果是 `kf_time_window_alignment`（检测 1：kfs 数 < 非空时段数）在旧授权面下
**结构性不可解**——三轮重跑都停在 `resolved=0`，并被误记成「模型补帧能力不足」。

## 授权列表的代价由本模块补回

允许增删 KF 之后，必须防止「靠增删/改写时段让覆盖检测闭嘴」。总计划 §1.1 写死了
两条验收，本模块就是它们：

  1. `kf_order_violations`       —— KF 必须落在镜内且**按时段单调**（增删后不得乱序）。
     对外入口按**差分**口径用（只拦本轮新引入/恶化的，见 `kf_structural_violations`）；
     绝对口径仍由质量门在最终提交处施加；
  2. `motion_event_preservation` —— `motion_desc` 的原事件文本必须**仍在、顺序不变**。
     这就是 §1.1 的「motion_desc + keyframes 原子候选」验收：模型**可以**补事件
     （shot 7 的正解就是补「张十三丢下灯盏，纵身跃上船梢」），但不可以删事件或
     换顺序——那是「靠改检测输入让告警归零」，不是修复。

两条都**只读**：不写任何字段，只比较「本轮之前 vs 本轮之后」的镜头对象。
"""
from __future__ import annotations

import re
from collections import Counter as _Counter

from shared.kf_time import get_kf_t_sec
from shared.motion_segments import parse_segments, split_segment_texts

# 归一后仍短于该长度的事件片段不参与「是否仍在」判定（避免标点残片/单字残渣
# 产生假违例）。取 3：中文事件短语通常 >= 3 字，而解析残留几乎都是 1-2 字；
# 阈值定高了会把「乙闷哼」这类短事件漏掉，等于给「删事件」留后门。
_MIN_EVENT_CHARS = 3
_KEEP_RE = re.compile(r"[^\w]+", re.UNICODE)
_TOL = 0.011


def _norm(text: str) -> str:
    """只留字词（去标点/空白/时间标签残渣）——判「同一段话还在不在」."""
    return _KEEP_RE.sub("", str(text or ""))


def _shot_duration(shot: dict) -> float:
    try:
        dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0
    return dur if dur > 0 else 0.0


def _kf_times(shot: dict) -> list:
    """按**列表顺序**取每帧物理时刻（缺 t_sec → None，非 dict 元素跳过）."""
    kfs = shot.get("keyframes")
    if not isinstance(kfs, list):
        return []
    out: list = []
    for kf in kfs:
        if not isinstance(kf, dict):
            continue
        t = get_kf_t_sec(kf, shot)
        out.append(None if t is None else float(t))
    return out


def kf_order_violation_items(shot: dict) -> list:
    """结构化违例项 `(身份, 文案)`——**身份与帧下标无关**.

    为什么身份不能带下标：增删帧会移位（`kf2` 可能变 `kf3`），同一处违例的文案随之
    变化；差分验收若按文案/下标做集合相减，就会把「同一处旧违例」误判成新引入。
    身份取自**违例实质**：
      - `missing_t`                        —— 无 t_sec（同型按多重集计）
      - `out_of_window|t|镜长`             —— 越窗的那一帧（t 被改大 = 恶化 = 新）
      - `non_monotonic|当前|前一`          —— 非单调的那对时刻
    """
    out: list = []
    dur = _shot_duration(shot)
    times = _kf_times(shot)
    for i, t in enumerate(times):
        if t is None:
            out.append((("missing_t",), "kf%d 缺 t_sec" % i))
        elif dur > 0 and (t <= 0 or t > dur + _TOL):
            out.append((("out_of_window", round(float(t), 3), round(dur, 3)),
                        "kf%d t_sec=%g 越出镜长 %g" % (i, t, dur)))
    known = [t for t in times if t is not None]
    for i in range(1, len(known)):
        if known[i] < known[i - 1] - _TOL:
            out.append((("non_monotonic", round(float(known[i]), 3),
                         round(float(known[i - 1]), 3)),
                        "KF 物理时刻非单调：第 %d 帧 %g 早于前一帧 %g"
                        % (i, known[i], known[i - 1])))
            break
    return out


def kf_order_violations(shot: dict) -> list:
    """**绝对口径**：KF 物理时刻必须落在 `(0, 镜长]` 且按列表顺序**非递减**.

    回答「这份稿此刻干不干净」。最终提交侧由质量门施加同一口径
    （`quality_issues.kf_time_out_of_shot` / `kf_time_not_strict` 等 hard 项）；
    修复事务侧用**差分口径** `kf_structural_violations`（只问「本轮改坏了没有」）。
    """
    return [msg for _, msg in kf_order_violation_items(shot)]


def _role_of(text: str, pos: int, names) -> tuple:
    """动作词位置 `pos` 处的 `(施事, 受事)`：取**最近**的前置角色名与后置角色名.

    无 NLP 依赖的确定性代理——中文动作句里施事在动词前、受事在动词后：
    「杨旭踹倒刺客」→ (杨旭, 刺客)；「刺客踹倒杨旭」→ (刺客, 杨旭)。
    两侧都取「最近的」一个，够区分**换人**与**倒装**这类错改写。
    """
    # **倒装识别**（v6.152，复核反例）：中文的「把/被」句会颠倒施事受事的相对位置——
    #   · 「A把B+动词」→ 施事 A（把之前最近的名）、受事 B（把与动作之间的名）；
    #   · 「A被B+动词」→ 施事 B（被之后的名）、受事 A（被之前的名）。
    # 不做这一步，「杨旭把刺客踹倒」会被读成施事=刺客，于是「刺客踹倒」被判成关系一致。
    _head = text[:pos]
    for _marker, _swap in (("把", False), ("被", True)):
        _mi = _head.rfind(_marker)
        if _mi < 0:
            continue
        _pre = [(abs(_mi - _head.rfind(n, 0, _mi)), n) for n in names
                if _head.rfind(n, 0, _mi) >= 0]
        _mid = [(text.find(n, _mi + 1, pos), n) for n in names
                if text.find(n, _mi + 1, pos) >= 0]
        _a = min(_pre)[1] if _pre else ""
        _b = min(_mid)[1] if _mid else ""
        return (_b, _a) if _swap else (_a, _b)
    _before = [(abs(pos - text.rfind(n, 0, pos)), n) for n in names
               if text.rfind(n, 0, pos) >= 0]
    _after = [(text.find(n, pos + 1), n) for n in names
              if text.find(n, pos + 1) >= 0]
    agent = min(_before)[1] if _before else ""
    patient = min(_after)[1] if _after else ""
    return agent, patient


def _group_relation_ok(group, new_text: str, names: set, novel_window: str) -> bool:
    """**某一个**动作组：在原文里找到「动作词同组 且 施事/受事一致」的从句才算成立."""
    from step01_script_gen.cross_field_checks import _clauses
    from step01_script_gen.validation import normalize_for_grounding

    _words = [w for w in group if w in new_text]
    if not _words:
        return False
    _pos = min(new_text.find(w) for w in _words)
    _want = _role_of(new_text, _pos, names)
    for cl in _clauses(novel_window):
        ncl = normalize_for_grounding(cl)
        _hit = [w for w in group if w in ncl]
        if not _hit:
            continue
        _p = min(ncl.find(w) for w in _hit)
        if _role_of(ncl, _p, names) == _want:
            return True
    return False


def _motion_relation_ok(new_text: str, canonical, novel_window: str,
                        strict: bool = False) -> tuple:
    """核实改后段的**动作及其施事/受事关系** → `(是否成立, 原因)`.

    v6.150（复核反例）：上一版只核「同一从句里出现动作词与人名」，**不是关系**——
    离线复现两例都放行：①原文「杨旭踹倒刺客」→ 改稿「刺客踹倒杨旭」（施事受事对调）；
    ②原文只有「杨旭刺中刺客」，改稿追加「又踹倒刺客」（多动作组只要求任一组有出处）。
    现在三条同时要求：
      ① **必须绑定本镜原文事件**：`strict=True`（本镜台词能定位到原文窗口）。
         定位不到时窗口是整个章/全书，**不能**作为改写依据 → 一律合回。这条会放弃
         一部分真纠错（本镜无台词的镜头永远拿不到采纳），取舍见 `narrow_motion_patch`；
      ② **逐个动作组**核，不是任一组核到就通；
      ③ 每组必须在原文里找到「同组动作词 **且 施事/受事一致**」的从句（`_role_of`）。
    """
    from step01_script_gen.cross_field_checks import result_action_evidence_groups

    if not strict:
        return False, "本镜原文事件绑定不上（窗口为整章回落）——无法核实施事/受事，按合回处理"
    if not str(novel_window or "").strip():
        return False, "拿不到原文文本，动作与当事关系无法确认"
    groups = result_action_evidence_groups(new_text)
    if not groups:
        return False, "改写后仍未写出可核验的结果动作"
    names = {str(n) for n in canonical if n and str(n) in str(new_text)}
    for g in groups:
        if not _group_relation_ok(g, new_text, names, novel_window):
            return False, "动作「%s」在原文里找不到施事/受事一致的出处" % (g[0] if g else "?")
    return True, ""


def narrow_motion_patch(before_shot, after_shot, *, canonical=(),
                        novel_window: str = "", window_strict: bool = False) -> tuple:
    """把**未通过出处关系校验**的 motion 时段按基线原样合回 → `(新 motion_desc, 说明)`.

    v6.149（复核建议，取代 v6.146~.148 的三条放宽规则）：保留既有**完整镜头事务**
    （不恢复已停用的字段补丁通道），由**代码**做范围收窄——只接收「目标时段」的改写，
    其余时段逐字沿用基线。目标时段 = 与基线不同**且**通过 `_motion_relation_ok`
    （同一原文事件中的动作与施事/受事）的时段。

    这样既不必再放宽「削减」判据（被合回就等于没改），也消除了模型重写相邻时段
    造成的误拦：**改错了不会被采纳，而不是把整轮判死**。

    ⚠️ **取舍（v6.150，复核决定）**：`window_strict=False`（本镜台词定位不到、窗口
    回落整章）时**一律不采纳**——拿整章当依据无法核实施事/受事，实测能放行
    「杨旭踹倒刺客」→「刺客踹倒杨旭」这类错改写。代价：本镜无对白的镜头（如 shot_8）
    永远拿不到 motion 纠错采纳，相关确定性项会留到终局 blocking——**宁可如实 BLOCK，
    不可静默采纳无法核实的改写**（准则 7）。

    Returns:
        `(new_motion_desc | None, notes)`：
          - `None` = **结构不可安全合并**（段数变了 / 解析不出分段）→ 交调用方按违例处理；
          - `str`  = 合并后的 motion_desc（与基线逐字相同则**原样返回基线串**，避免标签
            被重排成数字区间这种无谓改动）。
    """
    from shared.motion_segments import build_motion_segments, format_motion_segments

    if not isinstance(before_shot, dict) or not isinstance(after_shot, dict):
        return None, []
    before_motion = str(before_shot.get("motion_desc") or "")
    if not before_motion.strip():
        return None, []
    bs = build_motion_segments(before_shot)
    as_ = build_motion_segments(after_shot)
    if len(bs) < 2:
        return None, []                       # 未显式分时段 → 无「事件文本」可锁
    if len(as_) != len(bs):
        return None, ["段数由 %d 变 %d，纠错不许增删段" % (len(bs), len(as_))]
    merged: list = []
    notes: list = []
    for i, (b, a) in enumerate(zip(bs, as_)):
        if _norm(b.get("text")) == _norm(a.get("text")):
            merged.append(b)
            continue
        ok, why = _motion_relation_ok(str(a.get("text") or ""), canonical,
                                     novel_window, strict=window_strict)
        if ok:
            merged.append(a)
            notes.append("第 %d 段按出处关系采纳改写" % i)
        else:
            merged.append(b)
            notes.append("第 %d 段改写未通过校验（%s）→ 按基线原样合回" % (i, why))
    if all(_norm(b.get("text")) == _norm(m.get("text")) for b, m in zip(bs, merged)):
        return before_motion, notes           # 没有任何时段被采纳 → 连标签都不动
    return format_motion_segments(merged, "zh"), notes


def motion_event_preservation(before_shot, after_shot) -> list:
    """`motion_desc` 的原事件文本必须仍在、顺序不变（总计划 §1.1）.

    只在原 `motion_desc` **显式分时段**（>= 2 段）时判定——未分时段时没有
    「事件文本」可锁，返回空表（不影响非动作镜）。

    v6.149：**回到逐字口径**。此前为「合法改写」加的放宽规则（v6.146~.148）已由
    `narrow_motion_patch` 取代——授权轮里未获采纳的改写由**代码合回基线**，所以走到
    这里时，合规差异已不存在；本函数只兜「段数/文本真的丢了」这类结构问题。
    """
    if not isinstance(before_shot, dict) or not isinstance(after_shot, dict):
        return []
    before_motion = str(before_shot.get("motion_desc") or "")
    if not before_motion.strip():
        return []
    dur = _shot_duration(before_shot) or _shot_duration(after_shot)
    if dur <= 0:
        return []
    try:
        segs = parse_segments(before_motion, dur, implicit_tail=True, clamp=True)
    except Exception:
        return []
    if len(segs) < 2:
        return []
    try:
        texts = split_segment_texts(before_motion, segs, dur)
    except Exception:
        return []
    hay = _norm(after_shot.get("motion_desc") or "")
    out: list = []
    last = -1
    for i, text in enumerate(texts):
        key = _norm(text)
        if len(key) < _MIN_EVENT_CHARS:
            continue
        pos = hay.find(key)
        if pos < 0:
            out.append("原事件文本丢失（第 %d 段）：「%s」" % (i, str(text)[:30]))
            continue
        if pos < last:
            out.append("事件顺序变化（第 %d 段）：「%s」" % (i, str(text)[:30]))
        last = max(last, pos)
    return out


def kf_structural_violations(before_shot, after_shot, *, skip_motion: bool = False) -> list:
    """增删 KF 之后必须同时满足的两条确定性验收（本模块对外唯一入口）.

    **差分口径**（2026-09-28 复核决定）：只报「本轮**新引入 / 恶化**」的 KF 结构违例。
    稿内既有的越窗/乱序不再让整轮修复被判死——实测 run 02：3 处越窗帧在修复前就存在
    （`preexisting=True`），却让当轮唯一那 3 条 `resolved` 全被回退 → 判「零改善」→
    停止修复，而越窗帧本身是不可修项（`kf_time_out_of_shot` repairable=False），
    于是必然进终局阻塞。

    为什么不做字符串集合相减（复核意见）：增删帧会改变 `kf%d` 下标，同一处违例的文案
    随之变化；同类旧问题也可能被改坏。故身份取自**违例实质**且按**多重集**抵消：
    同身份数量变多仍算新引入，t 被改大（恶化）算新引入。

    `skip_motion=True`：本轮的 motion 改写已由 `narrow_motion_patch` 收窄并合回基线
    （v6.149），此时**不再**叠加逐字事件保全——否则会把刚被采纳的目标时段判成丢失。

    绝对口径**不在这里放宽**：稿内既有违例照旧由质量门判 blocking
    （`kf_time_out_of_shot` 等 hard 项），最终提交仍须通过它。
    """
    _before = _Counter(i for i, _ in kf_order_violation_items(before_shot or {}))
    out: list = []
    for _ident, _msg in kf_order_violation_items(after_shot or {}):
        if _before[_ident] > 0:
            _before[_ident] -= 1          # 同身份逐个抵消（旧违例仍在 → 不算本轮引入）
            continue
        out.append(_msg)
    if skip_motion:
        return out
    return out + motion_event_preservation(before_shot, after_shot)
