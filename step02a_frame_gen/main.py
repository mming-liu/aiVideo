"""step02a_frame_gen - 分镜图片生成模块
独立运行：只生成首帧/尾帧/关键帧图片，不提交视频

用途：方便调试 - 可以先单独运行图片生成，确认图片质量后再生成视频

使用方法：
    python step02a_frame_gen/main.py                 # 生成全部镜头帧
    python step02a_frame_gen/main.py --shots 3,5-7   # 只生成指定镜头帧
    python step02a_frame_gen/main.py --shots 3 --force  # 强制重生成（忽略缓存）
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
from shared.shot_selector import parse_shot_selector


async def gen_all_frames(script_path: Path, work_dir: Path, force: bool = False,
                         only_indices: set[int] | None = None) -> dict:
    """生成所有镜头的首帧/尾帧/关键帧图片

    Args:
        force: 忽略缓存强制重生成
        only_indices: 只生成指定 index 的镜头帧（None=全部）

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
        if only_indices is None or 0 in only_indices:
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
        if only_indices is not None and idx not in only_indices:
            continue
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

    # v6.18：gen_shot_frames 内归一化会回写 shot["ff_desc"]（多动作→静态瞬间），
    # 落盘让 script_result.json 反映真实用于生图的文本，也保证 step02b 读到
    # 归一化后的 ff_desc（【视频起点】一致）。只在有归一化发生时落盘。
    with open(script_path, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    changed = False
    disk_shots = {s.get("index"): s.get("ff_desc") for s in on_disk.get("shots", [])}
    for s in script.get("shots", []):
        if s.get("ff_desc") != disk_shots.get(s.get("index")):
            changed = True
            break
    disk_hook = (on_disk.get("hook") or {}).get("ff_desc")
    if (script.get("hook") or {}).get("ff_desc") != disk_hook:
        changed = True
    if changed:
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        logger.info("[INFO] ff_desc 归一化已回写 script_result.json")

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
    import argparse
    import json
    from shared.paths import script_file, vimax_working_dir as wd

    parser = argparse.ArgumentParser(description="step02a 帧图片生成")
    parser.add_argument("--shots", "-s", default=None,
                        help="只生成指定镜头（逗号分隔，支持区间），如 3,5-7,9；默认全部")
    parser.add_argument("--force", "-f", action="store_true",
                        help="忽略缓存强制重生成")
    args = parser.parse_args()

    only_indices = parse_shot_selector(args.shots)

    script_path = script_file()
    work_dir = wd()

    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")

    if not script_path.exists():
        logger.error(f"[ERROR] script not found: {script_path}")
        return

    logger.info(f"[INFO] step02a_frame_gen start")
    logger.info(f"  script: {script_path}")
    logger.info(f"  output: {work_dir / 'shots'}")
    if only_indices is not None:
        logger.info(f"  shots:  {sorted(only_indices)} (指定镜头)")
    if args.force:
        logger.info(f"  force:  True (忽略缓存)")

    # 确保 working dir 结构
    asyncio.run(build_working_dir(
        script_path, work_dir,
        ImageGeneratorAiVideoAdapter(),
        force_first_frame=False,
        skip_frames=True,  # 只设置目录结构，不生成帧
    ))

    # v7: 场景/道具资产图生成（大场景/近景/道具白底图，供 r2v 一致性参考）
    # 资产图是横屏参考，独立于竖屏帧；在帧生成前跑，保证 r2v 参考图就绪
    try:
        from step02a_frame_gen.asset_gen import gen_all_assets
        asset_result = asyncio.run(gen_all_assets(force=args.force))
        logger.info(f"[ASSET] 资产图: 场景{asset_result.get('scenes', 0)} / 道具{asset_result.get('props', 0)}")
    except Exception as e:
        logger.warn(f"[ASSET] 资产图生成失败（不阻断帧生成）: {e}")

    # 生成帧（全部或指定镜头）
    results = asyncio.run(gen_all_frames(
        script_path, work_dir, force=args.force, only_indices=only_indices,
    ))

    logger.info(f"\n[INFO] step02a_frame_gen done!")
    for idx, result in results.items():
        _log_shot_frames(idx, result, logger)


if __name__ == "__main__":
    main()