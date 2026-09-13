"""step02b_video_gen - 视频生成模块
只提交视频生成任务并轮询，按 .env.content 的 VIDEO_REFERENCE_MODE 统一生成全部镜头视频。

模式（全程统一，不分 Mode A/B，镜头间无依赖、可并行提交 + 后台轮询）：
    text - 纯文生视频（跳过所有帧/参考图）
    r2v  - 参考图生视频：参考图由 prompt_builder 权威收集
           （character_portraits_registry.json 出镜角色肖像，视角匹配，最多5个）
    image- 图生视频：复用 step02a 生成的首帧/尾帧/关键帧图片

使用方法：
    python step02b_video_gen/main.py                 # 生成全部镜头视频
    python step02b_video_gen/main.py --shots 3,5-7   # 只生成指定镜头视频
    python step02b_video_gen/main.py --shots 3 --force  # 强制重生成（忽略缓存）

注意：image 模式需要先运行 step02a_frame_gen 生成帧图片。
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

from shared.paths import (
    script_file, vimax_working_dir, chapter_dir,
    vimax_shots_dir, SHOT_VIDEO_FILENAME, FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME,
)
from shared.tee_logger import get_logger
from step02_video_gen.submit_poll import _submit_video, _get_shot_hash, _failed_videos
from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
from shared.shot_selector import parse_shot_selector


async def gen_all_videos(script: dict, working_dir: Path, script_file_path: Path,
                         only_indices: set[int] | None = None,
                         force: bool = False) -> int:
    """按 VIDEO_REFERENCE_MODE 统一生成全部镜头视频，并行提交 + 后台轮询。

    统一模式：全部镜头按 .env.content 的 VIDEO_REFERENCE_MODE（text/r2v/image）
    处理，不做 Mode A/B 双模调度、不提取尾帧接续——参考图收集由
    prompt_builder._make_request 按模式权威完成。

    Args:
        only_indices: 只生成指定 index 的镜头视频（None=全部）
        force: 忽略缓存强制重生成

    Returns:
        int: 失败镜头数量
    """
    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02b.log", console_level="INFO")

    # 2026-09-13 审计修复：入口清空失败记录——旧入口 generate_alls() 会清，
    # 本入口不清，同进程复用/测试/工具调用会累计上一轮旧失败
    _failed_videos.clear()

    shots = script.get("shots", [])
    hook = script.get("hook")
    shots_dir = working_dir / "shots"
    script_mtime = script_file_path.stat().st_mtime if script_file_path.exists() else None

    # 加载配置（并发单源 shared/config.py，默认 4）
    from shared.config import VIDEO_CONCURRENCY
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()

    # Adapter
    video_adapter = VideoGeneratorAiVideoAdapter()

    logger.info(f"[INFO] Reference mode: {REFERENCE_MODE}（全程统一，不分 Mode A/B）")

    actual_concurrency = max(1, min(len(shots) + (1 if hook else 0), VIDEO_CONCURRENCY))
    poll_semaphore = asyncio.Semaphore(actual_concurrency)

    def _selected(idx: int) -> bool:
        return only_indices is None or idx in only_indices

    # 收集待生成任务：hook + 全部选中镜头（无先后依赖，全部并行提交）
    tasks = []
    if hook and _selected(0):
        tasks.append(hook)
    for shot in shots:
        if _selected(shot["index"]):
            tasks.append(shot)

    polls = []
    for shot in tasks:
        poll_task = await _process_one_shot_skip_frames(
            shot, shots_dir, video_adapter,
            script_mtime, script,
            poll_semaphore=poll_semaphore,
            force=force,
        )
        if poll_task is not None:
            polls.append((shot["index"], poll_task))

    # 后台轮询全部任务
    if polls:
        results = await asyncio.gather(*(pt for _, pt in polls), return_exceptions=True)
        for (idx, _), res in zip(polls, results):
            if isinstance(res, Exception):
                logger.warn(f"  [Shot {idx}] 轮询异常: {res}")

    # v6.35（08-15，P18 遗留）：失败计数修真——此前只统计 gather 抛出的
    # Exception，_poll_video 正常 return False（终态错误/QC 失败/超时）与
    # _submit_video 失败（return None）都没被计入，坏镜头带 Failed: 0 假象
    # 流进 step04。统一以 submit_poll._failed_videos 为准（提交失败 + 轮询
    # 失败 + QC 失败全部记录在内）。
    failed = len(_failed_videos)
    if failed:
        logger.warn(f"[FAIL] 失败镜头统计: {failed} 个（含提交失败/QC 失败/轮询失败）")
        for _idx, recs in _failed_videos.items():
            logger.warn(f"  [Shot {_idx}] {recs[-1].get('error', 'unknown')}")

    return failed


async def _process_one_shot_skip_frames(
    shot: dict, shots_dir: Path, video_adapter,
    script_mtime: float | None, script: dict,
    poll_semaphore: asyncio.Semaphore,
    force: bool = False,
):
    """处理单个镜头：跳过帧生成，直接提交视频（复用已有帧）。

    force=True 时忽略缓存强制重新提交视频。
    r2v/text 模式不依赖任何帧图片；image 模式复用 step02a 的首帧/尾帧/关键帧。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir

    logger = get_logger(chapter_dir() / "step02b.log", console_level="INFO")

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / SHOT_VIDEO_FILENAME

    # Cache check（force=True 时跳过）
    shot_hash = _get_shot_hash(shot, script)
    if not force and video_path.exists() and video_path.stat().st_size > 20 * 1024:
        hash_path = shot_dir / "prompt_hash.txt"
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            if old_hash == shot_hash:
                from shared.paths import clip_file
                _clip_dst = clip_file(idx)
                if _clip_dst.exists() and _clip_dst.stat().st_size > 20 * 1024:
                    logger.info(f"  [Shot {idx}] Cache hit, skip")
                    return None

    # 按模式收集参考图（r2v/text 不需要帧图片；image 复用 step02a 帧）
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    if REFERENCE_MODE == "r2v" or REFERENCE_MODE == "text":
        # r2v：参考图由 prompt_builder._make_request 权威收集（角色肖像），
        # 此处不传任何路径，避免传递不存在的 keyframes/ 子目录（旧逻辑断链）。
        # text：纯文生视频，不使用任何帧图片。
        frame_paths = []
        logger.info(f"  [Shot {idx}] Mode: {REFERENCE_MODE.upper()} (no frame files)")
    else:
        # image 模式：使用首帧/尾帧/关键帧
        ff = shot_dir / FIRST_FRAME_FILENAME
        lf = shot_dir / LAST_FRAME_FILENAME
        kf = shot_dir / "keyframes"
        kf_paths = []
        if kf.exists():
            kf_paths = [str(p) for p in kf.glob("*.png")]
        frame_paths = [str(p) for p in [ff, lf] if p.exists()]
        frame_paths.extend(kf_paths)
        logger.info(f"  [Shot {idx}] Mode: IMAGE (reuse step02a frames)")

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, script, idx,
        continuity_ref_image=None, mode="A",
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

    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    shots_dir = vimax_shots_dir()

    # r2v/text 模式不需要帧图片（r2v 参考图来自角色肖像注册表，非帧）
    if REFERENCE_MODE in ("r2v", "text"):
        logger.info(f"[INFO] Reference mode: {REFERENCE_MODE}（无需帧图片，跳过帧检查）")
    elif shots_dir.exists():
        # image 模式：提示缺帧，但允许继续（提交时缺帧自动降级 text-to-video）
        missing_frames = []
        for d in shots_dir.iterdir():
            if not d.is_dir():
                continue
            try:
                idx = int(d.name)
            except ValueError:
                continue
            if only_indices is not None and idx not in only_indices:
                continue
            ff = d / FIRST_FRAME_FILENAME
            lf = d / LAST_FRAME_FILENAME
            if idx == 0 or idx == 1:
                if not ff.exists() or not lf.exists():
                    missing_frames.append((idx, "ff+lf"))
            else:
                if not lf.exists():
                    missing_frames.append((idx, "lf"))

        if missing_frames:
            logger.warn(f"[WARN] Missing frames (image 模式将降级为 text-to-video):")
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

    # 将可能回填的字段写回 script（原子写，防半截 JSON；2026-09-13 审计修复）
    from shared.json_io import atomic_dump_json
    atomic_dump_json(script_path, script, ensure_ascii=False, indent=2)

    # 2026-09-13 审计修复：回填实际视频时长（actual_duration / duration_deviation_pct）
    # ——此前注释承诺但从未调用，元数据契约缺失
    try:
        from step02_video_gen.audio_pregen import _update_script_with_actual_durations
        _update_script_with_actual_durations(work_dir, script_path)
    except Exception as e:
        logger.warn(f"[WARN] 实际时长回填失败（不影响已生成视频）: {e}")

    logger.info(f"\n[INFO] step02b_video_gen done! Failed: {failed}")


if __name__ == "__main__":
    main()
