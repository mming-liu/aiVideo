"""视频生成（ViMax 后端） — 流水线优化版.

核心优化：分镜内串行（首帧→尾帧→提交），分镜间流水线（提交后后台轮询，继续处理下一镜）
  - 首帧/尾帧：使用 vimax_bridge.build_working_dir 预生成
  - 视频提交：每60s间隔，后台轮询
  - 打印简化：只用 [idx] 已提交 / [idx] OK / [idx] FAIL
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_project_root = Path(__file__).resolve().parent

from vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from vimax_bridge import build_working_dir
from vimax.utils.rate_limiter import RateLimiter

SCRIPT_FILE = _project_root / "script_result.json"
WORKING_DIR = Path(os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working"))
MAX_RETRIES = int(os.getenv("VIDEO_MAX_RETRIES", "3"))
SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "60"))
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))

_VIDEO_RPM = os.getenv("VIDEO_MAX_REQUESTS_PER_MINUTE")
VIDEO_RATE_LIMITER = RateLimiter(
    max_requests_per_minute=int(_VIDEO_RPM) if _VIDEO_RPM else None,
)


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"分镜脚本不存在: {path}\n请先运行 01_script_gen.py")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_prompt(shot: dict, script: dict) -> str:
    """构建单镜头 prompt，motion_desc + subtitle + context_bridge."""
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")
    audio_desc = shot.get("subtitle", "").strip()
    prompt = f"{motion_desc}\n{audio_desc}" if audio_desc else motion_desc

    extras = []
    # 不再使用 character_profile，通过角色名查画像
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"与上一镜视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"
    return prompt


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
) -> asyncio.Task | None:
    """提交单个镜头，返回轮询 Task."""
    idx = shot["index"]
    for attempt in range(1, max_retries + 1):
        try:
            await VIDEO_RATE_LIMITER.acquire()
            req = await _make_request_async(video_adapter, prompt, frame_paths, shot)
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


async def _make_request_async(video_adapter, prompt: str, frame_paths: list, shot: dict):
    """构建 VideoGenerationRequest."""
    from _video_gen import VideoGenerationRequest
    from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt

    full_prompt = sanitize_prompt(ensure_prefix(prompt, STYLE_PREFIX))
    image_path = None
    for fp in (frame_paths or []):
        if fp and Path(fp).exists():
            image_path = fp
            break
    if not image_path:
        image_path = _find_fallback_reference(
            Path(os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working"))
        )
    duration = shot.get("duration", 5)
    return VideoGenerationRequest(
        prompt=full_prompt,
        clip_index=shot["index"],
        duration=duration,
        negative_prompt=NEGATIVE_PROMPT,
        size="960x1792",
        image_path=image_path,
    )


def _find_fallback_reference(working_dir: Path) -> str | None:
    """从 character_portraits_registry.json 找可用参考图."""
    registry_path = working_dir / "character_portraits_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for ident, views in registry.items():
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 使用角色 {ident} 正面肖像")
                return front
    except Exception:
        pass
    return None


async def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    """流水线生成：首帧/尾帧预生成 → 顺序提交视频 → 后台并发轮询."""
    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. 预生成首帧/尾帧
    await build_working_dir(SCRIPT_FILE, working_dir, image_adapter, force_first_frame=force_first_frame)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None

    total_duration = sum(s.get("duration", 0) for s in shots)
    actual_concurrency = min(len(shots), VIDEO_CONCURRENCY)
    print(f"\n[流水线] {len(shots)} 镜, ~{total_duration}s, 间隔{SUBMIT_INTERVAL}s, 轮询{actual_concurrency}路\n")

    # 2. 收集需要生成的镜头（跳过缓存）
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
        })

    if not shot_tasks:
        print("[全部已缓存]")
        return 0

    # 3. 流水线提交 + 后台轮询
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
        )
        if task:
            poll_all_tasks.append(task)

    # 4. 等待所有轮询完成
    print(f"\n等待 {len(poll_all_tasks)} 个任务完成...\n")
    results = await asyncio.gather(*poll_all_tasks, return_exceptions=True)

    succeeded = sum(1 for r in results if r is True)
    failed = len(results) - succeeded

    print(f"\n完成: {succeeded} 成功 / {failed} 失败")
    return failed


if __name__ == "__main__":
    print("=" * 50)
    print("  ViMax 视频生成 (流水线优化版)")
    print("=" * 50)

    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")
    total_duration = sum(s.get("duration", 0) for s in shots)
    print(f"\n分镜: {len(shots)} 个, ~{total_duration}s")
    print(f"旁白: {narration[:50]}...")

    auto_confirm = os.getenv("VIDEO_AUTO_CONFIRM", "").strip() == "1"
    if auto_confirm:
        answer = "y"
        print("确认: y (AUTO)")
    else:
        answer = input("\n确认生成? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消")
        sys.exit(0)

    force_ff = os.getenv("SCRIPT_FIRST_FRAME_FORCE", "0").strip() == "1"
    failed = asyncio.run(generate_all(script, WORKING_DIR, force_first_frame=force_ff))
    if failed:
        sys.exit(1)