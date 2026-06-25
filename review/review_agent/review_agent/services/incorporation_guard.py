"""Post-compare guard for policy adoption by reference (Phase C1)."""

from __future__ import annotations

import re

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.named_policy_routing import extract_named_policy_title_keys

_ADOPTION_VERBS = re.compile(
    r"(?i)\b(agree|agrees|uphold|comply|complies|adopt|adopts|subject to|consistent with|bound by)\b"
)
_FALSE_GAP = re.compile(
    r"(?i)\b("
    r"does not (?:explicitly )?acknowledge|fail(?:s|ed)? to acknowledge|"
    r"no explicit acknowledgment|not explicitly acknowledge|"
    r"missing acknowledgment|does not reference|fails to reference|"
    r"does not adopt|fails to adopt|does not uphold|fails to uphold"
    r")\b"
)
_CONTRADICTION = re.compile(
    r"(?i)\b("
    r"prohibited|shall not|must not|below (?:the )?minimum|exceeds|"
    r"contradict|material(?:ly)? deviat|numeric|threshold|\d+\s*(?:days|months|years|%|percent)"
    r")\b"
)
_SUFFIX = " (Incorporation by reference detected.)"


def _has_adoption_language(text: str, policy_keys: list[str]) -> bool:
    haystack = (text or "").lower()
    if not haystack or not policy_keys:
        return False
    for key in policy_keys:
        idx = haystack.find(key)
        if idx < 0:
            continue
        window = haystack[max(0, idx - 80) : idx + len(key) + 80]
        if _ADOPTION_VERBS.search(window):
            return True
    return False


def _is_false_acknowledgment_gap(item: SectionCompareItem) -> bool:
    blob = f"{item.rationale} {item.dimension_label}"
    return bool(_FALSE_GAP.search(blob)) or "acknowledgment" in blob.lower()


def apply_incorporation_guard(
    items: list[SectionCompareItem],
    sections_by_id: dict[str, IndexedChunk],
) -> tuple[list[SectionCompareItem], int]:
    """Upgrade false NON_COMPLIANT when contract adopts policy by name."""
    upgraded = 0
    result: list[SectionCompareItem] = []
    for item in items:
        if item.status != ComplianceStatus.NON_COMPLIANT:
            result.append(item)
            continue
        section = sections_by_id.get(item.section_id)
        section_text = (section.text if section else "") or ""
        combined = f"{section_text} {item.contract_quote}"
        policy_keys = extract_named_policy_title_keys(combined)
        if not policy_keys or not _has_adoption_language(combined, policy_keys):
            result.append(item)
            continue
        if _CONTRADICTION.search(
            f"{item.rationale} {item.contract_quote} {item.policy_quote}"
        ) and not _FALSE_GAP.search(item.rationale):
            result.append(item)
            continue
        if not _is_false_acknowledgment_gap(item):
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
        upgraded += 1
    return result, upgraded
