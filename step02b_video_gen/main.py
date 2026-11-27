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
    is_core_keyframe_file, clip_file,
)
from shared.tee_logger import get_logger
from step02_video_gen.submit_poll import (
    _submit_video, _get_shot_hash, _failed_videos, cache_decision,
    build_frozen_for_shot, build_prepared_facts, read_frozen_snapshot,
    resume_candidate_attempt, repair_missing_clip, _script_revision,
)
from shared.vimax_adapters import VideoGeneratorAiVideoAdapter
from shared.shot_selector import parse_shot_selector


def _exit(code: int) -> int:
    return int(code)


def _core_keyframes_in(frames_dir: Path) -> list[Path]:
    """本镜核心关键帧，按**数字序**（keyframe_2 在 keyframe_10 前，S09）."""
    return sorted(
        (p for p in Path(frames_dir).glob("keyframe_*.png") if is_core_keyframe_file(p)),
        key=lambda p: int(p.stem.split("_")[1]))


def _collect_frame_paths(idx: int, frames_dir: Path, policy: str) -> list[str]:
    """按显式策略收集本镜帧文件；必需项缺失即 fail-closed（S02）.

    `reference` 策略按"实际选中集合"判定：ff/lf/kf 任一存在即可，不强制首尾帧。
    """
    from shared.video_media_plan import required_frames

    ff = frames_dir / FIRST_FRAME_FILENAME
    lf = frames_dir / LAST_FRAME_FILENAME
    kfs = [p for p in _core_keyframes_in(frames_dir) if p.exists()]
    need = required_frames(policy)
    missing: list[str] = []
    if "ff" in need and not ff.exists():
        missing.append("ff")
    if "lf" in need and not lf.exists():
        missing.append("lf")
    if policy == "reference" and not (ff.exists() or lf.exists() or kfs):
        missing.append("ff/lf/kf(任一)")
    if missing:
        if policy == "auto":
            # 保留旧文案（既有入口/测试契约），显式策略用更精确的素材名。
            raise FileNotFoundError(
                f"[Shot {idx}] image 模式缺少首/尾帧: ff={ff.exists()} lf={lf.exists()}")
        raise FileNotFoundError(
            f"[Shot {idx}] 策略 {policy} 缺少素材: {', '.join(missing)}")
    paths: list[str] = []
    if ff.exists():
        paths.append(str(ff))
    if lf.exists():
        paths.append(str(lf))
    paths.extend(str(p) for p in kfs)
    return paths


async def gen_all_videos(script: dict, working_dir: Path, script_file_path: Path,
                         only_indices: set[int] | None = None,
                         force: bool = False,
                         candidate_factory=None,
                         log_path: Path | None = None,
                         policy_source: str | None = None,
                         qa_report: dict | None = None) -> int:
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
    from shared import post_budget
    from shared.video_media_plan import (active_media_policy_source,
                                         resolve_shot_media_policy)
    _policy_source = (policy_source or active_media_policy_source()).strip().lower()
    if _policy_source == "step02a" and qa_report is None:
        _qa_path = Path(working_dir) / "step02a_quality.json"
        if _qa_path.is_file():
            qa_report = json.loads(_qa_path.read_text(encoding="utf-8"))
    _resolved_policies: dict[int, str] = {}
    for shot in tasks:
        _sidx = int(shot["index"])
        _resolved_policies[_sidx] = resolve_shot_media_policy(
            _sidx, qa_report=qa_report, policy_source=_policy_source,
        )
    for shot in tasks:
        if post_budget.quota_exhausted("video"):
            logger.warn("  [BUDGET] 视频额度已耗尽/熔断：停止后续镜头提交；"
                        "已提交任务继续轮询，额度恢复后可断点续跑剩余镜头")
            break
        poll_task = await _process_one_shot_skip_frames(
            shot, shots_dir, video_adapter,
            script_mtime, script,
            poll_semaphore=poll_semaphore,
            force=force,
            candidate_context=(candidate_factory(shot["index"])
                               if candidate_factory is not None else None),
            media_policy=_resolved_policies[int(shot["index"])],
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
    media_policy: str | None = None,
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

    # S02/S03：按单镜解析后的策略定素材（必需项缺失即失败），再判缓存。
    from shared.video_media_plan import active_media_policy
    policy = (media_policy or active_media_policy()).strip().lower()
    REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
    if REFERENCE_MODE == "r2v" or REFERENCE_MODE == "text":
        # r2v：参考图由 prompt_builder._make_request 权威收集（角色肖像），
        # 此处不传任何路径，避免传递不存在的 keyframes/ 子目录（旧逻辑断链）。
        # text：纯文生视频，不使用任何帧图片。
        frame_paths = []
        logger.info(f"  [Shot {idx}] Mode: {REFERENCE_MODE.upper()} (no frame files)")
    else:
        frame_paths = _collect_frame_paths(idx, frames_dir, policy)
        logger.info(f"  [Shot {idx}] Mode: IMAGE policy={policy} "
                    f"frames={[Path(p).name for p in frame_paths]}")

    # S04：读取 Step02a 画面计划并构建同一份执行要求；实际提交与 plan-only
    # 共用 build_requirements，缓存键因此也包含语义镜头/参考/声音事实。
    from shared.model_caps import active_video_model
    _visual_plan = _load_visual_plan(shots_dir.parent)
    _reqs = _execution_requirements(shot, script, _visual_plan, frame_paths,
                                    str(active_video_model() or ""),
                                    media_policy=policy)
    _req_issues = list(_reqs.get("issues") or [])
    shot_hash = _get_shot_hash(shot, script, shot_dir=frames_dir, media_policy=policy)
    _prepared_facts = None
    if _req_issues:
        logger.warn(f"  [Shot {idx}] 执行要求问题: {_req_issues[:3]}")
        # 跳过缓存后仍按同一入口提交；提交路径会在 prepare 后执行
        # `validate_requirements` 并拒收（零 POST），不会静默复用旧产物。
        logger.warn(f"  [Shot {idx}] 执行要求未通过，跳过缓存，交给 validate_requirements 拒收")
    else:
        # 只有要求干净时才允许走缓存：否则命中旧产物会绕过提交路径上的
        # `validate_requirements`，把本应被拦的视频直接复用（F3）。
        # S02-C1：缓存关系核对与提交同源——先 prepare 一次，失败即终局。
        _cache = None
        if REFERENCE_MODE == "image":
            from step02_video_gen.submit_poll import build_prepared_facts
            from shared.run_scope import current_scope_identity
            _scope = dict(getattr(candidate_context, "scope", None)
                          or current_scope_identity())
            _rev = str(getattr(candidate_context, "script_revision", "")
                       or _script_revision(script))
            _prepared_facts = build_prepared_facts(
                shot=shot, script=script, video_adapter=video_adapter,
                frame_paths=frame_paths, mode="A", scope=_scope,
                script_revision=_rev,
                provider=os.getenv("VIDEO_PROVIDER", ""),
                model_id=active_video_model(),
                execution_requirements=_reqs,
                candidate_context=candidate_context,
                media_policy=policy)
            if _prepared_facts.status == "prepared":
                _cache = cache_decision(
                    shot, script, shot_dir, video_path, clip_path=_clip_dst,
                    force=force, shot_hash=shot_hash,
                    frozen_request=_prepared_facts.frozen,
                    require_exact=(policy != "auto"))
            elif _prepared_facts.status == "unsupported":
                # 旧兼容只允许"没有快照"；有快照但无法核对 ⇒ 不得 legacy 命中。
                if read_frozen_snapshot(shot_dir) is None:
                    _cache = cache_decision(
                        shot, script, shot_dir, video_path, clip_path=_clip_dst,
                        force=force, shot_hash=shot_hash, frozen_request=None)
                else:
                    logger.warn(f"  [Shot {idx}] 快照存在但 provider 无 prepared 入口，"
                                f"拒绝按 legacy 复用")
            else:
                logger.warn(f"  [Shot {idx}] prepare 失败，跳过缓存: "
                            f"{(_prepared_facts.error or {}).get('code')}")
        else:
            # r2v/text 无帧级冻结关系：保持旧行为（无快照/无请求 ⇒ legacy）。
            _cache = cache_decision(shot, script, shot_dir, video_path,
                                    clip_path=_clip_dst, force=force,
                                    shot_hash=shot_hash, frozen_request=None)
        if _cache is not None and _cache["hit"]:
            logger.info(f"  [Shot {idx}] Cache hit({_cache['relation']}), skip: {_cache['reason']}")
            return None
        if _cache is not None and _cache["reason"] == "clip 产物缺失" and _clip_dst is not None:
            # R3：视频本体与 prompt_hash 完好、只有 clips/ 副本缺失 ⇒ 本地补拷，零远端 POST。
            if repair_missing_clip(shot_dir, video_path, _clip_dst):
                logger.info(f"  [Shot {idx}] Clip 缺失但视频完好，本地补拷 clips/（0 次远端提交）")
                return None
            logger.warn(f"  [Shot {idx}] Clip 缺失且本地补拷失败，回退为重新生成")

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, script, idx,
        continuity_ref_image=None, mode="A",
        candidate_context=candidate_context,
        execution_requirements=_reqs,
        prepared_facts=_prepared_facts,
        media_policy=policy,
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


def _load_visual_plan(working_dir: Path) -> dict | None:
    """读取 Step02a 写出的候选画面计划；缺失/损坏返回 None（调用方记未验证）."""
    path = Path(working_dir) / "visual_plan.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _execution_requirements(shot: dict, script: dict, visual_plan: dict | None,
                            frame_paths: list, model_id: str | None,
                            *, media_policy: str | None = None) -> dict:
    from step02_video_gen.execution_requirements import build_requirements
    return build_requirements(
        shot, script, visual_plan=visual_plan, frame_paths=frame_paths,
        reference_mode=os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower(),
        model_id=model_id, media_policy=media_policy)


def _audio_overflow_tolerance() -> float:
    """配音超窗容差（秒）：≤ 容差视为帧量化误差，不阻断生视频."""
    raw = os.getenv("STEP02B_AUDIO_OVERFLOW_TOLERANCE", "0.15").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.15
    return value if value >= 0 else 0.15


AUDIT_SCHEMA = "step03_duration_audit.v1"
#: 审计身份里与 scope 对齐的字段（缺省字段不比较，存在即必须一致）
_AUDIT_SCOPE_FIELDS = ("chapter", "book_index", "book_name", "novel_name", "part")


def _audit_identity_issues(audit: dict, *, script: dict, scope: dict | None) -> list[dict]:
    """核对审计报告的**身份**：schema / scope / script_revision / 音频摘要.

    只认"就是本次这份稿、这批音频"的实测数据。任何不一致都不得当作合法实测下限：
    旧章报告会被当成当前镜头的超窗证据（或旧短音频漏掉当前超窗），两者都是错误裁决。
    """
    issues: list[dict] = []
    if str(audit.get("schema") or "") != AUDIT_SCHEMA:
        issues.append({"code": "audit_schema_mismatch",
                       "actual": str(audit.get("schema") or "")})
        return issues
    audit_scope = audit.get("scope") if isinstance(audit.get("scope"), dict) else {}
    current_scope = scope if isinstance(scope, dict) else {}
    for field in _AUDIT_SCOPE_FIELDS:
        if field not in audit_scope and field not in current_scope:
            continue
        if str(audit_scope.get(field)) != str(current_scope.get(field)):
            issues.append({"code": "audit_scope_mismatch", "field": field,
                           "audit": str(audit_scope.get(field)),
                           "current": str(current_scope.get(field))})
    expected_rev = str(script.get("script_revision") or "")
    audit_rev = str(audit.get("script_revision") or "")
    if expected_rev and audit_rev != expected_rev:
        issues.append({"code": "audit_revision_mismatch",
                       "audit": audit_rev, "current": expected_rev})
    from shared.enhancement_io import sha256_file
    digest_mismatch = 0
    missing = 0
    identity_missing: list[str] = []
    for unit in audit.get("units") or []:
        if not isinstance(unit, dict):
            continue
        state = str(unit.get("state") or "")
        sha = str(unit.get("audio_sha256") or "").strip()
        path = str(unit.get("audio_path") or "").strip()
        if state != "confirmed":
            # prepared 单元允许没有音频身份，但也不能贡献实测下限（由投影保证）。
            continue
        if not sha or not path:
            identity_missing.append(str(unit.get("unit_id") or "?"))
            continue
        candidate = Path(path)
        if not candidate.is_file():
            missing += 1
            continue
        try:
            if sha256_file(candidate) != sha:
                digest_mismatch += 1
        except OSError:
            missing += 1
    if identity_missing:
        # 实测单元缺路径/摘要 ⇒ 拒用整份审计（不能当合法实测证据）
        issues.append({"code": "audit_identity_missing",
                       "units": sorted(identity_missing)[:20],
                       "count": len(identity_missing)})
    if digest_mismatch:
        issues.append({"code": "audit_audio_digest_mismatch", "count": digest_mismatch})
    if missing:
        issues.append({"code": "audit_audio_missing", "count": missing})
    return issues


def _load_duration_audit(shots_dir: Path, *, audit: dict | None = None,
                         script: dict | None = None,
                         scope: dict | None = None) -> tuple[dict | None, str, list]:
    """读 Step03 实测时长审计（`step03_duration_audit.json`）；不猜、不找"最新".

    查找顺序：显式 ``audit`` → 章目录（正式落点） → ``shots_dir`` 的父目录
    （候选/实验根的同层落点）。两处都没有 ⇒ ``(None, "", [])``，计划里如实记无实测下限。
    找到但**身份不符/音频摘要不符** ⇒ ``(None, path, issues)``：拒绝使用，并留下拒绝原因
    （调用方据此告警，不得把旧数据当本次实测）。
    """
    if isinstance(audit, dict) and audit:
        issues = _audit_identity_issues(audit, script=script or {}, scope=scope)
        return (None if issues else audit), "explicit", issues
    candidates: list[Path] = []
    try:
        candidates.append(Path(chapter_dir()) / "step03_duration_audit.json")
    except Exception:
        pass
    candidates.append(Path(shots_dir).parent / "step03_duration_audit.json")
    for path in candidates:
        if not path.is_file():
            continue
        # 找到的第一份就是本次数据源：坏 JSON / 错 schema 必须报**拒收原因**，
        # 不能伪装成"没有审计"再去翻下一个目录。
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 结构化原因透传
            return None, str(path), [{"code": "audit_unreadable",
                                      "source": str(path),
                                      "error": "%s: %s" % (type(exc).__name__, exc)}]
        if not isinstance(data, dict):
            return None, str(path), [{"code": "audit_not_object",
                                      "source": str(path),
                                      "actual_type": type(data).__name__}]
        if data.get("schema") != AUDIT_SCHEMA:
            return None, str(path), [{"code": "audit_schema_mismatch",
                                      "source": str(path),
                                      "actual": str(data.get("schema") or "")}]
        issues = _audit_identity_issues(data, script=script or {}, scope=scope)
        return (None if issues else data), str(path), issues
    return None, "", []


def _shot_audio_audit(audit: dict | None, shot_index) -> dict | None:
    """某镜的实测音频下限（只认 confirmed 实测；planned-only 不冒充超窗证据）."""
    if not isinstance(audit, dict):
        return None
    shots = audit.get("shots")
    if not isinstance(shots, dict):
        return None
    entry = shots.get(str(shot_index))
    return dict(entry) if isinstance(entry, dict) else None


def _audio_overflow(shot_audit: dict | None, shot: dict, tolerance: float) -> dict | None:
    """实测需要时长 > 可用窗口 + 容差 ⇒ 返回结构化超窗（否则 None）.

    优先用审计里的 ``window_sec``（已扣转场重叠的真实可用窗口）；没有才退回计划时长。
    """
    if not shot_audit:
        return None
    if shot_audit.get("measured") is not True:
        # prepared / 未实测的投影不得作为超窗证据
        return None
    need = shot_audit.get("audio_need_sec")
    planned = shot_audit.get("window_sec")
    if planned is None:
        planned = shot.get("duration_sec") or shot.get("duration")
    try:
        need = float(need) if need is not None else None
        planned = float(planned) if planned is not None else None
    except (TypeError, ValueError):
        return None
    if need is None or planned is None or planned <= 0:
        return None
    overflow = need - planned
    if overflow <= max(0.0, tolerance) + 1e-9:
        return None
    return {
        "overflow_sec": round(overflow, 6),
        "recommended_shot_dur": round(max(float(shot_audit.get("recommended_shot_dur") or 0.0),
                                          need), 3),
        "message": ("镜头 %s 实测配音 %.2fs 超过可用窗口 %.2fs（+%.2fs，容差 %.2fs）——"
                    "先改台词/拆镜或按推荐时长重排 step01，禁止带着超长台词生短视频"
                    % (shot.get("index"), need, planned, overflow, tolerance)),
    }


def _narration_overflows(audit: dict | None, tolerance: float) -> list[dict]:
    """跨镜旁白按**整段承载窗口**对账（不按 N 序号当镜头号）."""
    if not isinstance(audit, dict):
        return []
    out: list[dict] = []
    for unit_id, entry in (audit.get("narrations") or {}).items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("state") or "") != "confirmed":
            continue
        need = entry.get("actual_wav_sec")
        window = entry.get("carrier_window_sec")
        try:
            need = float(need) if need is not None else None
            window = float(window) if window is not None else None
        except (TypeError, ValueError):
            continue
        if need is None or window is None:
            continue
        overflow = need - window
        if overflow <= max(0.0, tolerance) + 1e-9:
            continue
        out.append({
            "unit_id": str(unit_id),
            "kind": "narration",
            "shot_indexes": list(entry.get("shot_indexes") or []),
            "overflow_sec": round(overflow, 6),
            "audio_need_sec": round(need, 6),
            "window_sec": round(window, 6),
            "message": ("旁白 %s 实测 %.2fs 超过承载窗口 %.2fs（+%.2fs）——跨镜旁白放不下，"
                        "先调整镜头时长或旁白文本" % (unit_id, need, window, overflow)),
        })
    return out


def _assert_audio_fit(script: dict, shots_dir: Path, only_indices,
                      *, duration_audit: dict | None = None,
                      scope: dict | None = None) -> dict:
    """生视频前核对实测配音下限：超窗镜头/旁白**启动即拒**（避免盲生短视频后截字）.

    返回``{"source", "rejected", "tolerance_sec", "checked", "blocked"}``；无审计文件时
    ``source=None``（如实记"无实测下限"，不假装已核）；审计身份不符时 ``rejected`` 列出原因。
    """
    audit, source, issues = _load_duration_audit(shots_dir, audit=duration_audit,
                                                 script=script, scope=scope)
    tolerance = _audio_overflow_tolerance()
    report = {"source": source or None, "rejected": list(issues or []),
              "tolerance_sec": tolerance, "checked": 0, "blocked": []}
    if not audit:
        return report
    hook = script.get("hook")
    queue = ([dict(hook, index=0)] if isinstance(hook, dict) else []) + list(
        script.get("shots") or [])
    for shot in queue:
        idx = shot.get("index")
        if only_indices is not None and idx not in only_indices:
            continue
        info = _shot_audio_audit(audit, idx)
        if not info:
            continue
        report["checked"] += 1
        problem = _audio_overflow(info, shot, tolerance)
        if problem:
            report["blocked"].append({"shot_index": idx, **problem,
                                      "audio_audit": info})
    report["blocked"].extend(_narration_overflows(audit, tolerance))
    return report


def build_plan(script: dict, shots_dir: Path, *, only_indices: set[int] | None,
               chapter: int, provider: str, model_id: str | None = None,
               precheck_ledger: Path | None = None,
               visual_plan: dict | None = None,
               ref_work_dir: Path | None = None,
               scope: dict | None = None,
               policy_source: str | None = None,
               qa_report: dict | None = None,
               duration_audit: dict | None = None) -> dict:
    """只组装**冻结请求 + 结构预检**（零网络、零生成 POST，§V2「plan-only+force 仍零网络」）。

    M4：image 模式下额外做首尾帧 SHA-256 / 差异度预检；给了
    ``precheck_ledger`` 时把结果追加到持久化账本（本函数仍不写正式章目录，
    账本路径由调用方决定）。首尾帧同一张图会在计划里标 blocked。

    注意：provider 的压缩/合规改写只在实际 prepare 时发生，因此这里冻结的是
    "组装完成、尚未 provider 化"的事实，``semantic_preflight=unverified``。

    ``ref_work_dir``：r2v 超限参考表的落盘根（plan-only 传候选实验根）。**不给**
    时 `_make_request` 用系统临时目录，绝不写生产 ``shots/``（F2）。
    """
    from shared.model_caps import active_video_model
    from shared.video_media_plan import (active_media_policy_source,
                                         resolve_shot_media_policy,
                                         validate_media_policy_context)
    from step02_video_gen import request_plan as rp

    _policy_source = (policy_source or active_media_policy_source()).strip().lower()
    if _policy_source == "step02a" and qa_report is None:
        _qa_disk = Path(shots_dir).parent / "step02a_quality.json"
        if _qa_disk.is_file():
            qa_report = json.loads(_qa_disk.read_text(encoding="utf-8"))
        else:
            raise ValueError(
                f"VIDEO_MEDIA_POLICY_SOURCE=step02a 需要 step02a 验收报告，但未找到 {_qa_disk}"
            )
    from shared.run_scope import current_scope_identity, scope_identity
    _scope = dict(scope_identity(scope)) if scope is not None else dict(current_scope_identity())
    resolved_model = model_id if model_id is not None else str(active_video_model() or "")
    if visual_plan is None:
        # S02-C1：提交路径读的是 Step02a 落盘的 visual_plan.json；plan-only 必须
        # 优先消费同一份，否则执行要求摘要不同、key 永远不同源。
        _on_disk = Path(shots_dir).parent / "visual_plan.json"
        if _on_disk.is_file():
            try:
                _loaded = json.loads(_on_disk.read_text(encoding="utf-8"))
                visual_plan = _loaded if isinstance(_loaded, dict) else None
            except Exception:
                visual_plan = None
    if visual_plan is None:
        try:
            from step02a_frame_gen.execution_plan import build_visual_plan
            _plan_scope = {"chapter": chapter,
                           "book_index": script.get("book_index") or 1,
                           "book_name": script.get("book_name") or ""}
            visual_plan = build_visual_plan(
                script, scope=_plan_scope, only_indices=only_indices)
        except Exception:
            visual_plan = None
    entries: list[dict] = []
    shots = list(script.get("shots") or [])
    hook = script.get("hook")
    queue = ([dict(hook, index=0)] if hook else []) + shots
    # S05/A2：消费 Step03 的实测时长审计（有就读；没有就按"无实测下限"如实记账）。
    # 身份不符/音频摘要不符 ⇒ 拒绝使用，并把原因写进计划（不把旧数据当本次实测）。
    _audio_audit, _audit_source, _audit_issues = _load_duration_audit(
        shots_dir, audit=duration_audit, script=script, scope=scope)
    _audio_tolerance = _audio_overflow_tolerance()
    _audit_shots: dict = {}
    _narr_overflows = _narration_overflows(_audio_audit, _audio_tolerance)
    for _nar in _narr_overflows:
        entries.append({"shot_index": _nar.get("shot_indexes") or [],
                        "mode": os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower(),
                        "verdict": "blocked", "code": "audio_overflow",
                        "field_path": "narration_lines",
                        "message": _nar["message"],
                        "narration": _nar})
    for shot in queue:
        idx = shot.get("index")
        if only_indices is not None and idx not in only_indices:
            continue
        _shot_policy = resolve_shot_media_policy(
            int(idx), qa_report=qa_report, policy_source=_policy_source,
        )
        mode = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
        entry = {"shot_index": idx, "mode": mode, "media_policy": _shot_policy, "generations": 0}
        _p_conflict = validate_media_policy_context(
            policy=_shot_policy, provider=provider, reference_mode=mode,
        )
        if _p_conflict:
            entry.update({"verdict": "blocked", "code": _p_conflict["code"],
                          "field_path": _p_conflict["field_path"],
                          "message": _p_conflict["message"]})
            entries.append(entry)
            continue
        # S05/A2：实测配音超出本镜时长 ⇒ 生视频前显式阻断（不带着超长台词盲生短视频）
        _shot_audit = _shot_audio_audit(_audio_audit, idx)
        _overflow = _audio_overflow(_shot_audit, shot, _audio_tolerance)
        if _shot_audit:
            entry["audio_audit"] = _shot_audit
            _audit_shots[str(idx)] = _shot_audit
        if _overflow:
            entry.update({"verdict": "blocked", "code": "audio_overflow",
                          "field_path": f"shots[{idx}].duration_sec",
                          "message": _overflow["message"],
                          "recommended_shot_dur": _overflow["recommended_shot_dur"],
                          "needed_change": "改台词/拆镜，或按推荐时长重排 step01（不截字）"})
            entries.append(entry)
            continue
        if mode == "image":
            dd = shots_dir / str(idx)
            try:
                frame_paths = _collect_frame_paths(idx, dd, _shot_policy)
            except FileNotFoundError as exc:
                entry.update({"verdict": "blocked", "code": "frames_missing",
                              "field_path": f"shots[{idx}].frames",
                              "message": str(exc),
                              "needed_change": "先跑 step02a_frame_gen 补齐所需帧"})
                entries.append(entry)
                continue
            # M4：first/last-frame hash + difference precheck（纯本地，零网络）。
            # 只在策略**同时要求** ff/lf 时做 pair 预检；first_only/reference
            # 没有尾帧，不能按「缺尾帧」误判 blocked。
            from shared.video_media_plan import required_frames as _required_frames
            _need = set(_required_frames(_shot_policy))
            if {"ff", "lf"} <= _need:
                from shared import frame_precheck as _fpc
                _frame_check = _fpc.precheck_shot(shot, frame_paths)
                entry["frame_precheck"] = _frame_check
                if precheck_ledger is not None:
                    try:
                        _fpc.record(precheck_ledger, _frame_check)
                    except Exception as _pc_exc:  # 账本损坏不阻断离线规划，但必须可见
                        entry.setdefault("warnings", []).append({
                            "code": "precheck_ledger_error",
                            "message": "首尾帧预检账本读写失败: %s" % _pc_exc,
                        })
                if _frame_check["verdict"] == "blocked":
                    entry.update({
                        "verdict": "blocked",
                        "code": _frame_check["code"],
                        "field_path": f"shots[{idx}].frames",
                        "message": "；".join(p["message"] for p in _frame_check["problems"]),
                        "needed_change": "重新生成缺失/损坏/重复的首尾帧后再提交",
                    })
                    entries.append(entry)
                    continue
        else:
            frame_paths = []
        _reqs = _execution_requirements(shot, script, visual_plan, frame_paths,
                                        resolved_model, media_policy=_shot_policy)
        entry["execution_requirements"] = _reqs
        _prepared_status = "not_prepared"
        _prep_block = None
        try:
            frozen, _req = build_frozen_for_shot(
                shot=shot, script=script, video_adapter=None, frame_paths=frame_paths,
                mode="A", scope=_scope, provider=provider,
                model_id=resolved_model, execution_requirements=_reqs,
                media_policy=_shot_policy,
                ref_sheet_dir=((Path(ref_work_dir) / str(idx))
                               if ref_work_dir is not None else None))
            # S04：预检走与实发相同的 prepare 链路（纯离线，不提交）。
            try:
                from _video_gen import create_service
                from _video_gen.models import VideoCapabilityError
                from shared.video_prompt_system import SystemCameraError
                _svc = create_service(provider)
                if _svc is None or not callable(getattr(_svc, "prepare_request", None)):
                    _prepared_status = "pending_verification: provider 未实现 prepare_request"
                else:
                    _prepared = _svc.prepare_request(_req)
                    if not _prepared.is_ready():
                        _prepared_status = ("pending_verification: %s"
                                            % (_prepared.unsupported_reason or "prepared_not_ready"))
                    else:
                        frozen, _req = build_frozen_for_shot(
                            shot=shot, script=script, video_adapter=None, frame_paths=frame_paths,
                            mode="A", scope=_scope, provider=provider,
                            model_id=resolved_model, prepared=_prepared,
                            execution_requirements=_reqs,
                            media_policy=_shot_policy,
                            ref_sheet_dir=((Path(ref_work_dir) / str(idx))
                                           if ref_work_dir is not None else None))
                        _prepared_status = "prepared"
            except (SystemCameraError, VideoCapabilityError) as _prep_exc:
                # 确定阻断：系统段冲突/超长、provider 能力不足
                _prep_block = {"code": str(getattr(_prep_exc, "code", "preflight_blocked")),
                               "message": str(getattr(_prep_exc, "message", str(_prep_exc)))[:500]}
            except Exception as _prep_exc:  # noqa: BLE001 - 无法离线准备：待验证，不 ready
                _prepared_status = "pending_verification: %s" % str(_prep_exc)[:200]
        except Exception as exc:  # noqa: BLE001 - 组装失败作为 blocked 证据，不抛栈
            entry.update({"verdict": "blocked", "code": "request_assembly_failed",
                          "message": str(exc)[:500],
                          "needed_change": "补齐参考图/注册表后重跑 plan"})
            entries.append(entry)
            continue
        if _prep_block:
            entry.update({"verdict": "blocked", "code": _prep_block["code"],
                          "message": _prep_block["message"],
                          "prepared_status": "blocked",
                          "problems": [{"code": _prep_block["code"],
                                        "message": _prep_block["message"]}],
                          "generations": 0})
            entries.append(entry)
            continue
        if str(_prepared_status).startswith("pending_verification"):
            entry.update({"verdict": "pending", "code": "prepared_pending_verification",
                          "message": _prepared_status,
                          "prepared_status": _prepared_status,
                          "generations": 0})
            entries.append(entry)
            continue
        problems = rp.validate_frozen_request(frozen)
        from step02_video_gen.execution_requirements import validate_requirements
        problems = list(problems) + validate_requirements(frozen, _reqs)
        # S22-D：运镜一致性告警；默认不阻断，SHOT_MOTION_STRICT=1 时升级为 blocked。
        from shared.shot_motion_checks import motion_warnings
        warnings = motion_warnings(shot)
        if os.getenv("SHOT_MOTION_STRICT", "0").strip() == "1" and warnings:
            problems = list(problems) + warnings
        entry.update({
            "verdict": "blocked" if problems else "ready",
            "request_key": frozen.request_key,
            "request": frozen.to_dict(),
            "problems": problems,
            "warnings": warnings,
            "prepared_status": _prepared_status,
            "semantic_preflight": "unverified",
            "generations": 0 if problems else 1,
        })
        entries.append(entry)
    ready = [e for e in entries if e.get("verdict") == "ready"]
    blocked = [e for e in entries if e.get("verdict") == "blocked"]
    pending = [e for e in entries if e.get("verdict") == "pending"]
    _audio_summary = {
        "source": _audit_source or None,
        "rejected": list(_audit_issues or []),
        "tolerance_sec": _audio_tolerance,
        "shots": _audit_shots,
        "overflow": sorted(str(e.get("shot_index")) for e in entries
                           if e.get("code") == "audio_overflow"),
        "narration_overflows": _narr_overflows,
    }
    return {"schema_version": "video_reroll_plan.v1", "chapter": chapter, "provider": provider,
            "model_id": resolved_model,
            "shots": entries, "ready": len(ready), "blocked": len(blocked),
            "pending": len(pending),
            "audio_audit": _audio_summary,
            "estimated_generations": sum(int(e.get("generations") or 0) for e in entries),
            "network_calls": 0}


def _plan_only(options, script: dict, shots_dir: Path, chapter: int,
               scope: dict | None = None,
               policy_source: str | None = None,
               qa_report: dict | None = None) -> int:
    """plan-only：零网络规划，落计划文件到候选实验根（不写正式产物）。

    2026-11-13（F2）：plan-only 也做 production snapshot 前后比对——r2v 参考表
    拼表等写路径一旦误落正式 ``shots/``，必须显式报 ``production_write_detected``，
    不能只写候选 ``plans/`` 就当隔离成立。
    """
    from shared import enhancement_io as _eio
    from shared.enhancement_io import package_root
    from shared.quality_gate import QualityGateError, require_gate
    from shared.video_media_plan import active_media_policy_source
    from step02b_video_gen import cli

    experiment_id = cli.resolve_experiment_root(options.candidate_root, chapter=chapter)
    out_root = package_root("video", experiment_id, chapter) / "plans"
    out_root.mkdir(parents=True, exist_ok=True)

    from shared import log_utils as _log_utils
    _prev_log_dir = _log_utils.current_log_dir()
    if _prev_log_dir is None:
        _log_utils.set_log_context(out_root.parent / "logs")
    try:
        only_indices = parse_shot_selector(options.shots)
        _src = (policy_source or active_media_policy_source()).strip().lower()
        if _src == "step02a":
            if qa_report is None:
                try:
                    qa_report = require_gate(
                        chapter_dir() / "step02a_quality.json",
                        script,
                        stage="step02a",
                        required_report_type="product_acceptance",
                        verify_outputs=False,
                    )
                except (FileNotFoundError, QualityGateError, ValueError, OSError) as _qa_exc:
                    print(f"[ERROR] {_qa_exc}", flush=True)
                    return _eio.EXIT_REJECTED
            try:
                _assert_step02a_scope(qa_report, only_indices, script, policy_source=_src)
            except (QualityGateError, ValueError) as _sc_exc:
                print(f"[ERROR] {_sc_exc}", flush=True)
                return _eio.EXIT_REJECTED
        ledger = out_root / "step02b_frame_precheck.json"
        before = production_snapshot(chapter)
        # 参考表/清单落点也钉到实验根：拼表逻辑只认参数，不知道"现在是只读预检"，
        # 不钉就会重写正式 shots/N/ref_compose_*.json（只差时间戳，但字节变了）。
        from shared import ref_compose as _ref_compose
        _prev_ref_dir = _ref_compose.current_ref_dir()
        _ref_compose.set_ref_dir_context(out_root / "refs")
        try:
            plan = build_plan(script, shots_dir, only_indices=only_indices, chapter=chapter,
                              provider=str(os.getenv("VIDEO_PROVIDER", "")),
                              precheck_ledger=ledger, ref_work_dir=out_root / "refs",
                              scope=scope, policy_source=_src, qa_report=qa_report)
        except ValueError as _plan_exc:
            print(f"[ERROR] {_plan_exc}", flush=True)
            return _eio.EXIT_INVALID_INPUT
        finally:
            _ref_compose.set_ref_dir_context(_prev_ref_dir)
        path = out_root / f"plan_{len(list(out_root.glob('plan_*.json'))) + 1:04d}.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        changes = snapshot_changes(before, production_snapshot(chapter))
        payload = {"plan": str(path), "ready": plan["ready"], "blocked": plan["blocked"],
                   "frame_precheck_ledger": str(ledger),
                   "estimated_generations": plan["estimated_generations"],
                   "production_unchanged": not any(changes.values()),
                   "network_calls": 0}
        if any(changes.values()):
            payload["isolation_violation"] = changes
            payload["code"] = "production_write_detected"
            print(json.dumps(payload, ensure_ascii=False))
            return _eio.EXIT_TOOL_FAIL
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    finally:
        if _prev_log_dir is None:
            _log_utils.set_log_context(None)


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
                   chapter: int, shots_dir: Path, *,
                   policy_source: str | None = None,
                   qa_report: dict | None = None) -> int:
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

    from shared import request_events as _req_ev
    _ev_path = package_root_dir / "logs" / "step02b_events.jsonl"
    _ev_tok = _req_ev.set_step02b_events_path(_ev_path)
    before = production_snapshot(chapter)
    posts_before = va.submit_post_count(package_root_dir)
    try:
        failed = asyncio.run(gen_all_videos(
            script, work_dir, script_path, only_indices=only_indices, force=options.force,
            candidate_factory=_factory, log_path=batch_log,
            policy_source=policy_source, qa_report=qa_report))
    finally:
        _req_ev.reset_step02b_events_path(_ev_tok)
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
               "generations": va.generations(package_root_dir),
               "events_path": str(_ev_path),
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


def _assert_step02a_scope(report: dict, only_indices, script: dict, *,
                          policy_source: str | None = None) -> None:
    """子集运行的门禁范围核对：报告覆盖的镜头必须包含本次要跑的镜头。

    step02a 的成品验收报告现在带 scope.shots（--shots 子集运行时只覆盖子集）。
    没有 scope 的旧报告视为全章覆盖（保持旧行为）。不能让「shot 1 的 PASS」
    给整章放行。
    """
    from shared.quality_gate import QualityGateError
    from shared.video_media_plan import (active_media_policy,
                                         active_media_policy_source,
                                         resolve_shot_media_policy)

    scope = (report or {}).get("scope") or {}
    _policy_source = (policy_source or active_media_policy_source()).strip().lower()
    wanted = set(only_indices) if only_indices is not None else None
    if wanted is None:
        wanted = {int(s.get("index", 0)) for s in script.get("shots", [])}
        if script.get("hook"):
            wanted.add(0)

    if _policy_source == "step02a":
        for idx in sorted(wanted):
            try:
                resolve_shot_media_policy(idx, qa_report=report, policy_source="step02a")
            except ValueError as exc:
                raise QualityGateError(str(exc)) from None
    else:
        _current_policy = active_media_policy()
        # 2026-11-18 O2：报告可带**逐镜策略**（shot 1 auto / shot 2 first_only）。
        # 有 policies 时按本次请求的每个镜头核它自己的策略；没有时才退回全报告
        # 单一 media_policy 的旧口径。
        _policies = scope.get("policies")
        if isinstance(_policies, dict) and _policies:
            for idx in sorted(wanted):
                declared = _policies.get(str(idx))
                if declared is None:
                    raise QualityGateError(
                        "step02a 验收缺少 shot %s 的逐镜策略（报告覆盖 %s）"
                        % (idx, sorted(_policies)))
                if str(declared) != _current_policy:
                    raise QualityGateError(
                        "step02a shot %s 策略 %s 与当前策略 %s 不一致，需重跑 step02a"
                        % (idx, declared, _current_policy))
        else:
            # S02：成品报告必须与当前素材策略同一口径；旧报告（无 media_policy）只在 auto 下沿用。
            _report_policy = scope.get("media_policy")
            if _report_policy is None:
                if _current_policy != "auto":
                    raise QualityGateError(
                        "step02a 成品报告缺少 media_policy（旧报告），当前策略 %s 需重跑 step02a"
                        % _current_policy)
            elif str(_report_policy) != _current_policy:
                raise QualityGateError(
                    "step02a 成品报告策略 %s 与当前策略 %s 不一致，需重跑 step02a"
                    % (_report_policy, _current_policy))
    scoped = {int(i) for i in (scope.get("shots") or [])}
    if not scoped:
        return
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
    # S01/S03：显式策略来源与运行上下文校验（支持按镜头从 step02a 继承混合策略）。
    from shared.video_media_plan import (active_media_policy,
                                         active_media_policy_source,
                                         resolve_shot_media_policy,
                                         validate_media_policy_context)
    try:
        _policy_source = (getattr(options, "media_policy_source", None)
                          or active_media_policy_source()).strip().lower()
        _policy = active_media_policy() if _policy_source == "env" else "auto"
    except ValueError as _policy_exc:
        print(f"[ERROR] {_policy_exc}", flush=True)
        raise SystemExit(2) from None
    if _policy_source == "env":
        _policy_conflict = validate_media_policy_context(
            policy=_policy, provider=os.getenv("VIDEO_PROVIDER", ""),
            reference_mode=REFERENCE_MODE)
        if _policy_conflict:
            print(f"[ERROR] {_policy_conflict['message']}", flush=True)
            raise SystemExit(2)
    elif REFERENCE_MODE != "image":
        # S03：逐镜策略的唯一事实源是 step02a 帧验收报告（scope.policies），
        # 只有 image 通道才有该报告与逐镜素材。别的通道显式要求它 = 配置错误，
        # 启动即拒绝，不等到提交前才抛未捕获异常。
        print(f"[ERROR] --media-policy-source=step02a 只适用于 VIDEO_REFERENCE_MODE=image"
              f"（当前 {REFERENCE_MODE}）：逐镜混合策略来自 step02a 帧验收报告",
              flush=True)
        raise SystemExit(2)
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
    _qa_report = None
    # 2026-10-28（M10）：`--plan-only` 是**零网络结构预检**，它的输出本身就是
    # 「哪些镜头缺帧/缺参考」（`frames_missing` 等 blocked 条目）。此前它排在
    # step02a 成品验收门**之后**，没有帧的章节走不到预检就先被门拦住，
    # 「没有图片不妨碍执行视频 plan-only」在入口上不成立。预检不生成、不提交、
    # 只写候选实验根，故跳过的是 step02a 门；step01 的质量与提交绑定仍然必查。
    if REFERENCE_MODE == "image" and not options.plan_only:
        _qa_report = require_gate(chapter_dir() / "step02a_quality.json", script,
                                  stage="step02a",
                                  required_report_type="product_acceptance",
                                  verify_outputs=True, output_indices=only_indices)
        _assert_step02a_scope(_qa_report, only_indices, script, policy_source=_policy_source)

    # ---- 候选模式分支（plan-only / resume / 候选生成）----
    if options.resume:
        raise SystemExit(_resume(options))
    if options.plan_only:
        if not options.candidate_root:
            logger.error("[ERROR] --plan-only 必须配合 --candidate-root 使用")
            raise SystemExit(2)
        raise SystemExit(_plan_only(options, script, shots_dir, scope["chapter"],
                                    scope=scope, policy_source=_policy_source,
                                    qa_report=_qa_report))

    # S05/A2：实测配音超窗的镜头不允许生视频（不截字，也不盲生短视频）
    _audio_fit = _assert_audio_fit(script, shots_dir, only_indices, scope=scope)
    if _audio_fit["rejected"]:
        logger.warn("[AUDIO-FIT] 时长审计身份不符，已拒绝作为实测下限：%s"
                    % _audio_fit["rejected"])
    if _audio_fit["source"]:
        logger.info("[AUDIO-FIT] 实测配音下限：核 %d 镜，超窗 %d 条（来源 %s）"
                    % (_audio_fit["checked"], len(_audio_fit["blocked"]),
                       _audio_fit["source"]))
    else:
        logger.warn("[AUDIO-FIT] 未找到可用的 step03_duration_audit.json：本次生视频**无"
                    "实测配音下限**（先跑 Step03 才能按真实时长前置拦截超窗）")
    if _audio_fit["blocked"]:
        for item in _audio_fit["blocked"]:
            print(f"[ERROR] {item['message']}", flush=True)
        raise SystemExit(2)

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
        from shared.video_media_plan import required_frames
        missing_frames = []
        for idx in expected:
            shot_policy = resolve_shot_media_policy(
                int(idx), qa_report=_qa_report, policy_source=_policy_source,
            )
            _s_conflict = validate_media_policy_context(
                policy=shot_policy, provider=os.getenv("VIDEO_PROVIDER", ""),
                reference_mode=REFERENCE_MODE)
            if _s_conflict:
                print(f"[ERROR] Shot {idx}: {_s_conflict['message']}", flush=True)
                raise SystemExit(2)
            _need = required_frames(shot_policy)
            d = shots_dir / str(idx)
            ff = d / FIRST_FRAME_FILENAME
            lf = d / LAST_FRAME_FILENAME
            _miss = []
            if "ff" in _need and not ff.exists():
                _miss.append("ff")
            if "lf" in _need and not lf.exists():
                _miss.append("lf")
            if shot_policy == "reference":
                _kfs = [p for p in _core_keyframes_in(d) if p.exists()]
                if not (ff.exists() or lf.exists() or _kfs):
                    _miss.append("ff/lf/kf")
            if _miss:
                missing_frames.append((idx, "+".join(_miss)))
        if missing_frames:
            detail = ", ".join(f"shot {idx}: missing {need}" for idx, need in missing_frames)
            raise FileNotFoundError(
                f"image 模式必需首尾帧缺失（禁止尾帧冒充首帧/静默降级）: {detail}"
            )

    if options.candidate_root:
        raise SystemExit(_run_candidate(options, script, work_dir, script_path,
                                        scope["chapter"], shots_dir,
                                        policy_source=_policy_source,
                                        qa_report=_qa_report))

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
        policy_source=_policy_source, qa_report=_qa_report,
    ))

    # Separate product acceptance from the pre-generation prompt report.
    try:
        from step02_video_gen.video_prompt_quality import write_video_product_quality
        _pq = write_video_product_quality(script, work_dir, only_indices=only_indices)
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
