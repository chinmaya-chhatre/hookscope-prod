"""Validated AND predicates over deterministic JSONPath key/index selectors."""

import json
import math

from .transform import compile_path

MISSING = object()


def validate_where(raw: object) -> tuple[dict, ...]:
    if not isinstance(raw, list):
        raise ValueError("where must be a list")
    for condition in raw:
        if not isinstance(condition, dict) or set(condition) - {"path", "op", "value"}:
            raise ValueError("invalid where condition")
        path, op = condition.get("path"), condition.get("op")
        if not isinstance(path, str) or op not in {"equals", "in", "gt", "lt", "exists"}:
            raise ValueError("where requires a path and supported op")
        if any(type(s) not in (str, int) for s in compile_path(path)):
            raise ValueError("where paths must select a single value")
        if op == "exists":
            if "value" in condition:
                raise ValueError("exists does not accept value")
        elif "value" not in condition:
            raise ValueError("where comparison requires value")
        value = condition.get("value")
        if op == "in" and not isinstance(value, list):
            raise ValueError("in requires a list")
        if op in {"gt", "lt"} and (
            type(value) not in (int, float) or not math.isfinite(value)
        ):
            raise ValueError("ordering requires a finite number")
    return tuple(dict(c) for c in raw)


def equal(a: object, b: object) -> bool:
    numeric = type(a) in (int, float) and type(b) in (int, float)
    return (numeric or type(a) is type(b)) and a == b


def matches_where(body: str, conditions: tuple[dict, ...]) -> bool:
    if not conditions:
        return True
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    for condition in conditions:
        value = payload
        for segment in compile_path(condition["path"]):
            if isinstance(value, dict) and isinstance(segment, str) and segment in value:
                value = value[segment]
            elif isinstance(value, list) and type(segment) is int and -len(value) <= segment < len(value):
                value = value[segment]
            else:
                value = MISSING
                break
        op, expected = condition["op"], condition.get("value")
        if op == "exists":
            passed = value is not MISSING
        elif value is MISSING:
            passed = False
        elif op == "equals":
            passed = equal(value, expected)
        elif op == "in":
            passed = any(equal(value, item) for item in expected)
        else:
            passed = type(value) in (int, float) and math.isfinite(value) and (
                value > expected if op == "gt" else value < expected
            )
        if not passed:
            return False
    return True
