"""Task classification for routing queries to the correct agent."""

from __future__ import annotations

import re


class TaskClassifier:
    """Classify user queries into task types.

    Current implementation uses keyword rules. Structured for future LLM-based
    classification without changing the orchestrator interface.
    """

    _RULES: list[tuple[str, re.Pattern[str]]] = [
        ("contract", re.compile(r"\b(contract|agreement|clause|NDA|MSA)\b", re.I)),
        ("drafting", re.compile(r"\b(draft|write|prepare|generate)\b.*\b(notice|petition|letter|memo)\b", re.I)),
        ("summary", re.compile(r"\b(summarize|summary|summarise)\b", re.I)),
        ("litigation", re.compile(r"\b(litigation|lawsuit|comparable cases|risk)\b", re.I)),
        ("compliance", re.compile(r"\b(compliance|regulatory|regulation)\b", re.I)),
        ("property", re.compile(r"\b(property|real estate|land|lease)\b", re.I)),
        ("ip", re.compile(r"\b(patent|trademark|copyright|intellectual property|IP)\b", re.I)),
        ("translation", re.compile(r"\b(translate|translation)\b", re.I)),
    ]

    DEFAULT_TASK_TYPE = "research"

    def classify(self, query: str, explicit_task_type: str | None = None) -> str:
        """Return the task type for a query.

        If ``explicit_task_type`` is provided it takes precedence.
        """
        if explicit_task_type:
            return explicit_task_type

        for task_type, pattern in self._RULES:
            if pattern.search(query):
                return task_type

        return self.DEFAULT_TASK_TYPE
