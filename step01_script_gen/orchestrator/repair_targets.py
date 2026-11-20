# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.repair_targets —— 修复目标定位、联动组、回滚、通道与字节预算.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
import re

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import final_audit as _mfa
from step01_script_gen.orchestrator import finalize as _mfz
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _shot_by_target_id(script: dict, tid: str):
    """按修复目标名取镜头对象（`shot_N` / 裸镜号 / `hook`）——取不到返回 None."""
    if not isinstance(script, dict):
        return None
    if str(tid) == "hook":
        return script.get("hook") if isinstance(script.get("hook"), dict) else None
    sid = str(tid)[5:] if str(tid).startswith("shot_") else str(tid)
    for s in script.get("shots") or []:
        if isinstance(s, dict) and str(s.get("index")) == sid:
            return s
    return None


def _shot_field_diff(before: dict, after: dict, *, limit: int = 8) -> list:
    """两个脚本之间**语义字段**发生变化的 `镜头.字段` 列表（最多 `limit` 条）.

    v6.92：诊断细化的第二步。原先回退日志只说「哪些镜头变了」——
    2026-09-22 20:54 实测 `非目标镜头被改动 3 处: [12, 12.333, 18]`，要定位到底是
    哪个字段被动过只能重新复现一次运行（且未必复现）。这里直接给出字段名。

    只比语义白名单（`shared.quality_gate._SEMANTIC_SHOT_FIELDS`，与逐镜 revision
    同一份定义）——派生字段 `timecode` 不在其中，不会误报。
    """
    from shared.quality_gate import _SEMANTIC_SHOT_FIELDS

    def _by_key(script: dict) -> dict:
        out: dict = {}
        for s in (script.get("shots") or []):
            if isinstance(s, dict):
                out[str(s.get("index"))] = s
        if isinstance(script.get("hook"), dict):
            out["hook"] = script["hook"]
        return out

    b, a = _by_key(before), _by_key(after)
    out: list = []
    for k in sorted(set(b) | set(a), key=str):
        sb, sa = b.get(k), a.get(k)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            out.append(f"{k}:新增/删除")
            if len(out) >= limit:
                return out
            continue
        for f in _SEMANTIC_SHOT_FIELDS:
            if sb.get(f) != sa.get(f):
                out.append(f"{k}.{f}")
                if len(out) >= limit:
                    return out
    return out


def _snapshot_untargeted(script: dict, picked: set) -> dict:
    """快照**未被点名**的镜头（含 hook）→ `{位置: deepcopy}`（含 `"hook"` 键）.

    v6.94：键从 `shot_{index}` 改为**列表位置**。理由是 v6.84 在
    `shared/semantic_barrier` 上踩过的同一个坑：轮内可能发生索引重编号
    （`shot_index_renumber` 把半整数子镜归一成整数），按 index 为键会让还原
    落到错的镜头上（实测：`非目标镜头被改动 9 处` 命中的正是拆分子镜）。
    """
    import copy as _c
    out: dict = {}
    if "hook" not in picked and isinstance(script.get("hook"), dict):
        out["hook"] = _c.deepcopy(script["hook"])
    for pos, sh in enumerate(script.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        if f"shot_{sh.get('index')}" not in picked:
            out[pos] = _c.deepcopy(sh)
    return out


def _restore_untargeted(script: dict, snapshot: dict) -> int:
    """把快照**逐字节**写回原 dict（保持对象身份）；返回还原的镜头数.

    用 `clear()+update()` 而不是替换引用——脚本里可能有别处持有的同一 dict。
    """
    n = 0
    hook = script.get("hook")
    if "hook" in snapshot and isinstance(hook, dict):
        hook.clear()
        hook.update(snapshot["hook"])
        n += 1
    for pos, sh in enumerate(script.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        snap = snapshot.get(pos)
        if snap is not None:
            # 按位置还原文案，但**保留当前 index**：重编号（phase 1 的
            # `shot_index_renumber`）是受认可的归一化，还回去会造成 index 重复。
            keep_index = sh.get("index")
            sh.clear()
            sh.update(snap)
            if keep_index is not None:
                sh["index"] = keep_index
            n += 1
    return n


def _deterministic_targets(issues) -> set:
    """本轮带**确定性**阻塞的目标 id 集合（`shot_<id>` / `hook`）.

    `model_audit_issue` 是模型审计条目，不算确定性阻塞——验收里 `resolved`
    计量的是前者（`audit_resolved` 是另一条并行判据），质量门也按前者判 BLOCK。
    """
    out: set = set()
    for rec in issues or ():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("code") or "") == "model_audit_issue":
            continue
        if str(rec.get("severity") or "") not in ("hard", "visual_risk"):
            continue
        sid = rec.get("shot_id")
        if sid is None:
            continue
        out.add("hook" if str(sid) in ("0", "hook") else f"shot_{sid}")
    return out


def _pick_repair_targets(shot_items: dict, deterministic: set, limit: int,
                         excluded=()) -> dict:
    """本轮修复目标（v6.107）：**带确定性阻塞的镜头优先**，其次按告警条数.

    实测（第二十九次重跑）：提交点第 1 轮有 1 条确定性阻塞 + 4 条审计问题，4 个名额
    按告警条数排序时两类打平 → 带确定性阻塞的那一镜掉出前 4 → 该轮**根本没尝试**
    它，`resolved=0` → 停止修复 → BLOCK。质量门计量的是确定性阻塞，名额分配必须
    先满足它，否则修复预算花在计量不到的目标上。

    v6.138（九审 P1-2）：`excluded`（上轮肇事目标）在**排序取名额之前**过滤——
    旧实现"取前 4 再剔除"会让被剔除目标的名额空转（后续目标补不上来）。反例：
    5 个目标、前 2 个被排除 → 旧实现本轮只修 2 镜；本实现修满 4 镜。
    """
    import itertools as _it
    _ex = set(excluded or ())
    _pool = {k: v for k, v in shot_items.items() if k not in _ex}
    return dict(_it.islice(
        sorted(_pool.items(),
               key=lambda kv: (0 if kv[0] in deterministic else 1, -len(kv[1]))),
        limit))


def _new_blocking_targets(keys, picked) -> set:
    """新增 blocking 落在哪些**本轮点名的**目标上（v6.105）.

    返回空集表示**无法定位**：出现了全局问题（`shot_id` 为空）或落在非目标镜头上
    ——后者不是本轮写入造成的，回退被点名的镜头解决不了它，只能整轮回退。
    """
    out: set = set()
    for key in keys or ():
        try:
            sid = str(key[1])
        except Exception:
            return set()
        if not sid:
            return set()
        tid = "hook" if sid in ("0", "hook") else f"shot_{sid}"
        if tid not in picked:
            return set()
        out.add(tid)
    return out


#: `_final_field_mask_violations` 的消息形状：`shot <id>.<字段>: 旧 -> 新`
_LOCKED_VIOLATION_RE = re.compile(r"^shot (\S+?)\.(\w+):")
#: `_kf_structural_violations` 的消息形状：`<目标名>: <违例说明>`
_KF_STRUCT_VIOLATION_RE = re.compile(r"^([A-Za-z_0-9]+):\s")


def _norm_target_name(raw) -> str:
    """把消息里的目标名归一到 `shot_<id>` / `hook`（与本文件其余口径一致）."""
    _t = str(raw or "").strip()
    if not _t:
        return ""
    if _t in ("0", "hook"):
        return "hook"
    if _t.startswith("shot_"):
        return _t
    return f"shot_{_t}"


def _locate_offending_targets(decision: dict, picked: set,
                              locked_diff: list | None = None) -> set:
    """从闸门给出的消息里定位**肇事目标**（v6.143 P4，纯函数）.

    为什么要它：`locked_changed` / `locked_field_changed` / `kf_structure_invalid`
    三个闸门此前都只给出**轮级**结论，于是「一处越界 → 整轮 `resolved` 清零」——
    实测 10 次跑 20 个修复轮算出 `resolved=109`，其中 **60 条（55%）**被整轮丢弃，
    `run 03` 一次就丢 13 条。

    可定位性来自消息本身（都是既有格式，不新增字段）：
      · `locked_field_changed` → `_final_field_mask_violations` 的 `shot <id>.<字段>: 旧 -> 新`；
      · `locked_changed`（round 级签名变化，可能来自**已授权**的锁定字段）→
        回退到 `_locked_signature_diff` 的 `<id>.<字段>: 旧 -> 新`（由调用方传入）；
      · `kf_structure_invalid` → `_kf_structural_violations` 的 `<目标名>: 违例`；
      · `layout_structure_invalid` → `frame_layout_structural_violations` 的同形状消息。

    **不可定位**的一律返回空集 → 调用方保持**整轮**回退语义（不是放松）：
      · `non_target_changed`：肇事镜头在 `picked` **之外**，回退被点名镜头解决不了；
      · `evidence_removed` / `checker_failed`：前者是「改写检测输入」这类整体性违规，
        后者是基础设施失败——都不该按目标部分采纳。

    只返回**本轮点名的**目标：定位到 `picked` 之外说明归因有误，宁可退回整轮。
    """
    out: set = set()
    for _msg in (decision.get("locked_by_llm_changed") or []):
        _m = _LOCKED_VIOLATION_RE.match(str(_msg))
        if _m:
            _tid = _norm_target_name(_m.group(1))
            if _tid in picked:
                out.add(_tid)
    for _msg in ((decision.get("kf_structural_violations") or [])
                 + (decision.get("layout_structural_violations") or [])):
        _m = _KF_STRUCT_VIOLATION_RE.match(str(_msg))
        if _m:
            _tid = _norm_target_name(_m.group(1))
            if _tid in picked:
                out.add(_tid)
    if not out:
        for _msg in (locked_diff or []):
            _m = re.match(r"^(\S+?)\.\w+: ", str(_msg))
            if _m:
                _tid = _norm_target_name(_m.group(1))
                if _tid in picked:
                    out.add(_tid)
    return out



def _linked_repair_groups(audit_issues, shots_by_id: dict, picked, *, cap: int) -> tuple:
    """把审计的**成对修复目标**折成「联动组」（v6.119，纯函数）.

    一条 issue 的 `repair_targets` 声明了「要修好这条问题必须一起改的 (镜头, 字段)」。
    本函数只做分组，不做授权、不改稿子——**同一组必须整组进本轮**，因为半次修复会
    制造新的自相矛盾（实测 shot 5：只授权 `keyframes[2].desc`、回填 `stage` 与布局
    `props` → 半条修复引入 3 条新阻塞 → 整镜回退、缺陷零进展直至 BLOCK）。

    规则：
      ① 组内镜头与本轮目标（或问题**定位**所在镜头）有交集才算「本轮相关」；
      ② 共享镜头的组**合并**（否则同一批镜头会被拆成两轮各修一半）；
      ③ 组自身超过 `cap` → 归入 `oversize`，调用方**整组放弃**（留 BLOCK，
         不拆半次）；
      ④ 只有**校验通过**的成对条目参与（镜头存在 + 路径是该镜真实叶子路径）。

    Returns:
        `(groups, rejects, oversize)`；`groups` 是 `[frozenset(target 名)]`。
    """
    from step01_script_gen.repair_fields import (
        validate_repair_targets, _target_id_of, _is_structural_code,
    )
    # ① 折候选组：每组记「成员」与「相关锚点」（成员 ∪ 问题**定位**所在镜头）
    candidates: list = []
    rejects: list = []
    for _ai in (audit_issues or []):
        # `None` = 旧条目（无成对目标，靠定位挑镜头）；`[]` = 显式「无修复目标」
        # （两态都不构成联动组，但语义不同：后者是 fail-closed 的不授权）
        _declared = getattr(_ai, "repair_targets", None)
        if not _declared:
            continue
        _code = str(getattr(_ai, "code", "") or "")
        _valid, _rejected = validate_repair_targets(
            _declared, shots_by_id, structural=_is_structural_code(_code))
        rejects.extend(_rejected)
        if _rejected:
            # 整条拒绝：有一条目标无效就不成组、不留半组（用户口径）
            continue
        _members = {_target_id_of(_p["shot_id"]) for _p in _valid}
        _members.discard("")
        if not _members:
            continue
        candidates.append((frozenset(_members),
                           frozenset(_members | {_target_id_of(
                               getattr(_ai, "shot_id", ""))})))
    # ② **闭包**：锚点与本轮目标集相交的组被纳入；纳入后其成员也进入目标集，
    #    可能让本来「无关」的组变相关（组 A 带进 shot_7 → 定位在 shot_7 的组 B
    #    随之相关）。只对初始 `picked` 求一次交集会漏掉 B，联动链就断在半途。
    _targets = set(picked)
    _admitted: list = []
    _changed = True
    while _changed:
        _changed = False
        for _cand in list(candidates):
            _members, _anchors = _cand
            if _members <= _targets:
                # 组内镜头**已经全在本轮目标里** → 仍必须**登记为组**，不能直接丢弃
                # 候选：否则后续「按目标回退」只回退组内肇事的那一镜、留下半组
                # （用户口径：整组修 / 整组验收 / 整组回退）。
                _admitted.append(_members)
                candidates.remove(_cand)
                continue
            if _anchors & _targets:
                _admitted.append(_members)
                _targets |= _members
                candidates.remove(_cand)
                _changed = True
    # ③ 合并共享镜头的组（否则同一批镜头会被拆成两轮各修一半）
    groups: list = []
    for _members in _admitted:
        _cur = set(_members)
        for _other in list(groups):
            if _cur & _other:
                _cur |= _other
                groups.remove(_other)
        groups.append(frozenset(_cur))
    oversize = [g for g in groups if len(g) > cap]
    return [g for g in groups if len(g) <= cap], rejects, oversize


def _expand_rollback_by_groups(offend: set, groups) -> tuple:
    """把「回退目标集」按联动组补全：组内任一镜肇事 → 整组回退（v6.119，纯函数）.

    Returns `(补全后的目标集, 被整组回退的组列表)`。
    """
    hit: list = []
    out = set(offend or ())
    for _grp in (groups or ()):
        if out & _grp:
            hit.append(sorted(_grp))
            out |= _grp
    return out, hit


def _restore_targets(script: dict, before_script: dict, targets: set) -> dict:
    """返回副本：把 `targets` 里的镜头还原为本轮开始前的版本，其余保持现状（v6.105）.

    与 `_restore_untargeted` 的分工：那个用于**未点名**镜头（后处理副作用），
    本函数用于**被点名但引入了新阻塞**的目标——「逐目标提交」要成立，验收侧也必须
    能只回退坏的那个目标，而不是连坐同轮其它合法修复。
    """
    import copy as _c
    out = _c.deepcopy(script)
    if "hook" in targets:
        old_hook = before_script.get("hook")
        if isinstance(out.get("hook"), dict) and isinstance(old_hook, dict):
            out["hook"] = _c.deepcopy(old_hook)
    want = {str(t)[5:] for t in (targets or ()) if str(t).startswith("shot_")}
    if not want:
        return out
    old_by_id = {str(b.get("index")): b
                 for b in (before_script.get("shots") or []) if isinstance(b, dict)}
    shots = out.get("shots") or []
    for i, sh in enumerate(shots):
        if isinstance(sh, dict) and str(sh.get("index")) in want:
            old = old_by_id.get(str(sh.get("index")))
            if isinstance(old, dict):
                shots[i] = _c.deepcopy(old)
    return out


def _record_repair_call_failure(repair_diag: list | None, repair_round: int, *,
                                error: str, truncated: bool = False,
                                usage: dict | None = None,
                                max_tokens: int | None = None) -> None:
    """把"修复调用没拿到完整结果"记进修复台账（用户 2026-09-25）.

    为什么必须单独记：2026-09-25 实跑的 BLOCK 直接原因是定向修复响应被截断
    （`incomplete_details.reason=max_output_tokens`，16k 上限含思维链 token），
    整份丢弃后稿件一个字没变，提交层只看到 revision 相同 → 记为"零改善"。
    于是**结构化诊断里没有"导致 BLOCK 的直接原因"**，只能翻 `step01.log`。
    这里把调用失败与"改了但没进展"分开落台账；`usage` 一并留下，为上限该设多大提供实测依据。
    **口径（2026-09-26 复核更正）**：`output_tokens` 已包含思维链 token，`reasoning_tokens`
    只是它内部的明细——余量只看 `output_tokens`（见 `_repair_cap_margin`），
    两个数相加会把思维链扣两遍。
    """
    if repair_diag is None:
        return
    _u = dict(usage or {})
    repair_diag.append({
        "repair_round": repair_round,
        "kind": "call_failed",          # 与"accepted/zero_gain"那类记录区分开
        "truncated": bool(truncated),
        "error": error[:400],
        "usage": _u,
        "max_tokens": max_tokens,       # 本轮**请求**的上限；None = 未收窄（跟随通道全局值）
        "reasoning_tokens": _u.get("reasoning_tokens"),
        "output_tokens": _u.get("output_tokens") or _u.get("completion_tokens"),
    })


def _record_repair_unusable(repair_diag: list | None, repair_round: int, *,
                            reason: str, detail: str = "",
                            raw_head: str = "",
                            extra: dict | None = None) -> None:
    """记一次"拿到了完整响应、但补丁不可用"（`kind="patch_unusable"`）.

    v6.133（2026-09-26 用户复核）：此前只有 **API 调用失败/截断**才算 `call_failed`，
    而「调用成功但补丁 JSON 解析失败 / envelope 结构无效 / 没返回任何替换 /
    替换全被校验拒」这四条早退路径**只打日志**——提交层看不到记录，
    于是一律报 `no_progress`，把"收到不可用响应"说成了"修复没有进展"。
    现在这四条各带 `reason` 落台账，提交层据此把它报成 `patch_unusable`。

    `reason` 取值：`parse_failed` / `invalid_structure` / `empty_patch` /
    `rejected_by_validation`（另可用 `budget_exhausted` 之类后续扩）。

    `raw_head`：**响应原文的头部**（封顶 `_DIAG_RAW_HEAD_MAX`）。与审计侧同一条理由：
    只留一句错误文案时，"模型回了一段散文"和"JSON 断在中间"事后分不开。
    """
    if repair_diag is None:
        return
    _raw = str(raw_head or "")
    _rec = {
        "repair_round": repair_round,
        "kind": "patch_unusable",
        "reason": str(reason),
        "detail": str(detail)[:300],
        "raw_text_head": _raw[:_mfa._DIAG_RAW_HEAD_MAX],
        "raw_text_len": len(_raw),
    }
    # M02：`detail` 只留前 3 条（日志可读），完整清单走结构化字段——
    # 「哪几个目标因为哪几条路径被拒」是台账要能逐条回答的问题。
    for _k, _v in (extra or {}).items():
        _rec[str(_k)] = _v
    repair_diag.append(_rec)


def _repair_max_output_tokens() -> int | None:
    """修复轮的输出上限：**默认为 `None`** = 跟随所在文本通道的全局上限（不额外收窄）.

    v6.133（用户 2026-09-25，实跑 BLOCK 根因）：这里原来硬编码 `16000`，注释理由是
    "agent_plan 的全局上限是 32768，16k 不触顶，留了 2 倍余量"——该假设在**文本通道切到
    原生 DeepSeek 后不再成立**，而 `.env.content` 对修复轮的推荐值本来就是 **50000**：

      - `DEEPSEEK_MAX_TOKENS=50000`，其注释写明"推荐分镜生成场景：40000-50000；
        钩子：8000；**修复轮：50000**"；
      - 且 `max_output_tokens` **包含思维链 token**（provider 注释与审计路径实测同证）。

    ⚠️ **适用范围**（2026-09-26 复核）：只有 **DeepSeek**（`req.max_tokens or
    config.DEEPSEEK_MAX_TOKENS`）与 **AgentPlan**（`AGENT_PLAN_TEXT_MAX_TOKENS`）
    这两条通道有项目级回退，默认 `None` 才等于"跟随通道全局值"；**DashScope / Agnes
    把 `req.max_tokens` 原样透传**，`None` 不会回退到任何项目上限（交给服务端默认）。
    文本通道切到这两条时，下面这段的前提不成立。

    实测（2026-09-25，`.tmp/repair_truncation_probe.py`，真实修复路径两次调用）：
    同一请求同一 16000 上限，**一次截断**（`incomplete_details.reason=max_output_tokens`，
    整份 JSON 丢弃 → 稿件零变化 → 提交层报"零改善"→ BLOCK）、**一次成功**，
    成功那次 `output_tokens=8172`（其中思维链 3695；**余量按 `output_tokens` 算**：
    16000−8172≈7.8k，不能再减一次 `reasoning_tokens`）；把上限放到
    32768 后两次都成功（`output 7553 / reasoning 2380`）。⇒ 16k 只是"典型调用的 2 倍"，
    思维链一长就踩线；这跟同一天审计路径 17:33 的截断是**同一个根因**（当时 16000 → 32000）。

    保留 `REPAIR_MAX_OUTPUT_TOKENS` 作为**显式收窄**的手段（设了才生效，值非法则 WARN 后
    回退为不传——宁可放宽也不要因为一个手误把上限变小）。
    """
    _v = os.getenv("REPAIR_MAX_OUTPUT_TOKENS", "").strip()
    if not _v:
        return None
    try:
        _n = int(_v)
    except ValueError:
        _mrt._logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_v!r} 不是整数，本轮不设上限"
                     f"（跟随文本通道全局值）")
        return None
    # v6.133（2026-09-26 用户复核）：本变量**只用于收窄**——`0`/负数不是合法上限，
    # 高于当前通道项目上限的值会**放宽**上限（与说明矛盾）。两者都不接受：
    # 记 WARN 后当作"未设置"，跟随通道全局值（宁可放宽也不因手误把上限变小，
    # 更不能因为一个手误把契约说反）。
    if _n <= 0:
        _mrt._logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_v!r} 不是正数，本轮不设上限"
                     f"（跟随文本通道全局值）")
        return None
    _chan_cap = _channel_text_max_tokens()
    if _chan_cap and _n > _chan_cap:
        _mrt._logger.warn(f"[WARN] REPAIR_MAX_OUTPUT_TOKENS={_n} 高于当前文本通道"
                     f"（{_mfa._active_text_provider() or '未知'}）的项目上限 {_chan_cap}——"
                     f"该变量只用于收窄，本轮忽略它（跟随通道全局值 {_chan_cap}）")
        return None
    return _n


def _channel_text_max_tokens() -> int | None:
    """当前文本通道的**项目级**输出上限；该通道没有项目级上限时返回 `None`.

    DeepSeek / AgentPlan 在 provider 里做 `req.max_tokens or config.X` 回退；
    DashScope / Agnes 把 `req.max_tokens` 原样透传（没有项目级上限）——
    对它们而言"收窄"没有可比较的基准，`REPAIR_MAX_OUTPUT_TOKENS` 原样生效。
    """
    _p = (_mfa._active_text_provider() or "").strip().lower()
    try:
        from _text_gen.config import config as _tcfg
    except Exception:
        return None
    if _p == "deepseek":
        return getattr(_tcfg, "DEEPSEEK_MAX_TOKENS", None)
    if _p == "agent_plan":
        return getattr(_tcfg, "AGENT_PLAN_TEXT_MAX_TOKENS", None)
    return None


def _repair_cap_margin(record: dict) -> int | None:
    """单条修复调用记录的**剩余上限**（`max_tokens - output_tokens`）；数不全则 `None`.

    **口径（2026-09-26 用户复核更正）**：`output_tokens`（= `completion_tokens`）
    **已经包含**思维链 token，`reasoning_tokens` 只是它内部的明细
    （DeepSeek 官方把后者列在 `output_tokens_details` 下）——所以**不能**再减一次
    `reasoning_tokens`，那会把思维链扣两遍。两个数都留在台账里：
    `output_tokens` 用来算余量，`reasoning_tokens` 用来看思维链占比。
    """
    _cap = record.get("max_tokens")
    _out = record.get("output_tokens") or record.get("completion_tokens")
    if _cap is None or _out is None:
        return None
    try:
        return int(_cap) - int(_out)
    except (TypeError, ValueError):
        return None


def _record_repair_call_usage(repair_diag: list | None, repair_round: int, *,
                              usage: dict | None = None,
                              max_tokens: int | None = None) -> None:
    """记一次**成功**的修复调用的 token 用量（`kind="call_ok"`）.

    用途是把"上限够不够"从感觉变成数字：`REPAIR_MAX_OUTPUT_TOKENS` 是**输出上限**，
    而 DeepSeek Responses 路径上 `max_output_tokens` **包含思维链 token**——所以要看的是
    `max_tokens - output_tokens`（见 `_repair_cap_margin`；**不**再减 `reasoning_tokens`）。
    2026-09-25：同一请求同一 16000 上限，一次 `incomplete/max_output_tokens` 截断、
    一次成功 ⇒ 余量已在临界，抬上限要有数字依据。
    """
    if repair_diag is None:
        return
    _u = dict(usage or {})
    _rec = {
        "repair_round": repair_round,
        "kind": "call_ok",
        "usage": _u,
        "max_tokens": max_tokens,
        "reasoning_tokens": _u.get("reasoning_tokens"),
        "output_tokens": _u.get("output_tokens") or _u.get("completion_tokens"),
        "total_tokens": _u.get("total_tokens"),
        # v6.152（D2）：**另加**总预算剩余秒数。上面的「余量」是**输出 token 余量**
        # （`max_tokens − output_tokens`），口径不同、保留不动；两者并排放，
        # 才看得出「这次离 token 上限多近」与「本跑还剩多少时间」。
        "budget_left_sec": _mrt._budget_left_sec(),
    }
    repair_diag.append(_rec)
    # 把"余量"直接打进 step01.log：不必翻台账就知道这次离上限多近（口径同上）
    _margin = _repair_cap_margin(_rec)
    _mrt._logger.info("[REPAIR] 输出用量 output_tokens=%s（其中思维链 %s）｜本轮上限 %s"
                 "｜余量 %s｜总预算剩余 %s 秒"
                 % (_rec["output_tokens"], _rec["reasoning_tokens"],
                    max_tokens if max_tokens is not None else "通道全局值",
                    _margin if _margin is not None else "未知",
                    _rec["budget_left_sec"] if _rec["budget_left_sec"] is not None
                    else "未知"))


REPAIR_PROVIDER_ENV = "REPAIR_TEXT_PROVIDER"
REPAIR_MODEL_ENV = "REPAIR_TEXT_MODEL"


def resolve_repair_channel() -> tuple:
    """定向修复的通道覆盖 → `(provider | None, model | None)`；未配置时 `(None, None)`.

    为什么要单开（2026-09-28 实测）：agent_plan 通道把 `json_schema` 降级成
    `response_format=json_object`（`REPAIR_SHOT_REPLACEMENT_SCHEMA` 整体失效），
    `deepseek-v4.1-flash` 在该条件下**整镜原样回抄**——6/6 次 `resolved=0`、
    `audit_field_touched=0`、没有任何「授权字段实际改动」行，修复轮等于不可用；
    原生 deepseek 通道保留 strict json_schema。

    解析语义（三态、缺模型显式抛错）见单一来源 `shared.text_channel.resolve_channel`。
    """
    from shared.text_channel import resolve_channel
    return resolve_channel(REPAIR_PROVIDER_ENV, REPAIR_MODEL_ENV)


def _frame_list_path(frame_id) -> str:
    """`ff` / `lf` / `kfN` → `frame_layouts.ff` / `.lf` / `.kfs[N]`（指不明 = 空串）.

    与 `repair_fields._layout_frame_lists` 同一套坐标（帧键 → 那一帧的人物列表），
    这里只做**方向相反**的换算：从问题定位用的 `frame_id` 得到那一帧的路径。
    """
    _f = str(frame_id or "").strip().lower()
    if _f in ("ff", "lf"):
        return "frame_layouts.%s" % _f
    if _f.startswith("kf") and _f[2:].isdigit():
        return "frame_layouts.kfs[%d]" % int(_f[2:])
    return ""


def _path_covered_by(tid: str, path: str, allowed_by_target: dict,
                     frame_id: str = "") -> bool:
    """`(镜头, 路径)` 是否落在该镜**自己**的授权面里（跨镜同名字段不算覆盖）.

    复核 2026-10-04 P1-3：旧写法对所有入轮目标求"任一路径被任一目标授权"，
    于是**镜 9 的问题会被镜 10 同名字段的授权判成"已覆盖"**——覆盖判据必须绑定镜头身份。

    第三轮的补充：覆盖要认**父帧授权**。实测反例——blocking 定位在同镜 `kf0/pose`，
    另一条审计授权的是那一帧的人物列表 `frame_layouts.kfs[0]`；按列表授权**删掉姿态
    错误的重复条目**是合法修法（真实守卫通过、验收 `accepted=True`），而只按"授权路径
    末段等于 `pose`"判会把这一轮当成零能力**提前跳过**。`frame_id` 正是生产者给的
    那一位身份，用它换算成帧路径去比，不靠猜。

    裸叶子（`field_path` 只写 `pose` / `props`）另按"该镜有哪条授权路径以它收尾"判。
    """
    from step01_script_gen.repair_fields import _norm_keep_index, path_allowed
    _allowed = set((allowed_by_target or {}).get(str(tid or "")) or ())
    if not _allowed:
        return False
    if path and path_allowed(path, _allowed):
        return True
    _frame = _frame_list_path(frame_id)
    if _frame and path_allowed(_frame, _allowed):
        return True
    if path and "." not in path and "[" not in path:
        return any(_norm_keep_index(a).split(".")[-1] == path for a in _allowed)
    return False


def _repair_blocking_coverage(blocking_issues, det_targets, allowed_by_target) -> list:
    """本轮入轮目标里，**能改到某条 blocking 点名字段**的那些目标（空 = 注定零产出）.

    v6.155（2026-10-04 实测根因，交付台账 §19.2）：章 2 提交前的最后一轮，5 条 blocking
    的 `repair_targets` 全为空 ⇒ 它们一条字段也授权不出来，而修复轮照样发起了调用：
    模型按审计建议新建了 `frame_layouts.ff` / `lf`、删了 `kfs` 里的重复条目、改了
    `kfs[1][0].pose` —— 全部被判「未授权路径已回填」，`字段触达=0` ⇒
    `no_target_resolved` ⇒ 整轮回退 ⇒ BLOCK。**这一轮从开场就没有能成功的路径。**

    判据（复核 2026-10-04 P1-3 修正后）分两路，缺一路就会跳过**原本能成功**的轮次：

    ① **确定性阻塞**：它的改善出口是 `before_keys - after_keys`（与路径无关，见
       `_repair_acceptance_decision`）——只要**带确定性阻塞的目标**在本轮拿到任何授权，
       就有改善出口。旧写法只喂模型审计 blocking，于是"审计缺授权 + 同镜确定性姿态问题
       有合法授权"这类轮次被提前返回基线，而真实验收本会给出 `accepted=True, resolved=1`。
    ② **模型审计阻塞**：按 `(镜头, 路径)` 判——`repair_targets` 的每一对用它自己的
       `shot_id`，定位字段用它自己的 `shot_id`；**跨镜同名字段不算覆盖**。

    **不能**拿 `_issue_reaches` 当判据：它答的是「这条问题授权了哪些目标」，而模型审计
    成对目标缺失时它一律返回 False——可本轮完全可能**经由确定性告警**授权了同一条路径
    （实测：`field_path=prompt` 的审计问题，授权来自告警路径，修复轮照样能把它记成已触达）。

    与 `_unauthorized_targets` 同一口径：不放开授权去硬修，只是不再为一个
    不可能成功的轮次付代价。返回的是**参与覆盖的目标名**（供日志与单测断言）。
    """
    from step01_script_gen.repair_fields import _target_id_of
    cover: list = []

    def _add(_tid: str) -> None:
        if _tid and _tid not in cover:
            cover.append(_tid)

    # ① 确定性阻塞：目标带确定性阻塞且本轮有授权 ⇒ 有改善出口
    for _tid in (det_targets or ()):
        if str(_tid) in (allowed_by_target or {}):
            _add(str(_tid))
    # ② 模型审计阻塞：按 (镜头, 路径) 判，绑定各自的镜头身份
    for _ai in (blocking_issues or []):
        _sid = getattr(_ai, "shot_id", None)
        for _p in (getattr(_ai, "repair_targets", None) or []):
            if not isinstance(_p, dict):
                continue
            if _path_covered_by(_target_id_of(_p.get("shot_id")),
                                str(_p.get("field_path") or "").strip(),
                                allowed_by_target):
                _add(_target_id_of(_p.get("shot_id")))
        _fp = str(getattr(_ai, "field_path", "") or "").strip()
        if _fp and _path_covered_by(_target_id_of(_sid), _fp, allowed_by_target,
                                    getattr(_ai, "frame_id", "")):
            _add(_target_id_of(_sid))
    return cover


# v6.160（S03，2026-11-07）：**修复轮按问题依赖选技能段**。
#
# 判定顺序（唯一实现，调用方不得各写一份）：
#   1. 问题文本/错误码命中运镜、打斗、表演、出场词表 → 只加载对应段；
#   2. 否则若问题码属于「关键帧/布局」这类结构类（kf_* / frame_layouts_*），
#      不加载技能——补帧要的是时间窗与现有帧，不是运镜词库；
#   3. 其余（未登记类型）→ 回退完整上下文，并**显式报告**尚未分类，
#      不静默减少模型可见信息。
_REPAIR_SKILL_PATTERNS = (
    ("craft", ("运镜", "景别", "机位", "光位", "逆光", "侧光", "顶光", "顶光位",
               "构图", "转场", "镜头运动", "推镜", "拉镜", "摇镜")),
    ("combat", ("打斗", "招式", "兵器", "出招", "格挡", "劈砍", "挥刀", "挥剑",
                "厮杀", "过招")),
    ("performance", ("表演", "情绪", "表情", "视线", "目光", "凝视", "神态")),
    ("entrance", ("出场", "登场", "亮相", "下轿", "下马")),
)
#: 明确「需要某段技能」的问题码家族（**结构化**判据）。
_REPAIR_SKILL_CODE_FAMILIES = (
    ("craft", ("camera_", "shot_size", "lighting_", "light_", "composition_",
               "transition_", "framing_")),
    ("combat", ("combat_", "fight_", "weapon_")),
    ("performance", ("performance_", "gaze_", "expression_", "emotion_")),
    ("entrance", ("entrance_",)),
)
#: 明确「不需要技能词库」的问题码家族：结构/时间窗/布局/文本/声音/时长/服色。
#: 逐个列家族，不再用宽泛前缀把整片码域吞掉（S03 复核 P1）。
_REPAIR_NO_SKILL_CODE_FAMILIES = (
    "kf_", "frame_layouts_", "layout_", "narration_", "subtitle", "text_",
    "voice_", "duration_", "shot_count", "hook_", "costume_", "wardrobe_",
    "character_color", "character_",
)
#: 审计码是自由文案家族：码本身不带依赖，依赖由 message 判定。
_REPAIR_AUDIT_CODE_FAMILY = "model_audit"


#: S03（2026-11-07）：修复请求的**体积上限**（编码后字节）。0 = 关闭。
#: 默认 120,000：当前 4 镜请求实测 62,610 B，仍在预算内；超过时才按整组拆分。
_REPAIR_MAX_REQUEST_BYTES = int(os.getenv("REPAIR_MAX_REQUEST_BYTES", "120000") or 0)
#: 请求中与目标数量无关的固定部分（system + 任务说明 + 示例 + 技能段）的保守估计。
_REPAIR_FIXED_OVERHEAD_BYTES = int(os.getenv("REPAIR_FIXED_OVERHEAD_BYTES", "35000") or 0)


def _estimate_repair_target_bytes(shot: dict, warnings) -> int:
    """单个目标的**编码后**字节估计（镜头正文 + 告警文本）."""
    import json as _json
    try:
        _shot = len(_json.dumps(shot or {}, ensure_ascii=False).encode("utf-8"))
    except Exception:
        _shot = 0
    _warn = len("\n".join(str(w) for w in (warnings or [])).encode("utf-8"))
    return _shot + _warn


def _repair_skill_sections(picked: dict, audit_by_shot: dict | None = None) -> tuple:
    """返回 (sections, unclassified)。空 sections 表示"本轮确实不需要技能段"。"""
    codes: list = []
    texts: list = []
    for _tid, _ws in (picked or {}).items():
        for _w in (_ws or []):
            _s = str(_w or "")
            texts.append(_s)
            if _s.startswith("[") and "]" in _s:
                codes.append(_s[1:_s.find("]")])
    for _tid, _issues in (audit_by_shot or {}).items():
        for _it in (_issues or []):
            if isinstance(_it, dict):
                codes.append(str(_it.get("code") or ""))
                texts.append(str(_it.get("message") or ""))
    blob = "\n".join(texts)
    # 1) **结构化判据**：按码决定依赖（唯一可验收的口径）。
    sections: set = set()
    unknown: list = []
    for _c in codes:
        _code = str(_c or "").strip()
        if not _code:
            continue
        if _code.startswith(_REPAIR_AUDIT_CODE_FAMILY):
            # 审计码：依赖由 message 判定（已在 blob 里扫过），码本身不作"未知"。
            continue
        _hit = [key for key, prefixes in _REPAIR_SKILL_CODE_FAMILIES
                if _code.startswith(prefixes)]
        if _hit:
            sections.update(_hit)
            continue
        if _code.startswith(_REPAIR_NO_SKILL_CODE_FAMILIES):
            continue
        unknown.append(_code)
    # 2) 文案只做**补充**：命中就往里加；但它不能证明"其它上下文不需要"。
    sections |= {key for key, words in _REPAIR_SKILL_PATTERNS
                 if any(w in blob for w in words)}
    if unknown:
        # 未知类型：保留完整上下文（调用方回退），并明确报告未覆盖。
        _mrt._logger.warn("[REPAIR] 有未登记技能依赖的问题码——本轮保留完整技能上下文：%s"
                     % sorted(set(unknown))[:6])
        return sections, True
    return sections, False


def _related_check_fields_for(target: str, allowed, shots_by_id: dict) -> list:
    """S04：该目标的**关联检查面**（扁平去重；不含授权字段本身）.

    检查面 ≠ 授权面：这里只声明「哪些字段会被一起核对」，授权仍由
    `allowed_repair_fields` 决定。取不到镜头对象或映射失败时返回空表（不阻塞修复）。
    """
    from step01_script_gen.repair_fields import related_check_fields

    shot = (shots_by_id or {}).get(target) or {}
    try:
        mapping = related_check_fields(allowed, shot)
    except Exception:
        return []
    return sorted({p for ps in mapping.values() for p in ps})


def _round_related_readback(before: dict, after: dict, allowed_by_target: dict,
                            *, model_shots: "dict | None" = None,
                            novel_text: str = "") -> dict:
    """S04：一轮修复的**四栏台账**（模型请求 / 实际应用 / 越权回填 / 投影后残余）.

    只报事实：改描述却不动同帧布局，说明这半条修复没做完——语义是否真的修好仍由
    本轮的 `resolved` / 重验与提交点审计判定，这里不代替它们下结论。

    `model_shots`：模型**原始**返回的镜头（`shot_replacements[].shot`）。给了它才能
    区分「模型请求改了什么」与「授权后实际改了什么」；没给时该栏为空表并标注依据。
    """
    from step01_script_gen.repair_fields import (related_check_fields, related_readback,
                                                 shot_by_target)

    rows: list = []
    for tid, allowed in (allowed_by_target or {}).items():
        _b = shot_by_target(before, tid)
        _a = shot_by_target(after, tid)
        if not isinstance(_b, dict) or not isinstance(_a, dict):
            continue
        _rel = related_check_fields(allowed, _b)
        if not _rel:
            continue
        _applied = related_readback(_b, _a, _rel)["targets"]
        _model = (model_shots or {}).get(tid)
        if _model is None:
            # `allowed_by_target` 的键是 `shot_4` / `hook`，模型返回的是裸 id
            _bare = str(tid)[5:] if str(tid).startswith("shot_") else str(tid)
            _model = (model_shots or {}).get(_bare)
        _requested = (related_readback(_b, _model, _rel)["targets"]
                      if isinstance(_model, dict) else {})
        for _tid2, _info in _applied.items():
            _req = (_requested.get(_tid2) or {}).get("changed_fields") or []
            _app = _info.get("changed_fields") or []
            rows.append({
                "target": _tid2,
                "target_changed": bool(_info.get("target_changed")),
                "requested": _req,                       # 模型请求变化
                "applied": _app,                         # 实际应用变化
                "reverted": [p for p in _req if p not in _app],   # 越权回填
                "half_repaired": bool(_info.get("target_changed")
                                      and _info.get("unchanged_fields")),
                "unchanged_fields": _info.get("unchanged_fields") or [],
            })
    # 投影后残余：把本轮结果跑一次 project_for_commit，看关联字段是否被投影再改
    residual = _projection_related_residual(after, allowed_by_target, novel_text)
    half = [{"target": r["target"], "fields": r["unchanged_fields"]}
            for r in rows if r["half_repaired"]]
    return {"targets": rows,
            "requested_basis": ("shot_replacements[].shot" if model_shots
                                else "unavailable"),
            "checked": sum(len(r["applied"]) + len(r["unchanged_fields"]) for r in rows),
            "changed": sum(len(r["applied"]) for r in rows),
            "reverted": sum(len(r["reverted"]) for r in rows),
            "half_repaired": half,
            "residual_after_projection": residual}


def _projection_related_residual(script: dict, allowed_by_target: dict,
                                 novel_text: str = "") -> dict:
    """投影后回读：`project_for_commit` 之后关联字段还剩哪些改动.

    投影会做派生字段同步与 hard-fix 归一化，**可能再次改写**关联字段；只看合并后的
    状态会漏掉这一层。失败不改变本轮结论，只把 `error` 记进台账。
    """
    from step01_script_gen.repair_fields import (related_check_fields, related_readback,
                                                 shot_by_target)
    if not allowed_by_target:
        return {"checked": 0, "changed": 0, "rows": [], "note": "no_related_scope"}
    try:
        projected, errs = _mfz.project_for_commit(script, novel_text, label="修复轮回读")
    except Exception as exc:                     # noqa: BLE001 - 诊断失败不改结论
        return {"checked": 0, "changed": 0, "rows": [], "error": str(exc)[:160]}
    if errs:
        return {"checked": 0, "changed": 0, "rows": [],
                "error": "; ".join(str(e) for e in errs)[:160]}
    rows: list = []
    for tid, allowed in (allowed_by_target or {}).items():
        _a = shot_by_target(script, tid)
        _p = shot_by_target(projected, tid)
        if not isinstance(_a, dict) or not isinstance(_p, dict):
            continue
        _rel = related_check_fields(allowed, _a)
        if not _rel:
            continue
        for _tid2, _info in related_readback(_a, _p, _rel)["targets"].items():
            rows.append({"target": _tid2,
                         "changed_fields": _info.get("changed_fields") or [],
                         "unchanged_fields": _info.get("unchanged_fields") or []})
    return {"checked": sum(len(r["changed_fields"]) + len(r["unchanged_fields"])
                           for r in rows),
            "changed": sum(len(r["changed_fields"]) for r in rows),
            "rows": rows}
