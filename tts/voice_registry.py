"""tts.voice_registry - 动态角色音色注册表.

自动为新角色分配音色，持久化保存，确保跨章节角色声音一致.
"""

import json
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")

# 可用的音色池 (DashScope sambert 系列模型 + 自定义参数)
# 每个音色有: id, gender(性别), style(风格描述), rate(语速), volume(音量)
AVAILABLE_VOICES = [
    {"id": "sambert-zhide-v1", "gender": "Male", "style": "青年男声,阳光", "rate": "0.9", "volume": "55"},
    {"id": "sambert-zhida-v1", "gender": "Male", "style": "成熟男声,沉稳", "rate": "0.85", "volume": "60"},
    {"id": "sambert-zhihao-003", "gender": "Male", "style": "中年男声,稳重", "rate": "0.88", "volume": "55"},
    {"id": "sambert-zhimo-v1", "gender": "Female", "style": "温柔女声,柔美", "rate": "0.95", "volume": "50"},
    {"id": "sambert-zhiyuan-v1", "gender": "Female", "style": "少女声,清脆", "rate": "1.0", "volume": "50"},
    {"id": "sambert-zhibai-v1", "gender": "Male", "style": "老年男声,苍劲", "rate": "0.8", "volume": "60"},
]

# 旁白专用音色
NARRATOR_VOICE = {"id": "sambert-zhihao-003", "gender": "Male", "style": "旁白,沉稳", "rate": "0.85", "volume": "55"}


class VoiceRegistry:
    """角色音色注册表."""

    def __init__(self, registry_path: Optional[Path] = None):
        if registry_path is None:
            registry_path = _PROJECT_ROOT / "output" / "voice_registry.json"
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

    def get_voice(self, character_name: str) -> dict:
        """获取角色音色，如果是新角色则自动分配."""
        if not character_name:
            # 无角色名，返回旁白音色
            return NARRATOR_VOICE.copy()

        # 已注册的角色，直接返回
        if character_name in self.mapping:
            return self.mapping[character_name].copy()

        # 新角色：分配未使用的音色
        new_voice = self._assign_new_voice()
        if new_voice:
            self.mapping[character_name] = new_voice.copy()
            self.used_voice_ids.add(new_voice["id"])
            self.save()
            print(f"[INFO] 新角色 '{character_name}' 分配音色: {new_voice['id']} ({new_voice['style']})")
            return new_voice

        # 音色池耗尽，返回默认
        print(f"[WARN] 音色池耗尽，角色 '{character_name}' 使用默认音色")
        return AVAILABLE_VOICES[0].copy()

    def _assign_new_voice(self) -> Optional[dict]:
        """从可用池中选择一个未使用的音色."""
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