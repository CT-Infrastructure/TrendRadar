# coding=utf-8
"""
通知推送工具

支持向已配置的通知渠道发送消息，自动检测 config.yaml 和 .env 中的渠道配置。
接受 markdown 格式内容，内部按各渠道要求自动转换格式后发送。
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

from trendradar.core.loader import _load_webhook_config, _load_notification_config
from trendradar.notification.batch import (
    truncate_to_bytes,
    get_batch_header,
    get_max_batch_header_size,
    add_batch_headers,
)
from trendradar.notification.formatters import strip_markdown
from trendradar.notification.senders import SMTP_CONFIGS

from ..utils.errors import MCPError, InvalidParameterError


# ==================== 渠道启用判断规则 ====================

# 每个渠道需要哪些配置项都非空才算"已配置"
# 注意：NTFY_SERVER_URL 在 loader 中有默认值 "https://ntfy.sh"，不作为判断依据
_CHANNEL_REQUIREMENTS = {
    "feishu": ["FEISHU_WEBHOOK_URL"],
    "dingtalk": ["DINGTALK_WEBHOOK_URL"],
    "wework": ["WEWORK_WEBHOOK_URL"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "email": ["EMAIL_FROM", "EMAIL_PASSWORD", "EMAIL_TO"],
    "ntfy": ["NTFY_TOPIC"],
    "bark": ["BARK_URL"],
    "slack": ["SLACK_WEBHOOK_URL"],
    "generic_webhook": ["GENERIC_WEBHOOK_URL"],
}

# 渠道显示名称
_CHANNEL_NAMES = {
    "feishu": "Feishu",
    "dingtalk": "DingTalk",
    "wework": "WeCom",
    "telegram": "Telegram",
    "email": "Email",
    "ntfy": "ntfy",
    "bark": "Bark",
    "slack": "Slack",
    "generic_webhook": "Generic Webhook",
}


# ==================== 批次处理配置 ====================

# 各渠道最大批次字节数的默认值
# 运行时从 config.yaml → advanced.batch_size 读取覆盖
_CHANNEL_BATCH_SIZES_DEFAULT = {
    "feishu": 30000,    # config.yaml: advanced.batch_size.feishu
    "dingtalk": 20000,  # config.yaml: advanced.batch_size.dingtalk
    "wework": 4000,     # config.yaml: advanced.batch_size.default
    "telegram": 4000,   # config.yaml: advanced.batch_size.default
    "email": 0,         # 邮件无字节限制，不分批
    "ntfy": 3800,       # 严格 4KB 限制（ntfy 代码默认值）
    "bark": 4000,       # config.yaml: advanced.batch_size.bark
    "slack": 4000,      # config.yaml: advanced.batch_size.slack
    "generic_webhook": 4000,
}

# 显示最新消息在前的渠道，批次需反序发送
_REVERSE_BATCH_CHANNELS = {"ntfy", "bark"}

# 批次发送间隔默认值（秒），运行时从 config.yaml → advanced.batch_send_interval 读取
_BATCH_INTERVAL_DEFAULT = 3.0


# ==================== 批次处理 ====================
# truncate_to_bytes, get_batch_header, get_max_batch_header_size,
# add_batch_headers 复用自 trendradar.notification.batch


def _split_text_into_batches(text: str, max_bytes: int) -> List[str]:
    """将文本按字节限制分批，优先在段落边界（双换行）切割

    分割策略（参考 trendradar splitter.py 的原子性保证）：
    1. 优先按段落（双换行 \\n\\n）拆分
    2. 段落仍超限时，按单行（\\n）拆分
    3. 单行仍超限时，用 _truncate_to_bytes 安全截断

    Args:
        text: 已转换为目标渠道格式的文本
        max_bytes: 单批最大字节数（已扣除批次头部预留）

    Returns:
        分批后的文本列表
    """
    if max_bytes <= 0 or len(text.encode("utf-8")) <= max_bytes:
        return [text]

    # 按段落分割
    paragraphs = text.split("\n\n")
    batches = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            # 当前段落放不下，先保存已有内容
            if current:
                batches.append(current)
                current = ""

            # 检查单个段落是否超限
            if len(para.encode("utf-8")) <= max_bytes:
                current = para
            else:
                # 段落本身超限，按行拆分
                lines = para.split("\n")
                for line in lines:
                    candidate = f"{current}\n{line}" if current else line
                    if len(candidate.encode("utf-8")) <= max_bytes:
                        current = candidate
                    else:
                        if current:
                            batches.append(current)
                            current = ""
                        # 单行超限，循环截断直到处理完
                        if len(line.encode("utf-8")) > max_bytes:
                            remaining = line
                            while remaining:
                                chunk = truncate_to_bytes(remaining, max_bytes)
                                if not chunk:
                                    break
                                batches.append(chunk)
                                # 移除已截断的部分
                                remaining = remaining[len(chunk):]
                        else:
                            current = line

    if current:
        batches.append(current)

    return batches if batches else [text]


def _format_for_channel(message: str, channel_id: str) -> str:
    """将通用 Markdown 适配并转换为目标渠道格式

    统一入口：先适配（剥离不支持的语法），再转换（Markdown→HTML/mrkdwn 等）。
    返回的文本可以直接用于字节分割和发送。

    Args:
        message: 原始 Markdown 格式文本
        channel_id: 目标渠道 ID

    Returns:
        目标渠道格式的文本
    """
    if channel_id == "feishu":
        return _adapt_markdown_for_feishu(message)
    elif channel_id == "dingtalk":
        return _adapt_markdown_for_dingtalk(message)
    elif channel_id == "wework":
        return _adapt_markdown_for_wework(message)
    elif channel_id == "telegram":
        return _markdown_to_telegram_html(message)
    elif channel_id == "ntfy":
        return _adapt_markdown_for_ntfy(message)
    elif channel_id == "bark":
        return _adapt_markdown_for_bark(message)
    elif channel_id == "slack":
        return _convert_markdown_to_slack(message)
    else:
        # email, generic_webhook: 保持原始 Markdown
        return message


def _prepare_batches(message: str, channel_id: str, batch_sizes: Dict = None) -> List[str]:
    """完整的分批管线：格式适配 → 字节分割 → 添加批次头部

    Args:
        message: 原始 Markdown 格式文本
        channel_id: 目标渠道 ID
        batch_sizes: 各渠道批次大小字典（来自 config.yaml），None 使用默认值

    Returns:
        准备好的批次列表（已添加头部，已处理反序）
    """
    sizes = batch_sizes or _CHANNEL_BATCH_SIZES_DEFAULT
    max_bytes = sizes.get(channel_id, sizes.get("default", 4000))
    if max_bytes <= 0:
        # 无字节限制（如 email），返回原始文本
        return [message]

    formatted = _format_for_channel(message, channel_id)

    # 预留批次头部空间后分割
    header_reserve = get_max_batch_header_size(channel_id)
    batches = _split_text_into_batches(formatted, max_bytes - header_reserve)

    # 添加批次头部（单批时不添加）
    batches = add_batch_headers(batches, channel_id, max_bytes)

    # ntfy/Bark 反序发送（客户端显示最新在前）
    if channel_id in _REVERSE_BATCH_CHANNELS and len(batches) > 1:
        batches = list(reversed(batches))

    return batches

CHANNEL_FORMAT_GUIDES = {'feishu': {'name': 'Feishu',
            'format': 'Markdown (card messages)',
            'max_length': 'About 29000 bytes',
            'supported': ['**bold**',
                          '[link text](URL)',
                          "<font color='red/green/grey/orange/blue'>colored text</font>",
                          '--- (horizontal rule)',
                          'Paragraphs separated by line breaks'],
            'unsupported': ['# headings (not rendered as headings)',
                            '> blockquotes',
                            'Tables / embedded images'],
            'prompt': 'Feishu card Markdown formatting:\n'
                      '1. Use **bold** for subheadings and important words.\n'
                      "2. Use <font color='red'>red</font> for urgent or important information.\n"
                      "3. Use <font color='grey'>gray</font> for supporting details, times, and sources.\n"
                      "4. Use <font color='orange'>orange</font> for warnings.\n"
                      "5. Use <font color='green'>green</font> for positive or successful outcomes.\n"
                      '6. Use [text](URL) for clickable links.\n'
                      '7. Separate topics with ---.\n'
                      '8. Do not use # headings; they do not render inside cards.\n'
                      '9. Do not use > blockquotes.\n'
                      '10. Use line breaks and bold text to show hierarchy.'},
 'dingtalk': {'name': 'DingTalk',
              'format': 'Markdown',
              'max_length': 'About 20000 bytes',
              'supported': ['### level 3 headings / #### level 4 headings',
                            '**bold**',
                            '[link text](URL)',
                            '> blockquotes',
                            '--- (horizontal rule)',
                            '- unordered lists / 1. ordered lists'],
              'unsupported': ['# level 1 / ## level 2 headings (may not render)',
                              '<font> colored text',
                              '~~strikethrough~~',
                              'Tables / embedded images'],
              'prompt': 'DingTalk Markdown formatting:\n'
                        '1. Use ### or #### for section headings, not # or ##.\n'
                        '2. Use **bold** for keywords and data.\n'
                        '3. Use > blockquotes for notes and supporting details.\n'
                        '4. Separate topics with ---.\n'
                        '5. Use [text](URL) for clickable links.\n'
                        '6. Organize key points in numbered lists.\n'
                        '7. Do not use <font> color tags; DingTalk does not support them.\n'
                        '8. Do not use strikethrough.\n'
                        '9. Add a blank line between headings and body text.'},
 'wework': {'name': 'WeCom',
            'format': 'Markdown (group bots) / plain text (personal WeChat)',
            'max_length': 'About 4000 bytes',
            'supported': ['**bold**', '[link text](URL)', '> blockquotes (first line only)'],
            'unsupported': ['# headings',
                            '--- (horizontal rule)',
                            '<font> colored text',
                            '~~strikethrough~~',
                            'Tables / embedded images / ordered lists'],
            'prompt': 'WeCom Markdown formatting:\n'
                      '1. Use **bold** for subheadings and important words.\n'
                      '2. Use [text](URL) for clickable links.\n'
                      '3. Use > blockquotes for notes; only the first line is styled.\n'
                      '4. Keep content concise to fit the 4KB limit.\n'
                      '5. Do not use # headings; they do not render.\n'
                      '6. Do not use ---; separate sections with blank lines instead.\n'
                      '7. Do not use <font> color tags.\n'
                      '8. Do not use strikethrough or ordered lists.\n'
                      '9. Use line breaks and bold text to show hierarchy.\n'
                      '10. Personal WeChat mode strips all formatting to plain text.'},
 'telegram': {'name': 'Telegram',
              'format': 'HTML (converted automatically from Markdown)',
              'max_length': 'About 4096 characters',
              'supported': ['<b>bold</b> (from **bold**)',
                            '<i>italic</i> (from *italic*)',
                            '<s>strikethrough</s> (from ~~strikethrough~~)',
                            '<code>inline code</code> (from `code`)',
                            "<a href='URL'>link</a> (from [text](URL))",
                            '<blockquote>quote</blockquote> (from > quote)'],
              'unsupported': ['# headings (# prefix is stripped)',
                              '--- (horizontal rules are stripped)',
                              '<font> colored text (tags are stripped)',
                              'Tables / embedded images'],
              'prompt': 'Telegram HTML formatting (input is Markdown, converted automatically to HTML):\n'
                        '1. Use **bold** for keywords; it becomes <b>.\n'
                        '2. Use *italic* for supporting details; it becomes <i>.\n'
                        '3. Use `code` for data values and times; it becomes <code>.\n'
                        '4. Use [text](URL) for links; it becomes <a>.\n'
                        '5. Use lines starting with > for quotes; they become <blockquote>.\n'
                        '6. Do not use # headings; Telegram has no heading style and # is stripped.\n'
                        '7. Do not use ---; it is stripped. Separate sections with blank lines.\n'
                        '8. Do not use <font> color tags; they are stripped.\n'
                        '9. Keep messages within the 4096-character limit.\n'
                        '10. Link previews are disabled by default for compact messages.'},
 'email': {'name': 'Email',
           'format': 'HTML (full page converted from Markdown)',
           'max_length': 'No hard limit',
           'supported': ['# / ## / ### headings (converted to <h1>/<h2>/<h3>)',
                         '**bold** / *italic* / ~~strikethrough~~',
                         '[link text](URL)',
                         '`inline code`',
                         '--- (horizontal rule)'],
           'unsupported': ['<font> colored text (escaped)', 'Complex tables'],
           'prompt': 'Email HTML formatting (Markdown input is converted to styled HTML):\n'
                     '1. Use #, ##, and ### for a clear heading hierarchy.\n'
                     '2. Use **bold** and *italic* for readability.\n'
                     '3. Use [text](URL) for clickable links, rendered in blue.\n'
                     '4. Separate sections with ---.\n'
                     '5. Use `code` for technical terms and data.\n'
                     '6. Longer content is supported; email has no strict length limit here.\n'
                     '7. The date and time are appended to the email subject automatically.\n'
                     '8. A plain-text alternative is included automatically.'},
 'ntfy': {'name': 'ntfy',
          'format': 'Markdown (native support)',
          'max_length': 'About 3800 bytes (4KB per-message limit)',
          'supported': ['**bold** / *italic*',
                        '[link text](URL)',
                        '> blockquotes',
                        '`inline code`',
                        '- lists'],
          'unsupported': ['# headings (client-dependent)',
                          '<font> colored text',
                          '--- (client-dependent)',
                          'Tables'],
          'prompt': 'ntfy Markdown formatting:\n'
                    '1. Use **bold** for keywords.\n'
                    '2. Use [text](URL) for clickable links.\n'
                    '3. Use > blockquotes for notes.\n'
                    '4. Use `code` for data values.\n'
                    '5. Keep content concise to fit the 4KB limit.\n'
                    '6. Do not use <font> color tags; they have no effect.\n'
                    '7. Do not rely on # headings or --- horizontal rules.\n'
                    '8. Organize content with blank lines and bold text.'},
 'bark': {'name': 'Bark',
          'format': 'Markdown (iOS notifications)',
          'max_length': 'About 3600 bytes (APNs 4KB limit)',
          'supported': ['**bold**', '[link text](URL)', 'Basic text formatting'],
          'unsupported': ['# headings',
                          '<font> colored text',
                          '--- (horizontal rule)',
                          '> blockquotes',
                          'Complex nested formatting'],
          'prompt': 'Bark formatting for iOS notifications:\n'
                    '1. Keep content very short for mobile reading.\n'
                    '2. Use **bold** for key information.\n'
                    '3. Use [text](URL) for links.\n'
                    '4. Avoid complex headings, colors, and blockquotes.\n'
                    '5. Keep the payload within the APNs 4KB limit.\n'
                    '6. Use indentation and line breaks to show hierarchy.\n'
                    '7. Prefer brief notifications and summaries over long articles.'},
 'slack': {'name': 'Slack',
           'format': 'mrkdwn (Slack format, converted automatically from Markdown)',
           'max_length': 'About 4000 bytes',
           'supported': ['*bold* (from **bold**)',
                         '_italic_',
                         '~strikethrough~ (from ~~strikethrough~~)',
                         '<URL|link text> (from [text](URL))',
                         '`inline code`',
                         '```code blocks```',
                         '> blockquotes'],
           'unsupported': ['# headings (converted to bold)',
                           '<font> colored text',
                           '--- horizontal rules (inconsistent rendering)',
                           'Tables'],
           'prompt': 'Slack mrkdwn formatting (Markdown input is converted automatically):\n'
                     '1. Use **bold** for keywords; it becomes *bold*.\n'
                     '2. Use ~~strikethrough~~ for outdated information; it becomes ~strikethrough~.\n'
                     '3. Use [text](URL) for links; it becomes <URL|text>.\n'
                     '4. Use > blockquotes for notes.\n'
                     '5. Use `code` for data values.\n'
                     '6. Do not use # headings; Slack has no heading style.\n'
                     '7. Do not use <font> color tags.\n'
                     '8. Organize content with blank lines and bold text.'},
 'generic_webhook': {'name': 'Generic Webhook',
                     'format': 'Markdown (or a custom template)',
                     'max_length': 'About 4000 bytes',
                     'supported': ['Standard Markdown syntax'],
                     'unsupported': ['Depends on the receiving service'],
                     'prompt': 'Generic webhook formatting:\n'
                               '1. Use standard Markdown.\n'
                               '2. Avoid platform-specific syntax.\n'
                               '3. If a custom template is configured, the content is inserted into its '
                               '{content} placeholder.'}}


# ==================== 渠道 Markdown 适配 ====================

def _adapt_markdown_for_feishu(text: str) -> str:
    """将通用 Markdown 适配为飞书卡片 Markdown 格式

    飞书卡片支持：**粗体**, [链接](url), <font color='...'>, ---
    不支持：# 标题, > 引用块
    """
    # 将 # 标题转换为粗体（飞书卡片不渲染标题语法）
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)
    # 去除引用语法前缀（飞书不支持）
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _adapt_markdown_for_dingtalk(text: str) -> str:
    """将通用 Markdown 适配为钉钉 Markdown 格式

    钉钉支持：### #### 标题, **粗体**, [链接](url), > 引用, ---
    不支持：# ## 标题, <font> 彩色文本, ~~删除线~~
    """
    # 去除 <font> 标签（钉钉不支持，保留内容）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    # 将 # 和 ## 标题降级为 ### （钉钉仅支持 ### 和 ####）
    text = re.sub(r'^##\s+(.+)$', r'### \1', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'### \1', text, flags=re.MULTILINE)
    # 去除删除线语法（钉钉不支持）
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _adapt_markdown_for_wework(text: str) -> str:
    """将通用 Markdown 适配为企业微信 Markdown 格式

    企业微信支持：**粗体**, [链接](url), > 引用（有限）
    不支持：# 标题, ---, <font>, ~~删除线~~, 有序列表
    """
    # 去除 <font> 标签（保留内容）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    # 将 # 标题转换为粗体（企业微信不渲染标题语法）
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)
    # 将 --- 分割线替换为多个换行（企业微信不渲染水平线）
    text = re.sub(r'^[\-\*]{3,}\s*$', '\n\n', text, flags=re.MULTILINE)
    # 去除删除线语法（企业微信不支持）
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 清理多余空行（保留最多两个）
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


def _adapt_markdown_for_ntfy(text: str) -> str:
    """将通用 Markdown 适配为 ntfy 格式

    ntfy 支持：**粗体**, *斜体*, [链接](url), > 引用, `代码`
    不可靠：# 标题, ---, <font>
    """
    # 去除 <font> 标签（ntfy 不支持）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _adapt_markdown_for_bark(text: str) -> str:
    """将通用 Markdown 适配为 Bark 格式（iOS 推送）

    Bark 支持：**粗体**, [链接](url), 基础文本
    不支持：# 标题, <font>, ---, > 引用, 复杂嵌套
    """
    # 去除 <font> 标签（保留内容）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    # 将 # 标题转换为粗体
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)
    # 将 --- 替换为换行
    text = re.sub(r'^[\-\*]{3,}\s*$', '\n', text, flags=re.MULTILINE)
    # 去除引用语法
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # 去除删除线语法
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ==================== 格式转换 ====================

def _markdown_to_telegram_html(text: str) -> str:
    """
    将 markdown 转换为 Telegram 支持的 HTML 格式

    Telegram 支持的标签：<b>, <i>, <s>, <code>, <a href="url">text</a>, <blockquote>
    """
    # 预处理：去除 <font> 标签（Telegram 不支持，保留内容）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)

    lines = text.split('\n')
    result_lines = []
    in_blockquote = False

    for line in lines:
        # 将标题符号 # ## ### 转换为粗体
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if header_match:
            line = f'**{header_match.group(2)}**'

        # 去除水平分割线
        if re.match(r'^[\-\*]{3,}\s*$', line):
            if in_blockquote:
                result_lines.append('</blockquote>')
                in_blockquote = False
            line = ''

        # 处理引用块 > text → <blockquote>text</blockquote>
        quote_match = re.match(r'^>\s*(.*)$', line)
        if quote_match:
            if not in_blockquote:
                result_lines.append('<blockquote>')
                in_blockquote = True
            result_lines.append(quote_match.group(1))
            continue
        elif in_blockquote:
            result_lines.append('</blockquote>')
            in_blockquote = False

        result_lines.append(line)

    if in_blockquote:
        result_lines.append('</blockquote>')

    text = '\n'.join(result_lines)

    # 转义 HTML 实体（在标记替换之前，但在 blockquote 标签之后）
    # 分段处理：保留已生成的 HTML 标签
    parts = re.split(r'(</?blockquote>)', text)
    escaped_parts = []
    for part in parts:
        if part in ('<blockquote>', '</blockquote>'):
            escaped_parts.append(part)
        else:
            part = part.replace('&', '&amp;')
            part = part.replace('<', '&lt;')
            part = part.replace('>', '&gt;')
            escaped_parts.append(part)
    text = ''.join(escaped_parts)

    # 转换链接 [text](url) → <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 转换粗体 **text** → <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # 转换斜体 *text* → <i>text</i>
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)

    # 转换删除线 ~~text~~ → <s>text</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 转换行内代码 `code` → <code>code</code>
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _convert_markdown_to_slack(text: str) -> str:
    """将 Markdown 转换为 Slack mrkdwn 格式（增强版）

    Slack mrkdwn 与标准 Markdown 差异：
    - 粗体: *text* (非 **text**)
    - 删除线: ~text~ (非 ~~text~~)
    - 链接: <url|text> (非 [text](url))
    - 不支持标题语法
    """
    # 去除 <font> 标签（保留内容）
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    # 将 # 标题转换为粗体（Slack 无标题样式）
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)
    # 去除 --- 分割线（Slack 渲染不稳定）
    text = re.sub(r'^[\-\*]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 转换链接格式: [文本](url) → <url|文本>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    # 转换删除线: ~~文本~~ → ~文本~
    text = re.sub(r'~~(.+?)~~', r'~\1~', text)
    # 转换粗体: **文本** → *文本*（必须在删除线之后）
    text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _markdown_to_simple_html(text: str) -> str:
    """
    将 markdown 转换为简单 HTML（用于 Email）
    """
    html = text

    # 转义
    html = html.replace('&', '&amp;')
    html = html.replace('<', '&lt;')
    html = html.replace('>', '&gt;')

    # 链接
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)

    # 标题 ### → <h3>
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

    # 粗体
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)

    # 斜体
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)

    # 删除线
    html = re.sub(r'~~(.+?)~~', r'<del>\1</del>', html)

    # 行内代码
    html = re.sub(r'`(.+?)`', r'<code>\1</code>', html)

    # 分割线
    html = re.sub(r'^[\-\*]{3,}\s*$', '<hr>', html, flags=re.MULTILINE)

    # 换行
    html = html.replace('\n', '<br>\n')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>TrendRadar Notification</title>
<style>body{{font-family:sans-serif;padding:20px;max-width:800px;margin:0 auto}}
a{{color:#1a73e8}}h1,h2,h3{{color:#333}}hr{{border:none;border-top:1px solid #ddd;margin:16px 0}}
code{{background:#f5f5f5;padding:2px 6px;border-radius:3px}}</style>
</head><body>{html}</body></html>"""


# ==================== 各渠道发送器 ====================

def _send_feishu(webhook_url: str, content: str, title: str) -> Dict:
    """飞书发送（纯文本消息，与 trendradar send_to_feishu 一致）

    飞书 webhook 使用 msg_type: "text"，所有信息整合到 content.text 中。
    """
    payload = {
        "msg_type": "text",
        "content": {
            "text": content,
        },
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        data = resp.json()
        ok = resp.status_code == 200 and (data.get("code") == 0 or data.get("StatusCode") == 0)
        detail = ""
        if not ok:
            detail = data.get("msg") or data.get("StatusMessage", "")
        return {"success": ok, "detail": detail}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_dingtalk(webhook_url: str, content: str, title: str) -> Dict:
    """钉钉发送（接收已适配的 Markdown）"""
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content}
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        data = resp.json()
        ok = resp.status_code == 200 and data.get("errcode") == 0
        return {"success": ok, "detail": data.get("errmsg", "") if not ok else ""}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_wework(webhook_url: str, content: str, title: str, msg_type: str = "markdown") -> Dict:
    """企业微信发送（接收已适配的 Markdown，text 模式自动剥离格式）"""
    if msg_type == "text":
        payload = {"msgtype": "text", "text": {"content": strip_markdown(content)}}
    else:
        payload = {"msgtype": "markdown", "markdown": {"content": content}}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        data = resp.json()
        ok = resp.status_code == 200 and data.get("errcode") == 0
        return {"success": ok, "detail": data.get("errmsg", "") if not ok else ""}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_telegram(bot_token: str, chat_id: str, content: str, title: str) -> Dict:
    """Telegram 发送（接收已转换的 HTML）"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()
        ok = resp.status_code == 200 and data.get("ok")
        return {"success": ok, "detail": data.get("description", "") if not ok else ""}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_email(
    from_email: str, password: str, to_email: str,
    message: str, title: str,
    smtp_server: str = "", smtp_port: str = ""
) -> Dict:
    """邮件发送（HTML 格式）"""
    try:
        domain = from_email.split("@")[-1].lower()
        html_content = _markdown_to_simple_html(message)

        # SMTP 配置
        if smtp_server and smtp_port:
            server_host = smtp_server
            port = int(smtp_port)
            use_tls = port != 465
        elif domain in SMTP_CONFIGS:
            cfg = SMTP_CONFIGS[domain]
            server_host = cfg["server"]
            port = cfg["port"]
            use_tls = cfg["encryption"] == "TLS"
        else:
            server_host = f"smtp.{domain}"
            port = 587
            use_tls = True

        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr(("TrendRadar", from_email))

        recipients = [addr.strip() for addr in to_email.split(",")]
        msg["To"] = ", ".join(recipients)

        now = datetime.now()
        msg["Subject"] = Header(f"{title} - {now.strftime('%Y-%m-%d %H:%M')}", "utf-8")
        msg["MIME-Version"] = "1.0"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        # 纯文本备选
        msg.attach(MIMEText(strip_markdown(message), "plain", "utf-8"))
        # HTML 主体
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if use_tls:
            server = smtplib.SMTP(server_host, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(server_host, port, timeout=30)
            server.ehlo()

        server.login(from_email, password)
        server.send_message(msg)
        server.quit()

        return {"success": True, "detail": ""}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_ntfy(server_url: str, topic: str, content: str, title: str, token: str = "") -> Dict:
    """ntfy 发送（接收已适配的 Markdown，与 trendradar send_to_ntfy 一致）

    注意：Title 使用 ASCII 字符避免 HTTP header 编码问题。
    支持 429 速率限制重试。
    """
    base_url = server_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    url = f"{base_url}/{topic}"

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Markdown": "yes",
        "Title": "TrendRadar Notification",  # ASCII，避免 HTTP header 编码问题
        "Priority": "default",
        "Tags": "news",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(url, data=content.encode("utf-8"), headers=headers, timeout=30)
        if resp.status_code == 200:
            return {"success": True, "detail": ""}
        elif resp.status_code == 429:
            # 速率限制，等待后重试一次（与 trendradar 一致）
            time.sleep(10)
            retry_resp = requests.post(url, data=content.encode("utf-8"), headers=headers, timeout=30)
            ok = retry_resp.status_code == 200
            return {"success": ok, "detail": "" if ok else f"retry status={retry_resp.status_code}"}
        elif resp.status_code == 413:
            return {"success": False, "detail": f"Message rejected because it is too large ({len(content.encode('utf-8'))} bytes)"}
        else:
            return {"success": False, "detail": f"status={resp.status_code}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_bark(bark_url: str, content: str, title: str) -> Dict:
    """Bark 发送（接收已适配的 Markdown，iOS 推送）"""
    parsed = urlparse(bark_url)
    device_key = parsed.path.strip('/').split('/')[0] if parsed.path else None
    if not device_key:
        return {"success": False, "detail": f"Could not extract device_key from URL: {bark_url}"}

    api_endpoint = f"{parsed.scheme}://{parsed.netloc}/push"
    payload = {
        "title": title,
        "markdown": content,
        "device_key": device_key,
        "sound": "default",
        "group": "TrendRadar",
        "action": "none",
    }

    try:
        resp = requests.post(api_endpoint, json=payload, timeout=30)
        data = resp.json()
        ok = resp.status_code == 200 and data.get("code") == 200
        return {"success": ok, "detail": data.get("message", "") if not ok else ""}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_slack(webhook_url: str, content: str, title: str) -> Dict:
    """Slack 发送（接收已转换的 mrkdwn）"""
    payload = {"text": content}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        ok = resp.status_code == 200 and resp.text == "ok"
        return {"success": ok, "detail": "" if ok else resp.text}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_generic_webhook(
    webhook_url: str, message: str, title: str, payload_template: str = ""
) -> Dict:
    """通用 Webhook 发送（Markdown 格式，支持自定义模板）"""
    try:
        if payload_template:
            json_content = json.dumps(message)[1:-1]
            json_title = json.dumps(title)[1:-1]
            payload_str = payload_template.replace("{content}", json_content).replace("{title}", json_title)
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {"title": title, "content": message}
        else:
            payload = {"title": title, "content": message}

        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        ok = 200 <= resp.status_code < 300
        return {"success": ok, "detail": "" if ok else f"status={resp.status_code}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


# ==================== 工具类 ====================

class NotificationTools:
    """通知推送工具类"""

    def __init__(self, project_root: str = None):
        if project_root:
            self.project_root = Path(project_root)
        else:
            current_file = Path(__file__)
            self.project_root = current_file.parent.parent.parent

    def _load_merged_config(self) -> Dict[str, Any]:
        """
        加载合并后的通知配置（config.yaml + .env）

        Returns:
            包含 webhook 配置和通知参数的合并字典
        """
        config_path = self.project_root / "config" / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
        else:
            config_data = {}

        webhook_config = _load_webhook_config(config_data)
        notification_config = _load_notification_config(config_data)
        return {**webhook_config, **notification_config}

    def _detect_config_source(self, env_key: str, yaml_value: str) -> str:
        """检测配置项来源：env / yaml / 未配置"""
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            return "env"
        elif yaml_value:
            return "yaml"
        return ""

    def get_channel_format_guide(self, channel: Optional[str] = None) -> Dict:
        """
        获取渠道格式化策略指南

        返回各渠道支持的 Markdown 特性、限制和最佳格式化提示词，
        供 LLM 在生成推送内容时参考，确保内容样式贴合目标渠道。

        Args:
            channel: 指定渠道 ID，None 返回所有渠道的策略

        Returns:
            格式化策略字典
        """
        if channel:
            if channel not in CHANNEL_FORMAT_GUIDES:
                valid = list(CHANNEL_FORMAT_GUIDES.keys())
                return {
                    "success": False,
                    "error": {
                        "code": "INVALID_CHANNEL",
                        "message": f"Invalid channel: {channel}",
                        "suggestion": f"Supported channels: {valid}",
                    },
                }
            guide = CHANNEL_FORMAT_GUIDES[channel]
            return {
                "success": True,
                "channel": channel,
                "guide": guide,
            }
        else:
            return {
                "success": True,
                "summary": f"Formatting guides for {len(CHANNEL_FORMAT_GUIDES)} channels",
                "guides": CHANNEL_FORMAT_GUIDES,
            }

    def get_notification_channels(self) -> Dict:
        """
        获取所有通知渠道的配置状态

        检测 config.yaml 和 .env 环境变量，返回每个渠道是否已配置。

        Returns:
            渠道状态字典
        """
        try:
            config = self._load_merged_config()
            enabled = config.get("ENABLE_NOTIFICATION", True)

            # 从 yaml 直接读取（用于判断来源）
            config_path = self.project_root / "config" / "config.yaml"
            yaml_channels = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                    yaml_channels = raw.get("notification", {}).get("channels", {})

            channels = []
            env_key_map = {
                "FEISHU_WEBHOOK_URL": ("feishu", "webhook_url"),
                "DINGTALK_WEBHOOK_URL": ("dingtalk", "webhook_url"),
                "WEWORK_WEBHOOK_URL": ("wework", "webhook_url"),
                "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
                "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
                "EMAIL_FROM": ("email", "from"),
                "EMAIL_PASSWORD": ("email", "password"),
                "EMAIL_TO": ("email", "to"),
                "NTFY_SERVER_URL": ("ntfy", "server_url"),
                "NTFY_TOPIC": ("ntfy", "topic"),
                "BARK_URL": ("bark", "url"),
                "SLACK_WEBHOOK_URL": ("slack", "webhook_url"),
                "GENERIC_WEBHOOK_URL": ("generic_webhook", "webhook_url"),
            }

            for channel_id, required_keys in _CHANNEL_REQUIREMENTS.items():
                is_configured = all(config.get(k) for k in required_keys)

                # 判断来源
                sources = set()
                for key in required_keys:
                    ch_name, field = env_key_map.get(key, ("", ""))
                    yaml_val = yaml_channels.get(ch_name, {}).get(field, "")
                    src = self._detect_config_source(key, yaml_val)
                    if src:
                        sources.add(src)

                channels.append({
                    "id": channel_id,
                    "name": _CHANNEL_NAMES.get(channel_id, channel_id),
                    "configured": is_configured,
                    "source": list(sources) if sources else [],
                })

            configured_count = sum(1 for ch in channels if ch["configured"])

            return {
                "success": True,
                "notification_enabled": enabled,
                "summary": f"{configured_count}/{len(channels)} channels configured",
                "channels": channels,
            }
        except Exception as e:
            return {
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": str(e)},
            }

    def send_notification(
        self,
        message: str,
        title: str = "TrendRadar Notification",
        channels: Optional[List[str]] = None,
    ) -> Dict:
        """
        向已配置的通知渠道发送消息

        接受 markdown 格式内容，内部自动转换为各渠道要求的格式。

        Args:
            message: markdown 格式的消息内容
            title: 消息标题
            channels: 指定发送的渠道列表，None 表示发送到所有已配置渠道
                      可选值: feishu, dingtalk, wework, telegram, email, ntfy, bark, slack, generic_webhook

        Returns:
            发送结果字典
        """
        if not message or not message.strip():
            return {
                "success": False,
                "error": {"code": "EMPTY_MESSAGE", "message": "Message content cannot be empty"},
            }

        try:
            config = self._load_merged_config()

            if not config.get("ENABLE_NOTIFICATION", True):
                return {
                    "success": False,
                    "error": {"code": "NOTIFICATION_DISABLED", "message": "Notifications are disabled (notification.enabled = false)"},
                }

            # 确定目标渠道
            all_channel_ids = list(_CHANNEL_REQUIREMENTS.keys())
            if channels:
                # 验证渠道名称
                invalid = [ch for ch in channels if ch not in all_channel_ids]
                if invalid:
                    raise InvalidParameterError(
                        f"Invalid channel: {invalid}",
                        suggestion=f"Supported channels: {all_channel_ids}"
                    )
                target_channels = channels
            else:
                # 发送到所有已配置渠道
                target_channels = [
                    ch_id for ch_id, keys in _CHANNEL_REQUIREMENTS.items()
                    if all(config.get(k) for k in keys)
                ]

            if not target_channels:
                return {
                    "success": False,
                    "error": {
                        "code": "NO_CHANNELS",
                        "message": "No configured destination channels",
                        "suggestion": "Configure at least one notification channel in config.yaml or .env",
                    },
                }

            # 逐渠道发送
            results = {}
            for ch_id in target_channels:
                required_keys = _CHANNEL_REQUIREMENTS[ch_id]
                if not all(config.get(k) for k in required_keys):
                    results[ch_id] = {"success": False, "detail": "Channel is not configured"}
                    continue

                result = self._dispatch_to_channel(ch_id, config, message, title)
                results[ch_id] = result

            success_count = sum(1 for r in results.values() if r["success"])
            total = len(results)

            return {
                "success": success_count > 0,
                "summary": f"{success_count}/{total} channels sent successfully",
                "results": {
                    ch_id: {
                        "name": _CHANNEL_NAMES.get(ch_id, ch_id),
                        **r,
                    }
                    for ch_id, r in results.items()
                },
            }

        except MCPError as e:
            return {"success": False, "error": e.to_dict()}
        except Exception as e:
            return {
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": str(e)},
            }

    def _dispatch_to_channel(
        self, channel_id: str, config: Dict, message: str, title: str
    ) -> Dict:
        """分发消息到指定渠道（格式适配 → 字节分批 → 多账号 × 逐批发送）

        从 config.yaml → advanced.batch_size / batch_send_interval 读取配置。
        """
        # 从 config 读取批次配置（与 trendradar 一致）
        batch_sizes = self._get_batch_sizes()
        batch_interval = self._get_batch_interval()

        # Email 无字节限制，不走分批管线
        if channel_id == "email":
            return _send_email(
                config["EMAIL_FROM"],
                config["EMAIL_PASSWORD"],
                config["EMAIL_TO"],
                message, title,
                config.get("EMAIL_SMTP_SERVER", ""),
                config.get("EMAIL_SMTP_PORT", ""),
            )

        # 统一分批管线：格式适配 → 字节分割 → 添加批次头部 → (可选)反序
        batches = _prepare_batches(message, channel_id, batch_sizes)

        # 按渠道路由发送
        if channel_id == "feishu":
            return self._send_batched_multi_account(
                config["FEISHU_WEBHOOK_URL"], batches, channel_id,
                lambda url, content: _send_feishu(url, content, title),
                batch_interval,
            )
        elif channel_id == "dingtalk":
            return self._send_batched_multi_account(
                config["DINGTALK_WEBHOOK_URL"], batches, channel_id,
                lambda url, content: _send_dingtalk(url, content, title),
                batch_interval,
            )
        elif channel_id == "wework":
            msg_type = config.get("WEWORK_MSG_TYPE", "markdown")
            return self._send_batched_multi_account(
                config["WEWORK_WEBHOOK_URL"], batches, channel_id,
                lambda url, content: _send_wework(url, content, title, msg_type),
                batch_interval,
            )
        elif channel_id == "telegram":
            return self._send_batched_telegram(
                config, batches, title, batch_interval,
            )
        elif channel_id == "ntfy":
            return self._send_batched_ntfy(
                config, batches, title, batch_interval,
            )
        elif channel_id == "bark":
            return self._send_batched_multi_account(
                config["BARK_URL"], batches, channel_id,
                lambda url, content: _send_bark(url, content, title),
                batch_interval,
            )
        elif channel_id == "slack":
            return self._send_batched_multi_account(
                config["SLACK_WEBHOOK_URL"], batches, channel_id,
                lambda url, content: _send_slack(url, content, title),
                batch_interval,
            )
        elif channel_id == "generic_webhook":
            template = config.get("GENERIC_WEBHOOK_TEMPLATE", "")
            return self._send_batched_multi_account(
                config["GENERIC_WEBHOOK_URL"], batches, channel_id,
                lambda url, content: _send_generic_webhook(url, content, title, template),
                batch_interval,
            )
        else:
            return {"success": False, "detail": f"Unknown channel: {channel_id}"}

    def _get_batch_sizes(self) -> Dict:
        """从 config.yaml 读取 advanced.batch_size，合并到默认值"""
        try:
            config_path = self.project_root / "config" / "config.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                advanced = raw.get("advanced", {})
                cfg_sizes = advanced.get("batch_size", {})
                # 从 config 构建渠道映射
                sizes = dict(_CHANNEL_BATCH_SIZES_DEFAULT)
                default_size = cfg_sizes.get("default", 4000)
                for ch_id in sizes:
                    if ch_id in cfg_sizes:
                        sizes[ch_id] = cfg_sizes[ch_id]
                    elif ch_id not in ("email", "ntfy") and sizes[ch_id] == 4000:
                        # 使用 config 中的 default
                        sizes[ch_id] = default_size
                return sizes
        except Exception:
            pass
        return dict(_CHANNEL_BATCH_SIZES_DEFAULT)

    def _get_batch_interval(self) -> float:
        """从 config.yaml 读取 advanced.batch_send_interval"""
        try:
            config_path = self.project_root / "config" / "config.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                return float(raw.get("advanced", {}).get("batch_send_interval", _BATCH_INTERVAL_DEFAULT))
        except Exception:
            pass
        return _BATCH_INTERVAL_DEFAULT

    def _send_batched_multi_account(
        self, urls_str: str, batches: List[str], channel_id: str, send_func,
        batch_interval: float = _BATCH_INTERVAL_DEFAULT,
    ) -> Dict:
        """多账号 × 逐批发送（; 分隔的 URL）"""
        urls = [u.strip() for u in urls_str.split(";") if u.strip()]
        if not urls:
            return {"success": False, "detail": "URL is empty"}

        any_ok = False
        details = []
        for url in urls:
            for i, batch in enumerate(batches):
                r = send_func(url, batch)
                if r["success"]:
                    any_ok = True
                elif r["detail"]:
                    details.append(r["detail"])
                # 批次间间隔
                if i < len(batches) - 1:
                    time.sleep(batch_interval)

        return {
            "success": any_ok,
            "detail": "; ".join(details) if details else "",
            "batches": len(batches),
        }

    def _send_batched_telegram(
        self, config: Dict, batches: List[str], title: str,
        batch_interval: float = _BATCH_INTERVAL_DEFAULT,
    ) -> Dict:
        """Telegram 多账号 × 逐批发送（token/chat_id 配对）"""
        tokens = config["TELEGRAM_BOT_TOKEN"].split(";")
        chat_ids = config["TELEGRAM_CHAT_ID"].split(";")
        if len(tokens) != len(chat_ids):
            return {"success": False, "detail": "bot_token and chat_id counts do not match"}

        any_ok = False
        details = []
        for token, cid in zip(tokens, chat_ids):
            token, cid = token.strip(), cid.strip()
            if not (token and cid):
                continue
            for i, batch in enumerate(batches):
                r = _send_telegram(token, cid, batch, title)
                if r["success"]:
                    any_ok = True
                elif r["detail"]:
                    details.append(r["detail"])
                if i < len(batches) - 1:
                    time.sleep(batch_interval)

        return {
            "success": any_ok,
            "detail": "; ".join(details) if details else "",
            "batches": len(batches),
        }

    def _send_batched_ntfy(
        self, config: Dict, batches: List[str], title: str,
        batch_interval: float = _BATCH_INTERVAL_DEFAULT,
    ) -> Dict:
        """ntfy 多账号 × 逐批发送（server/topic/token 配对，含速率限制处理）"""
        servers = config["NTFY_SERVER_URL"].split(";")
        topics = config["NTFY_TOPIC"].split(";")
        tokens_str = config.get("NTFY_TOKEN", "")
        tokens = tokens_str.split(";") if tokens_str else [""]
        if len(servers) != len(topics):
            return {"success": False, "detail": "server_url and topic counts do not match"}

        any_ok = False
        details = []
        for i, (srv, topic) in enumerate(zip(servers, topics)):
            srv, topic = srv.strip(), topic.strip()
            tk = tokens[i].strip() if i < len(tokens) else ""
            if not (srv and topic):
                continue
            # ntfy.sh 公共服务器用 2s 间隔（与 trendradar 一致）
            interval = 2.0 if "ntfy.sh" in srv else batch_interval
            for j, batch in enumerate(batches):
                r = _send_ntfy(srv, topic, batch, title, tk)
                if r["success"]:
                    any_ok = True
                elif r["detail"]:
                    details.append(r["detail"])
                if j < len(batches) - 1:
                    time.sleep(interval)

        return {
            "success": any_ok,
            "detail": "; ".join(details) if details else "",
            "batches": len(batches),
        }
