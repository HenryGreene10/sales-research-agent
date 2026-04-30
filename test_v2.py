import sqlite3
import unittest
from unittest.mock import patch

import agent
import database


class V2ResearchTests(unittest.TestCase):
    def setUp(self):
        self.original_db_file = database.DB_FILE
        self.memory_uri = "file:test_v2_db?mode=memory&cache=shared"
        self.anchor_connection = sqlite3.connect(self.memory_uri, uri=True)
        self.anchor_connection.row_factory = sqlite3.Row
        database.DB_FILE = self.memory_uri
        database.init_db()

        self.seller_profile = {
            "name": "Henry",
            "product_description": "AI sales automation software",
            "ideal_customer_profile": "Revenue teams at SaaS companies",
            "target_company_size": "Mid-Market",
            "target_industries": "B2B SaaS",
            "past_wins": "Closed similar workflow automation accounts",
            "disqualifiers": "Very small local businesses",
        }
        self.seller_id = database.save_seller_profile(self.seller_profile)

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.anchor_connection.close()

    def _completed_run(self, seller_id: int, company_name: str, company_type: str, score: float, rationale: str):
        resolution = {
            "input_name": company_name,
            "normalized_name": database.normalize_company_name(company_name),
            "resolved_name": company_name,
            "domain": f"{company_name.lower()}.com",
            "website": f"https://{company_name.lower()}.com",
            "company_type": company_type,
            "ticker": None,
            "cik": None,
            "industry": "Software",
            "confidence": 0.9,
            "evidence": [],
        }
        company_id = database.upsert_company_resolution(resolution)
        run_id = database.create_research_run(
            seller_id=seller_id,
            input_name=company_name,
            resolution=resolution,
            company_id=company_id,
        )
        brief = {
            "company": company_name,
            "resolved_company": resolution,
            "what_they_do": "Test summary",
            "key_customers": "Test buyers",
            "recent_news": "Test news",
            "pain_points": [f"{company_name} pain"],
            "why_now_signals": [],
            "outreach_angle": f"Pitch {company_name}",
            "opportunity_score": score,
            "fit_score": score,
            "timing_score": 5.0,
            "trigger_score": 5.0,
            "confidence": "high",
            "confidence_reason": "Test confidence",
            "score_rationale": rationale,
            "sources": [],
        }
        database.complete_research_run(run_id, brief)
        return run_id, company_id

    def test_company_resolution_schema_has_expected_keys(self):
        with patch.object(agent, "_tavily_search") as tavily_search, patch.object(agent, "_claude_message") as claude_message:
            tavily_search.return_value = [
                {
                    "url": "https://stripe.com",
                    "title": "Stripe | Financial Infrastructure",
                    "content": "Stripe provides payments and financial infrastructure for internet businesses.",
                }
            ]
            claude_message.return_value = """
            {
              "input_name": "Stripe",
              "normalized_name": "stripe",
              "resolved_name": "Stripe",
              "domain": "stripe.com",
              "website": "https://stripe.com",
              "company_type": "private",
              "ticker": null,
              "cik": null,
              "industry": "Financial Infrastructure",
              "confidence": 0.93,
              "evidence": []
            }
            """
            result = agent.resolve_company("Stripe")

        expected_keys = {
            "input_name",
            "normalized_name",
            "resolved_name",
            "domain",
            "website",
            "company_type",
            "ticker",
            "cik",
            "industry",
            "confidence",
            "evidence",
        }
        self.assertTrue(expected_keys.issubset(result.keys()))
        self.assertEqual(result["domain"], "stripe.com")
        self.assertEqual(result["company_type"], "private")

    def test_routing_logic_changes_by_company_type(self):
        public_plan = agent.build_research_plan(
            {
                "resolved_name": "Snowflake",
                "input_name": "Snowflake",
                "domain": "snowflake.com",
                "company_type": "public",
            },
            self.seller_profile,
        )
        private_plan = agent.build_research_plan(
            {
                "resolved_name": "Stripe",
                "input_name": "Stripe",
                "domain": "stripe.com",
                "company_type": "private",
            },
            self.seller_profile,
        )

        public_tools = {item["tool_name"] for item in public_plan}
        private_tools = {item["tool_name"] for item in private_plan}

        self.assertIn("sec_filings", public_tools)
        self.assertNotIn("funding_and_momentum", public_tools)
        self.assertIn("funding_and_momentum", private_tools)
        self.assertIn("website_positioning", private_tools)
        recent_news = next(item for item in private_plan if item["tool_name"] == "recent_news")
        self.assertIn("last 30 days", recent_news["query"])

    def test_seller_scoped_duplicate_handling(self):
        self._completed_run(self.seller_id, "Stripe", "private", 7.0, "Good fit")
        other_seller_id = database.save_seller_profile(
            {**self.seller_profile, "name": "Different Seller"}
        )

        self.assertTrue(database.company_exists("Stripe", seller_id=self.seller_id))
        self.assertFalse(database.company_exists("Stripe", seller_id=other_seller_id))

    def test_memory_retrieval_uses_best_score_row(self):
        _, target_company_id = self._completed_run(self.seller_id, "CurrentCo", "private", 4.0, "Current target")
        self._completed_run(self.seller_id, "ReferenceCo", "private", 5.0, "Weaker rationale")
        self._completed_run(self.seller_id, "ReferenceCo", "private", 9.0, "Best rationale")

        matches = database.get_similar_companies(
            seller_id=self.seller_id,
            company_id=target_company_id,
            normalized_name=database.normalize_company_name("CurrentCo"),
            company_type="private",
        )

        self.assertEqual(matches[0]["company"], "ReferenceCo")
        self.assertEqual(matches[0]["score"], 9.0)
        self.assertEqual(matches[0]["score_rationale"], "Best rationale")

    def test_signal_detection_adds_provenance_and_trigger_score(self):
        tool_results = [
            {
                "tool_name": "recent_news",
                "query": "Stripe latest news",
                "status": "ok",
                "evidence": [
                    {
                        "tool_name": "recent_news",
                        "query": "Stripe latest news",
                        "url": "https://example.com/funding",
                        "title": "Stripe raised a new funding round",
                        "snippet": "Stripe raised a new funding round and launched an AI product.",
                        "retrieved_at": agent.utc_now_iso(),
                        "metadata": {},
                    }
                ],
            }
        ]

        signals, trigger_score = agent.detect_why_now_signals(tool_results)

        self.assertGreater(trigger_score, 0)
        self.assertTrue(any(signal["type"] == "funding" for signal in signals))
        self.assertTrue(any(signal["evidence_url"] == "https://example.com/funding" for signal in signals))

    def test_score_components_include_evidence_score(self):
        tool_results = [
            {
                "tool_name": "general_web_search",
                "status": "ok",
                "evidence": [
                    {
                        "tool_name": "general_web_search",
                        "query": "example",
                        "url": "https://example.com/a",
                        "title": "AI automation for revenue teams",
                        "snippet": "This platform helps SaaS revenue teams automate sales workflows.",
                        "retrieved_at": agent.utc_now_iso(),
                        "metadata": {},
                    }
                ],
            }
        ]
        scores = agent.derive_score_components(
            resolution={
                "company_type": "private",
                "confidence": 0.8,
                "industry": "B2B SaaS",
            },
            seller_profile=self.seller_profile,
            tool_results=tool_results,
            signals=[],
            trigger_score=4.0,
            similar_examples=[],
        )

        self.assertIn("evidence_score", scores)
        self.assertGreater(scores["evidence_score"], 0)
        self.assertIn(scores["confidence"], {"low", "medium", "high"})

    def test_research_company_fallback_handles_external_failures(self):
        with patch.object(agent, "_tavily_search", side_effect=RuntimeError("search down")), patch.object(
            agent,
            "_claude_message",
            side_effect=RuntimeError("llm down"),
        ):
            result = agent.research_company(
                company_name="FallbackCo",
                seller_profile=self.seller_profile,
                seller_id=self.seller_id,
                force_refresh=True,
            )

        self.assertEqual(result["company"], "FallbackCo")
        self.assertIn(result["confidence"], {"low", "medium"})
        self.assertIn("resolved_company", result)
        self.assertIn("sources", result)

    def test_watchlist_monitoring_records_new_signal_event(self):
        self._completed_run(self.seller_id, "SignalCo", "private", 5.0, "Old rationale")
        watchlist_id = database.create_or_update_watchlist(self.seller_id, "SignalCo")

        def side_effect(company_name, seller_profile, seller_id, force_refresh):
            resolution = {
                "input_name": company_name,
                "normalized_name": database.normalize_company_name(company_name),
                "resolved_name": company_name,
                "domain": "signalco.com",
                "website": "https://signalco.com",
                "company_type": "private",
                "ticker": None,
                "cik": None,
                "industry": "Software",
                "confidence": 0.9,
                "evidence": [],
            }
            company_id = database.upsert_company_resolution(resolution)
            run_id = database.create_research_run(
                seller_id=seller_id,
                input_name=company_name,
                resolution=resolution,
                company_id=company_id,
            )
            brief = {
                "company": company_name,
                "resolved_company": resolution,
                "what_they_do": "Updated summary",
                "key_customers": "Revenue teams",
                "recent_news": "New funding and launch",
                "pain_points": ["Scaling GTM execution"],
                "why_now_signals": ["Funding detected from latest announcement"],
                "outreach_angle": "Lead with automation speed.",
                "opportunity_score": 7.5,
                "fit_score": 7.0,
                "timing_score": 8.0,
                "evidence_score": 7.0,
                "trigger_score": 8.0,
                "confidence": "high",
                "confidence_reason": "Fresh evidence exists.",
                "score_rationale": "Momentum increased.",
                "sources": [],
            }
            database.complete_research_run(run_id, brief)
            return brief

        with patch.object(agent, "research_company", side_effect=side_effect):
            result = agent.monitor_watchlist(
                company_name="SignalCo",
                seller_profile=self.seller_profile,
                seller_id=self.seller_id,
                watchlist_id=watchlist_id,
            )

        events = database.get_watchlist_events(watchlist_id)
        self.assertTrue(result["events"])
        self.assertTrue(any(event["event_type"] == "new_signal" for event in events))
        self.assertEqual(events[0]["watchlist_id"], watchlist_id)


if __name__ == "__main__":
    unittest.main()
