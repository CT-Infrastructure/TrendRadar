# coding=utf-8
"""调度状态查看命令"""

from typing import Dict

from trendradar import __version__
from trendradar.context import AppContext


def handle_status_commands(config: Dict) -> None:
    """显示当前调度状态"""
    ctx = AppContext(config)

    print("=" * 60)
    print(f"TrendRadar v{__version__} Schedule status")
    print("=" * 60)

    try:
        scheduler = ctx.create_scheduler()
        schedule = scheduler.resolve()

        now = ctx.get_time()
        date_str = ctx.format_date()

        print(f"\n⏰ Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({ctx.timezone})")
        print(f"📅 Current date: {date_str}")

        print(f"\n📋 Schedule details:")
        print(f"  Day plan: {schedule.day_plan}")
        if schedule.period_key:
            print(f"  Current period: {schedule.period_name or schedule.period_key} ({schedule.period_key})")
        else:
            print(f"  Current period: none (using defaults)")

        print(f"\n🔧 Enabled actions:")
        print(f"  Collect data: {'✅ Yes' if schedule.collect else '❌ No'}")
        print(f"  AI analysis:  {'✅ Yes' if schedule.analyze else '❌ No'}")
        print(f"  Send notifications: {'✅ Yes' if schedule.push else '❌ No'}")
        print(f"  Report mode: {schedule.report_mode}")
        print(f"  AI mode:  {schedule.ai_mode}")

        if schedule.period_key:
            print(f"\n🔁 Run-once controls:")
            if schedule.once_analyze:
                already_analyzed = scheduler.already_executed(schedule.period_key, "analyze", date_str)
                print(f"  AI analysis:  Once only {'(already run today ⚠️)' if already_analyzed else '(not yet run today ✅)'}")
            else:
                print(f"  AI analysis:  Unlimited")
            if schedule.once_push:
                already_pushed = scheduler.already_executed(schedule.period_key, "push", date_str)
                print(f"  Send notifications: Once only {'(already run today ⚠️)' if already_pushed else '(not yet run today ✅)'}")
            else:
                print(f"  Send notifications: Unlimited")

    except Exception as e:
        print(f"\n❌ Failed to retrieve schedule status: {e}")

    print("\n" + "=" * 60)

    ctx.cleanup()
