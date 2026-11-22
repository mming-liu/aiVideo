# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.generation —— spy/hook 脚本生成、候选生成.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
import json

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _warn_silent_video_model() -> None:
    """VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前视频模型无声时告警.

    适配 2026-08 新接入模型：Seedance 1.0 系列（generate_audio=False）、
    MiniMax v1 Hailuo 均不生成语音；此时若仍按「对话交给视频模型」架构运行，
    成片会无对白/背景音。提示换有声模型或设 VIDEO_MODEL_VOICE=0 走 step03 TTS。
    """
    from shared.model_caps import active_video_supports_audio
    voice_enabled = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
    if voice_enabled and not active_video_supports_audio():
        _mrt._console(
            "[!] 当前视频模型不生成语音（VIDEO_MODEL_VOICE=1 默认把对话/背景音交给"
            "视频模型，但该模型无声）。\n"
            "    成片将无对白/背景音。建议：换有声模型（Seedance 2.0+/wan2.7-r2v），"
            "或设 VIDEO_MODEL_VOICE=0 走 step03 TTS。"
        )


# ============================================================================
# 1. 从小说提取指定章节
# ============================================================================

def read_chapter(path: str | None = None, chapter: int = 1) -> str:
    """提取小说指定章节（合并多个小节），返回完整内容."""
    full_text = _mrt.extract_section(_mrt.resolve_novel_file(path), chapter)
    _mrt._logger.info(f"[INFO] 选取第 {chapter} 章全文 {len(full_text)} 字符作为素材")
    return full_text


def _check_fps_consistency(script: dict) -> None:
    """C5 修复（08-11）：接线 validate_fps_consistency（此前定义后无调用点）.

    在 _compute_timecodes 之后调用——timeline 此时才存在。分镜时长 × fps 若
    不对齐整数帧，视频生成会多/少一帧导致卡顿。只 WARN 不阻断。
    """
    from shared.style_prefix import validate_fps_consistency
    try:
        validate_fps_consistency(script)
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:_check_fps_consistency", _degrade_exc)


# ============================================================================
# 2. 文本生成
# ============================================================================

def gen_spy_script(novel_text: str, bank=None, objects_bank=None,
                   outline: dict | None = None,
                   temperature: float = 0.2,
                   model: str | None = None,
                   timeout_sec: int | None = None) -> str:
    """调用 LLM 生成脚本，返回原始文本.

    Args:
        outline: 预提取的章节大纲（v6.43 08-25 改为 main() 入口并发预提取，
            避免本函数再独立调一次 LLM 浪费 ~6s）。
        timeout_sec: 请求级客户端超时（v6.70.3 分级——候选 300s，防 600s 慢请求
            拖死整轮；None=provider 默认）。
    """
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    # v6.23：大纲两阶段（ShortVideoDirector director-outline 吸收）——大纲作分镜
    # 锚点，保证冲突/高潮分布。
    # v6.132（用户 2026-09-25）：构造失败**不再降级为「无锚点继续生成」**——见下方 except。
    outline_anchor = ""
    prop_anchor_block = ""
    contract_block = ""
    if outline:
        # v6.131（2026-09-25 收口）：**可行性门不在本函数里**。本函数是"每个候选都会走"
        # 的路径，门放这里等于每个候选重算一遍，而且会被候选循环的 `except RuntimeError`
        # 当成生成失败重试。门已上移到候选扇出入口 `_generate_script_candidates()`，
        # 一轮只验算一次、在**完整分镜生成之前**（角色/场景物体/大纲三个提取模型此前
        # 已并发调用过，所以不是"任何模型调用之前"）。
        try:
            from step01_script_gen.outline import outline_to_anchor
            outline_anchor = outline_to_anchor(outline, novel_text)
            # v6.132（用户 2026-09-25）：**生成执行契约**（不压缩）——本集选用的全部台词 +
            # 必保留事实 + 事件顺序。200 字大纲摘要（doubao/agent_plan 剖面）只能装摘要，
            # 选材必须走这一块；四个完整稿生成入口共用。
            contract_block = _mrt.render_execution_contract(outline)
            # v6.128（用户 2026-09-25 决定）：道具候选短提示**默认不再注入**。
            # 两次诊断生成都没过验收（提前释放且布局不跟随 / 连释放都不写），提示词
            # 路线暂停；离线代码与取证保留，`STEP01_PROP_ANCHOR_BLOCK=1` 可复测。
            from step01_script_gen.prop_state_anchor import prop_anchor_block_for
            prop_anchor_block = prop_anchor_block_for(outline)
            if prop_anchor_block:
                _mrt._logger.info("[INFO] 道具状态候选锚点 %d 条已注入独立短提示"
                             % len((outline or {}).get("prop_state_candidates") or []))
            elif (outline or {}).get("prop_state_candidates"):
                _mrt._logger.info("[INFO] 道具候选短提示已关闭（默认；"
                             "STEP01_PROP_ANCHOR_BLOCK=1 可开启），本轮不注入")
            _mrt._logger.info("[INFO] 章节大纲已预提取（并发），注入分镜 prompt")
        except Exception as e:
            # v6.132（用户 2026-09-25 收口）：这里**不再吞异常**。门已经判过计划可行，
            # 走到本函数说明计划应当被完整传递；构造失败还继续生成，就会产出一份
            # **没有执行契约**的稿子（选材不受约束），而报告仍会说"已过计划门"。
            # 显式失败 —— 异常在 `create_service()` 之前抛出，**不会调用模型**。
            # 用 `PlanInfeasibleError`（非 RuntimeError）：候选循环的 `except RuntimeError`
            # 是"生成失败→重试"语义，而这是确定性的计划传递失败，重试没有意义。
            from step01_script_gen.plan_feasibility import PlanInfeasibleError
            raise PlanInfeasibleError(
                "计划锚点／执行契约构造失败 ⇒ 不生成（否则稿件不受选材约束）：%s" % e
            ) from e
    # v6.68: prompt/system 跟着实际模型走——deepseek-v4-flash → deepseek 剖面；
    # doubao → doubao 剖面。两者都保留完整 novel，由 PROMPT_MAX_CHARS 统一控上限。
    # 此前 agent_plan 下所有模型共用 doubao prompt，是候选结构性零高潮的根因。
    _main_builder, _system = _mrt.resolve_model_prompt_profile(model)
    prompt = _mrt.append_execution_contract(
        _main_builder(novel_text, bank, objects_bank, outline_anchor,
                      prop_anchor_block=prop_anchor_block),
        contract_block)
    _mrt._metrics_inc("calls.script_generation")
    service = _mrt.create_service()
    req = _mrt.TextGenerationRequest(
        system_prompt=_system,
        user_prompt=prompt,
        temperature=temperature,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
        json_schema=SCRIPT_SCHEMA,
        model=model,  # v6.66: 跨模型候选池——per-request model 覆盖（None=用 provider 默认）
        timeout_sec=timeout_sec,  # v6.70.3: 候选 300s 分级超时
    )
    _mrt._logger.info("[INFO] 正在调用 LLM 生成分镜脚本...")
    _mrt._console("  [生成] 正在调用 LLM 生成分镜脚本（18 镜，约需 3-6 分钟）...")
    result = service.generate(req)
    if not result.success:
        raise RuntimeError(f"LLM 生成失败: {result.error}")
    import re
    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw


# ============================================================================
# 3. 后处理：注入全局配置 + 合规过滤 + 敏感词校验（v6增强）
# ============================================================================

def _post_process(script: dict) -> dict:
    """增强：注入全局渲染配置 + 合规过滤 + 敏感词校验."""
    # 注入全局渲染参数（若 LLM 未输出则补上）
    script = _mrt.style_prefix.inject_global_config_into_script(script)

    # 自动合规过滤
    if _mrt.COMPLIANCE_AUTO_FIX:
        script = _mrt.style_prefix.sanitize_script_shots(script, verbose=True)
        script = _mrt.scene_compliance.auto_sanitize_shot_prompts(script, verbose=True)
        # v6.32（08-12）：台词（narration/subtitle）做「台词级」敏感词替换——只替换
        # 语义风险词表里的词（如「杀人」→「出人命」），不套用画面 prompt 的全表
        # （「死了→倒了」「杀了→击了」会把对话语义改碎，step01.log 镜头16/18 实测）。
        # 让「杀人」类风险词不再触发 semantic_risk 硬检查，同时保留对话原意。
        for _shot in script.get("shots", []):
            for _f in ("narration_segment", "subtitle"):
                if _shot.get(_f):
                    _shot[_f] = _mrt.content_safety.sanitize_narration(_shot[_f])

    # v6 新增：敏感词校验（强制级）
    validation_result = _mrt.style_prefix.validate_sensitive_words(script, verbose=True)
    if validation_result["has_issues"]:
        _mrt._logger.warn("[WARN] 检测到残留敏感词，再次进行合规过滤...")
        # 再次过滤确保干净
        script = _mrt.style_prefix.sanitize_script_shots(script, verbose=True)
        # 再次校验
        validation_result = _mrt.style_prefix.validate_sensitive_words(script, verbose=True)
        if validation_result["has_issues"]:
            _mrt._logger.error("[ERROR] 仍有残留敏感词，请检查脚本!")

    return script


# ============================================================================
# 4. 钩子分镜生成（v6新增）
# ============================================================================

def gen_hook_script(novel_text: str, bank=None, objects_bank=None,
                    forbidden_lines: list[str] | None = None,
                    dup_note: str | None = None,
                    model: str | None = None) -> dict | None:
    """调用 LLM 生成钩子分镜，返回 hook dict 或 None.

    Args:
        novel_text: 小说原文
        bank: CharacterBank 实例，用于注入角色锚点（prompt 中用角色名而非模糊称呼）
        objects_bank: SceneObjectsBank 实例，用于注入场景物体尺寸锚点（prompt 中写出锚定尺寸）
        forbidden_lines: v6.32（08-12）主分镜各镜头台词体列表（_speech_body），
            注入钩子 prompt 防「钩子台词与主分镜重复」倒叙穿帮。
        dup_note: v6.32（08-12）重生成时的去重反馈——上一版钩子台词与主分镜镜头
            重复，把具体重复台词 + 换台词/改纯画面开场的选择喂给 LLM，引导它换一句。
        model: v6.70.1 钩子后置校验轮——互补模型重生钩子（None=provider 默认，
            零行为变化）
    """
    if not _mrt.HOOK_ENABLED:
        return None

    prompt = _mrt.build_hook_prompt(novel_text, bank=bank, objects_bank=objects_bank,
                               forbidden_lines=forbidden_lines)
    if dup_note:
        prompt = prompt + f"\n\n# 钩子台词去重反馈（必须遵守）\n{dup_note}"
    _mrt._metrics_inc("calls.hook_generation")
    service = _mrt.create_service()
    from _text_gen.deepseek_schema import HOOK_SCHEMA
    req = _mrt.TextGenerationRequest(
        system_prompt="你是短视频脚本大师，擅长在视频开头制造悬念和吸引力。禁止血腥暴力细节。",
        user_prompt=prompt,
        temperature=0.2,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
        json_schema=HOOK_SCHEMA,
        model=model,  # v6.70.1 钩子后置校验轮：互补模型重生（None=provider 默认）
        timeout_sec=_mrt._repair_timeout_sec(),  # v6.70.3: hook 180s（09-13 实测 600s 超时已让重生白等）
    )
    _mrt._logger.info("[INFO] 正在调用 LLM 生成钩子分镜...")
    _mrt._console("  [生成] 正在生成钩子分镜（约 30 秒）...")
    result = service.generate(req)
    if not result.success:
        _mrt._logger.warn(f"[WARN] 钩子分镜生成失败: {result.error}，跳过钩子")
        return None

    import re
    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

    try:
        hook_data = _mrt.parse_hook_response(raw)
        hook = hook_data.get("hook")
        if hook:
            # 钩子也需合规过滤
            hook["prompt"] = _mrt.style_prefix.sanitize_prompt(hook.get("prompt", ""), verbose=False)
            # 钩子镜头也需要做敏感词校验
            for field in ("ff_desc", "lf_desc", "motion_desc"):
                if hook.get(field):
                    hook[field] = _mrt.style_prefix.sanitize_prompt(hook[field], verbose=False)
            # v6.32：钩子台词（narration/subtitle）做「台词级」敏感词替换——只替换
            # 语义风险词表里的词（如「杀人」→「出人命」），不套用画面 prompt 的全表
            # （「死了→倒了」会把对话改得语义破碎）。见 shared.content_safety.sanitize_narration。
            for field in ("narration_segment", "subtitle"):
                if hook.get(field):
                    hook[field] = _mrt.content_safety.sanitize_narration(hook[field])
            # W16/W17 兜底：标记 is_hook=True，让 step02a vimax_bridge 在生成层
            # 追加泳池/室内禁词（即使 W17 因 hook ff_desc 缺水面词未触发）。
            hook["is_hook"] = True
        return hook
    except Exception as e:
        _mrt._logger.warn(f"[WARN] 钩子分镜解析失败: {e}，跳过钩子")
        return None


class _CandidateDialogueBroken(Exception):
    """单候选台词完整性破损（内部控制流标记）：携带 broken 明细，不进候选池.

    不继承 RuntimeError——候选循环的 `except RuntimeError` 是"生成失败→重试"语义，
    台词破损是**内容结论**，必须与 LLM 调用失败分开计数、分开报告。
    """

    def __init__(self, broken):
        super().__init__("%d 句破损" % len(broken or []))
        self.broken = list(broken or [])


class _CandidateGateError(Exception):
    """台词完整性门**自身异常**（如净化器 `sanitize_narration` 抛错），不是稿件破损.

    v6.135 四审 P1：门内匹配不再吞净化异常退回原样（那会在"两侧原样恰好相同"时
    静默放行、fail-closed 分支收不到错误）——异常向外传播到这里转成独立控制流标记。
    fail-closed：异常候选判无效不进池、尝试名额照常消耗；计数进 attempt_stats 的
    `gate_errors` 桶，**不得混入 dialogue_broken**（把检查故障统计成稿件截断 =
    四审 P2 同型误归因）。
    """


class _CandidateContractBroken(Exception):
    """候选违反**事件履约契约**（v6.138）——节拍覆盖/声明顺序/引用越界.

    与台词破损同性质（内容结论、非检查故障）：fail-closed 不进池、尝试名额照常
    消耗；独立类型独立计数（attempt_stats.contract_broken），**不得混入
    dialogue_broken**——把「节拍无承载」统计成「台词截断」= 五审同型误归因。
    """

    def __init__(self, violations):
        super().__init__(violations)
        #: check_event_contract 的 violations（list[dict]：code/shot/detail）
        self.violations = violations


def _stop_if_outline_unavailable(res, novel_text: str, deadline_monotonic) -> None:
    """结构化大纲不可执行 ⇒ 写对应诊断并**停止**（工作包 §4 D / §7.6）.

    - **调用类失败**（`call_failed` / `illegal_response` / `outline_invalid`）：**它就是主
      终局**——即使手上还有一份基线计划（复核 2026-10-02 之三 P1：旧写法把这种情形交给
      生成前内容门，终局变成 `PlanContractInvalidError` / `plan_contract_invalid`，真实的
      调用失败只剩在修订历史里，归因反了）。基线的内容问题用 `baseline_problems` **单独
      记录**在同一份诊断里，不改终局。
    - **计划层结论候选**（`contract_unclosed` / `budget_infeasible` / `no_change` / `loop`）
      且有基线：交给**生成前统一入口**（同一份验收链、同一份诊断）——它必定抛出；截止已到
      时由那里新增的**时间门**负责，错误文本与"故事不可行"分开。
    - **没有基线**：写 `unevaluable` 的"判不了"诊断（`conclusion=<失败码>`），**不冒充**
      内容结论，也不把 None 继续传成"无计划"。
    - `res is None`（未启用大纲提取）⇒ 什么都不做：后续由生成前门按"计划缺失"fail-closed。
    """
    if not isinstance(res, dict) or res.get("executable"):
        return
    from step01_script_gen.plan_feasibility import (
        dump_plan_unavailable, check_plan_before_generation, PlanInfeasibleError,
        OUTLINE_CALL_FAILURE_CODES)
    # 复核 2026-10-04：失败投影一律走**有界且显式标注**的展示出口，
    # 不用裸 `[:N]`（切在句中断开语义边界，读起来像另一句话）。
    from step01_script_gen.plan_revision import rejection_display
    _f = dict(res.get("failure") or {})
    _code = str(_f.get("code") or "unknown")
    _detail = str(_f.get("detail") or "")
    _plan = res.get("plan")
    _mrt._logger.error("[PLAN] 大纲未产出可执行计划（%s）⇒ 停在计划层，不生成候选：%s"
                  % (_code, rejection_display(_detail, limit=200)))
    if _code in OUTLINE_CALL_FAILURE_CODES:
        # 基线的内容问题**由生产者给**（`acceptance.problems`——大纲侧已算过同一份验收链）：
        # 报告层只组合透传，不重算第二份。复核 2026-10-02 之四 D-1：旧写法在这里重算，
        # 重算自己抛异常时兜底项是**字符串**，接着被 `.get()` 调用 ⇒ `AttributeError`，
        # 诊断一次也没写成。现在归一集中在一处（`problem_records` / `record_text`），
        # 兜底项也保持**记录形状**。
        from step01_script_gen.plan_revision import problem_records, record_text
        _acc = res.get("acceptance")
        _base = problem_records(_acc.get("problems") if isinstance(_acc, dict) else None)
        if not _base and isinstance(_plan, dict) and _plan:
            try:
                from step01_script_gen.outline import plan_acceptance_problems
                _base = problem_records(plan_acceptance_problems(_plan, novel_text))
            except Exception as _e:                   # noqa: BLE001 —— 另列栏不许拖垮终局
                _base = [{"code": "baseline_problems_unavailable",
                          "detail": "基线内容问题无法列出：%s: %s" % (type(_e).__name__, _e)}]
        if _base:
            _rest = max(0, len(_base) - 3)
            _mrt._logger.error(
                "[PLAN] 基线计划仍有 %d 条内容问题（**另列**，不改本轮终局）：%s%s"
                % (len(_base), "；".join(record_text(x) for x in _base[:3]),
                   "（另有 %d 条未显示，完整清单见 step01_plan_infeasible.json）" % _rest
                   if _rest else ""))
        # 本轮**响应为什么被拒**的具体记录（复核 2026-10-02 之五）：诊断只有一句
        # "关键字段缺失/类型错误"时，人看不出违反哪条规则、也回灌不了下一轮。
        _fprobs = problem_records(_f.get("problems"))
        if _fprobs:
            _mrt._logger.error("[PLAN] 首版被拒的具体原因（%d 条）：%s"
                          % (len(_fprobs), "；".join(record_text(x) for x in _fprobs[:3])))
        # **逐轮**拒收记录（复核 2026-10-02 之五）：`failure.detail` 只留最后一条，
        # 三轮各自为什么被拒收是收敛分析的输入（"留档要宽"）。
        _rev = res.get("revision")
        _rej = problem_records(_rev.get("rejections") if isinstance(_rev, dict) else None)
        dump_plan_unavailable(_mrt.CHAPTER_DIR, code=_code, detail=_detail, plan=_plan,
                              baseline_problems=_base, failure_problems=_fprobs or None,
                              replan_rejections=_rej or None)
        raise PlanInfeasibleError(
            "大纲未产出可执行计划（%s）⇒ 停在计划层，不生成完整稿：%s"
            % (_code, rejection_display(_detail, limit=200)))
    if isinstance(_plan, dict) and _plan:
        check_plan_before_generation(_plan, chapter_dir=_mrt.CHAPTER_DIR, novel_text=novel_text,
                                     deadline_monotonic=deadline_monotonic)
        # 走到这里说明基线通过了生成前门——与 `executable=False` 自相矛盾（两处判据不一致）
        raise PlanInfeasibleError(
            "大纲判定为不可执行，但基线计划通过了生成前统一入口——两处判据不一致，"
            "按 fail-closed 停在计划层")
    dump_plan_unavailable(_mrt.CHAPTER_DIR, code=_code, detail=_detail)
    raise PlanInfeasibleError(
        "大纲未产出可执行计划（%s）⇒ 停在计划层，不生成完整稿：%s" % (_code, _detail[:200]))


def _generate_script_candidates(
    novel_text: str,
    character_bank=None,
    scene_objects_bank=None,
    outline: dict | None = None,
    deadline_monotonic=None,
) -> list:
    """多路径候选生成（v6.65 并行化，09-12）.

    生成 SCRIPT_CANDIDATE_TOPK 个独立候选，返回 [(score_dict, script)]（分数降序）。
    score_dict 额外带 "_model" 键（生成本候选的模型 ID，供 v6.67 接力修复选互补模型）。
    - TOPK=1：串行路径零变化（deepseek 默认）
    - TOPK>1：并行生成——doubao 单候选 2-3 分钟，串行 3 候选 8 分钟是流水线
      最大耗时项；AGENT_PLAN_TEXT_RPM=10 限速下 3 路并发合法。
      每轮并行补齐差额，失败/解析坏的候选进入下一轮重试（至 _max_attempts）。
    - 不同候选用不同温度增加多样性（第1个默认 0.2，后续 +0.15 递增）
    """
    # v6.131（2026-09-25 收口）：**生成前的计划门**——原文核验 + 预算核算，挂在候选扇出
    # **之前**，因此一轮只验算一次；不可行/不可评估/缺计划时**零次候选生成**，直接终止。
    # `PlanInfeasibleError` 不是 `RuntimeError`，下方候选循环的 `except RuntimeError`
    # （"生成失败→重试"语义）接不住它，不存在"每个候选重试一次"。
    from step01_script_gen.plan_feasibility import check_plan_before_generation
    from step01_script_gen.plan_feasibility import (DialogueGateError,
                                                    DialogueIncompleteError,
                                                    EventContractBrokenError,
                                                    check_event_contract,
                                                    check_script_dialogue_integrity,
                                                    bind_plan_identity,
                                                    dump_dialogue_incomplete,
                                                    plan_dialogue_lines)
    # 工作包 §4 D：生成前门接**同一个**绝对截止时刻（默认 None 保持旧行为）——
    # 截止已到时它先写 `deadline_exhausted` 诊断再停，不把"时间用尽"写成"故事不可行"。
    check_plan_before_generation(outline, chapter_dir=_mrt.CHAPTER_DIR, novel_text=novel_text,
                                 deadline_monotonic=deadline_monotonic)

    from shared.config import SCRIPT_CANDIDATE_TOPK, SCRIPT_CANDIDATE_MODELS
    import threading as _threading
    _candidates: list = []  # [(score_dict, script)]
    _last_err: Exception | None = None
    # v6.135：台词完整性硬门的破损明细（每元素 = 一个破损候选的 broken 列表）
    _dialogue_broken: list = []
    _dialogue_gate_errs: list = []
    # v6.138：事件履约契约的违规明细（每元素 = 一个违规候选的 violations 列表）
    _contract_broken: list = []
    _dlg_total = len(plan_dialogue_lines(outline))
    _max_attempts = _mrt.SCRIPT_PARSE_RETRIES * max(1, SCRIPT_CANDIDATE_TOPK)
    _attempt = 0
    _task_idx = 0  # 候选序号（温度梯度 0.2 + 0.15*idx）
    _model_list = list(SCRIPT_CANDIDATE_MODELS or [])
    _model_cooldowns: set[str] = set()
    _model_lock = _threading.Lock()

    def _pick_model(_idx: int) -> str | None:
        """候选模型轮转；超时模型本 run 冷却，优先改用其他模型."""
        if not _model_list:
            return None
        with _model_lock:
            _cooled = set(_model_cooldowns)
        _available = [m for m in _model_list if m not in _cooled]
        _pool = _available or _model_list
        return _pool[_idx % len(_pool)]

    def _cool_model_after_timeout(_err: RuntimeError) -> None:
        """把超时模型加入本 run 冷却，避免同模型继续高成本重试."""
        if not getattr(_err, "candidate_timed_out", False):
            return
        _model = getattr(_err, "candidate_model", None)
        if not _model:
            return
        with _model_lock:
            if _model in _model_cooldowns:
                return
            _model_cooldowns.add(_model)
        _mrt._logger.warn(
            f"[CANDIDATE] 模型 {_model} 候选超时，本 run 冷却，"
            f"后续候选改用其他模型"
        )

    def _gen_one_candidate(_idx: int):
        """生成单个候选：调 LLM → 解析 → 打分（线程内完成，返回 (score, script)）."""
        _temp = 0.2 + 0.15 * _idx
        # v6.66: 跨模型候选池——候选按序号轮转分配模型；空列表=全用默认模型
        _model = _pick_model(_idx)
        try:
            raw = gen_spy_script(
                novel_text, character_bank, scene_objects_bank,
                outline=outline,
                temperature=_temp,
                model=_model,
                timeout_sec=_mrt._candidate_gen_timeout_sec(),  # v6.70.3: 300s——慢候选由早停+超时双通道兜住
            )
        except RuntimeError as _e:
            _e.candidate_model = _model
            _e.candidate_timed_out = (
                "超时" in str(_e) or "timeout" in str(_e).lower()
            )
            raise
        _cand = _mrt.parse_response(raw, novel_text=novel_text, plan=outline)

        # v6.135（工作包 sunny-kindling-wilkinson；预算语义 2026-09-26 二审 C1 定稿）：
        # 候选层**台词完整性硬门**——破损候选直接判无效、不占合格名额；**差额在既有
        # `_max_attempts` 预算内继续补取**（与解析失败共用同一预算池，不扩预算）；
        # 预算用尽仍不足 → 按实际合格数收池，**全部候选破损才显式失败**；早停只由
        # 合格候选触发。
        # ⚠️ 职责边界（二审 H3）：本门在 parse（含 post_fix/hard_fix）之后、但在
        # `finalize_step01_script` 与 `_scoped_split_over_cap_shots` **之前**——
        # 拆分层产生的句中碎片**不由本门负责**，由主流程 finalize 后、BEST-GUARD 前
        # 的终稿门负责（那里才盖到送选稿/提交门的最终形态）。
        # 在评分之前跑（确定性、零模型调用），省掉破损稿的评分/质检开销。
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(_cand, outline)
        except Exception as _e:
            # v6.135 四审 P1：门自身异常（净化器故障等）不得退回原样匹配放行，
            # 也不得计入"台词破损"——独立类型，循环层 fail-closed 分账。
            raise _CandidateGateError(str(_e)) from _e
        if not _dlg_ok:
            raise _CandidateDialogueBroken(_dlg_broken)
        # v6.138（九审根因 1）：**事件履约契约**——plan_refs 声明的节拍覆盖/顺序/
        # 越界（确定性、零模型调用）。台词逐字已由上一道门收口；这里核「声明与
        # 计划一致」，缺载节拍（如「恐吓隐瞒」无承载）在入池前就判无效，不再等
        # 提交点语义审计撞上修复轮不可修复死锁。
        # 2026-09-27（复核 P0-1）：**先绑定身份、再核验**。绑定是「替新稿声明来源」，
        # 不是「免检」——已有身份**不覆盖**（覆盖会把「对另一版计划作的声明」洗成对
        # 当前计划的），随后 `require_declaration=True` 要求它真的声明过并逐项核验。
        # 旧顺序（先核验、通过后才落戳）有洞：新稿没有 `plan_id` ⇒ 校验函数直接按
        # 「未声明」返回 ⇒ **未经覆盖/顺序核验**就被盖上「已核验」的身份。
        bind_plan_identity(_cand, outline)
        # 2026-11-07（P1-5，候选解析入口）：按**实际旁白落点**校正 plan_refs——只改
        # 声明、不改正文。必须在绑定身份之后（复核要机器读得懂 plan_id），并且
        # 只有「复核违例数不上升」时才保留（见 normalize_plan_refs 内的回滚闸门）。
        from step01_script_gen.plan_feasibility import normalize_plan_refs as _norm_refs
        try:
            _norm_refs(_cand, outline)
        except Exception as _degrade_exc:
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step01_script_gen/main.py:_gen_one_candidate", _degrade_exc)
        try:
            _ct_ok, _ct_viol, _ct_declared = check_event_contract(
                _cand, outline, require_declaration=True)
        except Exception as _e:
            raise _CandidateGateError(str(_e)) from _e
        if not _ct_ok:
            raise _CandidateContractBroken(_ct_viol)
        _scr = _msc._score_script(_cand, novel_text)
        # v6.73: attach the same blocking-risk/integrity view used by the final
        # gate before candidate ranking and early-stop decide anything.
        _scr.update(_msc._candidate_quality_summary(_cand, novel_text))
        content_score = float(_scr.get("total", 0) or 0)
        q = _scr
        gate_pass = (
            q.get("quality_checked") is True
            and int(q.get("blocking_risk_count", 0) or 0) == 0
            and q.get("integrity_ok") is True
        )
        _scr["content_score"] = round(content_score, 1)
        _scr["final_score"] = round(content_score if gate_pass else min(content_score, 95.0), 1)
        _scr["gate_status"] = "PASS" if gate_pass else "BLOCK"
        _tps = _scr.get("temporal_plan_stats") or {}
        _scr["detail"] = (
            f"{_scr.get('detail', '')}，内容{_scr['content_score']:.0f}"
            f"，最终{_scr['final_score']:.0f}，{_scr['gate_status']}"
            f"，风险{_scr.get('blocking_risk_count')}"
            f"{'' if _scr.get('integrity_ok') else '，完整性异常'}"
            f"，plan {_tps.get('present', 0)}/{_tps.get('shots', 0)}"
            f"（valid {_tps.get('valid', 0)}, grounded {_tps.get('grounded', 0)}）"
        )
        _mrt._metrics_update("candidate.temporal_plan", _tps)
        # v6.67 接力修复：把生成本候选的模型挂在分数上，main() 据此选互补修复模型
        _scr["_model"] = _model
        return _scr, _cand

    if SCRIPT_CANDIDATE_TOPK <= 1:
        # TOPK=1：原串行路径零变化（deepseek 默认，含重试 sleep 语义）
        while _attempt < _mrt.SCRIPT_PARSE_RETRIES:
            _attempt += 1
            try:
                _scr, _cand = _gen_one_candidate(_task_idx)
                _candidates.append((_scr, _cand))
                _mrt._logger.info(f"[CANDIDATE] 候选 1/1: {_scr.get('detail', '')}")
                _mrt._console(f"  [候选] 1/1: {_scr.get('detail', '')}")
                # v6.84（二次审核发现 1）：**结构不合法的候选要拒收**，不是「先收下
                # 再在末尾抱怨」。候选在结构上不合法（内容需求超 cap / 时长低于
                # 下限）时，只要还有生成预算就继续取下一个候选；预算用尽则明确
                # 结束为 BLOCK——由质量门判定，不假装收敛成功。
                # v6.102（第十六次修复项 #2）：**只有「超载」一类结构问题时提前采纳**。
                #
                # 实测（第十二～十五次每轮都一样）：同两条长台词（原文 50 / 59 字）
                # 让候选 1、2、3 全部结构不合法，每次白烧 3 个候选（各 ~26k token），
                # 而第三个候选手上已经是 100 分。而 `shot_content_over_cap` **有确定性
                # 修复路径**——后面的定向拆分（`_scoped_split_over_cap_shots`）会把它
                # 按时长上限切开，拆完照样过验收。既然问题一定能被修掉，就没必要为了
                # 「结构干净」再抽两次卡。
                #
                # 只放行这一类：`shot_duration_below_min`（过短）**没有**确定性路径
                # （要靠整脚本 regen 合并），所以仍然拒收重取。
                if _msc._candidate_only_splittable(_scr):
                    _mrt._logger.info(
                        "[CANDIDATE] 候选只有「内容超载」（可被定向拆分确定性修掉）→ "
                        f"提前采纳，不再耗费候选：{(_scr.get('structural_issues') or [])[:2]}"
                    )
                    _mrt._console("  [候选] 仅超载（定向拆分可修），提前采纳")
                    break
                if _scr.get("structural_ok") is not True and _attempt < _mrt.SCRIPT_PARSE_RETRIES:
                    _mrt._logger.warn(
                        "[CANDIDATE] 候选结构不合法（内容超 cap / 低于下限），"
                        f"拒收并重取（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）："
                        f"{(_scr.get('structural_issues') or [])[:2]}"
                    )
                    _mrt._console(
                        f"  [候选] 结构不合法（超载/过短），重新生成"
                        f"（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）..."
                    )
                    _task_idx += 1
                    continue
                if _scr.get("structural_ok") is not True:
                    _mrt._logger.warn(
                        "[CANDIDATE] 生成预算用尽，结构仍不合法——明确结束为 BLOCK："
                        f"{(_scr.get('structural_issues') or [])[:3]}"
                    )
                    _mrt._console("  [候选] 预算用尽，结构仍不合法（结果将判 BLOCK）")
                break
            except RuntimeError as e:
                _last_err = e
                _mrt._logger.warn(f"[WARN] LLM 生成失败（尝试 {_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）: {e}")
                _mrt._console(f"  [重试] LLM 生成失败，重新调用（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）...")
                import time as _t
                _t.sleep(_mrt.SCRIPT_PARSE_RETRY_SLEEP_SEC)
            except _CandidateDialogueBroken as _e:
                # v6.135：台词破损 = 内容结论，与"生成失败"分开计数分开报告；
                # 尝试名额照常消耗（预算语义见 _gen_one_candidate 注释）
                _dialogue_broken.append(_e.broken)
                _mrt._logger.warn(
                    "[CANDIDATE] dialogue integrity failed（台词完整性硬门，判无效不进池，"
                    f"{len(_e.broken)}/{_dlg_total} 句破损）："
                    + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                                for b in _e.broken[:3]))
                _mrt._console(f"  [候选] 台词破损（{len(_e.broken)}/{_dlg_total} 句），判无效"
                         f"（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）...")
            except _CandidateGateError as _e:
                # v6.135 四审 P1：门自身异常 fail-closed——判无效不进池、名额消耗，
                # 与"台词破损"分账（attempt_stats.gate_errors），不裸崩不放行
                _dialogue_gate_errs.append(str(_e))
                _mrt._logger.error(
                    "[CANDIDATE] dialogue gate error（台词门自身异常，fail-closed 判无效"
                    f"不进池，{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）：{_e}")
                _mrt._console(f"  [候选] 台词门异常（fail-closed，"
                         f"{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）...")
            except _CandidateContractBroken as _e:
                # v6.138：事件契约破损 = 内容结论，独立分账（attempt_stats.contract_broken）
                _contract_broken.append(_e.violations)
                _mrt._logger.warn(
                    "[CANDIDATE] event contract failed（事件履约契约，判无效不进池，"
                    f"{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）："
                    + "；".join("%s@%s:%s" % (v.get("code"), v.get("shot"),
                                             str(v.get("detail"))[:60])
                                for v in _e.violations[:3]))
                _mrt._console(f"  [候选] 事件契约破损（{len(_e.violations)} 条），判无效"
                         f"（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）...")
            except (ValueError, json.JSONDecodeError) as e:
                _last_err = e
                _mrt._logger.warn(f"[WARN] 分镜 JSON 解析失败（尝试 {_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）: {e}")
                _mrt._console(f"  [重试] JSON 解析失败，重新调用 LLM（{_attempt}/{_mrt.SCRIPT_PARSE_RETRIES}）...")
    else:
        import concurrent.futures as _fut
        _early_stop_hit = False
        while len(_candidates) < SCRIPT_CANDIDATE_TOPK and _attempt < _max_attempts:
            _need = SCRIPT_CANDIDATE_TOPK - len(_candidates)
            _batch = min(_need, _max_attempts - _attempt)
            if _batch <= 0:
                break
            _ex = _fut.ThreadPoolExecutor(max_workers=_batch)
            _futs = {
                _ex.submit(_gen_one_candidate, _task_idx + _j): _j
                for _j in range(_batch)
            }
            _task_idx += _batch
            _batch_done = 0
            try:
                for _f in _fut.as_completed(_futs):
                    _attempt += 1
                    _batch_done += 1
                    try:
                        _scr, _cand = _f.result()
                        _candidates.append((_scr, _cand))
                        _mrt._logger.info(
                            f"[CANDIDATE] 候选 {len(_candidates)}/{SCRIPT_CANDIDATE_TOPK}: "
                            f"{_scr['detail']}"
                        )
                        # v6.68：高分早停。批量提交仍保留（不额外发请求），当前批剩余
                        # 未完成候选不再阻塞等待；provider 请求无法可靠取消，已发出的
                        # 调用会自然结束。
                        if _mrt._candidate_is_early_stop(_scr, _cand):
                            _remaining = len(_futs) - _batch_done
                            _mrt._logger.info(
                                f"[CANDIDATE] 早停：候选分 {_scr['total']} ≥ "
                                f"CANDIDATE_EARLY_STOP_SCORE={_mrt._early_stop_score()}，"
                                f"放弃等待 {_remaining} 个慢候选"
                            )
                            _early_stop_hit = True
                            break
                    except _CandidateDialogueBroken as _e:
                        # v6.135：同串行路径——破损候选不进池，尝试名额照常消耗
                        _dialogue_broken.append(_e.broken)
                        _mrt._logger.warn(
                            "[CANDIDATE] dialogue integrity failed（台词完整性硬门，"
                            f"判无效不进池，{len(_e.broken)}/{_dlg_total} 句破损）："
                            + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                                        for b in _e.broken[:3]))
                    except _CandidateGateError as _e:
                        # v6.135 四审 P1：同串行路径——门自身异常 fail-closed 分账
                        _dialogue_gate_errs.append(str(_e))
                        _mrt._logger.error(
                            "[CANDIDATE] dialogue gate error（台词门自身异常，"
                            f"fail-closed 判无效不进池，{_attempt}/{_max_attempts}）：{_e}")
                        _mrt._console(f"  [候选] 台词门异常（fail-closed，"
                                 f"{_attempt}/{_max_attempts}）...")
                    except _CandidateContractBroken as _e:
                        # v6.138：同串行路径——事件契约破损独立分账
                        _contract_broken.append(_e.violations)
                        _mrt._logger.warn(
                            "[CANDIDATE] event contract failed（事件履约契约，判无效不进池，"
                            f"{_attempt}/{_max_attempts}）："
                            + "；".join("%s@%s:%s" % (v.get("code"), v.get("shot"),
                                                     str(v.get("detail"))[:60])
                                        for v in _e.violations[:3]))
                        _mrt._console(f"  [候选] 事件契约破损（{len(_e.violations)} 条），判无效"
                                 f"（{_attempt}/{_max_attempts}）...")
                    except RuntimeError as e:
                        _cool_model_after_timeout(e)
                        _last_err = e
                        _mrt._logger.warn(f"[WARN] LLM 生成失败（尝试 {_attempt}/{_max_attempts}）: {e}")
                        _mrt._console(f"  [重试] LLM 生成失败（{_attempt}/{_max_attempts}）...")
                    except (ValueError, json.JSONDecodeError) as e:
                        _last_err = e
                        _mrt._logger.warn(f"[WARN] 分镜 JSON 解析失败（尝试 {_attempt}/{_max_attempts}）: {e}")
                        _mrt._console(f"  [重试] JSON 解析失败（{_attempt}/{_max_attempts}）...")
            finally:
                # v6.70.3（09-14）：早停必须真正不等慢线程——with 块退出会
                # shutdown(wait=True)，09-14 实测 break 后仍被 600s 慢候选拖住
                # （100 分候选 64s 到手，总耗时 17m38s）。wait=False 让
                # _generate_script_candidates 立即返回；孤儿线程随进程退出，
                # 不影响主流程（写盘都在主线程）。
                _ex.shutdown(wait=not _early_stop_hit)
            if _early_stop_hit:
                break

    if not _candidates:
        # v6.135 四审 P1：门自身异常与"台词破损"都无合格候选 ⇒ 显式失败，
        # 但三者在计数与文案里严格分账（检查故障 ≠ 稿件截断 ≠ 生成失败）。
        # v6.135 五审：顶层结论随实际失败类型区分——纯门故障抛 `DialogueGateError`
        # + 诊断 `conclusion=dialogue_gate_error`（稿件完整性未被判定）；
        # 含真实破损（含混合失败）抛 `DialogueIncompleteError` + `dialogue_incomplete`，
        # 各类计数留在 attempt_stats。
        if _dialogue_broken or _dialogue_gate_errs or _contract_broken:
            # v6.135（决策点 1，用户采纳审核建议）：存在台词破损且无合格候选 → **显式失败**，
            # 不回退"最少破损"候选、不静默落回历史快照（破损稿进提交门 = 死锁）。
            # v6.135 三审 P2-2：尝试构成**分开计数**——破损与生成/解析失败不得混写成
            # "全部破损"（混合失败时那句结论会掩盖另一类故障）。
            # v6.138：事件契约破损同样显式失败，第四个独立分账桶（contract_broken）。
            _n_broken = len(_dialogue_broken)
            _n_gate = len(_dialogue_gate_errs)
            _n_contract = len(_contract_broken)
            _n_other = max(0, _attempt - _n_broken - _n_gate - _n_contract)
            _stats = {"total": _attempt, "dialogue_broken": _n_broken,
                      "gate_errors": _n_gate, "contract_broken": _n_contract,
                      "other_failed": _n_other}
            _mix = ("%d 次台词破损、%d 次台词门异常、%d 次事件契约破损、%d 次生成/解析失败"
                    % (_n_broken, _n_gate, _n_contract, _n_other))
            if _dialogue_broken:
                _dump_batches = _dialogue_broken
                _first = _dialogue_broken[0][0] if _dialogue_broken[0] else {}
                _exc_cls = DialogueIncompleteError
                _head = "候选层台词完整性硬门"
                _tail = "（破损稿在提交门不可修复，见 09-26 实跑 locked_changed 死锁）"
            elif _contract_broken:
                _dump_batches = [[{"line": "",
                                   "reason": "event_contract: %s"
                                             % _contract_broken[0][0].get("code")}]]
                _first = {"line": "事件履约契约违规（%s…）"
                                  % str(_contract_broken[0][0].get("detail"))[:40],
                          "reason": "event_contract: %s"
                                    % _contract_broken[0][0].get("code")}
                _exc_cls = EventContractBrokenError
                _head = "候选层事件履约契约（节拍承载/声明顺序）"
                _tail = "（无承载节拍的稿子在提交门不可修复，见 09-26 实跑 MISSING_PLOT 死锁）"
            else:
                _first = {"line": "",
                          "reason": "gate_error: %s" % _dialogue_gate_errs[0]}
                _dump_batches = [[_first]]
                _exc_cls = DialogueGateError
                _head = "候选层台词完整性门自身异常"
                _tail = "（fail-closed 不进入选稿/提交）"
            dump_dialogue_incomplete(_mrt.CHAPTER_DIR, _dump_batches,
                                     total_lines=_dlg_total, attempt_stats=_stats,
                                     plan=outline,
                                     conclusion=("event_contract_broken"
                                                 if _exc_cls is EventContractBrokenError
                                                 else None))
            raise _exc_cls(
                "%s：%d 次尝试中 %s"
                "（计划 %d 句；示例：%s…［%s］）⇒ 无合格候选，不进入选稿/提交%s"
                % (_head, _attempt, _mix, _dlg_total,
                   str(_first.get("line") or "")[:40], _first.get("reason"), _tail),
                broken_batches=_dump_batches)
        raise RuntimeError(f"分镜 JSON 连续 {_max_attempts} 次解析失败: {_last_err}")
    _candidates.sort(key=_msc._candidate_sort_key)
    return _candidates
