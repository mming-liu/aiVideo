# -*- coding: utf-8 -*-
"""生成前可行性 + 原文核验：计划里的**必保留内容**能否装进本集预算（2026-09-25，第二步）.

用户 2026-09-25 的边界（原文摘要）：

> **在生成前选内容、算预算；装不下就停在规划层。**但"计划是唯一来源"应收窄为
> "**计划是本集内容取舍和预算的权威**"——小说原文仍是事实与台词的依据。
> 1. 大纲标明必保留事实及证据、选用的逐字台词、可省略内容和事件顺序；**时长由代码用
>    现有估算器计算，不采信模型自报秒数**。对白与可同时发生的画面动作**不能简单重复相加**。
> 2. 若必保留内容的**估算下界已超过 90 秒**，报告"本集规划不可行／需拆集"，**不生成完整稿**，
>    也不靠加快语速或删必保留事实过关。
> 3. **第二步只接计划与生成前校验**；拆镜／重生仍以初稿为验收基线的问题留给第三步。

2026-09-25 收口（审核结论：第二步尚未闭环）——四处断点与修法：

1. **只验算一次，且在候选扇出之前**。门挂在 `_generate_script_candidates()` 入口，不是每个
   候选各跑一遍。`PlanInfeasibleError` **刻意不继承 `RuntimeError`**：候选循环里的
   `except RuntimeError` 是"生成失败→重试"语义，计划不可行不属于该语义，不能进重试。
2. **缺计划 = 明确停止**（不再是"只有 outline 非空才过门"）。`OUTLINE_ENABLED=0` 或大纲
   提取失败时，生成前无从验算预算 → 显式停止，不带着未验算的计划生成完整稿。
3. **原文核验与预算核算同在一个边界**：选用的逐字台词必须能在原文里整句找到；找不到的
   从计划里剔除（写日志与诊断），剔除后为空 → 不可评估 → 停止（"空台词＋非空节拍"
   不再放行）。
4. 口径更正：本门在**完整分镜生成之前**，不是"任何模型调用之前"——主流程此前已并发
   调用过提取模型（角色/场景物体/大纲）。

## 口径（为什么这么算）

- **时长由代码算**：逐句 `timecode._estimate_duration()` 求和；计划里 `time_budget` 之类的
  **模型自报秒数一律不采信**（它只是概略文字，实测写着"对白约30秒"，而当轮实际对白 91s）。
- **下界 = max(对白估时, 节拍下限)，不做加法**：说话要占时间、节拍也要占画面时间，但两者
  **可以同时发生**（对白本就常发生在动作镜里）。加法值只作诊断（`additive_sum_sec`），
  **不作为判据**。
- 判据是**下界 > 预算上限**：下界已超上限 ⇒ 无论怎么排都装不下。
- **只剔除幻觉台词，不为预算删内容**：原文核验剔除的是"原文里根本没有的台词"（生成阶段
  自己编的）；预算不够走的是 `infeasible` 分支——**不动计划**，只报告。本模块不加速语速、
  不删必保留事实、不产出"精简后的计划"。
"""
from __future__ import annotations

import re

from step01_script_gen.timecode import (MIN_SHOT_DURATION, _estimate_duration,
                                        active_shot_cap)

# 计划里"可核算"所必需的字段（缺任意一个 → 不可评估 → fail-closed）
_REQUIRED_PLAN_FIELDS = ("dialogue_lines", "shot_beats")

# 原文核验的标点归一化集合（与 `validation._check_grounding` 同族，另含直角引号/书名号）
_PUNCT_CHARS = '，。！？；：、—,!?:;“”"\'（）()「」『』《》〈〉'


def budget_band() -> tuple:
    """本集时长预算区间 `(min, max)` —— 唯一事实源在 `validation.py` 的红果标准."""
    from step01_script_gen.validation import _REDGUO_DURATION_MAX, _REDGUO_DURATION_MIN
    return float(_REDGUO_DURATION_MIN), float(_REDGUO_DURATION_MAX)


class PlanInfeasibleError(Exception):
    """计划在生成前即判定不可行／无法验算（装不进预算 / 缺计划 / 台词无原文依据）.

    **刻意不继承 `RuntimeError`**（用户 2026-09-25 收口）：候选生成的重试循环用
    `except RuntimeError` 表示"生成失败→换个候选/再试一次"，而计划不可行**不是**生成
    失败——它必须直接终止、零次候选生成。继承 `RuntimeError` 会让它被那条分支按
    "重试"语义处理（收口前实测：不可行计划被重试 `SCRIPT_PARSE_RETRIES` 次）。
    """

    #: 供调用方/测试识别"这是规划层结论，不是生成失败"
    is_plan_verdict = True


class DialogueIncompleteError(Exception):
    """候选层**台词完整性硬门**：计划选用的台词在稿件里破损（截断／跨镜句中拆散）.

    2026-09-26（工作包 sunny-kindling-wilkinson）：09-26 02:30 实跑 BLOCK 的根因是
    「选材台词被拆镜截断 → 审计报 `truncated_text` → 唯一正确修复字段是
    `narration_segment`（锁定字段）→ 绑定闸门 `locked_changed` 拒收 → 零改善即
    BLOCK」——缺陷的 curing 点放错了层。本门把台词完整性提前到候选层：破损稿
    **在被送进 BEST-GUARD／提交门之前**就被判不合格。

    **刻意不继承 `RuntimeError`**（与 `PlanInfeasibleError` 同一哲学）：这不是
    "生成失败→按重试语义吞掉"的故障——它带**内容结论**（哪些台词破损），调用方
    必须把它与 LLM 调用失败区分报告。
    """

    #: 供调用方/测试识别"这是台词完整性结论"
    is_dialogue_verdict = True

    def __init__(self, message: str, broken_batches=None):
        super().__init__(message)
        #: 每个破损候选的 broken 明细（list[list[dict]]），供诊断与日志
        self.broken_batches = list(broken_batches or [])


class DialogueGateError(DialogueIncompleteError):
    """台词完整性门**自身异常**（检查故障），不是「稿件台词破损」的内容结论.

    v6.135 五审（2026-09-26）：纯门故障（如净化器 `sanitize_narration` 抛错）不得
    顶着 `DialogueIncompleteError` 的类名把检查故障归为稿件不完整——顶层结论必须
    随实际失败类型区分（诊断 conclusion=dialogue_gate_error，见
    `dump_dialogue_incomplete`）。继承父类：既有 `except DialogueIncompleteError`
    与 `is_dialogue_verdict` 识别不受影响；混合失败（破损+门异常）仍抛父类，
    各类计数留在 attempt_stats。
    """


def plan_dialogue_lines(plan: dict | None) -> list:
    """计划里**选用的逐字台词**（非字符串项一律丢弃，不做任何补全）.

    只接受 `list`：字段被模型写成字符串时（schema 之外的异常输出）逐字符迭代会造出
    几十条假台词——一律当"没有可用台词"处理，由判定层 fail-closed 停止。
    """
    values = (plan or {}).get("dialogue_lines")
    if not isinstance(values, list):
        return []
    out = []
    for x in values:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def dialogue_seconds(lines) -> float:
    """逐句用现存估算器求和——**代码算，不采信计划里的自报秒数**."""
    return round(sum(_estimate_duration(s) for s in lines), 2)


def beats_floor_seconds(plan: dict | None) -> float:
    """节拍的画面下界：每个节拍至少要占 `MIN_SHOT_DURATION` 秒."""
    n = len([b for b in ((plan or {}).get("shot_beats") or []) if str(b or "").strip()])
    return round(n * float(MIN_SHOT_DURATION), 2)


# ---------------------------------------------------------------------------
# 原文核验：选用的台词是不是**逐字**来自原文
# ---------------------------------------------------------------------------
def _source_norm(text) -> str:
    """原文核验归一化：省略号折叠 → 删标点 → 删空白.

    **顺序不可换**——先把 `…/……/..` 折叠成占位符 `§`（普通字符，不会被标点归一化删掉），
    再删标点，最后把 `§` 还原成单字符省略号。反过来的话 `…` 会在删标点阶段就没了
    （与 `validation._check_grounding` 08-17 修掉的那个 bug 同源，这里按同一顺序）。
    """
    s = re.sub(r"\.{2,}|…{1,}", "§", str(text or ""))
    for p in _PUNCT_CHARS:
        s = s.replace(p, "")
    return s.replace("§", "…").replace(" ", "").replace("　", "").replace("\n", "")


def source_contains(novel_text: str, line: str) -> bool:
    """归一化后，这一句能否在原文里**整句**找到."""
    n = _source_norm(line)
    return bool(n) and n in _source_norm(novel_text)


def verify_dialogue_lines(lines, novel_text: str) -> tuple:
    """逐句核验选用台词；返回 `(kept, rejected)`，`rejected` 每项带原因.

    与 `validation._check_grounding` 的差别是**判据强度**：那边是"5 字滑窗命中"（旁白是
    改写过的叙述，不能要求整句逐字），这边是"整句包含"——选用台词按契约就是**逐字摘录**，
    整句找不到即视为幻觉台词。归一化规则同族（见 `_source_norm`），因此省略号/标点变体
    不会误伤。归一化后极短的台词命中会偏松，但代价极低（几个字），不做特殊分支。

    唯一一处宽容（与 `_check_grounding` 同源的理由）：**说话人前缀**（`张十三道：`）是
    生成层按规则0补的，不一定逐字来自原文——整句匹配不上时，再拿冒号后的台词体匹配一次。
    实测（2026-09-25，第 1 章）若不容忍前缀，真实稿件里带前缀的台词会**整批**被判"没有
    原文依据"，门会系统性误停。
    """
    novel_norm = _source_norm(novel_text)
    kept, rejected = [], []
    for s in lines:
        _forms = [s]
        # v6.138（九审 P1-3）：只有**真正的说话人前缀**（短且无子句标点）才容忍
        # 非逐字——"且慢，我告诉你：你先走。"的冒号前段是台词正文，不得丢掉再核
        _pfx, _body = _split_speaker_prefix(s)
        if _body.strip() and _body != s:
            _forms.append(_body)
        if any(_source_norm(f) and _source_norm(f) in novel_norm for f in _forms):
            kept.append(s)
        else:
            rejected.append({"line": s,
                             "reason": "原文中未逐字找到"
                             if _source_norm(s) else "归一化后为空（只有标点/空白）"})
    return kept, rejected


# ---------------------------------------------------------------------------
# 台词完整性硬门（2026-09-26，工作包 sunny-kindling-wilkinson）
#
# 为什么在候选层：`narration_segment` 是必保留内容（选材契约的锚），提交前修复
# 按设计禁止触碰（v6.94 绑定闸门）。选材台词一旦在生成/拆分层被拆镜截断，审计报
# `truncated_text` 后唯一正确修复就是改旁白 → 死锁（09-26 02:30 实损：
# `locked_changed` → 零改善 BLOCK）。所以完整性必须在候选层把关，而不是靠提交门
# 的审计"发现"。
# ---------------------------------------------------------------------------
# 句末标点：跨镜拼接的拆点必须落在句末——句中碎片正是审计 `truncated_text` 的
# 触发形态（09-26 实损：shot12 "…有些生意上的事情急需" 戛然而止）。
_SENT_END_CHARS = "。！？…；!?~"
_SENT_CLOSERS = "”’」』》〉）)]\"' 　"

# 跨镜拼接判据的*观测*反例（2026-09-26 实测，勿删）：
#   ① "截断+下一镜重复前缀"：shot12="…事情急需"（句中截断）+ shot13="公子爷赶回去处理，…"
#      ——纯整稿拼接能把台词拼回来，但 shot12 句中截断必然被审计报 truncated_text
#      → 死锁。句末完整性约束把它判破损。
#   ② "相邻镜头偶然跨界拼接"：台词恰好跨两镜的尾部+头部拼成——整段相等约束
#      （逐镜全消费/起点对齐）排除这类偶然命中。

#: 说话人前缀头部**不得**包含的标点（v6.138，九审 P1-3 单一事实源）：
#: 子句/句末标点出现在头部 ⇒ 冒号前是台词正文（"且慢，我告诉你"），不是说话人
#: ——说话人名不含这些标点。破折号/引号/括号不在此列（"张十三（低声）道："合法）。
_SPEAKER_PREFIX_FORBIDDEN_PUNCT = "，。！？…；、,;.!?"


def _is_speaker_prefix_head(head: str) -> bool:
    """头部文本是否够格当**说话人前缀**：非空、≤12 字、无子句/句末标点."""
    head = str(head or "").strip()
    return bool(head) and len(head) <= 12 and not any(
        ch in _SPEAKER_PREFIX_FORBIDDEN_PUNCT for ch in head)


def _split_speaker_prefix(text: str) -> tuple:
    """`张十三道：台词` → `("张十三道：", "台词")`；无头部说话人前缀 → `("", text)`.

    与拆分器 `_narration_parts` 同构，但**只认头部短前缀**（说话人名 ≤12 字）——
    台词正文里更深的冒号（"且慢，我告诉你：走开。"）不是前缀，留在正文里，
    否则切分估时会把它错当成每段都要重复的开销。

    v6.138（九审 P1-3）：「短」不再是唯一判据——头部含子句/句末标点（，；…等）
    的一律**不是**说话人前缀。实测反例：核验层曾把 `且慢，我告诉你：你先走。` 的
    冒号前段当前缀丢掉，用 `你先走。` 就通过了原文核验（原文只有后半句也放行）。
    说话人名（张十三/听香/群演）不含这些标点；该判据是 `verify_dialogue_lines` /
    本函数 / `_speaker_body` 的**单一事实源**。
    """
    text = str(text or "")
    m = re.split(r"[:：]", text, maxsplit=1)
    if len(m) == 2 and _is_speaker_prefix_head(m[0]):
        return m[0] + "：", m[1]
    return "", text


def find_legal_split(text: str, cap: float | None = None, *,
                     prefix_allowance_chars: int = 0,
                     max_parts: int = 4) -> list | None:
    """**统一判据求解**（A+B 单源，2026-09-26 用户决策）：按**句末边界**把 text 切成
    ≤`max_parts` 段、每段估时 ≤ `cap`.

    合法切点的唯一定义（规划层可表示性检查、定向拆分器、台词完整性门三方共用）：
    **句末边界** = `_SENT_END_CHARS`（。！？…；）标点串**之后**——连续标点串
    （如 `……`）只在串尾切，不在串中间切（六审 P2）。逗号/顿号切点与等字长兜底
    **一律非法**——句中碎片正是终稿台词门 `cross_shot_split` 判破损、审计报
    "同一句被截成两半"的形态（09-26 三次实跑复现"拆分成功、终稿失败"的直接机制）。

    求解 = **完整动态规划**（六审 P1-3）：本判据直接驱动规划重选与显式停止，
    误报"不可表示"的代价是错误的内容取舍——切点多时不截窗，精确求「最少段数、
    同段数内最长段最短」的解（六审 P2：两段已合法时不选三段）。

    Args:
        text: 台词/旁白原文，可带头部说话人前缀（前缀保留在每段开头）。
        cap: 单镜上限秒数；None 取 `active_shot_cap()`（模型能力注册表）。
        prefix_allowance_chars: 规划层台词还没有说话人前缀，按「每段将补这么长
            前缀」保守估时（生成层规则0前缀 "张十三道：" ≈5 字）。拆分器传 0
            （text 自带真实前缀）。
        max_parts: 段数上限（拆分器与 SPLIT_SHOT_SCHEMA 同为 4）。

    Returns:
        切段列表（各段含前缀），或 None——**text 整体已 ≤ cap（无需切）或不存在
        合法切法**。调用方（拆分器）拿到 None 必须在**调用拆分模型之前**停下，
        不得退回非法切点。
    """
    if cap is None:
        cap = active_shot_cap()
    cap = float(cap)
    text = str(text or "")
    if not text.strip():
        return None
    prefix, body = _split_speaker_prefix(text)
    _pad = "张" * max(0, int(prefix_allowance_chars))

    def _est(part: str) -> float:
        return _estimate_duration(prefix + _pad + part)

    if _est(body) <= cap + 0.01:
        return None  # 整条装得下：无切分需求
    import math
    need = _est(body)
    min_parts = max(2, int(math.ceil(need / cap - 1e-9)))
    if min_parts > max_parts:
        return None
    # 合法切点：句末标点**串**之后（串内位置不切；末尾位置不算切点）。
    # v6.138（九审边界项）：句末串后紧随的**收尾符**（闭引号/括号/空白）一并由
    # 前段消费——否则切点落在 `！` 与 `”` 之间，下一镜以孤立的闭引号开头。
    bounds = []
    _L = len(body)
    for i, ch in enumerate(body):
        if ch not in _SENT_END_CHARS or i + 1 >= _L \
                or body[i + 1] in _SENT_END_CHARS:
            continue
        _p = i + 1
        while _p < _L and body[_p] in _SENT_CLOSERS:
            _p += 1
        if _p < _L:
            bounds.append(_p)
    bounds = sorted(set(bounds))
    if not bounds:
        return None
    # 完整 DP：pts = [0] + bounds + [L]；D[k][j] = body[0:pts[j]] 切成 k 段
    # （段界都在切点上）的最小「最长段」估时。L 必须在 pts 里——末段是
    # body[最后切点:L]，漏掉 L 会把尾巴丢出估时（六审后自测抓到的真 bug）。
    pts = [0] + bounds + [len(body)]
    m = len(pts)
    L = len(body)
    INF = float("inf")
    _est_cache: dict = {}

    def _est_ij(i: int, j: int) -> float:
        key = (i, j)
        if key not in _est_cache:
            _est_cache[key] = _est(body[pts[i]:pts[j]])
        return _est_cache[key]

    rows = []          # rows[k-1] = k 段的 dp 行（下标 j）
    dp_prev = [0.0] + [INF] * (m - 1)   # k=0：仅位置 0 可达，值 0
    choice: dict = {}
    for k in range(1, max_parts + 1):
        dp_cur = [INF] * m
        for j in range(1, m):
            bv, bi = INF, -1
            for i in range(j):
                if dp_prev[i] == INF:
                    continue
                v = _est_ij(i, j)
                if dp_prev[i] > v:
                    v = dp_prev[i]
                if v < bv:
                    bv, bi = v, i
            dp_cur[j] = bv
            if bi >= 0:
                choice[(k, j)] = bi
        rows.append(dp_cur)
        dp_prev = dp_cur
    # 最少段数优先（k ≥ min_parts——更少段数必有一段超 cap），同段数内 DP 已取
    # 最小「最长段」；该 k 无可行解才进入 k+1。
    # 行语义：rows[r-1][j] = D(r, j) = 前 r 段覆盖 body[0:pts[j]]；总段数 k 的解
    # = D(k-1, j) + 末段 body[pts[j]:L] ⇒ 用 rows[k-2]，回溯重建 k-1 个内部切点。
    for k in range(min_parts, max_parts + 1):
        row = rows[k - 2]
        bv, bj = INF, -1
        for j in range(m - 1):              # 最后切点必须是真句末界（末段非空）
            if row[j] == INF:
                continue
            e_last = _est_ij(j, m - 1)      # 末段 body[pts[j]:L]
            v = e_last if e_last > row[j] else row[j]
            if v <= cap + 0.01 and v < bv:
                bv, bj = v, j
        if bj < 0:
            continue
        cuts: list = []
        cur = bj
        for r in range(k - 1, 1, -1):
            cuts.append(pts[cur])
            cur = choice[(r, cur)]
        cuts.append(pts[cur])               # 第一段的段尾
        cuts.reverse()
        parts = []
        prev = 0
        for c in cuts:
            parts.append(body[prev:c])
            prev = c
        parts.append(body[prev:])
        return [prefix + p for p in parts]
    return None

def check_dialogue_representable(line: str, cap: float | None = None, *,
                                 prefix_allowance_chars: int = 5) -> dict:
    """单句台词**可表示性**（A 判据；与拆分器 B 同源，见 `find_legal_split`）.

    判据：整条（含说话人前缀余量）装进单镜，**或**存在句末合法切分（≤4 段、
    每段含前缀余量 ≤ cap）。**不是**"整条 ≤12s"——多句长台词按句末切分可以保留
    （2026-09-26 用户决策：听香 64 字台词按省略号切两段 ≈5.9s/8.9s，保留）。

    Returns:
        `{"representable": bool, "est_sec": float, "split": list|None, "reason": str}`；
        `split` 是合法切段预览（重选反馈里给规划阶段看"怎么切能装下"）。
    """
    if cap is None:
        cap = active_shot_cap()
    cap = float(cap)
    line = str(line or "")
    # 前缀余量只在台词**还没有**说话人前缀时加（自带前缀的台词不再双算）
    _has_prefix, _ = _split_speaker_prefix(line)
    _allow = 0 if _has_prefix else max(0, int(prefix_allowance_chars))
    est = _estimate_duration("张" * _allow + line)
    if est <= cap + 0.01:
        return {"representable": True, "est_sec": round(est, 2), "split": None,
                "reason": ""}
    split = find_legal_split(line, cap, prefix_allowance_chars=_allow)
    if split:
        return {"representable": True, "est_sec": round(est, 2), "split": split,
                "reason": ""}
    n_end = len([i for i, ch in enumerate(line)
                 if ch in _SENT_END_CHARS and 0 < i + 1 < len(line)])
    if n_end == 0:
        reason = ("估时 %.1fs 超单镜上限 %.0fs，句内**无句末边界**（只有逗号/顿号）"
                  "——无法合法切分，须从原文重选" % (est, cap))
    else:
        reason = ("估时 %.1fs 超单镜上限 %.0fs，有 %d 个句末边界但切不出"
                  "每段 ≤ 上限的合法解" % (est, cap, n_end))
    return {"representable": False, "est_sec": round(est, 2), "split": None,
            "reason": reason}


def _speaker_body(text: str) -> str:
    """去说话人前缀（`张十三道：…` → `…`）——生成层规则0补的，不逐字来自计划.

    v6.138（九审 P1-3）：与 `_split_speaker_prefix` 同一判据——头部含子句/句末
    标点的冒号（"且慢，我告诉你：你先走。"）不是前缀，正文原样保留。
    """
    return _split_speaker_prefix(text)[1]


def _ends_sentence(raw: str) -> bool:
    """镜头旁白是否以句末收尾（剥尾随引号/括号/空白后看末字符）."""
    s = str(raw or "").strip()
    while s and s[-1] in _SENT_CLOSERS:
        s = s[:-1].rstrip()
    return bool(s) and s[-1] in _SENT_END_CHARS


def _match_forms(text: str) -> list:
    """台词/旁白匹配形态：**合规净化后 + 原样**两种归一化（2026-09-26 三审 P1-2 重定）.

    为什么匹配方向是 **sanitize（orig→safe）而不是还原（safe→orig）**：还原对
    "原词与安全词同句"本质歧义——计划"拿春药，再去抓药。"整段还原成
    "拿春药，再去抓春药。"，与稿件"拿药，再去抓药。"两个形态都对不上（三审实锤）。
    而 sanitize 正是产出稿件文本的那个确定性函数（`sanitize_narration`，
    长词优先、幂等）：计划句与稿件**都过同一遍 sanitize** 再比对，替换永远不会错位。
    原样形态保留给"两侧都未被净化"的路径；未命中安全词时 sanitize 是 no-op，
    两形态退化为同一形态。

    （`restore_narration_substitutions` 仍归 `_check_grounding` 使用——5 字滑窗容忍
    其"春药+抓药同句"歧义与单字 safe 误还原，修它属另一工作包。）

    **净化异常必须向外传播**（2026-09-26 四审 P1）：这里**禁止** try/except 退回
    "原样匹配"——回退会让"两侧原样恰好相同"的稿子在净化器故障时照样过门，提交层的
    fail-closed 分支收不到任何异常（硬门在自身故障时静默降级 = 门失效）。调用方
    （候选层 `_CandidateGateError`／终稿层／提交层／BEST-GUARD 快照过滤）各自
    fail-closed 接住。

    **判据边界（四审记录，有意取舍）**：本门契约 =「**净化后文本一致**」。净化后
    同形为同一安全词的原词（如 春药/媚药 → 药）**有意不可区分**——正式产物只含
    净化后文本，两个原词在成片里无差别；门保证的是"成片将呈现的台词与计划一致"，
    不是原词逐字一致。
    """
    t = str(text or "")
    from shared.content_safety import sanitize_narration as _sanitize
    variants = (_sanitize(t), t)
    out: list = []
    for v in variants:
        nf = _source_norm(v)
        if nf and nf not in out:
            out.append(nf)
    return out


def check_script_dialogue_integrity(script: dict, plan: dict | None) -> tuple:
    """**台词完整性硬门**（纯函数、确定性、零模型调用）：计划选用的台词是否逐字保住.

    判据（唯一事实源，候选层/终稿层/提交层共用）：
    - **命中①（单镜包含）**：`norm(台词)` 出现在**单个**镜头的 `narration_segment`
      里（台词侧与镜头侧都做说话人前缀宽容 + **合规净化对齐**——两侧同过
      `sanitize_narration`，替换永不错位；见 `_match_forms`）。
    - **命中②（跨镜整段拼接，审核约束④收口）**：台词跨多镜时，必须是
      「起点对齐（首镜旁白从头开始且句末完整）→ 中间镜整镜消费且句末完整 →
      末镜可只消费前缀（台词在该镜结束）」的**连续**分解——每个参审镜头的旁白
      都以句末收尾。句中碎片（09-26 实损形态）与偶然跨界拼接都不满足 ⇒ 破损。
      **逗号/顿号结尾的分句跨镜同样判破损**（2026-09-26 二审 M2 固化）：句中碎片
      正是审计 `truncated_text` 的触发形态——宁可显式失败，不做"逗号也算句末"的
      宽容。
    - **破损**：任一句未命中 ⇒ 整份候选不合格（宁显式失败，不做部分接受/阈值放行）。
    - `dialogue_lines` 为空 → 不可评估 → fail-closed 判不合格（同 `evaluate_plan`
      口径）。**调用方例外**：提交层对「无台词计划」（历史模式/旧缓存）不启用门——
      无契约可核 ≠ 有契约被破坏（见 `main._commit_step01_outputs`）。

    为什么不需要 `novel_text`：台词对原文的逐字依据已由
    `check_plan_before_generation` 收口（`_drop_unverified` 就地收窄）；本门核对的是
    「稿件是否保住计划选定的台词」——另一个维度。

    Returns:
        `(ok, broken)`；`broken` 每项 `{"line": 前60字, "reason": "cross_shot_split"|
        "not_found"|"extra_dialogue"}`。`reason=cross_shot_split` 表示整稿拼接能拼回但存在句中碎片
        （审计必然报 truncated_text → 提交门死锁的形态）。`extra_dialogue`（v6.138，
        九审 P2-4）= 带说话人前缀的旁白不含任何计划句（稿件另编了计划外对白）。
    """
    lines = plan_dialogue_lines(plan)
    if not lines:
        return False, [{"line": "", "reason": "dialogue_lines 为空（不可评估，fail-closed）"}]
    units_raw: list = []
    for s in (script or {}).get("shots") or []:
        if isinstance(s, dict):
            _n = str(s.get("narration_segment") or "").strip()
            if _n:
                units_raw.append(_n)
    # 镜头侧形态：全文/去说话人前缀 × 未还原/已还原（`_match_forms` 每文本给两形态）
    unit_forms_full = [_match_forms(u) for u in units_raw]
    unit_forms_body = [_match_forms(_speaker_body(u)) for u in units_raw]
    flat_unit_forms = [f for pair in (unit_forms_full + unit_forms_body) for f in pair]
    # 跨镜拼接需要**连贯**的形态集（不能逐镜混用净化/原样）：`_match_forms` 约定
    # [净化后, 原样]（无安全词时二者相同）
    body_plain = [p[-1] for p in unit_forms_body]
    body_sanitized = [p[0] for p in unit_forms_body]

    broken: list = []
    all_line_forms: list = []
    for line in lines:
        line_forms: list = []
        for f in (line, _speaker_body(line)):
            for nf in _match_forms(f):
                if nf and nf not in line_forms:
                    line_forms.append(nf)
        all_line_forms.extend(line_forms)
        hit = False
        _joinable = False
        for nf in line_forms:
            if hit:
                break
            # ① 单镜包含（镜头侧全部形态都试）
            if any(nf in u for u in flat_unit_forms if u):
                hit = True
                break
            # ② 跨镜整段拼接（约束见函数 docstring）；净化/原样两套连贯形态都试
            for bodies in (body_sanitized, body_plain):
                if hit:
                    break
                if nf and nf in "".join(bodies):
                    _joinable = True
                n = len(units_raw)
                for i in range(n):
                    u0 = bodies[i]
                    if not u0 or not nf.startswith(u0):
                        continue
                    if not _ends_sentence(_speaker_body(units_raw[i])):
                        continue  # 首镜句中截断 ⇒ 拼接救不回（09-26 实损形态）
                    acc = u0
                    j = i + 1
                    while j < n:
                        cand = acc + bodies[j]
                        if cand == nf:
                            hit = True
                            break
                        if nf.startswith(cand):
                            if not _ends_sentence(_speaker_body(units_raw[j])):
                                break  # 中间镜句中截断
                            acc = cand
                            j += 1
                            continue
                        if cand.startswith(nf):
                            hit = True  # 台词在末镜前缀内结束（末镜句首对齐）
                            break
                        break
                    if hit:
                        break
        if not hit:
            broken.append({"line": str(line)[:60],
                           "reason": "cross_shot_split" if _joinable else "not_found"})
    # v6.138（九审 P2-4）：**排他性**——计划句逐字保住还不够，稿件不得**另编**计划外
    # 对白（"5/5 命中 + 1 句自选"此前照样过门，含 BEST-GUARD 历史快照；自选台词 =
    # 生成层重新选材，违反计划权威）。判据收窄到**带说话人前缀**的旁白：其正文与
    # 全部计划句既无包含关系（整句在镜内）、也无被包含关系（跨镜碎片）⇒ 自选对白。
    # 无前缀的旁白（描述/画外音）不按对白拦截（审查明确要求）。
    for _ui, _u in enumerate(units_raw):
        if not _split_speaker_prefix(_u)[0]:
            continue
        _u_forms = [f for f in (unit_forms_full[_ui] + unit_forms_body[_ui]) if f]
        if not _u_forms:
            continue
        _consumed = False
        for _uf in _u_forms:
            for _lf in all_line_forms:
                if _lf and (_lf in _uf or _uf in _lf):
                    _consumed = True
                    break
            if _consumed:
                break
        if not _consumed:
            broken.append({"line": str(_u)[:60], "reason": "extra_dialogue"})
    return (not broken), broken


class EventContractBrokenError(DialogueIncompleteError):
    """候选层**事件履约契约**：镜头的 plan_refs 声明违反节拍覆盖/顺序/越界.

    v6.138（九审根因 1，2026-09-26）：计划的事件链（`shot_beats` 节拍）此前只作
    参考信息注入 prompt，没有任何可校验的承载声明——节拍「恐吓听香」在规划重选
    删掉承载台词后成片无承载（审计 MISSING_PLOT）、修复补丁把被删的「赶车命令」
    改成视觉手势且无承接（MISSING_ACTION），都只能在提交点靠语义审计发现，而修复
    轮又不许改台词（逐字锁）⇒ 结构性死锁。契约把「哪个镜头承载哪些节拍/台词」
    变成**显式、可确定性核验**的声明（`plan_refs`），在候选入池前核验。

    与 `DialogueIncompleteError` 同性质（内容结论、非检查故障）：fail-closed、
    独立分账（attempt_stats.contract_broken），**不得**混入 dialogue_broken——
    把「节拍无承载」统计成「台词截断」= 五审同型误归因。继承它只为让调用方的
    「显式失败、不重试」分支照常接住。
    """


#: 节拍载体标注段前缀（`shot_beats` 条目的「｜载体：…」段，v6.138）
_BEAT_CARRIER_TAG = "载体："
_BEAT_CARRIER_DIALOGUE_PREFIX = "台词"


def _beat_carrier(beat_text: str) -> tuple:
    """解析节拍的「｜载体：…」标注 → `("dialogue", k)` / `("visual", None)` / `(None, None)`.

    未声明（旧缓存计划没有该段）→ `(None, None)`：**没有契约可核 ≠ 契约被破坏**
    （与台词门对「无台词计划」的口径一致）。`载体：台词k` 的 k 取随后的整数；
    其余取值一律按画面承载处理。
    """
    for seg in str(beat_text or "").split("｜"):
        seg = seg.strip()
        if not seg.startswith(_BEAT_CARRIER_TAG):
            continue
        val = seg[len(_BEAT_CARRIER_TAG):].strip()
        if val.startswith(_BEAT_CARRIER_DIALOGUE_PREFIX):
            digits = ""
            for ch in val[len(_BEAT_CARRIER_DIALOGUE_PREFIX):]:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                return "dialogue", int(digits)
        return "visual", None
    return None, None


def _strip_beat_carrier(beat_text: str) -> str:
    """去掉节拍条目的「｜载体：…」段——规划重选核心字段比较用（只许改载体标注）."""
    return "｜".join(seg for seg in str(beat_text or "").split("｜")
                     if not seg.strip().startswith(_BEAT_CARRIER_TAG))


def check_event_contract(script: dict, plan: dict | None) -> tuple:
    """**事件履约契约**（纯函数、确定性、零模型调用）：`plan_refs` 声明的覆盖与顺序.

    计划侧单位（编号在 `prompts.render_execution_contract` 的契约块里给出）：
    **B1..Bn** = `shot_beats` 顺序（节拍），**D1..Dm** = `dialogue_lines` 顺序（选用
    台词）。每个镜头声明 `plan_refs = {"beats": [B…], "lines": [D…]}`，判据：

    - **覆盖**：每个节拍至少被一镜声明——缺 = 该节拍在成片没有承载（2026-09-26
      实跑：B5「恐吓隐瞒」无承载 → 审计 MISSING_PLOT；赶车命令被改成视觉手势且
      无承接 → MISSING_ACTION）；
    - **顺序**：按镜头序展开（镜内按声明序）后，B 与 D 序列都必须**非降**——
      后位事件不得在前位事件完成前呈现；
    - **越界**：引用不存在的 B/D → 声明与计划脱节。

    台词**逐字**是否保住不在此核（`check_script_dialogue_integrity` 收口）——
    这里核「声明与计划一致」这另一维度。声明缺失的容忍口径：**整稿**所有镜都
    没有 `plan_refs` → 「契约未声明」，返回 `(True, [], False)` 不判违规（历史
    快照没有声明，没有契约可核 ≠ 契约被破坏，否则跨 run 快照线全灭）；部分缺失
    → 逐镜记 `plan_refs_missing`（新生成稿不该出现混合形态）。

    Returns:
        `(ok, violations, declared)`；violations 每项 `{"code", "shot", "detail"}`，
        `declared=False` 表示整稿未声明（调用方不得据此阻断）。
    """
    beats = [str(x).strip() for x in ((plan or {}).get("shot_beats") or [])
             if str(x).strip()]
    n_lines = len(plan_dialogue_lines(plan))
    shots = [s for s in (script or {}).get("shots") or [] if isinstance(s, dict)]
    refs_list: list = []
    missing: list = []
    for s in shots:
        r = s.get("plan_refs")
        if (not isinstance(r, dict)
                or not isinstance(r.get("beats"), list)
                or not isinstance(r.get("lines"), list)):
            missing.append(str(s.get("index")))
            refs_list.append(None)
        else:
            refs_list.append(r)
    if not shots or len(missing) == len(shots):
        return True, [], False
    violations: list = []
    for sid in missing:
        violations.append({"code": "plan_refs_missing", "shot": sid,
                           "detail": "镜头缺少 plan_refs 声明（事件履约契约）"})
    if missing:
        # 有镜头缺声明时，覆盖/顺序无法公平评估（缺声明的那镜可能正承载着某节拍）——
        # 先报缺失，让下一轮候选带着完整声明进来再核其余判据。
        return False, violations, True

    covered: set = set()
    beat_seq: list = []
    line_seq: list = []
    for s, r in zip(shots, refs_list):
        sid = str(s.get("index"))
        for b in r["beats"]:
            if not isinstance(b, int) or isinstance(b, bool) \
                    or b < 1 or b > len(beats):
                violations.append({
                    "code": "plan_refs_out_of_range", "shot": sid,
                    "detail": "B%s 不存在（节拍共 %d 个）" % (b, len(beats))})
                continue
            covered.add(b)
            beat_seq.append((sid, b))
        for d in r["lines"]:
            if not isinstance(d, int) or isinstance(d, bool) \
                    or d < 1 or d > n_lines:
                violations.append({
                    "code": "plan_refs_out_of_range", "shot": sid,
                    "detail": "D%s 不存在（选用台词共 %d 句）" % (d, n_lines)})
                continue
            line_seq.append((sid, d))
    for seq, code, tag in ((beat_seq, "beats_order_violation", "B"),
                           (line_seq, "lines_order_violation", "D")):
        for (sid0, v0), (sid1, v1) in zip(seq, seq[1:]):
            if v1 < v0:
                violations.append({
                    "code": code, "shot": str(sid1),
                    "detail": "%s%d 在 %s%d 之后声明（事件/台词顺序不得回退）"
                              % (tag, v1, tag, v0)})
                break  # 每类报第一处即够定位
    for i, beat in enumerate(beats, 1):
        if i not in covered:
            violations.append({
                "code": "beats_not_carried", "shot": "",
                "detail": "B%d（%s…）没有被任何镜头声明承载" % (i, beat[:36])})
    return (not violations), violations, True


def dump_dialogue_incomplete(chapter_dir, broken_batches, *, total_lines: int = 0,
                             attempt_stats: dict | None = None,
                             conclusion: str | None = None,
                             layer: str = "候选层") -> None:
    """台词完整性门失败的**显式诊断**（决策点 2；五审：结论随实际失败类型区分）.

    `conclusion` 缺省按批次**自动归类**（五审 2026-09-26）：
    - 全部条目的 `reason` 以 `gate_error` 开头（且批次非空）→ `dialogue_gate_error`：
      **检查故障**结论——稿件完整性**未被判定**，不是生成稿完整性结论；
    - 否则（含真实破损；含破损+门异常的**混合失败**）→ `dialogue_incomplete`：
      生成稿完整性结论，混合失败的各类计数留在 `attempt_stats`
      （`{"total", "dialogue_broken", "gate_errors", "other_failed"}`，三审 P2-2
      + 四审 P1 分账口径）。

    消费者不得把任何一种解释成 `replan_incomplete`／触发计划重选（审核
    2026-09-26）。正式三件套不动。`layer` 只影响 note 文案（候选层/终稿层共用）。
    """
    if chapter_dir is None:
        return
    _gate_only = bool(broken_batches) and all(
        str((b or {}).get("reason") or "").startswith("gate_error")
        for batch in (broken_batches or []) for b in batch)
    _conclusion = conclusion or ("dialogue_gate_error" if _gate_only
                                 else "dialogue_incomplete")
    if _conclusion == "dialogue_gate_error":
        _note = (f"{layer}台词完整性门自身异常（检查故障）：稿件完整性**未被判定**，"
                 "不进入选稿/提交门；这不是计划可行性结论，不触发计划重选")
    else:
        _note = (f"{layer}台词完整性硬门：破损稿不进入 BEST-GUARD／提交门；"
                 "这不是计划可行性结论，不触发计划重选")
    try:
        from pathlib import Path
        from shared.json_io import atomic_dump_json
        atomic_dump_json(Path(chapter_dir) / "step01_plan_infeasible.json",
                         {"verdict": _conclusion,
                          "conclusion": _conclusion,
                          "note": _note,
                          "total_lines": total_lines,
                          "attempt_stats": dict(attempt_stats or {}),
                          "broken_batches": list(broken_batches or [])},
                         ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def evaluate_plan(plan: dict | None, *, novel_text: str = "",
                  budget_min: float | None = None,
                  budget_max: float | None = None) -> dict:
    """给出计划的**可行性判定**（纯函数：不改计划、不产出精简稿）.

    `novel_text` 是原文（事实与台词的依据）；核验与预算都用它，缺原文即不可评估
    （fail-closed）。

    Returns `{"verdict": "feasible"|"infeasible"|"unevaluable", "feasible",
    "evaluable", "missing", "dialogue_lines", "dialogue_sec", "dialogue_verified",
    "dialogue_rejected", "beats", "beats_floor_sec", "lower_bound_sec",
    "additive_sum_sec", "budget", "reasons", "suggestions", "note"}`。
    其中 `dialogue_lines`/`dialogue_sec` 口径是**核验通过**的那一组。
    """
    _bmin, _bmax = budget_band()
    bmin = float(budget_min if budget_min is not None else _bmin)
    bmax = float(budget_max if budget_max is not None else _bmax)

    def _stop(reasons, suggestions, *, missing=None, rejected=None) -> dict:
        return {"verdict": "unevaluable", "feasible": False, "evaluable": False,
                "missing": list(missing or []), "dialogue_lines": 0,
                "dialogue_sec": 0.0, "dialogue_verified": [],
                "dialogue_rejected": list(rejected or []),
                "dialogue_unrepresentable": [],
                "beat_carrier_dangling": [],
                "beats": 0, "beats_floor_sec": 0.0, "lower_bound_sec": None,
                "additive_sum_sec": None, "budget": {"min": bmin, "max": bmax},
                "reasons": reasons, "suggestions": suggestions, "note": ""}

    # ① 计划缺失（大纲提取失败 / OUTLINE_ENABLED=0）——生成前无从验算，必须显式停止
    if not plan:
        return _stop(
            ["本集计划缺失（大纲提取失败或 OUTLINE_ENABLED=0）——生成前无法核验内容取舍与预算"],
            ["重跑规划（或打开 OUTLINE_ENABLED=1）后再生成；计划是内容取舍与预算的权威"],
            missing=list(_REQUIRED_PLAN_FIELDS))

    # ② 字段缺失
    missing = [f for f in _REQUIRED_PLAN_FIELDS if f not in plan]
    if not missing:
        # 字段在但全是空 → 同样不可评估（空清单不能当作"零成本"）
        if not plan_dialogue_lines(plan) and not (plan or {}).get("shot_beats"):
            missing = ["dialogue_lines/shot_beats（都是空）"]
    if missing:
        return _stop(
            ["计划缺少可核算字段 %s——无法在生成前验算（fail-closed）" % missing],
            ["让规划阶段补齐这些字段后重跑；不要带着未验算的计划生成完整稿"],
            missing=missing)

    # ③ 没有原文就无法核验台词依据（原文是事实与台词的依据）
    if not str(novel_text or "").strip():
        return _stop(["缺少原文文本，无法核验选用台词是否逐字来自原文（fail-closed）"],
                     ["带原文重跑；不要带着未核验的台词生成完整稿"])

    # ④ 原文核验：逐字找不到的台词不是"本集选用的内容"
    lines = plan_dialogue_lines(plan)
    kept, rejected = verify_dialogue_lines(lines, novel_text)
    if not kept:
        if lines:
            reasons = ["计划选用的 %d 句台词**全部**未在原文中逐字找到——"
                       "生成阶段会自行决定台词，计划就不是内容取舍的权威" % len(lines)]
        else:
            reasons = ["计划未选用任何逐字台词（`dialogue_lines` 为空）——"
                       "生成阶段会自行决定台词，计划就不是内容取舍的权威"]
        return _stop(reasons,
                     ["回到规划层按原文**逐字**摘出台词（宁少勿滥）后重跑"],
                     rejected=rejected)

    _dlg = dialogue_seconds(kept)
    _floor = beats_floor_seconds(plan)
    lower = round(max(_dlg, _floor), 2)
    # ⑤ 单镜**可表示性**（v6.136 A 判据，2026-09-26 用户决策）：逐句检查"整条装进
    # 单镜，或存在句末合法切分"。这是**计划层**的检查——不可表示的台词进入生成后
    # 必然"拆分成功、终稿门失败"（09-26 三次实跑复现），必须前移到规划层。
    # **不是静默剔除**：走 infeasible → 规划重选反馈（点名台词+保留必保留事实/
    # 事件顺序）→ 重选后仍不可行才显式停止交内容取舍。
    _cap = active_shot_cap()
    _unrep = []
    for _ln in kept:
        _chk = check_dialogue_representable(_ln, _cap)
        if not _chk["representable"]:
            _unrep.append({"line": _ln, "est_sec": _chk["est_sec"],
                           "reason": _chk["reason"]})
    reasons, suggestions = [], []
    if rejected:
        reasons.append("选用台词 %d 句未在原文中逐字找到，已从计划中剔除" % len(rejected))
    if _unrep:
        reasons.append(
            "%d 句选用台词超出单镜上限 %.0fs 且**无句末合法切分**（判据：整条装进单镜，"
            "或按句末边界。！？…；切成 ≤4 段每段 ≤ 上限——与台词完整性门/拆分器同判据）：%s"
            % (len(_unrep), _cap,
               "；".join("「%s…」%s" % (u["line"][:20], u["reason"])
                         for u in _unrep[:3])))
        suggestions.append(
            "规划层**重选**：从原文重选能传达同样必保留事实的替代台词"
            "（**必保留事实与事件顺序不得静默删除**；重选反馈会点名这些台词）")
        suggestions.append(
            "若原文无合适替代 ⇒ 明确报告「规划无法满足当前单镜规则」，内容取舍交用户决定")
    # ⑥ 「无合适替代」标记出口（六审 P1-2）：重选反馈要求规划在原文确实没有更短
    # 替代台词时在 `time_budget` 明写——该标记是**内容取舍状态**：即使其余台词
    # 可表示、预算可行，也不得进入生成（必保留事实被静默丢弃的风险必须由用户
    # 裁决，代码不代决）。标记随计划进缓存 → 后续每轮都在本门显式停止。
    _no_alt = "无合适替代" in str((plan or {}).get("time_budget") or "")
    if _no_alt:
        reasons.append(
            "规划层明确报告：不可表示台词**无合适替代**（`time_budget` 标记）——"
            "须内容取舍（删情节/换场次/拆集），代码不代决")
        suggestions.append(
            "用户决策后修订计划（删除该标记并重选，或手工调整选材）再重跑")
    # ⑦ 节拍**载体悬空**（v6.138，九审根因 1）：节拍声明「载体：台词k」而第 k 句
    # 已不在选用台词里 ⇒ 该节拍在成片注定无台词承载（重选删掉了承载台词却没重新
    # 指定载体）。与 ⑤ 同口径走 infeasible → 重选反馈（允许且必须更新载体标注）
    # → 重选后仍悬空才显式停止。旧缓存计划无载体标注 → 不判悬空（无可核 ≠ 被破坏）。
    _beats_list = [str(x).strip() for x in ((plan or {}).get("shot_beats") or [])
                   if str(x).strip()]
    _dangling = []
    for _i, _bt in enumerate(_beats_list, 1):
        _kind, _k = _beat_carrier(_bt)
        if _kind == "dialogue" and not (1 <= (_k or 0) <= len(kept)):
            _dangling.append("B%d 载体「台词%s」不在当前选用台词中（共 %d 句）"
                             % (_i, _k, len(kept)))
    _no_carrier = "载体缺失：无合适承载" in str((plan or {}).get("time_budget") or "")
    if _dangling:
        reasons.append(
            "%d 个节拍的载体标注指向已移出的台词：%s"
            % (len(_dangling), "；".join(_dangling[:4])))
        suggestions.append(
            "规划层重选时**必须同步更新节拍的载体标注**（重选回原台词，或改标为"
            "保留台词/「载体：画面」）——节拍内容与顺序不得改动，只许改载体段")
    if _no_carrier:
        reasons.append(
            "规划层明确报告：节拍**载体缺失、无合适承载**（`time_budget` 标记）——"
            "须内容取舍，代码不代决")
        suggestions.append(
            "用户决策后修订计划（删除该标记并调整节拍/选材）再重跑")
    if lower > bmax:
        reasons.append(
            "必保留内容的估算下界 %.2fs 已超预算上限 %.0fs（对白 %.2fs / %d 句；节拍下限 %.2fs）"
            % (lower, bmax, _dlg, len(kept), _floor))
        reasons.append("下界已超上限 ⇒ 无论怎么排都装不下（不是靠排布/剪辑能救的）")
        suggestions.append("在规划层**重选**：按 `omittable` 换掉次要内容后重新规划")
        suggestions.append("或报告**本集需拆集**（本章内容超出单集预算）")
    elif lower < bmin:
        # 不足下限**不是**"不可行"，也**不能**据此说"内容不够"（用户 2026-09-25）：
        # 下界只说明"至少这么多"，对白与画面动作可同时发生，成片实际时长要等生成稿。
        # 这里只报告读数，判断留给实际时长。
        reasons.append(
            "必保留内容下界 %.2fs 低于下限 %.0fs——下界只说明「至少这么多」，"
            "**尚不能据下界判断成片是否达到下限**（对白与画面动作可同时发生）"
            % (lower, bmin))
        suggestions.append(
            "按生成稿的**实际正片时长**判断：若确实低于下限，再回规划层补内容"
            "（反应/环境/过场）或合并章节")
    return {"verdict": "infeasible" if (lower > bmax or _unrep or _no_alt
                                        or _dangling or _no_carrier) else "feasible",
            "feasible": (lower <= bmax and not _unrep and not _no_alt
                         and not _dangling and not _no_carrier),
            "evaluable": True, "missing": [],
            "dialogue_lines": len(kept), "dialogue_sec": _dlg,
            "dialogue_verified": kept, "dialogue_rejected": rejected,
            "dialogue_unrepresentable": _unrep,
            "beat_carrier_dangling": _dangling,
            "beats": int(round(_floor / float(MIN_SHOT_DURATION))), "beats_floor_sec": _floor,
            "lower_bound_sec": lower,
            # 只作诊断：两者可同时发生，**不得**用来判可行性（用户 2026-09-25）
            "additive_sum_sec": round(_dlg + _floor, 2),
            "budget": {"min": bmin, "max": bmax},
            "reasons": reasons, "suggestions": suggestions,
            "note": "判据取 max(对白, 节拍下限)，对白与同时发生的画面动作不重复相加"}


def _replan_note(plan) -> tuple:
    """规划层重选记录 → `(说明, 补充建议, 是否**没有判定结论**)`.

    用户 2026-09-25：**"重选没跑成"与"重选后仍超预算"必须分开**。调用/解析/校验失败
    （`result="replan_failed"`）**没有判定结论**，不能据此报告"本集需拆集"；只有
    `result="infeasible"`（真的产出过第二版计划并复核过）才是"仍装不下"的结论。
    第三个返回值同时驱动三件事：撤下"拆集"建议、异常开头不写"不可行／需拆集"、
    诊断写 `conclusion="replan_incomplete"`。
    """
    _r = (plan or {}).get("plan_replan") if isinstance(plan, dict) else None
    if not isinstance(_r, dict):
        return "", "", False
    try:
        from step01_script_gen.outline import FORCE_REPLAN_ENV
    except Exception:
        FORCE_REPLAN_ENV = "STEP01_FORCE_REPLAN"
    if _r.get("result") == "replan_failed":
        return ("规划层重选**未完成**（%s）——这**不是**「本集不可行」的判定，"
                "不能据此报告需拆集" % (_r.get("detail") or "调用/解析失败"),
                "先重跑该轮重选（`%s=1`）或删除大纲缓存后重新规划" % FORCE_REPLAN_ENV,
                True)
    if _r.get("result") == "infeasible":
        _un = int(_r.get("after_unrepresentable") or 0)
        return ("规划层已重选 %d 轮后仍不可行（对白 %.2fs → %.2fs%s）⇒ 需拆集，"
                "或按 `omittable` 重选后再规划"
                % (int(_r.get("rounds") or 1),
                   float(_r.get("before_dialogue_sec") or 0.0),
                   float(_r.get("after_dialogue_sec") or 0.0),
                   "；不可表示台词 %d 句" % _un if _un else ""), "", False)
    return "", "", False


def _log():
    """复用主流程 logger；独立调用（如单测）时退回标准 logging（不制造 import 环）."""
    try:
        from step01_script_gen.main import _logger as lg
        return lg
    except Exception:
        import logging
        return logging.getLogger("step01.plan")


def _drop_unverified(plan, rep: dict) -> list:
    """把计划里的 `dialogue_lines` 就地收窄为**核验通过**的那一组.

    这是本模块**唯一**改计划的地方，且只删"原文里根本没有的台词"；预算不够走
    `infeasible` 分支、**不动计划**。收窄后生成侧（`outline_to_anchor` 等）只能看到
    核验过的台词——"计划是本集内容取舍的权威"靠这一步落地。
    """
    if not isinstance(plan, dict):
        return []
    raw = plan.get("dialogue_lines")
    if not isinstance(raw, list):
        return []
    kept = list(rep.get("dialogue_verified") or [])
    if raw == kept:
        return []
    plan["dialogue_lines"] = kept
    return list(rep.get("dialogue_rejected") or [])


def check_plan_before_generation(plan: dict | None, *, chapter_dir=None,
                                 novel_text: str = "") -> dict:
    """**生成前唯一入口**（挂于候选扇出之前）：原文核验 + 预算核算，只验算一次.

    可行 → 把 `dialogue_lines` 收窄为核验通过的集合，返回报告（调用方据此记录同一组数：
    对白估时、下界、预算）。不可行/不可评估/缺计划 → 写诊断 `step01_plan_infeasible.json`
    并抛 `PlanInfeasibleError`（**不生成完整稿**）。
    """
    rep = evaluate_plan(plan, novel_text=novel_text)
    # 重选记录（若有）——把"重选没跑成"与"重选后仍超预算"分开写进诊断与报错（用户 2026-09-25）
    _note, _extra_sug, _no_verdict = _replan_note(plan)
    if _note:
        rep["reasons"] = list(rep["reasons"]) + [_note]
    if _no_verdict:
        rep["suggestions"] = [s for s in rep["suggestions"] if "拆集" not in s]
    if _extra_sug:
        rep["suggestions"] = list(rep["suggestions"]) + [_extra_sug]
    _log().info(
        "[PLAN] 对白 %d 句/估时 %.2fs（逐字核验剔除 %d 句；单镜不可表示 %d 句）；"
        "节拍 %d 个/下限 %.2fs；下界 %.2fs；预算 %.0f–%.0fs（%s）"
        % (rep["dialogue_lines"], rep["dialogue_sec"], len(rep["dialogue_rejected"]),
           len(rep.get("dialogue_unrepresentable") or []),
           rep["beats"], rep["beats_floor_sec"],
           rep["lower_bound_sec"] if rep["lower_bound_sec"] is not None else -1.0,
           rep["budget"]["min"], rep["budget"]["max"], rep["note"]))
    if rep["verdict"] == "feasible" and rep["reasons"]:
        # 可行但带读数（如"下界低于下限——尚不能据下界判断成片是否达到下限"）：
        # **通过路径也要看得见**（用户 2026-09-25 要的就是这条口径被报出来，
        # 而不是只在停下来时才出现在报错里）。
        _log().info("[PLAN] 注意：%s" % "；".join(rep["reasons"]))
    if rep["dialogue_rejected"]:
        # 准则 7：剔除必须看得见（哪几句、为什么）
        _log().warn("[PLAN] 下列选用台词未在原文中逐字找到，已从计划中剔除"
                    "（不参与预算，也不会进生成提示）："
                    + "；".join("%s（%s）" % (str(d.get("line"))[:40], d.get("reason"))
                                for d in rep["dialogue_rejected"]))
    if rep.get("dialogue_unrepresentable"):
        # 准则 7：不可表示台词必须看得见（A 判据；交规划重选，不静默剔除）
        _log().warn("[PLAN] 下列选用台词超出单镜上限且无句末合法切分"
                    "（交规划重选；必保留事实不得静默删除）："
                    + "；".join("「%s…」（%s）" % (str(u.get("line"))[:40],
                                                   u.get("reason"))
                                for u in rep["dialogue_unrepresentable"]))
    if rep["evaluable"]:
        _drop_unverified(plan, rep)
    if rep["verdict"] == "infeasible":
        _dump(chapter_dir, rep, plan, no_verdict=_no_verdict)
        # 用户 2026-09-25：**异常开头也必须分开**。`replan_failed`（调用/解析失败）没有判定
        # 结论，开头写"本集规划不可行／需拆集"就是把工具故障误报成内容结论；首版超预算的
        # **数字事实照旧保留**（在 reasons 里）。
        _head = ("规划重选未完成——尚无「本集不可行」的结论" if _no_verdict
                 else "本集规划不可行／需拆集")
        raise PlanInfeasibleError(
            "%s：%s ⇒ 不生成完整稿（%s）"
            % (_head, "；".join(rep["reasons"]), "；".join(rep["suggestions"])))
    if rep["verdict"] == "unevaluable":
        _dump(chapter_dir, rep, plan, no_verdict=_no_verdict)
        raise PlanInfeasibleError(
            "计划无法在生成前验算：%s ⇒ 不生成完整稿（%s）"
            % ("；".join(rep["reasons"]), "；".join(rep["suggestions"])))
    return rep


def _dump(chapter_dir, rep: dict, plan=None, *, no_verdict: bool = False) -> None:
    """把判定落成**诊断产物**（正式三件套不动）——准则 7：失败必须看得见.

    `verdict` 记的是**计划本身的读数**（事实：超预算 / 无法验算），`conclusion` 记的是
    **可以据此下的结论**：`teardown`（确认装不下）/ `replan_incomplete`（重选没跑成，
    尚无结论，用户 2026-09-25）。
    """
    if chapter_dir is None:
        return
    plan_replan = (plan or {}).get("plan_replan") if isinstance(plan, dict) else None
    try:
        from pathlib import Path
        from shared.json_io import atomic_dump_json
        atomic_dump_json(Path(chapter_dir) / "step01_plan_infeasible.json",
                         {"verdict": rep["verdict"],
                          "conclusion": ("replan_incomplete" if no_verdict
                                         else ("teardown" if rep["verdict"] == "infeasible"
                                               else "")),
                          "reasons": rep["reasons"],
                          "suggestions": rep["suggestions"],
                          "dialogue_lines": rep["dialogue_lines"],
                          "dialogue_sec": rep["dialogue_sec"],
                          "dialogue_rejected": rep["dialogue_rejected"],
                          "dialogue_unrepresentable": rep.get("dialogue_unrepresentable") or [],
                          "beats_floor_sec": rep["beats_floor_sec"],
                          "lower_bound_sec": rep["lower_bound_sec"],
                          # v6.132：规划层是否已经重选过一轮、重选前后各是多少——
                          # 「重选后仍超限」与「压根没重选」是两种不同的处置依据
                          "replan": (plan_replan if isinstance(plan_replan, dict) else None),
                          "budget": rep["budget"], "note": rep["note"]},
                         ensure_ascii=False, indent=2)
    except Exception:
        pass
