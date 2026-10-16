# -*- coding: utf-8 -*-
"""tools.enhancement_workers.alignment_worker - WhisperX 已知文本对齐 worker（计划 §C1/§F5）.

边界（计划原文）：

- **只用已知文本 alignment**：不启用 ASR 转写、不做说话人分离，不从强制拟合推断
  "声音说对了文本"；
- **禁止自动下载**：权重只从 ``model_dir`` 本地目录加载；目录缺失/为空 ⇒
  ``unavailable``/``weights_missing``（宿主还额外把 HF 置为离线模式，见 protocol.OFFLINE_ENV）；
- **字符正规化必须保留原索引映射**：返回的 unit 索引用的是**输入正文的 Unicode
  码点**（0 起、半开区间），正规化只用于匹配，不改变对外索引；
- 置信信息缺失时如实写 ``diagnostics``，**不创造阈值或置信分**。

运行（宿主自动按此 argv 拉起）::

    <py312>/python.exe tools/enhancement_workers/alignment_worker.py \
        --request R.json --response W.json

payload::

    {"audio_path": "...", "model_dir": "...", "spoken_text": "...",
     "language": "zh", "device": "cpu", "segments": [{"start_sec","end_sec"}]?}

result::

    {"units": [{"char_start","char_end","start_sec","end_sec"}],
     "backend": "whisperx", "backend_version": "...", "model_revision": "...",
     "diagnostics": {...}}
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from enhancement_workers.protocol import WorkerError, package_version  # noqa: E402
from enhancement_workers.runner import WorkerUnavailable, main, require_import  # noqa: E402

WORKER_NAME = "whisperx_align"


def _local_model_dir(model_dir) -> Path:
    """本地权重目录校验：**只读本地**，不触发任何下载."""
    if not model_dir:
        raise WorkerUnavailable("weights_missing", "未提供 --model-dir（本包禁止自动下载权重）",
                                field_path="model_dir")
    p = Path(str(model_dir))
    if not p.is_dir():
        raise WorkerUnavailable("weights_missing", f"本地权重目录不存在: {p}",
                                field_path="model_dir")
    entries = [x for x in p.iterdir() if not x.name.startswith(".")]
    if not entries:
        raise WorkerUnavailable("weights_missing", f"本地权重目录为空: {p}",
                                field_path="model_dir")
    return p


def _normalized_with_index(text: str) -> list:
    """正规化并**保留原索引**：返回 [{ch, index}]，index 是原串的 Unicode 码点位置.

    正规化仅用于与模型输出比对；对外 unit 索引一律用 ``index``（原串坐标）。
    """
    out = []
    for i, ch in enumerate(str(text)):
        norm = unicodedata.normalize("NFKC", ch)
        norm = norm.strip().lower()
        if not norm:
            continue
        out.append({"ch": norm, "index": i})
    return out


def _char_units(aligned: list, spoken_text: str) -> tuple[list, dict]:
    """把 WhisperX 的字级对齐映射回原串索引（不猜、不补：对不上的进 diagnostics）."""
    normalized = _normalized_with_index(spoken_text)
    cursor = 0
    units: list[dict] = []
    unmatched = 0
    missing_time = 0
    for seg in aligned or []:
        for word in seg.get("words") or []:
            chars = word.get("chars") or []
            if not chars:  # 没有字级对齐 ⇒ 用词级区间（覆盖整词字符）——不编造时间
                text = str(word.get("text") or "")
                start, end = word.get("start"), word.get("end")
                if start is None or end is None:
                    missing_time += len(text)
                    continue
                for _ch in text:
                    if cursor < len(normalized) and normalized[cursor]["ch"] == _norm_one(_ch):
                        units.append({"char_start": normalized[cursor]["index"],
                                      "char_end": normalized[cursor]["index"] + 1,
                                      "start_sec": float(start), "end_sec": float(end)})
                        cursor += 1
                    else:
                        unmatched += 1
                continue
            for ch in chars:
                start, end = ch.get("start"), ch.get("end")
                if start is None or end is None:
                    missing_time += 1
                    cursor += 1
                    continue
                if cursor < len(normalized) and normalized[cursor]["ch"] == _norm_one(ch.get("char", "")):
                    units.append({"char_start": normalized[cursor]["index"],
                                  "char_end": normalized[cursor]["index"] + 1,
                                  "start_sec": float(start), "end_sec": float(end)})
                    cursor += 1
                else:
                    unmatched += 1
    return units, {
        "normalized_char_count": len(normalized),
        "matched_char_count": len(units),
        "unmatched_char_count": unmatched,
        "char_without_time_count": missing_time,
        "index_coordinates": "input_text_unicode_codepoints",
    }


def _norm_one(ch: str) -> str:
    return unicodedata.normalize("NFKC", str(ch)).strip().lower()


def align_known_text(payload: dict) -> dict:
    audio_path = payload.get("audio_path")
    spoken_text = payload.get("spoken_text")
    if not audio_path or not Path(str(audio_path)).is_file():
        raise WorkerError("input_missing", f"音频不存在: {audio_path}", field_path="audio_path")
    if not isinstance(spoken_text, str) or not spoken_text:
        raise WorkerError("bad_payload", "payload 缺少 spoken_text", field_path="spoken_text")

    model_dir = _local_model_dir(payload.get("model_dir"))

    # 只有上面都通过、且本地权重存在时才 import 模型栈（缺包 ⇒ unavailable）
    whisperx = require_import("whisperx", "whisperx",
                              hint="仅在有本地中文对齐权重且已获真实对齐运行授权时准备隔离栈")
    import torch  # noqa: PLC0415 - 隔离环境内的重依赖，宿主永不 import

    device = str(payload.get("device") or ("cuda" if torch.cuda.is_available() else "cpu"))
    language = str(payload.get("language") or "zh")
    try:
        audio = whisperx.load_audio(str(audio_path))
    except Exception as exc:  # noqa: BLE001
        raise WorkerError("audio_decode_failed", f"WhisperX 读音频失败: {exc}") from exc

    duration = len(audio) / float(getattr(whisperx, "SAMPLE_RATE", 16000) or 16000)
    segments = payload.get("segments") or []
    if segments:
        template = [{"start": float(s.get("start_sec") or 0.0),
                     "end": float(s.get("end_sec") if s.get("end_sec") is not None else duration),
                     "text": spoken_text} for s in segments]
    else:
        # 已知文本 + 整段音频：**强制对齐**（不是 ASR，文本由调用方给定）
        template = [{"start": 0.0, "end": duration, "text": spoken_text}]

    try:
        model, metadata = whisperx.load_align_model(language_code=language,
                                                    device=device, model_name=str(model_dir))
        aligned = whisperx.align(template, model, metadata, audio, device,
                                 return_char_alignments=True)
    except Exception as exc:  # noqa: BLE001
        raise WorkerUnavailable("weights_unusable",
                                f"本地权重不可用或对齐失败: {type(exc).__name__}: {exc}") from exc

    units, diagnostics = _char_units(aligned.get("segments") if isinstance(aligned, dict) else aligned,
                                     spoken_text)
    diagnostics.update({"language": language, "device": device,
                        "model_dir": str(model_dir), "audio_duration_sec": duration,
                        "alignment_mode": "known_text_forced_alignment",
                        "asr_used": False, "speaker_diarization_used": False,
                        "confidence_note": "WhisperX 字级对齐不给置信分；本包不创造阈值"})
    return {
        "units": units,
        "backend": "whisperx",
        "backend_version": package_version("whisperx"),
        "model_revision": str(model_dir.name),
        "diagnostics": diagnostics,
    }


if __name__ == "__main__":
    sys.exit(main(WORKER_NAME, align_known_text))
