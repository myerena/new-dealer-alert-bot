"""CLI entry point for the Dealer Alert Bot.

Commands:
    init          Initialize database and load seed sources
    crawl         Run a crawl cycle across registered sources
    digest        Generate a lead digest from recent crawl data
    add-source    Manually add a source URL to the registry
    status        Show current registry and lead stats
    check-email   Poll the inbox for newsletter content
    check-social  Scrape social media profiles for lead signals
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import Config
from .db import Database
from .expander import ExpansionEngine
from .extractor import LeadExtractor
from .fetcher import FetchManager
from .models import Source, SourceCategory, SourceType
from .output import DigestGenerator

console = Console()


def _setup_logging(config: Config):
    """Configure logging based on config settings."""
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.log_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config.log_file))

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


@click.group()
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file")
@click.pass_context
def main(ctx, env_file):
    """New Dealer Alert Bot — high-recall lead radar for dealership activity."""
    config = Config.load(Path(env_file) if env_file else None)
    _setup_logging(config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["db"] = Database(config.database_path)


@main.command()
@click.option(
    "--seeds",
    type=click.Path(exists=True),
    default=None,
    help="Path to seed sources JSON",
)
@click.pass_context
def init(ctx, seeds):
    """Initialize the database and optionally load seed sources."""
    db: Database = ctx.obj["db"]

    console.print("[bold]Initializing database...[/bold]")
    db.init_schema()
    console.print("[green]Database schema created.[/green]")

    # Load seed sources
    seeds_path = Path(seeds) if seeds else Path("seeds/initial_sources.json")
    if seeds_path.exists():
        with open(seeds_path) as f:
            seed_data = json.load(f)

        count = 0
        for entry in seed_data:
            source = Source(
                url=entry["url"],
                source_type=SourceType(entry.get("source_type", "html")),
                category=SourceCategory(entry.get("category", "other")),
                name=entry.get("name", ""),
                geography=entry.get("geography", ""),
                priority=entry.get("priority", 5),
                notes=entry.get("notes", ""),
            )
            source_id = db.add_source(source)
            if source_id:
                count += 1

        console.print(f"[green]Loaded {count} seed sources from {seeds_path}[/green]")
    else:
        console.print(f"[yellow]No seed file found at {seeds_path}. Add sources manually.[/yellow]")

    total = db.get_source_count()
    console.print(f"[bold]Registry now has {total} sources.[/bold]")


@main.command()
@click.option("--limit", default=100, help="Max sources to crawl this cycle")
@click.option("--dry-run", is_flag=True, help="Show what would be fetched without fetching")
@click.pass_context
def crawl(ctx, limit, dry_run):
    """Run a crawl cycle across registered sources."""
    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]

    db.init_schema()
    sources = db.get_sources_due(limit=limit)

    if not sources:
        console.print("[yellow]No sources due for crawling.[/yellow]")
        return

    dry_run_label = "  [DRY RUN]" if dry_run else ""
    console.print(f"[bold]Crawling {len(sources)} sources{dry_run_label}...[/bold]")

    asyncio.run(_run_crawl(config, db, sources, dry_run))


async def _run_crawl(config: Config, db: Database, sources: list[Source], dry_run: bool):
    """Execute the crawl pipeline: fetch → extract → expand → store."""
    fetch_mgr = FetchManager(config)
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    expander = ExpansionEngine(config, db)

    log_id = db.start_crawl_log() if not dry_run else 0
    stats = {"crawled": 0, "leads": 0, "new_sources": 0, "errors": 0}

    async for result in fetch_mgr.fetch_sources(sources, dry_run=dry_run):
        stats["crawled"] += 1

        if result.error:
            stats["errors"] += 1
            if not dry_run:
                db.update_source_fetched(result.source_id, "", error=result.error)
            console.print(f"  [red]ERROR[/red] {result.url}: {result.error[:80]}")
            continue

        if not dry_run:
            db.update_source_fetched(result.source_id, result.content_hash)

        # Extract leads
        leads = extractor.extract(result)
        for lead in leads:
            if not dry_run:
                db.add_lead(lead)
            stats["leads"] += 1
            score_color = {"hot": "red", "warm": "yellow", "cold": "blue"}[lead.score.value]
            console.print(
                f"  [{score_color}]{lead.score.value.upper()}[/{score_color}] "
                f"{lead.dealer_name or 'Unknown'} — {lead.title[:60]} "
                f"({lead.city}, {lead.state})"
                if lead.city
                else f"  [{score_color}]{lead.score.value.upper()}[/{score_color}] "
                f"{lead.dealer_name or 'Unknown'} — {lead.title[:60]}"
            )

        # Expand: discover new sources from leads and links
        for lead in leads:
            discovered = expander.expand_from_lead(lead)
            for ds in discovered:
                if not dry_run:
                    db.add_discovered_source(ds)
                stats["new_sources"] += 1

        # Also expand from outbound links
        if result.links:
            link_discovered = expander.expand_from_links(result.source_id, result.links)
            for ds in link_discovered:
                if not dry_run:
                    db.add_discovered_source(ds)
                stats["new_sources"] += 1

    if not dry_run and log_id:
        db.finish_crawl_log(
            log_id, stats["crawled"], stats["leads"], stats["new_sources"], stats["errors"]
        )

    console.print()
    console.print("[bold]Crawl complete:[/bold]")
    console.print(f"  Sources crawled: {stats['crawled']}")
    console.print(f"  Leads found: {stats['leads']}")
    console.print(f"  New sources discovered: {stats['new_sources']}")
    console.print(f"  Errors: {stats['errors']}")


@main.command()
@click.option("--hours", default=24, help="Include leads from the last N hours")
@click.option("--dry-run", is_flag=True, help="Show stats without writing files")
@click.pass_context
def digest(ctx, hours, dry_run):
    """Generate a lead digest from recent crawl data."""
    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    generator = DigestGenerator(config, db)
    since = datetime.utcnow() - timedelta(hours=hours)
    summary = generator.generate(since=since, dry_run=dry_run)

    console.print("\n[bold]Digest Summary:[/bold]")
    console.print(f"  Total leads: {summary.get('total_leads', summary.get('leads', 0))}")
    console.print(f"  Hot: {summary.get('hot', 0)}")
    console.print(f"  Warm: {summary.get('warm', 0)}")
    console.print(f"  Cold: {summary.get('cold', 0)}")

    for f in summary.get("files", []):
        console.print(f"  [green]Written:[/green] {f}")


@main.command("add-source")
@click.argument("url")
@click.option(
    "--type",
    "source_type",
    default="html",
    help="Source type: rss, html, sitemap",
)
@click.option(
    "--category",
    default="other",
    help="Category: trade_media, chamber, dealer_group, etc.",
)
@click.option("--name", default="", help="Friendly name for the source")
@click.option(
    "--geography",
    default="",
    help="Geographic scope (e.g., 'national', 'TX', 'Dallas, TX')",
)
@click.option("--priority", default=5, help="Priority 1-10 (1 = highest)")
@click.pass_context
def add_source(ctx, url, source_type, category, name, geography, priority):
    """Manually add a source URL to the registry."""
    db: Database = ctx.obj["db"]
    db.init_schema()

    source = Source(
        url=url,
        source_type=SourceType(source_type),
        category=SourceCategory(category),
        name=name,
        geography=geography,
        priority=priority,
    )

    if db.source_exists(url):
        console.print(f"[yellow]Source already exists: {url}[/yellow]")
        return

    source_id = db.add_source(source)
    console.print(f"[green]Added source #{source_id}: {url}[/green]")


@main.command()
@click.pass_context
def status(ctx):
    """Show current registry and lead stats."""
    db: Database = ctx.obj["db"]
    db.init_schema()

    sources = db.get_all_sources(enabled_only=False)
    total_leads = db.get_lead_count()
    recent_leads = db.get_lead_count(since=datetime.utcnow() - timedelta(hours=24))

    console.print("\n[bold]Dealer Alert Bot Status[/bold]")
    console.print(f"  Total sources: {len(sources)}")
    console.print(f"  Enabled sources: {sum(1 for s in sources if s.enabled)}")
    console.print(f"  Total leads: {total_leads}")
    console.print(f"  Leads (last 24h): {recent_leads}")

    # Source breakdown by category
    if sources:
        table = Table(title="Sources by Category")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Enabled", justify="right")

        from collections import Counter

        cat_counts = Counter(s.category.value for s in sources)
        cat_enabled = Counter(s.category.value for s in sources if s.enabled)

        for cat, count in cat_counts.most_common():
            table.add_row(cat, str(count), str(cat_enabled.get(cat, 0)))

        console.print(table)


@main.command("check-email")
@click.option(
    "--hours",
    default=24,
    help="Only fetch emails from the last N hours",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Connect and count but don't download",
)
@click.pass_context
def check_email(ctx, hours, dry_run):
    """Poll the Gmail inbox for newsletter content and extract leads."""
    from .collectors import EmailCollector

    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    if not config.gmail_credentials_file.exists():
        console.print(
            "[red]Gmail not configured.[/red] "
            "Place your client_secret*.json in the project "
            "root and set GMAIL_CREDENTIALS_FILE in .env"
        )
        return

    collector = EmailCollector(
        credentials_file=config.gmail_credentials_file,
        token_file=config.gmail_token_file,
        email_address=config.email_address,
        max_emails=config.email_max_per_run,
    )

    since = datetime.utcnow() - timedelta(hours=hours)
    console.print(f"[bold]Checking inbox ({config.email_address})...[/bold]")

    results = collector.collect(
        since_date=since,
        mark_read=config.email_mark_read and not dry_run,
        dry_run=dry_run,
    )

    if not results:
        console.print("[yellow]No new emails found.[/yellow]")
        return

    # Run through the lead extraction pipeline
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    total_leads = 0

    for result in results:
        leads = extractor.extract(result)
        for lead in leads:
            if not dry_run:
                db.add_lead(lead)
            total_leads += 1
            score_color = {"hot": "red", "warm": "yellow", "cold": "blue"}[lead.score.value]
            console.print(
                f"  [{score_color}]{lead.score.value.upper()}"
                f"[/{score_color}] "
                f"{lead.dealer_name or 'Unknown'} — "
                f"{lead.title[:60]}"
            )

    console.print(
        f"\n[bold]Email check complete:[/bold] {len(results)} emails → {total_leads} leads"
    )


@main.command("check-social")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be scraped without scraping",
)
@click.option(
    "--url",
    multiple=True,
    help="Specific social URL(s) to scrape (repeatable)",
)
@click.option(
    "--search",
    "search_keywords",
    default="",
    help="Search X/Twitter for these keywords (comma-separated)",
)
@click.pass_context
def check_social(ctx, dry_run, url, search_keywords):
    """Scrape social media profiles for lead signals."""
    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    if dry_run:
        if url:
            console.print(f"[DRY RUN] Would scrape {len(url)} social URLs")
        if search_keywords:
            console.print(f"[DRY RUN] Would search X for: {search_keywords}")
        return

    # Gather URLs from CLI args and from DB (social source type)
    social_urls = list(url)

    # Also pull social-type sources from the registry
    all_sources = db.get_all_sources(enabled_only=True)
    for source in all_sources:
        if source.source_type.value == "social":
            social_urls.append(source.url)

    if not social_urls and not search_keywords:
        console.print(
            "[yellow]No social URLs provided and none "
            "in registry.[/yellow] "
            "Use --url or add sources with type 'social'."
        )
        return

    console.print(f"[bold]Checking {len(social_urls)} social profiles...[/bold]")

    asyncio.run(_run_social_check(config, db, social_urls, search_keywords))


async def _run_social_check(
    config: Config,
    db: Database,
    social_urls: list[str],
    search_keywords: str,
):
    """Run social media scraping and lead extraction."""
    from .collectors import SocialMonitor

    monitor = SocialMonitor()
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    total_leads = 0

    # Scrape known profiles
    if social_urls:
        results = await monitor.collect_all(
            social_urls,
            max_concurrent=config.social_max_concurrent,
        )

        for result in results:
            if result.error:
                console.print(f"  [red]ERROR[/red] {result.url}: {result.error[:80]}")
                continue

            leads = extractor.extract(result)
            for lead in leads:
                db.add_lead(lead)
                total_leads += 1
                score_color = {"hot": "red", "warm": "yellow", "cold": "blue"}[lead.score.value]
                console.print(
                    f"  [{score_color}]{lead.score.value.upper()}"
                    f"[/{score_color}] "
                    f"{lead.dealer_name or 'Unknown'} — "
                    f"{lead.title[:60]}"
                )

    # Keyword search on X/Twitter
    if search_keywords:
        keywords = [kw.strip() for kw in search_keywords.split(",")]
        console.print(f"[bold]Searching X for: {', '.join(keywords)}[/bold]")
        search_results = await monitor.keyword_search(keywords)
        for result in search_results:
            leads = extractor.extract(result)
            for lead in leads:
                db.add_lead(lead)
                total_leads += 1

    console.print(f"\n[bold]Social check complete:[/bold] {total_leads} leads found")


@main.command("find-newsletters")
@click.option(
    "--limit",
    default=50,
    help="Max source URLs to scan for signup forms",
)
@click.option(
    "--subscribe",
    is_flag=True,
    help="Actually submit signup forms (default: discover only)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be submitted without submitting",
)
@click.pass_context
def find_newsletters(ctx, limit, subscribe, dry_run):
    """Discover newsletter signup forms across registered sources.

    By default, only discovers and reports forms. Use --subscribe
    to actually submit the email address to found forms.
    """
    from .collectors import AutoSubscriber

    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    email_addr = config.email_address
    if not email_addr:
        console.print("[red]No email configured.[/red] Set GMAIL_ADDRESS in .env")
        return

    sources = db.get_all_sources(enabled_only=True)
    urls = [s.url for s in sources[:limit]]

    if not urls:
        console.print("[yellow]No sources in registry.[/yellow]")
        return

    console.print(f"[bold]Scanning {len(urls)} sources for newsletter signups...[/bold]")

    subscriber = AutoSubscriber(email=email_addr)
    signups = asyncio.run(subscriber.discover_from_urls(urls))

    if not signups:
        console.print("[yellow]No signup forms found.[/yellow]")
        return

    # Display results
    table = Table(title="Discovered Newsletter Signups")
    table.add_column("Type", style="cyan", width=6)
    table.add_column("Confidence", justify="right", width=6)
    table.add_column("Source", width=30)
    table.add_column("Signup URL / Action", width=45)
    table.add_column("Context", width=30)

    for s in sorted(signups, key=lambda x: -x.confidence):
        conf_color = "green" if s.confidence >= 0.7 else "yellow" if s.confidence >= 0.4 else "red"
        table.add_row(
            s.signup_type,
            f"[{conf_color}]{s.confidence:.0%}[/{conf_color}]",
            _truncate(s.source_url, 30),
            _truncate(s.signup_url or s.form_action, 45),
            _truncate(s.context_text, 30),
        )

    console.print(table)

    rss_count = sum(1 for s in signups if s.signup_type == "rss")
    form_count = sum(1 for s in signups if s.signup_type in ("form", "esp"))
    link_count = sum(1 for s in signups if s.signup_type == "link")

    console.print(
        f"\n[bold]Found:[/bold] {rss_count} RSS feeds, "
        f"{form_count} signup forms, {link_count} signup links"
    )

    # Register discovered RSS feeds as sources
    rss_added = 0
    for s in signups:
        if s.signup_type == "rss" and not db.source_exists(s.signup_url):
            from .models import SourceType

            db.add_source(
                Source(
                    url=s.signup_url,
                    source_type=SourceType.RSS,
                    name=s.context_text[:50],
                    notes="Auto-discovered RSS feed",
                )
            )
            rss_added += 1

    if rss_added:
        console.print(f"[green]Added {rss_added} new RSS feeds to source registry[/green]")

    # Subscribe to forms if requested
    if subscribe and form_count > 0:
        console.print(f"\n[bold]Subscribing {email_addr} to {form_count} forms...[/bold]")
        results = asyncio.run(subscriber.subscribe_all(signups, dry_run=dry_run))

        success = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        console.print(f"[bold]Results:[/bold] {success} succeeded, {failed} failed/manual")

        for r in results:
            if r.error:
                console.print(
                    f"  [red]FAIL[/red] {_truncate(r.signup.form_action, 50)}: {r.error[:60]}"
                )
    elif form_count > 0 and not subscribe:
        console.print(
            "\n[dim]Run with --subscribe to auto-submit forms, "
            "or --subscribe --dry-run to preview.[/dim]"
        )


@main.command("browser-fetch")
@click.argument("url")
@click.option("--headless/--visible", default=True, help="Run browser headless or visible")
@click.pass_context
def browser_fetch(ctx, url, headless):
    """Fetch a single URL using headless browser (bypasses bot detection)."""
    from .fetcher.browser import BrowserFetcher

    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    console.print(f"[bold]Browser fetching: {url}[/bold]")
    fetcher = BrowserFetcher(headless=headless)
    result = asyncio.run(fetcher.fetch(0, url))

    if result.error:
        console.print(f"[red]Error:[/red] {result.error}")
        return

    console.print(f"  Status: {result.status_code}")
    console.print(f"  Content: {len(result.content)} chars")
    console.print(f"  Links: {len(result.links)}")

    # Run through lead extraction
    extractor = LeadExtractor(hot_min_mentions=config.hot_lead_min_mentions)
    leads = extractor.extract(result)

    if leads:
        for lead in leads:
            db.add_lead(lead)
            score_color = {
                "hot": "red", "warm": "yellow", "cold": "blue"
            }[lead.score.value]
            console.print(
                f"  [{score_color}]{lead.score.value.upper()}"
                f"[/{score_color}] "
                f"{lead.dealer_name or 'Unknown'} — "
                f"{lead.title[:60]}"
            )
        console.print(f"\n[bold]{len(leads)} leads extracted[/bold]")
    else:
        console.print("[yellow]No leads found in page content[/yellow]")


@main.command("report")
@click.option("--hours", default=24, help="Include leads from the last N hours")
@click.option("--open", "open_browser", is_flag=True, help="Open report in browser")
@click.pass_context
def report(ctx, hours, open_browser):
    """Generate an HTML dashboard report with charts and lead data."""
    from .output.report import generate_report

    config: Config = ctx.obj["config"]
    db: Database = ctx.obj["db"]
    db.init_schema()

    console.print(f"[bold]Generating report (last {hours} hours)...[/bold]")
    path = generate_report(config, db, since_hours=hours)
    console.print(f"[green]Report:[/green] {path}")

    if open_browser:
        import webbrowser

        webbrowser.open(str(path))


@main.command("run-all")
@click.option("--crawl-limit", default=100, help="Max sources to crawl")
@click.option("--email-hours", default=24, help="Email lookback window in hours")
@click.option("--dry-run", is_flag=True, help="Run without side effects")
@click.pass_context
def run_all(ctx, crawl_limit, email_hours, dry_run):
    """Run the full pipeline: crawl → email → social → expand → digest."""
    from .runner import run_pipeline

    config: Config = ctx.obj["config"]

    console.print("[bold]Starting full pipeline run...[/bold]")
    if dry_run:
        console.print("[yellow][DRY RUN][/yellow]")

    stats = run_pipeline(
        config=config,
        crawl_limit=crawl_limit,
        email_hours=email_hours,
        dry_run=dry_run,
    )

    console.print(f"\n[bold]Pipeline complete in {stats.duration_seconds:.1f}s[/bold]")
    console.print(f"  Sources crawled: {stats.sources_crawled}")
    console.print(f"  Emails processed: {stats.emails_processed}")
    console.print(f"  Social profiles: {stats.social_profiles_checked}")
    console.print(f"  Leads found: {stats.leads_found}")
    console.print(f"  New sources: {stats.new_sources_discovered}")
    console.print(f"  Errors: {stats.errors}")
    for f in stats.digest_files:
        console.print(f"  [green]Digest:[/green] {f}")


def _truncate(text: str, length: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


if __name__ == "__main__":
    main()
