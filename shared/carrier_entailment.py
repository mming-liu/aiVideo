"""Evidence-first display verification contracts. No model calls or story keywords.

Extraction sees carriers only. Comparison sees the admitted observations and frozen
obligations. Model semantics remain fallible; this module validates provenance,
complete coverage and derives the executable verdict rather than trusting a summary.
"""
from __future__ import annotations

import hashlib
import json

PROTOCOL = "carrier_entailment.v1"
CLASSIC = "display.v1"
DECISIONS = ("entailed", "not_entailed", "contradicted", "unknown")


def protocol_of(view):
    from shared.content_verification import context_source_of
    context = context_source_of(view)
    return context.get("display_protocol", CLASSIC) if isinstance(context, dict) else CLASSIC


def _problem(code, reason, path="entailment", uid=""):
    return {"code": code, "kind": "verification", "uid": uid, "ref": uid,
            "path": path, "reason": reason,
            "fix": "保留冻结主张；补齐实际展示内容后按同一协议重验"}


def carrier_input(view):
    from shared.content_verification import display_carriers
    carriers = {}
    for fact in view.get("frozen_facts", []):
        for row in display_carriers(view, fact["uid"]):
            # One UID can expose multiple body segments. Keep all, in input order.
            key = (row["kind"], row["uid"], row["text"])
            carriers[key] = dict(row)
    return {"carriers": list(carriers.values())}


def input_digest(view):
    raw = json.dumps(carrier_input(view), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def inventory_problems(inventory, view):
    from shared import quantified_verification as quantified
    if protocol_of(view) == quantified.PROTOCOL:
        return quantified.inventory_problems(inventory, view)
    return _inventory_problems(inventory, view)


def _inventory_problems(inventory, view):
    """Every body occurs once, every quote belongs to that body; empty facts are legal."""
    if not isinstance(inventory, dict) or set(inventory) != {"carriers"}:
        return [_problem("verify_inventory_shape", "正文提取结果必须仅含 carriers 数组")]
    rows = inventory["carriers"]
    expected = carrier_input(view)["carriers"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        return [_problem("verify_inventory_coverage", "正文提取必须覆盖全部实际载体正文")]
    problems = []
    for i, (row, body) in enumerate(zip(rows, expected)):
        path = "entailment.inventory.carriers[%d]" % i
        if (not isinstance(row, dict) or set(row) != {"carrier_index", "observations"}
                or type(row.get("carrier_index")) is not int or row["carrier_index"] != i):
            problems.append(_problem("verify_inventory_identity", "载体索引必须原样回带", path))
            continue
        obs = row["observations"]
        if not isinstance(obs, list):
            problems.append(_problem("verify_inventory_shape", "observations 必须为数组", path))
            continue
        for j, item in enumerate(obs):
            at = path + ".observations[%d]" % j
            if (not isinstance(item, dict) or set(item) != {"text", "exhibitor_span"}
                    or any(not isinstance(item[k], str) or not item[k].strip()
                           for k in ("text", "exhibitor_span"))):
                problems.append(_problem("verify_inventory_shape", "观察项需非空正文与逐字证据", at))
                continue
            if item["exhibitor_span"] not in body["text"]:
                problems.append(_problem("verify_inventory_not_exhibited",
                                         "提取证据不在对应载体实际正文内", at))
    return problems


def observations_of(inventory, view):
    result = {}
    bodies = carrier_input(view)["carriers"]
    for row in inventory["carriers"]:
        index = row["carrier_index"]
        for j, item in enumerate(row["observations"]):
            # IDs are assigned by code, never by the model or by fact position.
            uid = "body:%d:observation:%d" % (index, j)
            result[uid] = dict(item, observation_id=uid,
                               carrier_uid=bodies[index]["uid"],
                               carrier_kind=bodies[index]["kind"])
    return result


def comparison_input(inventory, view, decomposition=None):
    from shared.content_verification import expected_obligations, display_carriers
    observed = observations_of(inventory, view)
    task = {"facts": [{"fact_uid": uid, "obligations": obligations,
                       "observations": [item for item in observed.values()
                           if (item["carrier_kind"], item["carrier_uid"]) in {
                               (c["kind"], c["uid"]) for c in display_carriers(view, uid)}]}
                      for uid, obligations in expected_obligations(view, decomposition).items()]}
    from shared import quantified_verification as quantified
    return (quantified.add_comparison_input(task, inventory, view)
            if protocol_of(view) == quantified.PROTOCOL else task)


def check_problems(checks, inventory, view, decomposition=None):
    from shared import quantified_verification as quantified
    if protocol_of(view) == quantified.PROTOCOL:
        return quantified.check_problems(checks, inventory, view, decomposition)
    return _check_problems(checks, inventory, view, decomposition)


def _check_problems(checks, inventory, view, decomposition=None):
    if not isinstance(checks, list):
        return [_problem("verify_entailment_shape", "checks 必须为数组")]
    task = comparison_input(inventory, view, decomposition)
    expected = {(f["fact_uid"], o["obligation_id"]): f
                for f in task["facts"] for o in f["obligations"]}
    seen = []
    problems = []
    keys = {"fact_uid", "obligation_id", "decision", "observation_ids",
            "counterexample", "reason"}
    for i, row in enumerate(checks):
        path = "entailment.checks[%d]" % i
        if (not isinstance(row, dict) or set(row) != keys
                or any(not isinstance(row[k], str) for k in keys - {"observation_ids"})
                or not isinstance(row["observation_ids"], list)
                or any(not isinstance(x, str) for x in row["observation_ids"])):
            problems.append(_problem("verify_entailment_shape", "比较项字段/类型错误", path))
            continue
        pair = (row["fact_uid"], row["obligation_id"])
        seen.append(pair)
        if pair not in expected:
            problems.append(_problem("verify_entailment_identity", "比较项不属冻结义务", path,
                                     row["fact_uid"]))
            continue
        available = {o["observation_id"] for o in expected[pair]["observations"]}
        ids = row["observation_ids"]
        if len(ids) != len(set(ids)) or any(x not in available for x in ids):
            problems.append(_problem("verify_entailment_observation", "证据不存在、重复或属其他事实", path,
                                     row["fact_uid"]))
        if row["decision"] not in DECISIONS or not row["reason"].strip():
            problems.append(_problem("verify_entailment_decision", "比较结论不合法或未写理由", path,
                                     row["fact_uid"]))
        if row["decision"] == "entailed" and not ids:
            problems.append(_problem("verify_entailment_evidence_missing", "蕴含结论必须引用观察项", path,
                                     row["fact_uid"]))
        if row["decision"] == "not_entailed" and not row["counterexample"].strip():
            problems.append(_problem("verify_entailment_counterexample_missing", "不蕴含结论须给可兼容反例", path,
                                     row["fact_uid"]))
    if len(seen) != len(set(seen)) or set(seen) != set(expected):
        problems.append(_problem("verify_entailment_coverage", "必须逐项覆盖全部冻结义务，不能删项或重复"))
    return problems


def derived_facts(checks, inventory, view, decomposition=None):
    from shared import quantified_verification as quantified
    if protocol_of(view) == quantified.PROTOCOL:
        return quantified.derived_facts(checks, inventory, view, decomposition)
    return _derived_facts(checks, inventory, view, decomposition)


def _derived_facts(checks, inventory, view, decomposition=None):
    from shared.content_verification import expected_obligations, _derived_fact_verdict
    observed = observations_of(inventory, view)
    by_pair = {(c["fact_uid"], c["obligation_id"]): c for c in checks}
    facts = []
    for uid, obligations in expected_obligations(view, decomposition).items():
        items = []
        for obligation in obligations:
            c = by_pair[(uid, obligation["obligation_id"])]
            verdict = {"entailed": "supported", "not_entailed": "partial",
                       "contradicted": "contradicted", "unknown": "unknown"}[c["decision"]]
            # A concrete counterexample vetoes a claimed entailment.
            if c["counterexample"].strip() and verdict == "supported":
                verdict = "partial"
            evidence = [{"carrier_uid": observed[x]["carrier_uid"],
                         "exhibitor_span": observed[x]["exhibitor_span"]}
                        for x in c["observation_ids"]]
            items.append({"obligation_id": obligation["obligation_id"], "text": obligation["text"],
                          "verdict": verdict, "evidence": evidence,
                          "missing": "" if verdict == "supported" else
                              c["counterexample"].strip() or c["reason"], "reason": c["reason"]})
        verdict = _derived_fact_verdict(items)
        facts.append({"fact_uid": uid, "verdict": verdict,
                      "support_spans": list(dict.fromkeys(e["exhibitor_span"]
                          for item in items for e in item["evidence"])),
                      "missing": "；".join(item["missing"] for item in items if item["missing"]),
                      "suggested_carriers": [], "obligations": items})
    return facts


def record_problems(record, view, decomposition=None):
    """Same admission on production, seed, cache, reuse and final acceptance."""
    from shared.quantified_verification import PROTOCOL as QUANTIFIED
    required = protocol_of(view) in (PROTOCOL, QUANTIFIED)
    if protocol_of(view) == QUANTIFIED:
        from shared.quantified_verification import requirements_problems, table_of
        bad = requirements_problems(table_of(view), view)
        if bad:
            return bad
    guard = record.get("entailment")
    if not required and guard is None:
        return []
    if not required:
        return [_problem("verify_entailment_policy", "新协议记录不能按旧策略重新采纳")]
    from shared.content_verification import display_input_digest
    if record.get("display_digest") != display_input_digest(view, decomposition):
        return [_problem("verify_display_digest_mismatch", "证书比较输入与本次冻结要求/义务/载体不一致")]
    keys = {"schema", "input_digest", "inventory", "checks", "extraction_call_id", "comparison_call_id"}
    if not isinstance(guard, dict) or set(guard) != keys or guard.get("schema") != protocol_of(view):
        return [_problem("verify_entailment_missing", "上下文要求正文提取/反例比较协议，缺有效证书")]
    if guard["input_digest"] != input_digest(view):
        return [_problem("verify_entailment_digest", "观察项属其他展示正文，禁止复用")]
    ids = [guard.get(k) for k in ("extraction_call_id", "comparison_call_id")]
    if any(not isinstance(x, str) or not x.strip() for x in ids) or ids[0] == ids[1]:
        return [_problem("verify_entailment_call_identity", "两阶段需不同、非空物理调用身份")]
    axes = record.get("axes")
    if isinstance(axes, dict) and (axes.get("display_call_id") != ids[1]
                                  or axes.get("source_call_id") in ids):
        return [_problem("verify_entailment_call_identity", "来源、提取、比较调用身份不一致或重复")]
    bad = inventory_problems(guard["inventory"], view)
    if bad:
        return bad
    bad = check_problems(guard["checks"], guard["inventory"], view, decomposition)
    if bad:
        return bad
    if record.get("facts") != derived_facts(guard["checks"], guard["inventory"], view, decomposition):
        return [_problem("verify_entailment_derived", "事实裁决必须由全部比较项派生，不能模型自报或改写")]
    return []
