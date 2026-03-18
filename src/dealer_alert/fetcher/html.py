"""HTML page fetcher with text extraction."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..models import FetchResult
from .base import BaseFetcher, extract_links

logger = logging.getLogger(__name__)


class HTMLFetcher(BaseFetcher):
    """Fetch and extract text from HTML pages."""

    async def fetch(self, source_id: int, url: str, client, **kwargs) -> FetchResult:
        """Fetch an HTML page, extract visible text and links."""
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"HTML fetch failed for {url}: {e}")
            return self._make_result(source_id, url, error=str(e))

        raw = response.text
        soup = BeautifulSoup(raw, "lxml")

        # Remove script/style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Extract text with some structure preserved
        text_parts = []
        for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "span", "div"]):
            text = element.get_text(strip=True)
            if text and len(text) > 10:  # Skip very short fragments
                tag_name = element.name.upper()
                if tag_name.startswith("H"):
                    text_parts.append(f"HEADING: {text}")
                else:
                    text_parts.append(text)

        content = "\n".join(text_parts)
        links = extract_links(raw, url)

        return self._make_result(
            source_id=source_id,
            url=url,
            status_code=response.status_code,
            content=content,
            links=links,
        )
