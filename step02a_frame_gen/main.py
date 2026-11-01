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

    if frames is None:
        from shared.video_media_plan import default_frame_selection
        frames = default_frame_selection()
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

    # v6.18 归一化发生在 gen_shot_frames 内（多动作/进行态 → 单一静态瞬间），但
    # **不得回写 `script_result.json`**（2026-10 复核 P0）：它是 step01 的已提交正式
    # 产物，`step01_commit.json` 锁着它的 SHA1，下游每个阶段入口都核
    # （`shared.quality_gate.verify_step01_commit_manifest`）。运行期回写 ⇒ 摘要失配
    # ⇒ step02a 之后 step02b/step03/step04 与任何重跑都被判
    # `artifact changed after commit` 拒收（跨阶段硬阻断）。
    #
    # 一致性**不依赖落盘**：归一化是确定性的（`shared.style_prefix.normalize_ff_desc`，
    # 幂等），每个消费点都在使用时各跑一次（生图与 step02b 组装视频提示同走
    # `step02_video_gen/prompt_builder`），所以磁盘上保留 step01 的字节即可。
    # 这里只比对并留痕，不写盘。
    with open(script_path, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    _normalized_in_memory = []
    disk_shots = {s.get("index"): s.get("ff_desc") for s in on_disk.get("shots", [])}
    for s in script.get("shots", []):
        if s.get("ff_desc") != disk_shots.get(s.get("index")):
            _normalized_in_memory.append(s.get("index"))
    if (script.get("hook") or {}).get("ff_desc") != (on_disk.get("hook") or {}).get("ff_desc"):
        _normalized_in_memory.append(0)
    if _normalized_in_memory:
        logger.info(
            "[INFO] ff_desc 归一化仅作用于内存（镜头 %s）：script_result.json 是 step01 "
            "已提交产物，保持只读；各消费点在使用时自行归一化"
            % sorted(k for k in _normalized_in_memory if k is not None)
        )

    return results


# 2026-10 复核 P0：本模块原有的 `_strip_runtime_fields` 与 `_atomic_dump_json` 只服务于
# 上面那段「把归一化结果回写 script_result.json」。回写已按「已提交产物字节级只读」
# 契约移除，两个本地帮助函数随之删除——留着等于给「再写一次已提交产物」留好路。
# step01 的 `_strip_runtime_fields` 仍在：它写的是自己的正式产物，且提交前要自验摘要。


def _log_shot_frames(idx: int, result: dict, logger):
    """日志输出帧生成结果"""
    ff = result.get("ff_path") or result.get("ff")
    lf = result.get("lf_path") or result.get("lf")
    kfs = result.get("keyframes_paths") or result.get("kf", [])

    logger.info(f"  Shot {idx} frames:")
    logger.info(f"    - First frame:  {Path(ff).name if ff else 'SKIP'}")
    logger.info(f"    - Last frame:   {Path(lf).name if lf else 'SKIP'}")
    logger.info(f"    - Keyframes:    {[Path(k).name for k in kfs] if kfs else 'NONE'}")


def _keyframes_unused_by_video() -> bool:
    """当前视频模型是否会把关键帧全部丢弃（不消费 reference_image）。

    seedance-1.0-pro 的 supports_reference_image=False，shared.video_media_plan
    会把 keyframe_*.png 全部丢掉——为它生成关键帧纯属多花约 60% 图费。
    STEP02A_FORCE_KEYFRAMES=1 可强制保留旧行为。
    """
    if os.getenv("STEP02A_FORCE_KEYFRAMES", "0").strip() == "1":
        return False
    try:
        from step02a_frame_gen.image_prompt_quality import _keyframes_consumed_by_video
        return not _keyframes_consumed_by_video()
    except Exception:
        return False


def _default_frames(frames, all_frames: bool):
    """未显式指定帧类型时，按视频模型能力决定默认集合。

    None 且模型不消费关键帧 ⇒ 只生成 ff/lf；显式 --all-frames / --frames 不动。
    """
    if frames is not None or all_frames:
        return frames
    if _keyframes_unused_by_video():
        return {"ff", "lf"}
    return frames


def _scoped_asset_identifiers(script: dict, units: list[dict]) -> dict:
    """目标镜头实际引用的场景/道具资产 id（S13B：子集运行不连带全章）."""
    import json
    from shared.assets_bank import PropAssetsBank, SceneAssetsBank

    scene_ids: set = set()
    sbank = SceneAssetsBank()
    sbank.load()
    for sid in sbank.get_all_identifiers():
        for unit in units:
            loc = str(unit.get("location") or unit.get("scene") or "")
            if loc and (sid in loc or loc in sid):
                scene_ids.add(sid)
                break
    prop_ids: set = set()
    pbank = PropAssetsBank()
    pbank.load()
    text = json.dumps(units, ensure_ascii=False)
    for pid in pbank.get_all_identifiers():
        if pid and pid in text:
            prop_ids.add(pid)
    return {"scenes": scene_ids, "props": prop_ids}


class _ConsoleLogger:
    """--plan-only 专用：只打印、不落盘（对 chapter 目录 0 文件改动）."""

    def info(self, msg, *a, **k):
        print(msg)

    def warn(self, msg, *a, **k):
        print(msg)

    def warning(self, msg, *a, **k):
        print(msg)

    def error(self, msg, *a, **k):
        print(msg)


def _plan_only_report(script: dict, work_dir: Path, only_indices, frames) -> dict:
    """零网络账单预检：列出本次会生成多少张肖像/场景/道具/分镜帧.

    计数口径：按**文件是否存在**判断缓存（与生成器的缓存判据一致）；分镜帧的
    内容哈希未在此重算——哈希不匹配的旧帧会在实跑时重生成，报告里单列说明。
    """
    import json
    from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
    from shared.assets_bank import PropAssetsBank, SceneAssetsBank
    from shared.character_bank import CharacterBank
    from shared.config import SCENE_NEAR_SHOT_ENABLED
    from shared.vimax_bridge import _characters_in_units, _units_for_indices

    if only_indices is not None:
        units = _units_for_indices(script, only_indices)
    else:
        units = ([script["hook"]] if isinstance(script.get("hook"), dict) else []) + \
                [s for s in (script.get("shots") or []) if isinstance(s, dict)]

    characters = _characters_in_units(script.get("characters") or [], units) \
        if only_indices is not None else list(script.get("characters") or [])
    bank = CharacterBank()
    bank.load()
    portraits = []
    for char in characters:
        ident = str(char.get("identifier") or "")
        if not ident:
            continue
        entry = bank.get_character(ident) if bank.has_character(ident) else None
        have = set()
        for view in ("base", "side", "back", "face"):
            path = str(((entry or {}).get("portraits") or {}).get(view, {}).get("path") or "")
            if path and Path(path).is_file():
                have.add(view)
        portraits.append({"character": ident,
                          "cached": sorted(have),
                          "to_generate": [v for v in ("base", "side", "back", "face")
                                          if v not in have]})

    scoped = _scoped_asset_identifiers(script, units)
    sbank = SceneAssetsBank()
    sbank.load()
    scene_plan = []
    for sid in sorted(scoped["scenes"]):
        scene_dir = sbank.get_asset_dir(sid)
        views = ["wide"] + (["near", "side"] if SCENE_NEAR_SHOT_ENABLED else [])
        scene_plan.append({"scene": sid,
                           "to_generate": [v for v in views
                                           if not (scene_dir / (v + ".png")).is_file()]})
    pbank = PropAssetsBank()
    pbank.load()
    prop_plan = []
    for pid in sorted(scoped["props"]):
        exists = (pbank.get_asset_dir(pid) / "prop.png").is_file()
        prop_plan.append({"prop": pid, "to_generate": 0 if exists else 1})

    frame_plan = []
    for unit in units:
        idx = int(unit.get("index", 0))
        shot_dir = Path(work_dir) / "shots" / str(idx)
        row = {"shot": idx}
        want_ff = frames is None or "ff" in frames
        want_lf = frames is None or "lf" in frames
        want_kf = frames is None or any(str(f).startswith("kf") for f in frames)
        if want_ff:
            row["ff"] = "cached" if (shot_dir / FIRST_FRAME_FILENAME).is_file() else "generate"
        if want_lf:
            row["lf"] = "cached" if (shot_dir / LAST_FRAME_FILENAME).is_file() else "generate"
        if want_kf:
            expected = sum(1 for k in (unit.get("keyframes") or [])
                           if isinstance(k, dict) and str(k.get("desc") or "").strip())
            cached = len(list(shot_dir.glob("keyframe_*.png")))
            row["kf"] = {"expected": expected, "cached": cached}
        frame_plan.append(row)

    frames_to_gen = sum(1 for row in frame_plan for key in ("ff", "lf")
                        if row.get(key) == "generate")
    kf_to_gen = sum(max(0, (row.get("kf") or {}).get("expected", 0) - (row.get("kf") or {}).get("cached", 0))
                    for row in frame_plan)
    total = (sum(len(p["to_generate"]) for p in portraits)
             + sum(len(s["to_generate"]) for s in scene_plan)
             + sum(p["to_generate"] for p in prop_plan)
             + frames_to_gen + kf_to_gen)
    # S22-4：--plan-only 必须把"实跑会写哪些文件"完整列出（含共享注册表），
    # 便于在授权前核对写入范围。
    from shared.paths import chapter_dir as _chapter_dir
    from shared.paths import global_dir as _global_dir
    _writes: list = []
    for _p in portraits:
        for _view in _p["to_generate"]:
            _writes.append(str(bank.get_portrait_dir(_p["character"]) / ("%s.png" % _view)))
    _writes.append(str(_global_dir() / "characters_bank" / "characters_bank.json"))
    for _row in frame_plan:
        _sd = work_dir / "shots" / str(_row["shot"])
        if _row.get("ff") == "generate":
            _writes.append(str(_sd / FIRST_FRAME_FILENAME))
        if _row.get("lf") == "generate":
            _writes.append(str(_sd / LAST_FRAME_FILENAME))
        _writes.extend([str(_sd / "frame_hash.txt"), str(_sd / "last_frame_hash.txt"),
                        str(_sd / "shot_description.json")])
    _writes.extend([str(work_dir / "character_portraits_registry.json"),
                    str(work_dir / "characters.json"),
                    str(_chapter_dir() / "step02a_quality.json"),
                    str(_chapter_dir() / "step02a_prompt_quality.json"),
                    str(_chapter_dir() / "step02a.log")])
    # S04：预检输出最终图片 prompt（与生成共用 compose_frame_prompt）。
    final_prompts: dict = {}
    try:
        from shared.vimax_bridge import compose_frame_prompt
        for _u in units:
            _idx = int(_u.get("index", 0))
            _entry: dict = {}
            if _u.get("ff_desc"):
                _entry["ff"] = compose_frame_prompt(_u, characters, "first_frame")
            if _u.get("lf_desc"):
                _entry["lf"] = compose_frame_prompt(_u, characters, "last_frame")
            if _entry:
                final_prompts[str(_idx)] = _entry
    except Exception as _exc:  # noqa: BLE001 - 预检失败要显式报出，不静默
        final_prompts = {"error": str(_exc)}
    return {
        "schema": "step02a.plan/1",
        "model_sends": 0,
        "shots": sorted(int(u.get("index", 0)) for u in units),
        "frames_requested": sorted(frames) if frames is not None else "default",
        "portraits": portraits,
        "scene_assets": scene_plan,
        "prop_assets": prop_plan,
        "shot_frames": frame_plan,
        "totals": {
            "portraits": sum(len(p["to_generate"]) for p in portraits),
            "scene_assets": sum(len(s["to_generate"]) for s in scene_plan),
            "prop_assets": sum(p["to_generate"] for p in prop_plan),
            "shot_frames": frames_to_gen,
            "keyframes": kf_to_gen,
            "images_total": total,
        },
        "writes": _writes,
        "final_prompts": final_prompts,
        "note": "分镜帧按文件存在性计数；内容哈希不匹配的旧帧会在实跑时重生成",
    }


def main():
    import argparse
    import json
    from shared.paths import script_file, vimax_working_dir as wd
    from shared.run_scope import (add_scope_arguments, apply_scope, scope_log_line,
                                  step01_hint as _step01_hint)

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
    parser.add_argument("--plan-only", action="store_true",
                        help="零网络账单预检：只打印本次将生成的肖像/场景/道具/帧张数，"
                             "不发任何生成请求")
    add_scope_arguments(parser)
    args = parser.parse_args()

    # 运行范围必须在 script_file()/chapter_dir() 之前生效（本入口的章级路径全部惰性解析）
    scope = apply_scope(args.chapter, args.book, args.book_name)

    only_indices = parse_shot_selector(args.shots)

    # 帧选择解析：--all-frames=全部帧；--frames=精确选择；均未给=默认 Mode A/B
    if args.all_frames:
        frames = {"ff", "lf", "kf"}
    elif args.frames:
        frames = parse_frame_selector(args.frames)
    else:
        frames = None

    # 未显式指定帧类型时，按当前视频模型是否消费关键帧决定默认集合
    # （gen_all_frames 对 None 也会用 default_frame_selection 兜底）
    frames = _default_frames(frames, args.all_frames)
    script_path = script_file()
    work_dir = wd()

    logger = (_ConsoleLogger() if args.plan_only
              else get_logger(chapter_dir() / "step02a.log", console_level="INFO"))
    logger.info(f"[INFO] {scope_log_line(scope)}")
    # 缺 step01 产物时说清楚是**哪一章**缺、该跑什么命令：裸 FileNotFoundError 会被
    # 当成"路径写错"，与真正的原因——该章还没跑出 COMMITTED 的 script_result.json——无关。
    if not script_path.exists():
        logger.error(
            f"[ERROR] {script_path} 不存在：chapter_{scope['chapter']:03d} 还没有 step01 产物。"
            f"先跑：{_step01_hint(scope)}")
        raise SystemExit(2)
    # Step02a owns image-prompt validation. Seedream/Qwen image constraints
    # are checked against the image fields before any frame generation.
    try:
        from step02a_frame_gen.image_prompt_quality import collect_image_prompt_quality
        from shared.quality_gate import (require_semantic_gate,
                                         verify_step01_commit_manifest)
        with open(script_path, "r", encoding="utf-8") as _sf:
            _script_for_qa = json.load(_sf)
        require_semantic_gate(
            chapter_dir() / "step01_quality.json",
            chapter_dir() / "step01_audit.json",
            _script_for_qa,
            stage="step01",
        )
        # v6.103（总计划阶段 4）：三份产物还必须属于**同一个提交批次**——
        # 逐个文件检查 revision 只能证明「每份都对得上当前 script」，证明不了
        # 「它们是同一次提交写出来的」。manifest 缺失 / 状态非 COMMITTED /
        # 任一摘要不符，一律 fail-closed（step01 BLOCK 时不写新产物，只写
        # BLOCK manifest，下游因此不会带着未解决的 blocking 继续生图）。
        verify_step01_commit_manifest(chapter_dir(), _script_for_qa, stage="step01",
                                      expected_chapter=scope["chapter"])
        _iq = collect_image_prompt_quality(_script_for_qa, write=not args.plan_only)
        logger.info(f"[IMAGE-QA] warnings: {_iq.get('warning_count', 0)}")
    except Exception as e:
        logger.error(f"[IMAGE-QA] upstream quality gate failed: {e}")
        raise

    if args.plan_only:
        with open(script_path, "r", encoding="utf-8") as _sf_plan:
            _script_plan = json.load(_sf_plan)
        print(json.dumps(
            _plan_only_report(_script_plan, work_dir, only_indices, frames),
            ensure_ascii=False, indent=2))
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
        only_indices=only_indices,   # S13B：子集运行只补目标镜头的角色肖像
    ))

    # v7: 场景/道具资产图生成（大场景/近景/道具白底图，供 r2v 一致性参考）
    # 资产图是横屏参考，独立于竖屏帧；在帧生成前跑，保证 r2v 参考图就绪
    try:
        from step02a_frame_gen.asset_gen import gen_all_assets
        from shared.vimax_bridge import _units_for_indices
        _scoped_assets = None
        if only_indices is not None:
            with open(script_path, "r", encoding="utf-8") as _sf_assets:
                _script_assets = json.load(_sf_assets)
            _scoped_assets = _scoped_asset_identifiers(
                _script_assets, _units_for_indices(_script_assets, only_indices))
        asset_result = asyncio.run(gen_all_assets(force=args.force,
                                                  concurrency=args.concurrency,
                                                  only_identifiers=_scoped_assets))
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
        _effective_frames = frames if frames is not None else {"ff", "lf", "kf"}
        _pq = write_image_product_quality(
            _script_final, work_dir, only_indices=only_indices,
            require_keyframes=any(f.startswith("kf") for f in _effective_frames))
        logger.info(f"[IMAGE-PRODUCT] status={_pq.get('status')} missing={len(_pq.get('missing') or [])}")
        if _pq.get("status") != "PASS":
            # S22-4：缺帧/不可解码默认非零退出，不能"报告 BLOCK 但 exit 0"。
            logger.error("[IMAGE-PRODUCT] BLOCK：缺帧或不可解码，按失败退出")
            raise SystemExit(1)
    except Exception as _pq_err:
        logger.error(f"[IMAGE-PRODUCT] product acceptance failed: {_pq_err}")
        raise

    logger.info(f"\n[INFO] step02a_frame_gen done!")
    for idx, result in results.items():
        _log_shot_frames(idx, result, logger)


if __name__ == "__main__":
    main()
