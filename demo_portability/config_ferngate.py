"""
A second, genuinely different fictional company - "Ferngate Security", a
vulnerability-management/compliance SaaS - used only to prove the claim in
the main README: swapping config (and this company's own data/ content)
lets pipeline.py run unmodified for a different business entirely. Nothing
here is a real company.

Deliberately a different domain from Thistlewire (project-management SaaS):
different categories of language, different sensitive topics, different
team-size/ARR framing - proving portability means proving the pipeline
doesn't quietly assume Thistlewire's specific vocabulary anywhere.
"""

CONFIG = {
    "company_name": "Ferngate Security",

    "categories": ["Service", "Success", "Sales"],

    "help_centre_categories": [
        "Login & Access",
        "Billing & Invoices",
        "Scan Errors & False Positives",
        "Integrations & API",
        "Feature Requests",
        "Cancel Subscription",
        "Close Account",
        "All Other Queries",
    ],

    "retention_risk_signals": [
        "close account",
        "cancel account",
        "cancel my account",
        "cancel my subscription",
        "cancel subscription",
        "downgrade",
        "switching to a competitor",
        "leaving ferngate",
    ],

    "formal_close_cancel_patterns": [
        r"\bclos\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,25}\bsubscription\b",
    ],

    "large_account_arr_bands": ["25k_to_100k", "100k_plus"],

    # Deliberately different sensitive-topic vocabulary from Thistlewire -
    # a compliance/security SaaS has different real stakes (audit
    # findings, regulatory exposure) than a project-management tool.
    "sensitive_topics": [
        "refund",
        "chargeback",
        "compliance audit failure",
        "regulatory penalty",
        "billing dispute",
        "gdpr",
        "data request",
        "legal",
        "data breach",
        "account breach",
        "unauthorized access",
        "false compliance report",
    ],

    "enterprise_ae_team_size_bands": ["200_to_1000", "1000_plus"],

    "arr_threshold_sales_ae": 5000,

    "self_serve_tiers_usd_per_month": {"min": 19, "max": 129},

    "confidence_bands": {"high": 80, "medium": 50},

    "categories_expecting_reference": ["Service"],

    # Deliberately different keyword vocabulary from Thistlewire's
    # project-management terms - proving the pipeline itself carries no
    # hidden assumptions about what "Service" or "Success" language looks
    # like for a specific product.
    "category_keywords": {
        "Service": [
            "login", "password", "2fa", "sso", "scan", "false positive",
            "cve", "vulnerability", "alert", "integration", "api", "siem",
            "dashboard", "billing", "invoice", "refund",
        ],
        "Success": [
            "audit", "renewal", "expand", "expansion", "coverage",
            "additional assets", "scale", "scaling", "grow", "growth",
            "onboarding", "compliance framework", "soc 2", "new team",
        ],
        "Sales": [
            "pricing", "price", "plan", "demo", "trial", "quote",
            "discount", "compare", "comparison", "sign up", "signing up",
            "new customer", "asset count", "seats",
        ],
    },

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
        "classify_extract": "claude-haiku-4-5",
        "draft": "claude-sonnet-5",
        "investigate": "claude-sonnet-5",
    },

    "investigation_trigger_bands": ["low"],

    "entry_channels": ["Support", "Sales", "Success"],

    "team_lead_triage_confidence_floor": 20,

    "health_context_categories": ["Service", "Success"],
    "health_risk_confidence_penalty": -10,
    "health_score_risk_threshold": 40,
    "csat_risk_bands": ["dissatisfied"],
    "ces_risk_bands": ["high_effort"],
    "health_risk_signal_tags": ["renewal_at_risk", "support_escalation_last_30d"],

    "mid_market_arr_threshold": 5_000,
    "enterprise_arr_threshold": 50_000,
    "assumed_new_logo_arr_by_team_size": {
        "under_10": 1_800,
        "10_to_50": 8_000,
        "50_to_200": 22_000,
        "200_to_1000": 65_000,
        "1000_plus": 150_000,
        "unknown": 4_000,
    },
    "assumed_new_logo_close_rate": 0.25,
    "assumed_expansion_rate": 0.15,
    "assumed_contraction_rate": 0.10,
}
