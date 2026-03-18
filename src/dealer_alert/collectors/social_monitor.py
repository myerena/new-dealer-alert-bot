"""Social media monitor — scrapes public pages/profiles for lead signals.

Monitors public posts from dealerships, chambers, industry figures, and
local business groups across Facebook, Instagram, LinkedIn, and X/Twitter.

Each platform has a dedicated scraper class that:
  1. Hits the public page/profile URL
  2. Extracts recent post text and metadata
  3. Returns FetchResult objects for the lead extraction pipeline

No login required — all scraping targets public content only.
Social pages break frequently. Each scraper has graceful fallback
behavior and logs warnings rather than crashing.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..fetcher.base import content_hash
from ..models import FetchResult

logger = logging.getLogger(__name__)

# Default headers to mimic a real browser
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class SocialPost:
    """A single social media post."""

    platform: str
    profile_url: str
    post_text: str
    post_url: str = ""
    post_date: datetime | None = None
    likes: int = 0
    shares: int = 0
    comments: int = 0
    images: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


class BaseSocialScraper(ABC):
    """Abstract base for platform-specific scrapers."""

    platform: str = ""

    @abstractmethod
    async def scrape_profile(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Scrape recent posts from a public profile/page."""
        ...

    def posts_to_fetch_result(
        self,
        profile_url: str,
        posts: list[SocialPost],
    ) -> FetchResult:
        """Convert scraped posts into a FetchResult for the pipeline."""
        if not posts:
            return FetchResult(
                source_id=0,
                url=profile_url,
                content="",
                content_hash="",
            )

        # Combine all post text for keyword extraction
        combined = "\n\n---\n\n".join(
            f"{p.post_text}"
            + (f"\n[{p.post_url}]" if p.post_url else "")
            for p in posts
        )

        links = [p.post_url for p in posts if p.post_url]

        return FetchResult(
            source_id=0,
            url=profile_url,
            status_code=200,
            content=combined,
            content_hash=content_hash(combined),
            links=links,
            fetched_at=datetime.utcnow(),
        )


class FacebookScraper(BaseSocialScraper):
    """Scrapes public Facebook pages for recent posts.

    Uses the mobile site (mbasic.facebook.com) which has simpler HTML
    and is easier to parse than the full desktop site.
    """

    platform = "facebook"

    async def scrape_profile(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Scrape recent posts from a public Facebook page."""
        # Convert to mobile URL for simpler HTML
        mobile_url = self._to_mobile_url(url)
        posts = []

        try:
            resp = await client.get(mobile_url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                logger.warning(
                    f"Facebook {url} returned {resp.status_code}"
                )
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            # mbasic.facebook.com wraps posts in divs with specific patterns
            post_divs = soup.find_all("div", {"class": "story_body_container"})

            if not post_divs:
                # Fallback: try finding article or post containers
                post_divs = soup.find_all("article") or soup.find_all(
                    "div", {"data-ft": True}
                )

            for div in post_divs[:10]:
                text = div.get_text(separator=" ", strip=True)
                if text and len(text) > 20:
                    post = SocialPost(
                        platform=self.platform,
                        profile_url=url,
                        post_text=text[:2000],
                        hashtags=re.findall(r"#(\w+)", text),
                    )
                    posts.append(post)

        except Exception as exc:
            logger.warning(f"Facebook scrape failed for {url}: {exc}")

        logger.debug(f"Facebook: scraped {len(posts)} posts from {url}")
        return posts

    @staticmethod
    def _to_mobile_url(url: str) -> str:
        """Convert a facebook.com URL to mbasic.facebook.com."""
        url = re.sub(
            r"https?://(www\.)?facebook\.com",
            "https://mbasic.facebook.com",
            url,
        )
        return url


class InstagramScraper(BaseSocialScraper):
    """Scrapes public Instagram profiles for recent post captions.

    Uses the /?__a=1&__d=dis JSON endpoint or falls back to HTML
    meta tags and embedded JSON for public profiles.
    """

    platform = "instagram"

    async def scrape_profile(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Scrape recent captions from a public Instagram profile."""
        posts = []

        try:
            # Try the HTML page and look for embedded JSON data
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                logger.warning(
                    f"Instagram {url} returned {resp.status_code}"
                )
                return []

            # Try to extract SharedData JSON from page
            posts = self._extract_from_html(resp.text, url)

            if not posts:
                # Fallback: extract from meta tags
                posts = self._extract_from_meta(resp.text, url)

        except Exception as exc:
            logger.warning(f"Instagram scrape failed for {url}: {exc}")

        logger.debug(f"Instagram: scraped {len(posts)} posts from {url}")
        return posts

    def _extract_from_html(
        self, html: str, profile_url: str
    ) -> list[SocialPost]:
        """Try to extract post data from embedded JSON in HTML."""
        posts = []

        # Look for _sharedData or similar JSON blobs
        patterns = [
            r'window\._sharedData\s*=\s*({.*?});',
            r'"edge_owner_to_timeline_media":\s*({.*?"edges":\s*\[.*?\])',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue

            try:
                data = json.loads(match.group(1))
                edges = self._find_edges(data)
                for edge in edges[:10]:
                    node = edge.get("node", edge)
                    caption_edges = (
                        node.get("edge_media_to_caption", {})
                        .get("edges", [])
                    )
                    caption = ""
                    if caption_edges:
                        caption = caption_edges[0].get("node", {}).get(
                            "text", ""
                        )

                    if caption:
                        shortcode = node.get("shortcode", "")
                        post_url = (
                            f"https://www.instagram.com/p/{shortcode}/"
                            if shortcode
                            else ""
                        )
                        posts.append(
                            SocialPost(
                                platform=self.platform,
                                profile_url=profile_url,
                                post_text=caption[:2000],
                                post_url=post_url,
                                hashtags=re.findall(r"#(\w+)", caption),
                            )
                        )
            except (json.JSONDecodeError, KeyError):
                continue

        return posts

    def _extract_from_meta(
        self, html: str, profile_url: str
    ) -> list[SocialPost]:
        """Fallback: extract whatever we can from meta tags."""
        soup = BeautifulSoup(html, "lxml")
        posts = []

        # og:description sometimes contains recent post info
        meta = soup.find("meta", property="og:description")
        if meta and meta.get("content"):
            content = meta["content"]
            if len(content) > 30:
                posts.append(
                    SocialPost(
                        platform=self.platform,
                        profile_url=profile_url,
                        post_text=content[:2000],
                    )
                )

        return posts

    @staticmethod
    def _find_edges(data: dict) -> list:
        """Recursively find edge arrays in Instagram JSON."""
        if isinstance(data, dict):
            if "edges" in data:
                return data["edges"]
            for val in data.values():
                result = InstagramScraper._find_edges(val)
                if result:
                    return result
        return []


class LinkedInScraper(BaseSocialScraper):
    """Scrapes public LinkedIn company pages for recent posts.

    LinkedIn is aggressive with bot detection, so this scraper
    is conservative: it only reads the public company page HTML
    and extracts visible post text.
    """

    platform = "linkedin"

    async def scrape_profile(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Scrape recent posts from a public LinkedIn company page."""
        posts = []

        try:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                logger.warning(
                    f"LinkedIn {url} returned {resp.status_code}"
                )
                return []

            soup = BeautifulSoup(resp.text, "lxml")

            # LinkedIn public pages show posts in specific containers
            # These selectors change frequently
            post_containers = soup.find_all(
                "div", class_=re.compile(r"feed-shared-update")
            )

            if not post_containers:
                # Fallback: look for any substantial text blocks
                post_containers = soup.find_all(
                    "p", class_=re.compile(r"break-words")
                )

            for container in post_containers[:10]:
                text = container.get_text(separator=" ", strip=True)
                if text and len(text) > 30:
                    posts.append(
                        SocialPost(
                            platform=self.platform,
                            profile_url=url,
                            post_text=text[:2000],
                            hashtags=re.findall(r"#(\w+)", text),
                        )
                    )

            # Also check meta description for company info
            if not posts:
                meta = soup.find("meta", {"name": "description"})
                if meta and meta.get("content"):
                    content = meta["content"]
                    if len(content) > 30:
                        posts.append(
                            SocialPost(
                                platform=self.platform,
                                profile_url=url,
                                post_text=content[:2000],
                            )
                        )

        except Exception as exc:
            logger.warning(f"LinkedIn scrape failed for {url}: {exc}")

        logger.debug(f"LinkedIn: scraped {len(posts)} posts from {url}")
        return posts


class XTwitterScraper(BaseSocialScraper):
    """Scrapes X/Twitter for public posts matching dealer keywords.

    Uses Nitter instances (open-source Twitter frontend) as the
    primary method since Twitter's own site requires authentication.
    Falls back to Twitter search RSS bridges.
    """

    platform = "x_twitter"

    # Public Nitter instances (these rotate — update as needed)
    NITTER_INSTANCES = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
    ]

    async def scrape_profile(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Scrape recent tweets from a public profile via Nitter."""
        posts = []
        username = self._extract_username(url)

        if not username:
            logger.warning(f"Could not extract username from {url}")
            return []

        for nitter_base in self.NITTER_INSTANCES:
            try:
                nitter_url = f"{nitter_base}/{username}"
                resp = await client.get(
                    nitter_url,
                    headers=BROWSER_HEADERS,
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue

                posts = self._parse_nitter_html(
                    resp.text, url, nitter_base
                )
                if posts:
                    break

            except Exception as exc:
                logger.debug(
                    f"Nitter instance {nitter_base} failed: {exc}"
                )
                continue

        logger.debug(
            f"X/Twitter: scraped {len(posts)} posts from {url}"
        )
        return posts

    async def search_keywords(
        self,
        keywords: list[str],
        client: httpx.AsyncClient,
    ) -> list[SocialPost]:
        """Search X/Twitter for posts matching keywords via Nitter."""
        query = " OR ".join(f'"{kw}"' for kw in keywords)
        posts = []

        for nitter_base in self.NITTER_INSTANCES:
            try:
                search_url = (
                    f"{nitter_base}/search?f=tweets"
                    f"&q={quote_plus(query)}"
                )
                resp = await client.get(
                    search_url,
                    headers=BROWSER_HEADERS,
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue

                posts = self._parse_nitter_html(
                    resp.text, search_url, nitter_base
                )
                if posts:
                    break

            except Exception as exc:
                logger.debug(
                    f"Nitter search at {nitter_base} failed: {exc}"
                )
                continue

        return posts

    def _parse_nitter_html(
        self, html: str, profile_url: str, nitter_base: str
    ) -> list[SocialPost]:
        """Parse tweets from Nitter HTML."""
        soup = BeautifulSoup(html, "lxml")
        posts = []

        # Nitter uses .timeline-item for each tweet
        items = soup.find_all("div", class_="timeline-item")

        for item in items[:10]:
            content_div = item.find("div", class_="tweet-content")
            if not content_div:
                continue

            text = content_div.get_text(separator=" ", strip=True)
            if not text:
                continue

            # Extract tweet permalink
            link_tag = item.find("a", class_="tweet-link")
            post_url = ""
            if link_tag and link_tag.get("href"):
                # Convert Nitter link back to twitter.com
                post_url = (
                    "https://twitter.com" + link_tag["href"]
                )

            # Extract timestamp
            time_tag = item.find("span", class_="tweet-date")
            post_date = None
            if time_tag:
                a_tag = time_tag.find("a")
                if a_tag and a_tag.get("title"):
                    import contextlib

                    with contextlib.suppress(ValueError):
                        post_date = datetime.strptime(
                            a_tag["title"], "%b %d, %Y · %I:%M %p %Z"
                        )

            posts.append(
                SocialPost(
                    platform=self.platform,
                    profile_url=profile_url,
                    post_text=text[:2000],
                    post_url=post_url,
                    post_date=post_date,
                    hashtags=re.findall(r"#(\w+)", text),
                )
            )

        return posts

    @staticmethod
    def _extract_username(url: str) -> str:
        """Extract username from a Twitter/X URL."""
        patterns = [
            r"(?:twitter|x)\.com/(@?\w+)",
            r"nitter\.\w+/(@?\w+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1).lstrip("@")
        return ""


class SocialMonitor:
    """Orchestrates social media scraping across all platforms.

    Usage::

        monitor = SocialMonitor()
        results = await monitor.collect_all(social_sources)
        for result in results:
            leads = extractor.extract(result)
    """

    def __init__(self):
        self.scrapers: dict[str, BaseSocialScraper] = {
            "facebook": FacebookScraper(),
            "instagram": InstagramScraper(),
            "linkedin": LinkedInScraper(),
            "x_twitter": XTwitterScraper(),
        }

    def detect_platform(self, url: str) -> str | None:
        """Detect which platform a URL belongs to."""
        domain_map = {
            "facebook.com": "facebook",
            "fb.com": "facebook",
            "instagram.com": "instagram",
            "linkedin.com": "linkedin",
            "twitter.com": "x_twitter",
            "x.com": "x_twitter",
        }
        url_lower = url.lower()
        for domain, platform in domain_map.items():
            if domain in url_lower:
                return platform
        return None

    async def scrape_url(self, url: str) -> FetchResult:
        """Scrape a single social media URL."""
        platform = self.detect_platform(url)
        if not platform:
            logger.warning(f"Unknown social platform for {url}")
            return FetchResult(
                source_id=0,
                url=url,
                error=f"Unknown social platform: {url}",
            )

        scraper = self.scrapers[platform]

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20,
        ) as client:
            posts = await scraper.scrape_profile(url, client)
            return scraper.posts_to_fetch_result(url, posts)

    async def collect_all(
        self,
        social_urls: list[str],
        max_concurrent: int = 5,
    ) -> list[FetchResult]:
        """Scrape multiple social media URLs with concurrency control.

        Args:
            social_urls: List of social media profile/page URLs.
            max_concurrent: Max concurrent scrape requests.

        Returns:
            List of FetchResult objects for the lead pipeline.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        results = []

        async def _scrape(url: str) -> FetchResult:
            async with semaphore:
                result = await self.scrape_url(url)
                # Be polite between requests
                await asyncio.sleep(2.0)
                return result

        tasks = [_scrape(url) for url in social_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        fetch_results = []
        for r in results:
            if isinstance(r, FetchResult):
                fetch_results.append(r)
            elif isinstance(r, Exception):
                logger.error(f"Social scrape exception: {r}")

        logger.info(
            f"Social monitor collected {len(fetch_results)} "
            f"results from {len(social_urls)} URLs"
        )
        return fetch_results

    async def keyword_search(
        self, keywords: list[str]
    ) -> list[FetchResult]:
        """Search X/Twitter for dealer-related keywords.

        This is an active search rather than monitoring known profiles.
        Useful for discovering new dealers/openings from the firehose.
        """
        x_scraper = self.scrapers["x_twitter"]
        if not isinstance(x_scraper, XTwitterScraper):
            return []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20,
        ) as client:
            posts = await x_scraper.search_keywords(keywords, client)
            return [
                x_scraper.posts_to_fetch_result("x_twitter_search", posts)
            ]
