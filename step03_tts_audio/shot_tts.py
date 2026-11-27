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
from tts.voice_registry import (NARRATOR_ROLE, NARRATOR_VOICE, UNIQUE_VOICE_TIERS,
                                VOICE_TIER_EXTRA, VOICE_TIER_PRIMARY,
                                VoicePoolExhaustedError, declared_voice_tier,
                                is_shared_tier, normalize_voice_tier, get_registry)
from tts.synthesis_job import (AudioArtifact, DurationRejectedError, SpeechUnit,
                               SynthesisError, TtsCandidateContext, finalize_in_place,
                               normalize_unit, partial_path, request_key_for,
                               resolve_applied, synthesize_unit)
from tts.service import provider_capabilities
from shared.paths import SHOT_AUDIO_FILENAME_FMT  # 单镜音频文件名单源: shared/paths.py
from shared.constants import TTS_MAX_RETRIES  # 重试次数单源: shared/constants.py
from shared.narration_voice import narration_role
from shared.audio_identity import (dialogue_speech_text, identity_path, read_identity,
                                   read_signature, sha256_text, speech_signature,
                                   verify_identity)

#: 旧 Sambert 池的单次长度上限（**能力事实**已登记在 `tts.service` 的能力表里；
#: 这里保留常量只作兼容别名，**不再用于任何截断**）
SAMBERT_SINGLE_CALL_LIMIT = 300
MAX_TEXT_LENGTH = SAMBERT_SINGLE_CALL_LIMIT

#: 声线角色（`NARRATOR_ROLE`）单源在 `tts.voice_registry`：没有识别到说话人时用它
#: 的稳定性来代替「统一默认声线」。


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


def resolve_shot_speaker(shot: dict, raw_for_speaker: str, characters: List[dict],
                         plan: dict | None = None) -> dict:
    """单镜对白片段的说话人解析（**唯一入口**，规划与真实合成共用）.

    三种情形必须分开（2026-10-31 复核 P1）："多条对白条目 ⇒ `shot_dialogue_entry`
    返回 None" **不等于**"不会被整体套声线"——旧实现随后还会探测文本前缀，只要整段
    开头有一个能识别的名字，多条对白照样会被归给同一个人。

    规则：

    - **多个对白条目** ⇒ `speaker_ambiguous` 拒收（本包不做多人对白拆分）；
    - **唯一有效条目** ⇒ 用条目的 `speaker`，并做成员校验（角色表为空 ⇒
      `characters_missing`；名字不在表里 ⇒ `speaker_unknown`；与显式前缀不一致 ⇒
      `speaker_conflict`）；
    - **唯一条目但没有 speaker** ⇒ 允许显式前缀（旧稿兼容）；
    - **没有结构化条目** ⇒ 允许显式前缀（旧稿兼容）；
    - 前缀也没有 ⇒ `speaker_unresolved`，不静默变成旁白。

    Returns: `{"role": 角色名, "source": "entry"|"prefix"}`
    """
    if not characters:
        # 没有角色表就没有可比对的身份：既不猜前缀，也不接受结构化名字
        raise SynthesisError(
            "characters_missing",
            "本次输入没有角色表——无法校验说话人身份（空表不接受任何前缀）",
            field_path="characters")
    entries = []
    if plan is not None:
        from step01_script_gen.plan_feasibility import shot_dialogue_entries
        entries = shot_dialogue_entries(shot, plan)
    if len(entries) > 1:
        raise SynthesisError(
            "speaker_ambiguous",
            "镜头 %s 声明了 %d 条对白条目（%s）——单声音单元不能归给其中任一人，"
            "拒绝整体套一个声线" % (shot.get("index"), len(entries),
                                    "、".join("D%s" % e.get("index") for e in entries)),
            field_path="plan_refs")
    # 只认**明确前缀**（X道：/X说：…）：`detect_speaker` 会把"以角色名开头的普通叙述"
    # 也当成发言，那既会误判旧稿，也会跟结构化 speaker 造出假的 speaker_conflict。
    from shared.dialogue import speaker_prefix
    detected = speaker_prefix(raw_for_speaker, characters)
    if entries:
        name = str(entries[0].get("speaker") or "").strip()
        if name:
            known = {str(c.get("identifier") or "") for c in (characters or [])}
            if not known:
                raise SynthesisError(
                    "characters_missing",
                    "对白条目声明说话人 %r，但本次输入没有角色表——身份无法校验，"
                    "拒绝直接采信" % name, field_path="characters")
            if name not in known:
                raise SynthesisError(
                    "speaker_unknown",
                    "对白条目声明的说话人 %r 不在本片角色表（%s）——拒绝按内容猜"
                    % (name, "、".join(sorted(known))), field_path="speaker")
            if detected and detected != name:
                raise SynthesisError(
                    "speaker_conflict",
                    "结构化说话人 %r 与文本前缀解析出的 %r 不一致——拒绝二选一"
                    % (name, detected), field_path="speaker")
            return {"role": name, "source": "entry"}
        if detected:
            return {"role": detected, "source": "prefix"}
        raise SynthesisError(
            "speaker_unresolved",
            "镜头 %s 的对白条目没有 speaker，文本也没有可识别的说话人前缀"
            % shot.get("index"), field_path="speaker")
    if detected:
        return {"role": detected, "source": "prefix"}
    raise SynthesisError(
        "speaker_unresolved",
        "镜头 %s 的对白既没有结构化说话人，也没有可识别的说话人前缀"
        % shot.get("index"), field_path="speaker")


# ---------------------------------------------------------------- 文本提取（单源）

def _dialogue_speech(shot: dict, plan: dict | None, characters=None) -> tuple:
    """该镜**该由对白轨合成**的文本 → `(raw_for_speaker, text)`（薄包装，单源在 shared）.

    判据与实现见 `shared.audio_identity.dialogue_speech_text`：结构化声音条目只取对白
    那一份且与消费侧逐字一致；无条目才回退旧稿标点启发式 + 前缀剥离。
    """
    return dialogue_speech_text(shot, plan, characters)


# ---------------------------------------------------------------- 声线解析

def _caps_of(provider, provider_name: str = ""):
    return provider_capabilities(provider if provider is not None else provider_name,
                                 name=provider_name or None)


def resolve_voice(registry, *, role_id: str, provider_name: str, caps,
                  gender: str | None = None, age_range: str | None = None,
                  tier: str = "", avoid: tuple = ()) -> dict:
    """声线解析（两条路，按 provider 能力事实分流，**不静默回退默认声线**）.

    - provider **没有**独立 speaker 概念（Sambert：模型 ID 就是音色 ID）⇒ 沿用旧
      音色池 `registry.get_voice(...)`，`model_id` 取音色 id（这就是它的请求语义）；
    - provider **有**独立 speaker（新模型）⇒ 走 profile 表：`profile_for` 按
      （作品 + 角色）稳定分配 `speaker`，模型 ID 与音色 ID 分开；非法 speaker 直接抛。

    S04/A1：`tier` 为共享等级（minor/extra）时改走龙套共享池
    （`get_shared_voice` / `shared_profile_for`）——同一桶内可共用声线，但
    **不撞**独占角色与旁白；`avoid` 是同一批分配里同镜/同场不得复用的声线。

    返回 `{"model_id", "speaker", "voice_profile_id", "mapping_version", "tier",
    "legacy_voice": <旧池音色字典或 None>}`。
    """
    mapping_version = str(getattr(tts_config, "MAPPING_VERSION", "v1") or "v1")
    norm_tier = normalize_voice_tier(tier)
    shared = is_shared_tier(norm_tier)
    if not bool(getattr(caps, "supports_speaker", False)):
        # 旧音色池通道（Sambert：模型 ID 就是音色 ID）。注册表能力缺失必须**显式失败**：
        # 2026-10-31 复核——此前这里 `else dict(NARRATOR_VOICE)`，而生效的注册表类恰好
        # 没有 get_voice，于是听香/张十三/杨旭全部落到旁白男声，且没有任何告警。
        if registry is None or not hasattr(registry, "get_voice"):
            raise SynthesisError(
                "voice_registry_unsupported",
                "provider %r 使用旧音色池（模型 ID 即音色 ID），但传入的注册表没有 "
                "get_voice——拒绝静默回退旁白音色" % provider_name,
                field_path="voice_registry")
        if str(role_id or "") == NARRATOR_ROLE:
            # 旁白身份：显式取旁白音色（它本来就该是这一个），不占用角色音色池
            voice = dict(NARRATOR_VOICE)
        elif shared and hasattr(registry, "get_shared_voice"):
            voice = dict(registry.get_shared_voice(
                role_id, gender=gender, age_range=age_range, avoid=tuple(avoid)) or {})
            if not voice.get("id"):
                raise SynthesisError(
                    "voice_unresolved",
                    "龙套角色 %r 没有可用共享音色，拒绝回退旁白" % role_id,
                    field_path="voice")
        else:
            voice = dict(registry.get_voice(role_id, gender, age_range) or {})
            if not voice.get("id"):
                raise SynthesisError(
                    "voice_unresolved",
                    "角色 %r 在旧音色池里没有可用音色（池空或全被占用），拒绝回退旁白"
                    % role_id, field_path="voice")
        voice_id = str(voice.get("id") or "")
        return {"model_id": voice_id, "speaker": "", "legacy_voice": voice,
                "voice_profile_id": "legacy-%s" % (voice_id or "default"),
                "mapping_version": mapping_version,
                "tier": norm_tier or (VOICE_TIER_PRIMARY
                                      if str(role_id) == NARRATOR_ROLE else "")}
    if registry is None or not hasattr(registry, "profile_for"):
        raise SynthesisError(
            "voice_registry_unsupported",
            "provider %r 需要独立 speaker，但传入的注册表不支持 profile_for"
            "（拒绝静默回退统一默认声线）" % provider_name,
            field_path="voice_registry")
    # provider 白名单 + 显式配置的扩容 speaker（`TTS_EXTRA_SPEAKERS`，零调用）
    from tts.voice_registry import legal_speakers_for
    legal = legal_speakers_for(caps)
    if shared and hasattr(registry, "shared_profile_for"):
        profile = registry.shared_profile_for(
            role_id, provider=provider_name, legal_speakers=legal,
            mapping_version=mapping_version, gender=gender, age_range=age_range,
            avoid=tuple(avoid), tier=norm_tier)
    else:
        profile = registry.profile_for(role_id, provider=provider_name,
                                       legal_speakers=legal,
                                       mapping_version=mapping_version,
                                       tier=norm_tier)
    return {"model_id": str(caps.model_id or tts_config.MODEL or ""),
            "speaker": str(profile.get("speaker") or ""),
            "voice_profile_id": str(profile.get("voice_profile_id") or ""),
            "mapping_version": str(profile.get("mapping_version") or mapping_version),
            "tier": norm_tier or str(profile.get("voice_tier") or ""),
            "legacy_voice": None}


# ---------------------------------------------------------------- 角色分级（S04/A1）

def _primary_min_units() -> int:
    """派生为独占角色的最低发言单元数（默认 2；0/非法按 2）。"""
    raw = os.getenv("VOICE_PRIMARY_MIN_UNITS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 2
    return value if value > 0 else 2


def role_visibility(script: dict) -> dict:
    """每个角色**在帧布局里实际出镜**的镜头集合（结构化字段，空镜不算）."""
    out: dict[str, set] = {}
    units = []
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    if hook:
        units.append(hook)
    units.extend(s for s in (script.get("shots") or []) if isinstance(s, dict))
    for shot in units:
        idx = int(shot.get("index", 0) or 0)
        layouts = shot.get("frame_layouts")
        if not isinstance(layouts, dict):
            continue
        for _kind, frames in layouts.items():
            groups = frames if isinstance(frames, list) else [frames]
            for group in groups:
                entries = group if isinstance(group, list) else [group]
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("on_screen") is False:
                        continue
                    name = str(entry.get("name") or "").strip()
                    if name:
                        out.setdefault(name, set()).add(idx)
    return out


def role_shot_sets(script: dict, plan: dict | None = None) -> dict:
    """角色 → 需要被听见/看见的镜头集合（发言镜 ∪ 出镜镜）.

    共享龙套池按「同镜不得撞声线」判定：两个角色在这个集合里有交集，就不能用
    同一条声线（观众会在同一场里同时听到两个一样的声音）。
    """
    shots = {int(s.get("index", 0) or 0): s
             for s in (script.get("shots") or []) if isinstance(s, dict)}
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    units_script = ([hook] if hook else []) + list(shots.values())
    characters = script.get("characters") or []
    out: dict[str, set] = {}
    for shot in units_script:
        try:
            raw, text = _dialogue_speech(shot, plan, characters)
            if raw is None or not text:
                continue
            role = resolve_shot_speaker(shot, raw, characters, plan)["role"]
        except Exception:  # noqa: BLE001 - 分级只做兼容读取，真错误由生成路径显式报
            continue
        role = canonical_character_role(role, characters)
        out.setdefault(role, set()).add(int(shot.get("index", 0) or 0))
    for role, shot_ids in role_visibility(script).items():
        out.setdefault(canonical_character_role(role, characters), set()).update(shot_ids)
    return out


def _has_frame_layout_data(script: dict) -> bool:
    """本次输入是否带可用的帧布局（有结构才谈"只在一镜露面"）."""
    units = []
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    if hook:
        units.append(hook)
    units.extend(s for s in (script.get("shots") or []) if isinstance(s, dict))
    for shot in units:
        layouts = shot.get("frame_layouts")
        if isinstance(layouts, dict) and any(layouts.get(k) for k in layouts):
            return True
    return False


def derive_voice_tiers(script: dict, plan: dict | None = None, *,
                       registry=None, roles: dict | None = None) -> dict:
    """角色 → 分级（确定性；输入声明 > 历史记录 > 本次发言/出镜结构）.

    - 旁白恒为 `primary`（它独占旁白音色，不进入角色池）；
    - 输入显式声明（`characters[].voice_tier`/`voice_pool`/`role_tier`）**最高优先**，
      并覆盖历史记录（人改了声明就是要换口径）；
    - **历史记录其次**：同一角色上一章怎么分级，本章保持不变（跨章音色稳定）；
    - 派生：发言单元数 ≥ `VOICE_PRIMARY_MIN_UNITS`（默认 2）或出镜 ≥ 2 镜 ⇒ primary；
      只有在**输入带帧布局**、且该角色仅发言 1 单元、仅出镜 ≤ 1 镜时才判 extra。
      没有帧布局就不猜"龙套"——保持旧的独占分配，宁可不共享也不误判主配角。
    """
    registry = registry if registry is not None else get_registry()
    characters = script.get("characters") or []
    if roles is None:
        roles = _role_counts_free(script, plan)
    chars_by_id = {}
    for c in characters:
        if not isinstance(c, dict):
            continue
        ident = str(c.get("identifier") or c.get("name") or "").strip()
        if ident:
            chars_by_id[ident] = c
    counts: dict[str, int] = {}
    for role, rec in (roles or {}).items():
        counts[str(role)] = int((rec or {}).get("units") or 0)
    visibility = role_visibility(script)
    has_layouts = _has_frame_layout_data(script)
    all_roles = set(counts) | set(visibility) | {NARRATOR_ROLE}
    threshold = _primary_min_units()
    out: dict = {}
    for role in sorted(all_roles):
        if role == NARRATOR_ROLE:
            out[role] = {"tier": VOICE_TIER_PRIMARY, "reason": "旁白专用音色",
                          "source": "narrator", "speech_units": counts.get(role, 0),
                          "visible_shots": len(visibility.get(role) or ())}
            continue
        known = {}
        if registry is not None and hasattr(registry, "role_tier"):
            known = registry.role_tier(role) or {}
        declared = declared_voice_tier(chars_by_id.get(role) or {})
        if declared:
            out[role] = {"tier": declared, "reason": "输入显式声明",
                          "source": "declared", "speech_units": counts.get(role, 0),
                          "visible_shots": len(visibility.get(role) or ())}
            if registry is not None and hasattr(registry, "record_role_tier"):
                registry.record_role_tier(role, declared, reason="输入显式声明",
                                          source="declared", replace=True)
            continue
        if known.get("tier"):
            out[role] = {"tier": normalize_voice_tier(known["tier"]),
                          "reason": str(known.get("reason") or "历史分级"),
                          "source": str(known.get("source") or "history"),
                          "speech_units": counts.get(role, 0),
                          "visible_shots": len(visibility.get(role) or ())}
            continue
        units_n = counts.get(role, 0)
        shots_n = len(visibility.get(role) or ())
        if has_layouts and units_n <= 1 and shots_n <= 1:
            out[role] = {"tier": VOICE_TIER_EXTRA,
                          "reason": "带帧布局下仅发言 %d 单元且出镜 %d 镜（一次性龙套）"
                                    % (units_n, shots_n),
                          "source": "derived", "speech_units": units_n,
                          "visible_shots": shots_n}
        else:
            out[role] = {"tier": VOICE_TIER_PRIMARY,
                          "reason": ("发言 %d 单元/出镜 %d 镜（≥%d 或 ≥2%s）"
                                     % (units_n, shots_n, threshold,
                                        "" if has_layouts else "；无帧布局不判龙套")),
                          "source": "derived", "speech_units": units_n,
                          "visible_shots": shots_n}
        # 只给**实际发声**的角色记录分级：不出声的角色没有音色，记录它会变成
        # 下一章的"历史分级"，反而锁死后来才开口的重要角色。
        if (counts.get(role, 0) > 0 and registry is not None
                and hasattr(registry, "record_role_tier")):
            registry.record_role_tier(role, out[role]["tier"],
                                      reason=out[role]["reason"], source="derived")
    return out


def plan_role_voices(script: dict, plan: dict | None = None, *,
                     registry=None, provider_name: str = "", caps=None,
                     tiers: dict | None = None, roles: dict | None = None) -> dict:
    """整章一次性分配声线：先独占（旁白+primary/recurring），再共享龙套池.

    顺序是确定性的（独占先排序、共享按桶内顺序），且共享角色的 `avoid` 由
    **同镜/同场**的已分配声线算出——两个会在同一镜被听见的龙套不会拿到同一条声线。
    返回 `{role: {voice fields..., "tier": ..., "tier_reason": ...}}`。
    """
    registry = registry if registry is not None else get_registry()
    caps = caps if caps is not None else provider_capabilities(provider_name or tts_config.PROVIDER)
    name = str(getattr(caps, "provider", "") or provider_name or tts_config.PROVIDER)
    characters = script.get("characters") or []
    if roles is None:
        roles = _role_counts_free(script, plan)
    tiers = tiers if isinstance(tiers, dict) and tiers else derive_voice_tiers(
        script, plan, registry=registry, roles=roles)
    shot_sets = role_shot_sets(script, plan)
    # 分级里没有发言角色的也要有记录（便于报告）；实际分配只针对有发言的角色
    speaking = set(roles or {})
    planned: dict[str, dict] = {}
    ordered = sorted([r for r in speaking if r],
                     key=lambda r: (0 if (tiers.get(r) or {}).get("tier") in UNIQUE_VOICE_TIERS
                                    else 1, r))
    for role in ordered:
        info = tiers.get(role) or {}
        tier = normalize_voice_tier(info.get("tier"))
        gender, age = _detect_speaker_attributes(role, characters)
        avoid = ()
        if is_shared_tier(tier):
            mine = set(shot_sets.get(role) or ())
            avoid = tuple(sorted({
                _voice_key(voice) for other, voice in planned.items()
                if _voice_key(voice) and mine & set(shot_sets.get(other) or ())
            }))
        try:
            voice = resolve_voice(registry, role_id=role, provider_name=name, caps=caps,
                                  gender=gender, age_range=age, tier=tier, avoid=avoid)
        except VoicePoolExhaustedError as exc:
            raise SynthesisError(
                "voice_pool_exhausted",
                "%s（角色 %r，等级 %s）" % (exc, role, tier or "unclassified"),
                field_path="voice",
                detail={"occupied_roles": sorted(exc.occupied),
                        "remaining": int(exc.remaining)}) from exc
        voice = dict(voice)
        voice.setdefault("tier", tier)
        voice["tier_reason"] = str(info.get("reason") or "")
        planned[role] = voice
    return planned


def _voice_key(voice: dict | None) -> str:
    """声线身份（旧池=model_id；新模型=speaker）；空值返回 ""。"""
    if not isinstance(voice, dict):
        return ""
    return str(voice.get("voice_profile_id") or voice.get("speaker")
               or voice.get("model_id") or "")


def _tier_for_standalone(role: str, script: dict, plan: dict | None,
                         registry) -> str:
    """单镜生成（无整章预分配）时的等级回退：仍按同一分级规则算。"""
    info = derive_voice_tiers(script, plan, registry=registry,
                              roles={role: {"units": 1}}) or {}
    return str((info.get(role) or {}).get("tier") or "")


# ---------------------------------------------------------------- 声音单元构造

def _window_of(shot: dict) -> Optional[tuple]:
    dur = float((shot or {}).get("duration_sec") or (shot or {}).get("duration") or 0)
    return (0.0, dur) if dur > 0 else None


def canonical_character_role(role: str, characters) -> str:
    """把称呼/别名归一为 characters 表的规范 identifier（如存在）."""
    name = str(role or "").strip()
    if not name:
        return ""
    for c in characters or []:
        if not isinstance(c, dict):
            continue
        ident = str(c.get("identifier") or c.get("name") or "").strip()
        if not ident:
            continue
        aliases = c.get("aliases") or []
        if name == ident or name in {str(a) for a in aliases}:
            return ident
        amap = c.get("alias_map")
        if isinstance(amap, dict) and str(amap.get(name) or "") == ident:
            return ident
    return name


def dialogue_speech_unit(shot: dict, *, scope: dict, script_revision: str,
                         voice: dict, provider_name: str, text: str,
                         strict: bool = True, voice_role: str = "") -> SpeechUnit:
    """单镜对白 → `SpeechUnit`（身份字段一次给全；缺字段由 normalize_unit 报）."""
    idx = int((shot or {}).get("index", 0) or 0)
    legacy = voice.get("legacy_voice")
    unit = SpeechUnit(
        unit_id="shot-%d" % idx, kind="dialogue", text=text,
        scope=dict(scope), script_revision=str(script_revision),
        source_locator="shots[%d]" % idx, planned_window=_window_of(shot),
        source_index=idx, voice_role=str(voice_role or ""),
        voice_profile_id=str(voice.get("voice_profile_id") or ""),
        provider=str(provider_name), model_id=str(voice.get("model_id") or ""),
        model_revision=str(getattr(tts_config, "MODEL_REVISION", "") or ""),
        speaker=str(voice.get("speaker") or ""),
        instruction="", language="zh",
        rate=float(legacy.get("rate", 1.0)) if legacy else 1.0,
        volume=float(legacy.get("volume")) if legacy and legacy.get("volume") is not None else None,
        format=str(tts_config.CONSUMER_FORMAT or "mp3"),
        sample_rate=int(tts_config.CONSUMER_SAMPLE_RATE or 0) or 24000,
        mapping_version=str(voice.get("mapping_version") or ""),
        params_strict=bool(strict), legacy_voice_signature=dict(legacy) if legacy else None,
        voice_tier=str(voice.get("tier") or ""),
    )
    return unit


def narration_speech_unit(entry_index: int, *, text: str, scope: dict,
                          script_revision: str, voice: dict, provider_name: str,
                          window: Optional[tuple], strict: bool = True,
                          voice_role: str = "") -> SpeechUnit:
    """一条图外旁白 → `SpeechUnit`（一个 N 编号一个音频；跨镜连续播放由窗口给足）."""
    legacy = voice.get("legacy_voice")
    return SpeechUnit(
        unit_id="narration-N%d" % int(entry_index), kind="narration", text=text,
        scope=dict(scope), script_revision=str(script_revision),
        source_locator="narration_lines[%d]" % int(entry_index),
        source_index=int(entry_index), planned_window=window, voice_role=voice_role,
        voice_profile_id=str(voice.get("voice_profile_id") or ""),
        provider=str(provider_name), model_id=str(voice.get("model_id") or ""),
        model_revision=str(getattr(tts_config, "MODEL_REVISION", "") or ""),
        speaker=str(voice.get("speaker") or ""),
        instruction="", language="zh",
        rate=float(legacy.get("rate", 1.0)) if legacy else 1.0,
        volume=float(legacy.get("volume")) if legacy and legacy.get("volume") is not None else None,
        format=str(tts_config.CONSUMER_FORMAT or "mp3"),
        sample_rate=int(tts_config.CONSUMER_SAMPLE_RATE or 0) or 24000,
        mapping_version=str(voice.get("mapping_version") or ""),
        params_strict=bool(strict), legacy_voice_signature=dict(legacy) if legacy else None,
        voice_tier=str(voice.get("tier") or ""),
    )


def _transition_hint(raw) -> tuple[str, float]:
    """把 clip_transition 文本折成 (kind, duration)；只用于窗口扣除重叠.

    step04 的权威解析在 `step04_merge/main._parse_transition`；step03 只需要
    「这个边界重叠多少秒」。非 cut 一律按 fade 计（kind 只影响 step04 的
    xfade 类型，不影响窗口算术），时长缺省 0.5s、夹在 [0.1, 1.5]。
    """
    text = str(raw or "").strip()
    if not text or "硬切" in text or text.lower() in ("cut", "none", "无"):
        return "cut", 0.0
    import re as _re
    match = _re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:s|秒)", text)
    duration = float(match.group(1)) if match else 0.5
    return "fade", max(0.1, min(duration, 1.5))


def _compute_voice_windows(script: dict, shots: list[dict]) -> tuple[dict, float]:
    """每镜在**最终成片时间轴**上的 (start, end)：hook 偏移 + 转场重叠 + 实测时长.

    S18-1：旧实现用裸累加 `duration_sec`，既没有 hook 偏移、也没扣 xfade 重叠。
    step03 据此放行的旁白，到 step04 按真实 clip_windows 验收时会抛
    「旁白比承载片段还长」而整章合并失败。这里与 step04 共用
    `windows_from_legacy`；时长优先取已生成视频的实测值（没有视频时回退
    `duration_sec`，离线单测照常可跑）；时间线非法时回退「累加-前序转场」。
    """
    from shared.paths import SHOT_VIDEO_FILENAME, vimax_working_dir

    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    units = ([hook] if hook else []) + [s for s in shots if isinstance(s, dict)]
    if not units:
        return {}, 0.0

    work_dir = None
    try:
        work_dir = vimax_working_dir()
    except Exception:
        work_dir = None

    durations: list[float] = []
    for unit in units:
        idx = int(unit.get("index", 0))
        actual = 0.0
        if work_dir is not None:
            try:
                path = Path(work_dir) / "shots" / str(idx) / SHOT_VIDEO_FILENAME
                if path.exists():
                    from step04_merge.stretch import get_media_duration
                    actual = float(get_media_duration(path) or 0)
            except Exception:
                actual = 0.0
        durations.append(actual if actual > 0 else float(unit.get("duration_sec") or 0))

    transitions = [_transition_hint(s.get("clip_transition")) for s in shots]
    try:
        from step04_merge.timeline import windows_from_legacy
        windows, total = windows_from_legacy(durations, transitions)
    except Exception:
        windows = []
        acc = 0.0
        for i, duration in enumerate(durations):
            overlap = float(transitions[i - 1][1]) if (i > 0 and transitions) else 0.0
            start = max(0.0, acc - overlap) if i > 0 else 0.0
            end = start + duration
            windows.append((start, end))
            acc = end
        total = acc

    out: dict = {}
    for unit, (start, end) in zip(units, windows):
        out[str(unit.get("index", 0))] = (float(start), float(end))
    return out, float(total)


def narration_subset_entries(plan: dict, shots: list, only_indices=None) -> list:
    """旁白在子集下的**可生成**条目：返回 [(原编号 N, entry), ...].

    编号保持全章 N 序号（不重排、不换身份），只裁剪：
      - 与子集无交集 → 跳过（属于其他镜头）；
      - 部分相交但未完全包含 → 跳过（跨镜旁白不截断）；
      - 完全落在子集内 → 保留。
    """
    from step01_script_gen.plan_feasibility import (broadcast_narration_entries,
                                                    voice_units)
    entries = broadcast_narration_entries(plan)
    if only_indices is None:
        return list(enumerate(entries, 1))
    want = {int(i) for i in only_indices}
    units = {u["index"]: u for u in voice_units(plan, shots) if u["kind"] == "narration"}
    keep = []
    for i, entry in enumerate(entries, 1):
        run = {int(x) for x in ((units.get(i) or {}).get("shot_indexes") or [])}
        if not run or not (run & want):
            continue  # 属于其他镜头：静默跳过
        if not run <= want:
            print("[WARN] 旁白 N%d 跨出本次 --shots 子集（承载 %s），跳过（不截断）"
                  % (i, sorted(run)))
            continue
        keep.append((i, entry))
    return keep


def _role_counts_free(script: dict, plan: dict | None) -> dict:
    """发言单元数（不含旁白）→ 分级用；与 `build_speech_units` 同一提取口径."""
    plan = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    characters = script.get("characters") or []
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    counts: dict[str, dict] = {}
    for shot in ([hook] if hook else []) + shots:
        try:
            raw_for_speaker, text = _dialogue_speech(shot, plan, characters)
            if raw_for_speaker is None or not text:
                continue
            role = resolve_shot_speaker(shot, raw_for_speaker, characters, plan)["role"]
        except Exception:  # noqa: BLE001 - 真错误由 build_speech_units 显式抛
            continue
        role = canonical_character_role(role, characters)
        rec = counts.setdefault(role, {"units": 0})
        rec["units"] += 1
    return counts


def build_speech_units(script: dict, *, scope: dict | None = None,
                       script_revision: str = "", registry=None,
                       provider_name: str = "", only_indices=None,
                       full_shots: list | None = None,
                       plan: dict | None = None) -> list:
    """整章 → `SpeechUnit` 列表（对白 + 旁白）——`plan` / `--plan-only` 与生成共用.

    只做**规划**：不实例化 provider、不调用模型（能力用登记表读数）。

    S04/A1：声线先按整章预分配（`plan_role_voices`）——独占角色（旁白/primary/
    recurring）先占音色，一次性龙套（minor/extra）再走共享桶，因此同一批分配
    与逐镜生成顺序无关，龙套也不会抢走独占角色要用的音色。
    """
    from step01_script_gen.plan_feasibility import (broadcast_narration_entries,
                                                    layout_voice_entries, voice_units)
    plan = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    units_script = ([hook] if hook else []) + shots
    characters = script.get("characters") or []
    scope = dict(scope or current_scope())
    revision = str(script_revision or script_revision_of(script))
    name = str(provider_name or tts_config.PROVIDER)
    caps = provider_capabilities(name)
    entries = broadcast_narration_entries(plan)
    roles = [narration_role(e, characters) for e in entries]
    _subset_shots = full_shots if full_shots is not None else shots
    _kept_narr = {i for i, _e in narration_subset_entries(plan, _subset_shots, only_indices)}
    registry = registry if registry is not None else get_registry()

    # ---- 第一遍：收集发言角色（对白 + 角色旁白）→ 整章分级 ----
    counts = _role_counts_free(script, plan)
    for entry, role in zip(entries, roles):
        norm = role if str(role).strip().lower() != "narrator" else NARRATOR_ROLE
        if norm != NARRATOR_ROLE:
            rec = counts.setdefault(canonical_character_role(norm, characters),
                                    {"units": 0})
            rec["units"] += 1
    tiers = derive_voice_tiers(script, plan, registry=registry, roles=counts)
    planned_voices = plan_role_voices(script, plan, registry=registry,
                                      provider_name=name, caps=caps,
                                      tiers=tiers, roles=counts)

    def _voice_for(role: str) -> dict:
        if role in planned_voices:
            return dict(planned_voices[role])
        # 分级里有但不发言的角色（或预分配遗漏）：按同一规则即时解析
        gender, age = _detect_speaker_attributes(role, characters)
        tier = normalize_voice_tier((tiers.get(role) or {}).get("tier"))
        voice = resolve_voice(registry, role_id=role, provider_name=name, caps=caps,
                              gender=gender, age_range=age, tier=tier)
        voice["tier_reason"] = str((tiers.get(role) or {}).get("reason") or "")
        return voice

    out: list = []
    for shot in units_script:
        raw_for_speaker, text = _dialogue_speech(shot, plan, characters)
        if raw_for_speaker is None or not text:
            continue
        role = resolve_shot_speaker(shot, raw_for_speaker, characters, plan)["role"]
        role = canonical_character_role(role, characters)
        out.append(dialogue_speech_unit(shot, scope=scope, script_revision=revision,
                                        voice=_voice_for(role), provider_name=name,
                                        text=text, voice_role=role))

    if entries:
        _win, _ = _compute_voice_windows(script, shots)
        tracks, _problems = layout_voice_entries(voice_units(plan, shots), _win)
        _track_by_id = {t["id"]: t for t in tracks if t.get("kind") == "narration"}
        for i, entry in enumerate(entries, 1):
            if i not in _kept_narr:
                continue
            role = roles[i - 1]
            if str(role).strip().lower() == "narrator":
                role = NARRATOR_ROLE
            else:
                role = canonical_character_role(role, characters)
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            run = [str(s) for s in
                   next((u["shot_indexes"] for u in voice_units(plan, shots)
                         if u["kind"] == "narration" and u["index"] == i), [])]
            tr = _track_by_id.get("N%d" % i) or {}
            end = _win.get(run[-1], (0.0, 0.0))[1] if run else 0.0
            start = float(tr.get("start") or 0.0)
            window = (start, float(end)) if run and float(end) > start else None
            out.append(narration_speech_unit(i, text=text, scope=scope, script_revision=revision,
                                             voice=_voice_for(role), provider_name=name,
                                             window=window,
                                             voice_role=role if "voice_role" in entry else ""))
    return out


# ---------------------------------------------------------------- 缓存判定

def check_voice_uniqueness(units, registry, characters) -> dict:
    """角色 ↔ 音色一致性（S04/A1 按等级分口径）.

    - **独占等级**（旁白 / primary / recurring / 未标等级）仍要求 1 角色 ↔ 1 音色，
      并且不与旁白、也不与任何其他角色撞音；
    - **共享等级**（minor / extra）允许同一条龙套声线被多个角色复用（这是龙套池
      的本意），但仍然要求**同一角色始终同一条声线**，且不得占用旁白或独占角色的音色。
    """
    role_voice: dict[str, set] = {}
    voice_role: dict[str, set] = {}
    narrator_voices: set = set()
    shared_roles: set = set()
    for u in units or []:
        kind = str(getattr(u, "kind", "") or "")
        role = str(getattr(u, "voice_role", "") or "")
        if kind == "narration" and not role:
            role = NARRATOR_ROLE
        if role and role != NARRATOR_ROLE:
            role = canonical_character_role(role, characters)
        voice = str(getattr(u, "voice_profile_id", "") or
                    getattr(u, "model_id", "") or getattr(u, "speaker", "") or "")
        if not role or not voice:
            continue
        tier = normalize_voice_tier(getattr(u, "voice_tier", ""))
        if is_shared_tier(tier) and role != NARRATOR_ROLE:
            shared_roles.add(role)
        role_voice.setdefault(role, set()).add(voice)
        voice_role.setdefault(voice, set()).add(role)
        if role == NARRATOR_ROLE:
            narrator_voices.add(voice)
    conflicts = []
    for role, voices in sorted(role_voice.items()):
        if len(voices) > 1:
            conflicts.append({"code": "role_multiple_voices", "role": role,
                              "voices": sorted(voices),
                              "tier": ("shared" if role in shared_roles else "unique")})
    for voice, roles in sorted(voice_role.items()):
        if len(roles) <= 1:
            continue
        unique_roles = sorted(r for r in roles
                              if r != NARRATOR_ROLE and r not in shared_roles)
        if unique_roles:
            conflicts.append({"code": "voice_multiple_roles", "voice": voice,
                              "roles": sorted(roles),
                              "unique_roles": unique_roles})
    for voice in sorted(narrator_voices):
        chars = sorted(r for r in voice_role.get(voice, set()) if r != NARRATOR_ROLE)
        if chars:
            conflicts.append({"code": "narrator_voice_collision",
                              "voice": voice, "roles": chars})
    # 共享等级不得占用独占角色的音色（上面已按 unique_roles 判，这里再显式记一条
    # 便于报告区分「独占撞独占」与「龙套撞独占」）
    for role in sorted(shared_roles):
        for voice in sorted(role_voice.get(role, set())):
            owners = sorted(r for r in voice_role.get(voice, set())
                            if r != role and r != NARRATOR_ROLE
                            and r not in shared_roles)
            if owners:
                conflicts.append({"code": "shared_voice_collides_unique", "role": role,
                                  "voice": voice, "unique_roles": owners})
    return {"ok": not conflicts, "conflicts": conflicts,
            "roles": {r: sorted(v) for r, v in sorted(role_voice.items())},
            "shared_roles": sorted(shared_roles)}


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
