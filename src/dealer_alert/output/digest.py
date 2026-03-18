"""Daily digest generator — produces CSV and JSON output files."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Config
from ..db import Database
from ..models import Lead, LeadScore

logger = logging.getLogger(__name__)


class DigestGenerator:
    """Generates daily digest files from accumulated leads."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def generate(
        self,
        since: datetime | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Generate digest files for leads since the given timestamp.

        Args:
            since: Only include leads discovered after this time.
                   Defaults to 24 hours ago.
            dry_run: If True, compute stats but don't write files.

        Returns:
            Summary dict with file paths and counts.
        """
        if since is None:
            since = datetime.utcnow() - timedelta(hours=24)

        leads = self.db.get_leads_since(since)

        if not leads:
            logger.info("No leads found for digest period.")
            return {"leads": 0, "files": []}

        # Group by score
        hot = [lead for lead in leads if lead.score == LeadScore.HOT]
        warm = [lead for lead in leads if lead.score == LeadScore.WARM]
        cold = [lead for lead in leads if lead.score == LeadScore.COLD]

        summary = {
            "period_start": since.isoformat(),
            "period_end": datetime.utcnow().isoformat(),
            "total_leads": len(leads),
            "hot": len(hot),
            "warm": len(warm),
            "cold": len(cold),
            "files": [],
        }

        if dry_run:
            logger.info(
                f"[DRY RUN] Would generate digest: "
                f"{len(hot)} hot, {len(warm)} warm, {len(cold)} cold"
            )
            return summary

        # Ensure output directory exists
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        # Generate files based on config
        fmt = self.config.output_format
        if fmt in ("csv", "both"):
            csv_path = output_dir / f"digest_{timestamp}.csv"
            self._write_csv(leads, csv_path)
            summary["files"].append(str(csv_path))
            logger.info(f"CSV digest written to {csv_path}")

        if fmt in ("json", "both"):
            json_path = output_dir / f"digest_{timestamp}.json"
            self._write_json(leads, summary, json_path)
            summary["files"].append(str(json_path))
            logger.info(f"JSON digest written to {json_path}")

        return summary

    def _write_csv(self, leads: list[Lead], path: Path):
        """Write leads to a CSV file."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Score", "Dealer Name", "City", "State", "Title",
                "Keywords", "People", "Source URL", "Snippet",
                "Mention Count", "Discovered At",
            ])
            for lead in leads:
                writer.writerow([
                    lead.score.value.upper(),
                    lead.dealer_name,
                    lead.city,
                    lead.state,
                    lead.title,
                    "; ".join(lead.keywords_matched),
                    "; ".join(lead.people),
                    lead.source_url,
                    lead.snippet[:300],
                    lead.mention_count,
                    lead.discovered_at.isoformat() if lead.discovered_at else "",
                ])

    def _write_json(self, leads: list[Lead], summary: dict, path: Path):
        """Write leads and summary to a JSON file."""
        data = {
            "summary": summary,
            "leads": [
                {
                    "score": lead.score.value,
                    "dealer_name": lead.dealer_name,
                    "dealer_group": lead.dealer_group,
                    "city": lead.city,
                    "state": lead.state,
                    "title": lead.title,
                    "snippet": lead.snippet,
                    "keywords_matched": lead.keywords_matched,
                    "people": lead.people,
                    "source_url": lead.source_url,
                    "outbound_links": lead.outbound_links[:10],
                    "mention_count": lead.mention_count,
                    "discovered_at": (
                        lead.discovered_at.isoformat() if lead.discovered_at else None
                    ),
                }
                for lead in leads
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
