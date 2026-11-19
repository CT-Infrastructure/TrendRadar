# coding=utf-8
"""通知测试命令"""

import copy
from pathlib import Path
from typing import Dict, Optional

from trendradar.context import AppContext


def _build_test_report_data(ctx: AppContext) -> Dict:
    now = ctx.get_time()
    time_display = now.strftime("%H:%M")
    title = f"TrendRadar test notification ({now.strftime('%Y-%m-%d %H:%M:%S')})"

    return {
        "stats": [
            {
                "word": "Connectivity test",
                "count": 1,
                "titles": [
                    {
                        "title": title,
                        "source_name": "TrendRadar",
                        "url": "https://github.com/CT-Infrastructure/TrendRadar",
                        "mobile_url": "",
                        "ranks": [1],
                        "rank_threshold": ctx.rank_threshold,
                        "count": 1,
                        "is_new": True,
                        "time_display": time_display,
                        "matched_keyword": "Connectivity test",
                    }
                ],
            }
        ],
        "failed_ids": [],
        "new_titles": [],
        "id_to_name": {},
    }


def _create_test_html_file(ctx: AppContext) -> Optional[str]:
    try:
        now = ctx.get_time()
        output_dir = Path("output") / "html" / ctx.format_date()
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / f"notification_test_{ctx.format_time()}.html"
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>TrendRadar Notification Test</title></head>
<body>
<h2>TrendRadar Notification Connectivity Test</h2>
<p>Test time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({ctx.timezone})</p>
<p>This test message checks whether the email channel can be reached.</p>
</body>
</html>"""
        html_path.write_text(html_content, encoding="utf-8")
        return str(html_path)
    except Exception as e:
        print(f"[Test notification] Failed to create test HTML: {e}")
        return None


def run_test_notification(config: Dict) -> bool:
    """发送测试通知到已配置渠道"""
    from trendradar.notification import NotificationDispatcher

    ctx = AppContext(config)

    try:
        has_notification = any(
            [
                config.get("FEISHU_WEBHOOK_URL"),
                config.get("DINGTALK_WEBHOOK_URL"),
                config.get("WEWORK_WEBHOOK_URL"),
                (config.get("TELEGRAM_BOT_TOKEN") and config.get("TELEGRAM_CHAT_ID")),
                (config.get("EMAIL_FROM") and config.get("EMAIL_PASSWORD") and config.get("EMAIL_TO")),
                (config.get("NTFY_SERVER_URL") and config.get("NTFY_TOPIC")),
                config.get("BARK_URL"),
                config.get("SLACK_WEBHOOK_URL"),
                config.get("GENERIC_WEBHOOK_URL"),
            ]
        )
        if not has_notification:
            print("No notification channels available. Configure one in config.yaml or environment variables first.")
            return False

        test_config = copy.deepcopy(config)
        test_display = test_config.setdefault("DISPLAY", {})
        test_regions = test_display.setdefault("REGIONS", {})
        test_regions.update(
            {
                "HOTLIST": True,
                "NEW_ITEMS": False,
                "RSS": False,
                "STANDALONE": False,
                "AI_ANALYSIS": False,
            }
        )

        if "AI_TRANSLATION" in test_config:
            test_config["AI_TRANSLATION"]["ENABLED"] = False

        proxy_url = test_config.get("DEFAULT_PROXY", "") if test_config.get("USE_PROXY") else None
        if proxy_url:
            print("[Test notification] Proxy detected; sending through the proxy")

        dispatcher = NotificationDispatcher(
            config=test_config,
            get_time_func=ctx.get_time,
            split_content_func=ctx.split_content,
            translator=None,
        )

        report_data = _build_test_report_data(ctx)
        html_file_path = _create_test_html_file(ctx)

        print("=" * 60)
        print("Notification connectivity test")
        print("=" * 60)

        results = dispatcher.dispatch_all(
            report_data=report_data,
            report_type="Notification Test",
            proxy_url=proxy_url,
            mode="daily",
            html_file_path=html_file_path,
        )

        if not results:
            print("No valid notification channels to test (configuration may be incomplete).")
            return False

        print("-" * 60)
        success_count = 0
        for channel, ok in results.items():
            if ok:
                success_count += 1
                print(f"✅ {channel}: Test succeeded")
            else:
                print(f"❌ {channel}: Test failed")

        print("-" * 60)
        print(f"Test results: {success_count}/{len(results)} channels succeeded")
        return success_count > 0
    finally:
        ctx.cleanup()
