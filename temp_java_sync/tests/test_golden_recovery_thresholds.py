"""Tests for RC-07 golden recovery thresholds."""

from __future__ import annotations

import pytest

from validate_p5_golden import _assert_obligation_cap, _assert_recovery_thresholds


def test_obligation_cap_passes_within_ceiling():
    diagnosis = {
        "obligation_pipeline": {
            "extract_cap": {"post_cap_count": 80, "dropped_count": 56},
            "funnel": {"extracted": 80},
        }
    }
    _assert_obligation_cap("atlassian", diagnosis)


def test_obligation_cap_fails_uncapped_extract():
    diagnosis = {
        "obligation_pipeline": {
            "extract_cap": {"post_cap_count": 117, "dropped_count": 0},
            "funnel": {"extracted": 117},
        }
    }
    with pytest.raises(AssertionError, match="obligations_extracted 117"):
        _assert_obligation_cap("atlassian", diagnosis)


def test_recovery_thresholds_pass():
    diagnosis = {
        "accuracy_paths": {
            "recover": {"compare_omitted_recovered": 20, "gap_sections": 21},
        }
    }
    _assert_recovery_thresholds("atlassian", diagnosis)


def test_recovery_thresholds_fail_starved():
    diagnosis = {
        "accuracy_paths": {
            "recover": {"compare_omitted_recovered": 0, "gap_sections": 1},
        }
    }
    with pytest.raises(AssertionError, match="compare_omitted_recovered 0"):
        _assert_recovery_thresholds("atlassian", diagnosis)
