"""Parent-level policy catalog profiling at ingest (Phase R0)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from document_core.config import DocumentCoreSettings, get_settings
from document_core.llm.ingest_llm import invoke_structured_json, llm_api_key_available
from document_core.schemas.chunk import DocumentTree, SectionNode
from document_core.schemas.policy_catalog import PolicyCatalogProfile, PolicyProfilerLLMResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "policy_profiler.md"
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _parent_outline(nodes: list[SectionNode]) -> str:
    lines: list[str] = []
    for node in nodes:
        title = (node.title or node.section_id or "").strip()
        lines.append(f"- {node.section_id}: {title}")
    return "\n".join(lines) or "- (no sections)"


def _keyword_profile(*, title: str, canonical_text: str) -> PolicyProfilerLLMResult:
    tokens = _TOKEN_RE.findall(f"{title} {canonical_text[:2000]}".lower())
    seen: set[str] = set()
    topics: list[str] = []
    for token in tokens:
        if token in seen or len(topics) >= 8:
            continue
        seen.add(token)
        topics.append(token)
    return PolicyProfilerLLMResult(
        summary=title,
        topics=topics,
        keywords=topics[:12],
        aliases=[title] if title else [],
        obligation_types=[],
    )


def _split_prompt(raw: str) -> tuple[str, str]:
    parts = raw.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


def _finalize_profile(
    raw: PolicyProfilerLLMResult,
    *,
    title: str,
    profiler: str,
    catalog_version: int = 1,
) -> PolicyCatalogProfile:
    aliases = list(dict.fromkeys([title, *raw.aliases]))
    profile = PolicyCatalogProfile(
        summary=raw.summary.strip() or title,
        topics=[t.strip().lower() for t in raw.topics if str(t).strip()],
        keywords=[k.strip() for k in raw.keywords if str(k).strip()],
        aliases=[a.strip() for a in aliases if a.strip()],
        obligation_types=[o.strip().lower() for o in raw.obligation_types if str(o).strip()],
        catalog_version=catalog_version,
        profiler=profiler,  # type: ignore[arg-type]
        profiled_at=datetime.now(timezone.utc).isoformat(),
    )
    return profile.with_profile_text(title=title)


async def profile_policy_tree(
    tree: DocumentTree,
    *,
    document_title: str,
    settings: DocumentCoreSettings | None = None,
    catalog_version: int = 1,
) -> tuple[PolicyCatalogProfile, dict[str, object]]:
    """Build catalog profile for a parsed policy tree."""
    cfg = settings or get_settings()
    title = (document_title or tree.title or "Policy").strip() or "Policy"
    body_sample = (tree.canonical_text or "")[: cfg.policy_profiler_max_body_chars]
    outline = _parent_outline(tree.sections)

    mode = cfg.policy_profiler_mode
    use_llm = mode == "llm" or (mode == "auto" and llm_api_key_available())

    if use_llm and mode != "keyword":
        try:
            template = _PROMPT_PATH.read_text(encoding="utf-8")
            prompt = template.format(
                document_title=title,
                section_outline=outline,
                body_sample=body_sample,
            )
            system, user = _split_prompt(prompt)
            result = await invoke_structured_json(
                model=cfg.policy_profiler_model,
                system=system,
                user=user,
                schema=PolicyProfilerLLMResult,
                temperature=0.0,
            )
            profile = _finalize_profile(result, title=title, profiler="llm", catalog_version=catalog_version)
            return profile, {"profiler": "llm"}
        except Exception as exc:
            logger.warning("policy profiler LLM failed, using keyword fallback: %s", exc)

    raw = _keyword_profile(title=title, canonical_text=tree.canonical_text or "")
    profile = _finalize_profile(raw, title=title, profiler="keyword", catalog_version=catalog_version)
    return profile, {"profiler": "keyword"}
