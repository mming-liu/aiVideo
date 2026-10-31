"""tts.synthesis_job - 请求规范化、无损分段、结果转换与候选事务（计划 §T1/§T2/§T2a）.

本模块是 T 包**唯一**的合成实现：同步/异步对白与旁白都走 :func:`synthesize_unit`，
不再各写一份「切分 + 拼接 + 落盘」的逻辑。

四条硬约束（逐条对应计划原文）：

1. **无损分段**（§T2）：移除「300 字截断/硬失败」。超 provider 单次上限时按正文
   位置分段，**拼回必须逐字符等于完整 text**——切点只落在标点上，不删字、不改台词、
   不压缩故事；每段记录 code point 起止。任一段失败 ⇒ 整 unit 不提交。
2. **PCM 执行侧**（§15）：解码 / 统一采样格式 / 拼接 / 消费格式编码**全部**用宿主
   `subprocess` 调 FFmpeg；不 import 任何音频库、不 import torch、不委托 whisperx
   worker。记录每段的输入与全部采样数。
3. **不假装参数生效**（§T1）：`instruction`/`rate`/`volume` 由 provider 能力事实判定；
   新路径（`params_strict=True`）不支持即**拒收**，旧请求兼容路径按事实记
   `*_applied=False`，且绝不把 instruction 拼进发声文本。
4. **时长守卫在统一 unit 层**（§T2a）：窗口不够 ⇒ :class:`DurationRejectedError`
   （code=`duration_rejected`），不截音、不暗自加速、**不因拆段宣布时间问题已解决**。

候选模式下产物落 `<candidate_root>/attempts/<attempt_id>/`，事务全部委托
`shared.enhancement_io`（锁 / 事件 / 不可变 manifest / accepted 指针 / 读回确认）。
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from shared import enhancement_io as eio
from shared.audio_identity import (IDENTITY_SCHEMA_V2, build_identity, identity_path,
                                   read_identity, sha256_file, sha256_text,
                                   verify_identity, write_identity,
                                   write_legacy_signature)
from shared.json_io import commit_json_swap, prepare_json_swap
from shared.media_inputs import CandidateClipContext
from tts.models import (KIND_DIALOGUE, KIND_NARRATION, LEGAL_KINDS, SpeechUnit,
                        TTSSuccessResult)
from tts.service import create_provider, provider_capabilities

#: 未提供修订号时的**显式**占位（不伪造出处：它就是说「本次没有版本信息」）
REVISION_UNVERSIONED = "unversioned"
#: 中间 PCM 的统一格式（s16le：宿主 FFmpeg 的通用落点，不需要任何音频库）
PCM_CODEC = "pcm_s16le"
PCM_BYTES_PER_SAMPLE = 2


# ---------------------------------------------------------------- 错误

class SynthesisError(Exception):
    """T 包合成失败基类（带 code / field_path / 结构化载荷）."""

    def __init__(self, code: str, message: str, *, field_path: str = "",
                 unit_id: str = "", stage: str = "synthesis", **fields):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path
        self.unit_id = unit_id
        self.stage = stage
        self.fields = dict(fields)

    def payload(self) -> dict:
        return {"code": self.code, "stage": self.stage, "unit_id": self.unit_id,
                "field_path": self.field_path, "message": self.message, **self.fields}


class DurationRejectedError(SynthesisError, RuntimeError):
    """窗口讲不完（§T2a）——**不截音、不加速**，把问题交回生产者.

    继承 ``RuntimeError``：既有消费点（step03 串行/异步、测试）按 RuntimeError 捕获。
    """

    def __init__(self, message: str, **fields):
        SynthesisError.__init__(self, "duration_rejected", message,
                                stage="duration_gate", **fields)
        RuntimeError.__init__(self, message)


class SegmentationError(SynthesisError):
    def __init__(self, message: str, **fields):
        super().__init__("segmentation_failed", message, stage="segment", **fields)


class ProviderCallError(SynthesisError):
    def __init__(self, message: str, *, code: str = "provider_failed", **fields):
        super().__init__(code, message, stage="provider", **fields)


class ToolUnavailableError(SynthesisError):
    def __init__(self, message: str, **fields):
        super().__init__("tool_unavailable", message, stage="pcm_pipeline", **fields)


# ---------------------------------------------------------------- 就地产物替换

def partial_path(target) -> Path:
    """同目录的**临时落点**（`<stem>.partial<pid><后缀>`；同卷才能原子替换）.

    后缀必须留着：FFmpeg 按后缀选封装器，去掉后缀会直接 mux 失败。
    """
    p = Path(target)
    return p.with_name("%s.partial%d%s" % (p.stem, os.getpid(), p.suffix))


def finalize_in_place(artifact: AudioArtifact, target) -> AudioArtifact:
    """两阶段替换：已生成成功的产物**原子换到** `target`（旧文件只在成功后被替换）.

    计划 §T2：「不先删旧缓存再生成」——生成失败/时长拒收时盘上的旧音轨必须**保持原样**，
    所以先在临时落点合成、成功后 `os.replace` 过去；身份侧车与其 `artifact_path`
    一起跟着换（否则侧车会指向临时路径）。
    """
    target = Path(target)
    src = Path(artifact.path)
    if src.resolve() == target.resolve():
        return artifact
    tmp_side = identity_path(src)
    legacy_side = src.with_suffix(".voice")
    ident = read_identity(src)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, target)                      # 同卷原子替换
    if ident is not None:
        ident["artifact_path"] = str(target.resolve())
        write_identity(target, ident)
        try:
            tmp_side.unlink(missing_ok=True)
        except OSError:
            pass
    if legacy_side.is_file():
        try:
            os.replace(legacy_side, target.with_suffix(".voice"))
        except OSError:
            pass
    artifact.path = target
    artifact.identity = ident or artifact.identity
    return artifact


# ---------------------------------------------------------------- 分段

@dataclass(frozen=True)
class Segment:
    """一段正文：``text`` + 它在大正文里的 **code point 半开区间** ``[start, end)``."""

    index: int
    text: str
    start: int
    end: int

    @property
    def n_chars(self) -> int:
        return self.end - self.start


#: 首选切点（句末优先，其次子句，最后空白）——**只是切点，不删字**
_SENT_END = "。！？…；!?~"
_SUB_END = "，、,;：:"
_WHITESPACE = " \t\n　"


def segment_text(text: str, cap: Optional[int]) -> list[Segment]:
    """把 ``text`` 按 ``cap``（provider 单次上限）分段——**无损**：拼回逐字符相等.

    - ``cap`` 为 None/<=0 ⇒ 不分段（整段一次）；
    - 切点**只**落在标点/空白之后（找不到就按 cap 硬切），切点字符**留在前一段**；
    - 不 strip、不合并空白：每一段的 ``[start, end)`` 精确覆盖原文，
      ``"".join(seg.text) == text`` 是**构造性**成立（见 :func:`assert_lossless`）。
    """
    raw = str(text or "")
    if not raw:
        raise SegmentationError("空正文不能分段", field_path="text")
    try:
        limit = int(cap) if cap is not None else 0
    except (TypeError, ValueError):
        raise SegmentationError(f"非法上限: {cap!r}", field_path="provider_cap") from None
    if limit <= 0 or len(raw) <= limit:
        return [Segment(1, raw, 0, len(raw))]

    out: list[Segment] = []
    cursor = 0
    n = len(raw)
    while cursor < n:
        hard_end = min(cursor + limit, n)
        if hard_end >= n:
            end = n
        else:
            end = hard_end
            # 从上限往回找切点：句末 → 子句 → 空白；找不到就硬切（cap 是硬约束）
            for chars in (_SENT_END, _SUB_END, _WHITESPACE):
                pos = -1
                for i in range(hard_end - 1, cursor, -1):
                    if raw[i] in chars:
                        pos = i + 1
                        break
                if pos > cursor:
                    end = pos
                    break
            if end <= cursor:            # 极端输入（整段无切点）：硬切，保证推进
                end = hard_end
        out.append(Segment(len(out) + 1, raw[cursor:end], cursor, end))
        cursor = end
    return out


def assert_lossless(text: str, segments: Iterable[Segment]) -> str:
    """分段无损断言：拼回逐字符等于原文、区间连续且精确覆盖；失败即抛."""
    segs = list(segments)
    joined = "".join(s.text for s in segs)
    if joined != text:
        raise SegmentationError(
            "分段拼接与完整正文不一致（拒绝用不完整文本合成）",
            field_path="text", n_expected=len(text), n_actual=len(joined))
    cursor = 0
    for i, s in enumerate(segs, 1):
        if s.index != i or s.start != cursor or s.end - s.start != len(s.text):
            raise SegmentationError(
                f"第 {i} 段的 code point 区间不连续/不自洽: "
                f"start={s.start} end={s.end} expected_cursor={cursor}",
                field_path="segments")
        cursor = s.end
    if cursor != len(text):
        raise SegmentationError(
            f"分段未覆盖完整正文: 覆盖 {cursor} / {len(text)}", field_path="segments")
    return joined


# ---------------------------------------------------------------- 编号定位

_LOCATOR_INDEX_RE = re.compile(r"\[(\d+)\]\s*$")


def parse_locator_index(locator) -> Optional[int]:
    """`shots[3]` / `narration_lines[2]` → 3 / 2；编不出来返回 None（**不猜**）."""
    if locator is None:
        return None
    m = _LOCATOR_INDEX_RE.search(str(locator).strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------- 身份与请求键

def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_key_for(unit: SpeechUnit, *, applied: dict | None = None,
                    provider: str = "", model_revision: str = "") -> str:
    """生成 key：完整 text + profile/provider/model(+revision) + speaker + instruction
    + language + **实际** rate/volume + 格式/采样率 + 映射版本（§T2）.

    「实际」= 真的进了请求的值（`applied`）；provider 不支持的参数在这里是 None——
    改它不会改变音频，也就不该换 key。
    """
    ap = dict(applied or {})
    # `provider`/`model_revision` 省略（空串）时**回落到 unit 自己的值**：否则同一个
    # unit 因为调用点有没有显式传参而算出两个不同的 key（缓存/复用判据就分叉了）。
    payload = {
        "text": str(unit.text),
        "voice_profile_id": str(unit.voice_profile_id),
        "provider": str(provider or unit.provider),
        "model_id": str(unit.model_id),
        "model_revision": str(model_revision or unit.model_revision),
        "speaker": str(unit.speaker),
        "instruction": str(ap.get("instruction") if ap.get("instruction") else ""),
        "language": str(unit.language),
        "rate": ap.get("rate"),
        "volume": ap.get("volume"),
        "format": str(unit.format),
        "sample_rate": int(unit.sample_rate or 0),
        "mapping_version": str(unit.mapping_version),
    }
    if unit.voice_role:
        payload["voice_role"] = unit.voice_role
    return sha256_text(_canonical(payload))


# ---------------------------------------------------------------- 结果

@dataclass
class AudioArtifact:
    """一次成功合成的产物（含完整生成身份与分段记录）."""

    unit_id: str
    kind: str
    path: Path
    sha256: str
    bytes: int
    format: str
    sample_rate: int
    duration_sec: float
    request_key: str
    identity: dict
    segments: list = field(default_factory=list)
    total_samples: int = 0
    applied: dict = field(default_factory=dict)
    provider: str = ""
    model_id: str = ""
    speaker: str = ""
    reused: bool = False
    #: 该 unit 在计划里的编号（对白=镜头序号含 hook0；旁白=N 编号）
    source_index: Optional[int] = None

    def _index_fields(self) -> dict:
        """单元编号：对白给 `shot_index`、旁白给 `narration_index`（取不到就不写）."""
        idx = self.source_index
        if idx is None:
            idx = parse_locator_index(self.identity.get("locator"))
        if idx is None:
            return {}
        return ({"narration_index": int(idx)} if str(self.kind) == KIND_NARRATION
                else {"shot_index": int(idx)})

    def as_manifest_unit(self) -> dict:
        """候选 manifest 的声音单元记录（形状与 `shared.candidate_inputs` 一致）.

        除 `voice_identity` 外再给一份 **`identity` 别名**与 `artifact_path` 别名：
        C 包的消费入口（`expectation_for` → `verify_identity(..., expected=entry)`）按
        「`path`/`artifact_path` + `identity` 子对象」读，两边消费同一份 manifest 时
        不必各自转换（额外键对 `resolve_candidate_inputs` 无害）。
        """
        resolved = str(Path(self.path).resolve())
        return {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "path": resolved,
            "artifact_path": resolved,          # C 侧 `_first_present` 的别名
            "sha256": self.sha256,
            "text_sha256": self.identity.get("text_hash"),
            "voice_identity": dict(self.identity),
            "identity": dict(self.identity),    # `expectation_for` 直接合并这一份
            "window": self.identity.get("window") or {},
            "locator": self.identity.get("locator"),
            "shot_id": self.identity.get("shot_id"),
            # 生产者**自己声明编号**（S 的 resolver 只透传固定字段，不按数组位置猜身份）
            **self._index_fields(),
            "media": {"duration_sec": self.duration_sec, "sample_rate": self.sample_rate,
                      "format": self.format},
        }


# ---------------------------------------------------------------- 候选上下文

def new_attempt_id(prefix: str = "tts") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return "%s-%s-%04d" % (prefix, stamp, random.randint(0, 9999))


@dataclass
class TtsCandidateContext:
    """TTS 候选运行的上下文：包根 / attempt / 素材来源 / 来源身份.

    - 路径协议全部委托 `shared.enhancement_io`（attempt 目录 = `<root>/attempts/<id>/`）；
    - `clips` 是**视频候选**素材上下文（`resolve_clip_input` 直接吃本对象，
      因为这里实现了 `path_for`）；
    - 单写者锁在**包根**上（`<root>/.lock`）：同一候选根同时只允许一个 TTS 运行。
    """

    package_root: Path
    attempt: Path
    attempt_id: str
    experiment_id: str
    scope: dict = field(default_factory=dict)
    script_revision: str = ""
    source: str = "fixture"
    clips: Optional[CandidateClipContext] = None
    lock: Optional[eio.AttemptLock] = None
    plan_only: bool = False

    # -- 生命周期 --
    @classmethod
    def create(cls, candidate_root, *, attempt_id: Optional[str] = None,
               experiment_id: str = "", scope: Optional[dict] = None,
               script_revision: str = "", source: str = "fixture",
               clips: Optional[CandidateClipContext] = None,
               plan_only: bool = False, package: str = "tts") -> "TtsCandidateContext":
        root = Path(candidate_root)
        if not root.is_absolute():
            raise SynthesisError("candidate_root_relative",
                                 f"--candidate-root 必须是绝对路径: {root}",
                                 field_path="candidate_root", stage="paths")
        aid = eio.assert_attempt_id(attempt_id) if attempt_id else new_attempt_id()
        attempt = root / eio.ATTEMPTS_DIRNAME / aid
        return cls(package_root=root, attempt=attempt, attempt_id=aid,
                   experiment_id=str(experiment_id or ""), scope=dict(scope or {}),
                   script_revision=str(script_revision or ""), source=str(source),
                   clips=clips, plan_only=bool(plan_only))

    def __enter__(self) -> "TtsCandidateContext":
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open(self) -> "TtsCandidateContext":
        """建目录 + 抢单写者锁 + 对齐 lifecycle 投影（事件历史是权威）."""
        self.package_root.mkdir(parents=True, exist_ok=True)
        self.lock = eio.AttemptLock(self.package_root / eio.LOCK_DIRNAME, purpose="tts")
        self.lock.acquire()
        self.attempt.mkdir(parents=True, exist_ok=True)
        eio.reconcile_lifecycle(self.attempt, initial_state="draft",
                                transitions=ATTEMPT_TRANSITIONS)
        return self

    def close(self) -> None:
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    # -- 路径 --
    @property
    def attempt_dir(self) -> Path:
        """`execution_log_file(context)` 与 `assert_candidate_path` 的锚点."""
        return self.attempt

    @property
    def log_dir(self) -> Path:
        return self.attempt / "logs"

    @property
    def outputs_dir(self) -> Path:
        return self.attempt / "outputs"

    @property
    def work_dir(self) -> Path:
        return self.attempt / "work"

    def for_unit(self, unit_id: str, ext: str) -> Path:
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(unit_id))
        return self.outputs_dir / ("%s.%s" % (safe or "unit", ext.lstrip(".")))

    def guard(self, path) -> Path:
        """候选路径守卫（绝对、在候选根内、非输入/正式产物路径）——委托 F 包的唯一实现."""
        return eio.assert_candidate_path(
            path, experiment_root=self.package_root,
            inputs=tuple(self.input_paths()), formal=eio.production_paths())

    def input_paths(self) -> list:
        """本次运行的**输入**路径（候选产物不得与它们相等或互为父子）."""
        out = []
        if self.clips is not None and self.clips.manifest_path is not None:
            out.append(self.clips.manifest_path)
        return out

    # -- 素材 --
    def path_for(self, idx: int):
        """`shared.media_inputs.resolve_clip_input` 的候选分支入口（委托给 clips）."""
        if self.clips is None:
            return None
        return self.clips.path_for(idx)

    def indexes(self) -> list:
        return self.clips.indexes() if self.clips is not None else []


# ---------------------------------------------------------------- FFmpeg（宿主 subprocess）

def _tool(name: str) -> str:
    """ffmpeg / ffprobe 定位：优先复用共享定位器，退回 PATH 与 FFMPEG_BIN_DIR."""
    if name == "ffmpeg":
        try:
            from shared.media_inspect import ffmpeg_executable
            exe = ffmpeg_executable()
            if exe:
                return exe
        except Exception:
            pass
    else:
        try:
            from shared.media_inspect import ffprobe_executable
            exe = ffprobe_executable()
            if exe:
                return exe
        except Exception:
            pass
    exe = shutil.which(name)
    if exe:
        return exe
    bin_dir = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if bin_dir:
        candidate = Path(bin_dir) / (name + ".exe")
        if candidate.exists():
            return str(candidate)
        candidate = Path(bin_dir) / name
        if candidate.exists():
            return str(candidate)
    raise ToolUnavailableError(
        f"找不到 {name}（PCM 解码/拼接/编码一律走宿主 FFmpeg；"
        "可设 FFMPEG_BIN_DIR 或 MEDIA_INSPECT_FFMPEG/MEDIA_INSPECT_FFPROBE）",
        tool=name)


def _run_ffmpeg(args: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    cmd = [_tool("ffmpeg"), "-nostdin", "-hide_banner"] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise SynthesisError(
            "ffmpeg_failed",
            "FFmpeg 调用失败（rc=%s）: %s" % (proc.returncode,
                                             (proc.stderr or "").strip()[-800:]),
            stage="pcm_pipeline", command=" ".join(cmd[:6]) + " …")
    return proc


def probe_duration(path) -> float:
    """ffprobe 时长（失败即抛——不把 0 当真实时长）."""
    p = Path(path)
    if not p.is_file():
        raise SynthesisError("artifact_missing", f"探测时文件不存在: {p}",
                             stage="pcm_pipeline", field_path="path")
    proc = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    try:
        value = float((proc.stdout or "").strip())
    except ValueError:
        value = 0.0
    if proc.returncode != 0 or value <= 0:
        raise SynthesisError("probe_failed",
                             f"ffprobe 探测失败（rc={proc.returncode}）: {p}: "
                             f"{(proc.stderr or '').strip()[:200]}",
                             stage="pcm_pipeline", field_path="path")
    return value


def wav_sample_count(path: Path, *, channels: int = 1) -> int:
    """WAV 的 `data` 块采样数（RIFF 分块遍历；纯字节算术，不 import 音频库）.

    只用于**计数**（解码/拼接/编码本身全部由 FFmpeg 做）：`data` 字节数 /
    (2 字节 * 声道数)。找不到 data 块即抛（不猜）。
    """
    raw = Path(path).read_bytes()
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise SynthesisError("pcm_not_wav", f"中间 PCM 不是合法 WAV: {path}",
                             stage="pcm_pipeline", field_path="path")
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset:offset + 4]
        size = int.from_bytes(raw[offset + 4:offset + 8], "little")
        if chunk_id == b"data":
            body = min(size, max(0, len(raw) - offset - 8))
            step = PCM_BYTES_PER_SAMPLE * int(channels)
            if body % step:
                raise SynthesisError(
                    "pcm_partial_sample",
                    f"PCM 字节数不是整采样（{body} % {step}）——解码不完整: {path}",
                    stage="pcm_pipeline", field_path="path")
            return body // step
        offset += 8 + size + (size % 2)
    raise SynthesisError("pcm_no_data_chunk", f"WAV 无 data 块: {path}",
                         stage="pcm_pipeline", field_path="path")


@dataclass
class PcmMergeResult:
    path: Path
    sample_rate: int
    channels: int
    total_samples: int
    per_segment_samples: list


def merge_pcm_segments(segment_files: list, out_path: Path, *, work_dir: Path,
                       sample_rate: int, channels: int, out_format: str) -> PcmMergeResult:
    """各段音频 → 统一 PCM → 拼接 → 编码成消费格式（**全部**走宿主 FFmpeg）.

    步骤（每一步都是独立可核的 FFmpeg 调用，失败即抛、不产出半份产物）：

    1. 逐段解码 + 统一采样格式（s16le / 单声道 / 统一采样率）→ `<work>/norm_XX.wav`，
       并记下该段采样数；
    2. 用 concat 解复用器把规范化段**按序**拼成一个 PCM WAV（`-c copy`）；
    3. 再编码成消费格式（`out_format`）。

    额外核对：拼接后总采样数 **必须**等于各段之和——不等即拒绝（拼接丢/多了采样）。
    """
    if not segment_files:
        raise SynthesisError("no_segments", "没有任何分段音频可拼接", stage="pcm_pipeline")
    work_dir.mkdir(parents=True, exist_ok=True)
    norm_files: list[Path] = []
    per_samples: list[int] = []
    for i, src in enumerate(segment_files, 1):
        src = Path(src)
        if not src.is_file():
            raise SynthesisError("segment_output_missing",
                                 f"第 {i} 段音频不存在（provider 结果路径不可用）: {src}",
                                 stage="pcm_pipeline", field_path="segments")
        norm = work_dir / ("norm_%02d.wav" % i)
        _run_ffmpeg(["-y", "-v", "error", "-i", str(src), "-vn",
                     "-acodec", PCM_CODEC, "-ar", str(int(sample_rate)),
                     "-ac", str(int(channels)), str(norm)])
        per_samples.append(wav_sample_count(norm, channels=int(channels)))
        norm_files.append(norm)

    list_file = work_dir / "concat.txt"
    list_file.write_text("".join("file '%s'\n" % p.as_posix() for p in norm_files),
                         encoding="utf-8")
    joined = work_dir / "joined.wav"
    _run_ffmpeg(["-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(joined)])
    total = wav_sample_count(joined, channels=int(channels))
    if total != sum(per_samples):
        raise SynthesisError(
            "pcm_concat_mismatch",
            "拼接后采样数 %d ≠ 各段之和 %d（拼接丢/多了采样）"
            % (total, sum(per_samples)),
            stage="pcm_pipeline", field_path="segments")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(["-y", "-v", "error", "-i", str(joined),
                 "-acodec", _encoder_for(out_format), "-ar", str(int(sample_rate)),
                 "-ac", str(int(channels)), str(out_path)])
    return PcmMergeResult(path=out_path, sample_rate=int(sample_rate),
                          channels=int(channels), total_samples=total,
                          per_segment_samples=per_samples)


#: 消费格式 → 编码器（ffmpeg 内置；找不到编码器会显式失败，不静默换格式）
_ENCODERS = {
    "mp3": "libmp3lame",
    "wav": PCM_CODEC,
    "pcm": PCM_CODEC,
    "m4a": "aac",
    "aac": "aac",
    "opus": "libopus",
    "flac": "flac",
    "ogg": "libvorbis",
}


def _encoder_for(fmt: str) -> str:
    key = str(fmt or "").strip().lower().lstrip(".")
    if key not in _ENCODERS:
        raise SynthesisError("unsupported_format",
                             f"没有登记的编码器: {fmt!r}（已登记: {sorted(_ENCODERS)}）",
                             stage="pcm_pipeline", field_path="format")
    return _ENCODERS[key]


# ---------------------------------------------------------------- 规范化与能力

def normalize_unit(unit: SpeechUnit | dict) -> SpeechUnit:
    """请求规范化：缺**关键身份**时返回带 field_path 的错误（不猜、不补默认）."""
    if isinstance(unit, dict):
        names = {f for f in SpeechUnit.__dataclass_fields__}
        unit = SpeechUnit(**{k: v for k, v in unit.items() if k in names})
    if not isinstance(unit, SpeechUnit):
        raise SynthesisError("invalid_unit", f"不是 SpeechUnit: {type(unit).__name__}",
                             field_path="unit")
    checks = (
        ("unit_id", str(unit.unit_id or "").strip()),
        ("kind", str(unit.kind or "").strip()),
        ("text", str(unit.text or "")),
        ("provider", str(unit.provider or "").strip()),
        ("model_id", str(unit.model_id or "").strip()),
        ("script_revision", str(unit.script_revision or "").strip()),
    )
    for name, value in checks:
        if not value:
            raise SynthesisError("identity_incomplete",
                                 f"SpeechUnit 缺关键身份字段 {name}（源缺失必须显式失败）",
                                 field_path=name, unit_id=str(unit.unit_id or ""))
    if unit.kind not in LEGAL_KINDS:
        raise SynthesisError("invalid_kind",
                             f"kind 必须是 {LEGAL_KINDS} 之一，收到 {unit.kind!r}",
                             field_path="kind", unit_id=unit.unit_id)
    if not isinstance(unit.scope, dict) or not unit.scope:
        raise SynthesisError("identity_incomplete",
                             "SpeechUnit 缺 scope（章/部等运行范围）",
                             field_path="scope", unit_id=unit.unit_id)
    if not str(unit.mapping_version or "").strip():
        raise SynthesisError("identity_incomplete", "SpeechUnit 缺 mapping_version",
                             field_path="mapping_version", unit_id=unit.unit_id)
    if not str(unit.voice_profile_id or "").strip():
        raise SynthesisError("identity_incomplete", "SpeechUnit 缺 voice_profile_id",
                             field_path="voice_profile_id", unit_id=unit.unit_id)
    if int(unit.sample_rate or 0) <= 0:
        raise SynthesisError("invalid_sample_rate",
                             f"sample_rate 必须为正整数: {unit.sample_rate!r}",
                             field_path="sample_rate", unit_id=unit.unit_id)
    if unit.planned_window is not None:
        win = tuple(unit.planned_window)
        if len(win) != 2 or float(win[1]) < float(win[0]):
            raise SynthesisError("invalid_window",
                                 f"planned_window 非法: {unit.planned_window!r}",
                                 field_path="planned_window", unit_id=unit.unit_id)
    return unit


def resolve_applied(unit: SpeechUnit, caps) -> dict:
    """按能力事实决定**哪些参数真的进请求**（§T1：不假装生效）.

    - `params_strict=True`：显式请求了 provider 不支持的参数 ⇒ 拒收
      （code=`unsupported_parameter`，带 field_path）；
    - `params_strict=False`（旧请求兼容）：不拒收，但按事实记 `*_applied=False`，
      且不把 instruction 交给 provider（避免拼进发声文本被念出来）。
    """
    applied = {"instruction": "", "rate": None, "volume": None}

    def _decide(name: str, requested, supported: bool):
        if requested is None or (isinstance(requested, str) and not requested.strip()):
            return None
        if supported:
            return requested
        if unit.params_strict:
            raise SynthesisError(
                "unsupported_parameter",
                f"provider {caps.provider or unit.provider!r} 不支持 {name}"
                f"（能力事实：{caps.evidence or '未声明依据'}）——拒绝假装参数生效",
                field_path=name, unit_id=unit.unit_id)
        return None

    # rate=1.0（中性值）与 volume=None 都算「没请求」：它们不改变发声，
    # 不能被当成"要求了 provider 不支持的参数"而拒收（否则 qwen3 逐个单元必挂）。
    rate_value = None
    if unit.rate is not None:
        try:
            rate_value = float(unit.rate)
        except (TypeError, ValueError):
            raise SynthesisError("invalid_rate", f"rate 不是数字: {unit.rate!r}",
                                 field_path="rate", unit_id=unit.unit_id) from None
        if abs(rate_value - 1.0) <= 1e-9:
            rate_value = None
    instruction = _decide("instruction", str(unit.instruction or ""),
                          bool(caps.supports_instruction))
    rate = _decide("rate", rate_value, bool(caps.supports_rate))
    volume = _decide("volume", unit.volume, bool(caps.supports_volume))
    applied["instruction"] = str(instruction or "")
    applied["rate"] = float(rate) if rate is not None else None
    applied["volume"] = volume
    applied["instruction_applied"] = bool(instruction)
    applied["rate_applied"] = rate is not None
    applied["volume_applied"] = volume is not None
    return applied


def _check_speaker(unit: SpeechUnit, caps) -> None:
    """非法 speaker **明确拒绝**（含 provider 未声明白名单时不校验具体值）."""
    from tts.voice_registry import IllegalSpeakerError, VoiceRegistry
    if not caps.supports_speaker:
        return
    registry = VoiceRegistry.__new__(VoiceRegistry)  # 只借校验逻辑，不碰任何文件
    try:
        registry.validate_speaker(unit.speaker, provider=caps.provider or unit.provider,
                                  legal=caps.legal_speakers)
    except IllegalSpeakerError as exc:
        raise SynthesisError("speaker_not_legal", str(exc), field_path="speaker",
                             unit_id=unit.unit_id, provider=exc.provider,
                             speaker=exc.speaker, legal_speakers=exc.legal) from exc


# ---------------------------------------------------------------- 合成主入口

def synthesize_unit(unit: SpeechUnit | dict, candidate_context: TtsCandidateContext | None = None,
                    *, provider=None, output_path=None, work_dir=None) -> AudioArtifact:
    """**统一**合成入口：对白与旁白、同步与异步都调用本函数.

    - 给了 `candidate_context` ⇒ 产物落该 attempt 的 `outputs/`，路径过候选守卫；
    - 没给 ⇒ 旧请求兼容（就写到 `output_path`）；
    - 任一环节失败 ⇒ **整 unit 不提交**（清掉本次写出的中间文件后抛出）。
    """
    unit = normalize_unit(unit)
    provider = provider or create_provider(unit.provider or None)
    caps = provider_capabilities(provider, name=unit.provider)
    _check_speaker(unit, caps)
    applied = resolve_applied(unit, caps)

    sample_rate = int(unit.sample_rate or 24000)
    channels = 1
    segments = segment_text(unit.text, caps.max_chars_per_call)
    assert_lossless(unit.text, segments)

    request_key = request_key_for(unit, applied=applied,
                                  provider=caps.provider or unit.provider,
                                  model_revision=caps.model_revision or unit.model_revision)

    if candidate_context is not None:
        base = candidate_context.work_dir / unit.unit_id
        target = candidate_context.for_unit(unit.unit_id, unit.format)
        candidate_context.guard(target)   # 绝对 / 在候选根内 / 非输入与正式产物路径
    else:
        if output_path is None:
            raise SynthesisError("output_path_required",
                                 "非候选模式必须给 output_path", field_path="output_path",
                                 unit_id=unit.unit_id)
        target = Path(output_path)
        base = Path(work_dir) if work_dir else target.parent / (".tts_work_" + target.stem)
    seg_dir = Path(base) / "segments"
    if seg_dir.exists():
        shutil.rmtree(seg_dir, ignore_errors=True)
    seg_dir.mkdir(parents=True, exist_ok=True)

    produced: list[dict] = []
    try:
        for seg in segments:
            ext = str(unit.format or "mp3").lstrip(".")
            seg_out = seg_dir / ("seg_%02d.%s" % (seg.index, ext))
            req = unit.as_request(text=seg.text)
            req.rate = float(applied["rate"]) if applied["rate"] is not None else unit.rate
            req.volume = int(applied["volume"]) if applied["volume"] is not None else 0
            req.instruction = str(applied["instruction"] or "")
            result = provider.synthesize(req, output=str(seg_out))
            if not isinstance(result, TTSSuccessResult) or not getattr(result, "success", False):
                raise ProviderCallError(
                    "第 %d/%d 段合成失败: %s" % (seg.index, len(segments),
                                                getattr(result, "error", result)),
                    code=str(getattr(result, "code", "") or "provider_failed"),
                    field_path="text", unit_id=unit.unit_id,
                    segment_index=seg.index, segment_span=[seg.start, seg.end])
            actual = getattr(result, "save_path", None) or str(seg_out)
            actual_path = Path(actual)
            if not actual_path.is_file():
                raise ProviderCallError(
                    "第 %d 段 provider 返回的 save_path 不存在（不 stat 假定的原后缀）: %s"
                    % (seg.index, actual_path),
                    code="provider_output_missing", field_path="save_path",
                    unit_id=unit.unit_id, segment_index=seg.index)
            produced.append({"index": seg.index, "start": seg.start, "end": seg.end,
                             "chars": seg.n_chars, "text_sha256": sha256_text(seg.text),
                             "input_path": str(actual_path.resolve()),
                             "input_bytes": actual_path.stat().st_size})

        merged = merge_pcm_segments([p["input_path"] for p in produced], target,
                                    work_dir=seg_dir.parent / "pcm",
                                    sample_rate=sample_rate, channels=channels,
                                    out_format=unit.format)
        for rec, samples in zip(produced, merged.per_segment_samples):
            rec["samples"] = int(samples)
        duration = probe_duration(target)
        _check_window(unit, duration)

        digest = sha256_file(target)
        segs = [dict(r, sample_rate=sample_rate) for r in produced]
        identity = build_identity(
            unit_id=unit.unit_id, scope=dict(unit.scope),
            script_revision=unit.script_revision, text=unit.text,
            provider=str(caps.provider or unit.provider), model_id=unit.model_id,
            model_revision=str(caps.model_revision or unit.model_revision or ""),
            voice_profile_id=unit.voice_profile_id, speaker=unit.speaker,
            mapping_version=unit.mapping_version, instruction=str(applied["instruction"] or ""),
            language=unit.language,
            rate=applied["rate"] if applied["rate"] is not None else None,
            rate_applied=bool(applied["rate_applied"]),
            volume=applied["volume"], volume_applied=bool(applied["volume_applied"]),
            format=str(unit.format), sample_rate=sample_rate,
            artifact_path=target, artifact_hash=digest,
            artifact_bytes=target.stat().st_size, request_key=request_key,
            kind=unit.kind, segments=segs,
            extra={**({"voice_role": unit.voice_role} if unit.voice_role else {}),
                   "locator": unit.source_locator,
                   "shot_id": (unit.source_locator or None),
                   "source_span": list(unit.source_span) if unit.source_span else None,
                   "planned_window": list(unit.planned_window) if unit.planned_window else None,
                   "window": ({"start_sec": float(unit.planned_window[0]),
                               "end_sec": float(unit.planned_window[1])}
                              if unit.planned_window else {}),
                   "duration_sec": duration,
                   "total_samples": merged.total_samples,
                   "channels": channels,
                   "capabilities": caps.describe(),
                   # 声线语义：`separate` = 模型与声线分开；`model_id` = 旧 provider
                   # （声线就是模型 ID，此时 speaker 为空是**事实**而不是缺字段）
                   "speaker_mode": "separate" if str(unit.speaker or "").strip() else "model_id",
                   "source": ("candidate" if candidate_context is not None else "in_place"),
                   "segmentation": {"cap": caps.max_chars_per_call,
                                    "n_segments": len(segments),
                                    "lossless": True}})
        write_identity(target, identity)
        if unit.legacy_voice_signature is not None:
            write_legacy_signature(target, unit.text, unit.legacy_voice_signature)
        return AudioArtifact(
            unit_id=unit.unit_id, kind=unit.kind, path=target, sha256=digest,
            bytes=target.stat().st_size, format=str(unit.format),
            sample_rate=sample_rate, duration_sec=duration, request_key=request_key,
            identity=identity, segments=segs, total_samples=merged.total_samples,
            applied=applied, provider=str(caps.provider or unit.provider),
            model_id=unit.model_id, speaker=unit.speaker,
            source_index=unit.source_index)
    except BaseException:
        # 整 unit 不提交：清掉本次写出的中间文件（**不**动盘上既有产物）
        shutil.rmtree(seg_dir.parent, ignore_errors=True)
        try:
            if candidate_context is None and target.exists():
                target.unlink(missing_ok=True)
                identity_path(target).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _check_window(unit: SpeechUnit, duration: float) -> None:
    """时长守卫（统一 unit 层）：超窗 ⇒ `duration_rejected`，不截音不加速."""
    if unit.planned_window is None:
        return
    avail = float(unit.planned_window[1]) - float(unit.planned_window[0])
    if avail <= 0:
        return
    from tts.config import config as _cfg
    tol = max(float(_cfg.WINDOW_TOLERANCE_MIN_SEC),
              avail * float(_cfg.WINDOW_TOLERANCE_RATIO))
    if duration > avail + tol:
        raise DurationRejectedError(
            "%s 时长超出承载窗口: actual=%.2fs 可用=%.2fs（窗口 %s，容差 %.2fs）"
            "——**禁止截句**（不截音、不暗自加速）；请改台词/停顿/镜头"
            % ("旁白" if unit.kind == KIND_NARRATION else "对白",
               duration, avail, unit.planned_window, tol),
            unit_id=unit.unit_id, field_path="planned_window",
            duration_sec=duration, available_sec=avail, tolerance_sec=tol)


# ---------------------------------------------------------------- 候选事务

#: 本包 attempt 的合法状态链（恢复校验用；词汇表按包不同，见
#: ``shared.enhancement_io.reconcile_lifecycle``）。零单元也要能从 frozen 直接收口。
ATTEMPT_TRANSITIONS = {
    "frozen": {"draft"},
    "synthesized": {"frozen", "synthesized"},
    "accepted": {"frozen", "synthesized"},
}

def freeze_and_record(ctx: TtsCandidateContext, payload: dict, *, kind: str = "inputs") -> None:
    """冻结事实输入并追加一条事件（锁内；调用方须已 open）."""
    eio.freeze_inputs(ctx.attempt, payload)
    eio.append_event(ctx.attempt, kind, after={"state": "frozen"})


def record_unit(ctx: TtsCandidateContext, artifact: AudioArtifact, *,
                event: str = "unit_synthesized", extra: dict | None = None) -> dict:
    """把一个 unit 的结果追加进事件历史（seq 递增、fsync 后才算确认）."""
    ev = eio.append_event(
        ctx.attempt, event,
        after={"state": "synthesized"},
        unit_id=artifact.unit_id, unit_kind=artifact.kind,
        path=str(Path(artifact.path).resolve()), sha256=artifact.sha256,
        text_sha256=artifact.identity.get("text_hash"),
        request_key=artifact.request_key, duration_sec=artifact.duration_sec,
        reused=bool(artifact.reused), **(extra or {}))
    return ev


def build_tts_manifest(ctx: TtsCandidateContext, artifacts: list, *,
                       checks: list | None = None, status: str = "completed",
                       extra: dict | None = None) -> dict:
    """构造候选 manifest（形状与 `shared.candidate_inputs` 逐字对齐）.

    `units` 每项：unit_id / kind / path / sha256 / text_sha256 / voice_identity /
    window（可被 `resolve_candidate_inputs` 直接消费）；`artifacts` 走
    `enhancement_io.artifact_record`（真实存在的文件 + 摘要）。
    """
    units = []
    records = []
    for art in artifacts:
        units.append(art.as_manifest_unit())
        records.append(eio.artifact_record(Path(art.path), role="audio",
                                           media={"duration_sec": art.duration_sec,
                                                  "sample_rate": art.sample_rate,
                                                  "format": art.format}))
    payload = {
        "package": "tts",
        "scope": dict(ctx.scope),
        "script_revision": str(ctx.script_revision),
        "source": str(ctx.source),
        "status": str(status),
        "checks": list(checks if checks is not None else default_checks(artifacts)),
        "units": units,
        "artifacts": records,
        "experiment_id": ctx.experiment_id,
        "attempt_id": ctx.attempt_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    return payload


def default_checks(artifacts: list) -> list:
    """默认检查项（**每一类都独立记状态**，空表不等于通过）."""
    if not artifacts:
        return [{"name": "units", "status": "failed", "detail": "没有任何产物"}]
    lossless = all(bool((a.identity.get("segmentation") or {}).get("lossless"))
                   for a in artifacts)
    identities = all(a.identity.get("schema_version") == IDENTITY_SCHEMA_V2
                     for a in artifacts)
    return [
        {"name": "segments_lossless", "status": "pass" if lossless else "failed"},
        {"name": "identity_complete", "status": "pass" if identities else "failed"},
        {"name": "units", "status": "pass", "detail": "%d 个声音单元" % len(artifacts)},
    ]


def publish_attempt(ctx: TtsCandidateContext, artifacts: list, *, status: str = "completed",
                    checks: list | None = None, extra: dict | None = None,
                    input_key: str = "") -> dict:
    """封 manifest + 两阶段替换 accepted（替换后**读回**确认）.

    返回 `{"manifest": …, "accepted": …}`；读回失败 ⇒ `commit_unconfirmed`（退出码 6），
    既不声称旧值仍在、也不盲目回滚。
    """
    # manifest 与 accepted 指针必须记**同一个**请求身份：只给指针记，核验器就没有
    # 可对照的一侧，输入 key 这一层形同不存在（2026-10-04 复核 P1-2 之二）。
    key = input_key or _input_key(ctx)
    payload = build_tts_manifest(ctx, artifacts, checks=checks, status=status,
                                 extra={**(extra or {}), "request_key": key})
    sealed = eio.seal_manifest(ctx.attempt, payload)
    manifest_path = ctx.attempt / eio.MANIFEST_FILENAME
    accepted = eio.commit_accepted(ctx.package_root, attempt_id=ctx.attempt_id,
                                   manifest_path=manifest_path,
                                   manifest_sha256=sealed["manifest_sha256"],
                                   input_key=key)
    # 只有 accepted 指针读回确认之后才补投影（事件历史与产物状态分开）
    eio.append_event(ctx.attempt, "accepted", after={"state": "accepted"},
                     attempt_id=ctx.attempt_id, status=str(status),
                     n_units=len(artifacts))
    return {"manifest": sealed, "accepted": accepted}


def _input_key(ctx: TtsCandidateContext) -> str:
    return eio.canonical_json_digest({
        "scope": ctx.scope, "script_revision": ctx.script_revision,
        "source": ctx.source,
        "video_manifest": (str(ctx.clips.manifest_path)
                           if ctx.clips is not None and ctx.clips.manifest_path else None),
    })


def synthesize_units(units: list, ctx: TtsCandidateContext, *, provider=None,
                     reuse: bool = True, collected: Optional[list] = None) -> list:
    """批量合成（候选事务内）：逐 unit 复用有效 accepted 或走新 attempt 路径.

    - `reuse=True`：先 :func:`reuse_accepted` 做**完整关系验证**，通过则复用（不重合成）；
    - 每个 unit 结果都在锁内追加事件（seq 递增、fsync 后才算确认）；
    - 任一 unit 失败 ⇒ 抛出（**整批不发布**：manifest/accepted 由调用方在全部成功后才写）。
    """
    provider = provider or create_provider()
    out: list = []
    for unit in units:
        unit = normalize_unit(unit)
        caps = provider_capabilities(provider, name=unit.provider)
        _check_speaker(unit, caps)
        applied = resolve_applied(unit, caps)
        key = request_key_for(unit, applied=applied, provider=caps.provider or unit.provider,
                              model_revision=caps.model_revision or unit.model_revision)
        artifact = reuse_accepted(ctx, unit, request_key=key) if reuse else None
        if artifact is None:
            artifact = synthesize_unit(unit, ctx, provider=provider)
        record_unit(ctx, artifact)
        out.append(artifact)
        if collected is not None:
            collected.append(artifact)
    return out


def unit_plan_entry(unit: SpeechUnit, *, cap: int | None = None) -> dict:
    """`SpeechUnit` → 可落盘的规划条目（含源文本位置与**完整** text，不含任何媒体）.

    `cap` 给了就顺带给出按能力算出的分段 code point 起止——规划阶段即可离线核
    「拼回等于原正文」（§T4：「长文本每段有 code point 起止，重组等于原正文后才允许采纳」）。
    """
    entry = {
        "unit_id": unit.unit_id, "kind": unit.kind, "locator": unit.source_locator,
        "shot_id": unit.source_locator, "text": unit.text, "chars": len(unit.text),
        "scope": dict(unit.scope), "script_revision": unit.script_revision,
        "planned_window": list(unit.planned_window) if unit.planned_window else None,
        "voice_profile_id": unit.voice_profile_id, "provider": unit.provider,
        "model_id": unit.model_id, "model_revision": unit.model_revision,
        "speaker": unit.speaker, "instruction": unit.instruction,
        "language": unit.language, "rate": unit.rate, "volume": unit.volume,
        "format": unit.format, "sample_rate": unit.sample_rate,
        "mapping_version": unit.mapping_version,
    }
    if cap is not None:
        segs = segment_text(unit.text, cap)
        entry["segments"] = [{"index": s.index, "start": s.start, "end": s.end}
                             for s in segs]
        entry["segments_lossless"] = ("".join(s.text for s in segs) == unit.text)
        entry["provider_cap"] = cap
    return entry


def write_speech_unit_plan(out_dir, units: list, *, scope=None, script_revision: str = "",
                           provider: str = "", source: str = "plan",
                           provider_name: str = "", extra: dict | None = None) -> Path:
    """`plan` / `--plan-only` 的产物：`<out_dir>/speech_units.json`（**只生产 SpeechUnits**）.

    不写媒体、不建 attempt、不碰 accepted 指针——这是「只规划」与「真正合成」的分界。
    """
    from tts.service import provider_capabilities as _caps_of
    name = str(provider or provider_name or "")
    caps = _caps_of(name) if name else None
    cap = caps.max_chars_per_call if caps is not None else None
    payload = {
        "schema_version": "tts_speech_units.v1",
        "scope": dict(scope or {}),
        "script_revision": str(script_revision or ""),
        "provider": name, "source": str(source),
        "capabilities": caps.describe() if caps is not None else None,
        "units": [unit_plan_entry(u, cap=cap) for u in units],
        "n_units": len(units),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "speech_units.json"
    commit_json_swap(target, prepare_json_swap(target, payload,
                                               ensure_ascii=False, indent=2))
    return target


def reuse_accepted(ctx: TtsCandidateContext, unit: SpeechUnit, *, request_key: str) -> AudioArtifact | None:
    """有效缓存 → **完整关系验证后**复用（§T2：「有效缓存→完整关系验证后复用」）.

    任一步不满足（无 accepted / 摘要不符 / 缺该 unit / request_key 变了 / 身份与盘上
    媒体不符）⇒ 返回 None（交调用方走新 attempt），**不**把坏缓存当命中。
    """
    try:
        verified = eio.verify_accepted(ctx.package_root)
    except eio.EnhancementError:
        return None
    manifest = verified["manifest"]
    if manifest.get("scope") != ctx.scope or \
            str(manifest.get("script_revision") or "") != str(ctx.script_revision):
        return None
    for rec in manifest.get("units") or []:
        if str(rec.get("unit_id")) != str(unit.unit_id):
            continue
        path = Path(str(rec.get("path") or ""))
        ident = rec.get("voice_identity") or {}
        if str(ident.get("request_key") or "") != request_key:
            return None
        if not path.is_file() or sha256_file(path) != str(rec.get("sha256") or ""):
            return None
        if verify_identity(path, ident) != "":
            return None
        return AudioArtifact(
            unit_id=unit.unit_id, kind=str(rec.get("kind") or unit.kind), path=path,
            sha256=str(rec.get("sha256")), bytes=path.stat().st_size,
            format=str(ident.get("format") or unit.format),
            sample_rate=int(ident.get("sample_rate") or unit.sample_rate),
            duration_sec=float(ident.get("duration_sec") or 0.0),
            request_key=request_key, identity=ident,
            segments=list(ident.get("segments") or []),
            total_samples=int(ident.get("total_samples") or 0),
            applied={"rate": ident.get("rate"), "volume": ident.get("volume"),
                     "instruction": ident.get("instruction"),
                     "rate_applied": ident.get("rate_applied"),
                     "volume_applied": ident.get("volume_applied")},
            provider=str(ident.get("provider") or ""), model_id=str(ident.get("model_id") or ""),
            speaker=str(ident.get("speaker") or ""), reused=True)
    return None


__all__ = [
    "REVISION_UNVERSIONED", "PCM_CODEC", "Segment", "SpeechUnit",
    "SynthesisError", "DurationRejectedError", "SegmentationError",
    "ProviderCallError", "ToolUnavailableError",
    "AudioArtifact", "TtsCandidateContext", "PcmMergeResult",
    "segment_text", "assert_lossless", "request_key_for", "normalize_unit",
    "resolve_applied", "synthesize_unit", "merge_pcm_segments", "probe_duration",
    "wav_sample_count", "new_attempt_id", "freeze_and_record", "record_unit",
    "build_tts_manifest", "default_checks", "publish_attempt", "reuse_accepted",
    "unit_plan_entry", "write_speech_unit_plan", "synthesize_units",
    "parse_locator_index",
]
