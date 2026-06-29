"""Tests for ReviewState LangGraph reducers."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.observability.timing import (
    merge_conflict_pairs,
    merge_dict_shallow,
    merge_findings,
    merge_id_lists,
)
from review_agent.state.review_state import merge_warnings


def test_merge_warnings_dedupes() -> None:
    assert merge_warnings(["a"], ["a", "b"]) == ["a", "b"]
    assert merge_warnings(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_merge_warnings_empty_new() -> None:
    assert merge_warnings(["a"], []) == ["a"]


def test_merge_id_lists_ordered_union() -> None:
    assert merge_id_lists(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert merge_id_lists(None, ["x"]) == ["x"]
    assert merge_id_lists(["x"], None) == ["x"]


def test_merge_dict_shallow_right_wins() -> None:
    left = {"a": 1, "b": {"x": 1}}
    right = {"b": {"y": 2}, "c": 3}
    assert merge_dict_shallow(left, right) == {"a": 1, "b": {"y": 2}, "c": 3}


def test_merge_conflict_pairs_dedupes() -> None:
    left = [["f1", "f2"]]
    right = [["f2", "f1"], ["f3", "f4"]]
    assert merge_conflict_pairs(left, right) == [["f1", "f2"], ["f3", "f4"]]


def _gap_finding(
    finding_id: str,
    *,
    section_id: str = "s1",
    gap_type: str = "no_policy",
) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=finding_id,
        dimension_id=f"{section_id}:{finding_id}",
        dimension_label="Gap",
        status=ComplianceStatus.INCONCLUSIVE,
        severity=Severity.IMPORTANT,
        contract_section_id=section_id,
        rationale="gap",
        metadata={"gap_type": gap_type},
    )


def test_merge_findings_drops_superseded_gap_on_final_verify() -> None:
    left = [_gap_finding("old-gap")]
    right = [
        ComplianceFinding(
            finding_id="new-gap",
            dimension_id="s1:final_gap",
            dimension_label="Recovered",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_section_id="s1",
            rationale="recovered",
            metadata={"final_verify": "gap_llm", "gap_type": "no_policy"},
        )
    ]
    merged = merge_findings(left, right)
    assert len(merged) == 1
    assert merged[0].finding_id == "new-gap"


def test_merge_findings_drops_inconclusive_on_final_verify_recompare() -> None:
    left = [
        ComplianceFinding(
            finding_id="old-unclear",
            dimension_id="s1:unclear",
            dimension_label="Unclear",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            contract_section_id="s1",
            rationale="unclear compare",
            metadata={"unclear": True},
        )
    ]
    right = [
        ComplianceFinding(
            finding_id="new-recompare",
            dimension_id="s1:recompare",
            dimension_label="Recovered",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_section_id="s1",
            rationale="recompare resolved",
            metadata={"final_verify": "recompare"},
        )
    ]
    merged = merge_findings(left, right)
    assert len(merged) == 1
    assert merged[0].finding_id == "new-recompare"
