"""Lead extraction pipeline — combines keyword matching with entity extraction."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from ..models import FetchResult, Lead, LeadScore
from .entities import extract_entities
from .keywords import KeywordMatch, find_keyword_matches

logger = logging.getLogger(__name__)

# Automotive-related terms for relevance filtering
_AUTOMOTIVE_TERMS = {
    "dealer",
    "dealership",
    "auto",
    "car",
    "vehicle",
    "motor",
    "automotive",
    "franchise",
    "lot",
    "showroom",
    "service department",
    "service center",
    "body shop",
    "collision",
    "repair",
    "maintenance",
    # Common automotive brands
    "ford",
    "chevrolet",
    "chevy",
    "toyota",
    "honda",
    "nissan",
    "hyundai",
    "kia",
    "dodge",
    "ram",
    "jeep",
    "chrysler",
    "subaru",
    "mazda",
    "bmw",
    "mercedes",
    "audi",
    "volkswagen",
    "vw",
    "gmc",
    "buick",
    "cadillac",
    "lincoln",
    "volvo",
    "acura",
    "lexus",
    "infiniti",
    "genesis",
}


class LeadExtractor:
    """Extracts leads from fetched content by combining signals.

    Designed for high recall — false positives are acceptable.
    Generates human-readable summaries for each lead.
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

        If matches are close together in the text, they belong to the
        same lead. Otherwise, each cluster becomes its own lead.
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

        # Start with initial score
        if strong_count >= 1 or len(cluster) >= self.hot_min_mentions:
            score = LeadScore.HOT
        elif medium_count >= 1:
            score = LeadScore.WARM
        else:
            score = LeadScore.COLD

        # Apply relevance filter: check if automotive terms appear near keyword matches
        if score == LeadScore.HOT:
            best_match = cluster[0]
            context_start = max(0, best_match.position - 200)
            context_end = min(
                len(fetch_result.content), best_match.position + len(best_match.match_text) + 200
            )
            context_window = fetch_result.content[context_start:context_end].lower()

            # Check if any automotive term appears in the context window
            has_automotive_term = any(term in context_window for term in _AUTOMOTIVE_TERMS)

            # If no automotive terms found, downgrade to COLD
            if not has_automotive_term:
                score = LeadScore.COLD

        # Best snippet comes from the strongest match
        best_match = cluster[0]
        keywords = list(dict.fromkeys(m.keyword for m in cluster))

        # Use first available entity data
        dealer_name = entities.dealer_names[0] if entities.dealer_names else ""
        city = entities.cities[0] if entities.cities else ""
        state = entities.states[0] if entities.states else ""

        # Generate a human-readable summary with full raw_text for context
        summary = self._generate_summary(
            context=best_match.context,
            raw_text=fetch_result.content,
            keywords=keywords,
            dealer_name=dealer_name,
            city=city,
            state=state,
            people=entities.people[:3],
            source_url=fetch_result.url,
        )

        return Lead(
            source_id=fetch_result.source_id,
            source_url=fetch_result.url,
            title=best_match.match_text,
            snippet=best_match.context[:1500],
            summary=summary,
            dealer_name=dealer_name,
            dealer_group="",
            city=city,
            state=state,
            people=entities.people[:5],
            keywords_matched=keywords,
            outbound_links=fetch_result.links[:20],
            score=score,
            mention_count=len(cluster),
            discovered_at=datetime.utcnow(),
            raw_text=fetch_result.content[:5000],
        )

    @staticmethod
    def _generate_summary(
        context: str,
        raw_text: str,
        keywords: list[str],
        dealer_name: str,
        city: str,
        state: str,
        people: list[str],
        source_url: str,
    ) -> str:
        """Generate a human-readable summary of a lead.

        Extracts the most meaningful sentences from the full raw_text
        around the keyword match, focusing on WHO, WHAT, WHERE.
        """
        # Use raw_text for more context, fallback to context if needed
        text = (raw_text or context or "").strip()
        if not text:
            return ""

        # Extract sentences that contain our keywords
        sentences = re.split(r"[.!?\n]+", text)
        relevant = []
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 15:
                continue
            # Keep sentences that mention keywords, dealers, or locations
            sent_lower = sent.lower()
            is_relevant = any(kw.lower() in sent_lower for kw in keywords)
            if dealer_name and dealer_name.lower() in sent_lower:
                is_relevant = True
            if city and city.lower() in sent_lower:
                is_relevant = True
            if any(p.lower() in sent_lower for p in people):
                is_relevant = True
            if is_relevant:
                relevant.append(sent.strip())

        # If no relevant sentences, use the first meaningful one
        if not relevant:
            for sent in sentences:
                sent = sent.strip()
                if len(sent) > 30:
                    relevant.append(sent)
                    break

        # Build the summary
        parts = []

        # WHO
        if dealer_name:
            parts.append(f"**{dealer_name}**")
        elif people:
            parts.append(f"**{people[0]}**")

        # WHERE
        location = ""
        if city and state:
            location = f" in {city}, {state}"
        elif state:
            location = f" in {state}"

        # WHAT (from keywords)
        action = ", ".join(keywords[:3])

        # Compose
        if parts:
            who = parts[0]
            summary = f"{who}{location} — {action}."
        else:
            summary = f"Signal: {action}{location}."

        # Add up to 3 relevant context sentences
        if relevant:
            # Pick the most informative sentences (up to 3, max 200 chars each)
            selected = []
            for sent in relevant[:3]:
                if len(sent) > 200:
                    sent = sent[:200] + "..."
                selected.append(sent)
            if selected:
                summary += f' "{" ".join(selected)}"'

        # Source domain
        from urllib.parse import urlparse

        domain = urlparse(source_url).netloc.replace("www.", "")
        summary += f" (via {domain})"

        return summary
