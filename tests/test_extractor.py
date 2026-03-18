"""Tests for keyword matching and entity extraction."""

from dealer_alert.extractor.entities import extract_entities
from dealer_alert.extractor.keywords import find_keyword_matches
from dealer_alert.extractor.lead import LeadExtractor
from dealer_alert.models import FetchResult, LeadScore


class TestKeywordMatching:
    """Tests for the keyword matching engine."""

    def test_strong_signal_grand_opening(self):
        text = "ABC Motors celebrated their grand opening in Springfield, IL today."
        matches = find_keyword_matches(text)
        assert len(matches) >= 1
        assert matches[0].signal_strength == "strong"
        assert "grand opening" in matches[0].keyword

    def test_strong_signal_ribbon_cutting(self):
        text = "The chamber hosted a ribbon cutting ceremony for the new dealership."
        matches = find_keyword_matches(text)
        assert any(m.signal_strength == "strong" for m in matches)

    def test_medium_signal_hiring(self):
        text = "Smith Auto Group is now hiring a General Manager for their new store."
        matches = find_keyword_matches(text)
        assert any(m.signal_strength in ("strong", "medium") for m in matches)

    def test_medium_signal_coming_soon(self):
        text = "A new Toyota dealership is coming soon to the north side of town."
        matches = find_keyword_matches(text)
        assert len(matches) >= 1

    def test_weak_signal_join_team(self):
        text = "Exciting career opportunities! Join our team at Premier Ford."
        matches = find_keyword_matches(text)
        assert any(m.signal_strength == "weak" for m in matches)

    def test_no_match_unrelated(self):
        text = "The weather today is sunny with a high of 75 degrees."
        matches = find_keyword_matches(text)
        assert len(matches) == 0

    def test_multiple_signals(self):
        text = (
            "Grand opening celebration! Smith Chevrolet is now open at their "
            "new location in Dallas, TX. The ribbon cutting ceremony was attended "
            "by the chamber of commerce."
        )
        matches = find_keyword_matches(text)
        assert len(matches) >= 2
        strong = [m for m in matches if m.signal_strength == "strong"]
        assert len(strong) >= 2

    def test_case_insensitive(self):
        text = "GRAND OPENING this Saturday at the new dealership!"
        matches = find_keyword_matches(text)
        assert len(matches) >= 1


class TestEntityExtraction:
    """Tests for entity extraction."""

    def test_dealer_name_extraction(self):
        text = "Smith Ford is opening a new location in Phoenix."
        entities = extract_entities(text)
        assert any("Smith Ford" in name for name in entities.dealer_names)

    def test_city_state_extraction(self):
        text = "The new store will be located in Springfield, IL near the mall."
        entities = extract_entities(text)
        assert "Springfield" in entities.cities
        assert "IL" in entities.states

    def test_city_state_full_name(self):
        text = "Opening a dealership in Austin, Texas next month."
        entities = extract_entities(text)
        assert "Austin" in entities.cities
        assert "TX" in entities.states

    def test_person_extraction(self):
        text = "General Manager John Smith will lead the new store operations."
        entities = extract_entities(text)
        assert any("John Smith" in p for p in entities.people)

    def test_person_name_title_format(self):
        text = "Jane Doe, General Manager of the new location, said..."
        entities = extract_entities(text)
        assert any("Jane Doe" in p for p in entities.people)


class TestLeadExtractor:
    """Tests for the full lead extraction pipeline."""

    def test_extracts_hot_lead(self):
        extractor = LeadExtractor(hot_min_mentions=2)
        result = FetchResult(
            source_id=1,
            url="https://example.com/news",
            content=(
                "Grand opening celebration at Smith Chevrolet in Dallas, TX. "
                "The ribbon cutting ceremony was attended by the chamber. "
                "General Manager John Doe welcomed the community."
            ),
            links=["https://smithchevrolet.com"],
        )
        leads = extractor.extract(result)
        assert len(leads) >= 1
        # Multiple strong signals → hot
        assert any(lead.score == LeadScore.HOT for lead in leads)

    def test_extracts_warm_lead(self):
        extractor = LeadExtractor(hot_min_mentions=3)
        result = FetchResult(
            source_id=1,
            url="https://example.com/jobs",
            content="Premier Auto is now hiring a Sales Manager for expansion plans.",
        )
        leads = extractor.extract(result)
        assert len(leads) >= 1

    def test_no_leads_from_empty(self):
        extractor = LeadExtractor()
        result = FetchResult(source_id=1, url="https://example.com", content="")
        leads = extractor.extract(result)
        assert len(leads) == 0

    def test_no_leads_from_error(self):
        extractor = LeadExtractor()
        result = FetchResult(
            source_id=1, url="https://example.com",
            content="Some content", error="Timeout"
        )
        leads = extractor.extract(result)
        assert len(leads) == 0
