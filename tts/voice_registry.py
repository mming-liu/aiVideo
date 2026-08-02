"""tts.voice_registry - 动态角色音色注册表.

自动为新角色分配音色，持久化保存，确保跨章节角色声音一致.
"""

import json
import hashlib
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
        self.load()

    def load(self) -> None:
        """从 JSON 文件加载注册表."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.mapping = data.get("mapping", {})
                    self.used_voice_ids = set(data.get("used_voice_ids", []))
            except Exception as e:
                print(f"[WARN] 加载音色注册表失败: {e}")

    def save(self) -> None:
        """保存注册表到 JSON 文件."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump({
                "mapping": self.mapping,
                "used_voice_ids": list(self.used_voice_ids)
            }, f, ensure_ascii=False, indent=2)

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

        # 新角色：根据性别和年龄段匹配
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
        self.save()
        print("[INFO] 音色注册表已重置")


# 全局单例
_registry_instance: Optional[VoiceRegistry] = None


def get_registry() -> VoiceRegistry:
    """获取全局音色注册表单例."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = VoiceRegistry()
    return _registry_instance