"""Tests for PR-1 compare prompt calibration."""

from pathlib import Path

from review_agent.config import ReviewSettings
from review_agent.services.compare_prompt_loader import obligation_compare_prompt_path

_PROMPTS = Path(__file__).resolve().parent.parent / "review_agent" / "prompts"


def test_section_prompt_no_five_findings_contradiction():
    text = (_PROMPTS / "section_compare.md").read_text(encoding="utf-8")
    assert "up to 5 findings" not in text.lower()
    assert "at most 4 findings" in text.lower()


def test_section_prompt_requires_one_per_section():
    text = (_PROMPTS / "section_compare.md").read_text(encoding="utf-8")
    assert "at least one" in text.lower()
    assert "section_id" in text


def test_section_prompt_truncation_instruction():
    text = (_PROMPTS / "section_compare.md").read_text(encoding="utf-8")
    assert "[truncated]" in text


def test_section_prompt_softened_topic_mismatch():
    text = (_PROMPTS / "section_compare.md").read_text(encoding="utf-8")
    assert "Retrieval already scoped" in text
    assert "Partial overlap" in text


def test_obligation_prompt_v2_quote_downgrade_warning():
    text = (_PROMPTS / "obligation_compare_v2.md").read_text(encoding="utf-8")
    assert "downgrades to INCONCLUSIVE" in text
    assert "preferred_position" in text
    assert "adopts or references" in text


def test_obligation_prompt_v2_batch_coverage():
    text = (_PROMPTS / "obligation_compare_v2.md").read_text(encoding="utf-8")
    assert "up to 24" in text
    assert "every obligation_id" in text.lower()
    assert "POLICY_CONFLICT" in text


def test_obligation_prompt_v1_single_obligation():
    text = (_PROMPTS / "obligation_compare_v1.md").read_text(encoding="utf-8")
    assert "single contract obligation" in text.lower()
    assert "up to 24" not in text


def test_obligation_prompt_loader_default_v1():
    cfg = ReviewSettings(obligation_compare_prompt_v2_enabled=False)
    assert obligation_compare_prompt_path(cfg).name == "obligation_compare_v1.md"


def test_obligation_extract_prompt_field_rules():
    text = (_PROMPTS / "obligation_extract.md").read_text(encoding="utf-8")
    assert "verbatim substring" in text
    assert "do not use `general`" in text
    assert "## USER" not in text


def test_semantic_routing_planner_prompt_batch_coverage():
    text = (_PROMPTS / "semantic_routing_planner.md").read_text(encoding="utf-8")
    assert "every obligation_id" in text.lower() or "one plan per obligation_id" in text.lower()
    assert "explicit_policy_mentions" in text
    assert "[truncated]" in text
    assert "do not output document_id" in text.lower()


def test_compliance_review_archived():
    assert not (_PROMPTS / "compliance_review.md").exists()
    assert (_PROMPTS / "archive" / "compliance_review.md").exists()
