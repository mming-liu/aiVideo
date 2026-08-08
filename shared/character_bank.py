"""跨章节角色与风格一致性持久化模块.

提供两个持久化 Bank:
  - CharacterBank: 角色外观 + 肖像图（前/侧/背面），跨章增量积累
  - StyleBank: 全局视觉风格锚点（Ch1 锁定，后续章引用）

数据布局:
  {OUTPUT_BASE}/book_{BOOK:02d}/global/characters_bank/
    characters_bank.json        ← 角色注册表
    <identifier>/front.png      ← 正面肖像
    <identifier>/side.png       ← 侧面肖像
    <identifier>/back.png       ← 背面肖像
  {OUTPUT_BASE}/book_{BOOK:02d}/global/style_bank/
    style_bank.json             ← 风格元数据
    style_anchor.png            ← Ch1 Shot1 首帧副本

全局（跨章节）数据按书归档在 book 目录的 global/ 下，与各 chapter_NN/ 平级。
"""

import json
import os
import shutil
from pathlib import Path
from datetime import datetime


def _book_global_dir() -> Path:
    """本书跨章节共享目录（统一路径来源: shared/paths.py）."""
    from shared.paths import global_dir
    return global_dir()


# ---------------------------------------------------------------------------
# CharacterBank
# ---------------------------------------------------------------------------

_EMPTY_BANK: dict = {"characters": {}, "meta": {"last_updated_chapter": 0, "total_characters": 0}}


class CharacterBank:
    """跨章节角色外观 + 肖像持久化."""

    def __init__(self, bank_dir: str | None = None):
        self._bank_dir = Path(bank_dir) if bank_dir else _book_global_dir() / "characters_bank"
        self._bank_path = self._bank_dir / "characters_bank.json"
        self._data: dict = dict(_EMPTY_BANK)

    # -- 读写 ----------------------------------------------------------------

    def load(self) -> dict:
        """从磁盘加载 bank，不存在则返回空结构."""
        if self._bank_path.exists():
            try:
                with open(self._bank_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # 确保必要 key 存在
                loaded.setdefault("characters", {})
                loaded.setdefault("meta", {"last_updated_chapter": 0, "total_characters": 0})
                self._data = loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bank] 角色库读取失败，使用空库: {e}")
                self._data = dict(_EMPTY_BANK)
        else:
            self._data = dict(_EMPTY_BANK)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入：先写 .tmp 再 rename，防止写入中断损坏 bank."""
        self._data["meta"]["total_characters"] = len(self._data.get("characters", {}))
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._bank_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._bank_path)
        except OSError as e:
            print(f"[bank] 保存角色库失败: {e}")

    # -- 查询 ----------------------------------------------------------------

    def has_character(self, identifier: str) -> bool:
        """角色是否已在 bank 中."""
        return identifier in self._data.get("characters", {})

    def get_character(self, identifier: str) -> dict | None:
        """获取角色条目（含肖像），不存在返回 None."""
        return self._data.get("characters", {}).get(identifier)

    def get_all_identifiers(self) -> list[str]:
        """返回所有已注册角色名."""
        return list(self._data.get("characters", {}).keys())

    def list_missing_portraits(self) -> list[str]:
        """返回已注册但缺肖像的角色名列表."""
        missing = []
        for ident, char in self._data.get("characters", {}).items():
            portraits = char.get("portraits", {})
            if not portraits or not portraits.get("front", {}).get("path"):
                missing.append(ident)
        return missing

    def get_portrait_dir(self, identifier: str) -> Path:
        """返回角色肖像目录（如 characters_bank/杨旭/）."""
        return self._bank_dir / identifier

    def get_all_portraits_as_registry(self) -> dict:
        """返回 ViMax 兼容的 character_portraits_registry dict.

        格式: {identifier: {front/side/back: {path, description}}}
        只包含已生成肖像的角色.
        """
        registry = {}
        for ident, char in self._data.get("characters", {}).items():
            portraits = char.get("portraits", {})
            if portraits:
                registry[ident] = portraits
        return registry

    # -- 修改 ----------------------------------------------------------------

    def register_character(
        self,
        identifier: str,
        static_features: str,
        dynamic_features: str,
        chapter_num: int,
    ) -> None:
        """注册新角色（尚无肖像）."""
        # v6.23：角色编号（C01/C02... 按注册顺序自动分配）
        max_n = 0
        for c in self._data["characters"].values():
            aid = (c or {}).get("asset_id", "")
            if aid and aid.startswith("C"):
                try:
                    max_n = max(max_n, int(aid[1:]))
                except ValueError:
                    pass
        self._data["characters"][identifier] = {
            "identifier": identifier,
            "asset_id": f"C{max_n + 1:02d}",
            "static_features": static_features,
            "dynamic_features": dynamic_features,
            "first_chapter": chapter_num,
            "last_chapter": chapter_num,
            "portraits": {},
        }

    def update_character_chapter(self, identifier: str, chapter_num: int) -> None:
        """更新角色最后出场章节."""
        char = self._data["characters"].get(identifier)
        if char:
            char["last_chapter"] = max(char.get("last_chapter", 0), chapter_num)

    def update_dynamic_features(self, identifier: str, dynamic_features: str) -> None:
        """更新角色可变特征（服装等），不变量 static_features 首次锁定不变."""
        char = self._data["characters"].get(identifier)
        if char and dynamic_features:
            char["dynamic_features"] = dynamic_features

    def set_portraits(self, identifier: str, portraits: dict) -> None:
        """设置角色肖像数据.

        portraits 格式: {front: {path, description}, side: {...}, back: {...}}
        """
        char = self._data["characters"].get(identifier)
        if char:
            char["portraits"] = portraits

    def get_meta(self) -> dict:
        """返回 bank 元数据."""
        return self._data.get("meta", {})

    def get_chapter_characters(self, chapter_num: int) -> list[str]:
        """返回指定章节中出现过的角色."""
        result = []
        for ident, char in self._data.get("characters", {}).items():
            first = char.get("first_chapter", 0)
            last = char.get("last_chapter", 0)
            if first <= chapter_num <= last:
                result.append(ident)
        return result


# ---------------------------------------------------------------------------
# StyleBank
# ---------------------------------------------------------------------------

_EMPTY_STYLE: dict = {
    "style_anchor_chapter": 0,
    "style_anchor_image": "",
    "is_locked": False,
    "created_at": "",
}


class StyleBank:
    """全局视觉风格锚点持久化.

    Ch1 首次运行时锁定风格锚点（Shot1 首帧），后续所有章节引用.
    """

    def __init__(self, bank_dir: str | None = None):
        self._dir = Path(bank_dir) if bank_dir else _book_global_dir() / "style_bank"
        self._json_path = self._dir / "style_bank.json"
        self._data: dict = dict(_EMPTY_STYLE)

    # -- 读写 ----------------------------------------------------------------

    def load(self) -> dict:
        """从磁盘加载，不存在返回空结构."""
        if self._json_path.exists():
            try:
                with open(self._json_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for k, v in _EMPTY_STYLE.items():
                    loaded.setdefault(k, v)
                self._data = loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bank] 风格库读取失败，使用空库: {e}")
                self._data = dict(_EMPTY_STYLE)
        else:
            self._data = dict(_EMPTY_STYLE)
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._json_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._json_path)
        except OSError as e:
            print(f"[bank] 保存风格库失败: {e}")

    # -- 查询 ----------------------------------------------------------------

    def is_locked(self) -> bool:
        """风格锚点是否已锁定."""
        return bool(self._data.get("is_locked", False))

    def get_anchor_path(self) -> str | None:
        """返回 style_anchor.png 的绝对路径，不存在返回 None."""
        path = self._data.get("style_anchor_image", "")
        if path and Path(path).exists():
            return path
        # 尝试从默认位置查找
        default = self._dir / "style_anchor.png"
        if default.exists():
            return str(default.resolve())
        return None

    def get_anchor_chapter(self) -> int:
        """返回锁定锚点的章节号."""
        return self._data.get("style_anchor_chapter", 0)

    # -- 修改 ----------------------------------------------------------------

    def lock(self, anchor_image_path: str, chapter_num: int) -> None:
        """锁定风格锚点：复制首帧到 style_bank/ + 写 JSON.

        anchor_image_path: Ch1 Shot1 first_frame.png 的路径
        chapter_num: 用来生成锚点的章节号（通常为 1）
        """
        src = Path(anchor_image_path)
        if not src.exists():
            print(f"[bank] 风格锚点源文件不存在: {anchor_image_path}")
            return

        self._dir.mkdir(parents=True, exist_ok=True)
        dst = self._dir / "style_anchor.png"
        try:
            shutil.copy2(str(src), str(dst))
            print(f"[bank] 风格锚点已锁定: {dst}")
        except OSError as e:
            print(f"[bank] 复制风格锚点失败: {e}")
            return

        self._data["style_anchor_chapter"] = chapter_num
        self._data["style_anchor_image"] = str(dst.resolve())
        self._data["is_locked"] = True
        self._data["created_at"] = datetime.now().isoformat()
        self.save()


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def load_banks():
    """加载两个 bank 的便捷函数."""
    cb = CharacterBank()
    cb.load()
    sb = StyleBank()
    sb.load()
    return cb, sb
