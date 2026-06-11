"""Integration tests for FastAPI endpoints (no real network)."""



from __future__ import annotations



from unittest.mock import AsyncMock, MagicMock, patch



import pytest

from fastapi.testclient import TestClient



from mcp.retrieval_server.main import app

from mcp.retrieval_server.models import SearchResult





def _fake_result(source_id: str, score: float, source_type: str) -> SearchResult:

    return SearchResult(

        source_id=source_id,

        source_type=source_type,  # type: ignore[arg-type]

        title=f"Result {source_id}",

        text_snippet="Snippet text here.",

        url=f"https://example.com/{source_id}",

        jurisdiction="India",

        relevance_score=score,

        metadata={},

    )





@pytest.fixture

def client() -> TestClient:

    with TestClient(app) as test_client:

        yield test_client





class TestHealth:

    def test_health_returns_200(self, client: TestClient) -> None:

        response = client.get("/health")

        assert response.status_code == 200

        data = response.json()

        assert data["status"] == "ok"

        assert data["service"] == "retrieval-mcp"

        assert data["version"] == "0.1.0"

        assert "timestamp" in data





class TestSearchEndpoint:

    @patch("mcp.retrieval_server.integrations.web_search.WebSearchClient.search")

    def test_search_all_returns_documented_shape(

        self,

        mock_web: AsyncMock,

        client: TestClient,

    ) -> None:

        mock_web.return_value = (

            [

                {

                    "url": "https://example.com/article",

                    "title": "Web Article",

                    "snippet": "A web snippet.",

                    "score": 0.7,

                    "engine": "duckduckgo",

                }

            ],

            False,

        )



        response = client.post(

            "/tools/search",

            json={

                "query": "non-compete enforceable",

                "search_type": "all",

                "max_results": 10,

            },

        )



        assert response.status_code == 200

        data = response.json()



        assert "request_id" in data

        assert data["query"] == "non-compete enforceable"

        assert data["search_type"] == "all"

        assert "results" in data

        assert "total_results" in data

        assert "degraded" in data

        assert "search_time_ms" in data

        assert data["degraded"] is False

        assert data["total_results"] == 1



        result = data["results"][0]

        assert "source_id" in result

        assert "source_type" in result

        assert "title" in result

        assert "text_snippet" in result

        assert "url" in result

        assert "jurisdiction" in result

        assert "relevance_score" in result

        assert "metadata" in result



    @patch("mcp.retrieval_server.integrations.web_search.WebSearchClient.search")

    def test_search_degraded_when_source_raises(

        self,

        mock_web: AsyncMock,

        client: TestClient,

    ) -> None:

        mock_web.return_value = ([], True)



        response = client.post(

            "/tools/search",

            json={

                "query": "test query",

                "search_type": "all",

            },

        )



        assert response.status_code == 200

        data = response.json()

        assert data["degraded"] is True

        assert data["total_results"] == 0



    @patch("mcp.retrieval_server.integrations.web_search.WebSearchClient.search")

    def test_web_search_timeout_degraded(

        self,

        mock_web: AsyncMock,

        client: TestClient,

    ) -> None:

        mock_web.return_value = ([], True)



        response = client.post(

            "/tools/search",

            json={

                "query": "contract law",

                "search_type": "web",

            },

        )



        assert response.status_code == 200

        data = response.json()

        assert data["degraded"] is True

        assert data["total_results"] == 0





class TestPhase2Endpoints:

    @patch("mcp.retrieval_server.semantic_service.semantic_search_web", new_callable=AsyncMock)
    @patch("mcp.retrieval_server.semantic_service.embed_text", new_callable=AsyncMock)
    def test_semantic_search_returns_results(
        self,
        mock_embed: AsyncMock,
        mock_search: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_embed.return_value = [0.1] * 384
        mock_search.return_value = [
            {
                "source_id": "https://example.com",
                "source_type": "web",
                "title": "Case",
                "text_snippet": "text",
                "similarity_score": 0.8,
            }
        ]

        response = client.post(
            "/tools/semantic_search",
            json={"query": "contract breach remedies", "threshold": 0.5},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["stub"] is False
        assert data["total_results"] == 1
        assert "request_id" in data

    @patch("mcp.retrieval_server.citation_service.CitationService._get_edges")
    def test_citation_graph_returns_graph(
        self,
        mock_edges,
        client: TestClient,
    ) -> None:
        edge = MagicMock()
        edge.from_source_id = "https://example.com/a"
        edge.to_source_id = "https://example.com/b"
        edge.to_source_type = "web"
        edge.citation_type = "cites"
        mock_edges.return_value = ([edge], [])

        response = client.post(
            "/tools/citation_graph",
            json={
                "source_id": "https://example.com/a",
                "source_type": "web",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["stub"] is False
        assert len(data["nodes"]) >= 1
        assert "request_id" in data



    def test_internal_search_without_tenant_id_returns_empty(

        self, client: TestClient

    ) -> None:

        response = client.post(

            "/tools/search",

            json={

                "query": "internal policy",

                "search_type": "internal",

            },

        )



        assert response.status_code == 200

        data = response.json()

        assert data["total_results"] == 0

        assert data["results"] == []


