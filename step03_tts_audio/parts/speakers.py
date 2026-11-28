# -*- coding: utf-8 -*-
"""S08: `step03_tts_audio.shot_tts` 的 speakers 职责（2026-11-28 拆分）.

父模块保留同步/异步合成循环与缓存；本模块只放说话人/声线与单元构造，
被调用方替换的函数在调用点经 `_pc` 解析，导入期不回导父模块。
"""
from pathlib import Path
import os
from tts.config import config as tts_config
from tts.pronunciation_lexicon import apply_tts_text
from tts.voice_registry import (NARRATOR_ROLE, NARRATOR_VOICE, UNIQUE_VOICE_TIERS,
                                VOICE_TIER_EXTRA, VOICE_TIER_PRIMARY,
                                VoicePoolExhaustedError, declared_voice_tier,
                                is_shared_tier, normalize_voice_tier, get_registry)
from tts.synthesis_job import (SpeechUnit,
                               SynthesisError)
from shared.audio_identity import (dialogue_speech_text)

from typing import List, Optional

_PARENT = "step03_tts_audio.shot_tts"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


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

def _dialogue_speech(shot: dict, plan: dict | None, characters=None) -> tuple:
    """该镜**该由对白轨合成**的文本 → `(raw_for_speaker, text)`（薄包装，单源在 shared）.

    判据与实现见 `shared.audio_identity.dialogue_speech_text`：结构化声音条目只取对白
    那一份且与消费侧逐字一致；无条目才回退旧稿标点启发式 + 前缀剥离。
    """
    return dialogue_speech_text(shot, plan, characters)

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
            raw, text = _pc("_dialogue_speech", shot, plan, characters)
            if raw is None or not text:
                continue
            role = _pc("resolve_shot_speaker", shot, raw, characters, plan)["role"]
        except Exception:  # noqa: BLE001 - 分级只做兼容读取，真错误由生成路径显式报
            continue
        role = _pc("canonical_character_role", role, characters)
        out.setdefault(role, set()).add(int(shot.get("index", 0) or 0))
    for role, shot_ids in role_visibility(script).items():
        out.setdefault(_pc("canonical_character_role", role, characters), set()).update(shot_ids)
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
    - **默认合同是 strict**（`VOICE_TIER_POLICY` 未设或=strict）：所有角色一律 primary，
      声明/历史/派生都不改变"一人一音色"的旧合同；只有显式 `VOICE_TIER_POLICY=tiered`
      才启用分级共享。这是 2026-11-27 复核要求的默认回退。
    """
    from tts.voice_registry import tier_policy
    registry = registry if registry is not None else get_registry()
    characters = script.get("characters") or []
    if tier_policy() != "tiered":
        # 严格合同：不读声明/历史、不派生，全部独占（旁白仍走专用音色）。
        strict_tiers: dict = {}
        for c in characters:
            if not isinstance(c, dict):
                continue
            ident = str(c.get("identifier") or c.get("name") or "").strip()
            if ident:
                strict_tiers[ident] = {
                    "tier": VOICE_TIER_PRIMARY, "reason": "strict 合同（默认）",
                    "source": "strict_policy",
                    "speech_units": int(((roles or {}).get(ident) or {}).get("units") or 0),
                }
        strict_tiers[NARRATOR_ROLE] = {
            "tier": VOICE_TIER_PRIMARY, "reason": "旁白专用音色", "source": "narrator",
        }
        return strict_tiers
    if roles is None:
        roles = _pc("_role_counts_free", script, plan)
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
    caps = caps if caps is not None else _pc("provider_capabilities", provider_name or tts_config.PROVIDER)
    name = str(getattr(caps, "provider", "") or provider_name or tts_config.PROVIDER)
    characters = script.get("characters") or []
    if roles is None:
        roles = _pc("_role_counts_free", script, plan)
    tiers = tiers if isinstance(tiers, dict) and tiers else _pc("derive_voice_tiers", 
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
            voice = _pc("resolve_voice", registry, role_id=role, provider_name=name, caps=caps,
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
    info = _pc("derive_voice_tiers", script, plan, registry=registry,
                              roles={role: {"units": 1}}) or {}
    return str((info.get(role) or {}).get("tier") or "")

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
        tts_text=apply_tts_text(text),
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
        tts_text=apply_tts_text(text),
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

def _window_of(shot: dict) -> Optional[tuple]:
    dur = float((shot or {}).get("duration_sec") or (shot or {}).get("duration") or 0)
    return (0.0, dur) if dur > 0 else None

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
            raw_for_speaker, text = _pc("_dialogue_speech", shot, plan, characters)
            if raw_for_speaker is None or not text:
                continue
            role = _pc("resolve_shot_speaker", shot, raw_for_speaker, characters, plan)["role"]
        except Exception:  # noqa: BLE001 - 真错误由 build_speech_units 显式抛
            continue
        role = _pc("canonical_character_role", role, characters)
        rec = counts.setdefault(role, {"units": 0})
        rec["units"] += 1
    return counts
