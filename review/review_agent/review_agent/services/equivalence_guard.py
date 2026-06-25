"""Post-compare semantic equivalence guard for known phrase pairs (Phase C4)."""

from __future__ import annotations

import re

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem

EQUIVALENCE_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"legal retention", "retention requirements", "applicable law"}),
        frozenset({"required by law", "legal retention", "otherwise required"}),
    ),
    (
        frozenset({"legal hold", "litigation hold"}),
        frozenset({"ongoing litigation", "audit", "investigation"}),
    ),
)

_CONTRADICTION = re.compile(
    r"(?i)\b(shall not|must not|prohibited|no right to|denied|forbidden)\b"
)
_SUFFIX = " (Semantic equivalence guard.)"


def _pair_matches(contract_blob: str, policy_blob: str) -> bool:
    for left, right in EQUIVALENCE_PAIRS:
        if any(p in contract_blob for p in left) and any(p in policy_blob for p in right):
            return True
        if any(p in contract_blob for p in right) and any(p in policy_blob for p in left):
            return True
    return False


def _item_matches_equivalence(item: SectionCompareItem) -> bool:
    contract_blob = f"{item.contract_quote} {item.rationale}".lower()
    policy_blob = f"{item.policy_quote} {item.rationale}".lower()
    if not _pair_matches(contract_blob, policy_blob):
        return False
    combined = f"{contract_blob} {policy_blob}"
    return not _CONTRADICTION.search(combined)


def apply_equivalence_guard(
    items: list[SectionCompareItem],
) -> tuple[list[SectionCompareItem], int]:
    """Downgrade false NON_COMPLIANT when contract/policy phrases are equivalent."""
    downgraded = 0
    result: list[SectionCompareItem] = []
    for item in items:
        if item.status != ComplianceStatus.NON_COMPLIANT or item.severity not in (
            Severity.CRITICAL,
            Severity.IMPORTANT,
        ):
            result.append(item)
            continue
        if not _item_matches_equivalence(item):
            result.append(item)
            continue
        rationale = item.rationale
        if _SUFFIX not in rationale:
            rationale = f"{rationale}{_SUFFIX}"
        result.append(
            item.model_copy(
                update={
                    "status": ComplianceStatus.COMPLIANT,
                    "severity": Severity.INFO,
                    "rationale": rationale,
                }
            )
        )
        downgraded += 1
    return result, downgraded
