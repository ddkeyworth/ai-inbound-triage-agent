# AI Inbound Triage Agent

A working prototype that reads inbound customer messages for a fictional B2B SaaS company ("Thistlewire") and does three things: classifies which team should own it (Service, Success, or Sales), extracts structured details (account reference, issue type, sentiment, urgency), and scores its own confidence in that read - honestly, using a rules-based rubric rather than asking the model to guess a percentage. It also drafts a reply for a human to review, and flags a fourth "Team Lead Triage" queue for messages it genuinely isn't sure about, rather than forcing a guess.

Nothing here is a real company. "Thistlewire" and everything in `data/` is synthetic, built to exercise the pipeline's guardrails on realistic-but-invented scenarios - the point of this project is the pipeline design, not the fictional company behind it.

## Results at a glance

Real, measured results from running the 120-message dev set (20 more held out and never touched during development) - see `results/reference_run.html` for the full per-message breakdown (drafts, confidence scores, reasoning):

- **97.5% accuracy** (117/120). All 3 "misses" are deliberately ambiguous or contradictory messages that scored low confidence and were correctly escalated to Team Lead Triage rather than force-guessed - arguably 120/120 on the behaviour that matters.
- **8/8 sensitive-topic detections, 0 false positives.** 10/10 retention-risk detections, 0 false positives.
- **$1.537 total cost** for 120 messages (~$0.013/message).
- **6.8s median latency per message** (8.4s mean, 5.0-17.7s range), run sequentially with no concurrency. Messages that trigger the investigation step run slower (11.2s median) than ones that don't (6.3s median) - see [`HOW_THE_AI_WORKS.md`](HOW_THE_AI_WORKS.md) for the full breakdown and why this isn't a production risk.
- The agentic investigation step is exercised across its full range in this run: correctly declining to guess a reference when none is given, correctly reporting a reference that doesn't exist in the system, and correctly pulling and reasoning over real account data (including a genuine Help Centre search) when one does.
- The account health/VoC feedback loop fired on 3 real dev-set messages this run, all correctly identified as at-risk accounts - two stayed in the high confidence band despite the penalty (strong signal elsewhere), one (a formal account-closure request) was correctly nudged from high into medium. See `calibration_report.py`.
- Every draft waits for human review, regardless of confidence - nothing here ever auto-sends.

## How it works

Read [`HOW_THE_AI_WORKS.md`](HOW_THE_AI_WORKS.md) for the full pipeline glossary and the literal text of all three real prompts used at runtime.

In short: classify + extract (one Haiku 4.5 call, structured JSON output) -> confidence score (pure Python, an additive rubric over the extracted fields, not an LLM-generated percentage) -> routing/guardrails (pure Python - sensitive topics always route to Service; retention risk routes to Success unless it's a formal, routine account-closure request; contradictory signals escalate to a human-reviewed Team Lead Triage queue rather than defaulting anywhere) -> draft a reply (one Sonnet 5 call, grounded in a matched Help Centre/playbook article where one exists) -> if confidence is low, an agentic investigation step (Sonnet 5, tool-use, the model decides for itself which of three read-only lookups are worth making) produces an advisory note for whoever reviews the message.

## Repo structure

- `pipeline.py` - the generic pipeline: classify/extract, confidence scoring, routing/guardrails, multi-team loop-in, the health/expansion flag, brand-guided drafting, and the agentic investigation step.
- `config.py` - all company-specific configuration (categories, keywords, sensitive topics, thresholds, model choices). Swapping this file for a different company's config should let the same pipeline code run unmodified.
- `batch_runner.py` - CLI batch runner with live progress output and cost/accuracy stats.
- `dashboard.py` - generates a dark, card-based, filterable, expandable HTML results dashboard from any run file.
- `opus_comparison.py` - a real, API-tested comparison of Opus 4.8 vs Haiku 4.5 on the hardest edge cases.
- `live_demo.py` - runs one arbitrary, typed-in-the-moment message through the real pipeline live.
- `regenerate_walkthrough.py` - re-runs a chosen set of message IDs with an optional runtime company-name override, for producing a differently-branded dashboard without ever hardcoding a name into a tracked file.
- `run_eval.py` - a small, fixed eval-as-CI suite (known-answer regression cases) wired to run on every push (see `.github/workflows/eval.yml`).
- `calibration_report.py` - reads `data/outcome_tags.json` (reviewer-recorded outcomes) and account health signals, and surfaces patterns for a human to act on - the "learning" half of the feedback loop. Never edits `config.py` itself.
- `preview_server.py` - a restricted local static file server (blocks `.env`, `.git`, `__pycache__` from being served or listed).
- `data/` - 140 synthetic sample messages (120 dev / 20 held-out) with ground-truth labels, plus mock brand guidelines, help-centre articles, playbooks, backend records, account health/VoC signals, and illustrative outcome tags the pipeline reads from.
- `deck/architecture_diagram.png` / `.html` - the pipeline architecture diagram, including the feedback loop.

## Design choices worth knowing about

- **Two distinct confidence scores.** Routing confidence (did this land in the right queue) and draft-quality confidence (is this specific draft likely good enough to send) are deliberately kept separate - a message can be routed correctly and still get a weak, unaided draft, or vice versa.
- **Three distinct review-priority signals, not one blended "urgent" flag.** `confidence_check_needed` (the routing might be wrong - verify it's really yours), `escalate_to_senior` (the topic is sensitive and needs a more careful set of eyes), and `fast_response_needed` (the customer's own message reads as urgent, regardless of who owns it or how confident the routing is). A message can be any combination of the three, or none - collapsing them into one flag would hide which one actually applies.
- **The held-out set stays permanently unrun.** Rather than a one-time validation pass, it's kept as a standing "pick any message, right now, genuinely unrehearsed" reserve - running it once would burn the thing that makes it useful for a live demo.
- **The one agentic step is deliberately narrow.** Only the investigation step (triggered on low-confidence messages) lets the model choose its own next action - read-only tools, capped at 4 iterations, advisory output only. Everything else is a fixed, auditable workflow, not an agent deciding its own steps.
- **Nothing ever auto-sends.** Every draft, regardless of confidence band, waits for human review before going anywhere.
- **Account health/VoC signals nudge, they don't override.** Health score, NPS, CSAT, CES, product feedback, and business-outcome status softly adjust routing confidence and draft tone for at-risk accounts (see `HOW_THE_AI_WORKS.md`'s "Feedback loop" section) - a small, config-driven penalty, not a hard rule, and only for categories where account history is actually relevant. `calibration_report.py` demonstrates how real outcome data would eventually validate or correct that weighting - it never adjusts anything automatically.

## Running it

```bash
pip install anthropic python-dotenv
# add ANTHROPIC_API_KEY to a .env file in this directory
python batch_runner.py --split dev
python dashboard.py outputs/run_<timestamp>_dev.json
python live_demo.py "type any message here"
python run_eval.py
python calibration_report.py
```

## What this is (and isn't)

This is a prototype built to demonstrate a design pattern - classify/route/draft with rule-based confidence and a narrow, auditable agentic step - not a production system. The confidence rubric's weights were set by looking at this build's own synthetic data and should be treated as a first draft, not a validated model. There's no real customer data anywhere in this repo.
