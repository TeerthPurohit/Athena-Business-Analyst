"""Capability definitions and epistemic role split for BA OS (§2.1)."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from agents.business_analyst.models import BaCapability


class CapabilityKind(str, Enum):
    ACQUISITION = "acquisition"  # collects external knowledge (e.g. interview, doc analysis)
    DERIVATION = "derivation"   # infers structured knowledge from graph (emits structured fields, not prose)
    PROJECTION = "projection"   # generates deliverables (rendering, no reasoning)


@dataclass
class CapabilitySpec:
    key: str
    kind: CapabilityKind
    name: str
    description: Optional[str] = None
    org_id: Optional[str] = None  # None = global
    is_side_effecting: bool = False  # Reserved from day one (§7.1)
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    conditions: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_model(cls, model: BaCapability) -> "CapabilitySpec":
        return cls(
            key=model.key,
            kind=CapabilityKind(model.kind),
            name=model.name,
            description=model.description,
            org_id=model.org_id,
            is_side_effecting=model.is_side_effecting,
            inputs=model.inputs,
            outputs=model.outputs,
            conditions=model.conditions,
        )
