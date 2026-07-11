"""step02_video_gen.submit_poll - 提交与轮询模块
"""
import sys
from pathlib import Path
import os
import asyncio
import shutil
import json

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from shared.vimax_bridge import build_working_dir
from shared.vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from vimax.utils.rate_limiter import RateLimiter

# 获取 rate limiter 配置
_VIDEO_RPM = os.getenv("VIDEO_MAX_REQUESTS_PER_MINUTE")
VIDEO_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_VIDEO_RPM) if _VIDEO_RPM else None,
)

MAX_RETRIES = int(os. getenv("VIDEO_MAX_RETRIES", "3"))
SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "60"))
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))


async def _poll_video(
    video_adapter, semaphore: asyncio.Semaphore,
    req, task_id: str, shot_idx: int,
    shot_dir: Path, video_path: Path, max_retries: int,
) -> bool:
    """后台轮询单个视频，semaphore 控制并发数."""
    async with semaphore:
        for attempt in range(1, max_retries + 1):
            try:
                result = await video_adapter.fetch_result(req, task_id)
                if result.success and result.save_path:
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(result.save_path, str(video_path))
                    size_kb = video_path.stat().st_size // 1024
                    print(f"[{shot_idx}] OK ({size_kb}KB)")
                    return True
                else:
                    print(f"[{shot_idx}] FAIL ({attempt}): {result.error}")
            except Exception as e:
                print(f"[{shot_idx}] FAIL ({attempt}): {e}")
            if attempt < max_retries:
                await asyncio.sleep(5)
        print(f"[{shot_idx}] FATAL: 已达最大重试")
        return False


async def _submit_single_shot(
    video_adapter, semaphore: asyncio.Semaphore,
    shot: dict, prompt: str, frame_paths: list,
    shot_dir: Path, video_path: Path, max_retries: int,
    audio_duration: float = 0,
) -> asyncio.Task | None:
    """提交单个镜头， 返回轮询 Task."""
    from step02_video_gen.prompt_builder import _make_request_async
    idx = shot["index"]
    for attempt in range(1, max_retries + 1):
        try:
            await VIDEO_RATE_LIMITER.acquire()
            req = await _make_request_async(video_adapter, prompt, frame_paths, shot, audio_duration)
            task_id = await video_adapter.submit_task(req)
            tag = f"(重{attempt})" if attempt > 1 else ""
            print(f"[{idx}] 已提交 {tag}")
            # 启动后台轮询
            task = asyncio.create_task(
                _poll_video(video_adapter, semaphore, req, task_id, idx, shot_dir, video_path, max_retries)
            )
            return task
        except Exception as e:
            print(f"[{idx}] 提交失败 ({attempt}): {e}")
            if attempt < max_retries:
                await asyncio.sleep(5)
    print(f"[{idx}] FATAL: 提交失败")
    return None


async def generate_alls(script: dict, working_dir: Path, script_file: Path, force_first_frame: bool = False) -> int:
    """流水线生成：首帧/尾帧预生成 → TTS 预生成(得实际时长) → 按时长提交视频 → 后台并发轮询."""
    from step02_video_gen.audio_pregen import _gen_shot_audio_duration, _update_script_with_actual_durations
    from tts import create_provider
    import os

    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. 预生成首帧/尾帧
    await build_working_dir(script_file, working_dir, image_adapter, force_first_frame=force_first_frame)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = script_file.stat().st_mtime if script_file.exists() else None

    # 2. 预生成 TTS 音频，获取每个镜头的实际时长（核心优化）
    print(f"\n[音频] 预生成 {len(shots)} 个镜头的 TTS 音频，获取实际时长...")
    tts_provider = create_provider()

    # 获取音频输出目录
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

    total_llm = sum(s.get("duration", 0) for s in shots)
    total_audio = sum(shot_audio_durations.values())
    print(f"\n[音频] LLM 预估总长: {total_llm:.0f}s | 音频实际总长: {total_audio:.1f}s")
    print(f"       差异: {abs(total_audio - total_llm):.1f}s")
    if abs(total_audio - total_llm) > 5:
        print(f"       [!] 音频与 LLM 预估偏差较大，视频将按音频实际时长生成")

    actual_concurrency = min(len(shots), VIDEO_CONCURRENCY)
    print(f"\n[流水线] {len(shots)} 镜, ~{total_audio:.0f}s, 间隔{SUBMIT_INTERVAL}s, 轮询{actual_concurrency}路\n")

    # 3. 收集需要生成的镜头（跳过缓存）
    shot_tasks = []
    for shot in shots:
        idx = shot["index"]
        shot_dir = shots_dir / str(idx)
        video_path = shot_dir / "video.mp4"

        # 缓存检查
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            if script_mtime is None or video_path.stat().st_mtime >= script_mtime:
                print(f"[{idx}] 跳过(已缓存)")
                continue

        from step02_video_gen.prompt_builder import _build_prompt
        prompt = _build_prompt(shot, script)
        first_frame = str(shot_dir / "first_frame.png")
        last_frame = str(shot_dir / "last_frame.png")
        frame_paths = [fp for fp in [first_frame, last_frame] if Path(fp).exists()]

        shot_tasks.append({
            "shot": shot,
            "prompt": prompt,
            "frame_paths": frame_paths,
            "shot_dir": shot_dir,
            "video_path": video_path,
            "audio_duration": shot_audio_durations.get(idx, 0),
        })

    if not shot_tasks:
        print("[全部已缓存]")
        return 0

    # 4. 流水线提交 + 后台轮询
    poll_semaphore = asyncio.Semaphore(actual_concurrency)
    poll_all_tasks: list[asyncio.Task] = []
    last_submit_time = 0.0

    for i, info in enumerate(shot_tasks):
        # 提交间隔控制
        if i > 0:
            elapsed = asyncio.get_event_loop().time() - last_submit_time
            if elapsed < SUBMIT_INTERVAL:
                wait = SUBMIT_INTERVAL - elapsed
                print(f"  等待 {int(wait)}s...")
                await asyncio.sleep(wait)

        last_submit_time = asyncio.get_event_loop().time()
        task = await _submit_single_shot(
            video_adapter, poll_semaphore,
            info["shot"], info["prompt"], info["frame_paths"],
            info["shot_dir"], info["video_path"], MAX_RETRIES,
            audio_duration=info["audio_duration"],
        )
        if task:
            poll_all_tasks.append(task)

    # 5. 等待所有轮询完成
    print(f"\n等待 {len(poll_all_tasks)} 个任务完成...\n")
    results = await asyncio.gather(*poll_all_tasks, return_exceptions=True)

    succeeded = sum(1 for r in results if r is True)
    failed = len(results) - succeeded

    print(f"\n完成: {succeeded} 成功 / {failed} 失败")

    # 6. 保存音频实际时长到 JSON
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已保存音频实际时长到 JSON")

    # 7. 回填视频实际时长到 JSON
    from step02_video_gen.audio_pregen import _update_script_with_actual_durations
    _update_script_with_actual_durations(working_dir, script_file)

    return failed


# 为兼容性保留别名
def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    """流水线生成入口（同步包装）."""
    import os
    book_idx = int(os.getenv("BOOK_INDEX", "1"))
    chapter_idx = int(os.getenv("NOVEL_CHAPTER", "1"))
    output_base = Path(os.getenv("OUTPUT_BASE", "./output/video"))
    script_file = output_base / f"book_{book_idx:02d}" / f"chapter_{chapter_idx:02d}" / "script_result.json"
    return asyncio.run(generate_alls(script, working_dir, script_file, force_first_frame))