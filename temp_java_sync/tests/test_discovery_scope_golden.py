"""Tests for RC-04 discovery scope golden gates."""

from __future__ import annotations

import pytest

from validate_p5_golden import _assert_discovery_scope


def test_discovery_scope_passes_within_ceiling():
    diagnosis = {"discovery": {"policies_discovered": 9}}
    _assert_discovery_scope("atlassian", diagnosis)


def test_discovery_scope_fails_when_polluted():
    diagnosis = {"discovery": {"policies_discovered": 29}}
    with pytest.raises(AssertionError, match="policies_discovered 29 > ceiling 10"):
        _assert_discovery_scope("atlassian", diagnosis)


def test_discovery_scope_reads_ipc_summary_fallback():
    diagnosis = {"ipc_summary": {"policies_discovered": 29}}
    with pytest.raises(AssertionError, match="29 > ceiling 10"):
        _assert_discovery_scope("atlassian", diagnosis)
