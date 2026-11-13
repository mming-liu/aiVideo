# -*- coding: utf-8 -*-
"""已确认问题的**状态台账**（M03）.

## 要解决的问题

现行提交门把「本轮审计没再报出某条问题」当成「这条问题已解决」：

    resolved = before_keys - after_keys          # main.py 的 `_repair_acceptance_decision`

而审计是概率性判断，措辞、严重度、漏报都会漂移。S06 已经实测到同一份**未变**稿件
在两次审计之间从「10 条 / 1 blocking」变成「11 条 / 0 blocking」，其中 3 条人工
确认的缺陷**没有**被再次报出。把「没报出」读成「已修好」，等于用模型的沉默给内容
背书。

## 本模块的口径

- **状态由证据决定，不由「本次没报」决定。** 未再报出只记
  `not_detected_this_round`；关闭（`fixed_with_evidence`）必须有确定性复核证据或
  有依据的重新裁决。
- **撤销也要留依据。** `withdrawn` 必须给 `reason` 与 `adjudicator`；缺一即拒收
  （抛 `LedgerError`，不静默降级）。
- **身份按「镜头 + 字段 + 事实」归并，不按 code 归并。** 同一事实在两次审计里可能
  换 code（实测 `PROP_CONTINUITY` → `STATE_CHAIN_CONFLICT`），按 code 比会把同一
  问题记成「消失 + 新增」；但同一字段上的**不同事实**必须分开，否则任意一条命中
  都会把另一条记成仍被报出。事实指纹取 evidence（缺失时用 code 兜底），匹配时只在
  同一字段存在多个事实时才用它消歧，避免措辞差异误伤唯一事实。
- **不是缺陷的条目不算漏报。** 人工已裁决为「已有合法表达」「改进建议」「证据不足」
  的条目，只记录状态，不参与「确认缺陷重报率」的分子分母。

本模块是纯函数，不读盘、不调模型；产物由调用方（工具或提交入口）落盘。
"""
from __future__ import annotations

from typing import Iterable

SCHEMA = "step01_issue_ledger.v1"

#: 确认缺陷的状态
STATUS_STILL_REPORTED = "still_reported"          # 本次仍被报出 → 未修复
STATUS_NOT_DETECTED = "not_detected_this_round"   # 本次未报出 → **不等于**已解决
STATUS_NEEDS_REVERIFY = "needs_reverify"          # 报出了但证据/字段对不上，需重验
STATUS_FIXED = "fixed_with_evidence"              # 有确定性复核证据或重新裁决 → 可关闭
STATUS_WITHDRAWN = "withdrawn"                    # 人工撤销（须给依据）

#: 不参与「确认缺陷」统计的人工处置
NON_DEFECT_DISPOSITIONS = ("legal_expression", "improvement", "undecided")

#: 允许的处置
DISPOSITIONS = ("confirmed",) + NON_DEFECT_DISPOSITIONS

_CLOSED_STATUSES = (STATUS_FIXED, STATUS_WITHDRAWN)


class LedgerError(ValueError):
    """台账输入不合法（缺依据、身份不完整、处置未知）."""


def _s(value) -> str:
    return str(value if value is not None else "").strip()


def _norm_fact(value) -> str:
    """事实指纹的归一化：去空白、小写（只做确定性规范化，不做语义猜测）."""
    return "".join(str(value if value is not None else "").split()).lower()


def _fact_fingerprint(item: dict) -> str:
    """事实指纹：优先 evidence；没有 evidence 时退回 code（仅作区分，不冒充证据）."""
    evidence = _norm_fact(item.get("evidence"))
    if evidence:
        return "evidence:" + evidence
    code = _norm_fact(item.get("code"))
    return ("code:" + code) if code else ""


def issue_identity(item: dict) -> tuple[str, str, str]:
    """稳定身份：`(shot_id, field_path, fact_fingerprint)`.

    - **不含 code 作为主身份**：同一事实换 code 是实测行为（`PROP_CONTINUITY` ↔
      `STATE_CHAIN_CONFLICT`），按 code 比会把同一问题记成「消失 + 新增」。
    - **含事实指纹**：同一字段上的两个不同事实（不同 evidence）不能合并成同一身份，
      否则任意一条命中都会把另一条记成 `still_reported`。evidence 缺失时用 code
      指纹兜底区分，但匹配侧仍把「无 evidence」当作弱事实处理（见
      :func:`_facts_match`），不让 code 漂移误伤同一事实。
    """
    shot = _s(item.get("shot_id"))
    field = _s(item.get("field_path"))
    if not shot or not field:
        raise LedgerError("身份不完整：需要非空 shot_id 与 field_path，实际=%r" % (item,))
    return shot, field, _fact_fingerprint(item)


def _facts_match(confirmed_fact: str, observed: dict) -> bool:
    """同一字段下判断观察项是否指向该确认事实.

    只有两侧都有事实指纹时才做区分：evidence 完全一致，或一方是另一方的子串
    （实测同一事实的措辞会增删细节）。任一侧没有 evidence/code 指纹时视为
    「无法区分」——由调用方按字段唯一性决定是否回退到字段级匹配。
    """
    observed_fact = _fact_fingerprint(observed)
    if not confirmed_fact or not observed_fact:
        return False
    if confirmed_fact == observed_fact:
        return True
    if confirmed_fact.startswith("evidence:") and observed_fact.startswith("evidence:"):
        a = confirmed_fact[len("evidence:"):]
        b = observed_fact[len("evidence:"):]
        return a in b or b in a
    return False


def _normalize_issue(item: dict, *, source: str) -> dict:
    if not isinstance(item, dict):
        raise LedgerError("%s 的条目不是对象：%r" % (source, item))
    ident = issue_identity(item)
    disposition = _s(item.get("disposition")) or "confirmed"
    if disposition not in DISPOSITIONS:
        raise LedgerError("未知处置 %r（允许：%s）" % (disposition, ", ".join(DISPOSITIONS)))
    return {
        "id": _s(item.get("id")) or "%s@%s" % (ident[0], ident[1]),
        "shot_id": ident[0],
        "field_path": ident[1],
        "fact_fingerprint": ident[2],
        "code": _s(item.get("code")),
        "disposition": disposition,
        "evidence": _s(item.get("evidence")),
        "why": _s(item.get("why")),
    }


def build_issue_ledger(confirmed: Iterable[dict], observed: Iterable[dict] | None, *,
                       candidate_revision: str,
                       fixed_evidence: dict | None = None,
                       withdrawn: dict | None = None) -> dict:
    """构建一份状态台账.

    Args:
        confirmed: 人工裁决过的条目（含 `disposition`）。
        observed: 本次审计/检查实际报出的条目；`None` 表示**没有观察输入**
            （此时所有确认缺陷都记 `needs_reverify`，不能记「未检出」）。
        candidate_revision: 本次观察针对的稿件身份。
        fixed_evidence: `{issue_id: {"kind": ..., "detail": ...}}`——关闭依据。
        withdrawn: `{issue_id: {"reason": ..., "adjudicator": ...}}`——撤销依据。

    Returns:
        `{"schema", "candidate_revision", "rows": [...], "summary": {...}}`
    """
    fixed_evidence = dict(fixed_evidence or {})
    withdrawn = dict(withdrawn or {})
    rows: list[dict] = []
    observed_items = None if observed is None else list(observed)

    # 观察侧按「镜头 + 字段」索引；同一字段下是否指向同一事实由事实指纹再判。
    seen: dict[tuple[str, str], list] = {}
    for item in observed_items or []:
        try:
            key = issue_identity(item)
        except LedgerError:
            continue
        seen.setdefault((key[0], key[1]), []).append(item)

    normalized = [_normalize_issue(raw, source="confirmed") for raw in confirmed or []]
    # 同一 (镜头, 字段) 下有几个不同事实：>1 时才需要事实指纹消歧；
    # 唯一事实保留旧的字段级匹配，避免 evidence 措辞差异把「仍被报出」误判成漏报。
    facts_by_field: dict[tuple[str, str], set[str]] = {}
    for issue in normalized:
        facts_by_field.setdefault(
            (issue["shot_id"], issue["field_path"]), set()).add(issue["fact_fingerprint"])

    for issue in normalized:
        field_key = (issue["shot_id"], issue["field_path"])
        field_hits = seen.get(field_key) or []
        if len(facts_by_field[field_key]) > 1:
            hits = [h for h in field_hits
                    if _facts_match(issue["fact_fingerprint"], h)]
        else:
            hits = field_hits
        row = {
            "id": issue["id"],
            "shot_id": issue["shot_id"],
            "field_path": issue["field_path"],
            "fact_fingerprint": issue["fact_fingerprint"],
            "code": issue["code"],
            "disposition": issue["disposition"],
            "evidence": issue["evidence"],
            "observed_codes": sorted({_s(h.get("code")) for h in hits if _s(h.get("code"))}),
            "observed_severities": sorted({_s(h.get("severity")) for h in hits
                                           if _s(h.get("severity"))}),
        }

        # 1) 人工撤销优先：它是显式裁决，不需要观察输入
        if issue["id"] in withdrawn:
            w = withdrawn[issue["id"]] or {}
            if not _s(w.get("reason")) or not _s(w.get("adjudicator")):
                raise LedgerError("撤销 %r 缺依据：需要 reason 与 adjudicator" % issue["id"])
            row.update({"status": STATUS_WITHDRAWN, "closed": True,
                        "close_basis": {"kind": "withdrawn",
                                        "reason": _s(w.get("reason")),
                                        "adjudicator": _s(w.get("adjudicator"))}})
            rows.append(row)
            continue

        # 2) 关闭必须有证据；没有证据时即使「本次没报」也不得关闭
        if issue["id"] in fixed_evidence:
            ev = fixed_evidence[issue["id"]] or {}
            kind = _s(ev.get("kind"))
            detail = _s(ev.get("detail"))
            if not kind or not detail:
                raise LedgerError(
                    "关闭 %r 缺证据：需要 kind 与 detail（本次未报出不足以关闭）" % issue["id"])
            row.update({"status": STATUS_FIXED, "closed": True,
                        "close_basis": {"kind": kind, "detail": detail,
                                        "candidate_revision": _s(ev.get("candidate_revision"))
                                        or candidate_revision}})
            rows.append(row)
            continue

        # 3) 没有观察输入 → 无法判断
        if observed_items is None:
            row.update({"status": STATUS_NEEDS_REVERIFY, "closed": False,
                        "note": "没有本次观察输入，不能判定为未检出"})
            rows.append(row)
            continue

        # 4) 非缺陷条目：只记录「是否仍被看见」，不计入确认缺陷统计
        if issue["disposition"] in NON_DEFECT_DISPOSITIONS:
            row.update({"status": STATUS_STILL_REPORTED if hits else STATUS_NOT_DETECTED,
                        "closed": False,
                        "note": "人工处置=%s，不计入确认缺陷" % issue["disposition"]})
            rows.append(row)
            continue

        # 5) 确认缺陷：报出 = 未修复；没报出 = 本次未检出（**不关闭**）
        if hits:
            row.update({"status": STATUS_STILL_REPORTED, "closed": False})
        else:
            row.update({"status": STATUS_NOT_DETECTED, "closed": False,
                        "note": "本次未报出，不等于已解决；关闭需要修订证据或重新裁决"})
        rows.append(row)

    confirmed_rows = [r for r in rows if r["disposition"] == "confirmed"]
    summary = {
        "total": len(rows),
        "confirmed": len(confirmed_rows),
        "still_reported": sum(1 for r in confirmed_rows
                              if r["status"] == STATUS_STILL_REPORTED),
        "not_detected": sum(1 for r in confirmed_rows
                            if r["status"] == STATUS_NOT_DETECTED),
        "needs_reverify": sum(1 for r in confirmed_rows
                              if r["status"] == STATUS_NEEDS_REVERIFY),
        "fixed_with_evidence": sum(1 for r in confirmed_rows
                                   if r["status"] == STATUS_FIXED),
        "withdrawn": sum(1 for r in confirmed_rows if r["status"] == STATUS_WITHDRAWN),
        "closed": sum(1 for r in confirmed_rows if r.get("closed")),
        "non_defect": sum(1 for r in rows if r["disposition"] in NON_DEFECT_DISPOSITIONS),
    }
    summary["confirmed_closed_ratio"] = (
        "0/%d" % summary["confirmed"] if not summary["confirmed"]
        else "%d/%d" % (summary["closed"], summary["confirmed"]))
    return {
        "schema": SCHEMA,
        "candidate_revision": _s(candidate_revision),
        "observed_provided": observed_items is not None,
        "rows": rows,
        "summary": summary,
    }


def render_markdown(ledger: dict) -> str:
    """把台账渲染成可阅读的表格（状态 + 依据）."""
    s = ledger.get("summary") or {}
    out = ["# 已确认问题状态台账", "",
           "- 稿件身份：`%s`" % ledger.get("candidate_revision"),
           "- 本次观察输入：%s" % ("有" if ledger.get("observed_provided") else "无"),
           "- 确认缺陷 %s 条：仍被报出 %s，本次未检出 %s，需重验 %s，有证据关闭 %s，撤销 %s"
           % (s.get("confirmed"), s.get("still_reported"), s.get("not_detected"),
              s.get("needs_reverify"), s.get("fixed_with_evidence"), s.get("withdrawn")),
           "", "| id | 镜头 | 字段 | 处置 | 状态 | 本次报出的 code |",
           "|---|---|---|---|---|---|"]
    for r in ledger.get("rows") or []:
        out.append("| %s | %s | `%s` | %s | %s | %s |" % (
            r["id"], r["shot_id"], r["field_path"], r["disposition"],
            r["status"], ", ".join(r.get("observed_codes") or []) or "—"))
    out.append("")
    out.append("> `not_detected_this_round` 只表示**本次审计没有报出**，不表示已修复；"
               "关闭需要修订证据或重新裁决。")
    out.append("")
    return "\n".join(out)
