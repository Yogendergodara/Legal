"""Merge hybrid compliance findings from prescreen and LLM passes."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding


def merge_compliance_findings(
    *,
    prescreen: list[ComplianceFinding],
    pass1: list[ComplianceFinding],
    pass2: list[ComplianceFinding],
) -> list[ComplianceFinding]:
    """Merge by category_id; later passes override earlier (pass2 > pass1 > prescreen)."""
    by_id: dict[str, ComplianceFinding] = {}
    for finding in prescreen:
        by_id[finding.dimension_id] = finding
    for finding in pass1:
        by_id[finding.dimension_id] = finding
    for finding in pass2:
        by_id[finding.dimension_id] = finding
    return list(by_id.values())
