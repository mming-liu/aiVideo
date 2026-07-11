"""视频生成（ViMax 后端） — 流水线优化版 v2.

核心优化：
  - **音频先行确定时长**（v2 新增）：提交视频前先为该镜头生成 TTS 音频，
    用实际音频时长计算视频帧数（num_frames），保证音画精准对齐
  - 分镜内串行（首帧→尾帧→提交），分镜间流水线（提交后后台轮询，继续处理下一镜）
  - 首帧/尾帧：使用 vimax_bridge.build_working_dir 预生成
  - 视频提交：每60s间隔，后台轮询
  - 时长回填：视频生成后用 ffprobe 测量实际时长，写回 JSON
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_project_root = Path(__file__).resolve().parent

from vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from vimax_bridge import build_working_dir
from vimax.utils.rate_limiter import RateLimiter
from tts import create_provider, TTSRequest
from tts.config import config as tts_config

# 分镜脚本路径需与 step01_script_gen/main.py 输出一致
_BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
_CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
_OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "./output/video"))
SCRIPT_FILE = _OUTPUT_BASE / f"book_{_BOOK_INDEX:02d}" / f"chapter_{_CHAPTER_INDEX:02d}" / "script_result.json"
AUDIO_TTS_DIR = _project_root / "output" / "audio"
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
        raise FileNotFoundError(f"分镜脚本不存在: {path}\n请先运行 step01_script_gen（python step01_script_gen/main.py）")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_ffprobe() -> str:
    """查找 ffprobe 可执行文件."""
    path = shutil.which("ffprobe")
    if path:
        return path
    # 尝试从 FFMPEG_BIN_DIR 查找
    ffmpeg_bin = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if ffmpeg_bin:
        ffprobe_path = Path(ffmpeg_bin) / "ffprobe.exe"
        if ffprobe_path.exists():
            return str(ffprobe_path)
    raise FileNotFoundError("ffprobe 未找到，请确保 FFmpeg 已安装并加入 PATH")


FFPROBE = _find_ffprobe()


def _measure_media_duration(path: Path) -> float:
    """用 ffprobe 测量媒体时长（秒）。"""
    if not path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {path.name}: {e}")
    return 0.0


def _gen_shot_audio_duration(shot: dict, audio_dir: Path, provider=None) -> float:
    """为指定镜头生成 TTS 音频，返回实际时长（秒）.

    核心优化：先生成音频拿到实际时长，再按音频时长计算视频帧数，保证音画精准对齐.
    """
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        print(f"  [{idx}] 无 narration_segment，回退使用 LLM 预估时长")
        return shot.get("duration", 5)

    audio_path = audio_dir / f"shot_{idx}.mp3"
    if audio_path.exists() and audio_path.stat().st_size > 1024:
        duration = _measure_media_duration(audio_path)
        if duration > 0:
            return duration
        audio_path.unlink()

    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        if provider is None:
            provider = create_provider()
        req = TTSRequest(
            text=text,
            model=tts_config.MODEL,
            rate=tts_config.RATE,
            volume=tts_config.VOLUME,
            format=tts_config.FORMAT,
            sample_rate=tts_config.SAMPLE_RATE,
        )
        result = provider.synthesize(req, output=str(audio_path))
        if not result.success:
            raise RuntimeError(result.error)

        duration = _measure_media_duration(audio_path)
        if duration > 0:
            size_kb = audio_path.stat().st_size / 1024
            print(f"  [{idx}] TTS ({size_kb:.0f}KB/{duration:.1f}s)")
            return duration
    except Exception as e:
        print(f"  [{idx}] TTS 失败: {e}")

    print(f"  [{idx}] 回退使用 LLM 预估时长")
    return shot.get("duration", 5)


def _measure_actual_duration(video_path: Path) -> float:
    """用 ffprobe 测量视频的实际时长（秒）."""
    if not video_path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[WARN] 测量时长失败 {video_path.name}: {e}")
    return 0.0


def _update_script_with_actual_durations(working_dir: Path) -> None:
    """测量所有已生成视频的实际时长，回填到 script_Result.json."""
    shots_dir = working_dir / "shots"
    if not shots_dir.exists():
        print("[WARN] shots 目录不存在，跳过时长回填")
        return

    # 读取现有脚本
    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    updated_count = 0

    for shot in shots:
        idx = shot.get("index", 0)
        video_path = shots_dir / str(idx) / "video.mp4"
        if video_path.exists():
            actual_duration = _measure_actual_duration(video_path)
            if actual_duration > 0:
                shot["actual_duration"] = round(actual_duration, 2)
                # 相对于音频时长的偏差（新标准：视频应匹配音频）
                audio_ref = shot.get("actual_audio_duration", 0)
                expected = audio_ref if audio_ref > 0 else shot.get("duration", 5)
                deviation = abs(actual_duration - expected) / expected * 100 if expected > 0 else 0
                shot["duration_deviation_pct"] = round(deviation, 1)
                updated_count += 1

    if updated_count > 0:
        # 保存回脚本文件
        with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 已回填 {updated_count} 个镜头的实际时长到 JSON")
    else:
        print("[INFO] 没有视频文件需要回填时长")


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
    audio_duration: float = 0,
) -> asyncio.Task | None:
    """提交单个镜头，返回轮询 Task."""
    idx = shot["index"]
    for attempt in range(1, max_retries + 1):
        try:
            await VIDEO_RATE_LIMITER.acquire()
            req = _make_request(video_adapter, prompt, frame_paths, shot, audio_duration)
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


def _make_request(video_adapter, prompt: str, frame_paths: list, shot: dict, audio_duration: float = 0) -> "VideoGenerationRequest":
    """构建 VideoGenerationRequest，使用音频实际时长计算视频帧数."""
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
    # 使用音频实际时长替代 LLM 预估的 duration，保证音画对齐
    duration = max(round(audio_duration), 3) if audio_duration > 0 else shot.get("duration", 5)
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
    """流水线生成：首帧/尾帧预生成 → TTS 预生成(得实际时长) → 按时长提交视频 → 后台并发轮询."""
    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. 预生成首帧/尾帧
    await build_working_dir(SCRIPT_FILE, working_dir, image_adapter, force_first_frame=force_first_frame)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None

    # 2. 预生成 TTS 音频，获取每个镜头的实际时长（核心优化）
    print(f"\n[音频] 预生成 {len(shots)} 个镜头的 TTS 音频，获取实际时长...")
    tts_provider = create_provider()
    shot_audio_durations = {}
    for shot in shots:
        idx = shot["index"]
        ad = _gen_shot_audio_duration(shot, AUDIO_TTS_DIR, provider=tts_provider)
        shot_audio_durations[idx] = ad
        shot["actual_audio_duration"] = round(ad, 2)

    total_llm = sum(s.get("duration", 0) for s in shots)
    total_audio = sum(shot_audio_durations.values())
    print(f"\n[音频] LLM 预估总长: {total_llm:.0f}s | 音频实际总长: {total_audio:.1f}s")
    print(f"       差异: {abs(total_audio - total_llm):.1f}s ({abs(total_audio - total_llm)/max(total_llm,1)*100:.0f}%)")
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

    # 6. 保存音频实际时长到 JSON，供 04 合并阶段使用
    with open(SCRIPT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已保存音频实际时长到 JSON")
    # 7. 回填视频实际时长到 JSON
    _update_script_with_actual_durations(working_dir)

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