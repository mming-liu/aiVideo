"""跨章节角色与风格一致性持久化模块.

提供两个持久化 Bank:
  - CharacterBank: 角色外观 + 肖像图（前/侧/背面），跨章增量积累
  - StyleBank: 全局视觉风格锚点（Ch1 锁定，后续章引用）

数据布局（目录计算统一走 shared/paths.py）:
  {OUTPUT_BASE}/{NOVEL_NAME}/global/characters_bank/
    characters_bank.json        ← 角色注册表
    <identifier>/front.png      ← 正面肖像
    <identifier>/side.png       ← 侧面肖像
    <identifier>/back.png       ← 背面肖像
  {OUTPUT_BASE}/{NOVEL_NAME}/global/style_bank/
    style_bank.json             ← 风格元数据
    style_anchor.png            ← Ch1 Shot1 首帧副本

全局（跨章节）数据在小说级 global/ 下，与部级 part_XX/ 及章级 chapter_NNN/ 平级。
"""

import copy
import json
import os
import shutil
import re
from pathlib import Path
from datetime import datetime


def _book_global_dir() -> Path:
    """本书跨章节共享目录（统一路径来源: shared/paths.py）."""
    from shared.paths import global_dir
    return global_dir()


def _current_run_id() -> str:
    """本进程当前 run 身份（`save(run_id="")` 时的兜底来源）.

    2026-10-02 复核：写库必须能自证"是谁写的"。让 `save()` **默认**取进程身份，
    而不是要求每个调用点都记得传——新加一个写库入口时不会漏（各写一遍必然漏一处）。
    """
    try:
        from shared.run_identity import current_run_id
        return current_run_id()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 角色名规范化（B2 修复 08-11，与 character_extractor 的 _normalize_identifier 同源）
#
# 历史遗留：旧版本角色提取未规范化 identifier，「杨旭。」「杨旭，」被注册成
# 双角色（character_bank 双 key、肖像分裂、prompt 锚点只命中一个）。本 Bank
# 在读写边界统一规范化：register 时存规范化名，查找时先精确再归一化匹配，
# 保证新注册不再产生双 key，旧数据也能被正确命中。
# ---------------------------------------------------------------------------

_TRAILING_NOISE = "。，、；；：:！!？?、 　"

_FULLWIDTH_MAP = {
    ch: chr(ord(ch) - 0xFEE0) for ch in "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
}


def normalize_identifier(name: str) -> str:
    """角色名规范化：strip + 剥尾部标点 + 全角字母归一（幂等）."""
    if not name:
        return name
    out = str(name).strip(_TRAILING_NOISE)
    for full, half in _FULLWIDTH_MAP.items():
        if full in out:
            out = out.replace(full, half)
    return out


# ---------------------------------------------------------------------------
# CharacterBank
# ---------------------------------------------------------------------------

#: 条目上保留的**来源调用**引用条数上限（最近 N 次，去重）：bank 的字段只表达
#: "首次创建与最近更新来源"，**不是**完整字段修改史（复核 2026-10-02 之七）。
CALL_IDS_KEEP = 10


def _source_call_ids(call_ids, prev=None) -> list:
    """把本轮的 call_id 并进条目的来源窗口（去重、保序、只留最近 N 条）."""
    _old = [str(c) for c in (prev or []) if str(c or "").strip()]
    _merged = list(_old)
    for _c in (call_ids or []):            # **本批内也要去重**（同一次更新可能传重复项）
        _c = str(_c or "").strip()
        if _c and _c not in _merged:
            _merged.append(_c)
    return _merged[-CALL_IDS_KEEP:]


_EMPTY_BANK: dict = {"characters": {}, "meta": {"last_updated_chapter": 0, "total_characters": 0}}


class CharacterBank:
    """跨章节角色外观 + 肖像持久化."""

    def __init__(self, bank_dir: str | None = None):
        if bank_dir:
            self._bank_dir = Path(bank_dir)
        else:
            from shared.paths import characters_bank_dir
            self._bank_dir = characters_bank_dir()
        self._bank_path = self._bank_dir / "characters_bank.json"
    # **空模板必须深复制**（复核之七）：`dict(_EMPTY_BANK)` 只复制了外层——`characters`
    # 与内层 `meta` 仍是**模块级同一个对象**，于是新建空库会把上一个实例写的条目带进来
    # （实测：同一进程里先建 A 库再建 B 库，B 一开库就带着 A 的角色；跨运行还可能把别的
    # 章节的实体当成已存在）。空库是**每个实例一份**状态。
        self._data: dict = copy.deepcopy(_EMPTY_BANK)
        #: 本次进程**登记/更新过**的角色名（save 时给它们盖 `last_run_id`）——
        #: 只标被这一轮碰过的条目：整库覆盖会让"来源"退化成"最后一次写库的人"。
        self._touched: set = set()
        #: 本次**新建**的条目（只有它们能盖 `first_run_id`）。
        #: 把 `first_run_id` 也盖给"本轮之前就存在"的条目 = 伪造来源：
        #: 它明明不是本轮首次登记的（实测 10-02 一轮里，老角色被盖成本轮 first_run_id）。
        self._created: set = set()

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
                self._data = copy.deepcopy(_EMPTY_BANK)
        else:
            self._data = copy.deepcopy(_EMPTY_BANK)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self, run_id: str = "", call_ids=None) -> bool:
        """原子写入：**在候选副本上**盖来源，写盘成功后才确认内存状态、消费本次变更.

        `run_id`（2026-10-02 复核）：写盘时记下**这一轮的来源身份**——
        `meta.last_run_id` / `meta.last_written_at`，以及每个**本次新增/更新**条目的
        `first_run_id` / `last_run_id`。旧库没有这些字段是**历史**（重构后未重跑），
        读取方按"缺字段 = 来源未知"处理；这里**不回溯伪造**上一轮的 id。

        `call_ids`（复核 2026-10-02 之七）：这一轮**确实发生**的调用（由台账给出）。
        条目上记 `last_call_ids`（最近若干次引用）+ `last_source`（`call` = 有调用证据；
        `mechanical` = 本轮没有对应调用：机械更新/缓存复用/旧资料）——**不编造模型调用**，
        也不拿 `run_id` 冒充"已知模型与规则版本"（那要顺着 call_id 去台账查）。

        **保存边界**（复核之七）：盖来源与写盘都发生在**副本**上，写成功才把副本换成
        内存状态并**清空本次变更集合**。于是——连续保存不会重盖首次来源、不会把未更新的
        条目再标一遍；写盘失败时内存与盘上都不动、变更集合留着，**后续重试能正确保存**。

        Returns: `True` = 真的写进了盘（调用方据此才可以说"已保存"）。
        """
        _rid = str(run_id or "").strip() or _current_run_id()
        # **候选副本**：失败 ⇒ 基线（内存与盘上）一个字节不改
        _cand = copy.deepcopy(self._data)
        # 统计也在**副本**上（复核 2026-10-02 之八 P1：save 期的改动一律不落在内存上，
        # 写盘失败时内存与盘上都不动）
        _cand["meta"]["total_characters"] = len(_cand.get("characters", {}))
        if _rid:
            self._stamp_run_id(_cand, _rid, call_ids)
        # **统一失败出口**（复核 2026-10-02 之九 P2）：目录创建、临时写入、替换三步里，
        # 原来只有后两步在异常边界内——注入目录创建失败（父路径被文件占住 / 只读）时
        # 直接抛 `OSError`，调用方拿到的是异常而不是 `False`，"失败保留基线、可重试"
        # 这条约定在**第一步**就断了。现在三步同一边界，且写盘统一走
        # `shared.json_io.atomic_dump_json`（它正是"写临时文件 + 替换 + 失败清理"的共享
        # 实现；临时文件名 `<path>.tmp` 与本模块原来拼的逐字相同）。
        try:
            self._bank_dir.mkdir(parents=True, exist_ok=True)
            from shared.json_io import atomic_dump_json
            atomic_dump_json(self._bank_path, _cand, ensure_ascii=False, indent=2)
        except (OSError, ValueError, TypeError) as e:
            print(f"[bank] 保存角色库失败（**盘上文件原样保留**，本次变更待重试）: {e}")
            return False
        # 成功才确认：内存换成写进盘的那份，并**消费**本次变更集合
        self._data = _cand
        self._touched.clear()
        self._created.clear()
        return True

    def _stamp_run_id(self, data: dict, run_id: str, call_ids=None) -> dict:
        """把来源记到**副本** `data` 的 meta 与本次登记过的条目上（不改 `self._data`）."""
        data.setdefault("meta", {})["last_run_id"] = run_id
        try:
            data["meta"]["last_written_at"] = datetime.now().isoformat(timespec="seconds")
        except Exception:
            pass
        _cids = [str(c) for c in (call_ids or []) if str(c or "").strip()]
        for _k in getattr(self, "_touched", ()) or ():
            _c = data.get("characters", {}).get(_k)
            if not isinstance(_c, dict):
                continue
            # **既有首次来源不得覆盖**（复核之七）：已经在盘上记过第一次的条目，
            # 即便本轮又更新了，它的"首次"也不是本轮——盖上去就是伪造来源。
            if _k in getattr(self, "_created", ()) and not str(_c.get("first_run_id") or "").strip():
                _c["first_run_id"] = run_id
            _c["last_run_id"] = run_id
            _c["last_call_ids"] = _source_call_ids(_cids, _c.get("last_call_ids"))
            _c["last_source"] = "call" if _cids else "mechanical"
        return data

    # -- 查询 ----------------------------------------------------------------

    def has_character(self, identifier: str) -> bool:
        """角色是否已在 bank 中（先精确，再归一化匹配——旧数据可能带尾部标点）."""
        if identifier in self._data.get("characters", {}):
            return True
        norm = normalize_identifier(identifier)
        return any(
            k == norm or normalize_identifier(k) == norm
            for k in self._data.get("characters", {})
        )

    def get_character(self, identifier: str) -> dict | None:
        """获取角色条目（含肖像），不存在返回 None.

        B2 修复：先精确匹配；未命中时按规范化名匹配（旧数据「杨旭。」vs
        新名「杨旭」→ 命中间接规范化 key），消除双角色分裂。
        """
        chars = self._data.get("characters", {})
        if identifier in chars:
            return chars[identifier]
        norm = normalize_identifier(identifier)
        for k, v in chars.items():
            if normalize_identifier(k) == norm:
                return v
        return None

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
        """注册新角色（尚无肖像）.

        B2 修复：identifier 先规范化（剥尾部标点/全角归一），若已存在同化名
        角色（旧数据「杨旭。」），合并到该条目而非新建双 key——保证同一角色
        跨章只占一个 key，肖像/特征不分裂。
        """
        identifier = normalize_identifier(identifier)
        # 合并旧化名条目：若规范化后与已有 key 一致，把新特征并入已有条目
        for k in list(self._data["characters"].keys()):
            if normalize_identifier(k) == identifier and k != identifier:
                existing = self._data["characters"].pop(k)
                # 合并特征（已有保留，新补充）
                for field in ("static_features", "dynamic_features"):
                    cur = existing.get(field, "")
                    newv = static_features if field == "static_features" else dynamic_features
                    if newv and newv not in str(cur):
                        existing[field] = (str(cur) + "，" + newv).strip("，")
                self._data["characters"][identifier] = existing
                self._data["characters"][identifier]["identifier"] = identifier
                self._touched.add(identifier)
                return
        # v6.23：角色编号（C01/C02... 按注册顺序自动分配）
        max_n = 0
        for c in self._data["characters"].values():
            aid = (c or {}).get("asset_id", "")
            if aid and aid.startswith("C"):
                try:
                    max_n = max(max_n, int(aid[1:]))
                except ValueError:
                    pass
        # **既有条目不是"本轮新建"**（复核之七）：同一个 key 已经在库里时再登记一次
        # （抽取把它当成新角色、或换了一章重跑），既不能算新建，也不能覆盖它的
        # **首次来源**——`first_run_id` 必须从旧条目上带过来，否则"首次"被重写成本轮。
        _old = self._data["characters"].get(identifier)
        _existed = isinstance(_old, dict)
        self._data["characters"][identifier] = {
            "identifier": identifier,
            "asset_id": f"C{max_n + 1:02d}",
            "static_features": static_features,
            "dynamic_features": dynamic_features,
            "first_chapter": chapter_num,
            "last_chapter": chapter_num,
            "portraits": {},
        }
        if _existed and str(_old.get("first_run_id") or "").strip():
            self._data["characters"][identifier]["first_run_id"] = _old["first_run_id"]
        self._touched.add(identifier)
        if not _existed:
            self._created.add(identifier)

    def update_character_chapter(self, identifier: str, chapter_num: int) -> None:
        """更新角色最后出场章节."""
        char = self.get_character(identifier)
        if char:
            char["last_chapter"] = max(char.get("last_chapter", 0), chapter_num)
            self._touched.add(str(char.get("identifier") or identifier))

    def update_dynamic_features(self, identifier: str, dynamic_features: str) -> None:
        """更新角色可变特征（服装等），不变量 static_features 首次锁定不变."""
        char = self.get_character(identifier)
        if char and dynamic_features:
            char["dynamic_features"] = dynamic_features
            self._touched.add(str(char.get("identifier") or identifier))

    def set_portraits(self, identifier: str, portraits: dict) -> None:
        """设置角色肖像数据.

        portraits 格式: {front: {path, description}, side: {...}, back: {...}}
        """
        char = self.get_character(identifier)
        if char:
            char["portraits"] = portraits
            self._touched.add(str(char.get("identifier") or identifier))

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
        if bank_dir:
            self._dir = Path(bank_dir)
        else:
            from shared.paths import style_bank_dir
            self._dir = style_bank_dir()
        self._json_path = self._dir / "style_bank.json"
        self._data: dict = copy.deepcopy(_EMPTY_STYLE)

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
                self._data = copy.deepcopy(_EMPTY_STYLE)
        else:
            self._data = copy.deepcopy(_EMPTY_STYLE)
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入（**统一失败出口**：目录创建、临时写入、替换同一边界）.

        复核之九 P2 的同一责任范围：旧写法把 `mkdir` 留在 try 之外——目录建不出来
        （父路径被文件占住 / 只读）时抛 `OSError` 而不是"保存失败"；写盘统一走共享的
        `shared.json_io.atomic_dump_json`（写临时文件 + 替换 + 失败清理）。
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            from shared.json_io import atomic_dump_json
            atomic_dump_json(self._json_path, self._data, ensure_ascii=False, indent=2)
        except (OSError, ValueError, TypeError) as e:
            print(f"[bank] 保存风格库失败（**盘上文件原样保留**）: {e}")

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
