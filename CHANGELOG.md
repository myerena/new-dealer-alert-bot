# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffolding with full dev-standards compliance
- Source registry with SQLite storage
- Fetcher layer: RSS, HTML, sitemap adapters
- Lead extractor with keyword matching and entity extraction
- Expansion engine for discovering related sources
- Digest output in CSV + JSON with hot/warm/cold scoring
- CLI with init, crawl, digest, and add-source commands
- Seed sources for v1 (trade media, chambers, associations)
- Dry-run mode for all external actions
