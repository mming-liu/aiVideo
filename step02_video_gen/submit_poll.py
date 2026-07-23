"""step02_video_gen.submit_poll - submit and poll module"""
import sys
from pathlib import Path
import os
import asyncio
import shutil
import json
import random
from collections import defaultdict
import hashlib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from shared.vimax_bridge import build_working_dir, gen_shot_frames
from shared.vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from shared.error_fmt import fmt_err as _fmt_err, parse_retry_after

# Smart polling config: exponential backoff + jitter
POLL_BASE_INTERVAL = int(os.getenv("POLL_BASE_INTERVAL", "10"))    # Initial poll interval (seconds)
POLL_MAX_INTERVAL = int(os.getenv("POLL_MAX_INTERVAL", "60"))      # Max poll interval (seconds)
POLL_EXPONENTIAL_FACTOR = float(os.getenv("POLL_EXPONENTIAL_FACTOR", "1.5"))  # Backoff multiplier
POLL_JITTER = int(os.getenv("POLL_JITTER", "3"))                   # Jitter range +/- seconds
INITIAL_POLL_DELAY = int(os.getenv("INITIAL_POLL_DELAY", "60"))    # Wait after submit before first poll

# 视频提交并发：在 _submit_video 入口处用 Semaphore 控制，与 _video_gen provider
# 的 KeyScheduler（每 Key 独立冷却）配合实现 Nx 吞吐。空闲 Key 越多，并发越多。
# 不再使用全局 RateLimiter 单令牌串行闸。
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "8"))

# Global failure storage
_failed_videos = defaultdict(list)


def _get_shot_hash(shot: dict, script: dict) -> str:
    """
    计算分镜的哈希值，用于判断是否需要重新生成视频。

    唯一算法：读写两侧必须都用本函数（写入在 _poll_video 成功保存视频后）。
    直接哈希实际提交给视频 API 的内容，而非分镜源字段：
    - _build_prompt 输出（已含 motion_desc/subtitle/角色特征锚点/context_bridge）
    - 风格前缀 + 负面提示词（全局配置变了所有视频都该重生成）
    - narration_segment（决定 TTS 音频时长，进而决定视频帧数）
    - 空镜镜头额外并入首帧内容哈希：首帧是 image-to-video 的首个关键帧，
      首帧生成输入（空镜约束等）变化时视频必须重提交，否则重做的首帧
      永远进不了视频。条件追加——非空镜镜头哈希与旧算法一致，其核心
      字段已含于 _build_prompt 输出，避免哈希算法升级触发全量重做。
    不含 duration：实际帧数由 TTS 音频时长决定，LLM 估算值变化不应触发重新生成。
    """
    from step02_video_gen.prompt_builder import _build_prompt
    from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT, is_empty_scene

    key_parts = [
        STYLE_PREFIX,
        NEGATIVE_PROMPT,
        _build_prompt(shot, script),
        shot.get("narration_segment", ""),
    ]
    if is_empty_scene(shot, script.get("characters", [])):
        from shared.vimax_bridge import _get_frame_content_hash
        key_parts.append(_get_frame_content_hash(shot, empty_scene=True))
    key = "|".join(key_parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _is_recoverable(err: str) -> bool:
    err_lower = err.lower()
    terminal_markers = [
        "content_policy", "5185bb98fdd",
        "datainspectionfailed",  # 图片内容违规
        "green net check failed",  # 绿网检查失败
        "401", "unauthorized", "invalid api key",
        "403", "forbidden",
        "invalid_param", "invalid parameter",
        "model not found", "model_not_found",
    ]
    for marker in terminal_markers:
        if marker in err_lower:
            return False
    return True


async def _poll_video(
    video_adapter, semaphore: asyncio.Semaphore,
    req, task_id: str, shot_idx: int,
    shot_dir: Path, video_path: Path, max_retries: int,
    shot_hash: str = "",
) -> bool:
    """Background poll with exponential backoff + jitter - reduces API queries."""
    async with semaphore:
        # Wait INITIAL_POLL_DELAY seconds after submit before first query (respect 1 RPM limit)
        logger.verbose(f"  [Shot {shot_idx}] Task submitted, waiting {INITIAL_POLL_DELAY}s before polling...")
        await asyncio.sleep(INITIAL_POLL_DELAY)

        # Exponential backoff state
        poll_interval = POLL_BASE_INTERVAL
        consecutive_failures = 0
        override_interval = None  # 429 时用服务端返回的剩余时间 + 0.5s（一次性生效）

        while True:
            try:
                # Calculate next poll time: 429 override > exponential backoff + jitter
                if override_interval is not None:
                    actual_interval = override_interval
                    override_interval = None
                else:
                    jitter = random.uniform(-POLL_JITTER, POLL_JITTER)
                    actual_interval = max(1, poll_interval + jitter)

                await asyncio.sleep(actual_interval)

                result = await video_adapter.fetch_result(req, task_id)
                if result.success and result.save_path:
                    shot_dir.mkdir(parents=True, exist_ok=True)

                    # 视频已下载成功；copy 失败只重试 copy，绝不重新轮询（否则会重复下载）
                    copy_ok = False
                    for copy_attempt in range(10):
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

                    # 写入哈希，供下次运行的缓存检测使用（与 _get_shot_hash 同一算法）
                    if shot_hash:
                        (shot_dir / "prompt_hash.txt").write_text(shot_hash, encoding="utf-8")

                    # 同步复制到 clips/ 目录（step04 合并用；缓存检测用）
                    from shared.paths import clips_dir as _clips_dir
                    clips_d = _clips_dir()
                    clips_d.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(str(video_path), str(clips_d / f"clip_{shot_idx}.mp4"))
                    except OSError:
                        pass  # clips 目录失败不影响主流程

                    return True
                else:
                    err = result.error or "unknown"
                    # 429 triggers longer backoff
                    if _is_recoverable(err):
                        consecutive_failures += 1
                        if "429" in err or "rate" in err.lower():
                            wait = parse_retry_after(err)
                            if wait is not None:
                                override_interval = wait + 0.5
                                logger.verbose(f"  [Shot {shot_idx}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err)}")
                            else:
                                poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                                logger.verbose(f"  [Shot {shot_idx}] Rate limited (interval {poll_interval:.0f}s): {_fmt_err(err)}")
                        else:
                            poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] Processing (interval {poll_interval:.0f}s, failures {consecutive_failures})")
                        continue
                    _failed_videos[shot_idx].append({"attempt": -1, "error": err, "task_id": task_id})
                    return False
            except Exception as e:
                err_str = str(e)
                # 429 triggers longer backoff
                if _is_recoverable(err_str):
                    consecutive_failures += 1
                    if "429" in err_str or "rate" in err_str.lower():
                        wait = parse_retry_after(err_str)
                        if wait is not None:
                            override_interval = wait + 0.5
                            logger.verbose(f"  [Shot {shot_idx}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err_str)}")
                        else:
                            poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    else:
                        poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                        logger.verbose(f"  [Shot {shot_idx}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    continue
                _failed_videos[shot_idx].append({"attempt": -1, "error": err_str, "task_id": task_id})
                return False


async def _submit_video(
    shot: dict, shot_dir: Path, video_path: Path, frame_paths: list,
    shot_hash: str, video_adapter, poll_semaphore,
    audio_duration: float, script: dict, idx: int,
) -> asyncio.Task | None:
    """提交视频任务并返回轮询 task."""
    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    prompt = _build_prompt(shot, script)
    characters = script.get("characters", [])

    for attempt in range(1, 4):
        try:
            req = _make_request(video_adapter, prompt, frame_paths, shot,
                                audio_duration=audio_duration, characters=characters)
            task_id = await video_adapter.submit_task(req)
            task = asyncio.create_task(
                _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                           shot_dir, video_path, 99, shot_hash)
            )
            return task
        except Exception as e:
            err_str = str(e)
            if "400" in err_str or "422" in err_str or "content_policy" in err_str.lower() or "5185bb98fdd" in err_str:
                from shared.content_policy_fix import auto_fix_prompt
                fixed_prompt = auto_fix_prompt(prompt, str(e), verbose=False)
                if fixed_prompt and fixed_prompt != prompt:
                    try:
                        req = _make_request(video_adapter, fixed_prompt, frame_paths, shot,
                                            audio_duration=audio_duration, characters=characters)
                        task_id = await video_adapter.submit_task(req)
                        task = asyncio.create_task(
                            _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                                       shot_dir, video_path, 3, shot_hash)
                        )
                        return task
                    except Exception:
                        pass
                _failed_videos[idx].append({"attempt": -attempt, "error": f"content_policy: {e}"})
                return None
            _failed_videos[idx].append({"attempt": -attempt, "error": f"submit: {e}"})
            if attempt < 3:
                await asyncio.sleep(5)

    _failed_videos[idx].append({"attempt": -3, "error": "FATAL: submit failed"})
    return None


async def _process_one_shot(
    shot: dict, shots_dir: Path, image_adapter, video_adapter,
    script_mtime: float | None, force_first_frame: bool,
    script: dict, audio_duration: float,
    poll_semaphore: asyncio.Semaphore,
) -> asyncio.Task | None:
    """Process single shot: frame gen -> submit video -> return poll task."""
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / "video.mp4"

    # Cache check: prompt_hash.txt + clips 目录双重验证
    # prompt 没变 *且* clips/clip_{idx}.mp4 也存在 → 跳过；否则重新生成
    # 这样只需清空 clips 目录就能触发部分/全部视频重新生成
    # force_first_frame（SCRIPT_FIRST_FRAME_FORCE=1）时跳过缓存，强制重新生成
    shot_hash = _get_shot_hash(shot, script)
    if not force_first_frame and video_path.exists() and video_path.stat().st_size > 20 * 1024:
        hash_path = shot_dir / "prompt_hash.txt"
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            if old_hash == shot_hash:
                # 双重验证：clips/ 目录不存在或缺少对应文件 → 重新生成
                # 删掉整个 clips 目录 = 触发全部视频重新生成
                from shared.paths import clips_dir as _clips_dir
                clips_d = _clips_dir()
                clip_file = clips_d / f"clip_{idx}.mp4"
                if clip_file.exists() and clip_file.stat().st_size > 20 * 1024:
                    logger.verbose(f"  [Shot {idx}] Cache hit, skip (prompt unchanged + clip exists)")
                    return None
                else:
                    # 帧文件已存在，跳过帧生成直接提视频
                    logger.verbose(f"  [Shot {idx}] Clip missing, re-generate video only (skip frames)")
                    ff = shot_dir / "first_frame.png"
                    lf = shot_dir / "last_frame.png"
                    frame_paths = [str(p) for p in [ff, lf] if p.exists()]
                    return await _submit_video(
                        shot, shot_dir, video_path, frame_paths, shot_hash,
                        video_adapter, poll_semaphore, audio_duration, script, idx,
                    )

    # Frame gen + submit
    characters = script.get("characters", [])
    frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                   force=force_first_frame, characters=characters)
    # 合并首帧、尾帧和中间关键帧
    kf_paths = frames.get("keyframes_paths", [])
    frame_paths = [p for p in [frames.get("ff_path"), frames.get("lf_path")] if p]
    frame_paths.extend(kf_paths)  # 添加中间关键帧

    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, audio_duration, script, idx,
    )
# 提交限速由 _video_gen provider 内的 KeyScheduler 负责（每 key 独立 61s 冷却，
# 8 key 轮询并行——全局间隔会把多 key 产能压成单 key 水平）。


async def generate_alls(script: dict, working_dir: Path, script_file: Path, force_first_frame: bool = False) -> int:
    """Pipeline: config -> TTS -> concurrent shots (frames -> submit -> poll)."""
    from step02_video_gen.audio_pregen import _gen_shot_audio_duration, _update_script_with_actual_durations
    from tts import create_provider
    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger

    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02.log", console_level="INFO")

    script_actual_mtime = script_file.stat().st_mtime if script_file.exists() else None

    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. Working dir setup (frames controlled by force_first_frame)
    await build_working_dir(script_file, working_dir, image_adapter,
                            force_first_frame=force_first_frame, skip_frames=False)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = script_actual_mtime

    # 2. TTS pre-gen
    tts_provider = create_provider()
    from shared.paths import audio_dir as _audio_dir
    audio_dir = _audio_dir()

    shot_audio_durations = {}
    for shot in shots:
        idx = shot["index"]
        ad = _gen_shot_audio_duration(shot, audio_dir, provider=tts_provider)
        shot_audio_durations[idx] = ad
        shot["actual_audio_duration"] = round(ad, 2)

    total_audio = sum(shot_audio_durations.values())

    actual_concurrency = min(len(shots), VIDEO_CONCURRENCY)
    poll_semaphore = asyncio.Semaphore(actual_concurrency)

    # 3. Build task list: hook + shots all in one gather (parallel)
    hook = script.get("hook")
    if hook and isinstance(hook, dict) and hook.get("prompt"):
        logger.info(f"\n[INFO] ===== 钩子视频 (index=0, ======")
        hook["index"] = 0
        hook_audio_dur = _gen_shot_audio_duration(hook, audio_dir, provider=tts_provider)
        hook["actual_audio_duration"] = round(hook_audio_dur, 2)
        shot_audio_durations[0] = hook_audio_dur
    else:
        hook = None

    # 钩子 + 正片一并并行
    all_specs = ([hook] if hook else []) + shots
    shot_tasks = [
        _process_one_shot(
            spec, shots_dir, image_adapter, video_adapter,
            script_mtime, force_first_frame, script,
            audio_duration=shot_audio_durations.get(spec.get("index", 0), 0),
            poll_semaphore=poll_semaphore,
        )
        for spec in all_specs
    ]
    poll_tasks = await asyncio.gather(*shot_tasks)
    poll_tasks = [t for t in poll_tasks if t is not None]

    if not poll_tasks:
        return 0

    # 4. Wait all polls (hook + shots)
    results = await asyncio.gather(*poll_tasks, return_exceptions=True)

    # Save hook audio duration back
    if hook:
        script["hook"] = hook

    # Build result table
    logger.separator("=", 70)
    logger.info("  shot    status  reason / task_id")
    logger.separator("=", 70)
    # Hook first
    if hook:
        if 0 in _failed_videos:
            errs = _failed_videos[0]
            err = _fmt_err(errs[0].get("error", "unknown"))
            tid = errs[0].get("task_id", "")
            reason = f"{err} | {tid}" if tid else err
            logger.info(f"  {'hook':>5}  FAIL    {reason}")
        else:
            hook_video = shots_dir / "0" / "video.mp4"
            if hook_video.exists() and hook_video.stat().st_size > 20 * 1024:
                logger.info(f"  {'hook':>5}  OK      -")
            else:
                logger.info(f"  {'hook':>5}  SKIP    (not generated)")
    for s in shots:
        sid = s["index"]
        if sid in _failed_videos:
            errs = _failed_videos[sid]
            err = _fmt_err(errs[0].get("error", "unknown"))
            tid = errs[0].get("task_id", "")
            reason = err
            if tid:
                reason += " | " + tid
            logger.info(f"  {sid:>5}  FAIL    {reason}")
        else:
            logger.info(f"  {sid:>5}  OK      -")
    logger.separator("=", 70)
    total_shots = len(shots) + (1 if hook else 0)
    failed_count = len(_failed_videos)
    ok_count = total_shots - failed_count
    logger.info(f"  total: {total_shots} shots (incl. hook) | OK: {ok_count} | FAIL: {failed_count}")

    # 5. Save
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    from step02_video_gen.audio_pregen import _update_script_with_actual_durations
    _update_script_with_actual_durations(working_dir, script_file)

    return len(_failed_videos)


# Sync entry
def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    from shared.paths import script_file as _script_file
    return asyncio.run(generate_alls(script, working_dir, _script_file(), force_first_frame))