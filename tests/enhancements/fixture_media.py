# -*- coding: utf-8 -*-
"""tests.enhancements 的媒体夹具生成器（B/C 包用；**本机 ffmpeg 现造，不下载任何资源**）.

- 所有 ffmpeg 调用都是参数列表（与生产同一约定）；
- 素材全部落在调用方给的隔离目录里（pytest tmp_path），**不碰 output/ 正式产物**；
- 每个夹具都是"有判别力"的对照：固定镜 / 合法静止 / 黑暗非黑场 / 闪光 / 快摇 /
  允许内部切镜 / 硬切 / 注入黑场 / 注入停帧 / 损坏媒体 / 无音轨 / 连续有声 / 静音间隙。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

W, H, FPS = 160, 120, 10
DEFAULT_SCOPE = {"novel": "fixture-novel", "book_index": 1, "chapter": 1}

_ESC = chr(92)  # filter 表达式里的逗号转义（不用裸反斜杠写字符串）


def ffmpeg_exe() -> str | None:
    return shutil.which("ffmpeg")


def _run(argv) -> None:
    proc = subprocess.run([str(a) for a in argv], shell=False, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg 夹具生成失败 rc=%s:\n%s" % (proc.returncode,
                                                proc.stderr.decode("utf-8", "replace")[-2000:]))


def _color(path: Path, color: str, seconds: float, *, rate: int = FPS) -> None:
    _run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
          f"color=c={color}:s={W}x{H}:r={rate}:d={seconds}", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", path])


def _concat_colors(path: Path, colors: list, seconds_each: float = 1.0) -> None:
    args = [ffmpeg_exe(), "-y", "-v", "error"]
    for color in colors:
        args += ["-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}:d={seconds_each}"]
    flows = "".join(f"[{i}:v]" for i in range(len(colors)))
    args += ["-filter_complex", f"{flows}concat=n={len(colors)}:v=1:a=0[v]",
             "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    _run(args)


def _with_audio(path: Path, *, video_src: str, audio_srcs: list) -> None:
    """视频 + 一段或多段音频（多段按顺序 concat：用来造"有声→静音"的真实间隙）."""
    args = [ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", video_src]
    for src in audio_srcs:
        args += ["-f", "lavfi", "-i", src]
    if len(audio_srcs) == 1:
        args += ["-map", "0:v", "-map", "1:a"]
    else:
        flows = "".join(f"[{i + 1}:a]" for i in range(len(audio_srcs)))
        args += ["-filter_complex", f"{flows}concat=n={len(audio_srcs)}:v=0:a=1[a]",
                 "-map", "0:v", "-map", "[a]"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path]
    _run(args)


def build_video_set(root: Path) -> dict:
    """生成 B 包 9 类对照 + 静音分支素材；返回 ``{name: path}``."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    out["static"] = root / "static.mp4"          # 固定镜（画面完全静止）
    _color(out["static"], "blue", 2.0)

    out["dark"] = root / "dark.mp4"              # 黑暗但非黑场（0x202020 高于 pix_th）
    _color(out["dark"], "0x202020", 2.0)

    out["flash"] = root / "flash.mp4"            # 闪光（合法亮度突变，不是黑场）
    _run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
          f"testsrc=s={W}x{H}:r={FPS}:d=2",
          "-vf", "eq=brightness=0.5:enable='between(t,0.8,1.0)'",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", out["flash"]])

    out["pan"] = root / "pan.mp4"                # 快摇（整段有位移，2s 内不静止）
    _run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
          f"testsrc=s={W * 2}x{H}:r={FPS}:d=2",
          "-vf", f"crop=w={W}:h={H}:x='min({W}*t\\,{W})':y=0",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", out["pan"]])

    out["cut_internal"] = root / "cut_internal.mp4"   # 内部硬切（red→blue @1.0s）
    _concat_colors(out["cut_internal"], ["red", "blue"])

    out["black_inject"] = root / "black_inject.mp4"   # 注入黑场（中段 1s）
    _concat_colors(out["black_inject"], ["red", "black", "red"])

    out["freeze_inject"] = root / "freeze_inject.mp4"  # 注入停帧（中段冻结）
    _run([ffmpeg_exe(), "-y", "-v", "error",
          "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r={FPS}:d=1",
          "-f", "lavfi", "-i", f"testsrc=s={W}x{H}:r={FPS}:d=1",
          "-filter_complex",
          "[0:v]trim=0:1,setpts=PTS-STARTPTS[v0];"
          "[1:v]trim=0:1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1.2[v1];"
          "[v0][v1]concat=n=2:v=1:a=0[v]",
          "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", out["freeze_inject"]])

    out["no_audio"] = root / "no_audio.mp4"      # 无音轨
    _color(out["no_audio"], "green", 2.0)

    out["tone"] = root / "tone.mp4"              # 连续有声（无静音段）
    _with_audio(out["tone"], video_src=f"color=c=blue:s={W}x{H}:r={FPS}:d=2",
                audio_srcs=["sine=frequency=440:sample_rate=16000:duration=2"])

    out["silence_gap"] = root / "silence_gap.mp4"  # 前 1s 有声 + 后 1s 静音（真实间隙）
    _with_audio(out["silence_gap"], video_src=f"color=c=blue:s={W}x{H}:r={FPS}:d=2",
                audio_srcs=["sine=frequency=440:sample_rate=16000:duration=1",
                            "anullsrc=r=16000:cl=mono:d=1"])

    out["silence_lead"] = root / "silence_lead.mp4"  # 整段静音（有音轨的静音）
    _with_audio(out["silence_lead"], video_src=f"color=c=blue:s={W}x{H}:r={FPS}:d=2",
                audio_srcs=["anullsrc=r=16000:cl=mono:d=2"])

    out["corrupt"] = root / "corrupt.mp4"        # 损坏媒体（截断 + 花掉 mdat）
    good = out["cut_internal"].read_bytes()
    blob = bytearray(good[: int(len(good) * 0.6)])
    for i in range(len(blob) // 2, min(len(blob), len(blob) // 2 + 4096)):
        blob[i] = 0x00
    out["corrupt"].write_bytes(bytes(blob))
    return out


def build_speech_wav(root: Path, *, seconds: float = 3.0, name: str = "speech.wav") -> Path:
    """C 包用：一段真实可解码的 WAV（正弦代替人声——夹具，不宣称是语音）."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    _run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
          f"sine=frequency=300:sample_rate=16000:duration={seconds}",
          "-ac", "1", "-c:a", "pcm_s16le", path])
    return path


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def write_json(path, payload) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_audio_identity(audio_path, *, unit_id: str, text: str = "", script_revision: str = "rev-1",
                         scope=None, schema: str = "audio_identity.v2", **extra) -> dict:
    """写 <audio 文件名>.identity.json 富身份侧车（T 包 audio_identity.v2 结构）.

    直接调用 T 包的 build_identity + write_identity：夹具与生产**同一份判据**，
    避免测试自造一套 key 名。返回写盘的 payload（供 manifest 的 identity 子对象复用）；
    schema="audio_identity.v1" 走旧稿兼容分支（只为构造"旧侧车"反例）。
    """
    from shared import audio_identity as ai
    if schema != ai.IDENTITY_SCHEMA_V2:
        payload = {"schema_version": schema, "unit_id": unit_id, "text": text}
        payload.update(extra)
        write_json(ai.identity_path(audio_path), payload)
        return payload
    payload = ai.build_identity(
        unit_id=unit_id,
        scope=(scope if scope is not None else dict(DEFAULT_SCOPE)),
        script_revision=script_revision,
        text=text, provider="fixture-provider", model_id="fixture-model",
        model_revision="rev-fixture-1", voice_profile_id="vp-fixture", speaker="fixture-voice",
        mapping_version="map-1", instruction="", language="zh", rate=None, rate_applied=False,
        volume=None, volume_applied=False, format="wav", sample_rate=16000,
        artifact_path=audio_path, artifact_hash=sha256_file(audio_path),
        artifact_bytes=Path(audio_path).stat().st_size, request_key="rk-fixture",
        kind="dialogue")
    payload.update(extra)
    ai.write_identity(audio_path, payload)
    return payload


def write_tts_manifest(path, *, unit_id: str, audio_path, identity: dict) -> Path:
    """写候选 manifest（T 包消费入口认得的结构：units[] 带 artifact_path + identity）."""
    return write_json(path, {
        "schema_version": "tts_candidate_manifest.v1",
        "package": "tts",
        "units": [{"unit_id": unit_id, "artifact_path": str(audio_path),
                    "artifact_hash": identity.get("artifact_hash"),
                    "identity": identity}],
    })


def write_audio_manifest(path, units: list, *, schema: str = "tts_candidate_manifest.v1") -> Path:
    """写音频候选 manifest（``units`` 为记录数组；按显式 unit_id 查，不按位置猜）."""
    return write_json(path, {"schema_version": schema, "package": "tts", "units": units})


def make_request_dict(*, audio_path, unit_id: str, spoken_text: str, subtitle_text: str,
                      mapping: list, segments: list, script_revision: str = "rev-1",
                      shot_index: int = 3, audio_manifest_path=None,
                      audio_manifest_hash=None, voice_signature: str = "fixture-voice|deadbeef",
                      scope=None) -> dict:
    """构造 ``subtitle_alignment_request.v1``（字段按 §15 补齐）."""
    return {
        "schema_version": "subtitle_alignment_request.v1",
        "script_revision": script_revision,
        "shot_index": shot_index,
        "unit_id": unit_id,
        "audio_path": str(audio_path),
        "audio_sha256": sha256_file(audio_path),
        "audio_manifest_path": str(audio_manifest_path) if audio_manifest_path else None,
        "audio_manifest_hash": (audio_manifest_hash if audio_manifest_hash is not None
                                else (sha256_file(audio_manifest_path) if audio_manifest_path else None)),
        "spoken_text": spoken_text,
        "subtitle_text": subtitle_text,
        "mapping": mapping,
        "voice_signature": voice_signature,
        "segments": segments,
        "scope": scope if scope is not None else {},
    }
