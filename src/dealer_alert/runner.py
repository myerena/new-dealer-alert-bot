"""Orchestrator — runs the full pipeline: crawl → email → social → expand → digest.

This is the main entry point for scheduled/automated runs. It chains all
collection methods together and produces a daily digest at the end.

Can be run directly:
    python -m dealer_alert.runner

Or via the CLI:
    dealer-alert run-all

Supports --dry-run for safe testing.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .db import Database
from .expander import ExpansionEngine
from .extractor import LeadExtractor
from .fetcher import FetchManager
from .models import FetchResult, Source
from .output import DigestGenerator

logger = logging.getLogger(__name__)


class PipelineStats:
    """Tracks statistics across the full pipeline run."""

    def __init__(self):
        self.started_at = datetime.utcnow()
        self.sources_crawled = 0
        self.emails_processed = 0
        self.social_profiles_checked = 0
        self.leads_found = 0
        self.new_sources_discovered = 0
        self.errors = 0
        self.digest_files: list[str] = []

    @property
    def duration_seconds(self) -> float:
        return (datetime.utcnow() - self.started_at).total_seconds()

    def summary(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 1),
            "sources_crawled": self.sources_crawled,
            "emails_processed": self.emails_processed,
            "social_profiles_checked": self.social_profiles_checked,
            "leads_found": self.leads_found,
            "new_sources_discovered": self.new_sources_discovered,
            "errors": self.errors,
            "digest_files": self.digest_files,
        }

    def log_summary(self):
        logger.info("=" * 60)
        logger.info("PIPELINE RUN COMPLETE")
        logger.info(f"  Duration: {self.duration_seconds:.1f}s")
        logger.info(f"  Sources crawled: {self.sources_crawled}")
        logger.info(f"  Emails processed: {self.emails_processed}")
        logger.info(f"  Social profiles: {self.social_profiles_checked}")
        logger.info(f"  Leads found: {self.leads_found}")
        logger.info(f"  New sources discovered: {self.new_sources_discovered}")
        logger.info(f"  Errors: {self.errors}")
        for f in self.digest_files:
            logger.info(f"  Digest: {f}")
        logger.info("=" * 60)


def run_pipeline(
    config: Config | None = None,
    crawl_limit: int = 100,
    email_hours: int = 24,
    dry_run: bool = False,
) -> PipelineStats:
    """Run the full collection and reporting pipeline.

    Steps:
        1. Web crawl — fetch registered sources, extract leads
        2. Email check — poll Gmail inbox for newsletter content
        3. Social check — scrape social media profiles
        4. Expand — promote discovered sources to registry
        5. Digest — generate CSV + JSON report

    Args:
        config: App config. Loads from .env if None.
        crawl_limit: Max sources to crawl per run.
        email_hours: Only process emails from the last N hours.
        dry_run: Run without side effects.

    Returns:
        PipelineStats with run summary.
    """
    if config is None:
        config = Config.load()

    # Set up logging
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if config.log_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config.log_file))

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    stats = PipelineStats()
    db = Database(config.database_path)
    db.init_schema()

    logger.info(f"Starting pipeline run (dry_run={dry_run}, crawl_limit={crawl_limit})")

    # Step 1: Web crawl
    try:
        _step_crawl(config, db, stats, crawl_limit, dry_run)
    except Exception as exc:
        logger.error(f"Crawl step failed: {exc}")
        stats.errors += 1

    # Step 2: Email check
    try:
        _step_email(config, db, stats, email_hours, dry_run)
    except Exception as exc:
        logger.error(f"Email step failed: {exc}")
        stats.errors += 1

    # Step 3: Social check
    try:
        _step_social(config, db, stats, dry_run)
    except Exception as exc:
        logger.error(f"Social step failed: {exc}")
        stats.errors += 1

    # Step 4: Promote discovered sources
    try:
        _step_promote_sources(db, stats, config, dry_run)
    except Exception as exc:
        logger.error(f"Source promotion failed: {exc}")
        stats.errors += 1

    # Step 5: Generate digest
    try:
        _step_digest(config, db, stats, email_hours, dry_run)
    except Exception as exc:
        logger.error(f"Digest step failed: {exc}")
        stats.errors += 1

    stats.log_summary()
    return stats


def _step_crawl(
    config: Config,
    db: Database,
    stats: PipelineStats,
    limit: int,
    dry_run: bool,
):
    """Step 1: Crawl registered web sources."""
    logger.info("Step 1/5: Web crawl")
    sources = db.get_sources_due(limit=limit)

    if not sources:
        logger.info("No sources due for crawling")
        return

    logger.info(f"Crawling {len(sources)} sources")

    fetch_mgr = FetchManager(config)
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    expander = ExpansionEngine(config, db)

    log_id = db.start_crawl_log() if not dry_run else 0

    # Collect all fetch results first (async), then process/write (sync)
    # This avoids SQLite concurrency issues on Windows
    collected_results: list[FetchResult] = []

    async def _crawl():
        async for result in fetch_mgr.fetch_sources(sources, dry_run=dry_run):
            collected_results.append(result)

    asyncio.run(_crawl())

    # Now process results and write to DB synchronously
    for result in collected_results:
        stats.sources_crawled += 1

        if result.error:
            stats.errors += 1
            if not dry_run:
                db.update_source_fetched(result.source_id, "", error=result.error)
            continue

        if not dry_run:
            db.update_source_fetched(result.source_id, result.content_hash)

        leads = extractor.extract(result)
        for lead in leads:
            if not dry_run:
                db.add_lead(lead)
            stats.leads_found += 1

        for lead in leads:
            discovered = expander.expand_from_lead(lead)
            for ds in discovered:
                if not dry_run:
                    db.add_discovered_source(ds)
                stats.new_sources_discovered += 1

        if result.links:
            link_discovered = expander.expand_from_links(result.source_id, result.links)
            for ds in link_discovered:
                if not dry_run:
                    db.add_discovered_source(ds)
                stats.new_sources_discovered += 1

    if not dry_run and log_id:
        db.finish_crawl_log(
            log_id,
            stats.sources_crawled,
            stats.leads_found,
            stats.new_sources_discovered,
            stats.errors,
        )

    logger.info(f"Crawl complete: {stats.sources_crawled} sources, {stats.leads_found} leads")

    # Retry failed sources (403s) with browser fetcher
    failed_sources = [s for s in sources if s.fetch_error_count > 0 and s.fetch_error_count < 10]
    if failed_sources and not dry_run:
        _step_browser_retry(config, db, stats, failed_sources)


def _step_browser_retry(
    config: Config,
    db: Database,
    stats: PipelineStats,
    failed_sources: list[Source],
):
    """Retry failed sources using headless browser (handles 403s)."""
    try:
        from .fetcher.browser import BrowserFetchManager
    except ImportError:
        logger.info("Playwright not available — skipping browser retry")
        return

    logger.info(f"Retrying {len(failed_sources)} failed sources with browser fetcher")

    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    manager = BrowserFetchManager(headless=True, max_concurrent=2)

    urls_with_ids = [(s.id or 0, s.url) for s in failed_sources]

    try:
        results = asyncio.run(manager.fetch_urls(urls_with_ids))
    except Exception as exc:
        logger.error(f"Browser fetch batch failed: {exc}")
        return

    for result in results:
        if result.error:
            logger.debug(f"Browser retry failed: {result.url}: {result.error[:80]}")
            continue

        stats.sources_crawled += 1
        db.update_source_fetched(result.source_id, result.content_hash)

        leads = extractor.extract(result)
        for lead in leads:
            db.add_lead(lead)
            stats.leads_found += 1

    browser_leads = sum(len(extractor.extract(r)) for r in results if not r.error)
    logger.info(f"Browser retry: {len(results)} sources, {browser_leads} additional leads")


def _step_email(
    config: Config,
    db: Database,
    stats: PipelineStats,
    hours: int,
    dry_run: bool,
):
    """Step 2: Check Gmail inbox for newsletter content."""
    logger.info("Step 2/5: Email check")

    if not config.gmail_credentials_file.exists():
        logger.info("Gmail not configured — skipping email step")
        return

    from .collectors import EmailCollector
    from .models import SourceCategory, SourceType

    # Ensure an "Email Inbox" source exists so email leads
    # have a valid source_id (avoids FOREIGN KEY errors)
    email_source_url = f"email://{config.email_address}"
    if not db.source_exists(email_source_url):
        db.add_source(
            Source(
                url=email_source_url,
                source_type=SourceType.HTML,
                category=SourceCategory.OTHER,
                name="Email Inbox",
                geography="national",
                priority=1,
                notes="Virtual source for email newsletter leads",
            )
        )

    # Look up the email source ID
    email_sources = [s for s in db.get_all_sources() if s.url == email_source_url]
    email_source_id = email_sources[0].id if email_sources else 0

    collector = EmailCollector(
        credentials_file=config.gmail_credentials_file,
        token_file=config.gmail_token_file,
        email_address=config.email_address,
        max_emails=config.email_max_per_run,
    )

    since = datetime.utcnow() - timedelta(hours=hours)
    results = collector.collect(
        since_date=since,
        mark_read=config.email_mark_read and not dry_run,
        dry_run=dry_run,
    )

    stats.emails_processed = len(results)
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)

    for result in results:
        # Assign the email source ID to each result
        result.source_id = email_source_id
        leads = extractor.extract(result)
        for lead in leads:
            lead.source_id = email_source_id
            if not dry_run:
                db.add_lead(lead)
            stats.leads_found += 1

    logger.info(f"Email check complete: {stats.emails_processed} emails processed")


def _step_social(
    config: Config,
    db: Database,
    stats: PipelineStats,
    dry_run: bool,
):
    """Step 3: Scrape social media profiles."""
    logger.info("Step 3/5: Social check")

    all_sources = db.get_all_sources(enabled_only=True)
    social_urls = [s.url for s in all_sources if s.source_type.value == "social"]

    if not social_urls:
        logger.info("No social sources registered — skipping")
        return

    from .collectors import SocialMonitor

    monitor = SocialMonitor()
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)

    async def _social():
        results = await monitor.collect_all(
            social_urls,
            max_concurrent=config.social_max_concurrent,
        )
        for result in results:
            stats.social_profiles_checked += 1
            if result.error:
                stats.errors += 1
                continue
            leads = extractor.extract(result)
            for lead in leads:
                if not dry_run:
                    db.add_lead(lead)
                stats.leads_found += 1

    asyncio.run(_social())

    logger.info(f"Social check complete: {stats.social_profiles_checked} profiles")


def _step_promote_sources(
    db: Database,
    stats: PipelineStats,
    config: Config,
    dry_run: bool,
):
    """Step 4: Promote discovered sources to the registry."""
    logger.info("Step 4/5: Promote discovered sources")

    unprocessed = db.get_unprocessed_discovered(limit=config.max_new_sources_per_crawl)

    promoted = 0
    for disc in unprocessed:
        url = disc["url"]
        if not db.source_exists(url):
            from .models import SourceCategory, SourceType

            source = Source(
                url=url,
                source_type=SourceType(disc.get("source_type", "html")),
                category=SourceCategory(disc.get("category", "other")),
                geography=disc.get("geography", ""),
                parent_source_id=disc.get("discovered_from_source_id"),
                notes=f"Auto-discovered: {disc.get('reason', '')}",
                priority=7,  # Lower priority than manual sources
            )
            if not dry_run:
                db.add_source(source)
            promoted += 1

        if not dry_run:
            db.mark_discovered_processed(disc["id"])

    logger.info(f"Promoted {promoted} new sources to registry")


def _step_digest(
    config: Config,
    db: Database,
    stats: PipelineStats,
    hours: int,
    dry_run: bool,
):
    """Step 5: Generate daily digest."""
    logger.info("Step 5/5: Generate digest")

    generator = DigestGenerator(config, db)
    since = datetime.utcnow() - timedelta(hours=hours)
    summary = generator.generate(since=since, dry_run=dry_run)

    stats.digest_files = summary.get("files", [])

    logger.info(
        f"Digest: {summary.get('total_leads', 0)} leads "
        f"({summary.get('hot', 0)} hot, "
        f"{summary.get('warm', 0)} warm, "
        f"{summary.get('cold', 0)} cold)"
    )


# Allow direct execution: python -m dealer_alert.runner
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the dealer alert pipeline")
    parser.add_argument("--dry-run", action="store_true", help="No side effects")
    parser.add_argument("--crawl-limit", type=int, default=100)
    parser.add_argument("--email-hours", type=int, default=24)
    parser.add_argument("--env-file", type=str, default=None)
    args = parser.parse_args()

    cfg = Config.load(Path(args.env_file) if args.env_file else None)
    run_pipeline(
        config=cfg,
        crawl_limit=args.crawl_limit,
        email_hours=args.email_hours,
        dry_run=args.dry_run,
    )
