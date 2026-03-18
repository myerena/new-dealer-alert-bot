"""Tests for lead scoring logic."""

from dealer_alert.models import Lead, LeadScore, SourceCategory
from dealer_alert.output.scoring import score_lead


def test_hot_from_multiple_mentions():
    """Multiple mentions should score hot."""
    lead = Lead(
        mention_count=3,
        keywords_matched=["now open"],
    )
    assert score_lead(lead, hot_min_mentions=2) == LeadScore.HOT


def test_hot_from_strong_keyword_and_high_value_source():
    """Strong keyword + high-value source category = hot."""
    lead = Lead(
        mention_count=1,
        keywords_matched=["grand opening"],
    )
    assert score_lead(lead, SourceCategory.TRADE_MEDIA) == LeadScore.HOT


def test_warm_from_hiring():
    """Hiring keywords should score warm."""
    lead = Lead(
        mention_count=1,
        keywords_matched=["hiring"],
    )
    assert score_lead(lead) == LeadScore.WARM


def test_warm_from_construction():
    """Construction language should score warm."""
    lead = Lead(
        mention_count=1,
        keywords_matched=["construction"],
    )
    assert score_lead(lead) == LeadScore.WARM


def test_cold_from_weak_signal():
    """Weak signals with no boost should score cold."""
    lead = Lead(
        mention_count=1,
        keywords_matched=["join our team"],
    )
    assert score_lead(lead) == LeadScore.COLD
