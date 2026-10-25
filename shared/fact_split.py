"""受控事实拆分的账本、折叠与应用（`fact_split.v1`）.

设计边界见 `docs/aivideo_step01_outline-governance-v9-2026-10-24.md` §3、§7：

- 账本只由代码写；本模块是**纯函数**，不调模型、不读文件、不写产物；
- `load_problems` 是账本的**装载校验**（身份、形状、父子一致性、证据来源、判定绑定）；
- `collapse` 把子事实折回父义务，供 `source_revision.admission_problems` 复用既有
  v1/v2 规则——它只改**读法**，不改 `baseline_facts` / `targets` / `source_preservation`；
- `split_state` 由消费者按证据计算 `invalid / pending / effective`，不持久化 `closed`；
- `apply_split_view` 是唯一允许改事实表的入口；不走 `apply_editable_view`，也不放宽
  `_frozen_facts_intact`。

P0 只覆盖：稳定 uid、父→子拆分、折叠准入、投影回读。narrow / 建声音 / 模型判定
属于后续包；本模块遇到非空 `narrowed_voices` / `beat_text` 一律 `split_operation_unsupported`。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from . import presentation_contract as pc

CONTEXT_FIELD = "fact_split_context"
SOURCE_FIELD = "source_revision_context"
VERSION = "fact_split.v1"
MIN_CHILDREN = 2
MAX_CHILDREN = 4

_PARENT_RE = re.compile(r"^f\d{2}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KINDS = (pc.KIND_VISUAL, pc.KIND_DIALOGUE, pc.KIND_NARRATION)
_VERDICTS = ("supported", "partial", "contradicted", "unknown")


def _rec(code, kind, uid, reason, path="") -> dict:
    """与 `presentation_contract` 同形的问题记录（不 import 私有 helper）."""
    return {"code": str(code), "kind": str(kind), "uid": str(uid or ""),
            "ref": str(uid or ""), "path": str(path or ""),
            "reason": str(reason), "fix": ""}


def _digest(value) -> str:
    _t = json.dumps(value, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str)
    return hashlib.sha256(_t.encode("utf-8")).hexdigest()


def inputs_digest(parent_claim, children) -> str:
    """M 判定的输入身份：**父主张 + 全部子主张**（不含证据）."""
    _claims = [str((c or {}).get("claim") or "") for c in (children or [])]
    return _digest({"parent_claim": str(parent_claim or ""), "claims": _claims})


def context(view) -> dict | None:
    """计划/视图里的拆分账本；缺席返回 None."""
    _pb = (view or {}).get("plan_basis")
    return _pb.get(CONTEXT_FIELD) if isinstance(_pb, dict) else None


def _source_context(view) -> dict | None:
    _pb = (view or {}).get("plan_basis")
    return _pb.get(SOURCE_FIELD) if isinstance(_pb, dict) else None


def _facts(view) -> list:
    return [f for f in ((view or {}).get("frozen_facts") or []) if isinstance(f, dict)]


def _fact_map(view) -> dict:
    return {str(f.get("uid") or ""): f for f in _facts(view)}


def _baseline(view) -> dict | None:
    """来源上下文的基线事实表；上下文缺席/不可读返回 None."""
    _ctx = _source_context(view)
    if not isinstance(_ctx, dict) or not isinstance(_ctx.get("baseline_facts"), list):
        return None
    _out = {}
    for _row in _ctx["baseline_facts"]:
        if (isinstance(_row, dict) and isinstance(_row.get("uid"), str)
                and isinstance(_row.get("claim"), str)):
            _out[_row["uid"]] = _row["claim"]
    return _out


def _dotted(view) -> list:
    return sorted(uid for uid in _fact_map(view) if "." in uid)


def _unit_uids(view, kind) -> set:
    _key = {pc.KIND_DIALOGUE: "dialogue_units",
            pc.KIND_NARRATION: "narration_units"}.get(kind)
    if _key is None:
        return {pc.ref_uid(b) for b in ((view or {}).get("beats") or [])
                if isinstance(b, dict)}
    return {pc.ref_uid(u) for u in ((view or {}).get(_key) or [])
            if isinstance(u, dict)}


def _child_uid_problem(parent_uid, child_uid, index) -> str:
    if not isinstance(child_uid, str) or not child_uid:
        return "子事实 %d 缺 uid" % index
    _pfx = str(parent_uid) + "."
    if not child_uid.startswith(_pfx) or child_uid == _pfx:
        return "子 uid %r 必须写成 `%s.n`（父 uid + 点 + 数字）" % (child_uid, parent_uid)
    _suffix = child_uid[len(_pfx):]
    if not _suffix.isdigit() or _suffix.startswith("0"):
        return "子 uid %r 的后缀必须是正整数（1、2、…），且不得再嵌套点号" % child_uid
    return ""


def _evidence_ok(evidence, spans) -> bool:
    _ev = str(evidence or "")
    return bool(_ev) and any(_ev in str(s) for s in (spans or []) if str(s))


def _entry_problems(view, entry, *, index=0) -> list:
    """一条账本条目的形状与父子一致性（装载与状态共用）."""
    _p: list = []
    _path = "%s.splits[%d]" % (CONTEXT_FIELD, index)
    if not isinstance(entry, dict):
        return [_rec("verify_fact_split_context", "split", "",
                     "%s 必须是对象" % _path, _path)]
    _parent = str(entry.get("parent_uid") or "")
    if not _PARENT_RE.fullmatch(_parent):
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "父 uid %r 不合法（只允许 `fNN`；本版不允许嵌套拆分）" % _parent,
                       _path + ".parent_uid"))
        return _p
    _facts = _fact_map(view)
    _bindings = {str(b.get("fact_uid") or ""): b.get("carriers")
                 for b in (view.get("fact_bindings") or []) if isinstance(b, dict)}
    if str(entry.get("parent_claim") or "").strip() == "":
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "`parent_claim` 不能为空", _path + ".parent_claim"))
    if str(entry.get("parent_evidence") or "").strip() == "":
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "`parent_evidence` 不能为空", _path + ".parent_evidence"))
    _spans = entry.get("parent_evidence_spans")
    if (not isinstance(_spans, list) or not _spans
            or any(not isinstance(s, str) or not s for s in _spans)):
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "`parent_evidence_spans` 必须是非空字符串数组",
                       _path + ".parent_evidence_spans"))
        _spans = []
    _kids = entry.get("children")
    if not isinstance(_kids, list):
        _p.append(_rec("split_children_invalid", "split", _parent,
                       "`children` 必须是数组", _path + ".children"))
        return _p
    _claims = [str((c or {}).get("claim") or "") for c in _kids if isinstance(c, dict)]
    if not MIN_CHILDREN <= len(_kids) <= MAX_CHILDREN:
        _p.append(_rec("split_children_out_of_range", "split", _parent,
                       "本包只接受 %d–%d 个子事实（现在 %d 条）"
                       % (MIN_CHILDREN, MAX_CHILDREN, len(_kids)),
                       _path + ".children"))
    _seen: set = set()
    for _i, _kid in enumerate(_kids, 1):
        _kp = "%s.children[%d]" % (_path, _i - 1)
        if not isinstance(_kid, dict):
            _p.append(_rec("split_children_invalid", "split", _parent,
                           "子事实必须是对象", _kp))
            continue
        _uid = str(_kid.get("uid") or "")
        _why = _child_uid_problem(_parent, _uid, _i)
        if _why:
            _p.append(_rec("split_children_invalid", "split", _parent, _why, _kp + ".uid"))
        if _uid in _seen:
            _p.append(_rec("split_children_invalid", "split", _parent,
                           "子 uid %r 重复" % _uid, _kp + ".uid"))
        _seen.add(_uid)
        if _uid not in _facts:
            _p.append(_rec("split_child_missing", "fact", _uid,
                           "子事实 %r 不在当前事实表里" % _uid, _kp))
            continue
        _row = _facts[_uid]
        if str(_kid.get("claim") or "") != str(_row.get("claim") or ""):
            _p.append(_rec("split_child_claim_mismatch", "fact", _uid,
                           "子事实 %r 的主张与账本不一致" % _uid, _kp + ".claim"))
        if str(_kid.get("evidence") or "") != str(_row.get("evidence") or ""):
            _p.append(_rec("split_child_claim_mismatch", "fact", _uid,
                           "子事实 %r 的证据与账本不一致" % _uid, _kp + ".evidence"))
        if _spans and not _evidence_ok(_kid.get("evidence"), _spans):
            _p.append(_rec("split_evidence_not_derived", "fact", _uid,
                           "子事实 %r 的证据不是父已准入 evidence span 的逐字子串" % _uid,
                           _kp + ".evidence"))
        _car = _kid.get("carrier")
        if not isinstance(_car, dict) or str(_car.get("kind") or "") not in _KINDS \
                or not str(_car.get("uid") or ""):
            _p.append(_rec("split_children_invalid", "fact", _uid,
                           "子事实 %r 必须有单个载体 {kind, uid}" % _uid, _kp + ".carrier"))
        else:
            _p.extend(_carrier_problems(view, _car))
            _actual = _bindings.get(_uid)
            if not isinstance(_actual, list) or len(_actual) != 1:
                _p.append(_rec("split_child_carrier_mismatch", "fact", _uid,
                               "子事实 %r 的当前绑定必须恰好一个载体（账本说 %s:%s）"
                               % (_uid, _car.get("kind"), _car.get("uid")),
                               _kp + ".carrier"))
            else:
                _a = _actual[0] if isinstance(_actual[0], dict) else {}
                if (str(_a.get("kind") or ""), str(_a.get("uid") or "")) != (
                        str(_car.get("kind") or ""), str(_car.get("uid") or "")):
                    _p.append(_rec("split_child_carrier_mismatch", "fact", _uid,
                                   "账本载体 %s:%s 与当前绑定 %s:%s 不一致"
                                   % (_car.get("kind"), _car.get("uid"),
                                      _a.get("kind"), _a.get("uid")),
                                   _kp + ".carrier"))
    if len(set(_claims)) != len(_claims):
        _p.append(_rec("split_children_invalid", "split", _parent,
                       "子主张不得重复", _path + ".children"))
    if _parent in _facts:
        _p.append(_rec("split_parent_still_present", "fact", _parent,
                       "父事实 %r 仍在当前事实表里——拆分没有替换它" % _parent,
                       _path + ".parent_uid"))
    _auth = entry.get("authority")
    if not isinstance(_auth, dict):
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "缺 authority 记录", _path + ".authority"))
    else:
        if str(_auth.get("fact_uid") or "") != _parent:
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "authority.fact_uid 与父 uid 不一致", _path + ".authority"))
        if str(_auth.get("code") or "") != "verify_fact_unsupported":
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "拆分授权必须来自 `verify_fact_unsupported`", _path + ".authority"))
        if str(_auth.get("verdict") or "") not in ("partial", "unknown"):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "拆分授权的展示轴 verdict 必须是 partial/unknown",
                           _path + ".authority"))
    _judge = entry.get("judgment")
    if _judge is not None:
        if not isinstance(_judge, dict):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "`judgment` 必须是对象或省略", _path + ".judgment"))
        else:
            _v = str(_judge.get("verdict") or "")
            if _v not in _VERDICTS:
                _p.append(_rec("verify_fact_split_context", "split", _parent,
                               "judgment.verdict 非法：%r" % _v, _path + ".judgment.verdict"))
            _want = inputs_digest(entry.get("parent_claim"), _kids)
            if str(_judge.get("inputs_digest") or "") != _want:
                _p.append(_rec("verify_fact_split_context", "split", _parent,
                               "judgment.inputs_digest 与父/子主张不一致（判定已失效）",
                               _path + ".judgment.inputs_digest"))
            _p.extend(judgment_problems(entry))
    _nv = entry.get("narrowed_voices")
    if _nv is None:
        _nv = []
    if not isinstance(_nv, list) or any(not isinstance(x, dict) for x in _nv):
        _p.append(_rec("verify_fact_split_context", "split", _parent,
                       "`narrowed_voices` 必须是对象数组", _path + ".narrowed_voices"))
    return _p


def judgment_problems(entry, *, require_supported=False) -> list:
    """M 调用证据/代码盖章检查；自报 supported 不能代替真实 M 执行."""
    _j = (entry or {}).get("judgment") if isinstance(entry, dict) else None
    if not isinstance(_j, dict):
        return [_rec("split_m_judgment_missing", "split",
                     str((entry or {}).get("parent_uid") or ""),
                     "拆分账本缺 M judgment")]
    _p: list = []
    if str(_j.get("mode") or "") != "model":
        _p.append(_rec("split_m_mode_invalid", "split",
                       str((entry or {}).get("parent_uid") or ""),
                       "M judgment.mode 必须是 model"))
    if not str(_j.get("call_id") or "").strip():
        _p.append(_rec("split_m_call_missing", "split",
                       str((entry or {}).get("parent_uid") or ""),
                       "M judgment 缺 call_id"))
    for _k in ("prompt_digest", "response_digest"):
        _v = str(_j.get(_k) or "")
        if len(_v) != 64 or any(ch not in "0123456789abcdef" for ch in _v):
            _p.append(_rec("split_m_digest_invalid", "split",
                           str((entry or {}).get("parent_uid") or ""),
                           "M judgment.%s 不是 sha256" % _k))
    if require_supported and str(_j.get("verdict") or "") != "supported":
        _p.append(_rec("split_m_not_supported", "split",
                       str((entry or {}).get("parent_uid") or ""),
                       "M 判定不是 supported"))
    return _p


def load_problems(view) -> list:
    """账本装载校验；**消费前必跑**。返回空表才可用于折叠/采纳."""
    _ctx = context(view)
    if _ctx is None:
        # 没有账本 = 这条拆分不是本事务产生的。子 uid 的合法性交回既有 v1/v2
        # （v2 的 `父uid.后缀` 是**来源修正**自己的拆分规则，不要求本账本）。
        # 账本存在时才校验"每个子 uid 都有出处"，见下方 `_recorded_kids`。
        return []
    if not isinstance(_ctx, dict):
        return [_rec("verify_fact_split_context", "view", "",
                     "`fact_split_context` 必须是对象", CONTEXT_FIELD)]
    _p: list = []
    if str(_ctx.get("version") or "") != VERSION:
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "未知的拆分账本版本：%r" % _ctx.get("version"),
                       CONTEXT_FIELD + ".version"))
    if not _HEX64.fullmatch(str(_ctx.get("novel_digest") or "")):
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "`novel_digest` 必须是 64 位十六进制", CONTEXT_FIELD + ".novel_digest"))
    if not _HEX64.fullmatch(str(_ctx.get("base_digest") or "")):
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "`base_digest` 必须是 64 位十六进制", CONTEXT_FIELD + ".base_digest"))
    _splits = _ctx.get("splits")
    if not isinstance(_splits, list) or not _splits:
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "`splits` 必须是非空数组", CONTEXT_FIELD + ".splits"))
        return _p
    _parents: list = []
    _recorded_kids: set = set()
    _baseline_claims = _baseline(view)
    for _i, _entry in enumerate(_splits):
        _p.extend(_entry_problems(view, _entry, index=_i))
        if not isinstance(_entry, dict):
            continue
        _parent = str(_entry.get("parent_uid") or "")
        _parents.append(_parent)
        for _kid in (_entry.get("children") or []):
            if isinstance(_kid, dict):
                _recorded_kids.add(str(_kid.get("uid") or ""))
        _auth = _entry.get("authority") if isinstance(_entry.get("authority"), dict) else {}
        if not _HEX64.fullmatch(str(_auth.get("plan_digest") or "")):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "authority.plan_digest 必须是 64 位十六进制",
                           "%s.splits[%d].authority.plan_digest" % (CONTEXT_FIELD, _i)))
        _bl = _entry.get("baseline_claim")
        if _baseline_claims is None:
            if _bl is not None:
                _p.append(_rec("verify_fact_split_context", "split", _parent,
                               "没有可读来源上下文时 baseline_claim 必须为 null",
                               "%s.splits[%d].baseline_claim" % (CONTEXT_FIELD, _i)))
        elif _parent in _baseline_claims:
            if not isinstance(_bl, str) or _bl != _baseline_claims[_parent]:
                _p.append(_rec("verify_fact_split_context", "split", _parent,
                               "baseline_claim 必须等于来源上下文里的父基线主张",
                               "%s.splits[%d].baseline_claim" % (CONTEXT_FIELD, _i)))
        elif _bl is not None:
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "父不在来源基线里（历史拆分）时 baseline_claim 必须为 null",
                           "%s.splits[%d].baseline_claim" % (CONTEXT_FIELD, _i)))
    if len(set(_parents)) != len(_parents):
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "同一父 uid 在账本里出现了多次", CONTEXT_FIELD + ".splits"))
    _kid_rows = [str(k.get("uid") or "") for _e in _splits if isinstance(_e, dict)
                 for k in (_e.get("children") or []) if isinstance(k, dict)]
    if len(set(_kid_rows)) != len(_kid_rows):
        _p.append(_rec("verify_fact_split_context", "view", "",
                       "同一子 uid 在账本里出现了多次", CONTEXT_FIELD + ".splits"))
    _dotted_uids = _dotted(view)
    for _uid in _dotted_uids:
        if _uid not in _recorded_kids:
            _p.append(_rec("split_ledger_missing", "fact", _uid,
                           "子事实 %r 没有对应的账本条目" % _uid, "frozen_facts"))
    return _p


def collapse(view) -> tuple:
    """把已记录的子事实折回父义务；`returns (uid→claim | None, problems)`.

    只处理 `baseline_claim != null` 的条目（父在来源基线里）；历史拆分（上下文建立
    前就已拆）跳过。折叠只改**读法**，不改任何持久字段。
    """
    _ctx = context(view)
    if _ctx is None:
        _probs = load_problems(view)
        return (None, _probs) if _probs else \
            ({str(f.get("uid") or ""): str(f.get("claim") or "") for f in _facts(view)}, [])
    _probs = load_problems(view)
    if _probs:
        return None, _probs
    _current = {str(f.get("uid") or ""): str(f.get("claim") or "") for f in _facts(view)}
    for _entry in _ctx.get("splits") or []:
        if not isinstance(_entry, dict) or _entry.get("baseline_claim") is None:
            continue
        _parent = str(_entry.get("parent_uid") or "")
        for _kid in _entry.get("children") or []:
            _current.pop(str((_kid or {}).get("uid") or ""), None)
        _current[_parent] = str(_entry.get("parent_claim") or "")
    return _current, []


def split_state(view, entry, *, record=None, plan_digest="") -> str:
    """消费者按证据计算的状态：`invalid | pending | effective`（不读持久化布尔）."""
    if not isinstance(entry, dict):
        return "invalid"
    _ctx = context(view)
    _index = 0
    if isinstance(_ctx, dict):
        for _i, _row in enumerate(_ctx.get("splits") or []):
            if _row is entry or (isinstance(_row, dict)
                                 and _row.get("parent_uid") == entry.get("parent_uid")):
                _index = _i
                break
    if _entry_problems(view, entry, index=_index):
        return "invalid"
    _judge = entry.get("judgment")
    if not isinstance(_judge, dict) or str(_judge.get("verdict") or "") != "supported":
        return "pending"
    if not isinstance(record, dict):
        return "pending"
    # **从当前候选算摘要**，不能只比较两个调用方传入的值：候选在拆分后被改过，
    # 旧记录 + 旧摘要会一起“自洽”，但已经不是同一个计划。
    from .content_verification import plan_digest_of
    try:
        _current = plan_digest_of(pc.plan_from_view(view))
    except pc.PresentationContractError:
        return "invalid"
    if plan_digest and str(plan_digest) != _current:
        # 外部摘要只作额外断言；与当前候选不符 ⇒ 调用方状态已错，判 invalid。
        return "invalid"
    if str(record.get("plan_digest") or "") != _current:
        return "pending"
    if str(record.get("status") or "") != "ok":
        return "pending"
    _facts = {str((r or {}).get("fact_uid") or ""): r
              for r in (record.get("facts") or []) if isinstance(r, dict)}
    _src = {str((r or {}).get("fact_uid") or ""): r
            for r in (record.get("source_support") or []) if isinstance(r, dict)}
    if not _src:
        # 双轴记录缺来源轴 = 还没核完，不能判 effective（缺证据不是通过）。
        return "pending"
    for _kid in entry.get("children") or []:
        _uid = str((_kid or {}).get("uid") or "")
        if str((_facts.get(_uid) or {}).get("verdict") or "") != "supported":
            return "pending"
        if str((_src.get(_uid) or {}).get("verdict") or "") != "supported":
            return "pending"
    _, _probs = collapse(view)
    if _probs:
        return "invalid"
    # collapse 成功只证明"能折叠"，不等于**来源准入**通过。有来源上下文时再跑一次准入；
    # 准入失败（跨基线、非目标改主张、账本不一致等）⇒ invalid，而不是 effective。
    from step01_script_gen import source_revision as _sr
    if _sr.context(view) is not None and _sr.admission_problems(view):
        return "invalid"
    return "effective"


def split_acceptance_problems(view, record) -> list:
    """把拆分状态接到**统一验收**：`pending/invalid` 都是正式残余."""
    _ctx = context(view)
    if not isinstance(_ctx, dict):
        return []
    _out: list = []
    for _entry in _ctx.get("splits") or []:
        if not isinstance(_entry, dict):
            continue
        _state = split_state(view, _entry, record=record)
        _parent = str(_entry.get("parent_uid") or "")
        if _state == "invalid":
            _out.append(_rec("split_invalid", "split", _parent,
                             "拆分账本/来源/候选状态无效，不能采纳"))
        elif _state == "pending":
            _out.append(_rec("split_pending", "split", _parent,
                             "拆分尚未得到候选完整核验，不能采纳"))
    return _out


def d_problems(parent, children, admitted_spans) -> list:
    """确定性 D 层：只查证据来源、uid 形状、子数与载体，不判主张完整性."""
    _p: list = []
    _parent = str((parent or {}).get("uid") or "")
    _kids = [c for c in (children or []) if isinstance(c, dict)]
    if not MIN_CHILDREN <= len(_kids) <= MAX_CHILDREN:
        return [_rec("split_children_out_of_range", "split", _parent,
                     "本包只接受 %d–%d 个子事实（现在 %d 条）"
                     % (MIN_CHILDREN, MAX_CHILDREN, len(_kids)))]
    _spans = [str(s) for s in (admitted_spans or []) if isinstance(s, str) and s]
    for _i, _kid in enumerate(_kids, 1):
        _uid = str(_kid.get("uid") or "")
        _why = _child_uid_problem(_parent, _uid, _i)
        if _why:
            _p.append(_rec("split_children_invalid", "split", _parent, _why))
        if not str(_kid.get("claim") or "").strip():
            _p.append(_rec("split_children_invalid", "split", _uid,
                           "子主张不能为空"))
        if not str(_kid.get("evidence") or "").strip():
            _p.append(_rec("split_evidence_not_derived", "split", _uid,
                           "子证据不能为空"))
        elif _spans and not _evidence_ok(_kid.get("evidence"), _spans):
            _p.append(_rec("split_evidence_not_derived", "split", _uid,
                           "子证据不是父已准入 evidence span 的逐字子串"))
        _car = _kid.get("carrier")
        if (not isinstance(_car, dict)
                or str(_car.get("kind") or "") not in _KINDS
                or not str(_car.get("uid") or "")):
            _p.append(_rec("split_children_invalid", "split", _uid,
                           "每个子事实必须有单个载体 {kind, uid}"))
    _claims = [str(c.get("claim") or "") for c in _kids]
    if len(set(_claims)) != len(_claims):
        _p.append(_rec("split_children_invalid", "split", _parent, "子主张不得重复"))
    _uids = [str(c.get("uid") or "") for c in _kids]
    if len(set(_uids)) != len(_uids):
        _p.append(_rec("split_children_invalid", "split", _parent, "子 uid 不得重复"))
    return _p


def _carrier_problems(view, carrier, *, allowed_uids=None) -> list:
    _kind = str((carrier or {}).get("kind") or "")
    _uid = str((carrier or {}).get("uid") or "")
    _p: list = []
    if _kind not in _KINDS:
        return [_rec("split_children_invalid", "split", _uid,
                     "载体 kind 必须是 %s 之一" % (_KINDS,))]
    if _uid not in _unit_uids(view, _kind):
        _p.append(_rec("split_children_invalid", "split", _uid,
                       "载体 %s:%s 不在当前视图里" % (_kind, _uid)))
    if allowed_uids is not None and _uid not in allowed_uids:
        _p.append(_rec("split_carrier_not_allowed", "split", _uid,
                       "本轮未授权把子事实绑到 %s（允许：%s）"
                       % (_uid, sorted(allowed_uids))))
    return _p


def _op_list(split_ops) -> list:
    if isinstance(split_ops, dict):
        return [split_ops]
    return [x for x in (split_ops or []) if isinstance(x, dict)]


def _scope_problems(scope) -> list:
    """拆分授权必须**显式**：`None` / `{}` 不是"不限范围"，而是缺授权."""
    if not isinstance(scope, dict):
        return [_rec("split_authorization_missing", "view", "",
                     "拆分必须有显式授权（`scope` 缺席不是不限范围）")]
    _p = []
    for _key, _label in (("allowed_split_fact_uids", "父事实"),
                         ("allowed_split_carrier_uids", "载体")):
        _v = scope.get(_key)
        if (not isinstance(_v, list) or not _v
                or any(not isinstance(x, str) or not x.strip() for x in _v)):
            _p.append(_rec("split_authorization_missing", "view", "",
                           "`%s` 必须是**非空**字符串数组（%s授权不得缺席/空白）"
                           % (_key, _label), _key))
    return _p


def _authority_problems(base_digest, ledger, entries, record, *,
                        authority_digest=None, view=None, plan=None,
                        novel_text="") -> list:
    """把账本绑定到**实际基线**与**已准入记录**（不是只比账本内部字段）.

    - `record.plan_digest` 必须等于实际基线摘要；
    - `record.novel_digest` 必须等于账本原文身份；
    - 新条目的 `authority.plan_digest/rule_version` 必须与本次基线和记录一致；
    - 父事实在记录里必须是展示轴 partial/unknown，且
      `parent_evidence_spans` 必须逐字等于记录里父事实的已准入证据。
    """
    if not isinstance(record, dict):
        return [_rec("split_authority_record_missing", "view", "",
                     "拆分必须带本轮已准入的核验记录（不能只带账本）")]
    from .content_verification import DEFAULT_RULE_VERSION, record_shape_problems
    _p: list = []
    if str(record.get("status") or "") != "ok":
        _p.append(_rec("split_authority_record_not_ok", "view", "",
                       "授权记录 status 不是 ok——失败/未完成的记录不能授权拆分",
                       "status"))
    if str(record.get("rule_version") or "") != DEFAULT_RULE_VERSION:
        _p.append(_rec("split_authority_record_mismatch", "view", "",
                       "授权记录不是当前核验规则版本（期望 %s，实际 %r）"
                       % (DEFAULT_RULE_VERSION, record.get("rule_version")),
                       "rule_version"))
    _shape = record_shape_problems(record)
    if _shape:
        _p.append(_rec("split_authority_record_malformed", "view", "",
                       "授权记录形状不可消费：%s" % (_shape[0].get("reason") or ""),
                       _shape[0].get("path") or ""))
        # 形状已经不可消费：下面所有按 `facts` / `source_support` 成员遍历的检查都不能再跑。
        # 保留形状检查前已收集的状态/规则错误，直接拒收；不把非法字段转成空集合。
        return _p
    if str(record.get("status") or "") != "ok":
        # 失败记录没有可信的内容结论；同样不再遍历其字段（可能缺失或类型非法）。
        return _p
    _auth_digest = str(authority_digest or base_digest)
    if str(record.get("plan_digest") or "") != _auth_digest:
        _p.append(_rec("split_authority_record_mismatch", "view", "",
                       "核验记录的 plan_digest 与授权基线不一致", "plan_digest"))
    if str(record.get("novel_digest") or "") != str(ledger.get("novel_digest") or ""):
        _p.append(_rec("split_authority_record_mismatch", "view", "",
                       "账本 novel_digest 与核验记录不一致", "novel_digest"))
    if isinstance(view, dict) and isinstance(plan, dict):
        from .content_verification import (record_basis_matches,
                                            record_protocol_problems)
        if not record_basis_matches(record, plan):
            _p.append(_rec("split_authority_record_basis_mismatch", "view", "",
                           "记录义务支座与冻结授权基线不一致", "obligation_basis"))
        _protocol = record_protocol_problems(
            record, view, plan_digest=_auth_digest, novel_text=novel_text,
            novel_digest=str(ledger.get("novel_digest") or ""))
        if _protocol:
            _p.append(_rec(
                "split_authority_record_protocol", "view", "",
                "核验记录未通过协议准入：%s"
                % "、".join(str((x or {}).get("code") or "") for x in _protocol[:6]),
                "protocol"))
    _src = {str(r.get("fact_uid") or ""): r for r in (record.get("source_support") or [])
            if isinstance(r, dict)}
    _facts = {str(r.get("fact_uid") or ""): r for r in (record.get("facts") or [])
              if isinstance(r, dict)}
    for _entry in entries:
        if not isinstance(_entry, dict):
            continue
        _parent = str(_entry.get("parent_uid") or "")
        _auth = _entry.get("authority") if isinstance(_entry.get("authority"), dict) else {}
        if str(_auth.get("plan_digest") or "") != _auth_digest:
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "新条目的 authority.plan_digest 必须等于授权基线摘要",
                           "authority.plan_digest"))
        if str(_auth.get("rule_version") or "") != str(record.get("rule_version") or ""):
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "新条目的 authority.rule_version 与核验记录不一致",
                           "authority.rule_version"))
        _f = _facts.get(_parent) or {}
        if str(_f.get("verdict") or "") not in ("partial", "unknown"):
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "父事实在本轮记录里不是展示轴 partial/unknown，不能作为拆分授权",
                           "facts"))
        _s = _src.get(_parent)
        if not isinstance(_s, dict):
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "核验记录缺父事实的 source_support，无法核对证据准入",
                           "source_support"))
            continue
        if str(_s.get("verdict") or "") != "supported":
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "父事实来源轴不是 supported，不能作为拆分授权"
                           "（原文不支持的主张不得靠拆分洗白）",
                           "source_support.verdict"))
            continue
        _spans = [str(x) for x in (_s.get("evidence_spans") or [])
                  if isinstance(x, str) and x]
        _want = [str(x) for x in (_entry.get("parent_evidence_spans") or [])
                 if isinstance(x, str)]
        if not _spans or _spans != _want:
            _p.append(_rec("split_authority_record_mismatch", "split", _parent,
                           "`parent_evidence_spans` 必须逐字等于记录里父事实的已准入证据",
                           "parent_evidence_spans"))
    return _p


def _merge_ledger(base_view, ledger, op_parents, base_digest) -> tuple:
    """**历史条目从基线账本复制**，只追加本轮 ops 的条目.

    拆分是追加式事务：新条目不能借"整份替换账本"改写历史父主张、授权或判定证据。
    返回 `(final_ledger, problems)`。
    """
    _base_ctx = context(base_view)
    _passed = {str(e.get("parent_uid") or ""): e
               for e in (ledger.get("splits") or []) if isinstance(e, dict)}
    _hist: list = []
    _p: list = []
    if isinstance(_base_ctx, dict):
        if str(_base_ctx.get("novel_digest") or "") != str(ledger.get("novel_digest") or ""):
            _p.append(_rec("split_history_modified", "view", "",
                           "本轮账本 novel_digest 与基线账本不一致", "novel_digest"))
        for _e in (_base_ctx.get("splits") or []):
            if not isinstance(_e, dict):
                continue
            _parent = str(_e.get("parent_uid") or "")
            _got = _passed.get(_parent)
            if _got is None or _digest(_got) != _digest(_e):
                _p.append(_rec("split_history_modified", "split", _parent,
                               "历史拆分条目被改写/删除——历史父主张、授权与判定证据必须保持",
                               "splits"))
            _hist.append(copy.deepcopy(_e))
    _hist_parents = {str(e.get("parent_uid") or "") for e in _hist}
    _ops_parents = [str(x) for x in op_parents]
    _allowed = _hist_parents | set(_ops_parents)
    for _parent in _passed:
        if _parent and _parent not in _allowed:
            _p.append(_rec("split_ledger_unexpected_entry", "split", _parent,
                           "账本条目既不是历史条目，也不在本轮 ops 内", "splits"))
    _new: list = []
    for _parent in _ops_parents:
        _got = _passed.get(_parent)
        if isinstance(_got, dict):
            _new.append(copy.deepcopy(_got))
    _final = {"version": VERSION,
              "novel_digest": str(ledger.get("novel_digest") or ""),
              "base_digest": str(base_digest),
              "splits": _hist + _new}
    return _final, _p


def authority_problems(base_view, base_plan, ledger, entries, record, *,
                       authority_digest=None, novel_text="") -> list:
    """P3-A 授权预检的公开入口（与 apply 内部使用同一份判据）."""
    from .content_verification import plan_digest_of
    return _authority_problems(
        plan_digest_of(base_plan), ledger, entries, record,
        authority_digest=authority_digest, view=base_view, plan=base_plan,
        novel_text=novel_text)


def split_preflight_problems(view, plan, record, parent_uid, *,
                             novel_text="") -> list:
    """槽位请求**之前**的拆分预检（零模型调用）.

    复用 `_authority_problems`（不是第二套授权规则）与 apply 的 mode/稳定 uid/父/账本
    判据；子级 D 依赖 kids，只能在回答后判，这里只判父级前置。
    """
    from .content_verification import plan_digest_of
    _parent = str(parent_uid or "")
    _p: list = []
    if str((view or {}).get("mode") or "") != pc.MODE_LEGACY:
        _p.append(_rec("split_mode_unsupported", "view", "",
                       "拆分事务只覆盖 legacy_audio（现在 %r）"
                       % (view or {}).get("mode")))
    _table = ((view or {}).get("plan_basis") or {}).get("fact_uids")
    _fact_rows = _facts(view)
    if (not isinstance(_table, list) or len(_table) != len(_fact_rows)
            or len(set(map(str, _table))) != len(_table)):
        _p.append(_rec("split_requires_stable_fact_uids", "view", "",
                       "基线缺少与事实表等长同序的 `fact_uids`"))
    _parent_fact = next((f for f in _fact_rows if str(f.get("uid")) == _parent),
                        None)
    if not isinstance(_parent_fact, dict):
        _p.append(_rec("split_parent_missing", "split", _parent,
                       "父事实 %r 不在当前事实表里" % _parent))
        return _p
    for _e in ((context(view) or {}).get("splits") or []):
        if isinstance(_e, dict) and str(_e.get("parent_uid")) == _parent:
            _p.append(_rec("split_already_recorded", "split", _parent,
                           "父事实 %r 已有账本条目——不重复拆分" % _parent))
            break
    if not isinstance(plan, dict):
        _p.append(_rec("split_authority_record_missing", "view", "",
                       "拆分必须带当前基线计划"))
        return _p
    try:
        _base_digest = plan_digest_of(plan)
    except Exception:                                                # noqa: BLE001
        _base_digest = ""
    _src = {str(r.get("fact_uid") or ""): r
            for r in ((record or {}).get("source_support") or [])
            if isinstance(r, dict)} if isinstance(record, dict) else {}
    _facts_rec = {str(r.get("fact_uid") or ""): r
                  for r in ((record or {}).get("facts") or [])
                  if isinstance(r, dict)} if isinstance(record, dict) else {}
    _spans = [str(x) for x in ((_src.get(_parent) or {}).get("evidence_spans") or [])
              if str(x)]
    _fv = str((_facts_rec.get(_parent) or {}).get("verdict") or "partial")
    _entry = {
        "parent_uid": _parent,
        "parent_claim": str(_parent_fact.get("claim") or ""),
        "parent_evidence": str(_parent_fact.get("evidence") or ""),
        "parent_evidence_spans": _spans,
        "baseline_claim": None,
        "authority": {
            "rule_version": str((record or {}).get("rule_version") or "")
            if isinstance(record, dict) else "",
            "plan_digest": _base_digest, "fact_uid": _parent,
            "code": "verify_fact_unsupported", "verdict": _fv},
        "children": []}
    _ledger = {"version": VERSION,
               "novel_digest": str((record or {}).get("novel_digest") or "")
               if isinstance(record, dict) else "",
               "base_digest": _base_digest, "splits": [_entry]}
    _p.extend(_authority_problems(
        _base_digest, _ledger, [_entry], record,
        authority_digest=_base_digest, view=view, plan=plan,
        novel_text=str(novel_text or "")))
    return _p


def apply_split_view(base_view, split_ops, *, scope=None, ledger=None,
                     record=None, accept=None, authority_digest=None,
                     novel_text="", authorization_view=None,
                     authorization_plan=None) -> tuple:
    """**唯一**允许改事实表的应用器；`returns (candidate_view, problems, result)`.

    只接受 legacy；要求基线有稳定 `fact_uids`；账本必须与 ops/基线一致；非目标事实
    与绑定逐条同构；`plan_basis` 除 `fact_uids` 与 `fact_split_context` 外不得变化。
    """
    _p: list = []
    _mode = str((base_view or {}).get("mode") or "")
    if _mode != pc.MODE_LEGACY:
        return None, [_rec("split_mode_unsupported", "view", "",
                           "拆分事务只覆盖 legacy_audio（现在 %r）" % _mode)], \
            {"kind": "illegal", "mapping": {}, "detail": "mode"}
    _scope_p = _scope_problems(scope)
    if _scope_p:
        return None, _scope_p, {"kind": "illegal", "mapping": {},
                                "detail": _scope_p[0].get("reason", "")}
    _scope = scope
    _allowed_facts = {str(x) for x in _scope["allowed_split_fact_uids"]}
    _allowed_cars = {str(x) for x in _scope["allowed_split_carrier_uids"]}
    _base_table = (base_view.get("plan_basis") or {}).get("fact_uids")
    if (not isinstance(_base_table, list) or len(_base_table) != len(_facts(base_view))
            or len(set(map(str, _base_table))) != len(_base_table)):
        return None, [_rec("split_requires_stable_fact_uids", "view", "",
                           "基线缺少与事实表等长同序的 `fact_uids`——先经一次正常投影升级，"
                           "再拆分（不做事后全量映射）")], \
            {"kind": "illegal", "mapping": {}, "detail": "fact_uids"}
    if not isinstance(ledger, dict) or not isinstance(ledger.get("splits"), list):
        return None, [_rec("verify_fact_split_context", "view", "",
                           "`ledger` 必须是完整账本对象")], \
            {"kind": "illegal", "mapping": {}, "detail": "ledger"}
    if str(ledger.get("version") or "") != VERSION:
        return None, [_rec("verify_fact_split_context", "view", "",
                           "未知的拆分账本版本：%r" % ledger.get("version"),
                           CONTEXT_FIELD + ".version")], \
            {"kind": "illegal", "mapping": {}, "detail": "ledger.version"}
    if (not _HEX64.fullmatch(str(ledger.get("base_digest") or ""))
            or not _HEX64.fullmatch(str(ledger.get("novel_digest") or ""))):
        return None, [_rec("verify_fact_split_context", "view", "",
                           "账本的 base_digest / novel_digest 必须是 64 位十六进制")], \
            {"kind": "illegal", "mapping": {}, "detail": "ledger.digest"}
    _base_load = load_problems(base_view)
    if _base_load:
        return None, _base_load, {"kind": "illegal", "mapping": {},
                                  "detail": _base_load[0].get("reason", "")}
    _ops = _op_list(split_ops)
    if not _ops:
        return None, [_rec("split_children_invalid", "split", "", "没有拆分指令")], \
            {"kind": "illegal", "mapping": {}, "detail": "ops"}
    _existing = context(base_view)
    _existing_parents = {str(e.get("parent_uid") or "")
                         for e in ((_existing or {}).get("splits") or [])
                         if isinstance(e, dict)}
    for _op in _ops:
        _parent = str(_op.get("parent_uid") or "")
        if _parent in _existing_parents:
            return None, [_rec("split_already_recorded", "split", _parent,
                               "父事实 %r 已有账本条目——不重复拆分" % _parent)], \
                {"kind": "illegal", "mapping": {}, "detail": "already_recorded"}
    # **实际基线摘要**：账本不能自说自话；它必须与基线投影后的计划摘要一致。
    try:
        _base_plan = pc.plan_from_view(base_view)
    except pc.PresentationContractError as _exc:
        return None, list(_exc.problems), {"kind": "illegal", "mapping": {},
                                           "detail": "base_plan"}
    from .content_verification import plan_digest_of
    _base_digest = plan_digest_of(_base_plan)
    if str(ledger.get("base_digest") or "") != _base_digest:
        return None, [_rec("split_base_digest_mismatch", "view", "",
                           "账本 base_digest 与实际基线计划摘要不一致——拒绝跨基线错账")], \
            {"kind": "illegal", "mapping": {}, "detail": "base_digest"}
    _by_parent = {str(e.get("parent_uid") or ""): e
                  for e in (ledger.get("splits") or []) if isinstance(e, dict)}
    _new_entries = [_by_parent.get(str(_op.get("parent_uid") or "")) for _op in _ops]
    _final_ledger, _hist_p = _merge_ledger(
        base_view, ledger, [str(_op.get("parent_uid") or "") for _op in _ops],
        _base_digest)
    if _hist_p:
        return None, _hist_p, {"kind": "illegal", "mapping": {},
                               "detail": _hist_p[0].get("reason", "")}
    _auth_p = _authority_problems(
        _base_digest, ledger, _new_entries, record,
        authority_digest=authority_digest,
        view=authorization_view if isinstance(authorization_view, dict) else base_view,
        plan=authorization_plan if isinstance(authorization_plan, dict) else _base_plan,
        novel_text=str(novel_text or (ledger.get("novel_text")
                                      if isinstance(ledger, dict) else "") or ""))
    if _auth_p:
        return None, _auth_p, {"kind": "illegal", "mapping": {},
                               "detail": _auth_p[0].get("reason", "")}
    _base_facts = _fact_map(base_view)
    _mapping: dict = {}
    _replaced: dict = {}
    for _op in _ops:
        _parent = str(_op.get("parent_uid") or "")
        if _parent not in _allowed_facts:
            _p.append(_rec("split_not_authorized", "split", _parent,
                           "父事实 %r 不在本轮拆分授权内" % _parent))
            continue
        _parent_fact = _base_facts.get(_parent)
        if _parent_fact is None:
            _p.append(_rec("split_parent_missing_no_ledger", "split", _parent,
                           "父事实 %r 不在当前事实表里，且账本没有该条" % _parent))
            continue
        _entry = _by_parent.get(_parent)
        if _entry is None:
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "账本缺少父事实 %r 的条目" % _parent))
            continue
        _kids = [c for c in (_op.get("children") or []) if isinstance(c, dict)]
        _p.extend(d_problems(_parent_fact, _kids,
                             _entry.get("parent_evidence_spans") if isinstance(_entry, dict)
                             else []))
        if str(_entry.get("parent_claim") or "") != str(_parent_fact.get("claim") or ""):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "账本 parent_claim 与基线事实不一致"))
        if str(_entry.get("parent_evidence") or "") != str(_parent_fact.get("evidence") or ""):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "账本 parent_evidence 与基线事实不一致"))
        _auth = _entry.get("authority") if isinstance(_entry, dict) else None
        if not isinstance(_auth, dict) \
                or str(_auth.get("plan_digest") or "") != str(
                    authority_digest or ledger.get("base_digest") or ""):
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "新条目的 authority.plan_digest 必须等于授权基线摘要"))
        _judge = _entry.get("judgment") if isinstance(_entry, dict) else None
        if not isinstance(_judge, dict) or str(_judge.get("verdict") or "") != "supported":
            _p.append(_rec("split_judgment_not_supported", "split", _parent,
                           "M 判定不是 supported（影子期不得自动采纳）"))
        else:
            _p.extend(judgment_problems(_entry, require_supported=True))
        if _entry.get("narrowed_voices"):
            _p.append(_rec("split_operation_unsupported", "split", _parent,
                           "P0 不支持 narrow（留到 P1）"))
        if _op.get("beat_text"):
            _p.append(_rec("split_operation_unsupported", "split", _parent,
                           "P0 不支持画面改写（留到 P1）"))
        _entry_kids = [c for c in (_entry.get("children") or []) if isinstance(c, dict)]

        def _kid_sig(_c):
            _car = _c.get("carrier") if isinstance(_c.get("carrier"), dict) else {}
            return (str(_c.get("uid") or ""), str(_c.get("claim") or ""),
                    str(_car.get("kind") or ""), str(_car.get("uid") or ""))
        if [_kid_sig(c) for c in _kids] != [_kid_sig(c) for c in _entry_kids]:
            _p.append(_rec("verify_fact_split_context", "split", _parent,
                           "ops 的子 uid/主张/载体与账本不一致"))
        for _kid in _kids:
            _p.extend(_carrier_problems(base_view, _kid.get("carrier"),
                                        allowed_uids=_allowed_cars))
        _mapping[_parent] = [str(c.get("uid") or "") for c in _kids]
        _replaced[_parent] = _kids
    if _p:
        return None, _p, {"kind": "illegal", "mapping": {}, "detail": _p[0].get("reason", "")}

    for _u in (base_view.get("evidence_updates") or []):
        if isinstance(_u, dict) and str(_u.get("fact_uid") or "") in _replaced:
            _p.append(_rec("split_parent_evidence_update", "fact",
                           str(_u.get("fact_uid") or ""),
                           "父事实被拆分后不能再保留指向它的 evidence_update——"
                           "该更新会失去主体；请改为给子事实各自的证据"))
    if _p:
        return None, _p, {"kind": "illegal", "mapping": {}, "detail": _p[0].get("reason", "")}

    # ---- 构造新事实表 / 新绑定 / 新 uid 表（父原位换子，其他逐字不变）----
    _new_facts: list = []
    _new_table: list = []
    _new_bind: list = []
    _base_bind = {str((b or {}).get("fact_uid") or ""): b
                  for b in (base_view.get("fact_bindings") or []) if isinstance(b, dict)}
    _kids_by_uid = {str(k.get("uid") or ""): k
                    for _kids in _replaced.values() for k in _kids}
    for _fact in _facts(base_view):
        _uid = str(_fact.get("uid") or "")
        if _uid in _replaced:
            for _kid in _replaced[_uid]:
                _new_facts.append({"uid": str(_kid["uid"]),
                                   "claim": str(_kid.get("claim") or ""),
                                   "evidence": str(_kid.get("evidence") or "")})
                _new_table.append(str(_kid["uid"]))
                _new_bind.append({"fact_uid": str(_kid["uid"]),
                                  "carriers": [dict(_kid["carrier"])]})
            continue
        _new_facts.append(copy.deepcopy(_fact))
        _new_table.append(_uid)
        _src_bind = _base_bind.get(_uid)
        if _src_bind is not None:
            _new_bind.append(copy.deepcopy(_src_bind))
        else:
            _new_bind.append({"fact_uid": _uid, "carriers": []})
    # 非目标事实必须逐字同构（不是多重集）
    _base_non = {str(f.get("uid")): (str(f.get("claim") or ""), str(f.get("evidence") or ""))
                 for f in _facts(base_view) if str(f.get("uid")) not in _replaced}
    _new_non = {str(f.get("uid")): (str(f.get("claim") or ""), str(f.get("evidence") or ""))
                for f in _new_facts if str(f.get("uid")) not in _kids_by_uid}
    for _uid, _pair in _base_non.items():
        if _new_non.get(_uid) != _pair:
            _p.append(_rec("split_non_target_modified", "fact", _uid,
                           "非目标事实 %r 的主张/证据被改动" % _uid))
    _base_bind_non = {str(b.get("fact_uid") or ""): b.get("carriers")
                      for b in (base_view.get("fact_bindings") or [])
                      if isinstance(b, dict) and str(b.get("fact_uid") or "") not in _replaced}
    _new_bind_non = {str(b.get("fact_uid") or ""): b.get("carriers")
                     for b in _new_bind
                     if str(b.get("fact_uid") or "") not in _kids_by_uid}
    if _digest(_base_bind_non) != _digest(_new_bind_non):
        _p.append(_rec("split_non_target_modified", "fact", "",
                       "非目标事实的绑定被改动"))
    if _p:
        return None, _p, {"kind": "illegal", "mapping": {}, "detail": _p[0].get("reason", "")}

    # ---- 新 plan_basis：只允许 fact_uids 与账本变化 ----
    _basis = copy.deepcopy(base_view.get("plan_basis") or {})
    _basis["fact_uids"] = list(_new_table)
    _basis[CONTEXT_FIELD] = copy.deepcopy(_final_ledger)
    _base_cmp = {k: v for k, v in (base_view.get("plan_basis") or {}).items()
                 if k not in ("fact_uids", CONTEXT_FIELD)}
    _new_cmp = {k: v for k, v in _basis.items()
                if k not in ("fact_uids", CONTEXT_FIELD)}
    if _digest(_base_cmp) != _digest(_new_cmp):
        return None, [_rec("split_basis_modified", "view", "",
                           "拆分事务只允许改 `fact_uids` 与 `fact_split_context`；"
                           "`plan_basis` 的其他字段与基线不符")], \
            {"kind": "illegal", "mapping": {}, "detail": "plan_basis"}
    _candidate = copy.deepcopy(base_view)
    _candidate["frozen_facts"] = _new_facts
    _candidate["fact_bindings"] = _new_bind
    _candidate["plan_basis"] = _basis
    _pv, _vp = pc.parse_view(_candidate, expected_mode=_mode)
    if _pv is None:
        return None, _vp, {"kind": "illegal", "mapping": {}, "detail": "parse_view"}
    _adm = pc.view_admission_problems(_pv)
    if _adm:
        return None, _adm, {"kind": "illegal", "mapping": {}, "detail": "admission"}
    _rel = pc.view_problems(_pv)
    if _rel:
        return None, _rel, {"kind": "illegal", "mapping": {}, "detail": "relations"}
    _load = load_problems(_pv)
    if _load:
        return None, _load, {"kind": "illegal", "mapping": {}, "detail": "ledger"}
    _res = list(accept(_pv) or []) if accept is not None else []
    return _pv, [], {"kind": "applied", "mapping": _mapping,
                     "residuals": _res, "detail": ""}
