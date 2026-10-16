"""step03_tts_audio - TTS audio generation entry point"""
import sys
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 运行范围（--chapter / --book / --book-name）与**候选参数**（--candidate-root /
# --video-candidates / --plan-only）必须在下面的 env 读取之前生效：
# CHAPTER_DIR / SCRIPT_FILE / AUDIO_OUTPUT_DIR 与 tts.config 的 OUTPUT_PATH 都是
# import 时求值，main() 里再设 env 已经晚了（会静默用错章）。
# 守卫与参数定义见 shared/run_scope.bootstrap 与 step03_tts_audio/cli.py 的文档。
_options = None
if __name__ == "__main__":
    from step03_tts_audio.cli import bootstrap as _bootstrap_scope
    _options = _bootstrap_scope("step03 TTS 语音生成")

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# Configuration
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import (audio_dir, audio_file, chapter_dir, narration_audio_file,
                          script_file)
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
AUDIO_OUTPUT_DIR = audio_dir()

from step03_tts_audio.cli import (ENV_ATTEMPT_ID, ENV_CANDIDATE_ROOT, StageOptions,
                                  attempt_id_from_env, candidate_root_from_env,
                                  plan_only_from_env, new_attempt_id)


class _LogContext:
    """给 `shared.paths.execution_log_file` 的最小上下文（它只读 log_dir/attempt_dir）."""

    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)


def _candidate_log_dir():
    """候选模式的日志目录（attempt 内）——**不写生产 step03.log**（§T4）."""
    from shared.enhancement_io import ATTEMPTS_DIRNAME
    root = candidate_root_from_env()
    if not root:
        return None
    aid = os.getenv(ENV_ATTEMPT_ID, "").strip() or "unassigned"
    return Path(root) / ATTEMPTS_DIRNAME / aid / "logs"


def _log_file() -> Path:
    """执行日志单源：有候选上下文 ⇒ attempt 内；否则原生产日志合同."""
    from shared.paths import execution_log_file
    ctx = _candidate_log_dir()
    return execution_log_file("step03", _LogContext(ctx) if ctx else None)


# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(_log_file(), console_level="INFO")

# Submodule imports
from step03_tts_audio.shot_tts import (build_speech_units, current_scope,
                                       gen_all_shot_tts, gen_narration_tts,
                                       load_script as load_script_module,
                                       script_revision_of)
from tts.config import config as tts_config
from tts.voice_registry import get_registry
from shared.media_inputs import (MediaInputError, load_candidate_clips,
                                 probe_actual_audio, resolve_clip_input)


def _resolve_clip_inputs(hook, shots, scope, clips_ctx) -> list:
    """逐镜素材解析（唯一 resolver；hook 就是 idx=0，不再有旧特殊路径）."""
    indexes = ([0] if hook else []) + [s.get("index", 0) for s in shots]
    out = []
    for idx in indexes:
        try:
            out.append(resolve_clip_input(idx, scope, None, clips_ctx, must_exist=False))
        except MediaInputError as exc:
            raise MediaInputError(
                exc.code,
                "第 %s 镜素材解析失败（候选 manifest 里没登记就显式失败，不回退正式目录）: %s"
                % (idx, exc.message),
                field_path=exc.field_path, idx=idx, path=exc.path,
                candidates=exc.candidates) from exc
    return out


def _candidate_attempt_dir(candidate_root, attempt_id: Path | str) -> Path:
    from shared.enhancement_io import ATTEMPTS_DIRNAME
    return Path(candidate_root) / ATTEMPTS_DIRNAME / str(attempt_id)


def _run_candidate(*, candidate_root, attempt_id, experiment_id, scope, revision,
                   source, clips_ctx, script, provider=None, plan_only=False) -> list:
    """候选运行：attempt 事务内合成全部声音单元并发布 manifest + accepted 指针."""
    from shared import enhancement_io as eio
    from tts.synthesis_job import (TtsCandidateContext, freeze_and_record,
                                   publish_attempt, synthesize_units)

    attempt = _candidate_attempt_dir(candidate_root, attempt_id)
    for name in (eio.MANIFEST_FILENAME, eio.EVENTS_FILENAME):
        if (attempt / name).exists():
            raise RuntimeError(
                "候选 attempt %s 已存在（%s）——拒绝覆盖既有 attempt；"
                "换 --attempt-id 或另给 --candidate-root" % (attempt_id, name))

    # 声音单元的 provider **必须**与真正拿去合成的 provider 一致：否则能力事实
    # （speaker 白名单/单次上限/是否支持 instruction）会取错一家，
    # 规划与实际请求分叉。
    from tts.service import provider_capabilities as _caps_of
    provider_name = (str(getattr(provider, "PROVIDER_NAME", "") or "")
                     or (_caps_of(provider).provider if provider is not None else "")
                     or tts_config.PROVIDER)
    units = build_speech_units(script, scope=scope, script_revision=revision,
                               provider_name=provider_name)
    ctx = TtsCandidateContext.create(
        candidate_root, attempt_id=attempt_id, experiment_id=experiment_id,
        scope=scope, script_revision=revision, source=source, clips=clips_ctx,
        plan_only=plan_only)
    with ctx:
        freeze_and_record(ctx, {
            "scope": scope, "script_revision": revision, "source": source,
            "provider": tts_config.PROVIDER,
            "units": [u.unit_id for u in units],
            "video_manifest": (str(clips_ctx.manifest_path)
                               if clips_ctx is not None and clips_ctx.manifest_path else None),
            "plan_only": bool(plan_only),
        })
        if plan_only:
            attempt.mkdir(parents=True, exist_ok=True)
            return []
        artifacts = synthesize_units(units, ctx, provider=provider)
        checks = None
        unresolved = [u.unit_id for u in units
                      if u.unit_id not in {a.unit_id for a in artifacts}]
        if unresolved:
            checks = [{"name": "units", "status": "failed",
                       "detail": "未产出: %s" % unresolved}]
        publish_attempt(ctx, artifacts, status="completed" if not unresolved else "partial",
                        checks=checks)
        return artifacts


def main(options=None):
    opts = options if options is not None else (_options or StageOptions())
    _logger.separator()
    _logger.info("  Step 3: TTS Audio Generation")
    _logger.info("  (Multi-voice support enabled)")
    _logger.separator()

    candidate_root = opts.candidate_root or candidate_root_from_env()
    plan_only = bool(opts.plan_only or plan_only_from_env())
    if plan_only and not candidate_root:
        raise ValueError("--plan-only 需要 --candidate-root（只规划不合成，产物落候选根）")

    # 检查前置步骤产物
    if not SCRIPT_FILE.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {SCRIPT_FILE}\n"
            f"请先运行 step01: python step01_script_gen/main.py"
        )

    if candidate_root is None:
        # 候选模式**不建**正式目录（隔离优先）
        CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
        AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load script
    script = load_script_module(SCRIPT_FILE)
    from shared.quality_gate import require_gate, verify_step01_commit_manifest
    require_gate(CHAPTER_DIR / "step01_quality.json", script, stage="step01",
                 required_report_type="quality_gate")
    # v6.103：三份 step01 产物必须属于同一个提交批次（见 shared/quality_gate）
    verify_step01_commit_manifest(CHAPTER_DIR, script, stage="step01",
                                  expected_chapter=CHAPTER_INDEX)
    shots = script.get("shots", [])
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None

    scope = current_scope(CHAPTER_INDEX)
    clips_ctx = None
    clips_path = (opts.video_candidates
                  or os.getenv("STEP03_VIDEO_CANDIDATES", "").strip())
    if clips_path:
        from step03_tts_audio.cli import ENV_VIDEO_CANDIDATES  # noqa: F401  (env 名单源)
        clips_ctx = load_candidate_clips(clips_path, expect_package="video")

    revision = script_revision_of(script)
    source = "accepted_script"
    if clips_ctx is not None:
        # 候选准入 + 身份核对（2026-10-03 复核 P1-7）：候选必须被 accepted 指针
        # **指名**，而且必须与**本次运行的章/稿**一致。旧写法直接继承候选自己声明的
        # scope/revision —— 那等于让另一章/旧稿的音频冒充本次正文的配音。
        import json as _json

        from shared.candidate_inputs import required_checks_for, require_accepted_manifest
        _raw = _json.loads(Path(clips_path).read_text(encoding="utf-8"))
        # 消费侧必需检查**按包**给：视频候选要过 media 轴，否则"检查未通过"的候选
        # 也能拿来配音（2026-10-04 复核 P1-2）。
        require_accepted_manifest(Path(clips_path), _raw,
                                  required_checks=required_checks_for("video"))

        from shared.run_scope import scope_identity

        want_scope = scope_identity(scope)
        cand_scope = scope_identity(clips_ctx.scope or {})
        if not cand_scope:
            raise RuntimeError(
                "视频候选 manifest 没有 scope：无法判断它属于哪一章，拒绝使用"
                "（不继承、不猜）")
        if cand_scope != want_scope:
            raise RuntimeError(
                "视频候选的 scope 与本次运行不符：候选 %s，本次 %s"
                "——上一章/上一批的候选不得配到本章正文上" % (cand_scope, want_scope))
        cand_rev = str(clips_ctx.script_revision or "")
        if not cand_rev:
            raise RuntimeError(
                "视频候选 manifest 没有 script_revision：无法判断它属于哪一稿，拒绝使用")
        if cand_rev != revision:
            raise RuntimeError(
                "视频候选的 script_revision 与当前稿不符：候选 %s，当前 %s"
                "——旧稿的视频不得配到新稿的配音上" % (cand_rev[:12], revision[:12]))
        source = clips_ctx.source or "video_candidates"

    plan = script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}

    # v6.73: model capability + actual clip audio streams determine the source.
    # 2026-10-03：素材解析改走**唯一 resolver**（Step03/04 与 hook 同规则）。
    from shared.audio_policy import resolve_audio_policy
    clip_inputs = _resolve_clip_inputs(hook, shots, scope, clips_ctx)
    actual_audio_present = probe_actual_audio(clip_inputs)
    # 图外旁白是正交的一维（复核第 7 轮）：视频模型不会念它（旁白是叙述文字，
    # step02 的 prompt 不把它当台词），所以**无论 native 还是 tts_mix** 都要有独立音轨。
    from step01_script_gen.plan_feasibility import plan_narration_entries
    from shared.audio_policy import require_narration_contract
    # **按计划自称的模式**核声音合同（主专题 §5）：新模式出现图外旁白 ⇒ **合同冲突**，
    # 退回规划者，不静默清空、也不"给它合成一条旁白轨"冒充合法无旁白。
    _narr_contract = require_narration_contract(plan)
    _narr_entries = plan_narration_entries(plan)
    policy = resolve_audio_policy(actual_audio_present=actual_audio_present,
                                  narration_present=bool(_narr_entries))
    forced = (os.getenv("STEP03_FORCE", "0").strip() == "1" or candidate_root is not None
              or plan_only)
    _logger.info(
        f"[AUDIO-POLICY] mode={_narr_contract['mode']} "
        f"model={policy.video_model or 'unknown'} "
        f"native_supported={policy.model_supports_audio} "
        f"actual_clip_audio={actual_audio_present} "
        f"tts_required={policy.tts_required} effective={policy.effective_merge_mode} "
        f"narration_tts={policy.narration_tts_required}（旁白 %d 条） "
        f"reason={policy.reason} contract={_narr_contract['reason']}" % len(_narr_entries)
    )
    if not policy.tts_required and not policy.narration_tts_required and not forced:
        _logger.info(
            "[SKIP] 视频模型原生发声且实际 clips 已有音频；跳过对白 TTS。"
            "需要备音轨请设 MERGE_AUDIO_MODE=tts_mix 或 STEP03_FORCE=1"
        )
        return

    _logger.info(f"Shots: {len(shots)}")
    _logger.info(f"Chapter: {CHAPTER_INDEX}, Book: {BOOK_INDEX}")
    if candidate_root is not None:
        _logger.info("[CANDIDATE] 候选根: %s（产物/日志/注册表全部隔离在此）" % candidate_root)

    # 检查已生成音频（仅作进度提示；M10 修复 2026-08-14：不能凭 mp3 存在就
    # 整体跳过——音色可能已变更，缓存由生成器逐镜校验并对变化的镜头自动重生成）
    existing_count = 0
    for shot in shots:
        idx = shot.get("index", 0)
        audio_path = audio_file(idx)
        if audio_path.exists() and audio_path.stat().st_size > 10240:
            existing_count += 1
    if candidate_root is None:
        if existing_count == len(shots) and existing_count > 0:
            _logger.info(f"[INFO] 发现 {existing_count} 个已生成音频，将按身份缓存逐镜复用/重生成")
        elif existing_count > 0:
            _logger.info(f"[INFO] 部分音频已存在 ({existing_count}/{len(shots)})，将补充生成缺失的")

    # M10 修复 2026-08-14：阻断式 input() 改为可配置确认——TTS_AUTO_CONFIRM=1
    # 或 stdin 非交互（CI/批处理重定向）时自动继续，不阻塞管线。
    # 候选/规划模式不做交互确认（plan-only 无费用；候选模式按 §11 的批处理形态走）。
    auto_confirm = os.getenv("TTS_AUTO_CONFIRM", "0").strip().lower() in ("1", "yes", "y", "true")
    if (not auto_confirm and sys.stdin.isatty() and candidate_root is None
            and not plan_only):
        try:
            answer = input("确认 TTS 生成? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _logger.info("已取消.")
            sys.exit(0)
        if answer not in ("y", "yes"):
            _logger.info("已取消.")
            sys.exit(0)

    # ---- 只规划：产出 SpeechUnits，不调用任何 provider（§T4） ----
    if plan_only:
        from tts.synthesis_job import write_speech_unit_plan
        units = build_speech_units(script, scope=scope, script_revision=revision)
        target = write_speech_unit_plan(Path(candidate_root) / "plan", units,
                                        scope=scope, script_revision=revision,
                                        provider=tts_config.PROVIDER, source="plan")
        _logger.info("[PLAN] 已写出 %d 个声音单元（未调用任何 provider）: %s"
                     % (len(units), target))
        return

    try:
        tts_config.validate()
        if candidate_root is not None:
            # ---- 候选运行：attempt 事务内合成 + 发布 manifest/accepted ----
            attempt_id = opts.attempt_id or attempt_id_from_env() or new_attempt_id()
            artifacts = _run_candidate(
                candidate_root=candidate_root, attempt_id=attempt_id,
                experiment_id=opts.experiment_id or "", scope=scope, revision=revision,
                source=source, clips_ctx=clips_ctx, script=script)
            produced = {a.unit_id for a in artifacts}
            units = build_speech_units(script, scope=scope, script_revision=revision)
            missing_d = [u.unit_id for u in units
                         if u.kind == "dialogue" and u.unit_id not in produced]
            missing_n = [u.unit_id for u in units
                         if u.kind == "narration" and u.unit_id not in produced]
            if missing_d or missing_n:
                _logger.error(
                    "[ERROR] 候选运行缺少音轨：对白 %s、旁白 %s——判定 step03 候选失败"
                    % (missing_d, missing_n))
                sys.exit(1)
            _logger.info("[OK] 候选 TTS 完成：%d 个声音单元（attempt=%s）"
                         % (len(artifacts), attempt_id))
            return

        voice_registry = get_registry()
        if policy.tts_required or forced:
            _logger.info("\n=== Generate per-shot TTS (dynamic voice) ===")
            # 初始化音色注册表（跨章节保持角色声音一致）
            gen_all_shot_tts(script, AUDIO_OUTPUT_DIR, voice_registry, use_async=True,
                             scope=scope, script_revision=revision)
        if policy.narration_tts_required:
            # 图外旁白：独立音轨（固定旁白音色，一条旁白一个文件）
            _logger.info("\n=== Generate off-screen narration TTS ===")
            gen_narration_tts(script, AUDIO_OUTPUT_DIR, scope=scope,
                              script_revision=revision, voice_registry=voice_registry)
    except Exception as e:
        _logger.error(f"[ERROR] {e}")
        sys.exit(1)

    # 2026-09-13 审计修复：单镜 TTS 失败此前被吞（返回 None 仅打印 [FAIL]），
    # step04 tts_mix 只记缺失数量——缺对白镜头会静默保留原声进成片。
    # 现在收尾核对：有台词的镜头必须存在有效音频，否则本阶段失败。
    audio_units = ([hook] if hook else []) + shots
    # 对白轨：判据与生成器单源——有声音条目走**结构化分流**（只有旧稿才回退标点
    # 启发式）。旧实现按「narration_segment 非空」要求音轨，于是每个纯叙述镜都被记成
    # "缺失"——要求了一个生成器**刻意不产出**的文件。
    from step01_script_gen.plan_feasibility import shot_needs_dialogue_track
    required = [s for s in audio_units
                if shot_needs_dialogue_track(s, plan)]
    missing = [
        s.get("index", 0) for s in required
        if not (audio_file(s.get("index", 0)).exists()
                and audio_file(s.get("index", 0)).stat().st_size > 10240)
    ] if (policy.tts_required or forced) else []
    if missing:
        _logger.error(
            f"[ERROR] {len(missing)}/{len(required)} 个有台词镜头缺少 TTS 音频"
            f"（shot index: {missing}）——tts_mix 模式会缺对白，判定 step03 失败"
        )
        sys.exit(1)
    # 旁白轨：计划里每一条旁白都必须有音轨（缺 = 成片没人念）
    _narr_missing = [
        e["index"] for e in _narr_entries
        if not (narration_audio_file(e["index"]).exists()
                and narration_audio_file(e["index"]).stat().st_size > 10240)
    ] if policy.narration_tts_required else []
    if _narr_missing:
        _logger.error(
            f"[ERROR] {len(_narr_missing)} 条图外旁白缺少 TTS 音轨（N{_narr_missing}）"
            "——旁白轨缺文件等于成片没人念，判定 step03 失败"
        )
        sys.exit(1)
    _logger.info(
        f"[OK] TTS 完成：对白轨 {len(required)} 镜、旁白轨 {len(_narr_entries)} 条音频齐全")


if __name__ == "__main__":
    main()
