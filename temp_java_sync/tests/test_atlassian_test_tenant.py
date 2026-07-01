"""Tests for isolated Atlassian test tenant resolution."""

from __future__ import annotations

import os

import atlassian_test_tenant as att


def test_default_tenant():
    assert att.resolve_atlassian_test_tenant() == "atlassian-test-run"


def test_normalize_user_style_name():
    assert att.normalize_tenant_id("Attlassian_test_run") == "atlassian-test-run"


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_TEST_TENANT_ID", "atlassian-demo")
    assert att.resolve_atlassian_test_tenant(cli_tenant="my-isolated-run") == "my-isolated-run"


def test_output_paths():
    assert att.sync_output_path("atlassian-test-run") == "sync_atlassian-test-run.json"
    assert att.review_output_path("atlassian-test-run") == "atlassian_atlassian-test-run_smoke.json"
