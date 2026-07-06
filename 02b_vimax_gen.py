"""视频生成（ViMax 后端） — 业务入口.

流水线: 分镜间并行（提交间隔控制 + 提交后并发轮询）+ 分境内并行（首帧/尾帧并行）
  1. build_working_dir: 摄像机组间并行生成首帧/尾帧（分境内并行）
  2. generate_all_videos: 所有分镜按间隔依次提交（避免 API 连续提交报错），
     提交后立即并发轮询等待结果（asyncio.gather），失败自动重试

经 ViMax 适配器调用 aiVideo 的 DashScope/Agnes 视频生成 provider，
产出存入 ViMax working_dir 布局（output/vimax_working/shots/<idx>/video.mp4）.
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
# 视频提交间隔（秒），Agnes API 需要在上一镜提交后等60s再提下一镜，否则报错浪费重试次数
SUBMIT_INTERVAL = float(os.getenv("VIDEO_CLIP_INTERVAL", "60"))
# 并发轮询数（提交后同时等待返回的镜头数）
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "6"))

# 速率限制（从 .env 读取，None = 不限制）
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
    """构建单镜头 prompt，使用 motion_desc + character_profile + context_bridge."""
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")
    audio_desc = shot.get("subtitle", "").strip()
    prompt = f"{motion_desc}\n{audio_desc}" if audio_desc else motion_desc

    extras = []
    cp = script.get("character_profile", "").strip()
    if cp:
        extras.append(f"全局人物统一外貌设定：{cp}")
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"与上一镜视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"
    return prompt


async def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    """分镜间并行：所有镜头并行提交 + 并行轮询（通过 asyncio.gather）."""
    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. 翻译 + 预生成首帧（摄像机组间并行 — 分境内并行）
    await build_working_dir(SCRIPT_FILE, working_dir, image_adapter, force_first_frame=force_first_frame)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None

    total_duration = sum(s.get("duration", 0) for s in shots)
    print(f"\n[PLAN] 分镜间并行：{len(shots)} 镜头，总时长 ~{total_duration}s，提交间隔 {SUBMIT_INTERVAL}s，轮询并发={VIDEO_CONCURRENCY}，每镜最多 {MAX_RETRIES} 次重试\n")

    # 构建每个镜头的生成任务（跳过缓存命中）
    shot_tasks = []
    for i, shot in enumerate(shots):
        idx = shot["index"]
        shot_dir = shots_dir / str(idx)
        video_path = shot_dir / "video.mp4"
        duration = shot.get("duration", 0)
        print(f"[PLAN] 镜头{idx}: {duration}s, 字幕: {shot.get('subtitle', 'N/A')[:60]}")

        # 缓存跳过
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            if script_mtime is not None and video_path.stat().st_mtime >= script_mtime:
                print(f"  [OK] 跳过已生成（{video_path.stat().st_size//1024}KB）")
                continue
            else:
                print(f"  [WARN] prompt 已更新，重新生成")

        prompt = _build_prompt(shot, script)
        first_frame = str(shot_dir / "first_frame.png")
        last_frame = str(shot_dir / "last_frame.png")
        frame_paths = []
        if Path(first_frame).exists():
            frame_paths.append(first_frame)
        if Path(last_frame).exists():
            frame_paths.append(last_frame)

        shot_tasks.append({
            "shot": shot,
            "prompt": prompt,
            "frame_paths": frame_paths,
            "shot_dir": shot_dir,
            "video_path": video_path,
        })

    if not shot_tasks:
        print(f"\n[全部分镜均已缓存]")
        return 0

    # 分镜间并行：按间隔依次提交，提交后立即并发轮询
    print(f"\n[PARALLEL] {len(shot_tasks)} 镜需生成，提交间隔 {SUBMIT_INTERVAL}s，轮询并发={VIDEO_CONCURRENCY}\n")

    # 提交队列：每 SUBMIT_INTERVAL 秒提交一个，已提交的立即开始轮询
    poll_semaphore = asyncio.Semaphore(VIDEO_CONCURRENCY)
    poll_tasks: list[asyncio.Task] = []

    for i, info in enumerate(shot_tasks):
        # 等待提交间隔（第一个镜头不等）
        if i > 0:
            print(f"  [间隔] 等待 {SUBMIT_INTERVAL}s 再提交下一镜...")
            await asyncio.sleep(SUBMIT_INTERVAL)

        # 提交当前镜头
        task = asyncio.create_task(
            _gen_single_shot_with_retry(
                video_adapter, poll_semaphore,
                info["shot"], info["prompt"], info["frame_paths"],
                info["shot_dir"], info["video_path"], MAX_RETRIES,
            )
        )
        poll_tasks.append(task)

    # 等待所有轮询完成
    print(f"\n[WAIT] 所有镜头已提交，等待剩余任务完成...\n")
    results = await asyncio.gather(*poll_tasks, return_exceptions=True)

    succeeded = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is not True)

    print(f"\n{'='*50}")
    print(f"[DONE] 生成完成：{succeeded} 成功 / {failed} 失败（共 {len(shot_tasks)} 镜）")
    return failed


async def _gen_single_shot_with_retry(
    video_adapter, semaphore: asyncio.Semaphore,
    shot: dict, prompt: str, frame_paths: list,
    shot_dir: Path, video_path: Path, max_retries: int,
) -> bool:
    """单个镜头的提交 + 轮询 + 重试（提交后并发轮询，受 semaphore 控制同时轮询数）."""
    idx = shot["index"]
    for attempt in range(1, max_retries + 1):
        try:
            req = await _make_request_async(video_adapter, prompt, frame_paths, shot)
            await VIDEO_RATE_LIMITER.acquire()
            task_id = await video_adapter.submit_task(req)
            tag = f"(重试{attempt}/{max_retries})" if attempt > 1 else ""
            print(f"  [镜头{idx}] 已提交 {tag} task_id={task_id[:40]}...")

            # 提交后立即并发轮询（semaphore 控制同时轮询的镜头数）
            async with semaphore:
                result = await video_adapter.fetch_result(req, task_id)
            if result.success and result.save_path:
                shot_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result.save_path, str(video_path))
                print(f"  [镜头{idx}] [OK] 生成完成（{video_path.stat().st_size//1024}KB）")
                return True
            else:
                print(f"  [镜头{idx}] [FAIL] 第{attempt}次: {result.error}")
        except Exception as e:
            print(f"  [镜头{idx}] [FAIL] 第{attempt}次异常: {e}")
        if attempt < max_retries:
            print(f"  [镜头{idx}] 重试 ({attempt + 1}/{max_retries})...")

    print(f"  [镜头{idx}] [FATAL] 已达最大重试次数 {max_retries}")
    return False


async def _make_request_async(video_adapter, prompt: str, frame_paths: list, shot: dict):
    """构建 VideoGenerationRequest，支持多参考图（首帧+尾帧）+ 动态 duration."""
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
    """first_frame 不存在时，从 character_portraits_registry.json 找可用参考图."""
    registry_path = working_dir / "character_portraits_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for ident, views in registry.items():
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 使用角色 {ident} 正面肖像作为视频参考图")
                return front
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  ViMax 后端视频生成任务启动（分镜间 + 分境内并行）")
    print("=" * 60)

    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")
    total_duration = sum(s.get("duration", 0) for s in shots)
    print(f"\n分镜脚本: {len(shots)} 个镜头，预计总时长 ~{total_duration}s")
    print(f"旁白({len(narration)}字): {narration[:60]}...\n")

    auto_confirm = os.getenv("VIDEO_AUTO_CONFIRM", "").strip() == "1"
    print(f"[提示] 分镜间并行：每隔 {SUBMIT_INTERVAL}s 提交一镜，提交后并发轮询（{VIDEO_CONCURRENCY}路）")
    if auto_confirm:
        answer = "y"
        print("确认开始生成？(y/N): y（VIDEO_AUTO_CONFIRM=1 自动确认）")
    else:
        answer = input("确认开始生成？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    force_ff = os.getenv("SCRIPT_FIRST_FRAME_FORCE", "0").strip() == "1"
    failed = asyncio.run(generate_all(script, WORKING_DIR, force_first_frame=force_ff))
    if failed:
        sys.exit(1)
