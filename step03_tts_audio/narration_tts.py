"""step03_tts_audio.narration_tts - Narration TTS generation module"""
import sys
from pathlib import Path
import os

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import json
from tts import create_provider, TTSRequest
from tts.config import config as tts_config


def load_narration(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        script = json.load(f)
    narration = script.get("narration", "")
    if not narration:
        raise ValueError(f"Narration field empty: {path}")
    return narration


def gen_single_tts(text: str, output_path: Path, provider=None) -> str:
    if provider is None:
        provider = create_provider()
    req = TTSRequest(
        text=text,
        model=tts_config.MODEL,
        rate=tts_config.RATE,
        volume=tts_config.VOLUME,
        format=tts_config.FORMAT,
        sample_rate=tts_config.SAMPLE_RATE,
    )
    result = provider.synthesize(req, output=str(output_path))
    if not result.success:
        raise RuntimeError(result.error)
    size_kb = output_path.stat().st_size / 10.24
    print(f"  [OK] {output_path.name} ({size_kb:.1f} KB)")
    return str(output_path)


def gen_narration_tts(text: str, output: str = None) -> str:
    if output is None:
        output = tts_config.OUTPUT_PATH
    provider = create_provider()
    req = TTSRequest(
        text=text,
        model=tts_config.MODEL,
        rate=tts_config.RATE,
        volume=tts_config.VOLUME,
        format=tts_config.FORMAT,
        sample_rate=tts_config.SAMPLE_RATE,
    )
    print(f"[INFO] TTS text length: {len(text)} chars")
    print(f"[INFO] Provider: {tts_config.PROVIDER}")
    print(f"[INFO] Model: {req.model} | Rate: {req.rate} | Volume: {req.volume}")
    result = provider.synthesize(req, output=output)
    if not result.success:
        raise RuntimeError(result.error)
    size_kb = len(result.audio_bytes) / 10.24
    print(f"[OK] Narration TTS done: {result.save_path} ({size_kb:.1f} KB)")
    return result.save_path