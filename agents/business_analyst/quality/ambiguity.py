"""Ambiguity Detection (§5, §9).

Identifies structural ambiguity, missing fields, and <UNSPECIFIED: x> placeholders.
"""
from typing import Any, Dict, List


def detect_ambiguities(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Scans graph nodes for ambiguous values or unspecified placeholders."""
    ambiguities: List[Dict[str, Any]] = []

    for node in nodes:
        node_id = node.get("id")
        ntype = node.get("type", "")
        attrs = node.get("attrs", {})

        for field, val in attrs.items():
            if isinstance(val, str) and val.startswith("<UNSPECIFIED:"):
                # Extract placeholder name from <UNSPECIFIED: field_name>
                placeholder = val[13:-1] if val.endswith(">") else val[13:]
                ambiguities.append({
                    "node_id": node_id,
                    "node_type": ntype,
                    "field": field,
                    "placeholder": placeholder.strip(),
                    "kind": "unspecified_placeholder",
                })
            elif val is None or val == "":
                ambiguities.append({
                    "node_id": node_id,
                    "node_type": ntype,
                    "field": field,
                    "placeholder": field,
                    "kind": "missing_value",
                })

    return ambiguities
