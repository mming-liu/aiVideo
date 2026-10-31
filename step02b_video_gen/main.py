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

候选模式（计划 §V2/§F4；产物全部落隔离实验根，**不**触碰正式 clips/shots/script）：
    python -m step02b_video_gen.main --chapter 1 --shots 7 --plan-only \
        --candidate-root <chapter>/_enhancement/<exp>/video      # 零网络规划
    python -m step02b_video_gen.main --chapter 1 --shots 7 \
        --candidate-root <chapter>/_enhancement/<exp>/video      # 真实候选生成（需费用确认）
    python -m step02b_video_gen.main --chapter 1 --resume <attempt 目录>   # 恢复已有 task

注意：image 模式需要先运行 step02a_frame_gen 生成帧图片。
"""
from __future__ import annotations
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 早期解析（计划 §F4/§15）：配置/路径/logger 求值之前生效运行范围与候选参数。
# 只在**脚本方式**运行时解析 argv——import 本模块（含 pytest 收集）不解析用户参数。
if __name__ == "__main__":
    from step02b_video_gen import cli as _cli

    _cli.bootstrap()

import asyncio
import json
import os

from shared.paths import (
    script_file, vimax_working_dir, chapter_dir,
    vimax_shots_dir, SHOT_VIDEO_FILENAME, FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME,
    keyframe_files, clip_file,
)
from shared.tee_logger import get_logger
from step02_video_gen.submit_poll import (
    _submit_video, _get_shot_hash, _failed_videos, cache_decision,
    build_frozen_for_shot, resume_candidate_attempt, repair_missing_clip,
)
from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
from shared.shot_selector import parse_shot_selector


def _exit(code: int) -> int:
    return int(code)


async def gen_all_videos(script: dict, working_dir: Path, script_file_path: Path,
                         only_indices: set[int] | None = None,
                         force: bool = False,
                         candidate_factory=None,
                         log_path: Path | None = None) -> int:
    """按 VIDEO_REFERENCE_MODE 统一生成全部镜头视频，并行提交 + 后台轮询。

    统一模式：全部镜头按 .env.content 的 VIDEO_REFERENCE_MODE（text/r2v/image）
    处理，不做 Mode A/B 双模调度、不提取尾帧接续——参考图收集由
    prompt_builder._make_request 按模式权威完成。

    Args:
        only_indices: 只生成指定 index 的镜头视频（None=全部）
        force: 忽略缓存强制重生成
        candidate_factory: ``idx -> CandidateContext``；非 None ⇒ 候选模式
            （每个镜头一个 attempt，产物/日志/哈希全部隔离，提交走 prepared 入口）
        log_path: 候选模式的批次日志路径（缺省用正式章目录日志）

    Returns:
        int: 失败镜头数量
    """
    log_dir = chapter_dir()
    logger = get_logger((log_path or (log_dir / "step02b.log")), console_level="INFO")

    # 2026-09-13 审计修复：入口清空失败记录——旧的 `generate_alls()` 入口会清，
    # 本入口当初不清，同进程复用/测试/工具调用会累计上一轮旧失败。
    # （`generate_alls()` 已于 2026-10 复核作为死代码删除；本行保留，因为它解决的是
    #  「同进程复用会累计旧失败」这个问题本身，与新入口是否是唯一入口无关。）
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
    if candidate_factory is not None:
        logger.info("[INFO] 候选模式：产物/日志/哈希全部落隔离 attempt，正式产物只读")

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
            candidate_context=(candidate_factory(shot["index"])
                               if candidate_factory is not None else None),
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
    candidate_context=None,
):
    """处理单个镜头：跳过帧生成，直接提交视频（复用已有帧）。

    force=True 时忽略缓存强制重新提交视频。
    r2v/text 模式不依赖任何帧图片；image 模式复用 step02a 的首帧/尾帧/关键帧。

    ``candidate_context`` 非空 ⇒ 候选模式（计划 §V2 改动 4/7）：
    - shot_dir/video_path/哈希/快照**全部落在 attempt 内**，正式 ``clips/``、
      正式 shot 目录、正式日志一个字节都不写；帧图片只**读**生产输入；
    - 命中缓存前先核冻结请求关系（``cache_decision``），不只看 mtime/hash；
    - 提交走 ``_submit_video(..., candidate_context=...)``（prepared 入口，
      一次尝试至多一个生成 POST）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir

    idx = shot["index"]
    # 模型输入（帧图片）始终来自生产/指定 shots 目录——候选模式**只读**它
    frames_dir = shots_dir / str(idx)

    if candidate_context is not None:
        candidate_context.prepare()
        attempt_dir = Path(getattr(candidate_context, "attempt_dir"))
        attempt_dir.mkdir(parents=True, exist_ok=True)
        shot_dir = candidate_context.shot_dir(idx)          # 全部产物落 attempt
        shot_dir.mkdir(parents=True, exist_ok=True)
        video_path = shot_dir / SHOT_VIDEO_FILENAME
        logger = get_logger(Path(candidate_context.log_dir) / "step02b.log",
                            console_level="INFO")
        _clip_dst = Path(candidate_context.outputs_dir) / f"clip_{idx}.mp4"
    else:
        shot_dir = frames_dir
        video_path = shot_dir / SHOT_VIDEO_FILENAME
        logger = get_logger(chapter_dir() / "step02b.log", console_level="INFO")
        _clip_dst = clip_file(idx)

    # Cache check（§V2 改动 4）：prompt_hash + 产物检查 + 冻结请求关系
    shot_hash = _get_shot_hash(shot, script, shot_dir=frames_dir)
    _cache = cache_decision(shot, script, shot_dir, video_path, clip_path=_clip_dst,
                            force=force, shot_hash=shot_hash)
    if _cache["hit"]:
        logger.info(f"  [Shot {idx}] Cache hit({_cache['relation']}), skip: {_cache['reason']}")
        return None
    if _cache["reason"] == "clip 产物缺失" and _clip_dst is not None:
        # R3：视频本体与 prompt_hash 完好、只有 clips/ 副本缺失 ⇒ 本地补拷，零远端 POST。
        if repair_missing_clip(shot_dir, video_path, _clip_dst):
            logger.info(f"  [Shot {idx}] Clip 缺失但视频完好，本地补拷 clips/（0 次远端提交）")
            return None
        logger.warn(f"  [Shot {idx}] Clip 缺失且本地补拷失败，回退为重新生成")

    # 按模式收集参考图（r2v/text 不需要帧图片；image 复用 step02a 帧）
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    if REFERENCE_MODE == "r2v" or REFERENCE_MODE == "text":
        # r2v：参考图由 prompt_builder._make_request 权威收集（角色肖像），
        # 此处不传任何路径，避免传递不存在的 keyframes/ 子目录（旧逻辑断链）。
        # text：纯文生视频，不使用任何帧图片。
        frame_paths = []
        logger.info(f"  [Shot {idx}] Mode: {REFERENCE_MODE.upper()} (no frame files)")
    else:
        # image 模式：使用首帧/尾帧/关键帧；首帧缺失必须失败，禁止尾帧顶上。
        ff = frames_dir / FIRST_FRAME_FILENAME
        lf = frames_dir / LAST_FRAME_FILENAME
        if not ff.exists() or not lf.exists():
            raise FileNotFoundError(
                f"[Shot {idx}] image 模式缺少首/尾帧: ff={ff.exists()} lf={lf.exists()}"
            )
        kf_paths = [str(p) for p in keyframe_files(idx)]
        frame_paths = [str(ff), str(lf)]
        frame_paths.extend(kf_paths)
        logger.info(f"  [Shot {idx}] Mode: IMAGE (reuse step02a frames)")

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, script, idx,
        continuity_ref_image=None, mode="A",
        candidate_context=candidate_context,
    )


# ---------------------------------------------------------------- 候选模式：规划 / 恢复 / 隔离核对

def production_snapshot(chapter: int | None = None) -> dict:
    """章目录**正式产物**的逐文件 SHA-256（计划 §15「绕过入口与排版」行）。

    覆盖 ``clips/``、``shots/``（vimax 工作目录）、``audio/`` 的**全部文件**，以及
    章级正式日志与报告（``*.log`` / ``*.json`` / 成片 / 字幕）。候选模式跑完必须与
    跑前逐字节一致——这是"实验不碰正式产物"的可执行判据，不是口号。

    读不动的文件记 ``unreadable:<原因>``（显式，不静默跳过）。
    """
    from shared import enhancement_io as _eio
    from shared.paths import audio_dir, chapter_dir as _chapter_dir, clips_dir, vimax_shots_dir

    out: dict[str, str] = {}
    for root in (clips_dir(chapter), vimax_shots_dir(chapter), audio_dir(chapter)):
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                try:
                    out[str(p)] = _eio.sha256_file(p)
                except OSError as exc:
                    out[str(p)] = f"unreadable:{type(exc).__name__}"
    ch = Path(_chapter_dir(chapter))
    if ch.is_dir():
        for p in sorted(ch.iterdir()):
            if p.is_file() and p.suffix.lower() in (".log", ".json", ".mp4", ".srt"):
                try:
                    out[str(p)] = _eio.sha256_file(p)
                except OSError as exc:
                    out[str(p)] = f"unreadable:{type(exc).__name__}"
    return out


def snapshot_changes(before: dict, after: dict) -> dict:
    """两份摘要的差异（新增/删除/改动），逐路径列出——不吞掉任何一类。"""
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": sorted(p for p in (set(before) & set(after)) if before[p] != after[p]),
    }


def build_plan(script: dict, shots_dir: Path, *, only_indices: set[int] | None,
               chapter: int, provider: str, model_id: str | None = None) -> dict:
    """只组装**冻结请求 + 结构预检**（零网络、零生成 POST，§V2「plan-only+force 仍零网络」）。

    注意：provider 的压缩/合规改写只在实际 prepare 时发生，因此这里冻结的是
    "组装完成、尚未 provider 化"的事实，``semantic_preflight=unverified``。
    """
    from shared.model_caps import active_video_model
    from step02_video_gen import request_plan as rp

    resolved_model = model_id if model_id is not None else str(active_video_model() or "")
    entries: list[dict] = []
    shots = list(script.get("shots") or [])
    hook = script.get("hook")
    queue = ([dict(hook, index=0)] if hook else []) + shots
    for shot in queue:
        idx = shot.get("index")
        if only_indices is not None and idx not in only_indices:
            continue
        mode = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
        entry = {"shot_index": idx, "mode": mode, "generations": 0}
        if mode == "image":
            dd = shots_dir / str(idx)
            ff, lf = dd / FIRST_FRAME_FILENAME, dd / LAST_FRAME_FILENAME
            if not (ff.exists() and lf.exists()):
                entry.update({"verdict": "blocked", "code": "frames_missing",
                              "field_path": f"shots[{idx}].frames",
                              "message": f"image 模式缺少首/尾帧: ff={ff.exists()} lf={lf.exists()}",
                              "needed_change": "先跑 step02a_frame_gen 补齐首尾帧"})
                entries.append(entry)
                continue
            frame_paths = [str(ff), str(lf)] + [str(p) for p in keyframe_files(idx)]
        else:
            frame_paths = []
        try:
            frozen, _req = build_frozen_for_shot(
                shot=shot, script=script, video_adapter=None, frame_paths=frame_paths,
                mode="A", scope={"chapter": chapter}, provider=provider,
                model_id=resolved_model)
        except Exception as exc:  # noqa: BLE001 - 组装失败作为 blocked 证据，不抛栈
            entry.update({"verdict": "blocked", "code": "request_assembly_failed",
                          "message": str(exc)[:500],
                          "needed_change": "补齐参考图/注册表后重跑 plan"})
            entries.append(entry)
            continue
        problems = rp.validate_frozen_request(frozen)
        entry.update({
            "verdict": "blocked" if problems else "ready",
            "request_key": frozen.request_key,
            "request": frozen.to_dict(),
            "problems": problems,
            "semantic_preflight": "unverified",
            "generations": 0 if problems else 1,
        })
        entries.append(entry)
    ready = [e for e in entries if e.get("verdict") == "ready"]
    return {"schema_version": "video_reroll_plan.v1", "chapter": chapter, "provider": provider,
            "model_id": resolved_model,
            "shots": entries, "ready": len(ready), "blocked": len(entries) - len(ready),
            "estimated_generations": sum(int(e.get("generations") or 0) for e in entries),
            "network_calls": 0}


def _plan_only(options, script: dict, shots_dir: Path, chapter: int) -> int:
    """plan-only：零网络规划，落计划文件到候选实验根（不写正式产物）。"""
    from shared.enhancement_io import package_root
    from step02b_video_gen import cli

    experiment_id = cli.resolve_experiment_root(options.candidate_root, chapter=chapter)
    out_root = package_root("video", experiment_id, chapter) / "plans"
    out_root.mkdir(parents=True, exist_ok=True)
    only_indices = parse_shot_selector(options.shots)
    plan = build_plan(script, shots_dir, only_indices=only_indices, chapter=chapter,
                      provider=str(os.getenv("VIDEO_PROVIDER", "")))
    path = out_root / f"plan_{len(list(out_root.glob('plan_*.json'))) + 1:04d}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plan": str(path), "ready": plan["ready"], "blocked": plan["blocked"],
                      "estimated_generations": plan["estimated_generations"],
                      "network_calls": 0}, ensure_ascii=False))
    return 0


def _resume(options) -> int:
    """恢复已有 task（无 ID 不许新生成；凭据不可解析 ⇒ 退出码 7）。"""
    from shared import enhancement_io as eio

    try:
        result = resume_candidate_attempt(Path(options.resume))
    except eio.EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False))
        return exc.exit_code
    print(json.dumps(result, ensure_ascii=False))
    status = result.get("status")
    if status == "accepted":
        return 0
    if status in ("failed", "rejected"):
        return eio.EXIT_REJECTED
    if status == "io_failed":
        return eio.EXIT_TOOL_FAIL
    # unverified 等：命令成功但**不**证明内容通过（rc=0 的语义见 §F3）
    return 0


def is_candidate_run(options) -> bool:
    """本次运行是否走候选/实验路径（**含 `--resume`**）.

    `--resume` 没有 `--candidate-root`，旧写法因此给它选了**正式日志**（2026-10-04
    复核 P2-9）。判据看两个参数，不只看一个。
    """
    return bool(getattr(options, "candidate_root", None)
                or getattr(options, "resume", None))


#: 批次失败类别 → 退出码（顺序即优先级；要求"真实异常→批处理→CLI 退出码"可核）
CANDIDATE_EXIT_BY_CODE = (
    ("submission_unknown", None),          # eio.EXIT_SUBMISSION_UNKNOWN
    ("commit_unconfirmed", None),          # eio.EXIT_COMMIT_UNCONFIRMED
)


def candidate_exit_code(failed: int, codes) -> int:
    """候选批次的退出码：失败类别**传到 CLI**，不能只看"正式产物没变"就报成功.

    优先序（与 `shared.enhancement_io` 的约定一致）：
    未知提交 7 › 提交未确认 6 › 候选被拒 4 › 其余工具失败 3；无失败 ⇒ 0。
    """
    from shared import enhancement_io as _eio

    if not failed:
        return 0
    codes = {str(c) for c in (codes or ()) if c}
    if "submission_unknown" in codes:
        return _eio.EXIT_SUBMISSION_UNKNOWN
    if "commit_unconfirmed" in codes:
        return _eio.EXIT_COMMIT_UNCONFIRMED
    if codes & {"prepared_unsupported", "preflight_blocked", "attempt_identity_conflict",
                "candidate_identity_missing", "candidate_unit_locator_missing",
                "candidate_rejected"}:
        return _eio.EXIT_REJECTED
    return _eio.EXIT_TOOL_FAIL


def _run_candidate(options, script: dict, work_dir: Path, script_path: Path,
                   chapter: int, shots_dir: Path) -> int:
    """候选模式生成：每镜一个 attempt，产物/日志全隔离；正式产物只读。

    跑前/跑后各取一次 ``production_snapshot`` 并逐路径比对；一旦正式 ``clips/``、
    ``shots/``、``audio/`` 或章级日志/报告发生变化，即报 ``production_write_detected``
    并以退出码 3（工具/IO 失败）收口——隔离被破坏不允许当成功。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va
    from step02b_video_gen import cli

    package_root_dir = None
    from shared.enhancement_io import package_root as _package_root
    experiment_id = cli.resolve_experiment_root(options.candidate_root, chapter=chapter)
    package_root_dir = _package_root("video", experiment_id, chapter)
    batch_log = package_root_dir / "logs" / "step02b.log"
    batch_log.parent.mkdir(parents=True, exist_ok=True)
    logger = get_logger(batch_log, console_level="INFO")

    only_indices = parse_shot_selector(options.shots)

    # 采纳 manifest 的来源身份取自**本次真实运行**：范围（章）与稿件 revision。
    # 不传 ⇒ 采纳出口拒收（旧行为是记空身份 + fixture，下游无法判"这是哪一稿"）。
    from shared.quality_gate import compute_script_revision

    from shared.run_scope import scope_identity

    candidate_scope = scope_identity(cli.apply_early_scope(options))
    candidate_revision = compute_script_revision(script)

    def _factory(idx: int):
        return cli.resolve_candidate_context(
            options, chapter=chapter, attempt_id=cli.new_attempt_id(f"shot{int(idx):02d}"),
            scope=candidate_scope, script_revision=candidate_revision,
            source="step02b_candidate")

    logger.info(f"[INFO] 候选模式 start：experiment={experiment_id}")
    logger.info(f"  candidate_root: {package_root_dir}")
    logger.info(f"  shots: {sorted(only_indices) if only_indices else '全部（--all-shots）'}")
    if options.force:
        logger.info("  force: True（仅跳过可复用缓存，不隐式允许新 POST）")

    before = production_snapshot(chapter)
    posts_before = va.submit_post_count(package_root_dir)
    failed = asyncio.run(gen_all_videos(
        script, work_dir, script_path, only_indices=only_indices, force=options.force,
        candidate_factory=_factory, log_path=batch_log))
    after = production_snapshot(chapter)
    changes = snapshot_changes(before, after)
    posts_delta = va.submit_post_count(package_root_dir) - posts_before

    # 实验分支**不**回写正式 script / 正式质量报告（计划 §3 表 step02b 行）
    logger.info(f"[INFO] 候选模式完成：失败 {failed} 个镜头；"
                f"正式 script/quality 报告未改动（实验分支不回写）")
    # 失败类别**传到退出码**（P2-12）：未知提交 / 提交未确认 / 候选被拒 各有各的码，
    # 不能因为"没检测到正式产物变化"就整体返回成功。
    from step02_video_gen import submit_poll as _sp

    _codes: set = set()
    for _idx, _items in (_sp._failed_videos or {}).items():
        for _it in _items or ():
            if isinstance(_it, dict) and _it.get("code"):
                _codes.add(str(_it["code"]))
    payload = {"failed": failed, "candidate_root": str(package_root_dir),
               "generation_posts_this_run": posts_delta,
               "production_unchanged": not any(changes.values()),
               "failure_codes": sorted(_codes)}
    if any(changes.values()):
        logger.error(f"[FAIL] 候选运行改动了正式产物（隔离被破坏）: {changes}")
        payload["isolation_violation"] = changes
        payload["code"] = "production_write_detected"
        print(json.dumps(payload, ensure_ascii=False))
        return eio.EXIT_TOOL_FAIL
    _exit = candidate_exit_code(failed, _codes)
    if _exit:
        payload["exit_code"] = _exit
        payload["code"] = {
            eio.EXIT_SUBMISSION_UNKNOWN: "submission_unknown",
            eio.EXIT_COMMIT_UNCONFIRMED: "commit_unconfirmed",
            eio.EXIT_REJECTED: "candidate_rejected",
        }.get(_exit, "candidate_shots_failed")
        logger.error(f"[FAIL] 候选模式有 {failed} 个镜头未完成：{payload['code']}")
        print(json.dumps(payload, ensure_ascii=False))
        return int(_exit)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _assert_step02a_scope(report: dict, only_indices, script: dict) -> None:
    """子集运行的门禁范围核对：报告覆盖的镜头必须包含本次要跑的镜头。

    step02a 的成品验收报告现在带 scope.shots（--shots 子集运行时只覆盖子集）。
    没有 scope 的旧报告视为全章覆盖（保持旧行为）。不能让「shot 1 的 PASS」
    给整章放行。
    """
    from shared.quality_gate import QualityGateError

    scope = (report or {}).get("scope") or {}
    scoped = {int(i) for i in (scope.get("shots") or [])}
    if not scoped:
        return
    wanted = set(only_indices) if only_indices is not None else None
    if wanted is None:
        wanted = {int(s.get("index", 0)) for s in script.get("shots", [])}
        if script.get("hook"):
            wanted.add(0)
    missing = sorted(wanted - scoped)
    if missing:
        raise QualityGateError(
            "step02a 验收范围不含本次请求的镜头: %s（报告覆盖 %s）"
            % (missing, sorted(scoped)))


def main(options=None):
    from step02b_video_gen import cli

    options = options if options is not None else cli.options_or_default()
    try:
        cli.validate_options(options)
    except ValueError as exc:
        # 非法参数组合：显式退出码 2（输入非法），不打裸栈
        print(f"[ERROR] {exc}", flush=True)
        raise SystemExit(2) from None

    # 运行范围必须在 script_file()/chapter_dir() 之前生效（本入口的章级路径全部惰性解析）
    scope = cli.apply_early_scope(options)

    only_indices = parse_shot_selector(options.shots)

    script_path = script_file()
    work_dir = vimax_working_dir()

    # 候选模式**从入口起**就不写正式日志（§15「绕过入口」P2-10）：日志上下文定下后，
    # provider/适配器/verbose_write 都按它取落点。候选根**推迟到"能不能跑"确认之后**
    # 才解析——否则缺 step01 产物时会被候选根校验抢先，退出码与提示都变。
    from shared.log_utils import resolve_log_file, set_log_context
    from shared.run_scope import scope_log_line

    # `--resume` 同样是候选/实验路径：它没有 `--candidate-root`，旧写法因此给恢复
    # 走了**正式日志**（复核 P2-9）。判据看两个参数，不只看一个。
    _candidate_mode = is_candidate_run(options)
    logger = None
    if not _candidate_mode:
        set_log_context(None)
        logger = get_logger(resolve_log_file("step02b.log"), console_level="INFO")
        logger.info(f"[INFO] {scope_log_line(scope)}")

    if not script_path.exists():
        # 缺 step01 产物时点明是哪一章缺、该跑什么命令（原来是 error 后 return 0，
        # 脚本调用方看不出失败；空值/失败就报失败，见 CLAUDE.md 准则 7）
        from shared.run_scope import step01_hint as _step01_hint
        _msg = (f"[ERROR] {script_path} 不存在：chapter_{scope['chapter']:03d} 还没有 "
                f"step01 产物。先跑：{_step01_hint(scope)}")
        if logger is not None:
            logger.error(_msg)
        else:
            print(_msg, flush=True)      # 候选模式：不往正式日志写这一条
        raise SystemExit(2)

    if _candidate_mode and not getattr(options, "candidate_root", None):
        # 恢复模式：日志落在被恢复 attempt 的**实验根**下（仍是候选隔离区，不碰正式日志）
        _att = Path(options.resume)
        set_log_context(_att.parent.parent / "logs")
        logger = get_logger(resolve_log_file("step02b.log"), console_level="INFO")
        logger.info(f"[INFO] {scope_log_line(scope)}（恢复 attempt={_att.name}）")
    elif _candidate_mode:
        from shared.enhancement_io import package_root as _pkg_root
        try:
            _cand_experiment = cli.resolve_experiment_root(options.candidate_root,
                                                           chapter=scope["chapter"])
        except ValueError as exc:
            print(f"[ERROR] {exc}", flush=True)     # 非法候选根 = 输入非法（与解析期同出口）
            raise SystemExit(2) from None
        set_log_context(_pkg_root("video", _cand_experiment, scope["chapter"]) / "logs")
        logger = get_logger(resolve_log_file("step02b.log"), console_level="INFO")
        logger.info(f"[INFO] {scope_log_line(scope)}")

    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    shots_dir = vimax_shots_dir()

    # Load script before frame validation: downstream must key off explicit shot ids,
    # not directory order.
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    from shared.quality_gate import require_gate, verify_step01_commit_manifest
    require_gate(chapter_dir() / "step01_quality.json", script, stage="step01",
                 required_report_type="quality_gate")
    # v6.103：三份 step01 产物必须属于同一个提交批次（见 shared/quality_gate）
    verify_step01_commit_manifest(chapter_dir(), script, stage="step01",
                                  expected_chapter=scope["chapter"])
    # 2026-10-28（M10）：`--plan-only` 是**零网络结构预检**，它的输出本身就是
    # 「哪些镜头缺帧/缺参考」（`frames_missing` 等 blocked 条目）。此前它排在
    # step02a 成品验收门**之后**，没有帧的章节走不到预检就先被门拦住，
    # 「没有图片不妨碍执行视频 plan-only」在入口上不成立。预检不生成、不提交、
    # 只写候选实验根，故跳过的是 step02a 门；step01 的质量与提交绑定仍然必查。
    if REFERENCE_MODE == "image" and not options.plan_only:
        _qa_report = require_gate(chapter_dir() / "step02a_quality.json", script,
                                  stage="step02a",
                                  required_report_type="product_acceptance",
                                  verify_outputs=True)
        _assert_step02a_scope(_qa_report, only_indices, script)

    # ---- 候选模式分支（plan-only / resume / 候选生成）----
    if options.resume:
        raise SystemExit(_resume(options))
    if options.plan_only:
        if not options.candidate_root:
            logger.error("[ERROR] --plan-only 必须配合 --candidate-root 使用")
            raise SystemExit(2)
        raise SystemExit(_plan_only(options, script, shots_dir, scope["chapter"]))

    # r2v/text 模式不需要帧图片（r2v 参考图来自角色肖像注册表，非帧）
    if REFERENCE_MODE in ("r2v", "text"):
        logger.info(f"[INFO] Reference mode: {REFERENCE_MODE}（无需帧图片，跳过帧检查）")
    else:
        if not shots_dir.exists():
            raise FileNotFoundError(
                f"image 模式缺少帧目录: {shots_dir}；请先运行 step02a_frame_gen/main.py"
            )
        expected = []
        hook = script.get("hook")
        if hook and (only_indices is None or 0 in only_indices):
            expected.append(0)
        for shot in script.get("shots", []):
            idx = shot.get("index", 0)
            if only_indices is None or idx in only_indices:
                expected.append(idx)
        missing_frames = []
        for idx in expected:
            d = shots_dir / str(idx)
            ff = d / FIRST_FRAME_FILENAME
            lf = d / LAST_FRAME_FILENAME
            if not ff.exists() or not lf.exists():
                missing_frames.append((idx, "ff+lf"))
        if missing_frames:
            detail = ", ".join(f"shot {idx}: missing {need}" for idx, need in missing_frames)
            raise FileNotFoundError(
                f"image 模式必需首尾帧缺失（禁止尾帧冒充首帧/静默降级）: {detail}"
            )

    if options.candidate_root:
        raise SystemExit(_run_candidate(options, script, work_dir, script_path,
                                        scope["chapter"], shots_dir))

    logger.info(f"[INFO] step02b_video_gen start")
    logger.info(f"  script: {script_path}")
    logger.info(f"  shots_dir: {shots_dir}")
    if only_indices is not None:
        logger.info(f"  shots: {sorted(only_indices)} (指定镜头)")
    if options.force:
        logger.info(f"  force: True (忽略缓存)")

    # Step02b owns video-prompt validation. Seedance-specific duration/motion/
    # camera/transition checks run on the actual assembled video prompt.
    try:
        from step02_video_gen.video_prompt_quality import collect_video_prompt_quality
        _vq = collect_video_prompt_quality(script)
        logger.info(f"[VIDEO-QA] warnings: {_vq.get('warning_count', 0)}")
    except Exception as e:
        logger.error(f"[VIDEO-QA] video prompt quality gate failed: {e}")
        raise

    failed = asyncio.run(gen_all_videos(
        script, work_dir, script_path,
        only_indices=only_indices, force=options.force,
    ))

    # Separate product acceptance from the pre-generation prompt report.
    try:
        from step02_video_gen.video_prompt_quality import write_video_product_quality
        _pq = write_video_product_quality(script, work_dir)
        logger.info(f"[VIDEO-PRODUCT] status={_pq.get('status')} missing={len(_pq.get('missing') or [])}")
    except Exception as _pq_err:
        logger.error(f"[VIDEO-PRODUCT] product acceptance failed: {_pq_err}")
        raise

    # `script_result.json` 是 step01 的**已提交正式产物**，`step01_commit.json` 锁着它的
    # SHA1，下游每个阶段入口都核（`shared.quality_gate.verify_step01_commit_manifest`）。
    # 此处原为**无条件**原子回写，且写的是带运行时字段的 `script`（未剥下划线键）——
    # 一旦回写，摘要即失配：step02b 跑成功之后 step03/step04 与任何重跑都被判
    # `artifact changed after commit` 拒收。生成期派生数据一律落 sidecar，
    # 正式产物保持字节级只读（2026-10 复核 P0）。
    #
    # 2026-09-13 审计要求的「实际视频时长」元数据保留，但改记到
    # `step02_video_metrics.json`。
    try:
        from step02_video_gen.audio_pregen import write_actual_durations_metrics
        write_actual_durations_metrics(work_dir, script_path)
    except Exception as e:
        logger.warn(f"[WARN] 实际时长指标落盘失败（不影响已生成视频）: {e}")

    logger.info(f"\n[INFO] step02b_video_gen done! Failed: {failed}")


if __name__ == "__main__":
    main()
