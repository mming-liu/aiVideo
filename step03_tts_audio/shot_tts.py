"""step03_tts_audio.shot_tts - Per-shot TTS generation module"""
import sys
from pathlib import Path
import os
import asyncio
import time
import random
import hashlib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


import json
from tts import create_provider, TTSRequest
from tts.config import config as tts_config
from tts.voice_registry import get_registry, NARRATOR_VOICE, AVAILABLE_VOICES
from shared.paths import SHOT_AUDIO_FILENAME_FMT  # 单镜音频文件名单源: shared/paths.py
from shared.constants import TTS_MAX_RETRIES  # 重试次数单源: shared/constants.py
from shared.dialogue import strip_speaker_prefix



def _tts_cache_sig(text: str, voice_config: dict) -> str:
    """TTS cache identity: voice + exact spoken text (not only voice id).

    2026-09-30 复核第 8 轮：实现搬到 `shared/audio_identity`——**消费侧（step04）
    合并前要用同一份签名核对**（缓存检查 ≠ 消费检查），两处各写一份必然漂移。
    """
    from shared.audio_identity import speech_signature
    return speech_signature(text, voice_config)



def _validate_tts_duration(output_path: Path, shot: dict) -> float:
    """Fail if TTS is unreadable or exceeds the shot's planned duration."""
    from step04_merge.stretch import get_media_duration
    actual = float(get_media_duration(output_path) or 0)
    if actual <= 0.2:
        raise RuntimeError(f"TTS 音频无效或时长探测失败: {output_path}")
    expected = float(shot.get("duration_sec") or shot.get("duration") or 0)
    if expected > 0:
        tolerance = max(0.05, expected * 0.02)
        if actual > expected + tolerance:
            raise RuntimeError(
                f"TTS 超出镜头时长: actual={actual:.2f}s expected={expected:.2f}s "
                f"tolerance={tolerance:.2f}s。请改台词/停顿/镜头，禁止合并时截句。"
            )
    return actual


def _has_dialogue(text: str) -> bool:
    """检测文本是否包含对话（引号或全角冒号）。判定逻辑统一在 shared/dialogue.py。"""
    from shared.dialogue import has_dialogue
    return has_dialogue(text)


from typing import Optional, List


# 并发限流：百炼免费版 QPS=2（可用 TTS_CONCURRENCY env 覆盖）
TTS_CONCURRENCY = max(int(os.getenv("TTS_CONCURRENCY", "2")), 1)
TTS_SEMAPHORE = asyncio.Semaphore(TTS_CONCURRENCY)
MAX_RETRIES = TTS_MAX_RETRIES
# 单段 TTS 文本上限：sambert 系 300 字/段。注意与 tts/dashscope_qwen3.py 的
# DASHSCOPE_TTS_MAX_CHARS=600（qwen3-tts 上限）是两个不同模型的上限，勿混用。
MAX_TEXT_LENGTH = 300


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
    # 文本长度检查：超长按句末标点安全截断（拦腰截断会语义残缺、
    # TTS 停在半句，v6.27 修复）。找不到标点才退回硬截断。
    if len(text) > MAX_TEXT_LENGTH:
        _window = text[:MAX_TEXT_LENGTH]
        _cut = max(_window.rfind(p) for p in "。！？；…\n")
        if _cut >= int(MAX_TEXT_LENGTH * 0.5):
            text = _window[: _cut + 1]
        else:
            text = _window
        print(f"  [WARN] Text too long, truncated to {len(text)} chars at punctuation")

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
        # #20 修复 2026-08-14：把 voice_registry 分配的音色 id 传给 provider
        # （此前 TTSRequest 无 voice_id 字段，qwen3 等 provider 恒用硬编码默认）
        voice_id=voice_config.get("id", "") if voice_config else "",
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


def _dialogue_speech(shot: dict, plan: dict | None, characters=None) -> tuple:
    """该镜**该由对白轨合成**的文本 → `(raw_for_speaker, text)`；不该合成时返回 `(None, None)`.

    复核第 8 轮 P1：「纯旁白和『对白＋旁白混合文本』都需要按结构化声音条目分流，
    不能再把整段 `narration_segment` 当对白文本」——串行与异步共用本函数，判据只有一份。

    - 计划**有**声音条目 ⇒ 按 `shot_voice_parts` 分流：只取对白那一份；空 ⇒ 本镜没有
      对白轨（纯旁白由独立旁白音轨负责）。**这一支不再二次 `strip_speaker_prefix`**：
      分片结果就是最终文本，必须与消费侧（step04 `shot_voice_parts(...)["dialogue"]`）
      **逐字一致**，否则签名核对会把正确的音轨判成「与当前文本不符」；
    - 计划**没有**声音条目（旧稿/无契约）⇒ 回退历史口径（标点启发式 + 前缀剥离）。
    """
    from step01_script_gen.plan_feasibility import shot_voice_parts
    raw_text = str(shot.get("narration_segment", "") or "").strip()
    if not raw_text:
        return None, None
    parts = shot_voice_parts(shot, plan)
    if not parts["entries"]:
        if not _has_dialogue(raw_text):
            return None, None
        _t = strip_speaker_prefix(raw_text, characters)
        return raw_text, _t
    if not parts["dialogue"].strip():
        return None, None
    if not parts["split_ok"]:
        print("  [WARN] Shot %s: 声音分流未成序（条目文本没能在本镜文本里按序定位），"
              "按整段对白处理" % shot.get("index", 0))
    return (parts["prefix"] or "") + parts["dialogue"].strip(), parts["dialogue"].strip()


def gen_shot_tts(shot: dict, output_dir: Path, provider=None,
                 voice_registry=None, characters: List[dict] = None,
                 plan: dict | None = None) -> Optional[str]:
    """为单个分镜生成 TTS 音频（**只合成对白那一份**）.

    Args:
        shot: 分镜数据，包含 index, narration_segment, main_subject 等字段
        output_dir: 输出目录
        provider: TTS 提供商实例
        voice_registry: 音色注册表实例，None 时自动创建
        characters: 角色列表，优先用于检测说话人
        plan: 章节计划（`chapter_outline`）——有声音条目时按结构化条目分流

    Returns:
        生成的音频文件路径，失败返回 None
    """
    idx = shot.get("index", 0)
    raw_text = shot.get("narration_segment", "").strip()
    if not raw_text:
        print(f"  [SKIP] Shot {idx}: no narration_segment")
        return None

    # 只有**对白**才进对白轨（v6.6 的判据 + 复核第 8 轮的结构化分流）
    narration_text, _dlg_text = _dialogue_speech(shot, plan, characters)
    if narration_text is None:
        print(f"  [SKIP] Shot {idx}: narration only (no dialogue), no TTS")
        return None

    # 1. 识别说话人角色（优先从对话中检测）
    # `_dlg_text` 已是最终文本（结构化分支不二次剥前缀）——签名必须与消费侧逐字一致
    text = _dlg_text if _dlg_text else strip_speaker_prefix(narration_text, characters)

    # 优先从对话中检测说话人
    detected_speaker = None
    if characters:
        detected_speaker = _detect_speaker_from_narration(narration_text, characters)

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
    else:
        voice_config = NARRATOR_VOICE.copy()

    output_path = output_dir / SHOT_AUDIO_FILENAME_FMT.format(idx)

    # 缓存检查：文件存在且大小正常且音色匹配则跳过
    if output_path.exists() and output_path.stat().st_size > 10240:
        voice_meta = output_path.with_suffix(".voice")
        cached_sig = voice_meta.read_text(encoding="utf-8").strip() if voice_meta.exists() else ""
        wanted_sig = _tts_cache_sig(text, voice_config)
        if cached_sig == wanted_sig:
            try:
                _validate_tts_duration(output_path, shot)
                print(f"  [CACHE] Shot {idx}: cached ({speaker_name or 'Narrator'})")
                return str(output_path)
            except Exception as _cache_err:
                print(f"  [CACHE] Shot {idx}: cached audio invalid, regenerating: {_cache_err}")
                output_path.unlink(missing_ok=True)
                voice_meta.unlink(missing_ok=True)
        print(f"  [CACHE] Shot {idx}: text/voice changed, regenerating")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # 使用角色专属音色生成
        display_name = speaker_name or "Narrator"
        print(f"  [TTS] Shot {idx} [{display_name}] <- {voice_config['id']}")
        gen_single_tts(text, output_path, provider, voice_config)
        _validate_tts_duration(output_path, shot)
        # 写入音色元数据，供缓存检查比对（防止 step02b 默认音色音频阻塞多角色生成）
        output_path.with_suffix(".voice").write_text(_tts_cache_sig(text, voice_config), encoding="utf-8")
        return str(output_path)
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        try:
            output_path.unlink(missing_ok=True)
            output_path.with_suffix(".voice").unlink(missing_ok=True)
        except OSError:
            pass
        return None


async def _gen_shot_tts_async(shot: dict, output_dir: Path, provider,
                              voice_registry, characters: List[dict],
                              plan: dict | None = None) -> Optional[str]:
    """异步版本的单分镜 TTS 生成."""
    idx = shot.get("index", 0)
    raw_text = shot.get("narration_segment", "").strip()
    if not raw_text:
        return None
    # 只有**对白**才进对白轨（与串行版本**同一判据**：结构化声音条目分流）。
    # 旧异步版本**没有**对白筛选：纯旁白镜会用角色音色发起合成，随后独立旁白轨
    # 再合成一次，tts_mix 还会消费那个逐镜文件（复核第 8 轮 P1）。
    _speech, _dlg_text = _dialogue_speech(shot, plan, characters)
    if _speech is None:
        return None
    raw_text = _speech
    text = _dlg_text if _dlg_text else strip_speaker_prefix(raw_text, characters)
    if not text:
        return None

    # 1. 识别说话人（在清洗前识别，避免把说话人前缀一起删掉）
    detected_speaker = None
    if characters:
        detected_speaker = _detect_speaker_from_narration(raw_text, characters)

    main_subject = shot.get("main_subject", "").strip()
    speaker_name = detected_speaker or (main_subject if main_subject and len(main_subject) <= 4 else None)

    # 2. 获取角色属性（性别、年龄段）
    gender, age_range = None, None
    if speaker_name and characters:
        gender, age_range = _detect_speaker_attributes(speaker_name, characters)

    # 3. 获取音色
    if speaker_name:
        voice_config = voice_registry.get_voice(speaker_name, gender, age_range)
    else:
        voice_config = NARRATOR_VOICE.copy()

    output_path = output_dir / SHOT_AUDIO_FILENAME_FMT.format(idx)

    # 缓存检查（含音色比对）
    if output_path.exists() and output_path.stat().st_size > 10240:
        voice_meta = output_path.with_suffix(".voice")
        cached_sig = voice_meta.read_text(encoding="utf-8").strip() if voice_meta.exists() else ""
        wanted_sig = _tts_cache_sig(text, voice_config)
        if cached_sig == wanted_sig:
            try:
                _validate_tts_duration(output_path, shot)
                print(f"  [CACHE] Shot {idx}: cached ({speaker_name or 'Narrator'})")
                return str(output_path)
            except Exception as _cache_err:
                print(f"  [CACHE] Shot {idx}: cached audio invalid, regenerating: {_cache_err}")
                output_path.unlink(missing_ok=True)
                voice_meta.unlink(missing_ok=True)
        print(f"  [CACHE] Shot {idx}: text/voice changed, regenerating")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        display_name = speaker_name or "Narrator"
        print(f"  [TTS] Shot {idx} [{display_name}] <- {voice_config['id']}")
        result = await _gen_single_tts_async(text, output_path, provider, voice_config)
        _validate_tts_duration(output_path, shot)
        output_path.with_suffix(".voice").write_text(_tts_cache_sig(text, voice_config), encoding="utf-8")
        return result
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        try:
            output_path.unlink(missing_ok=True)
            output_path.with_suffix(".voice").unlink(missing_ok=True)
        except OSError:
            pass
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
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    units = ([hook] if hook else []) + list(shots)
    _plan = script.get("chapter_outline")
    _plan = _plan if isinstance(_plan, dict) else None
    if not units:
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
    print(f"[INFO] Generating TTS for {len(units)} units (hook + shots) with dynamic voices...\n")

    if use_async:
        # 异步并发模式：利用限流信号量控制并发
        async def run_async():
            tasks = [
                _gen_shot_tts_async(unit, output_dir, provider, voice_registry,
                                    characters, plan=_plan)
                for unit in units
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_async())
        results = [r for r in results if r]  # 过滤 None
    else:
        # 原有串行模式（保持向后兼容）
        for unit in units:
            path = gen_shot_tts(unit, output_dir, provider, voice_registry, characters,
                                plan=_plan)
            if path:
                results.append(path)

    print(f"\n[INFO] TTS done: {len(results)}/{len(units)} success")
    return results


def _check_narration_duration(output_path: Path, avail: float, index: int,
                              run: list) -> float:
    """旁白音轨时长核验：不超过**承载它的连续镜头片段**的总时长（禁止截句）.

    与 `_validate_tts_duration`（单镜对白）的差别只在「上限怎么算」：对白的上限是
    它所在那一镜的时长；旁白可以**跨镜连续播放**，上限是那一段连续镜头的总时长。
    """
    from step04_merge.stretch import get_media_duration
    actual = float(get_media_duration(output_path) or 0)
    if actual <= 0.2:
        raise RuntimeError("旁白 N%d 音频无效或时长探测失败: %s" % (index, output_path))
    if avail > 0:
        tolerance = max(0.05, avail * 0.02)
        if actual > avail + tolerance:
            raise RuntimeError(
                "旁白 N%d 超出承载它的镜头片段时长: actual=%.2fs 可用=%.2fs"
                "（镜头 %s）——请减少旁白内容或调整镜头，**禁止截句**"
                % (index, actual, avail, run))
    return actual


def gen_narration_tts(script: dict, output_dir: Path, provider=None) -> List[str]:
    """为**图外旁白**生成独立 TTS 音轨（2026-09-30 复核第 7 轮第 3 项）.

    与 `gen_shot_tts` 的三点不同（都是**有意**的）：

      ① **固定旁白音色** `NARRATOR_VOICE`：旁白不是角色说的话——不做说话人识别、
         不查 `voice_registry`（把旁白文本喂进角色音色库会污染后续章节的音色分配）；
      ② **一条旁白一个文件**（`audio/narration_<NN>.mp3`，N 编号）——旁白跨镜连续播放，
         按「每镜一个文件」写会把一条旁白切碎；
      ③ 时长上限是**承载它的连续镜头片段**总长，不是单镜时长。

    落点（哪几镜、起止时间）不在这里算：由 `step01.plan_feasibility.voice_units`
    统一给出（与生成验收同一判据），本函数只做「够不够时间讲完」的核验。
    `gen_shot_tts` 跳过的「纯叙述镜」不再等于「没人念」——这条音轨就是念它的地方。
    """
    from shared.paths import NARRATION_AUDIO_FILENAME_FMT
    from step01_script_gen.plan_feasibility import (layout_voice_entries,
                                                    plan_narration_entries,
                                                    voice_units)
    plan = script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    entries = plan_narration_entries(plan)
    if not entries:
        return []
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    _by_index = {str(s.get("index")): s for s in shots}
    _units = {u["index"]: u for u in voice_units(plan, shots)
              if u["kind"] == "narration"}
    # 计划时长窗口（与 step04 用真实 clip_windows 的那份排版**同一规则**）：
    # 同镜多条声音按序、不重叠；排不进就是计划层该重选的事，这里先显式失败。
    _win: dict = {}
    _acc = 0.0
    for _s in shots:
        _d = float(_s.get("duration_sec") or 0)
        _win[str(_s.get("index"))] = (_acc, _acc + _d)
        _acc += _d
    # ① 逐条**结构**检查（不进 TTS 就能判、且能指出真因的）——先跑，
    #    免得"旁白超长"被后面的排版检查报成"排不进承载片段"。
    for _i, _e in enumerate(entries, 1):
        _t = str(_e.get("text") or "").strip()
        if not _t:
            continue
        if len(_t) > MAX_TEXT_LENGTH:
            # 准则 7：`gen_single_tts` 对超长文本会**按标点截断**——旁白截断就不再是
            # 原文那句话了。宁可显式失败，交回规划层换更短的原文句。
            raise RuntimeError(
                "旁白 N%d 长 %d 字，超过单段 TTS 上限 %d 字——旁白必须是原文的完整"
                "句子，不许截断讲：%s" % (_i, len(_t), MAX_TEXT_LENGTH, _t[:24]))
        if not list((_units.get(_i) or {}).get("shot_indexes") or []):
            raise RuntimeError(
                "旁白 N%d 在成稿里**没有连续落点**（承载它的节拍没有被镜头声明，或"
                "这些镜头的旁白拼不出这条旁白）——旁白必须有地方讲：%s"
                % (_i, _t[:24]))
        # **防重复发声**（复核第 8 轮确认的形态）：分流不成序时对白轨拿到的是整段文本，
        # 若旁白正文落在这一段里，它会被对白轨念一遍、旁白轨再念一遍 ⇒ 宁可显式失败。
        from step01_script_gen.plan_feasibility import (_source_norm, shot_voice_parts)
        for _sid in (_units.get(_i) or {}).get("shot_indexes") or []:
            _sh = _by_index.get(str(_sid))
            if not _sh:
                continue
            _p = shot_voice_parts(_sh, plan)
            if (_p["entries"] and not _p["split_ok"] and _p["dialogue"].strip()
                    and _source_norm(_t) in _source_norm(_p["dialogue"])):
                raise RuntimeError(
                    "旁白 N%d 的正文落在了镜头 %s 的**对白轨**文本里（该镜的声音分流未成序，"
                    "整段被当作对白）——合成会与独立旁白轨**重复发声**。请修正该镜的旁白"
                    "文本（让对白与旁白各自能按序定位）后重跑：%s"
                    % (_i, _sid, _t[:24]))
    # ② **排版**：同镜多条声音按序、不重叠、核可用时长（与 step04 同一规则）
    # 排版喂**全部**声音条目（对白 + 旁白）：只喂旁白会让「对白在前」这条规则失效，
    # 同镜里旁白仍从镜头起点开始、与对白轨叠加（视角扫描实测）。
    _tracks, _layout_problems = layout_voice_entries(voice_units(plan, shots), _win)
    _layout_problems = [p for p in _layout_problems if p.startswith("N")]
    if _layout_problems:
        raise RuntimeError("图外旁白排不进承载片段（不截句）：" + "；".join(_layout_problems[:3]))
    _track_by_id = {t["id"]: t for t in _tracks if t["kind"] == "narration"}
    if provider is None:
        provider = create_provider()
    output_dir.mkdir(parents=True, exist_ok=True)
    out: List[str] = []
    for _i, _e in enumerate(entries, 1):
        text = str(_e.get("text") or "").strip()
        if not text:
            continue
        run = list((_units.get(_i) or {}).get("shot_indexes") or [])
        _tr = _track_by_id.get("N%d" % _i) or {}
        _end = _win.get(run[-1], (0.0, 0.0))[1]
        avail = max(0.0, float(_end) - float(_tr.get("start") or 0.0))
        output_path = Path(output_dir) / NARRATION_AUDIO_FILENAME_FMT.format(_i)
        voice_meta = output_path.with_suffix(".voice")
        _sig = _tts_cache_sig(text, NARRATOR_VOICE)
        if output_path.exists() and output_path.stat().st_size > 10240:
            _cached = (voice_meta.read_text(encoding="utf-8").strip()
                       if voice_meta.exists() else "")
            if _cached == _sig:
                try:
                    _check_narration_duration(output_path, avail, _i, run)
                    print("  [CACHE] Narration N%d: cached" % _i)
                    out.append(str(output_path))
                    continue
                except Exception as _cache_err:
                    print("  [CACHE] Narration N%d: cached audio invalid, regenerating: %s"
                          % (_i, _cache_err))
                    output_path.unlink(missing_ok=True)
                    voice_meta.unlink(missing_ok=True)
            print("  [CACHE] Narration N%d: text/voice changed, regenerating" % _i)
        try:
            print("  [TTS] Narration N%d <- %s（%d 镜连续，可用 %.2fs）"
                  % (_i, NARRATOR_VOICE.get("id", ""), len(run), avail))
            gen_single_tts(text, output_path, provider, NARRATOR_VOICE)
            _check_narration_duration(output_path, avail, _i, run)
            voice_meta.write_text(_sig, encoding="utf-8")
            out.append(str(output_path))
        except Exception as exc:
            # 旁白缺音轨 = 成片里没人念（准则 7：不静默降级为"没有旁白"）
            print("  [FAIL] Narration N%d: %s" % (_i, exc))
            try:
                output_path.unlink(missing_ok=True)
                voice_meta.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return out
