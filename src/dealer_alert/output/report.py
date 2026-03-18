"""HTML dashboard report generator.

Produces a self-contained HTML file with:
- Lead summary stats
- Score distribution chart
- Geographic breakdown
- Source performance table
- Full lead table with filtering

Uses inline CSS and Chart.js (CDN) for zero dependencies.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..config import Config
from ..db import Database
from ..models import Lead, LeadScore

logger = logging.getLogger(__name__)


def generate_report(
    config: Config,
    db: Database,
    since_hours: int = 24,
    output_path: Path | None = None,
) -> Path:
    """Generate an HTML dashboard report.

    Args:
        config: App configuration.
        db: Database instance.
        since_hours: Report on leads from the last N hours.
        output_path: Where to write the HTML file. Defaults to output_dir.

    Returns:
        Path to the generated HTML file.
    """
    since = datetime.utcnow() - timedelta(hours=since_hours)
    leads = db.get_leads_since(since)
    all_sources = db.get_all_sources(enabled_only=False)

    # Score breakdown
    hot = [ld for ld in leads if ld.score == LeadScore.HOT]
    warm = [ld for ld in leads if ld.score == LeadScore.WARM]
    cold = [ld for ld in leads if ld.score == LeadScore.COLD]

    # Geographic breakdown
    state_counts: dict[str, int] = {}
    for ld in leads:
        st = ld.state.upper().strip() if ld.state else "Unknown"
        state_counts[st] = state_counts.get(st, 0) + 1

    # Source category breakdown
    cat_counts: dict[str, int] = {}
    for s in all_sources:
        cat = s.category.value
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Source error stats
    error_sources = [s for s in all_sources if s.fetch_error_count > 0]

    # Build HTML
    report_html = _build_html(
        leads=leads,
        hot=hot,
        warm=warm,
        cold=cold,
        state_counts=state_counts,
        cat_counts=cat_counts,
        total_sources=len(all_sources),
        enabled_sources=sum(1 for s in all_sources if s.enabled),
        error_sources=error_sources,
        since_hours=since_hours,
        generated_at=datetime.utcnow(),
    )

    # Write file
    if output_path is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = config.output_dir / f"report_{ts}.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_html)

    logger.info(f"Report written to {output_path}")
    return output_path


def _build_html(
    leads: list[Lead],
    hot: list[Lead],
    warm: list[Lead],
    cold: list[Lead],
    state_counts: dict[str, int],
    cat_counts: dict[str, int],
    total_sources: int,
    enabled_sources: int,
    error_sources: list,
    since_hours: int,
    generated_at: datetime,
) -> str:
    """Build the complete HTML report string."""

    # Top states for chart
    top_states = sorted(
        state_counts.items(), key=lambda x: x[1], reverse=True
    )[:15]

    leads_json = json.dumps([
        {
            "score": ld.score.value.upper(),
            "dealer": html.escape(ld.dealer_name or "Unknown"),
            "city": html.escape(ld.city or ""),
            "state": html.escape(ld.state or ""),
            "title": html.escape(ld.title[:80] if ld.title else ""),
            "keywords": ", ".join(ld.keywords_matched[:5]),
            "source": html.escape(ld.source_url[:60] if ld.source_url else ""),
            "discovered": (
                ld.discovered_at.strftime("%Y-%m-%d %H:%M")
                if ld.discovered_at else ""
            ),
        }
        for ld in leads
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dealer Alert Bot — Lead Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f5f5f5; color: #333; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
h1 {{ font-size: 28px; margin-bottom: 5px; }}
.subtitle {{ color: #666; margin-bottom: 30px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
              gap: 16px; margin-bottom: 30px; }}
.stat-card {{ background: white; border-radius: 12px; padding: 24px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
.stat-card .number {{ font-size: 36px; font-weight: 700; }}
.stat-card .label {{ font-size: 14px; color: #666; margin-top: 4px; }}
.stat-card.hot .number {{ color: #dc2626; }}
.stat-card.warm .number {{ color: #f59e0b; }}
.stat-card.cold .number {{ color: #3b82f6; }}
.stat-card.total .number {{ color: #059669; }}
.charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }}
.chart-card {{ background: white; border-radius: 12px; padding: 24px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.chart-card h3 {{ margin-bottom: 16px; font-size: 16px; }}
table {{ width: 100%; border-collapse: collapse; background: white;
        border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th {{ background: #1e293b; color: white; padding: 12px 16px; text-align: left;
     font-size: 13px; cursor: pointer; }}
th:hover {{ background: #334155; }}
td {{ padding: 10px 16px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
tr:hover {{ background: #f8fafc; }}
.score-badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px;
               font-size: 11px; font-weight: 600; text-transform: uppercase; }}
.score-hot {{ background: #fee2e2; color: #dc2626; }}
.score-warm {{ background: #fef3c7; color: #d97706; }}
.score-cold {{ background: #dbeafe; color: #2563eb; }}
.filter-bar {{ margin-bottom: 16px; display: flex; gap: 10px; align-items: center; }}
.filter-bar input {{ padding: 8px 14px; border: 1px solid #d1d5db; border-radius: 8px;
                    font-size: 14px; width: 300px; }}
.filter-bar select {{ padding: 8px 14px; border: 1px solid #d1d5db; border-radius: 8px;
                     font-size: 14px; }}
.section-title {{ font-size: 20px; font-weight: 600; margin: 30px 0 16px; }}
@media (max-width: 768px) {{ .charts-row {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <h1>Dealer Alert Bot</h1>
  <p class="subtitle">Lead Report — Last {since_hours} hours
    | Generated {generated_at.strftime('%B %d, %Y at %I:%M %p UTC')}</p>

  <div class="stats-grid">
    <div class="stat-card total"><div class="number">{len(leads)}</div>
      <div class="label">Total Leads</div></div>
    <div class="stat-card hot"><div class="number">{len(hot)}</div>
      <div class="label">Hot Leads</div></div>
    <div class="stat-card warm"><div class="number">{len(warm)}</div>
      <div class="label">Warm Leads</div></div>
    <div class="stat-card cold"><div class="number">{len(cold)}</div>
      <div class="label">Cold Leads</div></div>
    <div class="stat-card"><div class="number">{total_sources}</div>
      <div class="label">Total Sources</div></div>
    <div class="stat-card"><div class="number">{enabled_sources}</div>
      <div class="label">Active Sources</div></div>
  </div>

  <div class="charts-row">
    <div class="chart-card">
      <h3>Lead Score Distribution</h3>
      <canvas id="scoreChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Top States</h3>
      <canvas id="stateChart"></canvas>
    </div>
  </div>

  <div class="charts-row">
    <div class="chart-card">
      <h3>Source Categories</h3>
      <canvas id="catChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Sources with Errors ({len(error_sources)})</h3>
      <div style="max-height:300px;overflow-y:auto;font-size:13px;">
        {''.join(
            f'<div style="padding:6px 0;border-bottom:1px solid #eee;">'
            f'<strong>{html.escape(s.name or s.url[:40])}</strong> — '
            f'{s.fetch_error_count} errors</div>'
            for s in sorted(error_sources, key=lambda x: -x.fetch_error_count)[:20]
        ) or '<p style="color:#666;">No errors</p>'}
      </div>
    </div>
  </div>

  <h2 class="section-title">All Leads</h2>
  <div class="filter-bar">
    <input type="text" id="searchBox" placeholder="Search leads..."
           oninput="filterTable()">
    <select id="scoreFilter" onchange="filterTable()">
      <option value="">All Scores</option>
      <option value="HOT">Hot</option>
      <option value="WARM">Warm</option>
      <option value="COLD">Cold</option>
    </select>
  </div>

  <table id="leadsTable">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Score</th>
        <th onclick="sortTable(1)">Dealer</th>
        <th onclick="sortTable(2)">City</th>
        <th onclick="sortTable(3)">State</th>
        <th onclick="sortTable(4)">Signal</th>
        <th onclick="sortTable(5)">Keywords</th>
        <th onclick="sortTable(6)">Source</th>
        <th onclick="sortTable(7)">Discovered</th>
      </tr>
    </thead>
    <tbody id="leadsBody"></tbody>
  </table>
</div>

<script>
const leads = {leads_json};

// Populate table
const tbody = document.getElementById('leadsBody');
leads.forEach(l => {{
  const sc = l.score;
  const scoreClass = sc === 'HOT' ? 'score-hot' : sc === 'WARM' ? 'score-warm' : 'score-cold';
  const row = document.createElement('tr');
  row.innerHTML = `<td><span class="score-badge ${{scoreClass}}">${{l.score}}</span></td>
    <td>${{l.dealer}}</td><td>${{l.city}}</td><td>${{l.state}}</td>
    <td>${{l.title}}</td><td>${{l.keywords}}</td>
    <td><a href="${{l.source}}" target="_blank">${{l.source.substring(0,40)}}</a></td>
    <td>${{l.discovered}}</td>`;
  row.dataset.score = l.score;
  row.dataset.text = (l.dealer + l.city + l.state + l.title + l.keywords).toLowerCase();
  tbody.appendChild(row);
}});

// Filter
function filterTable() {{
  const search = document.getElementById('searchBox').value.toLowerCase();
  const score = document.getElementById('scoreFilter').value;
  tbody.querySelectorAll('tr').forEach(row => {{
    const matchSearch = !search || row.dataset.text.includes(search);
    const matchScore = !score || row.dataset.score === score;
    row.style.display = matchSearch && matchScore ? '' : 'none';
  }});
}}

// Sort
let sortDir = {{}};
function sortTable(col) {{
  const rows = Array.from(tbody.querySelectorAll('tr'));
  sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {{
    const aVal = a.cells[col].textContent;
    const bVal = b.cells[col].textContent;
    return sortDir[col] ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

// Charts
new Chart(document.getElementById('scoreChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Hot', 'Warm', 'Cold'],
    datasets: [{{ data: [{len(hot)}, {len(warm)}, {len(cold)}],
      backgroundColor: ['#dc2626', '#f59e0b', '#3b82f6'] }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('stateChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps([s[0] for s in top_states])},
    datasets: [{{ label: 'Leads', data: {json.dumps([s[1] for s in top_states])},
      backgroundColor: '#059669' }}]
  }},
  options: {{ responsive: true, indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('catChart'), {{
  type: 'pie',
  data: {{
    labels: {json.dumps(list(cat_counts.keys()))},
    datasets: [{{ data: {json.dumps(list(cat_counts.values()))},
      backgroundColor: ['#3b82f6','#059669','#f59e0b','#dc2626',
        '#8b5cf6','#ec4899','#06b6d4','#84cc16','#f97316'] }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
</script>
</body>
</html>"""
