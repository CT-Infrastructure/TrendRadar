#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
News crawler container management - supercronic
"""

import os
import sys
import subprocess
import time
import signal
from pathlib import Path

# Web 服务器配置
_raw_port = int(os.environ.get("WEBSERVER_PORT", "8080"))
WEBSERVER_PORT = _raw_port if 1 <= _raw_port <= 65535 else 8080
WEBSERVER_DIR = "/app/output"
WEBSERVER_PID_FILE = "/tmp/webserver.pid"


def manual_run():
    """Run the crawler once"""
    print("🔄 Running the crawler manually...")
    try:
        result = subprocess.run(
            ["python", "-m", "trendradar"], cwd="/app", capture_output=False, text=True
        )
        if result.returncode == 0:
            print("✅ Run completed")
        else:
            print(f"❌ Run failed; exit code: {result.returncode}")
    except Exception as e:
        print(f"❌ Execution error: {e}")


def parse_cron_schedule(cron_expr):
    """Return a readable description of a cron expression."""
    if not cron_expr or cron_expr == "Not set":
        return "Not set"
    
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return f"Expression: {cron_expr}"
        
        minute, hour, day, month, weekday = parts
        
        # 分析分钟
        if minute == "*":
            minute_desc = "every minute"
        elif minute.startswith("*/"):
            interval = minute[2:]
            minute_desc = f"every {interval} minutes"
        elif "," in minute:
            minute_desc = f"at minute {minute}"
        else:
            minute_desc = f"at minute {minute}"
        
        # 分析小时
        if hour == "*":
            hour_desc = "every hour"
        elif hour.startswith("*/"):
            interval = hour[2:]
            hour_desc = f"every {interval} hours"
        elif "," in hour:
            hour_desc = f"at hour {hour}"
        else:
            hour_desc = f"at hour {hour}"
        
        # 分析日期
        if day == "*":
            day_desc = "daily"
        elif day.startswith("*/"):
            interval = day[2:]
            day_desc = f"every {interval} days"
        else:
            day_desc = f"on day {day} of the month"
        
        # 分析月份
        if month == "*":
            month_desc = "monthly"
        else:
            month_desc = f"in month {month}"
        
        # 分析星期
        weekday_names = {
            "0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
            "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday"
        }
        if weekday == "*":
            weekday_desc = ""
        else:
            weekday_desc = f"on {weekday_names.get(weekday, weekday)}"
        
        # 组合描述
        if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
            # 简单的间隔模式，如 */30 * * * *
            return f"Run every {minute[2:]} minutes"
        elif hour != "*" and minute != "*" and day == "*" and month == "*" and weekday == "*":
            # daily特定时间，如 0 9 * * *
            return f"Run daily at {hour}:{minute.zfill(2)}"
        elif weekday != "*" and day == "*":
            # 每周特定时间
            return f"Run {weekday_desc} at {hour}:{minute.zfill(2)}"
        else:
            # 复杂模式，显示详细信息
            desc_parts = [part for part in [month_desc, day_desc, weekday_desc, hour_desc, minute_desc] if part and part != "monthly" and part != "daily" and part != "every hour"]
            if desc_parts:
                return "Run " + " ".join(desc_parts)
            else:
                return f"Cron expression: {cron_expr}"
    
    except Exception as e:
        return f"Could not parse: {cron_expr}"


def show_status():
    """显示容器状态"""
    print("📊 Container status:")

    # 检查 PID 1 状态
    supercronic_is_pid1 = False
    pid1_cmdline = ""
    try:
        with open('/proc/1/cmdline', 'rb') as f:
            pid1_cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore').strip()
        print(f"  🔍 PID 1 process: {pid1_cmdline}")

        if "supercronic" in pid1_cmdline.lower():
            print("  ✅ supercronic is running correctly as PID 1")
            supercronic_is_pid1 = True
        else:
            print("  ❌ PID 1 is not supercronic")
            print(f"  📋 Actual PID 1: {pid1_cmdline}")
    except Exception as e:
        print(f"  ❌ Could not read PID 1 information: {e}")

    # 检查环境变量
    cron_schedule = os.environ.get("CRON_SCHEDULE", "Not set")
    run_mode = os.environ.get("RUN_MODE", "Not set")
    immediate_run = os.environ.get("IMMEDIATE_RUN", "Not set")
    
    print(f"  ⚙️ Runtime configuration:")
    print(f"    CRON_SCHEDULE: {cron_schedule}")
    
    # 解析并显示cron表达式的含义
    cron_description = parse_cron_schedule(cron_schedule)
    print(f"    ⏰ Run frequency: {cron_description}")
    
    print(f"    RUN_MODE: {run_mode}")
    print(f"    IMMEDIATE_RUN: {immediate_run}")

    # 检查配置文件
    config_files = ["/app/config/config.yaml", "/app/config/frequency_words.txt"]
    print("  📁 Configuration files:")
    for file_path in config_files:
        if Path(file_path).exists():
            print(f"    ✅ {Path(file_path).name}")
        else:
            print(f"    ❌ {Path(file_path).name} is missing")

    # 检查关键文件
    key_files = [
        ("/usr/local/bin/supercronic-linux-amd64", "supercronic binary"),
        ("/usr/local/bin/supercronic", "supercronic symlink"),
        ("/tmp/crontab", "crontab file"),
        ("/entrypoint.sh", "Entrypoint script")
    ]
    
    print("  📂 Required files:")
    for file_path, description in key_files:
        if Path(file_path).exists():
            print(f"    ✅ {description}: present")
            # 对于crontab file，显示内容
            if file_path == "/tmp/crontab":
                try:
                    with open(file_path, 'r') as f:
                        crontab_content = f.read().strip()
                        print(f"         Contents: {crontab_content}")
                except Exception:
                    pass
        else:
            print(f"    ❌ {description}: missing")

    # 检查容器运行时间
    print("  ⏱️ Container timing:")
    try:
        # 检查 PID 1 的启动时间
        with open('/proc/1/stat', 'r') as f:
            stat_content = f.read().strip().split()
            if len(stat_content) >= 22:
                # starttime 是第22个字段（索引21）
                starttime_ticks = int(stat_content[21])
                
                # 读取系统启动时间
                with open('/proc/stat', 'r') as stat_f:
                    for line in stat_f:
                        if line.startswith('btime'):
                            boot_time = int(line.split()[1])
                            break
                    else:
                        boot_time = 0
                
                # 读取系统时钟频率
                clock_ticks = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                
                if boot_time > 0:
                    pid1_start_time = boot_time + (starttime_ticks / clock_ticks)
                    current_time = time.time()
                    uptime_seconds = int(current_time - pid1_start_time)
                    uptime_minutes = uptime_seconds // 60
                    uptime_hours = uptime_minutes // 60
                    
                    if uptime_hours > 0:
                        print(f"    PID 1 uptime: {uptime_hours} hours {uptime_minutes % 60} minutes")
                    else:
                        print(f"    PID 1 uptime: {uptime_minutes} minutes ({uptime_seconds} seconds)")
                else:
                    print(f"    PID 1 uptime: unable to calculate precisely")
            else:
                print("    ❌ Could not parse PID 1 statistics")
    except Exception as e:
        print(f"    ❌ Time check failed: {e}")

    # 状态总结和建议
    print("  📊 Status summary:")
    if supercronic_is_pid1:
        print("    ✅ supercronic is running correctly as PID 1")
        print("    ✅ Scheduled tasks should run normally")
        
        # 显示当前的调度信息
        if cron_schedule != "Not set":
            print(f"    ⏰ Current schedule: {cron_description}")
            
            # 提供一些常见的调度建议
            if "minute" in cron_description and "every 30 minutes" not in cron_description and "every 60 minutes" not in cron_description:
                print("    💡 Frequent runs, suitable for live monitoring")
            elif "hour" in cron_description:
                print("    💡 Hourly runs, suitable for periodic summaries")
            elif "daily" in cron_description or "day" in cron_description:
                print("    💡 Daily runs, suitable for daily reports")
        
        print("    💡 If scheduled tasks do not run, check:")
        print("       • The crontab syntax")
        print("       • The timezone setting")
        print("       • Application errors")
    else:
        print("    ❌ supercronic is not running as expected")
        if pid1_cmdline:
            print(f"    📋 Current PID 1: {pid1_cmdline}")
        print("    💡 Suggested steps:")
        print("       • Restart the container: docker restart trendradar")
        print("       • Check container logs: docker logs trendradar")

    # 显示日志检查建议
    print("  📋 Runtime diagnostics:")
    print("    • View full container logs: docker logs trendradar")
    print("    • Follow live logs: docker logs -f trendradar")
    print("    • Run manually: python manage.py run")
    print("    • Restart the container: docker restart trendradar")


def show_config():
    """显示当前配置"""
    print("⚙️ Current configuration:")

    env_vars = [
        # 运行配置
        "CRON_SCHEDULE",
        "RUN_MODE",
        "IMMEDIATE_RUN",
        # 通知渠道
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "WEWORK_WEBHOOK_URL",
        "WEWORK_MSG_TYPE",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NTFY_SERVER_URL",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "BARK_URL",
        "SLACK_WEBHOOK_URL",
        # AI 分析配置
        "AI_ANALYSIS_ENABLED",
        "AI_API_KEY",
        "AI_PROVIDER",
        "AI_MODEL",
        "AI_BASE_URL",
        # 远程存储配置
        "S3_BUCKET_NAME",
        "S3_ACCESS_KEY_ID",
        "S3_ENDPOINT_URL",
        "S3_REGION",
    ]

    for var in env_vars:
        value = os.environ.get(var, "Not set")
        # 隐藏敏感信息
        if var == "BARK_URL" or any(sensitive in var for sensitive in ["WEBHOOK", "TOKEN", "KEY", "SECRET"]):
            if value and value != "Not set":
                masked_value = value[:10] + "***" if len(value) > 10 else "***"
                print(f"  {var}: {masked_value}")
            else:
                print(f"  {var}: {value}")
        else:
            print(f"  {var}: {value}")

    crontab_file = "/tmp/crontab"
    if Path(crontab_file).exists():
        print("  📅 Crontab contents:")
        try:
            with open(crontab_file, "r") as f:
                content = f.read().strip()
                print(f"    {content}")
        except Exception as e:
            print(f"    Read failed: {e}")
    else:
        print("  📅 Crontab file does not exist")


def show_files():
    """显示输出文件"""
    print("📁 Output files:")

    output_dir = Path("/app/output")
    if not output_dir.exists():
        print("  📭 Output directory does not exist")
        return

    # 新结构：扁平化目录
    # - output/news/*.db
    # - output/rss/*.db
    # - output/txt/{date}/*.txt
    # - output/html/{date}/*.html

    # 检查 news 数据库
    news_dir = output_dir / "news"
    if news_dir.exists():
        db_files = sorted(news_dir.glob("*.db"), key=lambda x: x.name, reverse=True)
        if db_files:
            print(f"  💾 Trending news databases (news/): {len(db_files)}")
            for db_file in db_files[:5]:
                mtime = time.ctime(db_file.stat().st_mtime)
                size_kb = db_file.stat().st_size // 1024
                print(f"    📀 {db_file.name} ({size_kb}KB, {mtime.split()[3][:5]})")
            if len(db_files) > 5:
                print(f"    ... and {len(db_files) - 5} more")

    # 检查 RSS 数据库
    rss_dir = output_dir / "rss"
    if rss_dir.exists():
        db_files = sorted(rss_dir.glob("*.db"), key=lambda x: x.name, reverse=True)
        if db_files:
            print(f"  📰 RSS databases (rss/): {len(db_files)}")
            for db_file in db_files[:5]:
                mtime = time.ctime(db_file.stat().st_mtime)
                size_kb = db_file.stat().st_size // 1024
                print(f"    📀 {db_file.name} ({size_kb}KB, {mtime.split()[3][:5]})")
            if len(db_files) > 5:
                print(f"    ... and {len(db_files) - 5} more")

    # 检查 TXT 快照目录
    txt_dir = output_dir / "txt"
    if txt_dir.exists():
        date_dirs = sorted([d for d in txt_dir.iterdir() if d.is_dir()], reverse=True)
        if date_dirs:
            print(f"  📄 TXT snapshots (txt/): {len(date_dirs)} days")
            for date_dir in date_dirs[:3]:
                txt_files = list(date_dir.glob("*.txt"))
                if txt_files:
                    recent = sorted(txt_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
                    mtime = time.ctime(recent.stat().st_mtime)
                    print(f"    📅 {date_dir.name}: {len(txt_files)} files (latest: {mtime.split()[3][:5]})")

    # 检查 HTML 报告目录
    html_dir = output_dir / "html"
    if html_dir.exists():
        date_dirs = sorted([d for d in html_dir.iterdir() if d.is_dir()], reverse=True)
        if date_dirs:
            print(f"  🌐 HTML reports (html/): {len(date_dirs)} days")
            for date_dir in date_dirs[:3]:
                html_files = list(date_dir.glob("*.html"))
                if html_files:
                    recent = sorted(html_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
                    mtime = time.ctime(recent.stat().st_mtime)
                    print(f"    📅 {date_dir.name}: {len(html_files)} files (latest: {mtime.split()[3][:5]})")


def show_logs():
    """显示实时日志"""
    print("📋 Live logs (press Ctrl+C to exit):")
    print("💡 Tip: This displays output from the PID 1 process")
    try:
        # 尝试多种方法查看日志
        log_files = [
            "/proc/1/fd/1",  # PID 1 的标准输出
            "/proc/1/fd/2",  # PID 1 的标准错误
        ]
        
        for log_file in log_files:
            if Path(log_file).exists():
                print(f"📄 Trying to read: {log_file}")
                subprocess.run(["tail", "-f", log_file], check=True)
                break
        else:
            print("📋 Standard log files not found; use: docker logs trendradar")
            
    except KeyboardInterrupt:
        print("\n👋 Stopped following logs")
    except Exception as e:
        print(f"❌ Could not read logs: {e}")
        print("💡 Use: docker logs trendradar")


def restart_supercronic():
    """重启supercronic进程"""
    print("🔄 Restarting supercronic...")
    print("⚠️ Note: supercronic is PID 1 and cannot be restarted directly")

    # 检查当前 PID 1
    try:
        with open('/proc/1/cmdline', 'rb') as f:
            pid1_cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore').strip()
        print(f"  🔍 Current PID 1: {pid1_cmdline}")

        if "supercronic" in pid1_cmdline.lower():
            print("  ✅ PID 1 is supercronic")
            print("  💡 To restart supercronic, restart the entire container:")
            print("    docker restart trendradar")
        else:
            print("  ❌ PID 1 is not supercronic; this is unexpected")
            print("  💡 Restart the container to resolve this:")
            print("    docker restart trendradar")
    except Exception as e:
        print(f"  ❌ Could not inspect PID 1: {e}")
        print("  💡 Restart the container: docker restart trendradar")


def _read_proc_cmdline(pid: int) -> str:
    """读取进程 cmdline，失败时返回空字符串。"""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.exists():
        return ""
    try:
        with open(proc_cmdline, "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _is_expected_webserver_process(pid: int) -> bool:
    """检查 pid 是否是当前端口的 http.server 进程。"""
    cmdline = _read_proc_cmdline(pid)
    if not cmdline:
        return False
    return "http.server" in cmdline and str(WEBSERVER_PORT) in cmdline


def _terminate_webserver_process(pid: int, require_expected: bool = True) -> bool:
    """尝试终止 Web 服务器进程。

    require_expected=True 时，仅终止确认是 http.server 的进程，避免误杀。
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return True

    if require_expected and not _is_expected_webserver_process(pid):
        print(f"  ⚠️ PID {pid} exists but is not the web server; leaving it running")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            print(f"  ⚠️ Force-stopped web server (PID: {pid})")
        except OSError:
            print(f"  ✅ Web server stopped (PID: {pid})")
        return True
    except OSError:
        return True


def _is_webserver_running(pid: int) -> bool:
    """检查 Web 服务器进程是否真正在运行。"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    if not _is_expected_webserver_process(pid):
        return False

    try:
        import urllib.request
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(f"http://127.0.0.1:{WEBSERVER_PORT}/", method="HEAD")
        opener.open(req, timeout=3)
        return True
    except Exception:
        try:
            time.sleep(1)
            opener.open(req, timeout=3)
            return True
        except Exception:
            return False


def _cleanup_stale_pid():
    """清理失效的 PID 文件"""
    if not Path(WEBSERVER_PID_FILE).exists():
        return False

    try:
        with open(WEBSERVER_PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        os.remove(WEBSERVER_PID_FILE)
        print(f"  🧹 Removed stale PID file (PID: {old_pid})")
        return True
    except Exception:
        return False


def start_webserver():
    """启动 Web 服务器托管 output 目录"""
    print(f"🌐 Starting web server (port: {WEBSERVER_PORT})...")
    print(f"  🔒 Static files only; access is limited to {WEBSERVER_DIR}")

    # 检查是否已经运行
    if Path(WEBSERVER_PID_FILE).exists():
        try:
            with open(WEBSERVER_PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())

            # 使用增强的进程检查
            if _is_webserver_running(old_pid):
                print(f"  ⚠️ Web server is already running (PID: {old_pid})")
                print(f"  💡 Open: http://localhost:{WEBSERVER_PORT}")
                print("  💡 Stop the server: python manage.py stop_webserver")
                return

            # 进程异常时优先尝试终止旧进程，避免端口占用导致重启失败
            _terminate_webserver_process(old_pid, require_expected=True)
            _cleanup_stale_pid()
            print(f"  ℹ️ Removed a stale PID file")

        except Exception as e:
            print(f"  ⚠️ Cleaning up the old PID file: {e}")
            _cleanup_stale_pid()

    # 检查目录是否存在
    if not Path(WEBSERVER_DIR).exists():
        print(f"  ❌ Directory does not exist: {WEBSERVER_DIR}")
        return

    try:
        # 启动 HTTP 服务器
        # 使用 --bind 绑定到 0.0.0.0 使容器内部可访问
        # 工作目录限制在 WEBSERVER_DIR，防止访问其他目录
        process = subprocess.Popen(
            [sys.executable, '-m', 'http.server', str(WEBSERVER_PORT), '--bind', '0.0.0.0'],
            cwd=WEBSERVER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # 等待一下确保服务器启动
        time.sleep(1)

        # 检查进程是否还在运行
        if process.poll() is None:
            # 保存 PID
            with open(WEBSERVER_PID_FILE, 'w') as f:
                f.write(str(process.pid))
            print(f"  ✅ Web server started (PID: {process.pid})")
            print(f"  📁 Serving: {WEBSERVER_DIR} (read-only static files)")
            print(f"  🌐 URL: http://localhost:{WEBSERVER_PORT}")
            print(f"  📄 Home page: http://localhost:{WEBSERVER_PORT}/index.html")
            print("  💡 Stop the server: python manage.py stop_webserver")
        else:
            print(f"  ❌ Web server failed to start")
    except Exception as e:
        print(f"  ❌ Startup failed: {e}")


def stop_webserver():
    """停止 Web 服务器"""
    print("🛑 Stopping the web server...")

    if not Path(WEBSERVER_PID_FILE).exists():
        print("  ℹ️ Web server is not running")
        return

    try:
        with open(WEBSERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        _terminate_webserver_process(pid, require_expected=True)
        if Path(WEBSERVER_PID_FILE).exists():
            os.remove(WEBSERVER_PID_FILE)
    except Exception as e:
        print(f"  ❌ Could not stop the server: {e}")
        # 尝试清理 PID 文件
        try:
            os.remove(WEBSERVER_PID_FILE)
        except Exception:
            pass


def webserver_status():
    """查看 Web 服务器状态"""
    print("🌐 Web server status:")

    if not Path(WEBSERVER_PID_FILE).exists():
        print("  ⭕ Not running")
        print(f"  💡 Start the server: python manage.py start_webserver")
        return

    try:
        with open(WEBSERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # 使用增强的进程检查
        if _is_webserver_running(pid):
            print(f"  ✅ Running (PID: {pid})")
            print(f"  📁 Serving: {WEBSERVER_DIR}")
            print(f"  🌐 URL: http://localhost:{WEBSERVER_PORT}")
            print(f"  📄 Home page: http://localhost:{WEBSERVER_PORT}/index.html")
            print("  💡 Stop the server: python manage.py stop_webserver")
        else:
            print(f"  ⭕ Not running (PID file exists but process is unavailable)")
            _cleanup_stale_pid()
            print("  💡 Start the server: python manage.py start_webserver")
    except Exception as e:
        print(f"  ❌ Status check failed: {e}")


def show_help():
    """显示帮助信息"""
    help_text = """
🐳 TrendRadar Container Manager

📋 Commands:
  run              - Run the crawler once
  status           - Show container status
  config           - Show the current configuration
  files            - List output files
  logs             - Follow live logs
  restart          - Show restart instructions
  start_webserver  - Serve the output directory over HTTP
  stop_webserver   - Stop the web server
  webserver_status - Show web server status
  help             - Show this help

📖 Examples:
  # Inside the container
  python manage.py run
  python manage.py status
  python manage.py logs
  python manage.py start_webserver

  # On the host
  docker exec -it trendradar python manage.py run
  docker exec -it trendradar python manage.py status
  docker exec -it trendradar python manage.py start_webserver
  docker logs trendradar

💡 Common tasks:
  1. Check runtime status: status
     - Verify that supercronic is PID 1
     - Check the configuration and required files
     - Inspect the cron schedule

  2. Run a manual test: run
     - Fetch news immediately
     - Check that the application runs correctly

  3. View logs: logs
     - Follow current activity
     - Alternatively: docker logs trendradar

  4. Restart: restart
     - supercronic is PID 1, so restart the entire container
     - Run: docker restart trendradar

  5. Manage the web server:
     - Start: start_webserver
     - Stop: stop_webserver
     - Status: webserver_status
     - Open: http://localhost:8080
"""
    print(help_text)


def main():
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1]
    commands = {
        "run": manual_run,
        "status": show_status,
        "config": show_config,
        "files": show_files,
        "logs": show_logs,
        "restart": restart_supercronic,
        "start_webserver": start_webserver,
        "stop_webserver": stop_webserver,
        "webserver_status": webserver_status,
        "help": show_help,
    }

    if command in commands:
        try:
            commands[command]()
        except KeyboardInterrupt:
            print("\n👋 Operation cancelled")
        except Exception as e:
            print(f"❌ Execution error: {e}")
    else:
        print(f"❌ Unknown command: {command}")
        print("Run 'python manage.py help' to see available commands")


if __name__ == "__main__":
    main()
