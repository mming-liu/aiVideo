# -*- coding: utf-8 -*-
"""prompt 矛盾自检的**词表与判据参数**（内容无关 + 可按项目覆盖）.

## 为什么单独成模块

`shared/prompt_contradiction.py` 原先在函数体里写死了四组词：

* `_BLADE_WORDS`（刃器同类）
* `_check_shadow_contradiction` 里的 `黑影` / `人影`
* `_check_ff_lf_stage_conflict` 里的 `pre_state` 与 `injury_anchor`
  （`「伤处白袍」「一片暗红血迹洇开」…`）

后两组是从 2026-08 某一本书的真实 prompt 里提炼的措辞。后果是：同一个判据
换一本没有「白袍伤描写」、也没有「水下黑影伏笔」的书，就**永远不会触发**——
判据看起来在跑，实际只对一本书生效。

本模块把词表外置，并给出**通用中文视觉词汇**作为默认值：

* 失效模式是「前态词 + 伤情结束态」同时出现 ⇒ 与具体书无关，只与中文措辞有关；
* 失效模式是「禁令条款禁掉前文正要出现的东西」⇒ 与具体书无关，只与结构有关。

## 覆盖方式

项目级覆盖（可选）::

    PROMPT_CONTRA_LEXICON=config/prompt_contradiction_lexicon.json

覆盖文件是**增量**的，不复制整份默认值::

    {
      "schema": "prompt_contradiction_lexicon.overlay.v1",
      "add":  {"injury_terms": ["吐血"], "body_or_garment_terms": ["护心镜"]},
      "set":  {"prohibition_mode": "enumerate"}
    }

`add` 追加到同名列表（去重、保序），`set` 替换标量或整表。未知键 **直接报错**
——配置写错字不许静默失效，否则"改了配置以为判据收紧了"这件事无法被发现。

## 精度优先

`find_contradictions` 在默认严格模式下**抛错拦帧**，所以这里的默认值宁可窄、
不可宽：每条默认词都是判断「两段描述能否同时成立」所必需的最小集合，不是
"同类词大表"。想放宽是配置的事，不是默认值的事。

`prohibition_mode` 两种取值：

* `"explicit"`（默认）——只按 `prohibition_terms` 里的词判「禁令 vs 需要」；
* `"enumerate"`——从「禁止…」子句里机械切出 2–4 字被禁名词再判。
  更强，但会引入切词噪声，**默认关闭**，要开得自己在覆盖文件里显式声明。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "prompt_contradiction_lexicon.v1"
OVERLAY_SCHEMA = "prompt_contradiction_lexicon.overlay.v1"
VERSION = "2026-11-14.1"

#: 覆盖文件的环境变量名。
ENV_VAR = "PROMPT_CONTRA_LEXICON"

#: 默认词表。**通用中文视觉词汇**，不含任何具体作品的人名、地名、服饰专名。
DEFAULTS: dict = {
    "schema": SCHEMA,
    "version": VERSION,
    # ── C1 刃器同类 ────────────────────────────────────────────────────
    # 判「持有 X」与「否认 Y」是否指向同一类物体。X/Y 互为子串即同类，
    # 所以这里只需覆盖常见器类；「匕首 vs 短刃」这类同义靠本表连起来。
    "blade_terms": (
        "匕首", "短刃", "短刀", "长刀", "刀", "剑", "长剑", "短剑",
        "枪", "长枪", "斧", "锤", "戟", "矛", "戈", "鞭",
    ),
    # ── C2 禁令 vs 需要 ────────────────────────────────────────────────
    # 默认只认这几个"画面上会占一块实体区域、被禁掉就一定丢信息"的名词。
    # 判据本身是结构的（前文需要 + 禁令禁掉），词表只决定"什么算一个实体"。
    "prohibition_terms": (
        "黑影", "人影", "倒影", "烟雾", "火星", "飞溅", "血迹",
    ),
    #: 命中位置落在这些子串里不算「禁令命中」——`真人` 是风格前缀的组成部分
    #: （「古风真人影视剧」跨词含「人影」），不是被禁的实体。
    "prohibition_ignore_substrings": ("真人",),
    #: 禁令条款的起始标记与最大长度（超出即不解析，避免吃掉整段）。
    "prohibition_clause_marker": "禁止",
    "prohibition_clause_max_chars": 60,
    "prohibition_mode": "explicit",
    #: `enumerate` 模式切词时剥掉的前缀与判定为"太泛"的停用词。
    "prohibition_token_prefixes": (
        "出现", "任何", "其他", "其它", "额外", "多余", "以及", "或者",
        "不要", "不得", "不能", "再", "的", "有", "含", "带",
    ),
    "prohibition_stop_terms": (
        "人物", "人像", "东西", "元素", "物体", "背景", "图层", "字幕",
        "文字", "水印", "标志", "标识", "画面", "场景", "内容", "部分",
    ),
    # ── C3 前态 vs 伤情结束态 ──────────────────────────────────────────
    # 前态：描述"还没发生/将要发生"的动词词组。
    "pre_state_terms": (
        "闭目", "未觉", "尚未", "即将", "蓄势", "潜伏", "伏于", "蛰伏",
        "未刺", "未及", "不知", "茫然",
    ),
    # 结束态：伤情**刚刚成立**的可见证据（起始体/过程体，不是静态血迹色）。
    #
    # 这里刻意**不含**「暗红」「血迹」「血痕」「伤处」「伤口」这类静态词——
    # 2026-11-14 在 R4/R5 真实候选上实测：只要把「暗红」收进来，判据立刻在
    # 3 段正常描写上误报（`杨旭…心口暗红；张十三右手尚未扬起`：前者是旧伤、
    # 后者是动作未起，两者毫不矛盾）。静态血迹词表示"伤一直在"，与"尚未发生"
    # 并不冲突；只有"刺入/洇开/渗血/中刀"这种**此刻正在发生**的词才构成矛盾。
    "injury_terms": (
        "刺入", "中刀", "中剑", "中枪", "被刺", "刀伤", "剑伤", "枪伤",
        "洇开", "渗血", "血涌", "溅血", "喷血", "溢血", "新伤", "裂开", "涌出",
    ),
    # 伤情必须落在身体或衣物上才算"可见结束态"。这一项是精度的关键：
    # 桌上有一滩血、远处地上有血痕，都不构成"角色已被刺"的画面。
    "body_or_garment_terms": (
        "肩", "胸口", "心口", "背", "腹", "腰", "臂", "腕", "手", "腿",
        "膝", "颈", "脸", "额", "衣", "袍", "衫", "袖", "裙", "甲",
        "裳", "襟", "领",
    ),
    #: 伤情词两侧各取多少字作为"是否落在身体/衣物上"的窗口。
    "injury_anchor_window": 8,
    #: 前态词与伤情词的**最大净间距**：超过即视为两处独立描写，不算自相矛盾。
    "pre_state_max_distance": 60,
}

#: 覆盖文件里允许出现的 `add` / `set` 键。
LIST_KEYS = tuple(k for k, v in DEFAULTS.items() if isinstance(v, tuple))
SCALAR_KEYS = tuple(k for k, v in DEFAULTS.items()
                    if isinstance(v, (str, int)) and not isinstance(v, bool)
                    and k not in ("schema", "version"))
META_KEYS = ("schema", "version")


class LexiconError(ValueError):
    """词表覆盖文件不合法（写错字必须报错，不许静默失效）."""


def _as_tuple(value) -> tuple:
    if isinstance(value, str):
        return (value,)
    return tuple(str(x) for x in value)


def _merge(base: dict, overlay: dict) -> dict:
    out = {k: (_as_tuple(v) if isinstance(v, tuple) else v) for k, v in base.items()}
    schema = overlay.get("schema")
    if schema not in (None, OVERLAY_SCHEMA):
        raise LexiconError("覆盖文件 schema 应为 %r，实际 %r" % (OVERLAY_SCHEMA, schema))
    for section in ("add", "set"):
        block = overlay.get(section) or {}
        if not isinstance(block, dict):
            raise LexiconError("覆盖文件的 %r 必须是对象" % section)
        for key, value in block.items():
            if key in META_KEYS:
                raise LexiconError("覆盖文件不得改写 %r" % key)
            if section == "add":
                if key not in LIST_KEYS:
                    raise LexiconError("add.%s 不是列表项（可加的键：%s）"
                                       % (key, ", ".join(LIST_KEYS)))
                merged = list(out[key])
                for item in _as_tuple(value):
                    if item and item not in merged:
                        merged.append(item)
                out[key] = tuple(merged)
            else:
                if key not in LIST_KEYS and key not in SCALAR_KEYS:
                    raise LexiconError("set.%s 不是已知键" % key)
                out[key] = _as_tuple(value) if key in LIST_KEYS else value
    _validate(out)
    return out


def _validate(doc: dict) -> None:
    for key in LIST_KEYS:
        value = doc.get(key)
        if not isinstance(value, tuple) or not value:
            raise LexiconError("%s 必须是非空字符串列表" % key)
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise LexiconError("%s 含空项或非字符串：%r" % (key, item))
    if doc.get("prohibition_mode") not in ("explicit", "enumerate"):
        raise LexiconError("prohibition_mode 只能是 explicit / enumerate，实际 %r"
                           % doc.get("prohibition_mode"))
    marker = doc.get("prohibition_clause_marker")
    if not isinstance(marker, str) or not marker:
        raise LexiconError("prohibition_clause_marker 必须是非空字符串")
    for key in ("prohibition_clause_max_chars", "injury_anchor_window",
                "pre_state_max_distance"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise LexiconError("%s 必须是正整数" % key)


def defaults() -> dict:
    """返回默认词表（深拷贝，调用方可安全修改）."""
    return _merge(DEFAULTS, {})


def load(overlay_path: str | os.PathLike | None = None) -> dict:
    """默认值 + 覆盖文件合并后的词表；`overlay_path` 为空则只返回默认值."""
    base = defaults()
    path = Path(overlay_path) if overlay_path else None
    if path is None:
        return base
    if not path.is_file():
        raise LexiconError("覆盖文件不存在：%s" % path)
    try:
        overlay = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise LexiconError("覆盖文件不是合法 JSON：%s（%s）" % (path, exc)) from None
    if not isinstance(overlay, dict):
        raise LexiconError("覆盖文件顶层必须是对象：%s" % path)
    merged = _merge(base, overlay)
    merged["overlay_path"] = str(path)
    return merged


_CACHE: dict = {}


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def current() -> dict:
    """进程内当前生效的词表（按 `PROMPT_CONTRA_LEXICON` 与环境文件 mtime 缓存）."""
    path = os.getenv(ENV_VAR, "").strip()
    key = ("", 0, 0) if not path else ("p",) + _stamp(Path(path)) + (path,)
    hit = _CACHE.get(key)
    if hit is None:
        hit = load(path or None)
        _CACHE.clear()
        _CACHE[key] = hit
    return hit


def reset_cache() -> None:
    """测试用：丢弃缓存（改环境变量或改覆盖文件后调用）."""
    _CACHE.clear()
