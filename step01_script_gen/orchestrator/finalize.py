# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.finalize —— 确定性定稿与提交投影（finalize / 折叠重复 / project_for_commit / 钩子开关）.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
import copy

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import repair_apply as _mra
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
class FinalizeError(RuntimeError):
    """`finalize_step01_script` 返回了**非空错误**（时间轴不自洽 / 声音身份不成立）.

    为什么要一个专门的类型（2026-10-28）：提交入口原来把 `_finalize` 的失败写成
    `raise ValueError(...)`，再在调用点 `except ValueError` 转成 `finalize_failed`。
    于是 finalize **内部**或它调用的任何代码抛出的 `ValueError`（例如时间轴/窗口
    计算的普通取值错误）都会被记成「finalize 失败」，掩盖真实原因。
    只有这个类型代表「finalize 正常返回、但报告了错误」；其它异常一律向上抛，
    由调用方按崩溃处理，**不**折算成内容结论。
    """


def _is_hook_enabled() -> bool:
    """运行期读取 HOOK_ENABLED（S16-1 根因 B）.

    `HOOK_ENABLED` 是 import 期常量；同一进程里先 import main、再改
    `os.environ["HOOK_ENABLED"]` 时，提交投影仍按旧值决定 hook 去留，与新进程
    对同一份输入的结果不同（M09 跨进程 revision 分叉）。提交点改为动态读取：
    env 存在时以 env 为准，缺失时回退 import 期常量。
    """
    if _mrt.HOOK_ENABLED != _mrt._HOOK_ENABLED_DEFAULT:
        return bool(_mrt.HOOK_ENABLED)
    raw = os.environ.get("HOOK_ENABLED")
    if raw is None:
        return bool(_mrt.HOOK_ENABLED)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _hook_max_sends() -> int:
    """运行期读取**钩子分类发送预算**（0..3；缺失/非法回退 import 期常量）.

    2026-11-15（P05 实测）：钩子初始 1 次 + 重复重试 2 次 = 3 次，全部走同一通道；
    固定输入验证需要把它收窄（如 1），否则审计/修复额度会被钩子挤占。
    """
    if _mrt.HOOK_MAX_SENDS != _mrt._HOOK_MAX_SENDS_DEFAULT:
        return max(0, int(_mrt.HOOK_MAX_SENDS))
    raw = os.environ.get("HOOK_MAX_SENDS")
    if raw is None:
        return max(0, int(_mrt.HOOK_MAX_SENDS))
    try:
        return max(0, min(3, int(str(raw).strip())))
    except (TypeError, ValueError):
        return max(0, int(_mrt.HOOK_MAX_SENDS))


def _sync_missing_onscreen_layout_entries(script: dict) -> int:
    """确定性补齐 ff/lf/kfs 中「描述明确在场、布局列表漏登记」的角色条目.

    只补结构化 layout，不改描述/台词；角色名来自本次输入 characters[].identifier。
    否定语境（不见/没有/未出现/空无一人/离开/消失）不补。
    """
    import copy as _copy
    import re as _re
    names = [str(c.get("identifier") or "").strip()
             for c in (script.get("characters") or []) if isinstance(c, dict)]
    names = [n for n in names if n]
    if not names:
        return 0

    def _visible(name: str, text: str) -> bool:
        if not text:
            return False
        for m in _re.finditer(_re.escape(name), text):
            prefix = text[max(0, m.start() - 12):m.start()]
            # 2026-11-10：画外音（「画外听香欲呼传来」）是**不在画面内**的声源，
            # 旧写法把她补成 on_screen 布局条目（position 退化成「画面内」），
            # 触发 multi_person_staging 视觉风险——重放已验收稿会新引入 blocking。
            if any(neg in prefix for neg in ("不见", "没有", "未出现", "空无一人",
                                             "离开", "消失", "并未", "不在",
                                             "画外", "幕后")):
                continue
            return True
        return False

    def _position(name: str, text: str, fallback: str) -> str:
        for token in ("画中偏左", "画中偏右", "画左", "画右", "画中",
                      "船头", "船尾", "岸边", "水面", "前景", "背景",
                      "左侧", "右侧"):
            if token in text:
                return token
        return fallback or "画面内"

    def _pose(name: str, text: str, fallback: str) -> str:
        m = _re.search(_re.escape(name) + r"([^，。；]{1,18})", text or "")
        if m:
            frag = _re.sub(r"^(的|是|在|正|已|仍|还)+", "", m.group(1)).strip()
            frag = _re.sub(r"(可见|出现|位于|站在|坐在|跪在|躺在)$", "", frag).strip()
            if frag:
                return frag[:18]
        return fallback or "按本帧描述"

    def _entry_for(name, text, existing, seed):
        base = _copy.deepcopy(existing or seed or {})
        support = base.get("support") or {}
        if not support:
            for cand in (seed or {}).get("_all_entries") or []:
                if isinstance(cand, dict) and cand.get("support"):
                    support = _copy.deepcopy(cand["support"])
                    break
        return {
            "name": name,
            "on_screen": True,
            "position": _position(name, text, str(base.get("position") or "")),
            "pose": _pose(name, text, str(base.get("pose") or "")),
            "gaze_target": str(base.get("gaze_target") or "画面内"),
            "props": _copy.deepcopy(base.get("props") or []),
            "support": _copy.deepcopy(support or {}),
            "states": list(dict.fromkeys(list(base.get("states") or []) + ["在场"])),
        }

    added = 0
    prev_entries = []
    for shot in script.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        layouts = shot.get("frame_layouts")
        if not isinstance(layouts, dict):
            layouts = {}
            shot["frame_layouts"] = layouts
        all_entries = []
        for key in ("ff", "lf"):
            if isinstance(layouts.get(key), list):
                all_entries.extend(layouts[key])
        for kf_list in (layouts.get("kfs") or []):
            if isinstance(kf_list, list):
                all_entries.extend(kf_list)
        for e in all_entries:
            if isinstance(e, dict) and e.get("on_screen", True):
                e["_all_entries"] = all_entries
        known = {str(e.get("name") or ""): e for e in all_entries if isinstance(e, dict)}
        known.update({str(e.get("name") or ""): e for e in prev_entries
                      if isinstance(e, dict) and e.get("name")})
        frames = [("ff", str(shot.get("ff_desc") or "")),
                  ("lf", str(shot.get("lf_desc") or ""))]
        kfs = shot.get("keyframes") or []
        if not isinstance(layouts.get("kfs"), list):
            layouts["kfs"] = []
        kf_layouts = layouts["kfs"]
        for i, kf in enumerate(kfs):
            if isinstance(kf, dict):
                frames.append(("kfs[%d]" % i, str(kf.get("desc") or "")))
        for fkey, text in frames:
            if not text:
                continue
            if fkey.startswith("kfs["):
                idx = int(fkey[4:-1])
                while len(kf_layouts) <= idx:
                    kf_layouts.append([])
                target = kf_layouts[idx]
                if not isinstance(target, list):
                    target = []
                    kf_layouts[idx] = target
            else:
                target = layouts.get(fkey)
                if not isinstance(target, list):
                    target = []
                    layouts[fkey] = target
            present = {str(e.get("name") or "") for e in target if isinstance(e, dict)}
            for name in names:
                if name in present or not _visible(name, text):
                    continue
                entry = _entry_for(name, text, known.get(name),
                                   {"_all_entries": all_entries})
                entry.pop("_all_entries", None)
                target.append(entry)
                known[name] = entry
                added += 1
                _mrt._logger.info("[FINALIZE] 自动补齐漏登记的在场角色布局条目: shot=%s frame=%s name=%s"
                             % (shot.get("index"), fkey, name))
        for e in all_entries:
            if isinstance(e, dict):
                e.pop("_all_entries", None)
        prev_entries = [e for e in (layouts.get("lf") or []) if isinstance(e, dict)]
    return added


def project_for_commit(script: dict, novel_text: str = "", *, label: str = "落盘前",
                       repair_effects=None) -> tuple[dict, list[str]]:
    """提交点**实际会审计并落盘**的投影——唯一实现.

    2026-10-28（M07）：离线交付工具与提交入口此前各跑一遍 finalize，于是同一个候选
    出现两个 revision（离线 `73726ff…` / 提交 `8c7ab915…`），而审计只对后者有效；
    「离线通过」与「审计通过的同一 revision」因此对不上。现在两边都调这一个函数：

      ① hook 归一（`HOOK_ENABLED` 关闭时丢弃钩子——缓存稿/历史快照可能仍带旧钩子）；
      ② `finalize_step01_script`（就地写，所以先复制）；
      ③ 剥运行字段（`_strip_runtime_fields`）并绑定 revision。

    Returns `(projected, errors)`；`errors` 非空表示 finalize **报告了错误**，调用方
    必须拒收（提交入口据此 BLOCK/finalize_failed）。异常不被折算成错误字符串。
    """
    _s = copy.deepcopy(script)
    if not _is_hook_enabled():
        _s.pop("hook", None)
    _s, _errs = finalize_step01_script(_s, novel_text=novel_text, label=label,
                                       repair_effects=repair_effects)
    if _errs:
        return _s, [str(e) for e in _errs]
    # 显式修复效果（repair_effects）可能是在删除某角色的布局条目；此时不得
    # 用描述提及反向补回，否则会把用户的删除决定覆盖掉。常规提交才启用自愈。
    _autofill = str(os.getenv("STEP01_LAYOUT_AUTOFILL", "1")).strip().lower() not in (
        "0", "false", "off", "no")
    _added = (0 if (repair_effects is not None or not _autofill)
              else _sync_missing_onscreen_layout_entries(_s))
    if _added:
        _mrt._logger.info("[FINALIZE] 布局自愈共补齐 %d 条在场角色条目" % _added)
    _s = _msc._strip_runtime_fields(_s)
    _mrt.ensure_script_revision(_s)
    return _s, []


def collapse_repetition_fields(script: dict) -> int:
    """折叠各文本字段里**连续 ≥3 次**的重复词/句组；返回改动字段数（v6.115）.

    为什么由 `finalize` 兜底：三个入口（正常生成 / 缓存命中 / BEST-GUARD 恢复）
    都经过 `finalize_step01_script`，而**历史快照会绕过 `post_fix_shot_prompts`**
    ——实测 run 18 的 BLOCK 快照里 shot 8 仍有 6 个连续「眼神」与 6 组重复短句
    （折叠规则本身没失效，是旧快照没走早段）。

    必须在 `write_timeline_from_durations`（时间派生）**之前**调用——`motion_desc`
    含时段标记，被时间屏障视为时间字段，晚写会让已派生的 timecode 失配。
    """
    from shared.content_safety import collapse_repetition as _cr
    changed = 0
    units = list(script.get("shots") or []) + [script.get("hook")]
    for shot in units:
        if not isinstance(shot, dict):
            continue
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = shot.get(fld)
            if isinstance(v, str) and v:
                nv = _cr(v)
                if nv != v:
                    shot[fld] = nv
                    changed += 1
        for kf in shot.get("keyframes") or []:
            if isinstance(kf, dict):
                v = kf.get("desc")
                if isinstance(v, str) and v:
                    nv = _cr(v)
                    if nv != v:
                        kf["desc"] = nv
                        changed += 1
    return changed


def finalize_step01_script(script: dict, *, novel_text: str = "",
                           label: str = "落盘前",
                           repair_effects=None) -> tuple[dict, list[str]]:
    """时间收口：hard-fix（含阶段屏障）→ timeline 序列化 → 最终时间不变量.

    三条路径（正常生成 / 缓存命中 / BEST-GUARD 恢复）此前各写一遍
    「hard-fix → write_timeline_from_durations → 一致性检查」，顺序与守卫各不
    相同（缓存命中路径曾长期漏跑 hard-fix）。这里收成一个入口，任何入口都不能
    绕过阶段屏障与最终不变量检查。

    **顺序是契约的一部分**：最终时间不变量必须在**最后一个**时间写入者之后判定
    ——即 hard-fix 与 `write_timeline_from_durations`（`duration_sec` /
    `timecode` 的派生点）都跑完之后。

    Returns:
        (script, errors)：errors 为空表示时间轴自洽；非空由调用方决定是告警
        （正常 / 缓存路径）还是放弃（BEST-GUARD 恢复）。
    """
    from shared.narration_voice import narration_role
    _voice_errors = []
    for _ni, _entry in enumerate((script.get("chapter_outline") or {}).get("narration_lines") or [], 1):
        if isinstance(_entry, dict):
            try:
                narration_role(_entry, script.get("characters") or [])
            except ValueError as exc:
                _voice_errors.append("N%d: %s" % (_ni, exc))
    if _voice_errors:
        return script, _voice_errors

    # v6.115：早段清理兜底（时间派生之前；幂等，已清理过的稿子零改动）
    # §14 P3：`repair_effects` 是提交路径带过来的**只读**效果视图（修复轮通过验收
    # 的布局增删）。它让「本轮已授权删除的条目不得按类别别名重建」这条规则在这一趟
    # `is_final_pass=True` 的 hard-fix 上**同样成立**——只保证修复轮内成立的话，
    # 合法删除会在 finalize 被 `group_actor_continuity` 补回，下一轮审计重报
    # blocking，又判零改善。缺省 `None` ⇒ 约束行为**逐字不变**（三条入口共用本函数）。
    try:
        _n_rep = collapse_repetition_fields(script)
        if _n_rep:
            _mrt._logger.info(f"[FINALIZE] 折叠连续重复词句 {_n_rep} 处（{label}）")
    except Exception as _rep_err:
        _mrt._logger.warn(f"[FINALIZE] 重复折叠失败（保留原版）: {_rep_err}")

    from step01_script_gen.constraints import run_hard_fix as _hfix_final
    from shared.semantic_barrier import TimeFieldWriteTracker
    from step01_script_gen.quality_issues import final_time_invariant_issues

    tracker = TimeFieldWriteTracker(script)
    try:
        script = _hfix_final(script, is_in_repair_loop=False, is_final_pass=True,
                             novel_text=novel_text,
                             on_constraint_done=tracker.observe,
                             repair_effects=repair_effects)
    except Exception as _hfe:
        # 环境故障（依赖缺失 / 语法不兼容 / 名字未定义）意味着**约束链根本没跑完**。
        # 既不能降级成「保留原版」，更不能让后续提交照常写正式产物——2026-10 复核 P0：
        # `engine.apply_constraints` 上抛后若在此吞掉，排在该约束后面的全部约束都被
        # 连带跳过，而流程照旧打出 `[COMMIT] OK 三份产物已提交`。必须 fail-closed。
        from step01_script_gen.constraints.engine import (ENV_FAULT_TYPES,
                                                          fail_closed_types)
        if isinstance(_hfe, ENV_FAULT_TYPES):
            raise
        if isinstance(_hfe, fail_closed_types()):
            # 合同不可消费（改编块/呈现契约）同样等于「约束链没跑完」：返回错误让调用方
            # 拒收，不把「一半约束没跑」的稿子当成通过（2026-10-28 M07 实测的 155/400
            # 两份对象就是从这里来的）。
            _mrt._logger.error(f"[WARN] {label} hard_fix 因合同不可消费而中止: {_hfe}")
            return script, ["constraint_failed: %s: %s" % (type(_hfe).__name__, _hfe)]
        _mrt._logger.warn(f"[WARN] {label} hard_fix 失败（保留原版）: {_hfe}")
    _mra._record_time_field_write_audit(script, tracker, label=label)
    # v6.98（审核 P0-3）：时间字段之外，审计段的**语义/结构**写入同样要收口。
    _mra._record_semantic_write_audit(script, tracker, label=label)

    errors: list[str] = []
    try:
        _mrt.write_timeline_from_durations(script)
        _tl_errs = _mrt.timeline_consistency_errors(script)
        if _tl_errs:
            errors.extend(str(e) for e in _tl_errs)
    except Exception as _tl_e:
        errors.append(f"timeline 序列化失败: {_tl_e}")

    # 最后一个时间写入者之后：查「帧是否落在镜内」「时长是否单一口径」
    _inv = final_time_invariant_issues(script)
    if _inv:
        errors.extend(f"{i.code}@shot{i.shot_id}: {i.message}" for i in _inv)
        _mrt._logger.warn(
            f"[WARN] {label} 最终时间不变量不成立（{len(_inv)} 条，已交 quality gate "
            f"判 blocking）：{[i.code for i in _inv][:5]}"
        )
    if errors and not _inv:
        _mrt._logger.warn(f"[WARN] {label} 最终时间轴归一失败: {'; '.join(errors[:3])}")
    return script, errors
