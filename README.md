# New Dealer Alert Bot

A high-recall lead radar that monitors thousands of public sources for signals of dealership openings, expansions, relocations, and staffing changes. Built for breadth-first discovery — surfaces anything that smells like a new rooftop so a human can chase it.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Initialize the database and seed sources
dealer-alert init

# Run a crawl
dealer-alert crawl

# Generate today's digest
dealer-alert digest

# Dry run (no writes, just show what would happen)
dealer-alert crawl --dry-run
```

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌────────────────┐
│  Source Registry │────▶│ Fetcher Layer │────▶│ Lead Extractor │
│    (SQLite)      │     │ RSS/HTML/XML  │     │ Keywords + NER │
└─────────────────┘     └──────────────┘     └───────┬────────┘
                                                      │
                              ┌────────────────┐      │
                              │ Expansion Engine│◀─────┘
                              │ Discover related│
                              │ pages & sources │
                              └───────┬────────┘
                                      │
                              ┌───────▼────────┐
                              │  Digest Output  │
                              │  CSV + JSON     │
                              │  Hot/Warm/Cold  │
                              └────────────────┘
```

**Five core jobs:**

1. **Source Registry** — SQLite database of every source and endpoint with type, geography, priority, and last fetch time.
2. **Fetcher Layer** — Adapters for RSS, plain HTML, sitemap discovery, and link-following.
3. **Lead Extractor** — Keyword matching + light entity extraction for dealer names, locations, people, and opening signals.
4. **Expansion Engine** — When a lead mentions a dealer or city, discover related pages (chamber, careers, social, local news).
5. **Digest/Output** — Daily CSV + JSON with all fresh leads grouped by confidence score (hot/warm/cold).

## Features

- Monitors RSS feeds, HTML pages, XML sitemaps, and discovered links
- Loose keyword matching tuned for high recall (grand opening, ribbon cutting, now hiring, etc.)
- Light entity extraction for dealer names, locations, and people
- Auto-discovers new sources from crawled content
- Three-tier lead scoring: hot, warm, cold
- Daily digest output in CSV and JSON
- SQLite storage — zero config, single file
- Dry-run mode for safe testing
- Designed for ~5,000 endpoints in v1, scalable to 50,000+

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

See `.env.example` for all available settings.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## License

MIT
