"""API Gateway — single entry point for client requests.

Architecture:
    Client → POST /query → QueryOrchestrator → AgentRegistry → Agent
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import logging
import time

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

from legal_ai_platform.container import PlatformContainer, get_container
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage platform container lifecycle."""
    container = get_container()
    app.state.container = container
    yield
    await container.shutdown()


app = FastAPI(
    title="Legal AI Platform",
    description="API Gateway for the Legal AI multi-agent system",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "legal-ai-platform", "version": "0.1.0"}


@app.post("/query", response_model=AgentResponse)
async def query(body: AgentRequest) -> AgentResponse:
    """Submit a legal query to the orchestrator."""
    container: PlatformContainer = app.state.container
    started = time.perf_counter()
    logger.info(
        "query received task_type=%s query_len=%d thread_id=%s",
        body.task_type,
        len(body.query),
        body.thread_id,
    )
    try:
        response = await container.orchestrator.handle(body)
        logger.info(
            "query completed success=%s awaiting_input=%s output_len=%d elapsed_s=%.1f",
            response.success,
            response.awaiting_input,
            len(response.output or ""),
            time.perf_counter() - started,
        )
        return response
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
