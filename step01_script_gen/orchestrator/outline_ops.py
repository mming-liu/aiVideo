# -*- coding: utf-8 -*-
"""S09: `step01_script_gen.outline` 的 outline_ops 职责（2026-11-28 拆分）.

父模块保留生成/修订编排、提示常量和缓存提交；本模块只放结构校验、
ops 应用与修订反馈。父模块当前值在调用点经 `_pv`/`_pc` 读取，
导入期不导入 orchestrator 或父模块。
"""
from __future__ import annotations

from __future__ import annotations
import json
import re

_PARENT = "step01_script_gen.outline"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name):
    return _pv(name)


def apply_replan_ops(plan: dict, ops, novel_text: str) -> tuple:
    """把模型给的**操作**应用到旧计划上（**代码执行**，模型不重写整份 JSON）.

    2026-09-26 用户决定：实测两次重选，模型把上一版计划原样回显，而事后比对把
    「没改动」判成合规——要求模型整份重写既给了它「什么都不改」的退路，也让验收
    只能事后比对。改成操作式后：**改了什么由操作决定**，模型没有「回抄」这一退路。

    2026-09-27 用户决定（合并方案第 2 步）：操作从「只能替换」扩到
    `replace` / `insert_after` / `split`，并改为**整批三步走**：

    ① **先检查**——全部操作对着**修改前**的快照验（种类、编号范围、一批内不冲突、
       正文自带原文依据、载体合法且不越界）；一条不过整轮拒收，不半途改一半；
    ② **再统一应用**——对快照做纯函数变换（`split` 原地展开、`insert_after` 顺插）；
    ③ **后重新编号**——编号 = **应用后**的位置（B1..B%d）。下游生成契约
       （`prompts.render_execution_contract` 的 `B%d` / `D%d` 标签）与候选层
       `plan_refs` 一律按位置渲染，故自动跟随；本函数接收的 `index` 只在**这一轮**
       对照模型看到的反馈有意义，**不得**跨轮复用。

    Returns `(new_plan, "")`；`(None, "no_change")` = 模型没给任何操作（**显式**，
    不再算一次有效重选）；其余为拒收原因。
    """
    from step01_script_gen.plan_feasibility import (
        BeatRecord, check_dialogue_representable, is_sentence_end_terminated,
        plan_dialogue_lines, source_contains)
    # 1b（2026-09-27 复核）：**解析失败 / 结构错误 / 真正的空操作**必须分开——
    # 前两者落成 `no_change` 会把「模型没按契约回」记成「模型认为无需改动」，
    # 诊断上彻底指错方向（而且会被当成一次有效结论存进 plan_replan）。
    if not isinstance(ops, dict):
        return None, "invalid_result: 重选结果不是 JSON 对象"
    # **本地响应校验**（工作包 §4 C；复核 2026-10-02 之三 P1）：一处按**请求 schema** 核对
    # 形态——必填键、`null` 的允许位置、类型、以及**没定义过的键**（含嵌套条目）。旧实现把这
    # 些散成几条手写判断，结果 ①旁白 `text: 123` 被 `str()` 圆场、②旁白条目里的未知键被丢掉、
    # ③必填键 `dialogue_ops: null` 被当缺席读——三种"违反严格 schema 的响应"都能应用成功。
    # 这里一律拒收（`illegal_response`），**不靠转换或丢字段修正模型响应**。
    from step01_script_gen.plan_feasibility import (
        replan_ops_response_problems, effective_decl_ops)
    _shape = replan_ops_response_problems(ops)
    if _shape:
        # 截断**必须显式**，而且**总数写在最前面**：这条文本会进下一轮请求的拒收反馈，
        # 消费方（历史 200 字 / 拒收记录 400 字 / 提示块）都按**前缀**截断——把"另有
        # N 条"放在句尾等于没写（复核 2026-10-02 之五实测：尾巴先被截掉）。
        _head = ("%d 条形态问题：" % len(_shape) if len(_shape) <= 6
                 else "%d 条形态问题（只列前 6 条）：" % len(_shape))
        return None, "invalid_result: %s%s" % (_head, "；".join(_shape[:6]))
    _dlg = list(ops.get("dialogue_ops") or [])
    _bat = list(ops.get("beat_ops") or [])
    # **声明类操作**（`fact_carriers` / `hook_carrier` / `hook_spoken` / `narration_lines`）
    # 同样算"要改"：复核第 45 轮实测——只给声明时旧实现落 `no_change`，即"承载/钩子声明
    # 修不了"，而反馈块早就把它们列成"可以改"（请求与消费契约自相矛盾）。
    _decl = effective_decl_ops(ops)
    if not _dlg and not _bat and not _decl:
        return None, "no_change"

    new = json.loads(json.dumps(plan, ensure_ascii=False))
    from step01_script_gen.plan_feasibility import plan_narration_entries
    lines = list(plan_dialogue_lines(new))
    n_lines = len(lines)
    # **本批改完之后的旁白表**（复核 2026-10-02 P1）：先归一、先落地，之后所有
    # `旁白k` 的**引用范围**都按它校验——同一批"新增旁白 + 指定它由哪条节拍承载"
    # 是完全合法的联动修法，不能拿旧计划的条数把它判越界。
    _narr_out, _why_narr = _norm_narration_entries(ops.get("narration_lines"))
    if _why_narr:
        return None, _why_narr
    if _narr_out is not None:
        new["narration_lines"] = _narr_out
    # **本批完成后的**旁白条数：`旁白k` 的**引用范围**与残留**孤儿**都按它算
    # （复核 2026-10-02 之二 P1：按旧表枚举会把本批**删掉**的旁白当成残留孤儿）
    _n_narration = (len(plan_narration_entries(plan)) if _narr_out is None
                    else len(_narr_out))
    #: **修改前的快照**：本批全部 `index` 都对着它编号（节拍操作会改变条数与顺序，
    #: 若边改边编号，同一批里 `split` 之后的操作就会指到别的节拍上）。
    beats_in = list(new.get("shot_beats") or [])
    n_beats = len(beats_in)
    snapshot = [BeatRecord.parse(b) for b in beats_in]

    # ---- ① 台词操作：规范化 + 批内冲突检查 ---------------------------------
    _dlg_seen: dict = {}
    for op in _dlg:
        _i, _why_i = _coerce_op_index(op.get("index"), "D", "台词")
        if _why_i:
            return None, "dialogue_op：%s" % _why_i
        if not 1 <= _i <= n_lines:
            return None, "dialogue_op index %s 越界（共 %d 句）" % (op.get("index"), n_lines)
        if _i in _dlg_seen:
            # 一批里同一句被换两次 ⇒ 结果取决于应用顺序，不是**唯一记录**
            return None, "操作冲突：D%d 同时被多条台词操作替换（同一句至多一条）" % _i
        _dlg_seen[_i] = True
        _rep = str(op.get("replace_with") or "").strip()
        if not _rep:
            return None, "dialogue_op 第 %d 句的 replace_with 为空" % _i
        if not source_contains(novel_text, _rep):
            return None, "第 %d 句的替代未在原文逐字找到：%s" % (_i, _rep[:24])
        if not check_dialogue_representable(_rep)["representable"]:
            return None, "第 %d 句的替代不可单镜表示：%s" % (_i, _rep[:24])
        # 1a（2026-09-27 复核）：**逗号/顿号结尾的半句一律非法**——提示里明令禁止过
        # （实测模型两次都拿自己出处的逗号截断前缀当替代），但当时只有提示、没人拦：
        # 短半句是"可表示"的，能一路通过。判据与提示同源（`is_sentence_end_terminated`）。
        if not is_sentence_end_terminated(_rep):
            return None, ("第 %d 句的替代以「%s」收尾——逗号/顿号截断的半句一律非法"
                          "（成片里是没说完的话）：%s"
                          % (_i, _rep[-1] if _rep else "", _rep[:24]))
        lines[_i - 1] = _rep
    new["dialogue_lines"] = lines

    # ---- ② 节拍操作：**先整批检查**（范围/冲突/正文/载体），一条不过整轮拒收 ----
    #: index → {"edited": 改过它的操作种类 or None, "inserted": 是否已在其后插入}
    #: P1-3（2026-09-27 复核）：**两个槽位分别判**。「只记上一条操作种类」有洞——
    #: `replace → insert_after → replace` 时，第三条看到「上一条是插入」就被放行，
    #: 随后**覆盖**掉第一条 replace（改动被静默吞掉）。改该节拍 / 在它后面插入是
    #: **两件互不覆盖**的事，各自至多一次。
    _beat_seen: dict = {}
    _edit_ops: dict = {}          # index → (kind, [渲染好的节拍文本, ...])——改该节拍
    _insert_ops: dict = {}        # index → [渲染好的节拍文本, ...]——在该节拍**之后**插入
    for op in _bat:
        _kind = str(op.get("op") or "").strip() or _pv("BEAT_OP_REPLACE")
        if _kind not in _pv("BEAT_OP_KINDS"):
            return None, "beat_op 的操作种类非法：%r（只能是 %s）" % (
                _kind, " / ".join(_pv("BEAT_OP_KINDS")))
        _i, _why_i = _coerce_op_index(op.get("index"), "B", "节拍")
        if _why_i:
            return None, "beat_op：%s" % _why_i
        # `insert_after` 的 0 号位 = 插到最前（其余操作必须指到已有节拍）
        _lo, _hi = (0, n_beats) if _kind == _pv("BEAT_OP_INSERT_AFTER") else (1, n_beats)
        if not _lo <= _i <= _hi:
            return None, ("beat_op index %s 越界（%s 允许 %d..%d，共 %d 条节拍）"
                          % (op.get("index"), _kind, _lo, _hi, n_beats))
        _slot = _beat_seen.setdefault(_i, {"edited": None, "inserted": False})
        _is_insert = (_kind == _pv("BEAT_OP_INSERT_AFTER"))
        if _slot["inserted"] if _is_insert else (_slot["edited"] is not None):
            _prev_kind = "insert_after" if _is_insert else _slot["edited"]
            return None, ("操作冲突：B%d 同时被「%s」与「%s」两条操作改动"
                          "（同一节拍至多一条**改动** + 一条**插入**——索引指"
                          "**修改前**的编号，同槽位重复时后一条会覆盖前一条）"
                          % (_i, _prev_kind, _kind))
        if _is_insert:
            _slot["inserted"] = True
        else:
            _slot["edited"] = _kind
        # 被改节拍的旧记录（`insert_after` 是**新增**一条，没有旧载体可沿用）
        _old = snapshot[_i - 1] if _kind != _pv("BEAT_OP_INSERT_AFTER") else None
        if _kind == _pv("BEAT_OP_SPLIT"):
            _parts = op.get("parts")
            if not isinstance(_parts, list) or len(_parts) < 2:
                return None, "第 %d 条节拍的 split 至少要给两段" % _i
            _rendered = []
            for _j, _p in enumerate(_parts, 1):
                if not isinstance(_p, dict):
                    return None, "第 %d 条节拍的 split 第 %d 段不是对象" % (_i, _j)
                # split 的每段都必须自己写载体（拆分的**目的**就是给两段不同载体，
                # 沿用旧载体等于没拆）——故不传 `old_record`。
                _txt, _why = _pc("_render_beat_op")(_p.get("new_text"),
                                             _p.get("new_carrier"), None, n_lines,
                                             require_carrier=True,
                                             n_narration=_n_narration)
                if _why:
                    return None, "第 %d 条节拍 split 第 %d 段：%s" % (_i, _j, _why)
                _rendered.append(_txt)
            _edit_ops[_i] = (_kind, _rendered)
        elif _kind == _pv("BEAT_OP_INSERT_AFTER"):
            _txt, _why = _pc("_render_beat_op")(op.get("new_text"), op.get("new_carrier"),
                                         None, n_lines, require_carrier=True,
                                         n_narration=_n_narration)
            if _why:
                return None, "第 %d 条节拍（插入）：%s" % (_i, _why)
            _insert_ops[_i] = [_txt]
        else:
            _txt, _why = _pc("_render_beat_op")(op.get("new_text"), op.get("new_carrier"),
                                         _old, n_lines, n_narration=_n_narration)
            if _why:
                return None, "第 %d 条节拍：%s" % (_i, _why)
            _edit_ops[_i] = (_kind, [_txt])

    # ---- ③ 统一应用（对**快照**做纯函数变换；位置即**应用后**的编号）--------
    out: list = list(_insert_ops.get(0) or [])      # insert_after index=0 → 插到最前
    for _i, _b in enumerate(beats_in, 1):
        _edit = _edit_ops.get(_i)
        if _edit is None:
            out.append(_b)
        else:
            _kind, _rendered = _edit
            if _kind == _pv("BEAT_OP_SPLIT"):
                out.extend(_rendered)               # 原地展开成多段
            else:
                out.append(_rendered[0])
        out.extend(_insert_ops.get(_i) or [])       # 插在该节拍**之后**
    new["shot_beats"] = out
    # S03（2026-11-14）：节拍表变了（插入/删除/重排/拆分）⇒ 因果链的 `（Bk）`
    # 身份必须跟着重映射；找不到唯一对应节拍的引用改成「未落节拍」并留说明。
    from shared.execution_semantics import remap_causal_refs
    for _causal_note in remap_causal_refs(new, beats_in, out):
        print("[WARN] %s" % _causal_note)
    # 本批**改写过**的条目（改写的 + 插入的）——原文证据只核这些（见下方说明）
    _changed = ([t for _k, _ts in _edit_ops.values() for t in _ts]
                + [t for _ts in _insert_ops.values() for t in _ts])

    # 逐项保护必保留事实与事件顺序（操作只在旧计划上打补丁，结构上本就不会破——
    # 2026-09-27 复核要求把它作为**显式**验收写进代码，而不是靠"结构上不会"）
    _inv = _plan_invariants_intact(plan, new)
    if _inv:
        return None, _inv
    # 替换台词后**重新验算整张承载关系**（不能只验算字数）——2026-09-27 复核：
    # 重选可能把某句换掉、又把原指向它的节拍改成「载体：画面」，让那句**无人承载**；
    # 这种事不在计划层显形，会一路拖到候选层才被台词门整稿拒收。
    from step01_script_gen.plan_feasibility import plan_narration_entries
    # 承载关系的残留检查**按候选计划**（复核 2026-10-02 之二 P1）：旧实现按**旧表**枚举，
    # 于是"同批删除 N2、并把承载它的节拍改成画面"这种合法联动修法会被判「N2 无承载」
    # ——**被本批删掉的那条**被当成残留孤儿。两轴分开判：
    # - **节拍侧**（重复/非法标注）与**台词**孤儿：本批改坏了就拒收（既有裁决：改台词/
    #   节拍必须保持每句被承载）；
    # - **旁白**孤儿交**同一条验收链**：`narration_lines` 是**声明表**，新增/删除/替换
    #   都由声明操作表达，"还没人承载"是**结果不完整**（验收链报 `orphan_uncarried`，
    #   控制器据此反馈下一轮），不是"操作非法"（在这里拒收会以 `illegal_response`
    #   结束整轮，模型连反馈都拿不到）。身份取自**记录**，不从标签串反解。
    from step01_script_gen.plan_feasibility import carrier_relation_records
    _rel = carrier_relation_records(new.get("shot_beats"),
                                   len(plan_dialogue_lines(new)), _n_narration)
    _dup = ["B%d" % r["index"] for r in _rel if r["code"] == "carrier_duplicate"]
    _unlabeled = ["B%d" % r["index"] for r in _rel if r["code"] == "carrier_unlabeled"]
    _orphan_d = [r for r in _rel if r["code"] == "orphan_uncarried"
                 and r["kind"] == "dialogue"]
    # **责任范围 = 本批能改的东西**（复核 2026-10-02 之六 P1）：承载关系只有**台词操作**
    # （换掉某句 ⇒ 原指向它的节拍不再承载它）与**节拍操作**（改/插入/拆分 ⇒ 载体标注变了）
    # 才动得了；声明键（`fact_carriers` / `hook_*` / `narration_lines`）改不了台词内容，
    # 也改不了节拍上的「载体：」标注。旧写法对**每个**批次都跑这三条检查，于是"只修一条
    # 事实承载"的合法修订被**基线里本来就有的**孤儿台词判成 `illegal_response`
    # （离线实测：同一响应去掉基线孤儿后正常应用）。基线缺陷该由**验收链**报告、由控制器
    # 反馈下一轮，不该在这一批头上记一笔。改台词/节拍的闭合要求**继续保留，不放宽**。
    _touches_carriers = bool(_dlg or _bat)
    # **应用后验收：逐轴收集、一次把全部问题交回**（2026-10-04 实测根因，台账 §19.1）。
    # 旧写法每个轴各自 `return`，第一道不过后面的判据**一次都不跑**——章 1 第 2 轮只回了
    # 「D2 无任何节拍承载」1 条，而声明门里另 2 条（F7/F9）**从未被计算**（探针记录到该轮
    # `plan_contract_problems` 调用次数为 0）；第 3 轮契约门实检 6 条、被 `[:2]` 截成 2 条。
    # **责任范围不变**：仍然只有本批改过台词/节拍才跑这三轴。
    _post: list = []
    if _touches_carriers:
        if _dup:
            _post.append("节拍载体标注重复：%s（每条节拍只许一个载体）" % _brief_ids(_dup))
        if _unlabeled:
            _post.append("节拍缺载体标注：%s（计划带了标注就必须每条恰有一个载体）"
                         % _brief_ids(_unlabeled))
        if _orphan_d:
            _post.append("替换后 %s 无任何节拍承载（承载关系必须双向闭合）"
                         % _brief_ids("D%d" % r["index"] for r in _orphan_d))
    # 只核**被改写的**条目：未改动的条目是既有状态（早就过了流水线），把整份计划
    # 一起核会把「原本就抄得不精确的旧条目」也算到本轮头上而误拒——操作式的语义
    # 是「代码在旧计划上打补丁」，责任范围就是补丁本身。
    if _changed:
        _why = _plan_evidence_intact({"shot_beats": _changed, "required_facts": []},
                                     novel_text)
        if _why:
            _post.append(_why)
    # 替换台词后：事实/钩子的承载必须**逐项声明**（2026-09-30 复核裁决）——
    # 计划必须自洽，否则「生成按 dialogue_lines 写、审计按事实/钩子判」两边各自
    # 自洽，任何修复轮都改不动。不过就拒收本轮（见函数注释的完整理由）。
    _cd = _plan_carrier_declarations_intact(new, ops, len(plan_dialogue_lines(new)),
                                            n_narration=_n_narration,
                                            problems=_post)
    if _cd:
        _post.append(_cd)
    if _post:
        return None, _join_replan_problems(_post)
    # **应用后内容确实没变 ⇒ no_change**（工作包 §4 C）：例如把 `hook_carrier` 显式清成
    # 它本来就是的空值、或把某条台词换成它自己——"显式操作"不等于"内容变了"。
    if json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str) ==             json.dumps(new, ensure_ascii=False, sort_keys=True, default=str):
        return None, "no_change"
    return new, ""

def _norm_beat_carrier(raw, n_lines: int, old_record, n_narration: int = 0) -> tuple:
    """规范化一条操作给的载体 → `(载体文本, 原因)`；`原因` 非空 = 拒收.

    - 空 → 沿用 `old_record`（被改节拍）原有的载体——模型常常只想改正文，这比整轮
      拒收有用；沿用不到（原节拍本来就没有载体段）时留空，由承载关系验收判
      （那才是唯一判据）；
    - `画面` → 照收；`台词k` → k 必须在 `1..n_lines` 内（节拍操作**不**改台词条数）；
    - 其余一律拒收：载体是**唯一记录**，含糊值等于没声明（宁可显式失败）。

    **完整匹配**（2026-10-01 复核第 30 轮）：文法与数字转换**单源**在
    `plan_feasibility.parse_beat_carrier`。旧实现按"数字前缀"截取——模型给的
    `new_carrier="台词1-2"`（一条连续片段）被**静默**改成 `台词1`，契约检查还判通过：
    模型声明的意思被缩小了，而这正是修订控制器要反馈给规划器的东西。现在 `台词1-2`
    / `台词1、台词2` / `台词²` / 超长数字一律走"载体只能是…"这条拒收，并且**不抛异常**。
    """
    # 载体写法**单源**在 `CARRIER_KIND_FORMS` / `carrier_mode_hint()`（判据侧）——
    # 这里不再手抄"画面/台词/旁白"与那句拒收文案（复核 2026-10-02 的非阻塞观察）。
    from step01_script_gen.plan_feasibility import (
        CARRIER_KIND_FORMS, carrier_mode_hint, parse_beat_carrier)
    car = str(raw or "").strip()
    if not car and old_record is not None:
        kind, k = old_record.carrier()
        if kind == "dialogue" and k:
            car = "台词%d" % k
        elif kind == "narration" and k:
            car = "旁白%d" % k
        else:
            car = "画面" if kind == "visual" else ""
    if not car:
        return "", ""
    _kind, _k = parse_beat_carrier(car)
    if _kind == "visual":
        return CARRIER_KIND_FORMS["visual"][1], ""
    if _kind == "dialogue":
        _axis = CARRIER_KIND_FORMS["dialogue"][0]
        if _k is not None and 1 <= _k <= int(n_lines or 0):
            return "%s%d" % (_axis, _k), ""
        return "", "载体「%s」越界（本集共 %d 句台词）" % (car[:12], int(n_lines or 0))
    # 2026-09-30 复核第 6 轮：`旁白k` 是第三种合法载体（图外旁白）。没有这一支，
    # 「把某条节拍改成由旁白承载」这个唯一能修好交代类事实的操作会在应用器层被拒。
    if _kind == "narration":
        _axis = CARRIER_KIND_FORMS["narration"][0]
        if _k is not None and 1 <= _k <= int(n_narration or 0):
            return "%s%d" % (_axis, _k), ""
        return "", ("旁白载体「%s」越界（本集共 %d 条旁白；旁白要先写进 "
                    "narration_lines）" % (car[:12], int(n_narration or 0)))
    return "", "载体只能是 %s，收到「%s」" % (carrier_mode_hint(), car[:12])

def _coerce_op_index(raw, tag: str, axis: str) -> tuple:
    """操作 `index` → `(int, 原因)`；按**字段轴**解析编号.

    与 `plan_refs` 同源（`coerce_ref_index`）：接受整数 / 数字串 / **本轴**标签串
    （台词操作 `D1`、节拍操作 `B1`）。

    **错轴标签必须拒绝**：`"B1"` 出现在 `dialogue_ops` 里说明模型把两套编号体系搞混了
    ——按数字静默照收（B1 → 1）会把改动落到**另一条**对象上，比拒收危险得多。
    """
    from step01_script_gen.plan_feasibility import coerce_ref_index
    _v = coerce_ref_index(raw, tag)
    if _v is not None:
        return _v, ""
    _s = str(raw or "").strip()
    _other = "B" if tag == "D" else "D"
    if _s[:1].upper() == _other:
        return None, ("index 用了**另一套编号**「%s」——%s操作填的是%s编号（只填数字，"
                      "如 1）" % (_s[:12], axis, "台词" if tag == "D" else "节拍"))
    return None, "index 不是编号：%r（只填数字，如 1；不要带 %s 前缀）" % (raw, tag)

def _norm_narration_entries(raw) -> tuple:
    """把本批的 `narration_lines` 归一成**整表** → `(表, 原因)`；`raw is None` ⇒ `(None, "")`.

    归一只能做**一次**，而且要在这里就做完：旁白条数决定 `旁白k` 的合法范围，所以它必须
    在本批**所有承载引用校验之前**算出来（复核 2026-10-02 P1：旧实现拿**旧计划**的条数
    校验 `beat_ops` / `fact_carriers`——"同批新增 N1 + 指定它由哪条节拍承载"这种**合法
    联动修法**被判"本集共 0 条旁白"，记成 `illegal_response`；把 N1 先塞进旧计划反而能过）。
    """
    if raw is None:
        return None, ""
    if not isinstance(raw, list):
        return None, ("`narration_lines` 必须是对象数组（text/source/fact_refs）——"
                      "现在是 %s" % type(raw).__name__)
    _out: list = []
    for _i, _e in enumerate(raw, 1):
        # **不许圆场**（复核 2026-10-02 之三 P1）：`text`/`source` 必须是**字符串**——
        # 旧实现 `str(_e.get("text"))` 把模型给的数字 `123` 变成 `"123"` 写进计划，
        # 违反严格 schema 的响应被静默"修好"、整批记成应用成功。形态校验
        # （`replan_ops_response_problems`）已在应用器入口挡一道，这里是同一判据的第二道。
        if (not isinstance(_e, dict) or not isinstance(_e.get("text"), str)
                or not isinstance(_e.get("source"), str)
                or not _e.get("text").strip() or not _e.get("source").strip()):
            return None, ("`narration_lines` 第 %d 条非法（text/source 必须是**字符串**"
                          "且非空——数字/缺失都不接受）：%s" % (_i, str(_e)[:40]))
        # 与计划门同一口径（复核第 7 轮 P2）：`fact_refs` 必须存在、是数组、
        # 元素是**真正的整数**——`int("2")`/`int(True)`/`int(2.0)` 会把写错类型
        # 静默变成「引用了另一条事实」，应用器不能替模型圆场。
        _refs = _e.get("fact_refs")
        if not isinstance(_refs, list):
            return None, ("`narration_lines` 第 %d 条缺 `fact_refs`（整数数组；这条旁白"
                          "不承载任何事实就写 `[]`）：%s" % (_i, str(_e)[:40]))
        _bad = [x for x in _refs if isinstance(x, bool) or not isinstance(x, int)]
        if _bad:
            return None, ("`narration_lines` 第 %d 条的 `fact_refs` 里有非整数项（%s）"
                          "——必须写整数序号" % (_i, str(_bad[0])[:20]))
        _out.append({"text": _e.get("text").strip(),
                     "source": _e.get("source").strip(),
                     "fact_refs": [int(x) for x in _refs]})
    return _out, ""

def _brief_ids(items, cap: int = None) -> str:
    cap = _pv("_ID_BRIEF_CAP") if cap is None else cap
    """身份清单渲染成一行；超过上限时给「等 **共 N 条**」而不是静默砍掉."""
    _it = [str(x) for x in items]
    if len(_it) <= cap:
        return "，".join(_it)
    return "%s 等 **共 %d 条**" % ("，".join(_it[:cap]), len(_it))

def _join_replan_problems(lines: list) -> str:
    """把**逐轴收集**到的验收问题一次给全（不再只回第一条、也不再 `[:2]` 截断）.

    复核之六已确立的口径（「反馈保留完整问题身份：总数 / 类别计数 / code｜路径」）
    在操作式修订这条路上此前没落地：`apply_replan_ops` 每个轴各自 `return`，
    第一道不过后面的判据**一次都不跑**；声明门结尾又 `[:2]` 截断。实测章 1 三轮
    （2026-10-04）的反馈量分别是 1 / 1 / 2 条，而响应里实际残留 1 / ≥3 / 6 条
    ——模型每轮只看到问题集的一角，额度耗尽时终局记成 `illegal_response`，
    看起来像「模型违约」，实际是反馈没有把话说完（交付台账 §19.1）。
    """
    _items = [str(x).strip() for x in (lines or []) if str(x).strip()]
    if not _items:
        return ""
    if len(_items) == 1:
        return _items[0]
    return ("本轮响应仍有 **%d 条**问题（**逐条列全**，此前只回第一条）：\n%s"
            % (len(_items),
               "\n".join("%d) %s" % (_i, _t) for _i, _t in enumerate(_items, 1))))

def _plan_invariants_intact(old: dict, new: dict) -> str:
    """重排版本必须守住的不变量；返回 "" = 通过.

    2026-09-26 复核发现：只靠 `_plan_evidence_intact` 挡不住「**删掉一条必保留事实**
    或**调换事件顺序**，同时让剩下的条目都带合法引文」——那只核对了新稿里**仍然存在**
    的条目，对「少了什么 / 顺序变了没有」完全不看（实测：删事实 + 倒序 main_events
    仍判通过）。而 `_plan_core_preserved` 恰恰是因为这些字段**变了**才把控制权交过来，
    所以这一段必须逐项比对：

    - `main_events`：**逐项且同序**一致（事件顺序不得调换，事实不得删除）；
    - `required_facts`：旧条目**一条都不许少**（允许新增——重排只会让承载更明确）；
    - `climax`：不得改动。
    """
    _old = old or {}
    _new = new or {}
    if list(_old.get("main_events") or []) != list(_new.get("main_events") or []):
        return "main_events 被删除或调换了顺序"
    # 承载段（`｜承载：…`）是**后加的**登记信息，加它不等于改了事实本体 ⇒ 比对前剥掉
    _old_facts = [_strip_fact_carrier(f) for f in (_old.get("required_facts") or [])]
    _new_facts = [_strip_fact_carrier(f) for f in (_new.get("required_facts") or [])]
    _miss = [f for f in _old_facts if f not in _new_facts]
    if _miss:
        return "必保留事实被删除 %d 条（如 %s）" % (len(_miss), str(_miss[0])[:26])
    if str(_old.get("climax") or "") != str(_new.get("climax") or ""):
        return "climax 被改动"
    return ""

def _plan_evidence_intact(plan: dict, novel_text: str) -> str:
    """重排过节拍/载体的**新计划版本**的原文证据核验；返回 "" = 通过.

    为什么需要：单镜可表示性触发的重选，**允许改动 `shot_beats` 的正文与载体**
    （2026-09-26 用户决定：冯检校的长句出处通篇没有句末标点，只能把该节拍按原文
    顺序拆成「画面动作」+「由别人在原文里的完整短句承载同一事实」）。这是对
    `_plan_core_preserved` 的**例外**，所以必须换一道**更严**的闸，而不是把核心字段
    校验放宽：新版本里**每一条节拍、每一条必保留事实都必须带逐字可核的原文引文**。

    引文按「省略号分段，任一段 ≥4 字逐字命中原文」判定——模型引用时惯用
    「前段……」（如 `"杨旭让人宰了……"`），整串包含会因为省略号残留而永远不命中
    （与 `_check_grounding` 的单字/截断问题同源），故按段取最长命中。
    """
    if not str(novel_text or "").strip():
        return "无原文可核（调用方未传原文）"
    from step01_script_gen.plan_feasibility import _source_norm
    novel_norm = _source_norm(novel_text)

    _MIN_RUN = 8      # 长引文：至少要有这么长的**连续**逐字命中
    _MIN_SHORT = 4    # 短引文：整段必须逐字命中；归一化后不足这个长度不算证据

    def _hit(text: str) -> bool:
        """引文（按省略号分段）是否**逐字有据**（每段单独判定，任一段成立即算）.

        为什么不用「整段包含」：模型抄引文经常少一两个字（实测章 2
        `"这人虽是一身寒酸…"`，原文是「**这**个人虽是…」——丢了开头一个「个」），
        整段包含会把这种**内容忠实**的引文判死，机制永远跑不动。
        为什么不用「5 字滑窗任一命中」（`validation._check_grounding` 的口径）：
        那是给**改写过的旁白**用的最松档，拿它当「节拍有没有原文依据」的闸太松，
        伪造的节拍靠常用词组就能蒙过去。取「≥8 字连续命中」——单一规则、可陈述，
        少抄一两字不误杀、编造节拍过不去。

        短引文（归一化后 4-7 字，如 `"住嘴！"`）走**整段**逐字：这类引文本来就短，
        再允许少抄字就等于不设闸；归一化后不足 4 字的（纯标点/两字词）**不算证据**，
        避免拿 `"好！"` 这种当原文依据。
        """
        for quoted in _pc("_evidence_quotes")(text):
            for seg in re.split(r"…+|\.{2,}", quoted):
                seg = _source_norm(seg.strip())
                if len(seg) < _MIN_SHORT:
                    continue
                if len(seg) < _MIN_RUN:
                    if seg in novel_norm:
                        return True
                    continue
                if any(seg[i:i + _MIN_RUN] in novel_norm
                       for i in range(len(seg) - _MIN_RUN + 1)):
                    return True
        return False

    for i, beat in enumerate((plan or {}).get("shot_beats") or [], 1):
        if "原文依据" not in str(beat or ""):
            return "节拍 %d 缺「原文依据」（重排过的版本必须逐条给原文出处）" % i
        if not _hit(beat):
            return "节拍 %d 的原文依据逐字核不过：%s" % (i, str(beat)[:60])
    for i, fact in enumerate((plan or {}).get("required_facts") or [], 1):
        if "原文证据" not in str(fact or ""):
            return "必保留事实 %d 缺「原文证据」" % i
        if not _hit(fact):
            return "必保留事实 %d 的原文证据逐字核不过：%s" % (i, str(fact)[:60])
    return ""

def _strip_fact_carrier(fact) -> str:
    """去掉事实条目里的承载段（不变量比对用：加了承载 ≠ 改了事实本体）."""
    from step01_script_gen.plan_feasibility import strip_fact_carrier
    return strip_fact_carrier(fact)

def _plan_carrier_declarations_intact(new: dict, ops: dict, n_lines: int, *,
                                      n_narration=None, problems=None) -> str:
    """重选**替换了选用台词**时，必须逐项声明事实与钩子的承载；返回 "" = 通过.

    2026-09-30 复核裁决（第二章「计划选 5 句、审计却报漏句」的实质）：

    - `required_facts` 的「原文证据」是事实的**出处**，旧台词被换掉**不使出处失效**
      ⇒ 既不机械改写引文，也**不**仅因出处引用了未选台词就拒收；
    - 真正的缺口是**事实/钩子没有明确的成片承载**（实测：F2「四年前潜入青州」无承载；
      `end_hook` 要求「喃喃自语」绣春刀那句，而所选 5 句不含它）。审计报的其实是
      **内容缺失**，只是归因写成了「漏选用台词」——生成侧只按 `dialogue_lines` 写、
      审计侧按事实/钩子判，两边各自自洽，修复轮永远改不动；
    - 因此：替换台词时，重选结果必须**逐项**声明每条必保留事实的承载节拍与表达方式；
      **不过就拒收本轮**（不接受带警告放行，也不自动加重选轮次）。

    `problems` 给了就**逐条并入**那份收集箱、本函数返回 `""`（由调用方一次性渲染成
    反馈）——实检几条就回几条，不再 `[:2]` 截断。

    ⚠️ 本函数只做两件事：**把 ops 声明落进计划**（持久字段，随执行契约传给生成器）
    + 调 `plan_feasibility.plan_contract_problems`（**首版/重选版/缓存版共用的那道门**）。
    判据**不在这里实现**——同一套承载规则维护两份，等于改一处、另一处照旧。

    作用范围：`ops` 里没有 `dialogue_ops` 时不做这套校验——没换台词 ⇒ 承载关系不变，
    沿用既有的证据/不变量/承载闭合三项校验；首版与缓存版的承载由生成前统一入口
    （`check_plan_before_generation`）的契约门判。
    """
    if not isinstance(ops, dict):
        return ""
    from step01_script_gen.plan_feasibility import (
        FACT_CARRIER_SEG, carrier_mode_problem, coerce_ref_index, effective_decl_ops,
        parse_carrier_decl, plan_contract_problems, plan_has_carrier_contract,
        plan_narration_entries, strip_fact_carrier)
    # 改台词/节拍 ⇒ 编号可能顺延，承载必须逐项重声明（既有裁决）；**只有声明**的批次
    # 也必须走到这里（复核第 45 轮：旧实现在入口就落 `no_change`，声明根本改不动）。
    _touched_ids = bool(ops.get("dialogue_ops") or ops.get("beat_ops"))
    if not _touched_ids and not effective_decl_ops(ops):
        return ""
    facts = list((new or {}).get("required_facts") or [])
    decls = (ops or {}).get("fact_carriers")
    _hc_raw = ops.get("hook_carrier")
    # 2026-09-30 复核（第 3 组）：承载维护**不能按「本批改没改台词」决定**。
    # 只要**计划带承载契约**（v2：节拍标了载体 / 事实声明了承载），任何会改变节拍编号
    # 或台词编号的操作（**含 `beat_ops` 的插入与拆分**）都必须把承载重新声明一遍——
    # 否则 `承载：Bn/…` 会指到顺延后的别条节拍上，且新旧编号都「在范围内」，静默错位。
    # 没有承载契约的历史计划沿用容忍口径（无可核 ≠ 被破坏），由生成前的统一入口判。
    if not plan_has_carrier_contract(new) and not decls and not _hc_raw:
        return ""
    n_beats = len((new or {}).get("shot_beats") or [])
    # `旁白k` 的合法范围按**本批完成后**的表算（调用方已落地 `narration_lines`；
    # 复核 2026-10-02 P1）：缺参数时退回旧行为（本函数的历史调用方）。
    if n_narration is None:
        n_narration = len(plan_narration_entries(new))
    if decls is None and not _touched_ids:
        decls = []          # 声明独立修改：没提 `fact_carriers` = 不改承载
    if decls is not None and not isinstance(decls, list):
        return ("`fact_carriers` 必须是对象数组（现在是 %s）——每条给出 "
                "{index, beat, mode}" % type(decls).__name__)
    if not isinstance(decls, list):
        return ("本批改动了节拍/台词，必须逐项声明必保留事实的承载（缺 fact_carriers）："
                "每条事实都要给出承载节拍与表达方式，否则承载会指到顺延后的别条节拍上，"
                "或变成「审计报内容缺失、生成侧无从遵从」")
    by_index: dict = {}
    for d in decls:
        if not isinstance(d, dict):
            return "fact_carriers 条目必须是对象：%s" % str(d)[:40]
        _i = coerce_ref_index(d.get("index"), "")
        if _i is None:
            return "fact_carriers 条目的 index 必须是数字：%s" % str(d)[:40]
        if _i in by_index:
            return "必保留事实 F%d 的承载被声明了多次" % _i
        by_index[_i] = d
    for _i in sorted(by_index):
        if _i < 1 or _i > len(facts):
            return "fact_carriers 指向不存在的必保留事实 F%d（共 %d 条）" % (_i, len(facts))
    # **逐项给全**只对"改了台词/节拍"的批次成立（编号顺延会静默错位）；**声明独立修改**
    # 只落声明到的那几条——否则"只清钩子承载"这种批次会因为 F1 没被重新声明而被拒。
    _missing = ([i for i in range(1, len(facts) + 1) if i not in by_index]
                if _touched_ids else [])
    if _missing:
        return ("必保留事实 %s 未声明承载——替换台词后每条事实都必须有承载节拍与"
                "表达方式（否则审计会报内容缺失，而生成侧无从遵从）"
                % "、".join("F%d" % i for i in _missing[:4]))
    # ---- ① 声明落进计划：`｜承载：Bn/mode`（本体与原文证据一字不改）+ hook_carrier ----
    # 结构先判（工作包 §4 C：**越界编号 ⇒ 非法响应**）：种类与编号范围在这里挡，
    # "这条承载是否真演出了事实""画面/声音是否一致"这类**语义**仍归统一契约门。
    # 未声明到的条目**原样保留**（声明独立修改是"逐条打补丁"；整表重写只在
    # `_touched_ids` 那条路径上发生——那时 `_missing` 已经要求逐项给全）
    _segs = [str(f) for f in facts]
    for _i in sorted(by_index):
        _d = by_index[_i]
        _bi = coerce_ref_index(_d.get("beat"), "")
        if _bi is None or not 1 <= int(_bi) <= n_beats:
            return ("fact_carriers 的承载节拍越界：F%d → B%s（共 %d 条节拍）"
                    % (_i, _d.get("beat"), n_beats))
        _mode = str(_d.get("mode") or "").strip()
        _mp = carrier_mode_problem(_mode, n_lines, n_narration)
        if _mp:
            return "fact_carriers F%d：%s" % (_i, _mp)
        # 只对**声明到的那条**去掉旧承载段再写新声明（未声明的条目原样保留）
        _segs[_i - 1] = "%s｜%sB%d/%s" % (strip_fact_carrier(_segs[_i - 1]),
                                          FACT_CARRIER_SEG, int(_bi), _mode)
    new["required_facts"] = _segs
    # `narration_lines` **不在这里处理**：它已由 `apply_replan_ops` 在入口处
    # 归一并落地——旁白条数要先算出来，才能校验本批写下的所有 `旁白k` 引用
    # （复核 2026-10-02 P1：同批新增旁白并指定承载节拍曾被判越界）。
    if ops.get("hook_carrier") is not None:          # `null` = 缺席 = 不改
        _hc_txt = _hook_carrier_decl_text(ops.get("hook_carrier"))
        if not _hc_txt:
            # 显式空值 = **清掉**旧声明（2026-09-30 复核第 4 轮 P2-4；2026-10-02 起 schema
            # 不再强制这个键 ⇒ "不改"由**省略**表达，"清除"由空串表达，两者不再混同）。
            # 旧实现只在非空时覆盖，于是「钩子不再要求说出口」这一批会把旧声明留在计划里
            # → 变成「声明了承载、却没有要求说出口的句子」，被多声明判据拒收、且无法修复。
            # **复核之十二：清除一个空声明必须幂等**——`hook_carrier: ""` 与"没有这个键"
            # 是**同一个状态**（首版严格 schema 下这个键恒为 `""`），而旧写法无条件 `pop`：
            # 于是"清除"本身被记成内容变过（空串 → 键消失），`no_change` 判不出来、
            # 计划也白白多了一次无意义的写。
            if str(new.get("hook_carrier") or "").strip():
                new.pop("hook_carrier", None)
        else:
            # **两种形态走同一条校验**（2026-10-02 复核 P2）：编号范围与载体种类都在这里
            # 挡掉，越界/非法一律拒收本轮，不许带着坏声明进后续验收。
            for _d in _hc_txt.split("；"):
                _decl = _d.strip()
                if not _decl:
                    continue
                _b, _kind, _k = (parse_carrier_decl(_decl) or (None, None, None))
                if _b is None:
                    return ("`hook_carrier` 里的声明非法：%r（应为 `B2/台词1` 或 `B2/画面`）"
                            % _decl[:24])
                if not 1 <= int(_b) <= n_beats:
                    return "`hook_carrier` 的节拍越界：%s（共 %d 条节拍）" % (_decl[:24], n_beats)
                _mp = carrier_mode_problem(("画面" if _kind == "画面" else "%s%s" % (_kind, _k)),
                                           n_lines, n_narration)
                if _mp:
                    return "`hook_carrier`：%s" % _mp
            new["hook_carrier"] = _hc_txt
    if ops.get("hook_spoken") is not None:           # `null` = 缺席 = 不改
        _hs = ops.get("hook_spoken")
        if isinstance(_hs, str):
            _hs = [_hs] if _hs.strip() else []
        if not isinstance(_hs, list):
            return ("`hook_spoken` 必须是字符串数组（现在是 %s）——钩子要求说出口的"
                    "台词逐字给全；不再要求时说出口的句子就给空数组" % type(_hs).__name__)
        _bad = [x for x in _hs if not isinstance(x, str) or not x.strip()]
        if _bad:
            return ("`hook_spoken` 里有空/非法条目（%r）——必须是原文逐字台词"
                    % (str(_bad[0])[:20],))
        new["hook_spoken"] = [x.strip() for x in _hs]
    # ---- ② 改过台词/节拍的批次：结果必须当场闭合契约（既有裁决：不过就拒收本轮）----
    if not _touched_ids:
        # **声明独立修改**：没有编号顺延的风险，语义问题（承载是否真演出、画面是否一致）
        # 交**同一条**验收链判断——拒收/继续由控制器按统一停止原因收口（工作包 §4 C）。
        return ""
    # 校验交给共用契约门（编号/范围/语义交集/画面一致/钩子整句）
    _probs = list(plan_contract_problems(new, require_verification=False))
    if problems is not None:
        # 逐条并入调用方的收集箱（**不截断**）：旧写法 `[:2]` 让实检 6 条只回 2 条
        # （章 1 第 3 轮的实测形态），与「反馈保留完整问题身份」的口径相反。
        problems.extend(_probs)
        return ""
    return "；".join(_probs[:2])

def _hook_carrier_decl_text(raw) -> str:
    """把 `hook_carrier` 的**受支持形态**归一成声明串（空串 = 清除）.

    形态两种（历史兼容）：声明串 `"B3/台词2；B5/画面"`、对象 `{"beat": 3, "mode": "台词2"}`。
    **归一之后走同一条校验**——2026-10-02 复核 P2：旧实现给对象形态单开一条分支，直接
    拼出 `B{beat}/{mode}` 写进计划，越界编号（`B999/台词1`）与非法载体一律放行、记成
    "应用成功"，问题要等到后续验收才出现（C 的规定是越界操作**先判 `illegal_response`**）。
    """
    if isinstance(raw, dict):
        _hb = raw.get("beat")
        return "B%s/%s" % (str("" if _hb is None else _hb).strip(),
                           str(raw.get("mode") or "").strip())
    return str(raw or "").strip()

def _brief_value(value, limit: int = 60) -> str:
    """非法元素的**短摘要**（诊断与回灌提示用；不落整份对象）."""
    _s = repr(value)
    return _s if len(_s) <= limit else _s[:limit] + "…"
