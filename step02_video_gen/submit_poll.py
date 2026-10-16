"""step02_video_gen.submit_poll - submit and poll module"""
import sys
from pathlib import Path
import os
import asyncio
import shutil
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
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
from shared.config import VIDEO_CONCURRENCY, VIDEO_MAX_RETRIES  # 并发/重试单源: shared/config.py
from shared.paths import SHOT_VIDEO_FILENAME, FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
from shared.constants import VIDEO_SUBMIT_RETRIES, FRAME_COPY_RETRIES
# V 包（计划 §V1/§V2）：prepared 提交的事实与分类单源
from _video_gen.models import (
    SUBMIT_RATE_LIMITED,
    SUBMIT_REJECTED,
    SUBMIT_UNKNOWN,
    ExecutionContext,
    VideoCapabilityError,
    VideoSubmissionError,
)

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


def _qc_video_product(video_path: Path, shot_idx: int, expected_duration: float,
                      shot: dict | None = None,
                      characters: list | None = None) -> bool:
    """视频产物内容质检（A6 修复 08-11）——**旧接口，方向固定**。

    旧语义（§15「QC证据」）：``True`` = 现有门允许继续（**可能是探测失败软通过**）；
    ``False`` = 明确拒收。判据只有一份：委托 :func:`_qc_video_product_result`。
    需要区分"真测过"与"软通过"时用结构化版本。
    """
    return _qc_video_product_result(video_path, shot_idx, expected_duration,
                                    shot=shot, characters=characters).status != "failed"


@dataclass
class _VideoProductQc:
    """产物 QC 的结构化结果（计划 §15「QC证据」）。

    ``measured_pass`` = 真的测过且通过；``soft_allowed`` = 前置探测失败但按旧合同
    允许继续（**不得**满足 required_check 里"要求真实测量 PASS"的项）；
    ``failed`` = 明确检出问题。
    """

    status: str                      # measured_pass | soft_allowed | failed
    reason: str | None = None
    checks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "checks": list(self.checks)}


def _qc_video_product_result(video_path: Path, shot_idx: int, expected_duration: float,
                             shot: dict | None = None,
                             characters: list | None = None,
                             log_path: Path | None = None) -> _VideoProductQc:
    """视频产物内容质检（结构化版本；判据与旧 bool 版**同一份**）。

    ``log_path`` 非空 ⇒ 日志写该路径（候选模式用它把日志留在 attempt 内，
    不污染正式 ``step02.log``）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    logger = get_logger(log_path or (chapter_dir() / "step02.log"), console_level="INFO")
    checks: list[dict] = []
    try:
        if not video_path.exists() or video_path.stat().st_size <= 20 * 1024:
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：文件缺失或过小")
            return _VideoProductQc("failed", reason="文件缺失或过小",
                                   checks=[{"name": "file_size", "status": "fail"}])
        actual = _probe_video_duration(video_path)
        if actual <= 0:
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长探测失败，跳过时长校验")
            checks.append({"name": "duration", "status": "unmeasured"})
        else:
            exp = expected_duration or 0
            if exp > 0:
                dev_abs = abs(actual - exp)
                dev_ratio = dev_abs / exp if exp else 1.0
                if dev_abs > VIDEO_DURATION_DEV_SEC and dev_ratio > VIDEO_DURATION_DEV_RATIO:
                    logger.warn(
                        f"  [Shot {shot_idx}] 视频 QC FAIL：实际时长 {actual:.1f}s vs "
                        f"期望 {exp:.1f}s（偏差 {dev_abs:.1f}s / {dev_ratio:.0%}）——"
                        f"视频短截/动作没播完，判定坏视频"
                    )
                    return _VideoProductQc(
                        "failed", reason=f"时长偏差 {dev_abs:.1f}s/{dev_ratio:.0%}",
                        checks=checks + [{"name": "duration", "status": "fail",
                                          "actual": actual, "expected": exp}])
            logger.verbose(f"  [Shot {shot_idx}] 视频 QC：时长 {actual:.1f}s 校验通过")
            checks.append({"name": "duration", "status": "pass",
                           "actual": actual, "expected": expected_duration})
        if VIDEO_QC_ENABLED:
            try:
                from step02_video_gen.video_qc import qc_video_file_result
                vis = qc_video_file_result(video_path, shot_idx, shot=shot, characters=characters)
                checks.append({"name": "visual", "status": vis.status,
                               "measured": vis.measured, "reason": vis.reason})
                if vis.status == "critical":
                    logger.warn(f"  [Shot {shot_idx}] 视频 QC 视觉检测发现问题")
                    return _VideoProductQc("failed", reason="视觉 critical", checks=checks)
            except Exception as e:
                logger.verbose(f"  [Shot {shot_idx}] 视频视觉 QC 异常（不阻断）: {e}")
                checks.append({"name": "visual", "status": "unavailable"})
        measured = all(c.get("status") in ("pass",) for c in checks if c.get("name") == "duration")
        return _VideoProductQc("measured_pass" if measured else "soft_allowed",
                               reason=None if measured else "时长未测得（软通过）",
                               checks=checks)
    except Exception as e:
        logger.verbose(f"  [Shot {shot_idx}] 视频 QC 异常（不阻断）: {e}")
        return _VideoProductQc("soft_allowed", reason=f"QC 异常: {e}", checks=checks)


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


def _get_shot_hash(shot: dict, script: dict, shot_dir: Path | None = None) -> str:
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
    from shared.style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT

    # Seedance 系（volcengine / agent_plan）走精简路径，缓存哈希必须跟随
    # （否则旧完整 prompt 生成的视频会被错误复用，refit 错乱）
    # 用 active_video_is_seedance() 统一判断，新增 Seedance 系 provider 自动跟随。
    from shared.model_caps import active_video_is_seedance
    if active_video_is_seedance():
        from step02_video_gen.prompt_builder import _build_seedance_concise_prompt
        _prompt_for_hash = _build_seedance_concise_prompt(shot)
    else:
        _prompt_for_hash = _build_prompt(shot, script)

    def _file_digest(fp: Path) -> str:
        try:
            h = hashlib.sha1()
            with open(fp, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            return f"{fp.name}:{h.hexdigest()}"
        except Exception:
            return f"{fp.name}:unreadable"

    from shared.model_caps import active_video_model
    _resolution = (
        shot.get("resolution")
        or os.getenv("VOLCENGINE_VIDEO_RESOLUTION", "")
        or os.getenv("MINIMAX_VIDEO_RESOLUTION", "")
        or ""
    )
    key_parts = [
        # 供应商/模式/声音开关：这些维度变化都应触发缓存失效
        f"provider={os.getenv('VIDEO_PROVIDER', '')}",
        f"model={active_video_model() or ''}",
        f"ref_mode={os.getenv('VIDEO_REFERENCE_MODE', '')}",
        f"model_voice={os.getenv('VIDEO_MODEL_VOICE', '1')}",
        f"merge_audio={os.getenv('MERGE_AUDIO_MODE', 'native')}",
        f"duration={shot.get('duration_sec') or shot.get('duration') or ''}",
        f"resolution={_resolution}",
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

    # v6.73: actual image bytes are part of the request in image/r2v modes.
    # Text-only hashes reused stale videos when a frame was regenerated with
    # the same prompt but different pixels.
    if shot_dir is not None:
        try:
            candidates = []
            for name in ("first_frame.png", "last_frame.png"):
                fp = Path(shot_dir) / name
                if fp.exists():
                    candidates.append(fp)
            # v6.110：排除二修标注中间产物（`keyframe_<N>_doodle.png`）——
            # 它会被当成「帧」参与哈希，而且 step02b 的 frame_paths 走的是
            # `shared.paths.keyframe_files`（同一口径）。
            from shared.paths import is_core_keyframe_file
            candidates.extend(sorted(p for p in Path(shot_dir).glob("keyframe_*.png")
                                     if is_core_keyframe_file(p)))
            for _fp in candidates:
                key_parts.append(_file_digest(_fp))
        except Exception:
            pass

    # r2v: resolve the actual references now.  `_r2v_refs_cache` is a runtime
    # underscore field and is stripped on save, so relying on it here caused
    # portrait/asset changes to be silently ignored after a restart.
    if os.getenv("VIDEO_REFERENCE_MODE", "").strip().lower() == "r2v":
        try:
            from step02_video_gen.prompt_builder import (
                _collect_r2v_references, _load_character_registry,
            )
            _refs = _collect_r2v_references(
                shot, characters=script.get("characters") or [],
                registry=_load_character_registry(),
            )
            for _ref in _refs:
                _fp = Path(_ref)
                if _fp.exists():
                    key_parts.append(_file_digest(_fp))
        except Exception:
            pass
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


# ---------------------------------------------------------------------------
# 冻结请求快照与缓存判据（计划 §V2 改动 4：命中缓存先核冻结请求关系与产物检查）
# ---------------------------------------------------------------------------

FROZEN_SNAPSHOT_FILENAME = "frozen_request.json"


def frozen_snapshot_path(shot_dir) -> Path:
    return Path(shot_dir) / FROZEN_SNAPSHOT_FILENAME


def read_frozen_snapshot(shot_dir) -> dict | None:
    p = frozen_snapshot_path(shot_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 快照坏了按"关系不可核"处理（不静默命中）
        return {"_unparsable": str(exc)[:200]}
    return data if isinstance(data, dict) else {"_unparsable": "snapshot 不是对象"}


def write_frozen_snapshot(shot_dir, frozen) -> Path | None:
    """把本次实际生效的请求事实落盘（下次命中缓存时按 request_key 核对关系）。"""
    if frozen is None:
        return None
    p = frozen_snapshot_path(shot_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(frozen.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def generation_requirements() -> dict:
    """进**视频生成 key** 的 requirements（只放真正改变模型输入的事实）。

    ``MERGE_AUDIO_MODE`` 只在"对白由视频模型原生发声"的通道上改变 payload/声音
    （§V1：只影响 Step03/04 处理时属于后处理，不进视频生成 key）。判据不可得时
    **保守进 key**（宁可多失效一次，也不静默漏失效）。
    """
    try:
        from shared.model_caps import active_video_uses_native_voice
        native = bool(active_video_uses_native_voice())
    except Exception:  # noqa: BLE001
        native = True
    return ({"audio_policy": os.getenv("MERGE_AUDIO_MODE", "native")} if native else {})


def _chapter_index_safe():
    try:
        from shared.paths import chapter_index
        return chapter_index()
    except Exception:
        return None


def _script_revision(script) -> str:
    try:
        from shared.quality_gate import compute_script_revision
        return str(compute_script_revision(script))
    except Exception:
        return ""


def build_frozen_for_shot(*, shot, script, video_adapter, frame_paths, mode,
                          continuity_ref_image=None, shot_dir=None, script_revision=None,
                          scope=None, provider=None, model_id=None, prompt=None,
                          requirements=None):
    """按**当前输入**组装冻结请求（提交冻结与缓存关系核对共用同一份组装）。"""
    from step02_video_gen import request_plan as rp
    from step02_video_gen.prompt_builder import _build_prompt, _make_request
    from shared.model_caps import active_video_model

    if prompt is None:
        prompt = _build_prompt(shot, script, mode=mode)
    req = _make_request(video_adapter, prompt, frame_paths, shot,
                        characters=script.get("characters", []),
                        continuity_ref_image=continuity_ref_image, mode=mode)
    frozen = rp.build_frozen_request(
        req, shot=shot,
        scope=scope if scope is not None else {"chapter": _chapter_index_safe()},
        script_revision=(script_revision if script_revision is not None
                         else _script_revision(script)),
        provider=provider or str(os.getenv("VIDEO_PROVIDER", "")),
        model_id=model_id or str(active_video_model() or ""),
        requirements=requirements if requirements is not None else generation_requirements())
    return frozen, req


def cache_decision(shot, script, shot_dir, video_path, *, clip_path=None, force=False,
                   frozen_request=None, shot_hash=None) -> dict:
    """缓存命中判据：prompt_hash + 产物检查 + **冻结请求关系**（§V2 改动 4）。

    返回 ``{"hit": bool, "reason": str, "relation": "exact"|"legacy"|"mismatch"|"absent"}``：

    - ``exact``：本次组装的冻结请求与快照 ``request_key`` 一致 ⇒ 同一事实，可复用；
    - ``legacy``：产物+hash 命中但没有（或本次未组装）完整冻结快照 —— 这是**旧证据**，
      允许沿用旧行为，但不得据此宣称新身份已验证；
    - ``mismatch``：模型实际输入已变（hash 或 request_key 不符）⇒ 必须重新生成；
    - ``absent``：产物缺失（clip 缺、视频缺、无 hash）⇒ 走生成/补片。
    """
    shot_dir = Path(shot_dir)
    video_path = Path(video_path)
    if force:
        return {"hit": False, "reason": "force", "relation": "skipped"}
    if not (video_path.exists() and video_path.stat().st_size > 20 * 1024):
        return {"hit": False, "reason": "视频产物缺失或过小", "relation": "absent"}
    hash_path = shot_dir / "prompt_hash.txt"
    if not hash_path.exists():
        return {"hit": False, "reason": "无 prompt_hash（旧稿或首跑）", "relation": "absent"}
    shot_hash = shot_hash if shot_hash is not None else _get_shot_hash(shot, script, shot_dir=shot_dir)
    if hash_path.read_text(encoding="utf-8").strip() != shot_hash:
        return {"hit": False, "reason": "prompt_hash 不符（模型输入已变）",
                "relation": "mismatch"}
    if clip_path is not None:
        clip_path = Path(clip_path)
        if not (clip_path.exists() and clip_path.stat().st_size > 20 * 1024):
            return {"hit": False, "reason": "clip 产物缺失", "relation": "absent"}
    snapshot = read_frozen_snapshot(shot_dir)
    if snapshot is None:
        return {"hit": True, "reason": "hash+产物命中（无冻结快照：legacy 证据）",
                "relation": "legacy"}
    if snapshot.get("_unparsable"):
        return {"hit": False, "reason": f"冻结快照不可读：{snapshot['_unparsable']}",
                "relation": "mismatch"}
    if frozen_request is None:
        return {"hit": True, "reason": "hash+产物命中（快照存在但本次未组装请求）",
                "relation": "legacy"}
    if snapshot.get("request_key") != frozen_request.request_key:
        return {"hit": False, "reason": "冻结请求关系不符（request_key 变化）",
                "relation": "mismatch"}
    return {"hit": True, "reason": "冻结请求关系一致", "relation": "exact"}


def _report_to_dict(report) -> dict:
    return {"media_status": report.media_status, "semantic_status": report.semantic_status,
            "coverage": dict(report.coverage or {}),
            "issues": [{"issue_id": i.issue_id, "kind": i.kind, "time_range": i.time_range,
                        "entity_refs": list(i.entity_refs), "expected": i.expected,
                        "observed": i.observed, "confidence": i.confidence,
                        "required_change": i.required_change,
                        "field_paths": list(i.field_paths),
                        "evidence_hashes": list(i.evidence_hashes)} for i in report.issues],
            "tool_versions": dict(report.tool_versions or {}),
            "source": dict(report.source or {}), "request": dict(report.request or {}),
            "attempt": dict(report.attempt or {})}


def _candidate_attempt(shot_dir: Path, candidate_context):
    return Path(getattr(candidate_context, "attempt_dir", shot_dir))


def _qc_coverage(qc, media_status: str) -> tuple:
    """把产物 QC 的结构化结果翻成（媒体覆盖, 语义覆盖, 语义状态）。

    ``not_applicable`` = 这一轮**没有**要求语义检查（不是"测过且通过"）——
    接受的判据仍要求媒体轴**真测过**（§V4「好片/坏片/工具不可用」三态可分）。
    """
    from step02_video_gen import reroll_decision as rd

    checks = {str(c.get("name")): str(c.get("status")) for c in (qc.checks or [])}
    media_coverage = ["bytes"]
    if checks.get("file_size") in ("pass", "fail"):
        media_coverage.append("file_size")
    if checks.get("duration") in ("pass", "fail"):
        media_coverage.append("duration")
    if "visual" not in checks:
        return media_coverage, [], "not_applicable"
    visual = checks["visual"]
    if visual in ("clean", "pass"):
        return media_coverage, ["three_frames"], rd.STATUS_PASS
    if visual in ("critical", "fail"):
        return media_coverage, ["three_frames"], rd.STATUS_FAIL
    return media_coverage, ["three_frames"], rd.STATUS_UNKNOWN


def _record_candidate_qc(shot_dir: Path, candidate_context, *, qc, shot_idx, shot_hash,
                         frozen_request, video_path: Path) -> str:
    """把产物 QC 结果换成**结构化报告 + 裁决**（§V3：bool 不再控制重生成）。

    返回 ``{"kind", "coverage", "checks", "checks_report", "decision", …}``；
    分支状态与事件在同一处落盘，报告层不猜身份。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va
    from step02_video_gen import reroll_decision as rd

    attempt = _candidate_attempt(shot_dir, candidate_context)
    request_key = getattr(frozen_request, "request_key", "") if frozen_request else ""

    if qc.status == "failed":
        media_status = rd.STATUS_FAIL
        issues = [rd.VideoIssue(
            issue_id=f"shot{shot_idx}-qc", kind="timing_window",
            observed=qc.reason or "产物 QC 未通过",
            required_change="按修订接口回到输入层/原生产者（重生成属新费用，需人工确认）",
            field_paths=["parameters.duration"], evidence_paths=[str(video_path)])]
    elif qc.status == "measured_pass":
        media_status = rd.STATUS_PASS
        issues = []
    else:
        # 前置探测失败 ⇒ **未验证**（不得伪报 PASS，也不因此判生成错误）
        media_status = rd.STATUS_UNKNOWN
        issues = []

    # 语义轴：**没跑**（未启用/未要求）与 **跑了但测不到** 是两件事（§V3 覆盖率）：
    # - 没跑 ⇒ not_applicable（不阻断接受，但 manifest 的 check 记 not_run，不写 pass）；
    # - 跑了但工具不可用/证据不足 ⇒ unknown ⇒ 裁决只能是 unverified。
    media_coverage, semantic_coverage, semantic_status = _qc_coverage(qc, media_status)

    report = rd.VideoCheckReport(
        media_status=media_status, semantic_status=semantic_status,
        coverage={"media": media_coverage, "semantic": semantic_coverage},
        issues=issues, tool_versions={"qc": "step02._qc_video_product_result.v1"},
        request={"request_key": request_key},
        attempt={"attempt_id": attempt.name})
    decision = rd.decide(report, {"request_key": request_key})
    eio.append_event(attempt, "video_check", report=_report_to_dict(report),
                     checks=list(qc.checks or []), qc_status=qc.status)
    eio.append_event(attempt, "decision", decision=decision.to_dict())
    if decision.kind == rd.DECISION_ACCEPT and qc.status != "measured_pass":
        # 语义仍 unknown ⇒ 只能是 unverified（裁决已按 unknown 处理，这里再次核对）
        decision = rd.Decision(rd.DECISION_UNVERIFIED,
                               reasons=["语义轴未验证，不得声明内容 PASS"])
    # 裁决 → 生命周期终局（§V2 表：QC 内容问题=rejected 且**保留产物**；
    # 工具不可用/证据不足=unverified；只有 accept 才进 checked→accepted）
    if decision.kind == rd.DECISION_ACCEPT:
        target = va.STATE_CHECKED
    elif decision.kind in (rd.DECISION_REVISE_INPUTS, rd.DECISION_POSTPROCESS,
                           rd.DECISION_STOP):
        target = va.STATE_REJECTED
    else:
        target = va.STATE_UNVERIFIED
    va.transition(attempt, target, decision=decision.kind, issue_ids=decision.issue_ids)
    return {"kind": decision.kind, "coverage": report.coverage,
            "media_status": report.media_status, "semantic_status": report.semantic_status,
            "checks": list(qc.checks or []),
            "checks_report": [{"name": "media", "status": report.media_status},
                              {"name": "semantic", "status": report.semantic_status}],
            "decision": decision.to_dict()}


def accept_candidate_video(candidate_context, *, video_path: Path, frozen_request,
                           checks: list, clip_copy: Path | None = None,
                           coverage: dict | None = None,
                           tool_versions: dict | None = None,
                           accepted_root: Path | None = None) -> dict:
    """候选合格：不可变 manifest → 锁内换 accepted → **读回完整关系**（§V2/§F3）。

    只在候选上下文内工作；生产 `clips/` 一个字节都不动。返回 accepted 关系；
    提交后读回失败 ⇒ 抛 ``CommitUnconfirmedError``（退出码 6），不声称旧值仍在。

    ``accepted_root`` 默认 = attempt 目录：视频包**一个 task 一个 attempt**，
    指针落在 attempt 内才不会多镜互相覆盖（attempts/ 仍在包 root 下，见 §F2）。
    """
    from shared import enhancement_io as eio

    attempt = Path(getattr(candidate_context, "attempt_dir"))
    ctx_scope = dict(getattr(candidate_context, "scope", None) or {})
    ctx_revision = str(getattr(candidate_context, "script_revision", "") or "")
    ctx_source = str(getattr(candidate_context, "source", "") or "")
    missing = [name for name, value in (("scope", ctx_scope),
                                        ("script_revision", ctx_revision),
                                        ("source", ctx_source)) if not value]
    if missing:
        raise eio.EnhancementError(
            "candidate_identity_missing", "accept",
            f"候选上下文缺少来源身份 {missing}：采纳 manifest 必须能回答"
            "「哪一章、哪一稿、谁产生的」——缺一律拒收，不写空身份、不用 fixture 兜底",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name, field_path="source")

    # 消费方按**稳定编号**取素材（`resolve_candidate_inputs` 不按数组位置猜身份）：
    # 没有 shot_index/shot_id 的 manifest 会让下游直接 `unit_locator_missing`。
    frozen_dict = frozen_request.to_dict() if frozen_request is not None else {}
    fz_source = dict(frozen_dict.get("source") or {})
    shot_index = fz_source.get("shot_index")
    shot_id = fz_source.get("shot_id")
    if shot_index is None and not shot_id:
        raise eio.EnhancementError(
            "candidate_unit_locator_missing", "accept",
            "冻结请求没有 shot_index/shot_id：无法给出稳定的单元编号，拒绝产出"
            "下游读不出身份的候选 manifest",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name, field_path="unit_id")
    dur = (frozen_dict.get("parameters") or {}).get("duration")
    locator = str(shot_id) if shot_id else "shots[%s]" % shot_index
    video_sha = eio.sha256_file(Path(video_path))
    units = [{
        "unit_id": f"video-{locator}",
        "kind": "video",
        "locator": locator,
        "shot_index": shot_index,
        "shot_id": shot_id,
        "path": str(Path(video_path).resolve()),
        "sha256": video_sha,
        "window": ({"start_sec": 0.0, "end_sec": float(dur)} if dur else {}),
        "media": ({"duration_sec": float(dur)} if dur else {}),
    }]

    pointer_root = Path(accepted_root) if accepted_root is not None else attempt
    artifacts = [eio.artifact_record(Path(video_path), role="candidate_video")]
    if clip_copy and Path(clip_copy).is_file():
        artifacts.append(eio.artifact_record(Path(clip_copy), role="candidate_clip_copy"))
    inputs = attempt / eio.INPUT_FILENAME
    if inputs.is_file():
        artifacts.append(eio.artifact_record(inputs, role="frozen_request"))
    input_key = str(getattr(frozen_request, "request_key", "") or "")
    manifest = eio.seal_manifest(attempt, {
        "package": "video",
        "source": ctx_source,
        "scope": ctx_scope,
        "script_revision": ctx_revision,
        "request_key": input_key,
        "units": units,
        "request": frozen_dict,
        "status": "validated",
        "tool_versions": dict(tool_versions or {"qc": "step02._qc_video_product_result.v1"}),
        "artifacts": artifacts,
        "checks": list(checks),
        "coverage": dict(coverage or {}),
        "errors": [],
    })
    accepted = eio.commit_accepted(
        pointer_root, attempt_id=attempt.name,
        manifest_path=attempt / eio.MANIFEST_FILENAME,
        manifest_sha256=manifest["manifest_sha256"], input_key=input_key)
    return {"pointer": accepted, "manifest": manifest,
            "accepted_path": str(eio.accepted_path(pointer_root))}


def attempt_task_id(attempt: Path) -> dict:
    """从事件历史读回任务身份（含**晚到 task_id**，见 ``_create_prepared_task_once``）。

    返回 ``{"task_id", "generation_id", "submit_posts", "classification", "state"}``。
    只读事件（权威历史），不信任内存计数。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va

    attempt = Path(attempt)
    classification = None
    posts = 0
    for e in eio.read_events_strict(attempt)[0]:
        if e.get("kind") == "submit_post":
            posts += 1
            classification = e.get("classification") or classification
    # 身份从**全部**记录过它的事件回溯，并沿可验证父子链找——恢复出来的子尝试把
    # task_id 写在 opened/late_task_id/resume_query，只认 submit_post 会误判成
    # submission_unknown 并退出码 7（2026-10-03 复核实测）。
    found = va.task_id_of_attempt(attempt)
    return {"task_id": found["task_id"],
            "generation_id": found["generation_id"],
            "source_event": found["source_event"], "identity_attempt": found["attempt"],
            "submit_posts": posts, "classification": classification,
            "state": va.current_state(attempt)}


@dataclass
class _ResumeContext:
    """恢复尝试的轻量候选上下文（不重造 CandidateContext，避免路径推导分叉）。"""

    attempt_dir: Path
    package_root: Path
    scope: dict = field(default_factory=dict)
    #: 来源身份随恢复链一起带（§F4）：只带 scope 会让恢复采纳出空 script_revision 的
    #: manifest——下游据此判不了"这是哪一稿"，等于把候选做成不可消费的半成品。
    script_revision: str = ""
    source: str = "resume"

    @property
    def outputs_dir(self) -> Path:
        return self.attempt_dir / "outputs"

    def assert_path(self, path, *, field_path: str = "candidate_path", **kwargs) -> Path:
        from shared.enhancement_io import assert_candidate_path
        return assert_candidate_path(path, experiment_root=self.attempt_dir,
                                     field_path=field_path, **kwargs)


def _request_from_frozen(frozen: dict, clip_index: int):
    """按冻结事实重建请求对象（恢复时**不得**重新组装 prompt/参考）。"""
    from _video_gen.models import VideoGenerationRequest

    params = dict((frozen or {}).get("parameters") or {})
    first = last = None
    keyframes: list = []
    references: list = []
    for ref in ((frozen or {}).get("references") or []):
        path = ref.get("path")
        if not path:
            continue
        role = ref.get("role")
        if role == "first_frame" and first is None:
            first = path
        elif role == "last_frame" and last is None:
            last = path
        elif role == "keyframe":
            keyframes.append(path)
        elif role == "reference":
            references.append(path)
    return VideoGenerationRequest(
        prompt=str((frozen or {}).get("prompt") or ""), clip_index=int(clip_index),
        duration=int(params.get("duration") or 6),
        negative_prompt=str(params.get("negative_prompt") or ""),
        size=str(params.get("size") or "720x1280"),
        image_path=first, last_frame_image_path=last,
        extra_keyframe_paths=keyframes, reference_paths=references,
        seed=params.get("seed"),
        camera_movement=str(params.get("camera_movement") or ""))


def resume_candidate_attempt(attempt: Path, *, service=None, adapter=None,
                             poll_interval: float = 0.5, timeout_sec: float = 60.0) -> dict:
    """恢复**指定已有任务**：查询原 task_id → 下载 → 验收采纳；**不新生成**。

    没有可恢复 task_id（submission_unknown 且无晚到结果）⇒ 抛
    ``EnhancementError(code="submission_unknown", exit_code=7)``——「无 ID 不许新生成」。
    凭据引用不可解析 ⇒ ``credential_binding_unavailable``（退出码 7）。
    """
    import time as _time

    from shared import enhancement_io as eio
    from shared import video_attempts as va

    attempt = Path(attempt)
    inputs = eio.read_inputs(attempt) or {}
    info = attempt_task_id(attempt)
    if not info["task_id"]:
        raise eio.EnhancementError(
            "submission_unknown", "resume",
            "该 attempt 没有可恢复的 task_id（不知是否已建任务）；禁止在没有 ID 的情况下新生成",
            exit_code=eio.EXIT_SUBMISSION_UNKNOWN, attempt_id=attempt.name)

    if service is None:
        service = _video_service_for(adapter) if adapter is not None else None
    if service is None:
        from _video_gen.service import create_service
        service = create_service(str(inputs.get("provider") or "") or None)

    binding = {"status": "ok", "provider": inputs.get("provider"),
               "key_slot": inputs.get("key_slot"),
               "credential_profile_ref": inputs.get("credential_profile_ref"),
               "binding_version": 1}
    if not service.restore_task_binding(info["task_id"], binding):
        raise eio.EnhancementError(
            "credential_binding_unavailable", "resume",
            "该任务提交时的凭据引用不可解析（配置变更/凭据已移除）：不得用当前 key 猜历史任务",
            exit_code=eio.EXIT_SUBMISSION_UNKNOWN, attempt_id=attempt.name,
            request_id=info["task_id"])

    package_root = attempt.parent.parent
    resumed = va.open_recovery_attempt(package_root, attempt)
    if info["task_id"] and va.current_state(resumed) != va.STATE_SUBMITTED:
        # 晚到 task_id（不是 submit_post 事件带来的）⇒ 恢复尝试直接以 submitted 起步，
        # 并**补一条合法事件**再改投影（投影落后可按合法事件补，§F2）
        lock = eio.AttemptLock(resumed / eio.LOCK_DIRNAME, purpose="resume adopt task")
        with lock:
            before_state = va.current_state(resumed)
            event = eio.append_event(resumed, "late_task_id", task_id=info["task_id"],
                                     generation_id=info["generation_id"],
                                     before={"state": before_state},
                                     after={"state": va.STATE_SUBMITTED})
            proj = eio.read_lifecycle(resumed) or {}
            proj.update({"state": va.STATE_SUBMITTED, "task_id": info["task_id"],
                         "projected_from_event_seq": event["seq"]})
            eio.write_lifecycle(resumed, proj)
    ctx = _ResumeContext(attempt_dir=resumed, package_root=package_root,
                         scope=dict(inputs.get("scope") or {}),
                         script_revision=str(inputs.get("script_revision") or ""),
                         source="video_candidate_resume")
    clipped = ctx.outputs_dir / "candidate.mp4"
    clipped.parent.mkdir(parents=True, exist_ok=True)

    frozen = dict(inputs.get("frozen_request") or {})
    if not frozen:
        # 没有冻结请求就没有"本次要什么"的事实：不许用默认值拼一个出来（冻结的
        # 意义就是时长/声音/参考角色等按原样重放）。
        raise eio.EnhancementError(
            "frozen_request_missing", "resume",
            "该 attempt 没有冻结请求（input.json 缺 frozen_request）：无法按原事实重放，"
            "拒绝用默认参数重建请求",
            exit_code=eio.EXIT_REJECTED, attempt_id=attempt.name)
    shot_index = int(inputs.get("shot_index") or (frozen.get("source") or {}).get("shot_index") or 0)
    req = _request_from_frozen(frozen, shot_index)
    from _video_gen.models import ExecutionContext
    req.candidate_path = str(clipped)
    req.execution_context = ExecutionContext(
        experiment_root=str(resumed), attempt_id=resumed.name,
        candidate_path=str(clipped), scope=ctx.scope, package="video")

    if va.current_state(resumed) == va.STATE_SUBMITTED:
        va.transition(resumed, va.STATE_RUNNING, kind="resume_query",
                      task_id=info["task_id"])

    deadline = _time.monotonic() + max(0.0, float(timeout_sec))
    obs = None
    while True:
        obs = service.query_observation(info["task_id"])
        if obs.is_terminal():
            break
        if _time.monotonic() >= deadline:
            break
        _time.sleep(max(0.05, float(poll_interval)))

    if obs is None or not obs.is_success():
        state = (va.STATE_PROVIDER_FAILED if obs is not None and obs.status in ("failed", "expired")
                 else va.STATE_UNVERIFIED)
        va.transition(resumed, state, kind="resume_terminal",
                      status=getattr(obs, "status", "unknown"),
                      error=getattr(obs, "error", None))
        return {"status": "failed", "attempt_dir": str(resumed),
                "task_id": info["task_id"], "observation": obs.to_dict() if obs else None,
                "submit_posts": va.submit_post_count(package_root)}

    remaining = max(1.0, deadline - _time.monotonic())
    result = service.wait_for_completion(req, info["task_id"], timeout=int(remaining))
    if not result.success or not result.save_path:
        va.transition(resumed, va.STATE_IO_FAILED, kind="resume_download_failed",
                      error=str(getattr(result, "error", "download failed"))[:400])
        return {"status": "io_failed", "attempt_dir": str(resumed),
                "task_id": info["task_id"], "error": getattr(result, "error", None),
                "submit_posts": va.submit_post_count(package_root)}

    va.transition(resumed, va.STATE_DOWNLOADED, kind="downloaded", task_id=info["task_id"])
    frozen_obj = None
    try:
        from step02_video_gen import request_plan as rp
        frozen_obj = rp.FrozenVideoRequest(**{
            **frozen, "references": [rp.ReferenceBinding(**r) for r in (frozen.get("references") or [])]})
    except Exception:  # noqa: BLE001 - 冻结快照只用于身份绑定，坏了不阻断恢复
        frozen_obj = None
    _qc = _qc_video_product_result(Path(result.save_path), shot_index,
                                   float(getattr(req, "duration", 0) or 0))
    verdict = _record_candidate_qc(Path(resumed), ctx, qc=_qc, shot_idx=shot_index,
                                   shot_hash="", frozen_request=frozen_obj,
                                   video_path=Path(result.save_path))
    if verdict["kind"] != "accept":
        return {"status": verdict["kind"], "attempt_dir": str(resumed),
                "task_id": info["task_id"], "checks": _qc.checks,
                "media_status": verdict["media_status"],
                "semantic_status": verdict["semantic_status"],
                "submit_posts": va.submit_post_count(package_root)}

    accepted = accept_candidate_video(
        ctx, video_path=Path(result.save_path), frozen_request=frozen_obj,
        checks=(list(_qc.checks or []) + verdict["checks_report"]),
        coverage=verdict["coverage"])
    va.transition(resumed, va.STATE_ACCEPTED, kind="accepted",
                  manifest_sha256=accepted["manifest"]["manifest_sha256"])
    return {"status": "accepted", "attempt_dir": str(resumed), "task_id": info["task_id"],
            "manifest_sha256": accepted["manifest"]["manifest_sha256"],
            "submit_posts": va.submit_post_count(package_root)}


async def _poll_video(
    video_adapter, semaphore: asyncio.Semaphore,
    req, task_id: str, shot_idx: int,
    shot_dir: Path, video_path: Path, max_retries: int,
    shot_hash: str = "",
    qc_resubmit_limit: int = 0,
    shot: dict | None = None,
    characters: list | None = None,
    candidate_context=None,
    frozen_request=None,
) -> bool:
    """Background poll with exponential backoff + jitter - reduces API queries.

    ``candidate_context`` 非空 ⇒ 候选模式（计划 §V2 改动 3/7）：
    - **不**写生产 ``clips/clip_N.mp4``、**不**写生产 shot 目录的 ``prompt_hash.txt``
      （两者都改写到 attempt 内）；
    - 内容 QC **不再自动重交**（旧 ``VIDEO_QC_RESUBMIT`` 仅报告 deprecated_ignored，
      重生成属新费用，需人工确认）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir, execution_log_file
    if candidate_context is not None:
        # 候选模式：日志落 attempt（§F1/§F4——不得把实验日志写进正式 step02.log）
        logger = get_logger(execution_log_file("step02", context=candidate_context),
                            console_level="INFO")
    else:
        logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")
    if candidate_context is not None and qc_resubmit_limit:
        logger.verbose(
            f"  [Shot {shot_idx}] VIDEO_QC_RESUBMIT={qc_resubmit_limit} 在候选模式下 "
            f"deprecated_ignored：候选入口不自动重交（重生成需人工费用确认）")
    qc_resubmit_left = 0 if candidate_context is not None else max(0, qc_resubmit_limit)
    async with semaphore:
        # 获取提交时绑定的 key 索引（计划 §V2：只能走 provider 的**公开**接口；
        # 旧写法读 adapter 私有 `_task_keys`（该属性不存在）被 except 吞掉后恒回退
        # key-1——多 key 通道会用错 key 查不到任务。不可解析时如实标注，不假造。
        _binding: dict = {}
        try:
            _get_binding = getattr(video_adapter, "get_task_binding", None)
            _binding = _get_binding(task_id) if callable(_get_binding) else {}
        except Exception as _exc:
            _binding = {"status": "binding_unavailable", "error": str(_exc)[:120]}
        if isinstance(_binding, dict) and _binding.get("status") == "ok":
            key_idx = int(_binding.get("key_slot") or 0)
            key_display = f"key-{key_idx + 1}"
        else:
            try:
                key_idx = int(video_adapter.get_key_index())
            except Exception:
                key_idx = 0
            key_display = f"key-unbound(current={key_idx + 1})"
            logger.verbose(
                f"  [Shot {shot_idx}] 任务凭据绑定不可解析（binding_unavailable）——"
                f"查询使用当前 key，**不得**据此宣称跨重启可恢复")
        if candidate_context is not None:
            from shared import enhancement_io as _eio
            try:
                _eio.append_event(Path(getattr(candidate_context, "attempt_dir", shot_dir)),
                                  "task_binding", binding=_binding, task_id=task_id)
            except Exception:
                pass

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
                    # 期望时长取**冻结后的实际值**（prepare 可能改过时长）：否则首次
                    # 提交按原请求、恢复按冻结值，同一产物两套裁决（复核 P1-4 之二）
                    _expected_dur = float(
                        (getattr(frozen_request, "parameters", None) or {}).get("duration")
                        or getattr(req, "duration", 0) or 0)
                    _qc = _qc_video_product_result(
                        video_path, shot_idx, _expected_dur, shot=shot, characters=characters,
                        log_path=(execution_log_file("step02", context=candidate_context)
                                  if candidate_context is not None else None))
                    if _qc.status == "failed":
                        # 计划 §V2 改动 3：**删除内容 QC 自动重交动作**。
                        # 重生成属新的付费调用，必须走人工费用确认（候选模式下由
                        # 上层按 decide() 的 revise_inputs 建议处理）。
                        _failed_videos[shot_idx].append({
                            "attempt": -1,
                            "error": f"视频 QC 未通过（{_qc.reason or '内容/时长'}）：不自动重交",
                            "task_id": task_id,
                        })
                        if candidate_context is not None:
                            from shared import video_attempts as _va
                            try:
                                _attempt = _candidate_attempt(shot_dir, candidate_context)
                                _va.transition(_attempt, _va.STATE_DOWNLOADED,
                                               kind="downloaded", task_id=task_id)
                                _verdict = _record_candidate_qc(
                                    shot_dir, candidate_context, qc=_qc, shot_idx=shot_idx,
                                    shot_hash=shot_hash, frozen_request=frozen_request,
                                    video_path=video_path)
                                logger.verbose(
                                    f"  [Shot {shot_idx}] 候选 QC 裁决: {_verdict['kind']}"
                                    f"（保留产物与原错误，未自动重交）")
                            except Exception as _exc:  # noqa: BLE001 - 记录失败不吞：写事件并继续
                                logger.warn(f"  [Shot {shot_idx}] 候选 QC 裁决记录失败: {_exc}")
                            # 候选模式的坏片**保留**（rejected 保留供修订），只删生产哈希
                            return False
                        # 删坏视频 + 旧哈希，触发下次（经确认的）重生成
                        try:
                            video_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        try:
                            (shot_dir / "prompt_hash.txt").unlink(missing_ok=True)
                        except Exception:
                            pass
                        return False

                    if candidate_context is not None:
                        # 候选模式：先记 downloaded（lifecycle 投影），再走裁决 → 采纳事务
                        from shared import video_attempts as _va
                        try:
                            _va.transition(_candidate_attempt(shot_dir, candidate_context),
                                           _va.STATE_DOWNLOADED, kind="downloaded",
                                           task_id=task_id, bytes=video_path.stat().st_size)
                        except Exception as _exc:  # noqa: BLE001
                            logger.warn(f"  [Shot {shot_idx}] lifecycle downloaded 记录失败: {_exc}")

                    # 写入哈希，供下次运行的缓存检测使用（与 _get_shot_hash 同一算法）
                    # 候选模式：shot_dir 已在 attempt 内，绝不写生产 shot 目录。
                    if shot_hash:
                        (shot_dir / "prompt_hash.txt").write_text(shot_hash, encoding="utf-8")
                    if frozen_request is not None:
                        try:
                            write_frozen_snapshot(shot_dir, frozen_request)
                        except OSError as _exc:  # 快照写不了 ⇒ 下次按 legacy 判据，不阻断
                            logger.warn(f"  [Shot {shot_idx}] 冻结请求快照写入失败: {_exc}")

                    if candidate_context is None:
                        # 生产模式：同步复制到 clips/（step04 合并用；缓存检测用）。
                        # 失败必须进入明确出口（计划 §V2 改动 7：不许静默 pass）。
                        from shared.paths import clip_file
                        _clip_dst = clip_file(shot_idx)
                        _clip_dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(str(video_path), str(_clip_dst))
                        except OSError as _exc:
                            _failed_videos[shot_idx].append({
                                "attempt": -1,
                                "error": f"io_failed: clips 同步失败（{_exc}）——"
                                         f"step04 将读不到本镜，需恢复/重放",
                                "task_id": task_id,
                            })
                            logger.warn(
                                f"  [Shot {shot_idx}] clips/ 同步失败（不再静默忽略）: {_exc}")
                            return False
                    else:
                        # 候选模式：副本进 attempt（生产 clips/ 一个字节都不动），
                        # 随后走**不可变 manifest → accepted 读回**的采纳事务。
                        from shared import enhancement_io as _eio
                        from shared import video_attempts as _va
                        _dst = Path(getattr(candidate_context, "outputs_dir",
                                            Path(video_path).parent)) / f"clip_{shot_idx}.mp4"
                        try:
                            _dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(video_path), str(_dst))
                        except OSError as _exc:
                            _eio.append_event(Path(getattr(candidate_context, "attempt_dir", shot_dir)),
                                              "io_failed", error=f"candidate clip copy: {_exc}")
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"io_failed: 候选副本写入失败（{_exc}）",
                                "task_id": task_id, "code": "io_failed",
                            })
                            return False

                        _attempt = _candidate_attempt(shot_dir, candidate_context)
                        _verdict = _record_candidate_qc(
                            shot_dir, candidate_context, qc=_qc, shot_idx=shot_idx,
                            shot_hash=shot_hash, frozen_request=frozen_request,
                            video_path=video_path)
                        if _verdict["kind"] != "accept":
                            # 语义/媒体轴未通过 ⇒ 停在这一层（保留产物与原错误，零重生成）
                            _failed_videos[shot_idx].append({
                                "attempt": -1,
                                "error": f"candidate 未采纳（{_verdict['kind']}）：保留待修订",
                                "task_id": task_id, "decision": _verdict["kind"],
                            })
                            return False
                        try:
                            _accepted = accept_candidate_video(
                                candidate_context, video_path=video_path,
                                frozen_request=frozen_request, clip_copy=_dst,
                                checks=(list(_qc.checks or []) + _verdict["checks_report"]),
                                coverage=_verdict["coverage"])
                            _va.transition(_attempt, _va.STATE_ACCEPTED, kind="accepted",
                                           manifest_sha256=_accepted["manifest"]["manifest_sha256"])
                        except _eio.CommitUnconfirmedError as _exc:
                            # 替换后读回失败：不得声称旧值仍在，也不盲目回滚
                            _va.transition(_attempt, _va.STATE_COMMIT_UNCONFIRMED,
                                           kind="commit_unconfirmed", error=str(_exc)[:500])
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"commit_unconfirmed: {_exc}",
                                "task_id": task_id, "code": "commit_unconfirmed"})
                            return False
                        except Exception as _exc:  # noqa: BLE001 - 验收/提交失败如实返回
                            _failed_videos[shot_idx].append({
                                "attempt": -1, "error": f"accept_failed: {_exc}",
                                "task_id": task_id,
                                "code": getattr(_exc, "code", "") or "candidate_rejected"})
                            return False

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
                    # 2026-09-12 用户要求：终态失败打印完整报错（不走 fmt_err 截断）
                    logger.verbose(f"  [Shot {shot_idx}] [{key_display}] 最终失败完整报错: {err}")
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
                # 2026-09-12 用户要求：终态失败打印完整报错（不走 fmt_err 截断）
                logger.verbose(f"  [Shot {shot_idx}] [{key_display}] 最终失败完整报错: {err_str}")
                return False


async def _submit_video_candidate(*, shot: dict, shot_dir: Path, video_path: Path,
                                  frame_paths: list, shot_hash: str, video_adapter,
                                  poll_semaphore, script: dict, idx: int, prompt: str,
                                  characters: list, continuity_ref_image, mode: str,
                                  candidate_context, logger, make_request):
    """候选模式提交：**一次尝试 = 至多一个生成 POST**（计划 §V1/§V2 改动 1/2/6）。

    与旧路的区别（本函数是候选模式的**唯一**提交入口）：
    - 先 ``provider.prepare_request``（**无提交**）冻结实际生效文本/参数/参考，
      再冻结 FrozenVideoRequest 并做结构预检——预检不过则**零 POST**；
    - 提交走 ``provider.create_prepared_task``：materialize + **一次** POST；
    - 分类结果（不重提交、不在 except 内改 prompt 再发）：
      成功建任务 ⇒ submitted + 轮询；429 ⇒ rate_limited；网络/5xx/超时 ⇒
      submission_unknown（晚到 task_id 由提交线程落盘，恢复时按同一 ID 查询）；
      明确 4xx ⇒ provider_failed；content-policy 只产出**新候选计划**建议；
    - 任何分支都不写生产 ``clips/`` 或生产 ``prompt_hash.txt``。
    """
    from shared import enhancement_io as eio
    from shared import video_attempts as va
    from step02_video_gen import request_plan as rp

    attempt = Path(getattr(candidate_context, "attempt_dir", shot_dir))
    package_root = Path(getattr(candidate_context, "package_root", attempt.parent.parent))
    attempt_id = attempt.name
    scope = dict(getattr(candidate_context, "scope", None) or {})
    script_revision = str(getattr(candidate_context, "script_revision", "") or "")

    def _fail(error: str, *, code: str = "candidate_submit_failed"):
        _failed_videos[idx].append({"attempt": -1, "error": error, "code": code})
        return None

    # §F2「request_id/attempt_id 稳定**不复用**」：终局的 attempt 不许被下一轮覆盖
    # （覆盖会改写既有证据/采纳事实源）。要重新生成请用新的 attempt_id。
    if attempt.exists():
        _state = va.current_state(attempt)
        if _state in va.TERMINAL_STATES:
            eio.append_event(attempt, "attempt_reuse_rejected", state=_state,
                             code="attempt_identity_conflict")
            return _fail(f"attempt_identity_conflict: attempt {attempt_id} 已终局（{_state}），"
                         f"不得复用（请用新的 attempt_id 重新生成）",
                         code="attempt_identity_conflict")

    req = make_request(video_adapter, prompt, frame_paths, shot, characters=characters,
                       continuity_ref_image=continuity_ref_image, mode=mode)
    # 候选落盘路径 + 执行上下文（适配器原样透传请求对象；provider 据此下载进 attempt）
    req.candidate_path = str(video_path)
    req.execution_context = ExecutionContext.from_candidate_context(
        candidate_context, candidate_path=video_path)

    svc = _video_service_for(video_adapter)
    if svc is None or not callable(getattr(svc, "prepare_request", None)) \
            or not svc.supports_prepared():
        va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                        script_revision=script_revision,
                        provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
        eio.append_event(attempt, "prepared_unsupported",
                         reason=f"{type(svc).__name__ if svc else 'no-service'} 未实现 prepared 入口",
                         code="prepared_unsupported")
        return _fail("prepared_unsupported: provider 未实现 prepare/create_prepared_task，"
                     "拒绝降级提交", code="prepared_unsupported")

    # ---- prepare（无提交）：实际生效文本/参数/参考 ----
    try:
        prepared = svc.prepare_request(req, candidate_context)
    except VideoCapabilityError as exc:
        va.open_attempt(package_root, attempt_id, request_key="", scope=scope,
                        script_revision=script_revision,
                        provider=str(os.getenv("VIDEO_PROVIDER", "")), model_id="")
        eio.append_event(attempt, "preflight_blocked", code=exc.code,
                         message=exc.message, field_path=exc.fields.get("field_path"))
        return _fail(f"preflight_blocked({exc.code}): {exc.message}", code=exc.code)
    if not prepared.is_ready():
        eio.append_event(attempt, "preflight_blocked", code="prepared_unsupported",
                         message=prepared.unsupported_reason)
        return _fail(f"prepared_unsupported: {prepared.unsupported_reason}",
                     code="prepared_unsupported")

    # ---- 冻结"真正会发出去的请求"：以 **prepared 的实际事实**为准 ----
    # （只回填 prompt 是不够的：时长/声音开关/参考角色等仍取原请求，会记下与提交
    #   不符的值，而 request_key 照样"相同"——见 2026-10-03 复核 P1-4。）
    frozen = rp.build_frozen_request(
        req, shot=shot, scope=scope, script_revision=script_revision,
        provider=prepared.provider, model_id=prepared.model_id,
        capability_revision=prepared.capability_revision,
        requirements=generation_requirements(), prepared=prepared)
    prepared.request_key = frozen.request_key
    problems = rp.validate_frozen_request(frozen)
    if problems:
        va.open_attempt(package_root, attempt_id, request_key=frozen.request_key, scope=scope,
                        script_revision=script_revision, provider=prepared.provider,
                        model_id=prepared.model_id,
                        credential_profile_ref=prepared.credential_profile_ref)
        eio.append_event(attempt, "preflight_blocked", code="preflight_blocked",
                         problems=problems)
        return _fail(f"preflight: {problems}", code="preflight_blocked")

    gen_id = va.new_generation_id()
    va.open_attempt(package_root, attempt_id, request_key=frozen.request_key, scope=scope,
                    script_revision=script_revision, provider=prepared.provider,
                    model_id=prepared.model_id, generation_id=gen_id,
                    credential_profile_ref=prepared.credential_profile_ref)
    eio.freeze_inputs(attempt, {**(eio.read_inputs(attempt) or {}),
                                "frozen_request": frozen.to_dict(),
                                "prepared": prepared.to_dict(),
                                "shot_index": idx})
    va.transition(attempt, va.STATE_READY)
    eio.append_event(attempt, "prepared", content_key=prepared.content_key,
                     request_key=frozen.request_key,
                     modifications=prepared.modifications,
                     materials=[m.to_dict() for m in prepared.materials])

    # —— 唯一一次生成 POST（prepared 入口；无内部重试、无隐藏重发）——
    va.transition(attempt, va.STATE_SUBMITTING)
    try:
        task_id = await _create_prepared_task_once(svc, prepared, candidate_context,
                                                   attempt=attempt, generation_id=gen_id)
    except VideoCapabilityError as exc:
        # materialize 阶段拒收（素材字节变了/不可读）⇒ 可证未送出，零 POST
        va.transition(attempt, va.STATE_PROVIDER_FAILED, kind="submit_blocked",
                      error=f"{exc.code}: {exc.message}", generation_id=gen_id)
        return _fail(f"submit_blocked({exc.code}): {exc.message}", code=exc.code)
    except VideoSubmissionError as exc:
        state = (va.STATE_SUBMISSION_UNKNOWN
                 if exc.classification == SUBMIT_UNKNOWN else va.STATE_PROVIDER_FAILED)
        va.transition(attempt, state, kind="submit_post", task_id=None,
                      generation_id=gen_id, classification=exc.classification,
                      http_status=exc.http_status)
        if exc.classification == SUBMIT_RATE_LIMITED:
            eio.append_event(attempt, "submit_rate_limited", retry_after=exc.retry_after)
        if exc.http_status in (400, 422) or "content_policy" in str(exc).lower():
            _record_content_policy_revision(attempt, prompt, str(exc), eio)
        # 结构化分类**必须落到失败记录上**：不带 code 会被批处理记成默认
        # `candidate_submit_failed`，CLI 于是返回 3 而不是约定的 7（复核 P2-8）。
        _cls_code = submit_failure_code(exc.classification)
        return _fail(f"submit({exc.classification}): {exc}", code=_cls_code)

    task_id = str(task_id)
    va.transition(attempt, va.STATE_SUBMITTED, kind="submit_post", task_id=task_id,
                  generation_id=gen_id,
                  credential_profile_ref=prepared.credential_profile_ref)
    task = asyncio.create_task(
        _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                    shot_dir, video_path, VIDEO_MAX_RETRIES, shot_hash,
                    qc_resubmit_limit=0, shot=shot, characters=characters,
                    candidate_context=candidate_context, frozen_request=frozen)
    )
    return task


def submit_failure_code(classification: str) -> str:
    """一次生成 POST 的失败分类 → 失败记录的 `code`（批处理据此定退出码）.

    缺了这一步，批处理只能记默认的 `candidate_submit_failed`，`submission_unknown`
    这类需要人工处置的结局会被报成普通工具失败（2026-10-04 复核 P2-8）。
    """
    return {
        SUBMIT_UNKNOWN: "submission_unknown",
        SUBMIT_RATE_LIMITED: "rate_limited",
        SUBMIT_REJECTED: "provider_failed",
    }.get(str(classification or ""), "candidate_submit_failed")


def _record_content_policy_revision(attempt: Path, prompt: str, error: str, eio) -> None:
    """content-policy 修订只产出**新的候选计划**建议（不就地改 prompt 重发）。"""
    try:
        from shared.content_policy_fix import auto_fix_prompt
        fixed = auto_fix_prompt(prompt, error, verbose=False)
    except Exception:
        fixed = None
    if fixed and fixed != prompt:
        eio.append_event(attempt, "proposed_revision", operation="replace_prompt_text",
                         reason="content_policy", original_error=str(error)[:300],
                         replacement_prompt=fixed)


def _video_service_for(video_adapter):
    """解析 provider 服务对象（候选 prepared 入口用）。

    ``shared/vimax_adapters.py`` 属于并行实施线，本包不改它的公开面：优先取公开的
    ``service`` 属性，回退到其固有的 ``_svc`` 字段；两者都没有 ⇒ None（fail-closed，
    候选模式不降级到旧提交路径）。
    """
    svc = getattr(video_adapter, "service", None)
    if svc is None:
        svc = getattr(video_adapter, "_svc", None)
    return svc


def _submit_timeout() -> int:
    return int(os.getenv("VIDEO_SUBMIT_TIMEOUT", os.getenv("AGNES_VIDEO_SUBMIT_TIMEOUT", "180")))


async def _create_prepared_task_once(svc, prepared, candidate_context, *, attempt: Path,
                                     generation_id: str) -> str:
    """调用 provider 的 prepared 提交入口，**至多一个** POST。

    超时/取消**不**证明 HTTP 没发生（计划 §V2「底层线程超时后返回 task_id」）：
    提交在线程池里跑，done 回调把晚到的 task_id 落盘进 attempt 事件
    （``late_task_id``），恢复时按同一 ID 查询——主协程不因取消就断言没建任务。
    """
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                     thread_name_prefix="video-submit")
    cfut = executor.submit(svc.create_prepared_task, prepared, candidate_context)

    def _persist_late(fut) -> None:
        try:
            tid = fut.result()
        except BaseException:  # noqa: BLE001 - 晚到失败照常由主协程分类
            return
        if not tid:
            return
        try:
            import time as _t
            from shared import enhancement_io as eio

            def _already_recorded() -> bool:
                for event in eio.read_events_strict(Path(attempt))[0]:
                    if event.get("kind") == "submit_post" and str(event.get("task_id")) == str(tid):
                        return True
                return False

            # 正常路径下主协程随后就会登记同一 task_id（回调可能先到）：给它一点时间，
            # 只有主协程确实没登记（超时/取消类 unknown）时才写 late_task_id。
            for _ in range(20):
                if _already_recorded():
                    return
                _t.sleep(0.05)
            if _already_recorded():
                return
            lock = eio.AttemptLock(Path(attempt) / eio.LOCK_DIRNAME, purpose="late task id")
            with lock:
                if _already_recorded():
                    return
                eio.append_event(Path(attempt), "late_task_id", task_id=str(tid),
                                 generation_id=generation_id)
        except Exception:  # noqa: BLE001 - 落盘失败不阻断主流程（主协程仍按 unknown 记）
            pass

    cfut.add_done_callback(_persist_late)
    try:
        wrapped = asyncio.wrap_future(cfut)
        return await asyncio.wait_for(wrapped, timeout=_submit_timeout())
    except asyncio.TimeoutError as exc:
        raise VideoSubmissionError(
            SUBMIT_UNKNOWN,
            f"提交在 {_submit_timeout()}s 内未返回（底层线程可能仍在提交，"
            f"晚到 task_id 会落盘 late_task_id；主协程不据此断言未发生）") from exc
    finally:
        executor.shutdown(wait=False)


async def _submit_video(
    shot: dict, shot_dir: Path, video_path: Path, frame_paths: list,
    shot_hash: str, video_adapter, poll_semaphore,
    script: dict, idx: int,
    continuity_ref_image: str | None = None,
    mode: str = "A",
    candidate_context=None,
) -> asyncio.Task | None:
    """提交视频任务并返回轮询 task.

    候选模式（``candidate_context`` 非空，计划 §V2 改动 1/2）：
    - **每次尝试至多一个生成 POST**（单次人工确认范围内），不做重提交循环；
    - 429/拒绝/未知分别归类返回，**不在 except 内直接再 submit**；
    - content-policy 修订只产生"新的候选计划"记录，交上层按费用确认后重走
      prepare，而不是就地改 prompt 再提交。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    from step02_video_gen.prompt_builder import _build_prompt, _make_request

    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")

    prompt = _build_prompt(shot, script, mode=mode)
    characters = script.get("characters", [])

    if candidate_context is not None:
        return await _submit_video_candidate(
            shot=shot, shot_dir=shot_dir, video_path=video_path, frame_paths=frame_paths,
            shot_hash=shot_hash, video_adapter=video_adapter, poll_semaphore=poll_semaphore,
            script=script, idx=idx, prompt=prompt, characters=characters,
            continuity_ref_image=continuity_ref_image, mode=mode,
            candidate_context=candidate_context, logger=logger,
            make_request=_make_request)

    for attempt in range(1, 1 + VIDEO_SUBMIT_RETRIES):
        try:
            req = _make_request(video_adapter, prompt, frame_paths, shot,
                                characters=characters,
                                continuity_ref_image=continuity_ref_image, mode=mode)
            frozen = None
            try:
                from step02_video_gen import request_plan as _rp
                from shared.model_caps import active_video_model as _active_model
                frozen = _rp.build_frozen_request(
                    req, shot=shot,
                    scope={"chapter": _chapter_index_safe()},
                    script_revision=_script_revision(script),
                    provider=str(os.getenv("VIDEO_PROVIDER", "")),
                    model_id=str(_active_model() or ""),
                    requirements=generation_requirements())
            except Exception as _exc:  # noqa: BLE001 - 冻结快照是加固项，失败照样提交但如实告警
                logger.warn(f"  [镜头{idx}] 冻结请求组装失败（提交继续，本次无快照）: {_exc}")
            task_id = await video_adapter.submit_task(req)
            # 2026-09-13 审计修复：轮询连续失败上限走 VIDEO_MAX_RETRIES 配置
            # （此前正常路径硬编码 99、content-policy 路径硬编码 3，配置被架空）
            task = asyncio.create_task(
                _poll_video(video_adapter, poll_semaphore, req, task_id, idx,
                           shot_dir, video_path, VIDEO_MAX_RETRIES, shot_hash,
                           qc_resubmit_limit=VIDEO_QC_RESUBMIT,
                           shot=shot, characters=characters, frozen_request=frozen)
            )
            return task
        except Exception as e:
            err_str = str(e)
            # §V2 故障表「提交超时/连接断开，不知是否建任务」⇒ submission_unknown：
            # **禁止**再次 submit（重发会产生第二个远端任务/重复计费）。
            unknown = (isinstance(e, VideoSubmissionError) and e.classification == SUBMIT_UNKNOWN) \
                or isinstance(e, asyncio.TimeoutError)
            if unknown:
                _failed_videos[idx].append({
                    "attempt": -attempt,
                    "error": f"submission_unknown: {_fmt_err(err_str)}（不重发，需人工确认后按原任务恢复）",
                    "classification": SUBMIT_UNKNOWN,
                })
                logger.warn(
                    f"  [镜头{idx}] 提交结果未知（不知是否已建任务）：不再重发；"
                    f"如需恢复请按已有 task_id 查询/恢复")
                return None
            # provider 已**分类**的拒绝/限流：交上层记录（重发须是新的 prepared 候选 +
            # 新的费用确认），不在这一层再 POST（§V2 改动 1：分类，不靠字符串猜）
            if isinstance(e, VideoSubmissionError) and e.classification in (
                    SUBMIT_REJECTED, SUBMIT_RATE_LIMITED):
                _failed_videos[idx].append({
                    "attempt": -attempt,
                    "error": f"{e.classification}: {_fmt_err(err_str)}（不再重发）",
                    "classification": e.classification,
                    "http_status": e.http_status,
                })
                return None
            # 429 限流（**未分类**的旧式异常）：读服务端 retry-after，等待后重试
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
                                       shot_dir, video_path, VIDEO_MAX_RETRIES, shot_hash,
                                       qc_resubmit_limit=VIDEO_QC_RESUBMIT,
                                       shot=shot, characters=characters)
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
    candidate_context=None,
) -> asyncio.Task | None:
    """Process single shot: frame gen -> submit video -> return poll task.

    continuity_ref_image: 模式B时传入上一镜视频尾帧路径，跳过首帧生成。
    mode: "A" 关键帧模式 / "B" I2V接续模式。
    candidate_context: 非空 ⇒ 候选模式（帧/视频/哈希全部落 attempt，不写正式产物；
    提交走 prepared 入口，一次尝试至多一个生成 POST）。
    """
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    video_path = shot_dir / SHOT_VIDEO_FILENAME

    # Cache check（§V2 改动 4）：prompt_hash + 产物检查 + **冻结请求关系**
    #   - exact：本次组装出的冻结请求与快照 request_key 一致 ⇒ 同一事实，复用；
    #   - legacy：产物+hash 命中但没有完整快照（旧稿）⇒ 沿用旧行为，不得宣称新身份已验证；
    #   - mismatch：模型实际输入已变 ⇒ 重新生成。
    from shared.paths import clip_file
    _clip_dst = None if candidate_context is not None else clip_file(idx)
    shot_hash = _get_shot_hash(shot, script, shot_dir=shot_dir)
    _cache = cache_decision(shot, script, shot_dir, video_path, clip_path=_clip_dst,
                            force=force_first_frame, shot_hash=shot_hash)
    if _cache["hit"]:
        logger.verbose(f"  [Shot {idx}] Cache hit({_cache['relation']}), skip: {_cache['reason']}")
        return None
    if _cache["reason"] == "clip 产物缺失" and video_path.exists():
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
            candidate_context=candidate_context,
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
        candidate_context=candidate_context,
    )
# 提交限速由 _video_gen provider 内的 KeyScheduler 负责（每 key 独立 61s 冷却，
# 8 key 轮询并行——全局间隔会把多 key 产能压成单 key 水平）。


async def generate_alls(script: dict, working_dir: Path, script_file: Path,
                        force_first_frame: bool = False, candidate_context=None) -> int:
    """Pipeline: config -> shots (shot 0+1 parallel Mode A, shot 2+ serial Mode B).

    shot 0 (hook) + shot 1: Mode A, parallel submit (no dependency).
    shot >= 2: Mode B, serial — each waits for previous video tail frame.

    ``candidate_context`` 非空 ⇒ 候选模式：帧/视频/哈希全部落 attempt，提交走
    prepared 入口（一次尝试至多一个生成 POST），**不**回写正式 script。
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
            candidate_context=candidate_context,
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
            candidate_context=candidate_context,
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