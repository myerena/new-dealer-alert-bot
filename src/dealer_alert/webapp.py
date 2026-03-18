"""Local web app for reviewing leads with thumbs up/down feedback.

Run with:
    dealer-alert review

Opens a browser to http://localhost:5000 with a card-based UI
for reviewing each lead, marking it as good/bad, and adding notes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template_string, request

from .config import Config
from .db import Database
from .models import LeadScore
from .output.dedup import deduplicate_leads

logger = logging.getLogger(__name__)

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dealer Alert — Lead Review</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; min-height: 100vh; }

.header { background: #1e293b; padding: 16px 24px; display: flex;
          justify-content: space-between; align-items: center;
          border-bottom: 1px solid #334155; }
.header h1 { font-size: 20px; color: #f8fafc; }
.header .stats { display: flex; gap: 20px; font-size: 14px; color: #94a3b8; }
.header .stats span { font-weight: 600; }
.stat-hot { color: #ef4444 !important; }
.stat-warm { color: #f59e0b !important; }
.stat-cold { color: #3b82f6 !important; }
.stat-good { color: #22c55e !important; }
.stat-bad { color: #ef4444 !important; }

.filters { background: #1e293b; padding: 12px 24px; display: flex;
           gap: 12px; align-items: center; border-bottom: 1px solid #334155; }
.filters input { background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
                 padding: 8px 14px; border-radius: 8px; font-size: 14px; width: 300px; }
.filters select { background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
                  padding: 8px 14px; border-radius: 8px; font-size: 14px; }
.filters button { background: #334155; border: none; color: #e2e8f0;
                  padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px; }
.filters button:hover { background: #475569; }
.filters button.active { background: #3b82f6; }

.leads { padding: 24px; display: flex; flex-direction: column; gap: 16px;
         max-width: 1200px; margin: 0 auto; }

.lead-card { background: #1e293b; border-radius: 12px; padding: 20px;
             border: 1px solid #334155; transition: border-color 0.2s; }
.lead-card:hover { border-color: #475569; }
.lead-card.feedback-good { border-left: 4px solid #22c55e; }
.lead-card.feedback-bad { border-left: 4px solid #ef4444; opacity: 0.6; }

.lead-top { display: flex; justify-content: space-between; align-items: flex-start;
            margin-bottom: 12px; }
.lead-score { display: inline-block; padding: 3px 12px; border-radius: 12px;
              font-size: 11px; font-weight: 700; text-transform: uppercase; }
.score-hot { background: #451a1a; color: #ef4444; }
.score-warm { background: #451a00; color: #f59e0b; }
.score-cold { background: #172554; color: #3b82f6; }

.lead-summary { font-size: 15px; line-height: 1.6; margin-bottom: 12px; color: #cbd5e1; }
.lead-summary strong { color: #f8fafc; }

.lead-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
             gap: 8px; margin-bottom: 12px; font-size: 13px; }
.lead-meta-item { display: flex; gap: 6px; }
.lead-meta-label { color: #64748b; min-width: 70px; }
.lead-meta-value { color: #94a3b8; }
.lead-meta-value a { color: #60a5fa; text-decoration: none; }
.lead-meta-value a:hover { text-decoration: underline; }

.lead-keywords { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.keyword-tag { background: #334155; padding: 2px 10px; border-radius: 12px;
               font-size: 11px; color: #94a3b8; }

.lead-snippet { font-size: 13px; color: #64748b; line-height: 1.5;
                max-height: 80px; overflow: hidden; margin-bottom: 12px;
                cursor: pointer; }
.lead-snippet.expanded { max-height: none; }
.lead-snippet:hover { color: #94a3b8; }

.lead-actions { display: flex; gap: 8px; align-items: center; padding-top: 12px;
                border-top: 1px solid #334155; }
.btn { border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer;
       font-size: 13px; font-weight: 600; transition: all 0.2s; }
.btn-good { background: #14532d; color: #22c55e; }
.btn-good:hover, .btn-good.active { background: #22c55e; color: #052e16; }
.btn-bad { background: #450a0a; color: #ef4444; }
.btn-bad:hover, .btn-bad.active { background: #ef4444; color: #450a0a; }
.btn-skip { background: #334155; color: #94a3b8; }
.btn-skip:hover { background: #475569; }

.notes-input { flex: 1; background: #0f172a; border: 1px solid #334155;
               color: #e2e8f0; padding: 8px 12px; border-radius: 8px;
               font-size: 13px; margin-left: 8px; }

.empty { text-align: center; padding: 60px; color: #64748b; }
</style>
</head>
<body>

<div class="header">
  <h1>Lead Review</h1>
  <div class="stats">
    <div>Total: <span id="statTotal">0</span></div>
    <div>Hot: <span class="stat-hot" id="statHot">0</span></div>
    <div>Warm: <span class="stat-warm" id="statWarm">0</span></div>
    <div>Cold: <span class="stat-cold" id="statCold">0</span></div>
    <div>|</div>
    <div>Reviewed: <span class="stat-good" id="statGood">0</span> good
         / <span class="stat-bad" id="statBad">0</span> bad</div>
    <div>Unreviewed: <span id="statUnreviewed">0</span></div>
  </div>
</div>

<div class="filters">
  <input type="text" id="searchBox" placeholder="Search leads..."
         oninput="filterLeads()">
  <select id="scoreFilter" onchange="filterLeads()">
    <option value="">All Scores</option>
    <option value="hot">Hot</option>
    <option value="warm">Warm</option>
    <option value="cold">Cold</option>
  </select>
  <select id="feedbackFilter" onchange="filterLeads()">
    <option value="">All Feedback</option>
    <option value="unreviewed" selected>Unreviewed</option>
    <option value="good">Good</option>
    <option value="bad">Bad</option>
  </select>
  <button onclick="document.getElementById('feedbackFilter').value='';filterLeads()">
    Show All
  </button>
</div>

<div class="leads" id="leadsContainer"></div>

<script>
let allLeads = [];

async function loadLeads() {
  const resp = await fetch('/api/leads');
  allLeads = await resp.json();
  updateStats();
  filterLeads();
}

function updateStats() {
  document.getElementById('statTotal').textContent = allLeads.length;
  document.getElementById('statHot').textContent =
    allLeads.filter(l => l.score === 'hot').length;
  document.getElementById('statWarm').textContent =
    allLeads.filter(l => l.score === 'warm').length;
  document.getElementById('statCold').textContent =
    allLeads.filter(l => l.score === 'cold').length;
  document.getElementById('statGood').textContent =
    allLeads.filter(l => l.feedback === 'good').length;
  document.getElementById('statBad').textContent =
    allLeads.filter(l => l.feedback === 'bad').length;
  document.getElementById('statUnreviewed').textContent =
    allLeads.filter(l => !l.feedback).length;
}

function filterLeads() {
  const search = document.getElementById('searchBox').value.toLowerCase();
  const score = document.getElementById('scoreFilter').value;
  const fb = document.getElementById('feedbackFilter').value;

  const filtered = allLeads.filter(l => {
    if (search) {
      const text = (l.dealer_name + l.city + l.state + l.summary +
                    l.keywords.join(' ') + l.people.join(' ')).toLowerCase();
      if (!text.includes(search)) return false;
    }
    if (score && l.score !== score) return false;
    if (fb === 'unreviewed' && l.feedback) return false;
    if (fb === 'good' && l.feedback !== 'good') return false;
    if (fb === 'bad' && l.feedback !== 'bad') return false;
    return true;
  });

  renderLeads(filtered);
}

function renderLeads(leads) {
  const container = document.getElementById('leadsContainer');

  if (!leads.length) {
    container.innerHTML = '<div class="empty">No leads match your filters</div>';
    return;
  }

  container.innerHTML = leads.map(l => {
    const scoreClass = l.score === 'hot' ? 'score-hot' :
                       l.score === 'warm' ? 'score-warm' : 'score-cold';
    const fbClass = l.feedback === 'good' ? 'feedback-good' :
                    l.feedback === 'bad' ? 'feedback-bad' : '';
    const goodActive = l.feedback === 'good' ? 'active' : '';
    const badActive = l.feedback === 'bad' ? 'active' : '';

    return `
    <div class="lead-card ${fbClass}" id="lead-${l.id}">
      <div class="lead-top">
        <span class="lead-score ${scoreClass}">${l.score.toUpperCase()}</span>
        <span style="font-size:12px;color:#64748b;">
          ${l.discovered || ''}
        </span>
      </div>

      <div class="lead-summary">${formatSummary(l.summary)}</div>

      <div class="lead-meta">
        ${l.dealer_name ? `<div class="lead-meta-item">
          <span class="lead-meta-label">Dealer:</span>
          <span class="lead-meta-value"><strong>${esc(l.dealer_name)}</strong></span>
        </div>` : ''}
        ${l.city || l.state ? `<div class="lead-meta-item">
          <span class="lead-meta-label">Location:</span>
          <span class="lead-meta-value">${[l.city,l.state].filter(Boolean).join(', ')}</span>
        </div>` : ''}
        ${l.people.length ? `<div class="lead-meta-item">
          <span class="lead-meta-label">People:</span>
          <span class="lead-meta-value">${l.people.map(esc).join(', ')}</span>
        </div>` : ''}
        <div class="lead-meta-item">
          <span class="lead-meta-label">Source:</span>
          <span class="lead-meta-value">
            <a href="${esc(l.source_url)}" target="_blank">
              ${esc(l.source_url).substring(0, 50)}
            </a>
          </span>
        </div>
      </div>

      <div class="lead-keywords">
        ${l.keywords.map(k => `<span class="keyword-tag">${esc(k)}</span>`).join('')}
      </div>

      <div class="lead-snippet" onclick="this.classList.toggle('expanded')">
        ${esc(l.snippet)}
      </div>

      <div class="lead-actions">
        <button class="btn btn-good ${goodActive}"
                onclick="submitFeedback(${l.id}, 'good')">
          &#x1F44D; Good Lead
        </button>
        <button class="btn btn-bad ${badActive}"
                onclick="submitFeedback(${l.id}, 'bad')">
          &#x1F44E; Not Relevant
        </button>
        <input class="notes-input" type="text"
               placeholder="Optional notes..."
               id="notes-${l.id}"
               value="${esc(l.feedback_notes || '')}"
               onchange="submitNotes(${l.id})">
      </div>
    </div>`;
  }).join('');
}

function formatSummary(s) {
  if (!s) return '<em style="color:#64748b;">No summary available</em>';
  // Bold text between **...**
  s = s.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
  // Italic text between "..."
  s = s.replace(/"([^"]+)"/g, '<em style="color:#94a3b8;">"$1"</em>');
  return s;
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function submitFeedback(id, feedback) {
  await fetch('/api/feedback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      lead_id: id,
      feedback: feedback,
      notes: document.getElementById('notes-' + id)?.value || ''
    })
  });

  // Update local state
  const lead = allLeads.find(l => l.id === id);
  if (lead) {
    lead.feedback = feedback;
    updateStats();
    filterLeads();
  }
}

async function submitNotes(id) {
  const notes = document.getElementById('notes-' + id)?.value || '';
  const lead = allLeads.find(l => l.id === id);
  if (lead && lead.feedback) {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        lead_id: id,
        feedback: lead.feedback,
        notes: notes
      })
    });
    lead.feedback_notes = notes;
  }
}

loadLeads();
</script>
</body>
</html>"""


def create_app(config: Config | None = None) -> Flask:
    """Create the Flask review app."""
    if config is None:
        config = Config.load()

    db = Database(config.database_path)
    db.init_schema()

    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(TEMPLATE)

    @app.route("/api/leads")
    def api_leads():
        hours = int(request.args.get("hours", 168))  # Default: 7 days
        since = datetime.utcnow() - timedelta(hours=hours)
        leads = db.get_leads_since(since)

        # Apply deduplication
        leads = deduplicate_leads(leads)

        return jsonify(
            [
                {
                    "id": ld.id,
                    "score": ld.score.value,
                    "dealer_name": ld.dealer_name,
                    "city": ld.city,
                    "state": ld.state,
                    "title": ld.title,
                    "summary": ld.summary,
                    "snippet": ld.snippet[:500],
                    "keywords": ld.keywords_matched,
                    "people": ld.people,
                    "source_url": ld.source_url,
                    "discovered": (
                        ld.discovered_at.strftime("%Y-%m-%d %H:%M") if ld.discovered_at else ""
                    ),
                    "feedback": ld.feedback,
                    "feedback_notes": ld.feedback_notes,
                    "mention_count": ld.mention_count,
                }
                for ld in leads
            ]
        )

    @app.route("/api/feedback", methods=["POST"])
    def api_feedback():
        data = request.json
        lead_id = data.get("lead_id")
        feedback = data.get("feedback", "")
        notes = data.get("notes", "")

        if lead_id and feedback in ("good", "bad"):
            db.update_lead_feedback(lead_id, feedback, notes)
            return jsonify({"ok": True})

        return jsonify({"error": "Invalid request"}), 400

    @app.route("/api/stats")
    def api_stats():
        hours = int(request.args.get("hours", 168))
        since = datetime.utcnow() - timedelta(hours=hours)
        leads = db.get_leads_since(since)

        return jsonify(
            {
                "total": len(leads),
                "hot": sum(1 for ld in leads if ld.score == LeadScore.HOT),
                "warm": sum(1 for ld in leads if ld.score == LeadScore.WARM),
                "cold": sum(1 for ld in leads if ld.score == LeadScore.COLD),
                "good": sum(1 for ld in leads if ld.feedback == "good"),
                "bad": sum(1 for ld in leads if ld.feedback == "bad"),
                "unreviewed": sum(1 for ld in leads if not ld.feedback),
            }
        )

    return app
