# coding=utf-8
"""环境体检命令"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from trendradar import __version__
from trendradar.context import AppContext
from trendradar.core import load_config, parse_multi_account_config, validate_paired_configs


def _record_result(results: List[Tuple[str, str, str]], status: str, item: str, detail: str) -> None:
    icon_map = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    icon = icon_map.get(status, "•")
    results.append((status, item, detail))
    print(f"{icon} {item}: {detail}")


def _save_report(
    results: List[Tuple[str, str, str]],
    pass_count: int,
    warn_count: int,
    fail_count: int,
    config_path: Optional[str],
) -> None:
    report = {
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": config_path or os.environ.get("CONFIG_PATH", "config/config.yaml"),
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "ok": fail_count == 0,
        },
        "checks": [
            {"status": status, "item": item, "detail": detail}
            for status, item, detail in results
        ],
    }

    try:
        output_dir = Path("output") / "meta"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "doctor_report.json"
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Diagnostic report saved: {output_path}")
    except Exception as e:
        print(f"⚠️ Failed to save diagnostic report: {e}")


def run_doctor(config_path: Optional[str] = None) -> bool:
    """运行环境体检"""
    print("=" * 60)
    print(f"TrendRadar v{__version__} Environment diagnostics")
    print("=" * 60)

    results: List[Tuple[str, str, str]] = []
    config = None

    # 1) Python 版本检查
    py_ok = sys.version_info >= (3, 12)
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if py_ok:
        _record_result(results, "pass", "Python version", f"{py_version} (meets >= 3.12)")
    else:
        _record_result(results, "fail", "Python version", f"{py_version} (does not meet >= 3.12)")

    # 2) 关键文件检查
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")

    required_files = [
        (config_path, "Main configuration"),
        ("config/frequency_words.txt", "Keyword file"),
    ]
    optional_files = [
        ("config/timeline.yaml", "Schedule file"),
    ]

    for path_str, desc in required_files:
        if Path(path_str).exists():
            _record_result(results, "pass", desc, f"Found: {path_str}")
        else:
            _record_result(results, "fail", desc, f"Missing: {path_str}")

    for path_str, desc in optional_files:
        if Path(path_str).exists():
            _record_result(results, "pass", desc, f"Found: {path_str}")
        else:
            _record_result(results, "warn", desc, f"Not found: {path_str} (the default schedule template will be used)")

    # 3) 配置加载检查
    try:
        config = load_config(config_path)
        _record_result(results, "pass", "Configuration loading", f"Loaded: {config_path}")
    except Exception as e:
        _record_result(results, "fail", "Configuration loading", f"Loading failed: {e}")

    if config:
        _check_with_config(results, config)

    pass_count = sum(1 for status, _, _ in results if status == "pass")
    warn_count = sum(1 for status, _, _ in results if status == "warn")
    fail_count = sum(1 for status, _, _ in results if status == "fail")

    _save_report(results, pass_count, warn_count, fail_count, config_path)

    print("-" * 60)
    print(f"Diagnostic results: ✅ {pass_count} passed  ⚠️ {warn_count} warnings  ❌ {fail_count} failed")
    print("=" * 60)

    if fail_count == 0:
        print("Diagnostics passed.")
        return True

    print("Diagnostics failed. Resolve the failed checks before continuing.")
    return False


def _check_with_config(results: List[Tuple[str, str, str]], config: Dict) -> None:
    # 4) 调度配置检查
    try:
        ctx = AppContext(config)
        schedule = ctx.create_scheduler().resolve()
        detail = f"Schedule resolved (report_mode={schedule.report_mode}, ai_mode={schedule.ai_mode})"
        _record_result(results, "pass", "Schedule configuration", detail)
    except Exception as e:
        _record_result(results, "fail", "Schedule configuration", f"Parsing failed: {e}")

    # 5) AI 配置检查
    ai_analysis_enabled = config.get("AI_ANALYSIS", {}).get("ENABLED", False)
    ai_translation_enabled = config.get("AI_TRANSLATION", {}).get("ENABLED", False)
    ai_filter_enabled = config.get("FILTER", {}).get("METHOD", "keyword") == "ai"
    ai_enabled = ai_analysis_enabled or ai_translation_enabled or ai_filter_enabled

    if ai_enabled:
        try:
            from trendradar.ai.client import AIClient
            valid, message = AIClient(config.get("AI", {})).validate_config()
            if valid:
                _record_result(results, "pass", "AI configuration", f"Model: {config.get('AI', {}).get('MODEL', '')}")
            else:
                if ai_analysis_enabled or ai_translation_enabled:
                    _record_result(results, "fail", "AI configuration", message)
                else:
                    _record_result(results, "warn", "AI configuration", f"{message} (AI filtering will fall back to keyword mode)")
        except Exception as e:
            _record_result(results, "fail", "AI configuration", f"Validation error: {e}")
    else:
        _record_result(results, "warn", "AI configuration", "AI features disabled; skipping validation")

    # 6) 存储配置检查
    try:
        storage_cfg = config.get("STORAGE", {})
        backend = storage_cfg.get("BACKEND", "auto")
        remote = storage_cfg.get("REMOTE", {})
        missing_remote_keys = [
            k for k in ("BUCKET_NAME", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "ENDPOINT_URL")
            if not remote.get(k)
        ]

        if backend == "remote" and missing_remote_keys:
            _record_result(
                results, "fail", "Storage configuration",
                f"Missing remote storage settings: {', '.join(missing_remote_keys)}"
            )
        elif backend == "auto" and os.environ.get("GITHUB_ACTIONS") == "true" and missing_remote_keys:
            _record_result(
                results, "warn", "Storage configuration",
                "GitHub Actions with auto storage has incomplete remote settings; data may not persist"
            )
        else:
            sm = AppContext(config).get_storage_manager()
            _record_result(results, "pass", "Storage configuration", f"Current backend: {sm.backend_name}")
    except Exception as e:
        _record_result(results, "fail", "Storage configuration", f"Check failed: {e}")

    # 7) 通知渠道配置检查
    _check_notification_channels(results, config)

    # 8) 输出目录可写检查
    try:
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        probe_file = output_dir / ".doctor_write_probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        _record_result(results, "pass", "Output directory", f"Writable: {output_dir}")
    except Exception as e:
        _record_result(results, "fail", "Output directory", f"Not writable: {e}")


def _check_notification_channels(results: List[Tuple[str, str, str]], config: Dict) -> None:
    channel_details = []
    channel_issues = []
    max_accounts = config.get("MAX_ACCOUNTS_PER_CHANNEL", 3)

    for key, name in [
        ("FEISHU_WEBHOOK_URL", "Feishu"),
        ("DINGTALK_WEBHOOK_URL", "DingTalk"),
        ("WEWORK_WEBHOOK_URL", "WeCom"),
        ("BARK_URL", "Bark"),
        ("SLACK_WEBHOOK_URL", "Slack"),
        ("GENERIC_WEBHOOK_URL", "Generic webhook"),
    ]:
        values = parse_multi_account_config(config.get(key, ""))
        if values:
            channel_details.append(f"{name}({min(len(values), max_accounts)} accounts)")

    tg_tokens = parse_multi_account_config(config.get("TELEGRAM_BOT_TOKEN", ""))
    tg_chats = parse_multi_account_config(config.get("TELEGRAM_CHAT_ID", ""))
    if tg_tokens or tg_chats:
        valid, count = validate_paired_configs(
            {"bot_token": tg_tokens, "chat_id": tg_chats},
            "Telegram",
            required_keys=["bot_token", "chat_id"],
        )
        if valid and count > 0:
            channel_details.append(f"Telegram({min(count, max_accounts)} accounts)")
        else:
            channel_issues.append("Telegram bot_token/chat_id settings are incomplete or their counts do not match")

    ntfy_server = config.get("NTFY_SERVER_URL", "")
    ntfy_topics = parse_multi_account_config(config.get("NTFY_TOPIC", ""))
    ntfy_tokens = parse_multi_account_config(config.get("NTFY_TOKEN", ""))
    if ntfy_server and ntfy_topics:
        if ntfy_tokens:
            valid, count = validate_paired_configs(
                {"topic": ntfy_topics, "token": ntfy_tokens},
                "ntfy",
            )
            if valid and count > 0:
                channel_details.append(f"ntfy({min(count, max_accounts)} accounts)")
            else:
                channel_issues.append("ntfy topic/token counts do not match")
        else:
            channel_details.append(f"ntfy({min(len(ntfy_topics), max_accounts)} accounts)")

    email_ready = all(
        [config.get("EMAIL_FROM"), config.get("EMAIL_PASSWORD"), config.get("EMAIL_TO")]
    )
    if email_ready:
        channel_details.append("Email")
    elif any([config.get("EMAIL_FROM"), config.get("EMAIL_PASSWORD"), config.get("EMAIL_TO")]):
        channel_issues.append("Email settings are incomplete (from/password/to are all required)")

    if channel_issues and not channel_details:
        _record_result(results, "fail", "Notification configuration", "; ".join(channel_issues))
    elif channel_issues and channel_details:
        detail = f"Available channels: {', '.join(channel_details)}; issues: {'; '.join(channel_issues)}"
        _record_result(results, "warn", "Notification configuration", detail)
    elif channel_details:
        _record_result(results, "pass", "Notification configuration", f"Available channels: {', '.join(channel_details)}")
    else:
        _record_result(results, "warn", "Notification configuration", "No notification channels configured")
