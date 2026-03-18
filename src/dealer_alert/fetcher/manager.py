"""Fetch orchestration — coordinates fetching across all source types."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from ..config import Config
from ..models import FetchResult, Source, SourceType
from .html import HTMLFetcher
from .rss import RSSFetcher
from .sitemap import SitemapFetcher

logger = logging.getLogger(__name__)


class FetchManager:
    """Orchestrates fetching across multiple sources with concurrency control."""

    def __init__(self, config: Config):
        self.config = config
        self._fetchers = {
            SourceType.RSS: RSSFetcher(),
            SourceType.HTML: HTMLFetcher(),
            SourceType.SITEMAP: SitemapFetcher(),
            # Calendar, directory, social, job_board all use HTML fetcher as fallback
        }
        self._default_fetcher = HTMLFetcher()

    def _get_fetcher(self, source_type: SourceType):
        return self._fetchers.get(source_type, self._default_fetcher)

    async def fetch_sources(
        self, sources: list[Source], dry_run: bool = False
    ) -> AsyncIterator[FetchResult]:
        """Fetch a batch of sources with concurrency control.

        Yields FetchResult objects as they complete.
        """
        if dry_run:
            logger.info(f"[DRY RUN] Would fetch {len(sources)} sources")
            for source in sources:
                yield FetchResult(
                    source_id=source.id or 0,
                    url=source.url,
                    content=f"[DRY RUN] Would fetch {source.url}",
                )
            return

        semaphore = asyncio.Semaphore(self.config.max_concurrent_fetches)

        async with httpx.AsyncClient(
            timeout=self.config.fetch_timeout_seconds,
            follow_redirects=True,
            max_redirects=self.config.max_redirects,
            headers={"User-Agent": self.config.user_agent},
        ) as client:
            tasks = []
            for source in sources:
                task = self._fetch_one(semaphore, source, client)
                tasks.append(task)

            for coro in asyncio.as_completed(tasks):
                result = await coro
                yield result

    async def _fetch_one(
        self, semaphore: asyncio.Semaphore, source: Source, client: httpx.AsyncClient
    ) -> FetchResult:
        """Fetch a single source with rate limiting."""
        async with semaphore:
            fetcher = self._get_fetcher(source.source_type)
            logger.debug(f"Fetching {source.url} ({source.source_type.value})")
            try:
                result = await fetcher.fetch(source.id or 0, source.url, client)
            except Exception as e:
                logger.error(f"Unexpected error fetching {source.url}: {e}")
                result = FetchResult(
                    source_id=source.id or 0,
                    url=source.url,
                    error=str(e),
                )
            # Polite delay between requests
            await asyncio.sleep(self.config.crawl_delay_seconds)
            return result

    async def fetch_single(
        self, url: str, source_type: SourceType = SourceType.HTML
    ) -> FetchResult:
        """Convenience method to fetch a single URL outside of the normal pipeline."""
        async with httpx.AsyncClient(
            timeout=self.config.fetch_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.config.user_agent},
        ) as client:
            fetcher = self._get_fetcher(source_type)
            return await fetcher.fetch(0, url, client)
