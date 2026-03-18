"""Tests for the database layer."""

from datetime import datetime, timedelta

from dealer_alert.db import Database
from dealer_alert.models import (
    Lead,
    LeadScore,
    Source,
    SourceCategory,
    SourceType,
)


def test_init_schema(tmp_db: Database):
    """Schema should create all tables without error."""
    # init_schema is called by the fixture; verify tables exist
    with tmp_db.connect() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "sources" in table_names
        assert "leads" in table_names
        assert "discovered_sources" in table_names
        assert "crawl_log" in table_names


def test_add_and_get_source(tmp_db: Database):
    """Should insert a source and retrieve it."""
    source = Source(
        url="https://example.com/news",
        source_type=SourceType.HTML,
        category=SourceCategory.TRADE_MEDIA,
        name="Example News",
        geography="national",
        priority=2,
    )
    source_id = tmp_db.add_source(source)
    assert source_id > 0

    sources = tmp_db.get_all_sources()
    assert len(sources) == 1
    assert sources[0].url == "https://example.com/news"
    assert sources[0].category == SourceCategory.TRADE_MEDIA


def test_source_deduplication(tmp_db: Database):
    """Duplicate URLs should be silently ignored."""
    source = Source(url="https://example.com/feed", source_type=SourceType.RSS)
    tmp_db.add_source(source)
    tmp_db.add_source(source)  # Duplicate

    assert tmp_db.get_source_count() == 1


def test_source_exists(tmp_db: Database):
    """source_exists should return True for registered URLs."""
    tmp_db.add_source(Source(url="https://example.com/test"))
    assert tmp_db.source_exists("https://example.com/test")
    assert not tmp_db.source_exists("https://example.com/other")


def test_get_sources_due(tmp_db: Database):
    """Sources with null last_fetched_at should come first."""
    tmp_db.add_source(Source(url="https://a.com", priority=5))
    tmp_db.add_source(Source(url="https://b.com", priority=1))

    due = tmp_db.get_sources_due(limit=10)
    assert len(due) == 2
    urls = {s.url for s in due}
    assert "https://a.com" in urls
    assert "https://b.com" in urls


def test_update_source_fetched(tmp_db: Database):
    """Fetching should update hash and timestamp."""
    tmp_db.add_source(Source(url="https://example.com"))
    sources = tmp_db.get_all_sources()
    sid = sources[0].id

    tmp_db.update_source_fetched(sid, "abc123hash")

    updated = tmp_db.get_all_sources()
    assert updated[0].last_hash == "abc123hash"
    assert updated[0].last_fetched_at is not None
    assert updated[0].fetch_error_count == 0


def test_update_source_error(tmp_db: Database):
    """Errors should increment the error count."""
    tmp_db.add_source(Source(url="https://example.com"))
    sources = tmp_db.get_all_sources()
    sid = sources[0].id

    tmp_db.update_source_fetched(sid, "", error="Connection timeout")
    tmp_db.update_source_fetched(sid, "", error="Connection timeout")

    updated = tmp_db.get_all_sources()
    assert updated[0].fetch_error_count == 2


def test_add_and_get_leads(tmp_db: Database):
    """Should store and retrieve leads."""
    tmp_db.add_source(Source(url="https://example.com"))
    sources = tmp_db.get_all_sources()
    sid = sources[0].id

    lead = Lead(
        source_id=sid,
        source_url="https://example.com",
        title="Grand Opening",
        snippet="New dealership grand opening in Dallas",
        dealer_name="Smith Auto",
        city="Dallas",
        state="TX",
        keywords_matched=["grand opening"],
        score=LeadScore.HOT,
        mention_count=3,
    )
    lead_id = tmp_db.add_lead(lead)
    assert lead_id > 0

    # Retrieve since yesterday
    since = datetime.utcnow() - timedelta(hours=1)
    leads = tmp_db.get_leads_since(since)
    assert len(leads) == 1
    assert leads[0].dealer_name == "Smith Auto"
    assert leads[0].score == LeadScore.HOT
    assert "grand opening" in leads[0].keywords_matched


def test_crawl_log(tmp_db: Database):
    """Should track crawl runs."""
    log_id = tmp_db.start_crawl_log()
    assert log_id > 0
    tmp_db.finish_crawl_log(log_id, sources_crawled=10, leads_found=3, new_sources=5, errors=1)
