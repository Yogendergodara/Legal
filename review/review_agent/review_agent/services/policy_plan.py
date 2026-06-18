"""Build dynamic review categories from indexed policy documents."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import DocumentKind, IndexedChunk, ListSectionsRequest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.policy_plan_llm import filter_categories_llm


def search_queries_from_section(title: str, text: str) -> list[str]:
    """Derive lexical search queries from a policy section (no LLM)."""
    queries: list[str] = []
    title_clean = title.strip()
    if title_clean:
        queries.append(title_clean)

    body = text.strip()
    if title_clean and body.startswith(title_clean):
        body = body[len(title_clean) :].strip()

    if body:
        snippet = " ".join(body.split()[:12])
        if snippet and snippet not in queries:
            queries.append(snippet)

    if not queries and body:
        queries.append(" ".join(body.split()[:8]))

    return queries


def _parse_document_ids(raw_ids: list[str] | None) -> list[UUID]:
    if not raw_ids:
        return []
    parsed: list[UUID] = []
    for value in raw_ids:
        try:
            parsed.append(UUID(str(value)))
        except ValueError:
            continue
    return parsed


def _union_document_ids(
    indexed_policies: list[dict],
    policy_document_ids: list[str] | None,
    store_document_ids: list[UUID],
) -> list[UUID]:
    """Collect policy doc IDs: ingest this run ∪ request IDs ∪ store listing."""
    seen: set[UUID] = set()
    ordered: list[UUID] = []

    def add(doc_id: UUID) -> None:
        if doc_id not in seen:
            seen.add(doc_id)
            ordered.append(doc_id)

    for entry in indexed_policies:
        raw = entry.get("document_id")
        if raw:
            add(UUID(str(raw)))

    for doc_id in _parse_document_ids(policy_document_ids):
        add(doc_id)

    for doc_id in store_document_ids:
        add(doc_id)

    return ordered


def _document_applies(contract_type: str | None, applies_to: list[str] | None) -> bool:
    if not contract_type:
        return True
    if not applies_to:
        return True
    return contract_type in applies_to


async def build_review_plan(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    indexed_policies: list[dict],
    policy_document_ids: list[str] | None,
    contract_type: str | None,
    contract_sections: list[IndexedChunk] | None = None,
    settings: ReviewSettings,
) -> tuple[list[ReviewCategory], list[str]]:
    """Enumerate review categories from policy parent sections in the document store."""
    warnings: list[str] = []
    doc_meta: dict[str, dict] = {
        str(entry["document_id"]): entry for entry in indexed_policies if entry.get("document_id")
    }

    store_ids: list[UUID] = []
    if settings.review_policy_scope == "tenant":
        store_ids = await client.list_policies(tenant_id)
    elif settings.review_policy_scope == "discovered":
        store_ids = []

    document_ids = _union_document_ids(indexed_policies, policy_document_ids, store_ids)

    if not document_ids:
        warnings.append(
            f"No policy documents indexed for tenant '{tenant_id}'. "
            "Upload policies or provide policy_document_ids to enable compliance checking."
        )
        return [], warnings

    categories: list[ReviewCategory] = []

    for document_id in document_ids:
        meta = doc_meta.get(str(document_id), {})
        applies_to = meta.get("applies_to_contract_types") or []
        if isinstance(applies_to, str):
            applies_to = [applies_to]

        if not _document_applies(contract_type, applies_to):
            continue

        sections = await client.list_sections(
            ListSectionsRequest(
                tenant_id=tenant_id,
                document_id=document_id,
                kind=DocumentKind.POLICY,
            )
        )

        for section in sections:
            if len(section.text.strip()) < settings.review_min_section_chars:
                continue

            category_id = f"{document_id}:{section.section_id}"
            categories.append(
                ReviewCategory(
                    category_id=category_id,
                    label=section.title or section.section_id,
                    policy_document_id=document_id,
                    policy_section_id=section.section_id,
                    search_queries=search_queries_from_section(section.title, section.text),
                    source="policy_section",
                )
            )

    categories.sort(key=lambda c: (str(c.policy_document_id), c.policy_section_id))

    if len(categories) > settings.review_max_categories:
        total = len(categories)
        categories = categories[: settings.review_max_categories]
        warnings.append(
            f"Review capped at {settings.review_max_categories} categories "
            f"(policy has {total} sections); increase REVIEW_MAX_CATEGORIES for full coverage."
        )

    if categories:
        policy_titles = {
            str(entry["document_id"]): entry.get("title", "")
            for entry in indexed_policies
            if entry.get("document_id")
        }
        before = len(categories)
        categories = await filter_categories_llm(
            categories=categories,
            contract_sections=contract_sections or [],
            contract_type=contract_type,
            policy_titles_by_doc=policy_titles,
            settings=settings,
        )
        if settings.review_plan_llm_filter and len(categories) < before:
            warnings.append(
                f"LLM plan filter reduced categories from {before} to {len(categories)}."
            )

    return categories, warnings
