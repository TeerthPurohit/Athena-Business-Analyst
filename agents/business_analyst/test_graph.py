import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import select

from agents.business_analyst.facts import BATenantContext, assert_fact, register_source
from agents.business_analyst.graph import normalize_node_id, rebuild_graph
from agents.business_analyst.models import BaNode, BaEdge, BaProject


def test_normalize_node_id():
    assert normalize_node_id("Requirement", "REQ-1") == "requirement:req-1"
    assert normalize_node_id("  Goal  ", "goal_2 ") == "goal:goal_2"


@pytest.mark.asyncio
async def test_rebuild_graph_creates_nodes_and_edges(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="interview", tier="tier_1", content_hash="hash1")

    # 1. Assert an attribute fact (creates a node with attribute)
    await assert_fact(
        ctx,
        db_session,
        subject_type="Requirement",
        subject_key="req-1",
        predicate="text",
        value={"text": "Hello world"},
        source_id=source.id,
        asserted_by="test-suite",
    )

    # 2. Assert a relation fact (creates an edge + target node if not exist)
    await assert_fact(
        ctx,
        db_session,
        subject_type="Requirement",
        subject_key="req-1",
        predicate="derived_from",
        object_type="Goal",
        object_key="goal-1",
        source_id=source.id,
        asserted_by="test-suite",
    )

    # Rebuild graph
    await rebuild_graph(ctx, db_session)

    # Query nodes
    nodes_result = await db_session.execute(
        select(BaNode).where(BaNode.project_id == ctx.project_id).order_by(BaNode.id)
    )
    nodes = nodes_result.scalars().all()
    assert len(nodes) == 2

    node_req = [n for n in nodes if n.id == "requirement:req-1"][0]
    node_goal = [n for n in nodes if n.id == "goal:goal-1"][0]

    assert node_req.type == "Requirement"
    assert node_req.key == "req-1"
    assert node_req.attrs == {"text": {"text": "Hello world"}}

    assert node_goal.type == "Goal"
    assert node_goal.key == "goal-1"
    assert node_goal.attrs == {}

    # Query edges
    edges_result = await db_session.execute(
        select(BaEdge).where(BaEdge.project_id == ctx.project_id)
    )
    edges = edges_result.scalars().all()
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_node_id == "requirement:req-1"
    assert edge.target_node_id == "goal:goal-1"
    assert edge.relationship == "derived_from"


@pytest.mark.asyncio
async def test_rebuild_graph_winner_rule(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)

    # Create sources of different tiers
    src_tier3 = await register_source(ctx, db_session, kind="document", tier="tier_3", content_hash="h3")
    src_tier2 = await register_source(ctx, db_session, kind="document", tier="tier_2", content_hash="h2")
    src_tier1 = await register_source(ctx, db_session, kind="interview", tier="tier_1", content_hash="h1")

    # Tier 3 fact (first seq)
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="title",
        value={"val": "T3 Title"}, source_id=src_tier3.id, asserted_by="test-suite"
    )

    # Tier 1 fact (should win over Tier 3 even though Tier 3 was first)
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="title",
        value={"val": "T1 Title"}, source_id=src_tier1.id, asserted_by="test-suite"
    )

    # Tier 2 fact (lower priority than Tier 1, higher than Tier 3)
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="title",
        value={"val": "T2 Title"}, source_id=src_tier2.id, asserted_by="test-suite"
    )

    # Rebuild and assert that Tier 1 won
    await rebuild_graph(ctx, db_session)
    node = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))
    assert node.attrs["title"] == {"val": "T1 Title"}

    # Now, test that human_approval wins when tiers are equal
    # Create another Tier 1 fact, but approved
    f_unapproved = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="title",
        value={"val": "T1 Unapproved"}, source_id=src_tier1.id, asserted_by="test-suite"
    )
    from agents.business_analyst.facts import approve_fact
    # Approve it
    await approve_fact(ctx, db_session, fact_id=f_unapproved.id, asserted_by="ba-lead")

    await rebuild_graph(ctx, db_session)
    node = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))
    assert node.attrs["title"] == {"val": "T1 Unapproved"}

    # Now, test that seq wins when tiers and approval are equal
    # Add a newer approved Tier 1 fact
    f_new = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="title",
        value={"val": "T1 Approved New"}, source_id=src_tier1.id, asserted_by="test-suite"
    )
    await approve_fact(ctx, db_session, fact_id=f_new.id, asserted_by="ba-lead")

    await rebuild_graph(ctx, db_session)
    node = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))
    assert node.attrs["title"] == {"val": "T1 Approved New"}


@pytest.mark.asyncio
async def test_rebuild_graph_placeholder_rule(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="interview", tier="tier_1", content_hash="hp")

    # Fact with None value
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value=None, source_id=source.id, asserted_by="test-suite"
    )

    # Fact with dict containing None
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="details",
        value={"description": None, "priority": "high"}, source_id=source.id, asserted_by="test-suite"
    )

    # Fact with empty dict
    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="summary",
        value={}, source_id=source.id, asserted_by="test-suite"
    )

    await rebuild_graph(ctx, db_session)
    node = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))

    assert node.attrs["text"] == {"value": "<UNSPECIFIED: text>"}
    assert node.attrs["details"] == {"description": "<UNSPECIFIED: description>", "priority": "high"}
    assert node.attrs["summary"] == {"value": "<UNSPECIFIED: summary>"}


@pytest.mark.asyncio
async def test_rebuild_graph_tenant_isolation(db_session, ba_project):
    # Org A context
    ctx_a = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source_a = await register_source(ctx_a, db_session, kind="interview", tier="tier_1", content_hash="ha")
    await assert_fact(
        ctx_a, db_session, subject_type="Requirement", subject_key="req-a", predicate="text",
        value={"val": "Org A Requirement"}, source_id=source_a.id, asserted_by="test-suite"
    )

    # Org B context
    project_b = BaProject(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), name="Org B Project")
    db_session.add(project_b)
    await db_session.flush()
    ctx_b = BATenantContext(org_id=project_b.org_id, project_id=project_b.id)
    source_b = await register_source(ctx_b, db_session, kind="interview", tier="tier_1", content_hash="hb")
    await assert_fact(
        ctx_b, db_session, subject_type="Requirement", subject_key="req-b", predicate="text",
        value={"val": "Org B Requirement"}, source_id=source_b.id, asserted_by="test-suite"
    )

    # Rebuild graph only for Org A
    await rebuild_graph(ctx_a, db_session)

    # Org A's node should exist
    node_a = await db_session.get(BaNode, (ctx_a.project_id, "requirement:req-a"))
    assert node_a is not None
    assert node_a.attrs["text"] == {"val": "Org A Requirement"}

    # Org B's node should NOT exist
    node_b = await db_session.get(BaNode, (ctx_b.project_id, "requirement:req-b"))
    assert node_b is None

    # Rebuild graph only for Org B
    await rebuild_graph(ctx_b, db_session)

    node_b = await db_session.get(BaNode, (ctx_b.project_id, "requirement:req-b"))
    assert node_b is not None
    assert node_b.attrs["text"] == {"val": "Org B Requirement"}


@pytest.mark.asyncio
async def test_rebuild_graph_byte_identical(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="interview", tier="tier_1", content_hash="hident")

    await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value={"val": "Test"}, source_id=source.id, asserted_by="test-suite"
    )

    # Rebuild once
    await rebuild_graph(ctx, db_session)
    node1 = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))

    # Rebuild twice (should delete and recreate node)
    await rebuild_graph(ctx, db_session)
    node2 = await db_session.get(BaNode, (ctx.project_id, "requirement:req-1"))

    # The node properties should be identical
    assert node2.id == node1.id
    assert node2.project_id == node1.project_id
    assert node2.org_id == node1.org_id
    assert node2.type == node1.type
    assert node2.key == node1.key
    assert node2.attrs == node1.attrs
    assert node2.projection_algo_version == node1.projection_algo_version
