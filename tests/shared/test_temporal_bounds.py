"""Finite numeric matrix; does not pretend model normalization has been tested."""
import copy
import pytest
from shared.temporal_bounds import interval_entails, interval_problems, temporal_problems


def interval(lower=0, upper=36, unit="calendar_month", relation="elapsed", lc=True, uc=True):
    return {"lower": lower, "upper": upper, "unit": unit, "relation": relation,
            "lower_closed": lc if lower is not None else False,
            "upper_closed": uc if upper is not None else False}


@pytest.mark.parametrize("observed,required,expected", [
    (interval(0, 36), interval(0, 36), True),
    (interval(0, 24), interval(0, 36), True),
    (interval(0, 48), interval(0, 36), False),
    (interval(0, None), interval(0, 36), False),
    (interval(None, None), interval(0, 36), False),
    (interval(0, 36), interval(0, 36, uc=False), False),
    (interval(0, 36, uc=False), interval(0, 36), True),
    (interval(36, 36), interval(0, 36), True),
    (interval(0, 36, unit="second"), interval(0, 36), False),
    (interval(-48, -48, relation="offset"), interval(0, 36), False),
    (interval(0.1, 0.3, unit="second"), interval(0.1, 0.3, unit="second"), True),
])
def test_exact_interval_containment(observed, required, expected):
    assert interval_entails(observed, required) is expected


@pytest.mark.parametrize("field,value", [("lower", True), ("upper", "36"), ("upper", float("nan")),
    ("upper", float("inf")), ("upper", -1), ("lower_closed", 1), ("unit", "year"), ("relation", None)])
def test_bad_interval_rejects_without_throwing(field, value):
    bad = interval()
    bad[field] = value
    assert interval_problems(bad) and not interval_entails(bad, interval())


def test_elapsed_anchor_is_not_elapsed_duration_and_missing_is_not_zero():
    required = [{"id": "deadline", "fact_uid": "f-any", "span": "三年内完成",
                 "interval": interval()}]
    full = [{"id": "duration", "fact_uid": "f-any", "span": "三十六个月间完成",
             "interval": interval()}]
    cut = [{"id": "duration", "fact_uid": "f-any", "span": "此后完成",
            "interval": interval(0, None)}]
    match = [{"requirement_id": "deadline", "observation_id": "duration"}]
    assert temporal_problems(required, full, match) == []
    assert [(p["code"], p["uid"]) for p in temporal_problems(required, cut, match)] == [
        ("verify_fact_unsupported", "f-any")]
    anchor = copy.deepcopy(full)
    anchor[0]["interval"] = interval(-48, -48, relation="offset")
    assert temporal_problems(required, anchor, match)[0]["code"] == "verify_fact_unsupported"
    assert temporal_problems(required, full, [])[0]["code"] == "verify_fact_unsupported"


def test_wrong_scope_and_unknown_mapping_cannot_pass():
    req = [{"id": "r", "fact_uid": "f1", "span": "required", "interval": interval()}]
    obs = [{"id": "o", "fact_uid": "f2", "span": "observed", "interval": interval()}]
    match = [{"requirement_id": "r", "observation_id": "o"}]
    assert "verify_temporal_scope" in {p["code"] for p in temporal_problems(req, obs, match)}
    match[0]["observation_id"] = "unknown"
    assert "verify_temporal_identity" in {p["code"] for p in temporal_problems(req, obs, match)}
