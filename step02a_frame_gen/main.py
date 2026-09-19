"""step02a_frame_gen - 分镜图片生成模块
独立运行：只生成首帧/尾帧/关键帧图片，不提交视频

用途：方便调试 - 可以先单独运行图片生成，确认图片质量后再生成视频

使用方法：
    python step02a_frame_gen/main.py                 # 生成全部镜头帧
    python step02a_frame_gen/main.py --shots 3,5-7   # 只生成指定镜头帧
    python step02a_frame_gen/main.py --shots 3 --force  # 强制重生成（忽略缓存）
    python step02a_frame_gen/main.py --shots 3 --all-frames  # 一次生成 shot 3 全部图片
                            # （首帧+尾帧+全部关键帧；默认每镜已生成首帧）
    python step02a_frame_gen/main.py --shots 3 --frames lf   # 只生成 shot 3 尾帧
    python step02a_frame_gen/main.py --shots 3 --frames ff,kf0  # 首帧+第0个关键帧
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
from shared.shot_selector import parse_shot_selector, parse_frame_selector


async def gen_all_frames(script_path: Path, work_dir: Path, force: bool = False,
                         only_indices: set | None = None,
                         frames: set[str] | None = None,
                         concurrency: int | None = None) -> dict:
    """生成所有镜头的首帧/尾帧/关键帧图片

    Args:
        force: 忽略缓存强制重生成
        only_indices: 只生成指定 index 的镜头帧（None=全部）
        frames: 帧类型选择（ff/lf/kf/kf0/kf1...，见 parse_frame_selector）——
            None=默认 Mode A/B 行为；提供时精确控制生成哪些图片（调试用）
        concurrency: 镜头级并发数（2026-09-12 新增；None=读 STEP02A_CONCURRENCY
            env，默认 3）。1=串行（旧行为）。每镜一个独立协程（gen_shot_frames
            只写自己的 shot dict 与 shot_dir，无共享可变状态），信号量限流，
            单镜失败只记日志不中断整轮。

    Returns:
        dict: {shot_index: {"ff": path, "lf": path, "kf": [paths]}}
    """
    from shared.vimax_bridge import gen_shot_frames
    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02a.log", console_level="INFO")

    if concurrency is None:
        concurrency = int(os.getenv("STEP02A_CONCURRENCY", "3") or "3")
    concurrency = max(1, concurrency)

    # 加载 script
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    hook = script.get("hook")
    characters = script.get("characters", [])
    shots_dir = work_dir / "shots"

    image_adapter = ImageGeneratorAiVideoAdapter()
    script_mtime = script_path.stat().st_mtime if script_path.exists() else None

    results: dict = {}
    failed: list[int] = []

    # 组装任务列表（hook + 各镜头，统一并发单元）
    tasks_spec: list[tuple[int, dict, bool]] = []
    if hook and hook.get("prompt"):
        if only_indices is None or 0 in only_indices:
            hook["index"] = 0
            tasks_spec.append((0, hook, False))
    for shot in shots:
        idx = shot["index"]
        if only_indices is not None and idx not in only_indices:
            continue
        # v6.73: step02b 已统一为独立镜头提交，默认每镜必须有自己的首帧。
        # 仅显式设置 STEP02A_USE_CONTINUITY_FRAME=1 时才允许旧 Mode B 跳过。
        skip_first = (
            os.getenv("STEP02A_USE_CONTINUITY_FRAME", "0").strip() == "1"
            and idx >= 2
        )
        tasks_spec.append((idx, shot, skip_first))

    sem = asyncio.Semaphore(concurrency)
    logger.info(f"[INFO] 帧生成并发数: {concurrency}（{len(tasks_spec)} 个镜头单元）")

    async def _one(idx: int, shot: dict, skip_first: bool):
        async with sem:
            logger.info(f"\n[INFO] ===== Shot {idx} frame generation =====")
            result = await gen_shot_frames(
                shot, shots_dir, image_adapter,
                script_mtime, force=force,
                characters=characters,
                skip_first_frame=skip_first,
                frames=frames,
            )
        return idx, result

    # 并发执行 + 失败隔离：单镜失败只记日志，不中断整轮（失败的镜头无 results
    # 条目，下游 step02b 按缺帧自行处理/重生成）
    if tasks_spec:
        gathered = await asyncio.gather(
            *(_one(idx, shot, skip_first) for idx, shot, skip_first in tasks_spec),
            return_exceptions=True,
        )
        for (idx, _shot, _skip), res in zip(tasks_spec, gathered):
            if isinstance(res, BaseException):
                failed.append(idx)
                logger.error(f"[ERROR] Shot {idx} 帧生成失败（不中断整轮）: {res}")
                continue
            idx_ok, result = res
            results[idx_ok] = result
            _log_shot_frames(idx_ok, result, logger)
    if failed:
        logger.error(
            f"[ERROR] 本轮 {len(failed)} 个镜头帧生成失败（部分失败，非全量成功）: "
            f"{sorted(failed)}——下游 step02b 对缺帧镜头自行按其帧计划处理")

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
        # v6.x.x fix：shot["_qc_last"] 等下划线开头字段是运行时数据（QcResult dataclass），
        # 不可 JSON 序列化；sidecar 已单独落 *_hallucination.json + _qc_log.json，
        # 此处只持久化业务字段。下划线开头键 = 约定俗成的"不入盘"信号。
        # v6.x.x.x 二次修：atomic write 避免 dump 中途抛错污染原文件（08-30 实抽
        # 暴露：上次崩在 _qc_last 写入中，文件被截断到 L1326 损坏）。
        _atomic_dump_json(
            script_path,
            _strip_runtime_fields(script),
            default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o),
        )
        logger.info("[INFO] ff_desc 归一化已回写 script_result.json")

    return results


def _strip_runtime_fields(obj):
    """递归剥掉 dict 下划线开头的 key（运行时数据，不入盘）.

    list 内元素递归处理；非 dict/list 叶子直接返回。
    """
    if isinstance(obj, dict):
        return {k: _strip_runtime_fields(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [_strip_runtime_fields(v) for v in obj]
    return obj


def _atomic_dump_json(path, obj, **kwargs):
    """原子写 JSON（2026-09-13 收敛到 shared.json_io，保留本地别名兼容调用点）."""
    from shared.json_io import atomic_dump_json
    atomic_dump_json(path, obj, **kwargs)


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
    frame_grp = parser.add_mutually_exclusive_group()
    frame_grp.add_argument("--all-frames", action="store_true",
                           help="对指定分镜一次性生成全部图片（首帧+尾帧+全部关键帧）；"
                                "调试整镜视觉检查用")
    frame_grp.add_argument("--frames", default=None,
                           help="只生成指定图片类型（逗号分隔）：ff=首帧, lf=尾帧, "
                                "kf=全部关键帧, kf0/kf1...=指定关键帧；默认=Mode A/B 行为")
    parser.add_argument("--concurrency", "-c", type=int, default=None,
                        help="镜头级并发数（默认读 STEP02A_CONCURRENCY env，缺省 3；"
                             "1=串行旧行为）。图片供应商限速由 KeyScheduler 独立保证")
    args = parser.parse_args()

    only_indices = parse_shot_selector(args.shots)

    # 帧选择解析：--all-frames=全部帧；--frames=精确选择；均未给=默认 Mode A/B
    if args.all_frames:
        frames = {"ff", "lf", "kf"}
    elif args.frames:
        frames = parse_frame_selector(args.frames)
    else:
        frames = None

    script_path = script_file()
    work_dir = wd()

    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")
    # Step02a owns image-prompt validation. Seedream/Qwen image constraints
    # are checked against the image fields before any frame generation.
    try:
        from step02a_frame_gen.image_prompt_quality import collect_image_prompt_quality
        from shared.quality_gate import require_semantic_gate
        with open(script_path, "r", encoding="utf-8") as _sf:
            _script_for_qa = json.load(_sf)
        require_semantic_gate(
            chapter_dir() / "step01_quality.json",
            chapter_dir() / "step01_audit.json",
            _script_for_qa,
            stage="step01",
        )
        _iq = collect_image_prompt_quality(_script_for_qa)
        logger.info(f"[IMAGE-QA] warnings: {_iq.get('warning_count', 0)}")
    except Exception as e:
        logger.error(f"[IMAGE-QA] upstream quality gate failed: {e}")
        raise

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
    if frames is not None:
        logger.info(f"  frames: {sorted(frames)} (指定图片类型)")

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
        asset_result = asyncio.run(gen_all_assets(force=args.force,
                                                  concurrency=args.concurrency))
        logger.info(f"[ASSET] 资产图: 场景{asset_result.get('scenes', 0)} / 道具{asset_result.get('props', 0)}")
    except Exception as e:
        logger.warn(f"[ASSET] 资产图生成失败（不阻断帧生成）: {e}")

    # 生成帧（全部或指定镜头）
    results = asyncio.run(gen_all_frames(
        script_path, work_dir, force=args.force,
        only_indices=only_indices, frames=frames,
        concurrency=args.concurrency,
    ))

    # Product acceptance is separate from the pre-generation prompt report.
    try:
        from step02a_frame_gen.image_prompt_quality import write_image_product_quality
        with open(script_path, "r", encoding="utf-8") as _sf_final:
            _script_final = json.load(_sf_final)
        _pq = write_image_product_quality(_script_final, work_dir)
        logger.info(f"[IMAGE-PRODUCT] status={_pq.get('status')} missing={len(_pq.get('missing') or [])}")
    except Exception as _pq_err:
        logger.error(f"[IMAGE-PRODUCT] product acceptance failed: {_pq_err}")
        raise

    logger.info(f"\n[INFO] step02a_frame_gen done!")
    for idx, result in results.items():
        _log_shot_frames(idx, result, logger)


if __name__ == "__main__":
    main()
