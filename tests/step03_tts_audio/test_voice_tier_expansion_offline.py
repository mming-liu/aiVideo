# -*- coding: utf-8 -*-
"""声线分级扩容通道的离线验收（2026-11-28 O6）.

用**匿名** 12 角色 / 两章剧本规模的分配压力，验证 `VOICE_TIER_POLICY` 两种合同的
行为差异（不依赖真实小说、不调用 provider）：

- `strict`（默认，旧合同）：所有角色独占，池子不够就 fail-closed；
- `tiered`：primary/recurring 仍独占且**永不共享**，minor/extra 走同性别+年龄段的
  共享桶；共享集合与独占集合**交集为空**（含旁白）；
- 即便在 `tiered` 下，独占角色把 legal_speakers 占满时**依然 fail-closed**
  （不会把主角降级塞进龙套桶）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tts.voice_registry import (NARRATOR_ROLE, VoicePoolExhaustedError,
                                VoiceRegistry, character_voice_pool,
                                is_shared_tier, normalize_voice_tier, tier_policy)

#: 与 Sambert 旧池同量级（3 男 4 女 = 7）：正好卡在「多章累计角色 > 7」的边界上
LEGAL = ("speaker-m1", "speaker-m2", "speaker-m3", "speaker-f1",
         "speaker-f2", "speaker-f3", "speaker-f4")


def _registry(tmp_path: Path) -> VoiceRegistry:
    return VoiceRegistry(tmp_path / "voice_registry.json", work_id="anon-work")


def _unique_roles(reg: VoiceRegistry, count: int, *, provider="sambert") -> list[dict]:
    out = []
    for i in range(count):
        out.append(reg.profile_for("lead-%d" % i, provider=provider,
                                   legal_speakers=LEGAL, mapping_version="m1",
                                   tier="primary"))
    return out


def _shared_roles(reg: VoiceRegistry, count: int, *, provider="sambert",
                  avoid_by_role=()) -> list[dict]:
    out = []
    for i in range(count):
        gender, age = ("male", "young") if i % 2 == 0 else ("female", "old")
        out.append(reg.shared_profile_for(
            "extra-%d" % i, provider=provider, legal_speakers=LEGAL,
            mapping_version="m1", gender=gender, age_range=age,
            avoid=tuple(avoid_by_role), tier="extra"))
    return out


# ---------------------------------------------------------------------------
# 合同默认值与分级归一
# ---------------------------------------------------------------------------
def test_default_policy_is_strict(monkeypatch):
    monkeypatch.delenv("VOICE_TIER_POLICY", raising=False)
    assert tier_policy() == "strict"


def test_tiered_policy_requires_explicit_config(monkeypatch):
    monkeypatch.setenv("VOICE_TIER_POLICY", "tiered")
    assert tier_policy() == "tiered"
    monkeypatch.setenv("VOICE_TIER_POLICY", "nonsense")
    with pytest.raises(ValueError):
        tier_policy()


@pytest.mark.parametrize("raw,tier,shared", [
    ("primary", "primary", False), ("主角", "primary", False),
    ("recurring", "recurring", False), ("配角", "recurring", False),
    ("minor", "minor", True), ("小角色", "minor", True),
    ("extra", "extra", True), ("龙套", "extra", True),
    ("", "", False),
])
def test_tier_normalization_and_shared_classification(raw, tier, shared):
    assert normalize_voice_tier(raw) == tier
    assert is_shared_tier(tier) is shared


# ---------------------------------------------------------------------------
# strict：12 角色必然撞池
# ---------------------------------------------------------------------------
def test_strict_policy_blocks_the_twelfth_character(tmp_path):
    """7 条合法 speaker：旁白 1 + 6 个独占角色就把池子占满，第 7 个角色必须拒收."""
    reg = _registry(tmp_path)
    narrator = reg.profile_for(NARRATOR_ROLE, provider="sambert",
                               legal_speakers=LEGAL, mapping_version="m1")
    assert narrator["speaker"] in LEGAL
    used = _unique_roles(reg, 6)
    assert len({r["speaker"] for r in used} | {narrator["speaker"]}) == 7
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("lead-6", provider="sambert", legal_speakers=LEGAL,
                        mapping_version="m1", tier="primary")


def test_strict_policy_does_not_let_a_crowd_role_steal_a_unique_speaker(tmp_path):
    """strict 下没有任何共享桶：龙套也走独占分配；池满时同样 fail-closed."""
    reg = _registry(tmp_path)
    reg.profile_for(NARRATOR_ROLE, provider="sambert", legal_speakers=LEGAL,
                    mapping_version="m1")
    _unique_roles(reg, 6)
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("extra-0", provider="sambert", legal_speakers=LEGAL,
                        mapping_version="m1", tier="extra")


# ---------------------------------------------------------------------------
# tiered：4 主角独占 + 8 龙套共享
# ---------------------------------------------------------------------------
def test_tiered_policy_keeps_unique_and_shared_pools_disjoint(tmp_path):
    reg = _registry(tmp_path)
    narrator = reg.profile_for(NARRATOR_ROLE, provider="sambert",
                               legal_speakers=LEGAL, mapping_version="m1")
    unique = _unique_roles(reg, 4)
    unique_speakers = {r["speaker"] for r in unique}
    # 12 角色 = 旁白 + 4 主角/常驻 + 8 龙套（跨两章规模）
    shared = _shared_roles(reg, 8)
    shared_speakers = {r["speaker"] for r in shared}

    assert len(unique_speakers) == 4, "主角/常驻必须一人一 speaker"
    assert narrator["speaker"] not in unique_speakers
    assert not (unique_speakers & shared_speakers), "共享池与独占池必须物理不相交"
    assert narrator["speaker"] not in shared_speakers, "龙套不得占用旁白音色"
    assert len(shared_speakers) <= 2, "同性别+年龄段的龙套应复用同一桶音色"


def test_tiered_policy_still_fails_closed_for_primary_overflow(tmp_path):
    """tiered 不是「什么都共享」：独占角色占满后仍必须报错，不得降级进龙套桶."""
    reg = _registry(tmp_path)
    reg.profile_for(NARRATOR_ROLE, provider="sambert", legal_speakers=LEGAL,
                    mapping_version="m1")
    _unique_roles(reg, 6)
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("lead-6", provider="sambert", legal_speakers=LEGAL,
                        mapping_version="m1", tier="primary")
    # 此时共享桶也确实无可用（全部被独占占满）
    with pytest.raises(VoicePoolExhaustedError):
        _shared_roles(reg, 1)


# ---------------------------------------------------------------------------
# 跨章稳定：同一角色重新装载后拿到同一 speaker
# ---------------------------------------------------------------------------
def test_assignment_is_stable_across_registry_reloads(tmp_path):
    path = tmp_path / "voice_registry.json"
    reg = VoiceRegistry(path, work_id="anon-work")
    first = reg.profile_for("lead-0", provider="sambert", legal_speakers=LEGAL,
                            mapping_version="m1", tier="primary")
    extra = reg.shared_profile_for("extra-0", provider="sambert",
                                   legal_speakers=LEGAL, mapping_version="m1",
                                   gender="male", age_range="young",
                                   tier="extra")
    reloaded = VoiceRegistry(path, work_id="anon-work")
    again = reloaded.profile_for("lead-0", provider="sambert",
                                 legal_speakers=LEGAL, mapping_version="m1",
                                 tier="primary")
    again_extra = reloaded.shared_profile_for("extra-0", provider="sambert",
                                              legal_speakers=LEGAL,
                                              mapping_version="m1",
                                              gender="male", age_range="young",
                                              tier="extra")
    assert again["speaker"] == first["speaker"]
    assert again_extra["speaker"] == extra["speaker"]
    assert again["voice_profile_id"] == first["voice_profile_id"]


def test_character_pool_never_contains_the_narrator_voice(tmp_path):
    from tts.voice_registry import NARRATOR_VOICE

    pool_ids = {v["id"] for v in character_voice_pool()}
    assert NARRATOR_VOICE["id"] not in pool_ids
