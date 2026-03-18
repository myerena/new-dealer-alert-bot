"""Tests for lead deduplication."""

from dealer_alert.models import Lead, LeadScore
from dealer_alert.output.dedup import (
    deduplicate_leads,
    leads_are_similar,
    merge_leads,
    normalize_name,
)


class TestNormalizeName:
    def test_strips_common_suffixes(self):
        assert normalize_name("Smith Auto Group LLC") == "smith"

    def test_handles_empty(self):
        assert normalize_name("") == ""

    def test_strips_punctuation(self):
        assert normalize_name("Bob's Motors, Inc.") == "bobs"


class TestSimilarity:
    def test_same_dealer_same_city(self):
        a = Lead(dealer_name="Smith Auto", city="Dallas", state="TX")
        b = Lead(dealer_name="Smith Automotive", city="Dallas", state="TX")
        assert leads_are_similar(a, b)

    def test_different_dealers(self):
        a = Lead(dealer_name="Smith Auto", city="Dallas", state="TX")
        b = Lead(dealer_name="Jones Motors", city="Dallas", state="TX")
        assert not leads_are_similar(a, b)

    def test_same_dealer_different_city(self):
        a = Lead(dealer_name="Smith Auto", city="Dallas", state="TX")
        b = Lead(dealer_name="Smith Auto", city="Houston", state="TX")
        assert not leads_are_similar(a, b)


class TestMerge:
    def test_keeps_best_score(self):
        a = Lead(dealer_name="Smith", score=LeadScore.COLD, mention_count=1)
        b = Lead(dealer_name="Smith Auto", score=LeadScore.HOT, mention_count=2)
        merged = merge_leads([a, b])
        assert merged.score == LeadScore.HOT

    def test_combines_keywords(self):
        a = Lead(keywords_matched=["grand opening"])
        b = Lead(keywords_matched=["now open", "grand opening"])
        merged = merge_leads([a, b])
        assert "grand opening" in merged.keywords_matched
        assert "now open" in merged.keywords_matched

    def test_sums_mention_count(self):
        a = Lead(mention_count=2)
        b = Lead(mention_count=3)
        merged = merge_leads([a, b])
        assert merged.mention_count == 5


class TestDeduplicate:
    def test_merges_duplicates(self):
        leads = [
            Lead(
                dealer_name="Smith Auto",
                city="Dallas",
                state="TX",
                score=LeadScore.HOT,
            ),
            Lead(
                dealer_name="Smith Automotive",
                city="Dallas",
                state="TX",
                score=LeadScore.COLD,
            ),
            Lead(
                dealer_name="Jones Motors",
                city="Houston",
                state="TX",
                score=LeadScore.WARM,
            ),
        ]
        result = deduplicate_leads(leads)
        assert len(result) == 2  # Smith merged, Jones separate

    def test_empty_list(self):
        assert deduplicate_leads([]) == []

    def test_no_duplicates(self):
        leads = [
            Lead(dealer_name="A", city="X", state="TX"),
            Lead(dealer_name="B", city="Y", state="CA"),
        ]
        assert len(deduplicate_leads(leads)) == 2
