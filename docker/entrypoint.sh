#!/bin/bash
set -e

# 检查配置文件
if [ ! -f "/app/config/config.yaml" ] || [ ! -f "/app/config/frequency_words.txt" ]; then
    echo "❌ Configuration file missing"
    exit 1
fi

case "${RUN_MODE:-cron}" in
"once")
    echo "🔄 Running once"
    exec python -m trendradar
    ;;
"cron")
    # 校验 CRON_SCHEDULE 格式（仅允许 cron 表达式合法字符）
    CRON_EXPR="${CRON_SCHEDULE:-*/30 * * * *}"
    if ! echo "$CRON_EXPR" | grep -qE '^[0-9*/,[:space:]-]+$'; then
        echo "❌ Invalid CRON_SCHEDULE format: $CRON_EXPR"
        exit 1
    fi

    # 生成 crontab
    echo "$CRON_EXPR cd /app && python -m trendradar" > /tmp/crontab
    
    echo "📅 Generated crontab:"
    cat /tmp/crontab

    if ! /usr/local/bin/supercronic -test /tmp/crontab; then
        echo "❌ Crontab validation failed"
        exit 1
    fi

    # Running once immediately（如果配置了）
    if [ "${IMMEDIATE_RUN:-false}" = "true" ]; then
        echo "▶️ Running once immediately"
        python -m trendradar
    fi

    # 启动 Web server
    echo "🌐 Starting web server..."
    python manage.py start_webserver

    echo "⏰ Starting supercronic: $CRON_EXPR"
    echo "🎯 supercronic will run as PID 1"

    exec /usr/local/bin/supercronic -passthrough-logs /tmp/crontab
    ;;
*)
    exec "$@"
    ;;
esac
