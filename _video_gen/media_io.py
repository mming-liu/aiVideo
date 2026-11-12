# -*- coding: utf-8 -*-
"""媒体类型识别与 data URL 编码（Seedance 系 provider 共用）.

T1（2026-11）：此前两个 Seedance provider 的 ``_encode_media`` 对未知扩展名
一律回退 ``image/png``，``reference_paths`` 里的视频会被当作图片发给模型。
这里把「媒体类型判定」与「编码」收敛为一份实现，volcengine / agent_plan
共用，避免两边各自维护扩展名表。

判定顺序：扩展名 → ``mimetypes`` → 未知回退 ``image/png``（保持旧行为）。
视频参考的 role / content type 由 provider 决定，本模块只回答“这是什么媒体”。
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv", ".mpeg", ".mpg",
})
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
})
IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
})

_EXTENSION_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def media_kind(path: str | os.PathLike | None) -> str:
    """返回 ``image`` / ``video`` / ``audio`` / ``unknown``.

    先看扩展名（离线产物常见、可靠），再退回 ``mimetypes``；都不命中按
    ``unknown`` 处理（调用方沿用旧行为当图片）。
    """
    if not path:
        return "unknown"
    ext = Path(str(path)).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        major = mime.split("/", 1)[0]
        if major in ("image", "video", "audio"):
            return major
    return "unknown"


def media_mime(path: str | os.PathLike | None) -> str:
    """推断 data URL 的 mime；未知扩展名回退 ``image/png``（旧行为）."""
    if not path:
        return "image/png"
    ext = Path(str(path)).suffix.lower()
    if ext in _EXTENSION_MIME:
        return _EXTENSION_MIME[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


def encode_media(path: str | None) -> str | None:
    """读取本地文件，返回 base64 data URL（视频/音频按真实 mime 编码）."""
    if not path:
        return None
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return None
        mime = media_mime(p)
        with open(p, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None
