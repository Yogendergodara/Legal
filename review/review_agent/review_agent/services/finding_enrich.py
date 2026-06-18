"""Enrich compliance findings with policy document metadata."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding


def build_policy_title_map(
    indexed_policies: list[dict],
    discovered_policies: list[dict] | None = None,
) -> dict[str, str]:
    """Map policy document_id (str) -> human-readable playbook title."""
    titles: dict[str, str] = {}
    for entry in indexed_policies:
        doc_id = str(entry.get("document_id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if doc_id and title:
            titles[doc_id] = title
    for entry in discovered_policies or []:
        doc_id = str(entry.get("document_id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if doc_id and title and doc_id not in titles:
            titles[doc_id] = title
    return titles


def enrich_findings_policy_titles(
    findings: list[ComplianceFinding],
    title_map: dict[str, str],
) -> list[ComplianceFinding]:
    """Attach metadata['policy_title'] from indexed/discovered policy metadata."""
    if not title_map:
        return findings

    enriched: list[ComplianceFinding] = []
    for finding in findings:
        if finding.metadata.get("policy_title"):
            enriched.append(finding)
            continue
        doc_id = str(finding.policy_document_id) if finding.policy_document_id else ""
        title = title_map.get(doc_id, "")
        if not title:
            enriched.append(finding)
            continue
        enriched.append(
            finding.model_copy(
                update={"metadata": {**finding.metadata, "policy_title": title}}
            )
        )
    return enriched
