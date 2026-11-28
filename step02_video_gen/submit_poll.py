"""step02_video_gen.submit_poll - submit and poll module"""
import sys
from pathlib import Path
import os
import asyncio
import shutil
import random
from collections import defaultdict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


async def gen_shot_frames(*args, **kwargs):
    # 只在实际生成帧时加载重依赖；加载失败照常抛出，不以 None 冒充能力。
    from shared.vimax_bridge import gen_shot_frames as generate
    return await generate(*args, **kwargs)


from shared.error_fmt import fmt_err as _fmt_err, parse_retry_after
from step02_video_gen.continuity import ContinuityTracker, MODE_B_START_INDEX  # noqa: F401
# 上面这行现为**纯历史保留**：旧的 `generate_alls()` 双模调度（Mode A/B + 尾帧接续）
# 已于 2026-10 复核作为死代码删除，现行 `step02b_video_gen.gen_all_videos` 不做 Mode A/B
# 调度、不提取尾帧，`ContinuityTracker` 因此不再被实例化。import 保留是为了不改变
# 本模块的对外符号面（原有 `from step02_video_gen.submit_poll import …` 的调用方不受影响）。
from shared.config import VIDEO_CONCURRENCY, VIDEO_MAX_RETRIES  # 并发/重试单源: shared/config.py
from shared.paths import SHOT_VIDEO_FILENAME, FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
from shared.constants import VIDEO_SUBMIT_RETRIES, FRAME_COPY_RETRIES
from shared.video_prompt_system import SystemCameraError
# V 包（计划 §V1/§V2）：prepared 提交的事实与分类单源
from _video_gen.models import (
    SUBMIT_RATE_LIMITED,
    SUBMIT_UNKNOWN,
    ExecutionContext,
    VideoCapabilityError,
    VideoSubmissionError,
)

# Smart polling config: adaptive progressive backoff + jitter (default 30s initial delay, 20s->60s interval)
POLL_BASE_INTERVAL = int(os.getenv("POLL_BASE_INTERVAL", "20"))    # Initial poll interval (seconds)
POLL_MAX_INTERVAL = int(os.getenv("POLL_MAX_INTERVAL", "60"))      # Max poll interval (seconds)
POLL_EXPONENTIAL_FACTOR = float(os.getenv("POLL_EXPONENTIAL_FACTOR", "1.5"))  # Backoff multiplier
POLL_JITTER = int(os.getenv("POLL_JITTER", "5"))                   # Jitter range +/- seconds
INITIAL_POLL_DELAY = int(os.getenv("INITIAL_POLL_DELAY", "30"))    # Wait after submit before first poll (30s)

# 视频提交并发：在 _submit_video 入口处用 Semaphore 控制，与 _video_gen provider
# 的 KeyScheduler（每 Key 独立冷却）配合实现 Nx 吞吐。空闲 Key 越多，并发越多。
# 不再使用全局 RateLimiter 单令牌串行闸。（值单源 shared/config.py，默认 4）

# A6 视频产物质检（08-11 修复）：_poll_video 此前成功判定=文件>20KB，时长/内容
# 零校验，坏视频直接进 step04。现增加两级 QC：
#   1. 时长校验（always-on，ffprobe 探时长）：实际时长 vs 期望 duration_sec 偏差
#      > 阈值 → 判定坏视频（短截/黑场/动作没播完）。
#   2. 视觉质检（VIDEO_QC_ENABLED=1 时，抽首/中/尾帧走 image_qc 的 OpenCode 视觉通道）：检测人物
#      消失/字幕/幻觉。默认 0（视觉质检昂贵，逐镜跑会拖慢全片），时长校验兜底。
VIDEO_QC_ENABLED = os.getenv("VIDEO_QC_ENABLED", "0").strip() == "1"
# 时长偏差阈值：实际时长 vs 期望时长的相对偏差（比例）或绝对偏差（秒）任一超限即坏
VIDEO_DURATION_DEV_RATIO = float(os.getenv("VIDEO_DURATION_DEV_RATIO", "0.5"))
VIDEO_DURATION_DEV_SEC = float(os.getenv("VIDEO_DURATION_DEV_SEC", "1.5"))

# Global failure storage
_failed_videos = defaultdict(list)

# v6.35（08-15，P18 遗留）：QC 失败自动重提交次数。固定 seed 后同 prompt 同参考
# 重提交结果可复现（不再抽卡）。QC 失败 → 删坏视频 → 相同 req 重提交（可配 0=关闭）。
VIDEO_QC_RESUBMIT = int(os.getenv("VIDEO_QC_RESUBMIT", "2"))


# ---------------------------------------------------------------------------
# S07（2026-11-28）：拆分后兼容 re-export（父模块名字/调用点保持不变）
# ---------------------------------------------------------------------------
from step02_video_gen.parts.frozen import (  # noqa: F401
    FROZEN_SNAPSHOT_FILENAME, PreparedFacts, _chapter_index_safe, _get_shot_hash,
    _is_recoverable, _prepare_error, _read_qc_retry_state, _record_qc_failure,
    _scope_identity_safe, _script_revision, _service_supports_prepared,
    build_frozen_for_shot, build_prepared_facts, cache_decision,
    frozen_snapshot_path, generation_requirements, read_frozen_snapshot,
    record_submitted_duration, write_frozen_snapshot,
)
from step02_video_gen.parts.qc import (  # noqa: F401
    _VideoProductQc, _check_keyframe_hallucination, _first_frame_of, _qc_coverage,
    _qc_video_product, _qc_video_product_result, _record_candidate_qc,
    _report_to_dict, accept_candidate_video,
)
from step02_video_gen.parts.recovery import (  # noqa: F401
    _ResumeContext, _chapter_scope_str, _emit_video_ledger, _record_content_policy_revision,
    _request_from_frozen, _scope_matches, _work_id_str, attempt_task_id,
    find_recoverable_attempt, resume_candidate_attempt,
)
from step02_video_gen.parts.failures import (  # noqa: F401
    _PROVIDER_FAILURE_MARKERS, _classify_poll_failure, _poll_video_production,
    repair_missing_clip, submit_failure_code,
)


def _probe_video_duration(video_path: Path) -> float:
    """用 ffprobe 探测视频实际时长（秒），失败返回 0."""
    try:
        from step04_merge.stretch import get_media_duration
        return get_media_duration(video_path)
    except Exception:
        return 0.0




















# ---------------------------------------------------------------------------
# 冻结请求快照与缓存判据（计划 §V2 改动 4：命中缓存先核冻结请求关系与产物检查）
# ---------------------------------------------------------------------------

































def _candidate_attempt(shot_dir: Path, candidate_context):
    return Path(getattr(candidate_context, "attempt_dir", shot_dir))


























def _trace_video_event(attempt, kind, **fields):
    if attempt is None:
        return
    from shared import enhancement_io as eio
    with eio.AttemptLock(Path(attempt) / eio.LOCK_DIRNAME, purpose=kind):
        eio.append_event(Path(attempt), kind, **fields)


#: 远端明确失败（没有产出视频）的报错前缀：provider 在 failed/expired 与轮询超时时
#: 分别写「生成失败: …」/「轮询超时 …」（_video_gen/volcengine_provider.py），
#: 下载阶段失败则写「下载失败: …」（_video_gen/service.py）。








async def _poll_video(
    video_adapter, semaphore: asyncio.Semaphore,
    req, task_id: str, shot_idx: int,
    shot_dir: Path, video_path: Path, max_retries: int,
    shot_hash: str = "",
    qc_resubmit_limit: int = 0,
    shot: dict | None = None,
    characters: list | None = None,
    candidate_context=None,
    frozen_request=None,
    trace_attempt=None,
) -> bool:
    """Background poll with exponential backoff + jitter - reduces API queries.

    ``candidate_context`` 非空 ⇒ 候选模式（计划 §V2 改动 3/7）：
    - **不**写生产 ``clips/clip_N.mp4``、**不**写生产 shot 目录的 ``prompt_hash.txt``
      （两者都改写到 attempt 内）；
    - 内容 QC **不再自动重交**（旧 ``VIDEO_QC_RESUBMIT`` 仅报告 deprecated_ignored，
      重生成属新费用，需人工确认）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir, execution_log_file
    if candidate_context is not None:
        # 候选模式：日志落 attempt（§F1/§F4——不得把实验日志写进正式 step02.log）
        logger = get_logger(execution_log_file("step02", context=candidate_context),
                            console_level="INFO")
    else:
        logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")
    if candidate_context is not None and qc_resubmit_limit:
        logger.verbose(
            f"  [Shot {shot_idx}] VIDEO_QC_RESUBMIT={qc_resubmit_limit} 在候选模式下 "
            f"deprecated_ignored：候选入口不自动重交（重生成需人工费用确认）")
    qc_resubmit_left = 0 if candidate_context is not None else max(0, qc_resubmit_limit)
    def _emit_poll_ledger(outcome: str, error_code: str = "", attempt_dir: Path | None = None) -> None:
        fz_dict = frozen_request.to_dict() if frozen_request is not None else {}
        fz_src = dict(fz_dict.get("source") or {})
        att = Path(attempt_dir) if attempt_dir is not None else (
            trace_attempt if trace_attempt is not None else (
                _candidate_attempt(shot_dir, candidate_context) if candidate_context is not None else None
            )
        )
        att_inputs = {}
        posts_cnt = None
        if att is not None:
            try:
                from shared import enhancement_io as _eio_l
                att_inputs = _eio_l.read_inputs(att) or {}
                posts_cnt = attempt_task_id(att).get("submit_posts")
            except Exception:
                att_inputs = {}
        _emit_video_ledger(
            scope=dict(att_inputs.get("scope") or fz_src.get("scope") or (
                getattr(candidate_context, "scope", None) or {}
            )),
            script_revision=str(att_inputs.get("script_revision") or fz_src.get("script_revision") or (
                getattr(candidate_context, "script_revision", "") or ""
            )),
            shot_index=shot_idx,
            provider=str(att_inputs.get("provider") or fz_dict.get("provider") or ""),
            model_id=str(att_inputs.get("model_id") or fz_dict.get("model_id") or ""),
            media_policy=str(fz_src.get("media_policy") or (fz_dict.get("parameters") or {}).get("media_mode") or ""),
            request_key=str(att_inputs.get("request_key") or getattr(frozen_request, "request_key", "") or ""),
            generation_id=str(att_inputs.get("generation_id") or ""),
            task_id=str(task_id or ""),
            attempt_id=att.name if att is not None else "",
            action_type="recover" if att_inputs.get("parent_attempt_id") else "generate",
            outcome=outcome,
            error_code=error_code,
            recovered=bool(att_inputs.get("parent_attempt_id")),
            submit_posts=posts_cnt,
            context=candidate_context,
            request_id=str(att_inputs.get("generation_id") or task_id or ""),
        )

    def _transition_candidate_failure(target_state: str, kind: str, **fields) -> None:
        if candidate_context is None:
            return
        from shared import video_attempts as _va
        try:
            _cand = _candidate_attempt(shot_dir, candidate_context)
            cur = _va.current_state(_cand)
            if cur == _va.STATE_SUBMITTED:
                _va.transition(_cand, _va.STATE_RUNNING, kind="poll_started", task_id=task_id)
                cur = _va.STATE_RUNNING
            if cur == _va.STATE_RUNNING or target_state in _va.TRANSITIONS and cur in _va.TRANSITIONS[target_state]:
                _va.transition(_cand, target_state, kind=kind, task_id=task_id, **fields)
        except Exception:
            pass

    async with semaphore:
        # 获取提交时绑定的 key 索引（计划 §V2：只能走 provider 的**公开**接口；
        # 旧写法读 adapter 私有 `_task_keys`（该属性不存在）被 except 吞掉后恒回退
        # key-1——多 key 通道会用错 key 查不到任务。不可解析时如实标注，不假造。
        _binding: dict = {}
        try:
            _get_binding = getattr(video_adapter, "get_task_binding", None)
            _binding = _get_binding(task_id) if callable(_get_binding) else {}
        except Exception as _exc:
            _binding = {"status": "binding_unavailable", "error": str(_exc)[:120]}
        if isinstance(_binding, dict) and _binding.get("status") == "ok":
            key_idx = int(_binding.get("key_slot") or 0)
            key_display = f"key-{key_idx + 1}"
        else:
            try:
                key_idx = int(video_adapter.get_key_index())
            except Exception:
                key_idx = 0
            key_display = f"key-unbound(current={key_idx + 1})"
            logger.verbose(
                f"  [Shot {shot_idx}] 任务凭据绑定不可解析（binding_unavailable）——"
                f"查询使用当前 key，**不得**据此宣称跨重启可恢复")
        if candidate_context is not None:
            from shared import enhancement_io as _eio
            from shared import video_attempts as _va
            try:
                _cand_att = _candidate_attempt(shot_dir, candidate_context)
                _eio.append_event(_cand_att, "task_binding", binding=_binding, task_id=task_id)
                if _va.current_state(_cand_att) == _va.STATE_SUBMITTED:
                    _va.transition(_cand_att, _va.STATE_RUNNING, kind="poll_started", task_id=task_id)
            except Exception:
                pass

        _trace_video_event(trace_attempt, "task_binding", task_id=task_id, binding=_binding)

        # Wait INITIAL_POLL_DELAY seconds after submit before first query (respect 1 RPM limit)
        logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Task submitted, waiting {INITIAL_POLL_DELAY}s before polling...")
        await asyncio.sleep(INITIAL_POLL_DELAY)

        # 首次轮询前不再按旧文件短路：本任务刚提交，必须轮询新任务结果。
        # 若此处因历史遗留的旧 video.mp4 就 return True，--force/改 prompt 重生成会拿回旧视频、
        # 新任务白跑且 prompt_hash 不更新（code-audit 08-01 #1）。旧文件由下载成功后被新结果覆盖。
        # Exponential backoff state
        poll_interval = POLL_BASE_INTERVAL
        consecutive_failures = 0
        override_interval = None  # 429 时用服务端返回的剩余时间 + 0.5s（一次性生效）
        first_poll = True  # INITIAL_POLL_DELAY 已等待，首次轮询不再重复 sleep（修复双重等待）

        while True:
            try:
                # Calculate next poll time: 429 override > exponential backoff + jitter
                # 首次轮询直接查询（INITIAL_POLL_DELAY 已等过），避免 180s+180s 双重等待
                if first_poll:
                    first_poll = False
                elif override_interval is not None:
                    actual_interval = override_interval
                    override_interval = None
                    await asyncio.sleep(actual_interval)
                else:
                    jitter = random.uniform(-POLL_JITTER, POLL_JITTER)
                    actual_interval = max(1, poll_interval + jitter)
                    await asyncio.sleep(actual_interval)

                # 不再按旧文件短路：本任务结果必须经 fetch_result 拿到，成功后覆盖旧 video.mp4
                _trace_video_event(trace_attempt, "fetch_started", task_id=task_id)
                result = await video_adapter.fetch_result(req, task_id)
                _trace_video_event(trace_attempt, "fetch_result", task_id=task_id,
                                   success=result.success, save_path=str(result.save_path or ""),
                                   error=getattr(result, "error", None))
                if result.success and result.save_path:
                    shot_dir.mkdir(parents=True, exist_ok=True)

                    # 视频已下载成功；copy 失败只重试 copy，绝不重新轮询（否则会重复下载）
                    # 注意：bailian _download 直接存到 shots/{idx}/video.mp4，与 video_path 同路径，
                    # 此时 copy2 会抛 SameFileError，须跳过复制（文件已在目标位置）。
                    copy_ok = False
                    try:
                        same_file = Path(result.save_path).resolve() == video_path.resolve()
                    except OSError:
                        same_file = False
                    if same_file:
                        copy_ok = True
                    else:
                        for copy_attempt in range(FRAME_COPY_RETRIES):
                            try:
                                shutil.copy2(result.save_path, str(video_path))
                                copy_ok = True
                                break
                            except OSError as copy_err:
                                if "WinError 32" in str(copy_err) and copy_attempt < 9:
                                    wait = 1.0 + copy_attempt  # 递增等待 1s~10s
                                    logger.verbose(f"  [Shot {shot_idx}] File locked, retrying copy in {wait:.0f}s ({copy_attempt + 1}/10)...")
                                    await asyncio.sleep(wait)
                                else:
                                    logger.verbose(f"  [Shot {shot_idx}] Copy failed after {copy_attempt} retries: {copy_err}")
                                    break

                    if not copy_ok:
                        _failed_videos[shot_idx].append({"attempt": -1, "error": "文件被占用，copy 失败", "task_id": task_id})
                        return False

                    if trace_attempt is not None:
                        from shared import video_attempts as va
                        va.transition(trace_attempt, va.STATE_DOWNLOADED, task_id=task_id,
                                      path=str(video_path), bytes=video_path.stat().st_size)

                    # A6 修复（08-11）：视频产物接 QC——此前成功判定=文件>20KB，
                    # 时长/内容零校验，坏视频直接进 step04。现做两级 QC：
                    #   1. 时长校验（ffprobe，always-on）：实际 vs 期望 duration 偏差
                    #      超限 → 短截/黑场/动作没播完，删视频标记失败重生成。
                    #   2. 视觉质检（VIDEO_QC_ENABLED=1 时抽帧走 image_qc 的 OpenCode 视觉通道）。
                    # QC 失败不写哈希（下次缓存检测不会命中），返回 False 走失败重试。
                    # 期望时长取**冻结后的实际值**（prepare 可能改过时长）：否则首次
                    # 提交按原请求、恢复按冻结值，同一产物两套裁决（复核 P1-4 之二）
                    _expected_dur = float(
                        (getattr(frozen_request, "parameters", None) or {}).get("duration")
                        or getattr(req, "duration", 0) or 0)
                    _qc = _qc_video_product_result(
                        video_path, shot_idx, _expected_dur, shot=shot, characters=characters,
                        log_path=(execution_log_file("step02", context=candidate_context)
                                  if candidate_context is not None else None))
                    if trace_attempt is not None:
                        va.transition(trace_attempt, va.STATE_CHECKED, kind="video_check",
                                      task_id=task_id, qc=_qc.to_dict())
                        if _qc.status == "failed":
                            va.transition(trace_attempt, va.STATE_REJECTED, reason=_qc.reason)
                        elif _qc.status == "soft_allowed":
                            va.transition(trace_attempt, va.STATE_UNVERIFIED, reason=_qc.reason)
                    if _qc.status == "failed":
                        # 计划 §V2 改动 3：**删除内容 QC 自动重交动作**。
                        # 重生成属新的付费调用，必须走人工费用确认（候选模式下由
                        # 上层按 decide() 的 revise_inputs 建议处理）。
                        _failed_videos[shot_idx].append({
                            "attempt": -1,
                            "error": f"视频 QC 未通过（{_qc.reason or '内容/时长'}）：不自动重交",
                            "task_id": task_id,
                        })
                        if candidate_context is not None:
                            from shared import video_attempts as _va
                            try:
                                _attempt = _candidate_attempt(shot_dir, candidate_context)
                                _va.transition(_attempt, _va.STATE_DOWNLOADED,
                                               kind="downloaded", task_id=task_id)
                                _verdict = _record_candidate_qc(
                                    shot_dir, candidate_context, qc=_qc, shot_idx=shot_idx,
                                    shot_hash=shot_hash, frozen_request=frozen_request,
                                    video_path=video_path)
                                logger.verbose(
                                    f"  [Shot {shot_idx}] 候选 QC 裁决: {_verdict['kind']}"
                                    f"（保留产物与原错误，未自动重交）")
                            except Exception as _exc:  # noqa: BLE001 - 记录失败不吞：写事件并继续
                                logger.warn(f"  [Shot {shot_idx}] 候选 QC 裁决记录失败: {_exc}")
                            # 候选模式的坏片**保留**（rejected 保留供修订），只删生产哈希
                            _emit_poll_ledger("rejected", "qc_failed", attempt_dir=_candidate_attempt(shot_dir, candidate_context))
                            return False
                        # 先持久化 attempt；失败保留坏片与错误，不静默回到旧 seed。
                        try:
                            (shot_dir / "prompt_hash.txt").unlink(missing_ok=True)
                            _record_qc_failure(shot_dir, str(task_id))
                        except (OSError, ValueError) as exc:
                            logger.warn(f"  [Shot {shot_idx}] QC 重抽状态保存失败，保留视频: {exc}")
                            _failed_videos[shot_idx].append({"attempt": -1, "task_id": task_id,
                                                            "error": f"qc_retry_state: {exc}"})
                            _emit_poll_ledger("rejected", "qc_retry_state_failed")
                            return False
                        if trace_attempt is not None:
                            from shared import enhancement_io as eio
                            rejected = Path(trace_attempt) / "rejected.mp4"
                            shutil.copy2(video_path, rejected)
                            _trace_video_event(trace_attempt, "rejected_artifact",
                                               artifact=eio.artifact_record(rejected, role="rejected_video"))
                        try:
                            video_path.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warn(f"  [Shot {shot_idx}] 坏片清理失败: {exc}")
                        _emit_poll_ledger("rejected", "qc_failed")
                        return False

                    if candidate_context is not None:
                        # 候选模式：先记 downloaded（lifecycle 投影），再走裁决 → 采纳事务
                        from shared import video_attempts as _va
                        try:
                            _va.transition(_candidate_attempt(shot_dir, candidate_context),
                                           _va.STATE_DOWNLOADED, kind="downloaded",
                                           task_id=task_id, bytes=video_path.stat().st_size)
                        except Exception as _exc:  # noqa: BLE001
                            logger.warn(f"  [Shot {shot_idx}] lifecycle downloaded 记录失败: {_exc}")

                    # 写入哈希，供下次运行的缓存检测使用（与 _get_shot_hash 同一算法）
                    # 候选模式：shot_dir 已在 attempt 内，绝不写生产 shot 目录。
                    if shot_hash:
                        (shot_dir / "prompt_hash.txt").write_text(shot_hash, encoding="utf-8")
                    # 保留已通过 attempt 的 seed 序号，缓存重建与恢复仍使用同一请求身份。
                    if frozen_request is not None:
                        try:
                            write_frozen_snapshot(shot_dir, frozen_request)
                        except OSError as _exc:  # 快照写不了 ⇒ 下次按 legacy 判据，不阻断
                            logger.warn(f"  [Shot {shot_idx}] 冻结请求快照写入失败: {_exc}")

                    if candidate_context is None:
                        # 生产模式：同步复制到 clips/（step04 合并用；缓存检测用）。
                        # 失败必须进入明确出口（计划 §V2 改动 7：不许静默 pass）。
                        from shared.paths import clip_file
                        _clip_dst = clip_file(shot_idx)
                        _clip_dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(str(video_path), str(_clip_dst))
                        except OSError as _exc:
                            _failed_videos[shot_idx].append({
                                "attempt": -1,
                                "error": f"io_failed: clips 同步失败（{_exc}）——"
                                         f"step04 将读不到本镜，需恢复/重放",
                                "task_id": task_id,
                            })
                            logger.warn(
                                f"  [Shot {shot_idx}] clips/ 同步失败（不再静默忽略）: {_exc}")
                            _emit_poll_ledger("io_failed", "clips_copy_failed")
                            return False
                        # 生产 attempt 的成功终态必须闭合（R2）：此前只在候选分支
                        # 迁 ACCEPTED，生产分支停在 checked——不在 TERMINAL_STATES 里，
                        # 事后统计无法区分"成功"与"仍在跑"。
                        if trace_attempt is not None:
                            va.transition(trace_attempt, va.STATE_ACCEPTED, kind="accepted",
                                          task_id=task_id, clip_path=str(_clip_dst))
                        _emit_poll_ledger("accepted")
                    else:
                        # 候选模式：副本进 attempt（生产 clips/ 一个字节都不动），
                        # 随后走**不可变 manifest → accepted 读回**的采纳事务。
                        from shared import enhancement_io as _eio
                        from shared import video_attempts as _va
                        _dst = Path(getattr(candidate_context, "outputs_dir",
                                            Path(video_path).parent)) / f"clip_{shot_idx}.mp4"
                        try:
                            _dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(video_path), str(_dst))
                        except OSError as _exc:
                            _eio.append_event(Path(getattr(candidate_context, "attempt_dir", shot_dir)),
                                              "io_failed", error=f"candidate clip copy: {_exc}")
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"io_failed: 候选副本写入失败（{_exc}）",
                                "task_id": task_id, "code": "io_failed",
                            })
                            _emit_poll_ledger("io_failed", "candidate_copy_failed")
                            return False

                        _attempt = _candidate_attempt(shot_dir, candidate_context)
                        _verdict = _record_candidate_qc(
                            shot_dir, candidate_context, qc=_qc, shot_idx=shot_idx,
                            shot_hash=shot_hash, frozen_request=frozen_request,
                            video_path=video_path)
                        if _verdict["kind"] != "accept":
                            # 语义/媒体轴未通过 ⇒ 停在这一层（保留产物与原错误，零重生成）
                            _failed_videos[shot_idx].append({
                                "attempt": -1,
                                "error": f"candidate 未采纳（{_verdict['kind']}）：保留待修订",
                                "task_id": task_id, "decision": _verdict["kind"],
                            })
                            _emit_poll_ledger(_verdict["kind"], _verdict["kind"], attempt_dir=_attempt)
                            return False
                        try:
                            _accepted = accept_candidate_video(
                                candidate_context, video_path=video_path,
                                frozen_request=frozen_request, clip_copy=_dst,
                                checks=(list(_qc.checks or []) + _verdict["checks_report"]),
                                coverage=_verdict["coverage"])
                            _va.transition(_attempt, _va.STATE_ACCEPTED, kind="accepted",
                                           manifest_sha256=_accepted["manifest"]["manifest_sha256"])
                            _emit_poll_ledger("accepted", attempt_dir=_attempt)
                        except _eio.CommitUnconfirmedError as _exc:
                            # 替换后读回失败：不得声称旧值仍在，也不盲目回滚
                            _va.transition(_attempt, _va.STATE_COMMIT_UNCONFIRMED,
                                           kind="commit_unconfirmed", error=str(_exc)[:500])
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"commit_unconfirmed: {_exc}",
                                "task_id": task_id, "code": "commit_unconfirmed"})
                            _emit_poll_ledger("commit_unconfirmed", "commit_unconfirmed", attempt_dir=_attempt)
                            return False
                        except Exception as _exc:  # noqa: BLE001 - 验收/提交失败如实返回
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"accept_failed: {_exc}",
                                "task_id": task_id,
                                "code": getattr(_exc, "code", "") or "candidate_rejected"})
                            _emit_poll_ledger("rejected", getattr(_exc, "code", "") or "candidate_rejected", attempt_dir=_attempt)
                            return False

                    return True
                else:
                    err = result.error or "unknown"
                    # 429 triggers longer backoff
                    if _is_recoverable(err):
                        consecutive_failures += 1
                        if consecutive_failures > max_retries:
                            _failed_videos[shot_idx].append({"attempt": -1, "error": f"连续失败 {consecutive_failures} 次，超过上限 {max_retries}", "task_id": task_id})
                            from shared import video_attempts as _va
                            _transition_candidate_failure(_va.STATE_IO_FAILED, "poll_retries_exhausted", error=str(err)[:300])
                            _emit_poll_ledger("io_failed", "poll_retries_exhausted")
                            return False
                        if "429" in err or "rate" in err.lower():
                            wait = parse_retry_after(err)
                            if wait is not None:
                                override_interval = wait + 0.5
                                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err)}")
                            else:
                                poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited (interval {poll_interval:.0f}s): {_fmt_err(err)}")
                        else:
                            poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Processing (interval {poll_interval:.0f}s, failures {consecutive_failures})")
                        continue
                    # R1：远端明确失败（failed/expired）与下载失败必须分开记账，
                    # 否则 lifecycle 里全是 io_failed + unverified，无法归因。
                    _fail_state, _remote_status = _classify_poll_failure(err)
                    if trace_attempt is not None:
                        va.transition(trace_attempt, _fail_state, kind="poll_failed",
                                      task_id=task_id, remote_status=_remote_status,
                                      error=str(err)[:500])
                    _transition_candidate_failure(_fail_state, "poll_failed", remote_status=_remote_status, error=str(err)[:500])
                    _failed_videos[shot_idx].append({"attempt": -1, "error": err, "task_id": task_id})
                    _emit_poll_ledger(_fail_state, _remote_status or "poll_failed")
                    # 2026-09-12 用户要求：终态失败打印完整报错（不走 fmt_err 截断）
                    logger.verbose(f"  [Shot {shot_idx}] [{key_display}] 最终失败完整报错: {err}")
                    return False
            except Exception as e:
                err_str = str(e)
                # 429 triggers longer backoff
                if _is_recoverable(err_str):
                    consecutive_failures += 1
                    if consecutive_failures > max_retries:
                        _failed_videos[shot_idx].append({"attempt": -1, "error": f"连续失败 {consecutive_failures} 次，超过上限 {max_retries}", "task_id": task_id})
                        from shared import video_attempts as _va
                        _transition_candidate_failure(_va.STATE_IO_FAILED, "poll_retries_exhausted", error=err_str[:300])
                        _emit_poll_ledger("io_failed", "poll_retries_exhausted")
                        return False
                    if "429" in err_str or "rate" in err_str.lower():
                        wait = parse_retry_after(err_str)
                        if wait is not None:
                            override_interval = wait + 0.5
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err_str)}")
                        else:
                            poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    else:
                        poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                        logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    continue
                _fail_state, _remote_status = _classify_poll_failure(err_str)
                _transition_candidate_failure(_fail_state, "poll_exception", remote_status=_remote_status, error=err_str[:500])
                _failed_videos[shot_idx].append({"attempt": -1, "error": err_str, "task_id": task_id})
                _emit_poll_ledger(_fail_state, _remote_status or "poll_exception")
                # 2026-09-12 用户要求：终态失败打印完整报错（不走 fmt_err 截断）
                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] 最终失败完整报错: {err_str}")
                return False


async def _submit_video_candidate(*, shot: dict, shot_dir: Path, video_path: Path,
                                  frame_paths: list, shot_hash: str, video_adapter,
                                  poll_semaphore, script: dict, idx: int, prompt: str,
                                  characters: list, continuity_ref_image, mode: str,
                                  candidate_context, logger, make_request, production_output=False,
                                  execution_requirements=None, prepared_facts=None,
                                  media_policy=None):
    """候选模式提交：**一次尝试 = 至多一个生成 POST**（计划 §V1/§V2 改动 1/2/6）。

    与旧路的区别（本函数是候选模式的**唯一**提交入口）：
    - 先 ``provider.prepare_request``（**无提交**）冻结实际生效文本/参数/参考，
      再冻结 FrozenVideoRequest 并做结构预检——预检不过则**零 POST**；
    - 提交走 ``provider.create_prepared_task``：materialize + **一次** POST；
    - 分类结果（不重提交、不在 except 内改 prompt 再发）：
      成功建任务 ⇒ submitted + 轮询；429 ⇒ rate_limited；网络/5xx/超时 ⇒
      submission_unknown（晚到 task_id 由提交线程落盘，恢复时按同一 ID 查询）；
      明确 4xx ⇒ provider_failed；content-policy 只产出**新候选计划**建议；
    - 任何分支都不写生产 ``clips/`` 或生产 ``prompt_hash.txt``。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va
    from step02_video_gen import request_plan as rp

    attempt = Path(getattr(candidate_context, "attempt_dir", shot_dir))
    package_root = Path(getattr(candidate_context, "package_root", attempt.parent.parent))
    attempt_id = attempt.name
    scope = dict(getattr(candidate_context, "scope", None) or {})
    script_revision = str(getattr(candidate_context, "script_revision", "") or "")

    def _fail(error: str, *, code: str = "candidate_submit_failed"):
        _failed_videos[idx].append({"attempt": -1, "error": error, "code": code})
        return None

    # §F2「request_id/attempt_id 稳定**不复用**」：终局的 attempt 不许被下一轮覆盖
    # （覆盖会改写既有证据/采纳事实源）。要重新生成请用新的 attempt_id。
    if attempt.exists():
        _state = va.current_state(attempt)
        if _state in va.TERMINAL_STATES:
            eio.append_event(attempt, "attempt_reuse_rejected", state=_state,
                             code="attempt_identity_conflict")
            return _fail(f"attempt_identity_conflict: attempt {attempt_id} 已终局（{_state}），"
                         f"不得复用（请用新的 attempt_id 重新生成）",
                         code="attempt_identity_conflict")

    if prepared_facts is not None and prepared_facts.status in ("blocked", "unsupported"):
        # S02-C1：缓存路径已经确定失败/不支持——在这里单次终局，禁止二次 prepare。
        _err = dict(prepared_facts.error or {})
        _code = str(_err.get("code") or "preflight_blocked")
        _msg = str(_err.get("message") or _code)
        va.open_attempt(package_root, attempt_id,
                        request_key=(getattr(prepared_facts.frozen, "request_key", "") or ""),
                        scope=scope, script_revision=script_revision,
                        provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
        eio.append_event(attempt, "preflight_blocked", code=_code, message=_msg,
                         stage=_err.get("stage"), field_path=_err.get("field_path"))
        return _fail(f"{_code}: {_msg}", code=_code)

    if prepared_facts is not None and prepared_facts.status == "prepared":
        # S02-C1：复用缓存路径已经完成的 prepare 事实；不再调用 prepare_request()。
        req = prepared_facts.req
        prepared = prepared_facts.prepared
        frozen = prepared_facts.frozen
        svc = _video_service_for(video_adapter)
        if not production_output:
            req.candidate_path = str(video_path)
            req.execution_context = ExecutionContext.from_candidate_context(
                candidate_context, candidate_path=video_path)
        try:
            record_submitted_duration(shot, prepared)
        except VideoCapabilityError as exc:
            va.open_attempt(package_root, attempt_id, request_key=frozen.request_key,
                            scope=scope, script_revision=script_revision,
                            provider=prepared.provider, model_id=prepared.model_id)
            eio.append_event(attempt, "preflight_blocked", code=exc.code,
                             message=exc.message,
                             field_path=exc.fields.get("field_path"))
            return _fail(f"preflight_blocked({exc.code}): {exc.message}", code=exc.code)
        if execution_requirements is not None:
            from step02_video_gen.execution_requirements import validate_requirements
            _exec_problems = validate_requirements(frozen, execution_requirements)
            if _exec_problems:
                va.open_attempt(package_root, attempt_id, request_key=frozen.request_key,
                                scope=scope, script_revision=script_revision,
                                provider=prepared.provider, model_id=prepared.model_id)
                eio.append_event(attempt, "execution_requirements_blocked",
                                 problems=_exec_problems)
                return _fail(f"execution_requirements: {_exec_problems[:2]}",
                             code=(_exec_problems[0].get("code")
                                   or "execution_requirements_blocked"))
        prepared.request_key = frozen.request_key
    else:
        try:
            req = make_request(video_adapter, prompt, frame_paths, shot, characters=characters,
                               continuity_ref_image=continuity_ref_image, mode=mode,
                               ref_sheet_dir=attempt / "refs",
                               media_policy=media_policy,
                               **({"retry_attempt": _read_qc_retry_state(shot_dir)["count"]}
                                  if production_output else {}))
        except VideoCapabilityError as exc:
            va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                            script_revision=script_revision,
                            provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
            eio.append_event(attempt, "preflight_blocked", code=exc.code,
                             message=exc.message, field_path=exc.fields.get("field_path"))
            return _fail(f"preflight_blocked({exc.code}): {exc.message}", code=exc.code)
        # 候选落盘路径 + 执行上下文（适配器原样透传请求对象；provider 据此下载进 attempt）
        if not production_output:
            req.candidate_path = str(video_path)
            req.execution_context = ExecutionContext.from_candidate_context(
                candidate_context, candidate_path=video_path)

        svc = _video_service_for(video_adapter)
        if svc is None or not callable(getattr(svc, "prepare_request", None)) \
                or not svc.supports_prepared():
            va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                            script_revision=script_revision,
                            provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
            eio.append_event(attempt, "prepared_unsupported",
                             reason=f"{type(svc).__name__ if svc else 'no-service'} 未实现 prepared 入口",
                             code="prepared_unsupported")
            return _fail("prepared_unsupported: provider 未实现 prepare/create_prepared_task，"
                         "拒绝降级提交", code="prepared_unsupported")

        # ---- prepare（无提交）：实际生效文本/参数/参考 ----
        try:
            prepared = svc.prepare_request(req, candidate_context)
            # S08：实际时长以 prepared 为准回填；装不下计划即阻断（不静默改故事时长）。
            record_submitted_duration(shot, prepared)
        except VideoCapabilityError as exc:
            va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                            script_revision=script_revision,
                            provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
            eio.append_event(attempt, "preflight_blocked", code=exc.code,
                             message=exc.message, field_path=exc.fields.get("field_path"))
            return _fail(f"preflight_blocked({exc.code}): {exc.message}", code=exc.code)
        except SystemCameraError as exc:
            va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                            script_revision=script_revision,
                            provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
            eio.append_event(attempt, "preflight_blocked", code=exc.code,
                             message=exc.message)
            return _fail(f"preflight_blocked({exc.code}): {exc.message}", code=exc.code)
        if not prepared.is_ready():
            eio.append_event(attempt, "preflight_blocked", code="prepared_unsupported",
                             message=prepared.unsupported_reason)
            return _fail(f"prepared_unsupported: {prepared.unsupported_reason}",
                         code="prepared_unsupported")

        # ---- 冻结"真正会发出去的请求"：以 **prepared 的实际事实**为准 ----
        # （只回填 prompt 是不够的：时长/声音开关/参考角色等仍取原请求，会记下与提交
        #   不符的值，而 request_key 照样"相同"——见 2026-10-03 复核 P1-4。）
        frozen = rp.build_frozen_request(
            req, shot=shot, scope=scope, script_revision=script_revision,
            provider=prepared.provider, model_id=prepared.model_id,
            capability_revision=prepared.capability_revision,
            requirements=generation_requirements(), prepared=prepared)
        if execution_requirements is not None:
            from step02_video_gen.execution_requirements import (
                merge_into_frozen, validate_requirements,
            )
            merge_into_frozen(frozen, execution_requirements)
            _exec_problems = validate_requirements(frozen, execution_requirements)
            if _exec_problems:
                va.open_attempt(package_root, attempt_id, request_key=frozen.request_key,
                                scope=scope, script_revision=script_revision,
                                provider=prepared.provider, model_id=prepared.model_id)
                eio.append_event(attempt, "execution_requirements_blocked",
                                 problems=_exec_problems)
                return _fail(f"execution_requirements: {_exec_problems[:2]}",
                             code=(_exec_problems[0].get("code")
                                   or "execution_requirements_blocked"))
        prepared.request_key = frozen.request_key
    # S02：prepared 是下发事实源；冻结记录必须与它一致（prompt 逐字、其余规范化）。
    _mismatch = rp.verify_prepared_frozen(prepared, frozen)
    if _mismatch:
        eio.append_event(attempt, "prepared_frozen_mismatch", problems=_mismatch)
        return _fail(f"prepared_frozen_mismatch: {_mismatch[:2]}",
                     code="prepared_frozen_mismatch")
    problems = rp.validate_frozen_request(frozen)
    if problems:
        va.open_attempt(package_root, attempt_id, request_key=frozen.request_key, scope=scope,
                        script_revision=script_revision, provider=prepared.provider,
                        model_id=prepared.model_id,
                        credential_profile_ref=prepared.credential_profile_ref)
        eio.append_event(attempt, "preflight_blocked", code="preflight_blocked",
                         problems=problems)
        return _fail(f"preflight: {problems}", code="preflight_blocked")

    _recovery = find_recoverable_attempt(
        package_root,
        shot_index=idx,
        request_key=frozen.request_key,
        scope=scope,
        script_revision=script_revision,
        provider=prepared.provider,
    )
    if _recovery is not None:
        _r_act = _recovery["action"]
        _r_prev = Path(_recovery["attempt"])
        _r_gid = str(_recovery.get("generation_id") or "")
        _r_tid = _recovery.get("task_id")
        if _r_act == "submission_unknown_no_id":
            _emit_video_ledger(
                scope=scope, script_revision=script_revision, shot_index=idx,
                provider=prepared.provider, model_id=prepared.model_id,
                media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
                request_key=frozen.request_key, content_key=prepared.content_key,
                generation_id=_r_gid, attempt_id=_r_prev.name,
                action_type="blocked_recovery", outcome="submission_unknown",
                error_code="submission_unknown", submit_posts=attempt_task_id(_r_prev).get("submit_posts"),
                context=candidate_context, request_id=_r_gid or prepared.request_id,
            )
            return _fail(
                f"submission_unknown: 先前尝试 {_r_prev.name} 提交状态未知且无 task_id，禁止重发",
                code="submission_unknown",
            )
        if _r_act == "commit_unconfirmed":
            _emit_video_ledger(
                scope=scope, script_revision=script_revision, shot_index=idx,
                provider=prepared.provider, model_id=prepared.model_id,
                media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
                request_key=frozen.request_key, content_key=prepared.content_key,
                generation_id=_r_gid, task_id=str(_r_tid or ""), attempt_id=_r_prev.name,
                action_type="blocked_recovery", outcome="commit_unconfirmed",
                error_code="commit_unconfirmed", submit_posts=attempt_task_id(_r_prev).get("submit_posts"),
                context=candidate_context, request_id=_r_gid or prepared.request_id,
            )
            return _fail(
                f"commit_unconfirmed: 先前尝试 {_r_prev.name} 采纳未确认，禁止重发",
                code="commit_unconfirmed",
            )
        if _r_act == "recover_same_task" and _r_tid:
            _prev_inputs = dict(_recovery.get("inputs") or {})
            _bind_fn = getattr(svc, "restore_task_binding", None)
            if callable(_bind_fn):
                _binding = {
                    "status": "ok",
                    "provider": _prev_inputs.get("provider"),
                    "key_slot": _prev_inputs.get("key_slot"),
                    "credential_profile_ref": _prev_inputs.get("credential_profile_ref"),
                    "binding_version": 1,
                }
                if not _bind_fn(str(_r_tid), _binding):
                    _emit_video_ledger(
                        scope=scope, script_revision=script_revision, shot_index=idx,
                        provider=prepared.provider, model_id=prepared.model_id,
                        media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
                        request_key=frozen.request_key, content_key=prepared.content_key,
                        generation_id=_r_gid, task_id=str(_r_tid), attempt_id=_r_prev.name,
                        action_type="blocked_recovery", outcome="credential_binding_unavailable",
                        error_code="credential_binding_unavailable",
                        context=candidate_context, request_id=_r_gid or prepared.request_id,
                    )
                    return _fail(
                        "credential_binding_unavailable: 历史任务凭据绑定不可解析，禁止猜测查询或重发",
                        code="submission_unknown",
                    )
            _resumed = va.open_recovery_attempt(package_root, _r_prev, attempt_id=attempt_id)
            if logger is not None:
                logger.info(
                    f"  [Shot {idx}] 命中可恢复任务 task_id={_r_tid} (prev={_r_prev.name} state={_recovery['state']})"
                    f" -> 复用 {_resumed.name}（0 次新 POST）"
                )
            _emit_video_ledger(
                scope=scope, script_revision=script_revision, shot_index=idx,
                provider=prepared.provider, model_id=prepared.model_id,
                media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
                request_key=frozen.request_key, content_key=prepared.content_key,
                generation_id=_r_gid, task_id=str(_r_tid), attempt_id=_resumed.name,
                action_type="recover", outcome="resumed_poll", recovered=True,
                submit_posts=attempt_task_id(_resumed).get("submit_posts"),
                context=candidate_context, request_id=_r_gid or prepared.request_id,
            )
            poll = _poll_video_production if production_output else _poll_video
            return asyncio.create_task(
                poll(video_adapter, poll_semaphore, req, str(_r_tid), idx,
                     shot_dir, video_path, VIDEO_MAX_RETRIES, shot_hash,
                     qc_resubmit_limit=0, shot=shot, characters=characters,
                     candidate_context=None if production_output else candidate_context,
                     frozen_request=frozen,
                     **({"trace_attempt": _resumed} if production_output else {}))
            )

    gen_id = va.new_generation_id()
    prepared.source = {
        **dict(prepared.source or {}),
        "generation_id": gen_id,
        "scope": scope,
        "script_revision": script_revision,
        "shot_index": idx,
        "media_policy": str(media_policy or prepared.media_mode or (prepared.source or {}).get("media_policy", "")),
    }
    va.open_attempt(package_root, attempt_id, request_key=frozen.request_key, scope=scope,
                    script_revision=script_revision, provider=prepared.provider,
                    model_id=prepared.model_id, generation_id=gen_id,
                    credential_profile_ref=prepared.credential_profile_ref)
    eio.freeze_inputs(attempt, {**(eio.read_inputs(attempt) or {}),
                                "frozen_request": frozen.to_dict(),
                                "prepared": prepared.to_dict(),
                                "shot_index": idx,
                                "execution_mode": "production" if production_output else "candidate",
                                "requested_seed": getattr(req, "seed", None)})
    va.transition(attempt, va.STATE_READY)
    eio.append_event(attempt, "prepared", content_key=prepared.content_key,
                     request_key=frozen.request_key,
                     modifications=prepared.modifications,
                     materials=[m.to_dict() for m in prepared.materials])

    # —— 唯一一次生成 POST（prepared 入口；无内部重试、无隐藏重发）——
    va.transition(attempt, va.STATE_SUBMITTING)
    try:
        task_id = await _create_prepared_task_once(svc, prepared, candidate_context,
                                                   attempt=attempt, generation_id=gen_id)
    except VideoCapabilityError as exc:
        # materialize 阶段拒收（素材字节变了/不可读）⇒ 可证未送出，零 POST
        va.transition(attempt, va.STATE_PROVIDER_FAILED, kind="submit_blocked",
                      error=f"{exc.code}: {exc.message}", generation_id=gen_id)
        _emit_video_ledger(
            scope=scope, script_revision=script_revision, shot_index=idx,
            provider=prepared.provider, model_id=prepared.model_id,
            media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
            request_key=frozen.request_key, content_key=prepared.content_key,
            generation_id=gen_id, attempt_id=attempt.name,
            action_type="generate", outcome="submit_blocked", error_code=exc.code,
            context=candidate_context, request_id=prepared.request_id,
        )
        return _fail(f"submit_blocked({exc.code}): {exc.message}", code=exc.code)
    except VideoSubmissionError as exc:
        state = (va.STATE_SUBMISSION_UNKNOWN
                 if exc.classification == SUBMIT_UNKNOWN else va.STATE_PROVIDER_FAILED)
        va.transition(attempt, state, kind="submit_post", task_id=None,
                      generation_id=gen_id, classification=exc.classification,
                      http_status=exc.http_status, error=str(exc))
        if exc.classification == SUBMIT_RATE_LIMITED:
            eio.append_event(attempt, "submit_rate_limited", retry_after=exc.retry_after)
        if not production_output and (exc.http_status in (400, 422) or "content_policy" in str(exc).lower()):
            _record_content_policy_revision(attempt, prompt, str(exc), eio)
        # 结构化分类**必须落到失败记录上**：不带 code 会被批处理记成默认
        # `candidate_submit_failed`，CLI 于是返回 3 而不是约定的 7（复核 P2-8）。
        _cls_code = submit_failure_code(exc.classification)
        _emit_video_ledger(
            scope=scope, script_revision=script_revision, shot_index=idx,
            provider=prepared.provider, model_id=prepared.model_id,
            media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
            request_key=frozen.request_key, content_key=prepared.content_key,
            generation_id=gen_id, attempt_id=attempt.name,
            action_type="generate", outcome=state, error_code=_cls_code,
            submit_posts=1, context=candidate_context, request_id=prepared.request_id,
        )
        return _fail(f"submit({exc.classification}): {exc}", code=_cls_code)

    except Exception as exc:
        va.transition(attempt, va.STATE_SUBMISSION_UNKNOWN, kind="submit_post",
                      task_id=None, generation_id=gen_id, classification=SUBMIT_UNKNOWN,
                      error=str(exc))
        _emit_video_ledger(
            scope=scope, script_revision=script_revision, shot_index=idx,
            provider=prepared.provider, model_id=prepared.model_id,
            media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
            request_key=frozen.request_key, content_key=prepared.content_key,
            generation_id=gen_id, attempt_id=attempt.name,
            action_type="generate", outcome="submission_unknown", error_code="submission_unknown",
            submit_posts=1, context=candidate_context, request_id=prepared.request_id,
        )
        return _fail(f"submission_unknown: {exc}", code="submission_unknown")

    task_id = str(task_id)
    try:
        va.transition(attempt, va.STATE_SUBMITTED, kind="submit_post", task_id=task_id,
                      generation_id=gen_id,
                      credential_profile_ref=prepared.credential_profile_ref)
    except Exception as _persist_exc:
        _saved_tid = attempt_task_id(attempt).get("task_id")
        _err_code = "post_submit_persist_failed" if _saved_tid else "submission_unknown"
        _emit_video_ledger(
            scope=scope, script_revision=script_revision, shot_index=idx,
            provider=prepared.provider, model_id=prepared.model_id,
            media_policy=prepared.media_mode or (prepared.source or {}).get("media_policy", ""),
            request_key=frozen.request_key, content_key=prepared.content_key,
            generation_id=gen_id, task_id=str(_saved_tid or task_id), attempt_id=attempt.name,
            action_type="generate", outcome=_err_code, error_code=_err_code,
            submit_posts=1, context=candidate_context, request_id=prepared.request_id,
        )
        return _fail(
            f"{_err_code}: task_id={task_id} 已创建但状态落盘失败（{_persist_exc}），禁止重发",
            code=_err_code,
        )
    poll = _poll_video_production if production_output else _poll_video
    task = asyncio.create_task(
        poll(video_adapter, poll_semaphore, req, task_id, idx,
             shot_dir, video_path, VIDEO_MAX_RETRIES, shot_hash,
             qc_resubmit_limit=0, shot=shot, characters=characters,
             candidate_context=None if production_output else candidate_context,
             frozen_request=frozen, **({"trace_attempt": attempt} if production_output else {}))
    )
    return task






def _video_service_for(video_adapter):
    """解析 provider 服务对象（候选 prepared 入口用）。

    ``shared/vimax_adapters.py`` 属于并行实施线，本包不改它的公开面：优先取公开的
    ``service`` 属性，回退到其固有的 ``_svc`` 字段；两者都没有 ⇒ None（fail-closed，
    候选模式不降级到旧提交路径）。
    """
    svc = getattr(video_adapter, "service", None)
    if svc is None:
        svc = getattr(video_adapter, "_svc", None)
    return svc


def _submit_timeout() -> int:
    return int(os.getenv("VIDEO_SUBMIT_TIMEOUT", os.getenv("AGNES_VIDEO_SUBMIT_TIMEOUT", "180")))


async def _create_prepared_task_once(svc, prepared, candidate_context, *, attempt: Path,
                                     generation_id: str) -> str:
    """调用 provider 的 prepared 提交入口，**至多一个** POST。

    超时/取消**不**证明 HTTP 没发生（计划 §V2「底层线程超时后返回 task_id」）：
    提交在线程池里跑，done 回调把晚到的 task_id 落盘进 attempt 事件
    （``late_task_id``），恢复时按同一 ID 查询——主协程不因取消就断言没建任务。
    """
    import concurrent.futures
    import contextvars

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                     thread_name_prefix="video-submit")
    _submit_fn = getattr(svc, "create_prepared_task", None)
    _submit_mod = getattr(getattr(_submit_fn, "__func__", _submit_fn), "__module__", "")
    _builtin_http = _submit_mod in (
        "_video_gen.volcengine_provider",
        "_video_gen.agent_plan_provider",
    )

    def _invoke():
        if not _builtin_http:
            from shared import request_events as _req_ev
            _src = dict(getattr(prepared, "source", None) or {})
            _sc = dict(_src.get("scope") or {})
            _req_ev.emit_step02b(
                "http_attempt",
                context=candidate_context,
                request_id=str(getattr(prepared, "request_id", "") or ""),
                kind="video_submit",
                provider=str(getattr(prepared, "provider", "") or ""),
                model_id=str(getattr(prepared, "model_id", "") or ""),
                media_policy=str(_src.get("media_policy") or getattr(prepared, "media_mode", "") or "unknown"),
                request_key=str(getattr(prepared, "request_key", "") or ""),
                content_key=str(getattr(prepared, "content_key", "") or ""),
                shot_index=_src.get("shot_index", (getattr(prepared, "parameters", None) or {}).get("clip_index")),
                operation_id=str(_src.get("generation_id") or generation_id or ""),
                generation_id=str(_src.get("generation_id") or generation_id or ""),
                work_id=_work_id_str(_sc),
                chapter_scope=_chapter_scope_str(_sc),
                script_revision=str(_src.get("script_revision") or ""),
            )
        return svc.create_prepared_task(prepared, candidate_context)

    _cv_ctx = contextvars.copy_context()
    cfut = executor.submit(_cv_ctx.run, _invoke)

    def _persist_late(fut) -> None:
        try:
            tid = fut.result()
        except BaseException:  # noqa: BLE001 - 晚到失败照常由主协程分类
            return
        if not tid:
            return
        import time as _t
        try:
            from shared import enhancement_io as eio

            def _already_recorded() -> bool:
                for event in eio.read_events_strict(Path(attempt))[0]:
                    if event.get("kind") == "submit_post" and str(event.get("task_id")) == str(tid):
                        return True
                return False

            # 正常路径下主协程随后就会登记同一 task_id（回调可能先到）：给它一点时间，
            # 只有主协程确实没登记（超时/取消类 unknown）时才写 late_task_id。
            for _ in range(20):
                if _already_recorded():
                    return
                _t.sleep(0.05)
            if _already_recorded():
                return
            lock = eio.AttemptLock(Path(attempt) / eio.LOCK_DIRNAME, purpose="late task id")
            with lock:
                if _already_recorded():
                    return
                eio.append_event(Path(attempt), "late_task_id", task_id=str(tid),
                                 generation_id=generation_id)
        except Exception as _degrade_exc:  # noqa: BLE001 - 落盘失败不阻断主流程（主协程仍按 unknown 记）
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step02_video_gen/submit_poll.py:_persist_late", _degrade_exc)

    cfut.add_done_callback(_persist_late)
    try:
        wrapped = asyncio.wrap_future(cfut)
        return await asyncio.wait_for(wrapped, timeout=_submit_timeout())
    except asyncio.TimeoutError as exc:
        raise VideoSubmissionError(
            SUBMIT_UNKNOWN,
            f"提交在 {_submit_timeout()}s 内未返回（底层线程可能仍在提交，"
            f"晚到 task_id 会落盘 late_task_id；主协程不据此断言未发生）") from exc
    finally:
        executor.shutdown(wait=False)


async def _submit_video(
    shot: dict, shot_dir: Path, video_path: Path, frame_paths: list,
    shot_hash: str, video_adapter, poll_semaphore,
    script: dict, idx: int,
    continuity_ref_image: str | None = None,
    mode: str = "A",
    candidate_context=None,
    execution_requirements=None,
    prepared_facts=None,
    media_policy: str | None = None,
) -> asyncio.Task | None:
    """提交视频任务并返回轮询 task.

    候选模式（``candidate_context`` 非空，计划 §V2 改动 1/2）：
    - **每次尝试至多一个生成 POST**（单次人工确认范围内），不做重提交循环；
    - 429/拒绝/未知分别归类返回，**不在 except 内直接再 submit**；
    - content-policy 修订只产生"新的候选计划"记录，交上层按费用确认后重走
      prepare，而不是就地改 prompt 再提交。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    # 候选模式的提交/轮询日志必须落 attempt 的实验日志目录；否则候选运行会写
    # 正式 `chapter_dir/step02.log`，破坏 production snapshot 的逐字节隔离。
    _candidate_log_dir = (getattr(candidate_context, "log_dir", None)
                          if candidate_context is not None else None)
    logger = get_logger(
        (Path(_candidate_log_dir) if _candidate_log_dir else chapter_dir()) / "step02.log",
        console_level="INFO")

    prompt = _build_prompt(shot, script, mode=mode)
    characters = script.get("characters", [])

    if candidate_context is not None:
        return await _submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path, frame_paths=frame_paths,
            shot_hash=shot_hash, video_adapter=video_adapter, poll_semaphore=poll_semaphore,
            script=script, idx=idx, prompt=prompt, characters=characters,
            continuity_ref_image=continuity_ref_image, mode=mode,
            candidate_context=candidate_context, logger=logger,
            make_request=_make_request, execution_requirements=execution_requirements,
            prepared_facts=prepared_facts, media_policy=media_policy)

    from types import SimpleNamespace
    from shared.paths import production_video_attempts_root
    from shared import video_attempts as va
    root = production_video_attempts_root(shot_dir)
    ctx = SimpleNamespace(
        package_root=root, attempt_dir=root / "attempts" / va.new_attempt_id("production"),
        scope=_scope_identity_safe(), script_revision=_script_revision(script))
    try:
        return await _submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path, frame_paths=frame_paths,
            shot_hash=shot_hash, video_adapter=video_adapter, poll_semaphore=poll_semaphore,
            script=script, idx=idx, prompt=prompt, characters=characters,
            continuity_ref_image=continuity_ref_image, mode=mode, candidate_context=ctx,
            logger=logger, make_request=_make_request, production_output=True,
            execution_requirements=execution_requirements, prepared_facts=prepared_facts,
            media_policy=media_policy)
    except Exception as exc:
        # Includes post-submit persistence failure: never turn it into another POST.
        _failed_videos[idx].append({"attempt": str(ctx.attempt_dir),
                                    "code": "execution_record_failed", "error": str(exc)})
        logger.warn(f"  [镜头{idx}] 执行记录失败，不重交: {exc}; attempt={ctx.attempt_dir}")
        return None


async def _process_one_shot(
    shot: dict, shots_dir: Path, image_adapter, video_adapter,
    script_mtime: float | None, force_first_frame: bool,
    script: dict,
    poll_semaphore: asyncio.Semaphore,
    continuity_ref_image: str | None = None,
    mode: str = "A",
    candidate_context=None,
) -> asyncio.Task | None:
    """Process single shot: frame gen -> submit video -> return poll task.

    continuity_ref_image: 模式B时传入上一镜视频尾帧路径，跳过首帧生成。
    mode: "A" 关键帧模式 / "B" I2V接续模式。
    candidate_context: 非空 ⇒ 候选模式（帧/视频/哈希全部落 attempt，不写正式产物；
    提交走 prepared 入口，一次尝试至多一个生成 POST）。
    """
    from shared.tee_logger import get_logger
    from shared.log_utils import resolve_log_file
    # 候选模式下本函数曾经硬编码正式章目录日志：日志上下文优先（log_utils 唯一判据）
    logger = get_logger(resolve_log_file("step02.log"), console_level="INFO")

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / SHOT_VIDEO_FILENAME

    # Cache check（§V2 改动 4）：prompt_hash + 产物检查 + **冻结请求关系**
    #   - exact：本次组装出的冻结请求与快照 request_key 一致 ⇒ 同一事实，复用；
    #   - legacy：产物+hash 命中但没有完整快照（旧稿）⇒ 沿用旧行为，不得宣称新身份已验证；
    #   - mismatch：模型实际输入已变 ⇒ 重新生成。
    from shared.paths import clip_file
    _clip_dst = None if candidate_context is not None else clip_file(idx)
    shot_hash = _get_shot_hash(shot, script, shot_dir=shot_dir)
    _cache = cache_decision(shot, script, shot_dir, video_path, clip_path=_clip_dst,
                            force=force_first_frame, shot_hash=shot_hash)
    if _cache["hit"]:
        logger.verbose(f"  [Shot {idx}] Cache hit({_cache['relation']}), skip: {_cache['reason']}")
        return None
    if _cache["reason"] == "clip 产物缺失" and video_path.exists():
        # R3：只有副本缺失、视频本体与 prompt_hash 都完好 ⇒ 本地补拷，零远端 POST。
        # 旧行为在这里重新提交一次生成（付费），把「拷个文件」变成「重烧一条视频」。
        if _clip_dst is not None and repair_missing_clip(shot_dir, video_path, _clip_dst):
            logger.verbose(
                f"  [Shot {idx}] Clip 缺失但视频完好，本地补拷 clips/（0 次远端提交）")
            return None
        logger.warn(f"  [Shot {idx}] Clip 缺失且本地补拷失败，回退为重新生成")
        ff = shot_dir / FIRST_FRAME_FILENAME
        lf = shot_dir / LAST_FRAME_FILENAME
        frame_paths = [str(p) for p in [ff, lf] if p.exists()]
        # 模式B：用上一镜尾帧替代首帧
        if mode == "B" and continuity_ref_image:
            frame_paths = [continuity_ref_image] + frame_paths[1:]
        return await _submit_video(
            shot, shot_dir, video_path, frame_paths, shot_hash,
            video_adapter, poll_semaphore, script, idx,
            continuity_ref_image=continuity_ref_image, mode=mode,
            candidate_context=candidate_context,
        )

    # Frame gen + submit
    if mode == "B" and continuity_ref_image:
        # 模式B：跳过首帧生成，用上一镜视频尾帧作为 I2V 参考图
        # 仅生成尾帧用于本镜风格锚定
        characters = script.get("characters", [])
        frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                       force=force_first_frame, characters=characters,
                                       skip_first_frame=True)
        kf_paths = frames.get("keyframes_paths", [])
        frame_paths = [continuity_ref_image]  # 上一镜尾帧替代 ff_path
        lf = frames.get("lf_path")
        if lf:
            # 检查尾帧幻觉
            lf_check = _check_keyframe_hallucination(lf)
            if lf_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 尾帧检测到 critical 幻觉: {lf_check['issues']}，强制重新生成尾帧")
                # 删除有问题的尾帧，触发重新生成
                Path(lf).unlink(missing_ok=True)
                # 重新生成
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters,
                                               skip_first_frame=True)
                lf = frames.get("lf_path")
            frame_paths.append(lf)
        frame_paths.extend(kf_paths)
    else:
        # 模式A：完整帧生成（首帧+尾帧+关键帧）
        characters = script.get("characters", [])
        frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                       force=force_first_frame, characters=characters)
        kf_paths = frames.get("keyframes_paths", [])

        # 检查首帧幻觉
        ff_path = frames.get("ff_path")
        if ff_path:
            ff_check = _check_keyframe_hallucination(ff_path)
            if ff_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 首帧检测到 critical 幻觉: {ff_check['issues']}，强制重新生成首帧")
                Path(ff_path).unlink(missing_ok=True)
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters)
                ff_path = frames.get("ff_path")

        # 检查尾帧幻觉
        lf_path = frames.get("lf_path")
        if lf_path:
            lf_check = _check_keyframe_hallucination(lf_path)
            if lf_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 尾帧检测到 critical 幻觉: {lf_check['issues']}，强制重新生成尾帧")
                Path(lf_path).unlink(missing_ok=True)
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters)
                lf_path = frames.get("lf_path")

        # B7 修复：关键帧幻觉检查（补上——vimax_bridge 关键帧 QC 已写 sidecar，
        # 此处双保险：关键帧 critical 坏图不进视频）
        kf_paths = frames.get("keyframes_paths", [])
        bad_kf = [p for p in kf_paths if _check_keyframe_hallucination(p).get("has_critical")]
        if bad_kf:
            logger.verbose(f"  [镜头{idx}] 关键帧检测到 critical 幻觉: {len(bad_kf)} 帧，强制重新生成")
            for p in bad_kf:
                Path(p).unlink(missing_ok=True)
            frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                           force=True, characters=characters)
            kf_paths = frames.get("keyframes_paths", [])

        # 重新获取帧路径（可能已被重新生成）
        kf_paths = frames.get("keyframes_paths", [])
        frame_paths = [p for p in [frames.get("ff_path"), frames.get("lf_path")] if p]
        frame_paths.extend(kf_paths)

    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, script, idx,
        continuity_ref_image=continuity_ref_image, mode=mode,
        candidate_context=candidate_context,
    )
# 提交限速由 _video_gen provider 内的 KeyScheduler 负责（每 key 独立 61s 冷却，
# 8 key 轮询并行——全局间隔会把多 key 产能压成单 key 水平）。
