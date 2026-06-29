"""Tests for RC-11 Cisco golden score gates."""

from __future__ import annotations

import pytest

from beta_test.benchmark_score import specs_from_legacy_expected
from validate_p5_golden import (
    CISCO_EXPECTED,
    _assert_cisco_legal_score,
    _assert_section_compare_floors,
    _assert_wall_time_sanity,
)


def _cisco_findings_all_hit():
    specs = specs_from_legacy_expected(CISCO_EXPECTED)
    return {
        sid: {"status": next(iter(spec.expect_statuses)), "source": "playbook_compare"}
        for sid, spec in specs.items()
    }


def test_cisco_legal_score_passes_at_reference(monkeypatch):
    monkeypatch.setattr(
        "validate_p5_golden._findings_by_section",
        lambda _review: _cisco_findings_all_hit(),
    )
    _assert_cisco_legal_score("cisco", {})


def test_cisco_legal_score_fails_low_score(monkeypatch):
    monkeypatch.setattr(
        "validate_p5_golden._findings_by_section",
        lambda _review: {"1": {"status": "COMPLIANT", "source": "playbook_compare"}},
    )
    with pytest.raises(AssertionError, match="legal_score_10"):
        _assert_cisco_legal_score("cisco", {})


def test_section_compare_floors_fail_low_items():
    diagnosis = {"section_pipeline": {"compare_items": 8, "sections_compared": 8}}
    with pytest.raises(AssertionError, match="compare_items"):
        _assert_section_compare_floors("cisco", diagnosis)


def test_wall_time_sanity_fails_fast_low_nc():
    diagnosis = {}
    assessment = {"violation_count": 1}
    review = {"compliance_stats": {"review_wall_ms": 278_000}, "elapsed_seconds": 278}
    with pytest.raises(AssertionError, match="review_wall_ms"):
        _assert_wall_time_sanity("atlassian", diagnosis, assessment, review=review)
