"""Dynamic playbook hints from tenant-indexed policy metadata (Java sync)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from document_core.schemas.registry import PolicyRegistryRecord


@dataclass
class PlaybookHints:
    policy_ref: str | None = None
    title: str | None = None
    review_guidance: str | None = None
    preferred_position: str | None = None
    fallback_positions: list[dict[str, Any]] = field(default_factory=list)
    position_type: str | None = None

    def has_content(self) -> bool:
        return bool(
            self.review_guidance
            or self.preferred_position
            or self.fallback_positions
            or self.policy_ref
        )


def _meta_dict(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("metadata")
    return raw if isinstance(raw, dict) else {}


def _hints_from_entry(entry: dict[str, Any]) -> PlaybookHints:
    meta = _meta_dict(entry)
    fallbacks = meta.get("fallback_positions") or entry.get("fallback_positions") or []
    if not isinstance(fallbacks, list):
        fallbacks = []
    return PlaybookHints(
        policy_ref=str(entry.get("policy_ref") or meta.get("policy_ref") or "") or None,
        title=str(entry.get("title") or meta.get("title") or "") or None,
        review_guidance=str(
            entry.get("review_guidance") or meta.get("review_guidance") or ""
        ).strip()
        or None,
        preferred_position=str(
            entry.get("preferred_position") or meta.get("preferred_position") or ""
        ).strip()
        or None,
        fallback_positions=[f for f in fallbacks if isinstance(f, dict)],
        position_type=str(entry.get("position_type") or meta.get("position_type") or "")
        or None,
    )


def build_playbook_hints_by_document(
    indexed_policies: list[dict[str, Any]] | None,
    *,
    registry_records: list[PolicyRegistryRecord] | None = None,
    policy_ref_by_document_id: dict[str, str] | None = None,
) -> dict[str, PlaybookHints]:
    """Map policy document_id → hints from discovery/index metadata."""
    hints: dict[str, PlaybookHints] = {}

    for entry in indexed_policies or []:
        if not isinstance(entry, dict):
            continue
        doc_id = str(entry.get("document_id") or "").strip()
        if not doc_id:
            continue
        hints[doc_id] = _hints_from_entry(entry)

    for record in registry_records or []:
        doc_id = str(record.document_id)
        existing = hints.get(doc_id, PlaybookHints())
        meta = record.metadata if isinstance(record.metadata, dict) else {}
        merged = PlaybookHints(
            policy_ref=existing.policy_ref or record.policy_ref,
            title=existing.title or record.title,
            review_guidance=existing.review_guidance
            or (str(meta.get("review_guidance") or "").strip() or None),
            preferred_position=existing.preferred_position
            or (str(meta.get("preferred_position") or "").strip() or None),
            fallback_positions=existing.fallback_positions
            or (meta.get("fallback_positions") if isinstance(meta.get("fallback_positions"), list) else []),
            position_type=existing.position_type
            or (str(meta.get("position_type") or "").strip() or None),
        )
        hints[doc_id] = merged

    for doc_id, policy_ref in (policy_ref_by_document_id or {}).items():
        doc_key = str(doc_id).strip()
        if not doc_key:
            continue
        existing = hints.get(doc_key, PlaybookHints())
        if not existing.policy_ref:
            hints[doc_key] = PlaybookHints(
                policy_ref=policy_ref,
                title=existing.title,
                review_guidance=existing.review_guidance,
                preferred_position=existing.preferred_position,
                fallback_positions=existing.fallback_positions,
                position_type=existing.position_type,
            )

    return hints


def hints_from_chunk_metadata(metadata: dict[str, Any] | None) -> PlaybookHints | None:
    if not isinstance(metadata, dict) or not metadata:
        return None
    hints = _hints_from_entry(metadata)
    return hints if hints.has_content() else None


def _trim_compare_text(text: str, compare_max_chars: int | None) -> str:
    if not compare_max_chars or compare_max_chars <= 0 or len(text) <= compare_max_chars:
        return text
    return text[:compare_max_chars] + "\n[truncated]"


def format_playbook_hint_block(
    hints: PlaybookHints | None,
    *,
    compare_max_chars: int | None = None,
) -> str:
    if hints is None or not hints.has_content():
        return ""
    lines: list[str] = ["  **Playbook hints:**"]
    if hints.policy_ref:
        lines.append(f"  - ref: {hints.policy_ref}")
    if hints.title:
        lines.append(f"  - title: {hints.title}")
    if hints.position_type:
        lines.append(f"  - position_type: {hints.position_type}")
    if hints.review_guidance:
        lines.append(f"  - guidance: {hints.review_guidance}")
    if hints.preferred_position:
        pos = _trim_compare_text(hints.preferred_position, compare_max_chars)
        lines.append(f"  - preferred_position:\n```\n{pos}\n```")
    for idx, fallback in enumerate(hints.fallback_positions, start=1):
        label = fallback.get("label") or f"fallback_{idx}"
        text = (fallback.get("text") or "").strip()
        if text:
            text = _trim_compare_text(text, compare_max_chars)
            lines.append(f"  - fallback ({label}):\n```\n{text}\n```")
    return "\n".join(lines)
