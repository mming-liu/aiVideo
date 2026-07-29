"""step02a_frame_gen - 分镜图片生成模块
独立运行：只生成首帧/尾帧/关键帧图片，不提交视频

用途：方便调试 - 可以先单独运行图片生成，确认图片质量后再生成视频

使用方法：
    python step02a_frame_gen/main.py
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

from shared.vimax_adapters import ImageGeneratorAiVideoAdapter
from shared.paths import script_file, chapter_dir, vimax_working_dir
from shared.tee_logger import get_logger
from shared.vimax_bridge import build_working_dir


async def gen_all_frames(script_path: Path, work_dir: Path, force: bool = False) -> dict:
    """生成所有镜头的首帧/尾帧/关键帧图片

    Returns:
        dict: {shot_index: {"ff": path, "lf": path, "kf": [paths]}}
    """
    from shared.vimax_bridge import gen_shot_frames
    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02a.log", console_level="INFO")

    # 加载 script
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    hook = script.get("hook")
    characters = script.get("characters", [])
    shots_dir = work_dir / "shots"

    image_adapter = ImageGeneratorAiVideoAdapter()
    script_mtime = script_path.stat().st_mtime if script_path.exists() else None

    results = {}

    # 生成 hook 帧
    if hook and hook.get("prompt"):
        logger.info(f"\n[INFO] ===== Hook frame generation (index=0) =====")
        hook["index"] = 0
        hook_result = await gen_shot_frames(
            hook, shots_dir, image_adapter,
            script_mtime, force=force,
            characters=characters,
            skip_first_frame=False,
        )
        results[0] = hook_result
        _log_shot_frames(0, hook_result, logger)

    # 生成各镜头帧
    for shot in shots:
        idx = shot["index"]
        logger.info(f"\n[INFO] ===== Shot {idx} frame generation =====")

        # Mode A: shot 0, 1 使用完整帧生成
        # Mode B: shot >=2 跳过首帧（用上一镜尾帧）
        skip_first = idx >= 2  # Mode B start

        result = await gen_shot_frames(
            shot, shots_dir, image_adapter,
            script_mtime, force=force,
            characters=characters,
            skip_first_frame=skip_first,
        )
        results[idx] = result
        _log_shot_frames(idx, result, logger)

    return results


def _log_shot_frames(idx: int, result: dict, logger):
    """日志输出帧生成结果"""
    ff = result.get("ff_path") or result.get("ff")
    lf = result.get("lf_path") or result.get("lf")
    kfs = result.get("keyframes_paths") or result.get("kf", [])

    logger.info(f"  Shot {idx} frames:")
    logger.info(f"    - First frame:  {Path(ff).name if ff else 'SKIP'}")
    logger.info(f"    - Last frame:   {Path(lf).name if lf else 'SKIP'}")
    logger.info(f"    - Keyframes:    {[Path(k).name for k in kfs] if kfs else 'NONE'}")


def main():
    import json
    from shared.paths import script_file, vimax_working_dir as wd

    script_path = script_file()
    work_dir = wd()

    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")

    if not script_path.exists():
        logger.error(f"[ERROR] script not found: {script_path}")
        return

    logger.info(f"[INFO] step02a_frame_gen start")
    logger.info(f"  script: {script_path}")
    logger.info(f"  output: {work_dir / 'shots'}")

    # 确保 working dir 结构
    asyncio.run(build_working_dir(
        script_path, work_dir,
        ImageGeneratorAiVideoAdapter(),
        force_first_frame=False,
        skip_frames=True,  # 只设置目录结构，不生成帧
    ))

    # 生成所有帧
    results = asyncio.run(gen_all_frames(script_path, work_dir, force=False))

    logger.info(f"\n[INFO] step02a_frame_gen done!")
    for idx, result in results.items():
        _log_shot_frames(idx, result, logger)


if __name__ == "__main__":
    main()