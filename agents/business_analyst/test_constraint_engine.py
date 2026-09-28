import pytest

from agents.business_analyst.constraint_engine import (
    evaluate_condition,
    evaluate_conditions,
    resolve_path,
)


def test_resolve_path():
    ctx = {
        "attrs": {
            "title": {"val": "System Spec"},
            "status": "active",
        },
        "count": 5,
    }

    assert resolve_path(ctx, "count") == 5
    assert resolve_path(ctx, "attrs.status") == "active"
    assert resolve_path(ctx, "attrs.title.val") == "System Spec"
    assert resolve_path(ctx, "attrs.non_existent") is None
    assert resolve_path(ctx, "attrs.title.val.nested") is None


def test_evaluate_condition_equals():
    ctx = {"status": "approved", "count": 10}

    assert evaluate_condition({"path": "status", "op": "equals", "value": "approved"}, ctx) is True
    assert evaluate_condition({"path": "status", "op": "equals", "value": "draft"}, ctx) is False
    assert evaluate_condition({"path": "count", "op": "equals", "value": 10}, ctx) is True


def test_evaluate_condition_greater_than_less_than():
    ctx = {"count": 15, "empty": None}

    assert evaluate_condition({"path": "count", "op": "greater_than", "value": 10}, ctx) is True
    assert evaluate_condition({"path": "count", "op": "greater_than", "value": 20}, ctx) is False

    assert evaluate_condition({"path": "count", "op": "less_than", "value": 20}, ctx) is True
    assert evaluate_condition({"path": "count", "op": "less_than", "value": 10}, ctx) is False

    # None values evaluate to False for comparison
    assert evaluate_condition({"path": "empty", "op": "greater_than", "value": 0}, ctx) is False


def test_evaluate_condition_contains():
    ctx = {
        "tags": ["frontend", "security"],
        "text": "The hospital inventory system",
        "empty": None,
    }

    assert evaluate_condition({"path": "tags", "op": "contains", "value": "security"}, ctx) is True
    assert evaluate_condition({"path": "tags", "op": "contains", "value": "backend"}, ctx) is False

    assert evaluate_condition({"path": "text", "op": "contains", "value": "hospital"}, ctx) is True
    assert evaluate_condition({"path": "text", "op": "contains", "value": "school"}, ctx) is False

    assert evaluate_condition({"path": "empty", "op": "contains", "value": "test"}, ctx) is False


def test_evaluate_condition_exists():
    ctx = {"present": "value", "empty": None}

    assert evaluate_condition({"path": "present", "op": "exists", "value": True}, ctx) is True
    assert evaluate_condition({"path": "empty", "op": "exists", "value": True}, ctx) is False
    assert evaluate_condition({"path": "missing", "op": "exists", "value": True}, ctx) is False


def test_evaluate_condition_rejects_non_whitelisted_operator():
    ctx = {"code": "print('hello')"}

    # Attempt to pass forbidden / non-whitelisted operators
    with pytest.raises(ValueError, match="is not supported"):
        evaluate_condition({"path": "code", "op": "eval", "value": "print('hello')"}, ctx)

    with pytest.raises(ValueError, match="is not supported"):
        evaluate_condition({"path": "code", "op": "exec", "value": "1+1"}, ctx)

    with pytest.raises(ValueError, match="is not supported"):
        evaluate_condition({"path": "code", "op": "regex_match", "value": ".*"}, ctx)


def test_evaluate_conditions_list():
    ctx = {"status": "approved", "count": 10}

    conditions = [
        {"path": "status", "op": "equals", "value": "approved"},
        {"path": "count", "op": "greater_than", "value": 5},
    ]
    assert evaluate_conditions(conditions, ctx) is True

    failing_conditions = [
        {"path": "status", "op": "equals", "value": "approved"},
        {"path": "count", "op": "greater_than", "value": 20},
    ]
    assert evaluate_conditions(failing_conditions, ctx) is False
