"""Tests for RC-09 golden section status floors."""

from __future__ import annotations

import pytest

from validate_p5_golden import _assert_section_status_floors


def test_section_floor_passes_when_nc():
    assessment = {
        "section_results": [
            {"section_id": "15", "status": "NON_COMPLIANT"},
            {"section_id": "19", "status": "NON_COMPLIANT"},
            {"section_id": "20.4", "status": "NON_COMPLIANT"},
        ]
    }
    diagnosis = {"section_pipeline": {"compare_items": 28}}
    _assert_section_status_floors("atlassian", assessment, diagnosis)


def test_section_floor_fails_on_ipc():
    assessment = {
        "section_results": [
            {"section_id": "15", "status": "INSUFFICIENT_POLICY_CONTEXT"},
        ]
    }
    diagnosis = {"section_pipeline": {"compare_items": 28}}
    with pytest.raises(AssertionError, match="section 15"):
        _assert_section_status_floors("atlassian", assessment, diagnosis)
