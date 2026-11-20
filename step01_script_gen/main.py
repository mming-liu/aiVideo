"""step01_script_gen - 分镜脚本生成入口（v6 增强版）

功能：
  - 从小说提取章节内容
  - 调用 LLM 生成分镜脚本
  - 生成钩子分镜（v6新增）用于吸引观众
  - 验证音画同步 + 合规校验 + 敏感词校验（v6增强）
  - 注入全局渲染参数（style_prefix.inject_global_config_into_script）
  - 计算时间轴（含运镜量化参数）
  - 角色提取与持久化

输出：
  - script_result.json: 分镜脚本（位于 OUTPUT_BASE/book_N/chapter_N/）
"""
# <split:generated>
from __future__ import annotations
import sys
import os
import copy
from pathlib import Path
import json
# </split:generated>

# 确保从项目根目录解析导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 运行范围（--chapter / --book / --book-name）必须在下面第一个 config/env 读取之前生效：
# shared.config 的 NOVEL_CHAPTER 与本章的 CHAPTER_DIR/OUTPUT_FILE/_logger 都是 import 时求值，
# 在 main() 里再设 env 已经晚了（会静默用错章）。守卫见 shared/run_scope.bootstrap 文档。
if __name__ == "__main__":
    from shared.run_scope import bootstrap as _bootstrap_scope
    _bootstrap_scope("step01 分镜脚本生成", presentation_mode=True)

# <split:generated>
# 编排实现已按职责拆到 step01_script_gen/orchestrator/；本文件保留入口 main()。
# 门面：对本模块的读写（含 mock.patch / monkeypatch）转发到实现所在模块。
from step01_script_gen.orchestrator import _facade
from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import scoring as _msc
from step01_script_gen.orchestrator import regen as _mrg
from step01_script_gen.orchestrator import repair_apply as _mra
from step01_script_gen.orchestrator import final_audit as _mfa
from step01_script_gen.orchestrator import repair_accept as _mrc
from step01_script_gen.orchestrator import finalize as _mfz
from step01_script_gen.orchestrator import repair_targets as _mtg
from step01_script_gen.orchestrator import repair_round as _mrr
from step01_script_gen.orchestrator import persist as _mps
_facade.install(sys.modules[__name__], [_mrt, _msc, _mrg, _mra, _mfa, _mrc, _mfz, _mtg, _mrr, _mps])
# </split:generated>



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


def _write_blocked_manifest(revision: str, *, run_id: str, detail: str,
                            blocking: list, audit_status: str = "") -> None:
    """BLOCK 时只写**带 run id 的诊断 manifest**，不写三份产物.

    写 BLOCK manifest（而不是留着上一次 COMMITTED manifest）是刻意的：留着旧
    manifest 会让下游以为「上一版仍然有效」，于是带着本轮未解决的 blocking 继续
    生图。`verify_step01_commit_manifest` 见 `status != COMMITTED` 直接拒绝。
    """
    from shared.quality_gate import (build_step01_commit_manifest,
                                     write_step01_commit_manifest)
    try:
        manifest = build_step01_commit_manifest(
            run_id=run_id, created_at=_mrt._step01_now(), revision=revision,
            rule_source_fingerprint=_mrt.RULE_SOURCE_FINGERPRINT,
            rule_version=_mrt.RULE_VERSION, audit_scope=_mrt.STEP01_AUDIT_SCOPE,
            prompt_source_fingerprint=_mrt.PROMPT_SOURCE_FINGERPRINT,
            chapter_dir=_mrt.CHAPTER_DIR, status="BLOCK",
            blocking=blocking, detail=detail, audit_status=audit_status)
        write_step01_commit_manifest(_mrt.CHAPTER_DIR, manifest)
    except Exception as exc:
        _mrt._logger.warn(f"[COMMIT] BLOCK manifest 写入失败（不影响结论）: {exc}")


def _create_adoption_receipt(revision: str, run_id: str) -> str:
    """首次采纳：按现有 schema 生成凭据，**先验后写**，通过才落正式文件.

    缺失的身份信息显式写 null/说明，不编造；核验失败直接返回 create_failed，
    **不产生任何文件写入**——绝不留一份自称 adopted_and_verified 却没核验过的凭据。

    为什么不是"先写 .tmp 再 verify、失败删掉"（2026-11-14 改）：失败路径上的那次
    删除会把真正的失败原因顶掉。实测形态：`verify` 报 chapter_dir_mismatch → 走
    unlink → 在带删除审计的环境里进程被审计中断，调用方看到的是退出码 1，而
    "凭据为什么建不出来"这句话根本没进日志。核验不需要文件在盘上（verify 只核
    env/路径/摘要/清单），所以结构校验与身份核验都可以在内存里做完。
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path
    from tools import production_context as _pc

    ch = _Path(_mrt.CHAPTER_DIR)
    formal = {}
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        f = ch / name
        if not f.exists():
            return "create_failed:missing:%s" % name
        formal[name] = _hashlib.sha256(f.read_bytes()).hexdigest()
    from shared.config import duration_band
    _band = duration_band()
    receipt = {
        "schema": _pc.RECEIPT_SCHEMA,
        "status": "adopted_and_verified",
        "formal_chapter": str(ch),
        "output_base_override": str(_Path(os.getenv("OUTPUT_BASE") or ch.parent.parent.parent)),
        "script_revision": revision,
        "source_run_id": run_id,
        "adopted_at": _mrt._step01_now(),
        "scope": {"novel": os.getenv("NOVEL_NAME", ""),
                  "book_index": int(os.getenv("BOOK_INDEX", "1") or 1),
                  "book_name": os.getenv("BOOK_NAME", ""),
                  "chapter": int(os.getenv("NOVEL_CHAPTER", "1") or 1)},
        "formal_sha256": formal,
        "effective_config": {"DURATION_MIN_SEC": _band["min"],
                             "DURATION_MAX_SEC": _band["max"],
                             "DURATION_TARGET_MIN_SEC": _band["target_min"],
                             "DURATION_TARGET_MAX_SEC": _band["target_max"]},
        "authorization": {"adopted_by": "step01_commit",
                          "run_id": run_id,
                          "rule_version": _mrt.RULE_VERSION},
        "previous_receipt": None,
    }
    try:
        _pc.validate_receipt(receipt)
        _pc.verify(receipt, apply=False)
    except Exception as exc:                                  # noqa: BLE001
        # 带上稳定 code（ContextError.code）：调用方与日志按 code 判读，
        # 不必从中文消息里猜是哪一类失败。
        _code = str(getattr(exc, "code", "") or exc.__class__.__name__)
        return "create_failed:%s: %s" % (_code, exc)
    tmp = ch / "step01_adoption.json.tmp"
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ch / "step01_adoption.json")
    return "created"


def _sync_adoption_receipt(revision: str, run_id: str) -> str:
    """S06：COMMITTED 后把采纳凭据与新产物同步（同一事务内）.

    只更新**已存在**的凭据：首次采纳凭据由正式发布流程产生（本仓库只有消费端），
    在缺少契约生产端时凭空造一份会把"谁采纳的、依据什么"变成编造。返回状态串，
    写失败直接抛错——由调用方回滚整组发布。

    更新字段与 production_context 的消费口径一致：script_revision / source_run_id /
    adopted_at / formal_sha256（**SHA-256**，与 verify 的 _sha256_file 对齐）。
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path

    p = _Path(_mrt.CHAPTER_DIR) / "step01_adoption.json"
    if not p.exists():
        return _create_adoption_receipt(revision, run_id)
    receipt = json.loads(p.read_text(encoding="utf-8"))
    formal = dict(receipt.get("formal_sha256") or {})
    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        f = _Path(_mrt.CHAPTER_DIR) / name
        if not f.exists():
            raise RuntimeError("采纳凭据更新缺少正式产物: %s" % name)
        formal[name] = _hashlib.sha256(f.read_bytes()).hexdigest()
    receipt["formal_sha256"] = formal
    receipt["script_revision"] = revision
    receipt["source_run_id"] = run_id
    receipt["adopted_at"] = _mrt._step01_now()
    receipt["status"] = "adopted_and_verified"
    # 时长带必须跟着新产物走：凭据里的 effective_config 是 verify() 的核验输入，
    # 沿用旧带会让"新稿 + 旧配置"通过不了导入顺序检查（也会误导下游）。
    from shared.config import duration_band as _db
    _b = _db()
    receipt["effective_config"] = {"DURATION_MIN_SEC": _b["min"],
                                   "DURATION_MAX_SEC": _b["max"],
                                   "DURATION_TARGET_MIN_SEC": _b["target_min"],
                                   "DURATION_TARGET_MAX_SEC": _b["target_max"]}
    atomic = p.with_suffix(".json.tmp")
    atomic.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    from tools import production_context as _pc2
    try:
        _pc2.load_receipt(atomic)
        _pc2.verify(receipt, apply=False)
    except Exception as exc:                                  # noqa: BLE001
        try:
            atomic.unlink()
        except OSError:
            pass
        raise RuntimeError("采纳凭据核验失败（不替换旧凭据）: %s" % exc) from exc
    atomic.replace(p)
    return "updated"


def _commit_step01_outputs(script: dict, novel_text: str, *,
                           plan: "dict | None" = None,
                           hook_ctx: "dict | None" = None,
                           audit_model: "str | None" = None,
                           label: str = "落盘前",
                           source_revision: str = "",
                           source_aliases: "dict | None" = None) -> tuple:
    """**唯一最终提交点**：三条入口（正常 / 缓存 / BEST-GUARD）都只走这里.

    `source_revision` / `source_aliases`：BEST-GUARD 选中的**历史快照自身**的
    revision 与各版本身份（选稿边界算好带过来的，v6.129 / 2026-09-30）。它可能与
    本函数入口的 revision 不同（大纲覆盖会改哈希），也不等于修后 revision——
    BLOCK 时三者都要登记，否则选稿那边永远查不到这份欠账。

    Returns:
        `(script, summary)`。`summary["status"]` 是 `COMMITTED` / `BLOCK`；
        BLOCK 时正式三份产物**未被替换**，只写了 BLOCK manifest 与诊断快照。
    """
    from shared.quality_gate import (SCRIPT_REVISION_VERSION,
                                     build_step01_commit_manifest,
                                     compute_script_revision,
                                     ensure_script_revision,
                                     require_semantic_gate,
                                     verify_step01_commit_manifest,
                                     write_step01_commit_manifest)

    # 入口即复制（2026-10-28）：本函数会就地规范化稿件（例如按开关 pop 掉 hook），
    # 而失败路径要求**调用方手里的基线一字不变**——原实现直接把调用方的 dict 改掉，
    # 一次 finalize 失败就会把被保护基线换成「改了一半」的版本。返回值是副本，
    # 调用方必须使用返回值（三条入口本来就只看返回值）。
    script = copy.deepcopy(script)

    run_id = _mrt._step01_run_id()
    summary: dict = {"status": "BLOCK", "run_id": run_id, "audit_repair_rounds": 0,
                     # v6.133：修复轮结论（improved / no_progress / call_failed /
                     # patch_unusable / oversized_group）——"零改善"不再吞掉
                     # "修复调用失败"与"响应不可用"这两类
                     "repair_outcome": "",
                     "blocking": [], "reused_audit": False, "detail": ""}
    _mrt._metrics_inc("commit.attempts")

    # M2.3 / CP2（2026-11-16）：**候选已生成并 finalize**——阶段边界即落盘。
    # 之后任何阶段被预算/网络/上限拦下，都不必重新生成正文。
    from shared import step01_checkpoint as _ckpt
    try:
        summary["checkpoint_cp2"] = _ckpt.write(
            _mrt.CHAPTER_DIR, _ckpt.CP2_CANDIDATE_GENERATED, script=script,
            extra={"run_id": run_id, "label": label,
                   "source_revision": source_revision}, log=_mrt._logger)
    except Exception as _degrade_exc:  # noqa: BLE001
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:_commit_step01_outputs", _degrade_exc)

    # v6.125（用户 2026-09-25 决定，第 1 项）：BLOCK 时必须**同时**登记「进入提交点
    # 的原稿 revision」。
    #
    # 只登记修后 revision 的后果（实测 run 193736）：台账新增的是修后的
    # `1d1cb454`，而被 BEST-GUARD 选中的 `script_result_20260924_215530.json`
    # 自身 revision `f434e5090d7b` **不在台账内** → 下一轮选稿照样可以选中它 →
    # 又 BLOCK 一次。台账的本意（"让选稿避开已知欠账的旧稿"）在这条路径上落空。
    #
    # 2026-09-27（用户裁决 4）：`_cross_run_best_guard` 里「全部候选都欠账 →
    # 放开限制」的兜底分支**已删除**——判据指纹未变的欠账稿不再被放回候选池
    # （没有合格历史候选时由新稿进提交门）。这条登记因此是**硬排除**，不再是
    # 只在「有别的候选时」生效的软护栏。
    _entry_revision = ""
    _entry_aliases: dict = {}
    try:
        # 2026-09-30 复核第 4 轮 P1：别名要在**这一时点**算（此刻的 `script` 是入口
        # 原稿）——后面 `script` 会被修复轮替换，再算就对不上这份欠账了。
        _entry_aliases = _revision_aliases_or_empty(script)
        _entry_revision = str(_entry_aliases.get(SCRIPT_REVISION_VERSION) or "")
    except Exception as _e:
        _mrt._logger.warn(f"[COMMIT] 进入提交点的 revision 计算失败（欠账台账少记一条）: {_e}")
    summary["entry_revision"] = _entry_revision

    # M1.2：局部修订的增量审计作用域。仅在有已 COMMITTED 基线、且确有改动镜
    # 时才启用；作用域严格等于实际改动镜（不拉邻镜），邻镜连续性由确定性检查兜底。
    _active_audit_scope: set = set()
    try:
        _baseline_script = _mfa._committed_baseline_script()
        if _baseline_script is not None:
            _changed = _mfa._changed_shot_ids(script, _baseline_script)
            if 0 < len(_changed) < len(_baseline_script.get("shots") or []):
                _active_audit_scope = _changed
                _mrt._logger.info(
                    "[COMMIT] 增量审计作用域：changed_shot_ids=%s（其余镜头仅确定性质量门全量生效）"
                    % ",".join(sorted(_active_audit_scope)))
    except Exception as _scope_err:
        _mrt._logger.warn("[COMMIT] 增量审计作用域计算失败，回退全量审计: %s" % _scope_err)

    # 统一收口：缓存稿和 BEST-GUARD 历史快照可能仍含旧钩子——归一在
    # `project_for_commit` 里做（唯一实现）；这里只保证禁用时不跑钩子质量门。

    # ---- 1. hook QC / 必要的 hook regen（允许语义写入的最后阶段之前）----
    if (_mfz._is_hook_enabled() and hook_ctx is not None and isinstance(script.get("hook"), dict)):
        try:
            _ensure_hook_quality(script, novel_text=novel_text, **hook_ctx)
        except Exception as exc:
            _mrt._logger.warn(f"[COMMIT] 钩子质量门异常（继续提交，问题会在审计里暴露）: {exc}")

    # §14 P3：**已通过验收的布局效果**跨 attempt 累积，供下面 `_finalize` 那一趟
    # `is_final_pass=True` 的 hard-fix 复用——「本轮已授权删除的条目不得按类别别名
    # 重建」这条规则因此要在**两个 pass** 上都成立，而不是只在修复轮内成立。
    # （必须声明在 `_finalize` **之前**：闭包按调用时解析，而 `_finalize` 在下面
    #   就被调了一次。）
    _effect_state: dict = {"effects": [], "bindings": {}, "revision": ""}

    def _finalize(_s):
        # §14：把「已通过验收的布局效果」交给 finalize 那趟 hard-fix。没有效果时
        # `repair_effects=None` ⇒ 约束行为**逐字不变**（三条入口共用本函数）。
        _view = None
        if _effect_state.get("effects"):
            try:
                from step01_script_gen.constraints.types import RepairEffectView
                _view = RepairEffectView(
                    round_id="finalize", source_revision=_effect_state.get("revision", ""),
                    effects=tuple(_effect_state["effects"]),
                    bindings=dict(_effect_state.get("bindings") or {}))
            except Exception as _eff_view_err:
                _mrt._logger.warn(f"[COMMIT] 布局效果视图构建失败（finalize 按无效果处理）:"
                             f" {_eff_view_err}")
                _view = None
        # 投影算法只此一份（离线交付工具调同一个函数）：复制、hook 归一、finalize、
        # 剥运行字段、绑 revision。返回错误 ⇒ 由调用方决定 BLOCK。
        _out, _errs = _mfz.project_for_commit(_s, novel_text, label=label,
                                         repair_effects=_view)
        if _errs:
            raise _mfz.FinalizeError("; ".join(_errs))
        return _out

    try:
        script = _finalize(script)
    except _mfz.FinalizeError as exc:
        # finalize **正常返回但报告了错误**：不审计、不建 staged、不写三份产物，
        # 只写 BLOCK manifest 让下游知道这一版没有通过。`script` 仍是进入时的基线
        # （`_finalize` 在副本上跑），下一次尝试拿到的不是被改了一半的稿子。
        summary["repair_outcome"] = "finalize_failed"
        summary["detail"] = str(exc)
        summary["blocking"] = [{"code": "finalize_failed", "severity": "blocking",
                                "message": str(exc)}]
        _mfa._clear_staged_dir()
        _write_blocked_manifest(_entry_revision, run_id=run_id, detail=str(exc),
                                blocking=summary["blocking"])
        _mrt._console(f"  [COMMIT] finalize 未通过：{str(exc)[:160]}")
        return script, summary

    audit = None
    quality = None
    blocking: list = []
    # v6.135（工作包 sunny-kindling-wilkinson，审核约束③）：提交层**二次校验**（辅门，
    # 防单候选/缓存路径绕过候选层门）。位置契约：hook QC、finalize 等**所有可能改动
    # narration 的步骤之后**、最终提交判定之前。破损 ⇒ 不进审计/修复轮（修复按锁定
    # 契约不可修 `narration_segment`，09-26 02:30 实损：locked_changed 死锁），
    # BLOCK 原因记 `dialogue_truncated`（与 locked_changed/零改善区分，决策点 2 口径：
    # 这是生成稿完整性问题，不触发计划重选语义）。
    _dlg_skip_audit = False
    # 台词/事件两道契约门都通过后，才能把「仅指向锁定字段」的审计项降级；
    # 否则仍按 blocking 交回上游，避免掩盖真实台词/契约破损。
    _contracts_ok = True
    # v6.135：门只核「稿件 vs 本轮计划的选材台词」。**计划覆盖面（三审 P2-1 更正）**：
    # 生成/快照两入口的计划经 `check_plan_before_generation` 保证 dialogue_lines 非空；
    # **缓存入口的旧缓存可能没有 chapter_outline——此时门不启用（兼容取舍，显式记录），
    # 这类稿件不受台词门保护**，不得引申为"三入口均受硬门保护"。要给旧缓存补保护，
    # 需先给缓存路径接计划来源（另一工作包）。
    from step01_script_gen.plan_feasibility import (check_script_dialogue_integrity,
                                                    plan_dialogue_lines)
    _dlg_plan_lines = plan_dialogue_lines(plan) if plan else []
    _dlg_ok, _dlg_broken = True, []
    if _dlg_plan_lines:
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, plan)
        except Exception as _e:
            # v6.135 三审 P1-1：门自身异常 = **无法证明台词完整** → fail-closed BLOCK，
            # 不放行（缓存路径只靠本门复核，放行 = 门失效时裸奔进审计/提交）。
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _mrt._logger.error(f"[COMMIT] 台词完整性门异常，fail-closed BLOCK：{_e}")
    if not _dlg_ok:
        _dlg_total = len(_dlg_plan_lines)
        _gate_error = bool(_dlg_broken) and str(
            _dlg_broken[0].get("reason") or "").startswith("gate_error")
        # v6.135 四审 P2：门自身异常 ≠ 稿件截断——结构化消费者按 repair_outcome/
        # blocking[0].code 统计，检查故障不得计入 dialogue_truncated（把门故障算成
        # 稿件问题会污染"截断率"口径）。独立代码 dialogue_gate_error，detail 已区分。
        summary["repair_outcome"] = "dialogue_gate_error" if _gate_error \
            else "dialogue_truncated"
        summary["detail"] = (
            ("台词完整性硬门自身异常（fail-closed 不放行）：%s"
             % str(_dlg_broken[0].get("reason") or "")[:200])
            if _gate_error else
            "台词完整性硬门：%d/%d 句破损（%s），需在候选层重取——"
            "提交前修复按锁定契约不可修 narration_segment（09-26 locked_changed 死锁）"
            % (len(_dlg_broken), _dlg_total,
               "；".join("%s（%s）" % (b.get("line", "")[:30], b.get("reason"))
                         for b in _dlg_broken[:3])))
        _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
        _mrt._console(f"  [COMMIT] 台词完整性硬门 BLOCK（{len(_dlg_broken)}/{_dlg_total} 句破损）")
        blocking = [{"code": summary["repair_outcome"], "severity": "blocking",
                     "message": summary["detail"]}]
        _contracts_ok = False
        _dlg_skip_audit = True
    # v6.156（2026-09-30 复核第 5 组）：**事件履约契约同源复核**。拆分/修复/终稿都可能
    # 让「哪个镜头承载哪条节拍、说哪句台词」断开，而覆盖门与台词门**各自都能过**
    # （B1 声明由 D1 承载，成稿却在承载 B2 的镜头说 D1）。判据与候选入口**同一个
    # 函数**（不新造第二套口径）；只在计划带承载契约时生效（旧缓存沿用容忍口径）。
    if _dlg_plan_lines and not blocking:
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            # 2026-09-30 复核 P1-1：**终检也要强制声明**——删掉 `plan_id` 或整稿不填
            # `plan_refs` 时，候选入口过门不等于后续稿仍受约束（时长/结构/GAP 重生成
            # 会整稿替换）。带承载契约的计划在这里缺声明 ⇒ 显式失败。
            _ct_ok, _ct_viol, _ct_declared = check_event_contract(
                script, plan, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _mrt._logger.error(f"[COMMIT] 事件履约契约门异常，fail-closed BLOCK：{_e}")
        if not _ct_ok:
            summary["repair_outcome"] = "event_contract_broken"
            summary["detail"] = (
                "事件履约契约破损（B→D→镜头关系，拆分/修复后同源复核）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
            _mrt._console("  [COMMIT] 事件履约契约 BLOCK（节拍/台词/镜头关系断开）")
            blocking = [{"code": "event_contract_broken", "severity": "blocking",
                         "message": summary["detail"]}]
            _contracts_ok = False
            _dlg_skip_audit = True
    # `audit_ok` 必须**显式**记录：只看 `audit` 是否为 None 是 fail-open ——
    # 审计「调用失败」时 payload 照样是 dict（status=request_failed），
    # `if blocking or not audit` 会放行到提交。调用失败 **不等于** 0 issue。
    audit_ok = False
    # v6.135：台词完整性门已判破损时**零轮循环**——直接落到 BLOCK 路径
    # （blocking 已带 dialogue_truncated；summary["detail"] 已写）。
    _retry_state: dict = {}   # v6.137：修复轮 new_blocking 拒绝后的跨 attempt 重试状态
    # v6.139（修复 2，九审根因 2）：跨 attempt 的「已尝试未核实」审计问题身份集
    # ——触达兜底对同一身份只记一次账； `(shot, family)` 归并措辞漂移。
    _audit_attempted: set = set()
    _prev_audit_ids: set = set()   # 上一轮审计的身份集（跨轮比对：仍在/消失/新现）
    # 2026-11-14 S06 G1：上一轮 blocking 问题对象（不只是身份）——本轮若不再报出，
    # 必须有确定性证据证明已修，否则按「模型沉默 ≠ 已修复」拒收。
    _prev_audit_issues: list = []
    # G1 的结构化关闭依据基线：上一轮修复前的稿件快照（实体出现→消失的转变
    # 只能拿两版对照，不能只看当前稿）。deepcopy 与 `_prev_audit_issues` 同步。
    _prev_audit_script: dict | None = None
    # G1 的**独立关闭依据**：上一轮确定性质量门的 blocking 身份 `(镜头, family)`
    # 与"那一轮检查是否真的跑完"。只有「同一问题的确定性检查由报出转为干净」才算
    # 关闭——历史尝试记录（`_audit_attempted`）不作依据（2026-11-14 复核）。
    _prev_det_families: set = set()
    _prev_det_checked = False
    # v6.140（修复 3，九审根因 3）：本 run **暂缓**的联动组成员——某轮零解决且
    # 失败者是联动组时整组暂缓，剩余预算继续修其他目标；组问题留待提交门按
    # BLOCK（不做半条修复）。止损从「全局停机」限定到「失败组」。
    _deferred_targets: set = set()

    def _contract_reject_reason(_s) -> str:
        """**采纳修复稿之前**的契约复核（与候选入口同一个函数）；`""` = 通过.

        2026-09-30 复核第 4 轮 P1-1：提交层的契约门位于修复循环**之前**，而修复轮
        会写 narration / 拆镜 / 删补镜头并整份替换稿件（`script = _finalize(repaired)`）。
        破损的修复稿**不采纳**（保留基线），不把「改之前是对的」当成「改之后还是对的」。
        """
        if not _dlg_plan_lines:
            return ""
        from step01_script_gen.plan_feasibility import check_event_contract
        try:
            _ok, _viol, _ = check_event_contract(_s, plan, require_declaration=True)
        except Exception as _e:
            return "gate_error: %s" % _e
        if _ok:
            return ""
        return "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                      str(v.get("detail"))[:60])
                         for v in (_viol or [])[:3])

    # v6.153（2026-10-03 复核之十九 P1）：**同一次提交循环内**的审计复用。
    # 修复事务被**整轮回退**时稿件一个字没改（`compute_script_revision` 相同），
    # 于是下一轮的审计请求与上一轮**逐字相同**——再 POST 一次就是既定规则
    # （总计划 §1.3）禁止的"同一 revision 反复抽样博一次偶然 PASS"。实测代价：
    # 同一次运行内同请求指纹两次调用，白花 133.5 秒，且换回一份**不同**的问题
    # 清单（2 条 vs 5 条），把"零改善"这个结论也搅浑。
    # 键是**整条请求的身份**（提示逐字 + 请求参数），不是 revision：稿变了、
    # 定向项变了、规则/提示版本变了、档位或通道变了，身份都会变 ⇒ 自然重审。
    # **失败**的审计不进 memo（失败不能冒充可复用结论）。
    _audit_memo: dict = {}
    # T2（2026-11-18）：strict 预审门的「healed」修复稿由**调用方显式决定**是否
    # 消费。这里只允许每轮提交消费一次，避免自愈→送审→又自愈的循环。
    _pre_audit_heal_used = False
    for _attempt in range(0 if _dlg_skip_audit else _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS + 1):
        quality = _msc._build_step01_quality_gate(script, novel_text)
        _mrt._timing_mark("commit_quality", status=quality.get("status"))
        _q_blocking = _mfa._quality_blocking_records(quality)
        _audit_focus_now = _mfa._final_audit_focus(script, quality)
        _audit_identity = _mfa._final_audit_identity(
            script, novel_text, plan, _audit_focus_now, audit_model, run_id)
        _reuse_source = ""
        audit = _audit_memo.get(_audit_identity)
        if audit is not None:
            _reuse_source = "in_loop"
            _audit_reused = True
        else:
            # S05：把**内容身份**一起交给复用判定（原文/计划/定向项/契约都要对上）
            audit = _mfa._reusable_final_audit(
                script, expected_identity=_audit_identity,
                expected_source_identity=_mfa._final_audit_source_identity(
                    script, novel_text, plan, audit_model),
                # M2.3：同稿同契约、仅定向项不同时把失配点精确归到 audit_focus
                expected_identity_without_focus=_mfa._final_audit_identity(
                    script, novel_text, plan, [], audit_model, run_id))
            _audit_reused = audit is not None
            if _audit_reused:
                _reuse_source = "disk"
                _mrt._logger.info(
                    "[COMMIT] 复用已有 final audit（revision + 规则指纹 + scope 全同且为 ok）"
                )
            else:
                audit = _mfa._run_final_semantic_audit(
                    script, novel_text, plan, model=audit_model,
                    focus=_audit_focus_now, run_id=run_id)
                # T2（2026-11-18）：strict 预审门报告 `healed` 时，修复稿只是
                # **建议**——由这里显式决定是否消费到本轮内存稿。消费后必须重算
                # 质量门/定向项/审计身份再送审，避免拿旧身份复用或错配 CP3。
                if str(audit.get("status") or "") == "pre_audit_healed":
                    _healed_script = audit.get("healed_script")
                    if isinstance(_healed_script, dict) and not _pre_audit_heal_used:
                        _pre_audit_heal_used = True
                        script = _healed_script
                        _healed_meta = {
                            "hard_before": len(audit.get("issues") or []),
                            "repairs": len((audit.get("healed_report") or {})
                                           .get("repairs") or []),
                        }
                        summary["pre_audit_healed"] = _healed_meta
                        _mrt._logger.warn(
                            "[COMMIT] 送审前确定性局部自愈已消费到本轮内存稿"
                            "（不写正式产物）：hard %d→0，repairs=%d；重算身份后送审"
                            % (_healed_meta["hard_before"], _healed_meta["repairs"]))
                        quality = _msc._build_step01_quality_gate(script, novel_text)
                        _mrt._timing_mark("commit_quality", status=quality.get("status"))
                        _q_blocking = _mfa._quality_blocking_records(quality)
                        _audit_focus_now = _mfa._final_audit_focus(script, quality)
                        _audit_identity = _mfa._final_audit_identity(
                            script, novel_text, plan, _audit_focus_now,
                            audit_model, run_id)
                        audit = _mfa._run_final_semantic_audit(
                            script, novel_text, plan, model=audit_model,
                            focus=_audit_focus_now, run_id=run_id)
                        if str(audit.get("status") or "") == "pre_audit_healed":
                            audit = {
                                **audit,
                                "status": "blocked",
                                "audit_status": "blocked",
                                "error": ("pre_audit_hard: 自愈后再次要求自愈"
                                          "（拒绝循环，保持 blocked）"),
                                "pre_audit_status": "unresolved",
                            }
                    else:
                        audit = {
                            **audit,
                            "status": "blocked",
                            "audit_status": "blocked",
                            "error": ("pre_audit_hard: 自愈稿不可用或本轮已自愈过"
                                      "（拒绝循环，保持 blocked）"),
                            "pre_audit_status": "unresolved",
                        }
                _mrt._timing_mark("commit_audit", status=audit.get("status"))
                # 只把**真的跑出结论**、且身份可复核的审计放进 memo。
                _memo = _mfa._memoizable_final_audit(audit)
                if _memo is not None:
                    _audit_memo[_audit_identity] = _memo
        if _reuse_source == "in_loop":
            summary["reused_audit"] = True
            _mrt._logger.info(
                "[COMMIT] 复用**本轮已完成**的 final audit（稿件与请求逐字未变，不再调用）："
                "身份 %s" % _audit_identity[:16]
            )
        elif _reuse_source == "disk":
            summary["reused_audit"] = True
        # v6.112：每轮审计都进诊断台账（含复用轮），供区分「跨镜顺带修好」
        # 与「同一内容 severity 波动」——见 `_record_audit_diag`。
        _mfa._record_audit_diag(audit, phase="commit_round", round_index=_attempt + 1,
                           reused=_audit_reused, run_id=run_id, script=script,
                           reuse_source=_reuse_source)
        # 审计失败（超时/请求失败/解析失败）**不等于 0 issue** —— 直接 BLOCK。
        if str(audit.get("status") or "").strip().lower() != "ok":
            summary["detail"] = (
                "final semantic audit %s: %s" % (audit.get("status"), audit.get("error"))
            )
            _mrt._logger.warn(f"[COMMIT] x {summary['detail']}（调用失败 != 0 issue，直接 BLOCK）")
            break
        audit_ok = True
        # M2.3 / CP3（2026-11-16）：**首轮审计完成**——阶段边界即落盘（含复用轮）。
        # 只记首轮：后续轮次由 CP4（修复轮）承担，避免同一份稿反复写大文件。
        if _attempt == 0:
            from shared import step01_checkpoint as _ckpt
            try:
                summary["checkpoint_cp3"] = _ckpt.write(
                    _mrt.CHAPTER_DIR, _ckpt.CP3_INITIAL_AUDIT_DONE, script=script,
                    audit=audit, extra={"run_id": run_id, "round": 1,
                                        "reused": bool(_audit_reused)}, log=_mrt._logger)
            except Exception as _degrade_exc:  # noqa: BLE001
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("step01_script_gen/main.py:_commit_step01_outputs", _degrade_exc)
        # blocking = 审计条目 ∪ 确定性质量门条目（两者都必须被修掉才算通过）
        if _contracts_ok:
            _downgraded = _mfa._downgrade_locked_only_audit_issues(audit, contracts_ok=True)
            _stage_downgraded = _mfa._downgrade_stage_enum_false_positives(
                audit, script, contracts_ok=True)
            _delta_downgraded = _mfa._downgrade_out_of_delta_audit_issues(
                audit, _active_audit_scope, contracts_ok=True)
            if _downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项仅指向已过契约门的锁定字段，已降级为 warning"
                    % _downgraded)
            if _stage_downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项把合法 stage 枚举误判为非法，已降级为 warning"
                    % _stage_downgraded)
            if _delta_downgraded:
                _mrt._logger.warn(
                    "[COMMIT] 本轮 %d 条审计项落在未改动镜头（增量作用域），已降级为 warning"
                    % _delta_downgraded)
        blocking = _mfa._audit_blocking_records(audit) + _q_blocking
        # 2026-11-14 S06 G1（批准，仅阻塞级）+ P1/G1（完整裁决）：
        # **上一轮 blocking 本轮不再报出 ≠ 已修复**——必须有确定性关闭依据。
        # 证据是自由文本、从未字面出现在稿件里时 `_audit_issue_resolved` 返回 True
        # （无法区分），这类条目不在本闸门可判范围内，保持既有语义。
        #
        # 关闭依据只有三类，都必须**独立于"模型动过手"**：
        #   (a) 判据证据已不在稿件中（`_audit_issue_resolved`）；
        #   (b) **同一 (镜头, family) 的确定性检查**上一轮报出、本轮干净
        #       （两侧都真的跑完，checker_failed 不算）。这是"同一问题换了一个
        #       确定性判据复核通过"，不是尝试记录；
        #   (c) 问题点名的实体在修复前的目标布局里存在、修复后消失
        #       （`_audit_issue_structurally_removed`，结构化内容事实）。
        #
        # 明确**不再**接受「字段触达 / 历史尝试」当关闭依据（2026-11-14 复核）：
        # `_audit_attempted` 只记录"模型改过被点名的字段、且那一轮通过了验收"，
        # 而验收本身可以靠字段触达兜底——于是"改字段 → 修复轮接受 → 下轮审计沉默"
        # 就能豁免 G1。`_audit_attempted` 仍保留给兜底记账（同一身份只记一次），
        # 但不再参与本闸门。
        #
        # 旧豁免「判据证据缺失/过短就跳过」也已移除：缺证据不能反证已修复，
        # 落进未闭合（待复核）。
        if _prev_audit_issues:
            _now_ids_g1 = {
                _mrc._audit_issue_identity(_i)
                for _i in _mfa._audit_payload_to_issues(audit) if _mrt.is_blocking(_i)}
            _now_det_families = {_mrc._record_family_identity(_r) for _r in _q_blocking
                                 if isinstance(_r, dict)}
            _now_det_checked = not bool(quality.get("checker_failed"))
            _g1_unclosed = _mrc._g1_unclosed_issues(
                _prev_audit_issues, _now_ids_g1, script,
                prev_det_families=_prev_det_families,
                now_det_families=_now_det_families,
                det_checked=_prev_det_checked and _now_det_checked,
                prev_script=_prev_audit_script)
            if _g1_unclosed:
                for _pi in _g1_unclosed:
                    blocking.append({
                        "code": "blocking_unclosed_without_evidence",
                        "severity": "visual_risk",
                        "shot_id": getattr(_pi, "shot_id", ""),
                        "frame_id": getattr(_pi, "frame_id", "") or "",
                        "entity_id": getattr(_pi, "entity_id", "") or "",
                        "field_path": getattr(_pi, "field_path", "") or "",
                        "message": ("上一轮 blocking 本轮未再报出，但缺少确定性关闭依据"
                                    "（判据证据仍在稿件中，且同一问题的确定性检查"
                                    "本轮没有由报出转为干净——模型沉默、仅改字段、"
                                    "历史尝试都不等于已修复；G1 门禁）"),
                    })
                summary["detail"] = (
                    "G1：上一轮 %d 条 blocking 本轮未再报出且无确定性关闭依据"
                    "（证据仍在稿中或不可判，且无确定性复核通过；"
                    "blocking 必须有依据关闭）"
                    % len(_g1_unclosed))
                _mrt._logger.warn("[COMMIT] x %s" % summary["detail"])
                break
        if not blocking:
            break
        _mrt._logger.warn(
            "[COMMIT] 第 %d 轮发现 %d 条 blocking（审计 %d + 确定性质量门 %d）：%s"
            % (_attempt + 1, len(blocking), len(_mfa._audit_blocking_records(audit)),
               len(_q_blocking),
               [(b.get("shot_id"), b.get("code")) for b in blocking[:5]])
        )
        # 2026-10-28（M08）：审计包可以显式声明「只审计、不修复」。关闭传输重试
        # **不等于**关闭修复轮：修复轮会再发一次模型调用，还可能在下一轮触发
        # **第二次审计**。`STEP01_AUDIT_ONLY=1` 时本轮审计即终局——保留 blocking、
        # 不调用修复、不再发审计。默认关闭，未设该变量时行为逐字不变。
        if str(os.getenv("STEP01_AUDIT_ONLY", "0")).strip() == "1":
            summary["repair_outcome"] = "audit_only"
            summary["detail"] = (
                "STEP01_AUDIT_ONLY=1：保留 %d 条 blocking，未进入修复轮（不再发调用/审计）"
                % len(blocking))
            _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            break
        # v6.139（修复 2）：审计问题**跨轮身份比对**——按 (shot, family) 归并措辞
        # 漂移（同一问题四轮四名是实测常态），跨轮追踪「具体哪些事件矛盾还在」，
        # 不以审计条数判断收敛。
        _audit_issue_recs = _mfa._audit_payload_to_issues(audit)
        _now_ids = {_mrc._audit_issue_identity(i) for i in _audit_issue_recs
                    if _mrt.is_blocking(i)}
        if _prev_audit_ids:
            _still = _now_ids & _prev_audit_ids
            _gone = _prev_audit_ids - _now_ids
            _fresh = _now_ids - _prev_audit_ids
            _mrt._logger.warn(
                "[COMMIT] 审计身份比对（(镜头,族) 归并措辞漂移）：仍在 %s｜消失 %s｜"
                "新现 %s%s"
                % (sorted(_still) or "无", sorted(_gone) or "无", sorted(_fresh) or "无",
                   "｜其中 %s 为已尝试未核实身份（触达兜底已停用）"
                   % sorted(_fresh & _audit_attempted) if _fresh & _audit_attempted else "")
            )
        _prev_audit_ids = _now_ids
        _prev_audit_issues = _audit_issue_recs
        try:
            import copy as _copy_g1
            _prev_audit_script = _copy_g1.deepcopy(script)
        except Exception:
            _prev_audit_script = None
        # G1 的独立关闭依据基线：本轮（修复前）确定性质量门的 blocking 身份，
        # 以及检查是否真的跑完。下一轮审计若对同一 (镜头, family) 沉默，
        # 只有"确定性检查由报出转为干净"才算有依据关闭。
        _prev_det_families = {_mrc._record_family_identity(_r) for _r in _q_blocking
                              if isinstance(_r, dict)}
        _prev_det_checked = not bool(quality.get("checker_failed"))
        if _attempt >= _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS:
            summary["detail"] = (
                "达到 %d 个修复轮上限后仍有 %d 条 blocking"
                "（审计 %d + 确定性质量门 %d）"
                % (_msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS, len(blocking),
                   len(_mfa._audit_blocking_records(audit)), len(_q_blocking))
            )
            break
        # M04：提交层的每一轮修复同样要能容纳「本轮动作 + 最终审计 + 收尾」。
        # 不足即冻结候选并给 `budget_exhausted` 明确终局，不把「时间不够」
        # 写成「内容不可行」，也不借用上一轮审计结论。
        _adm_commit = _mrt._action_budget_admission(
            "commit_repair_round", _mrt._action_cost_sec("repair"))
        if _mrt._budget_exceeded_with_pause("commit_repair_round") or not _adm_commit.get("allowed"):
            _freeze_commit = _mfa._freeze_budget_exhausted(
                script, action="commit_repair_round",
                admission=_adm_commit, run_id=run_id)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_commit)
            summary["repair_outcome"] = "budget_exhausted"
            summary["detail"] = (
                "预算不足：剩余 %.1fs < 本轮修复 %.1fs + 最终审计 %.1fs + 收尾 %.1fs；"
                "已保存冻结候选（%s），停止 final audit 修复轮"
                % (float(_adm_commit.get("remaining_budget_sec") or 0),
                   float(_adm_commit.get("action_cost_sec") or 0),
                   float(_adm_commit.get("audit_cap_sec") or 0),
                   float(_adm_commit.get("commit_reserve_sec") or 0),
                   _freeze_commit.get("output_root") or "隔离根"))
            _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            break
        # v6.119（用户口径）：进修复轮**之前**先查成对目标契约——不可执行就显式
        # BLOCK，不再花两个审计轮去博一次「也许能顺带修好」（那纯属烧审计额度）。
        _shots_by_id_rt = {f"shot_{_s.get('index')}": _s
                           for _s in (script.get("shots") or [])
                           if isinstance(_s, dict)}
        if isinstance(script.get("hook"), dict):
            _shots_by_id_rt["hook"] = script["hook"]
        # 镜头号记法归一（`"shot5"` → `shot_5`，双射、指向不变）本身在
        # `_target_id_of` 里做；这里只让它**可见**——同一份提示词下模型可能第 1 轮
        # 写 `"5"`、第 2 轮写 `"shot5"`，静默归一就没人知道输入形态变了。
        from step01_script_gen.repair_fields import shot_id_aliases
        _sid_aliases = shot_id_aliases((audit or {}).get("issues") or [])
        if _sid_aliases:
            _mrt._logger.warn("[COMMIT] 审计镜头号记法已归一（%d 处，指向不变）：%s"
                         % (len(_sid_aliases), _sid_aliases[:8]))
        _rt_problems = _mfa._audit_repair_target_problems(audit, _shots_by_id_rt)
        if _rt_problems:
            # v6.142（09-26 用户决定，P1-3）：**降级为告警，不再就地 BLOCK**。
            # 旧口径（v6.119）是「任一条目标无效 → 整条拒绝」+「有一条不可执行就
            # 整轮 BLOCK、**一轮修复都不发**」。20:25 run 实锤：shot 3 的 12 条越界
            # 目标把整轮修复全部掐掉，同轮里完全可修的其它目标一个都没动——被一条
            # 坏目标连坐。新口径：有问题的条目**保留 blocking**（下一轮审计照常
            # 重报），本轮不授权它；其余合法目标照常进修复轮。改不动 / 全是坏目标
            # 的终局，由既有「提交前修复轮零改善即停」判据给出（失败照常显式）。
            _mrt._logger.warn(
                "[COMMIT] %d 条审计条目缺少可执行的定向修复目标"
                "（保留 blocking；本轮不授权该条，本轮其余目标继续修）：%s"
                % (len(_rt_problems), _rt_problems[:3]))
        # ---- 受限修复（字段授权 + 内容保留 + 候选验收，全部走修复事务）----
        _before_rev = compute_script_revision(script)
        _repair_diag: list = []
        _repair_block_reasons: list = []
        # M2.2b（2026-11-16，2026-11-17 补双层）：**成对额度预检**。修复与"修后复审"
        # 是一对：两层（逻辑调用 / 网络尝试）任一不足以覆盖两条时，发起修复只会产出
        # 一份没人复审的孤儿稿（19:01 实测：修复 accepted 后第 3 次调用被 cap 拦下，
        # 成果只留在内存）。这里先保存当前快照再报 budget_exhausted，不发起孤儿调用。
        _paired = _mps._paired_repair_budget()
        if _paired["short"]:
            _mps._persist_repair_round(script, round_no=_attempt, audit=audit,
                                  novel_text=novel_text)
            summary["repair_outcome"] = "budget_exhausted"
            summary["budget_paired_precheck"] = _paired
            summary["detail"] = (
                "成对额度预检不通过（不足项：%s）：逻辑 修复 %s / 复审 %s / 全局 %s；"
                "尝试 修复 %s / 复审 %s / 全局 %s——不足以覆盖「1 次修复 + 1 次修后"
                "复审」→ 不发起孤儿修复；已保存当前稿快照"
                % (", ".join(_paired.get("short_on") or []),
                   _paired.get("remaining_logical_repair"),
                   _paired.get("remaining_logical_audit"),
                   _paired.get("remaining_logical_total"),
                   _paired.get("remaining_attempts_repair"),
                   _paired.get("remaining_attempts_audit"),
                   _paired.get("remaining_attempts_total")))
            _mrt._logger.warn("[COMMIT] " + summary["detail"])
            break
        repaired = _mrr._repair_shot_warnings(
            script, novel_text,
            bank=(hook_ctx or {}).get("bank"),
            objects_bank=(hook_ctx or {}).get("objects_bank"),
            model=audit_model,
            audit_issues=_mfa._audit_payload_to_issues(audit),
            max_rounds=1,          # §1.1：提交点每一轮只发一次修复调用
            repair_diag=_repair_diag,
            block_reasons=_repair_block_reasons,
            retry_state=_retry_state,
            audit_attempted=_audit_attempted,
            defer_targets=_deferred_targets,
            effect_state=_effect_state,
            # §14 P2 窄协议：审计产出的「称谓 → 已在场人物」显式关系。
            # 修复端**只**用它裁决「这条删除该不该保留」；缺它 = 未声明 ⇒ 不猜。
            audit_relations=_mfa._audit_payload_to_relations(audit),
        )
        # v6.119：修复事务诊断立刻落台账（不等提交结果）——BLOCK 路径正是最需要它
        # 的时候，而 BLOCK 时后续代码可能直接 break/return。
        _mfa._record_repair_diag(run_id=run_id, attempt=_attempt + 1,
                            records=_repair_diag)
        # v6.122（用户决定 B）：**一次改不完的联动组 → 立即 BLOCK**。
        # 这一轮没有调用 LLM、也不会再发审计（省下两轮修复 + 两次审计调用）：
        # 组超过上限时跳过它只会让同一份审计每轮重复声明、直到额度耗尽。
        if _repair_block_reasons:
            summary["repair_outcome"] = "oversized_group"
            summary["detail"] = (
                "联动修复目标超出单轮可修上限（%d 条）：%s"
                % (len(_repair_block_reasons), _repair_block_reasons[:2]))
            _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            break
        summary["audit_repair_rounds"] += 1
        # 零改善：修复轮被拒或没动一个字 → 再发一轮只会重复同一批目标。
        #
        # v6.133（用户 2026-09-25）：**"模型没修好"与"修复请求没得到完整结果"必须分开报**。
        # 09-25 实跑的真实 BLOCK 直接原因是修复响应被截断（`max_output_tokens`，16k 上限含
        # 思维链 token）→ 整份丢弃 → 稿件一个字没变 → 这里只报"零改善"，把调用失败说成了
        # 内容结论。现在按台账里的 `kind="call_failed"` 记录区分，并把原因写进 detail。
        if compute_script_revision(repaired) == _before_rev:
            # v6.137：new_blocking 被拒 → 重试状态已导出（脚本回到基线，revision
            # 与被审计稿一致 ⇒ 下一轮审计**零审计调用**）。只在
            # 下一次 attempt 仍属修复预算（§1.1 最多 2 轮）时续跑。
            # v6.153：这句此前是**空头承诺**——盘上的 `_reusable_final_audit` 读的是
            # 正式产物/sidecar，而本轮刚跑完的审计**根本没写盘**，于是下一轮照旧
            # POST（实测同指纹两次调用、白花 133.5 秒）。现在由循环内的
            # `_audit_memo`（按**请求身份**索引）兜住这条路径。
            # M03：只有**结构化状态明确发放了一次答卷纠正机会**、且不是终局时
            # 才续跑。`feedback` 文本只作展示，不再充当判据；工程故障 / 重复
            # 拒因 / 一次机会已用完都在发请求之前终局。
            # 窄兼容通道：只有 v6.137 遗留格式（外部桩手写 `feedback`、完全
            # 没有 `feedback_records`）才按旧信号续跑。生产路径的
            # `_repair_shot_warnings` 一律写结构化状态，不会走这条，也不把文本
            # 当分类依据。
            _legacy_pending = (bool(_retry_state.get("feedback"))
                               and not _retry_state.get("feedback_records")
                               and not _retry_state.get("terminal"))
            _grant = (bool(_retry_state.get("correction_granted"))
                      or _legacy_pending)
            if (_grant and not _retry_state.get("terminal")
                    and _attempt + 1 < _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                _mrt._logger.warn(
                    "[COMMIT] 修复轮带结构化反馈重试待跑"
                    "（codes=%s，剔除 %s，revision=%s）→ 下一轮继续"
                    % ([r.get("code") for r in
                        (_retry_state.get("feedback_records") or [])],
                       _retry_state.get("excluded"),
                       str(_retry_state.get("source_revision") or "")[:12]))
                continue
            _call_failed = [r for r in _repair_diag if r.get("kind") == "call_failed"]
            # v6.133（2026-09-26 复核补口）：**"响应不可用"与"没改动稿件"也要分开**。
            # API 调用成功但补丁 JSON 解析失败 / envelope 结构无效 / 没返回替换 /
            # 替换全被校验拒——这四条此前只打日志，提交层看不见记录，于是与
            # "补丁有效但零改善"共用一个 `no_progress`，把"收到不可用响应"说成了
            # "修复没进展"。现在它们各自落 `kind="patch_unusable"` + `reason`。
            _unusable = [r for r in _repair_diag if r.get("kind") == "patch_unusable"]
            if _call_failed:
                summary["repair_outcome"] = "call_failed"
                summary["detail"] = ("修复调用失败/响应不完整（未得到完整结果，"
                                     "不是「修复无进展」）：%s"
                                     % str(_call_failed[-1].get("error") or "调用失败")[:200])
                _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            elif _unusable:
                _u_last = _unusable[-1]
                summary["repair_outcome"] = "patch_unusable"
                summary["detail"] = (
                    "修复响应不可用（拿到了完整响应、但补丁不可用，不是「修复无进展」）："
                    "%s%s" % (_u_last.get("reason") or "unknown",
                              ("｜%s" % _u_last.get("detail"))
                              if _u_last.get("detail") else ""))
                _mrt._logger.error(f"[COMMIT] {summary['detail']}")
            else:
                # `no_progress` 只表示**稿件没有改动**（且台账里没有调用失败/不可用补丁
                # 记录）；能取到上一轮事务时把它的结论一并写出，便于分辨"改完被回滚"
                # 与"根本没动"。
                _tx = [r for r in _repair_diag if "accepted" in r]
                # v6.140（修复 3，九审根因 3）：零解决发生在**真联动组**（≥2 成员，
                # `_linked_repair_groups` 会把单镜问题也折成单成员组）身上 → 止损
                # 局部化——组整组暂缓（本 run 后续轮次不再选它），剩余预算继续修
                # 其他目标；组的 blocking 保留，仍会照常 BLOCK（不做半条修复）。
                # 只有当 blocking 里还存在**组外**目标时才值得续跑，否则与直接
                # 停止等价、白烧一轮。
                _grp_members: set = set()
                if _tx:
                    for _g in (_tx[-1].get("groups") or []):
                        if len(_g) >= 2:
                            _grp_members.update(_g)
                _outside_exists = False
                if _grp_members:
                    from step01_script_gen.repair_fields import _target_id_of
                    for _b in blocking:
                        _tid = _target_id_of(str(_b.get("shot_id") or ""))
                        if _tid and _tid not in _grp_members:
                            _outside_exists = True
                            break
                if (_grp_members and _outside_exists
                        and _attempt + 1 < _msc.STEP01_AUDIT_REPAIR_MAX_ROUNDS):
                    _deferred_targets |= _grp_members
                    _mrt._logger.warn(
                        "[COMMIT] 联动组 %s 本轮零解决（reason=%s）→ 整组暂缓"
                        "（本 run 后续轮次不再选它）；组外目标（如 %s）继续用剩余"
                        "预算修复；组的 blocking 保留、仍会照常 BLOCK"
                        % (sorted(_grp_members), _tx[-1].get("reason"),
                           [b.get("shot_id") for b in blocking
                            if _target_id_of(str(b.get("shot_id") or ""))
                            not in _grp_members][:3]))
                    continue
                summary["repair_outcome"] = "no_progress"
                if _tx:
                    summary["detail"] = (
                        "提交前修复轮零改善（稿件未变；上一轮事务 accepted=%s resolved=%s "
                        "reason=%s），停止修复"
                        % (bool(_tx[-1].get("accepted")), _tx[-1].get("resolved"),
                           str(_tx[-1].get("reason") or "-")[:80]))
                else:
                    summary["detail"] = ("提交前修复轮零改善（稿件未变；台账中无调用失败/"
                                         "不可用补丁记录），停止修复")
                _mrt._logger.warn(f"[COMMIT] {summary['detail']}")
            script = repaired
            break
        try:
            _cand = _finalize(repaired)
        except _mfz.FinalizeError as exc:
            # 修复稿 finalize 失败 ⇒ **不采纳**：`script` 保持修复前基线（它自己的
            # finalize 已通过），本轮审计 blocking 与诊断原样保留；不再继续消耗调用。
            summary["repair_outcome"] = "repair_finalize_failed"
            summary["detail"] = str(exc)
            summary["repair_finalize_error"] = str(exc)
            break
        _ct_why = _contract_reject_reason(_cand)
        if _ct_why:
            # 契约破损的修复稿**不采纳**：`script` 保持在基线（本轮的审计 blocking 与
            # 这份基线同 revision，仍然有效），并显式说明「这一轮为什么白跑」。
            summary["repair_outcome"] = "contract_broken"
            summary["detail"] = (
                "修复稿破坏事件履约契约（%s）→ **不采纳**，保留基线；"
                "基线自身的 blocking 仍在（见 blocking 清单）" % _ct_why)
            _mrt._logger.error(f"[COMMIT] x {summary['detail']}")
            _mrt._console("  [COMMIT] 修复稿破坏事件履约契约 → 不采纳（保留基线）")
            break
        summary["repair_outcome"] = "improved"
        # M2.2（2026-11-16）：**先落盘再复审**——采纳即快照。修复已经花钱且通过
        # finalize/契约两道门，若随后的复审被发送上限或网络拦下，本轮成果不得丢失。
        summary["repair_round_snapshot"] = _mps._persist_repair_round(
            _cand, round_no=_attempt + 1, audit=audit, novel_text=novel_text)
        # M-A2（2026-11-17，用户复核第 3 条）：**采纳稿持久化失败必须阻断后续调用**。
        # "写失败只告警"对辅助检查点可以，对"已花钱且已采纳的修复稿"不行——继续发
        # 修后复审，只会产出一份无法恢复的结论。这里直接停，并把原因写进终局。
        if not (summary["repair_round_snapshot"] or {}).get("written"):
            _why = (summary["repair_round_snapshot"] or {}).get("error") or "unknown"
            summary["repair_outcome"] = "repair_snapshot_failed"
            summary["detail"] = (
                "修复稿已采纳但**落盘失败**（%s）→ 停止后续调用：继续复审只会产出一份"
                "无法恢复的结论。请检查磁盘/权限后重跑" % _why)
            _mrt._logger.error("[COMMIT] x " + summary["detail"])
            _mrt._console("  [COMMIT] 修复稿落盘失败 → 停止（不发起修后复审）")
            break
        script = _cand

    # ---- 写后不可变守卫：audit 前后 revision 必须相同 ----
    revision = ensure_script_revision(script)
    audit_rev = str((audit or {}).get("input_revision") or "")
    if audit and audit_rev and audit_rev != revision:
        # 审计之后又有语义写入 → 本轮审计作废（不写产物，交下一轮重来）
        summary["detail"] = (
            "final audit 之后 revision 发生变化（audit=%s current=%s）——审计作废"
            % (audit_rev[:12], revision[:12])
        )
        _mrt._logger.warn(f"[COMMIT] x {summary['detail']}")
        blocking = blocking or [{"code": "revision_changed_after_audit",
                                 "severity": "blocking", "message": summary["detail"]}]

    # S05（2026-11-11）：审计的**实际结论状态**贯通到提交摘要、提交清单与额度收据。
    # 此前它只在 BLOCK 分支里作为局部变量存在，COMMITTED 的 `step01_commit.json`
    # 因此写着空 `audit_status`——事后无法区分「审计跑成功」与「没有可用审计」。
    summary["audit_status"] = str((audit or {}).get("status") or "")

    if blocking or not audit_ok:
        summary["blocking"] = blocking
        summary["detail"] = summary["detail"] or (
            "final semantic audit 未通过" if audit_ok else "final semantic audit 未产出可用结论")
        # v6.152（C1/C2，2026-09-29 五次跑实测）：终局若是**审计调用失败**
        # （超时 / 传输耗尽 / 解析耗尽），`blocking` 还停在**上一次成功审计那一轮**
        # 的清单上——那是陈旧清单，写进 manifest 会误导读数（run 02 报 22 条、
        # run 04 报 6 条，真因却是调用失败），登记进欠账台账更会把一次**临时故障**
        # 变成稿件的**长期语义欠账**，污染 BEST-GUARD 选稿。
        # 处置：① manifest 只记本次失败这一条（`blocking_codes` 不再陈旧）；
        #      ② 陈旧清单降级为 `summary` 里的**诊断**字段，不进 manifest；
        #      ③ **不登记欠账**——「没有可用审计结论」不等于「稿件有这些欠账」。
        # 正式产物 `step01_audit.json` 依旧不动（与另两份同 revision）；本次是否
        # 审出来了由 manifest 的 `audit_status` 与 blocked sidecar 承担。
        _stale_blocking = blocking          # 只在审计调用失败时降级为诊断字段
        blocking, _audit_status, _audit_call_failed = _mfa._terminal_blocking_records(
            audit, blocking)
        summary["blocking"] = blocking
        # S05：终局状态以**本次实际审计**为准（含「调用失败」这类非 ok 值）
        summary["audit_status"] = _audit_status
        if _audit_call_failed:
            summary["last_known_blocking"] = _stale_blocking
            summary["last_known_blocking_note"] = (
                "审计调用失败（%s）——上述清单来自**上一次成功审计那一轮**，"
                "仅作诊断，不是本次结论" % _audit_status)
        _mrt._metrics_inc("commit.blocked")
        if _audit_call_failed:
            _mrt._logger.warn(
                "[COMMIT] 审计调用失败（%s）——本次**不登记欠账台账**："
                "「没有可用审计结论」不是稿件的语义欠账，登记会污染选稿台账；"
                "陈旧清单已降级为诊断字段 last_known_blocking（%d 条）"
                % (_audit_status, len(_stale_blocking)))
        else:
            _debt_issues = _debt_issue_records(blocking)
            _record_blocked_revision(revision, issues=_debt_issues,
                                     versions=_revision_aliases_or_empty(script))
            # v6.125（第 1 项）：同时登记**原稿** revision——被选中的历史稿本身才是
            # 「已知欠账的那一份」，只记修后版本等于把它留在候选池里反复入选。
            if _entry_revision and _entry_revision != revision:
                _record_blocked_revision(_entry_revision, issues=_debt_issues,
                                         versions=_entry_aliases)
                _mrt._logger.warn(
                    "[COMMIT] 欠账台账同时登记进入提交点的原稿 revision %s"
                    "（修后 %s）——否则该历史稿会因漏记而在后续选稿中再次入选"
                    % (_entry_revision[:12], revision[:12]))
            # v6.129（用户 2026-09-25 根因）：**选稿边界**记录的快照自身 revision 也要
            # 登记。它与入口/修后 revision 都可能不同（大纲覆盖参与哈希），而 guard 下次
            # 恰恰是用这个值查台账——不记它，同一份欠账稿会第三次被选中。
            if source_revision and source_revision not in (revision, _entry_revision):
                _record_blocked_revision(source_revision, issues=_debt_issues,
                                         versions=dict(source_aliases or {}))
                _mrt._logger.warn(
                    "[COMMIT] 欠账台账登记**被选中的历史快照自身** revision %s"
                    "（提交入口 %s / 修后 %s）——BEST-GUARD 用快照自身 revision 判欠账"
                    % (source_revision[:12], (_entry_revision or "")[:12], revision[:12]))
        _mfa._write_blocked_audit_sidecar(audit)
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking,
                               audit_status=_audit_status)
        _mfa._clear_staged_dir()
        try:
            _mps._snapshot_run_artifacts(script, audit=audit, novel_text=novel_text)  # 诊断证据 + 不可变配对
        except Exception:
            pass
        _mrt._console(f"  [COMMIT] x BLOCK：{summary['detail']}")
        _mrt._console("  [COMMIT] 三份正式产物未替换（step01_commit.json 标记为 BLOCK）")
        return script, summary

    # ---- staged 三件套 + 真自验 ----
    stage_dir = _mrt.CHAPTER_DIR / "_staged"
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json
        staged = {}
        for _name, _payload in (("step01_audit.json", audit),
                                ("step01_quality.json", quality),
                                (_mrt.OUTPUT_FILE.name, _msc._strip_runtime_fields(script))):
            _path = stage_dir / _name
            atomic_dump_json(_path, _payload, ensure_ascii=False, indent=2)
            staged[_name] = _path
        # 用**真实** gate 函数读 staged 文件——不是 mock，也不是自己重写一遍判据。
        staged_script = json.loads(staged[_mrt.OUTPUT_FILE.name].read_text(encoding="utf-8"))
        require_semantic_gate(
            staged["step01_quality.json"], staged["step01_audit.json"],
            staged_script, stage="step01")
    except Exception as exc:
        summary["detail"] = f"staged 自验失败：{exc}"
        _mrt._metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=blocking)
        _mfa._clear_staged_dir()
        _mrt._console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    # ---- 发布事务（S06）：一次事务覆盖**全部**发布步骤 ----
    # 单个文件的原子写不能保证"整组同时切换"：进程在写之间被杀，会留下
    # 新 audit + 旧 script 的身份不一致组合。这里先备份、再写、验完再 commit；
    # **manifest 构造与写入也在事务内**——它抛错同样要回滚，不能留下半套发布。
    from shared.publish_txn import PublishTransaction
    _txn = PublishTransaction(_mrt.CHAPTER_DIR, label="step01:%s" % run_id)
    manifest = None
    try:
        _txn.begin()
        from shared.json_io import atomic_dump_json
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_audit.json", audit,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_quality.json", quality,
                         ensure_ascii=False, indent=2)
        atomic_dump_json(_mrt.OUTPUT_FILE, _msc._strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
        # commit manifest 是「提交完成」的唯一标志
        manifest = build_step01_commit_manifest(
            run_id=run_id, created_at=_mrt._step01_now(), revision=revision,
            rule_source_fingerprint=_mrt.RULE_SOURCE_FINGERPRINT,
            rule_version=_mrt.RULE_VERSION, audit_scope=_mrt.STEP01_AUDIT_SCOPE,
            prompt_source_fingerprint=_mrt.PROMPT_SOURCE_FINGERPRINT,
            chapter_dir=_mrt.CHAPTER_DIR, status="COMMITTED",
            # S05：COMMITTED 也记审计实际状态——空字符串只表示「没有可用审计」，
            # 不能和「审计跑成功」混为一谈。
            audit_status=str(summary.get("audit_status") or ""))
        write_step01_commit_manifest(_mrt.CHAPTER_DIR, manifest)
        # 正式路径再自验一次（含 manifest）
        committed_script = json.loads(_mrt.OUTPUT_FILE.read_text(encoding="utf-8"))
        require_semantic_gate(_mrt.CHAPTER_DIR / "step01_quality.json",
                              _mrt.CHAPTER_DIR / "step01_audit.json", committed_script,
                              stage="step01")
        verify_step01_commit_manifest(_mrt.CHAPTER_DIR, committed_script, stage="step01",
                                      expected_chapter=_mrt.NOVEL_CHAPTER)
        # 凭据与新产物同事务：写失败同样回滚，避免"产物新、凭据旧"
        summary["adoption"] = _sync_adoption_receipt(revision, run_id)
    except Exception as exc:
        try:
            _restored = _txn.rollback("发布失败: %s" % exc)
            _rb = "已回滚 %d 个文件" % len(_restored)
        except Exception as _rb_err:                             # noqa: BLE001
            _rb = "回滚不完整（锁保留，待 recover_pending）：%s" % _rb_err
        # 保持阶段语义：替换阶段失败 vs 提交后自验失败，调用方/测试都按它判读。
        _stage = "提交后自验失败" if manifest is not None else "正式产物替换失败"
        summary["detail"] = f"{_stage}：{exc}（{_rb}）"
        _mrt._metrics_inc("commit.blocked")
        _write_blocked_manifest(revision, run_id=run_id,
                               detail=summary["detail"], blocking=[])
        _mfa._clear_staged_dir()
        _mrt._console(f"  [COMMIT] x {summary['detail']}")
        return script, summary

    _txn.commit()
    summary["status"] = "COMMITTED"
    summary["revision"] = revision
    summary["manifest"] = manifest
    # 调用方（main 的摘要/metrics）消费同一个 quality 对象——不再重算，
    # 避免「落盘的报告」与「打印的报告」是两个对象（那正是漂移的老形态）。
    summary["quality"] = quality
    summary["audit"] = audit
    _mrt._metrics_inc("commit.committed")
    _mrt._metrics_update("commit.last", {
        "status": "COMMITTED", "revision": revision,
        "audit_repair_rounds": summary["audit_repair_rounds"],
        "reused_audit": summary["reused_audit"],
        "blocking": 0, "run_id": run_id,
    })
    _mrt._logger.info(
        f"[COMMIT] OK 三份产物已提交（revision={revision[:12]}，"
        f"审计修复轮 {summary['audit_repair_rounds']}，"
        f"复用审计 {'是' if summary['reused_audit'] else '否'}）"
    )
    return script, summary


def _prepare_hook_for_qc(script: dict) -> None:
    _mrt._metrics_inc("hook.prepare")
    """Deterministically normalize a hook before the quality gate.

    Hook generation happens after the main hard-fix pass.  Without this, a hook
    with complete ``frame_layouts`` but missing legacy ``char_layout`` is sent
    for a full LLM regeneration, which is slow and random.  These constraints
    only synchronize already-known structure; they do not invent plot content.
    """
    from step01_script_gen.validation import post_fix_shot_prompts
    from step01_script_gen.constraints import ConstraintContext
    from step01_script_gen.constraints.element_markers import ElementMarkers
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint)
    from step01_script_gen.constraints.char_layout_sync import (
        CharLayoutSyncConstraint)
    from step01_script_gen.constraints.surface_backfill import (
        SurfaceBackfillConstraint)
    from step01_script_gen.constraints.gaze_state_repair import (
        GazeStateRepairConstraint)

    post_fix_shot_prompts(script)
    ctx = ConstraintContext.from_script(script, markers=ElementMarkers())
    for constraint in (
        FrameSpecConsistencyConstraint(),
        CharLayoutSyncConstraint(),
        SurfaceBackfillConstraint(),
        GazeStateRepairConstraint(),
    ):
        constraint.apply(ctx)


def _ensure_hook_quality(script: dict, *, novel_text: str, bank=None,
                         objects_bank=None, forbidden_lines=None,
                         producer_model=None) -> list[str]:
    """钩子质量门（v6.70.1，09-13；09-14 抽出供 BEST-GUARD 恢复路径共用）.

    hook 先过确定性结构补齐，再进 checklist；仍有 issue 才换互补模型重生 1 次。
    正式输出有两条来源（本次 run / BEST-GUARD 恢复的快照+补修复），门必须对
    两者同等生效——09-14 实测：恢复路径绕过本门，旧快照的病句/无主刀尖经
    非幂等补修复污染（刀尖句翻倍 + 对象库碎片）直接落盘。重生后补跑检查 7
    （FrameSpec 出镜覆盖）——重生钩子的 spec 同样可能漏人。返回残余 issues
    （空 = 过门）。
    """
    try:
        _prepare_hook_for_qc(script)
    except Exception as _prep_err:
        _mrt._logger.warn(f"[WARN] 钩子质量门前结构补齐失败（继续检查）: {_prep_err}")
    # v6.84（二次审核发现 5）：**先把钩子的时长/时间轴派生出来，再收集 issue**。
    #
    # 原顺序是「收集 issue → …」，而钩子质量门跑在 `_compute_timecodes` **之前**
    # ——`duration_sec` 还没派生，`final_time_invariant_issues()` 里 `dur<=0` 会
    # 整段跳过 KF 检查。本次实测钩子首帧 `t_sec=0.0` 就是这样漏过质量门的
    # （`hook.initial_issues=0` / `hook.final_issues=0`），直到落盘前的最终门才
    # 报出来，那时已经没有 regen 机会。现在门内先做与 `_compute_timecodes`
    # **同一份**时长推导，再用**同一套**最终不变量检查。
    try:
        from step01_script_gen.timecode import sync_hook_timeline
        sync_hook_timeline(script)
    except Exception as _sync_err:
        _mrt._logger.warn(f"[WARN] 钩子质量门时长派生失败（继续检查）: {_sync_err}")
    issues = _msc._collect_hook_issues(script)
    _mrt._metrics_set("hook.initial_issues", len(issues))
    if not issues:
        _mrt._metrics_set("hook.final_issues", 0)
        return []
    _mrt._logger.warn(
        f"[HOOK-QC] 钩子检出 {len(issues)} 条 issue，互补模型重生："
        + " | ".join(issues[:3]))
    _mrt._console(f"  [钩子校验] 检出 {len(issues)} 条 issue → 互补模型重生钩子…")
    _mrt._metrics_inc("hook.regenerations")
    _hook_regen = gen_hook_script(
        novel_text, bank=bank, objects_bank=objects_bank,
        forbidden_lines=forbidden_lines,
        model=_mrg._complementary_repair_model(producer_model),
    )
    if not _hook_regen:
        _mrt._logger.warn("[HOOK-QC] ⚠️ 钩子重生失败，带 issue 落盘（人工过目）：\n  - "
                     + "\n  - ".join(issues))
        _mrt._console("  [钩子校验] ⚠️ 重生失败，钩子带 issue 落盘")
        return issues
    script["hook"] = _hook_regen
    try:
        from step01_script_gen.validation import validate_hook
        _prepare_hook_for_qc(script)
        validate_hook(script["hook"], script.get("characters", []))
    except Exception as _e:
        _mrt._logger.warn(f"[WARN] 重生钩子修复/校验异常（不阻断）: {_e}")
    from step01_script_gen.timecode import sync_hook_timeline as _sync_hk
    try:
        _sync_hk(script)
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:_ensure_hook_quality", _degrade_exc)
    remaining = _msc._collect_hook_issues(script)
    _mrt._metrics_set("hook.final_issues", len(remaining))
    # v6.84（09-21 重跑发现）：钩子时长可能在门内被改（`sync_hook_timeline` 按
    # motion/台词重算，或换成了重生版钩子）。**必须重建时间轴**，否则 hook 的
    # `duration_sec` 与 `timeline[0].end` 不一致 → `timeline_consistency` BLOCK
    # （实测：timeline 写的是 4.0s，钩子被重算成 3.5s，total 78.14 vs sum 74.14）。
    try:
        _mrt.write_timeline_from_durations(script)
    except Exception as _tl_e:
        _mrt._logger.warn(f"[WARN] 钩子质量门后重建时间轴失败: {_tl_e}")
    if remaining:
        _mrt._logger.warn("[HOOK-QC] ⚠️ 重生后钩子仍有 issue（保留落盘，人工过目）：\n  - "
                     + "\n  - ".join(remaining))
        _mrt._console("  [钩子校验] ⚠️ 重生后仍有 issue，已按原样落盘（见日志）")
    else:
        _mrt._logger.info("[OK] 钩子后置校验通过（互补模型重生版）")
        _mrt._console("  [钩子校验] 重生钩子通过校验")
    return remaining


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
        _cand = _mrt.parse_response(raw, novel_text=novel_text)

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


# 「已知语义欠账」台账文件名（v6.115，审核）
_BLOCKED_LEDGER_NAME = "_blocked_revisions.json"


def _blocked_ledger_path():
    return _mrt.OUTPUT_FILE.parent / "snapshots" / _BLOCKED_LEDGER_NAME


def _revision_aliases_or_empty(script) -> dict:
    """`{版本号: revision}`——一份稿件在**各版 revision 算法**下的身份（失败 → 空表）.

    2026-09-30 复核第 4 轮 P1：换哈希算法不能让未解决的语义欠账失去匹配。
    第 5 轮 P1-2 补：身份**按版本**使用（判同/欠账都不许把各版本混成一个等价关系），
    所以这里返回的是版本字典而不是扁平列表。计算失败只降级为「少记兼容身份」，
    **绝不**因此打断 BLOCK / 选稿路径。
    """
    try:
        from shared.quality_gate import script_revision_aliases
        return {int(k): str(v) for k, v in dict(script_revision_aliases(script)).items()}
    except Exception as exc:
        _mrt._logger.warn(f"[BEST-GUARD] revision 别名计算失败（台账少记兼容身份）: {exc}")
        return {}


def _current_criteria() -> dict:
    """当前判据指纹（规则源 + 提示源）——台账按它判断「欠账是否仍然成立」."""
    return {"rules": str(_mrt.RULE_SOURCE_FINGERPRINT or ""),
            "prompts": str(_mrt.PROMPT_SOURCE_FINGERPRINT or "")}


def _load_blocked_ledger() -> dict:
    """台账原样读出（读坏/缺失 → 空 dict，不阻断选稿）."""
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_blocked_revisions() -> dict:
    """**在现行判据下仍然欠账**的身份，按**登记版本**分组：`{版本号: {revision}}`.

    （旧签名是 `set`；改按版本分组的原因见 `shared.quality_gate.script_revision_aliases`
    ——各版本身份不能混成一个等价关系。）

    2026-09-27（用户裁决 4）：台账条目带上登记时的判据指纹。判据/提示变了
    （`rules` / `prompts` 指纹不同）→ 那条欠账是**旧判据下**的结论，不再据此
    排除该稿：判据本身可能已经修掉（本轮 `frame_presence_consistency` 的时态误判、
    缺段重复计量都是这一类）。指纹不符的条目在选稿日志里显式列出，不静默丢弃。

    没有指纹的老条目（本改动之前登记的）按**仍然欠账**处理——保守方向：
    宁可少选一份旧稿，不可凭无法核对的旧结论放它回来。
    """
    data = _load_blocked_ledger()
    revs = [str(x) for x in (data.get("revisions") or [])]
    crit = data.get("criteria")
    cur = _current_criteria()
    # 2026-09-30 复核第 5 轮 P1-2：每条欠账记**登记时的算法版本**，选稿按版本比对。
    # 复核第 6 轮更正：**「旧记录只可能是 v1」这个假设要撤回**——上一版代码已经写出过
    # `"revision_version": 2` 但没有逐条 `entry_versions` 的台账（值本身就是 v2 算的），
    # 一律当 v1 会让同一份稿件的旧 v2 欠账**查不到**（离线已复现）。三级推断：
    #   ① 逐条 `entry_versions[rev]` → 用它（现行写法，唯一权威）；
    #   ② 顶层 `revision_version` + 无逐条映射（单版本台账）→ **按该版本解释**；这类
    #      台账无法逐条区分 v1/v2，故同时按 v1 登记（欠账宁可多排除一稿，不因迁移丢失，
    #      与台账既有的保守口径一致）；
    #   ③ 两者都没有（真·旧台账）→ v1。
    _ver_raw = (data.get("entry_versions")
                if isinstance(data.get("entry_versions"), dict) else {})
    _alias_raw = (data.get("alias") if isinstance(data.get("alias"), dict) else {})
    _ledger_ver = 0
    try:
        _ledger_ver = int(data.get("revision_version") or 0)
    except (TypeError, ValueError):
        _ledger_ver = 0

    def _versions_of(rev: str) -> set:
        _r = str(rev)
        if _r in _ver_raw:                       # ① 逐条权威
            try:
                return {int(_ver_raw[_r])}
            except (TypeError, ValueError):
                return {1}
        if _ledger_ver >= 2:                     # ② 单版本台账（旧 v2 欠账）
            return {1, _ledger_ver} if _r not in _alias_raw else {_ledger_ver}
        return {1}                               # ③ 真·旧台账

    def _put(dest: dict, rev: str) -> None:
        for _v in _versions_of(rev):
            dest.setdefault(_v, set()).add(str(rev))

    if not isinstance(crit, dict):
        out: dict = {}
        for _r in revs:
            _put(out, _r)
        return out
    _issues = data.get("issues") if isinstance(data.get("issues"), dict) else {}
    out, stale = {}, []
    _partly: list = []
    for rev in revs:
        _rec = crit.get(rev)
        _iss = _issues.get(rev)
        if isinstance(_iss, list) and _iss:
            # v6.149：**逐条**判——只把「所依赖判据已变」的那些条目作废，
            # 其余仍然排除该稿（旧口径是任何指纹一动就整批放行）。
            _force = [it for it in _iss
                      if isinstance(it, dict) and _debt_issue_in_force(it)]
            if _force:
                _put(out, rev)
                if len(_force) < len(_iss):
                    _partly.append((rev, len(_iss) - len(_force)))
                continue
            stale.append(rev)
            continue
        # 老条目（无逐条记录）：退回整条指纹口径
        if isinstance(_rec, dict) and (_rec.get("rules"), _rec.get("prompts")) != (
                cur["rules"], cur["prompts"]):
            stale.append(rev)
            continue
        _put(out, rev)
    if _partly:
        _mrt._logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿**部分**问题所依赖的判据已变"
            "（按具体问题计入，其余仍排除）：%s"
            % (len(_partly), ["%s×%d" % (r[:12], n) for r, n in _partly[:6]]))
    if stale:
        _mrt._logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿是在**旧判据**下登记的（规则/提示指纹已变）"
            "——本轮不据此排除，按现行判据重新评估：%s"
            % (len(stale), [r[:12] for r in stale[:6]]))
    return out


def _snapshot_cross_field_blocking(script: dict):
    """快照在**现行确定性判据**下的跨字段 blocking（v6.149 候选资格初筛用）.

    与提交点同源（`cross_field_issues`），而不是质量门的风险计数——实测
    2026-09-29：快照 `script_result_20260929_053150.json` 以「风险1」入选，一进
    提交点就带 `result_action_support` 被判 blocking，反复被选中、反复 BLOCK。
    读坏/异常 → 空表（不阻断选稿）。
    """
    from step01_script_gen.cross_field_checks import cross_field_issues
    try:
        return [i for i in (cross_field_issues(script)[0] or [])
                if str(getattr(i, "severity", "") or "")
                in ("hard", "visual_risk", "blocking")]
    except Exception as exc:
        # v6.150（复核发现 4）：**校验器异常 ≠ 没有问题**。旧实现返回空表，与「无
        # blocking」不可区分——校验器一旦坏掉，所有快照都会「干净」地进候选池。
        # 返回 None 表示**未能校验**，调用方据此让该快照本次失去候选资格。
        _mrt._logger.warn("[BEST-GUARD] 快照跨字段校验器异常（本次不具候选资格）: %s" % exc)
        return None


def _debt_issue_records(blocking) -> list:
    """把 BLOCK 清单转成**逐条欠账记录**（v6.149，复核根因 3）.

    每条记 `code/shot/field` + **该条所依赖的判据指纹**。判据分流：
      · `model_audit_*` → **审计提示版本** `audit_prompt_version()`；
      · 其余（确定性）→ **规则源指纹** `RULE_SOURCE_FINGERPRINT`。

    为什么必须逐条：旧口径只在整条 entry 上记「规则源 + 提示源」两个**全局**指纹，
    任何一处提示改动都会让**整批**欠账（实测 59 份）一起恢复候选资格，「改无关提示
    不能自动洗掉欠账」这条就守不住。分流之后，无关提示改动不再移动
    `audit_prompt_version()`，审计类欠账继续排除。
    """
    from step01_script_gen.audit import audit_prompt_version
    out: list = []
    for b in (blocking or []):
        if not isinstance(b, dict):
            continue
        code = str(b.get("code") or "").strip()
        if not code:
            continue
        if code.startswith("model_audit_"):
            kind, fp = "audit", str(audit_prompt_version() or "")
        else:
            kind, fp = "rules", str(_mrt.RULE_SOURCE_FINGERPRINT or "")
        out.append({"code": code,
                    "shot": str(b.get("shot_id") if b.get("shot_id") is not None else ""),
                    "field": str(b.get("field_path") or ""),
                    "crit_kind": kind, "crit_fp": fp})
    return out


def _debt_issue_in_force(rec: dict) -> bool:
    """这条欠账的判据**未变** → 仍然成立（现行判据下还会报同一条）."""
    from step01_script_gen.audit import audit_prompt_version
    kind = str(rec.get("crit_kind") or "")
    if kind == "audit":
        return str(rec.get("crit_fp") or "") == str(audit_prompt_version() or "")
    if kind == "rules":
        return str(rec.get("crit_fp") or "") == str(_mrt.RULE_SOURCE_FINGERPRINT or "")
    return True          # 认不出判据 → 保守：按仍然欠账


def _record_blocked_revision(revision: str, issues=None, versions=None) -> None:
    """把 BLOCK 的 revision 记进台账（v6.115，审核）.

    为什么需要：BEST-GUARD 按**确定性**分数与风险排序，它**不知道**某份历史快照
    还欠哪些**语义**问题——实测 run 17/18 反复选回 21:55 的旧快照，而那份保留着
    Rule-7 造成的旧文本（审计每轮重报）。台账让选稿能避开已知欠账的旧稿。

    `versions`：`{算法版本号: revision}`。台账只记**主 key 的登记版本**
    （`entry_versions`），选稿侧据此按版本比对——旧版本身份只用于匹配明确属于那个
    版本的旧记录（2026-09-30 复核第 4/5 轮）。

    v6.142（09-26 P1-4）：同时记 `debt[revision]` = **该稿卷入过几次 BLOCK 的
    run**（每次登记 +1）。台账原本只是个集合——「全部快照都欠账」时整表放开、
    改按分数/风险选，于是**欠账最重的那份照样被反复选中**（P1-4 实损形态）。
    有了次数，饱和放开时就能「优先选欠账最少的一份」，且被选中者下一轮计数
    +1、自然降位——棘轮不再失效。

    2026-09-27（用户裁决 4）：同时记 `criteria[revision]` = 登记时的**判据指纹**
    （规则源 + 提示源）。判据/提示改过之后，「已知 BLOCK」不再等于「现在还会
    BLOCK」（本轮就有两条：`frame_presence_consistency` 的时态误判、缺段重复计量）
    ——没有这个字段，修好的判据也换不回那份稿的重评机会。
    """
    if not revision:
        return
    try:
        path = _blocked_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                _data = json.load(f) or {}
            cur = list(_data.get("revisions") or [])
            # 容错口径与 `_load_blocked_debt` 同一处（`_coerce_debt_map`）——
            # 一条脏计数**不得**让整张台账写不进去（那等于静默废掉护栏）
            debt = _coerce_debt_map(_data.get("debt"), cur)
            _raw_crit = _data.get("criteria")
            if isinstance(_raw_crit, dict):
                crit = {str(k): v for k, v in _raw_crit.items() if isinstance(v, dict)}
            _raw_iss = _data.get("issues")
            if isinstance(_raw_iss, dict):
                iss = {str(k): v for k, v in _raw_iss.items() if isinstance(v, list)}
            # 登记版本表（2026-09-30 复核第 5 轮 P1-2）：`{主 revision: 算法版本号}`
            _raw_ver = _data.get("entry_versions")
            if isinstance(_raw_ver, dict):
                _tmp: dict = {}
                for _k, _v in _raw_ver.items():
                    try:
                        _tmp[str(_k)] = int(_v)
                    except (TypeError, ValueError):
                        continue
                vers = _tmp
        except Exception:
            cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        cur = [str(x) for x in cur if str(x) != revision]
        cur.append(str(revision))
        cur = cur[-64:]
        debt = {k: v for k, v in debt.items() if k in set(cur)}
        debt[str(revision)] = int(debt.get(str(revision), 0)) + 1
        # 2026-09-30 复核第 4/5 轮：记下**这条身份是哪个版本的算法产生的**。换算法
        # 后选稿按版本比对才能命中它（旧欠账不消失），同时不会让「同一段文字、契约
        # 不同」的另一份稿被它连坐。`debt` / `criteria` / `issues` 仍只按主 revision
        # 记一次——版本号不是一次新的欠账，不重复计数。
        vers = {k: v for k, v in vers.items() if k in set(cur)}
        from shared.quality_gate import SCRIPT_REVISION_VERSION as _rv_ver
        vers[str(revision)] = int(_rv_ver)
        # 2026-09-27（用户裁决 4）：记下**登记时的判据指纹**——下次读台账才知道
        # 这条欠账是不是在现行判据下得出的（判据变了要重新评估，不能一直排除）。
        crit = {k: v for k, v in crit.items() if k in set(cur)}
        crit[str(revision)] = _current_criteria()
        # v6.149：逐条欠账（含各自依赖的判据指纹）。**只补不覆盖**——同一条 revision
        # 多次 BLOCK 时，把新出现的条目并进去，已排除过的问题不会因覆盖而丢。
        if issues:
            _old_iss = iss.get(str(revision)) or []
            _merged, _seen = [], set()
            # v6.150（复核发现 2）：**新的在前**——同一 `(code, shot, field)` 在现行
            # 判据下再次 BLOCK 时，必须由新记录**覆盖**旧版。旧实现把旧条目排在前面，
            # 于是新记录被去重跳过、台账留着**已过期**的判据指纹，该稿下次又被判「可重评」
            # 放回候选池（正是要治的那条）。`[:40]` 截断同理——留最新的 40 条。
            for _it in list(issues) + list(_old_iss):
                if not isinstance(_it, dict):
                    continue
                _k = (str(_it.get("code")), str(_it.get("shot")), str(_it.get("field")))
                if _k in _seen:
                    continue
                _seen.add(_k)
                _merged.append(_it)
            iss[str(revision)] = _merged[:40]
        iss = {k: v for k, v in iss.items() if k in set(cur)}
        from shared.json_io import atomic_dump_json
        atomic_dump_json(path, {"revisions": cur, "debt": debt, "criteria": crit,
                                "issues": iss, "entry_versions": vers,
                                "revision_version": int(_rv_ver)},
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[BEST-GUARD] 欠账台账写入失败（不影响结论）: {exc}")


def _coerce_debt_map(raw, revisions) -> dict:
    """把台账里的 `debt` 段规整成 `{revision: episodes}`（**读写两侧共用**）.

    容错口径只有一处，读写必须一致——否则「读出来是 1 轮、再登记一次却变回 1」
    这种自相矛盾会让棘轮计数无声漂移：
      - 段缺失 / 不是 dict（老台账）→ 表内每条记 **1 轮**（至少欠过一次；记 0 会
        让老条目在饱和放开时无条件胜出，正是 P1-4 要治的方向）；
      - 单条脏值 → 只按「1 轮」计它自己，**不牵连同表其余条目**；
      - 不在 `revisions` 表内的键丢弃（表只留最近 64 条，计数跟着表走）。
    """
    _revs = {str(x) for x in (revisions or [])}
    if not isinstance(raw, dict):
        return {r: 1 for r in _revs}
    out: dict = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            out[str(k)] = 1
    return {k: v for k, v in out.items() if k in _revs}


def _load_blocked_debt() -> dict:
    """台账里的「欠账轮次」：`{revision: episodes}`（读坏/缺失返回空 dict）.

    2026-09-27（用户裁决 4）：**不再参与选稿排序**（候选池里已没有欠账稿）。
    保留读取口径是为了**可见性**：选稿日志会打出「欠账最重的几份」，
    以及台账写侧的容错读数（`_coerce_debt_map` 读写同源）。

    老台账（只有 `revisions`、没有 `debt`）→ 一律按 1 轮计：它们至少欠过一次，
    但不知道欠了几次；按 1 计不会让任何一份凭空占优（不写 = 0 会让老条目
    无条件胜出，正是要治的那个方向）。
    """
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return _coerce_debt_map(data.get("debt"), data.get("revisions"))
    except Exception:
        return {}


def _snapshot_new_candidate(script: dict) -> str:
    """给「BEST-GUARD 选择之前的新生成候选」留一份独立诊断快照，返回路径（失败 ""）.

    v6.123（用户口径）：此前的做法是——若 BEST-GUARD 选中历史稿，新稿只在内存里存在
    到 `script = _bg_selected` 那一刻；失败快照写的是**被选稿**，成功快照写的也是
    被选稿。于是「本次生成的质量」在事后没有任何落盘证据，而日志却宣称
    「本次 N 分版本保留在 snapshots/」。

    文件名用 `candidate_pre_bestguard_<ts>.json`（**不带** `script_result_` 前缀）：
    BEST-GUARD 的候选扫描就是按 `script_result_*.json` 找文件的，这份未定稿
    （无 `chapter_outline`、未过 hook QC）不得进入候选池。轮转在
    `_snapshot_run_artifacts()` 里按同一上限处理。
    """
    try:
        import time as _time

        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json as _adj
        _path = snap_dir / ("candidate_pre_bestguard_%s.json"
                            % _time.strftime("%Y%m%d_%H%M%S"))
        _adj(_path, script, ensure_ascii=False, indent=2)
        return str(_path)
    except Exception as _snap_e:
        try:
            _mrt._logger.warn(f"[BEST-GUARD] 新候选快照写入失败（不影响主流程）: {_snap_e}")
        except Exception:
            pass
        return ""


def _non_script_snapshot_reason(obj) -> str:
    """该 JSON 是否**不是分镜稿** → 返回跳过原因（空串 = 是稿件）.

    v6.124.2（用户 2026-09-25 决定）：BEST-GUARD 的候选必须先**验明正身**再评分。

    为什么必须挡在评分**之前**：`_candidate_quality_summary()` 在一份没有分镜的
    JSON 上查不出任何 issue，于是 `structural_ok=True`、`integrity_ok=True`、
    `blocking_risk_count=0`，`_quality_rank()` 给出 `(0, 0, 0)`——**优于任何真实
    稿件**（真稿至少带着 blocking 风险）。于是「**没有数据**」被读成「最干净」，
    这正是准则 7 的失败形态，而且落在选稿这个最关键的位置上。

        实测（2026-09-25 19:14）：审计诊断产物
        `snapshots/script_result_20260925_173311.audit.compare.json`（我的 (c)
        实验输出，非分镜稿）被选为终稿 → 公共管线审计它 → 报
        `empty_storyboard` + `missing_end_hook`（无字段可授权）→ 提交点 BLOCK。

    **刻意只校验最小结构**（用户口径：「别把旧版合法快照误挡掉」）：只要求
    顶层有非空的 `shots` 数组、元素是对象、且至少一个对象带 `index`/`prompt`。
    不校验 `timeline`/`characters` 等——那会把最小的合法稿件与旧版快照一起误挡，
    而本项目自己的最小稿件夹具就只有 `shots`（见
    `tests/step01_script_gen/test_best_guard.py::_script_with_dialogue`）。
    """
    if not isinstance(obj, dict):
        return "不是 JSON 对象"
    shots = obj.get("shots")
    if not isinstance(shots, list) or not shots:
        return "缺 shots 或 shots 为空数组"
    if not any(isinstance(s, dict) and (s.get("index") is not None or s.get("prompt"))
               for s in shots):
        return "shots 里没有可识别的镜头对象（缺 index/prompt）"
    return ""


def _cross_run_best_guard(script: dict, novel_text: str, *,
                          plan: "dict | None" = None) -> tuple:
    """跨 run 保优护栏（P4.6，2026-09-11）：防止更好的历史版本被差 run 覆盖.

    v6.103（2026-09-23 总计划阶段 4.1）：**它只选择候选，不再落盘**。

    旧实现在「新终稿已落盘」之后还可能把历史快照写回 `script_result.json`，
    并在 finalize **之后**重跑钩子质量门（可能重生 hook）——于是最终落盘的 hook
    从未被 final semantic audit 覆盖过；同时正式路径上有**两个**写入者，提交时序
    无法收敛成一个点。

    现在它返回 `(被选中的脚本 | None, 元数据)`：调用方拿到候选后仍走
    `_commit_step01_outputs()` 这一条公共管线（hook QC → finalize → revision →
    quality → final audit → 提交）。历史快照必须通过**当前版本**的质量初筛才有
    资格成为候选，但「能不能落盘」由公共管线的最终审计决定。

    Returns:
        `(selected_script, meta)`；没有更优快照时 `(None, meta)`。
    """
    meta: dict = {"selected": False, "reason": "", "new_score": None,
                  "best_score": None, "best_path": None}
    try:
        import os as _os
        from step01_script_gen.plan_feasibility import check_script_dialogue_integrity
        from shared.quality_gate import SCRIPT_REVISION_VERSION

        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        if not snap_dir.is_dir():
            return None, meta
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        if not snaps:
            return None, meta

        scan_n = max(1, int(_os.getenv("BEST_GUARD_SCAN_N", "8")))
        margin = float(_os.getenv("BEST_GUARD_MARGIN", "3"))
        keep_best = _os.getenv("KEEP_BEST_SNAPSHOT", "1") == "1"
        # v6.123（用户口径：候选留痕）：一旦选中历史稿，本次新生成的稿件在内存里被
        # 丢弃（`script = _bg_selected`），而**失败路径的快照保存的是被选稿**——于是
        # 「本次新稿质量如何」事后无从评价。旧文案还宣称「本次 N 分版本保留在
        # snapshots/」，实际上这一步什么都没写（日志与事实不符）。
        # 在**选择之前**真写一份独立诊断快照，并把真实路径带进 meta 供日志引用。
        # 文件名**不用** `script_result_` 前缀：那是候选扫描的口径，而这份是
        # **未定稿**（没有 chapter_outline、没过 hook QC），不得进入候选池。
        if keep_best:
            meta["new_candidate_snapshot"] = _snapshot_new_candidate(script)

        new_score = _msc._score_script(script, novel_text)["total"]
        new_quality = _msc._candidate_quality_summary(script, novel_text)
        new_rank = _msc._quality_rank(new_score, new_quality)
        meta.update({"new_score": new_score,
                     "new_risk": new_quality.get("blocking_risk_count")})

        # 本次稿件的语义 revision——用它排除「就是当前这一版」的快照。
        # 旧实现靠「snaps[:-1] 排除最新一份」来排除本次快照；那隐含假设「最新
        # 快照刚由本次写入」。选择点提前到落盘**之前**后该假设不再成立（最新快照
        # 是上一次 run 的，是合法候选），所以改成按 revision 排除。
        # 2026-09-30 复核第 4/5 轮：「是不是同一份稿」只比**现行版本**的完整身份；
        # 「是不是已知欠账」按**登记版本**逐版比对（旧版本身份只匹配明确属于那个
        # 版本的旧记录）。两处都不能把各版本混成一个等价关系——「同一段文字、契约
        # 不同」的两份稿 v2 不同而 v1 相同，混用会让好稿被旧欠账连坐。
        _cur_aliases = _revision_aliases_or_empty(script)
        if not _cur_aliases:
            # 「算不出身份」既不能证明快照不是当前这一版，也不能证明它不欠账——
            # 显式不选稿（准则 7），不把无法核验的东西放进候选池。
            meta["reason"] = "revision_uncomputable"
            _mrt._logger.warn("[BEST-GUARD] 本次终稿 revision 别名无法计算 → 本轮不做跨 run 选稿")
            return None, meta
        _blocked_by_ver = _load_blocked_revisions()
        _blocked_names: list = []

        # v6.124.2：非分镜稿**不进候选池**（见 `_non_script_snapshot_reason`）
        _invalid: list = []
        # v6.135（审核约束①）：历史快照也按**本轮计划**执行台词完整性门——快照生成时
        # 的计划与本轮不同，它没保住本轮选材 = 进提交门后必然死锁（09-26 实损链）。
        # 门**不参与**下方"全欠账放宽"（那是欠账台账的例外，不是完整性门的）。
        _dlg_broken_snaps: list = []
        _stale_plan_snaps: list = []

        def _plan_snapshot_reason(old: dict) -> str:
            """快照的 `plan_refs` 是不是**对本轮计划**作的声明（2026-09-27 复核 P0-2）.

            带身份且与本轮计划不符 → 排除（它的编号指的不是同一条节拍；只查编号范围
            会静默放行——`insert_after`/`split` 会重排 B 编号）。**无身份**的老快照按
            既定兼容政策继续参与排名，但**不算**「通过了本轮事件契约」（提交门的语义
            审计仍是它的兜底）。
            """
            if plan is None:
                return ""
            # 就地 import：本闭包所在的 BEST-GUARD 函数与候选门的 import 块**不在同一
            # 作用域**（实跑被 `test_best_guard_excludes_broken_snapshot...` 抓到：
            # NameError 被外围的「护栏失败不影响主流程」吞掉，身份门**静默失效**）。
            from step01_script_gen.plan_feasibility import plan_identity_mismatch
            return plan_identity_mismatch(old, plan)

        def _dlg_snapshot_reason(old: dict) -> str:
            if plan is None:
                return ""
            try:
                _ok, _broken = check_script_dialogue_integrity(old, plan)
            except Exception as _e:
                # v6.135 四审 P1：门自身异常 → 该快照**无法证明台词完整** → 排除出
                # 候选池（fail-closed：宁可不选，不可选中无法验证的快照），原因留痕
                return "gate_error: %s" % _e
            if _ok:
                return ""
            return "%d 句破损，例：%s（%s）" % (
                len(_broken), str((_broken or [{}])[0].get("line") or "")[:30],
                (_broken or [{}])[0].get("reason"))

        candidates = []
        for sp in snaps:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    old = json.load(f)
            except Exception as _e:
                _invalid.append(f"{sp.name}（JSON 解析失败：{_e}）")
                continue
            _why = _non_script_snapshot_reason(old)
            if _why:
                _invalid.append(f"{sp.name}（{_why}）")
                continue
            if not _mrt.HOOK_ENABLED:
                old.pop("hook", None)
            _old_aliases = _revision_aliases_or_empty(old)
            if not _old_aliases:
                # 同上：快照身份不可核 → 本次不具候选资格，原因留痕（而不是当作
                # 「不是当前这一版、也不欠账」默默放行）。
                _invalid.append(f"{sp.name}（revision 别名无法计算：身份不可核）")
                continue
            # 就是当前这一版：只比**现行版本**的完整身份（复核第 5 轮 P1-2）。
            if _old_aliases.get(SCRIPT_REVISION_VERSION) == _cur_aliases.get(
                    SCRIPT_REVISION_VERSION):
                continue
            # v6.115（审核）：跳过**已知语义欠账**的旧稿——它们确定性分数可能很高
            # （所以此前总被选中），却带着审计反复报的旧问题。
            # 2026-09-30 第 5 轮：**逐版本**比对——台账条目只按其登记版本的身份命中
            # （v1 记录只认 v1 身份，v2 记录只认 v2 身份），跨版本不算等价。
            if any(_old_aliases.get(_v) in (_blocked_by_ver.get(_v) or ())
                   for _v in _old_aliases):
                _blocked_names.append(sp.name)
                continue
            _dlg_why = _dlg_snapshot_reason(old)
            if _dlg_why:
                _dlg_broken_snaps.append(f"{sp.name}（{_dlg_why}）")
                continue
            _plan_why = _plan_snapshot_reason(old)
            if _plan_why:
                _stale_plan_snaps.append(f"{sp.name}（{_plan_why}）")
                continue
            candidates.append((sp, old))
        # 诊断清单去重（`invalid` / 台词破损 / 计划不符各自只扫一遍，但同一份快照
        # 可能同时命中多条）——准则 7 要的是**准确**的可见性，不是更大的数字。
        _dlg_broken_snaps = list(dict.fromkeys(_dlg_broken_snaps))
        _stale_plan_snaps = list(dict.fromkeys(_stale_plan_snaps))
        if _dlg_broken_snaps:
            # 显式记录（准则 7）：哪几份快照因台词破损被排除，为什么
            _mrt._logger.warn(
                "[BEST-GUARD] 台词完整性门排除 %d 份历史快照（未过本轮计划的选材门，"
                "不参与评分）：%s"
                % (len(_dlg_broken_snaps), _dlg_broken_snaps[:6])
            )
            meta["dialogue_broken_snapshots"] = _dlg_broken_snaps
        if _stale_plan_snaps:
            # 显式记录（准则 7 / 复核 P0-2）：哪几份快照因**计划身份不符**被排除。
            # 这些快照的 plan_refs 是对另一版计划作的声明，编号即使仍在范围内也指向
            # 别的节拍（`insert_after`/`split` 会重排 B 编号）。
            _mrt._logger.warn(
                "[BEST-GUARD] 计划身份不符，排除 %d 份历史快照（它们的 plan_refs 不是"
                "对本轮计划作的声明，不参与评分）：%s"
                % (len(_stale_plan_snaps), _stale_plan_snaps[:6])
            )
            meta["stale_plan_snapshots"] = _stale_plan_snaps
        if _invalid:
            # 显式记录（准则 7）：跳过的原因必须能看见，否则"为什么没选历史稿"无从归因
            _mrt._logger.warn(
                "[BEST-GUARD] 跳过 %d 份**非分镜稿**的 JSON（不评分、不参与选稿；"
                "常见原因是审计/诊断产物落进了 snapshots/ 候选目录）：%s"
                % (len(_invalid), _invalid[:6])
            )
            meta["invalid_candidates"] = _invalid
        # ------------------------------------------------------------------
        # 2026-09-27（用户裁决 4）：**欠账稿不再放回候选池**。
        #
        # 旧行为是「全都有欠账 → 放开限制，按欠账轮次升序选一份」。实测（08:11
        # run）：全部 20 份快照都在台账内 → 选中上一轮刚 BLOCK 的 `072416`
        # （风险 2 只是**确定性**风险，欠的语义问题不在这个数里）→ 重跑同一套审计、
        # 同样的 4 条 blocking、`resolved=0` → 再 BLOCK，并把它自己的新 revision
        # 也写进台账。每轮原地空转：既拿不到新稿的检验结果，又把「确定性风险低」
        # 读成了「已能提交」。
        #
        # 现在的语义：**已 BLOCK 且判据/修复契约未变**的稿不再作为「最佳」稿被重复
        # 修复；没有合格历史候选时如实返回 None——本轮新稿照常进提交门（门仍可
        # BLOCK，正式产物仍受保护：不过门只写 BLOCK manifest）。
        #
        # 判据变过的欠账稿不在此列：`_load_blocked_revisions()` 只把**指纹相符**的
        # 条目当欠账（旧结论可能正是被修掉的那条判据得出的）。
        if not candidates and _blocked_names:
            _debt_map = _load_blocked_debt()
            _owed = sorted(
                ((int(_debt_map.get(r, 1)), r)
                 for _revs in _blocked_by_ver.values() for r in _revs),
                reverse=True)[:5]
            _mrt._logger.warn(
                "[BEST-GUARD] 全部历史快照都在欠账台账内（%d 份，判据指纹未变）"
                "——本轮**不选历史稿**，让新稿直接进提交门检验；"
                "欠账最重的几份：%s（正式产物仍受保护）"
                % (len(_blocked_names),
                   "、".join(f"{r[:12]}×{n}" for n, r in _owed)))
            meta["reason"] = "all_snapshots_owed"
            meta["blocked_snapshots"] = _blocked_names
            return None, meta
        candidates = candidates[-scan_n:]

        best_path = None
        best_score = -1.0
        best_quality = None
        best_rank = None
        best_script = None
        _prefilter_rejected: list[str] = []
        for sp, old in candidates:
            try:
                sc = _msc._score_script(old, novel_text)["total"]
                q = _msc._candidate_quality_summary(old, novel_text)
                rank = _msc._quality_rank(sc, q)
            except Exception:
                continue
            # 初筛是候选资格，不是冠军资格。先排除不合格快照再排名，
            # 否则第一名被拒后，同一窗口内的次优合格快照永远得不到机会。
            _new_risk = new_quality.get("blocking_risk_count")
            _old_risk = q.get("blocking_risk_count")
            # v6.149（复核根因 3）：候选资格初筛**用与提交点同源的确定性判据**。
            # 此前初筛只看质量门的风险计数，而**跨字段**检查是另一个来源——实测
            # 2026-09-29：快照 `script_result_20260929_053150.json`（它本身就是同一天
            # 早先被判死的输出）以「风险1」入选，一进提交点就带 `result_action_support`
            # 被判 blocking，于是反复被选中、反复 BLOCK（59 份欠账重评也没拦住它）。
            # 全部快照都不合格时 BEST-GUARD 返回 None → 按既有路径改选新稿进提交门。
            _cf_blocking = _snapshot_cross_field_blocking(old)
            if _cf_blocking is None:
                _prefilter_rejected.append(f"{sp.name}（跨字段校验器异常，未能校验）")
                continue
            if _cf_blocking:
                _prefilter_rejected.append(
                    "%s（跨字段 blocking %d 条，例：%s）"
                    % (sp.name, len(_cf_blocking),
                       str(getattr(_cf_blocking[0], "code", "?"))))
                continue
            if (q.get("quality_checked") is not True
                    or rank[0] > 0 or rank[1] > 0
                    or (_new_risk is not None and _old_risk is not None
                        and int(_old_risk) > int(_new_risk))):
                _prefilter_rejected.append(
                    f"{sp.name}（风险{_old_risk}，结构={q.get('structural_ok')}，"
                    f"完整性={q.get('integrity_ok')}）")
                continue
            # v6.73: compare integrity -> blocking risks -> total, not only
            # the raw story score.  A clean 90 must not lose to a corrupted 100.
            # 2026-09-27（用户裁决 4）：欠账轮次不再参与排序——候选池里已经没有
            # 欠账稿（它们不再被放回），那道次键恒为 0，留着只会误导读者。
            _key = rank
            if best_rank is None or _key < best_rank:
                best_score = sc
                best_quality = q
                best_rank = rank
                best_path = sp
                best_script = old

        if best_path is None:
            if _prefilter_rejected:
                # v6.152（C5）：清单**打全**——此前 `[:6]` 截断，事后无法复核
                # 到底哪几份被拒（run 01 就因此答不出 081241 是否在列）。
                _mrt._logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                             % (len(_prefilter_rejected), _prefilter_rejected))
                meta["prefilter_rejected_snapshots"] = _prefilter_rejected
                meta["reason"] = "snapshot_failed_prefilter"
            return None, meta
        if _prefilter_rejected:
            _mrt._logger.warn("[BEST-GUARD] %d 份快照未通过当前质量初筛：%s"
                         % (len(_prefilter_rejected), _prefilter_rejected))
            meta["prefilter_rejected_snapshots"] = _prefilter_rejected
        meta.update({"best_score": best_score, "best_path": str(best_path),
                     "best_risk": (best_quality or {}).get("blocking_risk_count")})

        _same_risk_tier = (
            best_rank[:3] == new_rank[:3]
        )
        # 09-26 P1：**新稿胜出也要留日志**。此前只有「快照胜出」有两行 WARN，
        # 新稿胜出（同档在 margin 内 / 新稿风险档不更差）静默 return——20:49 run
        # 日志里只能看到 BEST-GUARD 存在，看不出快照为什么失利，稳定版与新版本的
        # 取舍无法复核。这里补一行对称 WARN（含 best/new 分数与风险、tier、margin）。
        def _log_new_kept(_reason: str):
            _mrt._logger.warn(
                "[BEST-GUARD] 保留新终稿（" + _reason + "）：本次 "
                + str(new_score) + " 分/风险"
                + str(new_quality.get("blocking_risk_count"))
                + " vs 快照最优 " + str(best_score) + " 分/风险"
                + str((best_quality or {}).get("blocking_risk_count"))
                + "（" + best_path.name + "，tier "
                + str(tuple(best_rank)) + " vs " + str(tuple(new_rank))
                + "，margin=" + str(margin) + "）"
                + "——快照未胜出，不替换"
            )

        if _same_risk_tier:
            if best_score <= new_score + margin:
                meta["reason"] = "same_tier_within_margin"
                _log_new_kept("同风险档，快照分未超出新稿 + margin")
                return None, meta
        else:
            # Prefer the safer snapshot whenever its integrity/risk tier is
            # strictly better than the new run, regardless of raw score.
            if not (best_rank[:3] < new_rank[:3]):
                meta["reason"] = "new_tier_not_worse"
                _log_new_kept("新稿结构/完整性/风险档不更差")
                return None, meta

        _mrt._logger.warn(
            "[BEST-GUARD] 快照综合排序胜出：新终稿 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "；快照最优 "
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "（" + best_path.name + "）"
        )
        if not keep_best:
            _mrt._logger.warn("[BEST-GUARD] KEEP_BEST_SNAPSHOT=0 → 仅告警，保留新终稿")
            meta["reason"] = "keep_best_disabled"
            return None, meta

        # v6.123：文案必须与事实一致——真写了才说「已留档 <路径>」，没写成要说没写成。
        _new_snap = str(meta.get("new_candidate_snapshot") or "")
        _mrt._logger.warn(
            "[BEST-GUARD] 选中历史快照作为候选（" + str(best_score) + " 分/风险"
            + str(best_quality.get("blocking_risk_count")) + "）；本次 "
            + str(new_score) + " 分版本"
            + ("已留档 " + _new_snap if _new_snap
               else "**快照写入失败，未留档**（见上方 WARN）")
            + "。候选仍走公共提交管线（hook QC → finalize → quality → final audit）"
        )
        _mrt._console(
            "  [BEST-GUARD] 本次 " + str(new_score) + " 分/风险"
            + str(new_quality.get("blocking_risk_count")) + "，快照综合排序更优："
            + str(best_score) + " 分/风险"
            + str((best_quality or {}).get("blocking_risk_count"))
            + "，已**选中**历史最优版为候选（仍须通过公共提交与最终审计）"
        )
        meta["selected"] = True
        meta["reason"] = "snapshot_strictly_better"
        # v6.129（用户 2026-09-25 根因）：**在选稿边界**把快照**自身**的 revision
        # 一并带出去。它在选稿时成立，但主流程随后会用**本轮大纲**覆盖
        # `script["chapter_outline"]`，而 `chapter_outline` 参与 revision 哈希
        # （`shared/quality_gate._stable_payload`）→ 分镜一字未改，进入提交点时
        # revision 已经变成另一个值（实测 f434e509→380da4c1→0fe1f2b2）。
        # 台账若只记后两者，下次 guard 仍拿快照自身的 revision 查台账 → 漏拦、
        # 同一份欠账稿被反复选中（这就是旧稿反复入选的完整触发链）。
        meta["selected_path"] = str(best_path)
        # 版本字典一并带出去：提交点 BLOCK 时台账要把**主 key 的登记版本**记上，
        # 否则换算法之后这份欠账又查不到了（2026-09-30 复核第 4/5 轮）。
        _sel_aliases = _revision_aliases_or_empty(best_script)
        meta["selected_revision"] = str(_sel_aliases.get(SCRIPT_REVISION_VERSION) or "")
        meta["selected_revision_aliases"] = _sel_aliases
        if not meta["selected_revision"]:
            _mrt._logger.warn("[BEST-GUARD] 快照自身 revision 计算失败"
                         "（欠账台账可能漏一条）")
        return best_script, meta
    except Exception as _bg_e:
        try:
            _mrt._logger.warn(f"[BEST-GUARD] 护栏执行失败（不影响主流程）: {_bg_e}")
        except Exception:
            pass
        meta["reason"] = f"error: {_bg_e}"
        return None, meta


def _fatal_summary(exc, tb: str) -> str:
    """失败摘要的**单行完整**形态（复核 2026-10-04）.

    `tb.strip().splitlines()[-1]` 在异常消息**含换行**时只拿到最后**一行片断**——计划层的
    停止原因恰好是多行的（问题清单逐条列全），于是控制台上只剩半句：

        [FATAL] 2) F8 声明由 B8 的**画面**承载，但该节拍标的是「载体：台词2」——一条节拍）——额度已耗尽；这是**响应违约**（模型没

    前半截（"连续 2 次响应被拒收…1) F6 …"）在别的行上，读失败摘要的人看不到。改成从
    **异常对象**取完整消息并压掉换行——与 `shared.error_fmt` 的单行打印约定一致。
    """
    if exc is not None:
        _text = "%s: %s" % (type(exc).__name__, exc)
    else:
        _tb = (tb or "").strip()
        _text = _tb.splitlines()[-1] if _tb else ""
    return " ".join(str(_text).split())


def _presentation_mode_argv(argv=None) -> str:
    """早期解析 `--presentation-mode`（**不用 argparse**：本模块没有顶层 parser，
    且必须能在任何配置/日志求值之前跑完）.

    只认这一个参数；其余 argv 原样忽略（Step01 由上层 runner 传参）。非法值抛
    `ValueError` —— 由 `main()` 的调用方转成退出码 2，**不在生成侧才发现**。
    """
    import sys as _sys
    _av = list(_sys.argv[1:] if argv is None else argv)
    _flag = "--presentation-mode"
    if _flag not in _av:
        return ""
    _i = _av.index(_flag)
    if _i + 1 >= len(_av):
        raise ValueError("--presentation-mode 缺少取值（legacy_audio|screen_hybrid）")
    return _av[_i + 1]


def main() -> None:
    """主流程：读取章节 → 生成脚本 → 生成钩子 → 验证 → 后处理 → 保存.

    控制台静默策略：运行期间所有 print()（校验 WARN、LLM 推理过程等）
    重定向到 step01.log，控制台只显示关键进度和最终摘要。

    **早期 CLI**（主专题 §6.3）：`--presentation-mode legacy_audio|screen_hybrid`，
    **缺省仍是 `legacy_audio`**。在拿锁、建目录、算缓存键**之前**解析并写入运行身份——
    模式进计划/缓存身份（`presentation.mode` + `plan_contract_version`），下游 Step03/04
    从**经提交验证的稿件**读它，不在下游另传开关改稿。非法模式在启动期就退出（码 2），
    不走到生成侧才发现。
    """
    from step01_script_gen.outline import presentation_mode

    _cli_mode = presentation_mode(_presentation_mode_argv())
    globals()["PRESENTATION_MODE"] = _cli_mode
    os.environ["STEP01_PRESENTATION_MODE"] = _cli_mode

    _mrt._logger.separator()
    _mrt._logger.info("  步骤1: 分镜脚本生成 (v6.0)")
    _mrt._logger.info("  新增: 钩子分镜 + 敏感词校验")
    _mrt._logger.separator()

    # v6.70.2：章节级 single-flight 锁——禁止两个 step01 并发写同一章
    # （09-14 实测两个 Python 进程交错写 step01.log/script_result.json）。
    if not _mrt._acquire_step01_lock():
        return

    _mrt._timing_start()  # v6.70.3: 阶段计时起点（拿锁成功 = 本 run 独占计时器）
    # 每次模型调用记一条**带 run_id** 的台账（2026-10-02 复核：metrics.calls 全空、
    # 日志里也没有 run_id，事后无法对账）。落点：章节目录的 step01_calls.jsonl。
    from shared.call_ledger import set_ledger_path
    try:
        set_ledger_path(_mrt.CHAPTER_DIR / "step01_calls.jsonl")
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("step01_script_gen/main.py:main", _degrade_exc)

    try:
        # 无声视频模型告警：VIDEO_MODEL_VOICE=1（对话交给视频模型）但当前模型不生成语音时，
        # 成片会无对白/背景音。Seedance 1.0 系列 / MiniMax v1 Hailio 均无声（08-16 适配）。
        _warn_silent_video_model()

        # 创建输出目录
        _mrt.CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

        # 运行期间 print() 全部重定向到 step01.log，控制台静默
        # 最终摘要用 _console() 写 _REAL_STDOUT（模块级保存的真实 stdout）
        # 同时让 logger 控制台输出静默，避免 logger.print 经重定向回流日志造成双写
        sys.stdout = _mrt._redirect_to_log()
        _mrt._logger.console_silent = True

        # 读取章节
        novel_text = read_chapter(None, chapter=_mrt.NOVEL_CHAPTER)
        # v6.115（审核）：缓存指纹此前只取章节**前 1000 字**——后文改动不会改变
        # 指纹，于是「原文变了但缓存命中」→ 用旧稿当新稿用。指纹必须覆盖全部输入。
        novel_source_text = novel_text

        # 加载角色库（v6.1 新增）
        character_bank = _mrt.CharacterBank()
        character_bank.load()

        # 加载场景物体库（v6.6 新增）
        scene_objects_bank = _mrt.SceneObjectsBank()
        scene_objects_bank.load()

        # v6.43（08-25）：3 个 LLM 预提取并发跑（角色/场景物体/大纲）——
        # 原来按顺序执行 = 3 × LLM 延迟（~16s 总耗时），并发后 = max(3) ≈ 5s，
        # 节省 ~10s。3 个 extractor 各自独立 schema/服务/写入目标，并发安全。
        pre_extracted_outline = None
        # 工作包 §4 D / §7.4：**整次运行唯一的绝对截止时刻**在这里算一次，之后
        # `extract_all_concurrent` → `generate_outline_result` → 修订控制器 → 生成前门
        # 全部用它；任何一层都不许再算 `now + cap`（`_planning_deadline_at` 只在此处调用）。
        _deadline_monotonic = _mrt._planning_deadline_at()
        # 结构化大纲结果（§7.2）：未启用大纲提取时为 None；失败原因从它读，不再丢成 None。
        _outline_result = None
        if _mrt.CHARACTER_EXTRACTION_ENABLED or _mrt.SCENE_OBJECT_EXTRACTION_ENABLED or _mrt.OUTLINE_ENABLED:
            _mrt._logger.info("[INFO] 并发预提取 角色/场景物体/大纲（3 路并发）...")
            from step01_script_gen.concurrent_extract import extract_all_concurrent
            from shared.config import SCRIPT_CANDIDATE_MODELS as _pre_models
            _pre_model = _pre_models[0] if len(_pre_models) == 1 else None
            pre_results = extract_all_concurrent(
                novel_text,
                character_bank,
                scene_objects_bank,
                enable_characters=_mrt.CHARACTER_EXTRACTION_ENABLED,
                enable_scene_objects=_mrt.SCENE_OBJECT_EXTRACTION_ENABLED,
                enable_outline=_mrt.OUTLINE_ENABLED,
                model=_pre_model,
                outline_cache_path=_mrt.CHAPTER_DIR / "chapter_outline.cache.json",
                outline_deadline_monotonic=_deadline_monotonic,
                log_func=_mrt._logger.info,
            )
            # === 角色：注册到 bank（主流程后段仍可复用） ===
            # `None` = **这次没产出**（调用/解析失败）⇒ **不写库、不报"已保存"**
            # （2026-10-02 日志复核：鉴权失败那轮把 0 条写进库并报成功）。
            # `[]` = 真的没有角色 ⇒ 正常保存（内容不变，仍留一次来源 run_id）。
            chars = pre_results.get("characters")
            if chars is None:
                _mrt._logger.warn("[WARN] 角色预提取未产出（不写库；既有角色库原样保留）")
            else:
                pre_extracted_chars = chars
                for c in chars:
                    ident = c.get("identifier", "")
                    if not ident:
                        continue
                    if c.get("is_new", True):
                        character_bank.register_character(
                            ident,
                            c.get("static_features", ""),
                            c.get("dynamic_features", ""),
                            _mrt.NOVEL_CHAPTER,
                        )
                    elif c.get("dynamic_features"):
                        character_bank.update_dynamic_features(
                            ident, c.get("dynamic_features", ""),
                        )
                        character_bank.update_character_chapter(ident, _mrt.NOVEL_CHAPTER)
                # 来源=**这一轮确实发生**的调用（从台账取，复核之七）：条目上记
                # `last_call_ids`（最近若干次）+ `last_source`，不编造模型调用；
                # 写盘**成功**才说"已保存"（旧写法无条件报成功）。
                from shared import call_ledger as _cl
                _ok = character_bank.save(
                    run_id=_mrt._step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="characters"))
                if _ok:
                    _mrt._logger.info(f"[INFO] 预提取到 {len(chars)} 个角色特征（已保存到角色库）")
                else:
                    _mrt._logger.warn("[WARN] 角色库**未写入**（保存失败，盘上原样保留）——"
                                 "本次预提取结果未留档")
            # === 场景物体：注册到 bank ===
            objs = pre_results.get("scene_objects")
            if objs is None:
                _mrt._logger.warn("[WARN] 场景物体预提取未产出（不写库；既有物体库原样保留）")
            else:
                for o in objs:
                    scene_objects_bank.register_object(o, _mrt.NOVEL_CHAPTER)
                from shared import call_ledger as _cl
                _ok = scene_objects_bank.save(
                    run_id=_mrt._step01_run_id(),
                    call_ids=_cl.call_ids_for(schema="scene_objects"))
                if _ok:
                    _mrt._logger.info(f"[INFO] 预提取到 {len(objs)} 个场景物体（已保存到场景物体库）")
                else:
                    _mrt._logger.warn("[WARN] 场景物体库**未写入**（保存失败，盘上原样保留）"
                                 "——本次预提取结果未留档")
            # === 大纲：预提取，主分镜生成时注入 prompt（**结构化结果**，§7.2） ===
            _outline_result = pre_results.get("outline")
            if isinstance(_outline_result, dict) and _outline_result.get("executable"):
                pre_extracted_outline = _outline_result.get("plan")
            # `executable=False` **不在这里停**（§7.6）：缓存命中路径不需要计划，停点放在
            # 真正要用它的生成入口——见下方 `_stop_if_outline_unavailable` 的调用。

        # 检查缓存（场景物体库内容变化也视为源变更，v6.6）
        objects_bank_data = json.dumps(
            scene_objects_bank._data, ensure_ascii=False, sort_keys=True
        )
        prompt_changed = _mrt._prompt_changed(
            Path(__file__).parent / "prompts.py", _mrt.SCRIPT_FILE, novel_source_text, _mrt.NOVEL_CHAPTER,
            objects_bank_data, rule_version=_mrt.RULE_VERSION,
        )

        if not prompt_changed:
            _mrt._logger.info(f"[CACHE] 脚本已缓存: {_mrt.SCRIPT_FILE}")
            with open(_mrt.SCRIPT_FILE, "r", encoding="utf-8") as f:
                script = json.load(f)
            if not _mrt.HOOK_ENABLED:
                script.pop("hook", None)
            # 补写章节元信息（部名/章名，老缓存可能缺失）
            if not script.get("chapter_title"):
                script.update(_mrt.get_chapter_meta(_mrt.resolve_novel_file(), _mrt.NOVEL_CHAPTER))
            # 重新计算时间轴（使用增强算法）
            _mrt._compute_timecodes(script)
            # v7: 缓存命中时也要跑场景/道具资产提取（老缓存无 scene_assets/prop_assets），
            # 结果写回 script 再落盘，保证 step02a 资产生成能拿到数据
            _mps._extract_assets_and_update_script(script)
            # v6.62.3（09-08）：缓存命中路径此前**漏跑 hard_fix** —— 只算时间轴+提资产就
            # 落盘，导致两个落盘点行为不一致（正常生成路径见 line ~1926「每次落盘前跑一次
            # hard_fix」，v6.41 P19 铁律）。后果 09-08 实测：命中缓存把 v6.61 期的节拍词
            # 占位（「冲突，反转，真相，局势骤变」17 处）+ F8 合并标记（3 处）原样重新落盘，
            # 且 log 承诺的 W6 时长拉伸（constraints/runtime_extend）从未执行（正片 54.9s
            # < 60s 硬下限）。必须放在所有 transform 之后、dump 之前，且**不可**再调
            # _compute_timecodes（会按 motion_desc 覆盖 W6 拉伸的 duration_sec）。
            # v6.83（二次审核发现 1 实施 #3）：缓存命中路径此前直接跑 hard-fix、
            # **没有接入阶段屏障**（发现 1 第 2 条失守点）。现在与正常生成路径走
            # 同一个收口函数。
            # v6.103：finalize 由唯一提交点统一执行（此处不再重复跑一遍，
            # 避免同一个 run 里出现两个「最后一次时间写入者」）。
            # C5 修复（08-11）：fps 一致性校验（timeline 由提交点的 finalize 产出）
            # v6.62.3：必须晚于 hard_fix —— runtime_extend 会改 duration_sec，
            # 早于 hard_fix 校验会拿到过期时长（与非缓存路径同序）。
            _check_fps_consistency(script)
            # v6.103（总计划阶段 4）：缓存命中路径此前**只写 script_result.json**
            # ——不产 quality、不产 final audit、不写 commit manifest，于是「三份
            # 产物同 revision」在缓存路径上从来不成立，下游 `require_semantic_gate`
            # 必然拿到过期或缺失的审计。现在与正常生成路径共用唯一提交点。
            # 钩子禁止台词清单：正常路径由钩子生成分支采集（在缓存分支**之后**的
            # 代码里赋值）——缓存命中时那条分支根本不会执行，直接引用会
            # UnboundLocalError。这里就地按同一口径重建（shots 已在缓存里）。
            _cached_forbidden: list = []
            for _cs in script.get("shots") or []:
                _cb = _mrg._speech_body(_cs.get("narration_segment", ""))
                if _cb:
                    _cached_forbidden.append(_cb)
            if pre_extracted_outline:
                script["chapter_outline"] = pre_extracted_outline
            script, _commit_summary = _commit_step01_outputs(
                script, novel_text,
                plan=script.get("chapter_outline") or {},
                hook_ctx={"bank": character_bank,
                          "objects_bank": scene_objects_bank,
                          "forbidden_lines": _cached_forbidden,
                          "producer_model": None},
                audit_model=_mrg._candidate_model_for_audit(),
                label="缓存命中提交")
            # C1：**两处提交返回走同一个投影**——提交 BLOCK 必须落到运行终局，
            # 缓存路径此前只打了一行 console，metrics/timing 仍报 ok。
            _mrt._project_commit_outcome(_commit_summary)
            _mrt._timing_mark("commit", status=_commit_summary.get("status"))
            _mps._snapshot_run_artifacts(script)
            _mrt._logger.info("时间轴已重新计算")
            # 缓存命中：恢复 stdout 并显示最终摘要
            sys.stdout = _mrt._REAL_STDOUT
            _mrt._logger.console_silent = False
            shots = script.get("shots", [])
            _mrt._console("")
            _mrt._console("=" * 56)
            _mrt._console("  [步骤1] 分镜脚本摘要（缓存命中，未重新生成）"
                     if _commit_summary.get("status") == "COMMITTED"
                     else "  [步骤1] ✗ BLOCK —— 正式产物未更新（缓存命中路径）")
            _mrt._console("=" * 56)
            if _commit_summary.get("status") != "COMMITTED":
                _mrt._console(f"  [BLOCK] {_commit_summary.get('detail')}")
            _mrt._console(f"  输出: {_mrt.SCRIPT_FILE}")
            _mrt._console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
            _mrt._console(f"  总时长: {script.get('total_duration', 0):.1f}s")
            _sa = script.get("scene_assets", {})
            _pa = script.get("prop_assets", {})
            if _sa or _pa:
                _mrt._console(f"  资产: 场景{len(_sa)} / 道具{len(_pa)}")
            _mrt._console("=" * 56)
            return

        # 生成脚本（含 JSON 解析重试）
        # LLM 偶发输出损坏的 JSON（DeepSeek 尤甚：内部中文引号未转义/缺逗号等）。
        # 解析失败时重新调用 LLM 重试，而不是让整个流水线崩溃（每次约 3-6 分钟）。
        # v6.64（P4.5，09-11）：多路径择优——生成 SCRIPT_CANDIDATE_TOPK 个独立候选，
        # 取质量分最高的进入 GAP 循环。用 token 换稳定性，对冲模型方差。
        # 默认 doubao=2 / deepseek=1（向下兼容：=1 时行为零变化）。
        # v6.132（用户 2026-09-25）：生成执行契约（不压缩）——四个完整稿生成入口共用。
        # 在这里算：计划已定稿（缓存命中或重选都已完成），后续 GAP/收敛轮直接复用。
        # 工作包 §4 D / §7.6：真正要用计划时才判"能不能下发"——缓存命中路径已在上面
        # 提前返回，不受影响；这里停下 ⇒ **零次候选生成**，且失败原因保真到 main。
        _stop_if_outline_unavailable(_outline_result, novel_text, _deadline_monotonic)

        _contract = _mrt.render_execution_contract(pre_extracted_outline)
        _mrt._logger.info("[INFO] 生成执行契约 %d 字符（选用台词 %d 句）"
                     % (len(_contract),
                        len((pre_extracted_outline or {}).get("dialogue_lines") or [])))
        _candidates = _generate_script_candidates(
            novel_text, character_bank, scene_objects_bank, pre_extracted_outline,
            deadline_monotonic=_deadline_monotonic,
        )
        _mrt._timing_mark("candidate_generation",
                     n_candidates=len(_candidates),
                     best_score=_candidates[0][0]["total"] if _candidates else None)


        # 选总分最高的候选
        _candidates.sort(key=_msc._candidate_sort_key)
        _best_score, script = _candidates[0]
        # v6.67 接力修复：记录当前稿件的产出模型。GAP 重生成若被采纳会重置为 None
        #（产出者回到 provider 默认模型，接力标识失效）——见下方 GAP 循环。
        _producer_model = _best_score.get("_model")
        _mrt._logger.warn(f"[DEBUG-RELAY] _best_score keys={list(_best_score.keys())[:5]}, _producer_model={_producer_model!r}")
        if _mrt.SCRIPT_CANDIDATE_TOPK > 1:
            _mrt._logger.info(
                f"[CANDIDATE] 多路径择优完成：{len(_candidates)} 个候选 → "
                f"最优分 {_best_score['total']}（{_best_score['detail']}）"
            )
            _mrt._console(
                f"  [择优] {len(_candidates)} 个候选 → 最优 {_best_score['total']}分 "
                f"（{_best_score['detail']}）"
            )
            # 其余候选存盘供调试
            import copy as _cp
            try:
                from shared.json_io import atomic_dump_json as _adj
                _cand_dir = _mrt.CHAPTER_DIR / "candidates"
                _cand_dir.mkdir(parents=True, exist_ok=True)
                for _ci, (_cs, _cscript) in enumerate(_candidates):
                    _cpath = _cand_dir / f"candidate_{_ci+1}_score{int(_cs['total'])}.json"
                    _adj(_cpath, _msc._strip_runtime_fields(_cp.deepcopy(_cscript)),
                         ensure_ascii=False, indent=2)
            except Exception as _degrade_exc:
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("step01_script_gen/main.py:main", _degrade_exc)

        # ========== 差距分析（v5.0 新增）==========
        # 若差距过大，重新生成（最多2次）

        if _mrt.GAP_ANALYSIS_ENABLED:
            # v6.62.4（09-08）预算动态化：原 `for attempt in range(GAP_MAX_REGEN_ATTEMPTS)`。
            # agent_plan(doubao) 预算是 1（v6.62.1 实测「2 次重试全部劣化回退、白烧
            # 5 分钟」刻意压到 1）。但「正片零高潮覆盖」是**结构性缺陷**（整段剧情没拍），
            # 不是措辞问题，值得单独破例给 1 次。仅在预算即将耗尽时追加，且只追加一次；
            # deepseek（预算 2）不会凭空变 3，除非它也零高潮。
            # 追加那次自动受下方 v6.47 `_is_retry_degraded` 回退保护（时长塌缩/台词
            # 失锚即回退上一版），所以多给一次不引入劣化风险。
            _gap_budget = _mrt.GAP_MAX_REGEN_ATTEMPTS
            _zero_climax_bonus_used = False
            attempt = 0
            while attempt < _gap_budget:
                # 快速校验
                validation = _mrt.validate_script_gap(script, novel_text)

                if validation["overall_pass"]:
                    _mrt._logger.info(f"[GAP] 差距分析通过 (尝试 {attempt + 1}/{_gap_budget})")
                    # 保存脚本并生成完整报告
                    partial_path = _mrt.CHAPTER_DIR / "script_result.partial.json"
                    from shared.json_io import atomic_dump_json as _adj
                    _adj(partial_path, _msc._strip_runtime_fields(script),
                         ensure_ascii=False, indent=2)
                    report = _mrt.generate_gap_report(
                        str(partial_path),
                        _mrt.resolve_novel_file(),
                        _mrt.NOVEL_CHAPTER
                    )
                    # 打印报告摘要
                    _mrt.print_gap_report(report)
                    # 清理临时文件
                    if partial_path.exists():
                        partial_path.unlink()
                    break
                if not _mrt.gap_needs_full_regen(validation):
                    _mrt._logger.warn(
                        "[GAP] 仅软告警（角色特征锚点等确定性可修复项），"
                        "跳过整稿重生，转交下游约束修复"
                    )
                    _mrt._console("  [GAP] 仅软告警，跳过整稿重生")
                    break

                # 发现问题，反馈给 LLM 重新生成
                # v6.62.4：零高潮=结构性缺陷 → 预算耗尽前额外追加 1 次（只一次）
                _dist = validation.get("distribution", {}) or {}
                if (_dist.get("climax_shots", 0) == 0
                        and not _zero_climax_bonus_used
                        and attempt + 1 >= _gap_budget):
                    _gap_budget += 1
                    _zero_climax_bonus_used = True
                    _mrt._logger.warn(
                        "[GAP] 正片零高潮覆盖（结构性缺陷：原文核心冲突整段未拍）"
                        f"→ 额外追加 1 次重试预算（{_gap_budget} 次）"
                    )
                    _mrt._console("  [GAP] 正片零高潮覆盖 → 额外追加 1 次重试")

                _mrt._logger.warn(f"[GAP] 发现差距问题， 重新生成 (尝试 {attempt + 1}/{_gap_budget})...")

                # 获取差距分析上下文并追加到 prompt（函数已在模块级导入）
                gap_context = _mrt._load_gap_analysis_context(validation)
                # v6.47（08-27）反馈前置：重生成铁律放在反馈区**最前**——长 prompt 后段
                # 规则易失声（实测重试版把总时长写塌到 11s、编造原文没有的台词），
                # 必须在反馈前重申非协商规则作为拒收标准
                gap_context = _mrt._build_regen_iron_rules() + gap_context
                # v6.33（08-12）：附加 checklist 聚合反馈——差距分析之外，把内容密度/景别
                # 跳跃/致命伤等真实缺陷也喂给重生成 LLM（此前只喂覆盖率/分布，重生成还是老问题）
                _checklist_fb = _mrt.build_regen_feedback(script, novel_text)
                if _checklist_fb:
                    gap_context = gap_context + _checklist_fb

                # 追加差距反馈到 prompt，重新生成
                # v6.68: GAP 重生成未指定模型（=provider 默认模型），prompt 剖面同步跟随。
                # 默认模型 deepseek-v4-flash → deepseek 剖面不截断，救援能直接看到原文高潮
                #（此前 1600 截断下救援只能靠 gap_context 里的高潮摘录）。
                # v6.132（用户 2026-09-25）：最终组装（锚点 → 反馈 → **契约在末尾**）
                # 抽成 `_build_gap_regen_prompt()`——"块在提示里"不等于"块在末尾"，
                # 抽出来才能对**最终请求**断言末尾位置。
                prompt, _gap_system = _mrg._build_gap_regen_prompt(
                    novel_text, character_bank, scene_objects_bank,
                    pre_extracted_outline, _contract, gap_context)

                _mrt._metrics_inc("calls.gap_regen")
                service = _mrt.create_service()
                from _text_gen.deepseek_schema import SCRIPT_SCHEMA
                req = _mrt.TextGenerationRequest(
                    system_prompt=_gap_system,
                    user_prompt=prompt,
                    temperature=0.2,  # v6.5: 降低温度防止幻觉；v6.64 多候选用不同温度增加多样性
                    json_schema=SCRIPT_SCHEMA,
                    timeout_sec=_mrt._full_script_timeout_sec(),  # v6.70.3: GAP 重生 300s 分级
                )
                result = service.generate(req)
                if not result.success:
                    # v6.62.5（09-08）：原先这里 `raise RuntimeError` 会**穿出 GAP 循环
                    # 直接崩掉 step01**（本循环只 catch ValueError/JSONDecodeError）。
                    # v6.62.5 起 provider 把「输出截断」也判为 success=False，这条路径
                    # 从罕见（网络错误）变常见（截断），必须与站点 422/1146 一致地软降级：
                    # 保留上一版脚本继续跑，而不是让整个流水线挂掉。
                    _mrt._logger.warn(f"[WARN] GAP 重生成失败，保留上一版: {result.error}")
                    _mrt._console("  [GAP] 重生成失败（详见 step01.log），保留上一版")
                    break

                import re
                raw = result.text.strip()
                raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
                raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

                try:
                    new_script = _mrt.parse_response(raw, novel_text=novel_text)
                except (ValueError, json.JSONDecodeError) as e:
                    # 重生成结果 JSON 损坏 → 保留上一版，不中断流水线
                    _mrt._logger.warn(f"[WARN] 重生成 JSON 解析失败，保留上一版: {e}")
                    break
                # v6.62.7（09-09）零高潮救援：先判断这次重试是不是**修好了结构性
                # 缺陷**。回退保护此前对「结构性缺陷 vs 措辞瑕疵」一视同仁，于是把
                # 唯一一次修好结构的重试也扔掉——09-08 实测链路：正片零高潮 →
                # v6.62.4 特批 1 次重试 → 23:31 重试版以「台词失锚 2 条」被判退化回退
                # → 终稿仍零高潮（原文 3100-4847 字的刺杀/尖叫/掩尸整段没拍，刺客与
                # 张十三被提取进 characters 却零出镜，成片是 63s 吹箫风光片配一个
                # 「公子遇刺」的钩子）。两个修复互相抵消，跟 v6.62.6 那次同构。
                _new_climax = ((_mrt.validate_script_gap(new_script, novel_text)
                                .get("distribution") or {}).get("climax_shots", 0))
                _climax_rescue = _dist.get("climax_shots", 0) == 0 and _new_climax > 0
                # 救援态下被豁免的软判据仍要留痕，否则「放宽了什么」不可追溯
                _waived = (_msc._retry_degrade_reason(script, new_script, novel_text)
                           if _climax_rescue else None)
                # v6.47（08-27）回退保护：重试版质量退化（时长塌缩/台词失锚变多）→
                # 保留上一版。GAP 通过≠质量好——08-27 重试版 11 镜×1s 靠加镜头骗过
                # 覆盖检查但时长崩了，事后 W6 把 1s 拉到 8s（8 倍慢动作）。
                if _msc._is_retry_degraded(script, new_script, novel_text,
                                      climax_rescue=_climax_rescue):
                    _reason = _msc._retry_degrade_reason(script, new_script, novel_text,
                                                    climax_rescue=_climax_rescue)
                    _mrt._logger.warn(f"[GAP] 重试版质量退化：{_reason}，回退保留上一版")
                    _mrt._console(f"  [GAP] 重试版质量退化，回退保留上一版（{_reason}）")
                    break
                # v6.157（2026-09-30 复核第 4 轮「重生成身份维护」）：GAP 重生成
                # 也是**整份替换**——采纳前绑定本轮计划身份并核完整契约，不合格保留
                # 上一版（此前这里只判「质量退化」，契约与身份都不在判据里）。
                _id_why = _mrg._bind_regen_plan_identity(script, new_script,
                                                    plan=pre_extracted_outline)
                if _id_why:
                    _mrt._logger.warn("[GAP] 重生成稿未保住计划身份/契约：%s，回退保留上一版"
                                 % "；".join(_id_why[:2]))
                    _mrt._console("  [GAP] 重生成稿未保住计划身份/契约，回退保留上一版")
                    break
                if _climax_rescue:
                    _mrt._logger.warn(
                        f"[GAP] 零高潮救援：上一版 0 个高潮镜 → 重试版 {_new_climax} 个，"
                        f"采纳重试版" + (f"（已豁免软判据：{_waived}）" if _waived else "")
                    )
                    _mrt._console(f"  [GAP] 零高潮救援：采纳重试版（+{_new_climax} 高潮镜）")
                script = new_script
                # v6.67 接力修复：GAP 重生成走的是 provider 默认模型，产出者标识重置
                _producer_model = None
                # 不在循环内 post_process：循环退出后统一在 line 334 做一次
                # （code-audit 07-31 #7：原 :328 与 :334 重复调用 _post_process）
                attempt += 1   # v6.62.4：while 循环手动推进（原 for 由 range 推进）
            else:
                # 达到最大尝试次数仍有问题，记录但不中断
                # while...else 与原 for...else 语义一致：无 break 走完才告警
                _mrt._logger.warn("[GAP] 达到最大重试次数，仍有差距问题，将继续处理")
            _mrt._timing_mark("gap_loop")  # v6.70.3: 通过/耗尽均在此收口（含 else 分支）

        # v6.67（09-12）接力修复：赢家生成、输家修复——修复轮用互补模型换视角。
        # 修复 prompt 已有「只动告警镜头」+ Mask 字段约束，换模型影响面可控。
        # 候选池空/单模型/产出者无标识（GAP 重生成已采纳）→ _repair_model=None=
        # 同 provider 默认模型，零行为变化。GAP 重生成本身暂不接力（方案②范围）。
        _repair_model = _mrg._complementary_repair_model(_producer_model)
        if _repair_model:
            if _repair_model == _producer_model:
                _mrt._logger.info(f"[RELAY] 单模型继续修复: {_repair_model}")
                _mrt._console(f"  [单模型] 规划/生成/审查/修复均使用 {_repair_model}")
            else:
                _mrt._logger.info(
                    f"[RELAY] 产出模型 {_producer_model} → 修复轮接力 {_repair_model}"
                )
                _mrt._console(
                    f"  [接力] 修复轮改用 {_repair_model}（产出模型 {_producer_model} 的互补模型）"
                )

        # v6.7: 镜头数严重超限时收敛重生成（独立于 gap_analysis，默认开启）
        # v6.70.3: 超总预算 → 跳过可选修复轮（失败即保留原版语义，跳过无数据风险）
        # M04：额外动作启动**之前**为最终审计预留完整时间（不是等到审计开始时
        # 才截短额度）。动作耗时取最终生效的通道超时上限，保守按最坏情况算。
        _adm_regen = (_mrt._action_budget_admission(
            "regen_over_limit", _mrt._action_cost_sec("regen"))
            if _mrt.SHOT_COUNT_REGEN_ENABLED else {"allowed": False})
        if _mrt.SHOT_COUNT_REGEN_ENABLED and _adm_regen.get("allowed"):
            _mrt._timing_mark("pre_regen_over_limit")
            from step01_script_gen.outline import outline_to_anchor as _o2a
            script = _mrg._regen_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                # v6.131：与 GAP 重生/结构收敛同源——带上大纲锚点（含核验过的选用台词）
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                contract_block=_contract,
                # 2026-09-30 复核第 5 轮 P1-1：**本请求的权威计划**显式交给采纳入口
                # （不能让它去基线里翻 `chapter_outline`——候选阶段它还没写入）
                plan=pre_extracted_outline,
            )
            _mrt._timing_mark("regen_over_limit")
        elif _mrt.SHOT_COUNT_REGEN_ENABLED:
            _mrt._timing_mark("regen_over_limit", status="skipped_budget")
            _freeze_regen = _mfa._freeze_budget_exhausted(
                script, action="regen_over_limit", admission=_adm_regen)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_regen)
            _mrt._logger.warn("[BUDGET] 跳过镜头超限收敛轮（保留当前稿；终局=%s）"
                         % _freeze_regen.get("terminal"))
            _mrt._console("  [预算] 未给最终审计留下完整时间，冻结候选后停止额外生成")

        # v6.84（二次审核发现 1，P0）：**结构收敛轮**——单镜内容需求超 cap /
        # 时长低于下限。这两类缺陷字段级修复轮修不了（不是文本问题，是镜头切分
        # 问题），此前只有报告没有收敛路径，必然带着 BLOCK 落盘。这里让 LLM
        # **重做 shot-plan**（超限段落拆成多个完整子镜；过短镜头并入相邻剧情）。
        _adm_structural = (_mrt._action_budget_admission(
            "regen_structural_over_limit", _mrt._action_cost_sec("regen"))
            if _mrt.SHOT_COUNT_REGEN_ENABLED else {"allowed": False})
        if _mrt.SHOT_COUNT_REGEN_ENABLED and _adm_structural.get("allowed"):
            _mrt._timing_mark("pre_regen_structural")
            from step01_script_gen.outline import outline_to_anchor as _o2a
            # v6.128：结构收敛轮的道具候选短提示同样默认关闭（用户 2026-09-25 决定）
            from step01_script_gen.prop_state_anchor import (
                prop_anchor_block_for as _pabf)
            script = _mrg._regen_structural_over_limit(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                outline_anchor=_o2a(pre_extracted_outline, novel_text),
                prop_anchor_block=_pabf(pre_extracted_outline),
                contract_block=_contract,
                plan=pre_extracted_outline,      # 同上（复核第 5 轮 P1-1）
            )
            _mrt._timing_mark("regen_structural")
        elif _mrt.SHOT_COUNT_REGEN_ENABLED:
            _mrt._timing_mark("regen_structural", status="skipped_budget")
            _freeze_structural = _mfa._freeze_budget_exhausted(
                script, action="regen_structural_over_limit",
                admission=_adm_structural)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_structural)

        # v6.73: same-model independent audit before targeted repair.
        _audit_issues = []
        try:
            from shared.config import SCRIPT_CANDIDATE_MODELS as _audit_models
            _audit_enabled = (
                len(_audit_models) == 1
                and os.getenv("SINGLE_MODEL_AUDIT", "1").strip() == "1"
            )
            _budget_hit_mid = _mrt._budget_exceeded_with_pause("mid_pipeline_audit")
            if _audit_enabled and not _budget_hit_mid:
                from step01_script_gen.audit import run_same_model_audit_with_status
                _mrt._metrics_inc("calls.audit")
                _allow, _deadline, _to_info = _mrt._audit_budget_allowance()
                _mrt._logger.info("[AUDIT] 本次审计可用 %ss（%s）｜总预算剩余 %s 秒"
                             % (_allow, _to_info.get("why") or "-",
                                _to_info.get("left") if _to_info.get("left") is not None
                                else "未知"))
                _audit_result = run_same_model_audit_with_status(
                    novel_text, pre_extracted_outline, script,
                    model=_repair_model or _producer_model,
                    timeout_sec=int(_to_info.get("cap") or _mrt.AUDIT_TIMEOUT_CAP_SEC),
                    deadline_ts=_deadline,
                )
                _audit_issues = _audit_result.issues
                # M04：中间轮审计同样分别记四项读数（不进产物，只进 metrics/日志）。
                _mid_timing = _mrt._audit_budget_timing(
                    _audit_result, allowance_sec=_allow,
                    left_sec=_to_info.get("left"), phase="mid_pipeline")
                _mrt._metrics_set("audit.budget_timing_mid", _mid_timing)
                _mrt._logger.info(
                    "[AUDIT][BUDGET] mid_pipeline 剩余预算=%s｜审计允许=%ss｜"
                    "服务端等待=%ss｜流式耗时=%s（来源=%s）"
                    % (_mid_timing["remaining_budget_sec"],
                       _mid_timing["audit_allowance_sec"],
                       _mid_timing["server_wait_sec"],
                       _mid_timing["stream_elapsed_sec"],
                       _mid_timing["stream_timing_source"]))
                _mrt._metrics_set("audit.status", _audit_result.status)
                _mrt._metrics_set("audit.issues", len(_audit_issues))
                # v6.112：中间轮审计也进同一个台账（phase 区分），
                # 否则「修复轮之前那一次审计说了什么」仍然无处可查。
                _mfa._record_audit_diag(
                    {"status": _audit_result.status, "error": _audit_result.error,
                     # v6.117（方案 A）：中间轮审计与提交轮走同一个解析器，可能同样走
                     # 专用通道——台账必须记实际通道/模型，否则同一份台账里两个 phase
                     # 的 provider 归属口径不一致。
                     "model": (getattr(_audit_result, "channel_model", "") or ""
                               or _repair_model or _producer_model),
                     "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                     "schema_deviations": [str(d) for d in (
                         getattr(_audit_result, "schema_deviations", None) or [])],
                     "usage": getattr(_audit_result, "usage", None),
                     "issues": [i.to_dict() for i in _audit_issues]},
                    phase="mid_pipeline", round_index=0, script=script)
                try:
                    from shared.json_io import atomic_dump_json as _audit_dump
                    # v6.103：中间轮的 advisory 审计**不得**写正式
                    # `step01_audit.json`——那是唯一提交点的产物，被这里覆盖会
                    # ① 让「提交点之外的第二个写入者」重新出现；
                    # ② 破坏 final audit 的复用判据（revision/scope 都对不上）。
                    _audit_dump(
                        _mrt.CHAPTER_DIR / "step01_audit.advisory.json",
                        {
                            "status": _audit_result.status,
                            # 与提交轮同一口径：记**实际提交**的模型/通道
                            "model": (getattr(_audit_result, "channel_model", "") or ""
                                      or _repair_model or _producer_model),
                            "channel_provider": getattr(_audit_result, "channel_provider", "") or "",
                            "usage": getattr(_audit_result, "usage", None),
                            "error": _audit_result.error,
                            "issues": [i.to_dict() for i in _audit_issues],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as _audit_dump_err:
                    _mrt._logger.warn(f"[AUDIT] 审查结果落盘失败（不阻断）: {_audit_dump_err}")
                if _audit_result.status == "ok":
                    _mrt._logger.info(f"[AUDIT] same-model audit issues={len(_audit_issues)}")
                else:
                    _mrt._metrics_set("audit.error", _audit_result.error)
                    _mrt._logger.warn(
                        f"[AUDIT] same-model audit {_audit_result.status}"
                        f"（区别于 0 issue，不阻断）: {_audit_result.error}"
                    )
        except Exception as _audit_err:
            _mrt._metrics_set("audit.status", "exception")
            _mrt._metrics_set("audit.error", str(_audit_err))
            _mrt._logger.warn(f"[AUDIT] same-model audit failed（不阻断）: {_audit_err}")

        # v6.35（08-15，P18 闭环）：GAP 通过后仍存在的高价值告警（开篇冲突/
        # 爆点密度/威胁源朝向/关键帧质量/内容密度等）→ 定向修复轮，只重写
        # 告警镜头（此前这些 WARN 纯打印落盘，无人修，带伤进 step02）。
        _adm_repair = (_mrt._action_budget_admission(
            "repair_rounds", _mrt._action_cost_sec("repair"))
            if _mrt.REPAIR_ROUNDS_ENABLED else {"allowed": False})
        if _mrt.REPAIR_ROUNDS_ENABLED and _adm_repair.get("allowed"):
            _mrt._timing_mark("pre_repair_rounds")
            script = _mrr._repair_shot_warnings(
                script, novel_text,
                bank=character_bank, objects_bank=scene_objects_bank,
                model=_repair_model,
                audit_issues=_audit_issues,
            )
            _mrt._timing_mark("repair_rounds")
        elif _mrt.REPAIR_ROUNDS_ENABLED:
            _mrt._timing_mark("repair_rounds", status="skipped_budget")
            _freeze_repair = _mfa._freeze_budget_exhausted(
                script, action="repair_rounds", admission=_adm_repair)
            _mrt._metrics_set("budget.frozen_candidate", _freeze_repair)
            _mrt._logger.warn("[BUDGET] 跳过定向修复轮（保留当前稿，告警仍会落 SAVE-WARN；"
                         "终局=%s）" % _freeze_repair.get("terminal"))
            _mrt._console("  [预算] 未给最终审计留下完整时间，跳过定向修复轮")

        # 后处理：注入全局配置 + 合规过滤 + 敏感词校验
        script = _post_process(script)

        # 脚本已更新，删除旧的音频和视频文件。
        # 2026-08-09 用户需求：若新旧脚本只是分镜数量差异且总时长≤3min，逐镜对比后
        # 保留未变镜头的帧/视频产物，只清理新增/改动/删除的镜头（避免重跑 step02 全量
        # 重新生成白烧额度）。
        # 2026-08-09 修复：原实现把此段放在 script 生成之前，引用未赋值变量 script
        # 触发 UnboundLocalError，逐镜保留从未生效（每次都全量清理）。现在移到
        # _post_process 之后——新分镜已定稿（含收敛/合规过滤），旧 script_result.json
        # 仍在磁盘可读，对比才有意义；且 LLM 生成失败时旧产物也不会被提前删掉。
        _preserve_shots: list[int] = []
        try:
            from shared.shot_diff import compute_shot_diff, should_preserve_artifacts
            _old_script = json.load(open(_mrt.SCRIPT_FILE, encoding="utf-8")) if _mrt.SCRIPT_FILE.exists() else None
            if _old_script and should_preserve_artifacts(_old_script, script):
                _diff = compute_shot_diff(_old_script, script)
                _preserve_shots = _diff.get("unchanged", [])
                _mrt._logger.info(
                    f"[PRESERVE] 逐镜对比放行：未变 {len(_preserve_shots)} 镜"
                    f"（保留旧产物），新增/改动 {len(_diff.get('added', [])) + len(_diff.get('changed', []))} 镜重新生成"
                )
                # 只清理 changed/removed 镜头的帧/视频（未变镜头保留）
                for _sub_path, _shots_root in (
                    (_mrt.vimax_working_dir(), _mrt.vimax_shots_dir()),
                    (_mrt.stretched_dir(), _mrt.stretched_dir() / "shots"),
                ):
                    if _sub_path.exists():
                        if _shots_root.exists():
                            for _sid in _diff.get("changed", []) + _diff.get("removed", []):
                                _shot_dir = _shots_root / str(_sid)
                                if _shot_dir.exists():
                                    import shutil as _sh
                                    _sh.rmtree(_shot_dir)
                                    _mrt._logger.verbose(f"  [已清理] {_sub_path.name}/shots/{_sid}/（内容变更）")
        except Exception as _preserve_err:
            _mrt._logger.warn(f"[PRESERVE] 逐镜对比失败，全量清理: {_preserve_err}")

        if not _preserve_shots:
            _mrt._logger.info(f"[INFO] 分镜脚本已更新，清理旧的音视频文件...")
            for subdir_path in (_mrt.audio_dir(), _mrt.vimax_working_dir(), _mrt.stretched_dir()):
                if subdir_path.exists():
                    import shutil
                    try:
                        shutil.rmtree(subdir_path)
                        _mrt._logger.verbose(f"  [已删除] {subdir_path.name}/")
                    except PermissionError as _e:
                        # 09-13 实测：Explorer/看图器/杀毒占用目录 → WinError 32。
                        # 清理失败就 FATAL 会吞掉整个 LLM 生成结果（此时 script 尚未
                        # 落盘，两轮实跑各 ~10 min 全白跑）。旧帧由内容哈希自动失效
                        # （ff/kf desc 变更 → hash mismatch → step02a 重抽），跳过即可。
                        _mrt._logger.warn(f"  [WARN] {subdir_path.name}/ 被占用（{type(_e).__name__}），"
                                     "跳过清理——旧文件由内容哈希自动失效")
        # 删除最终视频和字幕（脚本结构变了，最终产物必须重做）
        for fpath in (_mrt.final_file(), _mrt.subtitles_file()):
            if fpath.exists():
                try:
                    fpath.unlink()
                    _mrt._logger.verbose(f"  [已删除] {fpath.name}")
                except PermissionError:
                    _mrt._logger.warn(f"  [WARN] {fpath.name} 被占用（播放器？），跳过重删")

        # ========================================================================
        # 钩子分镜生成（v6 沿用 + v6.39 调用顺序硬约束）
        # ========================================================================
        # 严格 3 步调用顺序（C4 修复 08-11 + v6.32 强化 08-12），禁止任何步骤乱序：
        #
        #   ┌─────────────────────────────────────────────────────────────┐
        #   │  ① 提取 forbidden_lines  ← 必须在主分镜 shots 落盘后做         │
        #   │     (剥说话人前缀的台词体列表，注入钩子 prompt 防重复穿帮)      │
        #   │                          ↓                                  │
        #   │  ② 调用 hook = gen_hook_script(forbidden_lines=...)          │
        #   │     (钩子 LLM 在 prompt 看到禁止清单，从源头不写重复台词)       │
        #   │                          ↓                                  │
        #   │  ③ 合并 + 去重 retry + 兜底清空                               │
        #   │     (挂载后比对台词，仍重复则重试 2 次；再重复则清空台词字段)     │
        #   └─────────────────────────────────────────────────────────────┘
        #
        # 关键 invariant（破坏任意一条会引发「钩子台词=主分镜台词」倒叙穿帮）：
        #   - 步骤① forbidden_lines 必须在 shots 落盘后才能采集（空列表=无效）
        #   - 步骤② gen_hook_script 调用时 forbidden_lines 必须非 None
        #   - 步骤③ _hook_dup_hits 比对时 shots 列表未变（避免 race）
        # ========================================================================
        _forbidden_lines: list[str] | None = None
        if _mrt.HOOK_ENABLED and _mfz._hook_max_sends() > 0:
            #: 钩子分类预算（初始 1 次 + 重复重试；上限 `_hook_max_sends()`）
            _hook_sends = 0
            # ===== ① 提取 forbidden_lines（assert 保护调用顺序）=====
            _shots_for_hook = script.get("shots", [])
            assert _shots_for_hook, (
                "HOOK pipeline 顺序保护：步骤②前必须先生成主分镜 shots。"
                "当前 script['shots'] 为空——主分镜 LLM 调用可能失败或被跳过。"
            )
            _forbidden_lines: list[str] = []
            for _s in _shots_for_hook:
                _body = _mrg._speech_body(_s.get("narration_segment", ""))
                if _body:
                    _forbidden_lines.append(_body)
            _mrt._logger.info(f"[INFO] 钩子禁止台词清单已就绪（{len(_forbidden_lines)} 条）")

            # ===== ② 调用 hook LLM（注入 forbidden_lines）=====
            _mrt._logger.info("\n[INFO] 开始生成钩子分镜...")
            hook = gen_hook_script(
                novel_text,
                bank=character_bank,
                objects_bank=scene_objects_bank,
                forbidden_lines=_forbidden_lines,  # 必传参数，v6.32 强约束
            )
            _hook_sends += 1
            _mrt._timing_mark("hook_generation", ok=hook is not None)

            if hook:
                # ===== ③ 合并 + 去重 retry + 兜底清空 =====
                script["hook"] = hook
                _mrt._logger.info(f"[OK] 钩子分镜已生成: {hook.get('narration_segment', '')[:30]}...")

                # 闭包：引用外层 _shots_for_hook（步骤③ shots 列表不变 invariant）
                def _hook_dup_hits() -> list:
                    """钩子台词与主分镜任一镜头台词重复的镜头 index 列表（空=无重复）."""
                    _hl = _mrg._speech_body(hook.get("narration_segment", ""))
                    _hits = []
                    if not _hl:
                        return _hits
                    for _s in _shots_for_hook:  # ← 闭包用 _shots_for_hook 而非 script.get，避免 race
                        _sb = _mrg._speech_body(_s.get("narration_segment", ""))
                        if _sb and (_hl == _sb or _hl in _sb or _sb in _hl):
                            _hits.append(_s.get("index", "?"))
                    return _hits

                # C4 修复（08-11）+ v6.32 强化（08-12）：挂载后立即比对台词去重，
                # 重复则自动重新生成钩子（换一句台词），最多再试 2 次；仍重复才 WARN
                # 提示人工处理。钩子是「最精彩瞬间」的倒叙，与主分镜台词重复会直接
                # 穿帮（观众刚在钩子听到、又在下个镜头原样听到）。
                _dup_hits = _hook_dup_hits()
                _regen_attempt = 0
                while (_dup_hits and _regen_attempt < 2
                       and _hook_sends < _mfz._hook_max_sends()):
                    _regen_attempt += 1
                    _hook_line_prev = _mrg._speech_body(hook.get("narration_segment", ""))
                    _mrt._logger.warn(
                        f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复（C4 修复），"
                        f"自动重新生成钩子换台词（尝试 {_regen_attempt}/2）..."
                    )
                    _mrt._console(f"  [WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复，重新生成钩子（{_regen_attempt}/2）...")
                    _dup_note = (
                        f"上一版钩子台词「{_hook_line_prev[:30]}」与主分镜镜头 {_dup_hits} 的台词重复"
                        f"（观众刚在钩子听到、又在正片听到，倒叙穿帮）。请严格换一句完全不同的台词：\n"
                        f"1. 优先选主分镜其它镜头未用过的对话（如角色丙的质问/密谋、角色甲的台词、管事丁的试探）；\n"
                        f"2. 若所有人物对话都与主分镜重复，则把 narration_segment 和 subtitle 设为空字符串\"\"，"
                        f"钩子改纯画面动作开场（袭击者破水而出、匕首出鞘的瞬间），靠视觉冲击不靠台词。"
                    )
                    _hook_retry = gen_hook_script(
                        novel_text, bank=character_bank, objects_bank=scene_objects_bank,
                        forbidden_lines=_forbidden_lines, dup_note=_dup_note,
                    )
                    _hook_sends += 1
                    if not _hook_retry:
                        break  # 重生成失败，保留原钩子
                    hook = _hook_retry
                    script["hook"] = hook
                    _mrt._logger.info(f"[OK] 钩子分镜已重新生成: {hook.get('narration_segment', '')[:30]}...")
                    _dup_hits = _hook_dup_hits()
                if _dup_hits:
                    # 确定性兜底（08-12）：重试仍重复则清空钩子台词，改纯画面开场——
                    # 钩子画面（刺客破水+匕首下刺）本身是强开场，宁可无台词也不倒叙穿帮。
                    hook["narration_segment"] = ""
                    hook["subtitle"] = ""
                    _mrt._logger.warn(
                        f"[WARN] 钩子台词与主分镜镜头 {_dup_hits} 重复且重生成仍未解决，"
                        f"已强制改为纯画面开场（narration_segment 清空）——无台词优于倒叙穿帮。"
                    )
                    _mrt._console(f"  [WARN] 钩子台词仍重复，已改为纯画面开场")
                # v6.18：钩子在 parse_response 之后才挂载（_validate_script 已跑完），
                # 必须补自动修复 + 校验，否则钩子 ff_desc 零校验零修复（多动作堆叠
                # 直接落盘，导致首帧图画不出三个同时态动作）。
                try:
                    from step01_script_gen.validation import post_fix_shot_prompts, validate_hook
                    post_fix_shot_prompts(script)  # 幂等：shots 已修复过，主要触发 hook 分支
                    validate_hook(script["hook"], script.get("characters", []))
                except Exception as _hook_fix_err:
                    _mrt._logger.warn(f"[WARN] 钩子修复/校验异常（不阻断）: {_hook_fix_err}")

                # ===== ④ 钩子后置校验轮（v6.70.1，09-13；09-14 抽出共享门）=====
                # 结构性缺口：钩子生成时点在定向修复轮之后，checklist 的 LLM 修复轮
                # 永远轮不到 hook——09-13 实测：修复轮 600s 超时保留原版 + 钩子原生
                # 输出带无主刀尖直接落盘 → step02a QC 名单打架整镜烧穿。质量门抽为
                # _ensure_hook_quality 共享：对两条正式输出来源（本次 run /
                # BEST-GUARD 恢复的快照）同等生效。
                try:
                    _ensure_hook_quality(
                        script, novel_text=novel_text, bank=character_bank,
                        objects_bank=scene_objects_bank,
                        forbidden_lines=_forbidden_lines,
                        producer_model=_producer_model)
                except Exception as _e:
                    _mrt._logger.warn(f"[WARN] 钩子后置校验异常（不阻断）: {_e}")
            else:
                _mrt._logger.info("[INFO] 跳过钩子分镜")

        # 提取角色（跨章节一致性）
        if _mrt.CHARACTER_EXTRACTION_ENABLED:
            if pre_extracted_chars is not None:
                # 复用预提取结果，避免二次 LLM 调用（角色库已在预提取阶段保存）
                chars = pre_extracted_chars
                _mrt._logger.info(f"[INFO] 复用预提取的 {len(chars)} 个角色")
            else:
                # 预提取阶段失败时才重新提取
                _mrt._logger.info("[INFO] 提取角色信息...")
                # #26 修复 2026-08-14：LLM 提取失败不应中断整条流水线——
                # 回退到脚本解析阶段已收集的 characters（可能为空），仅 WARN。
                # 2026-10-02：提取器把"没产出"（调用/解析失败）与"真的没有角色"分开
                # ——前者返回 `None`，这里同样按失败回退（旧实现两者都是 `[]`，
                # 于是 `script["characters"]` 会被写成 None / 记成"0 个"）。
                try:
                    chars = _mrt.extract_characters(novel_text, character_bank)
                    if chars is None:
                        _mrt._logger.warn(
                            "[WARN] 角色提取未产出（调用/解析失败）——回退使用脚本内已有 characters")
                        chars = script.get("characters", []) or []
                except Exception as _char_err:
                    _mrt._logger.warn(
                        f"[WARN] 角色提取失败（不阻断）: {_char_err}，"
                        f"回退使用脚本内已有 characters")
                    chars = script.get("characters", []) or []
            script["characters"] = chars
            # 注意：角色库已在预提取阶段保存，无需重复保存
            _mrt._logger.info(f"[INFO] 已提取 {len(chars)} 个角色")

        # 写入场景物体锚定（v6.6）：供下游 step02/validation 引用统一尺寸
        _scene_objects_out = {}
        for _oid in scene_objects_bank.get_all_identifiers():
            _obj = scene_objects_bank.get_object(_oid)
            if _obj:
                _scene_objects_out[_oid] = {
                    k: _obj[k] for k in ("category", "subtype", "length_m", "width_m",
                                         "height_m", "capacity", "structure",
                                         "consistent_terms")
                    if k in _obj
                }
        script["scene_objects"] = _scene_objects_out

        # v7: 场景/道具资产提取（依赖 script 的 shots[].scene/location/prompt，
        # 必须在脚本生成/收敛之后执行；缓存命中路径也调用同一函数）
        _mps._extract_assets_and_update_script(script)
        _mrt._timing_mark("asset_extraction")

        # 计算时间轴（v5.0 运镜量化参数）
        _mrt._compute_timecodes(script)
        # 08-23 v6.41 P19 闭环：硬修复层必须在 _compute_timecodes 之后调用——
        # 此前 hard_fix 在 REPAIR 内部调用，但 line 1659 _compute_timecodes 会按
        # motion_desc 重算 duration_sec，覆盖 W6 时长拉伸的值。改为：每次落盘前
        # 跑一次 hard_fix_shot_prompts（含 W6 时长拉伸），保证最终值持久化。
        # v6.42：改用新 Constraint 框架（is_final_pass=True 让 RuntimeExtendConstraint 拉伸时长）
        # v6.83（09-20，二次审核发现 1 实施 #3）：三条路径统一走
        # `finalize_step01_script()` —— hard-fix（含阶段屏障）+ timeline 序列化 +
        # 最终时间不变量，顺序写在一处，任何入口都不能绕过。
        script, _finalize_errs = _mfz.finalize_step01_script(
            script, novel_text=novel_text, label="落盘前")
        # C5 修复（08-11）：fps 一致性校验（timeline 此时已存在）
        _check_fps_consistency(script)

        # 章节元信息（部名/章名，来自小说标题行「第一部 杀青州 第NNN章 章名」）
        script.update(_mrt.get_chapter_meta(_mrt.resolve_novel_file(), _mrt.NOVEL_CHAPTER))

        # 写入 hash
        # v6.83（二次审核发现 6）：**必须用非下划线键**。
        #
        # 落盘前会调 `_strip_runtime_fields()`，它递归删除所有下划线开头的键——
        # 写 `_source_hash` 等于写了个立刻被删的字段，`_prompt_changed()` 下次
        # 永远读到空值、永远返回 True：**每次运行 step01 都会重新调用模型**，
        # 「重跑 = 缓存重验」这条路径从未生效过（chapter 001 实测
        # `_source_hash=None`）。缓存/version 迁移测试也因此失去意义。
        # 读侧仍兼容旧文件里的 `_source_hash`（见 hash_cache._prompt_changed）。
        script["source_hash"] = _mrt._hash_source(
            str(Path(__file__).parent / "prompts.py"), novel_source_text, _mrt.NOVEL_CHAPTER,
            objects_bank_data, rule_version=_mrt.RULE_VERSION,
        )

        # v6.50（08-29）：保存前重算 shot_count——timecode pre-pass 拆镜发生在
        # parse 期校正之后，元数据会落后实际镜头数（15:12 run 16 vs 20 实测）
        script["shot_count"] = len(script.get("shots", []))

        # v6.70.1（09-14）：BEST-GUARD 可能选中历史快照——v6.103 起它**只选候选**，
        # 不再落盘、不再 finalize、不再跑钩子门；被选中的快照与本次终稿走**同一条**
        # 公共提交管线（hook QC → finalize → revision → quality → final audit）。
        if not _mrt.HOOK_ENABLED:
            script.pop("hook", None)
        # v6.135（工作包 sunny-kindling-wilkinson，审核约束③时序）：**终稿台词完整性门**
        # ——盖在定向拆分（`_scoped_split_over_cap_shots` 会重排 narration）与
        # finalize **之后**、BEST-GUARD 之前：这一版才是实际送入选稿/提交门的最终形态。
        # 09-26 02:30 实损证明碎片可能由拆分层产生（候选层单镜完整、拆后句中截断），
        # 只查候选会漏。破损 ⇒ 显式失败（决策点 1），不静默落回历史快照。
        from step01_script_gen.plan_feasibility import (DialogueGateError,
                                                        DialogueIncompleteError,
                                                        check_script_dialogue_integrity,
                                                        dump_dialogue_incomplete,
                                                        plan_dialogue_lines)
        # v6.156（2026-09-30 复核第 5 组）：事件履约契约**同源复核**——拆分层可能
        # 让 plan_refs 的节拍/台词关系断开（覆盖门与台词门各自都能过），这里与台词门
        # 同位置、同 fail-closed 口径再核一次。
        try:
            from step01_script_gen.plan_feasibility import (EventContractBrokenError,
                                                           check_event_contract)
            # 同上（P1-1）：拆分/finalize 之后的终稿层同样要求逐镜声明
            _ct_ok, _ct_viol, _ = check_event_contract(
                script, pre_extracted_outline, require_declaration=True)
        except Exception as _e:
            _ct_ok = False
            _ct_viol = [{"code": "gate_error", "shot": "", "detail": str(_e)}]
            _mrt._logger.error(f"[CANDIDATE] 事件履约契约门异常（终稿层，fail-closed）：{_e}")
        if not _ct_ok:
            _mrt._logger.error(
                "[CANDIDATE] event contract failed（终稿层，拆分/finalize 后）："
                + "；".join("%s@%s %s" % (v.get("code"), v.get("shot") or "-",
                                          str(v.get("detail"))[:60])
                            for v in _ct_viol[:3]))
            raise EventContractBrokenError(_ct_viol)
        try:
            _dlg_ok, _dlg_broken = check_script_dialogue_integrity(script, pre_extracted_outline)
        except Exception as _e:
            # v6.135 四审 P1：门自身异常 = 无法证明台词完整 → fail-closed 显式 BLOCK
            # （独立诊断），不裸崩、不退回原样匹配放行
            _dlg_ok = False
            _dlg_broken = [{"line": "", "reason": "gate_error: %s" % _e}]
            _mrt._logger.error(f"[CANDIDATE] 台词门异常（终稿层，fail-closed BLOCK）：{_e}")
        if not _dlg_ok:
            _dlg_total = len(plan_dialogue_lines(pre_extracted_outline))
            dump_dialogue_incomplete(_mrt.CHAPTER_DIR, [_dlg_broken], total_lines=_dlg_total,
                                     layer="终稿层", plan=pre_extracted_outline)
            _dlg_gate_err = str(_dlg_broken[0].get("reason") or "").startswith("gate_error")
            _mrt._logger.error(
                "[CANDIDATE] dialogue integrity failed（终稿台词完整性门，拆分/finalize 后）："
                + "；".join("%s（%s）" % (b.get("line", "")[:40], b.get("reason"))
                            for b in _dlg_broken[:3]))
            if _dlg_gate_err:
                # v6.135 五审：纯门故障 ≠ 稿件不完整——独立异常类 + 诊断
                # conclusion=dialogue_gate_error（dump 按批次自动归类）
                raise DialogueGateError(
                    "终稿台词完整性硬门自身异常（fail-closed 不放行）：%s"
                    % str(_dlg_broken[0].get("reason") or "")[:200],
                    broken_batches=[_dlg_broken])
            raise DialogueIncompleteError(
                "终稿台词完整性硬门：%d/%d 句破损（示例：%s…［%s］）——"
                "碎片大概率由定向拆分产生，破损稿不进入选稿/提交门"
                % (len(_dlg_broken), _dlg_total,
                   str(_dlg_broken[0].get("line") or "")[:40],
                   _dlg_broken[0].get("reason")),
                broken_batches=[_dlg_broken])
        _bg_selected, _bg_meta = _cross_run_best_guard(script, novel_text,
                                                       plan=pre_extracted_outline)
        if _bg_selected is not None:
            script = _bg_selected
        if pre_extracted_outline:
            script["chapter_outline"] = pre_extracted_outline
        _mrt._metrics_update("best_guard", {
            "selected": bool(_bg_meta.get("selected")),
            "reason": _bg_meta.get("reason"),
            "new_score": _bg_meta.get("new_score"),
            "best_score": _bg_meta.get("best_score"),
            # v6.152（C5）：被初筛拒的快照清单落进产物（此前只在内存 meta 里，
            # 日志又截断到 6 条 ⇒ 事后无从复核）
            "prefilter_rejected": list(
                _bg_meta.get("prefilter_rejected_snapshots") or []),
        })

        # ===== 唯一最终提交点（v6.103，总计划阶段 4）=====
        # hook QC/重生 → finalize → revision → 确定性 quality → final semantic
        # audit → （有 blocking 则受限修复 ≤2 轮）→ staged 三件套自验 →
        # audit → quality → script 依次原子替换 → step01_commit.json。
        # 三条入口（正常生成 / 缓存命中 / BEST-GUARD 选中）都只走这里。
        script, _commit_summary = _commit_step01_outputs(
            script, novel_text,
            plan=script.get("chapter_outline") or pre_extracted_outline,
            hook_ctx={"bank": character_bank,
                      "objects_bank": scene_objects_bank,
                      "forbidden_lines": _forbidden_lines,
                      "producer_model": _producer_model},
            audit_model=_repair_model or _producer_model,
            label="落盘前提交",
            # v6.129：选稿边界记录的快照自身 revision（见 `_cross_run_best_guard`）
            source_revision=str(_bg_meta.get("selected_revision") or ""),
            source_aliases=dict(_bg_meta.get("selected_revision_aliases") or {}))
        # C1：提交结果 → 运行终局（**与缓存命中路径同一投影**）
        _mrt._project_commit_outcome(_commit_summary)
        # C5 修复（08-11）：fps 一致性校验（finalize 之后 timeline 已存在）
        _check_fps_consistency(script)
        # 提交成功后留档（失败时提交点自己会写诊断快照）
        if _commit_summary.get("status") == "COMMITTED":
            _mps._snapshot_run_artifacts(script)
        _mrt._timing_mark("commit", status=_commit_summary.get("status"))

        # v6.70.1（09-13）：提交后未解决 issue 汇总——对最终写盘版本生效。
        # 定向修复轮超时/失败走「保留原版」时只有一行 WARN，残伤数据静默落盘
        # （09-13 hook 无主刀尖即此形态），这里显式打出（不阻塞；确定性护栏已在
        # hard_fix 修过一轮，剩下的是 LLM 级）。
        try:
            _final_hook_issues = _msc._collect_hook_issues(script) if script.get("hook") else []
            _final_hard = _msc._collect_repair_warnings(script, novel_text)
            _n_hard_shots = len([k for k in (_final_hard.get("shots") or {})
                                 if k not in ("_has_hard",)])
            if _final_hard.get("_has_hard") or _final_hook_issues:
                _mrt._logger.warn(
                    f"[SAVE-WARN] ⚠️ 落盘脚本仍带未解决 issue：{_n_hard_shots} 镜 + "
                    f"钩子 {len(_final_hook_issues)} 条——修复轮可能超时保留原版，"
                    f"请过目 step01 日志再进 step02a")
                _mrt._console(f"  ⚠️ 落盘前仍有未解决 issue（{_n_hard_shots} 镜 / 钩子 "
                         f"{len(_final_hook_issues)} 条），详见日志")
        except Exception as _e:
            _mrt._logger.warn(f"[WARN] 落盘前 issue 汇总异常（不阻断）: {_e}")

        # 质量报告由提交点产出（staged → 自验 → 原子替换），这里只消费它的结果。
        _quality = _commit_summary.get("quality") or {}
        _shot_total = sum(float(s.get("duration_sec") or 0) for s in script.get("shots", []))
        _hook_total = float((script.get("hook") or {}).get("duration_sec") or 0)
        _mrt._metrics_update("duration", {
            "duration_band": _mrt.duration_band(),
            "shots_total_sec": round(_shot_total, 3),
            "hook_sec": round(_hook_total, 3),
            "total_sec": round(_shot_total + _hook_total, 3),
            "budget_status": (
                "PASS" if _mrt.DURATION_MIN_SEC - 0.01 <= _shot_total <= _mrt.DURATION_MAX_SEC + 0.01 else "BLOCK"
            ),
        })
        _mrt._metrics_update("quality", {
            "status": _quality.get("status"),
            "hard_count": _quality.get("hard_count", 0),
            "visual_risk_count": _quality.get("visual_risk_count", 0),
            "advisory_count": _quality.get("advisory_count", 0),
            "video_advisory_count": _quality.get("video_advisory_count", 0),
            "blocking_categories": _quality.get("blocking_categories", []),
            "rule_version": _quality.get("rule_version"),
        })
        if not _quality or _quality.get("status") is None:
            # 2026-09-27（复核三轮）：提交提前中止时（本轮实测：最终审计调用
            # `request_failed: Connection error`）**根本没有产出质量报告**——
            # `_commit_summary` 里没有 `quality` 键，于是下面那个 `else` 会打出
            # 「PASS: 无 HARD / VisualRisk 阻塞项」。那是**没检出**被说成**没问题**
            # （工作准则 7），而且同一个 run 的 manifest 里还列着 10 条 blocking。
            # 「没有可用的质量判定」必须显式说出来。
            _mrt._logger.warn(
                "[QUALITY-GATE] 未产出质量报告（提交提前中止 / 审计未完成）——"
                "本次没有可用的质量判定，不得据此认为无阻塞项"
            )
        elif _quality.get("blocking"):
            _mrt._logger.warn(
                f"[QUALITY-GATE] BLOCK: hard={_quality.get('hard_count', 0)}, "
                f"visual={_quality.get('visual_risk_count', 0)}; "
                f"categories={_quality.get('blocking_categories', [])}"
            )
            _mrt._console("  [QUALITY-GATE] BLOCK: 仍有生图阻塞告警，见 step01_quality.json")
        else:
            _mrt._logger.info("[QUALITY-GATE] PASS: 无 HARD / VisualRisk 阻塞项")
        # v6.103：**只有真的提交了**才能说「已保存」。提交点 BLOCK 时三份正式产物
        # 一个字都没换（旧脚本会被骗成「已保存」——真实重跑实测过：BLOCK 之后仍打
        # 了「[OK] 分镜脚本已保存至 …」，而文件时间戳还是上一版）。
        _committed = _commit_summary.get("status") == "COMMITTED"
        if _committed:
            _mrt._logger.info(f"[OK] 分镜脚本已提交至 {_mrt.OUTPUT_FILE}")
        else:
            _mrt._logger.error(
                f"[BLOCK] 分镜脚本**未提交**（三份正式产物保持上一版）："
                f"{_commit_summary.get('detail')}"
            )

        # ---- 最终摘要：写真实 stdout（控制台），绕过重定向 ----
        shots = script.get("shots", [])
        _mrt._console("")
        _mrt._console("=" * 56)
        _mrt._console("  [步骤1] " + ("完成 —— 分镜脚本摘要" if _committed
                                  else "✗ BLOCK —— 正式产物未更新"))
        _mrt._console("=" * 56)
        if not _committed:
            _mrt._console(f"  [BLOCK] {_commit_summary.get('detail')}")
            _mrt._console("  step01_commit.json 标记为 BLOCK；下游（step02a）会拒绝启动")
        _mrt._console(f"  输出: {_mrt.OUTPUT_FILE}")
        _mrt._console(f"  镜头数: {len(shots)}" + ("  + 钩子" if script.get("hook") else ""))
        _mrt._console(f"  总时长: {script.get('total_duration', 0):.1f}s")
        _scene_objs = script.get("scene_objects", {})
        if _scene_objs:
            _mrt._console(f"  场景物体: {', '.join(_scene_objs.keys())}")
        _sa = script.get("scene_assets", {})
        _pa = script.get("prop_assets", {})
        if _sa or _pa:
            _mrt._console(f"  资产: 场景{len(_sa)}个 {', '.join(_sa.keys())} / 道具{len(_pa)}个 {', '.join(_pa.keys())}")
        for k in ("resolution", "ratio", "fps", "render_style"):
            if k in script:
                _mrt._console(f"  {k}: {script[k]}")
        _mrt._console("  前 3 镜:")
        for shot in shots[:3]:
            idx = shot.get("index", "?")
            sub = (shot.get('subtitle', '') or '').strip()
            if sub:
                _mrt._console(f"    - 镜{idx}: {sub[:42]}")
            else:
                # subtitle 为空时回退到 main_subject（避免误报"空镜"）
                ms = (shot.get('main_subject', '') or '').strip()
                _mrt._console(f"    - 镜{idx}: [{ms[:42]}] （subtitle 空）" if ms else f"    - 镜{idx}: (无 main_subject)")
        _mrt._console("=" * 56)
        _mrt._console("  ⚠️ 详细校验 WARN 已写入 step01.log，控制台不再重复输出")
        _mrt._console("=" * 56)

        # 恢复 stdout
        sys.stdout = _mrt._REAL_STDOUT
        _mrt._logger.console_silent = False
    finally:
        _mrt._release_step01_lock()
        # **终局判定单点**（2026-10-02 复核）：`finally` 里 `sys.exc_info()` 非空 ⇒ 本轮
        # 是带着异常退出的（含 `PlanInfeasibleError` 这类"计划层停下"）⇒ metrics/timing
        # 如实写 failed + 码；旧实现无论怎么死都写 `_total status=ok`。
        _exc = sys.exc_info()[0]
        if _exc is not None:
            _mrt._run_finish("failed", getattr(_exc, "__name__", str(_exc)))
        _mrt._timing_dump(_mrt.CHAPTER_DIR / "step01_timing.json")  # v6.70.3: 阶段耗时指标落盘
        _mrt._metrics_dump(_mrt.CHAPTER_DIR / "step01_metrics.json")  # 调用/修复/时长结构化指标


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        # 完整 traceback 写入 step01.log（verbose 仅文件，不依赖 stderr）
        for line in tb.splitlines():
            _mrt._logger.verbose(line)
        # 恢复真实 stdout，控制台显示一行错误摘要
        sys.stdout = _mrt._REAL_STDOUT
        _mrt._logger.console_silent = False
        _mrt._logger.error(f"[FATAL] {_fatal_summary(sys.exc_info()[1], tb)}")
        sys.exit(1)
