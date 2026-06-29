"""Tests for RC-10 obligation extract structure recovery under HOT."""

from __future__ import annotations

from unittest.mock import patch

from review_agent.resilience.failure_policy import (
    ReviewPosture,
    should_batch_single_retry,
)


def test_obligation_extract_structure_retry_exempt_from_hot_cap():
    exc = ValueError("Expecting value: line 1 column 1 (char 0)")
    with (
        patch(
            "review_agent.resilience.failure_policy.get_current_review_posture",
            return_value=ReviewPosture.HOT,
        ),
        patch(
            "review_agent.resilience.failure_policy.get_hot_structure_splits",
            return_value=99,
        ),
    ):
        assert should_batch_single_retry(
            exc,
            batch_len=6,
            batch_retry_enabled=True,
            posture_enabled=True,
            stage="obligation_extract",
        )
        assert should_batch_single_retry(
            exc,
            batch_len=6,
            batch_retry_enabled=True,
            posture_enabled=True,
            stage="section_compare",
        )


def test_default_stage_respects_hot_structure_cap():
    exc = ValueError("json parse error")
    with (
        patch(
            "review_agent.resilience.failure_policy.get_current_review_posture",
            return_value=ReviewPosture.HOT,
        ),
        patch(
            "review_agent.resilience.failure_policy.get_hot_structure_splits",
            return_value=99,
        ),
    ):
        assert not should_batch_single_retry(
            exc,
            batch_len=6,
            batch_retry_enabled=True,
            posture_enabled=True,
        )
