# -*- coding: utf-8 -*-
"""shared.presentation_contract —— 计划**呈现协议**：单一事实源与稳定身份.

规格来源：
- `docs/aiVideo-step01-planning-closure-2026-10-04.md` **R1**（共用可编辑计划视图）
- `docs/aiVideo-screen-hybrid-plan-2026-10-04.md` **§2**（承载协议：单一事实源与稳定身份）

## 这个模块解决什么

旧形状里，**同一份关系被写了两遍**：节拍上写 `｜载体：旁白2`，事实条目上写 `｜承载：B2/旁白2`，
旁白条目里还要再登记一次 `fact_refs`。三处互指，全靠模型自己算前后编号——实测（R §2.1）
首两轮拒收就是**编号错位**：操作按修改前编号、声明按修改后编号，模型算不过来。

本模块把关系收成**一份**：`fact_bindings` 说"这条事实由谁承载"，其余全是**代码派生**：

| 派生字段 | 由什么派生 |
|---|---|
| `B{n}` / `D{n}` / `N{n}` 展示编号 | `beats` / `dialogue_units` / `narration_units` 的**数组位置** |
| 节拍的 `｜载体：…` 段 | 该节拍的 `voice_ref` |
| 事实条目的 `｜承载：B{n}/…` | `fact_bindings` ＋ 上面两项 |
| 旁白条目的 `fact_refs` | `fact_bindings` 里指向该旁白的事实 |
| `hook_carrier` 文案 | `hook_bindings` |

⇒ 模型只填**内容与归属**，编号一律不回填；改一条关系不会在三处各改一次。

`beats[].evidence` 与 `frozen_facts[].evidence` 存的是**逐字**的来源片段文本
（`原文依据：` / `原文证据：` 段里那一份原文，含原有引号形态）——本模块**不改写引号**，
这样旧计划经适配器进视图再投影回来是**逐字一致**的。

## 边界（有意）

- 本模块**不做语义判断**：不判"这段正文是否完整表达那条事实"（那是独立核验的责任，R4/主专题 §3.3）。
- 本模块**无副作用**：不改文件、不调模型、不碰缓存。
- 旧协议（数字 ops / 受限 full_plan）保留为**冻结响应回放/兼容适配器**，本模块不做形状猜测。
"""
from __future__ import annotations

import hashlib
import json
import re

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
PRESENTATION_SCHEMA = "presentation_contract.v1"
REVISION_SCHEMA = "plan_revision.v2"


MODE_LEGACY = "legacy_audio"
MODE_HYBRID = "screen_hybrid"
MODES = (MODE_LEGACY, MODE_HYBRID)


def fact_binding_policy(view):
    """Binding selection follows the representation's actual round-trip capacity.

    2026-10-18（A 窄版，用户裁决"允许同镜声画共存"）：`visual_requires_visual_beat` 不再按模式
    置 True。事实的**画面承载**记在事实的 `承载：B<uid>/画面` 上，节拍自己的**声音归属**记在
    节拍的 `载体：旁白k/台词k` 上——两者是不同字段，投影/回读不丢（已离线验证：
    `plan_from_view` → `view_from_plan` 同时保留 voice_ref 与该事实的 visual 承载）。
    因此带声音的节拍**可以**同时作为某条事实的画面承载。
    `max_carriers`（旧模式 1 条）与 `selection`（replace）保持不变：本改动不放开多载体。
    """
    single = str((view or {}).get('mode') or '') == MODE_LEGACY
    return {'max_carriers': 1 if single else None,
            'selection': 'replace' if single else 'extend',
            'visual_requires_visual_beat': False}


def selectable_visual_beat_uids(view):
    strict=fact_binding_policy(view)['visual_requires_visual_beat']
    return [str(beat.get('uid') or '') for beat in (view or {}).get('beats',[])
            if isinstance(beat,dict) and (not strict or
                (isinstance(beat.get('voice_ref'),dict) and beat['voice_ref'].get('kind')==KIND_VISUAL))]

#: 计划契约版本的**模式映射**（主专题 §2：「新模式使用计划契约版本6，旧模式继续使用版本5；
#: 按模式选择期望版本，**不直接提升全局常量**使全部旧缓存失效」）。
PLAN_CONTRACT_VERSION_LEGACY = 5
PLAN_CONTRACT_VERSION_HYBRID = 6
PLAN_CONTRACT_VERSION_BY_MODE = {MODE_LEGACY: PLAN_CONTRACT_VERSION_LEGACY,
                                 MODE_HYBRID: PLAN_CONTRACT_VERSION_HYBRID}

#: 载体种类。`visual` = 该节拍的画面；`screen_card` = 文字卡；`dialogue`/`narration` = 声音条目。
KIND_VISUAL = "visual"
KIND_CARD = "screen_card"
KIND_DIALOGUE = "dialogue"
KIND_NARRATION = "narration"
CARRIER_KINDS = (KIND_VISUAL, KIND_CARD, KIND_DIALOGUE, KIND_NARRATION)
#: **写了的载体标注但解析不出**（越界编号、区间写法、别字）——不是"没标"，
#: 也不是"标了别的"，是**非法**。三种状态必须分得开。
KIND_INVALID = "invalid"

#: 节拍段标签（与 `plan_feasibility` 同一套文法；这里只读，不另造第二套）
SEG_BODY = "节拍："
SEG_PURPOSE = "本镜信息目的："
SEG_EVIDENCE = "原文依据："
SEG_CARRIER = "载体："
_SEG_TAGS = (SEG_BODY, SEG_PURPOSE, SEG_EVIDENCE, SEG_CARRIER)


def _digest(obj) -> str:
    _t = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


def _rec(code, kind, ref, reason, path="", fix=""):
    return {"code": code, "kind": kind, "ref": str(ref or ""), "path": path,
            "reason": reason, "fix": fix}


#: 引用在 `presentation` 块里的**外部形状**（主专题 §2 示意）：
#: 画面载体按节拍 uid、卡片按 card_id、声音按单位 uid。内部统一成 `{"kind","uid"}`，
#: 边界上做一次归一——两套写法同时被读，写出去只写一套。
_REF_UID_KEYS = {"visual": "beat_uid", "screen_card": "card_id",
                 "dialogue": "uid", "narration": "uid"}


#: 三种历史 id 写法的**读取兜底序**（`kind` 的规范键永远排在最前）。
_ID_READ_KEYS = ("uid", "card_id", "beat_uid")


def ref_id_keys(ref) -> list:
    """引用上**实际写了**的 id 键（固定序）——判「多种写法是否冲突」用."""
    _r = ref if isinstance(ref, dict) else {}
    return [_k for _k in _ID_READ_KEYS if str(_r.get(_k) or "")]


def ref_uid(ref) -> str:
    """引用 → 目标 uid。**按 `kind` 的规范键优先**读，其余写法兜底.

    ⚠️ 复核 2026-10-06（第十轮）P1：原先按固定序 `uid > card_id > beat_uid` 取值，
    于是 `{"kind":"screen_card","card_id":"c01","uid":"c02"}` 读成 `c02`、
    `{"kind":"visual","beat_uid":"b01","uid":"b02"}` 读成 `b02` —— **绑定被悄悄改掉**。
    规范键由 `_REF_UID_KEYS` 单源给出（画面是 `beat_uid`、卡片是 `card_id`）。
    真正的**拒收**在 `ref_identity_problems`，这里只负责「读哪个」。
    """
    _r = ref if isinstance(ref, dict) else {}
    _canon = _REF_UID_KEYS.get(str(_r.get("kind") or ""))
    for _k in ([_canon] if _canon else []) + [x for x in _ID_READ_KEYS if x != _canon]:
        _v = _r.get(_k)
        if _v:
            return str(_v)
    return ""


def ref_items(value, where: str, problems=None, code: str = "view_field_type") -> list:
    """引用数组的**安全读取**：不是数组 ⇒ 记一条问题并返回 `[]`（**这一支停止遍历**）.

    复核 2026-10-06（第十一轮）P1：`hook_bindings.carriers = 1` 这类**嵌套类型违约**此前
    是"记一条问题、再继续往下走"，于是 `enumerate(1)` / `list(1)` 抛 `TypeError`——
    **真实修订入口**（`revise_plan_once`）、完整视图入口与读盘入口（`view_from_plan`）
    三处一起崩。形状准入的语义是**拒收这一支**，不是"记一笔、然后当没有"。

    `problems=None` = 只做安全读取、不记账（纯变换与投影路径用，它们自己不产记录）。
    """
    if isinstance(value, list):
        return value
    if value is None:
        # **缺席 / null 不是类型违约**：`hook_bindings` 的旧式单数写法（`carrier`）就是
        # 不写 `carriers`——那不是"给了一个不是数组的东西"，是"没给"。两者必须分开。
        return []
    if problems is not None:
        problems.append(_rec(code, "view", "",
                             "`%s` 必须是数组（现在是 %s）——不是数组就停在这一支，"
                             "不继续按项遍历" % (where, _entity_type_label(value)), where))
    return []


def ref_item_problems(ref, where: str) -> list:
    """**单条引用**的形状准入：成员是对象 + `kind` 合法 + 有目标 id + 别名不冲突.

    复核 2026-10-06（第十二轮）①：`ref_items()` 只检查了**容器**"是不是列表"，
    **成员**没检查——`carriers = [1]` / `["d01"]` / `[True]` / `[[...]]` 在读盘路径上
    全部抛 `AttributeError`（`(ref or {}).get(...)`）。这里把判据补到成员一级，
    并且**错误路径带到成员下标**。`kind` 取 `KIND_INVALID` 是**合法表示**（"写了但解析
    不出"），由 `view_problems` 按第三种状态处置，不算形状违约。
    """
    if not isinstance(ref, dict):
        return [_rec("view_ref_item_type", "view", "",
                     "%s 的每一项都必须是对象（当前是 %s）——不是对象就**停在这一项**："
                     "既不继续读它，也不把它从关系里悄悄删掉"
                     % (where, _entity_type_label(ref)), where)]
    _k = str(ref.get("kind") or "")
    if _k == KIND_INVALID:
        # 「写了载体标注但**解析不出**」是**合法的第三种状态**（不是"没标"、也不是"标了
        # 别的"），由构建者（`_voice_ref_of` 的 `_bad()`）产生、由 `view_problems` 处置。
        # 它没有 uid 是**定义如此**，不是"缺 id"——这里必须放行，否则每一条越界编号的
        # 旧稿都会在读盘时被判成形状违约。
        return []
    _p: list = []
    if _k not in CARRIER_KINDS:
        _p.append(_rec("view_ref_kind_invalid", "view", ref_uid(ref),
                       "%s 的 `kind` 非法：%r（合法：%s）"
                       % (where, _k, list(CARRIER_KINDS)), where))
    if not ref_uid(ref):
        _p.append(_rec("view_ref_id_missing", "view", "",
                       "%s 没有任何目标 id——要 `kind` + 对应身份键（`%s`）"
                       % (where, _REF_UID_KEYS.get(_k, "uid")), where))
    _p.extend(ref_identity_problems(ref, where))
    return _p


def ref_list_problems(value, where: str, problems=None, *,
                      container_code: str = "view_field_type") -> list:
    """引用**数组**的完整形状准入（容器 + 逐成员），返回**通过成员级检查**的那些引用.

    ⚠️ 返回的是"可以继续**检查**的对象"，**不是**清洗后的关系——出现任何问题，调用方
    必须**整条拒收**（基线停止修订），**不许**拿返回值当"过滤掉坏成员的干净表"：
    把损坏的引用从关系里删掉比崩掉更糟，那是**静默丢关系**。
    """
    _out: list = []
    if value is None:
        return _out
    if not isinstance(value, list):
        if problems is not None:
            problems.append(_rec(container_code, "view", "",
                                 "`%s` 必须是数组（现在是 %s）——不是数组就停在这一支，"
                                 "不继续按项遍历"
                                 % (where, _entity_type_label(value)), where))
        return _out
    for _i, _c in enumerate(value):
        _p = ref_item_problems(_c, "%s[%d]" % (where, _i))
        if _p:
            if problems is not None:
                problems.extend(_p)
            continue
        _out.append(_c)
    return _out


def ref_identity_problems(ref, where: str) -> list:
    """引用上的**身份写法冲突** → 问题记录（同值别名允许，冲突在查找之前拒收）.

    复核 2026-10-06（第十轮）P1：这与卡片实体的 `entity_identity_alias_conflict`
    **属于同一缺陷类别**，只是落点不同——实体身份修了，载体引用还没修。载体多了
    id 写法就等于多了「目标是谁」的第二种答案，静默取其中一个 = 悄悄改绑定关系。
    """
    if not isinstance(ref, dict):
        return []
    _keys = ref_id_keys(ref)
    if len({str(ref[_k]) for _k in _keys}) <= 1:
        return []
    return [_rec("view_ref_identity_conflict", "view", ref_uid(ref),
                 "%s 同时给了多个 id 写法且**互不一致**：%s —— 一条引用只能有一个目标，"
                 "冲突必须在查找/绑定之前拒收（静默取其中一个就是把绑定悄悄改掉）"
                 % (where, "、".join("%s=%r" % (_k, ref[_k]) for _k in _keys)), where)]


def _ref_out(ref) -> dict:
    """引用 → 写出去的外部形状。**全函数**：非对象不会再抛（形状判据在准入层）。

    `kind=invalid`（写了标注但解析不出）要把 `raw` **原样带走**：它是"当初写的是什么"
    的唯一痕迹，投影回计划时抹掉它，等于把"这条关系配错了"改成"没标载体"。
    """
    _k = str(ref.get("kind") or "") if isinstance(ref, dict) else ""
    if _k == KIND_INVALID:
        return {"kind": KIND_INVALID, "raw": str((ref or {}).get("raw") or "")}
    return {"kind": _k, _REF_UID_KEYS.get(_k, "uid"): ref_uid(ref)}


def _ref_in(ref):
    """引用 → 内部统一形状 `{"kind","uid"}`；非对象 ⇒ `None`（**不抛异常**）.

    ⚠️ 旧写法是 `(ref or {}).get("kind")`——`ref` 是**真值的非对象**（`1` / `True` /
    `"d01"` / 列表）时 `ref or {}` 还是它本身，于是 `.get` 抛 `AttributeError`。
    这里改成**全函数**；但"返回 `None`"**不等于**"可以静默丢掉"——准入层
    （`ref_list_problems` / `ref_item_problems`）已经在调用它之前把每一处报出来了。
    """
    if not isinstance(ref, dict):
        return None
    _k = str(ref.get("kind") or "")
    if _k == KIND_INVALID:
        # 「写了标注但解析不出」**必须原样带下去**：丢掉它就等于把"这条关系配错了"
        # 这件事从视图里抹掉，而 `view_problems` 正是靠这个标记报第三种状态的。
        return {"kind": KIND_INVALID, "uid": "", "raw": str(ref.get("raw") or "")}
    _u = ref_uid(ref)
    return {"kind": _k, "uid": _u} if _k and _u else None


# ---------------------------------------------------------------------------
# 可编辑视图：构造与解析
# ---------------------------------------------------------------------------
def _unit(uid, text, source=None, **extra):
    # `None` 是"没给正文"，不是字符串 `"None"`（`str(None)` 会造出一段观众读不到、
    # 验收也认不出的假正文）。
    _u = {"uid": str(uid), "text": ("" if text is None else str(text)),
          "source": dict(source or {})}
    _u.update(extra)
    return _u


#: 由**关系**重新生成、不作为"其余字段"搬运的计划键。
_RELATION_KEYS = ("shot_beats", "dialogue_lines", "narration_lines",
                  "required_facts", "hook_spoken", "hook_carrier")
#: 不进 `plan_basis` 的键：关系键（重新生成）、版本戳与核验记录（重新盖章）、
#: `main_events`（= `frozen_events`，由冻结表给）、`presentation`（重新生成）。
_BASIS_SKIP = set(_RELATION_KEYS) | {
    "presentation", "plan_contract_version", "plan_verification",
    "verification_rejected", "plan_revision", "main_events"}

#: `presentation` 块里的**并行 uid 表**：稳定身份必须随块保存，否则投影读回时会被
#: 按数组位置**重新编号**（"稳定 ID 只在内存里稳定"等于不稳定）。旧块缺这些键时按
#: 位置回退，但混合模式的**新**块一律写全。
_PRES_UID_KEYS = ("fact_uids", "beat_uids", "dialogue_uids", "narration_uids")


def plan_basis(plan) -> dict:
    """计划里**不由关系派生**的那部分（`climax` / `causal_chain` / `time_budget` …）.

    R1 的视图是**关系层**的可编辑投影，不是整份计划的替代品：其余字段由这里原样带着走，
    投影回来时原样写回。少了它，"往返"就只剩六个关系字段，`climax` 这类冻结字段会在
    修订链里悄悄丢掉。
    """
    if not isinstance(plan, dict):
        return {}
    return {k: v for k, v in plan.items() if k not in _BASIS_SKIP}


def plan_mode(plan) -> str:
    """计划自称的呈现模式；**非法/未知返回空串**（由调用方结构化拒收，不静默回退）.

    静默回退的后果是把"模式没生效"读成"一切正常"：一份 `screen_hybrid` 计划被当成旧模式
    读，`presentation.screen_cards` 整块被丢掉，卡片在成片里凭空消失。
    """
    _p = (plan or {}).get("presentation")
    if _p is None:
        return MODE_LEGACY
    if not isinstance(_p, dict):
        return ""
    if not _p:
        return MODE_LEGACY
    _m = str(_p.get("mode") or "")
    return _m if _m in MODES else ""


# ---------------------------------------------------------------------------
# 准入的**两层分界**（复核 2026-10-06 第十二轮 ① 的收口）
#
# 旧写法按**码名白名单**（`_FATAL_VIEW_CODES`，四个引用级码）判"视图能不能用"，而读盘
# 内部另外十几个码是"记一笔、`continue` / 留空表、照样出视图"——其中八个是**有损**的：
# 跳过的绑定没 append、卡片被丢、载体表被清空、uid 表按位置重新编号。于是函数自己的
# 契约（「`problems` 非空时 `view` 不可用」）只有那四个码在履行，其余情况返回的是一份
# **被清洗过的**视图，下游还把它当"已检查对象"消费。
#
# 现在**按不变式分层**，不再维护放行名单：
#
# - **读取准入（第一层）**：这份输入**能不能被完整、明确地表示**？容器 / 成员类型、
#   身份写法冲突、重复绑定、已给出的 uid 表失配、事件归属表失配……**任何一条命中 ⇒
#   读不成视图**（返回 `None` + 结构化诊断）。读盘**不做有损转换**——不过滤、不清空、
#   不按位置重新编号——所以不存在"记一笔再继续"。
# - **关系与内容验收（第二层）**：表示得出来、但关系对不对？载体悬空、绑定缺项、事实
#   没有完整展示……这些**保留原貌**，由 `parse_view` / `view_problems` /
#   `content_verification` 报出来交生产者修订。
#
# **合法转换不属于"有损"**：绑定表按事实顺序排列、同值别名归一、按已定关系派生编号，
# 都发生在准入**通过之后**，不能被"发生改写即失败"误伤。
# ---------------------------------------------------------------------------


class PresentationContractError(RuntimeError):
    """视图**消费准入**未通过（投影 / 核验请求 / 卡片消费的边界）.

    带 `problems`（生产者记录），调用方按**结构化拒收**处理——**不许**捕获后返回
    部分结果：那不是"容错"，是把损坏的关系悄悄删掉。
    """

    def __init__(self, problems, where: str = ""):
        self.problems = list(problems or [])
        self.where = str(where or "")
        self.codes = [str((x or {}).get("code") or "") for x in self.problems]
        super().__init__("%s准入未通过：%s" % (
            ("%s " % self.where) if self.where else "", "、".join(self.codes) or "（无记录）"))


def view_from_plan(plan: dict, *, mode: str | None = None) -> tuple:
    """既有计划 → **可编辑视图**（首版/缓存/旧稿都走这里进同一条关系）.

    `returns (view, problems)`：**读取准入**（第一层的判据见本文件顶部那一段）。
    `problems` 非空 ⇔ `view is None`——两者严格互为充要，**没有第三种返回**。

    这一条是复核第十二轮 ① 的落点：旧写法只对四个手写码返回 `None`，其余"记录-继续"
    的码照样交出一份被清洗过的视图。现在**读取准入里的每一条都直接拒读**，读盘不再有
    "丢掉坏项、留着干净项往外走"这条路；关系层的问题**不由这里报**（那是
    `parse_view` / `view_problems` 的事，它们读的是**已经读出来的**视图）。

    `mode=None` ⇒ **按计划自称的模式**。显式传一个模式时以它为准——调用方说"我要按
    新模式读这份计划"，读到旧计划就得报错，不能悄悄退回旧路径。
    """
    if not isinstance(plan, dict) or not plan:
        return None, [_rec("view_plan_missing", "plan", "", "计划缺失或不是对象")]
    _declared = plan_mode(plan)
    if mode is None:
        mode = _declared
    if mode not in MODES:
        return None, [_rec("view_mode_invalid", "plan", "",
                           "计划自称的呈现模式非法或未知：%s——"
                           "不静默退回旧模式（那会把卡片整块丢掉）"
                           % _mode_diagnosis(plan), "presentation.mode")]
    if _declared not in MODES or _declared != mode:
        return None, [_rec("view_mode_mismatch", "plan", "",
                           "计划自称模式 %r（%s）与本次读取模式 %r 不符"
                           % (_declared, _mode_diagnosis(plan), mode),
                           "presentation.mode")]
    if mode == MODE_HYBRID:
        _v, _p = _view_from_hybrid_plan(plan)
    else:
        _v, _p = _view_from_legacy_plan(plan, mode=mode)
    if _p:
        # **读取准入未通过 ⇒ 不交出视图**。这里返回的 `_v` 内部一定是用兜底值拼出来的
        # （位置编号、空表、跳过的项）——交出去就是把"损坏的关系"换成一串看起来正常的
        # 关系。诊断**逐条原样**交给调用方，由它决定停在哪一层。
        return None, _p
    return _v, _p


def _require_list(obj, key, probs, where="") -> list:
    """严格取列表：缺字段/类型不对都产**结构化问题**，绝不抛异常、不悄悄当空表.

    旧写法 `for x in (obj.get(k) or [])`：`k` 是字符串时按字符遍历、是 `None` 时当空表、
    是 `7` 时抛 `TypeError`——三种非法形态给出三种不可预期的行为。
    """
    if key not in obj:
        probs.append(_rec("view_field_missing", "view", "",
                          "视图缺字段 `%s`" % key, where or key))
        return []
    _v = obj[key]
    if not isinstance(_v, list):
        probs.append(_rec("view_field_type", "view", "",
                          "`%s` 必须是数组（现在是 %s）" % (key, type(_v).__name__),
                          where or key))
        return []
    return _v


def _plan_list(plan, key, probs, *, default=None) -> list:
    """严格取计划里的列表字段：类型不对就产结构化问题并返回空表（**不抛异常**）.

    旧写法 `[str(x) for x in (plan.get(k) or [])]` 在 `k` 是数字时抛 `TypeError`、
    在 `k` 是字符串时按字符遍历——两种都不该是"读一份计划"的结果。
    """
    if key not in plan:
        return list(default or [])
    _v = plan[key]
    if _v is None:
        return list(default or [])
    if not isinstance(_v, list):
        probs.append(_rec("view_plan_field_type", "plan", "",
                          "计划字段 `%s` 必须是数组（现在是 %s）"
                          % (key, type(_v).__name__), key))
        return []
    _bad = [i for i, x in enumerate(_v) if not isinstance(x, str)]
    if _bad:
        probs.append(_rec("view_plan_item_type", "plan", "",
                          "计划字段 `%s` 的第 %s 项不是字符串（是 %s）"
                          % (key, _bad[0], type(_v[_bad[0]]).__name__),
                          "%s[%d]" % (key, _bad[0])))
    return _v


def _value_brief(value, limit: int = 40) -> str:
    """原值的**短摘要**（问题记录里带上"现在写的是什么"，不只是类型）."""
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"


def _narration_entries(value, probs, *, field="narration_lines") -> list:
    """`narration_lines` 的**成员与字段准入**（新模式与旧模式**共用这一份**）.

    复核 2026-10-07 三轮 ②：legacy 读盘写成 `[e for e in (raw or []) if isinstance(e, dict)]`
    ——非对象成员被**筛掉**，`narration_lines=[1]` 于是被读成"没有旁白"（丢的是一条
    **声音单位**，比崩更糟："这份计划有几条旁白"静默变小，而 TTS / 字幕 / 承载闭合
    全按这个数算），真实修订入口还会拿这份被清空的计划去调一次模型。
    `text` / `source` 不是字符串时同样**不 `str()` 归一**（`123` 不是 `"123"`）。
    """
    _out: list = []
    if not isinstance(value, list):
        return _out
    for _i, _e in enumerate(value):
        _at = "%s[%d]" % (field, _i)
        if not isinstance(_e, dict):
            probs.append(_rec("view_narration_item_type", "plan", "",
                              "`%s` 必须是对象（现在是 %s：%s）——**不把它过滤掉**"
                              % (_at, _entity_type_label(_e), _value_brief(_e)), _at))
            continue
        _bad = False
        for _f in ("text", "source"):
            if _f in _e and not isinstance(_e.get(_f), str):
                probs.append(_rec("view_narration_%s_type" % _f, "plan", "",
                                  "`%s.%s` 必须是字符串（现在是 %s：%s）"
                                  "——不做字符串化"
                                  % (_at, _f, _entity_type_label(_e.get(_f)),
                                     _value_brief(_e.get(_f))),
                                  "%s.%s" % (_at, _f)))
                _bad = True
        if _bad:
            continue
        _out.append(_e)
    return _out


def _mode_diagnosis(plan) -> str:
    """模式读取失败时的**安全摘要**——绝不在报错路径上再崩一次.

    复核 2026-10-07 三轮 ②：`presentation=1` 时旧写法的诊断再 `.get("mode")` 一次，
    于是"报错本身"抛 `AttributeError`，调用方拿到的是崩溃、不是说明。
    """
    _p = plan.get("presentation") if isinstance(plan, dict) else None
    if _p is None:
        return "`presentation` 缺失"
    if not isinstance(_p, dict):
        return "`presentation` 不是对象（当前是 %s）" % type(_p).__name__
    return "`presentation.mode`=%r" % (_p.get("mode"),)


def _view_from_hybrid_plan(plan: dict) -> tuple:
    """`plan_contract_version=6` 的**新模式**计划 → 可编辑视图（关系读 `presentation` 块）.

    身份**按真实 UID 读**，不按数组位置重写：`fact_bindings` 只是"哪条事实由谁承载"的表，
    它的**排列顺序不携带任何含义**——按位置重写 `fact_uid` 会让"调换两行绑定"变成"两条
    事实串绑"，而那本该是同一种关系。
    """
    _p = plan.get("presentation")
    if not isinstance(_p, dict) or _p.get("schema") != PRESENTATION_SCHEMA:
        return None, [_rec("view_presentation_missing", "plan", "",
                           "新模式计划必须带 `presentation`（schema=%s）——不按旧形状猜"
                           % PRESENTATION_SCHEMA, "presentation")]
    _probs: list = []
    _beats_txt = [str(b) for b in _plan_list(plan, "shot_beats", _probs)]
    _lines = [str(x) for x in _plan_list(plan, "dialogue_lines", _probs)]
    _facts = [str(f) for f in _plan_list(plan, "required_facts", _probs)]
    for _k in ("main_events", "hook_spoken"):
        _plan_list(plan, _k, _probs)
    if _probs:
        return None, _probs

    # ---- 模式合同：新模式必须**显式** `narration_lines=[]`（`null` 与缺字段都不是"空"）----
    # ⚠️ 这里只判**读取准入**那三条（缺字段 / `null` / 非数组）：那三种情况下"旁白单位
    # 到底有几条"**无法确定**"，读不出来就不猜。
    # 「新模式带了旁白」**不是**读取准入——那份计划能被**完整、明确地表示**（旁白单位
    # 原样读进视图，一条不丢、一条不改），所以读盘放行，由**关系层**报出来
    # （`parse_view` 的 `view_hybrid_narration_forbidden`，以及
    # `plan_feasibility.presentation_records` 的 `presentation_narration_forbidden`）。
    # 旧写法把它记成读盘问题：它既不在四码白名单里（读得出来），又让读盘看起来
    # "有错但不拦"——那正是"记一笔、继续走"的形态。
    _narr_raw = plan.get("narration_lines", "__missing__")
    _units_n: list = []
    if _narr_raw == "__missing__":
        _probs.append(_rec("view_narration_field_missing", "plan", "",
                           "新模式必须显式给出 `narration_lines: []`（缺字段 ≠ 没有旁白）",
                           "narration_lines"))
    elif _narr_raw is None:
        _probs.append(_rec("view_narration_null", "plan", "",
                           "`narration_lines` 是 `null`——新模式要的是**显式空数组**，"
                           "`null` 不是「没有」（缺席≠null 的同一口径）", "narration_lines"))
    elif not isinstance(_narr_raw, list):
        _probs.append(_rec("view_narration_type", "plan", "",
                           "`narration_lines` 必须是数组（现在是 %s）"
                           % type(_narr_raw).__name__, "narration_lines"))

    # ---- 图外旁白：**成员与字段**逐条检查，**不静默过滤** ----
    # 复核 2026-10-06 二轮 P1：`narration_lines=[1]` 旧读数 = 「返回可用视图、问题为空、
    # 旁白被过滤成 `[]`」——丢的是一条**声音单位**。丢一条声音关系比崩更糟：它会让
    # "这条计划有几条旁白"静默变小，而下游（TTS / 字幕 / 承载闭合）全都按这个数算。
    _narr_objs = _narration_entries(_narr_raw, _probs)

    # ---- 并行 uid 表（稳定身份随块保存；缺表则按位置回退并**如实记录**）----
    def _uids(key, n, prefix):
        """`presentation.<key>` 的 uid 表 → `(uids, 是否来自已有表)`.

        ⚠️ **表已给出却失配 ⇒ 拒读，不按位置重新编号**（复核第十二轮 ① 的 UID 分支写死）。
        下面那两处 `prefix + "%02d"` 的兜底值**只为把遍历走完、把剩下的诊断收集齐**而存在
        ——读取准入一失败 `view_from_plan` 就返回 `None`，这些值**不会被交出去**。
        表**缺席**（`None`）走的是"已确认的旧格式缺表适配"，按位置派生编号是既有兼容规则，
        **不记问题**。
        """
        _v = _p.get(key)
        if _v is None:
            return [prefix + "%02d" % (_i + 1) for _i in range(n)], False
        if not isinstance(_v, list) or len(_v) != n:
            _probs.append(_rec("view_uid_table_mismatch", "plan", "",
                               "`presentation.%s` 必须是与目标等长的 uid 数组"
                               "（现在 %s / 目标 %d 项）——表已经给出了就不按位置重编号"
                               % (key, "非数组" if not isinstance(_v, list) else
                                  "%d 项" % len(_v), n), "presentation.%s" % key))
            return [prefix + "%02d" % (_i + 1) for _i in range(n)], False
        _out = [str(x) for x in _v]
        if any(not x for x in _out) or len(set(_out)) != len(_out):
            _probs.append(_rec("view_uid_table_invalid", "plan", "",
                               "`presentation.%s` 里有空 uid 或重复 uid"
                               "——重复 uid 会让两条实体共用同一个身份" % key,
                               "presentation.%s" % key))
        return _out, True

    _doc_n = len(_narr_objs)
    _fact_uids, _ = _uids("fact_uids", len(_facts), "f")
    _beat_uids, _ = _uids("beat_uids", len(_beats_txt), "b")
    _dial_uids, _ = _uids("dialogue_uids", len(_lines), "d")
    _narr_uids, _ = _uids("narration_uids", _doc_n, "n")

    _frozen = []
    for _u, _f in zip(_fact_uids, _facts):
        _claim, _ev = _split_claim_evidence(_f)
        _frozen.append({"uid": _u, "claim": _claim, "evidence": _ev})
    _units_d = [_unit(_u, _t) for _u, _t in zip(_dial_uids, _lines)]
    _units_n = [_unit(_u, _e.get("text"), _src_span(_e.get("source")))
                for _u, _e in zip(_narr_uids, _narr_objs)]

    # ---- 节拍：事件归属**按块里声明的那份读**（没有就标「未确定」，不按位置编）----
    _events = [str(e) for e in (plan.get("main_events") or [])]
    _beat_events = _p.get("beat_events")
    _declared = isinstance(_beat_events, list) and len(_beat_events) == len(_beats_txt)
    if _beat_events is not None and not _declared:
        _probs.append(_rec("view_beat_events_mismatch", "plan", "",
                           "`presentation.beat_events` 必须是与节拍等长的数组的数组",
                           "presentation.beat_events"))
    _cards = []
    _cards_raw = _require_list(_p, "screen_cards", _probs, "presentation.screen_cards")
    for _i, _c in enumerate(_cards_raw):
        if not isinstance(_c, dict):
            _probs.append(_rec("view_card_item_invalid", "card", "",
                               "卡片条目必须是对象（第 %d 项是 %s）"
                               % (_i + 1, type(_c).__name__),
                               "presentation.screen_cards[%d]" % _i))
            continue
        # 复核第十二轮（补查）P1：卡片条目**还要过实体合同**（身份别名冲突、字段类型）——
        # 与 `parse_view` 共用同一份 `entity_contract_problems`。旧写法只判"是不是对象"，
        # 于是 `card_id` 与 `uid` 打架的卡片被消费者按 `card_id` 静默取用。
        # 记账之后**照旧收下**：读取准入已经失败、这张视图不会被交出去，收下只是让
        # 后面的引用解析（`ref_uid`）还能走完、把剩下的诊断收齐。
        _probs.extend(entity_contract_problems(_c, "screen_cards",
                                               "presentation.screen_cards[%d]" % _i))
        _cards.append(dict(_c))

    _beats_v = []
    for _i, _b in enumerate(_beats_txt, 1):
        _rec_b = _beat_segments(_b)
        _ev = []
        if _declared:
            _row = _beat_events[_i - 1]
            # ⚠️ 复核第十二轮（补查）P1：**每一行也必须检查**。旧写法只判了
            # `beat_events` 整表是数组、长度对不对，行本身没判——`beat_events[0]=1`
            # 直接 `for x in 1` 抛 `TypeError`，读盘入口崩在半路。
            # 成员坚持 `str`：`str(x)` 会把 `1` 悄悄变成 `"1"`（一个不存在的
            # 事件名），那不是"归一"、是把违约转成了悬空引用。
            if not isinstance(_row, list) or any(not isinstance(x, str) for x in _row):
                _probs.append(_rec(
                    "view_beat_events_item_type", "plan", "",
                    "`presentation.beat_events[%d]` 必须是**字符串数组**（现在是 %s）"
                    "——每一项是 `main_events` 里的事件名"
                    % (_i - 1, _entity_type_label(_row)), "presentation.beat_events[%d]"
                    % (_i - 1)))
            else:
                _ev = list(_row)
        _beats_v.append({
            "uid": _beat_uids[_i - 1],
            "event_uids": _ev,
            "text": _rec_b.get(SEG_BODY, ""),
            "purpose": _rec_b.get(SEG_PURPOSE, ""),
            "evidence": _rec_b.get(SEG_EVIDENCE, ""),
            "source": {},
            "voice_ref": _voice_ref_of(_rec_b.get(SEG_CARRIER, ""), _units_d, _units_n,
                                       _cards, _beat_uids[_i - 1]),
        })

    # ---- 承载关系：**按 uid 索引**读，不看数组位置；重复项在这里就报出来 ----
    _known = {f["uid"] for f in _frozen}
    _bind: list = []
    _seen_bind: dict = {}
    for _i, _b in enumerate(_require_list(_p, "fact_bindings", _probs,
                                          "presentation.fact_bindings")):
        if not isinstance(_b, dict):
            _probs.append(_rec("view_binding_item_invalid", "fact", "",
                               "`fact_bindings` 第 %d 项不是对象" % (_i + 1),
                               "presentation.fact_bindings[%d]" % _i))
            continue
        _fu = str(_b.get("fact_uid") or "")
        if _fu not in _known:
            _probs.append(_rec("view_binding_unknown_fact", "fact", _fu,
                               "`fact_bindings` 指向不存在的冻结事实：%r" % _fu,
                               "presentation.fact_bindings[%d].fact_uid" % _i))
            continue
        if _fu in _seen_bind:
            _probs.append(_rec("view_binding_duplicate", "fact", _fu,
                               "事实 %s 在 `fact_bindings` 里出现了**两次**"
                               "（第 %d 项与第 %d 项）——每条事实恰好一项绑定"
                               % (_fu, _seen_bind[_fu] + 1, _i + 1),
                               "presentation.fact_bindings[%d]" % _i))
            continue
        # `_seen_bind` 只用来判**重复**（"这条事实在这张表里已经出现过"），所以写在
        # `carriers` 检查之前是对的。旧写法里它同时被当成"这条事实已经有绑定了"用
        # （`_missing` 从它反推）——于是 `carriers` 类型违约让绑定被跳过后，那条
        # 「这条事实没有绑定」的判据**再也不报**，成为"丢了绑定还不出声"（复核
        # 2026-10-06 第十二轮 ① 的现场）。现在读盘不交视图、也不再报绑定缺项，
        # 这个双重用途取消：**缺项由关系层 `parse_view` 按读出来的视图判**。
        _seen_bind[_fu] = _i
        _car_raw = _b.get("carriers")
        if _car_raw is None:
            # **缺席 = 没有载体**（不是类型违约）：旧式单数写法就是不写 `carriers`，
            # `null` 也按缺席读。"没有载体"由**关系层**判（`view_binding_empty`），
            # 不是读取准入——这份关系表示得出来。
            _bind.append({"fact_uid": _fu, "carriers": []})
            continue
        # **一整份判据**（容器 + 成员 + kind + 身份 + 别名冲突），错误路径带到成员下标。
        # 任何一条命中 ⇒ 读取准入不通过 ⇒ `view_from_plan` **不交视图**；
        # **不**把坏成员过滤掉继续走（那是静默丢关系，复核第十二轮 ① 明文）。
        _ok_car = ref_list_problems(
            _car_raw, "presentation.fact_bindings[%d].carriers" % _i, _probs,
            container_code="view_binding_carriers_type")
        if isinstance(_car_raw, list) and len(_ok_car) == len(_car_raw):
            _bind.append({"fact_uid": _fu,
                          "carriers": [_ref_in(_c) for _c in _ok_car]})
    # **把绑定表规范化到事实顺序**：`fact_bindings` 的**排列不携带含义**。不规范化的话，
    # 同一份关系因为数组顺序不同就会算出不同的 `view_digest`——循环判定会把"只重排了
    # 绑定"读成"改过了"，而关系其实一个字没动。
    _by_uid = {b["fact_uid"]: b for b in _bind}
    _bind = [_by_uid[f["uid"]] for f in _frozen if f["uid"] in _by_uid]
    # ⚠️ 「这些冻结事实没有绑定」**不由读盘报**（复核第十二轮 ① 的分层）：一条事实没有
    # 绑定**表示得出来**（就是"这项缺席"），属于**关系层**——`parse_view` 报
    # `view_binding_missing`。旧写法在这里也报一遍，于是同一个判断有两个生产者，
    # 而读盘那一条还**报不全**（见下面 `_seen_bind` 的位置）。

    # ---- 钩子：**全部关系**都要带（多句钩子不能只留第一句）----
    _hb = _p.get("hook_bindings")
    _spoken: list = []
    _carriers: list = []
    if _hb is not None:
        if not isinstance(_hb, dict):
            _probs.append(_rec("view_hook_bindings_type", "hook", "",
                               "`hook_bindings` 必须是对象", "presentation.hook_bindings"))
        else:
            _sp_raw = _hb.get("spoken_uids")
            if _sp_raw is None:
                _spoken = []
            elif not isinstance(_sp_raw, list):
                _probs.append(_rec("view_hook_spoken_type", "hook", "",
                                   "`hook_bindings.spoken_uids` 必须是数组（现在是 %s）"
                                   % _entity_type_label(_sp_raw),
                                   "presentation.hook_bindings.spoken_uids"))
            else:
                _spoken = [str(x) for x in _sp_raw]
            _cra = _hb.get("carriers")
            if _cra is None and _hb.get("carrier") is not None:
                _cra = [_hb.get("carrier")]          # 兼容单数写法
            _ok_hc = ref_list_problems(_cra, "presentation.hook_bindings.carriers",
                                       _probs,
                                       container_code="view_hook_carriers_type")
            if isinstance(_cra, list) and len(_ok_hc) == len(_cra):
                for _c in _ok_hc:
                    _carriers.append(_ref_in(_c))

    # ---- 出处修正：**成员必须是对象**（旧写法把非对象的项**过滤掉** ⇒ 静默丢修正）----
    # 复核第十二轮（补查）P1：`evidence_updates=[1]` 旧读数 = 「返回可用视图、问题为空、
    # 修正记录被过滤成 `[]`」。「更正了出处」是一条**内容变化**，把它悄悄丢掉比崩更糟。
    _eu_raw = _p.get("evidence_updates")
    _eu: list = []
    if _eu_raw is not None:
        if not isinstance(_eu_raw, list):
            _probs.append(_rec("view_evidence_updates_type", "plan", "",
                               "`presentation.evidence_updates` 必须是数组（现在是 %s）"
                               % _entity_type_label(_eu_raw),
                               "presentation.evidence_updates"))
        else:
            for _i, _x in enumerate(_eu_raw):
                if not isinstance(_x, dict):
                    _probs.append(_rec(
                        "view_evidence_item_type", "plan", "",
                        "`presentation.evidence_updates[%d]` 必须是对象（现在是 %s）"
                        "——**不把它过滤掉**：「更正了出处」这件事不许被悄悄丢掉"
                        % (_i, _entity_type_label(_x)),
                        "presentation.evidence_updates[%d]" % _i))
                    continue
                if "evidence" in _x and not isinstance(_x.get("evidence"), str):
                    _probs.append(_rec(
                        "view_evidence_item_type", "plan", "",
                        "`presentation.evidence_updates[%d].evidence` 必须是字符串"
                        "（现在是 %s）" % (_i, _entity_type_label(_x.get("evidence"))),
                        "presentation.evidence_updates[%d].evidence" % _i))
                    continue
                _eu.append(dict(_x))

    _view = {
        "schema": PRESENTATION_SCHEMA, "mode": MODE_HYBRID,
        "event_binding": "declared" if _declared else "unknown",
        "frozen_facts": _frozen, "frozen_events": _events,
        "beats": _beats_v, "dialogue_units": _units_d, "narration_units": _units_n,
        "screen_cards": _cards,
        "fact_bindings": _bind,
        "hook_bindings": {"spoken_uids": _spoken, "carriers": _carriers},
        "evidence_updates": _eu,
        "plan_basis": plan_basis(plan),
    }
    return _view, _probs


def _semantic_legacy(plan) -> bool:
    from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
    return isinstance(plan, dict) and plan.get(LEGACY_ACCEPTANCE_FIELD) == LEGACY_ACCEPTANCE_VERSION


def _beat_body_problems(text, uid, path) -> list:
    if isinstance(text, str) and text.strip():
        return []
    return [_rec("view_beat_text_blank", "beat", uid,
                 "节拍缺少可读取的正文：shot_beats 必须含非空 `节拍：正文` 段；"
                 "修订视图的 beats[].text 也须非空。编号或其它标签不能代替正文，"
                 "不要只写本镜信息目的或原文依据。", path)]


def plan_beat_body_problems(plan) -> list:
    """Reject beat fields that cannot survive the new legacy projection."""
    if not _semantic_legacy(plan) or not isinstance(plan.get("shot_beats"), list):
        return []
    from step01_script_gen.plan_feasibility import BeatRecord
    problems = []
    supported = {SEG_BODY, SEG_PURPOSE, SEG_EVIDENCE, SEG_CARRIER}
    for index, raw in enumerate(plan["shot_beats"]):
        if not isinstance(raw, str):
            continue  # The list/member schema owns type errors.
        beat = BeatRecord.parse(raw)
        uid, path = "b%02d" % (index + 1), "shot_beats[%d]" % index
        problems.extend(_beat_body_problems(beat.body, uid, path))
        if any(tag not in supported and text.strip() for tag, text in beat.segments):
            problems.append(_rec("view_beat_segment_unrepresentable", "beat", uid,
                                 "节拍含不能无损投影的段：仅支持节拍、本镜信息目的、原文依据、载体；"
                                 "请将实际正文写入 `节拍：`，事实关系写入 required_facts 的承载字段。", path))
        if any(beat.count(tag) > 1 for tag in supported):
            problems.append(_rec("view_beat_segment_duplicate", "beat", uid,
                                 "节拍字段重复；每种标签只能出现一次，不能只取第一段而丢掉其余内容。", path))
    return problems


def _view_from_legacy_plan(plan: dict, *, mode: str = MODE_LEGACY) -> tuple:
    """版本 5 的**旧形状**计划 → 可编辑视图（关系读 `｜载体：` / `｜承载：` 文案）."""
    _probs: list = plan_beat_body_problems(plan)

    _beats = [str(b) for b in _plan_list(plan, "shot_beats", _probs)]
    _lines = [str(x) for x in _plan_list(plan, "dialogue_lines", _probs)]
    _facts = [str(f) for f in _plan_list(plan, "required_facts", _probs)]
    for _k in ("main_events", "hook_spoken"):
        _plan_list(plan, _k, _probs)
    _narr_raw = plan.get("narration_lines")
    if _narr_raw is not None and not isinstance(_narr_raw, list):
        _probs.append(_rec("view_plan_field_type", "plan", "",
                           "计划字段 `narration_lines` 必须是数组（现在是 %s）"
                           % type(_narr_raw).__name__, "narration_lines"))
    # **成员与字段准入与新模式共用**（复核 2026-10-07 三轮 ②）：旧写法在这里
    # `[e for e in ... if isinstance(e, dict)]` 把非对象成员筛掉——`[1]` 被读成"没有旁白"。
    _narr = _narration_entries(_narr_raw, _probs)
    # `hook_carrier` 也不能 `str()` 洗白：数字/对象被洗成一段字面量后，`_split_decls`
    # 认不出 ⇒ **静默变成"没有钩子载体"**（与首版入口同一根因）。
    _hc_raw = plan.get("hook_carrier")
    if _hc_raw is not None and not isinstance(_hc_raw, str):
        _probs.append(_rec("view_hook_carrier_type", "plan", "",
                           "计划字段 `hook_carrier` 必须是字符串或 null（现在是 %s：%s）"
                           "——不做字符串化：认不出的声明不等于没有声明"
                           % (_entity_type_label(_hc_raw), _value_brief(_hc_raw)),
                           "hook_carrier"))
    if _probs:
        return None, _probs

    # 冻结事实：主张与证据从事实条目拆出来（主张不可编辑，证据可经 evidence_updates 更正）
    _frozen = []
    for _i, _f in enumerate(_facts, 1):
        _claim, _ev = _split_claim_evidence(_f)
        _frozen.append({"uid": "f%02d" % _i, "claim": _claim, "evidence": _ev})

    _units_d = [_unit("d%02d" % _i, _t) for _i, _t in enumerate(_lines, 1)]
    _units_n = [_unit("n%02d" % _i, _e.get("text"), _src_span(_e.get("source")))
                for _i, _e in enumerate(_narr, 1)]

    _events = [str(e) for e in (plan.get("main_events") or [])]
    _beats_v = []
    for _i, _b in enumerate(_beats, 1):
        _rec_b = _beat_segments(_b)
        _uid = "b%02d" % _i
        _beats_v.append({
            "uid": _uid,
            # ⚠️ 旧计划**没有**逐节拍的事件归属。这里**留空并标「未确定」**——按位置一对一
            # 编一份出来就是伪造关系（"仓库外景"会被登记成"周明辞职"，而关系检查照样
            # 通过）。归属由生产者补齐（v2 请求里明确要求 `event_uids`），代码不猜。
            "event_uids": [],
            "text": _rec_b.get(SEG_BODY, ""),
            "purpose": _rec_b.get(SEG_PURPOSE, ""),
            "evidence": _rec_b.get(SEG_EVIDENCE, ""),   # 逐字保留（含引号）——适配器往返要求
            "source": {},
            "voice_ref": _voice_ref_of(_rec_b.get(SEG_CARRIER, ""), _units_d, _units_n,
                                       None, _uid),
        })

    # 承载关系：从事实条目的 `｜承载：Bn/…` 反推（这是**旧形状**里的权威，读数不是重算）
    _bind = []
    for _i, _f in enumerate(_facts, 1):
        _decl = _carrier_decl(_f)
        _ref = _ref_from_decl(_decl, _beats_v, _units_d, _units_n)
        if not _ref and _decl:
            # **写了承载声明但解析不出** ⇒ 保留成 `kind=invalid` 的**第三种状态**：
            # 不默清、也不在这里拒读（关系层 `parse_view` 会报 `view_binding_dangling`）。
            # ⚠️ 旧写法的 `if _ref is None:` 分支是**死代码**——`_ref_from_decl` 从不返回
            # `None`（空串、越界编号、认不出的写法一律返回 `[]`），所以那条
            # `view_carrier_decl_invalid` 诊断**一次都没发过**：一条解析不出的声明被
            # **静默**读成"这条事实没有载体"。
            _ref = [{"kind": KIND_INVALID, "uid": "", "raw": _decl}]
        _bind.append({"fact_uid": "f%02d" % _i, "carriers": _ref})

    _hook_decls = [x for x in _split_decls(_hc_raw or "") if str(x).strip()]
    _hook_carriers: list = []
    for _d in _hook_decls:
        _r = _ref_from_decl(_d, _beats_v, _units_d, _units_n) or []
        # 同一条口径：解析不出 ⇒ 第三种状态（**不默清**），由关系层报出来
        _hook_carriers.append(_r[0] if _r
                              else {"kind": KIND_INVALID, "uid": "", "raw": _d})
    _spoken = [str(x) for x in (plan.get("hook_spoken") or [])]
    _spoken_uids = [u["uid"] for u in _units_d if u["text"] in _spoken]

    _view = {
        "schema": PRESENTATION_SCHEMA,
        "mode": mode,
        "event_binding": "unknown",
        "frozen_facts": _frozen,
        "frozen_events": _events,
        "beats": _beats_v,
        "dialogue_units": _units_d,
        "narration_units": _units_n,
        "screen_cards": [],
        "fact_bindings": _bind,
        "hook_bindings": {"spoken_uids": _spoken_uids, "carriers": _hook_carriers},
        "evidence_updates": [],
        "plan_basis": plan_basis(plan),
    }
    return _view, _probs


def _split_decls(text) -> list:
    """承载声明串 → 逐条（`；` / `;` 分隔）——**多句钩子不许只留第一句**."""
    _t = str(text or "").strip()
    if not _t:
        return []
    _out = []
    for _p in re.split("[；;]", _t):
        _p = _p.strip()
        if _p:
            _out.append(_p)
    return _out


def parse_view(obj, *, expected_mode: str | None = None) -> tuple:
    """严格解析视图（形状、uid、引用、模式合同）；`returns (view, problems)`.

    **所有非法输入都返回结构化问题**：缺字段、类型不对（字符串/数字/null 冒充数组）、
    重复 uid、悬空引用、绑定缺项/多项、模式合同冲突。没有一条路径会抛异常——调用方
    拿到的是"哪里不合法"，不是 traceback。
    """
    _p: list = []
    if not isinstance(obj, dict):
        return None, [_rec("view_not_object", "view", "", "视图不是 JSON 对象")]
    if obj.get("schema") != PRESENTATION_SCHEMA:
        return None, [_rec("view_schema_unknown", "view", "",
                           "未知或缺失的视图 schema：%r（期望 %s）"
                           % (obj.get("schema"), PRESENTATION_SCHEMA), "schema")]
    _mode = str(obj.get("mode") or "")
    if _mode not in MODES:
        return None, [_rec("view_mode_invalid", "view", "", "非法 mode：%r" % _mode, "mode")]
    if expected_mode is not None and _mode != expected_mode:
        return None, [_rec("view_mode_mismatch", "view", "",
                           "视图 mode=%s 与本次期望（%s）不符" % (_mode, expected_mode), "mode")]

    _req = ("frozen_facts", "frozen_events", "beats", "dialogue_units",
            "narration_units", "screen_cards", "fact_bindings", "hook_bindings")
    _missing = [_k for _k in _req if _k not in obj]
    if _missing:
        return None, [_rec("view_field_missing", "view", "",
                           "视图缺字段：%s" % "、".join(_missing), ",".join(_missing))]
    for _k in ("frozen_events",):
        if not isinstance(obj.get(_k), list):
            return None, [_rec("view_field_type", "view", "",
                               "`%s` 必须是数组（现在是 %s）"
                               % (_k, type(obj.get(_k)).__name__), _k)]
    # 字符串冒充数组 / 数字冒充数组：`for x in obj[k]` 会按字符遍历或抛 TypeError
    for _k in ("beats", "dialogue_units", "narration_units", "screen_cards",
               "fact_bindings", "frozen_facts"):
        _v = obj.get(_k)
        if not isinstance(_v, list):
            return None, [_rec("view_field_type", "view", "",
                               "`%s` 必须是数组（现在是 %s）"
                               % (_k, type(_v).__name__), _k)]
        for _i, _e in enumerate(_v):
            if not isinstance(_e, dict):
                _p.append(_rec("view_item_type", "view", "",
                               "`%s[%d]` 必须是对象（现在是 %s）"
                               % (_k, _i, type(_e).__name__), "%s[%d]" % (_k, _i)))
    if not isinstance(obj.get("hook_bindings"), dict):
        _p.append(_rec("view_field_type", "view", "", "`hook_bindings` 必须是对象",
                       "hook_bindings"))
    if obj.get("evidence_updates") is not None \
            and not isinstance(obj.get("evidence_updates"), list):
        _p.append(_rec("view_field_type", "view", "", "`evidence_updates` 必须是数组",
                       "evidence_updates"))
    if _p:
        return None, _p

    # ---- uid 唯一性（跨全部实体；重复一律拒收，不按相似度猜）----
    _seen: dict = {}
    for _k in ("beats", "dialogue_units", "narration_units", "screen_cards"):
        for _e in obj[_k]:
            _u = ref_uid(_e)
            if not _u:
                _p.append(_rec("view_uid_missing", "view", "", "`%s` 里有条目缺 uid" % _k, _k))
                continue
            if _u in _seen:
                _p.append(_rec("view_uid_duplicate", "view", _u,
                               "uid 重复：%s（已在 %s 出现）" % (_u, _seen[_u]), _k))
            _seen[_u] = _k
    for _k in ("beats", "dialogue_units", "narration_units", "screen_cards"):
        for _e in obj[_k]:
            _p.extend(entity_contract_problems(_e, _k, "%s" % _k))

    # ---- 载体引用的**身份写法冲突**：必须在 `_assign_formal_uids` **之前**核 ----
    # 复核 2026-10-06（第十轮）P1：改写引用那一步用 `_ref_in` 把引用重建成
    # `{"kind","uid"}`，**多余的 id 写法会被就地抹掉**——冲突一旦走到那里就再也看不见，
    # 结果是 applied + 绑定悄悄换人。所以形状门这里就拒收（两条入口共用这一处）。
    for _i, _b in enumerate(obj["fact_bindings"]):
        ref_list_problems(_b.get("carriers"), "fact_bindings[%d].carriers" % _i, _p,
                          container_code="view_binding_carriers_type")
    _hb_p = obj.get("hook_bindings") if isinstance(obj.get("hook_bindings"), dict) else {}
    ref_list_problems(_hb_p.get("carriers"), "hook_bindings.carriers", _p,
                      container_code="view_hook_carriers_type")
    for _bi, _b in enumerate(obj["beats"]):
        _vr0 = _b.get("voice_ref")
        if _vr0 is not None:
            _p.extend(ref_item_problems(_vr0, "beats[%d].voice_ref" % _bi))
    for _ci, _u in enumerate(ref_items(_hb_p.get("spoken_uids"),
                                       "hook_bindings.spoken_uids", _p)):
        if not isinstance(_u, str):
            _p.append(_rec("view_hook_spoken_item_type", "hook", "",
                           "`hook_bindings.spoken_uids[%d]` 必须是 id 字符串（当前是 %s）"
                           % (_ci, type(_u).__name__), "hook_bindings.spoken_uids"))
    # ⚠️ **消费准入那几处字段契约在这里也要过一遍**（复核第十二轮补查 P1）：
    # `parse_view` 与 `view_admission_problems` **共用同一份判据**，两处各写一遍就会漂移
    # ——本轮漂移出来的正是 `evidence_updates` / `plan_basis` / 冻结事实字段类型这三处。
    _p.extend(view_field_contract_problems(obj))
    if _p:
        return None, _p
    _frozen_uids = [str(f.get("uid") or "") for f in obj["frozen_facts"]]
    if "" in _frozen_uids or len(set(_frozen_uids)) != len(_frozen_uids):
        _p.append(_rec("view_fact_uid_invalid", "view", "",
                       "冻结事实的 uid 缺失或重复", "frozen_facts"))

    # ---- 模式合同：一条明确的边界 ----
    if _mode == MODE_HYBRID:
        _n = obj.get("narration_units")
        if _n:
            _p.append(_rec("view_hybrid_narration_forbidden", "view", "",
                           "新模式必须显式 `narration_units=[]`（不自动清空旁白）",
                           "narration_units"))
        if str(obj.get("event_binding") or "") != "declared":
            _p.append(_rec("view_event_binding_undeclared", "view", "",
                           "新模式必须声明每条节拍的事件归属（`event_binding=declared`）"
                           "——按位置一对一编一份出来是伪造关系，不能当覆盖证明",
                           "event_binding"))
    elif obj.get("screen_cards"):
        _p.append(_rec("view_legacy_cards_forbidden", "view", "",
                       "旧模式不接受 `screen_cards`", "screen_cards"))
    _eb = str(obj.get("event_binding") or "unknown")
    if _eb not in ("declared", "unknown"):
        _p.append(_rec("view_event_binding_invalid", "view", "",
                       "`event_binding` 只能是 declared / unknown：%r" % _eb,
                       "event_binding"))

    # ---- 引用闭合 ----
    _beat_uids = {ref_uid(b) for b in obj["beats"]}
    _card_ids = {ref_uid(c) for c in obj["screen_cards"]}
    _d_uids = {ref_uid(u) for u in obj["dialogue_units"]}
    _n_uids = {ref_uid(u) for u in obj["narration_units"]}

    def _bad_ref(ref, where):
        """引用 → `(具体 code, 原因)`；`code` 为空表示这里没有问题.

        返回具体 code 而不是只返回文案：**身份写法冲突**要与「悬空」分开记，
        否则调用方会把它笼统写成 `view_*_dangling`，诊断时看不出是"指错了"还是"写法打架"。
        """
        if not isinstance(ref, dict):
            return "", ("%s 的载体必须是对象（当前是 %s）——`[1]` 这类序号占位不是载体"
                        % (where, type(ref).__name__))
        _cf = ref_identity_problems(ref, where)
        if _cf:
            return _cf[0]["code"], _cf[0]["reason"]
        _k = str((ref or {}).get("kind") or "")
        _t = ref_uid(ref)
        if _k == KIND_INVALID:
            _raw = str((ref or {}).get("raw") or "")
            return "", ("%s 的载体标注**解析不出**（写了但指不到任何条目）：%r——越界编号、"
                        "区间写法（`台词1-2`）和别字都会落到这里；它不等于「没标载体」"
                        % (where, _raw))
        if _k not in CARRIER_KINDS:
            return "", "%s 的载体 kind 非法：%r" % (where, _k)
        _pool = {KIND_VISUAL: _beat_uids, KIND_CARD: _card_ids,
                 KIND_DIALOGUE: _d_uids, KIND_NARRATION: _n_uids}[_k]
        if _t not in _pool:
            return "", "%s 引用了不存在的 %s：%r" % (where, _k, _t)
        return "", ""

    # `voice_ref` 也必须闭合（旧写法只查承载表：节拍标着一个不存在的声音单位时无人发现）
    for _b in obj["beats"]:
        _vr = _b.get("voice_ref")
        if _vr is None:
            continue
        if not isinstance(_vr, dict):
            _p.append(_rec("view_voice_ref_type", "beat", ref_uid(_b),
                           "节拍 %s 的 `voice_ref` 必须是对象或 null" % ref_uid(_b),
                           "beats[%s].voice_ref" % ref_uid(_b)))
            continue
        _c, _m = _bad_ref(_vr, "节拍 %s 的 voice_ref" % ref_uid(_b))
        if _m:
            _p.append(_rec(_c or "view_voice_ref_dangling", "beat", ref_uid(_b), _m,
                           "beats[%s].voice_ref" % ref_uid(_b)))

    # 卡片的 `beat_uid` 也是一条**引用**（卡片挂在哪条节拍上）：不在节拍集合里 = 悬空。
    # 复核 2026-10-06（第十轮）：它此前只被检查「非空」，于是增量响应里把新增卡片挂到
    # 一个**不存在的节拍**上照样 `applied`——卡片此后永远落不了位，却过了整条验收链。
    for _c in obj["screen_cards"]:
        _bu = str(_c.get("beat_uid") or "")
        if _bu and _bu not in _beat_uids:
            _p.append(_rec("view_card_beat_dangling", "card", ref_uid(_c),
                           "卡片 %s 挂在一条不存在的节拍上：%r（画面/卡片的引用都要闭合）"
                           % (ref_uid(_c), _bu),
                           "screen_cards[%s].beat_uid" % ref_uid(_c)))

    _seen_fact: list = []
    for _b in obj["fact_bindings"]:
        _fu = str(_b.get("fact_uid") or "")
        if _fu not in set(_frozen_uids):
            _p.append(_rec("view_binding_unknown_fact", "fact", _fu,
                           "`fact_bindings` 指向不存在的冻结事实：%r" % _fu))
            continue
        if _fu in _seen_fact:
            _p.append(_rec("view_binding_duplicate", "fact", _fu,
                           "事实 %s 有多项绑定——每条事实恰好一项" % _fu))
            continue
        _seen_fact.append(_fu)
        _cars = ref_items(_b.get("carriers"), "fact_bindings[%s].carriers" % _fu, _p)
        if not _cars:
            _p.append(_rec("view_binding_empty", "fact", _fu,
                           "事实 %s 没有任何载体（重复登记不能代替表达，空也不等于不改）" % _fu))
        for _c in _cars:
            _code, _m = _bad_ref(_c, "事实 %s" % _fu)
            if _m:
                _p.append(_rec(_code or "view_binding_dangling", "fact", _fu, _m))
    _miss = sorted(set(_frozen_uids) - set(_seen_fact))
    if _miss:
        _p.append(_rec("view_binding_missing", "view", "",
                       "这些冻结事实没有恰好一项绑定：%s" % _miss, "fact_bindings"))

    _hb = obj["hook_bindings"] or {}
    for _u in ref_items(_hb.get("spoken_uids"), "hook_bindings.spoken_uids"):
        if str(_u) not in _d_uids:
            _p.append(_rec("view_hook_spoken_dangling", "hook", str(_u),
                           "`hook_bindings.spoken_uids` 引用了不存在的对白单位：%r" % _u))
    _cra = _hb.get("carriers")
    if _cra is None and _hb.get("carrier") is not None:
        _cra = [_hb["carrier"]]
    _cra = ref_items(_cra, "hook_bindings.carriers", _p)
    for _c in _cra:
        _code, _m = _bad_ref(_c, "钩子承载")
        if _m:
            _p.append(_rec(_code or "view_hook_carrier_dangling", "hook", "", _m))

    for _u in ref_items(obj.get("evidence_updates"), "evidence_updates"):
        if not isinstance(_u, dict):
            _p.append(_rec("view_evidence_item_type", "view", "",
                           "`evidence_updates` 的每一项都必须是对象（当前是 %s）"
                           % type(_u).__name__, "evidence_updates"))
            continue
        _fu = str((_u or {}).get("fact_uid") or "")
        if _fu not in set(_frozen_uids):
            _p.append(_rec("view_evidence_update_unknown_fact", "fact", _fu,
                           "`evidence_updates` 指向不存在的冻结事实：%r" % _fu))
        if not str((_u or {}).get("evidence") or "").strip():
            _p.append(_rec("view_evidence_update_empty", "fact", _fu,
                           "`evidence_updates` 的 evidence 为空——更正来源片段必须给出新片段"))

    return (obj if not _p else None), _p


# ---------------------------------------------------------------------------
# 投影：视图 → 既有计划形状（**派生字段全部由这里生成**）
# ---------------------------------------------------------------------------
def _obj_members(value, key, problems, *, member_code: str = "view_item_type") -> list:
    """容器 + 成员 → **`(真实下标, 对象)` 表**；容器/成员违约都记进 `problems`.

    ⚠️ **检查器自己必须是全函数**（复核 2026-10-06 二轮 P1）：上一版是
    `[x for x in (view.get("frozen_facts") or []) if isinstance(x, dict)]`——
    `frozen_facts=1` 时 `(1 or [])` 还是 `1`，`for x in 1` 直接抛 `TypeError`。
    结果就是复核说的那句：「**准入先记录类型错误，接着字段检查器仍然遍历它**」——
    结构化拒收根本发不出来，准入自己崩了。

    这里：容器不对 ⇒ 记一条并**返回空表**；成员不是对象 ⇒ 记一条并**跳过它**。
    调用方拿到的永远是"可以安全继续检查的那些对象"，**问题一条不少**。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(_rec("view_field_type", "view", "",
                             "`%s` 必须是数组（现在是 %s）"
                             % (key, _entity_type_label(value)), key))
        return []
    _out: list = []
    for _i, _x in enumerate(value):
        if not isinstance(_x, dict):
            problems.append(_rec(member_code, "view", "",
                                 "`%s[%d]` 必须是对象（现在是 %s）"
                                 % (key, _i, _entity_type_label(_x)),
                                 "%s[%d]" % (key, _i)))
            continue
        _out.append((_i, _x))
    return _out


def view_field_contract_problems(view) -> list:
    """**形状契约·字段层**：投影 / 核验 / 卡片实际会读的那些**非实体数组**字段.

    复核第十二轮（补查）P1：「准入检查只覆盖部分容器和引用，没有覆盖转换、投影及消费
    实际读取的完整字段契约」；二轮 P1 又指出检查器自己会崩。所以这一份的规矩是：

    - **先容器、再成员、最后字段**，每一层失败都只影响它自己那一层；
    - **任何输入都返回记录，不抛异常**（`_obj_members` 保证）。

    覆盖：冻结事实（容器/成员/`uid`·`claim`·`evidence` 类型）、冻结事件、
    `evidence_updates`、`plan_basis`、`hook_bindings` 与 `spoken_uids`。
    **`parse_view` 与消费准入共用这一份**——两处各写一遍必然漂移（本轮就是漂出来的）。
    """
    if not isinstance(view, dict):
        return [_rec("view_not_object", "view", "", "视图不是 JSON 对象")]
    _p: list = []
    for _i, _f in _obj_members(view.get("frozen_facts"), "frozen_facts", _p):
        for _fld in ("uid", "claim", "evidence"):
            if _fld in _f and not isinstance(_f.get(_fld), str):
                _p.append(_rec("view_field_type", "fact", "",
                               "`frozen_facts[%d].%s` 必须是字符串（现在是 %s）"
                               % (_i, _fld, _entity_type_label(_f.get(_fld))),
                               "frozen_facts[%d].%s" % (_i, _fld)))
    _fe = view.get("frozen_events")
    if _fe is not None:
        if not isinstance(_fe, list):
            _p.append(_rec("view_field_type", "view", "",
                           "`frozen_events` 必须是数组（现在是 %s）"
                           % _entity_type_label(_fe), "frozen_events"))
        else:
            for _i, _e in enumerate(_fe):
                if not isinstance(_e, str):
                    _p.append(_rec("view_event_item_type", "view", "",
                                   "`frozen_events[%d]` 必须是事件名字符串（现在是 %s）"
                                   % (_i, _entity_type_label(_e)),
                                   "frozen_events[%d]" % _i))
    # 出处修正的成员违约与**读盘**用同一个码（`view_evidence_item_type`）：
    # 同一条判据的两个入口报不同的名字，排查时要多认一套。
    for _i, _x in _obj_members(view.get("evidence_updates"), "evidence_updates", _p,
                               member_code="view_evidence_item_type"):
        if "evidence" in _x and not isinstance(_x.get("evidence"), str):
            _p.append(_rec("view_field_type", "fact", "",
                           "`evidence_updates[%d].evidence` 必须是字符串（现在是 %s）"
                           % (_i, _entity_type_label(_x.get("evidence"))),
                           "evidence_updates[%d].evidence" % _i))
        if "fact_uid" in _x and not isinstance(_x.get("fact_uid"), str):
            _p.append(_rec("view_field_type", "fact", "",
                           "`evidence_updates[%d].fact_uid` 必须是字符串（现在是 %s）"
                           % (_i, _entity_type_label(_x.get("fact_uid"))),
                           "evidence_updates[%d].fact_uid" % _i))
    _pb = view.get("plan_basis")
    if _pb is not None and not isinstance(_pb, dict):
        _p.append(_rec("view_field_type", "view", "",
                       "`plan_basis` 必须是对象（现在是 %s）——投影要把它原样写回计划"
                       % _entity_type_label(_pb), "plan_basis"))
    _hb = view.get("hook_bindings")
    if _hb is not None and not isinstance(_hb, dict):
        _p.append(_rec("view_field_type", "view", "",
                       "`hook_bindings` 必须是对象（现在是 %s）"
                       % _entity_type_label(_hb), "hook_bindings"))
    elif isinstance(_hb, dict):
        _sp = _hb.get("spoken_uids")
        if _sp is not None:
            if not isinstance(_sp, list):
                _p.append(_rec("view_field_type", "hook", "",
                               "`hook_bindings.spoken_uids` 必须是数组（现在是 %s）"
                               % _entity_type_label(_sp),
                               "hook_bindings.spoken_uids"))
            else:
                for _i, _x in enumerate(_sp):
                    if not isinstance(_x, str):
                        _p.append(_rec("view_hook_spoken_item_type", "hook", "",
                                       "`hook_bindings.spoken_uids[%d]` 必须是 id 字符串"
                                       "（现在是 %s）" % (_i, _entity_type_label(_x)),
                                       "hook_bindings.spoken_uids"))
    if isinstance(_pb, dict):
        from step01_script_gen.plan_coherence import admission_problems
        _p.extend(admission_problems(view))
        from step01_script_gen.source_revision import admission_problems as source_admission
        _p.extend(source_admission(view))
    return _p


def view_admission_problems(view) -> list:
    """**消费准入**：投影 / 核验请求 / 卡片消费之前跑的那**一次**（第二层之前的门）.

    复核 2026-10-06 第十二轮 ①：「建立完整的消费准入，不是逐函数加 `isinstance` 后跳过
    坏项。复用现有实体、引用判据，在投影、核验请求和卡片消费的边界检查一次；内部转换
    只消费已检查对象。」

    它只核**消费侧真的要索引的东西**——实体数组是不是数组、成员是不是对象、引用容器与
    成员合法、身份写法不冲突。**关系问题**（悬空 / 缺绑定 / 事实没展示）**不在这里**：
    那些是第二层，由 `parse_view` / `view_problems` / `content_verification` 判。

    与 `parse_view` 的关系：这份准入是它的**子集**（少了关系闭合与模式合同），刻意如此
    ——投影不需要"引用闭合"就能把关系搬到计划上，而核验请求与卡片消费也不需要。两条
    入口共用同一批底层判据（`ref_list_problems` / `ref_item_problems` /
    `entity_contract_problems`），不各写一份。

    ⚠️ **覆盖范围 = 消费侧真的会读的每一个字段**（复核第十二轮补查 P1）。旧写法只判了
    容器与引用，于是「视图 `evidence_updates=1` / `[1]`、`plan_basis=1`」这类输入
    **准入通过、投影随后抛异常`；卡片身份别名冲突、声音单位 `text=123` 也全放行。
    判据来自 `ENTITY_CONTRACT`（实体合同）与下面几条字段契约，**不再各自手写**。
    """
    _p: list = []
    if not isinstance(view, dict):
        return [_rec("view_not_object", "view", "", "视图不是 JSON 对象")]
    # ① **容器 + 成员 + 实体合同**：`_obj_members` 保证检查器自己是全函数
    # （容器不对 ⇒ 空表；成员不是对象 ⇒ 跳过它，问题照记）。
    # `frozen_facts` 的容器/成员由下面的**字段层**负责（同一份判据不重复报两遍）
    for _k in ("beats", "dialogue_units", "narration_units",
               "screen_cards", "fact_bindings"):
        _items = _obj_members(view.get(_k), _k, _p)
        if _k not in ENTITY_CONTRACT:
            continue
        for _i, _e in _items:
            # **实体合同**：身份别名冲突 + 字段类型（`text` 必须是字符串等）——
            # 与 `parse_view` 共用同一份判据。消费侧按 `text` 做文本比对、按 `card_id`
            # 找绑定，一条 `text=123` 的"声音单位"到这里必须被拦下（否则它会一路走到
            # 核验请求，**真的发起一次模型调用**）。
            # `require_all=False`：消费侧按 `.get(f) or 默认` 读，**缺席不算违约**
            # （手写视图省略可选字段是常态）；写错类型才是违约。
            _p.extend(entity_contract_problems(_e, _k, "%s[%d]" % (_k, _i),
                                               require_all=False))
    # ② **字段层**（冻结事实/事件、出处修正、plan_basis、hook_bindings、spoken_uids）：
    # 与 `parse_view` 共用同一份，**不再各写一遍**（本轮就是两处漂移出来的）。
    _p.extend(view_field_contract_problems(view))
    _hb = view.get("hook_bindings")
    _hb = _hb if isinstance(_hb, dict) else {}
    # ③ **引用**：容器 + 成员，与读盘 / 两条应用入口共用同一份判据
    for _i, _b in _obj_members(view.get("fact_bindings"), "fact_bindings", []):
        ref_list_problems(_b.get("carriers"), "fact_bindings[%d].carriers" % _i, _p,
                          container_code="view_binding_carriers_type")
    ref_list_problems(_hb.get("carriers"), "hook_bindings.carriers", _p,
                      container_code="view_hook_carriers_type")
    for _bi, _b in _obj_members(view.get("beats"), "beats", []):
        _vr = _b.get("voice_ref")
        if _vr is not None:
            _p.extend(ref_item_problems(_vr, "beats[%d].voice_ref" % _bi))
    return _p


def plan_from_view(view: dict) -> dict:
    """把视图**投影**成既有计划形状——编号、`载体：`、`fact_refs` 全由代码派生.

    这是**关系层的局部投影**：`shot_beats` / `dialogue_lines` / `narration_lines` /
    `required_facts` / `hook_spoken` / `hook_carrier` 由视图关系重新生成；
    **其余计划字段（`climax` / `causal_chain` / `time_budget` …）由 `plan_basis` 原样写回**，
    一个字节都不动。少了这一步，"往返保真"就只剩六个关系字段，冻结字段会在修订链里丢掉。

    ⚠️ **投影前先过消费准入**（复核第十二轮 ①：`dict(1)` / `(_c or {}).get(…)` 这类
    "假定成员是对象"的写法在这里会 `TypeError` / `AttributeError`，而"过滤掉坏项继续投影"
    就是**静默丢关系**）。准入不过 ⇒ 抛 `PresentationContractError`（带 `problems`），
    **不交付部分投影**；调用方按结构化拒收处理（`complete_candidate` 就是这么接的）。
    """
    _adm = view_admission_problems(view)
    if _adm:
        raise PresentationContractError(_adm, where="投影 plan_from_view")
    _frozen = {str(f.get("uid")): f for f in (view.get("frozen_facts") or [])}
    for _u in (view.get("evidence_updates") or []):        # 只更正**来源片段**
        _f = _frozen.get(str(_u.get("fact_uid")))
        if _f is not None:
            _frozen[str(_u.get("fact_uid"))] = dict(_f, evidence=str(_u.get("evidence")))

    _d = list(view.get("dialogue_units") or [])
    _n = list(view.get("narration_units") or [])
    _cards = list(view.get("screen_cards") or [])
    _beats = list(view.get("beats") or [])
    _didx = {ref_uid(u): i for i, u in enumerate(_d, 1)}
    _nidx = {ref_uid(u): i for i, u in enumerate(_n, 1)}
    _bidx = {ref_uid(b): i for i, b in enumerate(_beats, 1)}
    _cidx = {ref_uid(c): i for i, c in enumerate(_cards, 1)}

    def _carrier_text(ref) -> str:
        if not ref:
            return ""
        _k = str(ref.get("kind"))
        if _k == KIND_INVALID:
            # 解析不出的标注**没有可渲染的载体文案**；这种计划在验收链上已被
            # `view_problems` 拒收（`view_*_dangling`），投影只是不再凭空写成「卡片0」。
            return ""
        _u = ref_uid(ref)
        if _k == KIND_VISUAL:
            return "画面"
        if _k == KIND_DIALOGUE:
            return "台词%d" % _didx.get(_u, 0)
        if _k == KIND_NARRATION:
            return "旁白%d" % _nidx.get(_u, 0)
        return "卡片%d" % _cidx.get(_u, 0)

    _beats_txt = []
    for _b in _beats:
        _segs = [SEG_BODY + str(_b.get("text") or "")]
        if str(_b.get("purpose") or ""):
            _segs.append(SEG_PURPOSE + str(_b["purpose"]))
        if str(_b.get("evidence") or ""):
            _segs.append(SEG_EVIDENCE + str(_b["evidence"]))   # 逐字投影，不改写引号
        _vr = _b.get("voice_ref")
        # `载体：` 段是**派生**的（由 `voice_ref` 生成，不是搬运）：画面/台词k/旁白k 都要
        # 写出来，否则旧计划的 `｜载体：画面` 会在往返里丢掉。**没标载体**（`None`）与
        # **解析不出**（`invalid`）都不生成——前者是"未标注"、后者是错关系，两种都不该
        # 被投影成一条看起来正常的标注。
        if isinstance(_vr, dict) and str(_vr.get("kind")) in (
                KIND_VISUAL, KIND_DIALOGUE, KIND_NARRATION):
            _ct = _carrier_text(_vr)
            if _ct:
                _segs.append(SEG_CARRIER + _ct)
        _beats_txt.append("｜".join(_segs))

    # 旁白 `fact_refs`：由绑定**反查**（模型不再填）
    _nrefs = {ref_uid(_u): [] for _u in _n}
    _bind_by_fact = {str(b.get("fact_uid")): b for b in (view.get("fact_bindings") or [])}
    # **按冻结事实的顺序**生成 `required_facts`，不按绑定表的顺序——绑定表的排列不携带含义
    _fact_order = [str(f.get("uid")) for f in (view.get("frozen_facts") or [])]
    _facts_txt = []
    for _fi, _fu in enumerate(_fact_order, 1):
        _f = _frozen.get(_fu) or {}
        _b = _bind_by_fact.get(_fu) or {}
        _car = list(ref_items(_b.get("carriers"), "fact_bindings.carriers"))
        _first_vis = next((c for c in _car if c.get("kind") == KIND_VISUAL), None)
        _voice = next((c for c in _car if c.get("kind") in (KIND_DIALOGUE, KIND_NARRATION)),
                      None)
        if _voice and _voice.get("kind") == KIND_NARRATION:
            _nrefs.setdefault(ref_uid(_voice), []).append(_fi)
        _decl = ""
        if _first_vis is not None:
            _decl = "B%d/画面" % _bidx.get(ref_uid(_first_vis), 0)
        elif _voice is not None:
            _decl = "B%d/%s" % (_bidx.get(_voice_beat_uid(view, _voice), 0),
                                _carrier_text(_voice))
        _txt = "%s｜原文证据：“%s”" % (str(_f.get("claim") or ""), str(_f.get("evidence") or ""))
        if _decl:
            _txt += "｜承载：" + _decl
        _facts_txt.append(_txt)

    _hb = view.get("hook_bindings") or {}
    _hc = _hb.get("carriers")
    if _hc is None and _hb.get("carrier") is not None:
        _hc = [_hb["carrier"]]
    _hook_carrier = "；".join(
        "B%d/%s" % (_bidx.get(_voice_beat_uid(view, _c), 0), _carrier_text(_c))
        for _c in (_hc or []) if isinstance(_c, dict))

    _mode = str(view.get("mode") or MODE_LEGACY)
    _out = dict(view.get("plan_basis") or {})          # **其余计划字段原样写回**
    _out.update({
        "main_events": list(view.get("frozen_events") or []),
        "required_facts": _facts_txt,
        "shot_beats": _beats_txt,
        "dialogue_lines": [str(u.get("text") or "") for u in _d],
        "narration_lines": [{"text": str(u.get("text") or ""),
                             "source": _span_text(u.get("source")),
                             "fact_refs": sorted(_nrefs.get(ref_uid(u)) or [])}
                            for u in _n],
        "hook_spoken": [str(u.get("text") or "") for u in _d
                        if ref_uid(u) in (_hb.get("spoken_uids") or [])],
        "hook_carrier": _hook_carrier,
        "plan_contract_version": PLAN_CONTRACT_VERSION_BY_MODE.get(
            _mode, PLAN_CONTRACT_VERSION_LEGACY),
    })
    if _mode == MODE_HYBRID or _cards:
        # 新模式的**结构化关系是唯一承载事实源**。`｜承载：…` 文案仍投影出来（供人读、
        # 供旧消费者显示），但**不另作权威判据**——消费者读这一块。
        _out["presentation"] = {
            "schema": PRESENTATION_SCHEMA,
            "mode": _mode,
            # 稳定身份随块保存（投影读回时**不重新编号**）
            "fact_uids": _fact_order,
            "beat_uids": [ref_uid(b) for b in _beats],
            "dialogue_uids": [ref_uid(u) for u in _d],
            "narration_uids": [ref_uid(u) for u in _n],
            "beat_events": [list(b.get("event_uids") or []) for b in _beats],
            "screen_cards": [dict(c) for c in _cards],
            "fact_bindings": [
                {"fact_uid": _fu,
                 "carriers": [_ref_out(c) for c in ref_items(
                     (_bind_by_fact.get(_fu) or {}).get("carriers"),
                     "fact_bindings.carriers")]}
                for _fu in _fact_order],
            "hook_bindings": {
                "spoken_uids": [str(x) for x in (_hb.get("spoken_uids") or [])],
                "carriers": [_ref_out(c) for c in
                             ref_items(_hc, "hook_bindings.carriers")
                             if isinstance(c, dict)]},
        }
        _out["narration_lines"] = [
            {"text": str(u.get("text") or ""), "source": _span_text(u.get("source")),
             "fact_refs": sorted(_nrefs.get(ref_uid(u)) or [])} for u in _n]
    return _out


def view_digest(view: dict) -> str:
    """视图的**内容摘要**——候选状态与循环判定的身份.

    ⚠️ **包含 `evidence_updates`**（复核 2026-10-04 P1）：只补出处也是**有效的内容变化**，
    把它排除在摘要之外会让应用器把"补齐了出处"判成 `no_change`，候选根本拿不到，合法的
    局部修正被丢掉。真正冻结的**事实基础**另有 `fact_basis_digest`（主张 + 事件顺序）——
    那个才是不许动的部分。
    """
    return _digest(view or {})


def fact_basis_digest(view: dict) -> str:
    """**事实基础**摘要：**正文主张 + 事件顺序**——只含真正冻结的那部分.

    ⚠️ **不含证据**：来源片段可以经 `evidence_updates` 正当更正（R0 要求"交回事实生产者
    补齐出处"），把它并进基础摘要会让"补齐出处"和"篡改主张"分不出来。证据自己另有
    `evidence_digest`。
    """
    return _digest({"frozen_claims": [{"uid": str(f.get("uid")),
                                       "claim": str(f.get("claim"))}
                                      for f in ((view or {}).get("frozen_facts") or [])],
                    "frozen_events": (view or {}).get("frozen_events") or []})


def evidence_digest(view: dict) -> str:
    """**来源片段**摘要（含 `evidence_updates` 的更正）——与基础摘要分账."""
    _facts = {str(f.get("uid")): dict(f) for f in ((view or {}).get("frozen_facts") or [])}
    for _u in ((view or {}).get("evidence_updates") or []):
        _f = _facts.get(str(_u.get("fact_uid")))
        if _f is not None:
            _f["evidence"] = str(_u.get("evidence"))
    return _digest([{"uid": _k, "evidence": str(_facts[_k].get("evidence"))}
                    for _k in sorted(_facts)])


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _index_of_fact(view, fact_uid) -> int:
    for _i, _f in enumerate(view.get("frozen_facts") or [], 1):
        if str(_f.get("uid")) == str(fact_uid):
            return _i
    return 0


def _voice_beat_uid(view, ref) -> str:
    """声音条目所在的节拍 uid（`voice_ref` 指向它的那条节拍）."""
    _uid = ref_uid(ref)
    for _b in (view.get("beats") or []):
        _vr = _b.get("voice_ref") or {}
        if ref_uid(_vr) == _uid:
            return str(_b.get("uid") or "")
    return ""


def _split_claim_evidence(fact) -> tuple:
    from shared.fact_metadata import split_claim_evidence
    return split_claim_evidence(fact)


def _carrier_decl(fact) -> str:
    for _p in str(fact or "").split("｜"):
        if _p.strip().startswith("承载："):
            return _p.strip()[len("承载："):].strip()
    return ""


def _beat_segments(text) -> dict:
    _out: dict = {}
    for _seg in str(text or "").split("｜"):
        _s = _seg.lstrip()
        for _tag in _SEG_TAGS:
            if _s.startswith(_tag):
                _out[_tag] = _s[len(_tag):].strip()
                break
    return _out


_QUOTE_RE = re.compile("[“”\"‘’]")


def _unquote(text) -> str:
    """去掉引文两端的中/英引号（`原文依据：“…”` 与视图里的裸正文互转）."""
    return _QUOTE_RE.sub("", str(text or "")).strip()


def _src_span(src) -> dict:
    _t = str(src or "").strip()
    return {"text": _t} if _t else {}


def _span_text(span) -> str:
    return str((span or {}).get("text") or "")


#: 承载声明的**词表**（**单一事实源**）：解析（事实级 `｜承载：` 与节拍级 `载体：`）与
#: 投影（`_carrier_label`）共用同一份。
#:
#: 为什么要有这一张表（2026-10-06 实跑）：hybrid 提示块把卡片叫「**文字卡**」，视图投影
#: 写「**卡片k**」，而两个解析器一个只认 `卡片k`、另一个连卡片分支都没有——首版照着提示写
#: `｜承载：B1/文字卡1`，四条事实因此**零载体**，核验只好报"没有任何载体"。
#: 词表不统一过一次，提示、解析、投影就会各说各话。
CARRIER_WORDS = {
    "画面": KIND_VISUAL,
    "卡片": KIND_CARD,
    "文字卡": KIND_CARD,
    "台词": KIND_DIALOGUE,
    "旁白": KIND_NARRATION,
}


def carrier_words_text() -> str:
    """词表串（**从同一份表生成**，提示与报错都不手写第二遍）."""
    return "、".join(CARRIER_WORDS)


def carrier_ref_from_decl(decl, *, beats=(), dialogue=(), narration=(), cards=(),
                       self_beat_uid: str = "") -> dict:
    """承载声明 → **结构化解析结果**：`{}` = 没声明；`kind=invalid` = 声明了但解析不出.

    语法：`B<节拍号>/<载体词>[<序号>]`，或裸的 `<载体词>[<序号>]`。
    索引一律是**全局 1 起序号**（`dialogue_units` / `narration_units` / `screen_cards` 的
    数组位置），与投影方向 `_carrier_label` 写出来的 `台词k` / `卡片k` **同一套编号**。

    `self_beat_uid`：节拍级的裸 `画面` 指的是**这条节拍自己**（节拍上的 `载体：画面`），
    与事实级的 `Bn/画面`（指第 n 条节拍）是两种写法，用这个参数区分。

    **不猜、不截、不补**：词不认、序号越界、卡片不在声明的节拍上，一律返回
    `{"kind": KIND_INVALID, "uid": "", "raw": <原串>, "reason": <为什么>}`——
    调用方据此**保留原貌**（第三种状态）并在验收链上报出来，而不是清成"没有载体"。
    """
    _raw = str(decl or "").strip()
    if not _raw:
        return {}
    _body, _bn = _raw, None
    _m = re.match(r"^B(\d+)/(.+)$", _raw)
    if _m:
        _bn, _body = int(_m.group(1)), _m.group(2).strip()

    def _bad(_why):
        return {"kind": KIND_INVALID, "uid": "", "raw": _raw, "reason": _why}

    _word = next((w for w in CARRIER_WORDS if _body == w or _body.startswith(w)), "")
    if not _word:
        return _bad("认不出的载体词（合法：%s）" % carrier_words_text())
    _kind = CARRIER_WORDS[_word]
    _rest = _body[len(_word):].strip()
    if _bn is not None and not 1 <= _bn <= len(beats):
        return _bad("节拍号 B%d 越界（共 %d 条节拍）" % (_bn, len(beats)))
    if _kind == KIND_VISUAL:
        if _rest:
            return _bad("`画面` 不带序号")
        if _bn is not None:
            return {"kind": KIND_VISUAL, "uid": str(beats[_bn - 1]["uid"])}
        if self_beat_uid:
            return {"kind": KIND_VISUAL, "uid": str(self_beat_uid)}
        return _bad("`画面` 要写节拍号（`Bn/画面`）")
    if not _rest.isdigit():
        return _bad("`%s` 后面要跟序号（如 `%s1`）" % (_word, _word))
    _i = int(_rest)
    _pool = {KIND_CARD: list(cards), KIND_DIALOGUE: list(dialogue),
             KIND_NARRATION: list(narration)}[_kind]
    if not 1 <= _i <= len(_pool):
        return _bad("%s 序号 %d 越界（共 %d 条）" % (_word, _i, len(_pool)))
    _item = _pool[_i - 1]
    if _kind == KIND_CARD and _bn is not None:
        _on = str((_item or {}).get("beat_uid") or "")
        _want = str(beats[_bn - 1]["uid"])
        if _on and _on != _want:
            return _bad("卡片 %d 挂在 `%s` 上，声明里写的却是 `B%d`（`%s`）"
                        % (_i, _on, _bn, _want))
    return {"kind": _kind, "uid": ref_uid(_item)}


def _voice_ref_of(carrier_text, units_d, units_n, cards=None, beat_uid=""):
    """节拍上的 `载体：` 文案 → 视图引用（**与事实级共用同一份词表与解析器**）.

    `画面` 归到**节拍自己**（`uid = beat_uid`）：一条标着「载体：画面」的节拍，它的画面
    载体就是它本身。旧写法给空 uid，于是"悬空引用"检查永远发现不了真正悬空的那种，
    而这条合法形态又会被判成悬空。

    三种状态**分得开**（`None` / 合法引用 / `KIND_INVALID`）：
    **没写**载体标注 → `None`（那是"未标注"，由 `carrier_relation_records` 报）；
    **写了但解析不出**（越界编号、区间写法、别字、词表外的写法）→ `KIND_INVALID`
    （那是**错关系**，必须报出来，不能长得像"没写"）。
    """
    _r = carrier_ref_from_decl(carrier_text, dialogue=units_d, narration=units_n,
                            cards=list(cards or []), self_beat_uid=str(beat_uid or ""))
    return _r or None


def _ref_from_decl(decl, beats_v, units_d, units_n, cards=()) -> list:
    """`Bn/<载体词>[k]` → 引用列表（0 或 1 条）.

    **解析不出 ⇒ 一条 `kind=invalid`**（带 `raw` 与 `reason`）——不再返回空表。
    返回空表等于说"这条事实没有承载声明"，而真相是"写了、但读不懂"：
    两者在验收链上是不同的记录（`view_binding_empty` vs `view_binding_dangling`）。
    """
    _r = carrier_ref_from_decl(decl, beats=list(beats_v), dialogue=list(units_d),
                            narration=list(units_n), cards=list(cards))
    return [_r] if _r else []


def _carrier_label(ref, view) -> str:
    """视图引用 → **显示文案**（`画面` / `台词k` / `旁白k` / `卡片k`）——只给人看.

    写出来的词必须是 `CARRIER_WORDS` 里认得的：投影与解析**共用同一份词表**，
    改词表就要两边一起改（`test_the_carrier_vocabulary_is_one_source` 钉住）。
    """
    if not ref:
        return ""
    _k = str(ref.get("kind"))
    _u = ref_uid(ref)
    if _k == KIND_VISUAL:
        return "画面"
    _pool = {KIND_DIALOGUE: (view.get("dialogue_units") or [], "台词"),
             KIND_NARRATION: (view.get("narration_units") or [], "旁白"),
             KIND_CARD: (view.get("screen_cards") or [], "卡片")}.get(_k)
    if _pool is None:
        return ""
    for _i, _x in enumerate(_pool[0], 1):
        if ref_uid(_x) == _u:
            return "%s%d" % (_pool[1], _i)
    return ""


def view_problems(view: dict, *, base_view: dict | None = None) -> list:
    """视图的**关系**判据（不含形状——形状由 `parse_view` 管）.

    - **只有声明了事件归属**（`event_binding == "declared"`）才核事件覆盖与顺序；
      `"unknown"`（旧形状适配器）下事件归属**未确定**，既不核也不伪造覆盖率——
      按位置一对一编一份出来会让"仓库外景被登记成周明辞职"这种错关系通过检查。
    - 与 `base_view` 比较时：`evidence_updates` 只能改来源片段（正文主张由摘要保证）。
    """
    _p: list = []
    if _semantic_legacy(view.get("plan_basis")):
        for index, beat in enumerate(view.get("beats") or []):
            if isinstance(beat, dict):
                _p.extend(_beat_body_problems(beat.get("text"), ref_uid(beat),
                                             "beats[%d].text" % index))
    if str(view.get("event_binding") or "") == "declared":
        _events = [str(e) for e in (view.get("frozen_events") or [])]
        _pos: list = []
        for _b in (view.get("beats") or []):
            for _e in (_b.get("event_uids") or []):
                if str(_e) not in _events:
                    _p.append(_rec("view_event_unknown", "beat", str(_b.get("uid")),
                                   "节拍引用了不存在的事件：%r" % _e))
                else:
                    _pos.append((_events.index(str(_e)), str(_b.get("uid"))))
        _covered = {_e for _b in (view.get("beats") or [])
                    for _e in (_b.get("event_uids") or [])}
        _miss = [_e for _e in _events if _e not in _covered]
        if _miss:
            _p.append(_rec("view_event_uncovered", "view", "",
                           "这些冻结事件没有任何节拍承接：%s" % _miss, "beats"))
        _order = [_x[0] for _x in _pos]
        if _order != sorted(_order):
            _back = [(left, right) for left, right in zip(_pos, _pos[1:])
                     if right[0] < left[0]]
            _detail = "；".join("%s(事件%d) → %s(事件%d)" %
                              (left[1], left[0] + 1, right[1], right[0] + 1)
                              for left, right in _back)
            _p.append(_rec("view_event_order_reversed", "beat", _back[0][1][1],
                           "事件顺序在节拍里发生了回退：%s。冻结事件顺序不得改；"
                           "把新增/改挂节拍放回对应事件的位置，不能一律追加到结尾。" % _detail,
                           "beats", fix="用 order.beats 给出最终完整顺序，同时核对 event_uids；"
                           "不改变冻结事件，不由代码猜测或自动排序"))

    if base_view is not None:
        if fact_basis_digest(view) != fact_basis_digest(base_view):
            _p.append(_rec("view_fact_basis_changed", "view", "",
                           "事实基础摘要与基线不符——正文主张与事件顺序冻结，"
                           "只允许经 `evidence_updates` 更正**来源片段**", "frozen_facts"))
        _b = str(base_view.get("event_binding") or "unknown")
        if _b == "declared" and str(view.get("event_binding") or "") != "declared":
            _p.append(_rec("view_event_binding_downgraded", "view", "",
                           "基线声明了事件归属，候选把它退成「未确定」——不能靠"
                           "不声明来绕过事件覆盖检查", "event_binding"))

    # ---- 旧模式的**表达上限**：v5 计划只有一个「承载：Bn/Xk」位 ----
    # 实测（2026-10-06）：写 2 条载体时应用器返回 `applied`，**投影回计划再读回只剩
    # 第一条** —— 画面+旁白、旁白+旁白、画面+画面**全部**丢第二条。静默丢数据比拒收更
    # 糟（R2「不允许静默丢失」），所以在这里**明说拒收**；多载体是 `screen_hybrid`
    # （契约版本 6）的能力，不是旧模式的。
    #
    # 2026-10-18（A 窄版）：上述限制针对**一条事实的多个载体**（`max_carriers`），继续保留。
    # 但"**带声音的节拍**同时作为某条事实的画面承载"不是多载体：声音记在节拍载体标签、
    # 事实承载记在事实 `承载` 字段，往返不丢 ⇒ 不再拒绝该情形
    # （原先的 `view_binding_legacy_visual_mismatch` 已按用户裁决移除）。
    _carrier_limit = fact_binding_policy(view)['max_carriers']
    if _carrier_limit is not None:
        for _b in (view.get("fact_bindings") or []):
            if not isinstance(_b, dict):
                continue
            _n = len([_c for _c in ref_items(_b.get("carriers"),
                                             "fact_bindings.carriers") if _c])
            if _n > _carrier_limit:
                _fu = str(_b.get("fact_uid") or "")
                _p.append(_rec(
                    "view_binding_multi_carrier_legacy", "fact", _fu,
                    "旧模式（%s）表示不了一条事实的 %d 个载体：v5 计划只有一个"
                    "「承载：Bn/Xk」位，投影回计划时**第二条会被丢掉**"
                    "（实测：写入 %d 条、回读 1 条）。多载体要用 `screen_hybrid`"
                    "（契约版本 6）" % (MODE_LEGACY, _n, _n),
                    "fact_bindings[%s].carriers" % _fu))
        # hook 的画面载体同样不再要求"纯画面节拍"（与 fact 同一契约：声画可共镜）。
        # 其余 hook 约束（身份、数量、悬空引用）不变。
        _placed={(str(ref.get('kind') or ''),ref_uid(ref))
                 for beat in view.get('beats',[]) for ref in [beat.get('voice_ref')]
                 if isinstance(ref,dict)}
        if _placed:  # Preserve the historical all-unlabelled legacy adapter.
            for _kind,_collection in ((KIND_DIALOGUE,'dialogue_units'),(KIND_NARRATION,'narration_units')):
                for _unit in view.get(_collection,[]):
                    if (_kind,ref_uid(_unit)) not in _placed:
                        _p.append(_rec('view_voice_unit_unplaced',_kind,ref_uid(_unit),
                            '旧模式选中的声音条目必须有对应发声节拍，不能投影成 B0 或静默丢失',
                            '%s[%s]' % (_collection,ref_uid(_unit))))
    return _p

# ---------------------------------------------------------------------------
# R1 尾段：`plan_revision.v2` 请求 / 响应装配
# ---------------------------------------------------------------------------
def revision_request(base_view: dict, *, problems=None, rejections=None,
                     rule_version: str = "", remaining_calls=None,
                     remaining_sec=None, limits=None) -> dict:
    """构造 `plan_revision.v2` 请求（**完整可编辑视图替换**，R1 §「请求必备」）.

    与旧协议的差别不在字段多寡，而在**权威关系只有一份**：请求带的是**可编辑视图**，
    模型改视图、代码派生编号。旧协议让模型"回操作、并顺手重报修改后的编号"，实测
    （R §2.1）首两轮拒收正是这么来的。

    这里**只装配**，不判合法性、不调模型、不碰额度——预算值由调用方读给出，便于离线
    矩阵逐格构造。`problems` / `rejections` **原样全量**进请求（R3：回灌模型读完整原因，
    有界展示只发生在控制台）。
    """
    return {
        "schema": REVISION_SCHEMA,
        "rule_version": str(rule_version or ""),
        "mode": str(base_view.get("mode") or MODE_LEGACY),
        # 两个基线摘要 + 证据摘要：模型必须原样回带，代码据此确认"改的是同一份基线"
        "base_plan_hash": view_digest(base_view),
        "fact_basis_hash": fact_basis_digest(base_view),
        "evidence_hash": evidence_digest(base_view),
        "frozen_facts": [dict(f) for f in (base_view.get("frozen_facts") or [])],
        "frozen_events": list(base_view.get("frozen_events") or []),
        "view": base_view,
        "problems": list(problems or []),
        "rejections": list(rejections or []),
        "budget": {"remaining_calls": remaining_calls, "remaining_sec": remaining_sec},
        "limits": dict(limits or {}),
    }


def _response_problems(obj, *, base_view=None, expected_rule_version: str = "") -> tuple:
    """响应**信封**的形状、版本与基线核验（不看 `view` 内部——那是 `parse_view` 的事）.

    复核 2026-10-06（第十一轮）P2：完整视图入口**从来不核 `rule_version`**——缺版本或写
    `unknown.v999` 都 `applied`。增量入口有这道检查（`delta_response_problems`），两条
    入口共用同一条验收链，版本核验不能只有一条有。

    `expected_rule_version` **由分派者传入**（它知道这一轮按哪套规则问的）；空串 = 不核
    （离线矩阵/回放按需显式给出）。**不许**用"覆盖响应里的声明"来制造一致。
    """
    _p: list = []
    if not isinstance(obj, dict):
        return None, [_rec("view_response_not_object", "response", "",
                           "响应不是 JSON 对象")]
    if obj.get("schema") != REVISION_SCHEMA:
        return None, [_rec("view_response_schema_unknown", "response", "",
                           "未知或缺失的响应 schema：%r（期望 %s）"
                           % (obj.get("schema"), REVISION_SCHEMA), "schema")]
    for _k in ("base_plan_hash", "fact_basis_hash", "view"):
        if _k not in obj:
            _p.append(_rec("view_response_field_missing", "response", "",
                           "响应缺字段 `%s`" % _k, _k))
    if _p:
        return None, _p
    _rv = str(obj.get("rule_version") or "")
    if not _rv:
        _p.append(_rec("view_rule_version_missing", "response", "",
                       "响应缺 `rule_version`——规则版本要与本次请求核对，不能省",
                       "rule_version"))
    elif expected_rule_version and _rv != str(expected_rule_version):
        _p.append(_rec("view_rule_version_mismatch", "response", "",
                       "`rule_version` 是 %r，本次请求期望 %r——换了规则版本就不能按这套"
                       "语义读" % (_rv, expected_rule_version), "rule_version"))
    if _p:
        return None, _p
    # 模型**不得自报通过、放宽预算或改写冻结事实**：请求里没有这些口子，出现了就是越界
    for _k in ("passed", "executable", "budget", "time_budget", "accepted"):
        if _k in obj:
            _p.append(_rec("view_response_self_verdict", "response", "",
                           "响应不许自报结论（出现 `%s`）——通过与否由本地验收判" % _k, _k))
    if base_view is not None:
        _vp = str(base_view.get("mode") or "")
        if isinstance(obj.get("view"), dict):
            _claim_mode = str(obj["view"].get("mode") or "")
            if _claim_mode and _claim_mode != _vp:
                _p.append(_rec("view_mode_mismatch", "response", "",
                               "响应视图 mode=%s 与基线（%s）不符" % (_claim_mode, _vp),
                               "view.mode"))
        if str(obj.get("base_plan_hash") or "") != view_digest(base_view):
            _p.append(_rec("view_base_hash_mismatch", "response", "",
                           "`base_plan_hash` 与本地基线不符——改的不是同一份基线",
                           "base_plan_hash"))
        if str(obj.get("fact_basis_hash") or "") != fact_basis_digest(base_view):
            _p.append(_rec("view_fact_basis_hash_mismatch", "response", "",
                           "`fact_basis_hash` 与本地基线不符——事实基础（正文主张 + "
                           "事件顺序）冻结，不许在响应里换掉", "fact_basis_hash"))
    return (obj if not _p else None), _p


def parse_revision_response(obj, *, base_view=None, expected_rule_version: str = "") -> tuple:
    """响应信封 → `(view, problems)`（**先信封、再视图内部**，不猜形状）."""
    _env, _p = _response_problems(obj, base_view=base_view,
                                  expected_rule_version=expected_rule_version)
    if _env is None:
        return None, _p
    _exp = str((base_view or {}).get("mode") or "") or None
    _view, _vp = parse_view(_env.get("view"), expected_mode=_exp)
    return _view, _p + _vp


# ---------------------------------------------------------------------------
# R2：完整事务应用器
# ---------------------------------------------------------------------------
_UID_PREFIX = {"beats": "b", "dialogue_units": "d",
               "narration_units": "n", "screen_cards": "c"}
_COLLECTIONS = ("beats", "dialogue_units", "narration_units", "screen_cards")


def _shape_gate(view: dict) -> list:
    """映射**之前**的一整份引用形状判据（映射会重建引用、把冲突抹平）.

    复核第十二轮 ①：「规范化只处理已通过检查的引用；映射后再验一次」——
    这里就是"映射前"的那一次，映射后的那一次在 `apply_editable_view` 里（`parse_view`）。
    """
    _p: list = []
    for _i, _b in enumerate(view.get("fact_bindings") or []):
        if not isinstance(_b, dict):
            _p.append(_rec("view_binding_item_invalid", "fact", "",
                           "`fact_bindings` 第 %d 项不是对象" % (_i + 1),
                           "fact_bindings[%d]" % _i))
            continue
        ref_list_problems(_b.get("carriers"), "fact_bindings[%d].carriers" % _i, _p,
                          container_code="view_binding_carriers_type")
    _hb = view.get("hook_bindings")
    if isinstance(_hb, dict):
        ref_list_problems(_hb.get("carriers"), "hook_bindings.carriers", _p,
                          container_code="view_hook_carriers_type")
    for _bi, _b in enumerate(view.get("beats") or []):
        if not isinstance(_b, dict):
            continue
        _vr = _b.get("voice_ref")
        if _vr is not None:
            _p.extend(ref_item_problems(_vr, "beats[%d].voice_ref" % _bi))
    return _p


def _assign_formal_uids(view: dict, base_view: dict) -> tuple:
    """临时 uid → **正式 uid**（R1：「已有实体保留 uid；新增使用响应内唯一临时 uid，
    代码一次分配正式 uid」）.

    映射**一次做完再统一改写引用**：先逐集合分配，再遍历 `voice_ref` / `fact_bindings` /
    `hook_bindings` 把旧 uid 换成新 uid。缺一步就会留下指向临时 uid 的引用（那在下一轮
    请求里就是悬空引用）。
    """
    _gate = _shape_gate(view)
    if _gate:
        return None, {}, _gate
    _out = json.loads(json.dumps(view))
    _mapping: dict = {}
    _used = set()
    for _k in _COLLECTIONS:
        for _e in (_out.get(_k) or []):
            _used.add(ref_uid(_e))
    _base_uids = {_k: {ref_uid(_e) for _e in ((base_view or {}).get(_k) or [])}
                  for _k in _COLLECTIONS}
    _n = 0
    for _k in _COLLECTIONS:
        _pfx = _UID_PREFIX[_k]
        for _e in (_out.get(_k) or []):
            _u = ref_uid(_e)
            if _u in _base_uids[_k]:
                _mapping[_u] = _u                       # 已有实体：uid 不变
                continue
            while True:                                 # 新实体：分配一个不冲突的正式 uid
                _n += 1
                _cand = "%s%02d" % (_pfx, _n)
                if _cand not in _used:
                    break
            _used.add(_cand)
            _mapping[_u] = _cand
    if len(set(_mapping.values())) != len(_mapping):
        return None, {}, [_rec("view_uid_map_collision", "view", "",
                               "临时 uid 映射后出现重复——响应里有 uid 只差写法"
                               "（例如同时用了 `uid` 与 `card_id`）")]

    def _mv(u):
        return _mapping.get(str(u or ""), str(u or ""))

    for _k in _COLLECTIONS:
        for _e in (_out.get(_k) or []):
            _u = ref_uid(_e)
            if _k == "screen_cards":
                _e["card_id"] = _mv(_u)
                _e.pop("uid", None)
                # `beat_uid` 同样是引用：新增卡片挂到**新增节拍**上时，它必须跟着一起
                # 改写，否则合并后留下一个指向临时 id 的 `beat_uid`（下一轮就是悬空）。
                if str(_e.get("beat_uid") or ""):
                    _e["beat_uid"] = _mv(_e["beat_uid"])
            else:
                _e["uid"] = _mv(_u)
    for _b in (_out.get("beats") or []):
        _vr = _b.get("voice_ref")
        if isinstance(_vr, dict) and ref_uid(_vr):
            # ⚠️ **重建规范引用**，不能只改 `uid`（复核 2026-10-06 第十一轮 P2）：合法同值
            # 别名 `{"uid":"new:dialogue","card_id":"new:dialogue"}` 只改 `uid` 会变成
            # `{"uid":"d02","card_id":"new:dialogue"}` —— **一条自己跟自己打架的引用**。
            # 那一轮结果仍是 `applied`，可**再解析就被拒收**，下一轮空增量也变 `illegal`：
            # 变换把一份合法输入变成了非法基线。
            _b["voice_ref"] = {"kind": str(_vr.get("kind") or ""),
                               "uid": _mv(ref_uid(_vr))}
    for _b in _out.get("fact_bindings") or []:
        _car = []
        for _c in ref_items(_b.get("carriers"), "fact_bindings.carriers"):
            _r = _ref_in(_c) or {}          # 形状已过 `_shape_gate`，这里只做读取
            if _r.get("uid"):
                _c = {"kind": _r["kind"], "uid": _mv(_r["uid"])}
            _car.append(_c)
        _b["carriers"] = _car
    _hb = _out.get("hook_bindings")
    if isinstance(_hb, dict):
        _cra = _hb.get("carriers")
        if _cra is None and _hb.get("carrier") is not None:
            _cra = [_hb["carrier"]]
        _hb["carriers"] = [{"kind": (_c or {}).get("kind"), "uid": _mv(ref_uid(_c))}
                           for _c in ref_items(_cra, "hook_bindings.carriers")
                           if isinstance(_c, dict)]
        _hb.pop("carrier", None)
        _hb["spoken_uids"] = [_mv(u) for u in (_hb.get("spoken_uids") or [])]
    return _out, _mapping, []


def _frozen_facts_intact(view: dict, base_view: dict) -> list:
    """冻结事实的**uid 集合与正文主张**必须与基线逐一相同（证据可经 `evidence_updates` 更正）."""
    _b = {str(f.get("uid")): str(f.get("claim") or "")
          for f in ((base_view or {}).get("frozen_facts") or [])}
    _n = {str(f.get("uid")): str(f.get("claim") or "")
          for f in (view.get("frozen_facts") or [])}
    _p: list = []
    if set(_b) != set(_n):
        _p.append(_rec("view_frozen_facts_changed", "view", "",
                       "冻结事实表被增删或改了 uid：基线 %s → 候选 %s"
                       % (sorted(_b), sorted(_n)), "frozen_facts"))
        return _p
    for _u in sorted(_b):
        if _b[_u] != _n[_u]:
            _p.append(_rec("view_frozen_facts_changed", "fact", _u,
                           "事实 %s 的**正文主张**被改写（基线 %r → 候选 %r）——主张冻结，"
                           "只允许经 `evidence_updates` 更正来源片段"
                           % (_u, _b[_u][:40], _n[_u][:40]), "frozen_facts"))
    return _p


def apply_editable_view(base_view: dict, response, *, mode: str | None = None,
                        accept=None, expected_rule_version: str = "") -> tuple:
    """**完整关系事务**：严格解析 → 核基线 → 构造候选 → 分配 ID/派生映射 →
    核冻结事实事件 → 核来源及结构 → 收集残余 → 控制器决定.

    `returns (candidate_view|None, problems, result)`；`result` 始终是字典：

    - `kind`：`"applied"` / `"no_change"` / `"illegal"`；
    - `detail`：拒收或结论的**生产者原话**（含具体字段路径），调用方不重新解释；
    - `mapping`：临时 uid → 正式 uid（`applied` 时非空）；
    - `residuals`：给了 `accept` 时的**完整残余问题**（有候选才有，不是"可执行"的判据
      ——可执行与否由控制器按空表判）。

    **`illegal` 只表示形状、基线、引用或保护边界违约**（R2 明文）。合法候选即使仍有
    预算/内容残余也记 `applied` + 残余：不能因为"改了一项却还有独立基线问题"就把有效的
    局部修正丢掉，也不能接收破坏冻结事实的"改善"。

    `accept(candidate_view) -> [问题]` 由调用方注入（生产入口传统一验收链）；纯应用器
    **不调模型、不做语义判断**——语义核验在外层控制器（R4）。
    """
    _p: list = []
    _view, _p = parse_revision_response(response, base_view=base_view,
                                        expected_rule_version=expected_rule_version)
    if _view is None:
        return None, _p, {"kind": "illegal", "mapping": {}, "residuals": [],
                          "detail": _detail_of(_p)}
    _view = json.loads(json.dumps(_view))
    _mode = str(mode or base_view.get("mode") or MODE_LEGACY)
    if str(_view.get("mode") or "") != _mode:
        _p = [_rec("view_mode_mismatch", "view", "",
                   "候选 mode=%r 与本次期望（%s）不符" % (_view.get("mode"), _mode),
                   "mode")]
        return None, _p, {"kind": "illegal", "mapping": {}, "residuals": [],
                          "detail": _detail_of(_p)}

    # ① 冻结事实（uid 集合 + 正文主张）
    _p = _frozen_facts_intact(_view, base_view)
    if _p:
        return None, _p, {"kind": "illegal", "mapping": {}, "residuals": [],
                          "detail": _detail_of(_p)}
    # ② 分配正式 uid 并统一改写引用
    _view, _mapping, _mp = _assign_formal_uids(_view, base_view)
    if _view is None:
        return None, _mp, {"kind": "illegal", "mapping": {}, "residuals": [],
                           "detail": _detail_of(_mp)}
    # **②b 变换后重验完整形状**（复核 2026-10-06 第十一轮 P2）：uid 映射是一次**改写**，
    # 而"待写入的结果必须满足读取与恢复要求"是既有口径（CLAUDE.md「状态与恢复实施」）。
    # 旧写法只保证映射前合法，映射出来的东西没人再看过一眼——实测能造出
    # `{"uid":"d02","card_id":"new:dialogue"}` 这种**自己跟自己打架**的引用，结果记
    # `applied`，下一轮它变成基线就再也读不进去了。
    _pv, _pvp = parse_view(_view, expected_mode=_mode)
    if _pv is None or _pvp:
        return None, _pvp, {"kind": "illegal", "mapping": {}, "residuals": [],
                            "detail": _detail_of(_pvp)}
    # ③ 关系判定（事件覆盖/顺序、冻结基础与基线比对）
    _p = view_problems(_view, base_view=base_view)
    if _p:
        return None, _p, {"kind": "illegal", "mapping": {}, "residuals": [],
                          "detail": _detail_of(_p)}

    # ③b **其余计划字段**（`climax`/`causal_chain`/`time_budget` …）同样冻结：
    # 视图只开放**关系层**，模型不能借"回一整份视图"顺手改掉它们
    _bb = _digest(base_view.get("plan_basis") or {})
    if _digest(_view.get("plan_basis") or {}) != _bb:
        _p = [_rec("view_plan_basis_changed", "view", "",
                   "视图里**关系之外**的计划字段（`climax` / `causal_chain` / `time_budget` "
                   "等）与基线不符——R1 的视图是关系层的可编辑投影，不是整份计划的替代品",
                   "plan_basis")]
        return None, _p, {"kind": "illegal", "mapping": {}, "residuals": [],
                          "detail": _detail_of(_p)}

    # ④ 没变 = `no_change`（**代码比较摘要**，不接受"自报没改"）
    if view_digest(_view) == view_digest(base_view):
        return None, [], {"kind": "no_change", "mapping": {},
                          "residuals": [], "detail": "候选与基线逐字相同（摘要一致）"}

    # ⑤ 残余：**有候选之后**才收集；空表由控制器判"可执行"，这里不下结论
    _res = list(accept(_view) or []) if accept is not None else []
    return _view, [], {"kind": "applied", "mapping": _mapping,
                       "residuals": _res, "detail": ""}


def _detail_of(problems) -> str:
    """拒收详情：把生产者记录渲染成一行行**带字段路径与原因**的原话（不归纳）."""
    _lines = []
    for _p in (problems or []):
        _path = str((_p or {}).get("path") or "").strip()
        _txt = str((_p or {}).get("reason") or "").strip()
        _code = str((_p or {}).get("code") or "")
        _lines.append("%s%s｜%s" % ("%s " % _path if _path else "", _code, _txt))
    return chr(10).join(_lines)


# ---------------------------------------------------------------------------
# R 收口包 B：`plan_revision.delta.v1` —— **按稳定 UID 的增量修订**
#
# 为什么加这一层（复核 2026-10-06 的裁决）：v2 的「完整可编辑视图替换」要模型把整份
# 视图**逐字重抄**一遍再改几处，实测（§43/§46）这个输出负担本身就是失败的来源——模型
# 要么抄不下（`finish_reason=length` 截断），要么退化成假摘要。增量层只让模型回**改动**：
# 未提到的条目由本地基线保留，冻结主张 / 事件 / `plan_basis` **一个字都不用它重抄**。
#
# **它换的只是传输表示**（复核明文）：合并出来的仍是一份完整视图，随后走**同一条**
# `apply_editable_view` 验收链——R2 的「统一验收」不能有第二条。不按数组位置猜目标、
# 不自动删悬空引用、不静默补绑定；任一结构违约**整笔拒收、基线不动**。
# ---------------------------------------------------------------------------
DELTA_SCHEMA = "plan_revision.delta.v1"
DELTA_RULE_VERSION = "plan_revision.delta.v1"

#: 可由模型增删改的四个实体数组（键名与视图一致）
DELTA_ENTITY_KEYS = ("beats", "dialogue_units", "narration_units", "screen_cards")
#: **新增条目**的临时 uid 前缀（复核 2026-10-06 规格：「新增条目使用**明确的**临时
#: UID」）。这样"不存在的正式 uid"与"新条目"可判：**没带这个前缀、又不在基线里的 uid
#: 一律拒收**——不能把打错的正式 uid 静默当成新增（那会把「改 B3」变成「多出一条」）。
DELTA_TEMP_UID_PREFIX = "new:"

#: 增量响应**不许出现**的键。冻结内容由本地基线保留、不要求重抄；出现即越界，
#: 不按内容猜它想改什么（也不许自报结论）。
DELTA_FORBIDDEN_KEYS = ("view", "frozen_facts", "frozen_events", "plan_basis",
                        "mode", "event_binding", "passed", "executable", "budget")

#: 四类实体的**身份键**（单源）。卡片是 `card_id`，其余是 `uid`——复核 2026-10-06 P1：
#: 统一按 `uid` 读会把合法卡片判成"缺 uid"，再补个别名又变成"未知 id"。
DELTA_ID_KEY = {"beats": "uid", "dialogue_units": "uid",
                "narration_units": "uid", "screen_cards": "card_id"}
#: 增量响应**允许出现**的顶层键（**白名单**）。拼错的键（如 `fact_binding`）静默忽略
#: 等于没约束——改动会被悄悄丢掉，所以未知键一律拒收。
DELTA_TOP_KEYS = ("schema", "rule_version", "base_plan_hash", "fact_basis_hash",
                  "upsert", "delete", "order", "fact_bindings", "hook_bindings",
                  "evidence_updates", "evidence_delete")


def _delta_id(entity, key) -> str:
    """实体 → 稳定身份。**唯一读法**（卡片读 `card_id`，其余读 `uid`）.

    与 `ref_uid` 同源：解析、查找、删除、排序、映射共用它，不各自 `.get("uid")`。
    """
    return entity_identity(entity, key)


# ---------------------------------------------------------------------------
# 实体契约（**单源**）：四类实体的身份、必填字段、类型、允许空值、嵌套引用
#
# 复核 2026-10-06（第七轮）①：此前必填键由**基线键交集**推导，等于让样本决定契约——
# 空对白基线里新增一条空对白被判 `applied`，而带合法 `uid` 别名的卡片反而被判"缺字段"；
# 且四类实体的 `text` 给 `null`/数字/数组/对象**全部被接受**。这里把契约写死一处，
# 增量入口、完整视图解析与 D schema 共用同一份规则。
#
# 类型取值：`str` / `list` / `obj` / `list[str]` / `obj|null` / `str|null`。
# **空字符串不是类型违约**：`""` 的内容裁决交既有验收，不和类型检查混在一起。
# ---------------------------------------------------------------------------
ENTITY_CONTRACT = {
    "beats": ("uid", {"event_uids": "list[str]", "text": "str", "purpose": "str",
                      "evidence": "str", "source": "obj", "voice_ref": "obj|null"}),
    "dialogue_units": ("uid", {"text": "str", "source": "obj"}),
    "narration_units": ("uid", {"text": "str", "source": "obj"}),
    "screen_cards": ("card_id", {"beat_uid": "str", "template": "str",
                                 "text": "str", "placement": "str"}),
}
#: 身份**别名**：卡片可以写 `uid`，其余可以写 `card_id`；**同值才接受**，冲突拒收。
ENTITY_ID_ALIAS = {"beats": "card_id", "dialogue_units": "card_id",
                   "narration_units": "card_id", "screen_cards": "uid"}


def _type_ok(value, spec) -> bool:
    if spec == "str":
        return isinstance(value, str)
    if spec == "list":
        return isinstance(value, list)
    if spec == "obj":
        return isinstance(value, dict)
    if spec == "int":
        # `bool` 是 `int` 的子类：不排掉它，`True` 会被当成 1 —— 「卡片挂在 true 号节拍上」
        # 这种违约就会被收下（复核 2026-10-07）。
        return isinstance(value, int) and not isinstance(value, bool)
    if spec == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if spec.endswith("|null"):
        return value is None or _type_ok(value, spec[:-5])
    return True


def _entity_type_label(value) -> str:
    return "null" if value is None else type(value).__name__


def entity_contract_problems(entity, key: str, where: str, *,
                             require_all: bool = True) -> list:
    """一条实体 → 身份/字段/类型问题（**四类实体共用这一份判据**）.

    `require_all=True`（默认，`parse_view` 用）：**缺字段**也报——视图的实体是完整的。

    `require_all=False`（**消费准入**用）：只核**写出来的那些字段的类型**与身份别名，
    缺席不算违约。消费侧一律按 `.get(f) or 默认` 读，"没写"与"写错了"是两件事：
    手写的计划/视图里省略可选字段是常态，把它判成"读不出来"就是把合法输入拒之门外。
    """
    _p: list = []
    if not isinstance(entity, dict):
        return [_rec("entity_not_object", "view", "",
                     "%s 必须是对象（当前是 %s）" % (where, type(entity).__name__), where)]
    _idk, _fields = ENTITY_CONTRACT[key]
    _alias = ENTITY_ID_ALIAS[key]
    _std, _alt = entity.get(_idk), entity.get(_alias)
    if _std is not None and _alt is not None and str(_std) != str(_alt):
        _p.append(_rec("entity_identity_alias_conflict", "view", str(_std),
                       "%s 同时给了 `%s`=%r 与 `%s`=%r —— 别名与身份键**不一致**。"
                       "已有实体的身份必须保持稳定，冲突要在查找/替换/映射之前拒收"
                       % (where, _idk, _std, _alias, _alt), "%s.%s" % (where, _idk)))
    for _f, _spec in _fields.items():
        if _f not in entity:
            if require_all:
                _p.append(_rec("entity_field_missing", "view", str(_std or _alt or ""),
                               "%s 缺必填字段 `%s`（要 %s）" % (where, _f, _spec),
                               "%s.%s" % (where, _f)))
        elif not _type_ok(entity[_f], _spec):
            _p.append(_rec("entity_field_type", "view", str(_std or _alt or ""),
                           "%s 的 `%s` 类型不对：要 %s，当前是 %s"
                           % (where, _f, _spec, _entity_type_label(entity[_f])),
                           "%s.%s" % (where, _f)))
    # 实体**内部的嵌套引用**（`beats[].voice_ref`）同样受引用身份契约约束——
    # 复核 2026-10-06（第十轮）：引用冲突与实体别名冲突是**同一类**缺陷，落点不同而已。
    _vr = entity.get("voice_ref")
    if isinstance(_vr, dict):
        _p.extend(ref_identity_problems(_vr, "%s 的 `voice_ref`" % where))
    if _idk == "card_id" and "beat_uid" in entity and not str(entity["beat_uid"] or ""):
        _p.append(_rec("entity_field_type", "view", str(_std or ""),
                       "%s 的 `beat_uid` 不能为空——卡片必须挂在一条节拍上" % where,
                       "%s.beat_uid" % where))
    return _p



#: **卡片声明**（首版 / 增量请求里的**旧形状**：`beat` 是整数节拍序号，不是 `beat_uid`）
#: 的字段类型表：`字段 → (类型, 类型违约时的 code)`。
#:
#: 判据与 `ENTITY_CONTRACT["screen_cards"]` **同源同口径**（写错类型是违约、缺席不是、
#: `null` 按缺席读），差别只在**跑在转换之前**：`beat` 还没变成 `beat_uid`，
#: `str(...)` 也还没把 `text=123` 洗成一个合法字符串。
#:
#: 复核 2026-10-07 P1：`screen_cards` 的三类「转换把违约吃掉」——`str()` 洗类型、
#: 列表推导筛掉坏成员、容器不是数组时直接抛 `TypeError`——在**首版入口**与
#: **直接规范化入口**是同一条路（两处都调 `normalize_hybrid_plan`），判据只写这一份。
CARD_DECL_FIELDS = {
    "beat": ("int", "presentation_card_beat_type"),
    "template": ("str", "presentation_card_field_type"),
    "text": ("str", "presentation_card_field_type"),
    "placement": ("str", "presentation_card_field_type"),
    "anchor": ("str", "presentation_card_field_type"),
}


def value_brief(value, limit: int = 40) -> str:
    """原值的**短摘要**（问题记录与回灌提示里用；不落整份对象）."""
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"


def card_declaration_problems(decl, where: str, index=None) -> list:
    """**一条**卡片声明 → 转换之前的类型问题（带原字段路径与原值；不字符串化、不过滤）.

    `where` 形如 `screen_cards[0]`；`index` 是展示编号（1 起，缺省 `None`）。
    `null` 与缺席同读——可选字段的默认值在转换处给（`normalize_hybrid_plan`）。
    `source_spans`（可选出处）是**对象表**：容器与成员分别判，**不筛掉坏成员**
    （筛掉等于把"出处写坏了"悄悄变成"没有出处"）。

    ⚠️ **容器**（`screen_cards` 本身是不是数组）不在这里判：那必须在**取成员之前**判
    （否则 `enumerate(1)` 会先一步抛 `TypeError`），由调用方按全函数写法记一条
    `presentation_cards_type`。
    """
    _p: list = []
    if not isinstance(decl, dict):
        return [{"code": "presentation_card_item_invalid", "kind": "card", "index": index,
                 "path": where, "value": decl,
                 "reason": "%s 必须是对象（当前是 %s）"
                           % (where, type(decl).__name__)}]
    for _f, (_spec, _code) in CARD_DECL_FIELDS.items():
        _v = decl.get(_f)
        if _v is None or _type_ok(_v, _spec):
            continue
        _p.append({"code": _code, "kind": "card", "index": index,
                   "path": "%s.%s" % (where, _f), "value": _v,
                   "reason": "%s 的 `%s` 类型不对：要 %s，当前是 %s（%s）"
                             "——**不做字符串化、不截整、不按位置补**，请按 schema 给正确类型"
                             % (where, _f, _spec, value_brief(_v),
                                _entity_type_label(_v))})
    _spans = decl.get("source_spans")
    if _spans is not None and not isinstance(_spans, list):
        _p.append({"code": "presentation_card_source_spans_type", "kind": "card",
                   "index": index, "path": "%s.source_spans" % where, "value": _spans,
                   "reason": "%s 的 `source_spans` 必须是数组（当前是 %s）"
                             % (where, _entity_type_label(_spans))})
    elif isinstance(_spans, list):
        for _i, _x in enumerate(_spans):
            if not isinstance(_x, dict):
                _p.append({"code": "presentation_card_source_spans_item", "kind": "card",
                           "index": index, "path": "%s.source_spans[%d]" % (where, _i),
                           "value": _x,
                           "reason": "%s 的 `source_spans[%d]` 必须是对象（当前是 %s）"
                                     "——不筛掉坏成员"
                                     % (where, _i, _entity_type_label(_x))})
    return _p


def normalize_entity_identity(entity, key: str) -> dict:
    """实体 → **只保留标准身份键**的副本（别名同值时才接受）.

    复核 2026-10-06（第七轮）②：`_delta_id` 优先 `card_id`、`ref_uid` 优先 `uid`，
    两条读法分叉——已有卡片同时给 `card_id="c05"` 与 `uid="c99"` 时会被**重新编号**成
    `c01`。规范化在这里收口：查找到、替换、映射都用规范化后的对象。
    """
    _out = dict(entity)
    _idk, _alias = ENTITY_CONTRACT[key][0], ENTITY_ID_ALIAS[key]
    if not str(_out.get(_idk) or "") and str(_out.get(_alias) or ""):
        _out[_idk] = str(_out[_alias])
    _out.pop(_alias, None)
    if _idk != "uid":
        _out.pop("uid", None)
    return _out


def entity_identity(entity, key: str) -> str:
    """实体 → 身份（**规范化后**的唯一读法；别名同值时才算数）."""
    return str(normalize_entity_identity(entity, key).get(ENTITY_CONTRACT[key][0]) or "")


def _delta_rec(code, reason, path=""):
    return _rec(code, "delta", "", reason, path)


def delta_request(base_view: dict, *, problems=None, rejections=None,
                  rule_version: str = "", remaining_calls=None,
                  remaining_sec=None, limits=None) -> dict:
    """构造 `plan_revision.delta.v1` 请求：与 v2 请求**同源**，只有响应契约不同.

    请求侧要带的东西一模一样（基线摘要 / 冻结事实与事件 / **当前完整视图** / 全部问题与
    拒收记录）——模型得看得见当前形态才改得动；变的只是"它要回什么"。
    """
    _r = revision_request(base_view, problems=problems, rejections=rejections,
                          rule_version=(rule_version or DELTA_RULE_VERSION),
                          remaining_calls=remaining_calls,
                          remaining_sec=remaining_sec, limits=limits)
    return dict(_r, schema=DELTA_SCHEMA)


def delta_response_problems(obj, *, base_view=None,
                            expected_rule_version: str = "") -> list:
    """增量响应**信封 / 版本 / 改动块 / 嵌套类型**的严格准入（白名单，不猜形状）.

    复核 2026-10-06（第六轮）三条 P1 的落点都在这里：① 四类实体的**身份键**不同
    （卡片是 `card_id`）不能统一读 `uid`；② 未知顶层键不许静默忽略（拼错的
    `fact_binding` 等于没约束）；③ 规则版本要与**本次请求的期望值**核对。
    """
    _p: list = []
    if not isinstance(obj, dict):
        return [_delta_rec("delta_response_not_object", "响应不是 JSON 对象")]
    if obj.get("schema") != DELTA_SCHEMA:
        return [_delta_rec("delta_response_schema_unknown",
                           "未知或缺失的响应 schema：%r（期望 %s）"
                           % (obj.get("schema"), DELTA_SCHEMA), "schema")]
    # **顶层白名单**：未知键一律拒收（拼错的 `fact_binding` 静默忽略 = 改动被悄悄丢掉）
    for _k in sorted(set(obj) - set(DELTA_TOP_KEYS)):
        _p.append(_delta_rec("delta_unknown_top_key",
                             "增量响应里有未知顶层键 `%s`（合法键：%s）——不忽略，"
                             "拼错的键会让改动被悄悄丢掉" % (_k, list(DELTA_TOP_KEYS)), _k))
    _rv = str(obj.get("rule_version") or "")
    if not _rv:
        _p.append(_delta_rec("delta_rule_version_missing",
                             "缺 `rule_version`——规则版本要与本次请求核对，不能省",
                             "rule_version"))
    elif expected_rule_version and _rv != str(expected_rule_version):
        _p.append(_delta_rec("delta_rule_version_mismatch",
                             "`rule_version` 是 %r，本次请求期望 %r——换了规则版本就不能"
                             "按这套语义读" % (_rv, expected_rule_version), "rule_version"))
    for _k in DELTA_FORBIDDEN_KEYS:
        if _k in obj:
            _p.append(_delta_rec(
                "delta_forbidden_key",
                "增量响应**不许**出现 `%s`：冻结内容（事实主张 / 事件 / `plan_basis`）"
                "由本地基线保留、**不要求重抄**；结论字段也不许自报" % _k, _k))
    if base_view is not None:
        if str(obj.get("base_plan_hash") or "") != view_digest(base_view):
            _p.append(_delta_rec("delta_base_hash_mismatch",
                                 "`base_plan_hash` 与本地基线不符——改的不是同一份基线",
                                 "base_plan_hash"))
        if str(obj.get("fact_basis_hash") or "") != fact_basis_digest(base_view):
            _p.append(_delta_rec("delta_fact_basis_hash_mismatch",
                                 "`fact_basis_hash` 与本地基线不符——事实基础冻结",
                                 "fact_basis_hash"))

    # ---- 三个改动块：键名、类型、以及**逐项**的形状（含嵌套引用）----
    for _blk in ("upsert", "delete", "order"):
        _v = obj.get(_blk, {})
        if _v is None or not isinstance(_v, dict):
            _p.append(_delta_rec("delta_block_type",
                                 "`%s` 必须是对象（键为 %s；不改就省略该块）"
                                 % (_blk, list(DELTA_ENTITY_KEYS)), _blk))
            continue
        for _key, _items in _v.items():
            if _key not in DELTA_ENTITY_KEYS:
                _p.append(_delta_rec("delta_block_unknown_key",
                                     "`%s` 里出现未知数组名 %r（合法：%s）"
                                     % (_blk, _key, list(DELTA_ENTITY_KEYS)),
                                     "%s.%s" % (_blk, _key)))
                continue
            if not isinstance(_items, list):
                _p.append(_delta_rec("delta_block_items_type",
                                     "`%s.%s` 必须是数组" % (_blk, _key),
                                     "%s.%s" % (_blk, _key)))
                continue
            _idk = DELTA_ID_KEY[_key]
            for _i, _it in enumerate(_items):
                _where = "%s.%s[%d]" % (_blk, _key, _i)
                if _blk == "upsert":
                    if not isinstance(_it, dict):
                        _p.append(_delta_rec("delta_upsert_item_type",
                                             "`upsert` 的每一项都必须是**完整对象**"
                                             "（当前是 %s）——不按位置猜目标"
                                             % type(_it).__name__, _where))
                        continue
                    if not _delta_id(_it, _key):
                        _p.append(_delta_rec(
                            "delta_upsert_uid_missing",
                            "`upsert` 的每一项都要带 **`%s`**：已有实体用原值，新增用"
                            "本次响应内唯一、以 `%s` 开头的临时 id"
                            % (_idk, DELTA_TEMP_UID_PREFIX), _where))
                        continue
                    # **保留具体 code**（`entity_field_missing` / `entity_field_type` /
                    # `entity_identity_alias_conflict`…）——`path` 已指向
                    # `upsert.<集合>[i].<字段>`，覆盖成笼统 code 会丢掉可诊断性
                    _p.extend(entity_contract_problems(_it, _key, _where))
                elif not (isinstance(_it, str) and _it.strip()):
                    _p.append(_delta_rec("delta_uid_list_item_type",
                                         "`%s.%s` 的每一项都必须是非空 **id 字符串**"
                                         "（当前是 %s）" % (_blk, _key,
                                                          type(_it).__name__), _where))

    # ---- 关系块：嵌套类型在这里就核，**不让它走到后面抛异常** ----
    _fb = obj.get("fact_bindings", [])
    if not isinstance(_fb, list):
        _p.append(_delta_rec("delta_fact_bindings_type",
                             "`fact_bindings` 必须是数组（只列**改动过**的事实）",
                             "fact_bindings"))
    else:
        _known = {str(f.get("uid")) for f in (
            (base_view or {}).get("frozen_facts") or [])}
        for _i, _b in enumerate(_fb):
            if not isinstance(_b, dict):
                _p.append(_delta_rec("delta_binding_item_type",
                                     "`fact_bindings` 第 %d 项不是对象" % (_i + 1),
                                     "fact_bindings[%d]" % _i))
                continue
            _fu = str(_b.get("fact_uid") or "")
            if base_view is not None and _fu not in _known:
                _p.append(_delta_rec("delta_binding_unknown_fact",
                                     "`fact_bindings` 指向不存在的冻结事实：%r" % _fu,
                                     "fact_bindings[%d].fact_uid" % _i))
            if not isinstance(_b.get("carriers"), list):
                _p.append(_delta_rec("delta_binding_carriers_type",
                                     "事实 %s 的 `carriers` 必须是数组" % _fu,
                                     "fact_bindings[%d].carriers" % _i))
                continue
            for _ci, _c in enumerate(_b["carriers"]):
                _w = "fact_bindings[%d].carriers[%d]" % (_i, _ci)
                if not isinstance(_c, dict):
                    _p.append(_delta_rec("delta_binding_carrier_item_type",
                                         "载体必须是对象（当前是 %s）——`[1]` 这类序号"
                                         "占位一律拒收" % type(_c).__name__, _w))
                elif not ref_uid(_c):
                    _p.append(_delta_rec("delta_binding_dangling",
                                         "载体引用形状非法：%r（要 `kind` + 对应 id）"
                                         % (_c,), _w))
                else:
                    for _cp in ref_identity_problems(_c, _w):
                        _p.append(_delta_rec(_cp["code"], _cp["reason"], _w))
    _hb = obj.get("hook_bindings", None)
    if "hook_bindings" in obj and _hb is None:
        _p.append(_delta_rec("delta_hook_bindings_null",
                             "`hook_bindings` 是 `null`——**省略**表示保留、**完整空关系**"
                             "表示清空，`null` 不是任何一种（缺席≠null 的同一口径）",
                             "hook_bindings"))
    elif _hb is not None:
        if not isinstance(_hb, dict):
            _p.append(_delta_rec("delta_hook_bindings_type",
                                 "`hook_bindings` 必须是对象（给了就**整块替换**基线）",
                                 "hook_bindings"))
        else:
            _lack = [k for k in ("spoken_uids", "carriers") if k not in _hb]
            if _lack:
                _p.append(_delta_rec(
                    "delta_hook_bindings_incomplete",
                    "钩子是**整块替换**，必须同时给 `spoken_uids` 与 `carriers`（缺 %s）"
                    "——只要一个 `{}` 不能当清空，给不全就拒收" % _lack, "hook_bindings"))
            for _k in ("spoken_uids", "carriers"):
                if _k in _hb and not isinstance(_hb[_k], list):
                    _p.append(_delta_rec("delta_hook_bindings_field_type",
                                         "`hook_bindings.%s` 必须是数组" % _k,
                                         "hook_bindings.%s" % _k))
            for _ci, _c in enumerate(
                    ref_items(_hb.get("carriers"), "hook_bindings.carriers")):
                _w = "hook_bindings.carriers[%d]" % _ci
                if not isinstance(_c, dict):
                    _p.append(_delta_rec("delta_hook_carrier_item_type",
                                         "钩子载体必须是对象（当前是 %s）"
                                         % type(_c).__name__, _w))
                    continue
                if not ref_uid(_c):
                    _p.append(_delta_rec("delta_hook_carrier_dangling",
                                         "钩子载体引用形状非法：%r（要 `kind` + 对应 id）"
                                         % (_c,), _w))
                    continue
                for _cp in ref_identity_problems(_c, _w):
                    _p.append(_delta_rec(_cp["code"], _cp["reason"], _w))
    for _k in ("evidence_updates", "evidence_delete"):
        if _k in obj and not isinstance(obj.get(_k), list):
            _p.append(_delta_rec("delta_evidence_type",
                                 "`%s` 必须是数组" % _k, _k))
    return _p


def parse_delta_response(obj, *, base_view=None, expected_rule_version: str = "") -> tuple:
    """增量响应的**唯一解析入口**：形状/版本/基线违约 ⇒ `(None, 问题)`，不半可读地放行."""
    _p = delta_response_problems(obj, base_view=base_view,
                                 expected_rule_version=expected_rule_version)
    return (None, _p) if _p else (obj, [])


def merge_delta(base_view: dict, delta: dict) -> tuple:
    """`基线视图 + 增量` → **完整视图**（纯函数，不调模型、不判语义）.

    合并规则（复核 2026-10-06 规格）：
    - **未提到的条目一律保留**；`upsert` 里已有的 id = 整体替换该条目，临时 id = 新增；
    - 身份**按集合读**（卡片是 `card_id`）——四类实体共用 `_delta_id` 这一个读法；
    - `delete` 只认**显式** id；删不存在的、同一 id 既删又改、`upsert` 里 id 重复，
      都是结构违约；
    - **集合变了就必须给 `order`**（最终完整 id 顺序，恰好覆盖结果集合）；
    - `fact_bindings` 只替换**列出来的**事实，其余保留基线的绑定；**同一 fact_uid 重复
      直接拒收**（先覆盖再交验收 = 把关系信息在验证前消灭）；
    - `evidence_updates` **按事实身份增改**（未提及的修正保留），`evidence_delete`
      显式撤销（只删修正记录，不动主张与基线出处）；
    - `hook_bindings` 给了就整块替换，但**必须是完整的合法关系**（形状由准入层核过）；
    - `frozen_facts` / `frozen_events` / `plan_basis` / `mode` / `event_binding` 一律
      取基线——模型不重抄，也不许改。
    """
    _p: list = []
    _v = json.loads(json.dumps(base_view))

    _ups = delta.get("upsert") or {}
    _del = delta.get("delete") or {}
    _ord = delta.get("order") or {}

    for _key in DELTA_ENTITY_KEYS:
        _cur = [normalize_entity_identity(x, _key)
                for x in (_v.get(_key) or []) if isinstance(x, dict)]
        _known = [_delta_id(x, _key) for x in _cur]
        _ups_items = [normalize_entity_identity(x, _key)
                      for x in (_ups.get(_key) or []) if isinstance(x, dict)]
        _ups_ids = [_delta_id(x, _key) for x in _ups_items]
        _del_ids = [str(u) for u in (_del.get(_key) or []) if str(u).strip()]

        _dup = sorted({u for u in _ups_ids if _ups_ids.count(u) > 1})
        if _dup:
            _p.append(_delta_rec("delta_upsert_duplicate_id",
                                 "`upsert.%s` 里这些 id 出现了不止一次：%s"
                                 % (_key, _dup), "upsert.%s" % _key))
            continue
        _unknown = sorted({u for u in _ups_ids if u not in set(_known)
                           and not u.startswith(DELTA_TEMP_UID_PREFIX)})
        if _unknown:
            _p.append(_delta_rec(
                "delta_upsert_uid_unknown",
                "`upsert.%s` 里这些 id 既不在基线里、也不是临时 id：%s"
                "——**不存在的正式 id 不能当新增**（打错的 id 静默变新增，会把"
                "「改这一条」变成「多出一条」）。新条目请用 `%s` 开头的临时 id"
                % (_key, _unknown, DELTA_TEMP_UID_PREFIX), "upsert.%s" % _key))
            continue
        _missing = sorted({u for u in _del_ids if u not in set(_known)})
        if _missing:
            _p.append(_delta_rec("delta_delete_unknown_uid",
                                 "`delete.%s` 里这些 id 不在基线里：%s——删除只认**已有**"
                                 "稳定 id，不按相似度猜" % (_key, _missing),
                                 "delete.%s" % _key))
            continue
        _both = sorted(set(_del_ids) & set(_ups_ids))
        if _both:
            _p.append(_delta_rec("delta_delete_and_upsert_same_uid",
                                 "这些 id 同时出现在 `delete` 与 `upsert` 里：%s"
                                 "（自相矛盾，拒收而不是猜哪个优先）" % _both,
                                 "delete.%s" % _key))
            continue
        _after = [x for x in _cur if _delta_id(x, _key) not in set(_del_ids)]
        _idx = {_delta_id(x, _key): i for i, x in enumerate(_after)}
        for _x in _ups_items:
            _u = _delta_id(_x, _key)
            if _u in _idx:
                _after[_idx[_u]] = dict(_x)
            else:
                _idx[_u] = len(_after)
                _after.append(dict(_x))

        # **集合变了就必须给最终完整顺序**（「新增、删除或重排集合 ⇒ 给出最终完整
        # UID 顺序」）。不给的话「新条目排第几」只能靠隐式约定猜——那正是要避免的。
        _set_changed = bool(_del_ids) or any(u not in set(_known) for u in _ups_ids)
        _seq = _ord.get(_key)
        if _seq is None and _set_changed:
            _p.append(_delta_rec(
                "delta_order_required",
                "`upsert` / `delete` 改动了集合 ⇒ 必须给出 `order.%s` 的**最终完整 id "
                "顺序**（%d 项，含新增的临时 id）——新条目排第几不作隐式约定；"
                "只改正文、不动集合时才可以省略" % (_key, len(_after)),
                "order.%s" % _key))
            continue
        if _seq is not None:
            _want = [str(u) for u in _seq]
            _have = [_delta_id(x, _key) for x in _after]
            if sorted(_want) != sorted(_have) or len(set(_want)) != len(_want):
                _p.append(_delta_rec(
                    "delta_order_not_a_permutation",
                    "`order.%s` 必须是合并后那份 id 集合的**排列**——改动后 %d 条，"
                    "而 order 给了 %d 项（差值：%s）。顺序块不许增删条目，增删请走 "
                    "`upsert` / `delete`"
                    % (_key, len(_have), len(_want),
                       sorted(set(_have) ^ set(_want))), "order.%s" % _key))
                continue
            _by = {_delta_id(x, _key): x for x in _after}
            _after = [_by[u] for u in _want]
        _v[_key] = _after
    if _p:
        return None, _p

    # ---- 事实绑定：**先查重再合并**（同一 fact_uid 重复 ⇒ 拒收，不让"后者覆盖前者"）----
    _fb = delta.get("fact_bindings") or []
    if _fb:
        _seen_fu: list = []
        for _b in _fb:
            _fu = str(_b.get("fact_uid") or "")
            if _fu in _seen_fu:
                _p.append(_delta_rec("delta_binding_duplicate",
                                     "事实 %s 在 `fact_bindings` 里出现了**两次**——"
                                     "先覆盖再交完整验收会把前一项的载体关系在验证前"
                                     "消灭掉，所以这里就拒收" % _fu,
                                     "fact_bindings"))
            _seen_fu.append(_fu)
        if _p:
            return None, _p
        _by_fact = {str(b.get("fact_uid")): dict(b)
                    for b in (_v.get("fact_bindings") or []) if isinstance(b, dict)}
        for _b in _fb:
            _by_fact[str(_b.get("fact_uid"))] = {
                "fact_uid": str(_b.get("fact_uid")),
                "carriers": list(ref_items(_b.get("carriers"), "fact_bindings.carriers"))}
        _order = [str(f.get("uid")) for f in (_v.get("frozen_facts") or [])]
        _v["fact_bindings"] = [_by_fact[u] for u in _order if u in _by_fact]

    # ---- 出处修正：**按事实身份增改**，未提及的修正保留；`evidence_delete` 显式撤销 ----
    _upd = delta.get("evidence_updates") or []
    _edel = [str(x) for x in (delta.get("evidence_delete") or [])]
    if _upd or _edel:
        _seen_ev: list = []
        for _e in _upd:
            if not isinstance(_e, dict):
                _p.append(_delta_rec("delta_evidence_item_type",
                                     "`evidence_updates` 的每一项都必须是对象",
                                     "evidence_updates"))
                continue
            _fu = str(_e.get("fact_uid") or "")
            if _fu in _seen_ev:
                _p.append(_delta_rec("delta_evidence_duplicate",
                                     "事实 %s 在 `evidence_updates` 里出现了两次" % _fu,
                                     "evidence_updates"))
            _seen_ev.append(_fu)
        _both_ev = sorted(set(_seen_ev) & set(_edel))
        if _both_ev:
            _p.append(_delta_rec("delta_evidence_update_and_delete_same_fact",
                                 "这些事实同时在 `evidence_updates` 与 `evidence_delete` 里："
                                 "%s（同一份响应里既改又撤，拒收）" % _both_ev,
                                 "evidence_delete"))
        _exist = {str(e.get("fact_uid") or ""): dict(e)
                  for e in (_v.get("evidence_updates") or []) if isinstance(e, dict)}
        _unknown_ev = [u for u in _edel if u not in _exist]
        if _unknown_ev:
            _p.append(_delta_rec("delta_evidence_delete_unknown",
                                 "`evidence_delete` 里这些事实**没有**待撤销的出处修正：%s"
                                 "——撤销只删**已有修正记录**，不删主张、也不删基线出处"
                                 % _unknown_ev, "evidence_delete"))
        if _p:
            return None, _p
        for _u in _edel:
            _exist.pop(_u, None)
        for _e in _upd:
            _exist[str(_e.get("fact_uid"))] = dict(_e)
        _v["evidence_updates"] = [_exist[u] for u in
                                  [str(f.get("uid")) for f in (_v.get("frozen_facts") or [])]
                                  if u in _exist]

    # ---- 钩子绑定：给了就整块替换（形状与"必须完整"由准入层核过）----
    if isinstance(delta.get("hook_bindings"), dict):
        _v["hook_bindings"] = json.loads(json.dumps(delta["hook_bindings"]))
    return _v, []


def scope_violations(base_view: dict, candidate_view: dict, scope) -> list:
    """**本轮编辑范围**的实际差异检查（声明之外的改动一律违规）.

    `scope` 只声明**允许改**的面：`{"fact_bindings": [uid…], "beat_text": [uid…]}`。
    其余面**固定受保护**：声音单元的集合/正文、节拍的 `voice_ref`（声音归属）、
    节拍的集合与顺序、未列出的 `fact_bindings`。

    与 `view_voice_ref_dangling` / `view_voice_unit_unplaced` 的区别：那两条是**结构**判据——
    改声音只要仍闭合就能过。本函数按**授权**判：结构合法但改了声明的保护面，同样拒收。
    返回记录（`code/path/reason`），空表才算在范围内。**只读**，不改候选。
    """
    import json as _json

    def _norm(t):
        return "".join(str(t or "").split())

    _scope = scope if isinstance(scope, dict) else {}
    # 新形状（请求权限 / 动作有效权限）与旧形状（`fact_bindings` + `beat_text`）都接受：
    # 同源对象由 `plan_content_repair.build_editable_scope()` 产出，旧形状保留给既有样本。
    if "fact_bindings" in _scope or "beat_text" in _scope:
        _allow_fact = {str(x) for x in (_scope.get("fact_bindings") or [])}
        _allow_text = {str(x) for x in (_scope.get("beat_text") or [])}
        _allow_cards: set = set()
        _allow_insert: set = set()
    else:
        _allow_fact = {str(x) for x in (_scope.get("allowed_fact_uids") or [])}
        _allow_text = {str(x) for x in (_scope.get("allowed_rewrite_beat_uids") or [])}
        _allow_cards = {str(x) for x in (_scope.get("allowed_card_ids") or [])}
        _allow_insert = {str(x) for x in (_scope.get("allowed_insert_after_uids") or [])}
    _protected = tuple(_scope.get("beat_protected_fields") or
                       ("uid", "event_uids", "purpose", "evidence", "source", "voice_ref"))
    out: list = []

    def _add(code, path, reason):
        out.append({"code": code, "kind": "scope", "index": None, "path": path,
                    "reason": reason, "detail": reason, "basis": "本轮编辑范围", "fix": ""})

    # ---- 声音单元：**整个对象**受保护（集合、正文、source/说话人等全部字段）----
    def _units(view):
        d = {}
        for kind, key in (("narration", "narration_units"), ("dialogue", "dialogue_units")):
            for u in (view or {}).get(key) or []:
                if isinstance(u, dict):
                    d[str(u.get("uid") or "")] = (kind, u)
        return d

    _b, _c = _units(base_view), _units(candidate_view)
    for _uid in sorted(set(_b) | set(_c)):
        _bi, _ci = _b.get(_uid), _c.get(_uid)
        if _bi is None:
            _add("scope_voice_unit_modified", "%s_units[%s]" % (_ci[0], _uid),
                 "本轮不覆盖声音修复：不允许新增声音单元 %s（%s）" % (_uid, _ci[0]))
        elif _ci is None:
            _add("scope_voice_unit_modified", "%s_units[%s]" % (_bi[0], _uid),
                 "本轮不覆盖声音修复：不允许删除声音单元 %s（%s）" % (_uid, _bi[0]))
        elif _bi[0] != _ci[0]:
            _add("scope_voice_unit_modified", "%s_units[%s] → %s_units[%s]"
                 % (_bi[0], _uid, _ci[0], _uid),
                 "本轮不覆盖声音修复：不允许改变声音类型（%s 由 %s 变为 %s）"
                 % (_uid, _bi[0], _ci[0]))
        else:
            # 2026-10-23 复核 P1：只比"类型+正文"会漏掉 `source`（说话人）等字段——
            # 承诺冻结的是**整个声音单元**，逐字段比对并点名第一个变化的字段。
            _changed = []
            for _f in sorted(set(_bi[1]) | set(_ci[1])):
                if _json.dumps(_bi[1].get(_f), sort_keys=True) != _json.dumps(
                        _ci[1].get(_f), sort_keys=True):
                    _changed.append(_f)
            if _changed:
                _add("scope_voice_unit_modified",
                     "%s_units[%s].%s" % (_bi[0], _uid, _changed[0]),
                     "本轮不覆盖声音修复：声音单元 %s 的字段被改（%s）"
                     % (_uid, "、".join(_changed)))

    # ---- 节拍：集合/顺序、voice_ref、保护字段、正文（仅授权改写的那几条可改 text）----
    _bb = [str(x.get("uid") or "") for x in (base_view or {}).get("beats") or [] if isinstance(x, dict)]
    _cb = [str(x.get("uid") or "") for x in (candidate_view or {}).get("beats") or [] if isinstance(x, dict)]
    if _bb != _cb:
        _kept = [u for u in _cb if u in set(_bb)]
        if _kept != _bb:
            _add("scope_uneditable_beat_modified", "beats",
                 "本轮不允许删除或重排节拍：基线 %d 条、候选保留 %d 条" % (len(_bb), len(_kept)))
        else:
            _added = [u for u in _cb if u not in set(_bb)]
            _wild = "*" in _allow_insert
            _ok_insert = bool(_allow_insert) and all(
                _wild or (_cb.index(u) > 0 and _cb[_cb.index(u) - 1] in _allow_insert)
                for u in _added)
            if not _ok_insert:
                _add("scope_uneditable_beat_modified", "beats",
                     "本轮不允许在这些位置插入节拍：%s（授权锚点 %s）"
                     % (_added, sorted(_allow_insert)))
    _bmap = {str(x.get("uid") or ""): x for x in (base_view or {}).get("beats") or [] if isinstance(x, dict)}
    _cmap = {str(x.get("uid") or ""): x for x in (candidate_view or {}).get("beats") or [] if isinstance(x, dict)}
    for _uid in _bb:
        _x, _y = _bmap.get(_uid), _cmap.get(_uid)
        if not _x or not _y:
            continue
        for _f in _protected:
            if _f == "uid":
                continue
            if _json.dumps(_x.get(_f), sort_keys=True) == _json.dumps(_y.get(_f), sort_keys=True):
                continue
            if _f == "voice_ref":
                _add("scope_beat_voice_ref_modified", "beats[%s].voice_ref" % _uid,
                     "本轮不允许改声音归属：节拍 %s 的 voice_ref 变了（%r → %r）"
                     % (_uid, _x.get("voice_ref"), _y.get("voice_ref")))
            else:
                _add("scope_beat_protected_field_modified", "beats[%s].%s" % (_uid, _f),
                     "节拍 %s 的受保护字段 `%s` 被改（本轮只允许改授权节拍的 text）" % (_uid, _f))
        if _norm(_x.get("text")) != _norm(_y.get("text")) and _uid not in _allow_text:
            _add("scope_uneditable_beat_modified", "beats[%s].text" % _uid,
                 "本轮只允许改写这些节拍的正文：%s；%s 不在其中" % (sorted(_allow_text), _uid))

    # ---- 截图卡：只有授权卡片可改正文与绑定 ----
    def _cards(view):
        d = {}
        for c in (view or {}).get("screen_cards") or []:
            if isinstance(c, dict):
                d[str(c.get("card_id") or c.get("uid") or "")] = _json.dumps(c, sort_keys=True)
        return d

    _kb, _kc = _cards(base_view), _cards(candidate_view)
    for _cid in sorted(set(_kb) | set(_kc)):
        if _kb.get(_cid) != _kc.get(_cid) and _cid not in _allow_cards:
            _add("scope_card_modified", "screen_cards[%s]" % _cid,
                 "本轮只允许改这些卡片：%s；%s 不在其中" % (sorted(_allow_cards), _cid))

    # ---- 事实绑定：只有声明的条目可改 ----
    def _carriers(view):
        d = {}
        for b in (view or {}).get("fact_bindings") or []:
            if isinstance(b, dict):
                d[str(b.get("fact_uid") or "")] = _json.dumps(b.get("carriers"), sort_keys=True)
        return d

    _fb, _fc = _carriers(base_view), _carriers(candidate_view)
    for _fu in sorted(set(_fb) | set(_fc)):
        if _fb.get(_fu) == _fc.get(_fu):
            continue
        if _fu not in _allow_fact:
            _add("scope_uneditable_binding_modified", "fact_bindings[%s].carriers" % _fu,
                 "本轮只允许改这些事实的承载：%s；%s 不在其中" % (sorted(_allow_fact), _fu))
    # ---- 钩子：本轮不覆盖（整块受保护）----
    _hb = _json.dumps((base_view or {}).get("hook_bindings"), sort_keys=True)
    _hc = _json.dumps((candidate_view or {}).get("hook_bindings"), sort_keys=True)
    if _hb != _hc:
        _add("scope_uneditable_binding_modified", "hook_bindings",
             "本轮不覆盖钩子修复：hook_bindings 被改动")
    return out


def apply_delta_view(base_view: dict, response, *, mode: str | None = None,
                     accept=None, scope=None, editable_scope=None) -> tuple:
    """`plan_revision.delta.v1` 一轮应用：**严格解析 → 内存合并 → 同一条完整验收**.

    `editable_scope`（与 `scope` 同义，后者保留兼容）：本轮允许改什么。给了就在
    **accept/核验之前**先判，越权直接 `kind=illegal, stage="scope"`。

    `returns (candidate_view|None, problems, result)`——与 `apply_editable_view`
    **逐字同一个形状**（同样返回**候选视图**，由调用方交 `complete_candidate` 投影），
    只是 `result` 多一个 `protocol` 标出这一轮走的是哪套传输表示：调用方据此记账，
    判据本身不因此分叉。
    """
    _delta, _p = parse_delta_response(response, base_view=base_view,
                                      expected_rule_version=DELTA_RULE_VERSION)
    if _delta is None:
        # ⚠️ **拒收阶段必须可辨**（2026-10-11 逐笔诊断）：同一条 `kind=illegal` 可能来自
        # **响应校验器**（`delta_response_problems`）或**应用器**（`merge_delta`）——实测
        # 两轮拒收分属两个阶段，只留 `detail` 时**回放要用哪个入口只能猜**（第一轮那个
        # 校验器上重放第 3 轮响应得到空表，被误当成"复现不出"）。这里显式记 `stage`。
        return None, _p, {"kind": "illegal", "stage": "response", "mapping": {},
                          "residuals": [], "detail": _detail_of(_p),
                          "protocol": DELTA_SCHEMA}
    _merged, _mp = merge_delta(base_view, _delta)
    if _merged is None:
        return None, _mp, {"kind": "illegal", "stage": "apply", "mapping": {},
                           "residuals": [], "detail": _detail_of(_mp),
                           "protocol": DELTA_SCHEMA}
    # **确定性关系收尾**（无语义判断、可审计）：模型改 `voice_ref`/`fact_bindings`
    # 常漏掉"收掉被替换的旧单元"，导致整份响应被原子拒收。这里只做有唯一解的三件事
    # （临时 id 前缀、正文被已承载单元覆盖的孤儿、随之跟随的载体），
    # 无覆盖关系或无唯一解时**不动**，让原判据继续拒收。
    from .delta_reconcile import reconcile as _reconcile
    _merged, _notes = _reconcile(_merged, base_view=base_view, delta=_delta)
    # **本轮编辑范围先判**（2026-10-23 复核：此前放在 accept 之后，核验已经跑过才拒收）。
    # 越权属**协议/授权**问题，必须在内容核验之前判 `illegal`；结构闭合但改受保护面同样拒。
    _scope_eff = editable_scope if editable_scope is not None else scope
    if _scope_eff is not None:
        _sv = scope_violations(base_view, _merged, _scope_eff)
        if _sv:
            return None, _sv, {"kind": "illegal", "stage": "scope", "mapping": {},
                               "residuals": [], "detail": _detail_of(_sv),
                               "protocol": DELTA_SCHEMA, "reconciled": _notes}
    # 合并出来的就是一份完整视图 ⇒ 交**同一条**验收链（R2「统一验收」不许有第二条）
    # **原样透传响应声明的版本**（不 `or` 一个默认值来"制造一致"）：增量响应缺版本、
    # 或与期望不符，`parse_delta_response` 已经拒收过；这里只是把它交给**同一条**验收链
    # 再核一次（两条入口共用一份版本判据）。
    _view, _vp, _rk = apply_editable_view(
        base_view,
        {"schema": REVISION_SCHEMA,
         "rule_version": _delta.get("rule_version"),
         "base_plan_hash": _delta.get("base_plan_hash"),
         "fact_basis_hash": _delta.get("fact_basis_hash"), "view": _merged},
        mode=mode, accept=accept, expected_rule_version=DELTA_RULE_VERSION)
    _rk = dict(_rk, protocol=DELTA_SCHEMA, reconciled=_notes)
    return _view, _vp, _rk
