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
from tts.voice_registry import get_registry, NARRATOR_VOICE, AVAILABLE_VOICES
from typing import Optional, List


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _detect_speaker_from_narration(narration: str, characters: List[dict]) -> Optional[str]:
    """从叙述文本中检测说话人角色名.

    检测逻辑：
    1. 在文本开头匹配 "XXX道："、"XXX说："等对话格式
    2. 匹配 "XXX笑道："、"XXX怒道："等带情感词的情况
    3. 匹配 "XXX低声"等动作描述
    """
    if not narration or not characters:
        return None

    # 角色名列表
    char_names = [c.get("identifier", "") for c in characters if c.get("identifier")]
    if not char_names:
        return None

    # 按名字长度降序排列，优先匹配更长的名字
    char_names = sorted(char_names, key=len, reverse=True)

    # 对话格式匹配
    import re
    dialogue_patterns = [
        r"^([^\s，。,]{1,4})(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂|低声道|低声说|低声问道|低声笑道|笑道|冷笑道|急道|忙道)[:：]",
        r"^([^\s，。,]{1,4})(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂)[:：]",
    ]

    for pattern in dialogue_patterns:
        match = re.match(pattern, narration)
        if match:
            speaker = match.group(1)
            # 验证匹配到的确实是角色名
            for char_name in char_names:
                if char_name in speaker or speaker in char_name:
                    return char_name
            if speaker in char_names:
                return speaker

    # 尝试在文本开头找角色名（动作+说话）
    for char_name in char_names:
        if narration.startswith(char_name) or narration[:6].find(char_name) >= 0:
            if any(v in narration[len(char_name):len(char_name)+3] for v in ["道", "说", "问", "笑", "怒", "哭"]):
                return char_name

    return None


def gen_single_tts(text: str, output_path: Path, provider=None,
                   voice_config: Optional[dict] = None) -> str:
    """生成单个 TTS 音频.

    Args:
        text: 要转换的文本
        output_path: 输出文件路径
        provider: TTS 提供商实例
        voice_config: 音色配置 {id, rate, volume, ...}，None 时使用默认配置
    """
    if provider is None:
        provider = create_provider()

    # 使用角色专属音色或默认配置
    if voice_config:
        model = voice_config.get("id", tts_config.MODEL)
        rate = float(voice_config.get("rate", tts_config.RATE))
        volume = int(voice_config.get("volume", tts_config.VOLUME))
    else:
        model = tts_config.MODEL
        rate = tts_config.RATE
        volume = tts_config.VOLUME

    req = TTSRequest(
        text=text,
        model=model,
        rate=rate,
        volume=volume,
        format=tts_config.FORMAT,
        sample_rate=tts_config.SAMPLE_RATE,
    )
    result = provider.synthesize(req, output=str(output_path))
    if not result.success:
        raise RuntimeError(result.error)
    size_kb = output_path.stat().st_size / 10.24
    print(f"  [OK] {output_path.name} ({size_kb:.1f} KB)")
    return str(output_path)


def gen_shot_tts(shot: dict, output_dir: Path, provider=None,
                 voice_registry=None, characters: List[dict] = None) -> Optional[str]:
    """为单个分镜生成 TTS 音频.

    Args:
        shot: 分镜数据，包含 index, narration_segment, main_subject 等字段
        output_dir: 输出目录
        provider: TTS 提供商实例
        voice_registry: 音色注册表实例，None 时自动创建
        characters: 角色列表，优先用于检测说话人

    Returns:
        生成的音频文件路径，失败返回 None
    """
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        print(f"  [SKIP] Shot {idx}: no narration_segment")
        return None

    # 1. 识别说话人角色（优先从对话中检测）
    narration_text = shot.get("narration_segment", "")

    # 优先从对话中检测说话人
    detected_speaker = None
    if characters:
        detected_speaker = _detect_speaker_from_narration(narration_text, characters)

    # 如果检测不到说话人，尝试使用 main_subject
    main_subject = shot.get("main_subject", "").strip()
    speaker_name = detected_speaker or (main_subject if main_subject and len(main_subject) <= 4 else None)

    # 2. 获取动态音色配置
    if voice_registry is None:
        voice_registry = get_registry()

    if speaker_name:
        voice_config = voice_registry.get_voice(speaker_name)
    else:
        # 无说话人信息，使用旁白音色
        voice_config = NARRATOR_VOICE.copy()

    output_path = output_dir / f"shot_{idx}.mp3"

    # 缓存检查：文件存在且大小正常则跳过
    if output_path.exists() and output_path.stat().st_size > 10240:
        print(f"  [CACHE] Shot {idx}: cached ({speaker_name or 'Narrator'})")
        return str(output_path)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # 使用角色专属音色生成
        display_name = speaker_name or "Narrator"
        print(f"  [TTS] Shot {idx} [{display_name}] <- {voice_config['id']}")
        gen_single_tts(text, output_path, provider, voice_config)
        return str(output_path)
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        return None


def gen_all_shot_tts(script: dict, output_dir: Path,
                     voice_registry=None) -> List[str]:
    """为所有分镜生成 TTS 音频.

    Args:
        script: 完整脚本数据
        output_dir: 输出目录
        voice_registry: 音色注册表实例

    Returns:
        成功生成的音频文件路径列表
    """
    shots = script.get("shots", [])
    if not shots:
        print("[WARN] No shots data")
        return []

    # 获取角色列表（用于说话人检测）
    characters = script.get("characters", [])

    # 收集所有角色信息（用于日志）
    if characters:
        char_names = [c.get("identifier", "") for c in characters if c.get("identifier")]
        if char_names:
            print(f"[INFO] Detected characters: {', '.join(sorted(char_names))}")

    output_dir.mkdir(parents=True, exist_ok=True)

    provider = create_provider() if not voice_registry else None
    results = []
    print(f"[INFO] Generating TTS for {len(shots)} shots with dynamic voices...\n")

    for shot in shots:
        path = gen_shot_tts(shot, output_dir, provider, voice_registry, characters)
        if path:
            results.append(path)

    print(f"\n[INFO] Shot TTS done: {len(results)}/{len(shots)} success")
    return results