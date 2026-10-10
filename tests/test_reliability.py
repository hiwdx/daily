import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIConnectionError, AuthenticationError

import generate
from scripts import briefing_health as health
import test_generate


class SelectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = test_generate.PractitionerEditingTests()
        fixture.setUp()
        self.now, self.candidates, self.payload = fixture.now, fixture.candidates, fixture.payload

    def test_duplicate_url_and_publisher_are_removed_locally(self):
        original = copy.deepcopy(self.payload)
        self.payload["top_stories"].append(copy.deepcopy(self.payload["top_stories"][0]))
        duplicate = {**self.payload["top_stories"][0], "url": "https://lab.example/second"}
        self.payload["top_stories"].append(duplicate)
        self.candidates.append({**self.candidates[0], "url": duplicate["url"]})
        normalized = generate.normalize_payload_selection(self.payload)
        self.assertEqual(normalized, original)
        self.assertEqual(generate.validate_payload_quality(normalized, self.candidates), [])
        self.assertEqual(len(self.payload["top_stories"]), 3)  # No mutation.

    def test_cross_section_duplicate_removed_without_inventing_summary(self):
        self.payload["other_stories"].append(self.payload["top_stories"][0])
        normalized = generate.normalize_payload_selection(self.payload)
        self.assertEqual(len(normalized["other_stories"]), 1)
        self.assertEqual(generate.validate_payload_quality(normalized, self.candidates), [])

    def test_theme_dropped_when_its_evidence_was_removed(self):
        duplicate = {**self.payload["top_stories"][0], "url": "https://lab.example/second"}
        self.payload["top_stories"].append(duplicate)
        self.payload["theme_observation"]["evidence_urls"][1] = duplicate["url"]
        self.assertIsNone(generate.normalize_payload_selection(self.payload)["theme_observation"])

    def test_invalid_records_still_fail_quality_gate(self):
        self.payload["other_stories"].append({"url": "https://unknown.example/new"})
        normalized = generate.normalize_payload_selection(self.payload)
        self.assertTrue(generate.validate_payload_quality(normalized, self.candidates))

    def test_duplicate_model_draft_succeeds_without_four_failed_retries(self):
        self.payload["top_stories"] *= 2
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))])
        with patch.dict(generate.os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             patch.object(generate, "NOW", self.now), \
             patch.object(generate, "_api_create_with_retry", return_value=response) as api:
            result = generate.fetch_briefing("test", official_candidates=self.candidates)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(len(generate.parse_top_stories(result)), 1)
        self.assertNotIn("briefing_mode: source_links", result)

    def test_missing_key_and_connection_error_and_bad_key_publish_transparent_links(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        failures = [None, APIConnectionError(request=request),
                    AuthenticationError("invalid key", response=httpx.Response(401, request=request), body=None)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch.dict(generate.os.environ, {"DEEPSEEK_API_KEY": "test" if failure else ""}), \
                 patch.object(generate, "NOW", self.now), \
                 patch.object(generate, "_api_create_with_retry", side_effect=failure):
                result = generate.fetch_briefing("test", official_candidates=self.candidates)
            self.assertIn("briefing_mode: source_links", result)
            self.assertEqual(generate.parse_top_stories(result), [])
            self.assertEqual(len(generate.parse_other_stories(result)), 3)
            self.assertNotIn("产品技术视角", result)

    def test_exhausted_editorial_repairs_use_safe_links(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])
        with patch.dict(generate.os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             patch.object(generate, "NOW", self.now), \
             patch.object(generate, "_api_create_with_retry", return_value=response) as api:
            result = generate.fetch_briefing("test", official_candidates=self.candidates)
        self.assertEqual(api.call_count, generate.MAX_MODEL_CALLS)
        self.assertIn("briefing_mode: source_links", result)

    def test_fallback_rejects_stale_future_undated_and_history_links(self):
        for timestamp in [(self.now - timedelta(days=3)).isoformat(),
                          (self.now + timedelta(hours=1)).isoformat(), "invalid"]:
            with self.subTest(timestamp=timestamp), patch.object(generate, "NOW", self.now):
                bad = [{**self.candidates[0], "published_at": timestamp}]
                with self.assertRaises(RuntimeError):
                    generate.build_official_feed_fallback(bad)
        with patch.object(generate, "NOW", self.now), self.assertRaises(RuntimeError):
            generate.build_official_feed_fallback(self.candidates, self.candidates)

    def test_fallback_caps_platform_volume_and_never_promotes_it_to_top(self):
        urls = ["https://github.com/a/b/releases/1", "https://github.com/c/d/releases/2",
                "https://vercel.com/changelog/one", "https://cloudflare.com/changelog/two"]
        candidates = [{**self.candidates[0], "url": url, "source": str(i)} for i, url in enumerate(urls)]
        with patch.object(generate, "NOW", self.now):
            result = generate.build_official_feed_fallback(candidates)
        self.assertEqual(len(generate.parse_other_stories(result)), 2)
        self.assertEqual(generate.parse_top_stories(result), [])


class PublicationHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.docs = Path(self.temp.name)
        self.date = "2026-10-10"
        self.body = "<p>Reliable briefing body with several real news items and their original source links.</p>"
        self.html = '<span class="hero-date">2026年10月10日 周六</span><!-- Briefing body -->' + self.body + '<!-- Archive -->'
        self.archive = self.docs / "archive/2026-10/2026-10-10.html"
        self.archive.parent.mkdir(parents=True)
        self.archive.write_text(self.html)
        (self.docs / "index.html").write_text(self.html)
        (self.docs / "archive.json").write_text(json.dumps([{"date": self.date}]))
        (self.docs / "rss.xml").write_text('<rss><channel><item><link>https://daily.hiwd.com/archive/2026-10/2026-10-10.html</link><description><![CDATA[' + self.body + ']]></description></item></channel></rss>')
        (self.docs / "status.json").write_text(json.dumps({"date": self.date, "mode": "editorial"}))

    def test_complete_editorial_issue_skips_regeneration(self):
        self.assertFalse(health.should_generate(self.docs, self.date))

    def test_provisional_issue_and_absent_status_retry(self):
        (self.docs / "status.json").write_text(json.dumps({"date": self.date, "mode": "source_links"}))
        self.assertTrue(health.should_generate(self.docs, self.date))
        (self.docs / "status.json").unlink()
        self.assertTrue(health.should_generate(self.docs, self.date))

    def test_archive_index_alone_cannot_hide_missing_page(self):
        self.archive.unlink()
        self.assertTrue(health.should_generate(self.docs, self.date))

    def test_stale_homepage_or_rss_retries(self):
        (self.docs / "index.html").write_text(self.html.replace("2026年10月10日", "2026年10月08日"))
        self.assertTrue(health.should_generate(self.docs, self.date))
        (self.docs / "index.html").write_text(self.html)
        (self.docs / "rss.xml").write_text('<rss><channel/></rss>')
        self.assertTrue(health.should_generate(self.docs, self.date))

    def test_mismatched_same_date_homepage_retries(self):
        self.archive.write_text(self.html.replace("Reliable", "Different"))
        self.assertTrue(health.should_generate(self.docs, self.date))

    def test_online_verification_rejects_unpublished_local_files(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b"old deployed content"
        with patch.object(health, "urlopen", return_value=Response()), self.assertRaises(ValueError):
            health.verify_online(self.docs, self.date, "https://daily.hiwd.com", attempts=1)

    def test_online_verification_checks_all_five_artifacts(self):
        def respond(request, **kwargs):
            relative = request.full_url.split("daily.hiwd.com/")[1].split("?")[0]
            return (self.docs / relative).open("rb")
        with patch.object(health, "urlopen", side_effect=respond) as network:
            health.verify_online(self.docs, self.date, "https://daily.hiwd.com", attempts=1)
        self.assertEqual(network.call_count, 5)
