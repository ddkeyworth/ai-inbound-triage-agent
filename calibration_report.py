"""
Feedback-loop calibration report: reads data/outcome_tags.json (what
reviewers actually recorded after a message was handled) alongside
results/reference_run.json and data/mock_backend.json's health_signals,
and surfaces patterns a human could act on - it never changes config.py
itself.

This is the "learning" half of the feedback loop. The other half - the
account health/VoC signal nudging confidence and draft tone - runs on
every message (see get_account_health_context and account_health_is_risk
in pipeline.py). This script is what would eventually tell you whether
that nudge's weight (health_risk_confidence_penalty in config.py) is
calibrated correctly, once there's enough real outcome history to say
so with any confidence.

Honest limitation: data/outcome_tags.json has 6 illustrative example
entries because this repo has never had real usage - there is no real
history to calibrate against yet. This script demonstrates the shape
of the analysis a real deployment would run, not a validated finding.
Run it again once real outcome tags exist.

Usage: python calibration_report.py
"""

import json
from pathlib import Path

from config import CONFIG
from pipeline import account_health_is_risk, get_account_health_context

DATA_DIR = Path(__file__).parent / "data"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    tags = load_json(DATA_DIR / "outcome_tags.json")["tags"]
    reference_run = load_json(Path(__file__).parent / "results" / "reference_run.json")
    backend = load_json(DATA_DIR / "mock_backend.json")
    results_by_id = {r["id"]: r for r in reference_run["results"]}

    print(f"Calibration report - {len(tags)} outcome tags (illustrative, see module docstring)\n")

    confirmed = [t for t in tags if t["routing_confirmed"]]
    corrected = [t for t in tags if not t["routing_confirmed"]]
    print(f"Routing confirmed by reviewer: {len(confirmed)}/{len(tags)}")
    print(f"Routing corrected by reviewer: {len(corrected)}/{len(tags)}")

    print("\nPer-tag detail:")
    at_risk_scores = []
    not_at_risk_scores = []
    for tag in tags:
        result = results_by_id.get(tag["message_id"])
        if not result:
            print(f"  {tag['message_id']}: not found in reference run, skipping")
            continue

        extraction = result["extraction"]
        health_context = get_account_health_context(extraction, backend)
        is_risk, risk_reasons = account_health_is_risk(health_context, CONFIG)
        score = result["confidence"]["score"]
        (at_risk_scores if is_risk else not_at_risk_scores).append(score)

        risk_note = f"AT-RISK ({'; '.join(risk_reasons)})" if is_risk else "not flagged at-risk"
        confirmed_note = "confirmed" if tag["routing_confirmed"] else "CORRECTED"
        health_delta = tag.get("health_score_change_30d")
        health_note = f", health score {health_delta:+d} over next 30d" if health_delta is not None else ""
        print(f"  {tag['message_id']}: confidence={score} ({risk_note}) - {confirmed_note}{health_note}")

    print("\nConfidence score by health-risk status (small sample - directional only):")
    if at_risk_scores:
        print(f"  At-risk accounts:     n={len(at_risk_scores)}  avg confidence={sum(at_risk_scores)/len(at_risk_scores):.0f}")
    else:
        print("  At-risk accounts:     n=0")
    if not_at_risk_scores:
        print(f"  Not flagged at-risk:  n={len(not_at_risk_scores)}  avg confidence={sum(not_at_risk_scores)/len(not_at_risk_scores):.0f}")
    else:
        print("  Not flagged at-risk:  n=0")

    print(
        "\nWhat this would tell you with real data: if routing stays "
        "reliably 'confirmed' on at-risk accounts despite the confidence "
        "penalty, health_risk_confidence_penalty in config.py is probably "
        "too aggressive and could be lowered. If corrections cluster on "
        "at-risk accounts the penalty isn't currently catching, it's "
        "probably too weak, or health_score_risk_threshold needs "
        "tightening. Either way, this script only surfaces the pattern - "
        "a human decides whether and how to adjust config.py, the same "
        "auditable pattern as every other tuning knob in this build."
    )


if __name__ == "__main__":
    main()
