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
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# 资产画风隔离（2026-11-28 O9）
#
# 历史问题：`global/` 下的肖像与画风锚点没有画风标记，从 `anime` 切到 `gufeng`
# 真人风后，旧动漫肖像/锚点仍会被解析器命中并塞进新画风镜头。
#
# 解决：资产与锚点都带 `video_style` + `asset_contract_version`。缺字段的**历史**
# 资产按「风格纪元（style epoch）」处理：首次在某个画风下使用本代码时，把该画风记进
# `meta.style_stamp`，此后缺字段的旧资产视为**属于该纪元**；一旦 `VIDEO_STYLE` 换到
# 另一个画风，缺字段的旧资产就变成 `legacy_unstamped` 拒绝复用（因为无法证明它属于
# 新画风），而带标记的资产只在 `video_style == 当前画风` 时可用。
#
# 这样既不会在切换画风后串用旧资产，又不会把当前书已有的资产一次性判死。
# ---------------------------------------------------------------------------

ASSET_CONTRACT_VERSION = 2
#: 逃生开关：`0` 时把 `style_mismatch` 降级为告警（仍记录原因，不静默）。
STYLE_STRICT_ENV = "ASSET_STYLE_STRICT"


def normalize_video_style(value) -> str:
    return str(value or "").strip().lower()


def current_video_style() -> str:
    """当前生效画风（`VIDEO_STYLE`）；读不到时返回空串（不做过滤）.

    解析顺序与全仓配置一致：进程环境优先（shell 覆盖 `.env*`），其次
    `shared.config` 里的同名常量（若有）。`shared.config` 目前不导出 `VIDEO_STYLE`，
    所以以环境变量为准。
    """
    value = normalize_video_style(os.getenv("VIDEO_STYLE", ""))
    if value:
        return value
    try:
        from shared.config import VIDEO_STYLE  # type: ignore[attr-defined]
        return normalize_video_style(VIDEO_STYLE)
    except Exception:
        return ""


def style_strict() -> bool:
    return str(os.getenv(STYLE_STRICT_ENV, "1")).strip().lower() not in (
        "0", "false", "off", "no")


def resolve_style_state(entry_style, *, current: str, epoch: str = "") -> str:
    """判定单个资产在当前画风下能否复用（**逐资产来源判据**）.

    返回：`ok`（带标记且一致）/ `style_mismatch`（带标记但不一致）/
    `legacy_unstamped`（缺标记——无论库级风格纪元是什么，纪元都不能替代
    逐资产来源证明）/ `style_unknown`（当前画风读不到，明确失败）。

    `epoch` 仅保留签名兼容：S03 起它**不参与放行**；缺标记一律
    `legacy_unstamped`，由调用方按 `strict` 决定拒收或诊断降级。
    """
    current = normalize_video_style(current)
    entry = normalize_video_style(entry_style)
    if not current:
        return "style_unknown"
    if entry:
        return "ok" if entry == current else "style_mismatch"
    return "legacy_unstamped"


_STYLE_REASON_TEXT = {
    "style_mismatch": "资产画风与当前 VIDEO_STYLE 不一致",
    "legacy_unstamped": "资产缺少画风标记，无法证明其来源画风",
    "style_unknown": "无法读取当前 VIDEO_STYLE，按明确失败处理",
    "unsupported_contract": "资产合同版本高于本代码支持版本",
    "invalid_structure": "资产字段类型非法",
    "no_portraits": "没有肖像",
    "not_found": "角色/锚点不存在",
}


def style_reason_text(reason: str) -> str:
    return _STYLE_REASON_TEXT.get(str(reason), str(reason))


def stamp_style(data: dict, style: str | None = None) -> str:
    """把风格纪元写进 `data`（已是 dict 的那份）并返回纪元画风.

    **只改传入的那份**：调用方在候选副本上盖章，写盘失败时内存与盘上一个字节不动
    （与两个 bank 的「失败保留基线」约定一致）。
    """
    meta = data.setdefault("meta", {})
    stamp = meta.setdefault("style_stamp", {})
    if not normalize_video_style(stamp.get("video_style")):
        stamp["video_style"] = (normalize_video_style(style) if style is not None
                                else current_video_style())
        stamp["asset_contract_version"] = ASSET_CONTRACT_VERSION
        stamp["stamped_at"] = datetime.now().isoformat(timespec="seconds")
    return normalize_video_style(stamp.get("video_style"))


class BankLoadError(ValueError):
    """Bank 只读装载失败（结构化异常，调用方可按 `reason` 分派）.

    `reason` 取值：
      - `invalid_json`：文件存在但不是合法 JSON；
      - `invalid_structure`：JSON 合法但结构非法（顶层/字段类型不符）；
      - `unreadable`：文件存在但无法读取。

    文件缺失不属于异常：`load(read_only=True)` 返回空库并把
    `load_state` 置为 `"missing"`，调用方据此区分“没有库”和“库坏了”。
    """

    def __init__(self, path: str | Path, reason: str, message: str = ""):
        self.path = str(path)
        self.reason = str(reason)
        self.message = str(message or "")
        detail = f"{self.reason}: {self.path}"
        if self.message:
            detail = f"{detail}: {self.message}"
        super().__init__(detail)

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason, "message": self.message}


def _contract_problem(record: dict | None) -> str:
    """资产合同版本检查：`""` / `invalid_structure` / `unsupported_contract`."""
    if not isinstance(record, dict) or "asset_contract_version" not in record:
        return ""
    version = record.get("asset_contract_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return "invalid_structure"
    if version > ASSET_CONTRACT_VERSION:
        return "unsupported_contract"
    return ""


def style_epoch(data: dict | None) -> str:
    """读取资产库里的**风格纪元**（`meta.style_stamp.video_style`），纯读取.

    缺字段/类型非法时返回空串——查询不负责盖章；`stamp_style()` 才写。
    """
    if not isinstance(data, dict):
        return ""
    meta = data.get("meta")
    stamp = meta.get("style_stamp") if isinstance(meta, dict) else None
    return normalize_video_style(stamp.get("video_style") if isinstance(stamp, dict) else "")


def filter_portraits_by_style(
    portraits: dict,
    *,
    current: str,
    epoch: str,
    strict: bool | None = None,
) -> tuple[dict, list[dict]]:
    """按统一画风判据过滤一组肖像视图（纯读取）.

    返回 `(allowed, rejected)`；`rejected` 每项含 `view` / `reason` /
    `video_style`。与 `resolve_portraits()` 的视图级判定同源。
    """
    _strict = style_strict() if strict is None else bool(strict)
    allowed: dict = {}
    rejected: list[dict] = []
    if not normalize_video_style(current):
        for view, record in (portraits or {}).items():
            rejected.append({"view": str(view), "reason": "style_unknown",
                             "video_style": normalize_video_style(
                                 (record or {}).get("video_style")
                                 if isinstance(record, dict) else "")})
        return {}, rejected
    for view, record in (portraits or {}).items():
        if not isinstance(record, dict):
            rejected.append({"view": str(view), "reason": "invalid_structure",
                             "video_style": ""})
            continue
        problem = _contract_problem(record)
        if problem:
            rejected.append({"view": str(view), "reason": problem,
                             "video_style": normalize_video_style(
                                 record.get("video_style"))})
            continue
        state = resolve_style_state(record.get("video_style"), current=current,
                                    epoch=epoch)
        if state in ("style_mismatch", "legacy_unstamped"):
            rejected.append({"view": str(view), "reason": state,
                             "video_style": normalize_video_style(
                                 record.get("video_style"))})
            if _strict:
                continue
        allowed[view] = record
    return allowed, rejected


def filter_portrait_registry(
    registry: dict,
    *,
    bank: "CharacterBank | None" = None,
    style: str | None = None,
) -> tuple[dict, list[dict]]:
    """按画风判据过滤派生注册表（`character_portraits_registry.json`）.

    派生缓存**不能替代 Bank 守卫**：给了 `bank` 时逐角色走
    `resolve_portraits()`；被画风拒收的角色整条剔除，仍可用的角色只保留
    Bank 允许的视图。没有 Bank（或角色未登记）时退回逐视图判据。
    """
    allowed_registry: dict = {}
    rejected_rows: list[dict] = []
    current = normalize_video_style(style) if style is not None else ""
    epoch = ""
    if bank is not None:
        current, epoch = bank.style_context(style)
    elif style is not None:
        current = normalize_video_style(style)
    else:
        current = current_video_style()
    for ident, views in (registry or {}).items():
        if not isinstance(views, dict):
            rejected_rows.append({"identifier": str(ident), "view": "",
                                  "reason": "invalid_structure"})
            continue
        effective = views
        if bank is not None and hasattr(bank, "resolve_portraits"):
            resolved = bank.resolve_portraits(ident, style=style)
            reason = str(resolved.get("reason") or "")
            if not resolved.get("ok") and reason in ("style_mismatch", "legacy_unstamped"):
                rejected_rows.append({"identifier": str(ident), "view": "", "reason": reason})
                continue
            if resolved.get("ok"):
                bank_views = resolved.get("portraits") or {}
                effective = ({k: v for k, v in views.items() if k in bank_views}
                             or dict(bank_views))
        filtered, rejected = filter_portraits_by_style(
            effective, current=current, epoch=epoch)
        rejected_rows.extend({"identifier": str(ident), **row} for row in rejected)
        if filtered:
            allowed_registry[str(ident)] = filtered
    return allowed_registry, rejected_rows


def _bank_load(path: Path, *, read_only: bool, validator, empty: dict) -> dict:
    """两个 Bank 共用的装载边界（`load_state` + 结构化失败）.

    `read_only=False` 保持历史容错：损坏/非法结构打印后退回空库；
    `read_only=True` 抛出 `BankLoadError`，由调用方决定拒绝还是降级。
    """
    if not path.exists():
        return {"data": copy.deepcopy(empty), "state": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {"data": validator(payload), "state": "ok"}
    except json.JSONDecodeError as exc:
        error = BankLoadError(path, "invalid_json", str(exc))
    except BankLoadError as exc:
        error = exc
    except OSError as exc:
        error = BankLoadError(path, "unreadable", str(exc))
    if read_only:
        raise error
    print(f"[bank] 资产库读取失败，使用空库（{error.reason}）: {error}")
    return {"data": copy.deepcopy(empty), "state": error.reason}


def _validate_characters_payload(payload) -> dict:
    """CharacterBank 结构校验；非法时抛 `BankLoadError(invalid_structure)`."""
    if not isinstance(payload, dict):
        raise BankLoadError("characters_bank.json", "invalid_structure",
                            "顶层必须是对象")
    characters = payload.get("characters", {})
    if not isinstance(characters, dict):
        raise BankLoadError("characters_bank.json", "invalid_structure",
                            "characters 必须是对象")
    for ident, entry in characters.items():
        if not isinstance(entry, dict):
            raise BankLoadError("characters_bank.json", "invalid_structure",
                                f"角色 {ident!r} 的条目必须是对象")
        if "portraits" in entry and not isinstance(entry.get("portraits"), dict):
            raise BankLoadError("characters_bank.json", "invalid_structure",
                                f"角色 {ident!r} 的 portraits 必须是对象")
    meta = payload.get("meta", {})
    if not isinstance(meta, dict):
        raise BankLoadError("characters_bank.json", "invalid_structure",
                            "meta 必须是对象")
    loaded = dict(payload)
    loaded["characters"] = characters
    loaded.setdefault("meta", {"last_updated_chapter": 0, "total_characters": 0})
    return loaded


def _validate_style_payload(payload) -> dict:
    """StyleBank 结构校验；非法时抛 `BankLoadError(invalid_structure)`."""
    if not isinstance(payload, dict):
        raise BankLoadError("style_bank.json", "invalid_structure", "顶层必须是对象")
    video_style = payload.get("video_style")
    if video_style is not None and not isinstance(video_style, str):
        raise BankLoadError("style_bank.json", "invalid_structure",
                            "video_style 必须是字符串或 null")
    meta = payload.get("meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise BankLoadError("style_bank.json", "invalid_structure",
                                "meta 必须是对象")
        stamp = meta.get("style_stamp")
        if stamp is not None and not isinstance(stamp, dict):
            raise BankLoadError("style_bank.json", "invalid_structure",
                                "meta.style_stamp 必须是对象")
        epoch = stamp.get("video_style") if isinstance(stamp, dict) else None
        if epoch is not None and not isinstance(epoch, str):
            raise BankLoadError("style_bank.json", "invalid_structure",
                                "meta.style_stamp.video_style 必须是字符串或 null")
    loaded = dict(payload)
    for k, v in _EMPTY_STYLE.items():
        loaded.setdefault(k, v)
    return loaded


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
        #: 最近一次 `load()` 的结果：`unloaded` / `ok` / `missing` / 结构化失败原因。
        #: `read_only=True` 时没有目录副作用，调用方靠它区分“没有库”和“库坏了”。
        self.load_state: str = "unloaded"
        #: 本次进程**登记/更新过**的角色名（save 时给它们盖 `last_run_id`）——
        #: 只标被这一轮碰过的条目：整库覆盖会让"来源"退化成"最后一次写库的人"。
        self._touched: set = set()
        #: 本次**新建**的条目（只有它们能盖 `first_run_id`）。
        #: 把 `first_run_id` 也盖给"本轮之前就存在"的条目 = 伪造来源：
        #: 它明明不是本轮首次登记的（实测 10-02 一轮里，老角色被盖成本轮 first_run_id）。
        self._created: set = set()

    # -- 读写 ----------------------------------------------------------------

    def load(self, *, read_only: bool = False) -> dict:
        """从磁盘加载 bank，不存在则返回空结构.

        `read_only=True`：查询/预检专用——**不建目录、不保存、不盖章**；
        文件缺失返回空库并把 `load_state` 置为 `"missing"`；JSON 损坏、
        结构非法或不可读抛 `BankLoadError`（带 `reason`），不静默降级。
        默认 `read_only=False` 保持历史容错与目录创建语义。
        """
        result = _bank_load(self._bank_path, read_only=read_only,
                            validator=_validate_characters_payload, empty=_EMPTY_BANK)
        self._data = result["data"]
        self.load_state = result["state"]
        if not read_only:
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
        # 2026-11-28 O9：风格纪元盖在**副本**上——写盘失败时内存不得被改动
        stamp_style(_cand)
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

    def get_all_portraits_as_registry(self, *, style: str | None = None) -> dict:
        """返回 ViMax 兼容的 character_portraits_registry dict.

        格式: {identifier: {front/side/back: {path, description}}}
        只包含已生成肖像的角色.

        2026-11-28 O9：按**当前画风**过滤——画风不一致或历史资产已离开其风格纪元时
        不再出现在注册表里（`resolve_portraits` 里有逐条原因）。旧调用方签名不变。
        """
        registry = {}
        for ident in self._data.get("characters", {}):
            resolved = self.resolve_portraits(ident, style=style)
            if resolved.get("portraits"):
                registry[ident] = resolved["portraits"]
        return registry

    # -- 画风隔离（2026-11-28 O9）------------------------------------------

    def ensure_style_stamp(self, style: str | None = None) -> str:
        """返回本书资产库的**风格纪元**；首次调用时把它钉在当前画风上.

        只改内存，随下一次 `save()` 落盘（`load()` 保持只读语义）。
        """
        return stamp_style(self._data, style)

    def style_context(self, style: str | None = None) -> tuple[str, str]:
        """`(当前画风, 风格纪元)`；当前画风缺省读 `VIDEO_STYLE`.

        **纯读取**：纪元只从已装载数据里读，查询不盖章、不改 `_data`；
        盖章由 `ensure_style_stamp()`（显式）或 `save()`（候选副本）负责。
        """
        current = normalize_video_style(style) if style is not None else current_video_style()
        return current, style_epoch(self._data)

    def resolve_portraits(self, identifier: str, *, style: str | None = None) -> dict:
        """按画风解析某角色的可用肖像；返回 `{ok, reason, video_style, portraits, rejected}`.

        S03 默认政策：**逐视图**要求明确的画风标记——缺标记/空白/null 一律
        `legacy_unstamped`，即使库里有风格纪元也不放行；角色级 `video_style`
        只能用于拒收，不能证明未标记视图的来源。`ASSET_STYLE_STRICT=0` 时
        降级放行并如实返回 `strict=False` 与原因，不报告成严格通过。
        """
        current, epoch = self.style_context(style)
        strict = style_strict()
        char = self.get_character(identifier)
        if not char:
            return {"ok": False, "reason": "not_found", "identifier": identifier,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "portraits": {}}
        if not current:
            return {"ok": False, "reason": "style_unknown", "identifier": identifier,
                    "video_style": "", "epoch": epoch, "strict": strict,
                    "message": style_reason_text("style_unknown"), "portraits": {}}
        entry_problem = _contract_problem(char)
        if entry_problem:
            return {"ok": False, "reason": entry_problem, "identifier": identifier,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "portraits": {}}
        entry_state = resolve_style_state(char.get("video_style"), current=current,
                                          epoch=epoch)
        entry_rejected = entry_state in ("style_mismatch",)
        if entry_rejected and strict:
            return {"ok": False, "reason": entry_state, "identifier": identifier,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "message": style_reason_text(entry_state), "portraits": {}}
        portraits: dict = {}
        rejected: list[dict] = []
        for view, record in (char.get("portraits") or {}).items():
            if not isinstance(record, dict):
                rejected.append({"view": str(view), "reason": "invalid_structure",
                                 "video_style": ""})
                continue
            problem = _contract_problem(record)
            if problem:
                rejected.append({"view": str(view), "reason": problem,
                                 "video_style": normalize_video_style(
                                     record.get("video_style"))})
                continue
            state = resolve_style_state(record.get("video_style"), current=current,
                                        epoch=epoch)
            if state in ("style_mismatch", "legacy_unstamped"):
                rejected.append({"view": str(view), "reason": state,
                                 "video_style": normalize_video_style(
                                     record.get("video_style"))})
                if strict:
                    continue
            portraits[str(view)] = record
        if not portraits:
            reason = rejected[0]["reason"] if rejected else "no_portraits"
            return {"ok": False, "reason": reason, "identifier": identifier,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "message": style_reason_text(reason), "portraits": {},
                    "rejected": rejected}
        reason = rejected[0]["reason"] if rejected else (
            entry_state if entry_state in ("style_mismatch",) else "ok")
        return {"ok": True, "reason": reason, "identifier": identifier,
                "video_style": current, "epoch": epoch, "strict": strict,
                "portraits": portraits, "rejected": rejected}

    def style_audit(self, *, style: str | None = None) -> dict:
        """全部资产的画风可用性审计（只读；给报告/测试用）."""
        current, epoch = self.style_context(style)
        rows = []
        for ident in self._data.get("characters", {}):
            resolved = self.resolve_portraits(ident, style=style)
            rows.append({"identifier": ident, "ok": bool(resolved.get("ok")),
                         "reason": resolved.get("reason"),
                         "video_style": resolved.get("video_style"),
                         "views": sorted(resolved.get("portraits") or {}),
                         "rejected": resolved.get("rejected") or []})
        return {"video_style": current, "epoch": epoch,
                "asset_contract_version": ASSET_CONTRACT_VERSION,
                "strict": style_strict(), "characters": rows}

    # -- 修改 ----------------------------------------------------------------

    def register_character(
        self,
        identifier: str,
        static_features: str,
        dynamic_features: str,
        chapter_num: int,
        *,
        video_style: str | None = None,
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
            # 2026-11-28 O9：登记即盖画风，历史条目由风格纪元兜底
            "video_style": (normalize_video_style(video_style) if video_style is not None
                            else current_video_style()),
            "asset_contract_version": ASSET_CONTRACT_VERSION,
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

    def set_portraits(self, identifier: str, portraits: dict,
                      *, video_style: str | None = None) -> None:
        """设置角色肖像数据.

        portraits 格式: {front: {path, description}, side: {...}, back: {...}}

        2026-11-28 O9：逐视图盖 `video_style` + `asset_contract_version`（解析时
        按画风过滤）；未显式给画风时取当前 `VIDEO_STYLE`。

        S03：本方法是**显式写入新肖像**的入口，传入的记录视为本次新数据——
        已有标记被当前画风覆盖（而不是 setdefault 留下旧标记）；调用方不得
        用它给历史图片补来源。查询/普通保存不会走到这里。
        """
        char = self.get_character(identifier)
        if char:
            style = (normalize_video_style(video_style) if video_style is not None
                     else current_video_style())
            stamped = {}
            for view, record in (portraits or {}).items():
                rec = dict(record or {})
                if style:
                    rec["video_style"] = style
                rec["asset_contract_version"] = ASSET_CONTRACT_VERSION
                stamped[view] = rec
            char["portraits"] = stamped
            if style and not normalize_video_style(char.get("video_style")):
                char["video_style"] = style
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
        #: 与 `CharacterBank.load_state` 同义（`unloaded/ok/missing/失败原因`）。
        self.load_state: str = "unloaded"

    # -- 读写 ----------------------------------------------------------------

    def load(self, *, read_only: bool = False) -> dict:
        """从磁盘加载，不存在返回空结构.

        `read_only=True` 时无目录副作用；损坏/非法结构抛 `BankLoadError`
        （`not_found` 仍返回空库 + `load_state="missing"`）。
        """
        result = _bank_load(self._json_path, read_only=read_only,
                            validator=_validate_style_payload, empty=_EMPTY_STYLE)
        self._data = result["data"]
        self.load_state = result["state"]
        if not read_only:
            self._dir.mkdir(parents=True, exist_ok=True)
        return self._data

    def save(self) -> None:
        """原子写入（**统一失败出口**：目录创建、临时写入、替换同一边界）.

        复核之九 P2 的同一责任范围：旧写法把 `mkdir` 留在 try 之外——目录建不出来
        （父路径被文件占住 / 只读）时抛 `OSError` 而不是"保存失败"；写盘统一走共享的
        `shared.json_io.atomic_dump_json`（写临时文件 + 替换 + 失败清理）。
        """
        # 2026-11-28 O9：风格纪元随锚点一起落盘；写盘失败时内存原样保留
        payload = copy.deepcopy(self._data)
        stamp_style(payload)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            from shared.json_io import atomic_dump_json
            atomic_dump_json(self._json_path, payload, ensure_ascii=False, indent=2)
        except (OSError, ValueError, TypeError) as e:
            print(f"[bank] 保存风格库失败（**盘上文件原样保留**）: {e}")
            return
        self._data = payload

    # -- 查询 ----------------------------------------------------------------

    def is_locked(self) -> bool:
        """风格锚点是否已锁定."""
        return bool(self._data.get("is_locked", False))

    # -- 画风隔离（2026-11-28 O9）------------------------------------------

    def ensure_style_stamp(self, style: str | None = None) -> str:
        """返回风格纪元；首次调用钉在当前画风上（只改内存，随 save 落盘）."""
        return stamp_style(self._data, style)

    def style_context(self, style: str | None = None) -> tuple[str, str]:
        """`(当前画风, 风格纪元)`；纯读取，查询不盖章（见 `CharacterBank.style_context`）."""
        current = normalize_video_style(style) if style is not None else current_video_style()
        return current, style_epoch(self._data)

    def get_anchor_for_style(self, style: str | None = None) -> dict:
        """按画风解析风格锚点；缺标记/不一致按 S03 默认政策拒收.

        `ASSET_STYLE_STRICT=0` 时降级放行并返回 `strict=False` 与原因；
        当前画风不可确定、字段非法或合同版本过高时不因该开关放行。
        """
        current, epoch = self.style_context(style)
        strict = style_strict()
        path = str(self._data.get("style_anchor_image") or "")
        if not path or not Path(path).exists():
            default = self._dir / "style_anchor.png"
            path = str(default.resolve()) if default.exists() else ""
        if not path:
            return {"ok": False, "reason": "not_found", "path": None,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "message": style_reason_text("not_found")}
        if not current:
            return {"ok": False, "reason": "style_unknown", "path": None,
                    "candidate": path, "video_style": "", "epoch": epoch,
                    "strict": strict,
                    "message": style_reason_text("style_unknown")}
        problem = _contract_problem(self._data)
        if problem:
            return {"ok": False, "reason": problem, "path": None,
                    "candidate": path, "video_style": current, "epoch": epoch,
                    "strict": strict, "message": style_reason_text(problem)}
        state = resolve_style_state(self._data.get("video_style"), current=current,
                                    epoch=epoch)
        if state in ("style_mismatch", "legacy_unstamped") and strict:
            return {"ok": False, "reason": state, "path": None, "candidate": path,
                    "video_style": current, "epoch": epoch, "strict": strict,
                    "asset_video_style": normalize_video_style(self._data.get("video_style")),
                    "message": style_reason_text(state)}
        return {"ok": True, "reason": "ok" if state == "ok" else state,
                "path": path, "video_style": current, "epoch": epoch,
                "strict": strict,
                "asset_video_style": normalize_video_style(self._data.get("video_style"))}

    def style_audit(self, *, style: str | None = None) -> dict:
        resolved = self.get_anchor_for_style(style)
        return {"video_style": resolved.get("video_style"),
                "epoch": resolved.get("epoch"),
                "asset_contract_version": ASSET_CONTRACT_VERSION,
                "strict": style_strict(),
                "locked": self.is_locked(), "anchor": resolved}

    def get_anchor_path(self) -> str | None:
        """返回当前画风下可用的 style_anchor.png 绝对路径；不可用返回 None.

        2026-11-28 O9：不再无脑返回——画风不一致 / 历史资产已离开其风格纪元时返回
        `None`（原因见 `get_anchor_for_style()`），避免旧动漫锚点被塞进新画风镜头。
        """
        resolved = self.get_anchor_for_style()
        path = resolved.get("path")
        if not path:
            if resolved.get("reason") not in ("not_found",):
                print("[bank] 风格锚点按画风拒绝复用：%s（%s）"
                      % (resolved.get("reason"), resolved.get("message", "")))
            return None
        return str(path)

    def get_anchor_chapter(self) -> int:
        """返回锁定锚点的章节号."""
        return self._data.get("style_anchor_chapter", 0)

    # -- 修改 ----------------------------------------------------------------

    def lock(
        self,
        anchor_image_path: str,
        chapter_num: int,
        *,
        anchor_kind: str = "environment",
        allow_characters: bool = False,
        expected_persons: int | None = None,
        video_style: str | None = None,
    ) -> bool:
        """锁定风格锚点：校验无人物纯画风锚并写为规范 PNG 到 style_bank/ + 写 JSON."""
        src = Path(anchor_image_path)
        if not src.exists():
            print(f"[bank] 风格锚点源文件不存在: {anchor_image_path}")
            return False

        # F3: validate readability & reject character-containing anchor unless explicitly overridden
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(src) as _im:
                _im.verify()
            with _PILImage.open(src) as _im2:
                _im2.load()
                src_format = str(_im2.format or "").upper()
                img_size = tuple(_im2.size)
        except Exception as exc:
            print(f"[bank] 风格锚点图片损坏或不可读 ({src}): {exc}")
            return False

        if expected_persons is not None and int(expected_persons) > 0 and not allow_characters:
            print(
                f"[bank] 拒绝将含人物图像({expected_persons}人)锁为画风锚点: {src}；"
                "请使用纯空镜/纯环境画风标准帧，避免串人/串景"
            )
            return False

        sidecar_candidate = src.with_name(f"{src.stem}_hallucination.json")
        if sidecar_candidate.is_file() and not allow_characters:
            try:
                sc_data = json.loads(sidecar_candidate.read_text(encoding="utf-8"))
                pc = sc_data.get("person_count")
                if isinstance(pc, int) and pc > 0:
                    print(
                        f"[bank] 拒绝将含 {pc} 名人物的帧锁为画风锚点 ({src})；"
                        "请改用无人物纯环境画风标准帧"
                    )
                    return False
            except Exception:
                pass

        self._dir.mkdir(parents=True, exist_ok=True)
        dst = self._dir / "style_anchor.png"
        tmp_dst = self._dir / "style_anchor.png.tmp"
        try:
            if src_format == "PNG":
                shutil.copy2(str(src), str(tmp_dst))
            else:
                from PIL import Image as _PILImage
                with _PILImage.open(src) as _conv:
                    _conv.convert("RGB").save(str(tmp_dst), format="PNG")
            os.replace(tmp_dst, dst)
            print(f"[bank] 风格锚点已锁定: {dst} ({anchor_kind}, {img_size[0]}x{img_size[1]})")
        except OSError as e:
            try:
                tmp_dst.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"[bank] 复制风格锚点失败: {e}")
            return False

        import hashlib as _hl
        try:
            anchor_sha = _hl.sha256(dst.read_bytes()).hexdigest()
        except OSError:
            anchor_sha = ""

        self._data["style_anchor_chapter"] = chapter_num
        self._data["style_anchor_image"] = str(dst.resolve())
        self._data["anchor_kind"] = str(anchor_kind or "environment")
        self._data["allow_characters"] = bool(allow_characters)
        self._data["source_path"] = str(src.resolve())
        self._data["anchor_sha256"] = anchor_sha
        self._data["is_locked"] = True
        self._data["created_at"] = datetime.now().isoformat()
        # 2026-11-28 O9：锚点与风格纪元一起钉住画风（此后切画风会被硬隔离）
        style = (normalize_video_style(video_style) if video_style is not None
                 else current_video_style())
        if style:
            self._data["video_style"] = style
        self._data["asset_contract_version"] = ASSET_CONTRACT_VERSION
        self.ensure_style_stamp(style or None)
        self.save()
        return True


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
