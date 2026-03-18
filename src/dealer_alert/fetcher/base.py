"""Base fetcher interface and shared utilities."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from urllib.parse import urljoin, urlparse

from ..models import FetchResult

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """Generate a short hash of content for change detection."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def normalize_url(url: str, base_url: str = "") -> str:
    """Normalize a URL, resolving relative paths against a base."""
    if not url:
        return ""
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    # Strip fragments, normalize
    return parsed._replace(fragment="").geturl()


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract all href links from HTML content."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        full = normalize_url(href, base_url)
        if full and full.startswith("http"):
            links.append(full)
    return list(set(links))


class BaseFetcher(ABC):
    """Abstract base for content fetchers."""

    @abstractmethod
    async def fetch(
        self, source_id: int, url: str, client, **kwargs
    ) -> FetchResult:
        """Fetch content from a URL and return a FetchResult."""
        ...

    def _make_result(
        self,
        source_id: int,
        url: str,
        status_code: int = 0,
        content: str = "",
        links: list[str] | None = None,
        error: str | None = None,
    ) -> FetchResult:
        return FetchResult(
            source_id=source_id,
            url=url,
            status_code=status_code,
            content=content,
            content_hash=content_hash(content) if content else "",
            links=links or [],
            fetched_at=datetime.utcnow(),
            error=error,
        )
