"""Tests for turning spoken inspection notes into structured findings."""

import json
import logging
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from app.ai import voice_analysis
from app.ai.schemas import Condition, DefectType, Severity
from app.core.config import settings


def test_keyword_fallback_reads_spoken_product_and_defects() -> None:
    """A rotten, cracked apple is graded from the words alone."""
    finding = voice_analysis.analyze_transcript(
        "My apple is rotten and has cracks.", ["apple", "banana"]
    )
    assert finding.produce_type == "apple"
    assert finding.detected_item == "Apple"
    assert finding.item_is_fruit is True
    assert finding.condition is Condition.ROTTEN
    assert DefectType.ROT in finding.defect_types
    assert DefectType.DEFORMITY in finding.defect_types
    assert finding.severity is Severity.HIGH
    assert finding.bbox is None


def test_keyword_fallback_handles_unclear_notes() -> None:
    """A note without a product stays ungraded instead of naming an item."""
    finding = voice_analysis.analyze_transcript("Hello, are you there?", ["apple"])
    assert finding.produce_type == "unknown"
    assert finding.detected_item == ""
    assert finding.item_is_fruit is False
    assert finding.condition is Condition.UNSURE
    assert finding.confidence == 0.2


def test_keyword_fallback_marks_out_of_category_fruit_as_unknown() -> None:
    """An edible fruit outside the active categories keeps its display name."""
    from app.services.inspection_service import _normalize_category

    finding = voice_analysis.analyze_transcript("This mango looks fresh.", ["apple"])
    finding = _normalize_category(finding, ["apple"])
    assert finding.produce_type == "unknown"
    assert finding.detected_item == "Mango"
    assert finding.item_is_fruit is True
    assert finding.condition is Condition.FRESH


def test_groq_analysis_returns_validated_finding(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Valid model JSON is accepted and coordinates are discarded for voice notes."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("test-key"))
    client = Mock()
    client.chat.completions.create.return_value = Mock(
        choices=[
            Mock(
                message=Mock(
                    content=json.dumps(
                        {
                            "produce_type": "apple",
                            "detected_item": "Apple",
                            "item_is_fruit": True,
                            "condition": "rotten",
                            "confidence": 0.9,
                            "visual_observation": "The apple is rotten.",
                            "defect_types": ["rot"],
                            "severity": "high",
                            "bbox": [0.1, 0.1, 0.9, 0.9],
                        }
                    )
                )
            )
        ]
    )
    with patch("app.ai.client_factory.Groq", return_value=client):
        finding = voice_analysis.analyze_transcript("The apple is rotten.", ["apple"])
    assert finding.produce_type == "apple"
    assert finding.condition is Condition.ROTTEN
    assert finding.bbox is None
    assert "Voice transcript analysis using model" in caplog.text


def test_invalid_model_output_uses_keyword_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unusable provider output never loses the inspection."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("test-key"))
    client = Mock()
    client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="not json"))]
    )
    with patch("app.ai.client_factory.Groq", return_value=client):
        finding = voice_analysis.analyze_transcript("My apple is rotten.", ["apple"])
    assert finding.produce_type == "apple"
    assert finding.condition is Condition.ROTTEN
