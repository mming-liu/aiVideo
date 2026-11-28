# -*- coding: utf-8 -*-
"""S09: `step01_script_gen.outline` 的 outline_feedback 职责（2026-11-28 拆分）.

父模块保留生成/修订编排、提示常量和缓存提交；本模块只放结构校验、
ops 应用与修订反馈。父模块当前值在调用点经 `_pv`/`_pc` 读取，
导入期不导入 orchestrator 或父模块。
"""
from __future__ import annotations

from __future__ import annotations
import json
from _text_gen import TextGenerationRequest
from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
from shared.presentation_contract import (MODE_LEGACY, DELTA_SCHEMA, REVISION_SCHEMA,
                                          PresentationContractError,
                                          apply_delta_view, plan_from_view,
                                          plan_mode, ref_uid, view_from_plan,
                                          view_digest)

_PARENT = "step01_script_gen.outline"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name):
    return _pv(name)


def _problem_identity(rec) -> str:
    """问题记录的**身份**：`code（字段路径）`（展开与否都按这一份口径给）."""
    _code = str(rec.get("code") or "无code")
    _path = str(rec.get("path") or "").strip()
    return "%s（%s）" % (_code, _path) if _path else _code

def _unexpanded_block(recs) -> list:
    """未展开问题的**身份块**（计数 + 路径）：受长度限制必须分组时，总数与身份都要给.

    复核 2026-10-02 之六 P2：旧写法 `problems[:12]` 把第 13 项之后**静默省略**——ch1
    基线 28 项里，预算超限、不可表示等**根本没下发**，模型看不到；`prompt_tokens`
    增长只证明请求变长，证明不了反馈完整。这里把"没展开"说成事实：总数、按类别计数、
    每一条的身份（超长再显式说明还剩几条）。
    """
    if not recs:
        return []
    _by_code: dict = {}
    for _r in recs:
        _c = str(_r.get("code") or "无code")
        _by_code[_c] = _by_code.get(_c, 0) + 1
    _lines = ["", "**未展开的 %d 项**（它们**同样**在本轮验收范围内，不是「没有」）——"
                  "按类别计数：%s" % (len(recs),
                                    "、".join("%s×%d" % (c, n)
                                              for c, n in sorted(_by_code.items(),
                                                                 key=lambda x: (-x[1], x[0]))))]
    _ids = [_problem_identity(_r) for _r in recs[:_pv("FEEDBACK_IDENTITY_LIMIT")]]
    _lines.append("它们的身份（code｜字段路径）：%s" % "；".join(_ids))
    if len(recs) > _pv("FEEDBACK_IDENTITY_LIMIT"):
        _lines.append("（还有 %d 项身份未列出——完整清单见诊断的 `problems` / `baseline_problems` 栏）"
                      % (len(recs) - _pv("FEEDBACK_IDENTITY_LIMIT")))
    return _lines

def _revision_feedback_block(problems, plan) -> str:
    """把验收问题**连同字段路径与判据依据**反馈给规划器（复核第 31 轮第 1、2 条）.

    2026-10-02 复核 P1：**可以改什么 / 必须保持不变**只在 `_PLAN_OPS_INSTRUCTION` 里
    写一份（与 schema 描述同源）；本函数只回答"这轮为什么被拦下"——问题清单 + 只读的
    当前计划（编号与验收同一套）。同一个契约写两遍必然漂移，而漂移的恰好是"哪些键能改、
    空值什么意思"这类**协议语义**。

    2026-10-02 复核之六 P2：**总数与未展开项的身份必须给**（旧写法 `[:12]` 静默省略）。

    R3（复核 2026-10-04 P1-3）：**回灌模型的原因不许被截断**。原型这里是
    `detail[:160] basis[:80]`——章 1 第 4 轮 22 项问题里后 10 项的完整原因根本没进请求，
    合成一条 180 字详情的反例时结尾**无省略提示地消失**。现在展开项**给完整原因**；
    仍受 `FEEDBACK_DETAIL_LIMIT` 限制的只是"展开几条"，超出部分走 `_unexpanded_block`
    （总数 + 按类别计数 + 每条身份），**身份不丢、内容有界**。
    """
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    _n = len(_recs)
    if _n > _pv("FEEDBACK_DETAIL_LIMIT"):
        _head = "未通过的项目（**共 %d 项**；下列前 %d 项给出完整原因与判据依据）：" % (
            _n, _pv("FEEDBACK_DETAIL_LIMIT"))
    else:
        _head = "未通过的项目（共 %d 项）：" % _n
    _lines = ["【本轮修订要求】上一版计划未通过统一验收。", "", _head]
    for _p in _recs[:_pv("FEEDBACK_DETAIL_LIMIT")]:
        _line = "- %s（能力：%s）｜%s｜%s" % (
            _p.get("path") or "（未定位到字段）",
            _p.get("capability") or "未登记",
            str(_p.get("reason") or _p.get("detail") or ""),
            str(_p.get("basis") or ""))
        # S06（2026-11-21）：核验员的载体提示是**未核实**的线索，不是修法指令——
        # 单独标注，避免被当成"照它改就必过"（它没有载体能力校验）。
        _hints = [str(x) for x in (_p.get("hint_carriers") or []) if str(x).strip()]
        if _hints:
            _line += "；[%s] 候选载体：%s" % (
                str(_p.get("hint_note") or "核验员提示，未核实"), "、".join(_hints))
        _lines.append(_line)
    if _n > _pv("FEEDBACK_DETAIL_LIMIT"):
        _lines += _unexpanded_block(_recs[_pv("FEEDBACK_DETAIL_LIMIT"):])
        _lines.append("本轮**能改的键**见上面的操作说明；改不动或没改完的项会在下一轮继续反馈。")
    _lines += ["", _render_plan_for_revision(plan)]
    return chr(10).join(_lines)

def _rejection_feedback_block(rejections) -> str:
    """把**仍有效的被拒收响应**（具体字段 + 原因）反馈给规划器（复核 2026-10-02 之五）.

    与 `_revision_feedback_block` **分账**且**都要给**：那里说的是"这份计划哪里不合格"
    （验收结论），这里说的是"你上一次回的东西为什么不合法"（响应违约）。只反馈前者，
    模型会重复同一个错误——实测同一次运行里两轮都回了同一个未知键 `type`。

    原因**原样取自生产者**（`apply_replan_ops` 的 `why`，已含字段路径与判据），本函数
    只排版：不解释、不归纳、不把"哪一处"换成"响应不合法"这类没有可操作信息的话。

    2026-11-21（S03）：控制器只传**最后一次成功应用之后**的拒收（`RevisionRejections`），
    并携带 `applied_rounds`。有已应用轮次时标题不再写"上一轮……一个字节没改"（那对
    第 4 轮是假的——第 2/3 轮真的改了），改为"此前的回复被拒收"并显式列出已生效的
    修订，避免模型把已应用的改动回退。完整历史仍在结果对象与诊断里，不在请求里。
    """
    _items = [x for x in (rejections or []) if isinstance(x, dict)]
    if not _items:
        return ""
    _applied = [x for x in (getattr(rejections, "applied_rounds", None) or [])
                if isinstance(x, dict)]
    if _applied:
        _head = ["", "【此前的回复被本地校验拒收：**计划一个字节都没有改**】"]
        _labels = []
        for _a in _applied:
            _label = str(_a.get("label") or _a.get("kind") or "applied")
            _labels.append("第 %s 轮（%s）" % (_a.get("round"), _label))
        _head.append("已生效的修订：%s——这些改动已经应用，不要回退；"
                     "只需处理下面被拒收的回复。" % "、".join(_labels))
    else:
        _head = ["", "【上一轮你的回复被本地校验拒收：**计划一个字节都没有改**】"]
    _lines = _head + ["被拒收的具体位置与原因（照原样引用）："]
    _shown = _items[-3:]
    if len(_items) > len(_shown):
        _lines.append("（共 %d 条，以下按轮次列出最近 %d 条。）"
                      % (len(_items), len(_shown)))
    for _x in _shown:
        # **原样给全，不再截断**（复核 2026-10-04 P1-1）。
        # 旧写法是 `[:300]`：章 1 第 3 轮的拒收原因实测 533 字（6 条问题），截到 300 就把
        # 后 3 条（`F6`/`F8`/`F9`）**无声丢掉**；随后我改成"上限 1200 + 显式标注"——
        # 那仍然是在**限制模型能看到的反馈**，而复核的要求是"下一轮请求读取**完整**问题，
        # 不继续靠抬高字符上限解决"。所以这里去掉上限：控制器存完整、展示走
        # `plan_revision.rejection_display()`（有界且标注）、**回灌模型读完整**。
        # 代价是请求体随拒收文本增长；真遇到预算问题要动的是**预算感知的渲染**，
        # 不是在这里悄悄砍掉模型需要的信息。
        _lines.append("- 第 %s 轮：%s" % (_x.get("round"), str(_x.get("detail") or "")))
    _lines.append("请按上面的原因改正后，重新给出本轮要求的**完整**回复——"
                  "不要再出现被拒收的写法，也不要因为上次被拒就什么都不改"
                  "（空操作会被记成 `no_change`，同样不采纳）。")
    return chr(10).join(_lines)

def _view_feedback_block(problems, view) -> str:
    """v2 请求的**问题清单**：逐项给**视图身份**、完整原因、所需能力与预算值（R3）.

    ⚠️ **必须用 v2 词汇**（实跑 2026-10-06 根因）：旧写法把验收记录**原样**倒进 prompt，
    而那些记录是 **v5 编号域**的——`required_facts[4].承载`、`F5`、`B4`、`narration_lines[1]`。
    这些字段在 v2 视图里**根本不存在**，而问题清单又恰好落在 prompt 的**末尾**
    （模型最后读到的位置）。实测三次响应全部回成下标占位
    （`"beats": [0,1,2,...]`）或 v2 信封 + v5 计划体的混合体。

    这里把每条记录的**字段路径与实体身份翻译到视图**（uid + 视图字段），
    但**原因原文照给**——原因里说的「该怎么修」是生产者写的，不改写。
    """
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    if not _recs:
        return ""
    _lines = ["", "【本轮未通过的项目（共 %d 条，下面**逐条完整**给出）】" % len(_recs),
              "**下列路径都是「可编辑视图」里的位置**——视图里没有 "
              "`required_facts` / `shot_beats` / `narration_lines` 这些字段名，"
              "它们已被翻译成 `fact_bindings` / `beats` / `narration_units`。"]
    for _p in _recs:
        _u = _view_identity(_p, view)
        _lines.append(
            "- %s｜视图路径：%s%s｜所需修改能力：%s｜预算/限制：%s"
            % (_p.get("code") or "无code",
               _view_path(_p, view),
               ("（实体 uid=%s）" % _u) if _u else "",
               _p.get("capability") or "（未登记）",
               json.dumps(_p.get("limits") or {}, ensure_ascii=False)
               if _p.get("limits") else "（无）"))
        _lines.append("  原因：%s" % str(_p.get("reason") or ""))
        # S06（2026-11-21 D4）：核验员的载体候选是**未核实提示**，不是修法指令——
        # 单独标注（核验员没有载体能力校验，不能当"照它改必过"的依据）。
        _hints = [str(x) for x in (_p.get("hint_carriers") or []) if str(x).strip()]
        if _hints:
            _lines.append("  提示：[%s] 候选载体：%s"
                          % (str(_p.get("hint_note") or "核验员提示，未核实"),
                             "、".join(_hints)))
        if _p.get("basis"):
            _lines.append("  判据：%s" % _p["basis"])
        if _p.get("fix"):
            _lines.append("  修正出口：%s" % _p["fix"])
    _lines += ["",
               "**注意**：上面说的是「这份视图哪里不合格」，"
               "要改的字段只有这些：`beats` / `dialogue_units` / `narration_units` / "
               "`screen_cards` / `fact_bindings` / `hook_bindings` / `evidence_updates`。"]
    return chr(10).join(_lines)

def _structure_feedback_block(problems) -> str:
    """首版被**结构校验**拒收 ⇒ 下一轮请求里的改正要求（复核 2026-10-02 之五）.

    这是"回灌"的落地处：**具体字段与原因**来自 `outline_structure_problems` 的记录
    （生产者给），不是"结构校验未通过"这类没有可操作信息的话。要求是**重新给完整计划**，
    不是操作——不安全的首版对象**不许**进操作应用器（复核 2026-10-02 之五）。

    与验收反馈同一条口径（复核之六 P2）：展开列不完时**总数与身份都要给**。
    """
    _recs = [x for x in (problems or []) if isinstance(x, dict)]
    if not _recs:
        return ""
    _lines = ["", "【上一版计划被本地结构校验拒收：**没有被采纳、没有写缓存**】",
              "被拒收的具体位置与原因（**共 %d 项**；下列前 %d 项，照原样引用）："
              % (len(_recs), min(len(_recs), _pv("FEEDBACK_DETAIL_LIMIT")))]
    for _p in _recs[:_pv("FEEDBACK_DETAIL_LIMIT")]:
        _lines.append("- %s｜%s" % (str(_p.get("path") or _p.get("kind") or "（未定位）"),
                                    str(_p.get("reason") or _p.get("detail") or "")[:240]))
    if len(_recs) > _pv("FEEDBACK_DETAIL_LIMIT"):
        _lines += _unexpanded_block(_recs[_pv("FEEDBACK_DETAIL_LIMIT"):])
    _lines.append("请**重新给出完整计划**（不是操作），上面每一处都按说明改正："
                  "该给字符串的字段不要给对象/数组，类型必须与约定的 JSON 形状一致。")
    return chr(10).join(_lines)

def _allowed_fields_block(allowed_fields) -> str:
    """通道字段白名单**写进请求**（S14-04，2026-11-22）.

    应用侧 `revision_route.delta_field_violations` 一直按白名单整轮拒收，但请求里从没说过
    （cmp ch2/legacy 实测：模型回了 `evidence_updates` ⇒ `route_field_violation`）。
    请求与应用读**同一份** `allowed_fields`；`None` = 本轮不限字段，不输出。
    """
    if allowed_fields is None:
        return ""
    _ok = sorted(str(x) for x in allowed_fields)
    return ("# 本轮通道只允许改这些字段：%s\n"
            "增量里**只能出现**上面这些键（`upsert`/`delete` 里同样只针对它们）；其它键——"
            "例如 `evidence_updates`、`screen_cards`——本轮一律不要出现，出现即整轮拒收。"
            "来源证据类问题留到承载闭合后的单独轮次处理。" % "、".join(_ok))

def render_protected_fields_rule() -> str:
    """渲染「必须逐字保持不变」的字段清单（与守卫**同一份口径**，见 `replan_protected_fields`）."""
    _prot = list(_pc("replan_protected_fields")())
    _carrier = [_f for _f in _prot if _f in _pv("_CARRIER_REGISTRATION_FIELDS")]
    _plain = [_f for _f in _prot if _f not in _pv("_CARRIER_REGISTRATION_FIELDS")]
    _lines = []
    if _plain:
        _lines.append("- %s——取值**一字不许改**；"
                      % "、".join("`%s`" % _f for _f in _plain))
    if _carrier:
        # **条数也要说**（复核之十二 P2）：守卫比的是**整张表**（逐项 + 条数），旧措辞只说
        # "正文与依据"，而模型最常做的修补恰恰是"补一条承载某句的节拍 / 把一条拆成两条"
        # ——实测 3 轮都因此被 `core_field_changed: shot_beats` 拒收。规则说清"表不能变长
        # 变短"，模型才有机会改走承载登记或改选材。
        _lines.append("- %s 的**正文、「原文依据/原文证据」与条数**——末尾的「｜承载：…」"
                      "「｜载体：…」**登记**可以跟着改（**不许新增/删除/拆分/合并条目**）。"
                      % "、".join("`%s`" % _f for _f in _carrier))
    return chr(10).join(_lines)

def render_protected_fields_inline() -> str:
    """同上一份清单的**单行**写法（预算重选的反馈块用）；字段来源完全相同."""
    _prot = list(_pc("replan_protected_fields")())
    _carrier = [_f for _f in _prot if _f in _pv("_CARRIER_REGISTRATION_FIELDS")]
    _plain = [_f for _f in _prot if _f not in _pv("_CARRIER_REGISTRATION_FIELDS")]
    _txt = "、".join("`%s`" % _f for _f in _plain)
    if _carrier:
        _txt += ("；%s 的**正文、「原文依据/原文证据」与条数**（末尾承载登记可以跟着改，"
                 "条目不许增删）"
                 % "、".join("`%s`" % _f for _f in _carrier))
    return _txt

def render_plan_baseline(plan) -> str:
    """当前计划的**逐字基线**（完整计划修订 / 预算重选的请求都带它）.

    复核之十一 P1：规则要求"逐字保留"，就必须让模型**看得见**要保留的原值——
    "照抄旧值"是这一轮唯一被允许的省事做法，请求里少了它，模型只能靠猜。
    """
    return chr(10).join([
        "", "【当前计划：**逐字基线**（下面就按这份回，别改 `必须逐字保持不变` 的字段）】",
        "```json",
        json.dumps(plan, ensure_ascii=False, indent=2),
        "```"])

def render_full_plan_rule() -> str:
    """渲染完整计划修订的规则块（**单一渲染入口**）：可改/必须保留的字段各只有一份来源.

    请求与判别性用例都调它——"什么能动、什么不能动"只有一个来源
    （`replan_protected_fields()` / `_REPLAN_FREE_FIELDS`），改一处两边同时变
    （写两份必然漂移，而漂移的恰好是这条协议语义）。复核之十一 P1 之前，这份规则
    手写死了一份 5 个键的"必须逐字保持不变"清单，而守卫比对的是另一份——两边对不上。
    """
    return _pv("_FULL_PLAN_REVISION_RULE").replace(
        "__PROTECTED__", render_protected_fields_rule()).replace(
        "__FREE__",
        "、".join("`%s`" % _f for _f in _pv("_REPLAN_FREE_FIELDS"))
        + "、`hook_spoken`、`hook_carrier`")

def _render_plan_for_revision(plan) -> str:
    """给规划器看的**当前计划**（编号与验收用的**同一套** B/D/N/F）.

    复核第 32 轮 P1：「实际修订请求没有携带当前计划，模型无法可靠对应 B/D/F 编号」。
    """
    _l = ["【当前计划（已规范化；下面的编号就是验收用的编号）】"]
    for _i, _b in enumerate(plan.get("shot_beats") or [], 1):
        _l.append("B%d %s" % (_i, _b))
    for _i, _t in enumerate(plan.get("dialogue_lines") or [], 1):
        _l.append("D%d %s" % (_i, _t))
    for _i, _e in enumerate(plan.get("narration_lines") or [], 1):
        if isinstance(_e, dict):
            _l.append("N%d %s（出处：%s；承载事实：%s）"
                      % (_i, _e.get("text"), _e.get("source"), _e.get("fact_refs")))
    for _i, _f in enumerate(plan.get("required_facts") or [], 1):
        _l.append("F%d %s" % (_i, _f))
    return chr(10).join(_l)

def _full_plan_response_problem(raw) -> str:
    """**完整计划响应**先过的两道门（单一来源：两个"回一整份计划"的入口共用）；"" = 通过.

    ① **完整性**（复核之十实跑暴露）：这一轮要的是**完整计划**（首版那份 JSON 契约），
       模型只回操作/部分字段时必须**如实这么说**——旧写法把这种响应交给核心守卫，
       拒收原因变成"改动了核心字段「causal_chain」"，模型据此改不动，归因也指向错原因；
    ② **结构诊断**（与首版同一条纯诊断）：给出**具体到字段/项号**的原因，而不是一句
       "结果不合法"——这条原因会进下一轮请求的拒收反馈。

    复核之十二 P1：预算重选的整份重写分支此前**没有**这两道门（`_validate_outline`
    拒收后只说一句 `invalid_result`）。接口裁决修好之后，混合问题（预算超限 × 单镜
    不可表示）也会走这条分支，两个"完整计划"入口必须是**同一条判据**。
    """
    if not isinstance(raw, dict):
        return "invalid_result: JSON 解析失败"
    _missing = [k for k in _pc("_plan_required_fields")() if k not in raw]
    if _missing:
        _head = "、".join(_missing[:6]) + ("…" if len(_missing) > 6 else "")
        return ("invalid_result: 这轮要的是**完整计划**（首版那份 JSON 契约），"
                "响应里缺 %d 个顶层字段（%s）——请回**整份计划**，不是只回操作"
                % (len(_missing), _head))
    _bad = _pc("outline_structure_problems")(raw)
    if _bad:
        return "invalid_result: %s" % _pc("_structure_detail")(_bad)
    return ""

def _view_identity(rec, view) -> str:
    """问题记录 → **视图里的稳定身份**（R3：记录自带 uid，不从文案猜）.

    验收链仍按 v5 编号域产记录（`kind` + 1 起的 `index`），而 v2 视图用 uid。
    这里做**一次**映射：`(kind, index)` → 该实体的 uid。
    映射不出来就**明说**未定位，不编一个 uid 出来。
    """
    _given_uid = (rec or {}).get("uid")
    if isinstance(_given_uid, str) and _given_uid:
        return _given_uid
    _kind = str((rec or {}).get("kind") or "")
    _idx = (rec or {}).get("index")
    if not isinstance(_idx, int) or _idx < 1:
        return ""
    _pool = {"fact": view.get("frozen_facts"), "beat": view.get("beats"),
             "dialogue": view.get("dialogue_units"),
             "narration": view.get("narration_units"),
             "card": view.get("screen_cards")}.get(_kind) or []
    if _idx > len(_pool):
        return ""
    return ref_uid(_pool[_idx - 1])

def _view_path(rec, view) -> str:
    """问题记录的字段路径 → **v2 视图路径**（v5 前缀换掉；换不掉的如实标注）."""
    _raw = str((rec or {}).get("path") or "")
    if not _raw:
        _u = _view_identity(rec, view)
        return ("uid=%s" % _u) if _u else "（未定位到字段）"
    for _v5, _v2 in _pv("_V5_PATH_TO_VIEW").items():
        if not _raw.startswith(_v5):
            continue
        _tail = _raw[_raw.index("]") + 1:] if "]" in _raw else ""
        _u = _view_identity(rec, view)
        _tail = _pv("_V5_VIEW_FIELD_TAILS").get(_v5, {}).get(_tail, _tail)
        if "]" in _raw:
            try:
                _u = _view_identity(
                    dict(rec, index=int(_raw[len(_v5):_raw.index("]")]) + 1), view)
            except (TypeError, ValueError):
                pass  # Stable producer uid survives a non-numeric path bracket.
        return "%s%s]%s" % (_v2, ("uid=%s" % _u) if _u else "?",
                            _tail or "")
    # 换不掉的（`hook_carrier` / `time_budget` 之类）——**如实说它在视图里的位置**
    _map = {"hook_carrier": "hook_bindings",
            "hook_spoken": "hook_bindings.spoken_uids",
            "time_budget": "plan_basis.time_budget",
            "omittable": "plan_basis.omittable",
            "presentation.screen_cards": "screen_cards"}
    return _map.get(_raw, _raw)

def _view_response_shape(data, protocol: str) -> dict:
    """视图两协议这一轮**原始响应**的实际形状（归因第三项）.

    `expected` 由**实际分派**的协议给（它知道这一轮要的是什么）；`shape` 按响应里
    **实际出现的键**判——**不许**从期望值倒填。复核 2026-10-06（第十轮）P2：旧写法两栏
    都硬写成 `"view"`，走增量的六份记录全部冒充 v2。
    """
    _expect = DELTA_SCHEMA if str(protocol) == _pv("ROUTE_VIEW_DELTA") else REVISION_SCHEMA
    if not isinstance(data, dict):
        return {"shape": "unparsed", "expected": _expect}
    # 形状取**响应自报的 `schema`**（它自己声明走哪套协议），不按"有没有改动块"猜——
    # 只补出处的增量响应一个改动块都没有，按键猜会把它记成 `view`。
    _decl = str(data.get("schema") or "")
    _shape = ("delta" if _decl == DELTA_SCHEMA
              else "view" if _decl == REVISION_SCHEMA else "unknown")
    _out = {"shape": _shape, "declared_schema": _decl,
            "expected": _expect, "keys": sorted(data)}
    if _shape == "delta":
        _out["changes"] = {k: (len(data[k]) if isinstance(data[k], list) else 1)
                           for k in ("upsert", "delete", "order", "fact_bindings",
                                     "hook_bindings", "evidence_updates",
                                     "evidence_delete") if k in data}
    _v = data.get("view")
    _out["view_digest"] = view_digest(_v) if isinstance(_v, dict) else ""
    return _out

def _response_summary(data, plan, expect: str = "ops") -> dict:
    """这一轮**原始响应**实际带了什么（归因第三项）：形状 + 键/条数 或 与上一版不同的字段.

    不看文案、不猜意图——只记"它回了什么形状、动了哪些字段"。`no_change` 的归因要靠它
    区分「回填了原声明（形状是 ops、但结果与输入一致）」与「什么都没回（空操作）」。

    `expect`（复核之十实跑暴露）：**形态由调用方给**（它知道这一轮要的是什么），
    **不许从响应内容猜**——完整计划里本来就带 `hook_spoken`/`narration_lines` 这些键，
    按内容猜会把"完整计划"误记成 `ops`（实跑里三个完整计划轮次的证据全记错了，
    归因会被这条错误证据带偏）。
    """
    if not isinstance(data, dict):
        return {"shape": "unparsed", "expected": expect}
    if expect == "ops":
        from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS as _OK
        _op_keys = [k for k in sorted(data) if k in _OK]
        return {"shape": "ops", "expected": "ops",
                "keys": {k: (len(data[k]) if isinstance(data[k], list) else 1)
                         for k in _op_keys}}
    _missing = [k for k in _pc("_plan_required_fields")() if k not in data]
    # `plan_contract_version` 是**代码自己盖的**版本戳（`_validate_outline` 统一写）——
    # 原始响应里本来就不会有它，拿它当"改过的字段"是**假读数**（证据要如实：
    # 归因时不能把代码的盖章算成模型改了什么）。
    _stamped = {"plan_contract_version"}
    return {"shape": "full_plan", "expected": "full_plan",
            "missing_fields": _missing,
            "changed_fields": [k for k in sorted(set(plan or {}) | set(data))
                               if k not in _stamped
                               and (plan or {}).get(k) != data.get(k)]}

def _canonical_view_map(view) -> tuple:
    """视图 → 投影再读回的规范视图 + 旧身份到新身份的映射 `(view_n, uid_map, problems)`.

    映射只来自**同一节拍序列的逐位配对**：节拍 i ↔ 节拍 i，节拍 i 的声音引用 ↔ 读回后
    节拍 i 的声音引用。节拍数或某位的载体种类不一致即判失败，不做相似度匹配。
    """
    try:
        _plan = plan_from_view(view)
    except PresentationContractError as _exc:
        return None, {}, list(_exc.problems)
    _vn, _vp = view_from_plan(_plan)
    if _vn is None or _vp:
        return None, {}, list(_vp or [])
    _a = [b for b in (view or {}).get("beats") or [] if isinstance(b, dict)]
    _b = [b for b in (_vn or {}).get("beats") or [] if isinstance(b, dict)]
    if len(_a) != len(_b):
        return None, {}, [{"code": "canonical_beat_count_mismatch",
                           "reason": "读回后节拍数变化（%d → %d）" % (len(_a), len(_b))}]
    _map: dict = {}
    for _x, _y in zip(_a, _b):
        _map[str(_x.get("uid") or "")] = str(_y.get("uid") or "")
        _rx, _ry = _x.get("voice_ref") or {}, _y.get("voice_ref") or {}
        if str(_rx.get("kind") or "") != str(_ry.get("kind") or ""):
            return None, {}, [{"code": "canonical_voice_kind_mismatch",
                               "reason": "节拍 %s 读回后声音种类变化" % _x.get("uid")}]
        if str(_rx.get("kind") or "") in ("dialogue", "narration"):
            _ox, _oy = str(ref_uid(_rx) or ""), str(ref_uid(_ry) or "")
            if _ox in _map and _map[_ox] != _oy:
                return None, {}, [{"code": "canonical_voice_identity_conflict",
                                   "reason": "声音 %s 读回后对应两个身份" % _ox}]
            _map[_ox] = _oy
    return _vn, _map, []

def _replan_by_ops(plan, problems, novel_text, *, model=None, base_prompt=None,
                   system_prompt=None, timeout_sec=None,
                   deadline_monotonic=None, rejections=None,
                   evidence: dict | None = None,
                   response=None) -> tuple:
    """契约/承载类问题：操作式反馈（模型只回操作，代码在旧计划上应用）.

    `evidence`（复核之九 交付证据要求）：**可选出参**——把这一轮的归因证据
    （请求信息 / 原始响应形状 / 应用结果）原样填进去，供轮次档留档。不改返回元数，
    免得所有调用点跟着改。

    `response`：**冻结响应回放**用——给了它就**不调模型**，直接把这个历史响应交给
    应用器（`apply_replan_ops`）。生产链路上永远为 `None`；那是"旧协议仅兼容回放"的
    具体落点。
    """
    from shared.config import PROMPT_MAX_CHARS
    # ---- **冻结响应回放**：给了 `response` 就**不调模型**，直接交给应用器 ----
    if response is not None:
        _data = response if isinstance(response, dict) else _pc("_parse_outline_json")(response)
        if not isinstance(_data, dict):
            return None, "invalid_result: 回放响应不是可解析的 JSON 对象", ""
        if evidence is not None:
            evidence["response"] = dict(_response_summary(_data, plan, expect="ops"),
                                        replayed=True)
        _new, _why = _pc("apply_replan_ops")(plan, _data, novel_text)
        if evidence is not None:
            evidence["applied"] = _new is not None
        return _new, _why, ""
    # 复核 2026-10-02 P1：操作式请求里**不能**带「输出整份大纲」的 `OUTLINE_PROMPT`
    # （它的「# 输出格式」列着整份 JSON 字段表），否则与"只回操作"自相矛盾——模型按
    # "整份大纲"的理解把 schema 必填的声明键填成空值，就会静默清掉合法声明。
    # 操作说明**单源**在 `_PLAN_OPS_INSTRUCTION`（与预算重选的操作分支共用同一份）。
    _base = base_prompt or _pv("_REVISION_NOVEL_PROMPT").format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    # **本集编号上限必须随请求说清**（2026-11-06 实跑：模型写了「旁白4」，而本集只有
    # 3 条旁白 ⇒ 整轮拒收，两轮重选额度直接耗尽）。编号是**本轮的**计划，不是固定值。
    _n_d = len([x for x in (plan or {}).get("dialogue_lines") or [] if x])
    _n_n = len([x for x in (plan or {}).get("narration_lines") or [] if x])
    _bounds = ("**本集编号上限**（载体只能引用这些编号，越界整批拒收）："
               "台词 1~%d（共 %d 条）、旁白 1~%d（共 %d 条）。"
               % (_n_d, _n_d, _n_n, _n_n))
    _prompt = chr(10).join([_base, _pv("_PLAN_OPS_INSTRUCTION"), _bounds,
                            _revision_feedback_block(problems, plan),
                            _rejection_feedback_block(rejections)])
    try:
        service = _pc("create_service")()
        result = service.generate(TextGenerationRequest(
            system_prompt=system_prompt or _pv("_OUTLINE_SYSTEM_PROMPT"),
            user_prompt=_prompt, max_tokens=_pc("_outline_max_tokens")(), temperature=0.3,
            json_schema=REPLAN_OPS_SCHEMA, model=model, timeout_sec=timeout_sec,
            deadline_monotonic=deadline_monotonic))
    except Exception as exc:                       # noqa: BLE001
        print("[WARN] 计划修订调用异常: %s" % exc)
        from shared.call_ledger import call_id_of
        return None, "call_failed: %s" % exc, call_id_of(exc)
    # 这一次调用的唯一标识（完整正文已按它落盘；修订轮据此关联证据）
    _call_id = str(getattr(result, "call_id", "") or "")
    if not result.success:
        print("[WARN] 计划修订调用失败: %s" % result.error)
        return None, "call_failed: %s" % result.error, _call_id
    data = _pc("_parse_outline_json")(result.text)
    if evidence is not None:
        evidence["response"] = _response_summary(data, plan, expect="ops")
    if not isinstance(data, dict):
        return None, "invalid_result: JSON 解析失败", _call_id
    _new, _why = _pc("apply_replan_ops")(plan, data, novel_text)
    if evidence is not None:
        evidence["applied"] = _new is not None
        evidence["applied_ops"] = bool(_new is not None and _why in ("", "no_change"))
    return _new, _why, _call_id

def _replan_by_slots(plan, problems, novel_text, budget, rejections=None, *, model=None,
                     system_prompt=None, evidence=None, verification=None,
                     ctx=None, verify_axes=None, target_fact_uids=None,
                     allow_create=True, allowed_repair_actions=None) -> tuple:
    """**内容槽位一轮修订**：模型只选内容与关系 → 代码装配既有 delta → 同一条验收链.

    与 `_replan_by_view` **同一份返回形状**（`(new_plan|None, why, call_id, evidence)`），
    所以 `revise_plan_once` 的三态分派不分叉。差别只在**响应契约**：这里要的是"逐槽的
    内容与关系选择"，编号 / `order` / 哈希 / 实体表全由代码装配——实测里模型在增量协议上
    反复写下的**数字占位**（`"beat_events": [1, 1, 1, …]`、`upsert.beats[0]` 是 `int`）
    正是"要求它重抄实体表"造成的。

    一次槽位调用 = **一次额度**（`budget` 由控制器在轮首扣过，这里**不重发**：坏回答原样
    拒收并回灌下一轮，不用"槽位失败 + delta 重发"冒充一次）。
    """
    from shared.config import PROMPT_MAX_CHARS
    from step01_script_gen import plan_content_repair as _pcr
    _ctx = ctx or {}
    _base_view = _ctx.get("view")
    _found = _ctx.get("found")
    _mode = str(_ctx.get("mode") or plan_mode(plan) or MODE_LEGACY)
    if _base_view is None or not _found:
        _ok, _why, _ctx2 = _pc("_content_slots_applicable")(
            plan, problems, mode=_mode, allow_create=allow_create,
            target_fact_uids=target_fact_uids, verification=verification,
            novel_text=novel_text,
            allowed_repair_actions=allowed_repair_actions)
        if not _ok or not (_ctx2.get("found") or {}).get("slots"):
            return None, "内容槽位不适用：%s" % (_why or _ctx2.get("state")), "", \
                (evidence or {})
        _base_view, _found, _mode = _ctx2["view"], _ctx2["found"], _ctx2["mode"]
    _slots = _found["slots"]
    if evidence is not None:
        evidence["route"] = "content_slots"
        evidence["slots"] = [{"slot_id": _s["slot_id"], "kind": _s["kind"],
                              "actions": list(_s["allowed_actions"]),
                              "targets": len(_s["target_uids"])} for _s in _slots]
        evidence["unexpressible"] = list(_found["unexpressible"])
    _limits = {"mode": _mode, "rule_version": _pcr.REPAIR_RULE_VERSION,
               "max_calls": getattr(budget, "max_calls", None),
               "calls_left": getattr(budget, "calls_left", None),
               "deadline_at": getattr(budget, "deadline_at", None)}
    _req = _pcr.slot_request(_base_view, _slots, limits=_limits,
                             remaining_calls=getattr(budget, "calls_left", None),
                             remaining_sec=budget.remaining_sec() if budget is not None
                             else None)
    _prompt = chr(10).join([
        _pv("_REVISION_NOVEL_PROMPT").format(novel_text=novel_text[:PROMPT_MAX_CHARS]),
        _view_feedback_block(problems, _base_view),
        _rejection_feedback_block(rejections),
        # **形状规格最后说**（槽位表 + 选择规则）——请求里已经带了允许集合
        _pcr.slot_prompt(_req),
        "请现在逐槽给出选择；不要返回示例或空回答。",
    ])
    _sys = system_prompt or _pv("_OUTLINE_SYSTEM_PROMPT")
    _timeout = budget.remaining_sec() if budget is not None else None
    # ⚠️ 不写死 `max_tokens`（同 `_replan_by_view`）：它按项目约定归 `.env`
    _res = _pc("create_service")().generate(TextGenerationRequest(
        system_prompt=_sys, user_prompt=_prompt, temperature=0.3,
        json_schema=_pcr.answer_json_schema(_slots), model=model,
        deadline_monotonic=getattr(budget, "deadline_at", None), timeout_sec=_timeout))
    _cid = str(getattr(_res, "call_id", "") or "")
    if not _res.success:
        return None, "call_failed: %s" % _res.error, _cid, (evidence or {})
    _data = _pc("_parse_outline_json")(_res.text)
    if not isinstance(_data, dict):
        if evidence is not None:
            evidence['failure_stage']='answer_parsing'
        return None, "槽位回答不是可解析的 JSON 对象", _cid, (evidence or {})
    _data_raw = _data
    _data, _normalization, _nprob = _pcr.normalize_slot_answer(_data, _slots)
    if evidence is not None:
        evidence["raw_response_text"] = str(getattr(_res, "text", "") or "")
        evidence["slot_answer_raw"] = _data_raw
        evidence["slot_answer_normalized"] = _data
        evidence["normalization_records"] = _normalization
        evidence["normalization_version"] = _pcr.NORMALIZATION_VERSION
        evidence['applied']=False
        _ans = _data.get("answers")
        evidence["response"] = {
            "shape": "slots" if str(_data.get("schema") or "") == _pcr.SLOT_ANSWER_SCHEMA
                     else "unknown",
            "declared_schema": str(_data.get("schema") or ""),
            "expected": _pcr.SLOT_ANSWER_SCHEMA, "keys": sorted(_data),
            "answers": (len(_ans) if isinstance(_ans, list) else 1)}
    if _nprob:
        if evidence is not None:
            evidence['failure_stage']='answer_normalization'
            evidence["slots_normalization_problems"] = [
                str((x or {}).get("code")) for x in _nprob]
        return None, _detail_vp(_nprob), _cid, (evidence or {})
    _pb = _pcr.answer_problems(_data, _slots, where="槽位回答")
    if _pb:
        if evidence is not None:
            evidence['failure_stage']='answer_contract'
            evidence["slots_answer_problems"] = [str((x or {}).get("code")) for x in _pb]
        return None, _detail_vp(_pb), _cid, (evidence or {})
    # 新建画面的「原文依据」由代码从**已准入的核验记录**里取（逐字出自原文），
    # 不让模型抄原文：冻结事实自带的 `evidence` 不保证逐字（源轴允许引用别的片段）。
    _ev_by_fact = _pcr.source_spans_by_fact(
        _ctx.get("verification_record")
        or _verification_record_for(verification, plan) or {})
    # **动作有效权限**：请求权限 ∩ 回答动作授权（`bind_visual` 不改正文；
    # `rewrite_visual` 只改所选 target 的正文）。与请求里那份 `editable_scope` 同源。
    _scope_eff = _pcr.build_editable_scope(_base_view, _slots, answer=_data)
    if evidence is not None:
        evidence["editable_scope"] = {
            "phase": _scope_eff["phase"],
            "facts": _scope_eff["allowed_fact_uids"],
            "rewrite": _scope_eff["allowed_rewrite_beat_uids"],
            "bind": _scope_eff["allowed_bind_beat_uids"]}
        evidence["revision_outcome"] = {
            "protocol_ok": None, "content_repaired": None,
            "protection_intact": None, "final_accepted": None,
            "source": "slots 编译与统一验收（未核验前 content/final 不填 true）"}
    _delta, _cp = _pcr.compile_delta(_base_view, _slots, _data, novel_text=novel_text,
                                     evidence_by_fact=_ev_by_fact)
    if _delta is None:
        if evidence is not None:
            evidence['failure_stage']='slot_compilation'
            evidence["slots_compile_problems"] = [str((x or {}).get("code")) for x in _cp]
        return None, _detail_vp(_cp), _cid, (evidence or {})
    if evidence is not None:
        evidence["compiled_delta"] = {
            "upsert": {_k: len(_v) for _k, _v in (_delta.get("upsert") or {}).items()},
            "order": len((_delta.get("order") or {}).get("beats") or []),
            "fact_bindings": len(_delta.get("fact_bindings") or [])}
    _accept = lambda _v: _accept_view_candidate(_v, novel_text, verification, verify_axes=verify_axes)
    _cand_view, _probs, _res_k = apply_delta_view(_base_view, _delta, mode=_mode,
                                                  accept=_accept,
                                                  editable_scope=_scope_eff)
    _kind = _res_k["kind"]
    if _kind == "no_change":
        return None, "no_change", _cid, evidence
    if _kind == "illegal":
        if evidence is not None:
            _stage = str(_res_k.get("stage") or "")
            evidence['failure_stage'] = ('scope_guard' if _stage == 'scope'
                                          else 'delta_application')
            evidence["revision_outcome"] = {
                "protocol_ok": True,
                "content_repaired": None,
                "protection_intact": (False if _stage == "scope" else None),
                "protection_structural": (False if _stage == "scope" else None),
                "final_accepted": False,
                "candidate_digest": None,
                "source": ("范围守卫（accept 之前）" if _stage == "scope"
                           else "delta 应用/统一验收")}
        return None, _detail_vp(_probs) or "编译出的增量被拒收（形状/基线/引用/保护边界）",             _cid, evidence
    _new, _final_problems = complete_candidate(_cand_view, novel_text, verification, verify_axes=verify_axes)
    if _new is None:
        if evidence is not None:
            evidence['failure_stage']='candidate_projection'
            evidence["revision_outcome"] = {
                "protocol_ok": True, "content_repaired": None,
                "protection_intact": True, "final_accepted": False,
                "source": "候选投影失败（字段守卫已通过）"}
        return None, "候选无法投影成计划：%s" % _detail_vp(_final_problems), _cid,             (evidence or {})
    _new["plan_revision"] = {
        "schema": _pcr.SLOT_REQUEST_SCHEMA, "transport_protocol": _pcr.SLOT_REQUEST_SCHEMA,
        "rule_version": _pcr.REPAIR_RULE_VERSION,
        "view_schema": REVISION_SCHEMA, "view_rule_version": _pv("PLAN_VIEW_RULE_VERSION"),
        "mapping": _res_k["mapping"], "residuals": list(_final_problems), "call_id": _cid,
        "slots": [str(_s["slot_id"]) for _s in _slots]}
    if evidence is not None:
        _codes = [str((x or {}).get("code") or "") for x in (_final_problems or [])]
        _fact_targets = {
            str(_s.get("fact_uid")) for _s in _slots
            if _s.get("kind") not in ("card_text", "voice_kind", "voice_text")
            and str(_s.get("fact_uid")) not in ("", "None")}
        _affected = {
            str(_f) for _s in _slots
            for _f in (_s.get("affected_facts") or [])
            if str(_f) not in ("", "None")}
        _voice_targets = {
            str(_s.get("unit_uid")) for _s in _slots
            if _s.get("kind") in ("voice_kind", "voice_text")
            and str(_s.get("unit_uid")) not in ("", "None")}
        _mapping = _res_k.get("mapping") or {}
        _voice_targets, _voice_unresolved = _voice_targets_from_slots(
            _slots, _mapping)
        _co_affected = bool(_scope_eff.get("co_carried_facts_by_target")) \
            or bool(_scope_eff.get("protected_hook"))
        _sem_ok = None
        _rec_for_sem = None
        if verification is not None:
            _rec_for_sem = _new.get("plan_verification")
            if isinstance(_rec_for_sem, dict):
                from shared.content_verification import (plan_digest_of,
                                                        record_protocol_problems)
                _sem_probs = record_protocol_problems(
                    _rec_for_sem, _cand_view,
                    plan_digest=plan_digest_of(_new), novel_text=novel_text,
                    novel_digest=str(getattr(verification, "novel_digest", "") or ""))
                _sem_ok = not _sem_probs
            else:
                _sem_ok = False
        evidence["applied"] = True
        evidence["residuals"] = len(_final_problems)
        evidence["mapping"] = _res_k["mapping"]
        evidence["semantic_evidence_ok"] = _sem_ok
        evidence["verification_record"] = _rec_for_sem
        evidence["revision_outcome"] = _slots_revision_outcome(
            _final_problems, target_fact_uids=_fact_targets,
            target_voice_uids=_voice_targets,
            affected_fact_uids=_affected, co_carried=_co_affected,
            voice_identity_unresolved=_voice_unresolved,
            verification_evidence_ok=_sem_ok,
            candidate=_new)
    return _new, "slots_applied: %d 条残余" % len(_final_problems), _cid, evidence

def _content_slots_applicable(plan, problems, *, mode=None, allow_create=True,
                              target_fact_uids=None, verification=None,
                              novel_text: str = "",
                              allowed_repair_actions=None,
                              allow_split=False, split_target_uids=None) -> tuple:
    """本轮问题**能不能被内容槽位全部表达**（§5.6 第 4 条：能表达才接入）.

    `returns (ok, why, ctx)`：`ok=False` 时调用方**整轮落回既有 delta**——判据只有
    `plan_content_repair.content_slots` 一份（它按稳定 UID 归并，表达不了的 code 原样列出）。
    `ctx` 带上已经读出来的视图与槽位，`_replan_by_slots` 直接复用，不重算一遍。

    ⚠️ **`ctx["state"]` 是分派依据，`why` 只是给人看的文案**（复核 2026-10-08 P2 第 5 条）：
    旧写法只有"适用 / 不适用"两态，于是**上游记录把目标身份写错**（卡片 `cZZ` 不在基线里）
    与**这个修法槽位表达不了**走同一条出口——前者本该**零调用**，实测却照落回 delta 发了
    一次 POST。现在两种出口分别是 `STATE_UNKNOWN_TARGET`（`invalid_target`，零调用）与
    `STATE_UNSUPPORTED`（保留备用路，一次额度）。
    """
    from step01_script_gen import plan_content_repair as _pcr
    if not problems:
        return False, "没有生产者记录（无从证明表达得了）", \
            {"state": _pcr.STATE_NO_SLOTS, "view": None, "found": None,
             "mode": None, "blocked": []}
    _mode = str(mode or plan_mode(plan) or MODE_LEGACY)
    _base_view, _vp = view_from_plan(plan, mode=_mode)
    if _base_view is None:
        return False, "基线读不成可编辑视图：%s" % _detail_vp(_vp), \
            {"state": _pcr.STATE_UNREADABLE, "view": None, "found": None,
             "mode": _mode, "blocked": []}
    _rec = _verification_record_for(verification, plan)
    _admitted = False
    if isinstance(_rec, dict):
        from shared.content_verification import (plan_digest_of,
                                                record_protocol_problems)
        _rule = record_protocol_problems(
            _rec, _base_view, plan_digest=plan_digest_of(plan),
            novel_text=novel_text,
            novel_digest=str(getattr(verification, "novel_digest", "") or ""))
        _admitted = not _rule
    if allow_split:
        from shared import fact_split as _fs
        _targets = [str(x) for x in (split_target_uids or []) if str(x)]
        if len(_targets) != 1:
            return False, "拆分必须显式给出恰好一个 target_fact_uids（当前 %r）" \
                % (_targets,), {"state": _pcr.STATE_UNKNOWN_TARGET,
                                "view": _base_view, "found": None,
                                "mode": _mode, "blocked": []}
        if verification is None or verification.capacity() <= 0:
            return False, "拆分需要仍有容量的核验控制器（R0/R2 都走它）" \
                , {"state": _pcr.STATE_UNKNOWN_TARGET, "view": _base_view,
                   "found": None, "mode": _mode, "blocked": []}
        _pre = _fs.split_preflight_problems(
            _base_view, plan, _rec, _targets[0], novel_text=novel_text)
        if _pre:
            return False, "拆分预检未通过（零调用）：%s" % "、".join(
                str(x.get("code") or "") for x in _pre), \
                {"state": _pcr.STATE_UNKNOWN_TARGET, "view": _base_view,
                 "found": None, "mode": _mode, "blocked": list(_pre)}
    _found = _pcr.content_slots(
        _base_view, problems, allow_create=allow_create,
        target_fact_uids=target_fact_uids,
        verification_record=_rec,
        novel_text=novel_text, record_admitted=_admitted,
        allow_split=bool(allow_split),
        split_target_uids=(split_target_uids if allow_split else None))
    if allowed_repair_actions is not None:
        _allow_actions = {str(_a) for _a in allowed_repair_actions}
        for _slot in _found.get("slots") or []:
            _slot["allowed_actions"] = [
                str(_a) for _a in (_slot.get("allowed_actions") or [])
                if str(_a) in _allow_actions]
        if not _found.get("slots") or any(
                not _s.get("allowed_actions") for _s in _found["slots"]):
            return False, "限定动作与原本合法动作交集为空：%s" % sorted(
                _allow_actions), {
                "state": _pcr.STATE_ACTION_CONVERGENCE_EMPTY,
                "view": _base_view, "found": _found, "mode": _mode,
                "blocked": list(_found.get("blocked") or []),
                "deferred": list(_found.get("deferred") or [])}
    _ctx = {"view": _base_view, "found": _found, "mode": _mode,
            "verification_record": _verification_record_for(verification, plan),
            "blocked": list(_found.get("blocked") or []),
            "narrow_unavailable": list(_found.get("narrow_unavailable") or []),
            "deferred": list(_found.get("deferred") or [])}
    # 2026-11-22（M07 ch1，A）：单载体替换会丢掉已表达部分的复合事实。只在不受限的
    # 普通修订里开放（受限动作/显式目标沿用原语义）；拆分预检与核验容量零调用先判。
    _vsplit_ready: list = []
    _vsplit_unavail: list = []
    if (_found.get("voice_split") and allowed_repair_actions is None
            and not allow_split):
        from shared import fact_split as _fs_v
        for _e in _found.get("voice_split") or []:
            if verification is None or verification.capacity() <= 0:
                _vsplit_unavail.append({"fact_uid": _e.get("fact_uid"),
                                        "codes": ["split_verification_capacity"]})
                continue
            _pre = _fs_v.split_preflight_problems(
                _base_view, plan, _rec, str(_e.get("fact_uid") or ""),
                novel_text=novel_text)
            if _pre:
                _vsplit_unavail.append({"fact_uid": _e.get("fact_uid"),
                                        "codes": [str(x.get("code") or "") for x in _pre]})
                continue
            _vsplit_ready.append(_e)
    _ctx["voice_split"] = _vsplit_ready
    _ctx["voice_split_unavailable"] = _vsplit_unavail
    _vsplit_codes = sorted({str(c) for _e in (_found.get("voice_split") or [])
                            for c in (_e.get("codes") or [])})
    # **身份失效优先**：目标实体都不存在时，先修记录（生产者），不是先改内容
    if _ctx["blocked"]:
        return False, "本轮问题里的**目标身份失效**（%s）——这些实体不在基线视图里，" \
            "内容槽位无从下手" % "、".join(str(_x.get("reason") or _x.get("uid") or "")
                                          for _x in _ctx["blocked"]), \
            dict(_ctx, state=_pcr.STATE_UNKNOWN_TARGET)
    if _vsplit_codes and _found["slots"]:
        # 普通槽位先修其余事实；复合事实本轮 deferred，由完整验收继续报告，
        # 等它成为唯一可落槽的问题时再走「新建声音 + 拆分」（避免与其他部分修订同轮
        # 让保护事实的核验措辞漂移，拆分采纳因此只能记无法确认）。
        _ctx["deferred"] = sorted(set(_ctx.get("deferred") or []) | set(_vsplit_codes))
    if _vsplit_ready and not _found["slots"]:
        _deferred_codes, _blocking_codes = _slot_deferral(
            _found["unexpressible"], problems) if _found["unexpressible"] else ([], [])
        if not _blocking_codes:
            return True, "", dict(_ctx, state=_pcr.STATE_VOICE_SPLIT,
                                  deferred=sorted(set(_ctx.get("deferred") or [])
                                                  | set(_deferred_codes)))
    if _found["unexpressible"]:
        # S04（2026-11-21）：按**能力类别**决定"留到下一轮"还是"挡路"，不再逐个 code 豁免。
        # 可 defer 的问题不改变最终验收——完整验收下一轮照旧报告它们；这里只决定**本轮
        # 先修哪些**。全部表达不了时仍走既有非槽位备用路（一次调用，不是零调用）。
        _deferred_codes, _blocking_codes = _slot_deferral(
            _found["unexpressible"], problems)
        if _blocking_codes:
            return False, "这些修法内容槽位表达不了（%s）" % "、".join(_blocking_codes), \
                dict(_ctx, state=_pcr.STATE_UNSUPPORTED,
                     deferred=sorted(set(_ctx.get("deferred") or [])
                                     | set(_deferred_codes)))
        if not _found["slots"]:
            return False, "本轮只剩槽位表达不了的项（内容槽位不适用：%s）" \
                % "、".join(_deferred_codes), \
                dict(_ctx, state=_pcr.STATE_UNSUPPORTED,
                     deferred=sorted(set(_ctx.get("deferred") or [])
                                     | set(_deferred_codes)))
        return True, "", dict(_ctx, state=_pcr.STATE_OK,
                              deferred=sorted(set(_ctx.get("deferred") or [])
                                              | set(_deferred_codes)))
    if not _found["slots"]:
        return False, "本轮没有问题能落成内容槽位", \
            dict(_ctx, state=_pcr.STATE_NO_SLOTS)
    return True, "", dict(_ctx, state=_pcr.STATE_OK)

def _slots_revision_outcome(problems, *, target_fact_uids=None, target_voice_uids=None,
                            affected_fact_uids=None, co_carried=False,
                            voice_identity_unresolved=False,
                            verification_evidence_ok=None,
                            protocol_ok=True, protection_structural=True,
                            candidate=None,
                            source="complete_candidate + 控制器完整验收（含一致性轴）") -> dict:
    """槽位修订的四维记账（三态 true/false/null，按**目标身份**与**有效证据**判定）.

    - `content_repaired`：目标事实 + 受影响事实 + 目标声音的**目标修复**；
      目标声音的核验失败必须使其为 false。缺少有效核验记录 ⇒ null。
    - `protection_intact`：结构面由范围守卫保证；同载事实/hook 的语义保护需要核验
      证据，缺证据时 null。
    - `final_accepted`：完整验收残余为空（非目标残余也会让它 false）。
    """
    import hashlib as _hl
    import json as _json
    probs = [p for p in (problems or []) if isinstance(p, dict)]
    codes = [str(p.get("code") or "") for p in probs]
    _facts = {str(x) for x in (target_fact_uids or [])
              if str(x) not in ("", "None")}
    _affected = {str(x) for x in (affected_fact_uids or [])
                 if str(x) not in ("", "None")}
    _voices = {str(x) for x in (target_voice_uids or [])
               if str(x) not in ("", "None")}
    _no_ev = {"verify_record_missing", "verify_bad_response", "verify_call_failed",
              "verify_deadline_exhausted"}

    def _hit(p, ids) -> bool:
        if not ids:
            return False
        _u = str(p.get("uid") or p.get("ref") or p.get("unit_uid")
                 or p.get("fact_uid") or "")
        return _u in ids

    _no_evidence = any(c in _no_ev for c in codes)
    _fact_bad = any(c.startswith("verify_") and c not in _no_ev
                    and not c.startswith("verify_voice_")
                    and _hit(p, _facts | _affected)
                    for c, p in zip(codes, probs))
    _voice_bad = any(c.startswith("verify_voice_") and c not in _no_ev
                     and _hit(p, _voices)
                     for c, p in zip(codes, probs))
    _axes = []
    if _facts or _affected:
        _axes.append(False if _fact_bad else (None if _no_evidence else True))
    if _voices:
        _axes.append(False if _voice_bad else (None if _no_evidence else True))
    if voice_identity_unresolved:
        _axes.append(None)
    if not _axes:
        _content = None if _no_evidence else True
    elif False in _axes:
        _content = False
    elif None in _axes:
        _content = None
    else:
        _content = True
    if verification_evidence_ok is False and _content is not False:
        # 记录缺项/重复/悬空/身份不匹配时，没有有效语义证据；不得因"没有带目标 uid
        # 的负面问题"推出修好。已出现的**目标判负**仍保留 false。
        _content = None
    if not protection_structural:
        _protect = False
    elif co_carried and _no_evidence:
        _protect = None          # 同载语义未评估
    elif co_carried and (_fact_bad or _voice_bad):
        _protect = False         # 有核验且目标侧已出现退化
    elif verification_evidence_ok is False:
        _protect = None          # 语义保护没有有效记录
    else:
        _protect = True
    _digest = None
    if isinstance(candidate, dict):
        _body = {k: v for k, v in candidate.items() if k != "plan_verification"}
        _digest = _hl.sha256(_json.dumps(_body, ensure_ascii=False,
                                         sort_keys=True).encode("utf-8")).hexdigest()[:16]
    _detail = lambda reason: {"reason": reason, "stage": "slot_round",
                              "candidate_digest": _digest, "source": source}
    return {
        "protocol_ok": bool(protocol_ok),
        "content_repaired": _content,
        "protection_intact": _protect,
        "protection_structural": bool(protection_structural),
        "final_accepted": (not probs),
        "candidate_digest": _digest,
        "details": {
            "protocol_ok": _detail("回答解析、准入与编译成功"),
            "content_repaired": _detail(
                "无有效语义记录 ⇒ null" if verification_evidence_ok is False
                else ("无目标相关核验证据 ⇒ null" if _content is None
                      else ("目标事实或目标声音仍有核验残余" if _content is False
                            else "目标事实/声音无核验残余"))),
            "protection_intact": _detail(
                "结构保护成立" if _protect is None else
                ("范围守卫在 accept 之前通过" if _protect else "范围/同载保护判负")),
            "final_accepted": _detail("完整验收残余：%s" % (codes or "无")),
        },
        "source": source,
    }

def _slot_deferral(unexpressible, problems) -> tuple:
    """把槽位**表达不了**的问题按**能力类别**分成 `deferred` / `blocking`（S04）.

    背景（2026-11-21 D2）：旧实现只要有一条表达不了的 code 就整轮退回 36K 通用增量请求
    ——11-21 run 第 4 轮本来只有 `verify_plan_coherence` 一条表达不了，f03/f06 两条事实
    已经能落成槽位，却整轮被它拖走。这里改为按**修法能力类别**判断，不再逐个列 code：

    - `verify_*` / `view_voice*` / `voice_*`：核验结论与声音轴——槽位没有对应适配器，
      下一轮由新核验记录/视图协议继续报告（与旧实现豁免 `verify_voice_speaker_missing`
      同一口径，只是改用类别而非单个 code）；
    - 能力表动作为 `ops` / `view`：delta 或视图协议表达得了，留到下一轮；
    - 其余（`content` 增删内容、`plan_field` 结构/计划字段、未登记 fail-closed）：
      **仍挡路**——它们要么需要更强的接口，要么本轮无从证明可修。

    返回 `(deferred, blocking)`，元素保留原始展示文本（可能带"（缺稳定 UID）"等后缀）。

    边界（2026-11-21 实测校准）：**已有槽位适配器**（`SLOT_CODE_KIND` 覆盖）的 code
    出现在 `unexpressible` 里，说明适配器本可表达但这次缺记录/身份/权限（例如
    `verify_voice_not_speech` 缺可迁移来源类型）——这不是"本轮不归槽位管"，而是
    **证据不足**，仍走既有非槽位备用路（blocking），不能把这类残余 defer 掉。
    只有"槽位适配器根本不接"的能力类别（核验轴/声音轴/ops/view）才 defer。
    """
    from step01_script_gen.plan_content_repair import SLOT_CODE_KIND as _slot_kinds
    _marks: dict = {}
    for _p in (problems or []):
        if isinstance(_p, dict):
            _marks.setdefault(str(_p.get("code") or ""), str(_p.get("fix") or ""))
    _deferred: list = []
    _blocking: list = []
    for _item in (unexpressible or []):
        _text = str(_item)
        _code = _text.split("（", 1)[0].strip()
        _mark = _marks.get(_code, "")
        _cap = (_pv("_FIX_MARK_TO_CAPABILITY").get(_mark) if _mark
                else _pv("PLAN_FIX_CAPABILITY").get(_code))
        _action = _cap[0] if _cap else ""
        if _code in _slot_kinds:
            # 适配器覆盖但未能表达 ⇒ 证据/身份不足，保留非槽位备用路（fail-closed）
            _blocking.append(_text)
        elif _code.startswith(("verify_", "view_voice", "voice_")):
            _deferred.append(_text)
        elif _action in _pv("_SLOT_DEFER_ACTIONS"):
            _deferred.append(_text)
        else:
            _blocking.append(_text)
    return _deferred, _blocking

def _voice_targets_from_slots(slots, mapping) -> tuple:
    """从有效槽位派生**候选里**的目标声音身份。

    reclassify 的旧 uid 不能直接查 `mapping[旧 uid]`：实际映射是
    `旧 uid → to_temp_uid → 最终 uid`。缺映射时不回退旧 uid，返回 unresolved，
    让记账记为证据不完整，而不是把旧编号误当目标。
    """
    _targets: set = set()
    _unresolved = False
    for _s in slots or []:
        if not isinstance(_s, dict):
            continue
        _kind = str(_s.get("kind") or "")
        if _kind == "voice_text":
            _uid = str(_s.get("unit_uid") or "")
            if _uid:
                _targets.add(_uid)
            continue
        if _kind == "voice_kind":
            _tmp = str((_s.get("reclassify") or {}).get("to_temp_uid") or "")
            _final = str((mapping or {}).get(_tmp) or "") if _tmp else ""
            if _final:
                _targets.add(_final)
            else:
                _unresolved = True
            continue
        if _kind == "fact_visual" and "create_voice" in (
                _s.get("allowed_actions") or []):
            from step01_script_gen import plan_content_repair as _pcr
            _tmp = _pcr._create_voice_temp_uid(_s)
            _final = str((mapping or {}).get(_tmp) or "")
            if _final:
                _targets.add(_final)
            # 未选择 create_voice 时 mapping 本来就没有该键；不能据此判未解析。
            continue
    return _targets, _unresolved

def _split_terminal(stop_reason, *, applied=False, no_model_call=False,
                    detail="", evidence=None) -> dict:
    """拆分轮的统一 terminal 标记（控制器只信它来停，不跳过既有守卫）."""
    return {"stop_reason": str(stop_reason), "applied": bool(applied),
            "no_model_call": bool(no_model_call), "detail": str(detail),
            "evidence": dict(evidence or {})}

def _verification_record_for(verification, plan):
    """当前计划对应的核验记录（**按计划摘要**取，不按视图摘要）."""
    _recs = getattr(verification, "records", None)
    if not isinstance(_recs, dict):
        return None
    from shared.content_verification import plan_digest_of
    return _recs.get(plan_digest_of(plan)) or None

def _detail_vp(problems) -> str:
    """视图/响应问题记录 → 一行行带字段路径与原因的原话（不归纳、不截断）."""
    _out = []
    for _p in (problems or []):
        _path = str((_p or {}).get("path") or "").strip()
        _out.append("%s%s｜%s" % ("%s " % _path if _path else "",
                                  str((_p or {}).get("code") or ""),
                                  str((_p or {}).get("reason") or "")))
    return chr(10).join(_out)

def complete_candidate(view, novel_text, verification=None, *, context=None,
                       verify_axes=None):
    """**唯一的候选完成出口** → `(candidate, problems)`.

    顺序固定：**关系投影 → 挂上下文 → 确定候选身份 → 附加核验记录 → 完整验收**。

    ⚠️ 复核 2026-10-05 P1 的根因就在这里：此前「挂记录」发生在 `accept` 回调里，而
    **回调返回的只是问题**——被验收的对象是一个临时副本，控制器返回的候选是**另一个**
    对象（没有 `plan_verification`）。于是：控制器判 ok → 返回候选没记录 → 最终真实验收
    报 `verify_record_missing`。**验收通过但交付的东西没被验收过**，这是最坏的一类。

    所以：这里返回的 `candidate` 就是**被验收、被采纳、被保存、被下发的那一个对象**。
    `accept` 回调只负责调它，不再自己造副本。

    ⚠️ **投影准入不过 ⇒ `(None, problems)`**（复核 2026-10-06 第十二轮 ① 的"投影/候选
    完成"边界）：结构化拒收，**不交付部分投影**。调用方（`_replan_by_view`）据此把这一轮
    判成拒绝，不是"应用了但结果不可用"。

    `context`（复核 r6 §4.2）是**本地生成**的核验上下文：给了就在**算候选身份之前**挂上
    ——上下文进 `plan_digest_of`，所以"挂完再算"与"算完再补"是两份不同的身份。不给时
    仍按计划自带的那一份（投影时经 `plan_basis` 保真透传）走：老稿没有它就是合法的
    根策略。**上下文绝不能等到验收之后再补。**
    """
    try:
        _plan = plan_from_view(view)
    except PresentationContractError as _e:
        return None, list(_e.problems)
    if context is not None:
        # **挂上下文紧跟在投影之后**（r6 §4.2 的固定次序）：它进内容身份，不许后补。
        _plan = dict(_plan)
        from shared.content_verification import VERIFICATION_CONTEXT_KEY
        _plan[VERIFICATION_CONTEXT_KEY] = context
    # **顺序投影先于核验**（2026-11-15）：承载次序唯一且与数组序不一致时，先把
    # `dialogue_lines` 与全部 D 引用改到同一顺序，再算内容身份/挂核验记录——
    # 否则核验记录会绑在旧编号上，验收时又被判摘要不符（提前绑定旧身份）。
    from step01_script_gen.plan_feasibility import apply_voice_order_projection
    _plan, _voice_order = apply_voice_order_projection(_plan)
    if _voice_order.get("changed"):
        _plan = dict(_plan)
        _plan.pop("plan_verification", None)
        _plan.pop("verification_rejected", None)
    if verification is not None:
        from step01_script_gen.content_verify import attach_verification
        from shared.carrier_entailment import PROTOCOL, protocol_of
        from shared.quantified_verification import PROTOCOL as QUANTIFIED
        if verify_axes is None and protocol_of(_plan) in (PROTOCOL, QUANTIFIED):
            from step01_script_gen.content_verify import verify_view_axes_with_service
            def verify_axes(_view, _digest, **kwargs):
                return verify_view_axes_with_service(_view, _digest,
                    novel_text=novel_text, novel_digest=verification.novel_digest,
                    remaining=verification.max_verifications, display_max_tokens=8000, **kwargs)
        # `verify_axes` 显式给了才走两轴控制器路径（复核 r7 §4.2，生产默认不启用）。
        _plan = attach_verification(_plan, verification, verify_axes=verify_axes)
    return _plan, _pc("plan_acceptance_problems")(_plan, novel_text)

def _accept_view_candidate(view, novel_text, verification, *, verify_axes=None):
    """视图候选的**问题清单**（`complete_candidate` 的薄包装）."""
    return complete_candidate(view, novel_text, verification, verify_axes=verify_axes)[1]
