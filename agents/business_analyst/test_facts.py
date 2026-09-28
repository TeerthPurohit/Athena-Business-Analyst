import uuid

import pytest

from agents.business_analyst.facts import BATenantContext, assert_fact, register_source, get_facts, approve_fact
from agents.business_analyst.models import BaProject


@pytest.mark.asyncio
async def test_register_source_scopes_to_project_and_org(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)

    source = await register_source(
        ctx, db_session, kind="interview", tier="tier_1", content_hash="abc123"
    )

    assert source.id is not None
    assert source.project_id == ba_project.id
    assert source.org_id == ba_project.org_id
    assert source.kind == "interview"


@pytest.mark.asyncio
async def test_assert_fact_creates_an_unapproved_unreplaced_fact(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_2", content_hash="h1")

    fact = await assert_fact(
        ctx,
        db_session,
        subject_type="Actor",
        subject_key="nurse-1",
        predicate="has_goal",
        value={"text": "see stock levels"},
        source_id=source.id,
        asserted_by="test-suite",
    )

    assert fact.id is not None
    assert fact.seq is not None
    assert fact.project_id == ba_project.id
    assert fact.org_id == ba_project.org_id
    assert fact.human_approval is False
    assert fact.replaces is None
    assert fact.value == {"text": "see stock levels"}


@pytest.mark.asyncio
async def test_assert_fact_has_no_human_approval_parameter(db_session, ba_project):
    # human_approval must never be settable from assert_fact's public signature — only
    # approve_fact (Task 7) may produce a fact with human_approval=True.
    import inspect

    sig = inspect.signature(assert_fact)
    assert "human_approval" not in sig.parameters


@pytest.mark.asyncio
async def test_get_facts_returns_only_the_current_value_in_a_replaces_chain(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h2")

    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    corrected = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-1", predicate="text",
        value={"v": 2}, source_id=source.id, asserted_by="test-suite", replaces=original.id,
    )

    facts = await get_facts(ctx, db_session, subject_key="req-1", predicate="text")

    assert [f.id for f in facts] == [corrected.id]
    assert facts[0].value == {"v": 2}


@pytest.mark.asyncio
async def test_get_facts_walks_multi_level_replaces_chains(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h3")

    v1 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    v2 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 2}, source_id=source.id, asserted_by="test-suite", replaces=v1.id,
    )
    v3 = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-2", predicate="text",
        value={"v": 3}, source_id=source.id, asserted_by="test-suite", replaces=v2.id,
    )

    facts = await get_facts(ctx, db_session, subject_key="req-2", predicate="text")

    assert [f.id for f in facts] == [v3.id]


@pytest.mark.asyncio
async def test_get_facts_never_returns_another_orgs_rows_even_with_a_matching_project_id(
    db_session, ba_project
):
    real_ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(real_ctx, db_session, kind="document", tier="tier_1", content_hash="h4")
    await assert_fact(
        real_ctx, db_session, subject_type="Requirement", subject_key="req-3", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    # A forged context: correct project_id, wrong org_id — must return nothing, proving the
    # org filter (not just the project filter) is enforced in the query.
    forged_ctx = BATenantContext(org_id="org-attacker", project_id=ba_project.id)
    facts = await get_facts(forged_ctx, db_session, subject_key="req-3")

    assert facts == []


@pytest.mark.asyncio
async def test_approve_fact_inserts_a_new_row_and_never_touches_the_original(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h5")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-4", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    approved = await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead")

    assert approved.id != original.id
    assert approved.human_approval is True
    assert approved.replaces == original.id
    assert approved.value == original.value
    assert approved.subject_key == original.subject_key

    # The original row is untouched — re-fetch it from the DB to be sure nothing mutated it in place.
    # session.refresh() expires the object from the identity map and re-fetches it from the database,
    # ensuring we actually verify the DB row, not just the Python object in memory.
    await db_session.refresh(original)
    assert original.human_approval is False


@pytest.mark.asyncio
async def test_approve_fact_becomes_the_current_value(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h6")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-5", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    approved = await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead")

    facts = await get_facts(ctx, db_session, subject_key="req-5", predicate="text")
    assert [f.id for f in facts] == [approved.id]


@pytest.mark.asyncio
async def test_approve_fact_rejects_a_fact_from_another_tenant(db_session, ba_project):
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="h7")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-6", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    forged_ctx = BATenantContext(org_id="org-attacker", project_id=ba_project.id)
    with pytest.raises(ValueError):
        await approve_fact(forged_ctx, db_session, fact_id=original.id, asserted_by="attacker")


@pytest.mark.asyncio
async def test_get_facts_superseded_check_is_tenant_scoped(db_session, ba_project):
    # Finding 1: a Replacement row belonging to a DIFFERENT org must not be able to hide a
    # fact from its own org's get_facts — the superseded subquery must filter on
    # org_id/project_id, not just the bare `replaces == id` correlation.
    org_a_ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source_a = await register_source(org_a_ctx, db_session, kind="document", tier="tier_1", content_hash="ha")
    fact_a = await assert_fact(
        org_a_ctx, db_session, subject_type="Requirement", subject_key="req-cross", predicate="text",
        value={"v": 1}, source_id=source_a.id, asserted_by="test-suite",
    )

    # A second, unrelated org+project.
    project_b = BaProject(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), name="Org B Project")
    db_session.add(project_b)
    await db_session.flush()
    org_b_ctx = BATenantContext(org_id=project_b.org_id, project_id=project_b.id)
    source_b = await register_source(org_b_ctx, db_session, kind="document", tier="tier_1", content_hash="hb")

    # Org B fabricates a fact whose `replaces` points at org A's fact — org B has no business
    # pointing at org A's row, but nothing stops it at the DB layer; get_facts must still not
    # treat fact_a as superseded when queried from org A's own context.
    await assert_fact(
        org_b_ctx, db_session, subject_type="Requirement", subject_key="req-cross", predicate="text",
        value={"v": 999}, source_id=source_b.id, asserted_by="attacker", replaces=fact_a.id,
    )

    facts = await get_facts(org_a_ctx, db_session, subject_key="req-cross", predicate="text")
    assert [f.id for f in facts] == [fact_a.id]


@pytest.mark.asyncio
async def test_approve_fact_forking_a_replacement_chain_raises(db_session, ba_project):
    # Finding 2: two facts must never both set `replaces` to the same target. approve_fact
    # guards this in-app (the partial unique index ba_0003_unique_replaces is not in models/fact.py).
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="hfork")
    original = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-fork", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead")

    # Approving the same already-superseded fact again forks the chain: a second row also
    # sets replaces=original.id.
    with pytest.raises(ValueError, match="already superseded"):
        await approve_fact(ctx, db_session, fact_id=original.id, asserted_by="ba-lead-again")


@pytest.mark.asyncio
async def test_register_source_rejects_a_project_from_another_org(db_session, ba_project):
    # Finding 3: register_source must verify ctx.project_id actually belongs to ctx.org_id.
    forged_ctx = BATenantContext(org_id=str(uuid.uuid4()), project_id=ba_project.id)
    with pytest.raises(ValueError):
        await register_source(forged_ctx, db_session, kind="document", tier="tier_1", content_hash="hx")


@pytest.mark.asyncio
async def test_assert_fact_rejects_a_source_from_another_tenant(db_session, ba_project):
    # Finding 3: assert_fact must verify source_id belongs to ctx.org_id/ctx.project_id.
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="hy")

    forged_ctx = BATenantContext(org_id=str(uuid.uuid4()), project_id=ba_project.id)
    with pytest.raises(ValueError):
        await assert_fact(
            forged_ctx, db_session, subject_type="Requirement", subject_key="req-forged-source",
            predicate="text", value={"v": 1}, source_id=source.id, asserted_by="attacker",
        )


@pytest.mark.asyncio
async def test_get_facts_orders_by_seq(db_session, ba_project):
    # Finding 6: seq is the only ordering tiebreaker — get_facts must return rows in seq
    # (insertion) order.
    ctx = BATenantContext(org_id=ba_project.org_id, project_id=ba_project.id)
    source = await register_source(ctx, db_session, kind="document", tier="tier_1", content_hash="hseq")
    first = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-order-1", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )
    second = await assert_fact(
        ctx, db_session, subject_type="Requirement", subject_key="req-order-2", predicate="text",
        value={"v": 1}, source_id=source.id, asserted_by="test-suite",
    )

    facts = await get_facts(ctx, db_session)
    ids_in_order = [f.id for f in facts]
    assert ids_in_order.index(first.id) < ids_in_order.index(second.id)
    assert facts == sorted(facts, key=lambda f: f.seq)
