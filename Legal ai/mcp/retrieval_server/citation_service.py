"""Citation graph traversal over stored citation edges."""

from __future__ import annotations

import time
from collections import deque

from db.models import CitationEdge as CitationEdgeModel
from db.session import get_session
from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.logging_setup import get_logger
from mcp.retrieval_server.models import (
    CitationEdge,
    CitationGraphRequest,
    CitationGraphResponse,
    CitationNode,
)

logger = get_logger(__name__)


class CitationService:
    """BFS citation graph traversal over stored citation edges."""

    def __init__(self, settings: Settings | None = None, http_client=None) -> None:
        from mcp.retrieval_server.config import get_settings

        self._settings = settings or get_settings()
        self._http_client = http_client

    async def citation_graph(
        self, request: CitationGraphRequest, request_id: str
    ) -> CitationGraphResponse:
        start = time.perf_counter()

        logger.info(
            "citation graph traversal started",
            request_id=request_id,
            source_id=request.source_id,
            depth=request.depth,
            direction=request.direction,
        )

        try:
            nodes: dict[str, CitationNode] = {}
            edges: list[CitationEdge] = []
            seen_edges: set[tuple[str, str, str]] = set()
            visited: set[str] = set()
            queue: deque[tuple[str, int]] = deque([(request.source_id, 0)])

            nodes[request.source_id] = CitationNode(
                source_id=request.source_id,
                source_type=request.source_type,
                title=request.source_id,
                url=self._source_url(request.source_id),
            )

            while queue:
                current_id, depth = queue.popleft()
                if current_id in visited or depth > request.depth:
                    continue
                visited.add(current_id)

                outgoing, incoming = self._get_edges(current_id, request.direction)

                for edge_row in outgoing + incoming:
                    edge_key = (
                        edge_row.from_source_id,
                        edge_row.to_source_id,
                        edge_row.citation_type,
                    )
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append(
                            CitationEdge(
                                from_id=edge_row.from_source_id,
                                to_id=edge_row.to_source_id,
                                citation_type=edge_row.citation_type,
                            )
                        )

                    neighbor = (
                        edge_row.to_source_id
                        if edge_row.from_source_id == current_id
                        else edge_row.from_source_id
                    )
                    if neighbor not in nodes:
                        nodes[neighbor] = CitationNode(
                            source_id=neighbor,
                            source_type=edge_row.to_source_type,
                            title=neighbor,
                            url=self._source_url(neighbor),
                        )
                    if depth < request.depth and neighbor not in visited:
                        queue.append((neighbor, depth + 1))

            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "citation graph traversal complete",
                request_id=request_id,
                nodes=len(nodes),
                edges=len(edges),
                depth=request.depth,
                duration_ms=elapsed_ms,
            )

            return CitationGraphResponse(
                request_id=request_id,
                source_id=request.source_id,
                nodes=list(nodes.values()),
                edges=edges,
                depth=request.depth,
                direction=request.direction,
                stub=False,
                graph_time_ms=elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "citation graph failed",
                request_id=request_id,
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=elapsed_ms,
                exc_info=True,
            )
            return CitationGraphResponse(
                request_id=request_id,
                source_id=request.source_id,
                nodes=[],
                edges=[],
                depth=request.depth,
                direction=request.direction,
                stub=True,
                stub_reason=f"{type(exc).__name__}: {exc}",
                graph_time_ms=elapsed_ms,
            )

    def _get_edges(self, source_id: str, direction: str) -> tuple[list, list]:
        outgoing: list = []
        incoming: list = []
        with get_session(self._settings.database_url) as session:
            if direction in ("outgoing", "both"):
                outgoing = session.query(CitationEdgeModel).filter(
                    CitationEdgeModel.from_source_id == source_id
                ).all()
            if direction in ("incoming", "both"):
                incoming = session.query(CitationEdgeModel).filter(
                    CitationEdgeModel.to_source_id == source_id
                ).all()
        return outgoing, incoming

    @staticmethod
    def _source_url(source_id: str) -> str:
        if source_id.startswith("http://") or source_id.startswith("https://"):
            return source_id
        return ""
