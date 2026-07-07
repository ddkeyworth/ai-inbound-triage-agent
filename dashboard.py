"""
Generates a static, self-contained HTML dashboard from a batch_runner
results file - dark, card-based visual style (matching Dan's other
project dashboards), with filter pills by queue and expandable rows
showing the full extraction, confidence rubric, draft, and agentic
investigation trace per message. No server, no dependencies beyond the
standard library - just open the file in a browser.
"""

import argparse
import html
import json
from pathlib import Path

OUTPUTS_DIR = Path(__file__).parent / "outputs"

QUEUE_COLORS = {
    "Service": {"col": "#60a5fa", "bg": "#0b1e3f", "bdr": "#1e3a8a"},
    "Success": {"col": "#34d399", "bg": "#052e16", "bdr": "#065f46"},
    "Sales": {"col": "#c4b5fd", "bg": "#1e1533", "bdr": "#4c1d95"},
    "Team Lead Triage": {"col": "#fbbf24", "bg": "#2a1500", "bdr": "#7c2d12"},
}
BAND_COLORS = {
    "high": {"col": "#34d399", "bg": "#052e16", "bdr": "#065f46"},
    "medium": {"col": "#fbbf24", "bg": "#2a1500", "bdr": "#7c2d12"},
    "low": {"col": "#f87171", "bg": "#350a0a", "bdr": "#991b1b"},
}

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; padding: 24px; font-size: 13px; line-height: 1.5; }
h1 { font-size: 20px; font-weight: 600; color: #f8fafc; margin-bottom: 6px; }
.subtitle { font-size: 13px; color: #64748b; margin-bottom: 22px; }

.stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.stat { background: #1a1d2e; border: 1px solid #2d3149; border-radius: 8px; padding: 10px 14px; text-align: center; min-width: 96px; flex: 1; }
.stat .sv { font-size: 18px; font-weight: 700; color: var(--col, #f8fafc); }
.stat .sl { font-size: 10px; color: #64748b; margin-top: 3px; }

.filter-bar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 20px; padding: 12px 14px; background: #141628; border: 1px solid #2d3149; border-radius: 8px; }
.filter-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #475569; margin-right: 4px; white-space: nowrap; }
.fbtn { padding: 4px 12px; border-radius: 12px; font-size: 11px; font-weight: 600; cursor: pointer; border: 1px solid #334155; background: #1a1d2e; color: #475569; transition: all 0.15s; }
.fbtn.active { background: var(--bg); border-color: var(--bdr); color: var(--col); }
.fbtn:hover { opacity: 0.85; }
.row-hint { font-size: 10px; color: #475569; margin-left: auto; white-space: nowrap; }

.divider { margin: 26px 0 10px; font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #475569; display: flex; align-items: center; gap: 8px; }
.divider::after { content: ''; flex: 1; height: 1px; background: #2d3148; }

.pipeline { display: flex; flex-direction: column; gap: 10px; }
.card { background: #1e2130; border: 1px solid #2d3148; border-radius: 10px; overflow: hidden; transition: border-color 0.2s; }
.card:hover { border-color: #4a5080; }
.card-summary { padding: 12px 16px; display: flex; align-items: flex-start; gap: 12px; cursor: pointer; }
.avatar { width: 32px; height: 32px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; flex-shrink: 0; background: var(--bg); color: var(--col); border: 1px solid var(--bdr); }
.card-body { flex: 1; min-width: 0; }
.card-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 5px; }
.msg-id { font-family: ui-monospace, monospace; color: #64748b; font-size: 11px; }
.msg-snip { font-size: 12px; color: #94a3b8; flex: 1; min-width: 160px; }
.header-right { display: flex; align-items: center; gap: 6px; margin-left: auto; flex-shrink: 0; }
.badge { font-size: 10px; font-weight: 600; padding: 3px 9px; border-radius: 20px; white-space: nowrap; background: var(--bg); color: var(--col); border: 1px solid var(--bdr); }
.badge-gray { background: rgba(107,114,128,0.15); color: #9ca3af; border: 1px solid #334155; }
.badge-mismatch { background: rgba(239,68,68,0.12); color: #fca5a5; border: 1px solid #7f1d1d; }
.tag { display: inline-block; font-size: 10px; padding: 2px 8px; border-radius: 10px; margin: 1px; background: rgba(148,163,184,0.1); color: #94a3b8; border: 1px solid #334155; }
.tag.flag { background: rgba(251,191,36,0.08); color: #fbbf24; border-color: #7c2d12; }
.gt-line { font-size: 11px; color: #64748b; }
.gt-line b { color: #cbd5e1; font-weight: 500; }

.detail { display: none; border-top: 1px solid #2d3148; padding: 18px 20px; background: #171924; }
.detail.open { display: block; }
.detail h3 { font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.07em; margin: 16px 0 8px; }
.detail h3:first-child { margin-top: 0; }
.detail p { font-size: 12px; color: #94a3b8; line-height: 1.6; margin-bottom: 6px; }
.dl { display: flex; gap: 8px; font-size: 12px; padding: 3px 0; }
.dl .k { color: #64748b; min-width: 130px; flex-shrink: 0; }
.dl .v { color: #cbd5e1; }
.rubric { font-family: ui-monospace, monospace; font-size: 11px; padding: 2px 0; }
.rubric.pos { color: #6ee7a0; }
.rubric.neg { color: #fb923c; }
.quote { background: #1e2130; border-left: 2px solid #3b82f6; border-radius: 4px; padding: 10px 14px; font-size: 12px; color: #cbd5e1; line-height: 1.6; margin: 4px 0 10px; }
.quote.agent { border-left-color: #a78bfa; }
.reasoning { font-style: italic; color: #64748b; font-size: 12px; }
"""


def stat_card(value, label, color=None):
    style = f' style="--col:{color}"' if color else ""
    return f'<div class="stat"{style}><div class="sv">{html.escape(str(value))}</div><div class="sl">{html.escape(label)}</div></div>'


def badge(text, colors=None, cls=""):
    if colors:
        style = f'style="--col:{colors["col"]};--bg:{colors["bg"]};--bdr:{colors["bdr"]}"'
        return f'<span class="badge {cls}" {style}>{html.escape(text)}</span>'
    return f'<span class="badge {cls}">{html.escape(text)}</span>'


def tags(items, flag=False):
    cls = "tag flag" if flag else "tag"
    return "".join(f'<span class="{cls}">{html.escape(str(t))}</span>' for t in items)


def rubric_html(reasons):
    rows = []
    for r in reasons:
        cls = "neg" if r.strip().startswith("-") else "pos"
        rows.append(f'<div class="rubric {cls}">{html.escape(r)}</div>')
    return "".join(rows)


def card_html(r, idx):
    queue = r.get("queue", "Unknown")
    qc = QUEUE_COLORS.get(queue, {"col": "#9ca3af", "bg": "#1a1d2e", "bdr": "#334155"})
    extraction = r.get("extraction", {})
    confidence = r.get("confidence", {"score": "-", "band": "low"})
    bc = BAND_COLORS.get(confidence.get("band", "low"), BAND_COLORS["low"])
    gt = r.get("ground_truth_category", "")
    pred = extraction.get("category", "N/A")
    mismatch = gt and pred and gt != pred
    urgent = r.get("review_priority") == "urgent"
    avatar_letter = queue[0] if queue else "?"

    snippet = r["text"][:90] + ("..." if len(r["text"]) > 90 else "")
    gt_line = f'<span class="gt-line">Ground truth <b>{html.escape(gt)}</b> &rarr; predicted <b>{html.escape(pred)}</b></span>' if gt else ""

    header_badges = [
        badge(f"{confidence.get('band','?')} ({confidence.get('score','?')})", bc),
        badge(queue, qc),
    ]
    if mismatch:
        header_badges.append(badge("mismatch", cls="badge-mismatch"))
    if urgent:
        header_badges.append(badge("urgent", cls="badge-mismatch"))

    summary = f"""
    <div class="card-summary" onclick="toggleCard({idx})">
      <div class="avatar" style="--col:{qc['col']};--bg:{qc['bg']};--bdr:{qc['bdr']}">{html.escape(avatar_letter)}</div>
      <div class="card-body">
        <div class="card-header">
          <span class="msg-id">{html.escape(r['id'])}</span>
          <span class="msg-snip">{html.escape(snippet)}</span>
          <div class="header-right">{''.join(header_badges)}</div>
        </div>
        {gt_line}
        <div style="margin-top:4px">{tags(r.get('loop_in', []))}{tags([r.get('entry_channel')] if r.get('entry_channel') else [])}</div>
      </div>
    </div>"""

    # Detail panel - full extraction, rubric, routing, draft, investigation
    reasoning = extraction.get("reasoning", "")
    extraction_rows = "".join(
        f'<div class="dl"><span class="k">{html.escape(k)}</span><span class="v">{html.escape(str(v))}</span></div>'
        for k, v in [
            ("Category alternatives", ", ".join(extraction.get("category_alternatives", [])) or "none"),
            ("Contradictory signals", extraction.get("contradictory_signals", False)),
            ("Account reference", extraction.get("account_reference") or "none"),
            ("Issue type", extraction.get("issue_type", "")),
            ("Sentiment / urgency", f"{extraction.get('sentiment','')} / {extraction.get('urgency','')}"),
            ("Expansion intent", extraction.get("expansion_intent_language", False)),
            ("Retention risk language", extraction.get("retention_risk_language", False)),
            ("Team size band", extraction.get("team_size_band", "unknown")),
            ("Sensitive topic flags", ", ".join(extraction.get("sensitive_topic_flags", [])) or "none"),
            ("Entry channel", r.get("entry_channel") or "unknown"),
        ]
    )

    draft = r.get("draft")
    draft_block = f'<div class="quote">{html.escape(draft).replace(chr(10), "<br>")}</div>' if draft else '<p>No draft (extraction or draft call failed).</p>'

    investigation = r.get("investigation_summary")
    investigation_block = (
        f'<h3>Agent investigation (agentic - only runs on low confidence)</h3><div class="quote agent">{html.escape(investigation)}</div>'
        if investigation else ""
    )

    flags_block = tags(r.get("guardrail_flags", []), flag=True) or '<span class="tag">none</span>'
    cost = r.get("cost")
    cost_line = f"${cost:.6f}" if isinstance(cost, (int, float)) else "n/a"
    sales_path = r.get("sales_handling_path")
    sales_path_row = ""
    if sales_path:
        sales_path_row = (
            '<div class="dl"><span class="k">Sales handling path</span>'
            f'<span class="v">{html.escape(sales_path)}</span></div>'
        )

    draft_confidence = r.get("draft_confidence") or {}
    band = html.escape(str(draft_confidence.get("band", "n/a")))
    reason = html.escape(draft_confidence.get("reason", ""))
    draft_confidence_block = (
        '<div class="dl"><span class="k">Draft (answer-quality) confidence</span>'
        f'<span class="v">{band} - {reason}</span></div>'
    )

    matched_reference = r.get("matched_reference")
    reference_row = ""
    if matched_reference:
        ref_title = html.escape(matched_reference.get("title", ""))
        reference_row = (
            '<div class="dl"><span class="k">Reference used</span>'
            f'<span class="v">{ref_title}</span></div>'
        )

    detail = f"""
    <div class="detail" id="detail-{idx}">
      <h3>Full message</h3>
      <p>{html.escape(r['text'])}</p>
      {f'<p class="reasoning">"{html.escape(reasoning)}"</p>' if reasoning else ''}

      <h3>Extraction</h3>
      {extraction_rows}

      <h3>Confidence rubric</h3>
      {rubric_html(confidence.get('reasons', []))}
      <div class="dl" style="margin-top:6px"><span class="k">Final score / band</span><span class="v">{confidence.get('score','?')} / {confidence.get('band','?')}</span></div>

      <h3>Routing</h3>
      <div class="dl"><span class="k">Queue</span><span class="v">{html.escape(queue)}</span></div>
      <div class="dl"><span class="k">Looped in</span><span class="v">{', '.join(r.get('loop_in', [])) or 'none'}</span></div>
      <div class="dl"><span class="k">Guardrail flags</span><span class="v">{flags_block}</span></div>
      <div class="dl"><span class="k">Cost (this message)</span><span class="v">{cost_line}</span></div>
      {sales_path_row}

      <h3>Draft reply</h3>
      {draft_confidence_block}
      {reference_row}
      {draft_block}
      {investigation_block}
    </div>"""

    return summary + detail


def card_wrapper(r, idx):
    queue = r.get("queue", "Unknown")
    urgent = r.get("review_priority") == "urgent"
    return f'<div class="card" data-queue="{html.escape(queue)}" data-urgent="{"1" if urgent else "0"}">{card_html(r, idx)}</div>'


def build_dashboard(data, source_label):
    stats = data["stats"]
    results = [r for r in data["results"] if "extraction" in r]

    queues_present = ["Service", "Success", "Sales", "Team Lead Triage"]
    n_by_queue = {q: sum(1 for r in results if r.get("queue") == q) for q in queues_present}
    n_urgent = sum(1 for r in results if r.get("review_priority") == "urgent")

    accuracy = stats.get("overall_accuracy")
    accuracy_str = f"{round(accuracy * 100, 1)}%" if accuracy is not None else "N/A"
    sens = stats["sensitive_topic_detection"]
    reten = stats["retention_risk_detection"]

    stat_cards = "".join([
        stat_card(f"{stats['n_scored']}/{stats['n_total']}", "scored / total"),
        stat_card(accuracy_str, "accuracy", "#34d399"),
        stat_card(f"${stats['total_cost_usd']:.3f}", "total cost", "#60a5fa"),
        stat_card(f"H:{stats['confidence_band_counts']['high']} M:{stats['confidence_band_counts']['medium']} L:{stats['confidence_band_counts']['low']}", "confidence bands"),
        stat_card(f"{sens['caught']}/{sens['expected']}", "sensitive-topic recall", "#34d399" if sens['false_positives'] == 0 else "#fbbf24"),
        stat_card(f"{sens['false_positives']}/{sens['total_flagged']}", "sensitive false positives", "#f87171" if sens['false_positives'] else "#34d399"),
        stat_card(f"{reten['caught']}/{reten['expected']}", "retention-risk recall", "#34d399"),
        stat_card(n_by_queue.get("Team Lead Triage", 0), "manager triage", "#fbbf24"),
        stat_card(n_urgent, "urgent review", "#f87171"),
    ])

    filter_defs = [
        ("all", "All", "#e2e8f0", "#1a1d2e", "#334155"),
        ("Service", "Service", QUEUE_COLORS["Service"]["col"], QUEUE_COLORS["Service"]["bg"], QUEUE_COLORS["Service"]["bdr"]),
        ("Success", "Success", QUEUE_COLORS["Success"]["col"], QUEUE_COLORS["Success"]["bg"], QUEUE_COLORS["Success"]["bdr"]),
        ("Sales", "Sales", QUEUE_COLORS["Sales"]["col"], QUEUE_COLORS["Sales"]["bg"], QUEUE_COLORS["Sales"]["bdr"]),
        ("Team Lead Triage", "Team Lead triage", QUEUE_COLORS["Team Lead Triage"]["col"], QUEUE_COLORS["Team Lead Triage"]["bg"], QUEUE_COLORS["Team Lead Triage"]["bdr"]),
        ("urgent", "Urgent only", "#f87171", "#350a0a", "#991b1b"),
    ]
    filter_html = "".join(
        f'<button class="fbtn{"" if cat == "urgent" else " active"}" data-cat="{cat}" style="--col:{col};--bg:{bg};--bdr:{bdr}" onclick="toggleFilter(this)">{label}</button>'
        for cat, label, col, bg, bdr in filter_defs
    )

    cards_html = "".join(card_wrapper(r, i) for i, r in enumerate(results))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>AI Triage - Routing Results</title>
<style>{CSS}</style>
</head>
<body>
<h1>AI triage - routing results</h1>
<p class="subtitle">Source: {html.escape(source_label)} &middot; click any row to expand full reasoning, draft, and confidence rubric</p>

<div class="stats">{stat_cards}</div>

<div class="filter-bar">
  <span class="filter-label">Show</span>
  {filter_html}
  <span class="row-hint" id="row-hint"></span>
</div>

<div class="pipeline" id="pipeline">
{cards_html}
</div>

<script>
function toggleCard(idx) {{
  var d = document.getElementById('detail-' + idx);
  d.classList.toggle('open');
}}
(function() {{
  var btns = document.querySelectorAll('.fbtn');
  var queueBtns = document.querySelectorAll('.fbtn[data-cat]:not([data-cat="all"]):not([data-cat="urgent"])');
  var urgentBtn = document.querySelector('.fbtn[data-cat="urgent"]');

  window.toggleFilter = function(btn) {{
    var cat = btn.dataset.cat;
    if (cat === 'all') {{
      queueBtns.forEach(function(b) {{ b.classList.add('active'); }});
      urgentBtn.classList.remove('active');
    }} else {{
      btn.classList.toggle('active');
    }}
    applyFilters();
  }};

  function applyFilters() {{
    var activeQueues = [];
    var urgentOnly = false;
    btns.forEach(function(b) {{
      if (b.classList.contains('active')) {{
        if (b.dataset.cat === 'urgent') urgentOnly = true;
        else if (b.dataset.cat !== 'all') activeQueues.push(b.dataset.cat);
      }}
    }});
    var cards = document.querySelectorAll('#pipeline .card');
    var shown = 0;
    cards.forEach(function(c) {{
      var matchQueue = activeQueues.length === 0 || activeQueues.indexOf(c.dataset.queue) !== -1;
      var matchUrgent = !urgentOnly || c.dataset.urgent === '1';
      var visible = matchQueue && matchUrgent;
      c.style.display = visible ? '' : 'none';
      if (visible) shown++;
    }});
    document.getElementById('row-hint').textContent = shown + ' of ' + cards.length + ' shown';
  }}
  applyFilters();
}})();
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Build an HTML dashboard from a batch_runner results file.")
    parser.add_argument("results_file", nargs="?", help="Path to a run_*.json file. Defaults to the most recent one in outputs/.")
    args = parser.parse_args()

    if args.results_file:
        results_path = Path(args.results_file)
    else:
        candidates = sorted(OUTPUTS_DIR.glob("run_*.json"))
        if not candidates:
            print("No run_*.json files found in outputs/. Run batch_runner.py first.")
            return
        results_path = candidates[-1]

    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)

    html_content = build_dashboard(data, source_label=results_path.name)
    out_path = results_path.with_suffix(".html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Wrote {out_path}")
    return out_path


if __name__ == "__main__":
    main()
