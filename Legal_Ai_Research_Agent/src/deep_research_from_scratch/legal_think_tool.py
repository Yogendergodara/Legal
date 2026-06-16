
"""Legal Think Tool - domain-specific reasoning scratchpad for Indian legal research.

This replaces the generic ``think_tool``. It is a deliberate-pause mechanism: it
performs NO external action (no search, no database, no file access) and simply
records the model's structured legal reasoning so it can check its work and plan
the next step between tool calls.

Per the think-tool design guide, the tool implementation is intentionally
trivial - the power comes from this description plus the domain-specific
guidance and examples in the system prompts (see prompts.py).

The exported tool keeps the name ``think_tool`` so it slots directly into the
existing agent tool lists and prompt references.
"""

from langchain_core.tools import tool


@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Structured legal reasoning scratchpad for Indian legal research.

    This tool performs NO external action - it does not search, query a database,
    or read files. It only records your structured legal reasoning so you can
    pause, verify your work, and decide the next step. Use it between searches
    and before reaching any legal conclusion.

    WHEN TO USE (treat as mandatory):
    - After each web_search: assess what authority you actually found and what is still missing.
    - Before concluding an issue: confirm you have BOTH the governing statute (exact section/article) AND the controlling precedent (case + citation + ratio).
    - When authorities conflict: reason through the hierarchy under Article 141 (Supreme Court binds all; a High Court binds only its own state; per incuriam decisions do not bind; larger/later bench prevails).
    - Before deciding which law applies in time: old codes (IPC/CrPC/Indian Evidence Act) for offences before 1 July 2024, vs new codes (BNS/BNSS/BSA) on or after that date.

    STRUCTURE YOUR REFLECTION (IRAC-aware):
    - ISSUE: the precise question of law being analysed.
    - RULE: the statute and precedent found - cite ONLY authority actually retrieved this session; NEVER invent a case, citation, or section from memory.
    - APPLICATION: how the rule applies to the facts; relevant analogies or distinctions.
    - TREATMENT / CONFLICT: is each authority still good law, overruled, distinguished, or per incuriam? If they conflict, which one binds and why?
    - GAPS: which statute or precedent is still missing or NOT FOUND.
    - NEXT STEP: search again to fill a gap, or conclude this issue.

    Args:
        reflection: Your structured legal reasoning. Be specific, cite only
            retrieved authority, and never fabricate case law or citations.

    Returns:
        Confirmation that the legal reflection was recorded.
    """
    return f"Legal reflection recorded: {reflection}"
