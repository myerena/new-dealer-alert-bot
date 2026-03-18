"""CLI entry point for the Dealer Alert Bot.

Commands:
    init          Initialize database and load seed sources
    crawl         Run a crawl cycle across registered sources
    digest        Generate a lead digest from recent crawl data
    add-source    Manually add a source URL to the registry
    status        Show current registry and lead stats
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
    console.print(
        f"[bold]Crawling {len(sources)} sources{dry_run_label}...[/bold]"
    )

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
                f"({lead.city}, {lead.state})" if lead.city else
                f"  [{score_color}]{lead.score.value.upper()}[/{score_color}] "
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


if __name__ == "__main__":
    main()
