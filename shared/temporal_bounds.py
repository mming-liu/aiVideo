"""Exact interval entailment for a proposed structured temporal verification seam.

This is a bounded numeric checker, not an NLP parser. Models must independently
normalize requirements/observations and resolve event scope. No story wording,
Chinese time keyword table or literal-claim coverage rule lives here.
"""
from fractions import Fraction
import math


UNITS = ("calendar_month", "second")
RELATIONS = ("elapsed", "offset")
INTERVAL_KEYS = {"unit", "relation", "lower", "upper", "lower_closed", "upper_closed"}


def interval_problems(interval, *, requirement=False):
    if not isinstance(interval, dict) or set(interval) != INTERVAL_KEYS:
        return ["interval_shape"]
    if interval["unit"] not in UNITS or interval["relation"] not in RELATIONS:
        return ["interval_domain"]
    if any(type(interval[k]) is not bool for k in ("lower_closed", "upper_closed")):
        return ["interval_boundary_type"]
    for key in ("lower", "upper"):
        value = interval[key]
        if value is not None and (type(value) not in (int, float)
                                  or (type(value) is float and not math.isfinite(value))):
            return ["interval_number"]
        if value is None and interval[key + "_closed"]:
            return ["interval_infinite_closed"]
    lo, hi = interval["lower"], interval["upper"]
    if lo is not None and hi is not None:
        if lo > hi or (lo == hi and not (interval["lower_closed"] and interval["upper_closed"])):
            return ["interval_empty"]
    if interval["relation"] == "elapsed" and lo is not None and lo < 0:
        return ["interval_negative_elapsed"]
    if requirement and lo is None and hi is None:
        return ["interval_requirement_unbounded"]
    return []


def interval_entails(observed, required):
    """True iff all possible observed values satisfy the frozen requirement.

Null endpoints mean unbounded/unknown, never zero or a default duration. Units
must already be normalized, e.g. three years and 36 months -> calendar_month 36.
"""
    if interval_problems(observed) or interval_problems(required, requirement=True):
        return False
    if any(observed[k] != required[k] for k in ("unit", "relation")):
        return False
    for bound, sign in (("lower", 1), ("upper", -1)):
        req, obs = required[bound], observed[bound]
        if req is None:
            continue
        if obs is None:
            return False
        delta = (Fraction(str(obs)) - Fraction(str(req))) * sign
        if delta < 0:
            return False
        if delta == 0 and observed[bound + "_closed"] and not required[bound + "_closed"]:
            return False
    return True


def temporal_problems(requirements, observations, matches):
    """Frozen required edges -> explicit observation mapping -> numeric verdict.

Each input edge: id/fact_uid/span/interval. `span` provenance is the caller's
normalization admission responsibility. Missing matches are content failures,
unknown/duplicate/foreign mappings are protocol failures. Semantic event matching
remains the model's responsibility and must be independently calibrated.
"""
    problems = []
    def problem(code, uid, reason):
        return {"code": code, "kind": "verification", "ref": uid, "uid": uid,
                "path": "temporal_bounds", "reason": reason,
                "fix": "补齐该事件的实际时间关系，不能从目标主张反填展示时间"}
    groups = []
    for rows, required in ((requirements, True), (observations, False)):
        if not isinstance(rows, list):
            return [problem("verify_temporal_shape", "", "时间边必须为数组")]
        group = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "fact_uid", "span", "interval"}
                    or any(not isinstance(row[k], str) or not row[k].strip() for k in ("id", "fact_uid", "span"))
                    or interval_problems(row["interval"], requirement=required)):
                problems.append(problem("verify_temporal_shape", "", "时间边字段或区间非法"))
                continue
            if row["id"] in group:
                problems.append(problem("verify_temporal_identity", row["fact_uid"], "时间边重复"))
            group[row["id"]] = row
        groups.append(group)
    if problems:
        return problems
    reqs, obs = groups
    if not isinstance(matches, list):
        return [problem("verify_temporal_shape", "", "事件匹配须为数组")]
    mapping = {}
    for match in matches:
        if (not isinstance(match, dict) or set(match) != {"requirement_id", "observation_id"}
                or any(not isinstance(match[k], str) for k in match)):
            problems.append(problem("verify_temporal_shape", "", "事件匹配字段非法"))
            continue
        rid, oid = match["requirement_id"], match["observation_id"]
        if rid not in reqs or oid not in obs or rid in mapping:
            problems.append(problem("verify_temporal_identity", "", "未知、缺失或重复的事件匹配"))
            continue
        if reqs[rid]["fact_uid"] != obs[oid]["fact_uid"]:
            problems.append(problem("verify_temporal_scope", reqs[rid]["fact_uid"], "时间证据不属同一事实"))
            continue
        mapping[rid] = oid
    for rid, req in reqs.items():
        oid = mapping.get(rid)
        if oid is None or not interval_entails(obs[oid]["interval"], req["interval"]):
            problems.append(problem("verify_fact_unsupported", req["fact_uid"],
                "冻结时间关系 %s 没有足够的展示边界；实际可能区间不能被目标区间包含" % rid))
    return problems
