"""English defaults must preserve configured languages and source content."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from trendradar.core.analyzer import count_word_frequency
from trendradar.core.loader import _load_ai_analysis_config, _load_ai_translation_config


ROOT = Path(__file__).resolve().parents[1]


class EnglishCommandTests(unittest.TestCase):
    def test_cli_help_is_english_without_running_a_crawl(self):
        result = subprocess.run(
            [sys.executable, "-m", "trendradar", "--help"],
            cwd=ROOT,
            env={**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True"},
            capture_output=True, text=True, timeout=30, check=True,
        )
        self.assertIn("Trending news aggregation and analysis", result.stdout)
        self.assertIn("Show the current schedule", result.stdout)
        self.assertIn("Check the environment and configuration", result.stdout)
        self.assertNotRegex(result.stdout, r"[\u3400-\u9fff]")

    def test_schedule_status_is_english_and_respects_the_configured_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                "app:\n  timezone: America/New_York\n"
                "schedule:\n  enabled: false\n"
                "storage:\n  backend: local\n",
                encoding="utf-8",
            )
            (Path(directory) / "timeline.yaml").write_text(
                (ROOT / "config" / "timeline.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-m", "trendradar", "--show-schedule"],
                cwd=directory,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT),
                    "CONFIG_PATH": str(config_path),
                    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                },
                capture_output=True, text=True, timeout=30, check=True,
            )
        self.assertIn("Schedule status", result.stdout)
        self.assertIn("America/New_York", result.stdout)
        self.assertIn("Send notifications:", result.stdout)
        self.assertNotRegex(result.stdout, r"[\u3400-\u9fff]")

    def test_language_defaults_are_english_but_explicit_settings_win(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_load_ai_analysis_config({})["LANGUAGE"], "English")
            self.assertEqual(_load_ai_translation_config({})["LANGUAGE"], "English")
            for loader, section in (
                (_load_ai_analysis_config, "ai_analysis"),
                (_load_ai_translation_config, "ai_translation"),
            ):
                self.assertEqual(loader({section: {"language": "Chinese"}})["LANGUAGE"], "Chinese")

    def test_show_all_group_is_english_and_preserves_multilingual_titles(self):
        title = "人工智能 and AI news"
        with contextlib.redirect_stdout(io.StringIO()):
            stats, total = count_word_frequency(
                results={"example": {title: {"ranks": [1], "url": "https://example.com/news", "mobileUrl": ""}}},
                word_groups=[], filter_words=[], id_to_name={"example": "中文来源"},
                mode="daily",
            )
        self.assertEqual(total, 1)
        self.assertEqual(stats[0]["word"], "All News")
        self.assertEqual(stats[0]["titles"][0]["title"], title)
        self.assertEqual(stats[0]["titles"][0]["source_name"], "中文来源")


if __name__ == "__main__":
    unittest.main()
