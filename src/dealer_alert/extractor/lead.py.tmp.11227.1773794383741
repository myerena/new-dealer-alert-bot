"""Lead extraction pipeline — combines keyword matching with entity extraction."""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import FetchResult, Lead, LeadScore
from .entities import extract_entities
from .keywords import KeywordMatch, find_keyword_matches

logger = logging.getLogger(__name__)


class LeadExtractor:
    """Extracts leads from fetched content by combining signals.

    Designed for high recall — false positives are acceptable.
    """

    def __init__(self, hot_min_mentions: int = 2):
        self.hot_min_mentions = hot_min_mentions

    def extract(self, fetch_result: FetchResult) -> list[Lead]:
        """Extract leads from a single fetch result.

        Returns a list of leads (may be empty if no signals found).
        """
        if not fetch_result.content or fetch_result.error:
            return []

        text = fetch_result.content
        keyword_matches = find_keyword_matches(text)

        if not keyword_matches:
            return []

        entities = extract_entities(text)

        # Group matches by context region to avoid duplicate leads
        leads = self._group_matches_to_leads(
            matches=keyword_matches,
            entities=entities,
            fetch_result=fetch_result,
        )

        return leads

    def _group_matches_to_leads(
        self,
        matches: list[KeywordMatch],
        entities,
        fetch_result: FetchResult,
    ) -> list[Lead]:
        """Group keyword matches into coherent leads.

        If matches are close together in the text, they belong to the same lead.
        Otherwise, each cluster becomes its own lead.
        """
        if not matches:
            return []

        # Simple clustering: group matches within 500 chars of each other
        clusters: list[list[KeywordMatch]] = []
        current_cluster: list[KeywordMatch] = [matches[0]]

        for match in matches[1:]:
            if match.position - current_cluster[-1].position < 500:
                current_cluster.append(match)
            else:
                clusters.append(current_cluster)
                current_cluster = [match]
        clusters.append(current_cluster)

        leads = []
        for cluster in clusters:
            lead = self._cluster_to_lead(cluster, entities, fetch_result)
            leads.append(lead)

        return leads

    def _cluster_to_lead(
        self,
        cluster: list[KeywordMatch],
        entities,
        fetch_result: FetchResult,
    ) -> Lead:
        """Convert a cluster of keyword matches into a Lead."""
        # Determine score based on signal strength
        strengths = [m.signal_strength for m in cluster]
        strong_count = strengths.count("strong")
        medium_count = strengths.count("medium")

        if strong_count >= 1 or len(cluster) >= self.hot_min_mentions:
            score = LeadScore.HOT
        elif medium_count >= 1:
            score = LeadScore.WARM
        else:
            score = LeadScore.COLD

        # Best snippet comes from the strongest match
        best_match = cluster[0]  # Already sorted by strength
        keywords = list(dict.fromkeys(m.keyword for m in cluster))

        # Use first available entity data
        dealer_name = entities.dealer_names[0] if entities.dealer_names else ""
        city = entities.cities[0] if entities.cities else ""
        state = entities.states[0] if entities.states else ""

        return Lead(
            source_id=fetch_result.source_id,
            source_url=fetch_result.url,
            title=best_match.match_text,
            snippet=best_match.context[:500],
            dealer_name=dealer_name,
            dealer_group="",  # TODO: dealer group resolution
            city=city,
            state=state,
            people=entities.people[:5],  # Cap at 5
            keywords_matched=keywords,
            outbound_links=fetch_result.links[:20],  # Cap at 20
            score=score,
            mention_count=len(cluster),
            discovered_at=datetime.utcnow(),
            raw_text=fetch_result.content[:2000],  # Store first 2k chars
        )
