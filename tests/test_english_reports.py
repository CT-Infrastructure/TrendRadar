"""English report labels must not alter source text or delivery contracts."""

import copy
import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}):
    from trendradar.ai.analyzer import AIAnalysisResult, AIAnalyzer
    from trendradar.ai.formatter import (
        render_ai_analysis_markdown,
        render_ai_analysis_telegram,
    )
    from trendradar.notification.batch import (
        add_batch_headers,
        get_max_batch_header_size,
        truncate_preserving_footer,
    )
    from trendradar.notification.senders import send_to_ntfy
    from trendradar.notification.splitter import split_content_into_batches
    from trendradar.report.html import render_html_content
    from trendradar.report.rss_html import render_rss_html_content


NOW = datetime(2026, 11, 19, 10, 30)


def report_fixture(count=1):
    titles = [
        {
            "title": f"中文新闻 {i:03d}",
            "source_name": "中文来源",
            "time_display": "10:30",
            "count": 2,
            "ranks": [1, 2],
            "rank_threshold": 5,
            "url": f"https://example.com/article/{i}",
            "mobile_url": "",
            "is_new": False,
        }
        for i in range(count)
    ]
    return {
        "stats": [{"word": "人工智能", "count": count, "titles": titles}],
        "new_titles": [],
        "failed_ids": [],
        "total_new_count": 0,
    }


class EnglishReportTests(unittest.TestCase):
    def test_html_uses_english_metadata_and_preserves_escaped_source_text(self):
        data = report_fixture()
        data["stats"][0]["titles"][0]["title"] = "中文新闻 <script>&"
        before = copy.deepcopy(data)
        for mode, label in (
            ("daily", "Daily Summary"),
            ("current", "Current Ranking"),
            ("incremental", "Incremental Update"),
        ):
            with self.subTest(mode=mode):
                html = render_html_content(data, 1, mode, get_time_func=lambda: NOW)
                self.assertIn('<html lang="en">', html)
                self.assertIn("Trending News Analysis", html)
                self.assertIn("Report type", html)
                self.assertIn(label, html)
                self.assertIn("Search news headlines...", html)
                self.assertIn("Full-page image", html)
                self.assertIn("中文新闻 &lt;script&gt;&amp;", html)
                self.assertIn("人工智能", html)
                self.assertNotIn("中文新闻 <script>&", html)
        self.assertEqual(data, before)

    def test_rss_report_uses_english_labels_without_translating_articles(self):
        html = render_rss_html_content(
            [{"title": "新闻 & <内容>", "feed_id": "feed", "feed_name": "来源",
              "url": "https://example.com/", "published_at": "2026-11-19"}],
            1,
            get_time_func=lambda: NOW,
        )
        self.assertIn('<html lang="en">', html)
        self.assertIn("RSS Feed Updates", html)
        self.assertIn("Save as image", html)
        self.assertIn("Generated at", html)
        self.assertIn("新闻 &amp; &lt;内容&gt;", html)
        self.assertIn("来源", html)

    def test_ai_headings_are_english_and_telegram_content_is_escaped(self):
        result = AIAnalysisResult(
            success=True, core_trends="原文 <b>&", sentiment_controversy="Sentiment",
            signals="Signals", rss_insights="RSS", outlook_strategy="Outlook",
            standalone_summaries={"来源": "摘要"},
        )
        markdown = render_ai_analysis_markdown(result)
        for label in ("AI Trend Analysis", "Key trends", "Sentiment and debate",
                      "Emerging signals", "RSS insights", "Outlook and recommendations",
                      "Standalone source briefs"):
            self.assertIn(label, markdown)
        self.assertIn("原文 <b>&", markdown)
        self.assertIn("原文 &lt;b&gt;&amp;", render_ai_analysis_telegram(result))

    def test_notification_batches_preserve_all_headlines_within_byte_limits(self):
        data = report_fixture(24)
        before = copy.deepcopy(data)
        limit = 1600
        for channel in ("feishu", "dingtalk", "wework", "telegram", "ntfy", "bark", "slack"):
            with self.subTest(channel=channel):
                batches = split_content_into_batches(
                    data, channel,
                    max_bytes=limit - get_max_batch_header_size(channel),
                    get_time_func=lambda: NOW,
                    report_type="全天汇总",
                )
                batches = add_batch_headers(batches, channel, limit)
                self.assertGreater(len(batches), 1)
                for batch in batches:
                    self.assertLessEqual(len(batch.encode("utf-8")), limit)
                    self.assertIn("[Batch ", batch)
                    self.assertIn("Updated:", batch)
                combined = "\n".join(batches)
                self.assertIn("Daily Summary", combined)
                self.assertNotIn("全天汇总", combined)
                for title in data["stats"][0]["titles"]:
                    self.assertIn(title["title"], combined)
        self.assertEqual(data, before)

    def test_footer_truncation_recognizes_english_and_legacy_labels(self):
        for label in ("Updated:", "更新时间："):
            with self.subTest(label=label):
                footer = f"\n\n{label} 2026-11-19 10:30:00"
                result = truncate_preserving_footer("news item\n" * 50 + footer, 128)
                self.assertTrue(result.endswith(footer))
                self.assertLessEqual(len(result.encode("utf-8")), 128)

    def test_ntfy_accepts_english_and_legacy_report_types(self):
        response = Mock(status_code=200)
        for report_type in ("Daily Summary", "全天汇总"):
            with self.subTest(report_type=report_type), patch(
                "trendradar.notification.senders.requests.post", return_value=response
            ) as post:
                self.assertTrue(send_to_ntfy(
                    "https://ntfy.example.com", "news", None, report_fixture(), report_type,
                    split_content_func=lambda *args, **kwargs: ["News report"],
                ))
                self.assertEqual(post.call_args.kwargs["headers"]["Title"], "Daily Summary")


class EnglishAIConfigurationTests(unittest.TestCase):
    def test_analysis_defaults_to_english_but_respects_an_explicit_language(self):
        with patch("trendradar.ai.analyzer.AIClient") as client_class, patch(
            "trendradar.ai.analyzer.load_prompt_template", return_value=("System", "User")
        ):
            client_class.return_value.validate_config.return_value = (True, "")
            self.assertEqual(AIAnalyzer({}, {}, lambda: NOW).language, "English")
            self.assertEqual(
                AIAnalyzer({}, {"LANGUAGE": "Chinese"}, lambda: NOW).language, "Chinese"
            )

    def test_translated_parse_errors_still_trigger_json_repair(self):
        with patch("trendradar.ai.analyzer.AIClient") as client_class, patch(
            "trendradar.ai.analyzer.load_prompt_template",
            return_value=("Analyze news.", "{report_type}; {language}; {news_content}"),
        ), patch("json_repair.repair_json", return_value=[]):
            client = client_class.return_value
            client.validate_config.return_value = (True, "")
            client.api_key = "fixture-key"
            client.chat.side_effect = ["invalid JSON", '{"core_trends":"中文修复后"}']
            analyzer = AIAnalyzer({"MODEL": "test/model"}, {}, lambda: NOW)
            result = analyzer.analyze(report_fixture()["stats"])

            self.assertTrue(result.success)
            self.assertEqual(result.core_trends, "中文修复后")
            self.assertEqual(client.chat.call_count, 2)
            original_messages = client.chat.call_args_list[0].args[0]
            self.assertIn("Daily Summary; English;", original_messages[-1]["content"])
            self.assertIn("中文新闻", original_messages[-1]["content"])
            repair_messages = client.chat.call_args_list[1].args[0]
            self.assertIn("You repair malformed JSON.", repair_messages[0]["content"])
            self.assertIn("invalid JSON", repair_messages[-1]["content"])
            self.assertIn("Preserve the original meaning and language", repair_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
