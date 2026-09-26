# -*- coding: utf-8 -*-
"""道具状态变化锚点 → 帧级检出与**完整**授权（v6.125，离线验证阶段；**未接线**）.

⚠️ 本模块按用户 2026-09-25 的决定**只做离线验证**，尚未接入任何生产路径：
生产接线点与顺序见文末「接线计划」。它不写盘、不发模型调用、不改稿件。

## 为什么需要它（根因，用户核定）

原文写明释放事件：

    杨旭眸中带着惊恐和难以置信的光芒……本来紧握鱼杆的左手也无力地垂在船舷，
    五指一松，**咬钩的肥鱼便拖着那钓杆急急逃走了**

但：

1. `chapter_outline` 没把它记成必须保留的事实（`required_facts`/`main_events`
   里都没有这条），生成提示词因此拿不到这个锚点；
2. 稿件（`script_result_20260924_215530.json`）从头到尾**没写释放事件**，
   shot3 只写「左手钓杆无力垂在船舷」，之后各帧 `props` 一直列着钓杆；
3. 现有确定性检查 `cross_field_checks._possession_issues` 只在**描述里已经出现
   释放词**时才去比对布局 → 稿件没写 → **报 0 条**（确定性层静默）；
4. 真矛盾只能由最终审计发现，而审计把它声明成 **8 镜联动**
   （shot3 `lf_desc` + shot4–10 `ff.props`）——**漏了** shot3 与 shot4–10 的
   `lf.props`（用户核对冻结稿确认）。所以即使把组上限放宽到 8，仍是**半条矛盾**。

→ 缺的是「**源文事实 → 稿件一致性**」这道检查，而不是组容量。

## 本模块给出的三件事

- `extract_prop_state_changes()`：从**原文**提取「道具释放」事实（带原文证据）。
  通用：道具词取自 `prop_assets`（含 `consistent_terms` 别名，如 钓杆/鱼杆），
  释放判定用既有释放词表 + 通用「拖/拽/拉/扯 + 走/逃/离」位移式；不写死任何
  具体小说/道具/角色。
- `missing_release_issue()`：锚点存在、稿件却**没有释放事件**且仍在持有 →
  一条 `prop_release_missing`（`repairable=False`）。修法是**重生或拒绝该候选**，
  **禁止**用「批量删 props」伪造剧情（用户口径）。
- `post_release_possession_issues()` + `uncovered_holding_fields()`：释放事件明确后，
  把**全部**仍声明持有的帧（`ff` / `lf` / `kfs`，一个不漏）落成精确到条目的
  修复目标；若给定的授权集盖不全这些帧 → 显式判「不完整」（半条矛盾）。

## 接线计划（用户口径，待验证通过后再做）

1. **源头锚点**：在现有可缓存的大纲提取中记录带原文证据的关键道具状态变化；
   给生成/重生提示词一个**独立短锚点**（不能只塞进 `required_facts`——agent_plan
   会把普通大纲锚点压到约 200 字，模型看不到）。
2. **BEST-GUARD 选稿前验证源文一致性**：有可信锚点时，缺释放事件 → 要求重生或
   显式拒绝该候选；事件明确后才给帧级精确目标。这样带欠账的历史稿不会仅凭
   「风险数低」反复入选。

## v6.127 之后仍未做的接线前提（用户 2026-09-25 复核）

- **事件关联取代措辞匹配**：「帧文本匹配」与「≤3 句因果窗口」目前**只作诊断**，
  不能作为生产判据。生产接线需要：大纲节拍里的原文事件带**可核对标识**，
  再关联到稿件镜头；且 `cause_at` 与**实际写出的** `release_at` **分开记录**
  （本轮已分开，见 `locate_release()`）。
- **跨章节验证**因果窗口，或直接改用事件关联，不靠单章 gap=2/gap=12 两个样本。
- 稿件没写释放时，`release_at` 保持未知 → 走**重生／拒绝候选**路径，
  **不生成**任何清除授权（本轮已实现）。
- 仍未接：选稿门（BEST-GUARD）、质量门、自动修复事务。
"""
from __future__ import annotations

import os
import re

from step01_script_gen.cross_field_checks import (
    _RELEASE_MARKERS, _clauses, _layout_field_path, frame_entries, frame_ids,
    frame_text,
)
from step01_script_gen.quality_issues import Issue

# 通用「位移式释放」：拖/拽/拉/扯/卷 + 短距离 + 走/逃/离/去/翻
# 实测原文「咬钩的肥鱼便拖着那钓杆急急逃走了」——`_RELEASE_MARKERS` 里没有
# 「拖走」这一类，只有「脱手/掉落/掷出…」，所以必须补位移式，否则锚点漏提。
_MOTION_AWAY_RE = re.compile(r"(拖|拽|拉|扯|卷)[^，。；！？]{0,8}(走|逃|离|去|翻|不见)")

# 锚点 kind（当前只做 release；transfer 留给后续）
_RELEASE = "release"


def prop_tokens(prop: str, meta: dict | None = None) -> set:
    """该道具在文中的**全部写法**：条目名 + `consistent_terms`（别名表）.

    通用性来源：别名词表来自 `prop_assets`（`钓杆` 的 `consistent_terms` 含
    `鱼杆`），本模块不写死任何道具名。原文那句写的正是「鱼杆」。

    **只收长度 ≥ 2 的词**（v6.125 实测修正）：`刺客匕首` 的别名表里有单字「刀」，
    于是「刘掌柜讪讪地**放下刀**」被误挂成刺客匕首的释放锚点——那是另一章、另一个
    人的刀。单字别名会把任意同字器物吸进来，精度代价远大于收益；丢弃它只会漏提
    「只写了单字别名」的道具（此时视为无锚点，**显式不报**，不制造假锚点）。
    """
    out = {str(prop or "").strip()}
    for t in ((meta or {}).get("consistent_terms") or []):
        out.add(str(t or "").strip())
    return {t for t in out if len(t) >= 2}


def _clause_releases_prop(clause: str, tokens) -> bool:
    """该分句是否**点名**某道具并表达释放（释放词 或 通用位移式）."""
    if not any(t in clause for t in tokens):
        return False
    if any(m in clause for m in _ALL_RELEASE_MARKERS):
        return True
    return bool(_MOTION_AWAY_RE.search(clause))


def _prop_tokens_of(prop: str, prop_assets: dict | None) -> set:
    """道具名的**全部写法**（`prop_tokens` 收词表；没有词表时只用条目名本身）.

    无词表路径（大纲阶段）传的是**候选名词**，此时身份核对只能靠同名词。
    """
    if prop and (prop_assets or {}).get(prop):
        return prop_tokens(prop, (prop_assets or {}).get(prop))
    return {str(prop or "").strip()} - {""}


def _shots_in_order(script: dict) -> list:
    """按 `index` 排的镜头列表（缺 `index` 的排在后面，不抛错）."""
    rows = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    rows.sort(key=lambda s: (0, s["index"]) if isinstance(s.get("index"), int)
              else (1, 0))
    return rows


# ---------------------------------------------------------------------------
# v6.125.1：**无词表**的候选锚点提取（用户 2026-09-25 决定，仍为离线阶段）
#
# 为什么需要：`prop_assets` 是**生成稿之后**才提取的，分镜前的场景物体库里也
# 没有「钓杆」——大纲阶段拿不到任何道具词表，所以上面那套 `prop_tokens()` 依赖
# 词表的路径在那里用不了。这一节只做一件事：**直接从原文语句里就地取器物词**。
#
# ⚠️ 产出的东西叫**候选锚点**（`confidence="candidate"`），**不是**确定事实：
# 它没有词表背书，也没有语义仲裁，不能直接当成可阻塞生产的判据。按用户口径：
# 器物名不明确 / 否定句 / 可能指人 —— **宁可不产出**（进 `skipped` 并带原因，
# 便于事后统计漏提与误提）。
# ---------------------------------------------------------------------------
# 器物名前后的量词/指示词（提取时剥掉）
_QUANT_PREFIX = ("那", "这", "一", "两", "三", "几", "只", "柄", "把", "盏",
                 "个", "根", "条", "枚", "支", "些", "块", "张", "面", "口")
# 可能指人的字——命中即不产出（保守；不做人称识别）
_PERSON_MARKERS = ("人", "者", "们", "他", "她", "它", "我", "你", "谁", "某")
# 人物/身体/方位词——**远距反例实测**必须挡：
#   「放下了小姐」（人物身份）、「放下了…尸身」（人体）、「丢了出去」（方位补语）
#   都会被惰性捕获当成器物名。这是**genre 级**的封闭词表（不绑定本书/本章），
#   符合用户口径「可能指人宁可不产出」。刻意宁多勿少：漏提一个道具 < 误挂一条事实。
_NON_PROP_NOUNS = (
    "人", "身", "尸", "首", "级", "手", "脚", "腿", "臂", "肩", "腰", "眼", "脸",
    "口", "嘴", "头", "背", "胸", "腹", "心", "气",
    "小姐", "公子", "掌柜", "夫人", "老汉", "老人", "少年", "姑娘", "妇人",
    "男子", "女子", "孩子", "孩童", "丫鬟", "仆人", "护卫", "兵丁", "将军",
    "陛下", "殿下", "大人", "老爷", "太太", "婆婆", "妇", "婢", "妾", "卒", "兵",
    "出去", "过来", "下来", "起来", "下去", "上来", "进来", "回来", "上去", "过去",
    "东西", "什么", "地方", "时候",
    # 远距反例实测：「放下戒备」（抽象）、「放下小狗」（活物）
    "戒备", "架子", "身段", "面子", "身份", "屠刀",
    "小狗", "大狗", "猫", "马", "牛", "羊", "鸡", "鸭", "鸟", "鱼", "蛇",
    "虎", "狼", "兔", "驴", "骡", "猪", "鼠", "老牛", "瘦骨", "巴掌",
)
# 否定/未然——出现在谓词**之前**的小窗口里即不产出
_NEG_MARKERS = ("没", "未", "不", "别", "莫", "毋", "无须", "无需")
# 位移式：拖/拽/拉/扯/卷 (+着/了/起) (+量词/指示) + **器物**（懒惰 2–4 字）
#   ⚠️ 实测两处必须这样写（第 1 章真实数据）：
#   · 贪婪 `{1,6}` 会把「拖着那钓杆急急逃走了」捕成「钓杆急急逃走」→ 过长被挡 →
#     **真锚点漏提**；懒惰 `{2,4}?` 取到「钓杆」。
#   · 「绳子拖着一条瘦骨嶙峋的老牛」「裂开卷起一块块巴掌大小的土皮」会捕成
#     「瘦骨」「巴掌」→ **误提**；要求器物**之后 3 字内出现"离开"词**（走/逃/离/
#     飞去/不见）后，这两条自然落空（老牛被拖、土皮裂开都不是"道具离手"）。
_MOTION_LEAVE = (r"(?:急急|匆匆|飞快|一下|迅速|径自|直接|就|便|地|的){0,2}"
                 r"(?:逃走|走了|飞走|离去|不见|跑掉|掉进|落入|溜走)")
_MOTION_OBJ_RE = re.compile(
    r"(?:拖|拽|拉|扯|卷)(?:着|了|起)?(?:[那这一两三几只柄把盏个根条枚支]?)"
    r"([一-龥]{2,4}?)(?=[一-龥]{0,3}" + _MOTION_LEAVE + r")")
# 标记式（器物在谓词**之后**）：如「丢了灯盏」「放下酒壶」
#   ⚠️ 只收**丢弃/放开**类（远距反例实测）：原先还收了「放在」「松开」「撒手」
#   ——「放在几案上」是放置不是释放，「松开/撒手」多接身体部位，都是误提来源。
_MARKER_AFTER = ("放下", "丢下", "丢了", "扔掉", "扔下", "抛出", "掷出", "丢开")
# 标记式（器物在谓词**之前**）：如「钓杆脱手」
_MARKER_BEFORE = ("脱手", "滑落", "掉落", "坠入", "坠地", "松脱")

# 本模块在**稿件侧**还认的释放写法（`cross_field_checks._RELEASE_MARKERS` 里没有）。
#
# 为什么单列：`_RELEASE_MARKERS` 是**已接线**的判据（`prop_possession_consistency`
# 靠它触发质量门 blocking），往里加词会直接改变生产门禁的命中集——那属于判据口径
# 变更，得由用户决定（准则 3）。本模块仍处离线验证阶段，所以把**离线多认的写法**
# 收在这里：`_ALL_RELEASE_MARKERS` 只影响本模块与诊断工具，不动生产判据。
#
# 实测来源（诊断稿 `2050`）：
#   · shot7「钓杆已滑入水中」是真释放，但 base 只有「滑落」没有「滑入」→ 离线层
#     把它当成"仍在持有"的描述，完整事件序列里少了这次释放；
#   · shot9「张十三丢下灯盏」与 `_MARKER_AFTER` 的「丢下」同形，base 里只有「丢了」
#     → 该帧被当成"没写释放"，误报成 `missing`。
# 故 extras = 位移入水类 + `_MARKER_AFTER` 里 base 缺的那几个（两边口径对齐）。
_EXTRA_RELEASE_MARKERS = ("滑入", "落入", "掉入", "脱出", "滑脱", "脱开", "失手",
                          "丢下", "扔掉", "扔下", "丢开")
_ALL_RELEASE_MARKERS = tuple(_RELEASE_MARKERS) + _EXTRA_RELEASE_MARKERS


def _sentences_with_offsets(text: str) -> list:
    """`[(sentence, start, end)]`——保留**位置**（用户口径：证据 + 位置都要留）."""
    out: list = []
    _pos = 0
    for _part in re.split(r"([。！？\n])", text or ""):
        if _part and _part not in "。！？\n":
            out.append((_part, _pos, _pos + len(_part)))
        _pos += len(_part)
    return out


def _clean_noun(raw: str) -> str:
    """剥掉量词/指示词/助词前缀，返回器物名候选.

    实测需要剥的形态（远距反例）：「一些**东西**」（两字量词）、
    「丢**了**灯盏」「卷**起**…」（助词）。
    """
    _n = str(raw or "").strip()
    for _pre in ("一些", "一点", "一块", "一块块", "一群", "一片", "一串", "一只",
                 "一件"):
        if _n.startswith(_pre):
            _n = _n[len(_pre):]
    while _n and (_n[0] in _QUANT_PREFIX or _n[0] in "了着起过"):
        _n = _n[1:]
    return _n


def _skip_reason(noun: str, window: str) -> str:
    """保守判据：返回不产出的原因（空串 = 可以产出）."""
    if not noun:
        return "取不到器物名"
    if not re.fullmatch(r"[一-龥]{2,4}", noun):
        # 非纯汉字（标点/引号/虚词残片，如「’呢」）→ 不是器物名
        return "不是纯汉字名词"
    if len(noun) < 2:
        return "器物名只有一个字（不明确）"
    if len(noun) > 4:
        return "器物名过长（不明确）"
    if noun[-1] in "的之地得":
        return "是修饰短语不是器物名"
    if any(c in noun for c in _PERSON_MARKERS):
        return "可能指人"
    if any(w in noun for w in _NON_PROP_NOUNS):
        return "人物/身体/方位词（不是器物）"
    if any(n in window for n in _NEG_MARKERS):
        return "否定/未然句"
    return ""


def extract_release_candidates(chapter_text: str) -> tuple:
    """**无词表**地从原文提取释放**候选锚点** + 被保守判据挡下的匹配.

    Returns `(candidates, skipped)`：

      - candidate：`{"noun","kind":"release_candidate","evidence","sentence",
        "sentence_index","char_start","char_end","pattern","confidence"}`；
      - skipped：`{"noun","evidence","reason"}`——**统计漏提/误提用**，不进任何判据。

    与 `extract_prop_state_changes()`（有词表、按道具归并）并存：后者在
    `prop_assets` 已有时才用；本函数是**大纲阶段**唯一可用的形态。
    """
    cands: list = []
    skips: list = []
    for si, (sent, s_start, _s_end) in enumerate(_sentences_with_offsets(chapter_text)):
        for clause in _clauses(sent):
            _c_start = chapter_text.find(clause, s_start)
            if _c_start < 0:
                _c_start = s_start
            # ① 位移式（器物在谓词之后）
            for _m in _MOTION_OBJ_RE.finditer(clause):
                _noun = _clean_noun(_m.group(1))
                _why = _skip_reason(_noun, clause[max(0, _m.start() - 8):_m.start()])
                if _why:
                    skips.append({"noun": _noun, "evidence": clause, "reason": _why})
                else:
                    cands.append({"noun": _noun, "kind": "release_candidate",
                                  "evidence": clause, "sentence": sent,
                                  "sentence_index": si, "char_start": _c_start,
                                  "char_end": _c_start + len(clause),
                                  "pattern": "motion_away",
                                  "confidence": "candidate"})
                break
            # ② 标记式（器物在谓词之后）
            for _mk in _MARKER_AFTER:
                _i = clause.find(_mk)
                if _i < 0:
                    continue
                _tail = clause[_i + len(_mk):]
                _noun = _clean_noun(_tail[:6])[:4]
                _why = _skip_reason(_noun, clause[max(0, _i - 8):_i])
                _rec = {"noun": _noun, "evidence": clause}
                if _why:
                    skips.append(dict(_rec, reason=_why))
                else:
                    cands.append(dict(_rec, kind="release_candidate", sentence=sent,
                                      sentence_index=si, char_start=_c_start,
                                      char_end=_c_start + len(clause),
                                      pattern="marker_after",
                                      confidence="candidate"))
                break
            # ③ 标记式（器物在谓词之前）——**形状已停用**（v6.125.1）
            #    远距反例实测：65k 字抽样里它只命中 1 次（「发出重物坠地」）且是
            #    误提。按用户口径「器物名不明确宁可不产出」整条停用；**已知漏提**：
            #    「钓杆脱手」这类**器物在前**的写法会被漏掉，待有正文依据的规则或
            #    人工确认后再启用（`_MARKER_BEFORE` 保留供后续实现）。
            for _mk in ():
                _i = clause.find(_mk)
                if _i < 0:
                    continue
                _noun = _clean_noun(clause[max(0, _i - 4):_i])[-4:]
                _why = _skip_reason(_noun, clause[max(0, _i - 12):_i])
                _rec = {"noun": _noun, "evidence": clause}
                if _why:
                    skips.append(dict(_rec, reason=_why))
                else:
                    cands.append(dict(_rec, kind="release_candidate", sentence=sent,
                                      sentence_index=si, char_start=_c_start,
                                      char_end=_c_start + len(clause),
                                      pattern="marker_before",
                                      confidence="candidate"))
                break
    return cands, skips


def match_candidates_to_props(candidates, prop_assets: dict) -> list:
    """已有词表时把候选锚点归并到道具条目（`noun ∈ prop_tokens(prop)`）.

    没有命中任何条目时原样保留（`prop=None`）——**不猜**道具身份。
    """
    out: list = []
    for c in candidates or []:
        _hit = None
        for prop, meta in (prop_assets or {}).items():
            if str(c.get("noun")) in prop_tokens(prop, meta):
                _hit = str(prop)
                break
        out.append(dict(c, prop=_hit))
    return out


def _sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"[。！？\n]", text or "") if s.strip()]


def extract_prop_state_changes(chapter_text: str, prop_assets: dict) -> list:
    """从**原文**提取道具释放事实（带原文证据）——每个道具每句最多一条.

    Returns: `[{"prop","kind","evidence","sentence","tokens"}]`（按原文句序）。
    """
    out: list = []
    sents = _sentences(chapter_text)
    for prop, meta in (prop_assets or {}).items():
        tokens = prop_tokens(prop, meta)
        if not tokens:
            continue
        for si, sent in enumerate(sents):
            for clause in _clauses(sent):
                if _clause_releases_prop(clause, tokens):
                    out.append({"prop": str(prop), "kind": _RELEASE,
                                "evidence": clause, "sentence": sent,
                                "sentence_index": si,
                                "tokens": sorted(tokens)})
                    break
    out.sort(key=lambda a: a["sentence_index"])
    return out


def release_frames(script: dict, prop: str, prop_assets: dict,
                   *, after=None) -> list:
    """稿件里**写出了**释放的帧：`[(shot_index, frame_id, clause)]`（按帧序）.

    `after=(shot_index, frame_id)` 时只取其**之后**（含该帧）的帧——锚点声称的
    释放时点之前的帧本来就该持有道具，不算释放证据。
    """
    return _release_frames_for_tokens(script, _prop_tokens_of(prop, prop_assets),
                                      after=after)


def _release_frames_for_tokens(script: dict, tokens, *, after=None) -> list:
    """同 `release_frames`，但按**道具写法集合**匹配（无词表路径用候选名词）."""
    out: list = []
    _ord = _frame_order(script)
    _min = _ord.get(after, -1) if after else -1
    for shot in _shots_in_order(script):
        idx = shot.get("index")
        for fid in frame_ids(shot):
            if _ord.get((idx, fid), -1) < _min:
                continue
            for clause in _clauses(frame_text(shot, fid)):
                if _clause_releases_prop(clause, tokens):
                    out.append((idx, fid, clause))
                    break
    return out


def _frame_order(script: dict) -> dict:
    """`{(shot_index, frame_id): 全局序号}`——**按镜号**排，帧内 ff → kf0..kfN → lf.

    v6.125.3（用户 2026-09-25）：必须按 `index` 排而不是按列表顺序——「帧序」要的是
    **叙事顺序**，列表顺序只是文件写法（诊断稿构造/人工整理时可能乱序）。缺 `index`
    时回落到列表位置（不抛错）。
    """
    seq: dict = {}
    n = 0
    for shot in _shots_in_order(script):
        for fid in frame_ids(shot):
            seq[(shot.get("index"), fid)] = n
            n += 1
    return seq


def holding_frames(script: dict, prop: str, prop_assets: dict, *,
                   after=None) -> list:
    """仍**声明持有**该道具的帧：`[(shot_index, frame_id, entry_index, holder)]`.

    `after=(shot_index, frame_id)` 时只算该帧及其后的帧。

    v6.126：按**道具全部写法**（`prop_assets` 别名表）匹配，不只看条目名——
    稿件写「鱼杆」而词表条目是「钓杆」时，旧实现直接漏掉该帧的持有。
    """
    tokens = _prop_tokens_of(prop, prop_assets)
    out: list = []
    _ord = _frame_order(script)
    _min = _ord.get(after, -1) if after else -1
    for shot in _shots_in_order(script):
        idx = shot.get("index")
        for fid in frame_ids(shot):
            if _ord.get((idx, fid), -1) < _min:
                continue
            for ei, entry in enumerate(frame_entries(shot, fid)):
                props = [str(p).strip() for p in (entry.get("props") or [])
                         if str(p).strip()]
                if any(t in props for t in tokens):
                    out.append((idx, fid, ei, str(entry.get("name") or "")))
    return out


def missing_release_issue(script: dict, anchor: dict, prop_assets: dict,
                          *, release_at=None, chapter_text=None,
                          candidate=None) -> "Issue | None":
    """锚点说原文有释放，稿件**没有写出释放事件** → **重生/拒绝**级问题.

    `repairable=False` 且**不给** `repair_targets`：修法不是改字段（那是伪造剧情），
    而是重生或拒绝该候选。

    ⚠️ **返回 `None` ≠ 通过**（用户 2026-09-25 第 1 点）：本函数只回答「**缺没缺**」
    释放事件。**时点对不对**由 `release_verification()` 判定——「写了但写在起因之前」
    （诊断稿 shot4 把「钓杆已脱手」写在刺入之前）在本函数下同样是 `None`。因此
    调用方**不得**把 `None` 当作"可以清理后续 `props`"的依据：v6.127 起
    `release_verification()` **不再返回**任何"可否清理"的执行许可（用户 2026-09-25：
    原 `may_clear_following_props` 名实不符，已从授权口径移除），清理清单只由
    `precise_state_diff()` 以**只读建议**形态给出。

    ⚠️ v6.126（用户 2026-09-25 第 3 点）：**不再要求「仍在持有」才报**。旧实现
    `if not holding: return None` 会让「把 `props` 全删掉」变成过关手段——诊断稿
    `2106` 正是这个形态（原文有释放、稿件一处未写、布局里铺了 42 帧持有）。
    剧情事件缺失是**叙述层**的欠账，与布局里还有没有这条 `props` 无关：
    删字段消矛盾属于伪造剧情（`repairable=False`），不是修复。
    """
    prop = str(anchor.get("prop") or anchor.get("prop_guess") or "")
    if not prop:
        return None
    _tokens = _prop_tokens_of(prop, prop_assets)
    if _release_frames_for_tokens(script, _tokens, after=release_at):
        return None
    holding = holding_frames(script, prop, prop_assets, after=release_at)
    _shots = sorted({h[0] for h in holding}, key=str)
    return Issue(
        code="prop_release_missing", severity="hard",
        message=(
            f"原文有「{prop}」的释放事实（证据：「{anchor.get('evidence')}」），"
            f"但稿件在释放时点之后**没有写出释放事件**"
            + (f"，且仍有 {len(holding)} 处帧布局声明持有（镜头 {_shots}）"
               if holding else
               "，布局里也已无该道具的持有记录（**删 props 不构成修复**："
               "叙述层的事件仍然缺失）")
            + "。修法是**重生或拒绝该候选**——禁止只删 `props` 来消掉矛盾"
              "（那是改写剧情，不是修复）。"),
        shot_id=(release_at[0] if release_at else None),
        frame_id=(release_at[1] if release_at else ""),
        entity_id=prop,
        field_path="",
        evidence=str(anchor.get("evidence") or ""),
        repair_targets=[],          # 显式「无字段级修复目标」→ 不授权定向修复
        repairable=False,
    )


def post_release_possession_issues(script: dict, prop: str, prop_assets: dict,
                                   *, release_at) -> list:
    """释放事件明确后：释放时点（含）之后的帧仍声明持有 → 逐帧精确目标.

    **完整性是关键**：`ff` / `lf` / `kfs` 一视同仁——审计此前只点了 `ff.props`，
    漏掉 `lf.props` 就是半条矛盾（用户核对冻结稿确认）。
    """
    out: list = []
    for idx, fid, ei, holder in holding_frames(script, prop, prop_assets,
                                               after=release_at):
        out.append(Issue(
            code="prop_possession_after_release", severity="visual_risk",
            message=(f"释放时点之后，{fid} 布局仍声明「{holder}」持有「{prop}」——"
                     f"与释放事实矛盾"),
            shot_id=idx, frame_id=fid, entity_id=holder,
            field_path=_layout_field_path(fid, "props"),
            evidence=f"frame_layouts.{fid}[{ei}].props 含「{prop}」",
            repair_targets=[{"shot_id": idx, "field_path": f"frame_layouts.{fid}[{ei}].props"}],
            repairable=True,
        ))
    return out


def uncovered_holding_fields(script: dict, prop: str, prop_assets: dict,
                             *, release_at, declared) -> list:
    """给定授权集（`repair_targets` 形态）**盖不全**的持有字段——半条矛盾检测.

    用途：审计声明 8 镜联动时，判断它是否**真的**覆盖了所有仍持有的帧字段
    （冻结稿实测：只覆盖 `ff.props`，漏掉 `lf.props`）→ 不完整就给不出
    「可修复」结论，必须显式 BLOCK。

    Returns: `["shot_4/frame_layouts.lf.0.props", ...]`（空表 = 完整）。
    """
    from step01_script_gen.repair_fields import _norm_keep_index, path_allowed

    _by_shot: dict = {}
    for p in (declared or []):
        if isinstance(p, dict):
            _by_shot.setdefault(str(p.get("shot_id")), []).append(
                str(p.get("field_path") or ""))
    out: list = []
    for idx, fid, ei, _holder in holding_frames(script, prop, prop_assets,
                                                after=release_at):
        _exact = _norm_keep_index(f"frame_layouts.{fid}[{ei}].props")
        # 声明的条目下标可能与本条目的 entry 下标不同（如一律写 `[0]`）→ 两种都试
        _cands = [c for _p in (_by_shot.get(str(idx)) or [])
                  for c in (_p, re.sub(r"\[\d+\]$", f"[{ei}]", _p))]
        if not any(c and path_allowed(_exact, [c]) for c in _cands):
            out.append(f"shot_{idx}/{_exact}")
    return out


# ---------------------------------------------------------------------------
# v6.125.2：候选锚点 → 大纲字段 + **独立短提示块**（用户 2026-09-25 决定）
#
# 用户口径（原文）：
#   · 「只让 `motion_away`、`marker_after` 两种候选进入现有大纲提取，并带原文位置
#     和"待核实"标记」；
#   · 「生成及 GAP 重生若使用它们，须走**独立短提示**，避免被 agent_plan 的 200 字
#     大纲压缩吞掉」（实测 `prompts._compact_doubao_outline_anchor` 只保【高潮事件】，
#     其余行按 200 字预算裁剪 → 并进 `outline_anchor` 必被吞）；
#   · **不接 BEST-GUARD、不接质量门、不自动删 `props`**——本模块只影响提示词。
# ---------------------------------------------------------------------------
_ALLOWED_PATTERNS = ("motion_away", "marker_after")


def prop_anchor_block_enabled() -> bool:
    """道具候选短提示块是否注入生产提示：**默认关闭**.

    用户 2026-09-25：两次诊断生成都没过验收（第一次写了释放但提前、布局不跟随；第二次
    连释放都不写、持有铺到 42 帧），提示词路线**暂停**——生产默认**不注入**，
    离线代码与取证保留；将来要复测时置 `STEP01_PROP_ANCHOR_BLOCK=1` 即可，
    **不需要改代码**。
    """
    return str(os.environ.get("STEP01_PROP_ANCHOR_BLOCK", "")).strip().lower() in (
        "1", "true", "yes", "on")


def prop_anchor_block_for(outline: dict | None) -> str:
    """生产注入唯一入口：关闭时返回**空串**（调用点无需各自判断开关）."""
    if not prop_anchor_block_enabled():
        return ""
    return render_prop_anchor_block((outline or {}).get("prop_state_candidates"))


def outline_prop_anchors(chapter_text: str, *, limit: int = 6) -> list:
    """大纲阶段随稿携带的道具状态候选（**只**取两种形状，带位置与待核实标记）.

    Returns `[{"prop_guess","pattern","evidence","char_start","char_end","status"}]`；
    `status` 恒为 `"待核实"`——它进提示词，不进任何判据。
    """
    cands, _skips = extract_release_candidates(chapter_text)
    out: list = []
    for c in cands:
        if c.get("pattern") not in _ALLOWED_PATTERNS:
            continue
        out.append({"prop_guess": c.get("noun", ""), "pattern": c.get("pattern"),
                    "evidence": str(c.get("evidence") or "")[:60],
                    "char_start": c.get("char_start"), "char_end": c.get("char_end"),
                    "status": "待核实"})
    return out[:max(0, int(limit))]


def render_prop_anchor_block(anchors) -> str:
    """把候选锚点渲染成**独立短提示块**（**不**并入 `outline_anchor`）.

    块内明确三件事：① 这是**待核实**的原文候选，不是确定事实；② 只在能确认离手时
    才写释放事件，并让释放之后各帧的 `props` 与描述一致；③ **拿不准就保持原样，
    不要删 `props`**（用户口径：不得用删 props 伪造剧情）。
    """
    _rows = [a for a in (anchors or []) if isinstance(a, dict) and a.get("evidence")]
    if not _rows:
        return ""
    lines = ["【原文道具状态候选（**待核实**，不是确定事实，不构成硬性要求）】"]
    for a in _rows[:6]:
        _pos = a.get("char_start")
        lines.append("- 疑似「%s」离手：原文「%s」%s"
                     % (a.get("prop_guess") or "?", a.get("evidence"),
                        ("（原文第 %s 字处）" % _pos) if isinstance(_pos, int) else ""))
    lines.append(
        "用法：**只有**当你能从上下文确认该道具确实离手时，才写出这一动作，**并且必须"
        "按原文的因果顺序安放**：它发生在**起因事件（如中刃）之后**，不得提前到起因之前；"
        "在同一镜的 `ff → kf → lf` 帧序里，**释放之前可以声明持有、释放之后（含该帧）"
        "不得再声明持有**，同镜内不得出现「后面的帧又写持有」这种倒序。"
        "**拿不准就保持原样、不要删 `props`** —— 删字段消矛盾属于伪造剧情，禁止。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# v6.125.3（用户 2026-09-25 口径）：「写了释放」与「写对时点」必须分开
#
# 现场：诊断稿把「钓杆已脱手」写在第 4 镜首帧，而原文的因果是**中刃之后才松手**
# （该稿刺入在第 6 镜）——**提前释放**。若检测只看"有没有释放措辞"，这一版会被
# 误当成通过；更危险的是据此去清后续帧的 `props`（那会把"结果"清在不该清的位置）。
#
# ---------------------------------------------------------------------------
# v6.127（用户 2026-09-25 复核）：**起因帧 ≠ 释放帧**
#
# 用户口径（原文）：
#   · 「还不能把 `release_at=(6, "kf1")` 当作已定位的释放帧。代码目前取的是最早的
#     **起因帧**；原文相隔 2 句只证明"释放在起因之后"，不证明同帧」；
#   · 「分别记录 `cause_at` 与实际写出的 `release_at`；稿件没写释放时，后者保持
#     未知，走重生／拒绝候选路径，不生成清除授权」；
#   · 「`may_clear_following_props` 的名字与当前含义冲突，建议从授权口径中移除；
#     `clear_candidates` 也只能是只读建议，不能充当执行许可」。
#
# 因此本节只做两件**互相独立**的记录：
#   `cause_at`   —— 起因帧（源文锚点 → 稿件镜头，需唯一）
#   `release_at` —— 稿件**实际写出**释放的位置（需唯一）
# 二者不互相推导；状态只表达"写出的释放与起因帧的先后关系"，不宣称同帧即正确。
# ---------------------------------------------------------------------------
_REL_STATE_MISSING = "missing"
_REL_STATE_UNKNOWN = "written_timing_unknown"
_REL_STATE_EARLY = "written_before_cause"
_REL_STATE_AT_CAUSE = "written_at_cause_frame"
_REL_STATE_AFTER = "written_after_cause"

# 「释放写在起因帧本身」时是否给清理清单：**诊断期保守规则，非永久禁止**。
# 用户 2026-09-25：「同帧释放暂不给清单，我也同意作为诊断期的保守规则，但不应定为
# 永久禁止——将来若能核实同帧内"先受创、后离手"的顺序，可以重新判定」。
# 因此做成开关：将来有帧内顺序证据（分句先后 + 动作链可核对）时置 True 即可重判，
# 不需要改动判定链本身。
_SAME_FRAME_RELEASE_GIVES_ADVISORY = False


def release_event_sequence(script: dict, anchor: dict, prop_assets: dict = None,
                           *, tokens=None) -> list:
    """稿件里**全部**释放事件（按帧序）——不是"找到的第一处".

    v6.126（用户 2026-09-25 第 3 点）：「当前 `release_verification()` 只取找到的
    第一处释放，接生产门前还需改为检查完整事件序列」。冻结稿 `2050` 正是这个形态：
    shot4 首帧「钓杆已脱手」（提前）、shot7 kf1「钓杆已滑入水中」（起因之后）——
    只看第一处会把后者整条丢掉。
    """
    _tokens = tokens if tokens is not None else _prop_tokens_of(
        str((anchor or {}).get("prop") or (anchor or {}).get("prop_guess") or ""),
        prop_assets)
    _ord = _frame_order(script)
    return [{"shot_index": i, "frame": f, "clause": c, "order": _ord.get((i, f), -1)}
            for i, f, c in _release_frames_for_tokens(script, _tokens, after=None)]


def pickup_or_transfer_frames(script: dict, anchor: dict,
                              prop_assets: dict = None, *, tokens=None,
                              after=None) -> list:
    """**再次拾取 / 转交**分句：`[{shot_index, frame, clause, kind, names, order}]`.

    用户 2026-09-25 第 2 点：「同时检查相关描述和再次拾取／转交……漏掉描述或
    **合法的再次持有**，都会造出新矛盾」。所以释放之后的再次拾取/转交帧必须被
    识别出来——它们之后的 `props` 持有是**合法**的，不得被当成残留去清。
    """
    _tokens = tokens if tokens is not None else _prop_tokens_of(
        str((anchor or {}).get("prop") or (anchor or {}).get("prop_guess") or ""),
        prop_assets)
    _ord = _frame_order(script)
    _min = _ord.get(after, -1) if after else -1
    out: list = []
    for shot in _shots_in_order(script):
        idx = shot.get("index")
        for fid in frame_ids(shot):
            if _ord.get((idx, fid), -1) < _min:
                continue
            _names = [str(e.get("name") or "") for e in frame_entries(shot, fid)]
            for clause in _clauses(frame_text(shot, fid)):
                if not any(t in clause for t in _tokens):
                    continue
                _pick = any(m in clause for m in _PICKUP_MARKERS)
                _trans = any(m in clause for m in _TRANSFER_MARKERS)
                if not (_pick or _trans):
                    continue
                out.append({"shot_index": idx, "frame": fid, "clause": clause,
                            "kind": "transfer" if _trans else "pickup",
                            "names": [n for n in _names if n and n in clause],
                            "order": _ord.get((idx, fid), -1)})
    return out


def release_verification(script: dict, anchor: dict, prop_assets: dict = None,
                         *, cause_at=None, reference=None) -> dict:
    """释放事件的**完整序列** + 与**起因帧**的先后关系（不宣称同帧即正确）.

    Returns `{"state", "cause_at", "release_point"(稿件里首个写出的释放),
    "reference", "reference_kind", "release_events", "premature_events",
    "later_events", "re_pickup_or_transfer", "still_holding_at_reference",
    "still_holding_after"}`。

    状态（v6.127，用户 2026-09-25 复核）：

      · `missing`                  全稿没有写出任何释放事件
      · `written_timing_unknown`   写了释放，但**没有可用的起因帧**（起因不唯一 /
                                   原文因果对不上）→ 不得当作时点已核
      · `written_before_cause`     存在**早于起因帧**的释放事件（提前释放）
      · `written_at_cause_frame`   释放写在**起因帧本身**（同帧 ≠ 同一事件，不作为通过）
      · `written_after_cause`      释放写在起因帧之后（无提前事件、且不在起因帧）

    `cause_at` 由调用方（`locate_release()`）给出，本函数**不**从起因帧推导释放帧。
    `reference` 是判残留的基准点：调用方显式给出 > 稿件里**最早写出的释放** >
    `cause_at`（此时 `reference_kind=cause_at_release_missing`，只作证据）。

    ⚠️ 本函数**不返回**"可否清理后续 `props`"这类执行许可（用户 2026-09-25：
    原 `may_clear_following_props` 的名字与含义冲突，从授权口径中移除）。
    `precise_state_diff()` 给出的清单也只是**只读建议**。
    """
    prop = str((anchor or {}).get("prop") or (anchor or {}).get("prop_guess") or "")
    _tokens = _prop_tokens_of(prop, prop_assets)
    _seq = release_event_sequence(script, anchor, prop_assets, tokens=_tokens)
    _ord = _frame_order(script)
    _cause_ord = _ord.get(tuple(cause_at), None) if cause_at else None
    for e in _seq:
        if _cause_ord is None:
            e["relation"] = "cause_unknown"
        elif e["order"] < _cause_ord:
            e["relation"] = "before_cause"
        elif e["order"] == _cause_ord:
            e["relation"] = "at_cause_frame"
        else:
            e["relation"] = "after_cause"
    _premature = [e for e in _seq if e["relation"] == "before_cause"]
    _at_cause = [e for e in _seq if e["relation"] == "at_cause_frame"]
    _later = [e for e in _seq if e["relation"] == "after_cause"]
    if not _seq:
        _state = _REL_STATE_MISSING
    elif _cause_ord is None:
        _state = _REL_STATE_UNKNOWN
    elif _premature:
        _state = _REL_STATE_EARLY
    elif _at_cause:
        _state = _REL_STATE_AT_CAUSE
    else:
        _state = _REL_STATE_AFTER

    # 判残留的基准点：**必须**落在"稿件里最早写出的释放"上才谈得上"释放之后的持有"。
    # 显式给的 `reference` 只在本身也不早/不晚于该点时成立（安全判定见
    # `precise_state_diff().advisory_blocked_reason`）。
    _first_event = ((_seq[0]["shot_index"], _seq[0]["frame"]) if _seq else None)
    if reference is not None:
        _ref, _ref_kind = tuple(reference), "explicit"
    elif _first_event is not None:
        _ref, _ref_kind = _first_event, "earliest_written_release"
    elif cause_at is not None:
        _ref, _ref_kind = tuple(cause_at), "cause_at_release_missing"
    else:
        _ref, _ref_kind = None, ""
    _ref_ord = _ord.get(_ref, -1) if _ref else -1

    _at, _after = [], []
    _shots_by_index = {s.get("index"): s for s in _shots_in_order(script)}
    for idx, fid, ei, holder in holding_frames(script, prop, prop_assets,
                                               after=None):
        _o = _ord.get((idx, fid), -1)
        _row = frame_entries(_shots_by_index.get(idx) or {}, fid)
        _props = ([str(p) for p in (_row[ei].get("props") or [])]
                  if 0 <= ei < len(_row) else [])
        _item = {"shot_index": idx, "frame": fid, "holder": holder, "props": _props,
                 "entry_index": ei}
        if _ref is None or _o < _ref_ord:
            continue
        if _o == _ref_ord:
            _at.append(_item)
        else:
            _after.append(_item)
    _pickup = pickup_or_transfer_frames(script, anchor, prop_assets,
                                        tokens=_tokens, after=_ref)
    return {"state": _state, "cause_at": (tuple(cause_at) if cause_at else None),
            "release_point": _first_event,
            "reference": _ref, "reference_kind": _ref_kind,
            "release_events": _seq, "premature_events": _premature,
            "later_events": _later,
            "re_pickup_or_transfer": _pickup,
            "still_holding_at_reference": _at,
            "still_holding_after": _after}


# ---------------------------------------------------------------------------
# v6.126（用户 2026-09-25 设计）：**时点先定位，再判连续性**
#
# 用户口径（原文）：
#   · 「缺的是一条可信的状态转移：什么道具、由谁持有、在哪一帧因什么事件离手」；
#   · 「给原文释放候选配对起因事件，保留双方原文证据和位置；生成稿用大纲节拍关联到
#     镜头、帧。只有道具身份、起因、先后顺序和落点都能唯一核对时，才得到
#     `release_at`。对不上就记"时点未知"，不猜 shot4，也不清 `props`」；
#   · 「授权精确到叶子，但暂不自动改……先做只读差异报告」；
#   · 「不要把"后续所有镜头的 props"笼统交给 LLM」。
#
# 本节的产出**全部是只读**：`precise_state_diff()` 恒 `apply=False`，不写盘、不改稿、
# 不发模型调用，也不接任何门禁。候选仍是**候选**，不因本节的输出升级为生产阻塞。
#
# ⚠️ 下面这组标记常量供**本文件前面的** `release_verification()` /
# `pickup_or_transfer_frames()` 使用（模块级绑定，运行期可见）。
# ---------------------------------------------------------------------------
# 起因事件（受创/失能类，genre 级封闭词表——不绑定本书/本章）：
# 与 `cross_field_checks._RESULT_ACTIONS` 的「刺入/刺进/刺中」同向，避免两套口径。
_CAUSE_MARKERS = (
    "刺入", "刺进", "刺中", "刺穿", "捅进", "扎进", "贯穿",
    "中刃", "中刀", "中剑", "中箭", "劈中", "砍中", "击中", "重击", "砸中",
)
# 「先因后果」的**因果窗口**（句）：释放句与起因句相隔超过这个数就不配对。
#
# ⚠️ v6.127（用户 2026-09-25 决定）：窗口**暂留 3 句，但仅作诊断筛选**。
# gap=2 与 gap=12 还不足以证明"≤3 就是同一因果事件"，更不能据此确定释放帧；
# 接生产前需**跨章节验证**，或改用**明确的事件关联**（见 `locate_release` 与
# 文末"接线计划"：大纲节拍里的原文事件要有可核对的标识，再关联到稿件镜头）。
_CAUSE_WINDOW_SENTENCES = 3
# 再次拾取 / 转交（释放**之后**出现的，使持有重新合法）
_PICKUP_MARKERS = ("拾起", "捡起", "拿起", "取回", "拾回", "接住", "抓回",
                   "重新握住", "又握住", "再次握", "重新拿起", "握回", "攥紧")
_TRANSFER_MARKERS = ("递给", "交给", "塞给", "夺过", "抢过", "接过", "传给",
                     "递到", "交到")


def _first_clause_with(text: str, markers) -> str:
    for c in _clauses(text):
        if any(m in c for m in markers):
            return c
    return ""


def cause_candidates(chapter_text: str) -> list:
    """**原文侧**的起因事件候选（受创/失能类），带证据与位置.

    Returns `[{"marker","evidence","sentence","sentence_index","char_start",
    "char_end"}]`（每句最多一条）。
    """
    out: list = []
    for si, (sent, s_start, _s_end) in enumerate(_sentences_with_offsets(chapter_text)):
        _hit = _first_clause_with(sent, _CAUSE_MARKERS)
        if not _hit:
            continue
        out.append({"marker": next(m for m in _CAUSE_MARKERS if m in _hit),
                    "evidence": _hit, "sentence": sent, "sentence_index": si,
                    "char_start": s_start, "char_end": s_start + len(sent)})
    return out


def cause_frames(script: dict) -> list:
    """**稿件侧**的起因帧：`[(shot_index, frame_id, clause)]`（按帧序，一帧一条）."""
    out: list = []
    for shot in _shots_in_order(script):
        idx = shot.get("index")
        for fid in frame_ids(shot):
            _hit = _first_clause_with(frame_text(shot, fid), _CAUSE_MARKERS)
            if _hit:
                out.append((idx, fid, _hit))
    return out


def pair_release_with_cause(chapter_text: str, candidate: dict) -> dict:
    """给**原文**释放候选配对起因事件（双方证据与位置都留在返回值里）.

    Returns `{"cause","order","gap","reason"}`：`order` 取
    `release_after_cause` / `release_before_cause` / `too_far` / `None`。
    只有 `release_after_cause` 才允许推出预期时点；其余一律 `None` + 原因。
    """
    _causes = cause_candidates(chapter_text or "")
    _rel_si = (candidate or {}).get("sentence_index")
    if not _causes:
        return {"cause": None, "order": None, "gap": None,
                "reason": "原文没有可核对的起因事件（不猜时点）"}
    _idx = {c["sentence_index"] for c in _causes}
    if len(_idx) > 1:
        return {"cause": None, "order": None, "gap": None,
                "reason": "原文起因事件不唯一（%d 处：句 %s），无法确定配对"
                          % (len(_causes), sorted(_idx))}
    _cause = _causes[0]
    if not isinstance(_rel_si, int):
        return {"cause": _cause, "order": None, "gap": None,
                "reason": "释放候选没有句序，无法判先后"}
    if _rel_si < _cause["sentence_index"]:
        return {"cause": _cause, "order": "release_before_cause", "gap": None,
                "reason": "原文顺序是「释放在前、起因在后」，不能据此断言时点"}
    _gap = _rel_si - _cause["sentence_index"]
    if _gap > _CAUSE_WINDOW_SENTENCES:
        return {"cause": _cause, "order": "too_far", "gap": _gap,
                "reason": "释放句与起因相隔 %d 句（>%d 句），不是同一因果窗口"
                          % (_gap, _CAUSE_WINDOW_SENTENCES)}
    return {"cause": _cause, "order": "release_after_cause", "gap": _gap,
            "reason": ""}


# v6.128（用户 2026-09-25 决定）：**离线事件关联**（**只报不判**，暂停继续扩展）
#
# 用户口径（原文）：
#   · 「给原文的起因、释放事件保留可核对的位置和事件标识，再关联大纲节拍与
#     稿件帧；关联不唯一就保持未知」；
#   · 「3 句窗口继续只作诊断筛选，**不能替代**事件关联」；
#   · 随后叫停扩展：「现在应暂停扩展这条离线链路，保留现有只读取证；**不要继续加
#     事件 ID 或自动修复**」——原因是这条链路只提高了诊断严谨性，**尚未证明 Step01
#     质量提升**，也**没有缩短运行时间**。
#
# 因此本节的产出**只作报告**：不参与 `locate_release()` / `precise_state_diff()` 的
# 任何判据（已把先前加过的关联门禁撤回），也没有清理授权。
# 已知不精确（**暂停在此，未修**）：节拍匹配用「公共子串 ≥4 字 or 关键词命中」，
# 实测第 1 章起因事件会同时命中 beat_6 与 beat_8（后者靠很短的公共片段），
# 要用于判据前必须先收紧阈值或换成事件关联。
# ---------------------------------------------------------------------------
def _longest_common_substring(a: str, b: str) -> int:
    """两串最长公共子串长度（**朴素**实现：诊断规模下够用，无第三方依赖）."""
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return 0
    best = 0
    # 滚动一维 DP：dp[j] = 以 b[j] 结尾的最长公共子串长度
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def source_events(chapter_text: str, *, tokens=None) -> list:
    """原文事件清单（带**可核对标识**与位置）：起因事件 + 释放候选事件.

    `tokens` 给定时，释放事件只取点名该道具（写法集合）的那些；不给则取全部候选。

    Returns `[{event_id, kind, evidence, sentence_index, char_start, char_end,
    marker, keys}]`，`event_id` = `cause#N` / `release#N`（按原文出现顺序编号）。
    """
    out: list = []
    for i, c in enumerate(cause_candidates(chapter_text or ""), start=1):
        out.append({"event_id": f"cause#{i}", "kind": "cause",
                    "evidence": c["evidence"], "sentence_index": c["sentence_index"],
                    "char_start": c["char_start"], "char_end": c["char_end"],
                    "marker": c["marker"], "keys": [c["marker"]]})
    _cands, _skips = extract_release_candidates(chapter_text or "")
    # 先对**全部**候选编号再按 `tokens` 过滤 —— 标识必须稳定：同一段原文，
    # 换一个道具去查时同一条事件的 `event_id` 不能变。
    for n, c in enumerate(_cands, start=1):
        if tokens and c.get("noun") not in set(tokens):
            continue
        out.append({"event_id": f"release#{n}", "kind": "release",
                    "evidence": c.get("evidence"), "sentence_index": c.get("sentence_index"),
                    "char_start": c.get("char_start"), "char_end": c.get("char_end"),
                    "marker": c.get("pattern"), "keys": [c.get("noun")]})
    out.sort(key=lambda e: (e.get("char_start") if isinstance(e.get("char_start"), int)
                            else 10 ** 9, e["event_id"]))
    return out


def outline_beats(outline: dict | None) -> list:
    """大纲节拍清单（带可核对标识）：`[{beat_id, text, evidence}]`.

    来源是大纲的 `shot_beats`（生成规则要求每条都带「原文依据」）；`beat_id` 按
    列表顺序编号 `beat_1..n`。没有节拍时返回空表（**不做**任何补偿推导）。
    """
    out: list = []
    for i, b in enumerate((outline or {}).get("shot_beats") or [], start=1):
        _t = str(b or "").strip()
        if not _t:
            continue
        _ev = ""
        for _part in re.split(r"[｜|]", _t):
            if "原文依据" in _part or "原文证据" in _part:
                _ev = _part.strip()
                break
        out.append({"beat_id": f"beat_{i}", "text": _t, "evidence": _ev})
    return out


def associate_event_to_beats(event: dict, beats) -> dict:
    """事件 ↔ 大纲节拍：`{"beat_ids", "methods"}`（命中 0 或 ≥2 条即不唯一）."""
    _ids, _methods = [], {}
    _ev = str(event.get("evidence") or "")
    for b in beats or []:
        _hay = b["text"] + " " + b["evidence"]
        _hits = []
        if _longest_common_substring(_ev, _hay) >= 4:
            _hits.append("evidence_substring")
        if any(k and k in _hay for k in (event.get("keys") or [])):
            _hits.append("key_term")
        if _hits:
            _ids.append(b["beat_id"])
            _methods[b["beat_id"]] = _hits
    return {"beat_ids": _ids, "methods": _methods}


def associate_event_to_draft(script: dict, event: dict, *, tokens=None) -> dict:
    """事件 ↔ 稿件帧：`{"frames", "shots", "clauses", "method"}`（按帧序）.

    起因事件的取帧方式：先按**该事件自己的**起因词（如「刺进」）找；一条都没有时
    退回"稿件里任意起因词"的帧，并把 `method` 记为 `any_cause_marker`——**措辞不必
    与原文逐字相同**（实测：原文写「刺进」、诊断稿写「刺入」），但这条退路更松，
    必须让调用方看得见（用户：措辞匹配只是诊断用，生产接线要换成事件关联）。
    """
    if not script:
        return {"frames": [], "shots": [], "clauses": [], "method": ""}
    _frames: list = []
    _method = ""
    if event.get("kind") == "cause":
        _marker = str(event.get("marker") or "")
        for shot in _shots_in_order(script):
            for fid in frame_ids(shot):
                _clause = _first_clause_with(frame_text(shot, fid), (_marker,))
                if _clause:
                    _frames.append((shot.get("index"), fid, _clause))
        _method = "marker_exact"
        if not _frames:
            _frames = [(c[0], c[1], c[2]) for c in cause_frames(script)]
            _method = "any_cause_marker" if _frames else ""
    else:
        _tk = set(tokens or event.get("keys") or [])
        _frames = _release_frames_for_tokens(script, _tk, after=None)
        _method = "release_tokens" if _frames else ""
    return {"frames": _frames, "shots": sorted({f[0] for f in _frames}, key=str),
            "clauses": [f[2] for f in _frames], "method": _method}


def event_association(chapter_text: str, *, outline: dict | None = None,
                      script: dict | None = None, tokens=None) -> dict:
    """**三方关联**：原文事件 → 大纲节拍 + 稿件帧；任一侧不唯一即 `unique=False`.

    Returns `{"events": [...], "available": {"outline","draft"}, "n_events"}`，
    每条事件含 `beat_ids` / `draft_shots` / `unique` / `reason`。

    ⚠️ 本函数只产出**诊断**结论（用户 2026-09-25：只做离线事件关联，不接生产门）。
    """
    _events = source_events(chapter_text or "", tokens=tokens)
    _beats = outline_beats(outline)
    rows: list = []
    for e in _events:
        _b = associate_event_to_beats(e, _beats)
        _d = associate_event_to_draft(script or {}, e, tokens=tokens)
        _ok_b = len(_b["beat_ids"]) == 1
        _ok_d = len(_d["shots"]) == 1
        _why = []
        if not _beats:
            _why.append("没有提供大纲节拍")
        elif not _ok_b:
            _why.append("节拍命中 %d 条（需唯一）" % len(_b["beat_ids"]))
        if not script:
            _why.append("没有提供稿件")
        elif not _ok_d:
            _why.append("稿件帧命中 %d 个镜头（需唯一）" % len(_d["shots"]))
        rows.append(dict(e, beat_ids=_b["beat_ids"], beat_methods=_b["methods"],
                         draft_frames=[(f[0], f[1]) for f in _d["frames"]],
                         draft_shots=_d["shots"], draft_method=_d["method"],
                         unique=(_ok_b and _ok_d), reason="；".join(_why)))
    return {"events": rows, "n_events": len(rows),
            "available": {"outline": bool(_beats), "draft": bool(script)}}


def association_of(assoc: dict | None, kind: str, *, prop_noun: str = "") -> dict:
    """从关联结果里取某一类事件的**唯一**一条：`{"event_id","unique","reason"}`.

    多条同类事件（如"多个相似起因"）时**不合并**——直接判不唯一并说明有几条。
    """
    _rows = [r for r in ((assoc or {}).get("events") or []) if r.get("kind") == kind]
    if prop_noun:
        _hit = [r for r in _rows if prop_noun in (r.get("keys") or [])]
        if _hit:
            _rows = _hit
    if not _rows:
        return {"event_id": None, "unique": False, "reason": "原文没有该类事件"}
    if len(_rows) > 1:
        return {"event_id": None, "unique": False,
                "reason": "原文有 %d 条同类事件（%s），无法确定是哪一条"
                          % (len(_rows), [r["event_id"] for r in _rows])}
    return {"event_id": _rows[0]["event_id"], "unique": bool(_rows[0]["unique"]),
            "reason": _rows[0]["reason"],
            "beat_ids": _rows[0]["beat_ids"], "draft_shots": _rows[0]["draft_shots"]}


def _draft_prop_names(script: dict) -> set:
    """稿件 `frame_layouts` 里出现过的全部道具名."""
    out: set = set()
    for shot in _shots_in_order(script):
        for fid in frame_ids(shot):
            for e in frame_entries(shot, fid):
                for p in (e.get("props") or []):
                    _s = str(p).strip()
                    if _s:
                        out.add(_s)
    return out


def _prop_recorded_in_draft(script: dict, tokens) -> bool:
    """稿件里是否有这件道具的**任何记录**（`props` 条目 或 帧文本提名）.

    无记录 = 无法定位这件道具（不猜时点）；这与"稿件写错了道具名"不同，
    后者由诊断工具的**串名检查**（候选名 vs 稿件释放名）单独报出。
    """
    if _draft_prop_names(script) & set(tokens):
        return True
    for shot in _shots_in_order(script):
        for fid in frame_ids(shot):
            if any(t in frame_text(shot, fid) for t in tokens):
                return True
    return False


def _find_release_candidate(chapter_text: str, tokens) -> dict | None:
    """原文里该道具的释放候选（无词表路径：候选名词 ∈ tokens）."""
    cands, _skips = extract_release_candidates(chapter_text or "")
    for c in cands:
        if c.get("noun") in set(tokens):
            return c
    return None


def locate_release(script: dict, anchor: dict, prop_assets: dict = None,
                   *, chapter_text=None, candidate=None,
                   outline: dict | None = None) -> dict:
    """**分别**定位 `cause_at`（起因帧）与 `release_at`（稿件**实际写出**的释放）.

    用户 2026-09-25 复核：「代码目前取的是最早的**起因帧**；原文相隔 2 句只证明
    "释放在起因之后"，不证明同帧」。所以这里**不做**"起因帧 ⇒ 释放帧"的推导：

      - `cause_at`：稿件里起因事件落在**唯一一个镜头**内时取该镜最早的起因帧
        （同镜多帧写同一次起因不算歧义；跨镜则 `None`）。
      - `cause_usable`：只有原文顺序为**先因后果**、且在因果窗口内时才为 True。
        窗口（`_CAUSE_WINDOW_SENTENCES=3`，用户 2026-09-25 决定）**仅作诊断筛选**：
        gap=2 与 gap=12 还不足以证明"≤3 句就是同一因果事件"，更不能据此确定释放帧；
        接生产前需跨章节验证，或改用明确的事件关联。
      - `release_at`：**只看稿件**——释放事件必须①存在 ②落在唯一一个镜头内
        ③（有可用起因时）不早于起因帧。取该镜里最早的写出帧。任一条件不满足即
        `release_known=False`（**不猜**），且此时**不生成任何清除授权**。
      - 稿件没写释放 → `release_known=False`，理由里点明走**重生／拒绝候选**路径。

    Returns `{"tokens","identity","cause_at","cause_gates","cause","cause_usable",
    "source_pair","release_at","release_known","release_shot","release_events",
    "release_reason","relation","reason","association"}`。

    另附 `association`（v6.128，**只作离线事件关联**）：原文的起因/释放事件 → 大纲
    节拍 → 稿件帧三方关联结果；`cause_assoc` / `release_assoc` 给出各自是否唯一。
    关联不唯一**不改变**本函数的稿件侧结论（`release_at` 是稿件事实），但它是
    `precise_state_diff()` 给清理清单的**额外前提**（用户：关联不唯一就保持未知）。
    """
    prop = str((anchor or {}).get("prop") or (anchor or {}).get("prop_guess") or "")
    _tokens = _prop_tokens_of(prop, prop_assets)
    _base = {"tokens": sorted(_tokens), "identity": False, "cause_at": None,
             "cause_gates": {"identity": False, "cause_unique": False,
                             "source_pair": False},
             "cause": None, "cause_usable": False, "source_pair": None,
             "release_at": None, "release_known": False, "release_shot": None,
             "release_events": [], "release_reason": "", "relation": "",
             "reason": "", "association": None, "cause_assoc": None,
             "release_assoc": None}
    # v6.128：**关联结果只作报告，不参与判据**。用户 2026-09-25 已叫停这条离线链路
    # 的继续扩展（诊断严谨性 ≠ Step01 质量提升），原始「关联不唯一就保持未知」的
    # 门禁未接线：下面的结论口径与 v6.127 完全一致，关联只作为附带信息返回。
    if chapter_text:
        _assoc = event_association(chapter_text, outline=outline, script=script,
                                   tokens=_tokens)
        _base["association"] = _assoc
        _base["cause_assoc"] = association_of(_assoc, "cause")
        _base["release_assoc"] = association_of(
            _assoc, "release", prop_noun=sorted(_tokens)[0] if _tokens else "")
    if not prop:
        return dict(_base, reason="锚点没有道具名")

    # ① 道具身份（稿件里确有这件道具的记录）
    if not _prop_recorded_in_draft(script, _tokens):
        return dict(_base, reason="稿件里没有这件道具的任何记录（不猜时点）",
                    release_reason="稿件里没有这件道具的任何记录")
    _base["identity"] = True
    _base["cause_gates"]["identity"] = True

    # ② 起因帧：必须落在唯一一个镜头内
    _cf = cause_frames(script)
    _cause_shots = sorted({c[0] for c in _cf}, key=str)
    _cause_frame = None
    if len(_cause_shots) == 1 and _cf:
        _cause_frame = {"shot_index": _cf[0][0], "frame": _cf[0][1],
                        "clause": _cf[0][2], "shot_frames": [(c[0], c[1]) for c in _cf],
                        "source": None}
        _base["cause_at"] = (_cf[0][0], _cf[0][1])
        _base["cause_gates"]["cause_unique"] = True
    _base["cause"] = _cause_frame
    _cause_reason = ("" if _cause_frame else
                     ("稿件里起因事件%s（需落在唯一一个镜头才能用）"
                      % ("未出现" if not _cf else "跨 %d 个镜头：%s"
                         % (len(_cause_shots), [(c[0], c[1]) for c in _cf]))))

    # ③ 源文侧：先因后果 + 因果窗口（**仅诊断用**，见 docstring）
    if _cause_frame is not None and chapter_text:
        _cand = candidate or _find_release_candidate(chapter_text, _tokens)
        if _cand:
            _pair = pair_release_with_cause(chapter_text, _cand)
            _base["source_pair"] = _pair
            _cause_frame["source"] = _pair.get("cause")
            _cause_frame["release_candidate"] = _cand
            _cause_frame["gap_sentences"] = _pair.get("gap")
            _base["cause_gates"]["source_pair"] = (
                _pair.get("order") == "release_after_cause")
    elif _cause_frame is not None:
        _base["source_pair"] = {"order": None, "gap": None,
                                "reason": "没有提供原文，无法核对起因与释放的先后"}
    # v6.128：**关联不参与判据**（只报不判，见上）；`cause_usable` 口径与 v6.127 一致。
    _base["cause_usable"] = bool(_base["cause_gates"]["cause_unique"]
                                 and _base["cause_gates"]["source_pair"])

    # ④ 释放帧：**只看稿件**（唯一镜头 + 不早于起因）
    _seq = release_event_sequence(script, anchor, prop_assets, tokens=_tokens)
    _base["release_events"] = _seq
    _cause_ord = _frame_order(script).get(_base["cause_at"], None) \
        if _base["cause_at"] else None
    if not _seq:
        return dict(_base, relation="no_release",
                    release_reason=("稿件没有写出释放事件（**释放缺失**）——走"
                                    "**重生／拒绝候选**路径（删 props 不构成修复）"),
                    reason=_cause_reason or "稿件没有写出释放事件")
    if _cause_ord is not None:
        _cands = [e for e in _seq if e["order"] >= _cause_ord]
        if not _cands:
            return dict(_base, relation="before_cause",
                        release_reason="稿件的释放**全部**写在起因帧之前（提前释放），"
                                       "没有可用落点",
                        reason=_cause_reason or "释放全部早于起因帧")
    else:
        _cands = list(_seq)
    _shots = sorted({e["shot_index"] for e in _cands}, key=str)
    if len(_shots) != 1:
        return dict(_base, relation="",
                    release_reason=("稿件的释放跨 %d 个镜头（%s），落点不唯一"
                                    % (len(_shots),
                                       [(e["shot_index"], e["frame"]) for e in _cands])),
                    reason=_cause_reason or "释放落点不唯一")
    _first = min(_cands, key=lambda e: e["order"])
    return dict(_base, release_at=(_first["shot_index"], _first["frame"]),
                release_known=True, release_shot=_first["shot_index"],
                release_reason="",
                relation=("before_cause" if _cause_ord is not None
                          and _first["order"] < _cause_ord else
                          ("at_cause_frame" if _cause_ord is not None
                           and _first["order"] == _cause_ord else
                           ("after_cause" if _cause_ord is not None else
                            "cause_unknown"))),
                reason=_cause_reason)


# ---------------------------------------------------------------------------
# 只读差异报告（用户 2026-09-25 第 2 点）
# ---------------------------------------------------------------------------
def _exact_props_path(frame_id: str, entry_index: int) -> str:
    """**精确到叶子**的 `props` 路径（真实下标，人工可核对）."""
    if frame_id.startswith("kf"):
        return f"frame_layouts.kfs.{int(frame_id[2:])}.{entry_index}.props"
    return f"frame_layouts.{frame_id}.{entry_index}.props"


def _desc_field_path(frame_id: str) -> str:
    """该帧描述字段的路径（`ff_desc` / `lf_desc` / `keyframes[i].desc`）."""
    if frame_id.startswith("kf"):
        return f"keyframes.{int(frame_id[2:])}.desc"
    return f"{frame_id}_desc"


def _holding_clause(text: str, tokens) -> str:
    """仍**声明持有**该道具的分句（提名了道具，且既非释放、也非拾取/转交）."""
    for c in _clauses(text):
        if not any(t in c for t in tokens):
            continue
        if any(m in c for m in _ALL_RELEASE_MARKERS) or _MOTION_AWAY_RE.search(c):
            continue
        if any(m in c for m in _PICKUP_MARKERS) or any(m in c
                                                      for m in _TRANSFER_MARKERS):
            continue
        return c
    return ""


def precise_state_diff(script: dict, anchor: dict, prop_assets: dict = None, *,
                       release_at=None, chapter_text=None, candidate=None,
                       outline: dict | None = None,
                       apply: bool = False) -> dict:
    """**只读**差异报告：释放点之后仍持有的**叶子级**字段 + 相关描述 + 再次拾取/转交.

    用户 2026-09-25 第 2 点：授权精确到叶子、**暂不自动改**；「不要把"后续所有镜头
    的 props"笼统交给 LLM；漏掉描述或合法的再次持有，都会造出新矛盾。先做只读
    差异报告」。因此：

      - `residuals`：基准点之后仍声明持有的条目，逐条给出**精确叶子**路径
        （`frame_layouts.<帧>.<条目>.props`）与授权层形态（`_layout_field_path`）；
      - `premature_residuals`：**提前释放点之后**的残留（"提前释放 + 后续残留"
        是同一条缺陷的两半，分开看会漏掉一半）；
      - `descriptions_still_holding`：**描述层**仍写着持有的字段——它不是"删字段"
        能修的（需重写文案），所以恒 `auto_fixable=False`，只报不改；
      - `re_pickup` / `transfer`：释放后的再次拾取、转交——其后的持有**合法**，
        标 `exempted=True`，**不进** `clear_candidates`；
      - `clear_candidates` + `advisory_only=True`：**只读建议，不是执行许可**
        （用户 2026-09-25：不得充当执行许可）。给出它的前提是下面**全部**成立，
        否则为空表 + `blocked_reason`：
          ① `release_at` 已知（= 稿件里**实际写出**的释放点，不由起因帧推导）；
          ② 调用方若显式指定了 `release_at`，它必须**等于**稿件里最早写出的那个
             释放事件（见下"防线"）；
          ③ 无提前于起因的释放事件；④ 释放**不落在起因帧本身**（同帧 ≠ 同一事件）；
          ⑤ 释放之后无再次拾取/转交。
        `residuals` / `descriptions_still_holding` 的基准点则恒取**稿件里最早写出
        的释放**（`reference_kind=earliest_written_release`；无释放事件时退到
        `cause_at`，只作证据）——证据口径不受调用方传入的点影响。
      - `apply` 恒为 False —— 本模块不写盘、不改稿；将来若批准自动修复，应由
        调用方另起**可回滚的确定性事务**，不是把它塞进现有 LLM 联动组。

    **防线（用户 2026-09-25 复核要求）**：调用方可以显式传 `release_at`，但只有它
    **等于稿件里最早写出的释放事件**时才可能给出清单；否则「只在起因之后、
    但**晚于指定点**才写释放」的情形会把**真正释放之前**的持有当成残留——那正是要
    拦住的反例。不等时一律空表 + 原因。
    """
    prop = str((anchor or {}).get("prop") or (anchor or {}).get("prop_guess") or "")
    _tokens = _prop_tokens_of(prop, prop_assets)
    _loc = locate_release(script, anchor, prop_assets,
                          chapter_text=chapter_text, candidate=candidate,
                          outline=outline)
    _ord = _frame_order(script)
    _first_event = None
    if _loc["release_events"]:
        _e0 = min(_loc["release_events"], key=lambda e: e["order"])
        _first_event = (_e0["shot_index"], _e0["frame"])
    _explicit = release_at is not None
    if release_at is None and _loc["release_known"]:
        release_at = _loc["release_at"]
    _cause_at = _loc["cause_at"] if _loc["cause_usable"] else None
    # 残留/描述的基准点恒取**稿件里最早写出的释放**（`reference=None` 让
    # `release_verification` 自己选，见其 `reference_kind`）：稿件一旦声称道具离手，
    # 那之后仍声明持有的帧就与**它自己的说法**矛盾——无论该说法本身是否提前。
    # 显式传入的 `release_at` 只用于下面的**防线**判定，不改变证据口径。
    _ver = release_verification(script, anchor, prop_assets, cause_at=_cause_at)
    _ref, _ref_kind = _ver["reference"], _ver["reference_kind"]
    _ref_ord = _ord.get(_ref, -1) if _ref else -1
    _pickup = _ver["re_pickup_or_transfer"]
    # **豁免窗口**（v6.128，用户复核）：从拾取/转交帧起豁免后续持有，但**到下一次
    # 释放事件为止**——"释放→拾取→再次释放→仍持有"里的第二次释放之后，持有重新
    # 成为残留，不得再被豁免（旧实现从**首次**拾取起无脑豁免到最后，会把这条盖住）。
    _rel_orders = sorted(e["order"] for e in _ver["release_events"])
    _exempt_windows = []
    for _p in _pickup:
        _nxt = next((o for o in _rel_orders if o > _p["order"]), None)
        _exempt_windows.append((_p["order"], _nxt if _nxt is not None else 10 ** 9))

    def _is_exempt(order: int) -> bool:
        return any(a <= order < b for a, b in _exempt_windows)

    _shots_by_index = {s.get("index"): s for s in _shots_in_order(script)}
    # 提前释放点之后的残留（"提前释放 + 后续残留"是同一条缺陷的两半，必须一起看）
    _early_ref = None
    if _ver["premature_events"]:
        _e0 = min(_ver["premature_events"], key=lambda e: e["order"])
        _early_ref = (_e0["shot_index"], _e0["frame"])
    _early_ord = _ord.get(_early_ref, -1) if _early_ref else -1
    _residuals, _early_residuals, _descs = [], [], []
    for idx, fid, ei, holder in holding_frames(script, prop, prop_assets, after=None):
        _o = _ord.get((idx, fid), -1)
        _row = frame_entries(_shots_by_index.get(idx) or {}, fid)
        _props = ([str(p) for p in (_row[ei].get("props") or [])]
                  if 0 <= ei < len(_row) else [])
        if _early_ref and _o > _early_ord:
            _early_residuals.append({
                "shot_id": idx, "frame": fid, "entry_index": ei, "holder": holder,
                "props": _props, "field_path": _exact_props_path(fid, ei),
                "relative_to": "premature_release", "at": _early_ref,
                "auto_fixable": False,
                "reason": "提前释放点之后仍声明持有——先修释放时点，不得只清这里",
            })
        if _ref is None or _o <= _ref_ord:
            continue
        _exempted = _is_exempt(_o)
        _residuals.append({
            "shot_id": idx, "frame": fid, "entry_index": ei, "holder": holder,
            "props": _props,
            "field_path": _exact_props_path(fid, ei),
            "auth_field_path": _layout_field_path(fid, "props"),
            "exempted": _exempted,
            "exempt_reason": ("该帧起有再次拾取/转交，持有重新合法"
                              if _exempted else ""),
            "auto_fixable": False,
            "note": "只读报告：本模块不改稿；自动修复须另起可回滚的确定性事务",
        })
    for shot in _shots_in_order(script):
        idx = shot.get("index")
        for fid in frame_ids(shot):
            _o = _ord.get((idx, fid), -1)
            if _ref is None or _o <= _ref_ord:
                continue
            _clause = _holding_clause(frame_text(shot, fid), _tokens)
            if not _clause:
                continue
            _descs.append({
                "shot_id": idx, "frame": fid, "clause": _clause,
                "field_path": _desc_field_path(fid),
                "auto_fixable": False,
                "reason": "描述需重写（删字段修不了文案）；只报不改",
            })

    # 阻断顺序即"为什么现在不能给清单"，逐条都要能对上证据
    _sp = _loc.get("source_pair") or {}
    if _explicit and _first_event is None:
        _blocked = "稿件没有写出释放事件：清单无从谈起（走重生/拒绝候选路径）"
    elif _explicit and tuple(release_at) != _first_event:
        _blocked = ("指定点 %s **不是**稿件里最早的释放位置 %s：二者之间的持有不属于"
                    "「释放之后」，不得据此清 props（先确认释放写在哪一帧）"
                    % (tuple(release_at), _first_event))
    elif not _loc["release_known"]:
        _blocked = "释放落点未知：" + (_loc["release_reason"] or _loc["reason"])
    elif _ver["state"] == _REL_STATE_MISSING:
        _blocked = ("稿件未写释放事件（缺失）：属**重生/拒绝**级欠账——下方残留只作"
                    "证据，**不得**只删 `props` 过关")
    elif _ver["state"] == _REL_STATE_UNKNOWN:
        _blocked = ("没有可用的起因帧：无法判断释放时点——"
                    + (_sp.get("reason") or _loc["reason"] or ""))
    elif _ver["premature_events"]:
        _blocked = ("存在提前释放（早于起因帧 %s）：不得据此清后续 props"
                    % (_cause_at,))
    elif _ver["state"] == _REL_STATE_AT_CAUSE and not _SAME_FRAME_RELEASE_GIVES_ADVISORY:
        _blocked = ("释放写在**起因帧本身**（%s）：同帧不等于同一事件，落点未被独立"
                    "确认——不得据此清 props" % (_cause_at,))
    elif _pickup:
        _blocked = ("释放点之后有再次拾取/转交 %s：其后的持有合法，不得整段清"
                    % [(p["shot_index"], p["frame"], p["kind"]) for p in _pickup])
    elif _descs:
        # v6.128（用户复核第 1 条）：描述层仍声明持有 → **不可拆的联合任务**。
        # 只清 props 而不动描述就是半条修复，所以清单为空，改为给出联合任务。
        _blocked = ("描述层仍有 %d 处声明持有：`props` 与描述是**不可拆的联合任务**"
                    "（单清 props = 半条修复）——清单为空，联合任务见 `joint_task`"
                    % len(_descs))
    else:
        _blocked = ""
    _clear = [] if _blocked else [
        {"shot_id": r["shot_id"], "frame": r["frame"], "holder": r["holder"],
         "field_path": r["field_path"], "auth_field_path": r["auth_field_path"]}
        for r in _residuals if not r["exempted"]]
    _joint_task = {
        "required": bool(_descs),
        "descriptions": [{"shot_id": d["shot_id"], "frame": d["frame"],
                          "field_path": d["field_path"], "clause": d["clause"]}
                         for d in _descs],
        "layout_leaves": [{"shot_id": r["shot_id"], "frame": r["frame"],
                           "holder": r["holder"], "field_path": r["field_path"],
                           "auth_field_path": r["auth_field_path"]}
                          for r in _residuals if not r["exempted"]],
        "reason": ("描述与布局必须同批处理：只清 props 会留下"
                   "「描述说持有、布局已清」的新矛盾（用户 2026-09-25）"
                   if _descs else ""),
    }
    return {"prop": prop, "tokens": sorted(_tokens), "locate": _loc,
            "cause_at": _cause_at, "cause_usable": _loc["cause_usable"],
            "reference": _ref, "reference_kind": _ref_kind,
            "release_at": release_at, "release_known": _loc["release_known"],
            "release_written_at": _loc["release_at"],
            "timing_state": _ver["state"],
            "release_events": _ver["release_events"],
            "premature_events": _ver["premature_events"],
            "later_events": _ver["later_events"],
            "residuals": _residuals,
            "premature_residuals": _early_residuals,
            "descriptions_still_holding": _descs,
            "re_pickup": [p for p in _pickup if p["kind"] == "pickup"],
            "transfer": [p for p in _pickup if p["kind"] == "transfer"],
            "clear_candidates": _clear,
            "joint_task": _joint_task,
            "advisory_only": True,      # 只读建议：**不是**执行许可
            "blocked_reason": _blocked,
            "apply": False,   # 恒 False：本模块不写盘、不改稿
            "read_only": True}
