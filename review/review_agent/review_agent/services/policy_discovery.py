"""Discover tenant policy documents by routing topics (Pass 2 — no LLM)."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import DocumentKind, SearchRequest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.schemas.discovered_policy import DiscoveredPolicy


async def discover_policies_from_topics(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    topics: list[str],
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
) -> tuple[list[DiscoveredPolicy], list[str]]:
    """Search tenant policy index per topic; dedupe and rank by best score."""
    warnings: list[str] = []
    if not topics:
        warnings.append("No routing topics provided; policy discovery skipped.")
        return [], warnings

    aggregated: dict[str, DiscoveredPolicy] = {}

    for topic in topics:
        topic_clean = topic.strip()
        if not topic_clean:
            continue
        hits = await client.search_policy(
            SearchRequest(
                tenant_id=tenant_id,
                query=topic_clean,
                kind=DocumentKind.POLICY,
                contract_type=contract_type,
                policy_type=policy_type,
                top_k=settings.discovery_top_k_per_topic,
            )
        )
        for hit in hits:
            if hit.score < settings.discovery_min_score:
                continue
            parent = hit.parent_chunk
            doc_id = str(parent.document_id)
            doc_title = str(parent.metadata.get("document_title") or "").strip() or parent.title or ""
            existing = aggregated.get(doc_id)
            applies = list(parent.applies_to_contract_types or [])
            if existing is None:
                aggregated[doc_id] = DiscoveredPolicy(
                    document_id=doc_id,
                    title=doc_title,
                    policy_type=parent.policy_type,
                    match_score=hit.score,
                    matched_topics=[topic_clean],
                    applies_to_contract_types=applies,
                )
            else:
                matched = list(existing.matched_topics)
                if topic_clean not in matched:
                    matched.append(topic_clean)
                aggregated[doc_id] = existing.model_copy(
                    update={
                        "match_score": max(existing.match_score, hit.score),
                        "matched_topics": matched,
                        "title": existing.title or doc_title or parent.title or "",
                        "policy_type": existing.policy_type or parent.policy_type,
                        "applies_to_contract_types": existing.applies_to_contract_types or applies,
                    }
                )

    ranked = sorted(aggregated.values(), key=lambda p: p.match_score, reverse=True)
    capped = ranked[: settings.discovery_max_policies]

    if not capped:
        warnings.append(
            f"No policies discovered for tenant '{tenant_id}' from {len(topics)} topic(s). "
            "Ensure playbooks are indexed in the document store."
        )

    return capped, warnings


def discovered_to_indexed_entries(policies: list[DiscoveredPolicy]) -> list[dict]:
    """Map discovery results to indexed_policies metadata shape."""
    return [
        {
            "document_id": p.document_id,
            "title": p.title,
            "policy_type": p.policy_type,
            "applies_to_contract_types": list(p.applies_to_contract_types),
            "discovery_score": p.match_score,
            "matched_topics": list(p.matched_topics),
        }
        for p in policies
    ]


def parse_discovered_document_ids(policies: list[DiscoveredPolicy]) -> list[str]:
    """Stable document_id list for policy_plan."""
    seen: set[str] = set()
    ordered: list[str] = []
    for policy in policies:
        if policy.document_id not in seen:
            seen.add(policy.document_id)
            ordered.append(policy.document_id)
    return ordered
