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


class VoiceRegistry:
    """角色音色注册表."""

    def __init__(self, registry_path: Optional[Path] = None):
        if registry_path is None:
            # 走 shared/paths.py 的全书共享目录，保证音色跨章/跨部持久一致
            # （code-audit 07-29：原硬编码 output/voice_registry.json 绕过 paths.py）
            from shared.paths import global_dir
            registry_path = global_dir() / "voice_registry.json"
        self.registry_path = registry_path
        self.mapping: dict[str, dict] = {}  # { "角色名": {id, rate, volume, ...} }
        self.used_voice_ids: set[str] = set()
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

    def save(self) -> None:
        """保存注册表到 JSON 文件（#19 修复：先写临时文件再原子替换）."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.registry_path.with_name(self.registry_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({
                "mapping": self.mapping,
                "used_voice_ids": list(self.used_voice_ids)
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.registry_path)

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

        # 音色池耗尽：按角色名哈希确定性分配，优先匹配性别
        # 优先尝试性别匹配
        if gender:
            candidates = [v for v in AVAILABLE_VOICES if v.get("gender") == gender.lower() and v["id"] not in self.used_voice_ids]
            if candidates:
                idx = int(hashlib.md5(character_name.encode("utf-8")).hexdigest(), 16) % len(candidates)
                fallback = candidates[idx].copy()
                print(f"[WARN] 音色池耗尽，角色 '{character_name}' 回退到 {fallback['id']}（性别匹配）")
                return fallback

        # 兜底：按名哈希全池分配
        idx = int(hashlib.md5(character_name.encode("utf-8")).hexdigest(), 16) % len(AVAILABLE_VOICES)
        fallback = AVAILABLE_VOICES[idx].copy()
        print(f"[WARN] 音色池耗尽，角色 '{character_name}' 回退到 {fallback['id']}（按名哈希分配）")
        return fallback

    def _assign_new_voice(self, gender: str = None, age_range: str = None) -> Optional[dict]:
        """根据性别和年龄段匹配音色。

        匹配优先级：
        1. 精确匹配 gender + age_range
        2. 仅匹配 gender
        3. 随机选一个未使用的（向后兼容）
        """
        # 尝试精确匹配 gender + age_range
        if gender and age_range:
            for voice in AVAILABLE_VOICES:
                if voice.get("gender") == gender.lower() and voice.get("age_range") == age_range.lower() and voice["id"] not in self.used_voice_ids:
                    return voice.copy()

        # 尝试仅匹配 gender
        if gender:
            candidates = [v for v in AVAILABLE_VOICES if v.get("gender") == gender.lower() and v["id"] not in self.used_voice_ids]
            if candidates:
                return candidates[0].copy()

        # 兜底：返回第一个未使用的音色（向后兼容）
        for voice in AVAILABLE_VOICES:
            if voice["id"] not in self.used_voice_ids:
                return voice.copy()
        return None

    def reset(self) -> None:
        """重置注册表（清除所有分配）."""
        self.mapping = {}
        self.used_voice_ids = set()
        self.profiles = {}
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


class VoiceRegistry:
    """角色音色注册表."""

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

    def profile_for(self, role_id: str, *, provider: str, legal_speakers=(),
                    work_id: Optional[str] = None, mapping_version: str = "",
                    speaker: Optional[str] = None, voice_profile_id: str = "") -> dict:
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
            if chosen:
                self.validate_speaker(chosen, provider=provider, legal=legal_sorted)
            elif legal_sorted:
                used = self._used_speakers(work, exclude_role=role)
                free = [x for x in legal_sorted if x not in used] or legal_sorted
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
            }
            bucket[role] = rec
            self.save()
            print("[INFO] 角色 '%s'（work=%s）分配 profile: speaker=%s provider=%s"
                  % (role, work, chosen or "(provider 默认)", provider or "?"))
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
