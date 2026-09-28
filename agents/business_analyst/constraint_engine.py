"""Constraint Engine for BA OS. Evaluates capability conditions against graph/node context.

CLAUDE.md non-negotiable: NO eval(). Operators are a fixed whitelist:
['equals', 'greater_than', 'less_than', 'contains', 'exists'].
"""
from typing import Any, Dict, List, Optional

ALLOWED_OPERATORS = {"equals", "greater_than", "less_than", "contains", "exists"}


def resolve_path(context: Any, path: str) -> Any:
    """Resolves a dotted path (e.g. 'attrs.title.val') in a nested dictionary or object context."""
    if not path:
        return context
    parts = path.split(".")
    curr = context
    for part in parts:
        if curr is None:
            return None
        if isinstance(curr, dict):
            curr = curr.get(part)
        elif hasattr(curr, part):
            curr = getattr(curr, part)
        else:
            return None
    return curr


def evaluate_condition(condition: Dict[str, Any], context: Any) -> bool:
    """Evaluates a single condition dictionary without using eval().

    Expected shape: {"path": "...", "op": "...", "value": ...}
    """
    if not isinstance(condition, dict):
        raise ValueError(f"Condition must be a dictionary, got {type(condition)}")

    path = condition.get("path")
    op = condition.get("op")
    target_value = condition.get("value")

    if not path or not isinstance(path, str):
        raise ValueError("Condition must contain a valid string 'path'")

    if op not in ALLOWED_OPERATORS:
        raise ValueError(
            f"Operator '{op}' is not supported. Allowed whitelisted operators: {sorted(ALLOWED_OPERATORS)}. "
            "Dynamic code execution (eval) is forbidden."
        )

    actual_value = resolve_path(context, path)

    if op == "equals":
        return actual_value == target_value
    elif op == "greater_than":
        if actual_value is None or target_value is None:
            return False
        try:
            return actual_value > target_value
        except TypeError:  # mismatched types (e.g. str vs int): fail closed
            return False
    elif op == "less_than":
        if actual_value is None or target_value is None:
            return False
        try:
            return actual_value < target_value
        except TypeError:
            return False
    elif op == "contains":
        if actual_value is None:
            return False
        try:
            return target_value in actual_value
        except TypeError:
            return False
    elif op == "exists":
        if isinstance(target_value, bool):
            return (actual_value is not None) == target_value
        return actual_value is not None

    return False


def evaluate_conditions(conditions: Optional[List[Dict[str, Any]]], context: Any) -> bool:
    """Evaluates a list of conditions with AND logic. Returns True if conditions is empty or None."""
    if not conditions:
        return True
    return all(evaluate_condition(cond, context) for cond in conditions)
