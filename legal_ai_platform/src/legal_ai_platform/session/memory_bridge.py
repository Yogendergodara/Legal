"""Platform-owned long-term memory via retrieval-mcp (MEMORY.md)."""

from __future__ import annotations

from typing import Any, Protocol

from legal_ai_platform.session.memory_postgres import PostgresMemoryStore
from legal_ai_platform.session.models import MatterSnapshot


class MemorySearchClient(Protocol):
    """Minimal MCP client surface used by MemoryBridge."""

    async def search_memory(self, query: str) -> list[dict[str, Any]]: ...

    async def save_memory(
        self, title: str, content: str, hook: str = ""
    ) -> dict[str, Any]: ...


def format_memory_hits(results: list[dict[str, Any]]) -> str:
    """Turn MCP memory search hits into injectable text for agents."""
    if not results:
        return ""
    parts: list[str] = []
    for hit in results:
        name = hit.get("name", "memory")
        content = hit.get("content", "")
        if content:
            parts.append(f"--- {name} ---\n{content}")
    if not parts:
        return ""
    return "Prior legal memories (long-term):\n\n" + "\n\n".join(parts)


def build_memory_hook(*, agent: str, tenant_id: str, thread_id: str, detail: str) -> str:
    """Standard hook line for MEMORY.md index (searchable tags)."""
    return f"[{agent}][{tenant_id}][{thread_id}] {detail}"


def build_search_queries(
    *,
    query: str,
    tenant_id: str,
    task_type: str,
    matter: MatterSnapshot,
) -> list[str]:
    """Queries to prefetch relevant long-term memories for this turn."""
    terms: list[str] = []
    q = (query or "").strip()
    if q:
        terms.append(q)
    terms.append(f"compliance tenant {tenant_id}")
    if task_type == "review":
        title = matter.contract_title or "Contract"
        terms.append(f"review {title}")
        if matter.contract_type:
            terms.append(f"review {matter.contract_type} policy compliance")
    # Preserve order, drop duplicates
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def build_review_memory_payload(
    report: dict[str, Any],
    *,
    tenant_id: str,
    thread_id: str,
    contract_title: str,
) -> tuple[str, str, str] | None:
    """Build durable review memory (findings summary, not full chat log)."""
    findings = report.get("findings") or []
    if not findings:
        return None

    critical = sum(1 for f in findings if f.get("severity") == "critical")
    non_compliant = [
        f for f in findings if f.get("status") == "NON_COMPLIANT"
    ]
    structure = report.get("structure_confidence", "high")
    title = f"Review: {contract_title} [{tenant_id}]"
    hook = build_memory_hook(
        agent="review",
        tenant_id=tenant_id,
        thread_id=thread_id,
        detail=f"{len(findings)} findings ({critical} critical); structure={structure}",
    )

    lines = [
        f"# Review: {contract_title}",
        "",
        f"Tenant: {tenant_id} | Thread: {thread_id}",
        f"Findings: {len(findings)} ({critical} critical, {len(non_compliant)} non-compliant)",
        "",
    ]
    for finding in findings[:12]:
        status = finding.get("status", "UNKNOWN")
        severity = finding.get("severity", "info")
        label = finding.get("dimension_label", finding.get("dimension_id", "finding"))
        rationale = (finding.get("rationale") or "").strip()
        contract_quote = (finding.get("contract_quote") or "").strip()
        lines.append(f"## {label} [{severity}] — {status}")
        if contract_quote:
            lines.append(f"Contract: {contract_quote[:400]}")
        if rationale:
            lines.append(rationale[:500])
        lines.append("")

    return title, "\n".join(lines).strip(), hook


class MemoryBridge:
    """Single platform entry point for retrieval-mcp long-term memory."""

    def __init__(
        self,
        client: MemorySearchClient | None = None,
        *,
        postgres_store: PostgresMemoryStore | None = None,
        max_hits: int = 5,
    ) -> None:
        self._client = client
        self._postgres_store = postgres_store
        self._max_hits = max_hits

    async def search(
        self,
        *,
        query: str,
        tenant_id: str,
        task_type: str,
        matter: MatterSnapshot,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Search MCP memory; return formatted snippets and raw hits."""
        seen: set[str] = set()
        hits: list[dict[str, Any]] = []
        queries = build_search_queries(
            query=query,
            tenant_id=tenant_id,
            task_type=task_type,
            matter=matter,
        )
        if self._postgres_store is not None:
            batch = self._postgres_store.search(tenant_id, queries, limit=self._max_hits)
            for item in batch:
                key = item.get("name") or (item.get("content", "")[:80])
                if key in seen:
                    continue
                seen.add(key)
                hits.append(item)
        elif self._client is not None:
            for q in queries:
                try:
                    batch = await self._client.search_memory(q)
                except Exception:  # noqa: BLE001
                    continue
                for item in batch:
                    key = item.get("name") or (item.get("content", "")[:80])
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(item)

        trimmed = hits[: self._max_hits]
        return format_memory_hits(trimmed), trimmed

    async def save_review_report(
        self,
        report: dict[str, Any],
        *,
        tenant_id: str,
        thread_id: str,
        contract_title: str,
    ) -> dict[str, Any] | None:
        """Persist durable review facts after a successful review turn."""
        payload = build_review_memory_payload(
            report,
            tenant_id=tenant_id,
            thread_id=thread_id,
            contract_title=contract_title,
        )
        if payload is None:
            return None
        title, body, hook = payload
        try:
            if self._postgres_store is not None:
                result = self._postgres_store.save(
                    title=title,
                    content=body,
                    hook=hook,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    agent="review",
                )
            elif self._client is not None:
                result = await self._client.save_memory(title, body, hook)
            else:
                return None
            return {
                "memory_saved": True,
                "memory_save_message": result.get("message", "saved"),
                "memory_title": title,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "memory_saved": False,
                "memory_save_error": str(exc),
            }
