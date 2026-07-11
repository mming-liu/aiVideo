"""step03_tts_audio.shot_tts - Per-shot TTS generation module"""
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
from typing import Optional, List


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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


def gen_shot_tts(shot: dict, output_dir: Path, provider=None) -> Optional[str]:
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        print(f"  [SKIP] Shot {idx}: no narration_segment")
        return None
    output_path = output_dir / f"shot_{idx}.mp3"
    if output_path.exists() and output_path.stat().st_size > 10240:
        print(f"  [CACHE] Shot {idx}: cached")
        return str(output_path)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        gen_single_tts(text, output_path, provider)
        return str(output_path)
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        return None


def gen_all_shot_tts(script: dict, output_dir: Path) -> List[str]:
    shots = script.get("shots", [])
    if not shots:
        print("[WARN] No shots data")
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = create_provider()
    results = []
    print(f"[INFO] Generating TTS for {len(shots)} shots...\n")
    for shot in shots:
        path = gen_shot_tts(shot, output_dir, provider)
        if path:
            results.append(path)
    print(f"\n[INFO] Shot TTS done: {len(results)}/{len(shots)} success")
    return results