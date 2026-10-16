"""shared.media_inputs - 逐镜素材（clip / hook）的**唯一** resolver（计划 §T2、§15）.

背景（2026-10-03 计划 §T2 原文）：「现状并非已共用resolver：Step03只读clip_file；
Step04有目录选择、正文路径选择、hook恒用clip_file(0)等路径」。三处分头解析同一个
「第 idx 镜的素材在哪」，判据必然漂移——于是新增本模块作为**单源**：

- **候选 manifest 优先**：给定了候选上下文就只从它取素材，某镜缺失即显式失败
  （不回退正式目录——那会把「候选实验」与「正式产物」混成一次运行）；
- **生产**：`CLIP_SOURCE=vimax` ⇒ `paths.shot_video_file(idx)`；其它 ⇒
  `paths.flat_clip_file(idx, explicit_root=有效 VIDEO_OUTPUT_DIR 或 clips_dir())`；
- **hook 与正文同规则**：hook 就是 `idx=0`，不再有「hook 恒用 clip_file(0)」的
  特殊路径；缺素材明确失败，不以旧路径兜底。

本模块只做**解析与探测**：不写任何文件、不决定是否重跑（由调用方按自己的判据处理）。
`probe_actual_audio` 用与 step04 相同的 `has_audio_stream` 探测真实音轨，供两端
在**同一批实际文件**上调用同一条 `resolve_audio_policy`。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from shared.paths import chapter_index, clips_dir, flat_clip_file, shot_video_file

#: 生产默认素材来源（与 step04_merge.main / step02b 同名同默认值）
CLIP_SOURCE_VIMAX = "vimax"
CLIP_SOURCE_ENV = "CLIP_SOURCE"
VIDEO_OUTPUT_DIR_ENV = "VIDEO_OUTPUT_DIR"

#: hook 的镜头序号（与 step04 的 `hook_shot["index"] = 0` 同一约定）
HOOK_INDEX = 0


class MediaInputError(Exception):
    """素材解析失败（结构化：code / field_path / idx / 实际探测过的路径）."""

    def __init__(self, code: str, message: str, *, field_path: str = "",
                 idx: int | None = None, path: str | None = None,
                 candidates: Iterable[str] = (), **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path
        self.idx = idx
        self.path = path
        self.candidates = [str(c) for c in candidates]
        self.extra = dict(extra)

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message,
                "field_path": self.field_path, "idx": self.idx, "path": self.path,
                "candidates": list(self.candidates), **self.extra}


# ---------------------------------------------------------------- 配置读数

def source_config_from_env() -> dict:
    """从环境读**生效**的素材来源配置（显式传参可覆盖这两项）."""
    return {
        "clip_source": os.getenv(CLIP_SOURCE_ENV, CLIP_SOURCE_VIMAX).strip() or CLIP_SOURCE_VIMAX,
        "video_output_dir": os.getenv(VIDEO_OUTPUT_DIR_ENV, "").strip(),
    }


def _config_value(source_config, key: str, env_key: str, default: str = "") -> tuple:
    """返回 ``(值, 来源)``：显式配置 > 环境 > 默认。空串按「没给」处理。"""
    if isinstance(source_config, Mapping) and str(source_config.get(key) or "").strip():
        return str(source_config[key]).strip(), "explicit"
    env_value = os.getenv(env_key, "").strip()
    if env_value:
        return env_value, "env"
    return default, "default"


def _chapter_of(scope) -> int | None:
    """从 scope 读数章号：``{"chapter": N}`` / 裸 int / None（沿用当前运行范围）."""
    if scope is None:
        return None
    if isinstance(scope, bool):
        return None
    if isinstance(scope, int):
        return int(scope)
    if isinstance(scope, Mapping):
        raw = scope.get("chapter")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise MediaInputError(
                "invalid_scope_chapter", f"scope.chapter 不是整数: {raw!r}",
                field_path="scope.chapter") from None
    raw = getattr(scope, "chapter", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise MediaInputError(
            "invalid_scope_chapter", f"scope.chapter 不是整数: {raw!r}",
            field_path="scope.chapter") from None


# ---------------------------------------------------------------- 候选素材上下文

@dataclass(frozen=True)
class CandidateClipContext:
    """候选素材上下文：`{镜头序号: 素材路径}`（由候选 manifest 提供）.

    ``clips`` 为空但 ``manifest_path`` 有值 ⇒ 载入时就是空表，属调用方的输入缺陷，
    取任意 idx 都会显式失败（不回退正式目录）。

    ``scope`` / ``script_revision`` / ``source`` 直接取自视频候选 manifest：
    ``shared.candidate_inputs.resolve_candidate_inputs`` 会**交叉核对**声音候选
    manifest 的 scope/revision 与视频侧一致，T 包写自己的 manifest 时要原样继承
    （不许各自另算一份摘要）。
    """

    clips: Mapping[int, Path] = field(default_factory=dict)
    manifest_path: Path | None = None
    manifest_sha256: str | None = None
    experiment_root: Path | None = None
    scope: Mapping = field(default_factory=dict)
    script_revision: str = ""
    source: str = ""
    package: str = ""

    def has(self, idx: int) -> bool:
        return int(idx) in {int(k) for k in self.clips}

    def path_for(self, idx: int) -> Path | None:
        for key, value in self.clips.items():
            if int(key) == int(idx):
                return Path(value)
        return None

    def indexes(self) -> list[int]:
        return sorted(int(k) for k in self.clips)


def load_candidate_clips(manifest_path, *, expect_package: str | None = None) -> CandidateClipContext:
    """读候选 manifest → 上下文。**读不动即失败**（不静默退化成生产解析）.

    接受两种素材登记形态（与 V 包候选 manifest 兼容）：
    ``{"clips": {"0": "…/clip_0.mp4", …}}`` 或
    ``{"units": [{"unit_id","shot_index","path",…}, …]}`` / ``{"artifacts": [{path, role,
    idx}, …]}``。``expect_package`` 给出时必须与 manifest 的 ``package`` 相符
    （``--video-candidates`` 传视频 manifest 时可写 ``"video"``）。
    """
    p = Path(manifest_path)
    if not p.is_file():
        raise MediaInputError("candidate_manifest_missing",
                              f"候选 manifest 不存在: {p}",
                              field_path="candidate_manifest", path=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MediaInputError("candidate_manifest_unreadable",
                              f"候选 manifest 读不动: {p}: {exc}",
                              field_path="candidate_manifest", path=str(p)) from exc
    if not isinstance(data, dict):
        raise MediaInputError("candidate_manifest_shape",
                              f"候选 manifest 顶层不是对象: {type(data).__name__}",
                              field_path="candidate_manifest", path=str(p))
    package = str(data.get("package") or "")
    if expect_package and package != expect_package:
        raise MediaInputError(
            "candidate_manifest_package",
            f"候选 manifest 包名不符: {package!r} != {expect_package!r}",
            field_path="candidate_manifest.package", path=str(p))
    clips: dict[int, Path] = {}
    raw_clips = data.get("clips")
    if isinstance(raw_clips, Mapping):
        for key, value in raw_clips.items():
            try:
                clips[int(key)] = Path(str(value))
            except (TypeError, ValueError):
                raise MediaInputError(
                    "candidate_manifest_shape",
                    f"候选 manifest clips 键不是镜头序号: {key!r}",
                    field_path="candidate_manifest.clips", path=str(p)) from None
    for rec in (data.get("units") or data.get("artifacts") or []):
        if not isinstance(rec, Mapping):
            continue
        role = str(rec.get("role") or "")
        raw_idx = rec.get("idx", rec.get("shot_index"))
        if not rec.get("path") or raw_idx is None:
            continue
        if role and role not in ("clip", "shot_video"):
            continue
        try:
            clips[int(raw_idx)] = Path(str(rec.get("path")))
        except (TypeError, ValueError):
            raise MediaInputError(
                "candidate_manifest_shape",
                f"候选 manifest 产物 idx 非法: {raw_idx!r}",
                field_path="candidate_manifest.artifacts", path=str(p)) from None
    from shared.enhancement_io import sha256_file
    return CandidateClipContext(clips=clips, manifest_path=p,
                                manifest_sha256=sha256_file(p),
                                experiment_root=p.parent,
                                scope=dict(data.get("scope") or {}),
                                script_revision=str(data.get("script_revision") or ""),
                                source=str(data.get("source") or ""),
                                package=package)


# ---------------------------------------------------------------- 解析

@dataclass(frozen=True)
class ClipInput:
    """一次素材解析的结果（路径 + 来源 + 存在性，均按**实际盘上**读数）."""

    idx: int
    path: Path
    source: str            # candidate_manifest | vimax | flat
    exists: bool
    size_bytes: int
    config_source: str = "default"

    @property
    def is_hook(self) -> bool:
        return int(self.idx) == HOOK_INDEX

    def __str__(self) -> str:  # 日志友好
        return f"idx={self.idx} source={self.source} path={self.path} exists={self.exists}"


def resolve_clip_input(idx: int, scope=None, source_config=None,
                       candidate_context: CandidateClipContext | None = None,
                       *, must_exist: bool = False) -> ClipInput:
    """第 ``idx`` 镜（hook=0）的素材 → :class:`ClipInput`.

    - 给了 ``candidate_context`` ⇒ 只认候选 manifest；该 idx 不在表里即
      ``candidate_clip_missing``（**不**回退正式目录）；
    - 否则按生效 ``CLIP_SOURCE`` 解析：vimax ⇒ ``shot_video_file(idx)``，
      其它 ⇒ ``flat_clip_file(idx, explicit_root=VIDEO_OUTPUT_DIR 或 clips_dir())``；
    - ``must_exist=True`` 时素材缺失抛 ``clip_missing``，并在错误里带上
      「解析过的唯一路径」与生效来源配置——不做多路径猜测。
    """
    try:
        idx_i = int(idx)
    except (TypeError, ValueError):
        raise MediaInputError("invalid_clip_index", f"镜头序号不是整数: {idx!r}",
                              field_path="idx") from None
    chapter = _chapter_of(scope)

    if candidate_context is not None:
        path = candidate_context.path_for(idx_i)
        if path is None:
            raise MediaInputError(
                "candidate_clip_missing",
                f"候选 manifest 中没有第 {idx_i} 镜的素材（候选上下文不回退正式目录）"
                f"；已登记: {candidate_context.indexes()}",
                field_path="candidate_manifest.clips", idx=idx_i,
                candidates=[str(candidate_context.manifest_path or "")])
        exists = path.is_file()
        if must_exist and not exists:
            raise MediaInputError(
                "clip_missing", f"候选素材不存在: {path}",
                field_path="candidate_manifest.clips", idx=idx_i, path=str(path))
        return ClipInput(idx_i, path, "candidate_manifest", exists,
                         path.stat().st_size if exists else 0, "candidate")

    clip_source, cfg_source = _config_value(source_config, "clip_source",
                                            CLIP_SOURCE_ENV, CLIP_SOURCE_VIMAX)
    if clip_source == CLIP_SOURCE_VIMAX:
        path = shot_video_file(idx_i, chapter)
        source = "vimax"
    else:
        out_dir, dir_source = _config_value(source_config, "video_output_dir",
                                            VIDEO_OUTPUT_DIR_ENV, "")
        root = Path(out_dir) if out_dir else clips_dir(chapter)
        path = flat_clip_file(idx_i, explicit_root=root, chapter=chapter)
        source = "flat"
        if dir_source == "explicit":
            cfg_source = "explicit"
    exists = path.is_file()
    if must_exist and not exists:
        raise MediaInputError(
            "clip_missing",
            f"第 {idx_i} 镜缺少素材（来源 {source}，CLIP_SOURCE={clip_source}）: {path}",
            field_path="clips", idx=idx_i, path=str(path),
            clip_source=clip_source)
    return ClipInput(idx_i, path, source, exists,
                     path.stat().st_size if exists else 0, cfg_source)


def resolve_clip_inputs(indexes: Iterable[int], scope=None, source_config=None,
                        candidate_context: CandidateClipContext | None = None,
                        *, must_exist: bool = False) -> list[ClipInput]:
    """批量解析（逐条独立失败：第一个缺素材的 idx 就抛，带它的具体下标）."""
    return [resolve_clip_input(i, scope, source_config, candidate_context,
                               must_exist=must_exist) for i in indexes]


def probe_actual_audio(clips: Iterable[ClipInput]) -> bool | None:
    """用与 step04 相同的探测函数核**实际文件**的音轨.

    - 没有任何存在的素材 ⇒ ``None``（未知，交给模型能力判据——与
      ``resolve_audio_policy(actual_audio_present=None)`` 的既有语义一致）；
    - 存在素材但探测不到音轨 ⇒ ``False``（据此可切 tts_mix，避免静音的成片）；
    - 全部都有音轨 ⇒ ``True``。
    """
    from step04_merge.stretch import has_audio_stream
    existing = [c.path for c in clips if c.exists]
    if not existing:
        return None
    return all(bool(has_audio_stream(p)) for p in existing)


def resolve_audio_policy_for(clips: Iterable[ClipInput], *, narration_present: bool = False):
    """在**同一批实际文件**上解析发声来源（两端共用：先生效配置、后实际文件）."""
    from shared.audio_policy import resolve_audio_policy
    return resolve_audio_policy(actual_audio_present=probe_actual_audio(clips),
                                narration_present=narration_present)


__all__ = [
    "CLIP_SOURCE_VIMAX", "CLIP_SOURCE_ENV", "VIDEO_OUTPUT_DIR_ENV", "HOOK_INDEX",
    "MediaInputError", "CandidateClipContext", "ClipInput",
    "source_config_from_env", "load_candidate_clips", "resolve_clip_input",
    "resolve_clip_inputs", "probe_actual_audio", "resolve_audio_policy_for",
]
