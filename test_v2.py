import sqlite3
import unittest
from unittest.mock import patch

import agent
import batch
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

    def test_resolution_prefers_official_domain_over_news_source(self):
        evidence = [
            {
                "url": "https://news.example.com/stripe-funding",
                "title": "Stripe funding update",
                "snippet": "Stripe announced fresh funding.",
            },
            {
                "url": "https://stripe.com",
                "title": "Stripe | Financial Infrastructure",
                "snippet": "Official homepage for Stripe.",
            },
        ]

        resolution = agent._heuristic_resolution("Stripe", evidence)

        self.assertEqual(resolution["domain"], "stripe.com")
        self.assertTrue(resolution["resolution_trace"])

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

    def test_account_snapshot_contains_score_component_summary(self):
        tool_results = [
            {
                "tool_name": "recent_news",
                "status": "ok",
                "evidence": [
                    {
                        "tool_name": "recent_news",
                        "query": "example",
                        "url": "https://example.com/news",
                        "title": "Expansion news",
                        "snippet": "The company expanded into Europe.",
                        "retrieved_at": agent.utc_now_iso(),
                        "metadata": {},
                    }
                ],
            }
        ]
        signals = [
            {
                "type": "market_expansion",
                "reason": "Expansion creates operational complexity.",
            }
        ]
        score_components = {
            "fit_score": 6.0,
            "timing_score": 7.0,
            "evidence_score": 5.5,
            "confidence_score": 6.1,
        }

        snapshot = agent.build_account_snapshot(
            company_name="ExampleCo",
            resolution={
                "resolved_name": "ExampleCo",
                "company_type": "private",
                "domain": "example.com",
                "industry": "Software",
            },
            tool_results=tool_results,
            signals=signals,
            score_components=score_components,
        )

        self.assertEqual(snapshot["domain"], "example.com")
        self.assertIn("score_components", snapshot)
        self.assertEqual(snapshot["score_components"]["fit_score"], 6.0)

    def test_score_explanation_is_human_readable(self):
        explanation = agent.build_score_explanation(
            company_name="ExampleCo",
            score_components={
                "fit_score": 7.5,
                "timing_score": 8.0,
                "evidence_score": 6.5,
            },
            signals=[{"type": "funding"}, {"type": "product_launch"}],
        )

        self.assertEqual(len(explanation), 3)
        self.assertTrue(any("strong fit" in line.lower() for line in explanation))
        self.assertTrue(any("timing" in line.lower() for line in explanation))

    def test_snapshot_delta_highlights_score_and_signal_changes(self):
        delta = agent.build_snapshot_delta(
            previous_snapshot={
                "signal_types": ["funding"],
                "latest_source_titles": ["Old source"],
                "score_components": {
                    "fit_score": 5.0,
                    "timing_score": 4.0,
                    "evidence_score": 4.5,
                    "confidence_score": 5.0,
                },
            },
            latest_snapshot={
                "signal_types": ["funding", "product_launch"],
                "latest_source_titles": ["Old source", "New source"],
                "score_components": {
                    "fit_score": 6.0,
                    "timing_score": 7.0,
                    "evidence_score": 6.5,
                    "confidence_score": 6.0,
                },
            },
        )

        self.assertEqual(delta["fit_score_change"], 1.0)
        self.assertEqual(delta["timing_score_change"], 3.0)
        self.assertIn("product_launch", delta["new_signal_types"])
        self.assertIn("New source", delta["new_source_titles"])

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

    def test_compare_run_evidence_changes_detects_website_and_source_deltas(self):
        previous_evidence = [
            {
                "tool_name": "website_positioning",
                "title": "Old positioning",
                "snippet": "Legacy workflow automation messaging",
                "url": "https://example.com/old",
            },
            {
                "tool_name": "recent_news",
                "title": "Old news",
                "snippet": "Old event",
                "url": "https://news.example.com/old",
            },
        ]
        latest_evidence = [
            {
                "tool_name": "website_positioning",
                "title": "New positioning",
                "snippet": "AI revenue workflow platform for enterprise teams",
                "url": "https://example.com/new",
            },
            {
                "tool_name": "recent_news",
                "title": "New source",
                "snippet": "Fresh announcement",
                "url": "https://news.example.com/new",
            },
        ]

        events = agent.compare_run_evidence_changes(previous_evidence, latest_evidence, "SignalCo")

        event_types = {event["event_type"] for event in events}
        self.assertIn("website_messaging_change", event_types)
        self.assertIn("new_source_detected", event_types)

    def test_company_run_history_returns_snapshots(self):
        self._completed_run(self.seller_id, "HistoryCo", "private", 6.5, "History rationale")
        history = database.get_company_run_history(self.seller_id, "HistoryCo")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["company"], "HistoryCo")
        self.assertIn("account_snapshot", history[0])

    def test_execute_tool_plan_isolates_timeout_failures(self):
        plan = [
            {"tool_name": "recent_news", "query": "SignalCo latest news"},
            {"tool_name": "jobs_and_hiring", "query": "SignalCo hiring"},
        ]
        successful_result = {
            "tool_name": "jobs_and_hiring",
            "query": "SignalCo hiring",
            "status": "ok",
            "retrieved_at": agent.utc_now_iso(),
            "freshness_days": 90,
            "intent": "hiring_momentum",
            "evidence": [],
            "error": None,
        }

        with patch.object(
            agent,
            "_search_tool",
            side_effect=[TimeoutError("Tavily search timed out after 20 seconds"), successful_result],
        ):
            results = agent.execute_tool_plan(plan)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(results[0]["error_type"], "TimeoutError")
        self.assertEqual(results[0]["intent"], "recent_signals")
        self.assertEqual(results[1]["status"], "ok")

    def test_batch_processing_collects_failures_without_stopping(self):
        successful_result = {
            "company": "Alpha",
            "opportunity_score": 7.5,
            "trigger_score": 6.0,
            "confidence": "high",
            "pain_points": [],
            "why_now_signals": [],
            "outreach_angle": "Lead with workflow speed.",
        }

        with patch.object(batch, "company_exists", return_value=False), patch.object(
            batch,
            "research_company",
            side_effect=[successful_result, RuntimeError("search down"), {**successful_result, "company": "Gamma", "from_cache": True}],
        ):
            results = batch.process_batch(
                companies=["Alpha", "Beta", "Gamma"],
                seller_profile=self.seller_profile,
                seller_id=self.seller_id,
                force_refresh=True,
            )

        summary = batch.summarize_batch_results(results)
        dataframe = batch.results_to_dataframe(results)

        self.assertEqual(len(results), 3)
        self.assertEqual(summary["successful"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("Beta", summary["failed_companies"])
        self.assertIn("Error", dataframe.columns)
        self.assertTrue(any(dataframe["Company"] == "Beta"))

    def test_parse_company_csv_dedupes_and_skips_blank_rows(self):
        companies, stats = batch.parse_company_csv(
            "company\nStripe\n \nstripe \nSnowflake\n"
        )

        self.assertEqual(companies, ["Stripe", "Snowflake"])
        self.assertEqual(stats["duplicates_removed"], 1)
        self.assertEqual(stats["blank_rows_skipped"], 1)

    def test_parse_company_csv_accepts_case_insensitive_header_and_bom(self):
        companies, stats = batch.parse_company_csv(
            b"\xef\xbb\xbfCompany\nNotion\nFigma\n"
        )

        self.assertEqual(companies, ["Notion", "Figma"])
        self.assertEqual(stats["duplicates_removed"], 0)
        self.assertEqual(stats["blank_rows_skipped"], 0)

    def test_recent_run_can_be_found_by_resolution_alias_and_domain(self):
        self._completed_run(self.seller_id, "Stripe, Inc.", "private", 7.2, "Alias rationale")

        cached = database.get_recent_research_run_for_resolution(
            seller_id=self.seller_id,
            resolution={
                "input_name": "Stripe",
                "resolved_name": "Stripe, Inc.",
                "domain": "stripe, inc..com",
                "website": "https://stripe, inc..com",
            },
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached["final_brief"]["company"], "Stripe, Inc.")


if __name__ == "__main__":
    unittest.main()
