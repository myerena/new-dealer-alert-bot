"""SQLite database layer for source registry and leads storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import DiscoveredSource, Lead, LeadScore, Source, SourceCategory, SourceType

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'html',
    category TEXT NOT NULL DEFAULT 'other',
    name TEXT NOT NULL DEFAULT '',
    geography TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1,
    parent_source_id INTEGER REFERENCES sources(id),
    last_fetched_at TEXT,
    last_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    fetch_error_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    dealer_name TEXT NOT NULL DEFAULT '',
    dealer_group TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    people TEXT NOT NULL DEFAULT '[]',
    keywords_matched TEXT NOT NULL DEFAULT '[]',
    outbound_links TEXT NOT NULL DEFAULT '[]',
    score TEXT NOT NULL DEFAULT 'cold',
    mention_count INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    raw_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS discovered_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'html',
    category TEXT NOT NULL DEFAULT 'other',
    discovered_from_source_id INTEGER NOT NULL REFERENCES sources(id),
    geography TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    sources_crawled INTEGER NOT NULL DEFAULT 0,
    leads_found INTEGER NOT NULL DEFAULT 0,
    new_sources_discovered INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled);
CREATE INDEX IF NOT EXISTS idx_sources_category ON sources(category);
CREATE INDEX IF NOT EXISTS idx_sources_last_fetched ON sources(last_fetched_at);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score);
CREATE INDEX IF NOT EXISTS idx_leads_discovered ON leads(discovered_at);
CREATE INDEX IF NOT EXISTS idx_leads_dealer ON leads(dealer_name);
CREATE INDEX IF NOT EXISTS idx_discovered_processed ON discovered_sources(processed);
"""


class Database:
    """SQLite database manager for dealer alert data."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        """Context manager for database connections.

        Uses DELETE journal mode instead of WAL to avoid Windows file-locking
        issues with .db-wal and .db-shm files. Includes 30s busy timeout
        for concurrent access.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        """Create tables if they don't exist."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Check/set schema version
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    # ── Source operations ───────────────────────────────────────────

    def add_source(self, source: Source) -> int:
        """Insert a new source. Returns the new source ID."""
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO sources
                   (url, source_type, category, name, geography, priority, enabled,
                    parent_source_id, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.url,
                    source.source_type.value,
                    source.category.value,
                    source.name,
                    source.geography,
                    source.priority,
                    int(source.enabled),
                    source.parent_source_id,
                    source.notes,
                ),
            )
            return cursor.lastrowid or 0

    def get_sources_due(self, limit: int = 100) -> list[Source]:
        """Get enabled sources ordered by staleness (least recently fetched first)."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM sources
                   WHERE enabled = 1 AND fetch_error_count < 10
                   ORDER BY last_fetched_at ASC NULLS FIRST, priority ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._row_to_source(r) for r in rows]

    def get_all_sources(self, enabled_only: bool = True) -> list[Source]:
        """Get all registered sources."""
        with self.connect() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM sources WHERE enabled = 1 ORDER BY priority ASC"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sources ORDER BY priority ASC").fetchall()
            return [self._row_to_source(r) for r in rows]

    def update_source_fetched(self, source_id: int, content_hash: str, error: str | None = None):
        """Mark a source as fetched, updating hash and error count."""
        with self.connect() as conn:
            if error:
                conn.execute(
                    """UPDATE sources
                       SET last_fetched_at = datetime('now'),
                           fetch_error_count = fetch_error_count + 1
                       WHERE id = ?""",
                    (source_id,),
                )
            else:
                conn.execute(
                    """UPDATE sources
                       SET last_fetched_at = datetime('now'),
                           last_hash = ?,
                           fetch_error_count = 0
                       WHERE id = ?""",
                    (content_hash, source_id),
                )

    def source_exists(self, url: str) -> bool:
        """Check if a source URL is already registered."""
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM sources WHERE url = ?", (url,)).fetchone()
            return row is not None

    def get_source_count(self) -> int:
        """Get the total number of sources."""
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM sources").fetchone()
            return row[0]

    # ── Lead operations ─────────────────────────────────────────────

    def add_lead(self, lead: Lead) -> int:
        """Insert a new lead. Returns the new lead ID."""
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO leads
                   (source_id, source_url, title, snippet, dealer_name, dealer_group,
                    city, state, people, keywords_matched, outbound_links, score,
                    mention_count, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lead.source_id,
                    lead.source_url,
                    lead.title,
                    lead.snippet,
                    lead.dealer_name,
                    lead.dealer_group,
                    lead.city,
                    lead.state,
                    json.dumps(lead.people),
                    json.dumps(lead.keywords_matched),
                    json.dumps(lead.outbound_links),
                    lead.score.value,
                    lead.mention_count,
                    lead.raw_text,
                ),
            )
            return cursor.lastrowid or 0

    def get_leads_since(self, since: datetime) -> list[Lead]:
        """Get all leads discovered since a given timestamp."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM leads WHERE discovered_at >= ? "
                "ORDER BY score ASC, discovered_at DESC",
                (since.isoformat(),),
            ).fetchall()
            return [self._row_to_lead(r) for r in rows]

    def get_lead_count(self, since: datetime | None = None) -> int:
        """Get total lead count, optionally since a date."""
        with self.connect() as conn:
            if since:
                row = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?",
                    (since.isoformat(),),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM leads").fetchone()
            return row[0]

    # ── Discovered source operations ────────────────────────────────

    def add_discovered_source(self, ds: DiscoveredSource) -> int:
        """Queue a discovered source for future registration."""
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO discovered_sources
                   (url, source_type, category, discovered_from_source_id, geography, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ds.url,
                    ds.source_type.value,
                    ds.category.value,
                    ds.discovered_from_source_id,
                    ds.geography,
                    ds.reason,
                ),
            )
            return cursor.lastrowid or 0

    def get_unprocessed_discovered(self, limit: int = 50) -> list[dict]:
        """Get discovered sources not yet promoted to the registry."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM discovered_sources WHERE processed = 0 "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_discovered_processed(self, disc_id: int):
        """Mark a discovered source as processed."""
        with self.connect() as conn:
            conn.execute("UPDATE discovered_sources SET processed = 1 WHERE id = ?", (disc_id,))

    # ── Crawl log ───────────────────────────────────────────────────

    def start_crawl_log(self) -> int:
        """Start a new crawl log entry. Returns the log ID."""
        with self.connect() as conn:
            cursor = conn.execute("INSERT INTO crawl_log DEFAULT VALUES")
            return cursor.lastrowid or 0

    def finish_crawl_log(
        self, log_id: int, sources_crawled: int, leads_found: int, new_sources: int, errors: int
    ):
        """Finalize a crawl log entry."""
        with self.connect() as conn:
            conn.execute(
                """UPDATE crawl_log
                   SET finished_at = datetime('now'),
                       sources_crawled = ?,
                       leads_found = ?,
                       new_sources_discovered = ?,
                       errors = ?
                   WHERE id = ?""",
                (sources_crawled, leads_found, new_sources, errors, log_id),
            )

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"],
            url=row["url"],
            source_type=SourceType(row["source_type"]),
            category=SourceCategory(row["category"]),
            name=row["name"],
            geography=row["geography"],
            priority=row["priority"],
            enabled=bool(row["enabled"]),
            parent_source_id=row["parent_source_id"],
            last_fetched_at=(
                datetime.fromisoformat(row["last_fetched_at"]) if row["last_fetched_at"] else None
            ),
            last_hash=row["last_hash"],
            created_at=(datetime.fromisoformat(row["created_at"]) if row["created_at"] else None),
            fetch_error_count=row["fetch_error_count"],
            notes=row["notes"],
        )

    @staticmethod
    def _row_to_lead(row: sqlite3.Row) -> Lead:
        return Lead(
            id=row["id"],
            source_id=row["source_id"],
            source_url=row["source_url"],
            title=row["title"],
            snippet=row["snippet"],
            dealer_name=row["dealer_name"],
            dealer_group=row["dealer_group"],
            city=row["city"],
            state=row["state"],
            people=json.loads(row["people"]),
            keywords_matched=json.loads(row["keywords_matched"]),
            outbound_links=json.loads(row["outbound_links"]),
            score=LeadScore(row["score"]),
            mention_count=row["mention_count"],
            discovered_at=(
                datetime.fromisoformat(row["discovered_at"]) if row["discovered_at"] else None
            ),
            raw_text=row["raw_text"],
        )
