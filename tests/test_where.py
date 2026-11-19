import json

import pytest

from hookscope.rules import InvalidRuleError, parse_rules


def rule(conditions):
    return parse_rules([{"target_url": "http://localhost/hook", "where": conditions}])[0]


def event(body):
    return {"verification": "valid", "source": "generic", "body": json.dumps(body)}


@pytest.mark.parametrize("op,value,yes,no", [
    ("equals", 2, 2, True), ("in", [2, 3], 3, "3"),
    ("gt", 2, 3, 2), ("lt", 2, 1, 2),
])
def test_comparisons(op, value, yes, no):
    r = rule([{"path": "$.a[0]", "op": op, "value": value}])
    assert r.matches(event({"a": [yes]}))
    assert not r.matches(event({"a": [no]}))
    assert not r.matches(event({}))
    assert rule(r.to_dict()["where"]).to_dict() == r.to_dict()


def test_exists_distinguishes_null_and_missing_and_all_conditions_apply():
    r = rule([{"path": "$.a", "op": "exists"},
              {"path": "$.b", "op": "equals", "value": False}])
    assert r.matches(event({"a": None, "b": False}))
    assert not r.matches(event({"b": False}))
    assert not r.matches(event({"a": None, "b": 0}))
    assert not r.matches({"verification": "valid", "body": "not json"})


@pytest.mark.parametrize("condition", [
    {}, {"path": "$.*", "op": "exists"}, {"path": "$.a", "op": "bad"},
    {"path": "$.a", "op": "in", "value": "a"},
    {"path": "$.a", "op": "gt", "value": True},
    {"path": "$.a", "op": "exists", "value": True},
])
def test_invalid_configuration(condition):
    with pytest.raises(InvalidRuleError):
        rule([condition])
