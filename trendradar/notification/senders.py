# coding=utf-8
"""
消息发送器模块

将报告数据发送到各种通知渠道：
- 飞书 (Feishu/Lark)
- 钉钉 (DingTalk)
- 企业微信 (WeCom/WeWork)
- Telegram
- 邮件 (Email)
- ntfy
- Bark
- Slack

每个发送函数都支持分批发送，并通过参数化配置实现与 CONFIG 的解耦。
"""

import smtplib
import time
import json
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import requests

from trendradar.report.helpers import english_report_type

from .batch import add_batch_headers, get_max_batch_header_size
from .formatters import convert_markdown_to_mrkdwn, strip_markdown


def _extract_ai_stats(ai_analysis) -> Optional[Dict]:
    """从 AI 分析结果中提取统计数据"""
    if not ai_analysis or not getattr(ai_analysis, "success", False):
        return None
    return {
        "total_news": getattr(ai_analysis, "total_news", 0),
        "analyzed_news": getattr(ai_analysis, "analyzed_news", 0),
        "max_news_limit": getattr(ai_analysis, "max_news_limit", 0),
        "hotlist_count": getattr(ai_analysis, "hotlist_count", 0),
        "rss_count": getattr(ai_analysis, "rss_count", 0),
        "hotlist_analyzed": getattr(ai_analysis, "hotlist_analyzed", 0),
        "rss_analyzed": getattr(ai_analysis, "rss_analyzed", 0),
        "standalone_analyzed": getattr(ai_analysis, "standalone_analyzed", 0),
        "ai_mode": getattr(ai_analysis, "ai_mode", ""),
        "include_rss": getattr(ai_analysis, "include_rss", True),
        "include_standalone": getattr(ai_analysis, "include_standalone", False),
    }


def _render_ai_analysis(ai_analysis: Any, channel: str) -> str:
    """渲染 AI 分析内容为指定渠道格式"""
    if not ai_analysis:
        return ""

    try:
        from trendradar.ai.formatter import get_ai_analysis_renderer
        renderer = get_ai_analysis_renderer(channel)
        return renderer(ai_analysis)
    except ImportError:
        return ""


# === SMTP 邮件配置 ===
SMTP_CONFIGS = {
    # Gmail（使用 STARTTLS）
    "gmail.com": {"server": "smtp.gmail.com", "port": 587, "encryption": "TLS"},
    # QQ邮箱（使用 SSL，更稳定）
    "qq.com": {"server": "smtp.qq.com", "port": 465, "encryption": "SSL"},
    # Outlook（使用 STARTTLS）
    "outlook.com": {"server": "smtp-mail.outlook.com", "port": 587, "encryption": "TLS"},
    "hotmail.com": {"server": "smtp-mail.outlook.com", "port": 587, "encryption": "TLS"},
    "live.com": {"server": "smtp-mail.outlook.com", "port": 587, "encryption": "TLS"},
    # 网易邮箱（使用 SSL，更稳定）
    "163.com": {"server": "smtp.163.com", "port": 465, "encryption": "SSL"},
    "126.com": {"server": "smtp.126.com", "port": 465, "encryption": "SSL"},
    # 新浪邮箱（使用 SSL）
    "sina.com": {"server": "smtp.sina.com", "port": 465, "encryption": "SSL"},
    # 搜狐邮箱（使用 SSL）
    "sohu.com": {"server": "smtp.sohu.com", "port": 465, "encryption": "SSL"},
    # 天翼邮箱（使用 SSL）
    "189.cn": {"server": "smtp.189.cn", "port": 465, "encryption": "SSL"},
    # 阿里云邮箱（使用 TLS）
    "aliyun.com": {"server": "smtp.aliyun.com", "port": 465, "encryption": "TLS"},
    # Yandex邮箱（使用 TLS）
    "yandex.com": {"server": "smtp.yandex.com", "port": 465, "encryption": "TLS"},
    # iCloud邮箱（使用 SSL）
    "icloud.com": {"server": "smtp.mail.me.com", "port": 587, "encryption": "SSL"},
}


def send_to_feishu(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 29000,
    batch_interval: float = 1.0,
    split_content_func: Callable = None,
    get_time_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到飞书（支持分批发送，支持热榜+RSS合并+独立展示区）

    Args:
        webhook_url: 飞书 Webhook URL
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        get_time_func: 获取当前时间的函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Feishu ({account_label})" if account_label else "Feishu"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "feishu") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 预留批次头部空间，避免添加头部后超限
    header_reserve = get_max_batch_header_size("feishu")
    batches = split_content_func(
        report_data,
        "feishu",
        update_info,
        max_bytes=batch_size - header_reserve,
        mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "feishu", batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        # 根据 webhook 域名选择 payload 格式
        # www.feishu.cn 使用纯文本格式，其他域名（open.feishu.cn/open.larksuite.com）使用卡片 2.0
        if "www.feishu.cn" in webhook_url:
            payload = {
                "msg_type": "text",
                "content": {
                    "text": batch_content,
                },
            }
        else:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "schema": "2.0",
                    "body": {
                        "elements": [
                            {"tag": "markdown", "content": batch_content}
                        ]
                    },
                },
            }

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                # 检查飞书的响应状态
                if result.get("StatusCode") == 0 or result.get("code") == 0:
                    print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(batch_interval)
                else:
                    error_msg = result.get("msg") or result.get("StatusMessage", "Unknown error")
                    print(
                        f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], error: {error_msg}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], status: {response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True


def send_to_dingtalk(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 20000,
    batch_interval: float = 1.0,
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到钉钉（支持分批发送，支持热榜+RSS合并+独立展示区）

    Args:
        webhook_url: 钉钉 Webhook URL
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"DingTalk ({account_label})" if account_label else "DingTalk"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "dingtalk") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 预留批次头部空间，避免添加头部后超限
    header_reserve = get_max_batch_header_size("dingtalk")
    batches = split_content_func(
        report_data,
        "dingtalk",
        update_info,
        max_bytes=batch_size - header_reserve,
        mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "dingtalk", batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"TrendRadar Trend Analysis Report - {report_type}",
                "text": batch_content,
            },
        }

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(batch_interval)
                else:
                    print(
                        f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], error: {result.get('errmsg')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], status: {response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True


def send_to_wework(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 4000,
    batch_interval: float = 1.0,
    msg_type: str = "markdown",
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到企业微信（支持分批发送，支持 markdown 和 text 两种格式，支持热榜+RSS合并+独立展示区）

    Args:
        webhook_url: 企业微信 Webhook URL
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        msg_type: 消息类型 (markdown/text)
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"WeCom ({account_label})" if account_label else "WeCom"

    # 获取消息类型配置（markdown 或 text）
    is_text_mode = msg_type.lower() == "text"

    if is_text_mode:
        print(f"{log_prefix}: Using text format (personal WeChat mode)[{report_type}]")
    else:
        print(f"{log_prefix}: Using Markdown format (group bot mode)[{report_type}]")

    # text 模式使用 wework_text，markdown 模式使用 wework
    header_format_type = "wework_text" if is_text_mode else "wework"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "wework") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容，预留批次头部空间
    header_reserve = get_max_batch_header_size(header_format_type)
    batches = split_content_func(
        report_data, "wework", update_info, max_bytes=batch_size - header_reserve, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, header_format_type, batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        # 根据消息类型构建 payload
        if is_text_mode:
            # text 格式：去除 markdown 语法
            plain_content = strip_markdown(batch_content)
            payload = {"msgtype": "text", "text": {"content": plain_content}}
            content_size = len(plain_content.encode("utf-8"))
        else:
            # markdown 格式：保持原样
            payload = {"msgtype": "markdown", "markdown": {"content": batch_content}}
            content_size = len(batch_content.encode("utf-8"))

        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(batch_interval)
                else:
                    print(
                        f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], error: {result.get('errmsg')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], status: {response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True


def send_to_telegram(
    bot_token: str,
    chat_id: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 4000,
    batch_interval: float = 1.0,
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到 Telegram（支持分批发送，支持热榜+RSS合并+独立展示区）

    Args:
        bot_token: Telegram Bot Token
        chat_id: Telegram Chat ID
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    headers = {"Content-Type": "application/json"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Telegram ({account_label})" if account_label else "Telegram"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "telegram") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容，预留批次头部空间
    header_reserve = get_max_batch_header_size("telegram")
    batches = split_content_func(
        report_data, "telegram", update_info, max_bytes=batch_size - header_reserve, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "telegram", batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        payload = {
            "chat_id": chat_id,
            "text": batch_content,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(batch_interval)
                else:
                    print(
                        f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], error: {result.get('description')}"
                    )
                    return False
            else:
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], status: {response.status_code}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True


def send_to_email(
    from_email: str,
    password: str,
    to_email: str,
    report_type: str,
    html_file_path: str,
    custom_smtp_server: Optional[str] = None,
    custom_smtp_port: Optional[int] = None,
    *,
    get_time_func: Callable = None,
) -> bool:
    """
    发送邮件通知

    Args:
        from_email: 发件人邮箱
        password: 邮箱密码/授权码
        to_email: 收件人邮箱（多个用逗号分隔）
        report_type: 报告类型
        html_file_path: HTML 报告文件路径
        custom_smtp_server: 自定义 SMTP 服务器（可选）
        custom_smtp_port: 自定义 SMTP 端口（可选）
        get_time_func: 获取当前时间的函数

    Returns:
        bool: 发送是否成功

    Note:
        AI 分析内容已在 HTML 生成时嵌入，无需再追加
    """
    report_type = english_report_type(report_type)
    try:
        if not html_file_path or not Path(html_file_path).exists():
            print(f"Error: HTML file is missing or was not provided: {html_file_path}")
            return False

        print(f"Using HTML file: {html_file_path}")
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        domain = from_email.split("@")[-1].lower()

        if custom_smtp_server and custom_smtp_port:
            # 使用自定义 SMTP 配置
            smtp_server = custom_smtp_server
            smtp_port = int(custom_smtp_port)
            # 根据端口判断加密方式：465=SSL, 587=TLS
            if smtp_port == 465:
                use_tls = False  # SSL 模式（SMTP_SSL）
            elif smtp_port == 587:
                use_tls = True  # TLS 模式（STARTTLS）
            else:
                # 其他端口优先尝试 TLS（更安全，更广泛支持）
                use_tls = True
        elif domain in SMTP_CONFIGS:
            # 使用预设配置
            config = SMTP_CONFIGS[domain]
            smtp_server = config["server"]
            smtp_port = config["port"]
            use_tls = config["encryption"] == "TLS"
        else:
            print(f"Unknown email provider: {domain}; using generic SMTP settings")
            smtp_server = f"smtp.{domain}"
            smtp_port = 587
            use_tls = True

        msg = MIMEMultipart("alternative")

        # 严格按照 RFC 标准设置 From header
        sender_name = "TrendRadar"
        msg["From"] = formataddr((sender_name, from_email))

        # 设置收件人
        recipients = [addr.strip() for addr in to_email.split(",")]
        if len(recipients) == 1:
            msg["To"] = recipients[0]
        else:
            msg["To"] = ", ".join(recipients)

        # 设置邮件主题
        now = get_time_func() if get_time_func else datetime.now()
        subject = f"TrendRadar Trend Analysis Report - {report_type} - {now.strftime('%Y-%m-%d %H:%M')}"
        msg["Subject"] = Header(subject, "utf-8")

        # 设置其他标准 header
        msg["MIME-Version"] = "1.0"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        # 添加纯文本部分（作为备选）
        text_content = f"""
TrendRadar Trend Analysis Report
========================
Report type: {report_type}
Generated at: {now.strftime('%Y-%m-%d %H:%M:%S')}

Open this message in an HTML-capable email client to view the complete report.
        """
        text_part = MIMEText(text_content, "plain", "utf-8")
        msg.attach(text_part)

        html_part = MIMEText(html_content, "html", "utf-8")
        msg.attach(html_part)

        print(f"Sending email to {to_email}...")
        print(f"SMTP server: {smtp_server}:{smtp_port}")
        print(f"From: {from_email}")

        try:
            if use_tls:
                # TLS 模式
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                server.set_debuglevel(0)  # 设为1可以查看详细调试信息
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                # SSL 模式
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
                server.set_debuglevel(0)
                server.ehlo()

            # 登录
            server.login(from_email, password)

            # 发送邮件
            server.send_message(msg)
            server.quit()

            print(f"Email sent [{report_type}] -> {to_email}")
            return True

        except smtplib.SMTPServerDisconnected:
            print("Email failed: the server disconnected unexpectedly. Check the connection or try again later.")
            return False

    except smtplib.SMTPAuthenticationError as e:
        print("Email failed: authentication error. Check the email address and password or app password.")
        print(f"Error details: {str(e)}")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        print(f"Email failed: recipient address rejected {e}")
        return False
    except smtplib.SMTPSenderRefused as e:
        print(f"Email failed: sender address rejected {e}")
        return False
    except smtplib.SMTPDataError as e:
        print(f"Email failed: message data error {e}")
        return False
    except smtplib.SMTPConnectError as e:
        print(f"Email failed: could not connect to SMTP server {smtp_server}:{smtp_port}")
        print(f"Error details: {str(e)}")
        return False
    except Exception as e:
        print(f"Email failed [{report_type}]: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_to_ntfy(
    server_url: str,
    topic: str,
    token: Optional[str],
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 3800,
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到 ntfy（支持分批发送，严格遵守4KB限制，支持热榜+RSS合并+独立展示区）

    Args:
        server_url: ntfy 服务器 URL
        topic: ntfy 主题
        token: ntfy 访问令牌（可选）
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    # 日志前缀
    log_prefix = f"ntfy ({account_label})" if account_label else "ntfy"

    # 避免 HTTP header 编码问题
    report_type_en_map = {
        "Daily Summary": "Daily Summary",
        "Current Ranking": "Current Ranking",
        "Incremental Update": "Incremental Update",
        "Notification Test": "Notification Test",
    }
    report_type_en = report_type_en_map.get(report_type, "News Report")

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Markdown": "yes",
        "Title": report_type_en,
        "Priority": "default",
        "Tags": "news",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 构建完整URL，确保格式正确
    base_url = server_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    url = f"{base_url}/{topic}"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "ntfy") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容，预留批次头部空间
    header_reserve = get_max_batch_header_size("ntfy")
    batches = split_content_func(
        report_data, "ntfy", update_info, max_bytes=batch_size - header_reserve, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "ntfy", batch_size)

    total_batches = len(batches)
    print(f"{log_prefix}: Sending message in {total_batches} batches [{report_type}]")

    # 反转批次顺序，使得在ntfy客户端显示时顺序正确
    # ntfy显示最新消息在上面，所以我们从最后一批开始推送
    reversed_batches = list(reversed(batches))

    print(f"{log_prefix}: Sending batches in reverse order so the client displays them correctly")

    # 逐批发送（反向顺序）
    success_count = 0
    for idx, batch_content in enumerate(reversed_batches, 1):
        # 计算正确的批次编号（用户视角的编号）
        actual_batch_num = total_batches - idx + 1

        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {actual_batch_num}/{total_batches} (delivery order: {idx}/{total_batches}, size: {content_size} bytes) [{report_type}]"
        )

        # 检查消息大小，确保不超过4KB
        if content_size > 4096:
            print(f"Warning: {log_prefix}: Batch {actual_batch_num} is too large ({content_size} bytes) and may be rejected")

        # 更新 headers 的批次标识
        current_headers = headers.copy()
        if total_batches > 1:
            current_headers["Title"] = f"{report_type_en} ({actual_batch_num}/{total_batches})"

        try:
            response = requests.post(
                url,
                headers=current_headers,
                data=batch_content.encode("utf-8"),
                proxies=proxies,
                timeout=30,
            )

            if response.status_code == 200:
                print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} sent [{report_type}]")
                success_count += 1
                if idx < total_batches:
                    # 公共服务器建议 2-3 秒，自托管可以更短
                    interval = 2 if "ntfy.sh" in server_url else 1
                    time.sleep(interval)
            elif response.status_code == 429:
                print(
                    f"{log_prefix}: Batch {actual_batch_num}/{total_batches} rate limited [{report_type}]; waiting before retrying"
                )
                time.sleep(10)  # 等待10秒后重试
                # 重试一次
                retry_response = requests.post(
                    url,
                    headers=current_headers,
                    data=batch_content.encode("utf-8"),
                    proxies=proxies,
                    timeout=30,
                )
                if retry_response.status_code == 200:
                    print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} retry succeeded [{report_type}]")
                    success_count += 1
                else:
                    print(
                        f"{log_prefix}: Batch {actual_batch_num}/{total_batches} retry failed, status: {retry_response.status_code}"
                    )
            elif response.status_code == 413:
                print(
                    f"{log_prefix}: Batch {actual_batch_num}/{total_batches} rejected because it is too large [{report_type}], message size: {content_size} bytes"
                )
            else:
                print(
                    f"{log_prefix}: Batch {actual_batch_num}/{total_batches} failed [{report_type}], status: {response.status_code}"
                )
                try:
                    print(f"Error details: {response.text}")
                except:
                    pass

        except requests.exceptions.ConnectTimeout:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} connection timed out [{report_type}]")
        except requests.exceptions.ReadTimeout:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} read timed out [{report_type}]")
        except requests.exceptions.ConnectionError as e:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} connection error [{report_type}]: {e}")
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {actual_batch_num}/{total_batches} [{report_type}]: {e}")

    # 判断整体发送是否成功
    if success_count == total_batches:
        print(f"{log_prefix}: All {total_batches} batches sent [{report_type}]")
    elif success_count > 0:
        print(f"{log_prefix}: Partially delivered: {success_count}/{total_batches} batches [{report_type}]")
    else:
        print(f"{log_prefix}: Delivery failed for every batch [{report_type}]")
        return False

    return True


def send_to_bark(
    bark_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 3600,
    batch_interval: float = 1.0,
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到 Bark（支持分批发送，使用 markdown 格式，支持热榜+RSS合并+独立展示区）

    Args:
        bark_url: Bark URL（包含 device_key）
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    # 日志前缀
    log_prefix = f"Bark ({account_label})" if account_label else "Bark"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 解析 Bark URL，提取 device_key 和 API 端点
    # Bark URL 格式: https://api.day.app/device_key 或 https://bark.day.app/device_key
    parsed_url = urlparse(bark_url)
    device_key = parsed_url.path.strip('/').split('/')[0] if parsed_url.path else None

    if not device_key:
        print(f"{log_prefix} Invalid URL; unable to extract device_key: {bark_url}")
        return False

    # 构建正确的 API 端点
    api_endpoint = f"{parsed_url.scheme}://{parsed_url.netloc}/push"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "bark") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容，预留批次头部空间
    header_reserve = get_max_batch_header_size("bark")
    batches = split_content_func(
        report_data, "bark", update_info, max_bytes=batch_size - header_reserve, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "bark", batch_size)

    total_batches = len(batches)
    print(f"{log_prefix}: Sending message in {total_batches} batches [{report_type}]")

    # 反转批次顺序，使得在Bark客户端显示时顺序正确
    # Bark显示最新消息在上面，所以我们从最后一批开始推送
    reversed_batches = list(reversed(batches))

    print(f"{log_prefix}: Sending batches in reverse order so the client displays them correctly")

    # 逐批发送（反向顺序）
    success_count = 0
    for idx, batch_content in enumerate(reversed_batches, 1):
        # 计算正确的批次编号（用户视角的编号）
        actual_batch_num = total_batches - idx + 1

        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {actual_batch_num}/{total_batches} (delivery order: {idx}/{total_batches}, size: {content_size} bytes) [{report_type}]"
        )

        # 检查消息大小（Bark使用APNs，限制4KB）
        if content_size > 4096:
            print(
                f"Warning: {log_prefix}: Batch {actual_batch_num}/{total_batches} is too large ({content_size} bytes) and may be rejected"
            )

        # 构建JSON payload
        payload = {
            "title": report_type,
            "markdown": batch_content,
            "device_key": device_key,
            "sound": "default",
            "group": "TrendRadar",
            "action": "none",  # 点击推送跳到 APP 不弹出弹框,方便阅读
        }

        try:
            response = requests.post(
                api_endpoint,
                json=payload,
                proxies=proxies,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 200:
                    print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} sent [{report_type}]")
                    success_count += 1
                    # 批次间间隔
                    if idx < total_batches:
                        time.sleep(batch_interval)
                else:
                    print(
                        f"{log_prefix}: Batch {actual_batch_num}/{total_batches} failed [{report_type}], error: {result.get('message', 'Unknown error')}"
                    )
            else:
                print(
                    f"{log_prefix}: Batch {actual_batch_num}/{total_batches} failed [{report_type}], status: {response.status_code}"
                )
                try:
                    print(f"Error details: {response.text}")
                except:
                    pass

        except requests.exceptions.ConnectTimeout:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} connection timed out [{report_type}]")
        except requests.exceptions.ReadTimeout:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} read timed out [{report_type}]")
        except requests.exceptions.ConnectionError as e:
            print(f"{log_prefix}: Batch {actual_batch_num}/{total_batches} connection error [{report_type}]: {e}")
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {actual_batch_num}/{total_batches} [{report_type}]: {e}")

    # 判断整体发送是否成功
    if success_count == total_batches:
        print(f"{log_prefix}: All {total_batches} batches sent [{report_type}]")
    elif success_count > 0:
        print(f"{log_prefix}: Partially delivered: {success_count}/{total_batches} batches [{report_type}]")
    else:
        print(f"{log_prefix}: Delivery failed for every batch [{report_type}]")
        return False

    return True


def send_to_slack(
    webhook_url: str,
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 4000,
    batch_interval: float = 1.0,
    split_content_func: Callable = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到 Slack（支持分批发送，使用 mrkdwn 格式，支持热榜+RSS合并+独立展示区）

    Args:
        webhook_url: Slack Webhook URL
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Slack ({account_label})" if account_label else "Slack"

    # 渲染 AI 分析内容并提取统计数据
    ai_content = _render_ai_analysis(ai_analysis, "slack") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容，预留批次头部空间
    header_reserve = get_max_batch_header_size("slack")
    batches = split_content_func(
        report_data, "slack", update_info, max_bytes=batch_size - header_reserve, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部（已预留空间，不会超限）
    batches = add_batch_headers(batches, "slack", batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        # 转换 Markdown 到 mrkdwn 格式
        mrkdwn_content = convert_markdown_to_mrkdwn(batch_content)

        content_size = len(mrkdwn_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        # 构建 Slack payload（使用简单的 text 字段，支持 mrkdwn）
        payload = {"text": mrkdwn_content}

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )

            # Slack Incoming Webhooks 成功时返回 "ok" 文本
            if response.status_code == 200 and response.text == "ok":
                print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                # 批次间间隔
                if i < len(batches):
                    time.sleep(batch_interval)
            else:
                error_msg = response.text if response.text else f"Status: {response.status_code}"
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], error: {error_msg}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True


def send_to_generic_webhook(
    webhook_url: str,
    payload_template: Optional[str],
    report_data: Dict,
    report_type: str,
    update_info: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    mode: str = "daily",
    account_label: str = "",
    *,
    batch_size: int = 4000,
    batch_interval: float = 1.0,
    split_content_func: Optional[Callable] = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    ai_analysis: Any = None,
    display_regions: Optional[Dict] = None,
    standalone_data: Optional[Dict] = None,
) -> bool:
    """
    发送到通用 Webhook（支持分批发送，支持自定义 JSON 模板，支持热榜+RSS合并+独立展示区）

    Args:
        webhook_url: Webhook URL
        payload_template: JSON 模板字符串，支持 {title} 和 {content} 占位符
        report_data: 报告数据
        report_type: 报告类型
        update_info: 更新信息（可选）
        proxy_url: 代理 URL（可选）
        mode: 报告模式 (daily/current)
        account_label: 账号标签（多账号时显示）
        batch_size: 批次大小（字节）
        batch_interval: 批次发送间隔（秒）
        split_content_func: 内容分批函数
        rss_items: RSS 统计条目列表（可选，用于合并推送）
        rss_new_items: RSS 新增条目列表（可选，用于新增区块）

    Returns:
        bool: 发送是否成功
    """
    report_type = english_report_type(report_type)
    if split_content_func is None:
        raise ValueError("split_content_func is required")

    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 日志前缀
    log_prefix = f"Generic webhook ({account_label})" if account_label else "Generic webhook"

    # 渲染 AI 分析内容并提取统计数据（通用 Webhook 使用 markdown 格式）
    ai_content = _render_ai_analysis(ai_analysis, "wework") if ai_analysis else None
    ai_stats = _extract_ai_stats(ai_analysis)

    # 获取分批内容
    # 使用 'wework' 作为 format_type 以获取 markdown 格式的通用输出
    # 预留一定空间给模板外壳
    template_overhead = 200
    batches = split_content_func(
        report_data, "wework", update_info, max_bytes=batch_size - template_overhead, mode=mode,
        rss_items=rss_items,
        rss_new_items=rss_new_items,
        ai_content=ai_content,
        standalone_data=standalone_data,
        ai_stats=ai_stats,
        report_type=report_type,
    )

    # 统一添加批次头部
    batches = add_batch_headers(batches, "wework", batch_size)

    print(f"{log_prefix}: Sending message in {len(batches)} batches [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        content_size = len(batch_content.encode("utf-8"))
        print(
            f"Sending {log_prefix} batch {i}/{len(batches)} ({content_size} bytes) [{report_type}]"
        )

        try:
            # 构建 payload
            if payload_template:
                # 简单的字符串替换
                # 注意：content 可能包含 JSON 特殊字符，需要先转义
                json_content = json.dumps(batch_content)[1:-1] # 去掉首尾引号
                json_title = json.dumps(report_type)[1:-1]
                
                payload_str = payload_template.replace("{content}", json_content).replace("{title}", json_title)
                
                # 尝试解析为 JSON 对象以验证有效性
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError as e:
                    print(f"{log_prefix} Failed to parse JSON template: {e}")
                    # 回退到默认格式
                    payload = {"title": report_type, "content": batch_content}
            else:
                # 默认格式
                payload = {"title": report_type, "content": batch_content}

            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            
            if response.status_code >= 200 and response.status_code < 300:
                print(f"{log_prefix}: Batch {i}/{len(batches)} sent [{report_type}]")
                if i < len(batches):
                    time.sleep(batch_interval)
            else:
                print(
                    f"{log_prefix}: Batch {i}/{len(batches)} failed [{report_type}], status: {response.status_code}, response: {response.text}"
                )
                return False
        except Exception as e:
            print(f"{log_prefix}: Error sending batch {i}/{len(batches)} [{report_type}]: {e}")
            return False

    print(f"{log_prefix}: All {len(batches)} batches sent [{report_type}]")

    return True
