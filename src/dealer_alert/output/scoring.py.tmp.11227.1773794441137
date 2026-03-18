"""Lead scoring logic.

Three-tier scoring:
- Hot:  2+ mentions OR 1 strong mention from trade/chamber/dealer source
- Warm: Staffing, hiring, teaser, construction, or coming-soon language
- Cold: Weak mention, but still worth parking in the queue
"""

from __future__ import annotations

from ..models import Lead, LeadScore, SourceCategory

# Source categories that boost a lead's score
HIGH_VALUE_CATEGORIES = {
    SourceCategory.TRADE_MEDIA,
    SourceCategory.CHAMBER,
    SourceCategory.STATE_ASSOCIATION,
    SourceCategory.DEALER_GROUP,
}


def score_lead(
    lead: Lead,
    source_category: SourceCategory = SourceCategory.OTHER,
    hot_min_mentions: int = 2,
) -> LeadScore:
    """Score a lead based on keyword strength, source quality, and mention count.

    This re-scores a lead considering the source category, which the extractor
    may not have had access to.
    """
    strong_keywords = any(
        kw in lead.keywords_matched
        for kw in _strong_keyword_stems()
    )

    # Hot: 2+ mentions, or 1 strong from a high-value source
    if lead.mention_count >= hot_min_mentions:
        return LeadScore.HOT
    if strong_keywords and source_category in HIGH_VALUE_CATEGORIES:
        return LeadScore.HOT

    # Warm: medium signals or hiring language
    medium_keywords = any(
        kw in " ".join(lead.keywords_matched).lower()
        for kw in ["hiring", "construction", "coming soon", "teaser", "expansion",
                    "renovation", "remodel", "welcome our new"]
    )
    if medium_keywords:
        return LeadScore.WARM

    # Cold: everything else
    return LeadScore.COLD


def _strong_keyword_stems() -> list[str]:
    """Keywords that indicate strong opening signals."""
    return [
        "grand opening",
        "ribbon cutting",
        "ribbon-cutting",
        "now open",
        "new location",
        "new dealership",
        "new rooftop",
        "relocated",
        "second location",
        "third location",
        "franchise agreement",
        "groundbreaking",
        "broke ground",
        "acquired",
        "acquisition",
        "new owner",
    ]
