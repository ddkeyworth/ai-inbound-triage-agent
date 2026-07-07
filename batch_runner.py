"""
Batch runner: executes the pipeline over the sample messages and writes
a timestamped results file plus aggregate cost/accuracy stats.

Re-run safety: the sample messages are read-only test fixtures, never
mutated here. Each run writes to a new timestamped file rather than
overwriting a fixed output, so a bad or partial run never destroys a
previous good one. Supports running the full set, a single split, or a
small subset of message IDs so iteration doesn't require a full re-run.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from config import CONFIG
from pipeline import process_message

PROGRESS_PATH = Path(__file__).parent / "outputs" / "progress.json"

# Published per-million-token pricing for the models this pipeline uses.
# Pinned here (not fetched live) so cost figures are reproducible run to run.
PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
}

DATA_PATH = Path(__file__).parent / "data" / "sample_messages.json"
OUTPUTS_DIR = Path(__file__).parent / "outputs"


def load_messages(split=None, ids=None):
    with open(DATA_PATH, encoding="utf-8") as f:
        messages = json.load(f)
    if ids:
        wanted = set(ids)
        messages = [m for m in messages if m["id"] in wanted]
    elif split and split != "all":
        messages = [m for m in messages if m["split"] == split]
    return messages


def compute_cost(usage_list):
    total = 0.0
    for u in usage_list:
        rates = PRICING[u["model"]]
        total += u["input_tokens"] / 1_000_000 * rates["input"]
        total += u["output_tokens"] / 1_000_000 * rates["output"]
    return total


def write_progress(current, total, started_at, last_message_id, status):
    elapsed = time.monotonic() - started_at
    avg_per_message = elapsed / current if current > 0 else None
    remaining = (
        avg_per_message * (total - current)
        if avg_per_message is not None and status == "running"
        else 0
    )
    PROGRESS_PATH.parent.mkdir(exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "current": current,
            "total": total,
            "percent": round(current / total * 100, 1) if total else 0,
            "elapsed_seconds": round(elapsed, 1),
            "avg_seconds_per_message": round(avg_per_message, 2) if avg_per_message else None,
            "estimated_remaining_seconds": round(remaining, 1),
            "last_message_id": last_message_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)


def run_batch(messages, client, config):
    results = []
    total = len(messages)
    started_at = time.monotonic()
    write_progress(0, total, started_at, None, "running")
    for i, msg in enumerate(messages, start=1):
        msg_started = time.monotonic()
        result = process_message(client, msg, config)
        result["latency_seconds"] = round(time.monotonic() - msg_started, 3)
        result["ground_truth_category"] = msg["ground_truth_category"]
        result["split"] = msg["split"]
        result["edge_case_type"] = msg.get("edge_case_type")
        result["expected_sensitive_topic"] = msg.get("sensitive_topic", False)
        result["expected_retention_risk_override"] = msg.get("retention_risk_override", False)
        result["cost"] = compute_cost(result.get("usage", []))
        results.append(result)
        write_progress(i, total, started_at, msg["id"], "running")
        print(f"  [{i}/{total}] {msg['id']} -> queue={result.get('queue')} confidence={result.get('confidence', {}).get('band')}")
    write_progress(total, total, started_at, messages[-1]["id"] if messages else None, "complete")
    return results


def compute_stats(results, categories):
    scored = [r for r in results if "extraction" in r]
    failed = [r for r in results if "extraction" not in r]

    confusion = {gt: {pred: 0 for pred in categories} for gt in categories}
    for r in scored:
        gt = r["ground_truth_category"]
        pred = r["extraction"]["category"]
        if gt in confusion and pred in confusion[gt]:
            confusion[gt][pred] += 1

    per_category = {}
    for cat in categories:
        tp = confusion[cat][cat] if cat in confusion else 0
        fn = sum(confusion[cat][p] for p in categories if p != cat) if cat in confusion else 0
        fp = sum(confusion[gt][cat] for gt in categories if gt != cat and cat in confusion.get(gt, {}))
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        per_category[cat] = {"precision": precision, "recall": recall, "support": tp + fn}

    correct = sum(1 for r in scored if r["extraction"]["category"] == r["ground_truth_category"])
    overall_accuracy = correct / len(scored) if scored else None

    # Guardrail-specific accuracy: did we detect the signals the test data
    # was deliberately built to exercise (recall), and did we avoid flagging
    # ones that weren't supposed to be flagged (false positives/precision)?
    sensitive_cases = [r for r in scored if r["expected_sensitive_topic"]]
    sensitive_caught = sum(1 for r in sensitive_cases if r["extraction"]["sensitive_topic_flags"])
    sensitive_flagged_total = sum(1 for r in scored if r["extraction"]["sensitive_topic_flags"])
    sensitive_false_positives = sum(
        1 for r in scored if r["extraction"]["sensitive_topic_flags"] and not r["expected_sensitive_topic"]
    )

    retention_cases = [r for r in scored if r["expected_retention_risk_override"]]
    retention_caught = sum(1 for r in retention_cases if r["extraction"]["retention_risk_language"])
    retention_flagged_total = sum(1 for r in scored if r["extraction"]["retention_risk_language"])
    retention_false_positives = sum(
        1 for r in scored if r["extraction"]["retention_risk_language"] and not r["expected_retention_risk_override"]
    )

    confidence_bands = {"high": 0, "medium": 0, "low": 0}
    for r in scored:
        confidence_bands[r["confidence"]["band"]] += 1

    total_cost = sum(r["cost"] for r in results)

    latencies = sorted(r["latency_seconds"] for r in results if "latency_seconds" in r)
    investigated_latencies = sorted(
        r["latency_seconds"] for r in results if r.get("investigation_summary") and "latency_seconds" in r
    )

    def _percentile(values, pct):
        if not values:
            return None
        idx = min(len(values) - 1, int(len(values) * pct))
        return round(values[idx], 2)

    return {
        "n_total": len(results),
        "n_scored": len(scored),
        "n_failed": len(failed),
        "overall_accuracy": overall_accuracy,
        "confusion_matrix": confusion,
        "per_category": per_category,
        "confidence_band_counts": confidence_bands,
        "sensitive_topic_detection": {
            "expected": len(sensitive_cases),
            "caught": sensitive_caught,
            "total_flagged": sensitive_flagged_total,
            "false_positives": sensitive_false_positives,
        },
        "retention_risk_detection": {
            "expected": len(retention_cases),
            "caught": retention_caught,
            "total_flagged": retention_flagged_total,
            "false_positives": retention_false_positives,
        },
        "total_cost_usd": round(total_cost, 6),
        "latency_seconds": {
            "min": round(latencies[0], 2) if latencies else None,
            "median": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": round(latencies[-1], 2) if latencies else None,
            "median_when_investigated": _percentile(investigated_latencies, 0.5),
            "max_when_investigated": round(investigated_latencies[-1], 2) if investigated_latencies else None,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run the AI message-routing pipeline over sample messages.")
    parser.add_argument("--split", choices=["dev", "held_out", "all"], default="dev",
                         help="Which split to run (default: dev). Ignored if --ids is given.")
    parser.add_argument("--ids", nargs="+", help="Specific message IDs to run instead of a full split.")
    args = parser.parse_args()

    load_dotenv()
    # Explicit timeout + retry config rather than relying on SDK defaults -
    # 3 retries with exponential backoff on transient errors (rate limits,
    # 5xx, connection drops), 60s per-call timeout so a hung request can't
    # stall the whole batch. Real support volume runs async in the
    # background against whatever inbox the message already sits in (see
    # PIPELINE_REFERENCE.md), so a slow or failed call never blocks a
    # human from working the ticket manually in the meantime - it only
    # means the AI enrichment arrives late or not at all for that message,
    # and process_message() already degrades that to human review safely.
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)

    messages = load_messages(split=args.split, ids=args.ids)
    if not messages:
        print("No messages matched the given split/ids.")
        return

    print(f"Running {len(messages)} message(s)...")
    results = run_batch(messages, client, CONFIG)
    stats = compute_stats(results, CONFIG["categories"])

    OUTPUTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.split if not args.ids else "custom"
    out_path = OUTPUTS_DIR / f"run_{timestamp}_{label}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "results": results}, f, indent=2)

    print(f"\nWrote {out_path}")
    print(f"Scored: {stats['n_scored']}/{stats['n_total']} (failed: {stats['n_failed']})")
    print(f"Overall accuracy: {stats['overall_accuracy']}")
    print(f"Confidence bands: {stats['confidence_band_counts']}")
    print(f"Sensitive topic detection: {stats['sensitive_topic_detection']['caught']}/{stats['sensitive_topic_detection']['expected']} "
          f"(false positives: {stats['sensitive_topic_detection']['false_positives']}/{stats['sensitive_topic_detection']['total_flagged']} flagged)")
    print(f"Retention risk detection: {stats['retention_risk_detection']['caught']}/{stats['retention_risk_detection']['expected']} "
          f"(false positives: {stats['retention_risk_detection']['false_positives']}/{stats['retention_risk_detection']['total_flagged']} flagged)")
    print(f"Total cost: ${stats['total_cost_usd']}")
    lat = stats["latency_seconds"]
    print(f"Latency (seconds): min={lat['min']} median={lat['median']} p95={lat['p95']} max={lat['max']} "
          f"| when investigated: median={lat['median_when_investigated']} max={lat['max_when_investigated']}")
    return out_path


if __name__ == "__main__":
    main()
