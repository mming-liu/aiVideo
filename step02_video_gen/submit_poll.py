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
from vimax.utils.rate_limiter import RateLimiter

# Smart polling config: exponential backoff + jitter
POLL_BASE_INTERVAL = int(os.getenv("POLL_BASE_INTERVAL", "10"))    # Initial poll interval (seconds)
POLL_MAX_INTERVAL = int(os.getenv("POLL_MAX_INTERVAL", "60"))      # Max poll interval (seconds)
POLL_EXPONENTIAL_FACTOR = float(os.getenv("POLL_EXPONENTIAL_FACTOR", "1.5"))  # Backoff multiplier
POLL_JITTER = int(os.getenv("POLL_JITTER", "3"))                   # Jitter range +/- seconds
INITIAL_POLL_DELAY = int(os.getenv("INITIAL_POLL_DELAY", "60"))    # Wait after submit before first poll

_VIDEO_RPM = os.getenv("VIDEO_MAX_REQUESTS_PER_MINUTE")
VIDEO_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_VIDEO_RPM) if _VIDEO_RPM else None,
)

SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "60"))
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))

# Global failure storage
_failed_videos = defaultdict(list)


def _get_shot_hash(shot: dict) -> str:
    """
    计算分镜的哈希值，用于判断是否需要重新生成视频。
    优化：只使用决定视频内容的核心字段，移除不稳定的 ff_desc 和 lf_desc。
    """
    key = "|".join([
        # ff_desc 和 lf_desc 是用于生成图片的，具有随机性，不应作为视频是否变化的依据
        # shot.get("ff_desc", ""),
        # shot.get("lf_desc", ""),

        # 使用更稳定的字段：
        shot.get("prompt", ""),      # 发送给视频生成AI的最终提示词
        shot.get("motion_desc", ""), # 动作描述，是prompt的核心部分
        str(shot.get("duration", 0)), # 视频时长，时长变了视频肯定也变了
        shot.get("narration_segment", ""), # 旁白内容，如果旁白变了，视频节奏可能也要变
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _is_recoverable(err: str) -> bool:
    err_lower = err.lower()
    terminal_markers = [
        "content_policy", "5185bb98fdd",
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
) -> bool:
    """Background poll with exponential backoff + jitter - reduces API queries."""
    async with semaphore:
        # Wait INITIAL_POLL_DELAY seconds after submit before first query (respect 1 RPM limit)
        print(f"  [Shot {shot_idx}] Task submitted, waiting {INITIAL_POLL_DELAY}s before polling...")
        await asyncio.sleep(INITIAL_POLL_DELAY)

        # Exponential backoff state
        poll_interval = POLL_BASE_INTERVAL
        consecutive_failures = 0

        while True:
            try:
                # Calculate next poll time with exponential backoff + jitter
                jitter = random.uniform(-POLL_JITTER, POLL_JITTER)
                actual_interval = max(1, poll_interval + jitter)

                await asyncio.sleep(actual_interval)

                result = await video_adapter.fetch_result(req, task_id)
                if result.success and result.save_path:
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(result.save_path, str(video_path))
                    return True
                else:
                    err = result.error or "unknown"
                    # 429 triggers longer backoff
                    if _is_recoverable(err):
                        if "429" in err or "rate" in err.lower():
                            poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                        else:
                            poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                        consecutive_failures += 1
                        print(f"  [Shot {shot_idx}] Processing (interval {poll_interval:.0f}s, failures {consecutive_failures})")
                        continue
                    _failed_videos[shot_idx].append({"attempt": -1, "error": err, "task_id": task_id})
                    return False
            except Exception as e:
                err_str = str(e)
                # 429 triggers longer backoff
                if _is_recoverable(err_str):
                    if "429" in err_str or "rate" in err_str.lower():
                        poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                    else:
                        poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                    consecutive_failures += 1
                    print(f"  [Shot {shot_idx}] Poll error (interval {poll_interval:.0f}s): {err_str[:50]}")
                    continue
                _failed_videos[shot_idx].append({"attempt": -1, "error": err_str, "task_id": task_id})
                return False


async def _process_one_shot(
    shot: dict, shots_dir: Path, image_adapter, video_adapter,
    script_mtime: float | None, force_first_frame: bool,
    script: dict, audio_duration: float,
    poll_semaphore: asyncio.Semaphore,
    submit_interval: "_SubmitInterval",
) -> asyncio.Task | None:
    """Process single shot: frame gen -> submit video -> return poll task."""
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / "video.mp4"

    # Cache check: use prompt_hash.txt
    if video_path.exists() and video_path.stat().st_size > 20 * 1024:
        hash_path = shot_dir / "prompt_hash.txt"
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            new_hash = _get_shot_hash(shot)
            if old_hash == new_hash:
                return None

    # Frame gen
    frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime, force=force_first_frame)
    frame_paths = [p for p in [frames.get("ff_path"), frames.get("lf_path")] if p]

    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    await submit_interval.wait(video_adapter)

    prompt = _build_prompt(shot, script)

    for attempt in range(1, 4):
        try:
            await VIDEO_RATE_LIMITER.acquire()
            req = _make_request(video_adapter, prompt, frame_paths, shot, audio_duration=audio_duration)
            task_id = await video_adapter.submit_task(req)
            task = asyncio.create_task(
                _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                           shot_dir, video_path, 99)
            )
            return task
        except Exception as e:
            err_str = str(e)
            if "400" in err_str or "422" in err_str or "content_policy" in err_str.lower() or "5185bb98fdd" in err_str:
                from shared.content_policy_fix import auto_fix_prompt
                fixed_prompt = auto_fix_prompt(prompt, str(e), verbose=False)
                if fixed_prompt and fixed_prompt != prompt:
                    try:
                        req = _make_request(video_adapter, fixed_prompt, frame_paths, shot, audio_duration=audio_duration)
                        task_id = await video_adapter.submit_task(req)
                        task = asyncio.create_task(
                            _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                                       shot_dir, video_path, 3)
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


class _SubmitInterval:
    """Global submit interval controller with key-aware timing.

    - Different key: 30s interval
    - Same key: 61s interval
    """
    # Different key interval 30s, same key interval 61s (free user 1 RPM = 60s)
    INTERVAL_DIFF_KEY = 30  # Different key
    INTERVAL_SAME_KEY = 61  # Same key

    def __init__(self):
        self._last_time: float = 0.0
        self._last_key_idx: int = -1
        self._lock = asyncio.Lock()

    async def wait(self, video_adapter):
        """Wait appropriate time before submitting."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            current_key_idx = video_adapter.get_key_index()

            # Calculate interval
            if self._last_key_idx < 0:
                # First submit, no wait
                interval = 0
            elif current_key_idx != self._last_key_idx:
                # Different key, 30s interval
                interval = self.INTERVAL_DIFF_KEY
            else:
                # Same key, 61s interval
                interval = self.INTERVAL_SAME_KEY

            if self._last_time > 0 and interval > 0:
                elapsed = now - self._last_time
                if elapsed < interval:
                    wait_time = interval - elapsed
                    print(f"  [Submit interval] Key {current_key_idx + 1}, waiting {wait_time:.1f}s ({interval}s interval)")
                    await asyncio.sleep(wait_time)

            # Update record
            self._last_time = asyncio.get_event_loop().time()
            self._last_key_idx = current_key_idx


async def generate_alls(script: dict, working_dir: Path, script_file: Path, force_first_frame: bool = False) -> int:
    """Pipeline: config -> TTS -> concurrent shots (frames -> submit -> poll)."""
    from step02_video_gen.audio_pregen import _gen_shot_audio_duration, _update_script_with_actual_durations
    from tts import create_provider

    script_actual_mtime = script_file.stat().st_mtime if script_file.exists() else None

    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. Working dir setup (skip frames)
    await build_working_dir(script_file, working_dir, image_adapter,
                            force_first_frame=force_first_frame, skip_frames=True)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = script_actual_mtime

    # 2. TTS pre-gen
    tts_provider = create_provider()
    book_idx = int(os.getenv("BOOK_INDEX", "1"))
    chapter_idx = int(os.getenv("NOVEL_CHAPTER", "1"))
    output_base = Path(os.getenv("OUTPUT_BASE", "./output/video"))
    audio_dir = output_base / f"book_{book_idx:02d}" / f"chapter_{chapter_idx:02d}" / "audio"

    shot_audio_durations = {}
    for shot in shots:
        idx = shot["index"]
        ad = _gen_shot_audio_duration(shot, audio_dir, provider=tts_provider)
        shot_audio_durations[idx] = ad
        shot["actual_audio_duration"] = round(ad, 2)

    total_audio = sum(shot_audio_durations.values())

    actual_concurrency = min(len(shots), VIDEO_CONCURRENCY)
    poll_semaphore = asyncio.Semaphore(actual_concurrency)
    submit_interval = _SubmitInterval()

    # 3. Concurrent shot processing
    shot_tasks = [
        _process_one_shot(
            shot, shots_dir, image_adapter, video_adapter,
            script_mtime, force_first_frame, script,
            audio_duration=shot_audio_durations.get(shot["index"], 0),
            poll_semaphore=poll_semaphore,
            submit_interval=submit_interval,
        )
        for shot in shots
    ]
    poll_tasks = await asyncio.gather(*shot_tasks)
    poll_tasks = [t for t in poll_tasks if t is not None]

    if not poll_tasks:
        return 0

    # 4. Wait all polls
    results = await asyncio.gather(*poll_tasks, return_exceptions=True)

    # Build result table
    all_shot_ids = [s["index"] for s in shots]
    total = len(all_shot_ids)
    success_count = total - len(_failed_videos)

    print()
    print("=" * 70)
    print("  shot    status  reason / task_id")
    print("=" * 70)
    for s in shots:
        sid = s["index"]
        if sid in _failed_videos:
            errs = _failed_videos[sid]
            err = errs[0].get("error", "unknown")[:36]
            tid = errs[0].get("task_id", "")[:20]
            reason = err
            if tid:
                reason += " | " + tid
            print(f"  {sid:>5}  FAIL    {reason}")
        else:
            print(f"  {sid:>5}  OK      -")
    print("=" * 70)
    print(f"  total: {total} shots | OK: {success_count} | FAIL: {len(_failed_videos)}")
    print()

    # 5. Save
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    from step02_video_gen.audio_pregen import _update_script_with_actual_durations
    _update_script_with_actual_durations(working_dir, script_file)

    return len(_failed_videos)


# Sync entry
def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    book_idx = int(os.getenv("BOOK_INDEX", "1"))
    chapter_idx = int(os.getenv("NOVEL_CHAPTER", "1"))
    output_base = Path(os.getenv("OUTPUT_BASE", "./output/video"))
    script_file = output_base / f"book_{book_idx:02d}" / f"chapter_{chapter_idx:02d}" / "script_result.json"
    return asyncio.run(generate_alls(script, working_dir, script_file, force_first_frame))