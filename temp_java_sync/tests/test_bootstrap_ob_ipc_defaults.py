"""Tests for OB IPC recovery bootstrap defaults."""

from __future__ import annotations

import os

from bootstrap_env import apply_golden_review_defaults, apply_ob_ipc_recovery_defaults


def test_apply_ob_ipc_recovery_defaults(monkeypatch):
    monkeypatch.delenv("OB_IPC_RECOVERY_OPT_OUT", raising=False)
    monkeypatch.delenv("OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS", raising=False)
    monkeypatch.delenv("EVIDENCE_MIN_CONCEPT_OVERLAP", raising=False)
    monkeypatch.delenv("ROUTING_COMPARE_MIN_CONFIDENCE", raising=False)
    apply_ob_ipc_recovery_defaults()
    assert os.environ["OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS"] == "false"
    assert os.environ["EVIDENCE_MIN_CONCEPT_OVERLAP"] == "0.15"
    assert os.environ["ROUTING_COMPARE_MIN_CONFIDENCE"] == "0.75"


def test_golden_review_defaults_includes_ob_ipc(monkeypatch):
    monkeypatch.delenv("OB_IPC_RECOVERY_OPT_OUT", raising=False)
    monkeypatch.delenv("OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS", raising=False)
    apply_golden_review_defaults()
    assert os.environ.get("OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS") == "false"
