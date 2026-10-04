# -*- coding: utf-8 -*-
"""shared.audio_identity - 语音音轨的**身份签名**（生成侧与消费侧共用一份）.

2026-09-30 复核第 8 轮 P1：「`_collect_narration_tracks` 只检查文件存在、大小和时长，
不检查当前文本、音色签名。离线注入旧签名音轨，仍被接受。Step 03 的缓存检查不能替代
Step 04 的消费检查：计划重选或 N 编号变化后，直接合并可能播放上一稿的旁白。」

签名 = `f"{音色 id}|{正文 sha256 前 16 位}"`（与 step03 历史写法逐字一致，只是搬到
这里做单源）。音轨旁的 `.voice` 边车文件保存它：
  - **生成侧**（step03）写完音频写签名，缓存命中要求签名一致；
  - **消费侧**（step04）合并前重新核对——**缓存检查 ≠ 消费检查**。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: 边车文件后缀（`shot_3.mp3` → `shot_3.voice`）
SIGNATURE_SUFFIX = ".voice"


def speech_signature(text: str, voice_config: dict | None) -> str:
    """语音签名：`音色 id|正文 sha256 前 16 位`（正文取 strip 后的原样）."""
    digest = hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:16]
    voice_id = str((voice_config or {}).get("id", "") or "")
    return "%s|%s" % (voice_id, digest)


def read_signature(audio_path) -> str:
    """读音轨旁的签名（不存在/不可读 → 空串）."""
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


__all__ = ["SIGNATURE_SUFFIX", "speech_signature", "read_signature",
           "verify_signature", "verify_text_signature"]
