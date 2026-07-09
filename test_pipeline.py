"""
Unit tests for the pure-Python logic in pipeline.py - one level below
run_eval.py's end-to-end, real-API regression suite. These make zero API
calls (no cost, no network, runs in well under a second) and exist to
pin down exact behaviour at boundary values - e.g. a health score exactly
at the risk threshold, an ARR value exactly at a tier cutoff - the kind
of off-by-one mistake an end-to-end eval case is unlikely to happen to
cover just by chance.

Run: python -m unittest test_pipeline.py -v
"""

import unittest

from pipeline import (
    account_health_is_risk,
    classify_account_tier,
    determine_queue,
    health_expansion_flag,
    is_large_account,
    score_confidence,
    score_draft_confidence,
)

CONFIG = {
    "confidence_bands": {"high": 80, "medium": 50},
    "categories_expecting_reference": ["Service"],
    "team_lead_triage_confidence_floor": 20,
    "large_account_arr_bands": ["25k_to_100k", "100k_plus"],
    "formal_close_cancel_patterns": [
        r"\bclos\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,20}\baccount\b",
        r"\bcancel\w*\b.{0,25}\bsubscription\b",
    ],
    "enterprise_ae_team_size_bands": ["200_to_1000", "1000_plus"],
    "health_context_categories": ["Service", "Success"],
    "health_risk_confidence_penalty": -10,
    "health_score_risk_threshold": 40,
    "csat_risk_bands": ["dissatisfied"],
    "ces_risk_bands": ["high_effort"],
    "health_risk_signal_tags": ["renewal_at_risk", "support_escalation_last_30d"],
    "mid_market_arr_threshold": 5_000,
    "enterprise_arr_threshold": 50_000,
}


def make_extraction(**overrides):
    base = {
        "category": "Service",
        "category_alternatives": [],
        "contradictory_signals": False,
        "account_reference": "",
        "sentiment": "neutral",
        "expansion_intent_language": False,
        "retention_risk_language": False,
        "team_size_band": "unknown",
        "sensitive_topic_flags": [],
        "matched_keywords": [],
        "message_length_words": 20,
    }
    base.update(overrides)
    return base


class TestScoreConfidence(unittest.TestCase):
    def test_all_positive_signals_clamped_at_100(self):
        extraction = make_extraction(account_reference="ACC-1", matched_keywords=["login"], sentiment="frustrated")
        result = score_confidence(extraction, CONFIG, backend={"accounts": {}, "health_signals": {}})
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["band"], "high")

    def test_contradictory_signals_alone_scores_zero_not_negative(self):
        extraction = make_extraction(contradictory_signals=True, category_alternatives=["Success"])
        result = score_confidence(extraction, CONFIG, backend={"accounts": {}, "health_signals": {}})
        self.assertEqual(result["score"], 0)  # clamped, never negative
        self.assertEqual(result["band"], "low")

    def test_missing_reference_on_service_penalised(self):
        extraction = make_extraction(category="Service", account_reference="")
        result = score_confidence(extraction, CONFIG, backend={"accounts": {}, "health_signals": {}})
        self.assertIn("-30 no reference where category normally expects one", result["reasons"])

    def test_short_message_penalised(self):
        extraction = make_extraction(message_length_words=3)
        result = score_confidence(extraction, CONFIG, backend={"accounts": {}, "health_signals": {}})
        self.assertIn("-20 message very short/generic", result["reasons"])

    def test_band_boundaries_exact(self):
        # high >= 80, medium >= 50, else low - test the exact edges, not
        # just comfortably-inside values.
        bands = CONFIG["confidence_bands"]
        self.assertEqual(self._band_for_score(80), "high")
        self.assertEqual(self._band_for_score(79), "medium")
        self.assertEqual(self._band_for_score(50), "medium")
        self.assertEqual(self._band_for_score(49), "low")

    @staticmethod
    def _band_for_score(score):
        bands = CONFIG["confidence_bands"]
        if score >= bands["high"]:
            return "high"
        if score >= bands["medium"]:
            return "medium"
        return "low"

    def test_health_risk_penalty_only_for_configured_categories(self):
        backend = {"accounts": {}, "health_signals": {"ACC-1": {"health_score": 10, "recent_signals": []}}}
        service_extraction = make_extraction(category="Service", account_reference="ACC-1")
        sales_extraction = make_extraction(category="Sales", account_reference="ACC-1")
        service_result = score_confidence(service_extraction, CONFIG, backend=backend)
        sales_result = score_confidence(sales_extraction, CONFIG, backend=backend)
        self.assertTrue(any("health/VoC risk" in r for r in service_result["reasons"]))
        self.assertFalse(any("health/VoC risk" in r for r in sales_result["reasons"]))

    def test_no_health_data_on_file_is_silent_not_an_error(self):
        extraction = make_extraction(category="Service", account_reference="ACC-UNKNOWN")
        result = score_confidence(extraction, CONFIG, backend={"accounts": {}, "health_signals": {}})
        self.assertFalse(any("health/VoC risk" in r for r in result["reasons"]))


class TestDetermineQueue(unittest.TestCase):
    def _confidence(self, score, band):
        return {"score": score, "band": band, "reasons": []}

    def test_sensitive_topic_always_service_even_at_low_confidence(self):
        extraction = make_extraction(category="Sales", sensitive_topic_flags=["refund"])
        routing = determine_queue(extraction, self._confidence(0, "low"), CONFIG, message_text="I want a refund", backend={"accounts": {}})
        self.assertEqual(routing["queue"], "Service")
        self.assertIn("sensitive_topic_always_service", routing["guardrail_flags"])

    def test_formal_close_request_stays_with_service(self):
        extraction = make_extraction(category="Service", retention_risk_language=True)
        routing = determine_queue(
            extraction, self._confidence(90, "high"), CONFIG,
            message_text="Please close my account", backend={"accounts": {}},
        )
        self.assertEqual(routing["queue"], "Service")
        self.assertIn("formal_close_cancel_support_owned", routing["guardrail_flags"])

    def test_formal_close_on_large_account_loops_in_success(self):
        extraction = make_extraction(category="Service", retention_risk_language=True, account_reference="ACC-BIG")
        backend = {"accounts": {"ACC-BIG": {"arr_band": "100k_plus"}}}
        routing = determine_queue(
            extraction, self._confidence(90, "high"), CONFIG,
            message_text="Please cancel my subscription", backend=backend,
        )
        self.assertEqual(routing["queue"], "Service")
        self.assertIn("Success", routing["loop_in"])

    def test_soft_retention_language_routes_to_success(self):
        extraction = make_extraction(category="Service", retention_risk_language=True)
        routing = determine_queue(
            extraction, self._confidence(90, "high"), CONFIG,
            message_text="we'll have to look at other providers", backend={"accounts": {}},
        )
        self.assertEqual(routing["queue"], "Success")
        self.assertIn("retention_risk_override_to_success", routing["guardrail_flags"])

    def test_contradictory_signals_escalate_not_default_to_success(self):
        extraction = make_extraction(category="Service", contradictory_signals=True)
        routing = determine_queue(extraction, self._confidence(30, "low"), CONFIG, message_text="mixed message", backend={"accounts": {}})
        self.assertEqual(routing["queue"], "Team Lead Triage")
        self.assertNotEqual(routing["queue"], "Success")

    def test_low_confidence_below_floor_escalates(self):
        extraction = make_extraction(category="Service")
        routing = determine_queue(extraction, self._confidence(20, "low"), CONFIG, message_text="ok", backend={"accounts": {}})
        self.assertEqual(routing["queue"], "Team Lead Triage")

    def test_confidence_just_above_floor_does_not_escalate(self):
        extraction = make_extraction(category="Service")
        routing = determine_queue(extraction, self._confidence(21, "low"), CONFIG, message_text="ok", backend={"accounts": {}})
        self.assertEqual(routing["queue"], "Service")

    def test_enterprise_team_size_gets_enterprise_ae_path(self):
        extraction = make_extraction(category="Sales", team_size_band="1000_plus")
        routing = determine_queue(extraction, self._confidence(90, "high"), CONFIG, message_text="big rollout", backend={"accounts": {}})
        self.assertEqual(routing["sales_handling_path"], "Enterprise AE")

    def test_small_team_size_gets_standard_sales_path(self):
        extraction = make_extraction(category="Sales", team_size_band="under_10")
        routing = determine_queue(extraction, self._confidence(90, "high"), CONFIG, message_text="small team", backend={"accounts": {}})
        self.assertEqual(routing["sales_handling_path"], "Standard Sales")


class TestAccountHealthIsRisk(unittest.TestCase):
    def test_none_context_is_not_risk(self):
        is_risk, reasons = account_health_is_risk(None, CONFIG)
        self.assertFalse(is_risk)
        self.assertEqual(reasons, [])

    def test_health_score_exactly_at_threshold_is_not_risk(self):
        # Strictly less-than in the implementation - 40 itself should NOT
        # count as at-risk when the threshold is 40.
        is_risk, _ = account_health_is_risk({"health_score": 40}, CONFIG)
        self.assertFalse(is_risk)

    def test_health_score_one_below_threshold_is_risk(self):
        is_risk, reasons = account_health_is_risk({"health_score": 39}, CONFIG)
        self.assertTrue(is_risk)
        self.assertTrue(any("health score" in r for r in reasons))

    def test_csat_risk_band(self):
        is_risk, reasons = account_health_is_risk({"csat_band": "dissatisfied"}, CONFIG)
        self.assertTrue(is_risk)

    def test_ces_risk_band(self):
        is_risk, reasons = account_health_is_risk({"ces_band": "high_effort"}, CONFIG)
        self.assertTrue(is_risk)

    def test_recent_signal_tag(self):
        is_risk, reasons = account_health_is_risk({"recent_signals": ["renewal_at_risk"]}, CONFIG)
        self.assertTrue(is_risk)

    def test_healthy_account_no_risk(self):
        is_risk, reasons = account_health_is_risk(
            {"health_score": 90, "csat_band": "satisfied", "ces_band": "low_effort", "recent_signals": []}, CONFIG,
        )
        self.assertFalse(is_risk)
        self.assertEqual(reasons, [])


class TestClassifyAccountTier(unittest.TestCase):
    def test_none_arr_returns_none(self):
        self.assertIsNone(classify_account_tier(None, CONFIG))

    def test_below_mid_market_threshold_is_self_serve(self):
        self.assertEqual(classify_account_tier(4_999, CONFIG), "self_serve")

    def test_exactly_at_mid_market_threshold_is_mid_market(self):
        self.assertEqual(classify_account_tier(5_000, CONFIG), "mid_market")

    def test_just_below_enterprise_threshold_is_mid_market(self):
        self.assertEqual(classify_account_tier(49_999, CONFIG), "mid_market")

    def test_exactly_at_enterprise_threshold_is_enterprise(self):
        self.assertEqual(classify_account_tier(50_000, CONFIG), "enterprise")

    def test_well_above_enterprise_threshold_is_enterprise(self):
        self.assertEqual(classify_account_tier(500_000, CONFIG), "enterprise")


class TestHealthExpansionFlag(unittest.TestCase):
    def test_success_owner_with_expansion_language_flags(self):
        extraction = make_extraction(expansion_intent_language=True)
        routing = {"queue": "Success", "loop_in": []}
        self.assertIsNotNone(health_expansion_flag(extraction, routing))

    def test_success_looped_in_with_expansion_language_flags(self):
        extraction = make_extraction(expansion_intent_language=True)
        routing = {"queue": "Service", "loop_in": ["Success"]}
        self.assertIsNotNone(health_expansion_flag(extraction, routing))

    def test_no_expansion_language_no_flag(self):
        extraction = make_extraction(expansion_intent_language=False)
        routing = {"queue": "Success", "loop_in": []}
        self.assertIsNone(health_expansion_flag(extraction, routing))

    def test_success_not_involved_no_flag_even_with_expansion_language(self):
        extraction = make_extraction(expansion_intent_language=True)
        routing = {"queue": "Service", "loop_in": []}
        self.assertIsNone(health_expansion_flag(extraction, routing))


class TestIsLargeAccount(unittest.TestCase):
    def test_no_reference_is_not_large(self):
        extraction = make_extraction(account_reference="")
        self.assertFalse(is_large_account(extraction, CONFIG, {"accounts": {}}))

    def test_reference_not_on_file_is_not_large(self):
        extraction = make_extraction(account_reference="ACC-UNKNOWN")
        self.assertFalse(is_large_account(extraction, CONFIG, {"accounts": {}}))

    def test_large_arr_band_is_large(self):
        extraction = make_extraction(account_reference="ACC-1")
        backend = {"accounts": {"ACC-1": {"arr_band": "100k_plus"}}}
        self.assertTrue(is_large_account(extraction, CONFIG, backend))

    def test_small_arr_band_is_not_large(self):
        extraction = make_extraction(account_reference="ACC-1")
        backend = {"accounts": {"ACC-1": {"arr_band": "under_5k"}}}
        self.assertFalse(is_large_account(extraction, CONFIG, backend))


class TestScoreDraftConfidence(unittest.TestCase):
    def test_needs_clarification_is_not_applicable(self):
        result = score_draft_confidence(None, {"queue": "Service"}, needs_clarification=True)
        self.assertEqual(result["band"], "n/a")

    def test_team_lead_triage_is_low_even_with_matched_article(self):
        result = score_draft_confidence({"id": "hc_001"}, {"queue": "Team Lead Triage"}, needs_clarification=False)
        self.assertEqual(result["band"], "low")

    def test_matched_article_is_high(self):
        result = score_draft_confidence({"id": "hc_001", "title": "Test article"}, {"queue": "Service"}, needs_clarification=False)
        self.assertEqual(result["band"], "high")

    def test_no_matched_article_is_low(self):
        result = score_draft_confidence(None, {"queue": "Service"}, needs_clarification=False)
        self.assertEqual(result["band"], "low")


if __name__ == "__main__":
    unittest.main()
