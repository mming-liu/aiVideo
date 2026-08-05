"""跨章节场景/道具资产持久化模块.

提供两个持久化 Bank（镜像 CharacterBank 的四层模板：生成器 → 专用适配器 →
bank → registry）：
  - SceneAssetsBank: 场景资产（大场景图 + 近景功能场景图），跨章增量积累
  - PropAssetsBank: 道具资产（白底道具资产图），跨章增量积累

方法论来源（references/ai-scene-prop-asset-guide.md）：
  1. 大场景（establishing）锁定地理/时间/天气色调/空间关系，是所有镜头的统一参考
  2. 近景功能场景从大场景派生，保持季节/光线/年代/风格一致
  3. 剧本道具提取 → 4. 道具资产图（棚拍白底，稳定贴图/视频生成基础）

数据布局:
  {OUTPUT_BASE}/<novel>/global/scenes_bank/
    scenes_bank.json          ← 场景注册表（含大场景/近景描述与图片路径）
    <identifier>/wide.png     ← 大场景建立镜头图（无人物）
    <identifier>/near.png     ← 近景功能场景图（从大场景派生，无人物）
  {OUTPUT_BASE}/<novel>/global/props_bank/
    props_bank.json           ← 道具注册表（含外观描述与资产图路径）
    <identifier>/prop.png     ← 道具资产图（棚拍白底）
"""

import json
from pathlib import Path


def _book_global_dir() -> Path:
    """本书跨章节共享目录（统一路径来源: shared/paths.py）."""
    from shared.paths import global_dir
    return global_dir()


# ---------------------------------------------------------------------------
# SceneAssetsBank
# ---------------------------------------------------------------------------

_EMPTY_SCENES: dict = {
    "scenes": {},
    "meta": {"last_updated_chapter": 0, "total_scenes": 0},
}


class SceneAssetsBank:
    """跨章节场景资产（大场景 + 近景）持久化."""

    def __init__(self, bank_dir: str | None = None):
        self._bank_dir = Path(bank_dir) if bank_dir else _book_global_dir() / "scenes_bank"
        self._bank_path = self._bank_dir / "scenes_bank.json"
        self._data: dict = dict(_EMPTY_SCENES)

    # -- 读写 ----------------------------------------------------------------

    def load(self) -> dict:
        """从磁盘加载 bank，不存在则返回空结构."""
        if self._bank_path.exists():
            try:
                with open(self._bank_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded.setdefault("scenes", {})
                loaded.setdefault("meta", {"last_updated_chapter": 0, "total_scenes": 0})
                self._data = loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bank] 场景资产库读取失败，使用空库: {e}")
                self._data = dict(_EMPTY_SCENES)
        else:
            self._data = dict(_EMPTY_SCENES)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入：先写 .tmp 再 rename，防止写入中断损坏 bank."""
        self._data["meta"]["total_scenes"] = len(self._data.get("scenes", {}))
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._bank_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._bank_path)
        except OSError as e:
            print(f"[bank] 保存场景资产库失败: {e}")

    # -- 查询 ----------------------------------------------------------------

    def has_scene(self, identifier: str) -> bool:
        """场景是否已在 bank 中."""
        return identifier in self._data.get("scenes", {})

    def get_scene(self, identifier: str) -> dict | None:
        """获取场景条目，不存在返回 None."""
        return self._data.get("scenes", {}).get(identifier)

    def get_all_identifiers(self) -> list[str]:
        """返回所有已注册场景名."""
        return list(self._data.get("scenes", {}).keys())

    def get_asset_dir(self, identifier: str) -> Path:
        """返回场景资产目录（如 scenes_bank/照月湾小舟/）."""
        return self._bank_dir / identifier

    def get_wide_image(self, identifier: str) -> str | None:
        """返回大场景图路径，不存在返回 None."""
        scene = self.get_scene(identifier)
        if not scene:
            return None
        p = scene.get("wide_image", "")
        if p and Path(p).exists():
            return p
        # 兼容直接文件名（首帧锁定后立即重读 bank 时）
        default = self.get_asset_dir(identifier) / "wide.png"
        if default.exists():
            return str(default.resolve())
        return None

    def get_near_image(self, identifier: str) -> str | None:
        """返回近景功能场景图路径，不存在返回 None."""
        scene = self.get_scene(identifier)
        if not scene:
            return None
        p = scene.get("near_image", "")
        if p and Path(p).exists():
            return p
        default = self.get_asset_dir(identifier) / "near.png"
        if default.exists():
            return str(default.resolve())
        return None

    def get_all_assets_as_registry(self) -> dict:
        """导出 ViMax 兼容的 scene_assets_registry dict.

        格式: {identifier: {wide: {path, description}, near: {path, description}}}
        只包含已有资产图的场景.
        """
        registry = {}
        for ident, scene in self._data.get("scenes", {}).items():
            assets = {}
            for key, fname, label in (
                ("wide", "wide.png", "大场景建立镜头"),
                ("near", "near.png", "近景功能场景"),
            ):
                p = scene.get(f"{key}_image", "")
                if p and Path(p).exists():
                    assets[key] = {"path": p, "description": f"{ident} {label}参考图"}
            if assets:
                registry[ident] = assets
        return registry

    # -- 修改 ----------------------------------------------------------------

    def register_scene(self, scene: dict, chapter_num: int) -> None:
        """注册或更新场景条目.

        scene: asset_extractor 返回的单个场景 dict（含 identifier/大场景描述/
        近景描述/地点/时间段等字段）。
        同一场景重复出现时：描述以最新为准，consistent_terms 累加去重，
        首次出现章节锁定不变，last_chapter 取最大。
        """
        ident = scene.get("identifier", "").strip()
        if not ident:
            return
        existing = self._data["scenes"].get(ident)
        if existing:
            for k in ("location", "time_of_day", "weather", "color_tone",
                      "wide_desc", "near_desc", "wide_prompt", "near_prompt"):
                if scene.get(k):
                    existing[k] = scene[k]
            terms = set(existing.get("consistent_terms", []) or [])
            terms.update(scene.get("consistent_terms", []) or [])
            existing["consistent_terms"] = sorted(terms)
            existing["last_chapter"] = max(existing.get("last_chapter", 0), chapter_num)
        else:
            entry = dict(scene)
            entry.setdefault("first_chapter", chapter_num)
            entry["last_chapter"] = chapter_num
            entry["consistent_terms"] = list(entry.get("consistent_terms", []) or [])
            self._data["scenes"][ident] = entry

        self._data["meta"]["last_updated_chapter"] = max(
            self._data["meta"]["last_updated_chapter"], chapter_num
        )

    def set_assets(self, identifier: str, assets: dict) -> None:
        """设置场景资产图数据.

        assets 格式: {wide: {path, description}, near: {path, description}}
        """
        scene = self._data["scenes"].get(identifier)
        if scene:
            for key in ("wide", "near"):
                if key in assets:
                    scene[f"{key}_image"] = assets[key].get("path", "")
                    scene[f"{key}_desc"] = assets[key].get("description", "")


# ---------------------------------------------------------------------------
# PropAssetsBank
# ---------------------------------------------------------------------------

_EMPTY_PROPS: dict = {
    "props": {},
    "meta": {"last_updated_chapter": 0, "total_props": 0},
}


class PropAssetsBank:
    """跨章节道具资产（白底资产图）持久化."""

    def __init__(self, bank_dir: str | None = None):
        self._bank_dir = Path(bank_dir) if bank_dir else _book_global_dir() / "props_bank"
        self._bank_path = self._bank_dir / "props_bank.json"
        self._data: dict = dict(_EMPTY_PROPS)

    # -- 读写 ----------------------------------------------------------------

    def load(self) -> dict:
        """从磁盘加载 bank，不存在则返回空结构."""
        if self._bank_path.exists():
            try:
                with open(self._bank_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded.setdefault("props", {})
                loaded.setdefault("meta", {"last_updated_chapter": 0, "total_props": 0})
                self._data = loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bank] 道具资产库读取失败，使用空库: {e}")
                self._data = dict(_EMPTY_PROPS)
        else:
            self._data = dict(_EMPTY_PROPS)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入：先写 .tmp 再 rename，防止写入中断损坏 bank."""
        self._data["meta"]["total_props"] = len(self._data.get("props", {}))
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._bank_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._bank_path)
        except OSError as e:
            print(f"[bank] 保存道具资产库失败: {e}")

    # -- 查询 ----------------------------------------------------------------

    def has_prop(self, identifier: str) -> bool:
        """道具是否已在 bank 中."""
        return identifier in self._data.get("props", {})

    def get_prop(self, identifier: str) -> dict | None:
        """获取道具条目，不存在返回 None."""
        return self._data.get("props", {}).get(identifier)

    def get_all_identifiers(self) -> list[str]:
        """返回所有已注册道具名."""
        return list(self._data.get("props", {}).keys())

    def get_asset_dir(self, identifier: str) -> Path:
        """返回道具资产目录（如 props_bank/背篓/）."""
        return self._bank_dir / identifier

    def get_asset_image(self, identifier: str) -> str | None:
        """返回道具资产图路径，不存在返回 None."""
        prop = self.get_prop(identifier)
        if not prop:
            return None
        p = prop.get("image", "")
        if p and Path(p).exists():
            return p
        default = self.get_asset_dir(identifier) / "prop.png"
        if default.exists():
            return str(default.resolve())
        return None

    def get_all_assets_as_registry(self) -> dict:
        """导出 prop_assets_registry dict.

        格式: {identifier: {path, description}}
        只包含已有资产图的道具.
        """
        registry = {}
        for ident, prop in self._data.get("props", {}).items():
            p = prop.get("image", "")
            if p and Path(p).exists():
                registry[ident] = {"path": p, "description": f"{ident} 道具资产参考图"}
        return registry

    # -- 修改 ----------------------------------------------------------------

    def register_prop(self, prop: dict, chapter_num: int) -> None:
        """注册或更新道具条目.

        prop: asset_extractor 返回的单个道具 dict（含 identifier/appearance/
        consistent_terms 等字段）。
        同一道具重复出现时：外观以最新为准，consistent_terms 累加去重。
        """
        ident = prop.get("identifier", "").strip()
        if not ident:
            return
        existing = self._data["props"].get(ident)
        if existing:
            if prop.get("appearance"):
                existing["appearance"] = prop["appearance"]
            terms = set(existing.get("consistent_terms", []) or [])
            terms.update(prop.get("consistent_terms", []) or [])
            existing["consistent_terms"] = sorted(terms)
            existing["last_chapter"] = max(existing.get("last_chapter", 0), chapter_num)
        else:
            entry = dict(prop)
            entry.setdefault("first_chapter", chapter_num)
            entry["last_chapter"] = chapter_num
            entry["consistent_terms"] = list(entry.get("consistent_terms", []) or [])
            self._data["props"][ident] = entry

        self._data["meta"]["last_updated_chapter"] = max(
            self._data["meta"]["last_updated_chapter"], chapter_num
        )

    def set_asset(self, identifier: str, asset: dict) -> None:
        """设置道具资产图数据.

        asset 格式: {path, description}
        """
        prop = self._data["props"].get(identifier)
        if prop:
            prop["image"] = asset.get("path", "")
            prop["image_desc"] = asset.get("description", "")
