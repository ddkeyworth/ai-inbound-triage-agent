# Pipeline & Prompts Reference

This document exists so nothing in the build is a black box. It defines every stage in plain English, then reproduces the *actual* text of every real prompt in the system - not a summary or a paraphrase. If a sentence in here doesn't match `pipeline.py`, the code is the source of truth and this file is stale and needs updating.

## How many prompts define this system?

Exactly three - the real system prompts sent to the Claude API at runtime, embedded in `pipeline.py`. These are what the deployed pipeline actually says to the model on every message, reproduced in full below.

## Pipeline stage glossary

Each stage below is a distinct step in a fixed, code-orchestrated sequence (a **workflow**, not an agent deciding its own steps). The one exception, the agentic investigation step, is marked as such.

| Stage | What it means, concretely |
|---|---|
| **Classify + Extract** | One Haiku 4.5 API call. Reads the raw message text and returns a single JSON object: which of Service/Success/Sales it best fits, any other categories that are also plausible, whether it's actually contradictory, the account reference if any, a short issue-type label, sentiment, urgency, and several boolean/array flags (expansion intent, retention-risk language, sensitive-topic matches, matched keywords). Ground truth is never shown to the model - only the message text and generic instructions. |
| **Confidence scoring** | Not an LLM call - pure Python arithmetic over the extraction output (`score_confidence` in `pipeline.py`). An additive/subtractive rubric (see table below) produces a 0-100 score and a high/medium/low band. Deliberately rule-based rather than asking the model "how confident are you 0-100" - every point is independently checkable, not an opaque number the model made up. |
| **Routing / guardrails** | Pure Python (`determine_queue`). Decides the queue (who owns the message) from the extraction + confidence, applying overrides in a fixed priority order (see table below). Includes a deterministic account-size lookup (`is_large_account`) and a regex-based formal-request check (`is_formal_close_cancel`) - both plain Python, no extra API call. |
| **Multi-team loop-in** | Part of `determine_queue`. When a second team has an independent signal (not just uncertainty about the same decision), that team is added to a `loop_in` list rather than taking ownership - a single primary owner is always kept. |
| **Enterprise AE routing** | Part of `determine_queue`. A Sales-category message with a stated team size in the top band(s) (config's `enterprise_ae_team_size_bands`) gets a `sales_handling_path` of "Enterprise AE" instead of "Standard Sales" - a handling-path distinction within Sales, not a 5th top-level queue. |
| **Health/expansion flag** | Pure Python (`health_expansion_flag`). A lightweight, explicitly-caveated "this message mentions growth/expansion" note, shown whenever Success has visibility (owner or looped in). Not a verified account-health score. |
| **Reference retrieval** | Pure Python (`find_matching_article`), no API call. Before drafting, looks up the queue's mock Help Centre/playbook content (`data/help_centre_articles.json`, `success_playbook.json`, `sales_playbook.json`) by keyword overlap against the extraction's `matched_keywords`/`issue_type`. Whether a match was found feeds both the draft prompt and the draft-quality confidence score below. |
| **Drafting** | One Sonnet 5 API call (`draft_response`). Writes a short reply for human review, grounded in the matched reference article if one was found, reading brand guidelines fresh from a JSON file on every call. If it's Service with a missing reference, the draft is a clarification request instead of a guess at resolution. If the queue is Team Lead Triage, the draft says so explicitly rather than presenting a guess as a decision. |
| **Draft-quality confidence** | Not an LLM call - pure Python (`score_draft_confidence`), distinct from the routing confidence score above. Answers "is this specific draft likely good enough to send," not "did this land in the right queue." See the dedicated section below. |
| **Agentic investigation** (the only agentic step) | One-to-four Sonnet 5 API calls in a tool-use loop (`investigate_uncertain_message`), triggered only for low-confidence messages. Unlike every step above, the model itself decides which of three read-only tools (if any) to call - two account lookups plus a Help Centre search - based on what's actually in the message, not a prescribed sequence. Output is advisory text for the human reviewer; it can never send, modify, or action anything. |

## Confidence rubric (exact signals, from `score_confidence`)

| Signal | Effect |
|---|---|
| Account reference present | +35 |
| Single fitting category, no hedging | +35 |
| Category-specific terminology matched | +15 |
| Sentiment/urgency stated unambiguously (not "mixed") | +15 |
| Contradictory category signals | -40 |
| No reference where the category normally expects one | -30 |
| Message very short/generic (<8 words) | -20 |
| Multiple categories plausible (no outright contradiction) | -15 |

Clamped to 0-100. Bands: high >= 80, medium >= 50, low < 50 (config).

## Routing priority order (exact, from `determine_queue`)

1. Sensitive topic present -> queue = Service, unconditionally (never downgraded by low confidence).
2. Else retention-risk language **and** a formal close-account/cancel-subscription request (regex match) -> queue = Service (Support keeps ownership of routine account-lifecycle requests); Success is looped in only if the account is large.
3. Else retention-risk language (softer language, not a formal request) -> queue = Success, unconditionally.
4. Else contradictory signals -> queue = **Team Lead Triage**, not Success.
5. Else confidence score <= Team Lead Triage floor (20/100) -> queue = Team Lead Triage.
6. Else -> queue = the model's predicted category.

Whenever the queue differs from the model's raw predicted category, that raw category is looped in for context rather than lost.

### Why contradictory signals don't default to Success

Routing every contradictory-signals message straight to Success would make Success a dumping ground for ambiguous technical escalations that Support should own, and would push Success toward being reactive (handling overflow triage) rather than proactive. It routes to Team Lead Triage instead - a Support-side escalation point - and Success only gets looped in through the same content-driven signals any other queue would trigger (an expansion mention, a Success category alternative), not merely because the signals were ambiguous.

### Why formal close/cancel requests don't default to Success either

"Close Account" and "Cancel Subscription" are 2 of the 8 real Help Centre support-form categories - a customer explicitly using that form is asking for a routine account-lifecycle action, not necessarily opening a relationship conversation. Support keeps ownership; Success is looped in only when the account is large (`arr_band` in config's `large_account_arr_bands`) - the retention stakes are high enough there to warrant proactive visibility. Softer language ("we'll have to look at other providers") isn't a formal request and keeps the original behaviour: Success owns it directly, since that genuinely is a relationship conversation.

### Three review-priority signals, not one blended "urgent" flag

An earlier version of this build had a single `review_priority` field ("urgent" vs "standard"), set whenever the message was sensitive *or* low-confidence. That conflates two different questions into one flag, and misses a third signal entirely - all three matter to a reviewer for different reasons:

| Signal | True when | What it tells the reviewer |
|---|---|---|
| `confidence_check_needed` | `confidence["band"] == "low"` | The AI's own routing might be wrong - double-check this is really yours before working it. |
| `escalate_to_senior` | a sensitive topic is present | The topic itself (refund, compliance, legal, security incident) warrants a more senior or careful set of eyes, independent of routing confidence. |
| `fast_response_needed` | `extraction["urgency"] == "high"` | The customer's own message reads as urgent - this needs a quick response regardless of who owns it or how confident the routing was. A junior agent can still handle it fast; this isn't about seniority. |

A message can be any combination of these, or none. A calm, low-urgency message can still need `escalate_to_senior` (a coolly-worded refund request); a customer who wrote "URGENT!!" doesn't automatically get escalated to a senior unless the topic itself is sensitive. Keeping them separate means the dashboard can show *which* thing applies, rather than one flag that means three different things depending on context.

---

## Prompt 1 of 3: `classify_and_extract` (model: claude-haiku-4-5)

Purpose: the single call that produces the structured extraction every later stage depends on. Runs on every message, no exceptions.

The system prompt is assembled per-message from config + an optional entry-channel block. Below is the exact template with the config-driven parts shown as `{...}`:

```
You classify inbound customer messages for {company_name},
a B2B project-management SaaS company, into exactly one of:
{categories}.

Service = support/technical issues (login, access, bugs,
integrations, outages, billing problems, refunds, compliance).
Success = existing customer wanting a business review, renewal
discussion, or to grow/expand their usage.
Sales = a prospect or existing customer asking about pricing,
plans, or signing up for something new.

[Entry channel prior - only included if entry_channel is known,
same "helpful prior, never determining" framing as the Success
mailbox note below.]

Reference terminology per category (a hint, not an exhaustive list):
- Service: login, password, 2fa, sso, outage, downtime, bug, error,
  crash, integration, api, sync, webhook, billing, invoice, refund,
  chargeback, compliance, gdpr
- Success: qbr, ebr, renewal, expand, expansion, scale, scaling, grow,
  growth, upgrade, review, account health, onboarding, enterprise,
  new team, new department
- Sales: pricing, price, plan, demo, trial, quote, discount, compare,
  comparison, sign up, signing up, new customer, setup fee,
  contract terms

retention_risk_language: set this true for explicit close/cancel
account or subscription requests, and also for softer but real
language about leaving or switching providers even without a formal
cancellation request. Anger alone ("this is ridiculous for a paying
customer") is NOT retention risk unless the message also expresses
an intent to leave or reconsider the relationship.

team_size_band only matters for Sales-category messages, mirroring a
real Sales/Contact form's "Approx. team size" field: under_10,
10_to_50, 50_to_200, 200_to_1000, 1000_plus, or unknown.

sensitive_topic_flags is a NARROW field. Only use terms from this
exact list, and only when clearly present: {sensitive_topics}.
Match the FULL concept, not a substring - a locked-out login is not
"unauthorized access"; a routine billing question is not a dispute.
[... full worked examples distinguishing near-misses from the real
thing, at temperature=0 for deterministic extraction ...]

Be honest about ambiguity: if a message clearly fits more than one
category, say so via category_alternatives and contradictory_signals
rather than forcing false confidence.
```

**Note:** this quoted block is abbreviated for readability - the real prompt in `pipeline.py` includes the full worked positive/negative examples for the sensitive-topic and retention-risk fields. `pipeline.py` is the source of truth; treat this doc as a plain-English orientation to it, not a byte-exact mirror.

The message itself is sent as the (only) user-turn content, with no ground truth attached. The response is constrained to a strict JSON schema (`output_config: {"format": {"type": "json_schema", ...}}`) - see `EXTRACTION_SCHEMA` in `pipeline.py` for the full field list.

---

## Prompt 2 of 3: `draft_response` (model: claude-sonnet-5)

Purpose: writes the actual reply draft a human reviews before sending. Never called for messages where a prior step failed; always produces a draft, never a sent message.

Three variants of the *instruction* line depending on routing outcome, then a shared brand-guidelines block appended when `data/brand_guidelines.json` is present (read fresh on every call, not cached):

```
[If Service and a reference is required but missing:]
Key information is missing (no account reference). Draft a
brief, polite reply asking the customer for that specific missing
detail. Do not attempt to resolve the issue.

[If routed to Team Lead Triage:]
Draft a brief, helpful reply addressing: {issue_type}. This message's
queue assignment is uncertain and pending manual review by a team lead,
so treat {category} as a best guess only, not a confirmed team. This
is a draft for human review before sending, not a final answer.

[Otherwise:]
Draft a brief, helpful reply for the {queue} team to send this
customer, addressing: {issue_type}. This is a draft for human review
before sending, not a final answer.
```

Reference block (appended when `find_matching_article` finds a matching Help Centre/playbook article for this queue):

```
Relevant reference material found for this message ("{article title}"):
{article answer}
Ground your reply in this - reuse its substance in your own words
rather than inventing an answer, but don't just paste it verbatim if
the customer's specific situation needs a more tailored response.
```

Brand block (appended whenever the guidelines file loads successfully):

```
Brand guidelines for {company_name} (follow these exactly):
Tone: {tone}
Voice principles:
{voice_principles, one per line}
Never use these words/phrases: {banned_words_or_phrases}
Formatting: {formatting}
Sign off with: {sign_off}

Separately, avoid AI-isms - words and patterns that read as
AI-generated rather than human-written:
Never use these words: {avoid_ai_isms.banned_words}
Never use these phrases: {avoid_ai_isms.banned_phrases}
Style rules:
{avoid_ai_isms.style_rules, one per line}
```

Full system prompt is: `You draft short customer-support replies for {company_name}. Keep it to 2-4 sentences unless technical detail requires more, no filler.` + the instruction + the reference block + the brand block. The user-turn content is `Original message: {text}\n\nInstruction: {instruction}`. `thinking` is explicitly disabled for this call - a short drafting task doesn't benefit from extended reasoning, and leaving it on would only inflate cost.

### Draft-quality confidence (the second, distinct confidence score)

The routing confidence score answers one question: *did this message land in the right queue?* It says nothing about whether the drafted reply itself is any good - a message can be routed perfectly and still get a weak, generic, unaided answer, or land in an uncertain queue and still happen to get a well-grounded draft. So `score_draft_confidence` is a second, separate score answering: *is this specific draft likely good enough to send?*

Rule-based, same philosophy as the routing confidence score: was this draft actually grounded in a real, matched reference article, or is it the model's own unaided attempt?

```
if needs_clarification:            band = "n/a"    (no answer was attempted)
elif queue == "Team Lead Triage":   band = "low"    (queue itself unconfirmed)
elif a reference article matched:   band = "high"   (grounded in known-correct source material)
else:                               band = "low"    (fully generative, nothing to check it against)
```

---

## Prompt 3 of 3: `investigate_uncertain_message` (model: claude-sonnet-5, agentic)

Purpose: the only agentic component. Triggered only when `confidence["band"]` is in `investigation_trigger_bands` (currently `["low"]`) - never on the full batch.

System prompt (fixed, no per-message templating):

```
You are helping a human support reviewer triage an uncertain customer
message. You have three read-only tools available: two account
lookups and a Help Centre search. Decide for yourself which, if any,
are worth calling, based on what the message actually contains - do
not call a lookup tool with a reference you are guessing at or
inventing, and do not search the Help Centre with a query unrelated to
what's actually being asked. If the message has no usable reference,
say so plainly rather than calling a tool anyway. When you are done,
write a short (2-3 sentence) note for the human reviewer summarising
what you found and what it means for handling this message.
```

User turn: `Message: {text}\n\nExtracted account reference (if any): {reference or "none found"}`.

Three tools available (`INVESTIGATION_TOOLS` in `pipeline.py`):

- **`lookup_subscription_status(account_reference)`** - live subscription/billing status (plan tier, billing status, seats used, last login). Returns `not_found` if the reference doesn't exist.
- **`lookup_account_context(account_reference)`** - account-level context (plan tier, account age, recent ticket volume, ARR band) for the same reference. Returns `not_found` if there's no account on file.
- **`search_help_centre(query)`** - free-text search over the mock Help Centre articles, returning the best-matching article's title and answer, or `not_found`.

All three are backed by synthetic mock data - read-only, no write capability exists at all. Hard cap of 4 iterations. The model decides for itself, per message, which (if any) of the three tools are worth calling - this is the one place in the whole build where the model chooses its own next action rather than following a fixed sequence, which is what makes it agentic rather than another workflow step.

### Why not just always call all three tools?

Two reasons. First, cost/latency: calling every tool regardless of relevance on every low-confidence message adds real latency for no accuracy benefit on messages where a given tool has nothing useful to return. Second, and more importantly: forcing a fixed "call everything" sequence would turn this back into a workflow, not an agent - the entire point of this being the one agentic step is that the model exercises judgement about *which* lookups are worth making based on what the message actually says, the same judgement a human reviewer exercises before looking things up.

## Error handling and latency (why neither is a production risk)

**Measured, not estimated:** timed on the 120-message reference run (`results/reference_run.json`), run sequentially with no concurrency. Per-message latency: median 6.8s, mean 8.4s, range 5.0-17.7s. Messages that trigger the agentic investigation step (about 38% of this dataset) run measurably slower - median 11.2s, mean 11.8s - versus median 6.3s, mean 6.3s for messages that don't, since that step makes 1-4 additional tool-call round trips before drafting. A full 120-message batch takes roughly 17 minutes end-to-end at this sequential pace.

In production, this pipeline would run as a background enrichment step, not a blocking one: a new message would still land in the normal helpdesk/CRM inbox exactly as it does today, visible and workable by a human immediately. The AI call happens asynchronously and writes its output (category, confidence, draft, flags) onto the ticket once it finishes - it never has to complete before a human can pick up and work the ticket manually.

That means a slow response or a failed API call (rate limit, timeout, partial outage) has a bounded, safe failure mode: that one ticket's enrichment simply arrives late or not at all, and a human handles it exactly as they would have without the AI at all. Nothing blocks, nothing silently mis-routes, and no message is ever auto-sent. `batch_runner.py` sets explicit `timeout=60.0` and `max_retries=3` on the API client so transient failures retry automatically before falling back to "no enrichment yet" rather than raising.

## What is NOT a prompt

`score_confidence`, `determine_queue`, `health_expansion_flag`, and `score_draft_confidence` make no API calls at all - they are plain Python functions operating on the structured output of Prompt 1. No prompt exists for them because none is needed; the whole point of extracting structured fields first is that routing logic can then be ordinary, auditable code instead of another opaque model judgement call.

## Feedback loop: account health/VoC signals

A real CS org doesn't just triage messages in isolation - it knows things about the account before the message even arrives: health score, NPS, CSAT, CES, product feedback, and whether the customer is actually achieving the outcomes they signed up for. This pipeline models that as a second input feeding two of the existing stages, plus a mechanism for learning from real outcomes over time. Nothing here is a hard override - see the routing-priority table above, which this doesn't change.

**Where the signal comes from.** `data/mock_backend.json`'s `health_signals` key - a mock stand-in for a real CS platform (Gainsight-style), keyed by account reference. `get_account_health_context` (pure Python, no API call) looks it up; `account_health_is_risk` applies the thresholds in `config.py` (`health_score_risk_threshold`, `csat_risk_bands`, `ces_risk_bands`, `health_risk_signal_tags`) to decide whether that account counts as "at risk" right now. Not every account has data on file - that's a realistic state (not every account has been surveyed), not a bug, and it's handled the same way a missing reference is handled elsewhere: gracefully, with no signal contributed.

**Where it's used - two places, both soft:**

1. **Confidence scoring** (`score_confidence`). An at-risk account applies `health_risk_confidence_penalty` (-10, about a third of the weight of a hard content signal like contradictory category signals at -40) - only for categories in `health_context_categories` (`Service`, `Success` - a net-new Sales prospect has no account history to weigh). This is deliberately light: account history is weaker evidence about *this specific message* than what the message itself says, so it nudges borderline cases toward review rather than overriding a clear read. A message with an otherwise-clean signal set can still land high confidence even on an at-risk account.
2. **Draft tone** (`draft_response`). When an account is at risk, or when it's exceeding its stated business outcomes, an internal-only context block is appended to the drafting prompt - explicitly instructed never to state the signal to the customer (no draft should ever say "I see you're a detractor"). It shapes tone (more proactive and careful for at-risk accounts, warmer for accounts doing well), not substance.

**The learning half: outcome tagging and calibration, not silent self-adjustment.** `data/outcome_tags.json` models what a reviewer would record after handling a message - was the AI's routing confirmed or corrected, and (where known) did the account's health score move afterward. `calibration_report.py` reads those tags alongside the reference run and health signals, and surfaces patterns - e.g. whether routing stays reliably confirmed on at-risk accounts despite the penalty (suggesting the penalty could be lowered), or whether corrections cluster on at-risk accounts the penalty isn't catching (suggesting it should be raised). It never touches `config.py` itself - a human reads the report and decides, the same auditable pattern as every other tuning knob in this build.

## Honest limitations

- **The confidence rubric needs real recalibration, not just tuning.** Every weight in `score_confidence`'s rubric was chosen by looking at the score distribution on this build's own small synthetic dataset - a reasonable starting point, not a substitute for real usage data. It should be treated as a first draft.
- **The feedback loop is a demonstrated mechanism, not a validated one.** `data/outcome_tags.json` has 6 illustrative example entries because this repo has never had real usage - there's no real outcome history to calibrate against yet. `calibration_report.py` shows the shape of the analysis a real deployment would run, not a finding to act on.
- **This is a prototype, not a production system.** 140 synthetic messages is enough to exercise the pipeline's design and guardrails, not enough to make a statistical accuracy claim that would hold on real, messy production traffic.
- **Attachment/malware scanning is deliberately out of scope.** That's a specialised security capability that belongs in a dedicated scanning service ahead of this pipeline, not homebrewed inside a triage agent.
