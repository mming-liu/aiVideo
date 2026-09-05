"""跨章节场景物体尺寸一致性持久化模块.

提供 SceneObjectsBank：同一场景反复出现的空间物体（船只/马车/厅堂/院落等）
的尺寸/容量/结构锚定，跨章增量积累，镜像 CharacterBank 的模式。

背景：step01 的规则 25 曾把船规格硬编码为「长约四米、可容两三人」，
导致图片模型把船画成只能装 2 人的小舢板。本模块改为从原文上下文推导
物体尺寸并持久化，避免每次遇到新物体都靠硬编码。

数据布局:
  {OUTPUT_BASE}/<novel>/global/objects_bank/
    objects_bank.json     ← 物体注册表（含尺寸/容量/结构/统一称呼）
"""

import json
from pathlib import Path


def _book_global_dir() -> Path:
    """本书跨章节共享目录（统一路径来源: shared/paths.py）."""
    from shared.paths import global_dir
    return global_dir()


_EMPTY_BANK: dict = {
    "objects": {},
    "meta": {"last_updated_chapter": 0, "total_objects": 0},
}


class SceneObjectsBank:
    """跨章节场景物体尺寸持久化."""

    def __init__(self, bank_dir: str | None = None):
        if bank_dir:
            self._bank_dir = Path(bank_dir)
        else:
            from shared.paths import objects_bank_dir
            self._bank_dir = objects_bank_dir()
        self._bank_path = self._bank_dir / "objects_bank.json"
        self._data: dict = dict(_EMPTY_BANK)

    # -- 读写 ----------------------------------------------------------------

    def load(self) -> dict:
        """从磁盘加载 bank，不存在则返回空结构."""
        if self._bank_path.exists():
            try:
                with open(self._bank_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                loaded.setdefault("objects", {})
                loaded.setdefault("meta", {"last_updated_chapter": 0, "total_objects": 0})
                self._data = loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[bank] 场景物体库读取失败，使用空库: {e}")
                self._data = dict(_EMPTY_BANK)
        else:
            self._data = dict(_EMPTY_BANK)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入：先写 .tmp 再 rename，防止写入中断损坏 bank."""
        self._data["meta"]["total_objects"] = len(self._data.get("objects", {}))
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._bank_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._bank_path)
        except OSError as e:
            print(f"[bank] 保存场景物体库失败: {e}")

    # -- 查询 ----------------------------------------------------------------

    def has_object(self, identifier: str) -> bool:
        """物体是否已在 bank 中."""
        return identifier in self._data.get("objects", {})

    def get_object(self, identifier: str) -> dict | None:
        """获取物体条目，不存在返回 None."""
        return self._data.get("objects", {}).get(identifier)

    def get_all_identifiers(self) -> list[str]:
        """返回所有已注册物体名."""
        return list(self._data.get("objects", {}).keys())

    def get_meta(self) -> dict:
        return self._data.get("meta", {})

    # -- 修改 ----------------------------------------------------------------

    def register_object(self, obj: dict, chapter_num: int) -> None:
        """注册或更新物体条目.

        obj: object_extractor 返回的单个物体 dict（含 identifier/length_m/
             width_m/capacity/structure/consistent_terms 等字段）。
        同一物体重复出现时合并：尺寸以最新推导为准，consistent_terms 累加去重，
        首次出现章节锁定不变，last_chapter 取最大。
        """
        ident = obj.get("identifier", "").strip()
        if not ident:
            return
        existing = self._data["objects"].get(ident)
        if existing:
            # 尺寸/容量/结构更新为最新推导
            for k in ("category", "subtype", "length_m", "width_m",
                      "height_m", "capacity", "structure", "size_reasoning"):
                if obj.get(k) is not None:
                    existing[k] = obj[k]
            # consistent_terms 累加去重（历史称呼保留，供跨章引用）
            terms = set(existing.get("consistent_terms", []) or [])
            terms.update(obj.get("consistent_terms", []) or [])
            existing["consistent_terms"] = sorted(terms)
            existing["last_chapter"] = max(existing.get("last_chapter", 0), chapter_num)
        else:
            entry = dict(obj)
            entry.setdefault("first_chapter", chapter_num)
            entry["last_chapter"] = chapter_num
            entry["consistent_terms"] = list(entry.get("consistent_terms", []) or [])
            self._data["objects"][ident] = entry

        self._data["meta"]["last_updated_chapter"] = max(
            self._data["meta"]["last_updated_chapter"], chapter_num
        )
