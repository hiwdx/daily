import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import generate
import test_generate


class MajorCapabilityTests(unittest.TestCase):
    def test_distinguishes_capability_openings_from_routine_updates(self):
        examples = {
            "Meta open-sources Muse hardware SDK": "hardware_opening",
            "Acme introduces open-source firmware for AI devices": "hardware_opening",
            "Muse 首次开放硬件接口与 SDK": "hardware_opening",
            "Example launches an agent platform SDK": "platform_opening",
            "NVIDIA Run:ai is now open source as part of its AI Factory platform": "platform_opening",
            "OpenAI launches a new Batch API": "platform_opening",
            "Lab open-sources model weights": "platform_opening",
            "Google releases a new Gemini model": "model_launch",
            "Introducing Claude 6": "model_launch",
            "Muse SDK bug fix and maintenance release": "standard",
            "LangGraph releases SDK v0.2.5": "standard",
            "Meta open-sources Muse hardware SDK v1.0.0": "hardware_opening",
            "Lab plans to open-source model weights": "standard",
            "GitHub weekly Copilot releases": "standard",
            "Client improves costs with a model": "standard",
            "OpenAI releases API documentation": "standard",
        }
        for title, expected in examples.items():
            with self.subTest(title=title):
                self.assertEqual(generate.capability_priority(title), expected)

    def test_muse_hardware_story_is_discovered_without_generic_ai_keyword(self):
        now = datetime(2026, 10, 3, 8, tzinfo=timezone.utc)
        data = f'<rss><channel><item><title>Meta open-sources Muse hardware SDK</title><link>https://example.com/muse-hardware</link><pubDate>{now.isoformat()}</pubDate><description>ESP32 firmware and Linux device SDK for Muse gadgets.</description></item></channel></rss>'
        result = generate.parse_official_feed(data.encode(), "Media", now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["editorial_priority"], "hardware_opening")
        self.assertEqual(result[0]["eligible_for_top"], "true")

    def test_platform_changelog_still_cannot_override_top_eligibility(self):
        now = datetime.now(timezone.utc)
        data = f'<rss><channel><item><title>GitHub launches a developer platform SDK</title><link>https://github.blog/changelog/sdk</link><pubDate>{now.isoformat()}</pubDate><description>New AI SDK.</description></item></channel></rss>'
        result = generate.parse_official_feed(data.encode(), "GitHub Changelog", now)
        self.assertEqual(result[0]["eligible_for_top"], "false")
        self.assertEqual(result[0]["editorial_priority"], "standard")

    def test_major_story_survives_the_candidate_cap_and_history_filter_still_applies(self):
        feeds = [generate.Feed(f"Source{i}", f"https://example{i}.com/feed", True, True) for i in range(40)]
        def parse(data, source, *args):
            index = int(source[6:])
            return [{"url": f"https://example{index}.com/story", "source": source, "title": source,
                     "published_at": "2026-10-14T00:00:00+00:00", "eligible_for_top": "true",
                     "editorial_priority": "hardware_opening" if index == 39 else "standard"}]
        with patch.object(generate, "OFFICIAL_UPDATE_FEEDS", feeds), \
             patch.object(generate, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"feed")), \
             patch.object(generate, "parse_official_feed", side_effect=parse):
            result = generate.get_official_candidates()
            self.assertEqual(len(result), generate.MAX_CANDIDATES)
            self.assertEqual(result[0]["source"], "Source39")
            result = generate.get_official_candidates([{"url": "https://example39.com/story"}])
            self.assertNotIn("Source39", [c["source"] for c in result])

    def test_important_candidate_cannot_be_silently_omitted(self):
        fixture = test_generate.PractitionerEditingTests()
        fixture.setUp()
        fixture.candidates[2]["editorial_priority"] = "platform_opening"
        errors = generate.validate_payload_quality(fixture.payload, fixture.candidates)
        self.assertTrue(any("priority_exclusions" in e for e in errors))
        fixture.payload["priority_exclusions"] = [{"url": fixture.candidates[2]["url"], "reason": "本期优先选择有公测状态和实际调用成本报告的接口发布。"}]
        self.assertEqual(generate.validate_payload_quality(fixture.payload, fixture.candidates), [])

    def test_older_major_story_is_not_dropped_by_one_publishers_newer_minor_posts(self):
        feed = generate.Feed("Media", "https://example.com/feed", True, True)
        candidates = [{"url": f"https://example.com/story{i}", "source": "Media", "title": str(i),
                       "published_at": f"2026-10-14T0{9-i}:00:00+00:00", "eligible_for_top": "true",
                       "editorial_priority": "hardware_opening" if i == 6 else "standard"} for i in range(7)]
        with patch.object(generate, "OFFICIAL_UPDATE_FEEDS", [feed]), \
             patch.object(generate, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"feed")), \
             patch.object(generate, "parse_official_feed", return_value=candidates):
            result = generate.get_official_candidates()
        self.assertEqual(result[0]["url"], "https://example.com/story6")

    def test_local_duplicate_removal_records_important_candidate_exclusion(self):
        fixture = test_generate.PractitionerEditingTests()
        fixture.setUp()
        candidate = {**fixture.candidates[0], "url": "https://lab.example/second", "editorial_priority": "platform_opening"}
        fixture.candidates.append(candidate)
        fixture.payload["top_stories"].append({**fixture.payload["top_stories"][0], "url": candidate["url"]})
        result = generate.normalize_payload_selection(fixture.payload, fixture.candidates)
        self.assertEqual(generate.validate_payload_quality(result, fixture.candidates), [])
        self.assertIn("本地去重", result["priority_exclusions"][0]["reason"])


class EditorialSpotlightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "spotlights.json"
        self.record = {"issue_date": "2026-10-14", "original_report_date": "2026-10-02",
                       "title": "Muse 开放硬件 SDK", "paragraphs": ["已开源设备 SDK，设备配对仍需要 token。"],
                       "sources": [{"label": "官方 SDK", "url": "https://github.com/facebookincubator/muse-gadget-sdk"}]}
        self.path.write_text(json.dumps([self.record]))

    def test_retrospective_has_dates_and_does_not_replace_fresh_top_stories(self):
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        original = test_generate.briefing(("新模型", "https://example.com/new", now))
        result = generate.append_editorial_spotlights(original, "2026-10-14", self.path)
        self.assertIn("行业重磅补充", result)
        self.assertIn("原始报道：2026-10-02", result)
        self.assertIn("本期补充解读：2026-10-14", result)
        self.assertEqual(generate.parse_top_stories(result), generate.parse_top_stories(original))

    def test_followup_does_not_repeat_on_future_days(self):
        self.assertEqual(generate.append_editorial_spotlights("unchanged", "2026-10-15", self.path), "unchanged")

    def test_future_report_date_and_unsafe_source_are_rejected(self):
        self.record["original_report_date"] = "2026-10-15"
        self.path.write_text(json.dumps([self.record]))
        with self.assertRaises(ValueError):
            generate.append_editorial_spotlights("body", "2026-10-14", self.path)
        self.record["original_report_date"] = "2026-10-02"
        self.record["sources"][0]["url"] = "javascript:alert(1)"
        self.path.write_text(json.dumps([self.record]))
        with self.assertRaises(ValueError):
            generate.append_editorial_spotlights("body", "2026-10-14", self.path)

    def test_real_muse_record_contains_sources_and_access_limits(self):
        result = generate.append_editorial_spotlights("body", "2026-10-14")
        self.assertIn("ESP32", result)
        self.assertIn("SDK token", result)
        self.assertIn("编辑判断", result)
        self.assertIn("https://gadgets.muse.ai/", result)
