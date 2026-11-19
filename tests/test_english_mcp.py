"""English MCP output must retain multilingual input and article content."""

import json
import os
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}):
    from mcp_server.tools.analytics import AnalyticsTools
    from mcp_server.tools.notification import (
        NotificationTools,
        _markdown_to_simple_html,
        _prepare_batches,
    )
    from mcp_server.utils.date_parser import DateParser
    from mcp_server.utils.errors import InvalidParameterError
    from mcp_server.utils.validators import validate_limit


class EnglishMCPTests(unittest.TestCase):
    def test_date_aliases_resolve_identically_with_english_descriptions(self):
        for english, chinese in (
            ("today", "今天"),
            ("yesterday", "昨天"),
            ("this week", "本周"),
            ("last month", "上月"),
            ("last 7 days", "最近7天"),
        ):
            with self.subTest(expression=english):
                result = DateParser.resolve_date_range_expression(english)
                legacy = DateParser.resolve_date_range_expression(chinese)
                self.assertEqual(result["date_range"], legacy["date_range"])
                self.assertEqual(result["normalized"], legacy["normalized"])
                self.assertEqual(result["description"], legacy["description"])
                self.assertNotRegex(result["description"], r"[\u3400-\u9fff]")

    def test_parameter_error_is_actionable_in_english(self):
        with self.assertRaises(InvalidParameterError) as raised:
            validate_limit(-1)
        error = raised.exception.to_dict()
        self.assertEqual(error["code"], "INVALID_PARAMETER")
        self.assertIn("greater than 0", error["message"])
        self.assertNotRegex(json.dumps(error, ensure_ascii=False), r"[\u3400-\u9fff]")

    def test_channel_guides_are_english_and_keep_all_channel_ids(self):
        tools = NotificationTools.__new__(NotificationTools)
        result = tools.get_channel_format_guide()
        self.assertEqual(set(result["guides"]), {
            "feishu", "dingtalk", "wework", "telegram", "email", "ntfy",
            "bark", "slack", "generic_webhook",
        })
        self.assertNotRegex(json.dumps(result, ensure_ascii=False), r"[\u3400-\u9fff]")
        self.assertIn("{content}", result["guides"]["generic_webhook"]["prompt"])
        invalid = tools.get_channel_format_guide("missing")
        self.assertFalse(invalid["success"])
        self.assertIn("Invalid channel", invalid["error"]["message"])

    def test_analysis_prompt_translates_instructions_without_editing_titles(self):
        headline = "中文新闻 & original <content>"
        analyzer = AnalyticsTools.__new__(AnalyticsTools)
        prompt = analyzer._create_sentiment_analysis_prompt([
            {"platform": "Original source", "title": headline, "date": "2026-01-01"}
        ], topic="AI")
        self.assertIn(headline, prompt)
        self.assertIn("Return the analysis in English", prompt)
        self.assertNotRegex(prompt.replace(headline, ""), r"[\u3400-\u9fff]")

    def test_email_escapes_content_and_batching_keeps_unicode(self):
        headline = "中文新闻 <script>alert(1)</script>"
        rendered = _markdown_to_simple_html(headline)
        self.assertIn('<html lang="en">', rendered)
        self.assertIn("TrendRadar Notification", rendered)
        self.assertIn("中文新闻", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        batches = _prepare_batches("中文新闻\n\n" * 80, "ntfy", {"ntfy": 200})
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch.encode("utf-8")) <= 200 for batch in batches))
        self.assertEqual(sum(batch.count("中文新闻") for batch in batches), 80)


if __name__ == "__main__":
    unittest.main()
