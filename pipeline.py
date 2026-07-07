"""
Generic classify -> extract -> confidence -> draft -> guardrail pipeline.

Nothing in this file is company-specific - all company detail is read
from the config object passed in, so the same code should run for a
different company by swapping the config. See config.py for the
values used at demo time.
"""

import json
import re
from pathlib import Path

import anthropic

BRAND_GUIDELINES_PATH = Path(__file__).parent / "data" / "brand_guidelines.json"
MOCK_BACKEND_PATH = Path(__file__).parent / "data" / "mock_backend.json"
REFERENCE_CONTENT_PATHS = {
    "Service": Path(__file__).parent / "data" / "help_centre_articles.json",
    "Success": Path(__file__).parent / "data" / "success_playbook.json",
    "Sales": Path(__file__).parent / "data" / "sales_playbook.json",
}


def load_brand_guidelines():
    """Read fresh on every call (not cached at import time) so that
    updating the guidelines file changes the next draft immediately -
    an informal stand-in for a live brand-platform query (e.g. Frontify),
    without needing a real MCP connection for this demo."""
    try:
        with open(BRAND_GUIDELINES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_mock_backend():
    """SYNTHETIC TEST DATA - stands in for live calls to a real
    subscription/billing API and product-usage analytics, plus a
    CRM/billing system for account context. See the README's 'test
    system vs. real system' framing for how this would be swapped in a
    real deployment."""
    try:
        with open(MOCK_BACKEND_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"subscriptions": {}, "accounts": {}}


def load_reference_content(queue):
    """Read fresh on every call, same pattern as load_brand_guidelines -
    a mock stand-in for a real Help Centre (Service) or team playbook
    (Success/Sales) search. This is deliberately a plain, lightweight
    tagged list matched by keyword overlap, not a vector index or model
    call - retrieval-grounded drafting doesn't need to be expensive, and
    demonstrating that cheaply here is the point. Team Lead Triage has no
    dedicated reference content (its category is itself unconfirmed)."""
    path = REFERENCE_CONTENT_PATHS.get(queue)
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("articles", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def find_matching_article(extraction, queue, min_overlap=1):
    """Rule-based retrieval: score every article in the queue's reference
    content by keyword overlap against the message's matched_keywords and
    issue_type, return the best match if it clears min_overlap. No LLM
    call - this is plain Python so a "was a real match used" signal can
    feed the draft-quality confidence score (see score_draft_confidence)
    without paying for a second model judgement about its own answer."""
    articles = load_reference_content(queue)
    if not articles:
        return None

    haystack_terms = set(t.lower() for t in extraction.get("matched_keywords", []))
    issue_words = set(extraction.get("issue_type", "").lower().split())
    haystack_terms |= issue_words

    best, best_score = None, 0
    for article in articles:
        tags = set(t.lower() for t in article.get("tags", []))
        score = 0
        for term in haystack_terms:
            if any(term in tag or tag in term for tag in tags):
                score += 1
        if score > best_score:
            best, best_score = article, score

    return best if best_score >= min_overlap else None


INVESTIGATION_TOOLS = [
    {
        "name": "lookup_subscription_status",
        "description": (
            "Look up live subscription/billing status for an account "
            "reference (plan tier, billing status, seats used, last "
            "login). Returns not_found if the reference does not exist "
            "in the system - only call this with a reference that "
            "actually appears in the message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_reference": {"type": "string", "description": "The account reference, e.g. ACC-12345"},
            },
            "required": ["account_reference"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lookup_account_context",
        "description": (
            "Look up account-level context (plan tier, account age, "
            "recent ticket volume, ARR band) associated with an account "
            "reference. Returns not_found if there is no account on "
            "file for that reference - only call this with a reference "
            "that actually appears in the message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_reference": {"type": "string", "description": "The account reference to look up context for"},
            },
            "required": ["account_reference"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_help_centre",
        "description": (
            "Search the Help Centre knowledge base by free-text query "
            "(e.g. 'two-factor login locked out', 'duplicate charge'). "
            "Returns the best-matching article's title and answer, or "
            "not_found if nothing matches well enough. Use this to "
            "check whether a known, documented answer already exists "
            "for what the customer is describing - not for "
            "account-specific data (use the other two tools for that)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A short free-text description of the issue to search for"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def _search_articles_by_text(query, articles):
    query_words = set(query.lower().split())
    best, best_score = None, 0
    for article in articles:
        tags = set(t.lower() for t in article.get("tags", []))
        score = sum(1 for w in query_words if any(w in tag or tag in w for tag in tags))
        if score > best_score:
            best, best_score = article, score
    return best if best_score >= 1 else None


def _execute_investigation_tool(tool_name, tool_input, backend):
    if tool_name == "search_help_centre":
        articles = load_reference_content("Service")
        match = _search_articles_by_text(tool_input.get("query", ""), articles)
        if not match:
            return json.dumps({"status": "not_found"})
        return json.dumps({"title": match["title"], "answer": match["answer"]})

    ref = tool_input.get("account_reference", "")
    if tool_name == "lookup_subscription_status":
        record = backend.get("subscriptions", {}).get(ref)
    elif tool_name == "lookup_account_context":
        record = backend.get("accounts", {}).get(ref)
    else:
        return json.dumps({"error": f"unknown tool {tool_name}"})
    return json.dumps(record) if record else json.dumps({"status": "not_found"})


def investigate_uncertain_message(client, message_text, extraction, config, max_iterations=4):
    """The one agentic component in this build - everything
    else is a deterministic workflow (see Architecture). For messages
    the base pipeline is uncertain about, the model decides for itself
    which read-only lookups (if any) are worth making before a human
    reviews the message, rather than a fixed, prescribed sequence.

    Prudent by construction: read-only tools only (no ability to send,
    modify, or action anything), a bounded iteration cap, triggered only
    on a narrow subset of messages (not the full batch), and the output
    is advisory text fed into the same human-review step that already
    exists - it never bypasses "nothing auto-sent without review"."""
    backend = load_mock_backend()
    system_prompt = (
        "You are helping a human support reviewer triage an uncertain "
        "customer message. You have three read-only tools available: two "
        "account lookups and a Help Centre search. Decide for yourself "
        "which, if any, are worth calling, based on what the message "
        "actually contains - do not call a lookup tool with a reference "
        "you are guessing at or inventing, and do not search the Help "
        "Centre with a query unrelated to what's actually being asked. "
        "If the message has no usable reference, say so plainly rather "
        "than calling a tool anyway. When you are done, write a short "
        "(2-3 sentence) note for the human reviewer summarising what you "
        "found and what it means for handling this message."
    )
    messages = [{
        "role": "user",
        "content": (
            f"Message: {message_text}\n\n"
            f"Extracted account reference (if any): "
            f"{extraction['account_reference'] or 'none found'}"
        ),
    }]

    usage = []
    for _ in range(max_iterations):
        response = client.messages.create(
            model=config["models"]["investigate"],
            max_tokens=500,
            thinking={"type": "disabled"},
            system=system_prompt,
            tools=INVESTIGATION_TOOLS,
            messages=messages,
        )
        usage.append({
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model": config["models"]["investigate"],
        })
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = next((b.text for b in response.content if b.type == "text"), "")
            return final_text, usage

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _execute_investigation_tool(block.name, block.input, backend)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

    return "Investigation did not conclude within the iteration limit.", usage


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["Service", "Success", "Sales"],
            "description": "The single best-fitting category for this message.",
        },
        "category_alternatives": {
            "type": "array",
            "items": {"type": "string", "enum": ["Service", "Success", "Sales"]},
            "description": "Any other categories that are also plausible. Empty if the message clearly fits only one category.",
        },
        "contradictory_signals": {
            "type": "boolean",
            "description": "True only if the message contains language pulling toward two categories at once (not just an aside), not merely because it mentions more than one topic.",
        },
        "account_reference": {
            "type": "string",
            "description": "The account reference mentioned in the message (e.g. an account or ticket number), or an empty string if none is present.",
        },
        "issue_type": {
            "type": "string",
            "description": "A short (2-6 word) label for what the message is actually about, e.g. 'login lockout' or 'renewal discussion'.",
        },
        "sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative", "mixed"],
        },
        "urgency": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "expansion_intent_language": {
            "type": "boolean",
            "description": "True if the message contains language suggesting the customer wants to grow, scale, or expand their use of the platform.",
        },
        "retention_risk_language": {
            "type": "boolean",
            "description": "True if the message explicitly asks to close or cancel the account/subscription, or contains language threatening or seriously considering leaving/switching away from the company - even if phrased as frustration rather than a formal request.",
        },
        "team_size_band": {
            "type": "string",
            "enum": ["under_10", "10_to_50", "50_to_200", "200_to_1000", "1000_plus", "unknown"],
            "description": "Only relevant for Sales-category messages, mirroring a real Sales/Contact form's 'Approx. team size' field. Set this from an explicit or clearly-implied team size the customer states (e.g. 'we've got about 40 people who'd use this' -> 10_to_50). 'unknown' if no size is stated or implied - do not guess a band from vague language like 'a big team' or 'growing fast' alone.",
        },
        "sensitive_topic_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only topics from the provided sensitive-topics reference list that are clearly present. Ordinary product or service issues are NOT sensitive topics on their own, even if urgent or the customer is upset. Empty if none of the listed topics apply.",
        },
        "matched_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Category-specific reference terms from the provided keyword list that clearly appear in or are closely paraphrased by the message.",
        },
        "message_length_words": {
            "type": "integer",
            "description": "Approximate word count of the original message.",
        },
        "reasoning": {
            "type": "string",
            "description": "One or two sentences on why this category and these signals were chosen.",
        },
    },
    "required": [
        "category", "category_alternatives", "contradictory_signals",
        "account_reference", "issue_type", "sentiment", "urgency",
        "expansion_intent_language", "retention_risk_language", "team_size_band",
        "sensitive_topic_flags", "matched_keywords", "message_length_words", "reasoning",
    ],
    "additionalProperties": False,
}


def classify_and_extract(client, message_text, config, entry_channel=None):
    """Single Haiku call: classify + extract structured fields.

    Ground truth is never included in this prompt - only the message
    text and generic config-driven instructions, per the re-run safety
    design (no train/test contamination).
    """
    keyword_lines = "\n".join(
        f"- {cat}: {', '.join(terms)}"
        for cat, terms in config["category_keywords"].items()
    )
    sensitive_topics_line = ", ".join(config["sensitive_topics"])
    channel_block = ""
    if entry_channel == "Success":
        channel_block = (
            f"\nEntry channel prior: this message was submitted via "
            f"{config['company_name']}'s Success mailbox. Unlike the Support "
            f"and Sales forms, this is a plain, unstructured inbox with no "
            f"form fields guiding what gets sent there - in practice it "
            f"fills up with a genuine mix of Service, Success, and Sales "
            f"content, because nothing about the channel itself signals "
            f"which one a message is. Treat this channel as carrying "
            f"essentially no predictive value for category - do not lean on "
            f"it at all, and classify based on message content alone as you "
            f"would with no entry channel provided.\n"
        )
    elif entry_channel:
        channel_block = (
            f"\nEntry channel prior: this message was submitted via "
            f"{config['company_name']}'s {entry_channel} inbound form. "
            f"There is no dedicated Success form - Support-channel messages "
            f"are usually Service issues, Sales-channel messages are usually "
            f"Sales, but existing customers often use whichever form is in "
            f"front of them (e.g. an existing account asking about "
            f"upgrading frequently goes through the Sales form even though "
            f"the real need is a Success conversation). Treat entry channel "
            f"as a helpful prior, never as a determining factor - if the "
            f"message content clearly points to a different category, "
            f"trust the content over the channel.\n"
        )
    system_prompt = (
        f"You classify inbound customer messages for {config['company_name']}, "
        f"a B2B project-management SaaS company, into exactly one of: "
        f"{', '.join(config['categories'])}.\n\n"
        f"Service = support/technical issues (login, access, bugs, "
        f"integrations, outages, billing problems, refunds, compliance).\n"
        f"Success = existing customer wanting a business review, renewal "
        f"discussion, or to grow/expand their usage.\n"
        f"Sales = a prospect or existing customer asking about pricing, "
        f"plans, or signing up for something new.\n"
        f"{channel_block}\n"
        f"Reference terminology per category (a hint, not an exhaustive "
        f"list):\n{keyword_lines}\n\n"
        f"retention_risk_language: set this true for explicit close/cancel "
        f"account or subscription requests, phrases like: "
        f"{', '.join(config['retention_risk_signals'])}, and also for "
        f"softer but real language about leaving or switching providers "
        f"even without a formal cancellation request. This exists so "
        f"retention risk routes correctly regardless of how it's phrased. "
        f"It requires the message to actually contain leaving/switching/"
        f"cancelling/downgrading language, however soft - anger, frustration, "
        f"or a customer describing themselves as a 'paying customer' who "
        f"deserves better is NOT by itself retention risk, even if strongly "
        f"worded, unless the message also expresses an intent to leave or "
        f"reconsider the relationship. Example that should NOT be flagged: "
        f"'this is ridiculous for a paying customer, please fix it' (angry, "
        f"but no leaving/switching language). Example that SHOULD be "
        f"flagged: 'if this keeps happening we'll have to look at other "
        f"providers' (soft but real switching language).\n\n"
        f"team_size_band only matters for Sales-category messages, "
        f"mirroring a real Sales/Contact form's 'Approx. team size' field: "
        f"under_10, 10_to_50, 50_to_200, 200_to_1000, 1000_plus, or "
        f"unknown. Set it from an explicit or clearly-implied stated size "
        f"(e.g. 'our team is about 30 people' -> 10_to_50, 'we have around "
        f"800 employees who'd be on this' -> 200_to_1000). Use 'unknown' "
        f"whenever no size is stated or clearly implied - do not infer a "
        f"band from vague language like 'a big team' or 'growing fast' "
        f"alone.\n\n"
        f"sensitive_topic_flags is a NARROW field. Only use terms from this "
        f"exact list, and only when clearly present: {sensitive_topics_line}. "
        f"Match the FULL concept, not a substring - the word 'access' "
        f"appearing anywhere does NOT mean 'unauthorized access' applies; "
        f"that term means someone other than the account owner is actively "
        f"in the account right now without permission - not a routine "
        f"password reset, a locked-out login, or a permissions question. A "
        f"customer locked out of their own account is a completed access "
        f"problem to troubleshoot, not an active security incident - do "
        f"not flag it even though 'access' is involved. Likewise "
        f"'overcharge'/'billing dispute' require the customer actually "
        f"disputing a charge as wrong or unacceptable, not just asking "
        f"about or being surprised by an invoice amount, and not a "
        f"neutral, matter-of-fact report of a duplicate charge or "
        f"data-entry mistake with no disputing language - but DO flag it "
        f"if the customer frames the charge as wrong, unacceptable, "
        f"ridiculous, or otherwise objects to it, even without a formal "
        f"dispute process invoked. 'Compliance' requires the customer "
        f"themselves to raise an actual regulatory, legal, or compliance "
        f"concern (using language like 'compliance', 'breaching', "
        f"'regulation', 'legal requirement', or asking what's required to "
        f"stay compliant) - not a routine complaint about a feature being "
        f"confusing, inconsistent, or missing, where the customer is not "
        f"themselves invoking compliance/regulatory language. A message "
        f"being urgent, negative, or about a real product problem (a "
        f"login bug, a sync issue, a confusing permissions error, a "
        f"feature behaving inconsistently with no explanation) is NOT by "
        f"itself a sensitive topic - leave this field empty unless one of "
        f"the listed topics specifically and fully applies. Examples that "
        f"should NOT be flagged: 'I can't log in, it says my password is "
        f"wrong' (routine access issue, not a security incident), 'the "
        f"invoice amount doesn't match what I expected, can you check' "
        f"(routine discrepancy, not a dispute), 'my account got downgraded "
        f"automatically and now I'm missing features, what happened' "
        f"(routine plan/billing confusion, not a breach), 'we got charged "
        f"twice for the same seat by mistake' (a billing error to correct, "
        f"not a disputed overcharge), 'a teammate got locked out then "
        f"un-locked then locked out again, which is it' (a confusing "
        f"status to clarify, not a security incident). Examples that "
        f"SHOULD be flagged: 'I want a refund', 'I'm disputing this charge "
        f"as wrong and want it reversed', 'this is a GDPR data request', "
        f"'we were charged twice and this is ridiculous for a paying "
        f"customer' (the customer is objecting to the charge, not "
        f"neutrally reporting it), 'someone logged into our workspace who "
        f"shouldn't have access, we think we've been hacked' (an active "
        f"security incident), 'what compliance documentation do we need, "
        f"we want to make sure we're not breaching anything' (the customer "
        f"is themselves raising a compliance/regulatory concern). Note the "
        f"two 'charged twice' examples above differ only in tone, not "
        f"facts - both describe the same kind of duplicate charge, but one "
        f"reports it neutrally ('by mistake', no objection) and the other "
        f"objects to it ('ridiculous', a paying customer being treated "
        f"unfairly). Any negative, objecting adjective attached to the "
        f"charge itself - ridiculous, unacceptable, wrong, unfair, a joke, "
        f"outrageous - is disputing language on its own, even paired with "
        f"an unrelated complaint like a slow response, and is enough to "
        f"flag it; don't let a plausible neutral reading of a similar-"
        f"sounding example override actual objecting words in this "
        f"message.\n\n"
        f"Be honest about ambiguity: if a message clearly fits more than "
        f"one category, say so via category_alternatives and "
        f"contradictory_signals rather than forcing false confidence."
    )

    response = client.messages.create(
        model=config["models"]["classify_extract"],
        max_tokens=1024,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": message_text}],
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
    )

    text = next(b.text for b in response.content if b.type == "text")
    extraction = json.loads(text)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "model": config["models"]["classify_extract"],
    }
    return extraction, usage


def score_confidence(extraction, config):
    """Rule-based 0-100 confidence score from defined signals.

    Deliberately not asked of the model as a percentage - every
    contributing signal here is visible and independently checkable.
    """
    score = 0
    reasons = []

    if extraction["account_reference"]:
        score += 35
        reasons.append("+35 account reference present")

    single_category = (
        not extraction["contradictory_signals"]
        and len(extraction["category_alternatives"]) == 0
    )
    if single_category:
        score += 35
        reasons.append("+35 single fitting category, no hedging")

    if extraction["matched_keywords"]:
        score += 15
        reasons.append("+15 category-specific terminology matched")

    if extraction["sentiment"] != "mixed":
        score += 15
        reasons.append("+15 sentiment/urgency stated unambiguously")

    if extraction["contradictory_signals"]:
        score -= 40
        reasons.append("-40 contradictory category signals")

    expects_reference = extraction["category"] in config["categories_expecting_reference"]
    if expects_reference and not extraction["account_reference"]:
        score -= 30
        reasons.append("-30 no reference where category normally expects one")

    if extraction["message_length_words"] < 8:
        score -= 20
        reasons.append("-20 message very short/generic")

    if len(extraction["category_alternatives"]) > 0 and not extraction["contradictory_signals"]:
        score -= 15
        reasons.append("-15 multiple categories plausible")

    score = max(0, min(100, score))

    bands = config["confidence_bands"]
    if score >= bands["high"]:
        band = "high"
    elif score >= bands["medium"]:
        band = "medium"
    else:
        band = "low"

    return {"score": score, "band": band, "reasons": reasons}


def is_large_account(extraction, config, backend):
    """Deterministic, rule-based account-size check (no API call) - reads
    the same mock_backend.json account records the agentic investigation
    tool can look up, but this lookup always runs, whereas that tool only
    fires for low-confidence messages. Used to decide whether Success
    gets looped in on account-lifecycle requests (see determine_queue),
    not to gate anything else."""
    ref = extraction.get("account_reference") or ""
    account = backend.get("accounts", {}).get(ref)
    if not account:
        return False
    return account.get("arr_band") in config.get("large_account_arr_bands", [])


def determine_queue(extraction, confidence, config=None, message_text=None, backend=None):
    """Guardrail routing: sensitive topics and retention risk override
    the raw category; contradictory signals escalate to Team Lead Triage
    (a Support-side escalation point, not an automatic hand-off to
    Success - see below); very low confidence routes to the same Team
    Lead Triage queue for manual assignment rather than guessing a
    category queue; low confidence (more broadly) routes to human review
    regardless of category.

    Sensitive-topic and retention-risk overrides are unconditional -
    they win even when confidence is at the Team Lead Triage floor, since
    those are safety/retention wins that must never be downgraded to
    "unassigned". Team Lead Triage only applies to the residual case: no
    guardrail fired, but the raw category guess itself is too weak to
    trust (see config's team_lead_triage_confidence_floor for how the
    threshold was chosen).

    Contradictory signals do NOT default to Success. A message pulling
    toward two categories at once (e.g. a technical issue mixed with an
    expansion mention) is a Support-side escalation, not automatically a
    Success one - defaulting every ambiguous case to Success would make
    Success a dumping ground for technical escalations and turn it
    reactive instead of proactive. It routes to Team Lead Triage instead;
    Success is looped in via the ordinary content-driven signals below
    (an expansion mention, a Success category alternative) exactly as it
    would be for any other queue, not because the signals were merely
    contradictory.

    Formal close/cancel requests are a distinct case from softer
    retention-risk language. "Close Account" and "Cancel Subscription"
    are 2 of the 8 real Help Centre support-form categories - a routine
    account-lifecycle action a customer explicitly requested through the
    Support channel, not necessarily an active relationship conversation.
    Support keeps ownership by default; Success is looped in as secondary
    only when the account is large (see is_large_account) - the retention
    stakes are high enough there to warrant proactive visibility, without
    making Success own every account closure/cancellation regardless of
    size. Softer retention language (e.g. "we'll have to look at other
    providers") is NOT a formal request and keeps the existing behavior:
    Success owns directly, since that genuinely is a relationship
    conversation, not routine account admin.

    Multi-team loop-in: some messages need more than one
    team's awareness at once (a real support issue, a retention risk,
    an expansion mention, all in one message). Rather than splitting
    ownership - the classic "everyone owns it, no one owns it" failure -
    a single primary queue is always kept, and any other team with a
    independently detected signal is added to a loop_in list
    instead of taking ownership. This is a distinct mechanism from
    category_alternatives/contradictory_signals, which represent
    uncertainty about which single category applies, not confirmed
    multiple simultaneous needs."""
    guardrail_flags = []
    config = config or {}
    backend = backend if backend is not None else load_mock_backend()

    is_sensitive = bool(extraction["sensitive_topic_flags"])
    is_retention_risk = extraction["retention_risk_language"]
    triage_floor = config.get("team_lead_triage_confidence_floor", -1)
    large_account = is_large_account(extraction, config, backend)

    is_formal_close_cancel = False
    if message_text:
        text_lower = message_text.lower()
        is_formal_close_cancel = any(
            re.search(pattern, text_lower)
            for pattern in config.get("formal_close_cancel_patterns", [])
        )

    if is_sensitive:
        queue = "Service"
        guardrail_flags.append("sensitive_topic_always_service")
    elif is_retention_risk and is_formal_close_cancel:
        queue = "Service"
        guardrail_flags.append("formal_close_cancel_support_owned")
        if large_account:
            guardrail_flags.append("large_account_retention_loop_in")
    elif is_retention_risk:
        queue = "Success"
        guardrail_flags.append("retention_risk_override_to_success")
    elif extraction["contradictory_signals"]:
        queue = "Team Lead Triage"
        guardrail_flags.append("contradictory_signals_escalated_to_team_lead_triage")
    elif confidence["score"] <= triage_floor:
        queue = "Team Lead Triage"
        guardrail_flags.append("team_lead_triage_low_confidence_floor")
    else:
        queue = extraction["category"]

    loop_in = []
    underlying_category = extraction["category"]
    if queue != underlying_category:
        # Ownership moved away from the raw category via a guardrail
        # override (retention risk, contradiction) - the underlying need
        # (e.g. a real product problem) is still real and must not be
        # lost just because another team now owns the conversation.
        loop_in.append(underlying_category)
    if is_retention_risk and queue != "Success":
        if is_formal_close_cancel:
            if large_account:
                loop_in.append("Success")
            # else: routine account admin on a normal-sized account - no
            # Success loop-in, so Success isn't pulled into every close/
            # cancel request regardless of size.
        else:
            loop_in.append("Success")
    if extraction["expansion_intent_language"] and queue != "Success":
        loop_in.append("Success")
    for alt in extraction["category_alternatives"]:
        if alt != queue:
            loop_in.append(alt)
    loop_in = sorted(set(loop_in) - {queue})

    if loop_in:
        guardrail_flags.append(f"looped_in:{','.join(loop_in)}")

    if confidence["band"] == "low":
        guardrail_flags.append("low_confidence_human_review")

    # Enterprise AE routing: a large stated team size on a Sales message
    # routes to a dedicated Enterprise AE handling path rather than
    # standard self-serve Sales, mirroring a real Sales/Contact form's
    # team-size field. This doesn't change queue ownership (Sales still
    # owns it) - it's a handling-path distinction within Sales, the same
    # way Team Lead Triage is a distinction within "uncertain", not a 5th
    # top-level queue.
    sales_handling_path = None
    if queue == "Sales":
        sales_handling_path = (
            "Enterprise AE"
            if extraction.get("team_size_band") in config.get("enterprise_ae_team_size_bands", [])
            else "Standard Sales"
        )
        if sales_handling_path == "Enterprise AE":
            guardrail_flags.append("enterprise_ae_routing")

    # Phase-1 scope: every first message in a thread is human-approved
    # before anything is sent, regardless of confidence band. The bands
    # only affect review priority/flagging shown in the dashboard.
    review_priority = "urgent" if (is_sensitive or confidence["band"] == "low") else "standard"

    return {
        "queue": queue,
        "loop_in": loop_in,
        "guardrail_flags": guardrail_flags,
        "review_priority": review_priority,
        "requires_human_review": True,
        "sales_handling_path": sales_handling_path,
    }


def health_expansion_flag(extraction, routing):
    """Lightweight, rule-based (no extra API call) health/expansion
    signal on the Success branch only. Explicitly text-only - not a
    verified account health score. "Success branch" now means Success
    has visibility at all, whether as primary owner or looped in on a
    message another team owns - the expansion signal is just as real
    either way."""
    success_involved = routing["queue"] == "Success" or "Success" in routing.get("loop_in", [])
    if not success_involved or not extraction["expansion_intent_language"]:
        return None
    return {
        "flag": "possible_expansion_signal",
        "note": (
            "Message contains expansion-intent language. This is a "
            "text-only signal derived from this single message, not a "
            "verified account health score - treat as a prompt to look "
            "closer, not a conclusion."
        ),
    }


def draft_response(client, message_text, extraction, confidence, routing, config):
    """Sonnet call: a conditional draft. If key info is missing, the
    draft is a clarification request, not a forced resolution. Thinking
    is disabled - this is a short drafting task, not one that benefits
    from extended reasoning, and leaving it on would inflate cost."""
    needs_clarification = (
        routing["queue"] == extraction["category"] == "Service"
        and extraction["category"] in config["categories_expecting_reference"]
        and not extraction["account_reference"]
    )

    if needs_clarification:
        instruction = (
            "Key information is missing (no account reference). "
            "Draft a brief, polite reply asking the customer for that "
            "specific missing detail. Do not attempt to resolve the issue."
        )
    elif routing["queue"] == "Team Lead Triage":
        # Confidence was too low to trust an auto-routed queue, so this
        # draft uses the model's best-guess category only as a starting
        # point for whichever team the team lead assigns it to manually -
        # it is not itself a routing decision.
        instruction = (
            f"Draft a brief, helpful reply addressing: {extraction['issue_type']}. "
            f"This message's queue assignment is uncertain and pending manual "
            f"review by a team lead, so treat {extraction['category']} as a "
            f"best guess only, not a confirmed team. This is a draft for "
            f"human review before sending, not a final answer."
        )
    else:
        instruction = (
            f"Draft a brief, helpful reply for the {routing['queue']} team "
            f"to send this customer, addressing: {extraction['issue_type']}. "
            f"This is a draft for human review before sending, not a final answer."
        )

    matched_article = None
    if not needs_clarification:
        matched_article = find_matching_article(extraction, routing["queue"])
    if matched_article:
        reference_block = (
            f"\n\nRelevant reference material found for this message "
            f"(\"{matched_article['title']}\"): {matched_article['answer']}\n"
            f"Ground your reply in this - reuse its substance in your own "
            f"words rather than inventing an answer, but don't just paste "
            f"it verbatim if the customer's specific situation needs a "
            f"more tailored response."
        )
    else:
        reference_block = ""

    brand = load_brand_guidelines()
    if brand:
        banned = ", ".join(brand.get("banned_words_or_phrases", []))
        voice_lines = "\n".join(f"- {v}" for v in brand.get("voice_principles", []))
        ai_isms = brand.get("avoid_ai_isms", {})
        ai_ism_words = ", ".join(ai_isms.get("banned_words", []))
        ai_ism_phrases = ", ".join(ai_isms.get("banned_phrases", []))
        ai_ism_style = "\n".join(f"- {s}" for s in ai_isms.get("style_rules", []))
        brand_block = (
            f"\n\nBrand guidelines for {config['company_name']} (follow these exactly):\n"
            f"Tone: {brand.get('tone', '')}\n"
            f"Voice principles:\n{voice_lines}\n"
            f"Never use these words/phrases: {banned}\n"
            f"Formatting: {brand.get('formatting', '')}\n"
            f"Sign off with: {brand.get('preferred_phrases', {}).get('sign_off', '')}\n\n"
            f"Separately, avoid AI-isms - words and patterns that read as AI-generated "
            f"rather than human-written:\n"
            f"Never use these words: {ai_ism_words}\n"
            f"Never use these phrases: {ai_ism_phrases}\n"
            f"Style rules:\n{ai_ism_style}"
        )
    else:
        brand_block = ""

    system_prompt = (
        f"You draft short customer-support replies for {config['company_name']}. "
        f"Keep it to 2-4 sentences unless technical detail requires more, no filler."
        f"{reference_block}"
        f"{brand_block}"
    )

    response = client.messages.create(
        model=config["models"]["draft"],
        max_tokens=400,
        thinking={"type": "disabled"},
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Original message: {message_text}\n\nInstruction: {instruction}",
        }],
    )

    draft_text = next(b.text for b in response.content if b.type == "text")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "model": config["models"]["draft"],
    }
    return draft_text, usage, needs_clarification, matched_article


def score_draft_confidence(matched_article, routing, needs_clarification):
    """A second, distinct confidence score from score_confidence/
    determine_queue above. That one answers "did this land in the right
    queue" (routing confidence); this one answers "is this specific
    draft likely good enough to send" (answer-quality confidence) -
    explicitly not the same question, and conflating them would hide
    that a message can be routed correctly but still get a weak,
    unaided answer, or vice versa.

    Rule-based, same philosophy as the routing score: not an LLM rating
    the quality of its own answer, which would just be asking the model
    to grade its own homework. The signal used instead is verifiable and
    external to the model's own judgement: was this draft actually
    grounded in a real, matched Help Centre/playbook article, or is it
    the model's own generative attempt with nothing to check it against?
    A draft that reuses known-correct source material is more likely to
    be accurate than a fully generative one, the same way a human agent
    who looks up the right article before replying is more likely to be
    right than one answering from memory. This is the answer to "how do
    you simulate draft-quality confidence without real usage data": it
    doesn't try to - it measures whether grounding was possible at all,
    which is honest about what this prototype can and can't verify."""
    if needs_clarification:
        return {
            "band": "n/a",
            "reason": "Draft is a clarification request, not an attempted answer - answer-quality confidence doesn't apply.",
        }
    if routing["queue"] == "Team Lead Triage":
        return {
            "band": "low",
            "reason": "Queue itself is unconfirmed, so answer quality can't be trusted until a team lead assigns the right team.",
        }
    if matched_article:
        return {
            "band": "high",
            "reason": f"Grounded in a matched reference article (\"{matched_article['title']}\") rather than a fully generative answer.",
        }
    return {
        "band": "low",
        "reason": "No matching reference article found for this message - this is the model's own unaided attempt, not grounded in known-correct source material. Review carefully before sending.",
    }


def process_message(client, message, config):
    """Run one message through the full pipeline. Returns a structured
    result dict; never raises on model/parse errors - falls back to
    human review so the batch always completes (safe degradation)."""
    try:
        extraction, extract_usage = classify_and_extract(
            client, message["text"], config, entry_channel=message.get("entry_channel"),
        )
    except (anthropic.APIError, json.JSONDecodeError, StopIteration) as e:
        return {
            "id": message["id"],
            "text": message["text"],
            "error": f"{type(e).__name__}: {e}",
            "queue": "Service",
            "guardrail_flags": ["extraction_failed_defaulted_to_human_review"],
            "requires_human_review": True,
            "usage": [],
        }

    confidence = score_confidence(extraction, config)
    routing = determine_queue(extraction, confidence, config, message_text=message["text"])
    health_flag = health_expansion_flag(extraction, routing)

    try:
        draft_text, draft_usage, is_clarification, matched_article = draft_response(
            client, message["text"], extraction, confidence, routing, config,
        )
        usage = [extract_usage, draft_usage]
        draft_confidence = score_draft_confidence(matched_article, routing, is_clarification)
    except (anthropic.APIError, StopIteration) as e:
        draft_text = None
        is_clarification = None
        matched_article = None
        draft_confidence = {"band": "low", "reason": "Drafting failed - no draft was produced."}
        usage = [extract_usage]
        routing["guardrail_flags"].append(f"draft_failed:{type(e).__name__}")

    investigation_summary = None
    if confidence["band"] in config.get("investigation_trigger_bands", []):
        try:
            investigation_summary, investigation_usage = investigate_uncertain_message(
                client, message["text"], extraction, config,
            )
            usage.extend(investigation_usage)
        except anthropic.APIError as e:
            investigation_summary = None
            routing["guardrail_flags"].append(f"investigation_failed:{type(e).__name__}")

    return {
        "id": message["id"],
        "text": message["text"],
        "entry_channel": message.get("entry_channel"),
        "extraction": extraction,
        "confidence": confidence,
        "queue": routing["queue"],
        "loop_in": routing["loop_in"],
        "guardrail_flags": routing["guardrail_flags"],
        "review_priority": routing["review_priority"],
        "requires_human_review": routing["requires_human_review"],
        "sales_handling_path": routing["sales_handling_path"],
        "health_expansion_flag": health_flag,
        "draft": draft_text,
        "draft_is_clarification_request": is_clarification,
        "draft_confidence": draft_confidence,
        "matched_reference": {"id": matched_article["id"], "title": matched_article["title"]} if matched_article else None,
        "investigation_summary": investigation_summary,
        "usage": usage,
    }
