# -*- coding: utf-8 -*-
"""step03 测试共用的离线桩 provider（不参与收集；下划线开头）.

只做协议/分段/身份/事务验收：写真实 WAV、每个 unit 一个**不同频率**的测试音，
**不**代表任何模型的音质或性能（`real_model=False`）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from tts.models import ProviderCapabilities, TTSFailureResult, TTSSuccessResult  # noqa: E402


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    bin_dir = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if bin_dir:
        for cand in (Path(bin_dir) / "ffmpeg.exe", Path(bin_dir) / "ffmpeg"):
            if cand.exists():
                return str(cand)
    raise RuntimeError("需要 ffmpeg")


class StubProvider:
    """可判别桩：文本 → 频率/时长都不同；可按调用序注入失败."""

    PROVIDER_NAME = "step03_stub_provider"

    def __init__(self, *, cap: int | None = 60, sample_rate: int = 24000,
                 dur_per_char: float = 0.12, max_dur: float = 5.0,
                 fail_at_call: int | None = None, legal_speakers=("stub-narrator",
                                                                  "stub-role-a",
                                                                  "stub-role-b"),
                 supports_speaker: bool = True):
        self.cap = cap
        self.sample_rate = sample_rate
        self.dur_per_char = dur_per_char
        self.max_dur = max_dur
        self.fail_at_call = fail_at_call
        self.calls: list = []
        self._caps = ProviderCapabilities(
            provider=self.PROVIDER_NAME, max_chars_per_call=cap,
            supports_instruction=False, supports_rate=False, supports_volume=False,
            supports_speaker=supports_speaker, legal_speakers=tuple(legal_speakers),
            output_formats=("wav",), sample_rates=(sample_rate,),
            model_id="stub-model", model_revision="stub-r1", real_model=False,
            evidence="离线桩（单元测试用）")

    def capabilities(self):
        return self._caps

    def synthesize(self, req, output=None):
        self.calls.append({"text": req.text, "speaker": req.effective_speaker(),
                           "model": req.model, "instruction": req.instruction})
        if self.fail_at_call is not None and len(self.calls) == self.fail_at_call:
            return TTSFailureResult(error="桩注入失败", code="injected_failure")
        out = Path(output).with_suffix(".wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        freq = 400 + 30 * (sum(ord(c) for c in req.text) % 9)
        dur = min(self.dur_per_char * len(req.text) + 0.30, self.max_dur)
        subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=%d:duration=%.3f:sample_rate=%d"
                        % (freq, dur, self.sample_rate),
                        "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        return TTSSuccessResult(save_path=str(out), actual_format="wav",
                                actual_sample_rate=self.sample_rate,
                                applied={"instruction": False, "rate": False,
                                         "volume": False})


def make_mp3(path: Path, dur: float, freq: int = 1200) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=%d:duration=%.3f:sample_rate=44100" % (freq, dur),
                    "-c:a", "libmp3lame", "-b:a", "128k", str(path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path
