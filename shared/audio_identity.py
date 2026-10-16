# -*- coding: utf-8 -*-
"""shared.audio_identity - 语音音轨的**身份签名**（生成侧与消费侧共用一份）.

历史（2026-09-30 复核第 8 轮 P1）：「`_collect_narration_tracks` 只检查文件存在、大小和
时长，不检查当前文本、音色签名。离线注入旧签名音轨，仍被接受。Step 03 的缓存检查不能
替代 Step 04 的消费检查」。签名 = `f"{音色 id}|{正文 sha256 前 16 位}"`，音轨旁的
`.voice` 边车保存它。

**2026-10-03 计划 §T2a 新增 `audio_identity.v2`**：旧签名只有「音色 id + 正文指纹」，
既分不出「模型 ID / speaker / mapping_version」，也无法表达 instruction、实际
rate/volume、实际格式与产物摘要——消费侧拿不到富身份就只能靠文本 signature 反推。
v2 侧车（`<音频文件名>.identity.json`，**与媒体同目录**）由生成侧写、消费侧读：

- 生成侧：每次成功合成后写完整身份（含分段记录），写前过 :func:`identity_problems`；
- 消费侧：按 `unit_id` 取期望身份（`--audio-candidates` manifest）→ 核媒体侧车与请求
  关系；**文本 signature 不能自行恢复富身份**，来源缺失一律
  :data:`IDENTITY_INCOMPLETE`，不许「缺就是通过」；
- 旧 `.voice` **只作旧请求兼容**（旧 provider 的旧语义），不复用于新 profile/参数。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from shared.json_io import commit_json_swap, prepare_json_swap

#: 旧边车文件后缀（`shot_3.mp3` → `shot_3.voice`），只作旧请求兼容
SIGNATURE_SUFFIX = ".voice"

#: v2 身份侧车后缀：**完整文件名 + 后缀**（`shot_3.mp3` → `shot_3.mp3.identity.json`）
IDENTITY_SUFFIX = ".identity.json"
IDENTITY_SCHEMA_V2 = "audio_identity.v2"

#: 消费侧拿不到完整身份时的**统一原因**（不许当成通过）
IDENTITY_INCOMPLETE = "identity_incomplete"

#: v2 必填字段（**键必须存在**；其中 `NULLABLE` 里的值允许 null/空——那是「未生效」
#: 这一**事实**的表达，不是缺字段）
REQUIRED_IDENTITY_FIELDS = (
    "unit_id", "scope", "script_revision", "text", "text_hash", "text_sha256",
    "provider", "model_id", "model_revision", "voice_profile_id", "speaker",
    "mapping_version", "language", "rate", "rate_applied", "volume",
    "volume_applied", "format", "sample_rate", "artifact_hash", "request_key",
)
#: 值可为 null/空串的必填字段：provider 不支持该参数、或该 provider 的**声线由
#: model_id 决定**时，这里如实记「未生效 / 无独立 speaker」，而不是判成结构缺字段。
#: （新模型的 speaker 由 profile/能力白名单保证非空；这条放宽只服务旧请求语义。）
NULLABLE_IDENTITY_FIELDS = ("model_revision", "instruction", "rate", "volume", "speaker")
#: 必须有**非空**值的字段（缺值即 identity_incomplete，不许拿空串糊过去）
NONEMPTY_IDENTITY_FIELDS = (
    "unit_id", "scope", "script_revision", "text", "text_hash", "text_sha256",
    "provider", "model_id", "voice_profile_id", "mapping_version", "language",
    "format", "sample_rate", "artifact_hash", "request_key",
)


def speech_signature(text: str, voice_config: dict | None) -> str:
    """旧语音签名：`音色 id|正文 sha256 前 16 位`（正文取 strip 后的原样）."""
    digest = hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:16]
    voice_id = str((voice_config or {}).get("id", "") or "")
    return "%s|%s" % (voice_id, digest)


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def read_signature(audio_path) -> str:
    """读旧签名边车（不存在/不可读 → 空串）."""
    p = Path(audio_path).with_suffix(SIGNATURE_SUFFIX)
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _text_digest(text: str) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:16]


def verify_signature(audio_path, text: str, voice_config: dict | None) -> str:
    """**消费侧**核对（正文 + 音色）：通过返回 ""，否则返回原因.

    缺签名边车也算不通过——消费侧不能假设「文件在」就等于「内容对」。
    """
    want = speech_signature(text, voice_config)
    got = read_signature(audio_path)
    if not got:
        return "缺少签名边车（%s%s）——无法确认它是不是本条声音" % (
            Path(audio_path).name, SIGNATURE_SUFFIX)
    if got == want:
        return ""
    _v_got, _, _d_got = got.partition("|")
    _v_want, _, _d_want = want.partition("|")
    if _d_got != _d_want:
        return "正文对不上（音轨是另一段文本合成的：%s）" % ("签名正文指纹 %s ≠ %s"
                                                              % (_d_got or "?", _d_want))
    return "音色对不上（音轨是另一音色合成的：%s ≠ %s）" % (_v_got or "?", _v_want)


def verify_text_signature(audio_path, text: str) -> str:
    """只核**正文**（不核音色）——消费侧拿不到角色音色配置时用（逐镜对白轨）.

    返回 ""（通过）/ "missing"（无签名）/ 原因文本（正文不符）。缺签名时由调用方
    决定是失败还是告警（历史产物可能没有边车）。
    """
    got = read_signature(audio_path)
    if not got:
        return "missing"
    _v_got, _, _d_got = got.partition("|")
    if _d_got != _text_digest(text):
        return ("正文对不上：签名正文指纹 %s ≠ %s（音轨是另一段文本合成的）"
                % (_d_got or "?", _text_digest(text)))
    return ""


# ---------------------------------------------------------------- v2 侧车：读写

def identity_path(audio_path) -> Path:
    """v2 侧车路径：``<音频同目录>/<音频文件名>.identity.json``."""
    p = Path(audio_path)
    return p.with_name(p.name + IDENTITY_SUFFIX)


def identity_problems(payload) -> list[str]:
    """完整身份的结构校验（返回带 field_path 的问题清单；空表 = 合法）.

    只判**结构与自洽**，不判业务关系（关系由 :func:`verify_identity` 核）：
    必填字段缺席/空、`text_hash` 与 `text` 不符、`artifact_hash` 不是 64 位十六进制。
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["identity: 顶层不是对象（%s）" % type(payload).__name__]
    if payload.get("schema_version") != IDENTITY_SCHEMA_V2:
        problems.append("identity.schema_version: 期望 %r，实际 %r"
                        % (IDENTITY_SCHEMA_V2, payload.get("schema_version")))
    unusable: set = set()
    for name in REQUIRED_IDENTITY_FIELDS:
        if name not in payload:
            problems.append("identity.%s: 缺少必填字段" % name)
            unusable.add(name)
        elif payload[name] is None and name not in NULLABLE_IDENTITY_FIELDS:
            problems.append("identity.%s: 值为 null（必须显式给出）" % name)
            unusable.add(name)
    for name in NONEMPTY_IDENTITY_FIELDS:
        value = payload.get(name)
        if isinstance(value, str) and not value.strip():
            problems.append("identity.%s: 空字符串（必须显式给出）" % name)
            unusable.add(name)
        elif name == "scope" and not isinstance(value, dict):
            problems.append("identity.scope: 必须是对象（章/部等运行范围）")
            unusable.add(name)
        elif name == "scope" and not value:
            problems.append("identity.scope: 空对象（必须给出运行范围）")
            unusable.add(name)
    sample_rate = payload.get("sample_rate")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        problems.append("identity.sample_rate: 必须是正整数（实际采样率）")
    for name in ("rate", "volume"):
        value = payload.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            problems.append("identity.%s: 必须是数字或 null（null = 未生效）" % name)
    if isinstance(payload.get("text"), str) and "text" not in unusable:
        want = sha256_text(payload["text"])
        if payload.get("text_hash") != want:
            problems.append("identity.text_hash: 与 text 不符（%s ≠ %s）"
                            % (str(payload.get("text_hash"))[:12], want[:12]))
        # `text_sha256` 是 `text_hash` 的**同一事实**的别名（S 包的字段名）：
        # 两个都在时不许各说各话
        if "text_sha256" in payload and payload.get("text_sha256") not in (None, "",
                                                                           want):
            problems.append("identity.text_sha256: 与 text_hash 不一致（%s ≠ %s）"
                            % (str(payload.get("text_sha256"))[:12],
                               str(payload.get("text_hash"))[:12]))
    artifact_hash = payload.get("artifact_hash")
    if isinstance(artifact_hash, str) and artifact_hash.strip():
        if len(artifact_hash) != 64 or any(c not in "0123456789abcdef" for c in artifact_hash):
            problems.append("identity.artifact_hash: 不是 sha256 十六进制小写")
    if not isinstance(payload.get("text"), str):
        problems.append("identity.text: 必须是字符串（合成用的完整正文）")
    if isinstance(payload.get("segments"), list):
        for i, seg in enumerate(payload["segments"]):
            if not isinstance(seg, dict):
                problems.append("identity.segments[%d]: 不是对象" % i)
                continue
            for key in ("index", "start", "end"):
                if not isinstance(seg.get(key), int) or isinstance(seg.get(key), bool):
                    problems.append("identity.segments[%d].%s: 必须是整数" % (i, key))
    return problems


def identity_missing_fields(payload) -> list:
    """身份里**必须非空**却缺失的字段清单（消费侧判 `identity_incomplete` 的单源）.

    与 :func:`identity_problems` 同一份判据：只有 :data:`NONEMPTY_IDENTITY_FIELDS`
    缺了才算不完整；`NULLABLE_IDENTITY_FIELDS`（rate/volume/instruction/
    model_revision/speaker）为 null/空是**合法事实**——「provider 不支持该参数」
    或「该 provider 的声线由 model_id 决定」，不是缺字段。

    消费侧请调本函数，不要自己再抄一份 `in (None, "")` 清单：抄一份就会与本模块
    的口径漂移（实测反例：另一边按 18 项全非空判，旧 Sambert 轨与不支持 rate 的
    新 provider 轨都会被误拒）。
    """
    if not isinstance(payload, dict):
        return ["identity"]
    missing = []
    for name in NONEMPTY_IDENTITY_FIELDS:
        value = payload.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    if not isinstance(payload.get("scope"), dict) or not payload.get("scope"):
        missing.append("scope")
    return sorted(set(missing))


def write_identity(audio_path, payload: dict) -> Path:
    """生成侧：原子写 v2 侧车；**写前过校验**（不合法就抛，不落盘半份身份）."""
    problems = identity_problems(payload)
    if problems:
        raise ValueError("拒绝写入非法身份侧车: " + "；".join(problems))
    target = identity_path(audio_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = prepare_json_swap(target, payload, ensure_ascii=False, indent=2)
    commit_json_swap(target, tmp)
    return target


def read_identity(audio_path) -> dict | None:
    """消费侧：读 v2 侧车；**不存在/读不动返回 None**（不猜、不造身份）."""
    p = identity_path(audio_path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _short(value, n: int = 16) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def verify_identity(audio_path, expected: dict | None = None, *,
                    require_media: bool = True) -> str:
    """消费侧**完整关系**核对：返回 "" 通过，否则返回原因（`identity_incomplete` 或缺项）.

    - 侧车缺席/坏 ⇒ :data:`IDENTITY_INCOMPLETE`（**不是通过**）；
    - `expected` 给出期望身份（来自 `--audio-candidates` manifest 的同一 `unit_id`）时，
      逐字段核 `unit_id / request_key / text_hash / provider / speaker /
      voice_profile_id / mapping_version / model_id`，并核 `artifact_hash` 与**盘上
      媒体**一致（媒体被换过 ⇒ 拒收）；
    - 核对失败一律返回具体字段，不用「文本 signature 相像」替代。
    """
    ident = read_identity(audio_path)
    if ident is None:
        return "%s：缺少 %s 身份侧车（%s）" % (IDENTITY_INCOMPLETE,
                                              IDENTITY_SCHEMA_V2, identity_path(audio_path).name)
    problems = identity_problems(ident)
    if problems:
        return "%s：侧车不完整（%s）" % (IDENTITY_INCOMPLETE, "；".join(problems[:3]))
    if require_media:
        p = Path(audio_path)
        if not p.is_file():
            return "媒体不存在：%s" % p
        actual = sha256_file(p)
        if actual != ident.get("artifact_hash"):
            return ("媒体与身份侧车不一致（artifact_hash %s ≠ 盘上 %s）"
                    % (_short(ident.get("artifact_hash")), _short(actual)))
    if expected is None:
        return ""
    for key in ("unit_id", "request_key", "text_hash", "provider", "speaker",
                "voice_profile_id", "mapping_version", "model_id"):
        if key not in expected or expected.get(key) in (None, ""):
            continue
        if str(ident.get(key)) != str(expected.get(key)):
            return ("%s 不符：侧车 %r ≠ 期望 %r" % (key, _short(ident.get(key)),
                                                   _short(expected.get(key))))
    return ""


def build_identity(*, unit_id: str, scope: dict, script_revision: str, text: str,
                   provider: str, model_id: str, model_revision: str,
                   voice_profile_id: str, speaker: str, mapping_version: str,
                   instruction: str, language: str, rate, rate_applied: bool,
                   volume, volume_applied: bool, format: str, sample_rate: int,
                   artifact_path, artifact_hash: str, artifact_bytes: int,
                   request_key: str, kind: str = "", segments: list | None = None,
                   extra: dict | None = None) -> dict:
    """构造 v2 身份（字段口径见 §T2a；`rate_applied` 等是**能力事实**）."""
    payload = {
        "schema_version": IDENTITY_SCHEMA_V2,
        "unit_id": str(unit_id),
        "kind": str(kind or ""),
        "scope": dict(scope or {}),
        "script_revision": str(script_revision or ""),
        "text": str(text),
        "text_hash": sha256_text(text),
        #: 与 `text_hash` 同一事实的别名：S 包的 `AUDIO_IDENTITY_V2_FIELDS` 用这个名字
        "text_sha256": sha256_text(text),
        "text_sha16": _text_digest(text),
        "provider": str(provider or ""),
        "model_id": str(model_id or ""),
        "model_revision": str(model_revision or ""),
        "voice_profile_id": str(voice_profile_id or ""),
        "speaker": str(speaker or ""),
        "mapping_version": str(mapping_version or ""),
        "instruction": str(instruction or ""),
        "language": str(language or ""),
        "rate": rate,
        "rate_applied": bool(rate_applied),
        "volume": volume,
        "volume_applied": bool(volume_applied),
        "instruction_applied": bool(str(instruction or "").strip()),
        "format": str(format or ""),
        "sample_rate": int(sample_rate or 0),
        "artifact_path": str(Path(artifact_path).resolve()),
        "artifact_hash": str(artifact_hash),
        "artifact_bytes": int(artifact_bytes),
        "request_key": str(request_key),
        "segments": list(segments or []),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    return payload


def write_legacy_signature(audio_path, text: str, voice_config: dict | None) -> Path:
    """旧请求兼容：写 `.voice` 边车（**只在旧语义路径**上调用，见 §T2a）."""
    p = Path(audio_path).with_suffix(SIGNATURE_SUFFIX)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(speech_signature(text, voice_config), encoding="utf-8")
    return p


# ---------------------------------------------------------------- v2 消费入口（audio-candidates）

def load_audio_candidates(path) -> dict:
    """读 `--audio-candidates` manifest → `{unit_id: 期望身份}`.

    接受 `{"units": [{unit_id, artifact_path, identity: {...}}, …]}` 或
    `{"units": {"<unit_id>": {...}}}`；未知形态显式失败（不静默给空表）。
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"audio-candidates manifest 不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"audio-candidates manifest 顶层不是对象: {type(data).__name__}")
    units = data.get("units")
    out: dict[str, dict] = {}
    if isinstance(units, dict):
        for unit_id, entry in units.items():
            out[str(unit_id)] = dict(entry or {})
    elif isinstance(units, list):
        for entry in units:
            if not isinstance(entry, dict) or not entry.get("unit_id"):
                raise ValueError("audio-candidates units[] 缺 unit_id")
            out[str(entry["unit_id"])] = dict(entry)
    else:
        raise ValueError("audio-candidates manifest 缺 units（对象或数组）")
    return out


def expectation_for(unit_id: str, candidates: dict) -> dict:
    """从 `load_audio_candidates` 的结果取该 `unit_id` 的期望身份（缺席即失败）."""
    entry = candidates.get(str(unit_id))
    if entry is None:
        raise ValueError("%s：audio-candidates manifest 中找不到 unit_id=%r"
                         % (IDENTITY_INCOMPLETE, unit_id))
    ident = entry.get("identity")
    merged = dict(entry)
    if isinstance(ident, dict):
        merged.update(ident)
    return merged


# ---------------------------------------------------------------- 对白文本提取（三方共用）

def dialogue_speech_text(shot, plan, characters=None) -> tuple:
    """该镜**该由对白轨合成**的文本 → `(raw_for_speaker, text)`；不该合成时 `(None, None)`.

    2026-10-03 计划 §15「发声与说话人」：`dialogue_speech_text` 返回
    `(raw_for_speaker, text)` 二元组——**身份摘要用 `text`、说话人识别用
    `raw_for_speaker`**；空轨返回 `(None, None)`，**不能只返回正文**（否则说话人
    识别拿到的是剥掉前缀的台词，voice 分叉到旁白音色）。

    两条分支（与既有 `step03.shot_tts._dialogue_speech` 逐字一致）：

    - 计划**有**声音条目 ⇒ 按 `shot_voice_parts` 结构化分流：只取**对白**那一份，
      文本就是最终合成文本（**不再二次 strip_speaker_prefix**——必须与消费侧
      `shot_voice_parts(...)["dialogue"].strip()` 逐字一致，否则身份核对会把正确的
      音轨判成「与当前文本不符」）；`raw_for_speaker` = 说话人前缀 + 该段正文；
    - 计划**没有**声音条目（旧稿/无契约）⇒ 回退历史口径（标点启发式 + 前缀剥离）。
    """
    from step01_script_gen.plan_feasibility import shot_voice_parts
    raw_text = str((shot or {}).get("narration_segment", "") or "").strip()
    if not raw_text:
        return None, None
    parts = shot_voice_parts(shot, plan)
    if not parts["entries"]:
        from shared.dialogue import has_dialogue, strip_speaker_prefix
        if not has_dialogue(raw_text):
            return None, None
        return raw_text, strip_speaker_prefix(raw_text, characters)
    piece = parts["dialogue"].strip()
    if not piece:
        return None, None
    return (parts.get("prefix") or "") + piece, piece


__all__ = [
    "SIGNATURE_SUFFIX", "IDENTITY_SUFFIX", "IDENTITY_SCHEMA_V2", "IDENTITY_INCOMPLETE",
    "REQUIRED_IDENTITY_FIELDS",
    "speech_signature", "read_signature", "verify_signature", "verify_text_signature",
    "sha256_text", "sha256_file",
    "identity_path", "identity_problems", "identity_missing_fields",
    "build_identity", "write_identity",
    "read_identity", "verify_identity", "write_legacy_signature",
    "load_audio_candidates", "expectation_for", "dialogue_speech_text",
]
