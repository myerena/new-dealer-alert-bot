"""Shared test fixtures."""

from __future__ import annotations

import pytest

from dealer_alert.config import Config
from dealer_alert.db import Database


@pytest.fixture
def tmp_db(tmp_path) -> Database:
    """Create a temporary database for testing."""
    db = Database(tmp_path / "test.db")
    db.init_schema()
    return db


@pytest.fixture
def config(tmp_path) -> Config:
    """Create a test config with temp paths."""
    return Config(
        database_path=tmp_path / "test.db",
        output_dir=tmp_path / "digests",
        max_concurrent_fetches=2,
        fetch_timeout_seconds=5,
        crawl_delay_seconds=0,
    )
