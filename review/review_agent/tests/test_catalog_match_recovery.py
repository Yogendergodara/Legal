"""Tests for catalog taxonomy recovery (IPC4 routing_or_skip)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from document_core.schemas.policy_catalog import CatalogSearchHit
from review_agent.config import ReviewSettings
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_match_recovery import taxonomy_recovery_candidates
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.catalog_registry import CatalogEntry


def _entry(document_id: str, title: str) -> CatalogEntry:
    return CatalogEntry(
        document_id=document_id,
        policy_ref=title.lower().replace(" ", "-"),
        title=title,
        aliases=[title],
        topics=[],
        summary=title,
    )


def test_taxonomy_recovery_sla_maps_to_product_terms():
    doc_sla = str(uuid4())
    doc_priv = str(uuid4())
    entries = [
        _entry(doc_sla, "Atlassian Product-Specific Terms"),
        _entry(doc_priv, "Atlassian Privacy Policy"),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="4-o3",
        intent="service level commitments",
        search_queries=["service level agreement requirements"],
        confidence=0.8,
    )
    scored = taxonomy_recovery_candidates(
        plan=plan,
        obligation_text="Vendor must meet Service Level Agreement commitments for cloud products.",
        section_title="Cloud Products",
        catalog_entries=entries,
        allowed={doc_sla, doc_priv},
        min_score=0.08,
        max_candidates=3,
    )
    assert doc_sla in scored
    assert scored[doc_sla] >= 0.08


def test_taxonomy_recovery_hipaa_maps_to_privacy():
    doc_priv = str(uuid4())
    doc_aup = str(uuid4())
    entries = [
        _entry(doc_priv, "Atlassian Data Processing Addendum"),
        _entry(doc_aup, "Atlassian Acceptable Use Policy"),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="6-o2",
        intent="HIPAA restrictions",
        confidence=0.9,
    )
    scored = taxonomy_recovery_candidates(
        plan=plan,
        obligation_text="Customer must not process protected health information without HIPAA compliance.",
        section_title="Regulatory",
        catalog_entries=entries,
        allowed={doc_priv, doc_aup},
        min_score=0.08,
        max_candidates=2,
    )
    assert doc_priv in scored


@pytest.mark.asyncio
async def test_catalog_match_uses_taxonomy_recovery_when_search_empty():
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = []
    plan = ObligationRoutingPlan(
        obligation_id="4-o3",
        search_queries=["service level agreement"],
        confidence=0.8,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Atlassian Product-Specific Terms")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(catalog_match_taxonomy_recovery_enabled=True),
        obligation_text="Service Level Agreement requirements for cloud product performance.",
        section_title="Products",
    )
    assert doc_id in match.candidate_doc_ids
    assert match.route_decision in ("expand", "compare")
    assert match.route_decision != "ipc"
