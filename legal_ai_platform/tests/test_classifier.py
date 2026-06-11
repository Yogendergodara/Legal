"""Tests for TaskClassifier."""

from legal_ai_platform.orchestration.classifier import TaskClassifier


def test_default_is_research():
    classifier = TaskClassifier()
    assert classifier.classify("What is Section 420 IPC?") == "research"


def test_explicit_task_type_overrides():
    classifier = TaskClassifier()
    assert classifier.classify("anything", explicit_task_type="contract") == "contract"


def test_contract_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Review this NDA contract clause") == "contract"


def test_drafting_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Draft a legal notice for breach") == "drafting"


def test_summary_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Summarize this judgment") == "summary"


def test_translation_keyword():
    classifier = TaskClassifier()
    assert classifier.classify("Translate this order to Hindi") == "translation"
