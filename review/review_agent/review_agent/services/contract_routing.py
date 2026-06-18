"""Contract routing: infer policy search topics from contract structure (Pass 1)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from document_core.schemas.chunk import DocumentKind, IndexedChunk, ListSectionsRequest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.contract_routing import ContractRoutingResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "contract_routing.md"
_HINTS_PATH = Path(__file__).resolve().parent.parent / "prompts" / "routing_topic_hints.yaml"

_DEFAULT_TOPICS = (
    "limitation of liability",
    "indemnification",
    "termination",
    "confidentiality",
)

_TOPIC_KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"liabilit", "limitation of liability"),
    (r"indemn", "indemnification"),
    (r"confidential", "confidentiality"),
    (r"terminat", "termination"),
    (r"\bip\b|intellectual property|ownership", "intellectual property"),
    (r"data\s+process|privacy|personal data|data protection", "data protection"),
    (r"governing law|jurisdiction", "governing law"),
    (r"warrant", "warranties"),
    (r"assign", "assignment"),
)


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("contract_routing.md must contain ## SYSTEM and ## USER sections")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def load_routing_topic_hints() -> list[str]:
    """Canonical search phrases for discovery recall (lexical index alignment)."""
    if _HINTS_PATH.is_file():
        data = yaml.safe_load(_HINTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data.get("topics") or []
            if isinstance(raw, list):
                return [str(item).strip() for item in raw if str(item).strip()]
    return list(_DEFAULT_TOPICS)


def _format_topic_hints_block(hints: list[str]) -> str:
    if not hints:
        return ""
    lines = "\n".join(f"- {hint}" for hint in hints)
    return f"### Topic vocabulary (prefer these search phrases when applicable)\n{lines}\n"


def _format_tenant_sections_block(titles: list[str]) -> str:
    if not titles:
        return ""
    lines = "\n".join(f"- {title}" for title in titles)
    return (
        "### Indexed playbook section titles in tenant "
        "(align topics with these headings when possible)\n"
        f"{lines}\n"
    )


async def fetch_tenant_section_titles(
    client: DocumentMCPClient,
    tenant_id: str,
    *,
    max_documents: int = 5,
    max_titles: int = 20,
) -> list[str]:
    """Read-only seed for routing — section titles from indexed tenant policies."""
    titles: list[str] = []
    seen: set[str] = set()
    try:
        doc_ids = await client.list_policies(tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("list_policies for routing seed failed: %s", exc)
        return []

    for doc_id in doc_ids[:max_documents]:
        try:
            sections = await client.list_sections(
                ListSectionsRequest(
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    kind=DocumentKind.POLICY,
                )
            )
        except Exception:  # noqa: BLE001
            continue
        for section in sections:
            title = (section.title or "").strip()
            if not title or len(title) < 3:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            titles.append(title)
            if len(titles) >= max_titles:
                return titles
    return titles


def _truncate(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    if "\n\n" in cut:
        cut = cut.rsplit("\n\n", 1)[0]
    elif " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "\n\n[... truncated for routing context ...]"


def build_routing_context(
    *,
    contract_text: str,
    contract_sections: list[IndexedChunk] | None,
    max_chars: int,
) -> str:
    """Build token-efficient routing context from sections or raw contract."""
    sections = contract_sections or []
    if sections:
        parts: list[str] = []
        for section in sections[:15]:
            title = (section.title or section.section_id).strip()
            snippet = _truncate(section.text.strip(), 400)
            parts.append(f"### {title}\n{snippet}")
        context = "\n\n".join(parts)
        return _truncate(context, max_chars)
    return _truncate(contract_text, max_chars)


def _topics_from_section_titles(titles: list[str], hints: list[str] | None = None) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    blob = " ".join(titles).lower()
    for pattern, phrase in _TOPIC_KEYWORDS:
        if re.search(pattern, blob, re.IGNORECASE):
            key = phrase.lower()
            if key not in seen:
                seen.add(key)
                topics.append(phrase)
    for title in titles:
        clean = title.strip()
        if not clean or len(clean) < 4:
            continue
        key = clean.lower()
        if key not in seen and len(clean) <= 80:
            seen.add(key)
            topics.append(clean)
    if not topics and hints:
        return hints[:10]
    return topics[:15] if topics else list(_DEFAULT_TOPICS)


def route_contract_lexical(
    *,
    contract_sections: list[IndexedChunk] | None,
    contract_text: str,
    contract_type_hint: str | None = None,
    topic_hints: list[str] | None = None,
) -> ContractRoutingResult:
    """Derive routing topics from section titles and keyword heuristics (no LLM)."""
    hints = topic_hints or load_routing_topic_hints()
    titles = [s.title for s in (contract_sections or []) if s.title.strip()]
    if not titles and contract_text.strip():
        for line in contract_text.splitlines():
            line = line.strip()
            if line and (line[0].isdigit() or re.match(r"^\d+\.", line)):
                titles.append(line[:120])
    topics = _topics_from_section_titles(titles, hints=hints)
    contract_type = (contract_type_hint or "unknown").strip().lower() or "unknown"
    return ContractRoutingResult(
        contract_type=contract_type,
        topics=topics,
        section_titles=titles[:50],
        confidence=None,
    )


async def route_contract(
    *,
    contract_text: str,
    contract_sections: list[IndexedChunk] | None = None,
    contract_type_hint: str | None = None,
    settings: ReviewSettings | None = None,
    client: DocumentMCPClient | None = None,
    tenant_id: str | None = None,
) -> tuple[ContractRoutingResult, list[str]]:
    """Route contract to policy search topics; LLM with lexical fail-open."""
    settings = settings or get_settings()
    warnings: list[str] = []
    topic_hints = load_routing_topic_hints()

    tenant_titles: list[str] = []
    if (
        client is not None
        and tenant_id
        and settings.review_policy_source == "tenant_auto"
    ):
        tenant_titles = await fetch_tenant_section_titles(client, tenant_id)

    if settings.contract_routing_mode == "lexical":
        return route_contract_lexical(
            contract_sections=contract_sections,
            contract_text=contract_text,
            contract_type_hint=contract_type_hint,
            topic_hints=topic_hints,
        ), warnings

    system_tpl, user_tpl = _load_prompt_template()
    context = build_routing_context(
        contract_text=contract_text,
        contract_sections=contract_sections,
        max_chars=settings.contract_routing_max_chars,
    )
    user = user_tpl.format(
        contract_type_hint=contract_type_hint or "",
        contract_context=context,
        topic_hints_block=_format_topic_hints_block(topic_hints),
        tenant_sections_block=_format_tenant_sections_block(tenant_titles),
    )

    last_error: Exception | None = None
    for attempt in range(1, settings.compliance_llm_max_retries + 2):
        try:
            model = get_review_model(
                temperature=settings.compliance_llm_temperature,
                max_tokens=settings.review_plan_llm_max_tokens,
            )
            result = await invoke_structured(
                model,
                ContractRoutingResult,
                system=system_tpl,
                user=user,
            )
            if contract_type_hint and result.contract_type == "unknown":
                result = result.model_copy(
                    update={"contract_type": contract_type_hint.strip().lower()}
                )
            return result, warnings
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("contract routing LLM attempt %s failed: %s", attempt, exc)

    warnings.append(
        f"Contract routing LLM failed ({last_error}); using lexical topic fallback."
    )
    return route_contract_lexical(
        contract_sections=contract_sections,
        contract_text=contract_text,
        contract_type_hint=contract_type_hint,
        topic_hints=topic_hints,
    ), warnings
