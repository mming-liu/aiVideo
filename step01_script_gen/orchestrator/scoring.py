# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.scoring —— 告警收集、候选质量摘要与打分、质量门.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import re

from step01_script_gen.orchestrator import runtime as _mrt
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
# v6.98（2026-09-22 审核 P1-2 复核确认）：完整性身份 = **与镜头序号无关**的稳定指纹。
#
# 只比数量的漏洞（确定性复现）：旧稿有问题 A、新稿修掉 A 而引入问题 B，数量仍是 1
# → 判「未退化」。对状态回退 / 角色 / 武器归属 / 事件结果这类问题，**身份**比数量重要。
#
# 为什么**不含** `shot_id`：整脚本重生成会重排镜头序号（合并 15 镜 → 13 镜），
# 带序号的身份会让**每个**问题都变成「新身份」→ 采纳分支永不通过（这正是本会话
# 反复修的那类「永不收敛」缺口）。序号剔除后，语义是「**同一类问题换了镜头**不算
# 新问题，**换了问题**才算」——正是要判的那件事。
_INTEGRITY_NUM_RE = re.compile(r"[0-9]+(?:[.][0-9]+)?")


def _blocking_identity(issue) -> str:
    """blocking 问题身份：code + 帧 + 实体 + 字段 + **去数字后的消息**.

    消息里的数字（镜头号、秒数、字数）全部归一成 `#`——它们随重排/收敛而变，
    不属于「问题身份」。

    v6.99（2026-09-23 二次审核 P0-A）：**所有 blocking 都走这一个身份函数**。
    原实现只给 9 个 `integrity_codes` 建身份，于是「统一验收」名不副实：
    `kf_time_out_of_shot` / `kf_layout_count_mismatch` / `kf_event_window_mismatch`
    等不在那 9 个码里的 blocking，**新稿引入它们照样被采纳**（确定性反例见审核
    文档第八节 P0-A：候选只多一个 `t_sec=9.0` 的 KF，`_accept_regen_candidate`
    返回 `(True, [])`，而其中 `kf_time_out_of_shot` 是 hard）。
    """
    msg = str(getattr(issue, "message", "") or "")
    msg = _INTEGRITY_NUM_RE.sub("#", msg)
    parts = (
        str(getattr(issue, "code", "") or ""),
        str(getattr(issue, "frame_id", "") or ""),
        str(getattr(issue, "entity_id", "") or ""),
        str(getattr(issue, "field_path", "") or ""),
        msg,
    )
    return "|".join(parts)


def _collect_repair_warnings(script: dict, novel_text: str) -> dict:
    """Return structured blocking issues for targeted repair.

    Candidate ranking, repair selection and the final gate all consume the same
    ``collect_structured_issues`` result.  Legacy callers still receive the
    historical ``{_has_hard, shots}`` shape.
    """
    if novel_text:
        _mrt._set_novel_text(novel_text)
    issues, checker_failed, rule_version = _mrt.collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_mrt._run_prompt_checklist,
    )
    per_shot: dict = {}
    for issue in issues:
        if not _mrt.is_blocking(issue):
            continue
        ids = []
        if issue.shot_id is not None:
            ids.append(issue.shot_id)
        ids.extend(issue.related_shot_ids or [])
        if not ids:
            per_shot.setdefault("global", []).append(issue.message)
            continue
        for sid in ids:
            if str(sid) in ("0", "hook"):
                per_shot.setdefault("hook", []).append(issue.message)
            else:
                per_shot.setdefault(f"shot_{sid}", []).append(issue.message)
    return {
        "_has_hard": any(_mrt.is_blocking(i) for i in issues),
        "shots": per_shot,
        "checker_failed": checker_failed,
        "rule_version": rule_version,
        "rule_source_fingerprint": _mrt.RULE_SOURCE_FINGERPRINT,
        "duration_band": _mrt.duration_band(),
        "issues": [i.to_dict() for i in issues],
    }


# hook 质量门只消费高价值 warning（避免普通告警触发重生循环）。
# gaze_state_conflict 是 09-13 实证会烧穿 QC 的确定性矛盾：闭眼角色 gaze 非无。
_HOOK_QC_WARNING_CATEGORIES = {"gaze_state_conflict"}


def _issue_from_record(rec: dict) -> _mrt.Issue:
    """`Issue.to_dict()` 记录 → `Issue` 对象（授权推导要它的 `field_path`）."""
    fields = getattr(_mrt.Issue, "__dataclass_fields__", {})
    return _mrt.Issue(**{k: v for k, v in (rec or {}).items() if k in fields})


def _collect_hook_issues(script: dict) -> list[str]:
    """Return all blocking issues attached to the hook shot."""
    issues, checker_failed, _ = _mrt.collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_mrt._run_prompt_checklist,
    )
    out: list[str] = []
    if checker_failed:
        out.append("[CHECK_FAILED] 质量检查器异常")
    for issue in issues:
        if str(issue.shot_id) not in ("0", "hook"):
            continue
        if _mrt.is_blocking(issue):
            out.append(f"[{issue.code}] {issue.message}")
    return out


_QUALITY_BLOCKING_CATEGORIES = {
    "gaze_state_conflict",
    "weapon_holder",
    "kf_temporal_consistency",
    "kf_time_window_alignment",
    "kf_frame_redundancy",
    "frame_layouts_required",
    "prompt_length_budget",
    "person_boat_position",
    "multi_person_staging",
    "ff_lf_redundancy",
    "cross_shot_continuity",
    "ff_desc_length",
    "text_integrity",
}


_VIDEO_ONLY_CATEGORIES = {
    "camera_movement",
    "camera_upgrade",
    "transition_keywords",
    "focal_switch",
    "prompt_length_budget",
}


def _collect_text_integrity_issues(script: dict) -> list[str]:
    """Deterministic structural integrity scan for model-visible fields."""
    issues: list[str] = []
    shots = list(script.get("shots") or [])
    targets = [(f"镜头{shot.get('index', i + 1)}", shot) for i, shot in enumerate(shots)]
    hook = script.get("hook")
    if isinstance(hook, dict):
        targets.append(("钩子镜头(index=0)", hook))
    _fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")
    _injury_words = (
        "刺中", "刺入", "中刀", "中剑", "心口", "胸口", "暗红", "洇痕",
        "血痕", "血迹", "血染", "伤口", "断气", "身亡", "尸体", "昏迷",
    )
    _open_eye_words = (
        "双目圆睁", "双眼圆睁", "双目大睁", "双眼大睁", "双目暴睁", "双眼暴睁",
    )
    for label, shot in targets:
        if not isinstance(shot, dict):
            continue
        # Context is shot-level.  A body-state phrase in ff_desc is valid when
        # an adjacent field such as lf_desc already records the wound.
        shot_text = " ".join(
            str(shot.get(f) or "") for f in (
                *_fields, "main_subject", "narration_segment", "subtitle"
            )
        )
        for field in _fields:
            text = shot.get(field)
            if not isinstance(text, str) or not text.strip():
                continue
            for err in _mrt.text_integrity_errors(text):
                issues.append(f"{label}.{field}: {err}")
            if (("双目紧闭" in text or "头颈僵直" in text)
                    and any(w in text for w in _open_eye_words)):
                issues.append(f"{label}.{field}: 闭眼/睁眼状态矛盾")
            if "仰面朝上双目紧闭" in text and not any(
                w in shot_text for w in _injury_words
            ):
                issues.append(f"{label}.{field}: 无伤情上下文却写死亡头态")
    return issues


def _temporal_plan_stats(script: dict) -> dict:
    """结构化事件表的**迁移期统计**：present / valid / grounded（二次审核发现 6）.

    决定 6：`temporal_plan` 继续保持**只读**，不为了覆盖率强制启用结构写入。
    但要先积累真实样本——统计每个候选里有多少镜头带了事件表、其中多少通过严格
    校验（整镜覆盖 / 连续性 / 与 motion 一致）、多少条的 `source_quote` 能**在本镜
    文本内**找到。达到稳定阈值后再决定是否把 schema 改成 required 或让 shot-plan
    消费它。

    `grounded` 是**本镜文本内**的定位代理（候选期通常拿不到章节原文）；它比
    `validation._check_temporal_plan` 的「本镜原文窗口」检查弱一档，只用于统计
    趋势，不参与任何判定——所以不会因为「统计值低」而改变流水线行为。
    """
    from shared.motion_segments import validate_events

    stats = {"shots": 0, "present": 0, "valid": 0, "grounded": 0}
    units = [script.get("hook")] if isinstance(script.get("hook"), dict) else []
    units += [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    for shot in units:
        stats["shots"] += 1
        plan = shot.get("temporal_plan")
        if not isinstance(plan, dict) or not plan.get("events"):
            continue
        stats["present"] += 1
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        motion = str(shot.get("motion_desc") or "")
        if validate_events(plan.get("events"), dur, motion_desc=motion or None):
            continue
        stats["valid"] += 1
        _own_text = "".join(str(shot.get(f) or "") for f in
                            ("narration_segment", "subtitle", "motion_desc",
                             "ff_desc", "lf_desc", "prompt"))
        _quotes = [str(ev.get("source_quote") or "").strip()
                   for ev in plan["events"] if isinstance(ev, dict)]
        if _quotes and all(q and q in _own_text for q in _quotes):
            stats["grounded"] += 1
    return stats


def _candidate_quality_summary(script: dict, novel_text: str = "") -> dict:
    """Candidate risk summary from the same structured issue collector."""
    if novel_text:
        _mrt._set_novel_text(novel_text)
    issues, checker_failed, rule_version = _mrt.collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_mrt._run_prompt_checklist,
    )
    blocking = [i for i in issues if _mrt.is_blocking(i)]
    integrity_codes = {
        "text_integrity", "eye_state_conflict",
        "action_time_out_of_range", "state_order_replay",
        "pose_conflict", "surface_conflict",
        "offscreen_character_action", "weapon_holder_missing",
        "duration_budget",
    }
    integrity_issues = [i for i in blocking if i.code in integrity_codes]
    # v6.84（二次审核发现 1）：**结构收敛类**单独成一档——它们比一般 blocking
    # 更硬：字段级修复轮修不了，必须重做 shot-plan。候选选择据此拒收。
    from step01_script_gen.quality_issues import STRUCTURAL_SHOT_CODES as _SSC
    structural_issues = [i for i in blocking if i.code in _SSC]
    return {
        "structural_ok": not checker_failed and not structural_issues,
        "structural_issues": [i.message for i in structural_issues],
        # v6.102：结构问题的 **code**（消息文本无法可靠判类型，候选采纳要按类型分流）
        "structural_codes": [i.code for i in structural_issues],
        # v6.87：**完整性异常计数**。布尔 `integrity_ok` 不能用来判「新稿是否劣化」——
        # 原稿本身就不干净时，任何新稿也必然 False，采纳判据会变成永远不通过
        # （2026-09-21 23:17 实测：结构轮 5 → 4 被拒，两个候选摘要都标「完整性异常」）。
        # 收口判据要的是「**不退化**」，所以必须能比较数量。checker 失败时给 None
        # ——「未知」不得被当成「干净」。
        "integrity_count": None if checker_failed else len(integrity_issues),
        # v6.98（审核 P1-2）：身份集合——数量只用于日志与排序，安全门看身份差。
        "integrity_ids": ([] if checker_failed
                          else sorted(_blocking_identity(i) for i in integrity_issues)),
        # v6.99（二次审核 P0-A）：**全部 blocking** 的身份（含重复次数）。
        # 统一验收消费的是这一份，不再是上面那个手工子集——名称与行为必须同义。
        "blocking_ids": ([] if checker_failed
                         else [_blocking_identity(i) for i in blocking]),
        "temporal_plan_stats": _temporal_plan_stats(script),
        "quality_checked": not checker_failed,
        "blocking_risk_count": None if checker_failed else len(blocking),
        "integrity_ok": not checker_failed and not integrity_issues,
        "integrity_issues": [i.message for i in integrity_issues],
        "rule_version": rule_version,
        "rule_source_fingerprint": _mrt.RULE_SOURCE_FINGERPRINT,
        "duration_band": _mrt.duration_band(),
        "checker_failed": checker_failed,
        "structured_issues": [i.to_dict() for i in issues if _mrt.is_blocking(i)],
    }


def _candidate_only_splittable(score: dict) -> bool:
    """候选的**唯一**结构问题是不是「内容超载」（= 有确定性修复路径）.

    v6.102（第十六次修复项 #2）：实测第十二～十五次每一轮都一样——同两条长台词
    （原文 50 / 59 字）让候选 1/2/3 全部结构不合法，每次白烧 3 个候选（各 ~26k token），
    而第三个候选手上已经是 100 分。`shot_content_over_cap` **有确定性修复路径**：
    后面的定向拆分（`_scoped_split_over_cap_shots`）会按时长上限把它切开、拆完照样
    过统一验收。既然一定能修掉，就没必要为了「结构干净」再抽两次卡。

    只放行这一类：`shot_duration_below_min`（过短）**没有**确定性路径
    （只能靠整脚本 regen 合并），所以仍然拒收重取。
    """
    codes = set(score.get("structural_codes") or [])
    return bool(codes) and codes <= {"shot_content_over_cap"}


def _candidate_sort_key(item: tuple[dict, dict]) -> tuple:
    """Sort clean candidates ahead of dirty high-score candidates."""
    score = item[0] if isinstance(item, tuple) and item else {}
    if score.get("quality_checked") is False:
        return (2, 10**6, -float(score.get("total", 0) or 0))
    integrity_bad = 0
    if score.get("quality_checked") and score.get("integrity_ok") is not True:
        integrity_bad = 1
    # v6.84（发现 1）：结构非法（超 cap / 低于下限）比内容分更优先——它决定
    # 这份稿子**能不能落地**，而不是好不好看。
    structural_bad = 0
    if score.get("quality_checked") and score.get("structural_ok") is not True:
        structural_bad = 1
    return (
        structural_bad,
        integrity_bad,
        int(score.get("blocking_risk_count", 0) or 0),
        -float(score.get("final_score", score.get("total", 0)) or 0),
    )


def _quality_rank(score: float, quality: dict | None) -> tuple:
    """Lower is better: integrity first, then blocking risks, then raw score."""
    quality = quality or {}
    integrity_ok = quality.get("integrity_ok")
    if integrity_ok is True:
        integrity_bad = 0
    elif integrity_ok is False:
        integrity_bad = 1
    else:
        integrity_bad = 2  # unknown must never outrank a verified clean version
    risks = quality.get("blocking_risk_count")
    risks = 10**6 if risks is None else int(risks or 0)
    structural_ok = quality.get("structural_ok")
    if structural_ok is True:
        structural_bad = 0
    elif structural_ok is False:
        structural_bad = 1
    else:
        structural_bad = 2
    if quality.get("quality_checked") is False:
        integrity_bad = max(integrity_bad, 2)
        structural_bad = max(structural_bad, 2)
        risks = max(risks, 10**6)
    return (structural_bad, integrity_bad, risks, -float(score or 0))


def _build_step01_quality_gate(script: dict, novel_text: str) -> dict:
    """构造确定性质量门报告（**不落盘**）——唯一提交点的 staged 产物之一."""
    if novel_text:
        _mrt._set_novel_text(novel_text)
    issues, checker_failed, rule_version = _mrt.collect_structured_issues(
        script, _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES,
        checklist_runner=_mrt._run_prompt_checklist,
    )
    hard = [i.to_dict() for i in issues if i.severity == "hard"]
    visual = [i.to_dict() for i in issues if i.severity == "visual_risk"]
    video_advisory = [i.to_dict() for i in issues if i.severity == "video_advisory"]
    advisory = [i.to_dict() for i in issues if i.severity in ("advisory", "unverified")]
    payload = {
        "hard_count": len(hard),
        "visual_risk_count": len(visual),
        "advisory_count": len(advisory),
        "video_advisory_count": len(video_advisory),
        "hard": hard,
        "visual_risk": visual,
        "advisory": advisory,
        "video_advisory": video_advisory,
        "blocking": bool(hard or visual or checker_failed),
        "blocking_categories": sorted({x["code"] for x in hard + visual}),
        "checker_failed": checker_failed,
        "rule_version": rule_version,
        "rule_source_fingerprint": _mrt.RULE_SOURCE_FINGERPRINT,
        "duration_band": _mrt.duration_band(),
        "status": "BLOCK" if (hard or visual or checker_failed) else "PASS",
    }
    _mrt.attach_gate_metadata(payload, script, stage="step01.quality_gate")
    return payload


def _write_step01_quality_gate(script: dict, novel_text: str) -> dict:
    """Write the final structured quality-gate report.

    ⚠️ v6.103 起**生产路径不再调用它**——正式产物由唯一提交点
    （`_commit_step01_outputs`）按 staged → 自验 → 原子替换的顺序写，且
    `script_result.json` 是**最后**一个被替换的文件。本函数保留给既有的
    单点测试与离线复算（它一次写 quality + script 两个文件）。
    """
    payload = _build_step01_quality_gate(script, novel_text)
    try:
        from shared.json_io import atomic_dump_json
        # attach_gate_metadata recomputes script_revision from final content.
        # Persist that revision back to script_result.json in the same step so
        # BEST-GUARD / downstream gates can never observe report != script.
        atomic_dump_json(_mrt.OUTPUT_FILE, _strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_quality.json", payload, ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[QUALITY-GATE] write failed: {exc}")
    return payload
# v6.137（2026-09-26 用户决策）：2 → 4。§1.1 定 2 轮时修复轮还不可靠（零改善/
# new_blocking 死路一轮即弃）；v6.137 之后每轮都有验收闸兜底且实测连续净改善
# （resolved=4/6、new=0），2 轮不够把 24 条 blocking 收敛到 0（实测 24→21→13）。
# 4 轮 = 修复调用 ≤4 次 + 每轮 1 次新审计（脚本已变不可复用），成本约 +5 分钟/次。
STEP01_AUDIT_REPAIR_MAX_ROUNDS = 4


def _strip_runtime_fields(obj):
    """递归剥掉 dict 下划线开头的 key（运行时数据，不入盘）.

    下划线开头键 = 「不入盘」信号（_run_metadata/_merge_candidate/_qc_last 等
    只留在内存）。本函数现为**单源实现**：step02a 的同名副本已随它唯一服务的
    那段「回写 script_result.json」一并移除（2026-10 复核 P0，已提交产物只读）。
    v6.63.1：step01 落盘时统一剥——此前只有 step02a 回写时才剥，
    _run_metadata 会泄进 script_result.json。
    """
    if isinstance(obj, dict):
        return {k: _strip_runtime_fields(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [_strip_runtime_fields(v) for v in obj]
    return obj

# ============================================================================
# GAP 重试回退保护（v6.47，08-27）——重试版质量退化时保留上一版
# ============================================================================

def _retry_raw_duration(script: dict) -> float:
    """重试版原始总时长（不含钩子）——parse_response 后、hard_fix 拉伸前的值.

    W6 会把不足 60s 的脚本事后拉伸，所以只看**原始** duration_sec 之和才能
    反映 LLM 是否真的响应了总时长规则（08-27 重试版原始 11s → 被拉到 68s 的
    事故就是只看拉伸后总值看不出来）。
    """
    return sum(float(sh.get("duration_sec") or sh.get("duration") or 0)
               for sh in script.get("shots", []) or [])


def _count_ungrounded_narrations(script: dict, novel_text: str) -> int:
    """统计台词未在原文找到的镜头数（原文锚定失败 = LLM 编造对白）."""
    if not novel_text:
        return 0
    cnt = 0
    for sh in script.get("shots", []) or []:
        narr = str(sh.get("narration_segment") or sh.get("narration") or "").strip()
        if not narr:
            continue
        s, e = _mrt._locate_narration_in_novel(narr, novel_text)
        if s < 0:
            cnt += 1
    return cnt


# v6.50（08-28）：文本截断检测——LLM 响应被截断后 json_repair 捞回的残句
# （实测「镜头推近至」「惊觉对」ff_desc 尾部悬空「庄仆」直接落盘）。
# 正常 prompt/ff/lf/motion 都应以终结标点收尾；台词 narration 允许逗号结尾
# （跨镜拆分对白），故只查这 4 个字段。
#
# v6.62.6（09-09）真实截断残句的判据：结尾停在**不能收句的虚词**上。
#
# 为什么换判据：旧实现用「不以终结标点收尾」当截断信号（原 _TERMINAL_TAIL，
# 本次已随判据一并删除），09-08 实测在真实数据上命中 40/44 = 91%（上一版
# 40/40 = 100%）——而命中的全是**完整**描述，只是 doubao 写描述性字段不习惯
# 加句号（「…孩童嬉玩可见」「…昏昏欲睡」「…转场时长0.5s」）。
# 于是 `new_trunc >= 2 and new_trunc > prev_trunc` 被行文风格的随机波动稳定触发，
# **把几乎每一次 GAP 重试都判成退化回退掉**，其中包括 v6.62.4 刚加的零高潮专项
# 重试（23:10 授予重试 → 23:13 以「48 > 39 个残句」回退 → 终稿仍零高潮）。
# 两个修复互相抵消，且旧文案从不打印真正命中的判据，所以一直没被看见。
#
# 选词原则：只收**几乎不作名词结尾**的虚词，并排掉本域高频名词/形容词。
# 古风权谋语料里以下字常作名词尾，**故意不收**：
#   地(土地/空地) 把(刀把) 被(棉被) 由(理由) 跟(脚跟) 朝(王朝) 沿(河沿)
#   将(大将) 向(方向) 和(柔和/温和) 得(取得/心得)
_DANGLING_TAIL = tuple("的与及或而且并从往于让给至对")
# _DANGLING_TAIL 里 于/对/至 三字仍有名词化/固定搭配风险（「甚至」「面对」是
# 完整句的合法收尾，不是残句），按二字组合排除。
# 不变式：**每一项都必须以 _DANGLING_TAIL 中的字结尾**，否则它是永不生效的死条目
# ——由 tests/step01_script_gen/test_main_regen_guard.py 断言守住。
_DANGLING_EXCEPT = (
    "面对", "一对", "相对", "绝对", "对于", "至于", "甚至", "以至",
    "由于", "在于", "属于", "等于", "终于", "过于",
)


def _count_mid_clause_tails(script: dict) -> int:
    """统计 4 文本字段以**悬挂虚词**收尾的数量（截断 salvage 的强信号）.

    判据见 `_DANGLING_TAIL`：09-08 实测把「缺句末标点」换成「悬挂虚词收尾」后，
    真实数据命中率 91% → 0%，而 docstring 里的真实残句样例（「镜头推近至」
    「惊觉对」「抬手指向」）仍全部命中。
    """
    cnt = 0
    for sh in script.get("shots", []) or []:
        for fld in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            v = str(sh.get(fld) or "").strip()
            if len(v) < 10:
                continue
            if v.endswith(_DANGLING_EXCEPT):
                continue
            if v.endswith(_DANGLING_TAIL):
                cnt += 1
    return cnt


def _climax_count(script: dict, novel_text: str) -> int:
    """该脚本被硬回退护栏判定为「高潮镜」的镜头数.

    回退护栏用更严格的语义字段判定：只看叙述/字幕/主体/动作，不把
    core_keywords 自身算进匹配池，也不让单字高潮词（杀/死/跑）虚增计数。
    """
    try:
        from step01_script_gen.climax_words import climax_words_for_gap
        words = [w for w in climax_words_for_gap(script) if len(w) >= 2]
        semantic_fields = (
            "narration_segment",
            "subtitle",
            "main_subject",
            "motion_desc",
        )
        count = 0
        for shot in script.get("shots", []):
            semantic_text = " ".join(
                str(shot.get(field) or "") for field in semantic_fields
            )
            if any(word in semantic_text for word in words):
                count += 1
        return count
    except Exception:
        return 0



def _score_script(script: dict, novel_text: str) -> dict:
    """通用脚本质量打分（v6.64，P4.5 多路径择优）。

    纯量化指标，零硬编码，换小说自动跟随。用于多候选择优：分数越高越好。

    返回 {
        "total": float,        # 总分 0-100
        "climax_count": int,   # 高潮镜数
        "climax_complete": bool,  # 高潮是否有结果
        "dialogue_ratio": float,  # 对白镜占比 0-1
        "ungrounded_count": int,  # 失锚台词数
        "duration_sec": float,    # 原始总时长
        "shot_count": int,        # 镜头数
        "detail": str,            # 人类可读摘要
    }
    """
    shots = script.get("shots", []) or []
    n = len(shots)
    score = 0.0
    detail_parts = []

    # 1) 高潮镜数（满分 25 分）：0=0, 1=10, 2=20, 3+=25
    climax_n = _climax_count(script, novel_text)
    if climax_n >= 3:
        score += 25
    elif climax_n == 2:
        score += 20
    elif climax_n == 1:
        score += 10
    detail_parts.append(f"高潮{climax_n}镜")

    # 2) 高潮完整性（满分 15 分）：有结果态 vs 只有起势
    try:
        from step01_script_gen.constraints.climax_completeness import (
            _get_climax_shots, _has_result,
        )
        cidx = _get_climax_shots(shots)
        climax_complete = bool(cidx) and any(
            _has_result(shots[i]) for i in cidx if i < n
        )
    except Exception:
        climax_complete = False
    if climax_complete:
        score += 15
    elif climax_n == 0:
        pass  # 零高潮，本条不加分也不减分（由高潮镜数项负责）
    else:
        score += 5  # 有高潮但不完整，给部分分
    detail_parts.append("高潮完整" if climax_complete else "高潮不完整")

    # 3) 对白密度（满分 20 分）：≥40%=20, ≥30%=15, ≥20%=10, ≥10%=5, 0=0
    from step01_script_gen.constraints.dialogue_density import _has_dialogue
    dlg_n = sum(1 for s in shots if _has_dialogue(s.get("narration_segment", "")))
    dlg_ratio = dlg_n / n if n else 0
    if dlg_ratio >= 0.40:
        score += 20
    elif dlg_ratio >= 0.30:
        score += 15
    elif dlg_ratio >= 0.20:
        score += 10
    elif dlg_ratio >= 0.10:
        score += 5
    detail_parts.append(f"对白{dlg_n}/{n}={int(dlg_ratio*100)}%")

    # 4) 台词锚定率（满分 20 分）：0 失锚=20, 1=15, 2=10, 3+=5
    ung = _count_ungrounded_narrations(script, novel_text) if novel_text else 0
    if ung == 0:
        score += 20
    elif ung == 1:
        score += 15
    elif ung == 2:
        score += 10
    else:
        score += 5
    detail_parts.append(f"失锚{ung}条")

    # 5) 时长合规（满分 10 分）：按 timecode 拆镜后的有效时长评分。
    # LLM 原始 duration_sec 常低估长台词；若按原值打分会把拆镜后超 90s
    # 的候选误判为满分（09-16：原始 17镜/75.5s → 拆后 21镜/97.6s）。
    dur = _retry_raw_duration(script)
    from step01_script_gen.timecode import project_post_split_metrics
    _projected = project_post_split_metrics(shots, fps=script.get("fps", 24))
    eff_dur = float(_projected.get("duration_sec", dur) or dur)
    eff_n = int(_projected.get("shot_count", n) or n)
    _split_n = int(_projected.get("split_count", 0) or 0)
    if _mrt.DURATION_MIN_SEC <= eff_dur <= _mrt.DURATION_MAX_SEC:
        score += 10
    elif _mrt.DURATION_MIN_SEC - 10 <= eff_dur < _mrt.DURATION_MIN_SEC or _mrt.DURATION_MAX_SEC < eff_dur <= _mrt.DURATION_MAX_SEC + 30:
        score += 7
    else:
        score += 3
    if abs(eff_dur - dur) >= 0.5:
        detail_parts.append(f"时长{dur:.0f}s→{eff_dur:.0f}s")
    else:
        detail_parts.append(f"时长{dur:.0f}s")

    # 6) 镜数合规（满分 10 分）：按拆镜后的有效镜数评分
    if 8 <= eff_n <= 20:
        score += 10
    elif 5 <= eff_n < 8 or eff_n > 20:
        score += 5
    if eff_n != n:
        detail_parts.append(f"镜数{n}→{eff_n}(拆{_split_n})")
    else:
        detail_parts.append(f"镜数{n}")

    # 7) 相邻镜重复视觉惩罚（2026-09-12 打分盲区复盘：镜12/12.5 文本逐字重复
    # 曾拿 95 分）。相邻镜动作文本 Jaccard ≥0.85 视为凑镜重复，每对扣 8 分。
    try:
        from shared.split_similarity import jaccard_bigram, shot_action_text
        _dup_pairs = sum(
            1 for _i in range(n - 1)
            if jaccard_bigram(shot_action_text(shots[_i]),
                              shot_action_text(shots[_i + 1])) >= 0.85
        )
        if _dup_pairs:
            score = max(0.0, score - 8.0 * _dup_pairs)
            detail_parts.append(f"重复镜-{8 * _dup_pairs}")
    except Exception:
        _dup_pairs = 0

    detail = ", ".join(detail_parts) + f" → {score:.0f}分"

    return {
        "total": round(score, 1),
        "climax_count": climax_n,
        "climax_complete": climax_complete,
        "dialogue_ratio": round(dlg_ratio, 3),
        "ungrounded_count": ung,
        "duration_sec": round(dur, 1),
        "shot_count": n,
        "projected_duration_sec": round(eff_dur, 1),
        "projected_shot_count": eff_n,
        "projected_split_count": _split_n,
        "detail": detail,
    }

def _retry_degrade_reason(prev: dict, new: dict, novel_text: str,
                          *, climax_rescue: bool = False) -> str | None:
    """返回重试版退化的**具体原因**，健康则返回 None（v6.62.6，09-09）.

    与 `_is_retry_degraded` 同判据，只是把命中的那一条讲清楚。拆出来的原因：
    09-08 实测日志打出「重试版质量退化（原始总时长 77s vs 上一版 66s，台词失锚
    0 条），回退保留上一版」——两个被打印的指标**都不是**触发项（77s 未塌缩、
    失锚 0 条），真正命中的是第 3 条「文本中途截断」，而它从不出现在文案里。
    结果是运维读日志会得出「护栏把更好的版本判退化了」的错误结论。

    Args:
        climax_rescue: True = 上一版**零高潮**而重试版补上了高潮镜（见调用点）。
            此时判据 2/3 降级为告警不再触发回退，判据 1/1.5（时长塌缩/高潮塌缩）仍硬拦截——
            理由是价值序：判据 2/3 拦的是**措辞瑕疵**（台词没逐字对上原文、
            句子收尾不干净），判据 1/1.5 拦的是**不可挽回的结构性损坏**
            （时长塌缩后 W6 拉伸把 1s 镜拉成 8s 慢动作；整章高潮没拍直接等于废片）。
    """
    # 1) 时长塌缩
    prev_dur = _retry_raw_duration(prev)
    new_dur = _retry_raw_duration(new)
    if prev_dur >= 40 and new_dur < 40:
        return (f"时长塌缩（原始总时长 {new_dur:.0f}s < 40s，上一版 {prev_dur:.0f}s）"
                f"——事后 W6 拉伸会把短镜拉成慢动作")
    # 1.6) 镜数/时长膨胀（v6.70.3，09-14）——塌缩的镜像判据。09-14 实测：100 分
    # 候选 17 镜 84s 被采纳后，GAP 重生版 21 镜 108s **静默替换**（三条旧判据只看
    # 「变差」方向：塌缩查 <40s、失锚查变多、截断查变多——膨胀一个都不命中），
    # 终稿 20 镜 123s。上一版健康（≥60s ≤90s 合法带内）而新版膨胀出带（>90s）或
    # 镜数多 3+ → 判退化回退。救援态不豁免（救援补高潮不靠膨胀镜头数达成）。
    _prev_n = len(prev.get("shots", []) or [])
    _new_n = len(new.get("shots", []) or [])
    if (prev_dur >= _mrt.DURATION_MIN_SEC and new_dur > _mrt.DURATION_MAX_SEC) or (_new_n - _prev_n >= 3):
        return (f"镜数/时长膨胀（新版 {_new_n} 镜 {new_dur:.0f}s，上一版 {_prev_n} 镜 "
                f"{prev_dur:.0f}s）——重生版把镜头预算炸出合法带（{_mrt.DURATION_MIN_SEC:g}-{_mrt.DURATION_MAX_SEC:g}s），"
                f"越修越多的镜头会让总时长超出本轮合法上限（{_mrt.DURATION_MAX_SEC:g}s）")
    # 1.5) 高潮塌缩（v6.62.8，09-09）——与「零高潮救援」对称的反向护栏。
    # 09-10 run3 实测：retry1 把高潮镜从 0 补到 1（救援采纳），retry2 又打回 0，
    # 而三条判据都不看高潮，于是 retry2 被静默接受，终稿高潮镜 0、台词镜 0。
    # 「零高潮」正是当初特批 bonus 重试要解决的那个缺陷——绝不能让后一次重试
    # 把它悄悄还原。**不受 climax_rescue 豁免**（救援态下 prev 恒为 0，本条
    # 天然不会命中，放在豁免之前只是为了「结构性缺陷永远优先」这个次序清晰）。
    if novel_text:
        prev_climax = _climax_count(prev, novel_text)
        if prev_climax > 0 and _climax_count(new, novel_text) == 0:
            return (f"高潮塌缩（上一版 {prev_climax} 个高潮镜 → 新版 0 个）"
                    f"——原文核心冲突整段又没拍，退回零高潮态")
    if climax_rescue:
        return None
    # 2) 台词失锚变多
    if novel_text:
        prev_ung = _count_ungrounded_narrations(prev, novel_text)
        new_ung = _count_ungrounded_narrations(new, novel_text)
        if new_ung > prev_ung and new_ung >= 2:
            return (f"台词失锚变多（新版 {new_ung} 条未在原文找到，"
                    f"上一版 {prev_ung} 条）——疑似原文幻觉")
    # 3) 文本中途截断变多
    new_trunc = _count_mid_clause_tails(new)
    prev_trunc = _count_mid_clause_tails(prev)
    if new_trunc >= 2 and new_trunc > prev_trunc:
        return (f"文本中途截断变多（新版 {new_trunc} 个字段以悬挂虚词收尾，"
                f"上一版 {prev_trunc} 个）——LLM 响应被截断后捞回了残句")
    return None


def _is_retry_degraded(prev: dict, new: dict, novel_text: str,
                       *, climax_rescue: bool = False) -> bool:
    """判定重试版是否比上一版退化（回退保护的四个强信号）.

    1) **时长塌缩**：上一版健康（≥40s）而新版原始总时长 < 40s——LLM 明显没
       响应总时长规则（08-27 重试版 11s 事故），事后拉伸会把 1s 镜头拉到 8s。
    1.5) **高潮塌缩**：上一版有高潮镜而新版归零——整章核心冲突整段没拍，
       属于不可挽回的结构性缺陷，必须硬拦截。
    1.6) **镜数/时长膨胀**（v6.70.3，09-14）：上一版在合法带内（≥60s）而新版
       膨胀出带（>90s）或镜数多 3+——「越修越多」与「越修越塌」同样是退化。
       09-14 实测：100 分 17 镜候选被 GAP 重生 21 镜版静默替换，终稿 123s。
    2) **台词失锚变多**：新版未在原文找到的台词数 > 上一版（原文幻觉，如
       「凶手是谁？如何击杀了公子」）。≥2 条才判退化，防短语匹配误报。
    3) **文本中途截断**（v6.50，08-28；判据 v6.62.6 重写）：新版 ≥2 个文本字段
       以**悬挂虚词**（`_DANGLING_TAIL`：的/与/至/对/于…）收尾且比上一版多——
       LLM 响应被截断、json_repair 捞回残句（实测「镜头推近至」「惊觉对」
       直接落盘）。**不再**用「缺句末标点」当信号，那条在真实数据上 91% 是误报。

    判据实现在 `_retry_degrade_reason`（单一事实源，附带命中原因文案）。
    `climax_rescue=True` 时只保留判据 1 和 1.5，语义见该函数 docstring。

    Returns:
        True = 重试版退化，应回退保留 prev。
    """
    return _retry_degrade_reason(
        prev, new, novel_text, climax_rescue=climax_rescue
    ) is not None
