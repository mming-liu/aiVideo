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

import copy
import json
import re
from pathlib import Path


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


_EMPTY_BANK: dict = {
    "objects": {},
    "meta": {"last_updated_chapter": 0, "total_objects": 0},
}


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


class SceneObjectsBank:
    """跨章节场景物体尺寸持久化."""

    def __init__(self, bank_dir: str | None = None):
        if bank_dir:
            self._bank_dir = Path(bank_dir)
        else:
            from shared.paths import objects_bank_dir
            self._bank_dir = objects_bank_dir()
        self._bank_path = self._bank_dir / "objects_bank.json"
    # **空模板必须深复制**（复核之七）：`dict(_EMPTY_BANK)` 只复制了外层——`characters`
    # 与内层 `meta` 仍是**模块级同一个对象**，于是新建空库会把上一个实例写的条目带进来
    # （实测：同一进程里先建 A 库再建 B 库，B 一开库就带着 A 的角色；跨运行还可能把别的
    # 章节的实体当成已存在）。空库是**每个实例一份**状态。
        self._data: dict = copy.deepcopy(_EMPTY_BANK)
        #: 本次进程**登记/更新过**的物体名（save 时盖来源 run_id；整库覆盖会让
        #: "来源"退化成"最后一次写库的人"）
        self._touched: set = set()
        #: 本次**新建**的条目（只有它们能盖 `first_run_id`；把 first 盖给老条目
        #: 等于伪造来源——它明明不是本轮首次登记的）
        self._created: set = set()

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
                self._data = copy.deepcopy(_EMPTY_BANK)
        else:
            self._data = copy.deepcopy(_EMPTY_BANK)
        self._bank_dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self, run_id: str = "", call_ids=None) -> bool:
        """原子写入：**在候选副本上**盖来源，写盘成功后才确认内存状态、消费本次变更.

        `run_id`（2026-10-02 复核）：记下**这一轮的来源身份**——`meta.last_run_id` /
        `meta.last_written_at`，以及本次新增/更新条目的 `first_run_id` / `last_run_id`。
        旧库没有这些字段是历史（重构后未重跑），读取方按"缺字段 = 来源未知"处理，
        这里**不回溯伪造**。

        `call_ids`（复核之七）：这一轮**确实发生**的调用（由台账给出）。条目上记
        `last_call_ids` 与 `last_source`（`call` / `mechanical`）——**不编造模型调用**。

        **保存边界**（复核之七）：盖来源与写盘都在**副本**上做，成功才换成内存状态并
        **清空本次变更集合** ⇒ 连续保存不重盖首次来源、未更新条目不被重标；写盘失败时
        基线一个字节不改、变更集合留着，**后续重试能正确保存**。

        Returns: `True` = 真的写进了盘（调用方据此才可以说"已保存"）。
        """
        _rid = str(run_id or "").strip() or _current_run_id()
        # **副本先建，save 期的三件事全部只在副本上做**（复核 2026-10-02 之八 P1）：
        # 合并（`_consolidate_aliases`）、统计、来源盖章。旧写法在复制**之前**改内存——
        # 别名合并已经把两条并成一条时写盘失败：盘上一个字节没变，内存却少了一条，
        # 调用方接着按内存跑（读到的"事实"与盘上不一致，重试也补不回来）。
        _cand = copy.deepcopy(self._data)
        self._consolidate_aliases(_cand)
        _cand["meta"]["total_objects"] = len(_cand.get("objects", {}))
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
            print(f"[bank] 保存场景物体库失败（**盘上文件原样保留**，本次变更待重试）: {e}")
            return False
        self._data = _cand
        self._touched.clear()
        self._created.clear()
        return True

    def _stamp_run_id(self, data: dict, run_id: str, call_ids=None) -> dict:
        """把来源记到**副本** `data` 的 meta 与本次登记过的条目上（不改 `self._data`）."""
        data.setdefault("meta", {})["last_run_id"] = run_id
        try:
            from datetime import datetime
            data["meta"]["last_written_at"] = datetime.now().isoformat(timespec="seconds")
        except Exception:
            pass
        _cids = [str(c) for c in (call_ids or []) if str(c or "").strip()]
        for _k in self._touched:
            _o = data.get("objects", {}).get(_k)
            if not isinstance(_o, dict):
                continue
            # 既有首次来源不得覆盖（复核之七）
            if _k in self._created and not str(_o.get("first_run_id") or "").strip():
                _o["first_run_id"] = run_id
            _o["last_run_id"] = run_id
            _o["last_call_ids"] = _source_call_ids(_cids, _o.get("last_call_ids"))
            _o["last_source"] = "call" if _cids else "mechanical"
        return data

    def _consolidate_aliases(self, data: dict | None = None) -> list:
        """同物异名合并（2026-09-12 根因修复）：≤2 字泛名是某长名的子串 → 折叠.

        LLM 抽取会把同一实体注册两条并给出互相矛盾的尺寸（实测：「舟」长5.5米
        宽1.5米 vs「无篷舴艋舟」宽1.4米），prompt 把两份尺寸参考都喂给模型 →
        帧描述出现两套打架的船体规格。折叠规则：泛名降级为长名条目的
        consistent_term；尺寸取「长宽齐全」的一侧（更完整推导优先），缺项互补；
        章节取 min/max。幂等，每次 save 归一。

        `data`（复核 2026-10-02 之八 P1）：**在传入的那份上合并**（save 传的是候选
        副本）；缺省仍是 `self._data`（兼容既有调用）。被合并到的目标条目**内容被改过**
        ⇒ 逐个记进本次变更集合（`_touched`），来源才会盖在它身上——旧写法只标记了
        调用方登记过的名字，合并出来的目标漏标。

        Returns: 本次被合并到的目标条目名（保序、去重）。
        """
        _target = self._data if data is None else data
        objects = _target.get("objects", {})
        _merged: list = []
        folded = True
        while folded:
            folded = False
            for short in list(objects):
                if len(short) > 2:
                    continue
                target = next(
                    (long_name for long_name in objects
                     if long_name != short and short in long_name),
                    None,
                )
                if not target:
                    continue
                _s, _l = objects.pop(short), objects[target]
                _s_complete = bool(_s.get("length_m") and _s.get("width_m"))
                _l_complete = bool(_l.get("length_m") and _l.get("width_m"))
                for k in ("length_m", "width_m", "height_m", "capacity"):
                    if _s.get(k) is None:
                        continue
                    if _l.get(k) is None or (_s_complete and not _l_complete):
                        _l[k] = _s[k]
                terms = (set(_l.get("consistent_terms", []) or [])
                         | set(_s.get("consistent_terms", []) or [])
                         | {short})
                _l["consistent_terms"] = sorted(t for t in terms if t)
                _l["first_chapter"] = min(
                    _l.get("first_chapter", 9999), _s.get("first_chapter", 9999))
                _l["last_chapter"] = max(
                    _l.get("last_chapter", 0), _s.get("last_chapter", 0))
                # 目标条目**内容变了** ⇒ 变更标记（来源盖章按它走）
                self._touched.add(target)
                if target not in _merged:
                    _merged.append(target)
                folded = True
                break
        return _merged

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
            self._touched.add(ident)
        else:
            entry = dict(obj)
            entry.setdefault("first_chapter", chapter_num)
            entry["last_chapter"] = chapter_num
            entry["consistent_terms"] = list(entry.get("consistent_terms", []) or [])
            self._data["objects"][ident] = entry
            self._touched.add(ident)
            self._created.add(ident)

        self._data["meta"]["last_updated_chapter"] = max(
            self._data["meta"]["last_updated_chapter"], chapter_num
        )


# ===========================================================================
# 船尺寸动态解析（v6.60.1 硬编码清理：v6_43 规则1 / T1 boat_width 共用）
# ===========================================================================
# 历史教训（v6.52）：硬编码「宽1.4米」与 scene_objects 动态锚（如 1.6米）并存时，
# 模型两侧都不信、船宽随机抽奖——尺寸只允许一个来源：scene_objects。
# 本组函数是唯一的匹配/取值入口，step01（ctx.script 内存）与 step02（bridge
# 重读 script_result.json）共用同一协议，防两套匹配漂移。

# scene_objects 未命中船规格时的兜底宽（v6.43 原硬编码值，保留为兜底而非删除）
BOAT_FALLBACK_WIDTH_M = 1.4

# B5 比例后验阈值（与 shared/vimax_bridge._inject_object_size_anchor 同口径）：
# 长/宽比 <1.5 或 >12 视为 LLM 推导的尺寸可疑，宽度不可信
_BOAT_RATIO_MIN, _BOAT_RATIO_MAX = 1.5, 12.0


def iter_matched_scene_objects(
    scene_objects: dict, loc: str, probe: str,
) -> list[tuple[str, dict]]:
    """v6.52 匹配协议：location 模糊匹配（公共连续子串 ≥2 字）∪ 镜头全文子串匹配.

    匹配源 = 物体名 + consistent_terms（统一称呼）+ subtype（形制字）。
    返回 [(name, obj)]，保持注册表顺序。
    """
    from shared.assets_bank import scene_matches_location
    matched: list[tuple[str, dict]] = []
    for name, obj in (scene_objects or {}).items():
        if not isinstance(obj, dict):
            continue
        match_sources = [name] + list(obj.get("consistent_terms", []) or [])
        subtype = obj.get("subtype", "")
        if subtype:
            match_sources.append(subtype)
        loc_matched = any(
            scene_matches_location(src, loc) for src in match_sources if src
        )
        text_matched = any(src and src in probe for src in match_sources if src)
        if loc_matched or text_matched:
            matched.append((name, obj))
    return matched


def resolve_boat_spec(shot: dict, scene_objects: dict) -> dict | None:
    """找本镜关联的船类场景物体（v6_43 规则1 / T1 boat_width 的尺寸来源）.

    判定：category 含「船/舟」，或 name/subtype 含「舟」。
    未命中返回 None（调用方走 BOAT_FALLBACK_WIDTH_M 兜底）。
    """
    loc = str(shot.get("location", "") or shot.get("scene", "") or "")
    probe = " ".join(filter(None, [
        loc,
        shot.get("ff_desc", "") or "",
        shot.get("lf_desc", "") or "",
        shot.get("motion_desc", "") or shot.get("prompt", "") or "",
        shot.get("main_subject", "") or "",
    ]))
    for name, obj in iter_matched_scene_objects(scene_objects or {}, loc, probe):
        category = str(obj.get("category", ""))
        subtype = str(obj.get("subtype", ""))
        if ("船" in category or "舟" in category
                or "舟" in subtype or "舟" in name):
            return obj
    return None


def _ratio_trusts_width(obj: dict) -> bool:
    """B5 比例后验：长宽比离谱时宽度不可信（与 bridge 注入侧同口径）."""
    length, width = obj.get("length_m"), obj.get("width_m")
    if not length or not width:
        return True  # 缺长度时无从验证，宽度按可信处理
    try:
        ratio = float(length) / float(width)
    except (TypeError, ValueError):
        return False
    return _BOAT_RATIO_MIN <= ratio <= _BOAT_RATIO_MAX


def boat_width_text(shot: dict, scene_objects: dict) -> str:
    """本镜船宽文本「宽X米」：scene_objects 动态优先，未命中走兜底常量.

    T1 boat_width（窄身→宽X米）消费。
    """
    spec = resolve_boat_spec(shot, scene_objects)
    if spec and spec.get("width_m") and _ratio_trusts_width(spec):
        try:
            return f"宽{float(spec['width_m']):g}米"
        except (TypeError, ValueError):
            pass
    return f"宽{BOAT_FALLBACK_WIDTH_M:g}米"


def build_boat_replacement(shot: dict, scene_objects: dict) -> str:
    """v6_43 规则1 的替换文本：小舟 → 无篷舴艋舟（长X米宽Y米[，可容N人]）.

    尺寸优先 scene_objects（含 B5 比例后验）；未命中任何船规格时回退
    历史硬编码文本「无篷舴艋舟（宽1.4米，可容3人）」（行为保持）。
    """
    spec = resolve_boat_spec(shot, scene_objects)
    if not spec:
        return f"无篷舴艋舟（宽{BOAT_FALLBACK_WIDTH_M:g}米，可容3人）"
    length, width = spec.get("length_m"), spec.get("width_m")
    if width and not _ratio_trusts_width(spec):
        width = None  # 比例离谱：宽度不可信，不写
    frags = []
    try:
        if length:
            frags.append(f"长{float(length):g}米")
        if width:
            frags.append(f"宽{float(width):g}米")
    except (TypeError, ValueError):
        pass
    if not frags:
        return "无篷舴艋舟"
    cap_txt = ""
    capacity = spec.get("capacity")
    if capacity:
        # capacity 原文可能是「可容2-3人（杨旭、听香、张十三） + 炭炉、酒菜…等」
        # 这类 LLM 全句（2026-09-06 hook prompt 实测「可容可容…」+ 括号不闭合 +
        # 半截截断的直接根因）。规范：剥掉「可容」前缀/「人」尾缀，再截到人数字面
        # 为止（拒绝把载荷清单拼进 prompt）。
        cap = str(capacity).strip()
        cap = re.sub(r"^可容", "", cap)
        cap = re.sub(r"人?\s*$", "", cap)
        # 截断：只保留到第一个（前缀限定词/分隔符——「2-3人（杨旭…）」→「2-3」
        m = re.match(r"[0-9０-９.\-－~～]+", cap)
        if m:
            cap = m.group(0)
        else:
            cap = ""
        if cap:
            cap_txt = f"，可容{cap}人"
    return f"无篷舴艋舟（{''.join(frags)}{cap_txt}）"
