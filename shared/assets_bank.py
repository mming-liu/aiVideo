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
from copy import deepcopy
from pathlib import Path


def _book_global_dir() -> Path:
    """本书跨章节共享目录（统一路径来源: shared/paths.py）."""
    from shared.paths import global_dir
    return global_dir()


def _next_asset_id(entries: dict, prefix: str) -> str:
    """为资产分配编号（S01/C01/P01），按注册顺序递增.

    扫描已有条目的 asset_id，取最大序号 +1。缺 asset_id 的旧条目按出现顺序补号，
    补号与返回的新编号都计入已用集合，避免撞号（修复 2026-08-14：旧版补号后
    max_n 不更新，新条目会与刚补号的旧条目返回同一编号）。
    """
    used = set()
    max_n = 0
    for ident, entry in entries.items():
        aid = (entry or {}).get("asset_id", "")
        num = None
        if aid and aid.startswith(prefix):
            try:
                num = int(aid[len(prefix):])
            except ValueError:
                num = None
        if num is not None:
            used.add(num)
            max_n = max(max_n, num)
        else:
            # 旧条目无编号 / 编号非数字 → 补号（不占用已用编号）
            n = 1
            while n in used:
                n += 1
            used.add(n)
            max_n = max(max_n, n)
            entries[ident]["asset_id"] = f"{prefix}{n:02d}"
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n:02d}"


# ---------------------------------------------------------------------------
# 场景名↔地点 模糊匹配辅助
# ---------------------------------------------------------------------------

def _longest_common_substr_len(a: str, b: str) -> int:
    """计算两个字符串的最长公共连续子串长度（中文字符按单个字符计）.

    用于场景名（如"照月湾小舟"）↔ 镜头 location（如"云河镇照月湾"）的
    模糊匹配。单纯双向子串（src in loc or loc in src）会漏掉两个都只含
    共同片段的情况："照月湾小舟" 与 "云河镇照月湾" 互为超集/子集不成立，
    但共同连续子串"照月湾"（3字）足以确认是同一地点。
    """
    if not a or not b:
        return 0
    # 较短的串作为滑动基准，减少 DP 开销
    if len(a) > len(b):
        a, b = b, a
    best = 0
    for start in range(len(a)):
        for end in range(start + best, len(a) + 1):
            seg = a[start:end]
            if seg in b:
                best = len(seg)
            else:
                break
    return best


def scene_matches_location(scene_src: str, loc: str, min_len: int = 2) -> bool:
    """判断场景匹配源（场景名/别名/location）与镜头 location 是否同一地点.

    min_len: 公共连续子串的最小长度。默认 2 字——地名公共片段 ≥2 字即可
    确认同一地点（如"照月湾"3字命中），1 字太弱（"湾"到处都有）容易误配。
    命中任一场景匹配源即返回 True。
    """
    if not scene_src or not loc:
        return False
    if scene_src in loc or loc in scene_src:
        return True  # 完整子串（旧逻辑保留，最快路径）
    return _longest_common_substr_len(scene_src, loc) >= min_len


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
        self._data: dict = deepcopy(_EMPTY_SCENES)

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
                self._data = deepcopy(_EMPTY_SCENES)
        else:
            self._data = deepcopy(_EMPTY_SCENES)
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

    def get_side_image(self, identifier: str) -> str | None:
        """返回侧面全景图路径（v6.23 多视图），不存在返回 None."""
        scene = self.get_scene(identifier)
        if not scene:
            return None
        p = scene.get("side_image", "")
        if p and Path(p).exists():
            return p
        default = self.get_asset_dir(identifier) / "side.png"
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
            # v6.23：资产编号（S01/S02... 按注册顺序自动分配，与中文名双轨）
            entry.setdefault("asset_id", _next_asset_id(self._data["scenes"], "S"))
            self._data["scenes"][ident] = entry

        self._data["meta"]["last_updated_chapter"] = max(
            self._data["meta"]["last_updated_chapter"], chapter_num
        )

    def set_assets(self, identifier: str, assets: dict) -> None:
        """设置场景资产图数据.

        assets 格式: {wide: {path, description}, near: {path, description}}

        注意：写独立字段 wide_image_desc/near_image_desc，绝不覆盖
        wide_desc/near_desc（LLM 提取的场景描述，供 asset_gen/r2v 参考图使用）。
        """
        scene = self._data["scenes"].get(identifier)
        if scene:
            for key in ("wide", "near"):
                if key in assets:
                    scene[f"{key}_image"] = assets[key].get("path", "")
                    scene[f"{key}_image_desc"] = assets[key].get("description", "")


# ---------------------------------------------------------------------------
# PropAssetsBank
# ---------------------------------------------------------------------------

_EMPTY_PROPS: dict = {
    "props": {},
    "meta": {"last_updated_chapter": 0, "total_props": 0},
}

# 别名去重时忽略的泛化词：单字类名/纯描述词（刀/利刃/寒光/车/船…）太泛，
# 不能作为「两件道具是同一件」的依据——否则 牛车 会误并入 马车。
_ALIAS_GENERIC_TERMS = frozenset({
    "刀", "刃", "利刃", "寒光", "冷光", "光", "银白", "金属", "锋利",
    "车", "船", "舟", "伞", "灯", "酒", "杯", "壶", "布", "帘",
})


class PropAssetsBank:
    """跨章节道具资产（白底资产图）持久化."""

    def __init__(self, bank_dir: str | None = None):
        self._bank_dir = Path(bank_dir) if bank_dir else _book_global_dir() / "props_bank"
        self._bank_path = self._bank_dir / "props_bank.json"
        self._data: dict = deepcopy(_EMPTY_PROPS)

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
                self._data = deepcopy(_EMPTY_PROPS)
        else:
            self._data = deepcopy(_EMPTY_PROPS)
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

    def _find_alias_conflict(self, ident: str, terms: list) -> str | None:
        """别名去重：新道具与既有道具是否同一件（仅 identifier 不同）.

        判据（任一命中即视为同一件）：
          1. 新 identifier 是既有道具的 consistent_term（别名）→ 明确同一件
          2. 既有 identifier 是新道具的 consistent_term（别名）→ 明确同一件
          3. 去泛化后别名交集非空（如 弯刀 vs 匕首 共享「匕首」）→ 强别名重叠

        返回应合并到的既有 identifier；无冲突返回 None。

        背景（08-16）：同一把刺客武器被 LLM 注册成「刺客弯刀」+「刺客匕首」两条，
        运行时按别名子串匹配会把两件道具参考图一起注入。identifier 精确去重拦不住
        （字符串不同），需要别名级判定。泛化词（刀/利刃/寒光…）不计入交集，防
        牛车/马车 这类共享「车」的不同道具被误并入。
        """
        new_terms = {t for t in (terms or []) if t}
        if not new_terms:
            return None
        for other_id, other in self._data["props"].items():
            if other_id == ident or not isinstance(other, dict):
                continue
            other_terms = {t for t in (other.get("consistent_terms", []) or []) if t}
            if not other_terms:
                continue
            # 强信号 1/2：一方 identifier 是另一方的别名
            if ident in other_terms or other_id in new_terms:
                return other_id
            # 强信号 3：去泛化后的别名交集非空
            specific = (new_terms & other_terms) - _ALIAS_GENERIC_TERMS
            if specific:
                return other_id
        return None

    def register_prop(self, prop: dict, chapter_num: int) -> None:
        """注册或更新道具条目.

        prop: asset_extractor 返回的单个道具 dict（含 identifier/appearance/
        consistent_terms 等字段）。
        同一道具重复出现时：外观以最新为准，consistent_terms 累加去重。
        别名强重叠（不同 identifier 但 consistent_terms 交集去泛化后非空）视为
        同一件，合并进既有条目而非新建（防 LLM 把同一武器注册成多条）。
        """
        ident = prop.get("identifier", "").strip()
        if not ident:
            return
        existing = self._data["props"].get(ident)
        is_alias_merge = False
        if not existing:
            alias_id = self._find_alias_conflict(ident, prop.get("consistent_terms", []))
            if alias_id:
                print(f"[bank] 道具「{ident}」与既有「{alias_id}」别名重叠视为同一件，"
                      f"忽略新建，并入既有条目")
                existing = self._data["props"].get(alias_id)
                is_alias_merge = True
        if existing:
            if is_alias_merge:
                # 别名吸收：canonical 条目为准——重复条目的外观/别名可能是幻觉
                # （弯刀的外观/别名是错的），不覆盖不并集，仅推进 last_chapter。
                existing["last_chapter"] = max(existing.get("last_chapter", 0), chapter_num)
            else:
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
            # v6.23：道具编号（P01/P02... 按注册顺序自动分配）
            entry.setdefault("asset_id", _next_asset_id(self._data["props"], "P"))
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
