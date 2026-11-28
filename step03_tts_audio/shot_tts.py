"""step03_tts_audio.shot_tts - 逐镜对白 / 图外旁白的 TTS 生成（统一走 tts.synthesis_job）.

2026-10-03 计划 §T1/§T2/§T2a 之后的接线：

- **一切合成**（串行/异步、对白/旁白）都调用
  :func:`tts.synthesis_job.synthesize_unit`——声音单元的身份、无损分段、PCM 拼接、
  时长窗口守卫、身份侧车与候选事务只有一份实现；
- **`gen_single_tts` 不再截断**：旧实现按标点截到 300 字，旁白被截就不再是原文那句话。
  现在超长正文交给 provider 能力上限分段，拼回**逐字符等于完整正文**；
- 文本提取单源：`shared.audio_identity.dialogue_speech_text`（返回
  `(raw_for_speaker, text)`——身份摘要用 `text`、说话人识别用 `raw_for_speaker`）；
- 旧 `.voice` 边车**只在旧请求兼容**时写/读（step04 现有消费者仍按它核对），
  新 profile/参数一律写 `audio_identity.v2` 侧车。
"""
import sys
from pathlib import Path
import os
import asyncio
import random
import hashlib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


import json
from tts import create_provider
from tts.config import config as tts_config
from tts.voice_registry import (NARRATOR_ROLE, get_registry)
from tts.synthesis_job import (AudioArtifact, DurationRejectedError, SpeechUnit,
                               SynthesisError, TtsCandidateContext, finalize_in_place,
                               normalize_unit, partial_path, request_key_for,
                               resolve_applied, synthesize_unit)
from tts.service import provider_capabilities
from shared.paths import SHOT_AUDIO_FILENAME_FMT  # 单镜音频文件名单源: shared/paths.py
from shared.constants import TTS_MAX_RETRIES  # 重试次数单源: shared/constants.py
from shared.narration_voice import narration_role
from shared.audio_identity import (identity_path, read_identity,
                                   read_signature, sha256_text, speech_signature,
                                   verify_identity)

#: 旧 Sambert 池的单次长度上限（**能力事实**已登记在 `tts.service` 的能力表里；
#: 这里保留常量只作兼容别名，**不再用于任何截断**）
SAMBERT_SINGLE_CALL_LIMIT = 300
MAX_TEXT_LENGTH = SAMBERT_SINGLE_CALL_LIMIT

#: 声线角色（`NARRATOR_ROLE`）单源在 `tts.voice_registry`：没有识别到说话人时用它
#: 的稳定性来代替「统一默认声线」。


# ---------------------------------------------------------------------------
# S08（2026-11-28）：拆分后兼容 re-export（父模块名字/调用点保持不变）
# ---------------------------------------------------------------------------
from step03_tts_audio.parts.speakers import (  # noqa: F401
    _compute_voice_windows, _detect_speaker_attributes, _dialogue_speech,
    _has_frame_layout_data, _primary_min_units, _role_counts_free,
    _tier_for_standalone, _transition_hint, _voice_key, _window_of,
    canonical_character_role, derive_voice_tiers, dialogue_speech_unit,
    narration_speech_unit, narration_subset_entries, plan_role_voices,
    resolve_shot_speaker, resolve_voice, role_shot_sets, role_visibility,
)
from step03_tts_audio.parts.units import (  # noqa: F401
    build_speech_units, check_voice_uniqueness,
)


def _tts_cache_sig(text: str, voice_config: dict) -> str:
    """旧 TTS 缓存身份：voice + 精确发声文本（**只服务旧请求兼容**）.

    2026-09-30 复核第 8 轮：实现搬到 `shared/audio_identity`——消费侧（step04）
    合并前要用同一份签名核对（缓存检查 ≠ 消费检查），两处各写一份必然漂移。
    """
    return speech_signature(text, voice_config)


def _validate_tts_duration(output_path: Path, shot: dict) -> float:
    """对白轨时长核验（保留的兼容入口；统一 unit 层也在窗口上核一遍）.

    Fail if TTS is unreadable or exceeds the shot's planned duration.
    """
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


def load_script(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 运行范围与来源身份

def current_scope(chapter: int | None = None) -> dict:
    """本次运行的 scope（进 SpeechUnit 与候选 manifest；与视频候选 manifest 交叉核对）."""
    from shared.paths import book_index, book_name, chapter_index
    return {"chapter": int(chapter if chapter is not None else chapter_index()),
            "book_index": book_index(), "book_name": book_name()}


def script_revision_of(script: dict) -> str:
    """脚本的来源身份：优先用显式 revision，否则用**内容**的规范摘要（不伪造 ID）."""
    for key in ("revision", "script_revision"):
        raw = (script or {}).get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    payload = json.dumps(script or {}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return sha256_text(payload)


# ---------------------------------------------------------------- 说话人识别





# ---------------------------------------------------------------- 文本提取（单源）



# ---------------------------------------------------------------- 声线解析

def _caps_of(provider, provider_name: str = ""):
    return provider_capabilities(provider if provider is not None else provider_name,
                                 name=provider_name or None)




# ---------------------------------------------------------------- 角色分级（S04/A1）

















# ---------------------------------------------------------------- 声音单元构造



















# ---------------------------------------------------------------- 缓存判定



def _cache_hit(output_path: Path, unit: SpeechUnit, request_key: str) -> bool:
    """缓存命中判据（**新 profile/参数不复用旧 `.voice`**，§T2a）.

    - 媒体存在且体量正常，且
      - `audio_identity.v2` 侧车与本次 `request_key` 完全一致（新路径唯一判据），或
      - **旧请求兼容**：unit 带旧音色签名时，`.voice` 与它逐字一致。
    """
    p = Path(output_path)
    if not (p.exists() and p.stat().st_size > 10240):
        return False
    if read_identity(p) is not None:
        return verify_identity(p, {"request_key": request_key}) == ""
    legacy = unit.legacy_voice_signature
    if unit.voice_role:
        return False  # legacy signatures cannot prove explicit ownership
    if legacy is not None:
        return read_signature(p) == _tts_cache_sig(unit.text, legacy)
    return False


def _drop_cached(p: Path) -> None:
    for target in (Path(p), identity_path(p), Path(p).with_suffix(".voice")):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- 合成（统一入口）

def _unit_artifact(unit: SpeechUnit, provider, candidate_context,
                   output_path: Optional[Path] = None) -> AudioArtifact:
    """统一合成本体（候选上下文给了就落候选 attempt；否则写到 `output_path`）."""
    return synthesize_unit(unit, candidate_context, provider=provider,
                           output_path=output_path)


def gen_single_tts(text: str, output_path: Path, provider=None,
                   voice_config: Optional[dict] = None, *,
                   unit: Optional[SpeechUnit] = None,
                   candidate_context: Optional[TtsCandidateContext] = None) -> str:
    """生成单个 TTS 音频（**不再截断**：超长正文按 provider 能力无损分段）.

    Args:
        text: 要转换的文本
        output_path: 输出文件路径
        provider: TTS 提供商实例
        voice_config: 旧音色配置 {id, rate, volume, ...}（旧请求兼容；新路径请传 unit）
        unit: 完整声音单元（新路径；给了就忽略 text/voice_config 的身份部分）
        candidate_context: 候选上下文（给了就落候选 attempt 并过事务）
    """
    if unit is None:
        name = tts_config.PROVIDER
        caps = provider_capabilities(provider if provider is not None else name, name=name)
        legacy = dict(voice_config or {})
        unit = SpeechUnit(
            unit_id="legacy-%s" % hashlib.md5(str(output_path).encode("utf-8")).hexdigest()[:12],
            kind="dialogue", text=str(text), scope=current_scope(),
            script_revision="unversioned", source_locator="legacy_inplace",
            voice_profile_id="legacy-%s" % (legacy.get("id") or "default"),
            provider=name, model_id=str(legacy.get("id") or tts_config.MODEL),
            model_revision=str(getattr(tts_config, "MODEL_REVISION", "") or ""),
            speaker="", instruction="", language="zh",
            rate=float(legacy.get("rate", 1.0) or 1.0),
            volume=float(legacy["volume"]) if legacy.get("volume") is not None else None,
            format=str(tts_config.CONSUMER_FORMAT or "mp3"),
            sample_rate=int(tts_config.CONSUMER_SAMPLE_RATE or 0) or 24000,
            mapping_version=str(getattr(tts_config, "MAPPING_VERSION", "v1") or "v1"),
            params_strict=False,                     # 旧请求语义：适配，不拒收
            legacy_voice_signature=legacy or None)
        if not bool(getattr(caps, "supports_speaker", False)):
            # 旧 provider（Sambert）：模型 ID 就是音色 ID
            unit.model_id = str(legacy.get("id") or tts_config.MODEL)
        else:
            unit.speaker = str(legacy.get("id") or "")
    unit = normalize_unit(unit)
    artifact = _unit_artifact(
        unit, provider, candidate_context,
        None if candidate_context is not None else partial_path(output_path))
    if candidate_context is None:
        artifact = finalize_in_place(artifact, Path(output_path))
    size_kb = artifact.bytes / 10.24
    print("  [OK] %s (%.1f KB, %d 段)" % (Path(artifact.path).name, size_kb,
                                          max(1, len(artifact.segments))))
    return str(artifact.path)


async def _gen_single_tts_async(unit: SpeechUnit, provider,
                                candidate_context: Optional[TtsCandidateContext] = None,
                                output_path: Optional[Path] = None) -> AudioArtifact:
    """异步包装（限流 + 429 退避重试）；合成本体仍是**同一个** `synthesize_unit`."""
    async with TTS_SEMAPHORE:
        retries = 0
        last_error = None
        while retries < MAX_RETRIES:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, _unit_artifact, unit, provider, candidate_context,
                    output_path)
            except Exception as e:
                error_str = str(e)
                # `429`：旧写法用子串匹配，会把 ffmpeg 的 `rc=4294967274` 之类
                # 也当成限流（实测误导：真实错误被三次退避掩盖）。只认独立的 429 标记。
                import re as _re
                if (_re.search(r"429", error_str)
                        or "RateLimit" in error_str
                        or "too many requests" in error_str.lower()):
                    wait_time = (2 ** retries) + random.uniform(0, 1)
                    print(f"  [RATE LIMIT] Retrying in {wait_time:.1f}s... (attempt {retries+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                    retries += 1
                    last_error = e
                else:
                    raise e
        raise RuntimeError(f"TTS failed after {MAX_RETRIES} retries: {last_error}")


def _prepare_shot_unit(shot: dict, *, output_dir: Path, voice_registry, characters,
                       plan, scope, script_revision, provider, candidate_context,
                       role_voices: dict | None = None, script: dict | None = None):
    """单镜对白 → `(unit, output_path, display_name)`；不该出声时返回 `(None, None, None)`.

    `role_voices`（整章预分配）优先：同一章里所有镜头的声线必须来自同一次分配，
    否则龙套共享桶与独占分配会随生成顺序漂移。
    """
    idx = int(shot.get("index", 0) or 0)
    raw_for_speaker, text = _dialogue_speech(shot, plan, characters)
    if raw_for_speaker is None or not text:
        return None, None, None
    role = resolve_shot_speaker(shot, raw_for_speaker, characters, plan)["role"]
    gender, age = _detect_speaker_attributes(role, characters)
    configured = str(tts_config.PROVIDER)
    caps = provider_capabilities(provider if provider is not None else configured,
                                 name=configured)
    name = str(caps.provider or configured)   # 与真正拿去合成的 provider 一致
    registry = voice_registry if voice_registry is not None else get_registry()
    role = canonical_character_role(role, characters)
    voice = (role_voices or {}).get(role)
    if voice is None:
        tier = _tier_for_standalone(role, script or {"characters": characters},
                                    plan, registry)
        voice = resolve_voice(registry, role_id=role, provider_name=name, caps=caps,
                              gender=gender, age_range=age, tier=tier)
    unit = dialogue_speech_unit(shot, scope=scope, script_revision=script_revision,
                                voice=voice, provider_name=name, text=text,
                                strict=candidate_context is not None, voice_role=role)
    if candidate_context is not None:
        output_path = candidate_context.for_unit(unit.unit_id, unit.format)
    else:
        output_path = Path(output_dir) / SHOT_AUDIO_FILENAME_FMT.format(idx)
    return unit, output_path, role


def gen_shot_tts(shot: dict, output_dir: Path, provider=None,
                 voice_registry=None, characters: List[dict] = None,
                 plan: dict | None = None, *, scope: dict | None = None,
                 script_revision: str = "",
                 candidate_context: Optional[TtsCandidateContext] = None,
                 collected: Optional[list] = None,
                 role_voices: dict | None = None,
                 script: dict | None = None) -> Optional[str]:
    """为单个分镜生成 TTS 音频（**只合成对白那一份**）.

    `role_voices` 是整章预分配的声线（`plan_role_voices` 的返回值）；不给时按
    同一分级规则即时解析（单镜工具/测试的兼容路径）。

    Returns: 生成的音频文件路径，失败返回 None（失败原因在日志里；不伪造成功）。
    """
    idx = shot.get("index", 0)
    raw_text = str(shot.get("narration_segment", "") or "").strip()
    if not raw_text:
        print(f"  [SKIP] Shot {idx}: no narration_segment")
        return None
    scope = dict(scope or current_scope())
    revision = str(script_revision or "unversioned")
    try:
        unit, output_path, display_name = _prepare_shot_unit(
            shot, output_dir=Path(output_dir), voice_registry=voice_registry,
            characters=characters, plan=plan, scope=scope, script_revision=revision,
            provider=provider, candidate_context=candidate_context,
            role_voices=role_voices, script=script)
    except SynthesisError as exc:
        print(f"  [FAIL] Shot {idx}: {exc}")
        return None
    if unit is None:
        print(f"  [SKIP] Shot {idx}: narration only (no dialogue), no TTS")
        return None

    caps = provider_capabilities(provider if provider is not None else unit.provider,
                                 name=unit.provider)
    applied = resolve_applied(unit, caps)
    key = request_key_for(unit, applied=applied, provider=caps.provider or unit.provider,
                          model_revision=caps.model_revision or unit.model_revision)
    if _cache_hit(output_path, unit, key):
        try:
            _validate_tts_duration(output_path, shot)
            print(f"  [CACHE] Shot {idx}: cached ({display_name})")
            return str(output_path)
        except Exception as _cache_err:
            print(f"  [CACHE] Shot {idx}: cached audio invalid, regenerating: {_cache_err}")
            _drop_cached(output_path)

    try:
        if candidate_context is None:
            Path(output_dir).mkdir(parents=True, exist_ok=True)   # 候选模式不建生产目录
        print("  [TTS] Shot %s [%s] <- %s%s" % (
            idx, display_name, unit.model_id or unit.speaker,
            (" / %s" % unit.speaker) if unit.speaker else ""))
        artifact = _unit_artifact(unit, provider, candidate_context,
                                  output_path if candidate_context is not None
                                  else partial_path(output_path))
        if candidate_context is None:
            artifact = finalize_in_place(artifact, output_path)   # 成功后才替换旧音频
        if collected is not None:
            collected.append(artifact)
        return str(artifact.path)
    except Exception as e:
        # 失败**保留基线**（不删旧音频：盘上那份只有在生成成功后才被替换）
        print(f"  [FAIL] Shot {idx}: {e}")
        if candidate_context is None:
            partial_path(output_path).unlink(missing_ok=True)
        return None


async def _gen_shot_tts_async(shot: dict, output_dir: Path, provider,
                              voice_registry, characters: List[dict],
                              plan: dict | None = None, *, scope: dict | None = None,
                              script_revision: str = "",
                              candidate_context: Optional[TtsCandidateContext] = None,
                              collected: Optional[list] = None,
                              role_voices: dict | None = None,
                              script: dict | None = None
                              ) -> Optional[str]:
    """异步版本的单分镜 TTS 生成（与串行**同一判据、同一实现**）."""
    idx = shot.get("index", 0)
    raw_text = str(shot.get("narration_segment", "") or "").strip()
    if not raw_text:
        return None
    scope = dict(scope or current_scope())
    revision = str(script_revision or "unversioned")
    try:
        unit, output_path, display_name = _prepare_shot_unit(
            shot, output_dir=Path(output_dir), voice_registry=voice_registry,
            characters=characters, plan=plan, scope=scope, script_revision=revision,
            provider=provider, candidate_context=candidate_context,
            role_voices=role_voices, script=script)
    except SynthesisError as exc:
        print(f"  [FAIL] Shot {idx}: {exc}")
        return None
    if unit is None:
        return None

    caps = provider_capabilities(provider if provider is not None else unit.provider,
                                 name=unit.provider)
    applied = resolve_applied(unit, caps)
    key = request_key_for(unit, applied=applied, provider=caps.provider or unit.provider,
                          model_revision=caps.model_revision or unit.model_revision)
    if _cache_hit(output_path, unit, key):
        try:
            _validate_tts_duration(output_path, shot)
            print(f"  [CACHE] Shot {idx}: cached ({display_name})")
            return str(output_path)
        except Exception as _cache_err:
            print(f"  [CACHE] Shot {idx}: cached audio invalid, regenerating: {_cache_err}")
            _drop_cached(output_path)

    try:
        if candidate_context is None:
            Path(output_dir).mkdir(parents=True, exist_ok=True)   # 候选模式不建生产目录
        print("  [TTS] Shot %s [%s] <- %s%s" % (
            idx, display_name, unit.model_id or unit.speaker,
            (" / %s" % unit.speaker) if unit.speaker else ""))
        artifact = await _gen_single_tts_async(
            unit, provider, candidate_context,
            output_path if candidate_context is not None else partial_path(output_path))
        if candidate_context is None:
            artifact = finalize_in_place(artifact, output_path)
        if collected is not None:
            collected.append(artifact)
        return str(artifact.path)
    except Exception as e:
        print(f"  [FAIL] Shot {idx}: {e}")
        if candidate_context is None:
            partial_path(output_path).unlink(missing_ok=True)
        return None


def gen_all_shot_tts(script: dict, output_dir: Path,
                     voice_registry=None, use_async: bool = False, *,
                     scope: dict | None = None, script_revision: str = "",
                     candidate_context: Optional[TtsCandidateContext] = None,
                     provider=None, collected: Optional[list] = None,
                     plan: dict | None = None) -> List[str]:
    """为所有分镜生成 TTS 音频（对白轨）.

    Returns: 成功生成的音频文件路径列表（失败项**不**计入，由调用方按自己的判据停下）。
    """
    shots = script.get("shots", [])
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    units = ([hook] if hook else []) + list(shots)
    _plan = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
    _plan = _plan if isinstance(_plan, dict) else None
    if not units:
        print("[WARN] No shots data")
        return []

    characters = script.get("characters", [])
    if characters:
        char_names = [c.get("identifier", "") for c in characters if c.get("identifier")]
        if char_names:
            print(f"[INFO] Detected characters: {', '.join(sorted(char_names))}")

    scope = dict(scope or current_scope())
    revision = str(script_revision or script_revision_of(script))
    output_dir = Path(output_dir)
    if candidate_context is None:
        output_dir.mkdir(parents=True, exist_ok=True)
    provider = provider if provider is not None else create_provider()
    results = []
    # S04/A1：整章只做**一次**声线分配（独占先、龙套共享后），逐镜不得各自分配。
    _caps = provider_capabilities(provider, name=str(getattr(provider, "PROVIDER_NAME", "")
                                                    or tts_config.PROVIDER))
    try:
        role_voices = plan_role_voices(script, _plan, registry=voice_registry,
                                       provider_name=str(_caps.provider or tts_config.PROVIDER),
                                       caps=_caps)
    except SynthesisError as exc:
        # 声线池满/非法 speaker：整章显式失败，绝不逐镜静默退回旁白或撞音
        print(f"[ERROR] 声线分配失败（{getattr(exc, 'code', 'voice')}）: {exc}")
        raise
    print(f"[INFO] 声线分级: " + ", ".join(
        "%s=%s(%s)" % (r, (v.get("tier") or "?"), (v.get("tier_reason") or "")[:18])
        for r, v in sorted(role_voices.items())))
    print(f"[INFO] Generating TTS for {len(units)} units (hook + shots) with dynamic voices...\n")

    if use_async:
        async def run_async():
            tasks = [
                _gen_shot_tts_async(unit, output_dir, provider, voice_registry,
                                    characters, plan=_plan, scope=scope,
                                    script_revision=revision,
                                    candidate_context=candidate_context,
                                    collected=collected, role_voices=role_voices,
                                    script=script)
                for unit in units
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_async())
        results = [r for r in results if r]  # 过滤 None
    else:
        for unit in units:
            path = gen_shot_tts(unit, output_dir, provider, voice_registry, characters,
                                plan=_plan, scope=scope, script_revision=revision,
                                candidate_context=candidate_context,
                                collected=collected, role_voices=role_voices,
                                script=script)
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
            raise DurationRejectedError(
                "旁白 N%d 超出承载它的镜头片段时长: actual=%.2fs 可用=%.2fs"
                "（镜头 %s）——请减少旁白内容或调整镜头，**禁止截句**"
                % (index, actual, avail, run),
                available_sec=avail, duration_sec=actual)
    return actual


def gen_narration_tts(script: dict, output_dir: Path, provider=None, *,
                      scope: dict | None = None, script_revision: str = "",
                      voice_registry=None,
                      candidate_context: Optional[TtsCandidateContext] = None,
                      collected: Optional[list] = None,
                      only_indices=None, full_shots: list | None = None,
                      plan: dict | None = None) -> List[str]:
    """为**图外旁白**生成独立 TTS 音轨（2026-09-30 复核第 7 轮第 3 项）.

    与 `gen_shot_tts` 的三点不同（都是**有意**的）：

      ① 音色来自条目 voice_role；缺席沿用叙述者，不从正文猜人物；
      ② **一条旁白一个文件**（`audio/narration_<NN>.mp3`，N 编号）——旁白跨镜连续播放，
         按「每镜一个文件」写会把一条旁白切碎；
      ③ 时长上限是**承载它的连续镜头片段**总长，不是单镜时长。

    2026-10-03（§T2a）：**移除「超过单段 300 字就硬失败」**——超长旁白改走
    「provider 能力分段 + 承载窗口验收」，窗口不够才是 `duration_rejected`，
    且**保留基线**（不写盘、不删旧音轨）。
    """
    from shared.paths import NARRATION_AUDIO_FILENAME_FMT
    from step01_script_gen.plan_feasibility import (broadcast_narration_entries,
                                                    layout_voice_entries,
                                                    voice_units)
    plan = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    entries = broadcast_narration_entries(plan)
    if not entries:
        return []
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    roles = [narration_role(e, script.get("characters") or []) for e in entries]
    _by_index = {str(s.get("index")): s for s in shots}
    _units = {u["index"]: u for u in voice_units(plan, shots)
              if u["kind"] == "narration"}
    # 子集：只生成**完全落在本次镜头内**的旁白（他镜跳过、跨镜跳过、编号不变）。
    _subset_shots = full_shots if full_shots is not None else shots
    _kept = {i for i, _e in narration_subset_entries(plan, _subset_shots, only_indices)}
    # 计划时长窗口（与 step04 用真实 clip_windows 的那份排版**同一规则**）：
    # 同镜多条声音按序、不重叠；排不进就是计划层该重选的事，这里先显式失败。
    _win, _ = _compute_voice_windows(script, shots)
    # ① 逐条**结构**检查（不进 TTS 就能判、且能指出真因的）——先跑。
    #    长度不再在这里判：超长由 provider 能力分段解决，讲不完由 duration 守卫判。
    for _i, _e in enumerate(entries, 1):
        if _i not in _kept:
            continue
        _t = str(_e.get("text") or "").strip()
        if not _t:
            continue
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
    _tracks, _layout_problems = layout_voice_entries(voice_units(plan, shots), _win)
    import re as _re
    _layout_problems = [p for p in _layout_problems if p.startswith("N")
                        and int(_re.match(r"N(\d+)", p).group(1)) in _kept]
    if _layout_problems:
        # 「窗口不足」在旁白侧**统一**报 `duration_rejected`（§T2a）：估时排不进与实测
        # 超窗是同一件事的两种读数，不该给消费方两种错误码。
        raise DurationRejectedError(
            "图外旁白排不进承载片段（不截句）：" + "；".join(_layout_problems[:3]),
            layout_problems=list(_layout_problems[:3]))
    _track_by_id = {t["id"]: t for t in _tracks if t["kind"] == "narration"}
    provider = provider if provider is not None else create_provider()
    output_dir = Path(output_dir)
    if candidate_context is None:
        output_dir.mkdir(parents=True, exist_ok=True)
    scope = dict(scope or current_scope())
    revision = str(script_revision or script_revision_of(script))
    configured = str(tts_config.PROVIDER)
    caps = provider_capabilities(provider if provider is not None else configured,
                                 name=configured)
    name = str(caps.provider or configured)
    registry = voice_registry if voice_registry is not None else get_registry()
    # S04/A1：与对白轨共用**同一次整章声线分配**（角色旁白不得另分一条声线）
    _counts = _role_counts_free(script, plan)
    for _r in roles:
        _norm = NARRATOR_ROLE if str(_r).strip().lower() == "narrator" else \
            canonical_character_role(_r, script.get("characters") or [])
        if _norm != NARRATOR_ROLE:
            _counts.setdefault(_norm, {"units": 0})["units"] += 1
    try:
        _role_voices = plan_role_voices(script, plan, registry=registry,
                                        provider_name=name, caps=caps, roles=_counts)
    except SynthesisError as exc:
        print(f"[ERROR] 旁白声线分配失败（{getattr(exc, 'code', 'voice')}）: {exc}")
        raise

    out: List[str] = []
    for _i, _e in enumerate(entries, 1):
        role = roles[_i - 1]
        role = (NARRATOR_ROLE if str(role).strip().lower() == "narrator"
                else canonical_character_role(role, script.get("characters") or []))
        gender, age = _detect_speaker_attributes(role, script.get("characters") or [])
        voice = dict(_role_voices.get(role) or {})
        if not voice:
            voice = resolve_voice(registry, role_id=role, provider_name=name, caps=caps,
                                  gender=gender, age_range=age,
                                  tier=(derive_voice_tiers(
                                      script, plan, registry=registry,
                                      roles={role: {"units": 1}}).get(role) or {}).get("tier", ""))
        text = str(_e.get("text") or "").strip()
        if not text:
            continue
        run = [str(s) for s in (_units.get(_i) or {}).get("shot_indexes") or []]
        _tr = _track_by_id.get("N%d" % _i) or {}
        _end = _win.get(run[-1], (0.0, 0.0))[1]
        start = float(_tr.get("start") or 0.0)
        avail = max(0.0, float(_end) - start)
        unit = narration_speech_unit(_i, text=text, scope=scope, script_revision=revision,
                                     voice=voice, provider_name=name,
                                     window=(start, float(_end)) if run else None,
                                     strict=candidate_context is not None,
                                     voice_role=role if "voice_role" in _e else "")
        if candidate_context is not None:
            output_path = candidate_context.for_unit(unit.unit_id, unit.format)
        else:
            output_path = output_dir / NARRATION_AUDIO_FILENAME_FMT.format(_i)
        applied = resolve_applied(unit, caps)
        key = request_key_for(unit, applied=applied, provider=caps.provider or unit.provider,
                              model_revision=caps.model_revision or unit.model_revision)
        if _cache_hit(output_path, unit, key):
            try:
                _check_narration_duration(output_path, avail, _i, run)
                print("  [CACHE] Narration N%d: cached" % _i)
                out.append(str(output_path))
                continue
            except Exception as _cache_err:
                print("  [CACHE] Narration N%d: cached audio invalid, regenerating: %s"
                      % (_i, _cache_err))
                _drop_cached(output_path)
        try:
            print("  [TTS] Narration N%d <- %s（%d 镜连续，可用 %.2fs，%d 字）"
                  % (_i, unit.model_id or unit.speaker, len(run), avail, len(text)))
            artifact = _unit_artifact(
                unit, provider, candidate_context,
                None if candidate_context is not None else partial_path(output_path))
            if candidate_context is None:
                artifact = finalize_in_place(artifact, output_path)
            if collected is not None:
                collected.append(artifact)
            if candidate_context is None:
                # 统一 unit 层已按窗口核过；这里再用**实际文件**复核一遍（同一判据）
                _check_narration_duration(Path(artifact.path), avail, _i, run)
            out.append(str(artifact.path))
        except Exception as exc:
            # 旁白缺音轨 = 成片里没人念（准则 7：不静默降级为"没有旁白"）；
            # 但盘上**旧音轨保持原样**（不先删旧缓存再生成，§T2）
            print("  [FAIL] Narration N%d: %s" % (_i, exc))
            if candidate_context is None:
                partial_path(output_path).unlink(missing_ok=True)
            raise
    return out
