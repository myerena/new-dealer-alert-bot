"""Expansion engine — discovers related pages when leads mention dealers or cities.

When a lead is found, this engine looks for related sources to add to the registry:
- Dealer website (careers, news, about pages)
- Chamber of commerce pages for the city
- Local news outlets
- Economic development pages
- Social media profiles extracted from the dealer's website
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ..config import Config
from ..db import Database
from ..models import (
    DiscoveredSource,
    Lead,
    SourceCategory,
    SourceType,
)

logger = logging.getLogger(__name__)

# URL patterns that suggest useful sub-pages on a domain
_INTERESTING_PATH_PATTERNS = [
    (r"/news", "news page"),
    (r"/press", "press page"),
    (r"/blog", "blog"),
    (r"/about", "about page"),
    (r"/careers?", "careers page"),
    (r"/jobs?", "jobs page"),
    (r"/locations?", "locations page"),
    (r"/events?", "events page"),
    (r"/calendar", "calendar"),
    (r"/ribbon.?cutting", "ribbon cutting page"),
    (r"/new.?member", "new member page"),
    (r"/member.?director", "member directory"),
    (r"/directory", "directory page"),
    (r"/economic.?develop", "economic development"),
    (r"/grand.?opening", "grand opening page"),
]


class ExpansionEngine:
    """Discovers new sources based on lead signals and outbound links."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def expand_from_lead(self, lead: Lead) -> list[DiscoveredSource]:
        """Given a lead, discover related sources worth crawling.

        Returns a list of DiscoveredSource objects (not yet registered).
        """
        if not self.config.auto_expand:
            return []

        discovered = []

        # 1. Examine outbound links from the lead for interesting pages
        discovered.extend(self._discover_from_links(lead))

        # 2. Generate search-style URLs for the dealer + city combo
        discovered.extend(self._generate_chamber_urls(lead))

        # Deduplicate and filter already-known sources
        discovered = self._deduplicate(discovered)

        # Respect the per-crawl cap
        if len(discovered) > self.config.max_new_sources_per_crawl:
            discovered = discovered[: self.config.max_new_sources_per_crawl]

        return discovered

    def expand_from_links(self, source_id: int, links: list[str]) -> list[DiscoveredSource]:
        """Discover interesting sub-pages from a set of outbound links."""
        discovered = []

        for link in links:
            parsed = urlparse(link)
            path = parsed.path.lower()

            for pattern, reason in _INTERESTING_PATH_PATTERNS:
                if re.search(pattern, path):
                    ds = DiscoveredSource(
                        url=link,
                        source_type=SourceType.HTML,
                        category=self._guess_category(link, reason),
                        discovered_from_source_id=source_id,
                        reason=f"Interesting path: {reason}",
                    )
                    discovered.append(ds)
                    break  # One match per link is enough

        return self._deduplicate(discovered)

    def _discover_from_links(self, lead: Lead) -> list[DiscoveredSource]:
        """Extract interesting URLs from the lead's outbound links."""
        discovered = []

        for link in lead.outbound_links:
            parsed = urlparse(link)
            path = parsed.path.lower()
            domain = parsed.netloc.lower()

            # Skip social media homepages (too noisy), but keep specific profiles
            if any(s in domain for s in ["facebook.com", "instagram.com", "linkedin.com"]):
                if "/" in path.strip("/"):  # Has a path beyond root
                    discovered.append(
                        DiscoveredSource(
                            url=link,
                            source_type=SourceType.SOCIAL,
                            category=SourceCategory.DEALER_SITE,
                            discovered_from_source_id=lead.source_id,
                            geography=f"{lead.city}, {lead.state}" if lead.city else "",
                            reason=f"Social profile linked from lead: {lead.title}",
                        )
                    )
                continue

            # Check for interesting page paths
            for pattern, reason in _INTERESTING_PATH_PATTERNS:
                if re.search(pattern, path):
                    discovered.append(
                        DiscoveredSource(
                            url=link,
                            source_type=SourceType.HTML,
                            category=self._guess_category(link, reason),
                            discovered_from_source_id=lead.source_id,
                            geography=f"{lead.city}, {lead.state}" if lead.city else "",
                            reason=f"{reason} linked from lead: {lead.title}",
                        )
                    )
                    break

        return discovered

    def _generate_chamber_urls(self, lead: Lead) -> list[DiscoveredSource]:
        """Generate likely chamber/econ-dev URLs for the lead's geography."""
        if not lead.city or not lead.state:
            return []

        discovered = []
        city_slug = lead.city.lower().replace(" ", "")

        # Common chamber URL patterns
        chamber_patterns = [
            f"https://www.{city_slug}chamber.com",
            f"https://www.{city_slug}chamber.org",
            f"https://{city_slug}chamber.com",
            f"https://www.chamberof{city_slug}.com",
        ]

        for url in chamber_patterns:
            discovered.append(
                DiscoveredSource(
                    url=url,
                    source_type=SourceType.HTML,
                    category=SourceCategory.CHAMBER,
                    discovered_from_source_id=lead.source_id,
                    geography=f"{lead.city}, {lead.state}",
                    reason=f"Generated chamber URL for {lead.city}, {lead.state}",
                )
            )

        return discovered

    def _guess_category(self, url: str, reason: str) -> SourceCategory:
        """Guess the source category from URL and context."""
        domain = urlparse(url).netloc.lower()
        if "chamber" in domain:
            return SourceCategory.CHAMBER
        if "econom" in domain or "develop" in domain:
            return SourceCategory.ECONOMIC_DEV
        if "news" in domain or "media" in domain:
            return SourceCategory.LOCAL_NEWS
        if "career" in reason or "job" in reason:
            return SourceCategory.DEALER_SITE
        return SourceCategory.OTHER

    def _deduplicate(self, discovered: list[DiscoveredSource]) -> list[DiscoveredSource]:
        """Remove duplicates and already-known sources."""
        seen_urls = set()
        unique = []

        for ds in discovered:
            if ds.url in seen_urls:
                continue
            if self.db.source_exists(ds.url):
                continue
            seen_urls.add(ds.url)
            unique.append(ds)

        return unique
