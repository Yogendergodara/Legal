"""Tests for PF-1C parallel pipeline tenant guard (RC-08)."""

from review_agent.config import ReviewSettings
from review_agent.services.pipeline_mode import parallel_pipeline_active


def test_parallel_requires_allowlisted_tenant():
    settings = ReviewSettings(
        review_pipeline_mode="parallel_hybrid",
        review_pipeline_tenant_allowlist="atlassian-demo,xecurify-demo",
    )
    assert parallel_pipeline_active("atlassian-demo", settings)
    assert not parallel_pipeline_active("e2e-demo", settings)


def test_parallel_all_tenants_when_allowlist_empty():
    settings = ReviewSettings(
        review_pipeline_mode="parallel_hybrid",
        review_pipeline_tenant_allowlist="",
    )
    assert parallel_pipeline_active("e2e-demo", settings)
