"""step02_video_gen.submit_poll - submit and poll module"""
import sys
from pathlib import Path
import os
import asyncio
import shutil
import json
import random
from collections import defaultdict
import hashlib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


from shared.vimax_bridge import build_working_dir, gen_shot_frames
from shared.vimax_adapters import ImageGeneratorAiVideoAdapter, VideoGeneratorAiVideoAdapter
from shared.error_fmt import fmt_err as _fmt_err, parse_retry_after
from step02_video_gen.continuity import ContinuityTracker, MODE_B_START_INDEX
from shared.config import VIDEO_CONCURRENCY  # 并发单源: shared/config.py（默认 4）
from shared.paths import SHOT_VIDEO_FILENAME, FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
from shared.constants import VIDEO_SUBMIT_RETRIES, FRAME_COPY_RETRIES

# Smart polling config: exponential backoff + jitter
POLL_BASE_INTERVAL = int(os.getenv("POLL_BASE_INTERVAL", "180"))   # Initial poll interval (seconds) - 3 minutes
POLL_MAX_INTERVAL = int(os.getenv("POLL_MAX_INTERVAL", "180"))      # Max poll interval (seconds)
POLL_EXPONENTIAL_FACTOR = float(os.getenv("POLL_EXPONENTIAL_FACTOR", "1.5"))  # Backoff multiplier
POLL_JITTER = int(os.getenv("POLL_JITTER", "10"))                    # Jitter range +/- seconds
INITIAL_POLL_DELAY = int(os.getenv("INITIAL_POLL_DELAY", "180"))     # Wait after submit before first poll (3 minutes)

# 视频提交并发：在 _submit_video 入口处用 Semaphore 控制，与 _video_gen provider
# 的 KeyScheduler（每 Key 独立冷却）配合实现 Nx 吞吐。空闲 Key 越多，并发越多。
# 不再使用全局 RateLimiter 单令牌串行闸。（值单源 shared/config.py，默认 4）

# A6 视频产物质检（08-11 修复）：_poll_video 此前成功判定=文件>20KB，时长/内容
# 零校验，坏视频直接进 step04。现增加两级 QC：
#   1. 时长校验（always-on，ffprobe 探时长）：实际时长 vs 期望 duration_sec 偏差
#      > 阈值 → 判定坏视频（短截/黑场/动作没播完）。
#   2. 视觉质检（VIDEO_QC_ENABLED=1 时，抽取中段帧跑 ds-vision-skill）：检测人物
#      消失/字幕/幻觉。默认 0（视觉质检昂贵，逐镜跑会拖慢全片），时长校验兜底。
VIDEO_QC_ENABLED = os.getenv("VIDEO_QC_ENABLED", "0").strip() == "1"
# 时长偏差阈值：实际时长 vs 期望时长的相对偏差（比例）或绝对偏差（秒）任一超限即坏
VIDEO_DURATION_DEV_RATIO = float(os.getenv("VIDEO_DURATION_DEV_RATIO", "0.5"))
VIDEO_DURATION_DEV_SEC = float(os.getenv("VIDEO_DURATION_DEV_SEC", "1.5"))

# Global failure storage
_failed_videos = defaultdict(list)

# v6.35（08-15，P18 遗留）：QC 失败自动重提交次数。固定 seed 后同 prompt 同参考
# 重提交结果可复现（不再抽卡）。QC 失败 → 删坏视频 → 相同 req 重提交（可配 0=关闭）。
VIDEO_QC_RESUBMIT = int(os.getenv("VIDEO_QC_RESUBMIT", "2"))


def _probe_video_duration(video_path: Path) -> float:
    """用 ffprobe 探测视频实际时长（秒），失败返回 0."""
    try:
        from step04_merge.stretch import get_media_duration
        return get_media_duration(video_path)
    except Exception:
        return 0.0


def _qc_video_product(video_path: Path, shot_idx: int, expected_duration: float) -> bool:
    """视频产物内容质检（A6 修复 08-11）.

    Returns:
        bool: True = 视频有效（通过 QC）；False = 检出问题（调用方应标记失败重生成）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")
    try:
        if not video_path.exists() or video_path.stat().st_size <= 20 * 1024:
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：文件缺失或过小")
            return False
        # --- 1. 时长校验（always-on） ---
        actual = _probe_video_duration(video_path)
        if actual <= 0:
            # ffprobe 不可用/解析失败：不做时长判定（软失败，不误杀）
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长探测失败，跳过时长校验")
        else:
            exp = expected_duration or 0
            if exp > 0:
                dev_abs = abs(actual - exp)
                dev_ratio = dev_abs / exp if exp else 1.0
                if dev_abs > VIDEO_DURATION_DEV_SEC and dev_ratio > VIDEO_DURATION_DEV_RATIO:
                    logger.warn(
                        f"  [Shot {shot_idx}] 视频 QC FAIL：实际时长 {actual:.1f}s vs "
                        f"期望 {exp:.1f}s（偏差 {dev_abs:.1f}s / {dev_ratio:.0%}）——"
                        f"视频短截/动作没播完，判定坏视频，重生成"
                    )
                    return False
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长 {actual:.1f}s 校验通过")
        # --- 2. 视觉质检（可选，VIDEO_QC_ENABLED=1 时） ---
        if VIDEO_QC_ENABLED:
            try:
                from step02_video_gen.video_qc import qc_video_file
                bad = qc_video_file(video_path, shot_idx)
                if bad:
                    logger.warn(f"  [Shot {shot_idx}] 视频 QC 视觉检测发现问题，重生成")
                    return False
            except Exception as e:
                logger.verbose(f"  [Shot {shot_idx}] 视频视觉 QC 异常（不阻断）: {e}")
        return True
    except Exception as e:
        logger.verbose(f"  [Shot {shot_idx}] 视频 QC 异常（不阻断）: {e}")
        return True


def _check_keyframe_hallucination(frame_path: str) -> dict:
    """
    检查关键帧是否有 critical 级别的幻觉。

    Returns:
        dict: {"has_critical": bool, "issues": list}
    """
    if not frame_path:
        return {"has_critical": False, "issues": []}

    frame_path_obj = Path(frame_path)
    # sidecar 命名契约与 shared/image_qc.sidecar_path 完全一致：<stem>_hallucination.json。
    # 此前用 with_suffix(".png").with_name(...) 拼名 + glob("*_hallucination.json") 盲取
    # 第一个（多帧时可能张冠李戴）——统一走 image_qc.sidecar_path，消除双份命名约定（审计 P0-3）。
    from shared.image_qc import sidecar_path
    hallucination_file = sidecar_path(frame_path_obj)

    if hallucination_file.exists():
        try:
            with open(hallucination_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            has_critical = False
            critical_issues = []
            for issue in data.get("issues", []):
                if issue.get("severity") == "critical":
                    has_critical = True
                    critical_issues.append(issue)
            return {"has_critical": has_critical, "issues": critical_issues}
        except Exception:
            pass

    return {"has_critical": False, "issues": []}


def _get_shot_hash(shot: dict, script: dict) -> str:
    """
    计算分镜的哈希值，用于判断是否需要重新生成视频。

    唯一算法：读写两侧必须都用本函数（写入在 _poll_video 成功保存视频后）。
    直接哈希实际提交给视频 API 的内容，而非分镜源字段：
    - _build_prompt 输出（已含 motion_desc/subtitle/角色特征锚点/context_bridge）
    - 风格前缀 + 负面提示词（全局配置变了所有视频都该重生成）
    - narration_segment（决定 prompt 中的台词，台词变了视频该重做）
    - ff_desc + lf_desc + keyframes：图片 prompt 变了帧就变了，
      I2V 参考图变了视频也该重生成（不只是空镜）。
    - VIDEO_PROVIDER / VIDEO_REFERENCE_MODE / VIDEO_MODEL_VOICE：供应商/参考图
      模式/声音开关变化会导致 prompt 形态、参考图列表、声音指令都不同，必须失效缓存
    - r2v 模式下的参考图列表路径：换角色肖像/换场景资产图都应触发重做
    不含 duration/duration_sec：实际帧数由 duration_sec 决定（step01 timecode），
    时长变化不应触发重新生成视频。"""
    def _normalize(text: str) -> str:
        import re
        t = (text or "").strip()
        t = re.sub(r'\s+', ' ', t)
        return t

    from step02_video_gen.prompt_builder import _build_prompt
    from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT

    # Seedance 系（volcengine / agent_plan）走精简路径，缓存哈希必须跟随
    # （否则旧完整 prompt 生成的视频会被错误复用，refit 错乱）
    # 用 active_video_is_seedance() 统一判断，新增 Seedance 系 provider 自动跟随。
    from shared.model_caps import active_video_is_seedance
    if active_video_is_seedance():
        from step02_video_gen.prompt_builder import _build_seedance_concise_prompt
        _prompt_for_hash = _build_seedance_concise_prompt(shot)
    else:
        _prompt_for_hash = _build_prompt(shot, script)

    key_parts = [
        # 供应商/模式/声音开关：这些维度变化都应触发缓存失效
        f"provider={os.getenv('VIDEO_PROVIDER', '')}",
        f"ref_mode={os.getenv('VIDEO_REFERENCE_MODE', '')}",
        f"model_voice={os.getenv('VIDEO_MODEL_VOICE', '1')}",
        # r2v 模式：参考图列表路径纳入哈希（换图/换注册表都触发重做）
        f"r2v_refs={','.join(shot.get('_r2v_refs_cache', []) or [])}",
        STYLE_PREFIX,
        NEGATIVE_PROMPT,
        _prompt_for_hash,
        shot.get("narration_segment", ""),
        # v4: 图片 prompt 变了帧就变了，I2V 参考图变了视频也应重做
        _normalize(shot.get("ff_desc", "")),
        _normalize(shot.get("lf_desc", "")),
    ]
    # keyframes 数据也纳入哈希（添加/移除/修改关键帧都会触发重做）
    kfs = shot.get("keyframes") or shot.get("intermediate_keyframes") or []
    if kfs:
        for kf in kfs:
            if isinstance(kf, dict):
                key_parts.append(_normalize(kf.get("desc", "")))
                key_parts.append(str(kf.get("time_ratio", "")))
    key = "|".join(key_parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _is_recoverable(err: str) -> bool:
    err_lower = err.lower()
    terminal_markers = [
        "content_policy", "5185bb98fdd",
        "datainspectionfailed",  # 图片内容违规
        "green net check failed",  # 绿网检查失败
        "401", "unauthorized", "invalid api key",
        "403", "forbidden",
        "invalid_param", "invalid parameter",
        "model not found", "model_not_found",
    ]
    for marker in terminal_markers:
        if marker in err_lower:
            return False
    return True


async def _poll_video(
    video_adapter, semaphore: asyncio.Semaphore,
    req, task_id: str, shot_idx: int,
    shot_dir: Path, video_path: Path, max_retries: int,
    shot_hash: str = "",
    qc_resubmit_limit: int = 0,
) -> bool:
    """Background poll with exponential backoff + jitter - reduces API queries."""
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")
    qc_resubmit_left = max(0, qc_resubmit_limit)
    async with semaphore:
        # 获取提交时绑定的 key 索引
        try:
            key_idx = video_adapter._task_keys.get(task_id, video_adapter._current_key_index)
        except Exception:
            key_idx = 0
        key_display = f"key-{key_idx + 1}"

        # Wait INITIAL_POLL_DELAY seconds after submit before first query (respect 1 RPM limit)
        logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Task submitted, waiting {INITIAL_POLL_DELAY}s before polling...")
        await asyncio.sleep(INITIAL_POLL_DELAY)

        # 首次轮询前不再按旧文件短路：本任务刚提交，必须轮询新任务结果。
        # 若此处因历史遗留的旧 video.mp4 就 return True，--force/改 prompt 重生成会拿回旧视频、
        # 新任务白跑且 prompt_hash 不更新（code-audit 08-01 #1）。旧文件由下载成功后被新结果覆盖。
        # Exponential backoff state
        poll_interval = POLL_BASE_INTERVAL
        consecutive_failures = 0
        override_interval = None  # 429 时用服务端返回的剩余时间 + 0.5s（一次性生效）
        first_poll = True  # INITIAL_POLL_DELAY 已等待，首次轮询不再重复 sleep（修复双重等待）

        while True:
            try:
                # Calculate next poll time: 429 override > exponential backoff + jitter
                # 首次轮询直接查询（INITIAL_POLL_DELAY 已等过），避免 180s+180s 双重等待
                if first_poll:
                    first_poll = False
                elif override_interval is not None:
                    actual_interval = override_interval
                    override_interval = None
                    await asyncio.sleep(actual_interval)
                else:
                    jitter = random.uniform(-POLL_JITTER, POLL_JITTER)
                    actual_interval = max(1, poll_interval + jitter)
                    await asyncio.sleep(actual_interval)

                # 不再按旧文件短路：本任务结果必须经 fetch_result 拿到，成功后覆盖旧 video.mp4
                result = await video_adapter.fetch_result(req, task_id)
                if result.success and result.save_path:
                    shot_dir.mkdir(parents=True, exist_ok=True)

                    # 视频已下载成功；copy 失败只重试 copy，绝不重新轮询（否则会重复下载）
                    # 注意：bailian _download 直接存到 shots/{idx}/video.mp4，与 video_path 同路径，
                    # 此时 copy2 会抛 SameFileError，须跳过复制（文件已在目标位置）。
                    copy_ok = False
                    try:
                        same_file = Path(result.save_path).resolve() == video_path.resolve()
                    except OSError:
                        same_file = False
                    if same_file:
                        copy_ok = True
                    else:
                        for copy_attempt in range(FRAME_COPY_RETRIES):
                            try:
                                shutil.copy2(result.save_path, str(video_path))
                                copy_ok = True
                                break
                            except OSError as copy_err:
                                if "WinError 32" in str(copy_err) and copy_attempt < 9:
                                    wait = 1.0 + copy_attempt  # 递增等待 1s~10s
                                    logger.verbose(f"  [Shot {shot_idx}] File locked, retrying copy in {wait:.0f}s ({copy_attempt + 1}/10)...")
                                    await asyncio.sleep(wait)
                                else:
                                    logger.verbose(f"  [Shot {shot_idx}] Copy failed after {copy_attempt} retries: {copy_err}")
                                    break

                    if not copy_ok:
                        _failed_videos[shot_idx].append({"attempt": -1, "error": "文件被占用，copy 失败", "task_id": task_id})
                        return False

                    # A6 修复（08-11）：视频产物接 QC——此前成功判定=文件>20KB，
                    # 时长/内容零校验，坏视频直接进 step04。现做两级 QC：
                    #   1. 时长校验（ffprobe，always-on）：实际 vs 期望 duration 偏差
                    #      超限 → 短截/黑场/动作没播完，删视频标记失败重生成。
                    #   2. 视觉质检（VIDEO_QC_ENABLED=1 时抽中段帧跑 ds-vision-skill）。
                    # QC 失败不写哈希（下次缓存检测不会命中），返回 False 走失败重试。
                    _expected_dur = float(getattr(req, "duration", 0) or 0)
                    if not _qc_video_product(video_path, shot_idx, _expected_dur):
                        # v6.35：QC 失败自动重提交（相同 req = 相同 seed，结果可复现）。
                        # 固定 seed 前重提交=抽卡，好坏反复横跳；固定 seed 后同 prompt
                        # 同参考重提交仍有概率出不同结果（模型概率性），给 2 次机会，
                        # 全失败才标记失败并删产物。QC 失败不写哈希（下次缓存不命中）。
                        if qc_resubmit_left > 0:
                            qc_resubmit_left -= 1
                            logger.warn(
                                f"  [Shot {shot_idx}] 视频 QC 未通过，"
                                f"相同 seed 重提交（剩余 {qc_resubmit_left} 次）..."
                            )
                            try:
                                video_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            try:
                                new_task_id = await video_adapter.submit_task(req)
                            except Exception as _re:
                                _failed_videos[shot_idx].append({
                                    "attempt": -1, "error": f"QC 重提交失败: {_re}", "task_id": task_id,
                                })
                                return False
                            task_id = new_task_id
                            key_idx = video_adapter._task_keys.get(task_id, key_idx)
                            consecutive_failures = 0
                            first_poll = True
                            await asyncio.sleep(INITIAL_POLL_DELAY)
                            continue
                        _failed_videos[shot_idx].append({
                            "attempt": -1, "error": "视频 QC 未通过（时长偏差/内容问题）",
                            "task_id": task_id,
                        })
                        # 删坏视频 + 旧哈希，触发下次重生成
                        try:
                            video_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        try:
                            (shot_dir / "prompt_hash.txt").unlink(missing_ok=True)
                        except Exception:
                            pass
                        return False

                    # 写入哈希，供下次运行的缓存检测使用（与 _get_shot_hash 同一算法）
                    if shot_hash:
                        (shot_dir / "prompt_hash.txt").write_text(shot_hash, encoding="utf-8")

                    # 同步复制到 clips/ 目录（step04 合并用；缓存检测用）
                    from shared.paths import clip_file
                    _clip_dst = clip_file(shot_idx)
                    _clip_dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(str(video_path), str(_clip_dst))
                    except OSError:
                        pass  # clips 目录失败不影响主流程

                    return True
                else:
                    err = result.error or "unknown"
                    # 429 triggers longer backoff
                    if _is_recoverable(err):
                        consecutive_failures += 1
                        if consecutive_failures > max_retries:
                            _failed_videos[shot_idx].append({"attempt": -1, "error": f"连续失败 {consecutive_failures} 次，超过上限 {max_retries}", "task_id": task_id})
                            return False
                        if "429" in err or "rate" in err.lower():
                            wait = parse_retry_after(err)
                            if wait is not None:
                                override_interval = wait + 0.5
                                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err)}")
                            else:
                                poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited (interval {poll_interval:.0f}s): {_fmt_err(err)}")
                        else:
                            poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Processing (interval {poll_interval:.0f}s, failures {consecutive_failures})")
                        continue
                    _failed_videos[shot_idx].append({"attempt": -1, "error": err, "task_id": task_id})
                    return False
            except Exception as e:
                err_str = str(e)
                # 429 triggers longer backoff
                if _is_recoverable(err_str):
                    consecutive_failures += 1
                    if consecutive_failures > max_retries:
                        _failed_videos[shot_idx].append({"attempt": -1, "error": f"连续失败 {consecutive_failures} 次，超过上限 {max_retries}", "task_id": task_id})
                        return False
                    if "429" in err_str or "rate" in err_str.lower():
                        wait = parse_retry_after(err_str)
                        if wait is not None:
                            override_interval = wait + 0.5
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Rate limited, waiting {override_interval:.1f}s (server retry-after): {_fmt_err(err_str)}")
                        else:
                            poll_interval = min(poll_interval * 2, POLL_MAX_INTERVAL)
                            logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    else:
                        poll_interval = min(poll_interval * POLL_EXPONENTIAL_FACTOR, POLL_MAX_INTERVAL)
                        logger.verbose(f"  [Shot {shot_idx}] [{key_display}] Poll error (interval {poll_interval:.0f}s): {_fmt_err(err_str)}")
                    continue
                _failed_videos[shot_idx].append({"attempt": -1, "error": err_str, "task_id": task_id})
                return False


async def _submit_video(
    shot: dict, shot_dir: Path, video_path: Path, frame_paths: list,
    shot_hash: str, video_adapter, poll_semaphore,
    script: dict, idx: int,
    continuity_ref_image: str | None = None,
    mode: str = "A",
) -> asyncio.Task | None:
    """提交视频任务并返回轮询 task."""
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")

    prompt = _build_prompt(shot, script, mode=mode)
    characters = script.get("characters", [])

    for attempt in range(1, 1 + VIDEO_SUBMIT_RETRIES):
        try:
            req = _make_request(video_adapter, prompt, frame_paths, shot,
                                characters=characters,
                                continuity_ref_image=continuity_ref_image, mode=mode)
            task_id = await video_adapter.submit_task(req)
            task = asyncio.create_task(
                _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                           shot_dir, video_path, 99, shot_hash,
                           qc_resubmit_limit=VIDEO_QC_RESUBMIT)
            )
            return task
        except Exception as e:
            err_str = str(e)
            # 429 限流：读服务端 retry-after，等待后重试（收敛 shared/rate_limit）
            if "429" in err_str or "rate limit" in err_str.lower() or "too many requests" in err_str.lower():
                from shared.rate_limit import compute_429_wait
                wait = compute_429_wait(err_str, fallback_seconds=61.0)
                logger.verbose(f"  [镜头{idx}] 视频提交限流 429，等待 {wait:.0f}s 后重试 ({attempt}/3): {_fmt_err(err_str)}")
                await asyncio.sleep(wait)
                continue
            if "400" in err_str or "422" in err_str or "content_policy" in err_str.lower() or "5185bb98fdd" in err_str:
                from shared.content_policy_fix import auto_fix_prompt
                fixed_prompt = auto_fix_prompt(prompt, str(e), verbose=False)
                if fixed_prompt and fixed_prompt != prompt:
                    try:
                        req = _make_request(video_adapter, fixed_prompt, frame_paths, shot,
                                            characters=characters,
                                            continuity_ref_image=continuity_ref_image, mode=mode)
                        task_id = await video_adapter.submit_task(req)
                        task = asyncio.create_task(
                            _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                                       shot_dir, video_path, 3, shot_hash,
                                       qc_resubmit_limit=VIDEO_QC_RESUBMIT)
                        )
                        return task
                    except Exception:
                        pass
                _failed_videos[idx].append({"attempt": -attempt, "error": f"content_policy: {e}"})
                return None
            _failed_videos[idx].append({"attempt": -attempt, "error": f"submit: {e}"})
            if attempt < 3:
                await asyncio.sleep(5)

    _failed_videos[idx].append({"attempt": -3, "error": "FATAL: submit failed"})
    return None


async def _process_one_shot(
    shot: dict, shots_dir: Path, image_adapter, video_adapter,
    script_mtime: float | None, force_first_frame: bool,
    script: dict,
    poll_semaphore: asyncio.Semaphore,
    continuity_ref_image: str | None = None,
    mode: str = "A",
) -> asyncio.Task | None:
    """Process single shot: frame gen -> submit video -> return poll task.

    continuity_ref_image: 模式B时传入上一镜视频尾帧路径，跳过首帧生成。
    mode: "A" 关键帧模式 / "B" I2V接续模式。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / SHOT_VIDEO_FILENAME

    # Cache check: prompt_hash.txt + clips 目录双重验证
    shot_hash = _get_shot_hash(shot, script)
    if not force_first_frame and video_path.exists() and video_path.stat().st_size > 20 * 1024:
        hash_path = shot_dir / "prompt_hash.txt"
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            if old_hash == shot_hash:
                from shared.paths import clip_file
                _clip_dst = clip_file(idx)
                if _clip_dst.exists() and _clip_dst.stat().st_size > 20 * 1024:
                    logger.verbose(f"  [Shot {idx}] Cache hit, skip (prompt unchanged + clip exists)")
                    return None
                else:
                    logger.verbose(f"  [Shot {idx}] Clip missing, re-generate video only (skip frames)")
                    ff = shot_dir / FIRST_FRAME_FILENAME
                    lf = shot_dir / LAST_FRAME_FILENAME
                    frame_paths = [str(p) for p in [ff, lf] if p.exists()]
                    # 模式B：用上一镜尾帧替代首帧
                    if mode == "B" and continuity_ref_image:
                        frame_paths = [continuity_ref_image] + frame_paths[1:]
                    return await _submit_video(
                        shot, shot_dir, video_path, frame_paths, shot_hash,
                        video_adapter, poll_semaphore, script, idx,
                        continuity_ref_image=continuity_ref_image, mode=mode,
                    )

    # Frame gen + submit
    if mode == "B" and continuity_ref_image:
        # 模式B：跳过首帧生成，用上一镜视频尾帧作为 I2V 参考图
        # 仅生成尾帧用于本镜风格锚定
        characters = script.get("characters", [])
        frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                       force=force_first_frame, characters=characters,
                                       skip_first_frame=True)
        kf_paths = frames.get("keyframes_paths", [])
        frame_paths = [continuity_ref_image]  # 上一镜尾帧替代 ff_path
        lf = frames.get("lf_path")
        if lf:
            # 检查尾帧幻觉
            lf_check = _check_keyframe_hallucination(lf)
            if lf_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 尾帧检测到 critical 幻觉: {lf_check['issues']}，强制重新生成尾帧")
                # 删除有问题的尾帧，触发重新生成
                Path(lf).unlink(missing_ok=True)
                # 重新生成
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters,
                                               skip_first_frame=True)
                lf = frames.get("lf_path")
            frame_paths.append(lf)
        frame_paths.extend(kf_paths)
    else:
        # 模式A：完整帧生成（首帧+尾帧+关键帧）
        characters = script.get("characters", [])
        frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                       force=force_first_frame, characters=characters)
        kf_paths = frames.get("keyframes_paths", [])

        # 检查首帧幻觉
        ff_path = frames.get("ff_path")
        if ff_path:
            ff_check = _check_keyframe_hallucination(ff_path)
            if ff_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 首帧检测到 critical 幻觉: {ff_check['issues']}，强制重新生成首帧")
                Path(ff_path).unlink(missing_ok=True)
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters)
                ff_path = frames.get("ff_path")

        # 检查尾帧幻觉
        lf_path = frames.get("lf_path")
        if lf_path:
            lf_check = _check_keyframe_hallucination(lf_path)
            if lf_check.get("has_critical"):
                logger.verbose(f"  [镜头{idx}] 尾帧检测到 critical 幻觉: {lf_check['issues']}，强制重新生成尾帧")
                Path(lf_path).unlink(missing_ok=True)
                frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                               force=True, characters=characters)
                lf_path = frames.get("lf_path")

        # B7 修复：关键帧幻觉检查（补上——vimax_bridge 关键帧 QC 已写 sidecar，
        # 此处双保险：关键帧 critical 坏图不进视频）
        kf_paths = frames.get("keyframes_paths", [])
        bad_kf = [p for p in kf_paths if _check_keyframe_hallucination(p).get("has_critical")]
        if bad_kf:
            logger.verbose(f"  [镜头{idx}] 关键帧检测到 critical 幻觉: {len(bad_kf)} 帧，强制重新生成")
            for p in bad_kf:
                Path(p).unlink(missing_ok=True)
            frames = await gen_shot_frames(shot, shots_dir, image_adapter, script_mtime,
                                           force=True, characters=characters)
            kf_paths = frames.get("keyframes_paths", [])

        # 重新获取帧路径（可能已被重新生成）
        kf_paths = frames.get("keyframes_paths", [])
        frame_paths = [p for p in [frames.get("ff_path"), frames.get("lf_path")] if p]
        frame_paths.extend(kf_paths)

    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    return await _submit_video(
        shot, shot_dir, video_path, frame_paths, shot_hash,
        video_adapter, poll_semaphore, script, idx,
        continuity_ref_image=continuity_ref_image, mode=mode,
    )
# 提交限速由 _video_gen provider 内的 KeyScheduler 负责（每 key 独立 61s 冷却，
# 8 key 轮询并行——全局间隔会把多 key 产能压成单 key 水平）。


async def generate_alls(script: dict, working_dir: Path, script_file: Path, force_first_frame: bool = False) -> int:
    """Pipeline: config -> shots (shot 0+1 parallel Mode A, shot 2+ serial Mode B).

    shot 0 (hook) + shot 1: Mode A, parallel submit (no dependency).
    shot >= 2: Mode B, serial — each waits for previous video tail frame.
    """
    from step02_video_gen.audio_pregen import _update_script_with_actual_durations
    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger

    log_dir = chapter_dir()
    logger = get_logger(log_dir / "step02.log", console_level="INFO")

    # 清空上一轮失败记录（同进程多章/重试时防止累计污染汇总计数）
    _failed_videos.clear()

    script_actual_mtime = script_file.stat().st_mtime if script_file.exists() else None

    image_adapter = ImageGeneratorAiVideoAdapter()
    video_adapter = VideoGeneratorAiVideoAdapter()

    # 1. Working dir setup (frames controlled by force_first_frame)
    await build_working_dir(script_file, working_dir, image_adapter,
                            force_first_frame=force_first_frame, skip_frames=False)

    shots = script.get("shots", [])
    shots_dir = working_dir / "shots"
    script_mtime = script_actual_mtime

    actual_concurrency = max(1, min(len(shots), VIDEO_CONCURRENCY))
    poll_semaphore = asyncio.Semaphore(actual_concurrency)

    # 2. Dual-mode scheduling: shot 0+1 parallel Mode A, shot 2+ serial Mode B
    hook = script.get("hook")
    if hook and isinstance(hook, dict) and hook.get("prompt"):
        logger.info(f"\n[INFO] ===== Hook video (index=0) =====")
        hook["index"] = 0
    else:
        hook = None

    tracker = ContinuityTracker(working_dir)

    # ---- Step A: Submit hook (shot 0) and shot 1 in parallel (both Mode A) ----
    mode_a_tasks = []  # (shot, mode, tail_ref) tuples to gather

    # Hook: always Mode A
    if hook:
        tracker.record_mode(0, "A")
        mode_a_tasks.append((hook, "A", None))

    # Shot 1: always Mode A (if present)
    if len(shots) > 0:
        s1 = shots[0]
        tracker.record_mode(s1["index"], "A")
        mode_a_tasks.append((s1, "A", None))

    # Submit all Mode A shots in parallel
    mode_a_polls = []
    for shot, mode, tail_ref in mode_a_tasks:
        idx = shot["index"]
        logger.verbose(f"  [Shot {idx}] Mode A (keyframes, parallel)")
        poll_task = await _process_one_shot(
            shot, shots_dir, image_adapter, video_adapter,
            script_mtime, force_first_frame, script,
            poll_semaphore=poll_semaphore,
            continuity_ref_image=None,
            mode="A",
        )
        if poll_task is not None:
            mode_a_polls.append(poll_task)

    # ---- Step B: Shot 2+ serial chain (Mode B, each waits for previous) ----
    # All Mode A polls are running in background; wait them all, then chain Mode B
    if mode_a_polls:
        await asyncio.gather(*mode_a_polls)

    # Extract tail frames from Mode A results for the chain
    for shot_dict, _, _ in mode_a_tasks:
        idx = shot_dict["index"]
        video_path = shots_dir / str(idx) / SHOT_VIDEO_FILENAME
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            tracker.advance(shot_dict, video_path)

    # Serial chain: remaining shots (index >= 2) are Mode B
    remaining = [s for s in shots if s["index"] >= MODE_B_START_INDEX]
    for shot in remaining:
        idx = shot["index"]
        tracker.record_mode(idx, "B")

        tail_ref = tracker.get_tail_frame()
        if tail_ref:
            logger.verbose(f"  [Shot {idx}] Mode B (I2V, tail={Path(tail_ref).name})")
        else:
            logger.verbose(f"  [Shot {idx}] Mode B (I2V, tail MISSING - fallback)")

        poll_task = await _process_one_shot(
            shot, shots_dir, image_adapter, video_adapter,
            script_mtime, force_first_frame, script,
            poll_semaphore=poll_semaphore,
            continuity_ref_image=tail_ref,
            mode="B",
        )

        if poll_task is not None:
            await poll_task  # Wait for completion before next shot

        # Extract tail frame for next shot in chain
        video_path = shots_dir / str(idx) / SHOT_VIDEO_FILENAME
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            tracker.advance(shot, video_path)

    # Save hook audio duration back
    if hook:
        script["hook"] = hook

    # Build result table
    logger.separator("=", 70)
    logger.info("  shot    mode  status  reason / task_id")
    logger.separator("=", 70)
    # Hook first
    if hook:
        if 0 in _failed_videos:
            errs = _failed_videos[0]
            err = _fmt_err(errs[0].get("error", "unknown"))
            tid = errs[0].get("task_id", "")
            reason = f"{err} | {tid}" if tid else err
            logger.info(f"  {'hook':>5}  A     FAIL    {reason}")
        else:
            hook_video = shots_dir / "0" / SHOT_VIDEO_FILENAME
            if hook_video.exists() and hook_video.stat().st_size > 20 * 1024:
                logger.info(f"  {'hook':>5}  A     OK      -")
            else:
                logger.info(f"  {'hook':>5}  A     SKIP    (not generated)")
    for s in shots:
        sid = s["index"]
        smode = tracker.get_mode(sid) or "?"
        if sid in _failed_videos:
            errs = _failed_videos[sid]
            err = _fmt_err(errs[0].get("error", "unknown"))
            tid = errs[0].get("task_id", "")
            reason = err
            if tid:
                reason += " | " + tid
            logger.info(f"  {sid:>5}  {smode}     FAIL    {reason}")
        else:
            logger.info(f"  {sid:>5}  {smode}     OK      -")
    logger.separator("=", 70)
    total_shots = len(shots) + (1 if hook else 0)
    failed_count = len(_failed_videos)
    ok_count = total_shots - failed_count
    logger.info(f"  total: {total_shots} shots (incl. hook) | OK: {ok_count} | FAIL: {failed_count}")

    # 5. Save
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # 保存连续性元数据（供 step04 merge 查询）
    tracker.save_meta()

    from step02_video_gen.audio_pregen import _update_script_with_actual_durations
    _update_script_with_actual_durations(working_dir, script_file)

    return len(_failed_videos)


# Sync entry
def generate_all(script: dict, working_dir: Path, force_first_frame: bool = False) -> int:
    from shared.paths import script_file as _script_file
    return asyncio.run(generate_alls(script, working_dir, _script_file(), force_first_frame))