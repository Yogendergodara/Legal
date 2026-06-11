"""Shared test fixtures."""

import pytest

from legal_ai_platform.container import reset_container


@pytest.fixture(autouse=True)
def _reset_container():
    """Reset DI container between tests."""
    reset_container()
    yield
    reset_container()
