"""step02b_video_gen - 视频生成模块
只提交视频生成任务并轮询，复用已生成的帧图片

使用方法：
    python step02b_video_gen/main.py                 # 生成全部镜头视频
    python step02b_video_gen/main.py --shots 3,5-7   # 只生成指定镜头视频
    python step02b_video_gen/main.py --shots 3 --force  # 强制重生成（忽略缓存）

注意：需要先运行 step02a_frame_gen 生成帧图片
指定镜头为 Mode B（index>=2）时，会复用上一镜已生成视频的尾帧做 I2V 接续；
若上一镜视频缺失则降级为 text-to-video。
"""
from __future__ import annotations
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import json
import os

from step02_video_gen.audio_pregen import _gen_shot_audio_duration
from tts import create_provider
from shared.paths import script_file, vimax_working_dir, chapter_dir, audio_dir as _audio_dir
from shared.tee_logger import get_logger
from step02_video_gen.submit_poll import _submit_video, _get_shot_hash, _check_keyframe_hallucination
from step02_video_gen.continuity import ContinuityTracker
from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
from shared.vimax_bridge import gen_shot_frames
from shared.shot_selector import parse_shot_selector


async def gen_all_videos(script: dict, working_dir: Path, script_file_path: Path,
                         only_indices: set[int] | None = None,
                         force: bool = False) -> int:
    """只生成视频，复用已有帧图片

    Args:
        only_indices: 只生成指定 index 的镜头视频（None=全部）
        force: 忽略缓存强制重生成

    Returns:
        int: 失败镜头数量
    """
    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02b.log", console_level="INFO")

    shots = script.get("shots", [])
    hook = script.get("hook")
    shots_dir = working_dir / "shots"
    script_mtime = script_file_path.stat().st_mtime if script_file_path.exists() else None

    # 加载配置
    import os
    VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "4"))
    MODE_B_START_INDEX = int(os.getenv("MODE_B_START_INDEX", "2"))
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()

    # Adapter
    video_adapter = VideoGeneratorAiVideoAdapter()
    tts_provider = create_provider()
    audio_dir = _audio_dir()

    # TTS 生成音频（获取时长）—— 只为待生成镜头生成，避免无谓 TTS 调用
    shot_audio_durations = {}
    for shot in shots:
        idx = shot["index"]
        if only_indices is not None and idx not in only_indices:
            continue
        ad = _gen_shot_audio_duration(shot, audio_dir, provider=tts_provider)
        shot_audio_durations[idx] = ad
        shot["actual_audio_duration"] = round(ad, 2)

    if hook and hook.get("prompt"):
        hook["index"] = 0
        if only_indices is None or 0 in only_indices:
            hook_audio_dur = _gen_shot_audio_duration(hook, audio_dir, provider=tts_provider)
            hook["actual_audio_duration"] = round(hook_audio_dur, 2)
            shot_audio_durations[0] = hook_audio_dur

    total_audio = sum(shot_audio_durations.values())
    logger.info(f"[INFO] Total audio duration: {total_audio:.1f}s")
    logger.info(f"[INFO] Reference mode: {REFERENCE_MODE}")

    actual_concurrency = min(len(shots), VIDEO_CONCURRENCY)
    poll_semaphore = asyncio.Semaphore(max(1, actual_concurrency))

    tracker = ContinuityTracker(working_dir)

    def _selected(idx: int) -> bool:
        return only_indices is None or idx in only_indices

    # ---- Mode A: hook + shot 0 + shot 1 并行 ----
    mode_a_tasks = []
    if hook and _selected(0):
        tracker.record_mode(0, "A")
        mode_a_tasks.append((hook, "A", None))

    # shot 0 (index=0)
    if len(shots) > 0 and _selected(shots[0]["index"]):
        s0 = shots[0]
        tracker.record_mode(s0["index"], "A")
        mode_a_tasks.append((s0, "A", None))

    # shot 1 (index=1)
    if len(shots) > 1 and _selected(shots[1]["index"]):
        s1 = shots[1]
        tracker.record_mode(s1["index"], "A")
        mode_a_tasks.append((s1, "A", None))

    mode_a_polls = []
    for shot, mode, tail_ref in mode_a_tasks:
        idx = shot["index"]
        ad = shot_audio_durations.get(idx, 0)
        logger.info(f"  [Shot {idx}] Mode A")

        poll_task = await _process_one_shot_skip_frames(
            shot, shots_dir, video_adapter,
            script_mtime, script,
            audio_duration=ad,
            poll_semaphore=poll_semaphore,
            continuity_ref_image=None,
            mode="A",
            force=force,
        )
        if poll_task is not None:
            mode_a_polls.append(poll_task)

    # 等待 Mode A 完成
    if mode_a_polls:
        await asyncio.gather(*mode_a_polls)

    # 更新 continuity tracker
    for shot_dict, _, _ in mode_a_tasks:
        idx = shot_dict["index"]
        video_path = shots_dir / str(idx) / "video.mp4"
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            tracker.advance(shot_dict, video_path)

    # ---- Mode B: shot >=2 串行 ----
    remaining = [s for s in shots
                 if s["index"] >= MODE_B_START_INDEX and _selected(s["index"])]
    failed_count = 0

    for shot in remaining:
        idx = shot["index"]
        ad = shot_audio_durations.get(idx, 0)
        tracker.record_mode(idx, "B")

        # 指定镜头模式下选择集可能有空洞：上一镜不在本次生成范围内时，
        # tracker 没有它的尾帧。此时从上一镜已生成视频补提尾帧做 I2V 接续，
        # 保持画面连贯；上一镜视频也缺失则降级 text-to-video。
        tail_ref = tracker.get_tail_frame()
        if tail_ref is None and only_indices is not None:
            prev_video = shots_dir / str(idx - 1) / "video.mp4"
            if prev_video.exists() and prev_video.stat().st_size > 20 * 1024:
                tail_ref = tracker.advance({"index": idx - 1}, prev_video)
                logger.info(f"  [Shot {idx}] 复用上一镜(shot {idx-1})已有视频尾帧做接续")

        logger.info(f"  [Shot {idx}] Mode B (tail={Path(tail_ref).name if tail_ref else 'NONE'})")

        poll_task = await _process_one_shot_skip_frames(
            shot, shots_dir, video_adapter,
            script_mtime, script,
            audio_duration=ad,
            poll_semaphore=poll_semaphore,
            continuity_ref_image=tail_ref,
            mode="B",
            force=force,
        )

        if poll_task is not None:
            # 等待本镜头完成
            await poll_task
            # 更新 tracker
            video_path = shots_dir / str(idx) / "video.mp4"
            if video_path.exists() and video_path.stat().st_size > 20 * 1024:
                tracker.advance(shot, video_path)
            else:
                failed_count += 1

    return failed_count


async def _process_one_shot_skip_frames(
    shot: dict, shots_dir: Path, video_adapter,
    script_mtime: float | None, script: dict, audio_duration: float,
    poll_semaphore: asyncio.Semaphore,
    continuity_ref_image: str | None = None,
    mode: str = "A",
    force: bool = False,
):
    """处理单个镜头：跳过帧生成，直接提交视频（复用已有帧）

    force=True 时忽略缓存强制重新提交视频。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    logger = get_logger(chapter_dir() / "step02b.log", console_level="INFO")

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / "video.mp4"

    # Cache check（force=True 时跳过）
    shot_hash = _get_shot_hash(shot, script)
    if not force and video_path.exists() and video_path.stat().st_size > 20 * 1024:
        hash_path = shot_dir / "prompt_hash.txt"
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            if old_hash == shot_hash:
                from shared.paths import clips_dir as _clips_dir
                clips_d = _clips_dir()
                clip_file = clips_d / f"clip_{idx}.mp4"
                if clip_file.exists() and clip_file.stat().st_size > 20 * 1024:
                    logger.info(f"  [Shot {idx}] Cache hit, skip")
                    return None

    # 收集已有帧路径（跳过 gen_shot_frames）
    # VIDEO_REFERENCE_MODE=text 时强制纯 T2V，跳过所有帧图片
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    if REFERENCE_MODE == "text":
        # 纯文生视频，不使用任何帧图片
        frame_paths = []
        logger.info(f"  [Shot {idx}] Mode: T2V (text-only, no frames)")
    elif REFERENCE_MODE == "r2v":
        # R2V 模式：使用参考图（最多5个）保持角色/道具一致性，不传首帧/尾帧
        kf = shot_dir / "keyframes"
        kf_paths = []
        if kf.exists():
            kf_paths = [str(p) for p in sorted(kf.glob("*.png"))[:5]]  # 最多5个
        frame_paths = kf_paths
        logger.info(f"  [Shot {idx}] Mode: R2V (reference images only, max 5)")
    else:
        # image 模式：使用首帧/尾帧/关键帧
        ff = shot_ / "first_frame.png"
        lf = shot_dir / "last_frame.png"
        kf = shot_dir / "keyframes"
        kf_paths = []
        if kf.exists():
            kf_paths = [str(p) for p in kf.glob("*.png")]

        frame_paths = [str(p) for p in [ff, lf] if p.exists()]

        # Mode B: 用上一镜尾帧替代首帧
        if mode == "B" and continuity_ref_image:
            frame_paths = [continuity_ref_image] + [p for p in frame_paths if "last_frame" in p]
            frame_paths.extend(kf_paths)
        else:
            frame_paths.extend(kf_paths)

    logger.info(f"  [Shot {idx}] Frames: {[Path(p).name for p in frame_paths]}")

    # Mode B 没有 continuity_ref_image 也允许继续（降级 text-to-video）
    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, audio_duration, script, idx,
        continuity_ref_image=continuity_ref_image, mode=mode,
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="step02b 视频生成")
    parser.add_argument("--shots", "-s", default=None,
                        help="只生成指定镜头（逗号分隔，支持区间），如 3,5-7,9；默认全部")
    parser.add_argument("--force", "-f", action="store_true",
                        help="忽略缓存强制重生成")
    args = parser.parse_args()

    only_indices = parse_shot_selector(args.shots)

    script_path = script_file()
    work_dir = vimax_working_dir()

    logger = get_logger(chapter_dir() / "step02b.log", console_level="INFO")

    if not script_path.exists():
        logger.error(f"[ERROR] script not found: {script_path}")
        return

    # Ensure shots dir structure for text-to-video mode (no frame images needed)
    shots_dir = work_dir / "shots"
    if not shots_dir.exists():
        logger.info(f"[INFO] shots dir not found, text-to-video mode (no frame images)")

    # Log frame availability (recover with text-to-video if missing)
    missing_frames = []
    if shots_dir.exists():
        for d in shots_dir.iterdir():
            if not d.is_dir():
                continue
            try:
                idx = int(d.name)
            except ValueError:
                continue
            # 指定镜头模式下只检查待生成镜头
            if only_indices is not None and idx not in only_indices:
                continue
            ff = d / "first_frame.png"
            lf = d / "last_frame.png"

            if idx == 0 or idx == 1:
                if not ff.exists() or not lf.exists():
                    missing_frames.append((idx, "ff+lf"))
            else:
                if not lf.exists():
                    missing_frames.append((idx, "lf"))

        if missing_frames:
            logger.warn(f"[WARN] Missing frames:")
            for idx, needed in missing_frames:
                logger.warn(f"    Shot {idx}: missing {needed}")

    logger.info(f"[INFO] step02b_video_gen start")
    logger.info(f"  script: {script_path}")
    logger.info(f"  shots_dir: {shots_dir}")
    if only_indices is not None:
        logger.info(f"  shots: {sorted(only_indices)} (指定镜头)")
    if args.force:
        logger.info(f"  force: True (忽略缓存)")

    # 加载 script
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    failed = asyncio.run(gen_all_videos(
        script, work_dir, script_path,
        only_indices=only_indices, force=args.force,
    ))

    
    # 将 actual_audio_duration 写回 script（供 step04 使用）
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    logger.info(f"\n[INFO] step02b_video_gen done! Failed: {failed}")


if __name__ == "__main__":
    main()