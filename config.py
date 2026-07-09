"""
Demo-company-specific configuration for the message-routing pipeline.

Everything generic (the classify/extract/confidence/draft/guardrail
pipeline itself) lives in pipeline.py and reads from this object rather
than hardcoding company specifics. Swapping this file for a different
company's config should let the same pipeline code run unmodified -
this is a genuinely reusable demo, not one-off code. The values below
describe "Thistlewire", a fictional project-management/team-collaboration
SaaS company, standing in for any real B2B SaaS business this pattern
could be pointed at. Nothing here names a real company.
"""

CONFIG = {
    "company_name": "Thistlewire",

    "categories": ["Service", "Success", "Sales"],

    # A representative set of Support categories, shaped by how real-world
    # B2B SaaS support intake forms are typically structured. None of these
    # correspond to Sales or Success - confirms that channel of entry is
    # Service/Support-shaped only; Success/Sales must be detected from
    # message content, not assumed from channel.
    "help_centre_categories": [
        "Login & Access",
        "Billing & Invoices",
        "Bugs & Errors",
        "Integrations & API",
        "Feature Requests",
        "Cancel Subscription",
        "Close Account",
        "All Other Queries",
    ],

    # Need-based signals that override channel-of-entry: these always
    # indicate retention risk. NOT all of them route to Success as primary
    # owner though - see formal_close_cancel_phrases below and
    # determine_queue's routing logic for the distinction.
    "retention_risk_signals": [
        "close account",
        "cancel account",
        "cancel my account",
        "cancel my subscription",
        "cancel subscription",
        "downgrade",
        "switching to a competitor",
        "leaving thistlewire",
    ],

    # Narrower than retention_risk_signals above: only the phrasing that
    # matches a formal "Close Account" or "Cancel Subscription" Help Centre
    # category request specifically (two of the 8 real support-form
    # categories) - a routine account-lifecycle action, not necessarily an
    # active relationship conversation. Checked as regex patterns against
    # the raw message text (deliberately not an LLM call - this is a
    # narrow, auditable distinction, not a judgement call). Patterns rather
    # than exact phrases so real phrasing variance (e.g. "close our
    # account" vs "close my account") isn't missed just because the
    # pronoun differs. Deliberately excludes softer signals like
    # "downgrade" or "switching to a competitor", which genuinely are
    # relationship-risk language and should stay Success-owned.
    # determine_queue uses this to keep Support as the owner of formal
    # close/cancel requests by default, looping in Success only when the
    # account is large (see large_account_arr_bands) - otherwise Success
    # becomes a dumping ground for routine account admin work and turns
    # reactive instead of proactive.
    "formal_close_cancel_patterns": [
        r"\bclos\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,25}\bsubscription\b",
    ],

    # ARR bands (see data/mock_backend.json's account records) treated as
    # "large" for retention-escalation purposes - a distinct, higher bar
    # than the arr_threshold_sales_ae below, which is about routing inbound
    # Sales enquiries, not escalating existing-account risk. Deliberately a
    # starting assumption pending real usage data (see confidence rubric
    # caveat in HOW_THE_AI_WORKS.md) - what counts as "large enough to
    # warrant proactive Success visibility" is a business judgement call,
    # not something inferable from this prototype alone.
    "large_account_arr_bands": ["25k_to_100k", "100k_plus"],

    # Sensitive topics: always route to Service, never auto-resolved,
    # regardless of confidence score.
    "sensitive_topics": [
        "refund",
        "chargeback",
        "compliance",
        "security incident",
        "overcharge",
        "billing dispute",
        "gdpr",
        "data request",
        "legal",
        "data breach",
        "account breach",
        "cyberattack",
        "hacked",
        "unauthorized access",
    ],

    # Team-size bands (mirroring a real Sales/Contact form's "Approx. team
    # size" or "number of seats" field) that route a Sales-category message
    # to a dedicated Enterprise AE handling path rather than standard
    # self-serve Sales. A concrete, form-grounded signal for the
    # arr_threshold_sales_ae's intent, not a separate rule - both exist to
    # catch the same kind of prospect, from two different angles (stated
    # team size here vs. inferred account revenue there).
    "enterprise_ae_team_size_bands": ["200_to_1000", "1000_plus"],

    # $5K ARR threshold: total account revenue (subscription plus any
    # usage-based/overage fees), not list-price subscription fees alone.
    # On a subscription-only basis nearly every self-serve account falls
    # under $5K given typical self-serve SaaS tiers, which would break the
    # threshold's purpose - so this must be applied against total account
    # revenue when available.
    "arr_threshold_sales_ae": 5000,

    # Representative self-serve monthly tiers (USD) for a B2B SaaS company,
    # used only to illustrate why subscription-only revenue can't drive the
    # ARR threshold.
    "self_serve_tiers_usd_per_month": {"min": 15, "max": 99},

    # Confidence scoring bands (0-100 scale, see pipeline.py for the
    # additive rubric that produces the raw score).
    "confidence_bands": {"high": 80, "medium": 50},

    # Categories where a missing account reference is itself a negative
    # confidence signal (Service messages are almost always about a
    # specific account/subscription issue).
    "categories_expecting_reference": ["Service"],

    # Reference terminology per category, used only as a signal for the
    # confidence score (does the message actually use category-specific
    # language, or is it generic). Not an exhaustive taxonomy.
    "category_keywords": {
        "Service": [
            "login", "password", "2fa", "sso", "outage", "downtime", "bug",
            "error", "crash", "integration", "api", "sync", "webhook",
            "billing", "invoice", "refund", "chargeback", "compliance", "gdpr",
        ],
        "Success": [
            "qbr", "ebr", "renewal", "expand", "expansion", "scale",
            "scaling", "grow", "growth", "upgrade", "review", "account health",
            "onboarding", "enterprise", "new team", "new department",
        ],
        "Sales": [
            "pricing", "price", "plan", "demo", "trial", "quote", "discount",
            "compare", "comparison", "sign up", "signing up", "new customer",
            "setup fee", "contract terms",
        ],
    },

    # Budget reality check inputs (illustrative), used by the commercial
    # cost model reporting, not by the pipeline itself.
    "budget": {
        "total_annual_budget": 600_000,
        "fte_costs": {
            "service": {"count": 30, "annual_cost": 15_000},
            "sales": {"count": 3, "annual_cost": 15_000},
            "cs": {"count": 5, "annual_cost": 18_000},
        },
        "monthly_volume_proxy": 6_000,
    },

    "models": {
        # Cheaper/faster model for classification + structured extraction.
        "classify_extract": "claude-haiku-4-5",
        # Stronger model reserved only for response drafting.
        "draft": "claude-sonnet-5",
        # Used for the agentic investigation step (tool-selection judgement
        # matters more here than in routine extraction, so it gets the
        # stronger model too - only triggered for low-confidence messages).
        "investigate": "claude-sonnet-5",
    },

    # Confidence bands at or below which the investigation agent triggers.
    "investigation_trigger_bands": ["low"],

    # Real-world entry channels: a typical B2B SaaS company's website
    # exposes a Support ("Help") inbound form and a separate Sales
    # ("Contact Sales") inbound form, both with real structure. "Success"
    # is a third, fictional/assumed channel modeling a plain inbox with no
    # form structure at all (e.g. a shared "success@" address given out ad
    # hoc by CSMs) - since there's no dedicated Success intake form in
    # reality, this models what actually happens instead: whatever loosely
    # Success-shaped traffic exists lands in an unstructured mailbox that
    # in practice fills up with a genuine mix of Service, Success, and
    # Sales content, because nothing about the channel itself signals
    # which one a message is. Entry channel is fed to classify_and_extract
    # as a prior, not a determining factor: Support-channel and
    # Sales-channel messages lean strongly toward their matching category,
    # but Success-channel messages should NOT be assumed Success just
    # because of where they arrived - that channel is deliberately modeled
    # as the messiest and least predictive of the three, so message
    # content must do essentially all of the work there. Existing
    # customers also routinely use whichever form is in front of them
    # regardless of channel (e.g. an existing account asking about
    # upgrading often goes through the Sales form even though the real
    # need is a Success conversation) - so content must always be able to
    # override the channel prior, not just for Success.
    "entry_channels": ["Support", "Sales", "Success"],

    # Below this rule-based confidence score (see score_confidence), route
    # to a 4th "Team Lead Triage" queue for manual assignment rather than
    # guessing a category queue. Routed to an existing team lead/principal
    # role, not a new hire - this is a volume-reduction lever, not a
    # headcount ask. Chosen from the observed score distribution on the
    # validated dev run: scores of 0/15/20 reflect essentially no positive
    # confidence signal firing at all, vs. 35+ where at least one strong
    # signal (e.g. a single confident category read) is present - the
    # largest natural gap in the distribution sits between 20 and 35.
    # Sensitive-topic and retention-risk overrides still take
    # unconditional precedence over this - see determine_queue. Expected
    # direction of travel: as classification accuracy improves (more real
    # data, better-tuned prompts), the share of messages landing below
    # this floor should fall - this queue's volume is a maturity signal to
    # track over time, not a fixed cost.
    "team_lead_triage_confidence_floor": 20,

    # --- Account health / VoC feedback loop -------------------------------
    # Mock stand-in for a real CS platform (Gainsight-style) feeding account
    # health, NPS, CSAT, CES, product feedback, and business-outcome status
    # into this pipeline - see data/mock_backend.json's "health_signals" key
    # and get_account_health_context in pipeline.py.
    #
    # Deliberately a SOFT signal, not a hard override: applied as one more
    # row in score_confidence's additive rubric (same mechanism as every
    # other signal there), so a message with an otherwise-clear read can
    # still land high confidence even on an at-risk account - it just makes
    # borderline cases more likely to fall into a review band. This is a
    # smaller penalty than the hard content-based signals (contradictory
    # signals, missing reference) on purpose: account history is weaker
    # evidence about THIS message than what the message itself says.
    #
    # Only applied when the predicted category is one where account
    # relationship context is actually relevant - a Sales enquiry from a
    # net-new prospect has no existing account to look up, and a routine
    # Service bug report shouldn't be second-guessed just because the
    # account's NPS dropped last quarter.
    "health_context_categories": ["Service", "Success"],

    # Confidence-rubric penalty applied when an account's health context
    # indicates risk (see score_confidence). Roughly half the weight of
    # "Multiple categories plausible" (-15) - a nudge, not a veto. This
    # exact weight is a first-draft assumption, same caveat as the rest of
    # the rubric: it should be recalibrated once calibration_report.py has
    # real outcome-tag data to test it against, not left as a permanent
    # guess.
    "health_risk_confidence_penalty": -10,

    # An account counts as "at risk" for this rubric signal if its health
    # score falls below this, OR its CSAT/CES band is in the risk set below,
    # OR any of its recent_signals match health_risk_signal_tags.
    "health_score_risk_threshold": 40,
    "csat_risk_bands": ["dissatisfied"],
    "ces_risk_bands": ["high_effort"],
    "health_risk_signal_tags": ["renewal_at_risk", "support_escalation_last_30d"],
}
