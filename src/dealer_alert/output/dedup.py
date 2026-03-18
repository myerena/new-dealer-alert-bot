"""Lead deduplication — merges duplicate leads by dealer/location similarity.

Leads from multiple sources often describe the same event (e.g., "Smith Auto
grand opening in Dallas" appears in a trade newsletter AND a chamber post).
This module detects and merges duplicates using fuzzy matching on:
  - Dealer name (normalized)
  - City + State
  - Keyword overlap

Dedup runs as part of digest generation, producing a cleaner output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from ..models import Lead, LeadScore

logger = logging.getLogger(__name__)

# Words to strip when normalizing dealer names
STRIP_WORDS = {
    "inc",
    "llc",
    "ltd",
    "corp",
    "corporation",
    "group",
    "auto",
    "automotive",
    "motors",
    "motor",
    "dealership",
    "dealer",
    "the",
    "of",
    "and",
    "&",
}


def normalize_name(name: str) -> str:
    """Normalize a dealer name for comparison.

    Strips common suffixes, lowercases, removes punctuation.
    """
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", "", name)
    words = [w for w in name.split() if w not in STRIP_WORDS]
    return " ".join(words).strip()


def normalize_location(city: str, state: str) -> str:
    """Normalize city + state for comparison."""
    city = city.lower().strip().replace(".", "")
    state = state.upper().strip().replace(".", "")
    return f"{city}|{state}"


def leads_are_similar(a: Lead, b: Lead, name_threshold: float = 0.7) -> bool:
    """Determine if two leads likely describe the same event.

    Returns True if dealer names are similar AND locations match.
    """
    # Both must have some dealer name to compare
    name_a = normalize_name(a.dealer_name)
    name_b = normalize_name(b.dealer_name)

    if not name_a or not name_b:
        # Without dealer names, only dedup if same source URL
        # (same article parsed multiple times)
        if a.source_url and a.source_url == b.source_url:
            return True
        # Or very high title similarity (>0.85)
        title_a = a.title.lower()[:80]
        title_b = b.title.lower()[:80]
        if title_a and title_b:
            return _simple_similarity(title_a, title_b) > 0.85
        return False

    # Name similarity
    name_sim = _simple_similarity(name_a, name_b)
    if name_sim < name_threshold:
        return False

    # Location match (if both have locations)
    loc_a = normalize_location(a.city, a.state)
    loc_b = normalize_location(b.city, b.state)

    if a.city and b.city:
        return loc_a == loc_b

    # If one is missing location, require very high name match
    return name_sim > 0.9


def merge_leads(leads: list[Lead]) -> Lead:
    """Merge a group of duplicate leads into one.

    Keeps the highest score, combines keywords, uses the best
    (most complete) dealer name and location.
    """
    if len(leads) == 1:
        return leads[0]

    # Sort by score (hot > warm > cold) and completeness
    score_order = {LeadScore.HOT: 0, LeadScore.WARM: 1, LeadScore.COLD: 2}
    leads_sorted = sorted(
        leads,
        key=lambda ld: (
            score_order.get(ld.score, 3),
            -(len(ld.dealer_name) + len(ld.city)),
        ),
    )

    best = leads_sorted[0]

    # Combine keywords from all duplicates
    all_keywords = []
    seen_kw = set()
    for ld in leads:
        for kw in ld.keywords_matched:
            if kw not in seen_kw:
                all_keywords.append(kw)
                seen_kw.add(kw)

    # Use best available data for each field
    dealer_name = max((ld.dealer_name for ld in leads), key=len, default="")
    city = max((ld.city for ld in leads), key=len, default="")
    state = max((ld.state for ld in leads), key=len, default="")

    # Combine outbound links
    all_links = []
    seen_links = set()
    for ld in leads:
        for link in ld.outbound_links:
            if link not in seen_links:
                all_links.append(link)
                seen_links.add(link)

    return replace(
        best,
        dealer_name=dealer_name,
        city=city,
        state=state,
        keywords_matched=all_keywords,
        outbound_links=all_links[:20],
        mention_count=sum(ld.mention_count for ld in leads),
    )


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
    """Deduplicate a list of leads by grouping similar ones.

    Returns a new list with duplicates merged. Order is preserved
    (first occurrence of each group).
    """
    if not leads:
        return []

    groups: list[list[Lead]] = []
    used = set()

    for i, lead_a in enumerate(leads):
        if i in used:
            continue

        group = [lead_a]
        used.add(i)

        for j, lead_b in enumerate(leads):
            if j in used or j <= i:
                continue
            if leads_are_similar(lead_a, lead_b):
                group.append(lead_b)
                used.add(j)

        groups.append(group)

    merged = [merge_leads(group) for group in groups]

    original_count = len(leads)
    merged_count = len(merged)
    if original_count != merged_count:
        logger.info(
            f"Dedup: {original_count} leads -> {merged_count} "
            f"({original_count - merged_count} duplicates merged)"
        )

    return merged


def _simple_similarity(a: str, b: str) -> float:
    """Simple character-level similarity (Jaccard on character bigrams).

    Good enough for dealer name matching without external deps.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    bigrams_a = set(a[i : i + 2] for i in range(len(a) - 1))
    bigrams_b = set(b[i : i + 2] for i in range(len(b) - 1))

    if not bigrams_a or not bigrams_b:
        return 0.0

    intersection = len(bigrams_a & bigrams_b)
    union = len(bigrams_a | bigrams_b)

    return intersection / union if union > 0 else 0.0
