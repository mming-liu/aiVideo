"""step03_tts_audio.shot_tts - Per-shot TTS generation module"""
import sys
from pathlib import Path
import os
import asyncio
import time
import random

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


import json
from tts import create_provider, TTSRequest
from tts.config import config as tts_config
from tts.voice_registry import get_registry, NARRATOR_VOICE, AVAILABLE_VOICES


def _has_dialogue(text: str) -> bool:
    """检测文本是否包含对话（引号或全角冒号）。判定逻辑统一在 shared/dialogue.py。"""
    from shared.dialogue import has_dialogue
    return has_dialogue(text)


from typing import Optional, List


# 并发限流：百炼免费版 QPS=2
TTS_SEMAPHORE = asyncio.Semaphore(2)
MAX_RETRIES = 3
MAX_TEXT_LENGTH = 300  # 单个 TTS 文本最大字符数


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _detect_speaker_attributes(character_name: str, characters: List[dict]) -> tuple:
    """根据角色名查找性别和年龄段。

    Args:
        character_name: 角色名
        characters: 角色列表

    Returns:
        (gender, age_range) 元组， 可能为 (None, None)
    """
    if not character_name or not characters:
        return None, None

    for char in characters:
        if char.get("identifier") == character_name:
            gender = char.get("gender", "").lower()
            age = char.get("age_range", "").lower()
            # 验证有效性
            if gender in ("male", "female"):
                return gender, age if age in ("young", "middle", "old") else None
    return None, None


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

    # 对话格式匹配 - 增强版正则，支持动作+说话混合
    import re
    dialogue_patterns = [
        # 匹配 "XXX道：" / "XXX说：" / "XXX问道" 等，0-4个中文字符的动作描写
        r"^([一-龥]{1,4})(?:[^一-龥]{0,4})?(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂|低声道|低声说|低声问道|低声笑道|笑道|冷笑道|急道|忙道|冷声道|朗声道|轻声道)[:：]",
        # 备用简化模式
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
    # 文本长度检查
    if len(text) > MAX_TEXT_LENGTH:
        print(f"  [WARN] Text too long ({len(text)} chars), truncating to {MAX_TEXT_LENGTH}")
        text = text[:MAX_TEXT_LENGTH]

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


async def _gen_single_tts_async(text: str, output_path: Path, provider,
                                voice_config: Optional[dict] = None) -> str:
    """异步包装的 TTS 生成函数，带限流和重试机制."""
    async with TTS_SEMAPHORE:
        retries = 0
        last_error = None

        while retries < MAX_RETRIES:
            try:
                # 同步转异步执行
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, gen_single_tts, text, output_path, provider, voice_config
                )
            except Exception as e:
                error_str = str(e)
                # 限流错误：429 或 RateLimit
                if "429" in error_str or "RateLimit" in error_str or "too many requests" in error_str.lower():
                    wait_time = (2 ** retries) + random.uniform(0, 1)
                    print(f"  [RATE LIMIT] Retrying in {wait_time:.1f}s... (attempt {retries+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                    retries += 1
                    last_error = e
                else:
                    # 其他错误直接抛出
                    raise e

        raise RuntimeError(f"TTS failed after {MAX_RETRIES} retries: {last_error}")


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

    # v6.6: 只有对话才生成 TTS，旁白（纯描述）不生成
    if not _has_dialogue(text):
        print(f"  [SKIP] Shot {idx}: narration only (no dialogue), no TTS")
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
    # 如果检测不到说话人，尝试使用 main_subject
    main_subject = shot.get("main_subject", "").strip()
    speaker_name = detected_speaker or (main_subject if main_subject and len(main_subject) <= 4 else None)

    # 2. 获取角色属性（性别、年龄段）
    gender, age_range = None, None
    if speaker_name and characters:
        gender, age_range = _detect_speaker_attributes(speaker_name, characters)

    # 3. 获取动态音色配置
    if voice_registry is None:
        voice_registry = get_registry()

    if speaker_name:
        voice_config = voice_registry.get_voice(speaker_name, gender, age_range)
    # 缓存检查：文件存在且大小正常且音色匹配则跳过
    if output_path.exists() and output_path.stat().st_size > 10240:
        voice_meta = output_path.with_suffix(".voice")
        cached_voice = voice_meta.read_text(encoding="utf-8").strip() if voice_meta.exists() else ""
        if cached_voice == voice_config.get("id", ""):
            print(f"  [CACHE] Shot {idx}: cached ({speaker_name or 'Narrator'})")
            return str(output_path)
        print(f"  [CACHE] Shot {idx}: voice changed ({cached_voice or 'none'} -> {voice_config.get('id', '')}), regenerating")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # 使用角色专属音色生成
        display_name = speaker_name or "Narrator"
        print(f"  [TTS] Shot {idx} [{display_name}] <- {voice_config['id']}")
        gen_single_tts(text, output_path, provider, voice_config)
        # 写入音色元数据，供缓存检查比对（防止 step02b 默认音色音频阻塞多角色生成）
        output_path.with_suffix(".voice").write_text(voice_config.get("id", ""), encoding="utf-8")
        return str(output_path)
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        return None


async def _gen_shot_tts_async(shot: dict, output_dir: Path, provider,
                              voice_registry, characters: List[dict]) -> Optional[str]:
    """异步版本的单分镜 TTS 生成."""
    idx = shot.get("index", 0)
    text = shot.get("narration_segment", "").strip()
    if not text:
        return None

    # 1. 识别说话人
    detected_speaker = None
    if characters:
        detected_speaker = _detect_speaker_from_narration(text, characters)

    main_subject = shot.get("main_subject", "").strip()
    speaker_name = detected_speaker or (main_subject if main_subject and len(main_subject) <= 4 else None)

    # 2. 获取角色属性（性别、年龄段）
    gender, age_range = None, None
    if speaker_name and characters:
        gender, age_range = _detect_speaker_attributes(speaker_name, characters)

    # 3. 获取音色
    if speaker_name:
        voice_Config = voice_registry.get_voice(speaker_name, gender, age_range)
    else:
        voice_config = NARRATOR_VOICE.copy()

    output_path = output_dir / f"shot_{idx}.mp3"

    # 缓存检查（含音色比对）
    if output_path.exists() and output_path.stat().st_size > 10240:
        voice_meta = output_path.with_suffix(".voice")
        cached_voice = voice_meta.read_text(encoding="utf-8").strip() if voice_meta.exists() else ""
        if cached_voice == voice_config.get("id", ""):
            print(f"  [CACHE] Shot {idx}: cached ({speaker_name or 'Narrator'})")
            return str(output_path)
        print(f"  [CACHE] Shot {idx}: voice changed ({cached_voice or 'none'} -> {voice_config.get('id', '')}), regenerating")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        display_name = speaker_name or "Narrator"
        print(f"  [TTS] Shot {idx} [{display_name}] <- {voice_config['id']}")
        result = await _gen_single_tts_async(text, output_path, provider, voice_config)
        output_path.with_suffix(".voice").write_text(voice_config.get("id", ""), encoding="utf-8")
        return result
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        return None


def gen_all_shot_tts(script: dict, output_dir: Path,
                     voice_registry=None, use_async: bool = False) -> List[str]:
    """为所有分镜生成 TTS 音频.

    Args:
        script: 完整脚本数据
        output_dir: 输出目录
        voice_registry: 音色注册表实例
        use_async: 是否使用异步并发模式（默认 False 保持向后兼容）

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

    provider = create_provider()
    results = []
    print(f"[INFO] Generating TTS for {len(shots)} shots with dynamic voices...\n")

    if use_async:
        # 异步并发模式：利用限流信号量控制并发
        async def run_async():
            tasks = [
                _gen_shot_tts_async(shot, output_dir, provider, voice_registry, characters)
                for shot in shots
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_async())
        results = [r for r in results if r]  # 过滤 None
    else:
        # 原有串行模式（保持向后兼容）
        for shot in shots:
            path = gen_shot_tts(shot, output_dir, provider, voice_registry, characters)
            if path:
                results.append(path)

    print(f"\n[INFO] Shot TTS done: {len(results)}/{len(shots)} success")
    return results