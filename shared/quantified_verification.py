"""Frozen quantitative temporal edges; models normalize, code compares bounds."""
import copy

from shared import temporal_bounds as bounds

PROTOCOL = "carrier_entailment.quantified.v1"


def requirements_problems(table, view):
    from shared.carrier_entailment import _problem
    if (not isinstance(view, dict) or not isinstance(view.get("frozen_facts"), list)
            or any(not isinstance(f, dict) or not isinstance(f.get("uid"), str)
                   or not isinstance(f.get("claim"), str) for f in view["frozen_facts"])):
        return [_problem("verify_temporal_requirements_coverage", "缺有效冻结事实视图")]
    facts = {f["uid"]: f["claim"] for f in view.get("frozen_facts", [])}
    if not isinstance(table, dict) or set(table) != set(facts):
        return [_problem("verify_temporal_requirements_coverage", "冻结时间边表须覆盖全部事实，包括空表")]
    problems, seen = [], set()
    for uid, rows in table.items():
        if not isinstance(rows, list):
            problems.append(_problem("verify_temporal_requirements_shape", "时间边必须为数组", uid=uid))
            continue
        for i, row in enumerate(rows):
            path = "temporal_requirements.%s[%d]" % (uid, i)
            if (not isinstance(row, dict) or set(row) != {"id", "fact_uid", "span", "scope", "interval"}
                    or any(not isinstance(row[k], str) or not row[k].strip() for k in ("id", "fact_uid", "span", "scope"))
                    or bounds.interval_problems(row["interval"], requirement=True)):
                problems.append(_problem("verify_temporal_requirements_shape", "冻结时间边字段或区间非法", path, uid))
                continue
            if row["fact_uid"] != uid or row["id"] in seen:
                problems.append(_problem("verify_temporal_requirements_identity", "时间边重复或不属本事实", path, uid))
            seen.add(row["id"])
            if row["span"] not in facts[uid]:
                problems.append(_problem("verify_temporal_requirements_span", "时间要求不在冻结主张中", path, uid))
    return problems


def table_of(view):
    from shared.content_verification import context_source_of
    return (context_source_of(view) or {}).get("temporal_requirements")


def inventory_problems(inventory, view):
    from shared import carrier_entailment as ce
    if not isinstance(inventory, dict) or not isinstance(inventory.get("carriers"), list):
        return [ce._problem("verify_inventory_shape", "正文提取缺 carriers")]
    clean = copy.deepcopy(inventory)
    problems = []
    bodies = ce.carrier_input(view)["carriers"]
    for row in clean["carriers"]:
        if not isinstance(row, dict) or not isinstance(row.get("observations"), list):
            continue  # base admission reports the exact shape
        index = row.get("carrier_index")
        for obs in row["observations"]:
            if not isinstance(obs, dict):
                continue
            edges = obs.pop("temporal_edges", None)
            if not isinstance(edges, list):
                problems.append(ce._problem("verify_temporal_observation_shape", "每项观察必须显式提供 temporal_edges 数组"))
                continue
            for edge in edges:
                if (not isinstance(edge, dict) or set(edge) != {"span", "scope", "interval"}
                        or any(not isinstance(edge[k], str) or not edge[k].strip() for k in ("span", "scope"))
                        or bounds.interval_problems(edge["interval"])):
                    problems.append(ce._problem("verify_temporal_observation_shape", "时间观察字段或区间非法"))
                    continue
                if (type(index) is not int or not 0 <= index < len(bodies)
                        or edge["span"] not in bodies[index]["text"]
                        or not isinstance(obs.get("exhibitor_span"), str)
                        or edge["span"] not in obs["exhibitor_span"]):
                    problems.append(ce._problem("verify_temporal_observation_span", "时间证据须在该观察的实际正文证据中"))
    return ce._inventory_problems(clean, view) + problems


def temporal_observations(inventory, view, uid):
    from shared import carrier_entailment as ce
    from shared.content_verification import display_carriers
    available = {(c["kind"], c["uid"]) for c in display_carriers(view, uid)}
    rows = []
    for oid, obs in ce.observations_of(inventory, view).items():
        if (obs["carrier_kind"], obs["carrier_uid"]) not in available:
            continue
        for i, edge in enumerate(obs["temporal_edges"]):
            rows.append({"id": oid + ":time:%d" % i, "fact_uid": uid,
                         "span": edge["span"], "scope": edge["scope"], "interval": edge["interval"]})
    return rows


def add_comparison_input(task, inventory, view):
    for fact in task["facts"]:
        uid = fact["fact_uid"]
        fact["temporal_requirements"] = table_of(view)[uid]
        fact["temporal_observations"] = temporal_observations(inventory, view, uid)
    return task


def _clean_checks(checks):
    rows = copy.deepcopy(checks)
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                row.pop("temporal_matches", None)
    return rows


def check_problems(checks, inventory, view, decomposition):
    from shared import carrier_entailment as ce
    bad = ce._check_problems(_clean_checks(checks), inventory, view, decomposition)
    if bad:
        return bad
    problems = []
    for row in checks:
        uid = row["fact_uid"]
        matches = row.get("temporal_matches")
        if not isinstance(matches, list):
            problems.append(ce._problem("verify_temporal_match_shape", "比较项须显式提供 temporal_matches 数组", uid=uid))
            continue
        # **时间边只在根义务上裁决**（`derived_facts` 只读根行的 `temporal_matches`）：组件义务上的
        # `temporal_matches` 是**冗余信息** ⇒ **接受但忽略**，不再整轮拒收。
        # ⚠️ 旧写法对"组件上也填了"报 `verify_temporal_match_scope` 并 `continue`——实测（2026-10-11
        # 真实闭环）把一次**内容合法**的修订整轮作废（模型在 c2/c3 上填的匹配与根一致）。责任划分：
        # **裁决**只由根义务派生；组件匹配填多了不改变裁决，但**字段形状与引用合法性照核**。
        # 保留的仍拒：类型/未知/重复的要求或观察、跨事实引用（协议类）；根义务缺匹配、错误事件
        # 对应、未知期限或区间越界继续由根行拒（内容类，不在这里；见 `derived_facts`）。
        _reqs = [{k: v for k, v in r.items() if k != "scope"} for r in table_of(view)[uid]]
        _obs = [{k: v for k, v in o.items() if k != "scope"}
                for o in temporal_observations(inventory, view, uid)]
        problems += [p for p in bounds.temporal_problems(_reqs, _obs, matches)
                     if p["code"] != "verify_fact_unsupported"]
    return problems


def derived_facts(checks, inventory, view, decomposition):
    from shared import carrier_entailment as ce
    from shared.content_verification import ROOT_OBLIGATION_ID, _derived_fact_verdict
    facts = ce._derived_facts(_clean_checks(checks), inventory, view, decomposition)
    root = {row["fact_uid"]: row for row in checks if row["obligation_id"] == ROOT_OBLIGATION_ID}
    for fact in facts:
        uid = fact["fact_uid"]
        bad = bounds.temporal_problems(
            [{k: v for k, v in r.items() if k != "scope"} for r in table_of(view)[uid]],
            [{k: v for k, v in r.items() if k != "scope"} for r in temporal_observations(inventory, view, uid)],
            root[uid]["temporal_matches"])
        if not bad:
            continue
        item = next(o for o in fact["obligations"] if o["obligation_id"] == ROOT_OBLIGATION_ID)
        if item["verdict"] == "supported":
            item["verdict"] = "partial"
        item["missing"] = "；".join(p["reason"] for p in bad)
        fact["verdict"] = _derived_fact_verdict(fact["obligations"])
        fact["missing"] = "；".join(o["missing"] for o in fact["obligations"] if o["missing"])
    return facts
