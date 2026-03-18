"""Browser-based fetcher — uses Playwright to fetch pages that block bots.

Some sites (NADA, AutoNation, Penske, etc.) return 403 to standard HTTP
requests because they detect non-browser User-Agents or missing JS
execution. This fetcher spins up a headless Chromium instance to fetch
pages as a real browser would.

Usage:
    The pipeline automatically falls back to this fetcher when a source
    has accumulated fetch errors (403s). It can also be used directly:

        fetcher = BrowserFetcher()
        result = await fetcher.fetch(source_id, url)

Requires Playwright browsers to be installed:
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..models import FetchResult
from .base import BaseFetcher, content_hash

logger = logging.getLogger(__name__)

# How long to wait for page load (ms)
DEFAULT_TIMEOUT = 30000
# Wait for network to settle after page load
NETWORK_IDLE_TIMEOUT = 5000


class BrowserFetcher(BaseFetcher):
    """Fetches pages using a headless Chromium browser via Playwright.

    Handles sites that block standard HTTP requests with:
    - Full JavaScript execution
    - Real browser fingerprint
    - Cookie handling
    - Redirect following
    """

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = DEFAULT_TIMEOUT,
    ):
        self.headless = headless
        self.timeout_ms = timeout_ms

    async def fetch(
        self, source_id: int, url: str, client=None, **kwargs
    ) -> FetchResult:
        """Fetch a page using headless Chromium.

        The `client` parameter is ignored — Playwright manages its own
        connections. It's kept for interface compatibility with BaseFetcher.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return self._make_result(
                source_id=source_id,
                url=url,
                error=(
                    "Playwright not installed. Run: "
                    "pip install playwright && playwright install chromium"
                ),
            )

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=self.headless)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                )

                page = await context.new_page()

                # Navigate and wait for network to settle
                response = await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=self.timeout_ms,
                )

                status_code = response.status if response else 0

                # Wait a bit more for dynamic content
                await page.wait_for_timeout(2000)

                # Extract text content from the page
                text = await page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )

                # Extract all links
                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.href)
                        .filter(href => href.startsWith('http'))
                """)

                await browser.close()

                logger.debug(
                    f"Browser fetch {url}: {status_code}, "
                    f"{len(text)} chars, {len(links)} links"
                )

                return self._make_result(
                    source_id=source_id,
                    url=url,
                    status_code=status_code,
                    content=text,
                    links=links,
                )

        except Exception as exc:
            logger.warning(f"Browser fetch failed for {url}: {exc}")
            return self._make_result(
                source_id=source_id,
                url=url,
                error=str(exc),
            )


class BrowserFetchManager:
    """Manages browser-based fetching for multiple URLs.

    Uses a single browser instance with multiple pages for efficiency.
    """

    def __init__(
        self,
        headless: bool = True,
        max_concurrent: int = 3,
        timeout_ms: int = DEFAULT_TIMEOUT,
    ):
        self.headless = headless
        self.max_concurrent = max_concurrent
        self.timeout_ms = timeout_ms

    async def fetch_urls(
        self,
        urls_with_ids: list[tuple[int, str]],
    ) -> list[FetchResult]:
        """Fetch multiple URLs using a shared browser instance.

        Args:
            urls_with_ids: List of (source_id, url) tuples.

        Returns:
            List of FetchResult objects.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error(
                "Playwright not installed. Run: "
                "pip install playwright && playwright install chromium"
            )
            return [
                FetchResult(
                    source_id=sid,
                    url=url,
                    error="Playwright not installed",
                )
                for sid, url in urls_with_ids
            ]

        results = []
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)

            async def _fetch_one(source_id: int, url: str) -> FetchResult:
                async with semaphore:
                    context = await browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        viewport={"width": 1920, "height": 1080},
                        locale="en-US",
                    )

                    try:
                        page = await context.new_page()
                        response = await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=self.timeout_ms,
                        )

                        status = response.status if response else 0
                        await page.wait_for_timeout(2000)

                        text = await page.evaluate(
                            "() => document.body ? document.body.innerText : ''"
                        )
                        links = await page.evaluate("""
                            () => Array.from(document.querySelectorAll('a[href]'))
                                .map(a => a.href)
                                .filter(href => href.startsWith('http'))
                        """)

                        logger.debug(
                            f"Browser fetch {url}: {status}, "
                            f"{len(text)} chars"
                        )

                        return FetchResult(
                            source_id=source_id,
                            url=url,
                            status_code=status,
                            content=text,
                            content_hash=content_hash(text),
                            links=links,
                            fetched_at=datetime.utcnow(),
                        )

                    except Exception as exc:
                        logger.warning(
                            f"Browser fetch failed for {url}: {exc}"
                        )
                        return FetchResult(
                            source_id=source_id,
                            url=url,
                            error=str(exc),
                        )
                    finally:
                        await context.close()
                        # Polite delay
                        await asyncio.sleep(2.0)

            tasks = [
                _fetch_one(sid, url) for sid, url in urls_with_ids
            ]
            results = await asyncio.gather(*tasks)

            await browser.close()

        return list(results)
