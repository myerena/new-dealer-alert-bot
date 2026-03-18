"""XML sitemap parser for URL discovery."""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import FetchResult
from .base import BaseFetcher

logger = logging.getLogger(__name__)


class SitemapFetcher(BaseFetcher):
    """Parse XML sitemaps to discover URLs for further crawling."""

    async def fetch(self, source_id: int, url: str, client, **kwargs) -> FetchResult:
        """Fetch a sitemap and extract all URLs from it."""
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"Sitemap fetch failed for {url}: {e}")
            return self._make_result(source_id, url, error=str(e))

        raw = response.text
        soup = BeautifulSoup(raw, "lxml-xml")

        urls = []

        # Handle sitemap index (sitemapindex > sitemap > loc)
        for loc in soup.find_all("loc"):
            urls.append(loc.get_text(strip=True))

        # Content is minimal for sitemaps — the value is the URLs
        content = f"Sitemap with {len(urls)} URLs discovered"
        return self._make_result(
            source_id=source_id,
            url=url,
            status_code=response.status_code,
            content=content,
            links=urls,
        )

    @staticmethod
    def guess_sitemap_url(base_url: str) -> str:
        """Given a domain, guess the likely sitemap URL."""
        return urljoin(base_url, "/sitemap.xml")
