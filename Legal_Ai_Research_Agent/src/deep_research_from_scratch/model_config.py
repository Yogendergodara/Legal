
"""Central LLM configuration (on-prem friendly).

All model creation in the project should go through ``get_chat_model`` so that
swapping in an on-prem / self-hosted LLM later requires **zero code changes** --
just environment variables.

Most on-prem servers (vLLM, Ollama, LM Studio, TGI, etc.) expose an
OpenAI-compatible API, which ``init_chat_model`` supports via ``base_url`` +
``api_key``. To point the whole project at your on-prem model:

    # .env
    LLM_BASE_URL=http://your-onprem-host:8000/v1
    LLM_API_KEY=dummy-or-real-key
    LLM_PROVIDER=openai          # most OpenAI-compatible servers
    LLM_MODEL=your-model-name    # e.g. llama-3.1-70b-instruct

You can also override a single role without touching the rest, e.g.
``LLM_MODEL_SUMMARIZER=your-small-model``. If no env vars are set, the original
cloud defaults (OpenAI/Anthropic) are used, so existing behavior is unchanged.

Per-role token limits are also supported:

    LLM_MAX_TOKENS_VERIFIER=1024   # cap output tokens for the verifier
    LLM_MAX_TOKENS_WRITER=4096     # cap output tokens for the writer
    LLM_MAX_TOKENS=2048            # global fallback cap for all roles

Role-specific values take precedence over the global ``LLM_MAX_TOKENS`` cap.
Explicit ``max_tokens`` kwargs passed to ``get_chat_model`` always win.

For models with a fixed context window (e.g. Nemotron 10k), also set:

    LLM_CONTEXT_LENGTH=10000       # total prompt + completion budget
    LLM_COMPLETION_BUFFER=128      # safety margin reserved from the window

``cap_max_tokens_for_prompt`` shrinks completion tokens at call time so
``input_tokens + max_tokens`` never exceeds ``LLM_CONTEXT_LENGTH``.
"""

import asyncio
import os
import random
import time
from collections.abc import Callable

from langchain.chat_models import init_chat_model

# Default model per logical role (used only when no env override is provided).
_ROLE_DEFAULTS = {
    "reasoning": "openai:gpt-4.1",
    "fast": "openai:gpt-4.1-mini",
    "summarizer": "openai:gpt-4.1-mini",
    "compress": "openai:gpt-4.1",
    "writer": "openai:gpt-4.1",
    "supervisor": "anthropic:claude-sonnet-4-20250514",
    "researcher": "anthropic:claude-sonnet-4-20250514",
    # Precise, low-temperature reviewer for the report verification gate.
    "verifier": "openai:gpt-4.1",
}


def _env(name: str, default=None):
    """Read an env var, treating empty strings as unset."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _context_length() -> int | None:
    """Total model context window, when configured."""
    value = _env("LLM_CONTEXT_LENGTH")
    return int(value) if value else None


def _completion_buffer() -> int:
    """Tokens reserved so prompt + completion stays inside the context window."""
    value = _env("LLM_COMPLETION_BUFFER")
    return int(value) if value else 128


def estimate_token_count(text: str) -> int:
    """Conservatively estimate how many tokens a prompt will consume."""
    if not text:
        return 0

    estimates: list[int] = [len(text) // 3 + 1]
    try:
        import tiktoken

        estimates.append(len(tiktoken.get_encoding("cl100k_base").encode(text)))
    except Exception:
        pass

    # Overestimate input so we under-allocate completion tokens and avoid 400s.
    return max(estimates)


def resolve_max_tokens(role: str, explicit_max_tokens: int | None = None) -> int:
    """Resolve the completion cap for a role (mirrors ``get_chat_model`` logic)."""
    requested = explicit_max_tokens
    if requested is None:
        role_cap = _env(f"LLM_MAX_TOKENS_{role.upper()}")
        requested = int(role_cap) if role_cap else int(_env("LLM_MAX_TOKENS") or 4096)

    global_cap = _env("LLM_MAX_TOKENS")
    if global_cap:
        requested = min(requested, int(global_cap))
    return requested


def _min_writer_completion_tokens() -> int:
    value = _env("LLM_MIN_WRITER_COMPLETION_TOKENS")
    return int(value) if value else 2048


def cap_max_tokens_for_prompt(
    prompt: str,
    *,
    role: str = "reasoning",
    requested_max_tokens: int | None = None,
) -> int | None:
    """Cap completion tokens so the prompt fits inside the model context window.

    Returns ``None`` when ``LLM_CONTEXT_LENGTH`` is unset (no dynamic cap).
    """
    context_length = _context_length()
    if context_length is None:
        return None

    requested = requested_max_tokens
    if requested is None:
        requested = resolve_max_tokens(role)

    input_tokens = estimate_token_count(prompt)
    available = context_length - input_tokens - _completion_buffer()
    if available < 1:
        return 1

    return min(requested, available)


def fit_writer_prompt(
    prompt: str,
    *,
    findings: str,
    trim_findings: Callable[[str, int], str],
    requested_max_tokens: int | None = None,
) -> tuple[str, int | None]:
    """Shrink findings until the writer has room for a full memorandum.

    Prevents ``cap_max_tokens_for_prompt`` from collapsing completion to a
    handful of tokens when findings + template exceed the context window.
    """
    min_completion = _min_writer_completion_tokens()
    char_budget = len(findings)
    floor = min(4000, char_budget)

    while char_budget >= floor:
        trimmed = trim_findings(findings, char_budget)
        candidate = prompt.replace(findings, trimmed, 1)
        capped = cap_max_tokens_for_prompt(
            candidate,
            role="writer",
            requested_max_tokens=requested_max_tokens,
        )
        if capped is None or capped >= min_completion:
            return candidate, capped
        char_budget = max(floor, char_budget // 2)

    trimmed = trim_findings(findings, floor)
    candidate = prompt.replace(findings, trimmed, 1)
    return candidate, cap_max_tokens_for_prompt(
        candidate,
        role="writer",
        requested_max_tokens=requested_max_tokens,
    )


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when an LLM provider rejected the call due to rate limiting."""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limited" in msg


def _rate_limit_retries() -> int:
    return int(_env("LLM_RATE_LIMIT_RETRIES") or 6)


def _rate_limit_base_delay() -> float:
    return float(_env("LLM_RATE_LIMIT_BASE_DELAY") or 5.0)


def _rate_limit_max_wait() -> float:
    """Cap total time spent sleeping on 429 retries."""
    return float(_env("LLM_RATE_LIMIT_MAX_WAIT") or 45.0)


async def ainvoke_with_retry(runnable, input, *, max_retries: int | None = None):
    """Invoke an async runnable, retrying with backoff on HTTP 429 rate limits."""
    retries = max_retries if max_retries is not None else _rate_limit_retries()
    delay = _rate_limit_base_delay()
    max_wait = _rate_limit_max_wait()
    started = time.perf_counter()
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            return await runnable.ainvoke(input)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_rate_limit_error(exc) or attempt >= retries:
                raise
            elapsed = time.perf_counter() - started
            remaining = max_wait - elapsed
            if remaining <= 0:
                raise
            wait = min(delay * (2**attempt) + random.uniform(0, 1.5), remaining)
            await asyncio.sleep(wait)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("ainvoke_with_retry failed without an exception")


def invoke_with_retry(runnable, input, *, max_retries: int | None = None):
    """Invoke a sync runnable, retrying with backoff on HTTP 429 rate limits."""
    retries = max_retries if max_retries is not None else _rate_limit_retries()
    delay = _rate_limit_base_delay()
    max_wait = _rate_limit_max_wait()
    started = time.perf_counter()
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            return runnable.invoke(input)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_rate_limit_error(exc) or attempt >= retries:
                raise
            elapsed = time.perf_counter() - started
            remaining = max_wait - elapsed
            if remaining <= 0:
                raise
            wait = min(delay * (2**attempt) + random.uniform(0, 1.5), remaining)
            time.sleep(wait)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("invoke_with_retry failed without an exception")


def _thinking_disabled() -> bool:
    """Whether to disable chain-of-thought / thinking on the LLM server."""
    env = _env("LLM_ENABLE_THINKING")
    if env is not None:
        return env.lower() not in ("true", "1", "yes")
    # vLLM reasoning models (e.g. Nemotron) return null content and are very slow
    # when thinking is left on during tool-calling agent loops.
    return _env("LLM_BASE_URL") is not None


def _apply_onprem_model_kwargs(kwargs: dict, base_url: str | None) -> None:
    """Apply defaults for self-hosted OpenAI-compatible servers."""
    if not base_url:
        return

    max_tokens_cap = _env("LLM_MAX_TOKENS")
    if max_tokens_cap and "max_tokens" not in kwargs:
        kwargs["max_tokens"] = int(max_tokens_cap)

    if _thinking_disabled():
        extra_body = dict(kwargs.get("extra_body") or {})
        chat_template_kwargs = dict(extra_body.get("chat_template_kwargs") or {})
        chat_template_kwargs.setdefault("enable_thinking", False)
        extra_body["chat_template_kwargs"] = chat_template_kwargs
        kwargs["extra_body"] = extra_body


def resolve_model_name(role: str) -> str:
    """Resolve the model id for a role, honoring env overrides.

    Precedence: ``LLM_MODEL_<ROLE>`` > ``LLM_MODEL`` (global) > built-in default.
    """
    return (
        _env(f"LLM_MODEL_{role.upper()}")
        or _env("LLM_MODEL")
        or _ROLE_DEFAULTS.get(role, _ROLE_DEFAULTS["reasoning"])
    )


def _split_model_provider(model: str) -> tuple[str, str | None]:
    """Split ``provider:model`` into bare model id and provider slug."""
    if ":" not in model:
        return model, None
    prefix, bare = model.split(":", 1)
    return bare, prefix


def _normalize_model_for_api(
    model: str,
    provider: str | None,
    base_url: str | None,
) -> tuple[str, str | None]:
    """Return the model id and provider to pass to ``init_chat_model``.

    Cloud APIs (Mistral, etc.) expect bare model names like
    ``mistral-small-latest``, not ``mistral:mistral-small-latest``.
    """
    bare, prefix = _split_model_provider(model)
    effective_provider = provider or prefix

    if base_url:
        # OpenAI-compatible self-hosted servers use the bare model name.
        return bare if prefix else model, effective_provider

    if prefix:
        # LangChain provider slug for Mistral is ``mistralai``.
        if effective_provider in ("mistral", "mistralai"):
            effective_provider = "mistralai"
        return bare, effective_provider

    return model, effective_provider


def get_chat_model(role: str = "reasoning", **overrides):
    """Create a chat model for a logical role, configured for cloud or on-prem.

    Args:
        role: Logical role -- one of the keys in ``_ROLE_DEFAULTS`` (e.g.
            ``"reasoning"``, ``"summarizer"``, ``"compress"``, ``"supervisor"``).
        **overrides: Extra kwargs passed straight to ``init_chat_model``
            (e.g. ``temperature=0.0``, ``max_tokens=32000``).

    Returns:
        A LangChain chat model instance.
    """
    model = resolve_model_name(role)

    kwargs: dict = {}

    base_url = _env("LLM_BASE_URL")
    api_key = _env("LLM_API_KEY") or _env("MISTRAL_API_KEY")
    provider = _env("LLM_PROVIDER")

    model, provider = _normalize_model_for_api(model, provider, base_url)

    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    # When the model id has no "provider:" prefix, we must tell init_chat_model
    # which provider class to use. On-prem OpenAI-compatible servers use "openai".
    if provider:
        effective_provider = provider
        if base_url and effective_provider == "nvidia":
            effective_provider = "openai"
        kwargs["model_provider"] = effective_provider
    elif ":" not in resolve_model_name(role):
        kwargs["model_provider"] = "openai"

    # Role-specific max_tokens (e.g. LLM_MAX_TOKENS_VERIFIER=1024).
    # Applied before call-site overrides so explicit kwargs still win.
    role_max_tokens = _env(f"LLM_MAX_TOKENS_{role.upper()}")
    if role_max_tokens and "max_tokens" not in kwargs:
        kwargs["max_tokens"] = int(role_max_tokens)

    # Per-call overrides (temperature, max_tokens, ...) win over everything.
    kwargs.update(overrides)

    _apply_onprem_model_kwargs(kwargs, base_url)

    max_tokens_cap = _env("LLM_MAX_TOKENS")
    if max_tokens_cap and "max_tokens" in kwargs:
        kwargs["max_tokens"] = min(int(kwargs["max_tokens"]), int(max_tokens_cap))

    return init_chat_model(model=model, **kwargs)


# Default embedding model (used only when no env override is provided).
_EMBEDDING_DEFAULT = "openai:text-embedding-3-small"


def get_embeddings(**overrides):
    """Create the embedding model used by vector memory backends (pgvector/Qdrant).

    On-prem friendly, mirroring ``get_chat_model``: point it at a self-hosted,
    OpenAI-compatible embeddings endpoint with environment variables and change
    no code.

        # .env
        EMBEDDING_MODEL=bge-large-en        # or text-embedding-3-small, etc.
        EMBEDDING_BASE_URL=http://your-onprem-host:8000/v1
        EMBEDDING_API_KEY=dummy-or-real-key
        EMBEDDING_PROVIDER=openai

    Falls back to ``LLM_BASE_URL`` / ``LLM_API_KEY`` if the embedding-specific
    ones are not set, so a single on-prem endpoint can serve both.

    Returns:
        A LangChain embeddings instance.
    """
    # Imported lazily so the project loads even when langchain-openai's embedding
    # extras are not installed yet (vector backend is optional today).
    from langchain.embeddings import init_embeddings

    model = _env("EMBEDDING_MODEL") or _EMBEDDING_DEFAULT

    kwargs: dict = {}
    base_url = _env("EMBEDDING_BASE_URL") or _env("LLM_BASE_URL")
    api_key = _env("EMBEDDING_API_KEY") or _env("LLM_API_KEY")
    provider = _env("EMBEDDING_PROVIDER")

    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if ":" not in model:
        kwargs["provider"] = provider or "openai"

    kwargs.update(overrides)

    return init_embeddings(model, **kwargs)
