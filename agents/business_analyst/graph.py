"""Projects facts into the knowledge graph (nodes + edges). Always rebuildable from facts."""
from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agents.business_analyst.facts import BATenantContext
from agents.business_analyst.models import BaFact, BaSource, BaNode, BaEdge


def normalize_node_id(subject_type: str, subject_key: str) -> str:
    """Deterministic node-id normalizer."""
    return f"{subject_type.strip().lower()}:{subject_key.strip().lower()}"


def get_tier_priority(tier: str) -> int:
    """Helper to convert source tier to integer priority for Winner Rule."""
    # Sources are stored as "tier1" (routes.py, scoring config); tests use "tier_1". Exact match
    # after dropping "_" so "tier10" doesn't read as tier1.
    t = tier.lower().strip().replace("_", "")
    if t == "tier1" or "primary" in t or "stakeholder" in t:
        return 4
    elif t == "tier2" or "secondary" in t or "document" in t:
        return 3
    elif t == "tier3" or "tertiary" in t or "external" in t:
        return 2
    elif "llm" in t or "inferred" in t:
        return 1
    return 0


def apply_placeholder_rule(val: dict | None, predicate: str) -> dict:
    """<UNSPECIFIED: x> placeholder rule for honest-failure derivation gaps."""
    if val is None:
        return {"value": f"<UNSPECIFIED: {predicate}>"}
    if isinstance(val, dict):
        new_val = {}
        for k, v in val.items():
            if v is None:
                new_val[k] = f"<UNSPECIFIED: {k}>"
            else:
                new_val[k] = v
        if not new_val:
            new_val["value"] = f"<UNSPECIFIED: {predicate}>"
        return new_val
    return val


async def rebuild_graph(ctx: BATenantContext, session: AsyncSession) -> None:
    """Full rebuild only (truncate + rebuild, one transaction)."""
    # 1. Truncate/delete all existing nodes and edges for this project and tenant in one transaction
    await session.execute(
        delete(BaEdge).where(BaEdge.project_id == ctx.project_id, BaEdge.org_id == ctx.org_id)
    )
    await session.execute(
        delete(BaNode).where(BaNode.project_id == ctx.project_id, BaNode.org_id == ctx.org_id)
    )
    await session.flush()

    # 2. Query all active, non-superseded facts for this project and tenant, joined with BaSource.tier, ordered by seq
    Replacement = aliased(BaFact)
    superseded = (
        select(Replacement.id)
        .where(
            Replacement.replaces == BaFact.id,
            Replacement.org_id == ctx.org_id,
            Replacement.project_id == ctx.project_id,
        )
        .exists()
    )

    stmt = (
        select(BaFact, BaSource.tier)
        .join(BaSource, BaFact.source_id == BaSource.id)
        .where(BaFact.org_id == ctx.org_id, BaFact.project_id == ctx.project_id)
        .where(~superseded)
        .order_by(BaFact.seq)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return

    # Extract facts and their source tiers
    active_facts = [r[0] for r in rows]
    fact_tiers = {r[0].id: r[1] for r in rows}

    # 3. Group facts and map to nodes + edges
    node_info = {}
    node_attrs = {}
    edges_to_create = []

    for fact in active_facts:
        subj_id = normalize_node_id(fact.subject_type, fact.subject_key)
        if subj_id not in node_info:
            node_info[subj_id] = {"type": fact.subject_type, "key": fact.subject_key}

        if fact.object_type and fact.object_key:
            # Relation fact (edge)
            obj_id = normalize_node_id(fact.object_type, fact.object_key)
            if obj_id not in node_info:
                node_info[obj_id] = {"type": fact.object_type, "key": fact.object_key}

            edges_to_create.append({
                "project_id": ctx.project_id,
                "source_node_id": subj_id,
                "target_node_id": obj_id,
                "relationship": fact.predicate,
                "org_id": ctx.org_id,
                "projection_algo_version": 1
            })
        else:
            # Attribute fact (property)
            if subj_id not in node_attrs:
                node_attrs[subj_id] = {}
            if fact.predicate not in node_attrs[subj_id]:
                node_attrs[subj_id][fact.predicate] = []
            node_attrs[subj_id][fact.predicate].append(fact)

    # 4. Resolve attribute values using the Winner Rule and apply Placeholder Rule
    nodes_to_create = []
    for nid, info in node_info.items():
        resolved_attrs = {}
        if nid in node_attrs:
            for predicate, facts in node_attrs[nid].items():
                # Winner Rule: sort by (tier priority, human approval, seq) descending
                sorted_facts = sorted(
                    facts,
                    key=lambda f: (
                        get_tier_priority(fact_tiers[f.id]),
                        f.human_approval,
                        f.seq
                    ),
                    reverse=True
                )
                winning_fact = sorted_facts[0]
                resolved_attrs[predicate] = apply_placeholder_rule(winning_fact.value, predicate)

        nodes_to_create.append(BaNode(
            project_id=ctx.project_id,
            id=nid,
            org_id=ctx.org_id,
            type=info["type"],
            key=info["key"],
            attrs=resolved_attrs,
            projection_algo_version=1,
            created_at=datetime.now(timezone.utc)
        ))

    # 5. Bulk insert nodes and edges
    for node in nodes_to_create:
        session.add(node)
    await session.flush()

    # Deduplicate edges before insert to prevent duplicate key errors (enforced by DB composite PK)
    unique_edges = {}
    for edge_data in edges_to_create:
        ekey = (edge_data["source_node_id"], edge_data["target_node_id"], edge_data["relationship"])
        if ekey not in unique_edges:
            unique_edges[ekey] = edge_data

    for edge_data in unique_edges.values():
        session.add(BaEdge(
            project_id=edge_data["project_id"],
            source_node_id=edge_data["source_node_id"],
            target_node_id=edge_data["target_node_id"],
            relationship=edge_data["relationship"],
            org_id=edge_data["org_id"],
            projection_algo_version=1,
            created_at=datetime.now(timezone.utc)
        ))
    await session.flush()
