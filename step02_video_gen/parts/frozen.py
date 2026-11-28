# -*- coding: utf-8 -*-
"""
from __future__ import annotations
S07: `step02_video_gen.submit_poll` 的 frozen 职责（2026-11-28 拆分）.

父模块保留提交/轮询主编排与运行状态；本模块在调用点读取父模块当前值，
导入期不回导父模块，保证父模块上的 monkeypatch 仍然生效。
"""
from pathlib import Path
import os
import json
from dataclasses import dataclass
import hashlib

_PARENT = "step02_video_gen.submit_poll"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


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

    2026-11-17（S02，用户复核）：判据从**provider 级**改为
    `provider 级原生发声 且 **模型级确实有声**`。

    为什么必须加后半个条件：`active_video_uses_native_voice()` 只按 provider 分类
    （volcengine → True），而 `volcengine` 下 `doubao-seedance-1-0-pro-*` 是**无声**
    模型（`supports_generate_audio=False`）。旧判据于是把
    `{"audio_policy": "native"}` 写进冻结请求——而 `requirements` 整体进
    `request_key`（`request_plan.key_facts()`），结果是：**payload 一字未变，仅切换
    `MERGE_AUDIO_MODE` 就会让视频缓存 key 失效**，白白重生成。
    无声模型没有原生音轨可混，`MERGE_AUDIO_MODE` 对它不可能改变任何输入，
    因此不得进 key。
    """
    try:
        from shared.model_caps import (active_video_supports_audio,
                                       active_video_uses_native_voice)
        native = bool(active_video_uses_native_voice()) and bool(
            active_video_supports_audio())
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

def _scope_identity_safe() -> dict:
    """当前运行范围身份（chapter/book_index/book_name）；失败时退回章号。"""
    try:
        from shared.run_scope import current_scope_identity
        return dict(current_scope_identity())
    except Exception:  # noqa: BLE001 - 路径未初始化时仍给最小身份
        return {"chapter": _chapter_index_safe()}

def build_frozen_for_shot(*, shot, script, video_adapter, frame_paths, mode,
                          continuity_ref_image=None, shot_dir=None, script_revision=None,
                          scope=None, provider=None, model_id=None, prompt=None,
                          requirements=None, prepared=None,
                          execution_requirements=None, ref_sheet_dir=None,
                          capability_revision=None, media_policy=None):
    """按**当前输入**组装冻结请求（提交冻结与缓存关系核对共用同一份组装）。

    ``ref_sheet_dir`` 是 r2v 超限参考表的落盘目录（attempt / 临时目录）；不给时
    由 `_make_request` 使用系统临时目录，**绝不**写生产 ``shots/``（F2）。

    2026-11-17 S02-C1：``prepared`` 给定时，``provider/model_id/capability_revision``
    也取 prepared 的事实——提交路径就是这样冻结的；此前只覆盖 prompt/参数/素材，
    会让 plan-only 与提交的 ``request_key`` 差一个 ``capability_revision``（非空 hash）。
    """
    from step02_video_gen import request_plan as rp
    from step02_video_gen.prompt_builder import _build_prompt, _make_request
    from shared.model_caps import active_video_model

    if prompt is None:
        prompt = _build_prompt(shot, script, mode=mode)
    req = _make_request(video_adapter, prompt, frame_paths, shot,
                        characters=script.get("characters", []),
                        continuity_ref_image=continuity_ref_image, mode=mode,
                        retry_attempt=_read_qc_retry_state(shot_dir)["count"] if shot_dir is not None else 0,
                        media_policy=media_policy,
                        **({"ref_sheet_dir": ref_sheet_dir} if ref_sheet_dir is not None else {}))
    if prepared is not None:
        provider = str(getattr(prepared, "provider", "") or provider or "")
        model_id = str(getattr(prepared, "model_id", "") or model_id or "")
        capability_revision = (getattr(prepared, "capability_revision", None)
                               or capability_revision)
    frozen = rp.build_frozen_request(
        req, shot=shot,
        scope=scope if scope is not None else _scope_identity_safe(),
        script_revision=(script_revision if script_revision is not None
                         else _script_revision(script)),
        provider=provider or str(os.getenv("VIDEO_PROVIDER", "")),
        model_id=model_id or str(active_video_model() or ""),
        requirements=requirements if requirements is not None else generation_requirements(),
        prepared=prepared, capability_revision=capability_revision)
    if execution_requirements is not None:
        from step02_video_gen.execution_requirements import merge_into_frozen
        merge_into_frozen(frozen, execution_requirements)
    return frozen, req

def _prepare_error(exc, *, stage: str) -> dict:
    fields = getattr(exc, "fields", None)
    field_path = fields.get("field_path") if isinstance(fields, dict) else None
    return {
        "code": str(getattr(exc, "code", "") or type(exc).__name__),
        "message": str(getattr(exc, "message", "") or exc)[:500],
        "field_path": field_path,
        "stage": stage,
    }

def _service_supports_prepared(svc) -> bool:
    if svc is None or not callable(getattr(svc, "prepare_request", None)):
        return False
    fn = getattr(svc, "supports_prepared", None)
    if not callable(fn):
        return True
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001 - 能力查询失败按不支持处理（fail-closed）
        return False

@dataclass
class PreparedFacts:
    """一次准备的结构化结果（缓存关系核对与提交共用；禁止二次 prepare）。

    - ``prepared``：provider 实际生效的请求事实已冻结；
    - ``unsupported``：provider 未实现 prepared 入口（旧兼容分支）；
    - ``blocked``：请求组装或 prepare 确定失败（必须单次终局，不再准备）。
    """

    status: str
    req: object | None = None
    prepared: object | None = None
    frozen: object | None = None
    error: dict | None = None
def build_prepared_facts(*, shot, script, video_adapter, frame_paths, mode,
                         scope=None, script_revision=None, provider=None,
                         model_id=None, execution_requirements=None,
                         candidate_context=None, ref_sheet_dir=None,
                         media_policy=None) -> PreparedFacts:
    """组装 + prepare 一次，返回结构化事实；失败不再重试（S02-C1）。

    与提交路径同源：``provider/model_id/capability_revision`` 取 prepared；执行要求
    并入 frozen 后重算 key。缓存消费者与提交入口共用本函数的结果，避免
    「缓存里 prepare 失败、提交里再 prepare 成功」的分叉。
    """
    from shared.model_caps import active_video_model

    _scope = dict(scope) if scope is not None else _scope_identity_safe()
    _revision = (script_revision if script_revision is not None
                 else _script_revision(script))
    _provider = str(provider or os.getenv("VIDEO_PROVIDER", "") or "")
    _model = str(model_id or active_video_model() or "")

    # 1) 组装请求（一次）
    try:
        _frozen0, req = build_frozen_for_shot(
            shot=shot, script=script, video_adapter=video_adapter,
            frame_paths=frame_paths, mode=mode, scope=_scope,
            script_revision=_revision, provider=_provider, model_id=_model,
            requirements=generation_requirements(),
            ref_sheet_dir=ref_sheet_dir,
            media_policy=media_policy)
    except Exception as exc:  # noqa: BLE001 - 组装失败是确定阻断，结构化返回
        return PreparedFacts(status="blocked", error=_prepare_error(exc, stage="request_assembly"))

    # 2) provider prepared 能力
    svc = _pc("_video_service_for", video_adapter)
    if not _service_supports_prepared(svc):
        return PreparedFacts(
            status="unsupported", req=req,
            error={"code": "prepared_unsupported",
                   "message": f"{type(svc).__name__ if svc else 'no-service'} 未实现 prepared 入口",
                   "field_path": None, "stage": "prepared_capability"})

    # 3) prepare（无提交）；失败即终局，不在这里重试
    try:
        prepared = svc.prepare_request(req, candidate_context)
    except Exception as exc:  # noqa: BLE001 - 确定失败：结构化返回，交统一落盘
        return PreparedFacts(status="blocked", req=req,
                             error=_prepare_error(exc, stage="prepare"))
    if not prepared.is_ready():
        return PreparedFacts(
            status="blocked", req=req, prepared=prepared,
            error={"code": "prepared_not_ready",
                   "message": str(getattr(prepared, "unsupported_reason", "")
                                  or "prepared_not_ready")[:500],
                   "field_path": None, "stage": "prepare"})

    # 4) 以 prepared 为事实源冻结，再并入执行要求
    try:
        frozen, _ = build_frozen_for_shot(
            shot=shot, script=script, video_adapter=video_adapter,
            frame_paths=frame_paths, mode=mode, scope=_scope,
            script_revision=_revision, provider=_provider, model_id=_model,
            requirements=generation_requirements(), prepared=prepared,
            execution_requirements=execution_requirements,
            ref_sheet_dir=ref_sheet_dir,
            media_policy=media_policy)
    except Exception as exc:  # noqa: BLE001
        return PreparedFacts(status="blocked", req=req, prepared=prepared,
                             error=_prepare_error(exc, stage="frozen_assembly"))
    return PreparedFacts(status="prepared", req=req, prepared=prepared, frozen=frozen)

def record_submitted_duration(shot: dict, prepared) -> None:
    """S08：把**实际提交**时长回填到 shot；计划装不下时阻断，不静默缩短故事时长."""
    from _video_gen.models import VideoCapabilityError

    params = getattr(prepared, "parameters", None) or {}
    actual = params.get("duration")
    if actual is None:
        return
    try:
        actual_i = int(actual)
    except (TypeError, ValueError):
        return
    planned = shot.get("planned_duration_sec")
    if planned is None:
        planned = shot.get("duration_sec") or shot.get("duration")
    try:
        import math as _math
        planned_i = int(_math.ceil(float(planned)))
    except (TypeError, ValueError):
        planned_i = 0
    if planned_i and actual_i < planned_i:
        raise VideoCapabilityError(
            "duration_insufficient",
            f"实际提交时长 {actual_i}s 小于计划 {planned_i}s（模型区间/环境覆盖夹取），"
            "禁止静默缩短故事时长",
            field_path="parameters.duration")
    shot["submitted_duration_sec"] = actual_i
    if planned_i and actual_i > planned_i:
        shot.setdefault("_duration_modifications", []).append(
            {"kind": "duration_extended", "planned": planned_i, "submitted": actual_i})

def cache_decision(shot, script, shot_dir, video_path, *, clip_path=None, force=False,
                   frozen_request=None, shot_hash=None, require_exact=False) -> dict:
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
    shot_hash = shot_hash if shot_hash is not None else _pc("_get_shot_hash", shot, script, shot_dir=shot_dir)
    if hash_path.read_text(encoding="utf-8").strip() != shot_hash:
        return {"hit": False, "reason": "prompt_hash 不符（模型输入已变）",
                "relation": "mismatch"}
    if clip_path is not None:
        clip_path = Path(clip_path)
        if not (clip_path.exists() and clip_path.stat().st_size > 20 * 1024):
            return {"hit": False, "reason": "clip 产物缺失", "relation": "absent"}
    snapshot = read_frozen_snapshot(shot_dir)
    # S01：非 auto 策略必须证明"旧产物对应当前实际素材模式"，legacy 不算命中。
    if require_exact and (snapshot is None or frozen_request is None):
        return {"hit": False, "reason": "策略要求精确冻结关系（无快照/未组装请求）",
                "relation": "legacy"}
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

def _read_qc_retry_state(shot_dir: Path) -> dict:
    """旧整数文件仍可读；损坏、空白及不可读状态必须显式失败。"""
    path = Path(shot_dir) / "qc_fail_count.txt"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"count": 0, "task_id": ""}
    if type(value) is int:
        value = {"count": value, "task_id": ""}
    if (not isinstance(value, dict) or set(value) != {"count", "task_id"}
            or type(value["count"]) is not int or value["count"] < 0
            or not isinstance(value["task_id"], str)):
        raise ValueError(f"QC 重抽状态不合法: {path}")
    return value

def _record_qc_failure(shot_dir: Path, task_id: str) -> None:
    """同一失败任务恢复幂等；先原子保存下一次 attempt，再允许清理坏片。"""
    from shared.json_io import atomic_dump_json
    state = _read_qc_retry_state(shot_dir)
    if task_id and state["task_id"] == str(task_id):
        return
    atomic_dump_json(Path(shot_dir) / "qc_fail_count.txt",
                     {"count": state["count"] + 1, "task_id": str(task_id)})

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

def _get_shot_hash(shot: dict, script: dict, shot_dir: Path | None = None, *,
                   media_policy: str | None = None) -> str:
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
    from shared.style_prefix import frame_canvas_ratio, style_profile

    _style = style_profile(ratio=frame_canvas_ratio())

    # Seedance 系（volcengine / agent_plan）走精简路径，缓存哈希必须跟随
    # （否则旧完整 prompt 生成的视频会被错误复用，refit 错乱）
    # 用 active_video_is_seedance() 统一判断，新增 Seedance 系 provider 自动跟随。
    from shared.model_caps import active_video_is_seedance
    if active_video_is_seedance():
        from step02_video_gen.prompt_builder import _build_seedance_concise_prompt
        _chars = script.get("characters", []) if isinstance(script, dict) else []
        _prompt_for_hash = _build_seedance_concise_prompt(shot, characters=_chars)
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
    from shared.video_media_plan import active_media_policy
    _policy = media_policy or active_media_policy()
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
        # S02-C1（2026-11-17 用户回放）：MERGE_AUDIO_MODE 只影响后处理/声音合同，
        # **不改变视频模型输入**。有声模型的失效由 request_key 里的 audio_policy /
        # effective_merge_mode 承担；无声模型不得因它抖动，否则 key 稳定也会在
        # prompt_hash 这一层被判 mismatch。
        f"duration={shot.get('duration_sec') or shot.get('duration') or ''}",
        f"resolution={_resolution}",
        # 与 _make_request 实际发送内容同源：画风前缀（含 9:16 画幅）+ 当前画风负向词
        _style.prefix,
        _style.video_negative,
        _prompt_for_hash,
        shot.get("narration_segment", ""),
        # v4: 图片 prompt 变了帧就变了，I2V 参考图变了视频也应重做
        _normalize(shot.get("ff_desc", "")),
        _normalize(shot.get("lf_desc", "")),
    ]
    # S01：策略只在非 auto 时进入 hash——auto 保持旧 hash（避免全量重生成），
    # 切换策略必然换 hash，旧视频不会因"素材文件没变"被复用。
    if _policy != "auto":
        key_parts.append(f"media_policy={_policy}")
    # keyframes 数据也纳入哈希（添加/移除/修改关键帧都会触发重做）
    kfs = shot.get("keyframes") or shot.get("intermediate_keyframes") or []
    if kfs:
        for kf in kfs:
            if isinstance(kf, dict):
                key_parts.append(_normalize(kf.get("desc", "")))
                _kf_time = kf.get("t_sec") if kf.get("t_sec") is not None else kf.get("time_ratio", "")
                key_parts.append(str(_kf_time))

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
            # 2026-11-18 O1：只哈希策略后实际会下发的帧；被 first_only/first_last
            # 丢弃的 lf/kf 变化不得让可复用视频失效。
            from shared.video_media_plan import filter_frame_paths_by_policy
            try:
                candidates = [Path(p) for p in filter_frame_paths_by_policy(
                    [str(p) for p in candidates], _policy)]
            except Exception as _degrade_exc:  # noqa: BLE001 - 过滤不可用时保持旧行为
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("step02_video_gen/submit_poll.py:_get_shot_hash", _degrade_exc)
            for _fp in candidates:
                key_parts.append(_file_digest(_fp))
        except Exception:
            pass

    # r2v: resolve the actual references now.  `_r2v_refs_cache` is a runtime
    # underscore field and is stripped on save, so relying on it here caused
    # portrait/asset changes to be silently ignored after a restart.
    # 2026-11-13（F2）：哈希是**纯读**路径——`materialize=False` 只收集源参考，
    # 不拼参考表、不落盘；此前这里会经 `fit_refs` 往生产 `shots/` 写 ref_sheet。
    if os.getenv("VIDEO_REFERENCE_MODE", "").strip().lower() == "r2v":
        from step02_video_gen.prompt_builder import (
            _collect_r2v_references, _load_character_registry,
        )
        try:
            _refs = _collect_r2v_references(
                shot, characters=script.get("characters") or [],
                registry=_load_character_registry(), materialize=False,
            )
            for _ref in _refs:
                _fp = Path(_ref)
                if _fp.exists():
                    key_parts.append(_file_digest(_fp))
        except Exception as _degrade_exc:
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step02_video_gen/submit_poll.py:_get_shot_hash", _degrade_exc)
    key = "|".join(key_parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


FROZEN_SNAPSHOT_FILENAME = "frozen_request.json"
