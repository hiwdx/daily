import tempfile
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate


def briefing(*entries: tuple[str, str, datetime], other: str = "") -> str:
    blocks = []
    for title, url, published_at in entries:
        blocks.append(
            f"""**标题**：[{title}]({url})
**来源**：[测试源]({url}) · {published_at:%Y-%m-%d}
<!-- published_at: {published_at.isoformat()} -->
**摘要**：
- 发生了什么
- 为什么重要
- 对谁有影响
**产品技术视角**：测试
"""
        )
    return "### 🎯 今日 Top 3\n\n" + "\n".join(blocks) + "\n### 📰 其他值得看的\n" + other


def briefing_with_source_date(title: str, url: str, published_at: datetime, source_date: str) -> str:
    return f"""### 🎯 今日 Top 3

**标题**：[{title}]({url})
**来源**：[测试源]({url}) · {source_date}
<!-- published_at: {published_at.isoformat()} -->
**摘要**：测试
**产品技术视角**：测试

### 📰 其他值得看的
"""


class FreshnessValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 13, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_accepts_story_inside_48_hour_window(self):
        text = briefing(("新模型发布", "https://example.com/new?utm_source=x", self.now - timedelta(hours=47)))
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_accepts_date_only_official_publication(self):
        published = self.now - timedelta(days=1)
        text = briefing(("官方更新日志发布新功能", "https://example.com/changelog/new", published))
        text = text.replace(published.isoformat(), published.strftime("%Y-%m-%d"))
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_rejects_story_older_than_48_hours(self):
        text = briefing(("旧模型发布", "https://example.com/old", self.now - timedelta(hours=49)))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("不在" in error for error in errors))

    def test_rejects_old_date_only_publication(self):
        published = self.now - timedelta(days=3)
        text = briefing(("过期的官方更新", "https://example.com/changelog/old", published))
        text = text.replace(published.isoformat(), published.strftime("%Y-%m-%d"))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("发布日期" in error and "超出" in error for error in errors))

    def test_rejects_old_visible_date_even_with_fresh_hidden_timestamp(self):
        text = briefing_with_source_date(
            "伪装成新内容的旧文章",
            "https://example.com/article",
            self.now - timedelta(hours=2),
            "2026-07-09",
        )
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("展示的来源日期" in error for error in errors))
        self.assertTrue(any("不一致" in error for error in errors))

    def test_accepts_one_day_source_date_difference_across_timezones(self):
        published = datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc)
        text = briefing_with_source_date(
            "媒体在当地前一日发布的更新",
            "https://techcrunch.com/2026/07/12/timezone-story/",
            published,
            "2026-07-12",
        )
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_rejects_homepage_as_top_story_url(self):
        text = briefing(("无法核验的主页新闻", "https://example.com/blog", self.now - timedelta(hours=2)))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("缺少可核验的文章 URL" in error for error in errors))

    def test_rejects_historical_url_after_canonicalization(self):
        text = briefing(("同一事件的新标题", "https://example.com/story?utm_source=x", self.now - timedelta(hours=2)))
        history = [{"date": "2026-07-12", "title": "历史标题", "url": "https://example.com/story"}]
        errors = generate.validate_briefing(text, history, now=self.now)
        self.assertTrue(any("URL 已在历史" in error for error in errors))

    def test_rejects_highly_similar_historical_title_on_different_url(self):
        text = briefing(("OpenAI 正式发布 GPT-6 新模型", "https://new.example/gpt-6", self.now - timedelta(hours=2)))
        history = [{
            "date": "2026-07-12",
            "title": "OpenAI 正式发布 GPT-6 新模型！",
            "url": "https://old.example/gpt-6",
        }]
        errors = generate.validate_briefing(text, history, now=self.now)
        self.assertTrue(any("疑似重复历史事件" in error for error in errors))

    def test_allows_explicit_empty_window_instead_of_old_filler(self):
        text = "### 🎯 今日 Top 3\n\n过去 48 小时暂无符合条件且未报道的内容。\n\n### 📰 其他值得看的"
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_allows_editor_to_reject_low_value_top_candidates(self):
        text = "### 🎯 今日 Top 3\n\n过去 48 小时暂无符合条件且未报道的内容。\n"
        candidates = [{"title": "官方更新", "url": "https://example.com/new"}]
        errors = generate.validate_briefing(
            text,
            now=self.now,
            official_candidates=candidates,
        )
        self.assertEqual(errors, [])

    def test_allows_two_stories_when_more_publishers_are_available(self):
        published = self.now - timedelta(hours=2)
        text = briefing(
            ("第一条", "https://example.com/one", published),
            ("第二条", "https://another.example/two", published),
        )
        candidates = [
            {"title": f"候选 {index}", "url": f"https://source{index}.example/{index}"}
            for index in range(6)
        ]
        candidates.extend([
            {"title": "第一条", "url": "https://example.com/one"},
            {"title": "第二条", "url": "https://another.example/two"},
        ])
        errors = generate.validate_briefing(text, now=self.now, official_candidates=candidates)
        self.assertEqual(errors, [])

    def test_rejects_same_publisher_filling_top_three(self):
        text = briefing(*[
            (f"GitHub 更新 {index}", f"https://github.blog/changelog/2026-07-1{index}-item", self.now - timedelta(hours=index))
            for index in range(1, 4)
        ])
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("同一发布方最多 1 条" in error for error in errors))

    def test_github_changelog_is_supplementary_only(self):
        feed = b"""<?xml version='1.0'?><rss><channel><item>
          <title>New AI feature</title>
          <link>https://github.blog/changelog/2026-07-12-ai-feature/</link>
          <pubDate>Sun, 12 Jul 2026 02:00:00 +0000</pubDate>
          <description>Available now.</description>
        </item></channel></rss>"""
        candidates = generate.parse_official_feed(feed, "GitHub Changelog", self.now)
        self.assertEqual(candidates[0]["eligible_for_top"], "false")

    def test_accepts_three_distinct_top_publishers(self):
        published = self.now - timedelta(hours=2)
        text = briefing(
            ("GitHub 更新", "https://github.blog/changelog/2026-07-12-github", published),
            ("Vercel 更新", "https://vercel.com/changelog/vercel-ai-update", published),
            ("媒体报道", "https://techcrunch.com/2026/07/12/ai-update/", published),
        )
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_rejects_one_publisher_dominating_other_reads(self):
        published = self.now - timedelta(hours=2)
        other = "\n".join(
            f"- **[扩展阅读 {index}](https://github.blog/changelog/2026-07-12-other-{index})** · GitHub\n- 简介"
            for index in range(3)
        )
        text = briefing(("Vercel 更新", "https://vercel.com/changelog/update", published), other=other)
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("其他值得看的" in error and "最多 1 条" in error for error in errors))

    def test_allows_empty_other_reads_quality_first(self):
        # The compact list is quality-first and variable-length: an empty
        # "其他值得看的" must NOT be rejected just because more candidates exist.
        # Padding to a quota is the filler this briefing avoids.
        published = self.now - timedelta(hours=2)
        # Multiple candidates must not force the model to pad Top 3; this
        # isolates the compact-list behaviour under test.
        candidates = [
            {"title": f"候选 {index}", "url": f"https://example.com/{index}"}
            for index in range(6)
        ]
        text = briefing(("Top 候选", candidates[0]["url"], published))
        errors = generate.validate_briefing(
            text, now=self.now, official_candidates=candidates
        )
        self.assertEqual(errors, [])

    def test_other_summary_is_rendered_in_the_same_list_item(self):
        candidate = {
            "source": "TechCrunch AI", "title": "可信候选", "url": "https://techcrunch.com/story",
            "published_at": self.now.isoformat(), "eligible_for_top": "true",
        }
        payload = {
            "top_stories": [{
                "title": "可信候选", "url": candidate["url"], "source": candidate["source"],
                "published_at": candidate["published_at"], "what_happened": "发生", "why_it_matters": "重要",
                "who_is_affected": "用户", "product_angle": "产品",
            }],
            "other_stories": [{
                "title": "扩展阅读", "url": "https://example.com/other", "source": "来源", "summary": "新增批量处理接口，适合需要减少逐条请求开销的开发者评估。",
            }],
        }
        other_candidate = {"source": "来源", "title": "原始扩展", "url": "https://example.com/other", "published_at": self.now.isoformat(), "eligible_for_top": "false"}
        text = generate.briefing_from_payload(payload, [candidate, other_candidate])
        self.assertIn("- **[扩展阅读](https://example.com/other)** · 来源", text)
        body = generate.md_to_html(generate.clean_briefing(text))
        self.assertRegex(body, r'(?s)<li>\s*<p><strong><a href="https://example.com/other".*?</p>\s*<p>新增批量处理接口.*?</p>\s*</li>')
        self.assertIn("TechCrunch AI、来源", text)
        self.assertNotIn("所有链接均来自程序已核验", text)

    def test_empty_state_is_rewritten_for_readers(self):
        text = """### 🎯 今日 Top 3

**无符合条件的新发布**

基于严格的时间窗口和内容筛选，过去 48 小时（2026-07-12 09:00 至 2026-07-14 09:00）内，未发现满足规则的内容。

### 📰 其他值得看的
"""
        formatted = generate.format_empty_top_state(text)
        self.assertIn("今天暂时没有新的重点动态", formatted)
        self.assertIn("有重要进展会及时更新", formatted)
        self.assertNotIn("2026-07-12", formatted)
        self.assertNotIn("严格的时间窗口", formatted)

    def test_non_empty_top_stories_are_not_rewritten(self):
        text = briefing(("真实的新发布", "https://example.com/article", self.now - timedelta(hours=2)))
        self.assertEqual(generate.format_empty_top_state(text), text)


class HistoryTests(unittest.TestCase):
    def test_reads_top_stories_from_all_archive_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir)
            for date, title in (("2026-01-01", "第一条"), ("2026-06-30", "第二条")):
                month = archive / date[:7]
                month.mkdir(parents=True, exist_ok=True)
                (month / f"{date}.html").write_text(
                    f'<h2>🎯 今日 Top 3</h2><p><strong>标题</strong>：'
                    f'<a href="https://example.com/{date}">{title}</a></p>'
                    '<h2>📰 其他值得看的</h2>',
                    encoding="utf-8",
                )
            stories = generate.get_previous_stories(archive)
            self.assertEqual({story["title"] for story in stories}, {"第一条", "第二条"})

    def test_prompt_keeps_history_local_to_control_cost(self):
        prompt = generate.build_user_prompt([
            {"date": "2026-01-01", "title": "已经报道", "url": "https://example.com/old"}
        ])
        self.assertIn("不得搜索", prompt)
        self.assertIn("只输出一个 JSON 对象", prompt)
        self.assertNotIn("已经报道", prompt)

    def test_prompt_disallows_model_search_and_hallucinated_urls(self):
        prompt = generate.build_user_prompt()
        self.assertIn("不得搜索", prompt)
        self.assertIn("不得补写不存在的链接", prompt)
        self.assertIn("Hacker News", prompt)

    def test_prompt_includes_bounded_prefetched_candidates(self):
        candidates = [{
            "source": "GitHub Changelog",
            "title": "Security reviews now available in the GitHub Copilot app",
            "url": "https://github.blog/changelog/2026-07-14-security-reviews",
            "published_at": "2026-07-14T12:54:12+00:00",
        }]
        prompt = generate.build_user_prompt(official_candidates=candidates)
        self.assertIn("候选（时间", prompt)
        self.assertIn(candidates[0]["title"], prompt)
        self.assertIn(candidates[0]["url"], prompt)


class OfficialFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 15, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_extracts_fresh_ai_entries_with_exact_links(self):
        feed = b"""<rss><channel>
          <item><title>Security reviews now available in GitHub Copilot</title>
            <link>https://github.blog/changelog/2026-07-14-security-reviews</link>
            <pubDate>Tue, 14 Jul 2026 12:54:12 +0000</pubDate></item>
          <item><title>Unrelated storage update</title>
            <link>https://example.com/2026-07-14-storage</link>
            <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate></item>
        </channel></rss>"""
        candidates = generate.parse_official_feed(feed, "GitHub Changelog", self.now)
        self.assertEqual(len(candidates), 1)
        self.assertIn("security-reviews", candidates[0]["url"])

    def test_rejects_old_article_with_recently_updated_feed_date(self):
        feed = b"""<rss><channel><item><title>Old AI model page updated</title>
          <link>https://example.com/changelog/2026-06-10-old-ai-page</link>
          <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate>
        </item></channel></rss>"""
        self.assertEqual(generate.parse_official_feed(feed, "Test", self.now), [])

    def test_extracts_atom_entries_and_iso_dates(self):
        feed = b"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>AI Gateway adds model leaderboard</title>
            <link rel="alternate" href="https://vercel.com/changelog/ai-gateway-leaderboard" />
            <published>2026-07-14T12:00:00Z</published></entry>
        </feed>"""
        candidates = generate.parse_official_feed(feed, "Vercel Changelog", self.now)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://vercel.com/changelog/ai-gateway-leaderboard")

    def test_filters_rumours_and_low_value_commentary(self):
        feed = b"""<rss><channel>
          <item><title>OpenAI researcher in talks to launch AI startup</title>
            <link>https://example.com/2026-07-14-rumour</link>
            <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate></item>
          <item><title>Singer says AI glasses are not stylish</title>
            <link>https://example.com/2026-07-14-comment</link>
            <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate></item>
        </channel></rss>"""
        self.assertEqual(generate.parse_official_feed(feed, "Media", self.now), [])

    def test_payload_renderer_rejects_model_invented_top_url(self):
        candidate = {
            "source": "GitHub Changelog",
            "title": "Verified update",
            "url": "https://github.blog/changelog/2026-07-15-verified",
            "published_at": "2026-07-15T00:30:00+00:00",
            "eligible_for_top": "true",
        }
        payload = {
            "top_stories": [{
                "title": "编造链接", "url": "https://example.com/invented",
                "what_happened": "发生了什么", "why_it_matters": "为什么重要",
                "who_is_affected": "谁受影响", "product_angle": "产品视角",
            }]
        }
        text = generate.briefing_from_payload(payload, [candidate])
        self.assertEqual(generate.parse_top_stories(text), [])
        self.assertTrue(generate.validate_payload_quality(payload, [candidate]))
        self.assertNotIn("example.com/invented", text)


class SensitiveContentTests(unittest.TestCase):
    def test_rejects_sensitive_exclusion_explanation(self):
        text = "本周涉及中国敏感监管内容的条目符合排除规则，因此未列示。"
        self.assertTrue(generate.contains_sensitive_politics(text))

    def test_allows_non_political_china_product_news(self):
        text = "一家中国公司发布了新的多模态模型和开发者 API。"
        self.assertFalse(generate.contains_sensitive_politics(text))

    def test_rejects_china_financing_and_valuation_narrative(self):
        text = "中国一级市场的 AI 融资与估值快速上升，形成全球竞争格局。"
        self.assertTrue(generate.contains_sensitive_politics(text))


class EditorialQualityTests(unittest.TestCase):
    def test_rejects_template_fallback_copy(self):
        text = """### 🎯 今日 Top 3

**标题**：[更新](https://example.com/update)
**来源**：[测试](https://example.com/update) · 2026-07-30
<!-- published_at: 2026-07-30T00:00:00+00:00 -->
**摘要**：官方或可信媒体订阅源确认了这项最新动态
"""
        self.assertTrue(generate.validate_editorial_quality(text))

    def test_rejects_prediction_as_top_story(self):
        text = briefing(("公司预测未来五年 AI 普及", "https://example.com/prediction", datetime.now(timezone.utc)))
        self.assertTrue(any("预测或观点" in error for error in generate.validate_editorial_quality(text)))

    def test_rejects_fundraising_plans_as_completed_news(self):
        text = briefing(("Lambda 拟融资并筹备上市", "https://example.com/funding", datetime.now(timezone.utc)))
        self.assertTrue(generate.validate_editorial_quality(text))


class PractitionerEditingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.candidates = [{
            "source": source, "title": title, "url": url,
            "published_at": self.now.isoformat(), "eligible_for_top": top,
            "summary": "The release adds batch processing and per-request cost reports. It is available in public beta and requires explicit configuration.",
        } for source, title, url, top in (
            ("Model Lab", "Batch API", "https://lab.example/batch", "true"),
            ("Engineering Blog", "Cost reporting", "https://engineering.example/report", "false"),
            ("Third Source", "Unselected story", "https://third.example/story", "true"),
        )]
        self.payload = {
            "top_stories": [{
                "title": "批处理接口开放公测", "url": self.candidates[0]["url"],
                "what_happened": "新增批处理接口和逐请求成本报告，当前为公开测试阶段。",
                "why_it_matters": "批处理提供新的调用方式，团队可以按请求核对成本。",
                "who_is_affected": "需要离线处理任务的 AI 工程团队。",
                "product_angle": "先在可重试任务中试用，再核对失败率与实际账单。",
            }],
            "other_stories": [{
                "title": "工程团队记录逐请求成本", "url": self.candidates[1]["url"],
                "summary": "新增逐请求成本报告，当前需显式配置；适合批处理团队在试用阶段核对实际账单。",
            }],
            "theme_observation": {
                "thesis": "上线新接口并不意味着单位任务成本已经下降。",
                "evidence_urls": [c["url"] for c in self.candidates[:2]],
                "evidence": "批处理接口与逐请求成本报告同时出现，让团队有机会测量真实任务开销。",
                "implication": "公测阶段仍应把集成与重试成本计入评估。",
                "watch": "用相同任务对比处理前后的失败率、账单和人工复核时间。",
            },
        }

    def test_valid_story_passes_full_pipeline_without_padding(self):
        self.assertEqual(generate.validate_payload_quality(self.payload, self.candidates), [])
        text = generate.briefing_from_payload(self.payload, self.candidates)
        self.assertEqual(generate.validate_briefing(text, now=self.now, official_candidates=self.candidates), [])
        self.assertEqual(generate.validate_editorial_quality(text), [])
        self.assertEqual(len(generate.parse_other_stories(text)), 1)
        self.assertNotIn("Unselected story", text)

    def test_no_top_story_still_keeps_useful_other_reads(self):
        self.payload["top_stories"] = []
        self.payload["theme_observation"] = None
        text = generate.briefing_from_payload(self.payload, self.candidates)
        self.assertEqual(len(generate.parse_top_stories(text)), 0)
        self.assertEqual(len(generate.parse_other_stories(text)), 1)
        self.assertEqual(generate.validate_payload_quality(self.payload, self.candidates), [])

    def test_rejects_theme_citing_an_unselected_candidate(self):
        self.payload["theme_observation"]["evidence_urls"][1] = self.candidates[2]["url"]
        self.assertTrue(any("未入选事件" in e for e in generate.validate_payload_quality(self.payload, self.candidates)))
        self.assertNotIn("今日主题观察", generate.briefing_from_payload(self.payload, self.candidates))

    def test_rejects_duplicate_evidence_and_missing_verification_action(self):
        theme = self.payload["theme_observation"]
        theme["evidence_urls"] = [self.candidates[0]["url"]] * 2
        del theme["watch"]
        errors = generate.validate_payload_quality(self.payload, self.candidates)
        self.assertTrue(any("至少两条" in e for e in errors))
        self.assertTrue(any("watch" in e for e in errors))

    def test_rejects_other_story_without_summary(self):
        del self.payload["other_stories"][0]["summary"]
        self.assertTrue(generate.validate_payload_quality(self.payload, self.candidates))
        text = generate.briefing_from_payload(self.payload, self.candidates)
        self.assertTrue(any("中文摘要" in e for e in generate.validate_editorial_quality(text)))

    def test_rejects_metadata_only_or_discussion_score_as_evidence(self):
        for summary, source in (("A Blog post by the author", "Blog"), ("HN discussion score 300. " * 6, "Hacker News")):
            with self.subTest(source=source):
                self.candidates[1].update(summary=summary, source=source)
                self.assertTrue(any("缺少原文事实" in e for e in generate.validate_payload_quality(self.payload, self.candidates)))

    def test_rejects_old_or_historically_published_other_story(self):
        candidate = self.candidates[1]
        candidate["published_at"] = (self.now - timedelta(days=3)).isoformat()
        text = generate.briefing_from_payload(self.payload, self.candidates)
        errors = generate.validate_briefing(text, [candidate], self.now, self.candidates)
        self.assertTrue(any("48 小时" in e for e in errors))
        self.assertTrue(any("历史简报" in e for e in errors))

    def test_empty_selection_requires_editorial_reason(self):
        payload = {"top_stories": [], "other_stories": [], "theme_observation": None}
        self.assertTrue(generate.validate_payload_quality(payload, self.candidates))
        payload["selection_note"] = "全部为缺少细节的营销公告。"
        self.assertEqual(generate.validate_payload_quality(payload, self.candidates), [])

    def test_top_eligibility_cannot_be_overridden_by_model(self):
        self.candidates[0]["eligible_for_top"] = "false"
        self.assertTrue(any("仅限补充阅读" in e for e in generate.validate_payload_quality(self.payload, self.candidates)))

    def test_model_failure_keeps_existing_publication(self):
        with patch.dict(generate.os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(EnvironmentError):
                generate.fetch_briefing("test", official_candidates=self.candidates)

    def test_malformed_draft_is_repaired_then_reviewed(self):
        valid = json.dumps(self.payload, ensure_ascii=False)
        responses = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=c))])
                     for c in ("[]", valid, valid)]
        with patch.dict(generate.os.environ, {"DEEPSEEK_API_KEY": "test-key"}), \
             patch.object(generate, "_api_create_with_retry", side_effect=responses) as api, \
             patch.object(generate, "NOW", self.now):
            result = generate.fetch_briefing("test", official_candidates=self.candidates)
        self.assertEqual(api.call_count, 3)
        self.assertIn("编辑判断", result)
        self.assertEqual(generate.validate_editorial_quality(result), [])


class SourceEvidenceTests(unittest.TestCase):
    def test_feed_top_eligibility_is_respected(self):
        now = datetime.now(timezone.utc)
        data = f'<rss><channel><item><title>AI update</title><link>https://vercel.com/changelog/update</link><pubDate>{now.isoformat()}</pubDate></item></channel></rss>'.encode()
        for source in ("Vercel Changelog", "Cloudflare Changelog", "Simon Willison"):
            with self.subTest(source=source):
                candidates = generate.parse_official_feed(data, source, now)
                self.assertEqual(candidates[0]["eligible_for_top"], "false")
        self.assertEqual(generate.parse_official_feed(data, "Custom Source", now, top_eligible=False)[0]["eligible_for_top"], "false")

    def test_namespaced_full_feed_content_supplies_evidence(self):
        root = generate.ElementTree.fromstring('''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Update</title><description>Teaser</description><content:encoded><![CDATA[<p>Actual engineering evidence.</p>]]></content:encoded></item></channel></rss>''')
        self.assertEqual(generate._feed_entries(root)[0][3], "Actual engineering evidence.")

    def test_article_body_wins_over_generic_metadata_and_navigation(self):
        body = "The model returns probabilities in one forward pass. Hardware and batch size affect the latency. " * 3
        page = '<meta content="A blog post" property="og:description"><nav><p>Subscribe now</p></nav><article><p>' + body + '</p><script>untrustedScript()</script></article>'
        excerpt = generate._extract_article_excerpt(page)
        self.assertIn("Hardware and batch size", excerpt)
        self.assertNotIn("Subscribe", excerpt)
        self.assertNotIn("untrustedScript", excerpt)

    def test_article_metadata_attribute_order_does_not_matter(self):
        self.assertEqual(generate._extract_article_excerpt('<meta content="Useful summary" name="description">'), "Useful summary")

    def test_url_dedup_preserves_article_identity_query(self):
        first = generate.canonicalize_url("https://news.ycombinator.com/item?id=123&utm_source=feed")
        second = generate.canonicalize_url("https://news.ycombinator.com/item?id=456")
        self.assertEqual(first, "https://news.ycombinator.com/item?id=123")
        self.assertNotEqual(first, second)

    def test_hosted_authors_and_repositories_are_distinct_publishers(self):
        self.assertNotEqual(generate._source_family("https://huggingface.co/blog/LiquidAI/open-d1"), generate._source_family("https://huggingface.co/blog/microsoft/thinkingbox"))
        self.assertNotEqual(generate._source_family("https://github.com/vllm-project/vllm/releases/tag/v1"), generate._source_family("https://github.com/huggingface/transformers/releases/tag/v1"))
        self.assertEqual(generate._source_family("https://research.google/blog/article"), generate._source_family("https://blog.google/article"))

    def test_curated_releases_skip_prereleases_and_keep_project_context(self):
        now = datetime.now(timezone.utc)
        data = '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
            f'<entry><title>{tag}</title><link href="https://github.com/vllm-project/vllm/releases/tag/{tag}"/><published>{now.isoformat()}</published><content>New batching support.</content></entry>'
            for tag in ("v1.0rc1", "v0.9.0", "v0.8.0")
        ) + '</feed>'
        with patch.object(generate, "CURATED_RELEASE_REPOS", ("vllm-project/vllm",)), patch.object(generate, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = data.encode()
            candidates = generate.get_github_release_candidates(now)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "vllm-project/vllm: v0.9.0")
        self.assertEqual(candidates[0]["eligible_for_top"], "false")

    def test_context_budget_does_not_cut_off_last_source(self):
        candidates = [{"url": f"https://source{i}.example/article", "title": "测试" * 30,
                       "source": f"Source {i}", "summary": "证据" * 1000,
                       "published_at": "2026-10-08T00:00:00+00:00", "eligible_for_top": "true"}
                      for i in range(generate.MAX_CANDIDATES)]
        prompt = generate.build_user_prompt(official_candidates=candidates)
        data = json.loads(prompt.split("候选（时间、来源、标题、URL、原文摘录、是否可进 Top 3）：\n")[1])
        self.assertEqual(len(data), generate.MAX_CANDIDATES)
        self.assertEqual(data[-1]["url"], candidates[-1]["url"])
        self.assertTrue(all(len(c["summary"]) >= 400 for c in data))

    def test_context_budget_is_spent_on_missing_evidence_after_rich_sources(self):
        candidates = [{"url": f"https://example.com/{i}", "source": "Test", "summary": "x" * 1000}
                      for i in range(generate.MAX_ARTICLE_CONTEXT_FETCHES)]
        candidates.append({"url": "https://example.com/needs-context", "source": "Test", "summary": ""})
        with patch.object(generate, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = ("<article><p>" + "Detailed evidence. " * 20 + "</p></article>").encode()
            result = generate.enrich_candidate_context(candidates)
        self.assertEqual(urlopen.call_count, 1)
        self.assertGreater(len(result[-1]["summary"]), 180)


if __name__ == "__main__":
    unittest.main()
