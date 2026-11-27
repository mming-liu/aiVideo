"""tts.voice_registry - 动态角色音色注册表.

自动为新角色分配音色，持久化保存，确保跨章节角色声音一致.
"""

import json
import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")

# 可用的音色池 (DashScope sambert 系列模型 + 自定义参数)
# 每个音色有: id, gender(性别), age_range(年龄段), style(风格描述), rate(语速), volume(音量)
AVAILABLE_VOICES = [
    # 男性青年 - 阳光
    {"id": "sambert-zhide-v1", "gender": "male", "age_range": "young", "style": "青年男声,阳光", "rate": "0.9", "volume": "55"},
    # 男性中年 - 沉稳
    {"id": "sambert-zhida-v1", "gender": "male", "age_range": "middle", "style": "成熟男声,沉稳", "rate": "0.85", "volume": "60"},
    # 男性中年 - 稳重
    {"id": "sambert-zhihao-003", "gender": "male", "age_range": "middle", "style": "中年男声,稳重", "rate": "0.88", "volume": "55"},
    # 女性青年 - 柔美
    {"id": "sambert-zhimo-v1", "gender": "female", "age_range": "young", "style": "温柔女声,柔美", "rate": "0.95", "volume": "50"},
    # 女性年轻 - 清脆
    {"id": "sambert-zhiyuan-v1", "gender": "female", "age_range": "young", "style": "少女声,清脆", "rate": "1.0", "volume": "50"},
    # 男性老年 - 苍劲
    {"id": "sambert-zhibai-v1", "gender": "male", "age_range": "old", "style": "老年男声,苍劲", "rate": "0.8", "volume": "60"},
    # 女性中年 - 温婉
    {"id": "sambert-zhimo-002", "gender": "female", "age_range": "middle", "style": "中年女声,温婉", "rate": "0.85", "volume": "50"},
    # 女性老年 - 慈祥
    {"id": "sambert-zhiyuan-002", "gender": "female", "age_range": "old", "style": "老年女声,慈祥", "rate": "0.75", "volume": "55"},
]

# 旁白专用音色
NARRATOR_VOICE = {"id": "sambert-zhihao-003", "gender": "male", "age_range": "middle", "style": "旁白,沉稳", "rate": "0.85", "volume": "55"}

#: 角色可选音色池 = 全池 **去掉旁白专用音色**。
#: 旁白可以用旁白音色（那是它的默认身份），但角色不能被分配到同一个 id——
#: 否则"换了个角色"听起来还是同一个人在念旁白。
CHARACTER_VOICES = [v for v in AVAILABLE_VOICES if v["id"] != NARRATOR_VOICE["id"]]


NARRATOR_ROLE = "Narrator"


# ===========================================================================
# S04/A1：角色分级（primary/recurring 独占音色；minor/extra 共享龙套池）
# ---------------------------------------------------------------------------
# 分级只决定“能不能共用声线”，不决定“谁重要”——等级来自**输入声明**或
# **本次实际发言/出镜结构**，不在代码里写死任何作品的角色。
# ===========================================================================

VOICE_TIER_PRIMARY = "primary"
VOICE_TIER_RECURRING = "recurring"
VOICE_TIER_MINOR = "minor"
VOICE_TIER_EXTRA = "extra"
VOICE_TIERS = (VOICE_TIER_PRIMARY, VOICE_TIER_RECURRING,
               VOICE_TIER_MINOR, VOICE_TIER_EXTRA)
#: 独占音色等级：全书一个角色一个音色（与旁白互斥）
UNIQUE_VOICE_TIERS = frozenset({VOICE_TIER_PRIMARY, VOICE_TIER_RECURRING})
#: 共享音色等级：允许同一 (gender, age_range) 桶内共用，但**不得**撞独占角色/旁白
SHARED_VOICE_TIERS = frozenset({VOICE_TIER_MINOR, VOICE_TIER_EXTRA})

#: 输入里可声明等级的字段（按顺序取第一个非空值）与常见别名。
VOICE_TIER_FIELDS = ("voice_tier", "voice_pool", "role_tier")
_TIER_ALIASES = {
    "primary": VOICE_TIER_PRIMARY, "main": VOICE_TIER_PRIMARY,
    "lead": VOICE_TIER_PRIMARY, "protagonist": VOICE_TIER_PRIMARY,
    "主角": VOICE_TIER_PRIMARY, "主要": VOICE_TIER_PRIMARY,
    "recurring": VOICE_TIER_RECURRING, "supporting": VOICE_TIER_RECURRING,
    "support": VOICE_TIER_RECURRING, "配角": VOICE_TIER_RECURRING,
    "常驻": VOICE_TIER_RECURRING,
    "minor": VOICE_TIER_MINOR, "小角色": VOICE_TIER_MINOR,
    "次要": VOICE_TIER_MINOR,
    "extra": VOICE_TIER_EXTRA, "crowd": VOICE_TIER_EXTRA,
    "walk_on": VOICE_TIER_EXTRA, "龙套": VOICE_TIER_EXTRA,
    "群众": VOICE_TIER_EXTRA, "一次性": VOICE_TIER_EXTRA,
}


def normalize_voice_tier(raw) -> str:
    """把输入里的等级写法归一；无法识别返回 ""（不猜、不默认）。"""
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    return _TIER_ALIASES.get(text, text if text in VOICE_TIERS else "")


def declared_voice_tier(character: dict) -> str:
    """读角色表里的显式等级声明；没有/非法声明返回 ""。"""
    if not isinstance(character, dict):
        return ""
    for key in VOICE_TIER_FIELDS:
        tier = normalize_voice_tier(character.get(key))
        if tier:
            return tier
    return ""


def is_shared_tier(tier: str) -> bool:
    return normalize_voice_tier(tier) in SHARED_VOICE_TIERS


# ===========================================================================
# S04/A1：音色容量扩容通道 —— **未交付**（2026-11-27 复核后撤回）
# ---------------------------------------------------------------------------
# 曾用 `TTS_EXTRA_VOICES` / `TTS_EXTRA_SPEAKERS` 把配置里的音色直接并入旧池与
# provider 白名单。复核指出：代码不校验 provider 侧是否真存在这些声线，把配置当成
# 已核实能力会制造"扩容完成"的假证据。故本包**移除**该通道：角色音色仍只取内置池，
# provider 合法声线仍只取 `caps.legal_speakers`。真正扩容须按 provider 能力表核对后
# 另行交付（并带零调用校验），不能靠环境变量白名单。
# ===========================================================================

TIER_POLICY_ENV = "VOICE_TIER_POLICY"
TIER_POLICY_STRICT = "strict"
TIER_POLICY_TIERED = "tiered"


def tier_policy() -> str:
    """生效的声线合同：默认 `strict`（严格 1:1 独占，旧合同）；`tiered` 才启用分级共享."""
    raw = str(os.getenv(TIER_POLICY_ENV, "") or "").strip().lower()
    if raw in ("", TIER_POLICY_STRICT):
        return TIER_POLICY_STRICT
    if raw == TIER_POLICY_TIERED:
        return TIER_POLICY_TIERED
    raise ValueError("%s=%r 非法；合法值：%s / %s"
                     % (TIER_POLICY_ENV, raw, TIER_POLICY_STRICT,
                        TIER_POLICY_TIERED))


def tiered_sharing_enabled() -> bool:
    return tier_policy() == TIER_POLICY_TIERED


def character_voice_pool() -> list:
    """角色可选音色池 = 内置池（旁白音色始终排除）.

    扩容通道未交付：不再从环境变量并入未核实的音色（见上方说明）。
    """
    base = list(CHARACTER_VOICES)
    known = {v["id"] for v in base}
    if NARRATOR_VOICE["id"]:
        known.add(NARRATOR_VOICE["id"])
    return base


def legal_speakers_for(caps) -> tuple:
    """provider 声明的合法声线（只认能力表，不用环境变量扩张白名单）."""
    return tuple(sorted({str(x) for x in (getattr(caps, "legal_speakers", ()) or ())}))


class VoicePoolExhaustedError(RuntimeError):
    """严格唯一性下无可用音色；禁止静默复用他人/旁白音色."""

    def __init__(self, message: str, *, occupied=None, remaining: int = 0):
        super().__init__(message)
        self.occupied = dict(occupied or {})
        self.remaining = int(remaining)
        self.field_path = "voice"

    def payload(self) -> dict:
        return {"code": "voice_pool_exhausted", "message": str(self),
                "occupied_roles": sorted(self.occupied),
                "remaining": self.remaining, "field_path": self.field_path}


def strict_unique() -> bool:
    return str(os.getenv("VOICE_STRICT_UNIQUE", "1")).strip().lower() not in (
        "0", "false", "off", "no")


class _LegacyVoicePool:
    """Sambert 旧音色池的分配算法（**只作为混入**，不是第二个注册表）.

    2026-10-31 复核：本文件曾先后定义两个同名 `VoiceRegistry`，后者覆盖前者且没有
    `get_voice`，于是 `shot_tts.resolve_voice` 的 `hasattr(registry, "get_voice")`
    判 False，Sambert 通道**静默**退回旁白音色（听香也用男声中音）。现在把旧池的
    分配能力收进这个混入，`VoiceRegistry` 仍是唯一的注册表类：唯一实例类型、唯一
    持久化、唯一锁。

    它只依赖 `self.mapping` / `self.used_voice_ids` / `self.save()`——由
    `VoiceRegistry` 提供；单独实例化本混入没有意义。
    """

    # 生命周期（__init__/load/save）由 VoiceRegistry 提供——混入里再写一份等于
    # 又留一条"哪份在生效"的路，那正是本次修复要消掉的东西。

    def get_voice(self, character_name: str, gender: str = None, age_range: str = None) -> dict:
        """获取角色音色，根据性别和年龄段智能匹配。

        Args:
            character_name: 角色名
            gender: 性别 (male/female)，可选
            age_range: 年龄段 (young/middle/old)，可选

        Returns:
            音色配置字典
        """
        if not character_name:
            # 无角色名，返回旁白音色
            return NARRATOR_VOICE.copy()

        # 已注册的角色，直接返回（保持一致性）
        if character_name in self.mapping:
            return self.mapping[character_name].copy()

        # 新角色：根据性别和年龄段匹配（加锁防并发重复分配/落盘竞态）
        with self._lock:
            # 双检：等待锁期间可能已被其他线程注册
            if character_name in self.mapping:
                return self.mapping[character_name].copy()
            new_voice = self._assign_new_voice(gender, age_range)
            if new_voice:
                self.mapping[character_name] = new_voice.copy()
                self.used_voice_ids.add(new_voice["id"])
                self.save()
                print(f"[INFO] 新角色 '{character_name}' 分配音色: {new_voice['id']} ({new_voice['style']}, gender={gender}, age={age_range})")
                return new_voice

        # 音色池耗尽：严格模式禁止按 MD5 复用他人音色。
        if strict_unique():
            with self._lock:
                occupied = dict(self.mapping)
                remaining = [v for v in character_voice_pool()
                             if v["id"] not in self.used_voice_ids]
            raise VoicePoolExhaustedError(
                "角色 %r 无可用音色（已占用 %d/%d；剩余 %d）——严格唯一性禁止复用他人音色"
                % (character_name, len(occupied), len(character_voice_pool()), len(remaining)),
                occupied=occupied, remaining=len(remaining))
        # 非严格兼容：按角色名哈希确定性分配，优先匹配性别
        # 优先尝试性别匹配（S04：共享桶音色仍不参与独占分配）
        _blocked = set(self.used_voice_ids) | set(self.shared_voice_ids)
        if gender:
            candidates = [v for v in character_voice_pool() if v.get("gender") == gender.lower() and v["id"] not in _blocked]
            if candidates:
                idx = int(hashlib.md5(character_name.encode("utf-8")).hexdigest(), 16) % len(candidates)
                fallback = candidates[idx].copy()
                print(f"[WARN] 音色池耗尽，角色 '{character_name}' 回退到 {fallback['id']}（性别匹配）")
                return fallback

        # 兜底：按名哈希全池分配
        candidates = [v for v in character_voice_pool() if v["id"] not in _blocked] or list(character_voice_pool())
        idx = int(hashlib.md5(character_name.encode("utf-8")).hexdigest(), 16) % len(candidates)
        fallback = candidates[idx].copy()
        print(f"[WARN] 音色池耗尽，角色 '{character_name}' 回退到 {fallback['id']}（按名哈希分配）")
        return fallback

    def _assign_new_voice(self, gender: str = None, age_range: str = None) -> Optional[dict]:
        """根据性别和年龄段匹配音色。

        匹配优先级：
        1. 精确匹配 gender + age_range
        2. 仅匹配 gender
        3. 随机选一个未使用的（向后兼容）

        S04：**独占角色不得占用龙套桶音色**（`shared_voice_ids`），否则后续龙套
        会被迫撞独占角色。龙套音色只能在显式共享路径里分配。
        """
        blocked = set(self.used_voice_ids) | set(self.shared_voice_ids)

        def _free(v: dict) -> bool:
            return v["id"] not in blocked

        pool = character_voice_pool()

        # 尝试精确匹配 gender + age_range
        if gender and age_range:
            for voice in pool:
                if voice.get("gender") == gender.lower() and voice.get("age_range") == age_range.lower() and _free(voice):
                    return voice.copy()

        # 尝试仅匹配 gender
        if gender:
            candidates = [v for v in pool if v.get("gender") == gender.lower() and _free(v)]
            if candidates:
                return candidates[0].copy()

        # 兜底：返回第一个未使用的音色（向后兼容）
        for voice in pool:
            if _free(voice):
                return voice.copy()
        return None

    def reset(self) -> None:
        """重置注册表（清除所有分配）."""
        self.mapping = {}
        self.used_voice_ids = set()
        self.profiles = {}
        self.shared_voice_ids = set()
        self.shared_buckets = {}
        self.role_tiers = {}
        self.save()
        print("[INFO] 音色注册表已重置")


# ===========================================================================
# 2026-10-03 计划 §T1：**新 profile**（provider / speaker / mapping_version）
# ---------------------------------------------------------------------------
# 旧 Sambert 池（上面的 AVAILABLE_VOICES / get_voice）原样保留；新模型不再借用
# 它的 id 当模型名（§T1：「注册表适配不能把 Sambert ID 当 Qwen 模型 ID」）。
# 新 profile 独立保存 provider/speaker/mapping_version，按**作品 + 角色**稳定分配：
# 同一输入永远得到同一 profile（持久化后跨轮复用），非法 speaker 直接拒绝——
# **不静默回退统一默认声线**，也**不自动迁移**旧人物（旧 mapping 里的 sambert id
# 不会被当成新模型的 speaker 使用）。
# ===========================================================================

PROFILES_FIELD = "profiles"
#: 本注册表里 profile 记录的形状版本（与 audio_identity 的 mapping_version 不同：
#: 那个是**音色映射口径**的版本，换口径时递增）
PROFILE_SCHEMA = "tts_voice_profile.v1"
#: S04/A1 的分级与共享池落盘字段（互不覆盖旧字段；旧注册表读入即按空处理）
SHARED_VOICE_IDS_FIELD = "shared_voice_ids"
SHARED_BUCKETS_FIELD = "shared_buckets"
ROLE_TIERS_FIELD = "role_tiers"


class IllegalSpeakerError(ValueError):
    """非法 speaker：显式拒绝（带 provider / 合法集合），绝不回退默认声线."""

    def __init__(self, message: str, *, provider: str = "", speaker: str = "",
                 legal=()):
        super().__init__(message)
        self.provider = provider
        self.speaker = speaker
        self.legal = list(legal)
        self.field_path = "speaker"

    def payload(self) -> dict:
        return {"code": "speaker_not_legal", "message": str(self),
                "field_path": self.field_path, "provider": self.provider,
                "speaker": self.speaker, "legal_speakers": list(self.legal)}


def default_profiles() -> dict:
    return {}


def _as_profiles(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for work, roles in raw.items():
        if not isinstance(roles, dict):
            continue
        out[str(work)] = {str(r): dict(v) for r, v in roles.items() if isinstance(v, dict)}
    return out


def _as_buckets(raw) -> dict:
    """`{work: {bucket: value}}`；形状不对的分支丢弃（读旧文件即空）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for work, buckets in raw.items():
        if not isinstance(buckets, dict):
            continue
        clean = {str(k): str(v) for k, v in buckets.items() if str(v or "").strip()}
        if clean:
            out[str(work)] = clean
    return out


def _as_tiers(raw) -> dict:
    """`{work: {role: {"tier","reason","source"}}}`；非法等级不猜、直接丢。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for work, roles in raw.items():
        if not isinstance(roles, dict):
            continue
        clean = {}
        for role, rec in roles.items():
            tier = normalize_voice_tier(rec.get("tier") if isinstance(rec, dict) else rec)
            if not tier:
                continue
            base = dict(rec) if isinstance(rec, dict) else {}
            base["tier"] = tier
            clean[str(role)] = base
        if clean:
            out[str(work)] = clean
    return out


class VoiceRegistry(_LegacyVoicePool):
    """角色音色注册表（旧 Sambert 池 + 新 profile，两种 provider 契约并存）."""

    def __init__(self, registry_path: Optional[Path] = None, *, work_id: str = ""):
        if registry_path is None:
            # 走 shared/paths.py 的全书共享目录，保证音色跨章/跨部持久一致
            # （code-audit 07-29：原硬编码 output/voice_registry.json 绕过 paths.py）
            from shared.paths import global_dir
            registry_path = global_dir() / "voice_registry.json"
        self.registry_path = registry_path
        self.mapping: dict[str, dict] = {}  # { "角色名": {id, rate, volume, ...} }
        self.used_voice_ids: set[str] = set()
        #: 新 profile：`{work_id: {role_id: {voice_profile_id, provider, speaker, ...}}}`
        self.profiles: dict = {}
        #: S04/A1：共享桶已占用的旧池音色 id（独占分配必须避开）
        self.shared_voice_ids: set[str] = set()
        #: S04/A1：`{work: {"<gender>|<age_range>": voice_id_or_speaker}}`（跨章稳定）
        self.shared_buckets: dict = {}
        #: S04/A1：`{work: {role: {"tier","reason","source"}}}`（分级跨章稳定）
        self.role_tiers: dict = {}
        self.work_id = str(work_id or "")
        # #19 修复 2026-08-14：get_voice 的 mapping 写入 + 落盘与并发读竞态加锁
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        """从 JSON 文件加载注册表.

        #19 修复 2026-08-14：加载失败不再只 WARN 后留空注册表（会导致音色
        全部重分配、跨章节声音漂移）——保留内存中已加载的 mapping，并把损坏
        文件备份为 .corrupt-<时间戳>，避免反复读坏文件；下次生成用备份恢复。
        """
        if not self.registry_path.exists():
            return
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError(f"注册表根节点类型错误: {type(data).__name__}")
                self.mapping = data.get("mapping", {}) or {}
                self.used_voice_ids = set(data.get("used_voice_ids", []) or [])
                self.profiles = _as_profiles(data.get(PROFILES_FIELD))
                self.shared_voice_ids = {str(x) for x in
                                         (data.get(SHARED_VOICE_IDS_FIELD) or []) if str(x)}
                self.shared_buckets = _as_buckets(data.get(SHARED_BUCKETS_FIELD))
                self.role_tiers = _as_tiers(data.get(ROLE_TIERS_FIELD))
        except Exception as e:
            if self.mapping:
                # 已有内存态（重载场景）：保留现役分配，不整体漂移
                print(f"[ERROR] 加载音色注册表失败，保留当前内存分配: {e}")
                return
            backup = self.registry_path.with_name(
                self.registry_path.name + f".corrupt-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            try:
                os.replace(self.registry_path, backup)
                print(f"[ERROR] 音色注册表损坏（{e}），已备份到 {backup}，将重建新注册表")
            except OSError:
                print(f"[ERROR] 音色注册表损坏且备份失败（{e}），将重建新注册表")
            self.mapping = {}
            self.used_voice_ids = set()
            self.profiles = {}
            self.shared_voice_ids = set()
            self.shared_buckets = {}
            self.role_tiers = {}

    def save(self) -> None:
        """保存注册表到 JSON 文件（#19 修复：先写临时文件再原子替换）."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.registry_path.with_name(self.registry_path.name + ".tmp")
        payload = {
            "mapping": self.mapping,
            "used_voice_ids": list(self.used_voice_ids),
        }
        if self.profiles:
            payload[PROFILES_FIELD] = self.profiles
        if self.shared_voice_ids:
            payload[SHARED_VOICE_IDS_FIELD] = sorted(self.shared_voice_ids)
        if self.shared_buckets:
            payload[SHARED_BUCKETS_FIELD] = self.shared_buckets
        if self.role_tiers:
            payload[ROLE_TIERS_FIELD] = self.role_tiers
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.registry_path)

    # ---- 新 profile：按作品 + 角色稳定分配（§T1） --------------------------

    def _work_of(self, work_id: Optional[str]) -> str:
        work = str(work_id or self.work_id or "").strip()
        if work:
            return work
        try:
            from shared.paths import novel_name
            return novel_name() or "default"
        except Exception:
            return "default"

    def _in_work(self, work: str) -> dict:
        return self.profiles.setdefault(work, {})

    def _used_speakers(self, work: str, *, exclude_role: str = "") -> set:
        used = set()
        for role, rec in (self.profiles.get(work) or {}).items():
            if role == exclude_role:
                continue
            sp = str(rec.get("speaker") or "").strip()
            if sp:
                used.add(sp)
        return used

    def validate_speaker(self, speaker: str, *, provider: str = "", legal=()) -> str:
        """核 speaker 是否合法；合法返回 ""，否则抛 :class:`IllegalSpeakerError`.

        `legal` 为空 = provider 未声明白名单（此时不校验具体值，但仍要求"要么显式给出、
        要么明确留空走 provider 默认"）；非空 = **白名单**：空值/未登记值一律拒绝，
        不做静默回退。
        """
        name = str(speaker or "").strip()
        legal_set = {str(x) for x in (legal or ())}
        if not legal_set:
            return ""
        if not name:
            raise IllegalSpeakerError(
                "provider %s 需要显式 speaker（合法集合: %s）——空值不会被静默替换成默认声线"
                % (provider or "?", sorted(legal_set)),
                provider=provider, speaker=name, legal=sorted(legal_set))
        if name not in legal_set:
            raise IllegalSpeakerError(
                "非法 speaker %r（provider %s，合法集合: %s）——拒绝静默回退默认音色"
                % (name, provider or "?", sorted(legal_set)),
                provider=provider, speaker=name, legal=sorted(legal_set))
        return ""

    def _ensure_narrator_profile(self, work: str, provider: str,
                                 legal_sorted: list, mapping_version: str) -> None:
        """旁白先占一个 speaker，使角色分配时天然避开旁白声线."""
        bucket = self._in_work(work)
        if NARRATOR_ROLE in bucket:
            return
        used = self._used_speakers(work, exclude_role=NARRATOR_ROLE)
        free = [x for x in legal_sorted if x not in used]
        if legal_sorted and not free:
            if strict_unique():
                raise VoicePoolExhaustedError(
                    "旁白无可用 speaker（已占用 %s）——拒绝与角色共用声线"
                    % sorted(used), occupied={r: v.get("speaker")
                                              for r, v in bucket.items()},
                    remaining=0)
            free = list(legal_sorted)
        chosen = free[0] if free else ""
        bucket[NARRATOR_ROLE] = {
            "schema_version": PROFILE_SCHEMA,
            "voice_profile_id": _profile_id(work, NARRATOR_ROLE, provider, chosen),
            "provider": str(provider or ""),
            "speaker": chosen,
            "mapping_version": str(mapping_version or ""),
            "role_id": NARRATOR_ROLE,
            "work_id": work,
        }
        self.save()

    def profile_for(self, role_id: str, *, provider: str, legal_speakers=(),
                    work_id: Optional[str] = None, mapping_version: str = "",
                    speaker: Optional[str] = None, voice_profile_id: str = "",
                    tier: str = "") -> dict:
        """取（必要时分配）该角色在本作品下的**稳定**新 profile.

        - 已存在 ⇒ 原样返回（并复核其 speaker 仍合法、映射版本一致；不合法/不一致即
          显式失败，由使用者决定是换 profile 还是修计划，**不静默替换**）；
        - 不存在 ⇒ 用显式 `speaker`（优先），或按 `(work, role)` 的确定性哈希从
          `legal_speakers` 里挑一个**本作品内未占用**的；`legal_speakers` 为空时
          留空（由 provider 自报其默认，并在身份里如实记录 speaker=""）；
        - **不读旧 `mapping`**（不自动迁移旧人物），**不回退统一默认声线**。
        """
        work = self._work_of(work_id)
        role = str(role_id or "").strip()
        if not role:
            raise ValueError("profile_for 需要 role_id（角色/单元标识），收到空值")
        legal_sorted = sorted({str(x) for x in (legal_speakers or ())})
        with self._lock:
            if role != NARRATOR_ROLE:
                # 旁白先占坑：角色分配时 used 已含旁白 speaker，天然避开。
                self._ensure_narrator_profile(work, provider, legal_sorted,
                                              mapping_version)
            bucket = self._in_work(work)
            existing = bucket.get(role)
            if existing:
                self.validate_speaker(str(existing.get("speaker") or ""),
                                      provider=provider, legal=legal_sorted)
                if mapping_version and str(existing.get("mapping_version") or "") != str(mapping_version):
                    raise IllegalSpeakerError(
                        "角色 %r 的 profile 映射版本 %r 与本次 %r 不符——"
                        "换映射口径请显式重建 profile"
                        % (role, existing.get("mapping_version"), mapping_version),
                        provider=provider, speaker=str(existing.get("speaker") or ""),
                        legal=legal_sorted)
                if voice_profile_id and str(existing.get("voice_profile_id") or "") != str(voice_profile_id):
                    raise IllegalSpeakerError(
                        "角色 %r 已绑定 voice_profile_id=%r，与本次请求 %r 不符"
                        % (role, existing.get("voice_profile_id"), voice_profile_id),
                        provider=provider, speaker=str(existing.get("speaker") or ""),
                        legal=legal_sorted)
                return dict(existing)

            chosen = str(speaker or "").strip()
            used = self._used_speakers(work, exclude_role=role)
            if chosen:
                self.validate_speaker(chosen, provider=provider, legal=legal_sorted)
                if chosen in used:
                    raise VoicePoolExhaustedError(
                        "角色 %r 请求的 speaker %r 已被其他角色/旁白占用"
                        % (role, chosen),
                        occupied={r: v.get("speaker") for r, v in bucket.items()
                                  if r != role},
                        remaining=0)
            elif legal_sorted:
                free = [x for x in legal_sorted if x not in used]
                if not free:
                    if strict_unique():
                        raise VoicePoolExhaustedError(
                            "角色 %r 无可用 speaker（合法集合 %s 已被占用）"
                            % (role, legal_sorted),
                            occupied={r: v.get("speaker")
                                      for r, v in bucket.items() if r != role},
                            remaining=0)
                    free = list(legal_sorted)
                digest = hashlib.md5(("%s|%s" % (work, role)).encode("utf-8")).hexdigest()
                chosen = free[int(digest, 16) % len(free)]
            rec = {
                "schema_version": PROFILE_SCHEMA,
                "voice_profile_id": str(voice_profile_id or _profile_id(work, role, provider, chosen)),
                "provider": str(provider or ""),
                "speaker": chosen,
                "mapping_version": str(mapping_version or ""),
                "role_id": role,
                "work_id": work,
                "voice_tier": normalize_voice_tier(tier) or VOICE_TIER_RECURRING,
            }
            bucket[role] = rec
            self.save()
            print("[INFO] 角色 '%s'（work=%s）分配 profile: speaker=%s provider=%s"
                  % (role, work, chosen or "(provider 默认)", provider or "?"))
            return dict(rec)

    # ---- S04/A1：分级记录与共享龙套桶 -----------------------------------

    def role_tier(self, role_id: str, *, work_id: Optional[str] = None) -> dict:
        """该角色**已记录**的分级（历史优先用于跨章稳定）；没有返回 {}。"""
        work = self._work_of(work_id)
        rec = (self.role_tiers.get(work) or {}).get(str(role_id or "").strip())
        return dict(rec) if isinstance(rec, dict) else {}

    def record_role_tier(self, role_id: str, tier: str, *, work_id: Optional[str] = None,
                         reason: str = "", source: str = "",
                         replace: bool = False) -> dict:
        """记录角色分级（默认同一角色只写一次；`replace=True` 用于显式声明覆盖）."""
        role = str(role_id or "").strip()
        norm = normalize_voice_tier(tier)
        if not role or not norm:
            return self.role_tier(role, work_id=work_id)
        work = self._work_of(work_id)
        with self._lock:
            roles = self.role_tiers.setdefault(work, {})
            if role in roles and not replace:
                return dict(roles[role])
            roles[role] = {"tier": norm, "reason": str(reason or ""),
                           "source": str(source or "")}
            self.save()
            return dict(roles[role])

    @staticmethod
    def _bucket_key(gender: str | None, age_range: str | None) -> str:
        return "%s|%s" % (str(gender or "any").strip().lower(),
                          str(age_range or "any").strip().lower())

    def get_shared_voice(self, role_id: str, *, gender: str = None,
                         age_range: str = None, work_id: Optional[str] = None,
                         avoid: tuple = ()) -> dict:
        """旧池（Sambert）共享音色分配：龙套桶内共享，**不撞独占角色/旁白**.

        `avoid` = 同一批分配里**不得复用**的音色（同镜/同场会同时被听见的角色），
        由调用方按发言/出镜重叠算出。桶内已有音色且不在 `avoid` 里就直接复用，
        这是"龙套共用一条声线"的正常情形。
        """
        role = str(role_id or "").strip()
        if not role:
            raise ValueError("get_shared_voice 需要角色标识，收到空值")
        work = self._work_of(work_id)
        key = self._bucket_key(gender, age_range)
        blocked = set(self.used_voice_ids) | set(avoid or ())
        with self._lock:
            if role in self.mapping:
                existing = dict(self.mapping[role])
                if str(existing.get("id") or ""):
                    self.shared_voice_ids.add(str(existing["id"]))
                return existing
            bucket_value = str((self.shared_buckets.get(work) or {}).get(key) or "")
            chosen = None
            if bucket_value and bucket_value not in blocked:
                chosen = next((v for v in character_voice_pool() if v["id"] == bucket_value), None)
            if chosen is None:
                free = [v for v in character_voice_pool()
                        if v["id"] not in blocked
                        and v["id"] not in self.shared_voice_ids]
                # 优先同性别+同龄段 → 同性别 → 任意；同分按 id 稳定排序
                free.sort(key=lambda v: (v.get("gender") != str(gender or "").lower(),
                                         v.get("age_range") != str(age_range or "").lower(),
                                         v["id"]))
                chosen = free[0] if free else None
            if chosen is None:
                # 桶内已有声线被本批 avoid 挡住、又无空余 ⇒ 明确失败，不静默撞人
                raise VoicePoolExhaustedError(
                    "龙套角色 %r（%s）无可用共享音色：角色音色池 %d 条已被独占/同镜角色占用"
                    "（avoid=%s）——扩充共享音色或把该角色声明为独占等级"
                    % (role, key, len(character_voice_pool()), sorted(avoid)),
                    occupied={r: v.get("id") for r, v in self.mapping.items()},
                    remaining=0)
            voice = dict(chosen)
            self.mapping[role] = voice
            self.shared_voice_ids.add(str(voice["id"]))
            self.shared_buckets.setdefault(work, {}).setdefault(key, str(voice["id"]))
            self.save()
            print("[INFO] 龙套角色 '%s' 分共享音色: %s（桶 %s）"
                  % (role, voice["id"], key))
            return voice

    def shared_profile_for(self, role_id: str, *, provider: str, legal_speakers=(),
                           work_id: Optional[str] = None, mapping_version: str = "",
                           gender: str = None, age_range: str = None,
                           avoid: tuple = (), tier: str = "") -> dict:
        """新模型（有独立 speaker）的共享 profile：桶内共享，不撞独占角色/旁白."""
        role = str(role_id or "").strip()
        if not role:
            raise ValueError("shared_profile_for 需要 role_id，收到空值")
        work = self._work_of(work_id)
        legal_sorted = sorted({str(x) for x in (legal_speakers or ())})
        key = self._bucket_key(gender, age_range)
        with self._lock:
            if role != NARRATOR_ROLE:
                self._ensure_narrator_profile(work, provider, legal_sorted,
                                              mapping_version)
            bucket = self._in_work(work)
            existing = bucket.get(role)
            if existing:
                self.validate_speaker(str(existing.get("speaker") or ""),
                                      provider=provider, legal=legal_sorted)
                return dict(existing)
            blocked = set(avoid or ())
            bucket_value = str((self.shared_buckets.get(work) or {}).get(key) or "")
            used = self._used_speakers(work, exclude_role=role)
            chosen = ""
            if bucket_value and bucket_value not in blocked and bucket_value not in used:
                chosen = bucket_value
            if not chosen and legal_sorted:
                free = [x for x in legal_sorted if x not in used and x not in blocked]
                if free:
                    digest = hashlib.md5(("%s|%s|%s" % (work, key, role)).encode("utf-8")).hexdigest()
                    chosen = free[int(digest, 16) % len(free)]
            if not chosen and legal_sorted:
                raise VoicePoolExhaustedError(
                    "龙套角色 %r（%s）无可用共享 speaker：合法集合 %s 已被独占角色占用"
                    % (role, key, legal_sorted),
                    occupied={r: v.get("speaker") for r, v in bucket.items()},
                    remaining=0)
            rec = {
                "schema_version": PROFILE_SCHEMA,
                "voice_profile_id": _profile_id(work, role, provider, chosen),
                "provider": str(provider or ""),
                "speaker": chosen,
                "mapping_version": str(mapping_version or ""),
                "role_id": role,
                "work_id": work,
                "voice_tier": normalize_voice_tier(tier) or VOICE_TIER_EXTRA,
            }
            bucket[role] = rec
            if chosen:
                self.shared_buckets.setdefault(work, {}).setdefault(key, chosen)
            self.save()
            print("[INFO] 龙套角色 '%s'（work=%s）分共享 speaker=%s（桶 %s）"
                  % (role, work, chosen or "(provider 默认)", key))
            return dict(rec)


# 全局单例（#19 修复：加锁防并发创建两个实例导致分配分裂）
_registry_instance: Optional[VoiceRegistry] = None
_registry_by_path: dict = {}
_registry_lock = threading.Lock()


def _profile_id(work: str, role: str, provider: str, speaker: str) -> str:
    digest = hashlib.md5(("%s|%s|%s|%s" % (work, role, provider, speaker)).encode("utf-8")).hexdigest()
    return "vp-%s" % digest[:16]


def get_registry(registry_path=None, *, work_id: str = "") -> VoiceRegistry:
    """获取音色注册表.

    **隔离优先**：显式 `registry_path`，或 env `TTS_VOICE_REGISTRY_PATH` 有值时，
    返回绑定该路径的实例（按路径缓存）——候选/实验运行绝不能经由本函数隐式写生产
    `global/voice_registry.json`（§T4）。两者都没有才用生产单例。
    """
    global _registry_instance
    raw = registry_path or os.getenv("TTS_VOICE_REGISTRY_PATH", "").strip()
    if raw:
        key = str(Path(raw))
        with _registry_lock:
            inst = _registry_by_path.get(key)
            if inst is None:
                inst = VoiceRegistry(Path(raw), work_id=work_id)
                _registry_by_path[key] = inst
            return inst
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = VoiceRegistry(work_id=work_id)
    return _registry_instance


def reset_registry_cache() -> None:
    """清掉实例缓存（测试/工具在同一进程里切换候选根时必须调它）.

    不清的话，`get_registry()` 会把上一次解析出的实例继续返回给新候选根——
    实验分配就会写进**上一个**根（甚至生产注册表）。
    """
    global _registry_instance
    with _registry_lock:
        _registry_by_path.clear()
        _registry_instance = None
