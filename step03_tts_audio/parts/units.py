# -*- coding: utf-8 -*-
"""S08: `step03_tts_audio.shot_tts` 的 units 职责（2026-11-28 拆分）.

父模块保留同步/异步合成循环与缓存；本模块只放说话人/声线与单元构造，
被调用方替换的函数在调用点经 `_pc` 解析，导入期不回导父模块。
"""
from tts.config import config as tts_config
from tts.voice_registry import (NARRATOR_ROLE, is_shared_tier, normalize_voice_tier, get_registry)
from shared.narration_voice import narration_role


from step03_tts_audio.parts.speakers import _detect_speaker_attributes

_PARENT = "step03_tts_audio.shot_tts"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


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
    scope = dict(scope or _pc("current_scope", ))
    revision = str(script_revision or _pc("script_revision_of", script))
    name = str(provider_name or tts_config.PROVIDER)
    caps = _pc("provider_capabilities", name)
    entries = broadcast_narration_entries(plan)
    roles = [narration_role(e, characters) for e in entries]
    _subset_shots = full_shots if full_shots is not None else shots
    _kept_narr = {i for i, _e in _pc("narration_subset_entries", plan, _subset_shots, only_indices)}
    registry = registry if registry is not None else get_registry()

    # ---- 第一遍：收集发言角色（对白 + 角色旁白）→ 整章分级 ----
    counts = _pc("_role_counts_free", script, plan)
    for entry, role in zip(entries, roles):
        norm = role if str(role).strip().lower() != "narrator" else NARRATOR_ROLE
        if norm != NARRATOR_ROLE:
            rec = counts.setdefault(_pc("canonical_character_role", norm, characters),
                                    {"units": 0})
            rec["units"] += 1
    tiers = _pc("derive_voice_tiers", script, plan, registry=registry, roles=counts)
    planned_voices = _pc("plan_role_voices", script, plan, registry=registry,
                                      provider_name=name, caps=caps,
                                      tiers=tiers, roles=counts)

    def _voice_for(role: str) -> dict:
        if role in planned_voices:
            return dict(planned_voices[role])
        # 分级里有但不发言的角色（或预分配遗漏）：按同一规则即时解析
        gender, age = _detect_speaker_attributes(role, characters)
        tier = normalize_voice_tier((tiers.get(role) or {}).get("tier"))
        voice = _pc("resolve_voice", registry, role_id=role, provider_name=name, caps=caps,
                              gender=gender, age_range=age, tier=tier)
        voice["tier_reason"] = str((tiers.get(role) or {}).get("reason") or "")
        return voice

    out: list = []
    for shot in units_script:
        raw_for_speaker, text = _pc("_dialogue_speech", shot, plan, characters)
        if raw_for_speaker is None or not text:
            continue
        role = _pc("resolve_shot_speaker", shot, raw_for_speaker, characters, plan)["role"]
        role = _pc("canonical_character_role", role, characters)
        out.append(_pc("dialogue_speech_unit", shot, scope=scope, script_revision=revision,
                                        voice=_voice_for(role), provider_name=name,
                                        text=text, voice_role=role))

    if entries:
        _win, _ = _pc("_compute_voice_windows", script, shots)
        tracks, _problems = layout_voice_entries(voice_units(plan, shots), _win)
        _track_by_id = {t["id"]: t for t in tracks if t.get("kind") == "narration"}
        for i, entry in enumerate(entries, 1):
            if i not in _kept_narr:
                continue
            role = roles[i - 1]
            if str(role).strip().lower() == "narrator":
                role = NARRATOR_ROLE
            else:
                role = _pc("canonical_character_role", role, characters)
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
            out.append(_pc("narration_speech_unit", i, text=text, scope=scope, script_revision=revision,
                                             voice=_voice_for(role), provider_name=name,
                                             window=window,
                                             voice_role=role if "voice_role" in entry else ""))
    return out

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
            role = _pc("canonical_character_role", role, characters)
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
