"""HTML page fetcher with aggressive content cleaning.

Strips navigation, sidebars, footers, menus, and other non-article
content before extracting text. This is critical for lead quality —
without cleaning, keyword matching picks up "Read More" links,
menu items, and sidebar widgets as leads.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import FetchResult
from .base import BaseFetcher, extract_links

logger = logging.getLogger(__name__)

# CSS classes and IDs that indicate non-article content
_JUNK_PATTERNS = re.compile(
    r"(nav|menu|sidebar|footer|header|widget|breadcrumb|"
    r"social|share|comment|ad-|advert|banner|popup|modal|"
    r"cookie|consent|newsletter-form|signup-form|search-form|"
    r"pagination|related-posts|recommended|trending|"
    r"masthead|toolbar|topbar|bottombar)",
    re.IGNORECASE,
)

# Minimum text length for a paragraph to be worth keeping
_MIN_PARAGRAPH_LEN = 30

# Phrases that indicate navigation/UI text, not article content
_UI_PHRASES = {
    "read more",
    "learn more",
    "sign up",
    "subscribe",
    "share this",
    "follow us",
    "view all",
    "load more",
    "see more",
    "click here",
    "back to top",
    "skip to content",
    "cookie policy",
    "privacy policy",
    "terms of use",
    "all rights reserved",
    "powered by",
}


class HTMLFetcher(BaseFetcher):
    """Fetch and extract clean article text from HTML pages."""

    async def fetch(
        self, source_id: int, url: str, client, **kwargs
    ) -> FetchResult:
        """Fetch an HTML page, extract clean article text and links."""
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"HTML fetch failed for {url}: {e}")
            return self._make_result(source_id, url, error=str(e))

        raw = response.text
        links = extract_links(raw, url)
        content = clean_html_to_text(raw)

        return self._make_result(
            source_id=source_id,
            url=url,
            status_code=response.status_code,
            content=content,
            links=links,
        )


def clean_html_to_text(html: str) -> str:
    """Extract clean article text from HTML, stripping all junk.

    This is aggressive — it removes anything that looks like
    navigation, sidebars, footers, widgets, or UI chrome.
    Only keeps substantial paragraph and heading text.
    """
    soup = BeautifulSoup(html, "lxml")

    # Step 1: Remove obviously non-content elements
    for tag in soup(
        ["script", "style", "nav", "footer", "header", "aside",
         "iframe", "noscript", "svg", "form", "button", "input",
         "select", "textarea"]
    ):
        tag.decompose()

    # Step 2: Remove elements with junk class/id names
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        if _JUNK_PATTERNS.search(classes) or _JUNK_PATTERNS.search(
            tag_id
        ):
            tag.decompose()

    # Step 3: Try to find the main article content
    # Look for <article>, <main>, or common content containers
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(
            r"(content|article|post|entry|story|body)",
            re.IGNORECASE,
        ))
        or soup.find("div", id=re.compile(
            r"(content|article|post|entry|story|main)",
            re.IGNORECASE,
        ))
    )

    # Use article container if found, otherwise use whole body
    container = article or soup.body or soup

    # Step 4: Extract clean text from paragraphs and headings only
    text_parts = []
    for element in container.find_all(
        ["h1", "h2", "h3", "h4", "p", "li", "blockquote"]
    ):
        text = element.get_text(separator=" ", strip=True)

        # Skip short fragments
        if len(text) < _MIN_PARAGRAPH_LEN:
            continue

        # Skip UI/navigation text
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in _UI_PHRASES):
            continue

        # Skip text that's mostly links (navigation lists)
        link_count = len(element.find_all("a"))
        word_count = len(text.split())
        if link_count > 0 and word_count > 0 and link_count / word_count > 0.3:
            continue

        # Add heading prefix for structure
        tag_name = element.name.upper()
        if tag_name.startswith("H"):
            text_parts.append(f"HEADING: {text}")
        else:
            text_parts.append(text)

    content = "\n".join(text_parts)

    # If we got very little content, fall back to body text
    if len(content) < 100 and soup.body:
        content = soup.body.get_text(separator="\n", strip=True)
        # Still clean up excessive whitespace
        content = re.sub(r"\n{3,}", "\n\n", content)

    return content
