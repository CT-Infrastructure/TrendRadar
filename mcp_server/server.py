"""
TrendRadar MCP Server, implemented with FastMCP 2.0.

Provides MCP tools over either stdio or HTTP.
"""

import asyncio
import json
from typing import List, Optional, Dict, Union

from fastmcp import FastMCP

from .tools.data_query import DataQueryTools
from .tools.analytics import AnalyticsTools
from .tools.search_tools import SearchTools
from .tools.config_mgmt import ConfigManagementTools
from .tools.system import SystemManagementTools
from .tools.storage_sync import StorageSyncTools
from .tools.article_reader import ArticleReaderTools
from .tools.notification import NotificationTools
from .utils.date_parser import DateParser
from .utils.errors import MCPError


# Create the FastMCP 2.0 application
mcp = FastMCP('trendradar-news')

# Shared tool instances, initialized on first request
_tools_instances = {}


def _get_tools(project_root: Optional[str] = None):
    """Get or create the shared tool instances."""
    if not _tools_instances:
        _tools_instances['data'] = DataQueryTools(project_root)
        _tools_instances['analytics'] = AnalyticsTools(project_root)
        _tools_instances['search'] = SearchTools(project_root)
        _tools_instances['config'] = ConfigManagementTools(project_root)
        _tools_instances['system'] = SystemManagementTools(project_root)
        _tools_instances['storage'] = StorageSyncTools(project_root)
        _tools_instances['article'] = ArticleReaderTools(project_root)
        _tools_instances['notification'] = NotificationTools(project_root)
    return _tools_instances


# ==================== MCP Resources ====================

@mcp.resource("config://platforms")
async def get_platforms_resource() -> str:
    """List configured hot-list platforms.

    Return the platform IDs and display names configured in config.yaml."""
    tools = _get_tools()
    config = await asyncio.to_thread(
        tools['config'].get_current_config, section="crawler"
    )
    return json.dumps({
        "platforms": config.get("platforms", []),
        "description": "Hot-list platforms configured for TrendRadar"
    }, ensure_ascii=False, indent=2)


@mcp.resource("config://rss-feeds")
async def get_rss_feeds_resource() -> str:
    """List RSS feeds and their current data.

    Return the RSS feed information available in the current status."""
    tools = _get_tools()
    status = await asyncio.to_thread(tools['data'].get_rss_feeds_status)
    return json.dumps({
        "feeds": status.get("today_feeds", {}),
        "description": "RSS feeds available in TrendRadar"
    }, ensure_ascii=False, indent=2)


@mcp.resource("data://available-dates")
async def get_available_dates_resource() -> str:
    """List dates with locally available news data."""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['storage'].list_available_dates, source="local"
    )
    return json.dumps({
        "dates": result.get("data", {}).get("local", {}).get("dates", []),
        "description": "Dates with locally available data"
    }, ensure_ascii=False, indent=2)


@mcp.resource("config://keywords")
async def get_keywords_resource() -> str:
    """Return the keyword groups configured in frequency_words.txt."""
    tools = _get_tools()
    config = await asyncio.to_thread(
        tools['config'].get_current_config, section="keywords"
    )
    return json.dumps({
        "word_groups": config.get("word_groups", []),
        "total_groups": config.get("total_groups", 0),
        "description": "TrendRadar keyword configuration"
    }, ensure_ascii=False, indent=2)


# ==================== Date resolution tools (call first)====================

@mcp.tool
async def resolve_date_range(
    expression: str
) -> str:
    """Resolve a natural-language date expression to a standard date range.

    Call this first when a user specifies a relative date. The server calculates
    the range from its current time so clients use consistent dates.

    Suggested flow:
        1. A user asks for AI sentiment over the last 7 days.
        2. Call resolve_date_range("last 7 days").
        3. Pass the returned date_range to analyze_sentiment(topic="AI", ...).

    Args:
        expression: A supported relative date expression, including:
            - Single day: "today", "yesterday".
            - Week: "this week", "last week".
            - Month: "this month", "last month".
            - Recent days: "last 7 days", "last 30 days", or "last N days".
            Chinese equivalents are also supported.

    Returns:
        JSON containing success, expression, date_range (start/end in YYYY-MM-DD
        format), current_date, and a human-readable description. The date_range
        object can be passed directly to other tools.

    Examples:
        resolve_date_range("last 7 days")
        resolve_date_range("this week")"""
    try:
        result = await asyncio.to_thread(DateParser.resolve_date_range_expression, expression)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except MCPError as e:
        return json.dumps({
            "success": False,
            "error": e.to_dict()
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(e)
            }
        }, ensure_ascii=False, indent=2)


# ==================== News query tools ====================

@mcp.tool
async def get_latest_news(
    platforms: Optional[List[str]] = None,
    limit: int = 50,
    include_url: bool = False
) -> str:
    """Get the latest collected batch of trending news.

    Args:
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        limit: Maximum number of results; default 50, maximum 1000.
        include_url: Include source URLs; default False to reduce output size.

    Returns:
        A JSON news list.

    Presentation guidance:
        Show all returned items unless the user asks for a summary or highlights.
        If they ask why only some items are displayed, provide the complete list."""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['data'].get_latest_news,
        platforms=platforms, limit=limit, include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_trending_topics(
    top_n: int = 10,
    mode: str = 'current',
    extract_mode: str = 'keywords'
) -> str:
    """Get trending-topic frequency statistics.

    Args:
        top_n: Number of leading topics to return; default 10.
        mode: "daily" for the day's accumulated data, or "current" for the latest
            batch (default).
        extract_mode: "keywords" uses config/frequency_words.txt (default).
            "auto_extract" discovers frequent terms in headlines without presets.

    Returns:
        JSON topic-frequency statistics.

    Examples:
        get_trending_topics(mode="current")
        get_trending_topics(extract_mode="auto_extract", top_n=20)"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['data'].get_trending_topics,
        top_n=top_n, mode=mode, extract_mode=extract_mode
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== RSS query tools ====================

@mcp.tool
async def get_latest_rss(
    feeds: Optional[List[str]] = None,
    days: int = 1,
    limit: int = 50,
    include_summary: bool = False
) -> str:
    """Get the latest RSS items, optionally spanning several days.

    RSS data is stored separately from hot-list news and presented chronologically.

    Args:
        feeds: Feed IDs such as ['hacker-news', '36kr']; omit for all feeds.
        days: Number of recent days; default 1 (today only), maximum 30.
        limit: Maximum number of results; default 50, maximum 500.
        include_summary: Include article summaries; default False.

    Returns:
        A JSON list of RSS items.

    Examples:
        get_latest_rss()
        get_latest_rss(days=7, feeds=['hacker-news'])"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['data'].get_latest_rss,
        feeds=feeds, days=days, limit=limit, include_summary=include_summary
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def search_rss(
    keyword: str,
    feeds: Optional[List[str]] = None,
    days: int = 7,
    limit: int = 50,
    include_summary: bool = False
) -> str:
    """Search RSS articles for a keyword.

    Args:
        keyword: Required search keyword.
        feeds: Feed IDs such as ['hacker-news', '36kr']; omit to search all feeds.
        days: Number of recent days to search; default 7, maximum 30.
        limit: Maximum number of results; default 50.
        include_summary: Include article summaries; default False.

    Returns:
        A JSON list of matching RSS items.

    Examples:
        search_rss(keyword="AI")
        search_rss(keyword="machine learning", feeds=['hacker-news'], days=14)"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['data'].search_rss,
        keyword=keyword,
        feeds=feeds,
        days=days,
        limit=limit,
        include_summary=include_summary
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_rss_feeds_status() -> str:
    """Get RSS feed status and collection statistics.

    Returns:
        JSON containing available_dates, total_dates, today_feeds (a mapping from
        feed ID to name and item_count), and generated_at.

    Examples:
        get_rss_feeds_status()"""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['data'].get_rss_feeds_status)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_news_by_date(
    date_range: Optional[Union[Dict[str, str], str]] = None,
    platforms: Optional[List[str]] = None,
    limit: int = 50,
    include_url: bool = False
) -> str:
    """Get news for a date or range for historical analysis and comparison.

    Args:
        date_range: A range object such as {"start": "2025-01-01", "end": "2025-01-07"},
            a supported relative expression such as "today" or "last 7 days",
            or a single date such as "2025-01-15". Defaults to today.
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        limit: Maximum number of results; default 50, maximum 1000.
        include_url: Include source URLs; default False to reduce output size.

    Returns:
        JSON news items including titles, platforms, and rankings."""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['data'].get_news_by_date,
        date_range=date_range,
        platforms=platforms,
        limit=limit,
        include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)



# ==================== Advanced analysis tools ====================

@mcp.tool
async def analyze_topic_trend(
    topic: str,
    analysis_type: str = "trend",
    date_range: Optional[Union[Dict[str, str], str]] = None,
    granularity: str = "day",
    spike_threshold: float = 3.0,
    time_window: int = 24,
    lookahead_hours: int = 6,
    confidence_threshold: float = 0.7
) -> str:
    """Analyze a topic's trend, lifecycle, unusual popularity, or outlook.

    Resolve relative dates with resolve_date_range first for a precise range.

    Args:
        topic: Required topic keyword.
        analysis_type: "trend" (default), "lifecycle", "viral" (unusual attention),
            or "predict" (topic outlook).
        date_range: A range with start/end in YYYY-MM-DD format; defaults to the
            last 7 days.
        granularity: Time granularity; default "day".
        spike_threshold: Popularity multiplier threshold in viral mode; default 3.0.
        time_window: Detection window in hours in viral mode; default 24.
        lookahead_hours: Prediction horizon in hours in predict mode; default 6.
        confidence_threshold: Confidence threshold in predict mode; default 0.7.

    Returns:
        JSON trend-analysis results.

    Examples:
        analyze_topic_trend(topic="AI", date_range={"start": "2025-01-01", "end": "2025-01-07"})
        analyze_topic_trend(topic="Tesla", analysis_type="lifecycle")"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].analyze_topic_trend_unified,
        topic=topic,
        analysis_type=analysis_type,
        date_range=date_range,
        granularity=granularity,
        threshold=spike_threshold,
        time_window=time_window,
        lookahead_hours=lookahead_hours,
        confidence_threshold=confidence_threshold
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def analyze_data_insights(
    insight_type: str = "platform_compare",
    topic: Optional[str] = None,
    date_range: Optional[Union[Dict[str, str], str]] = None,
    min_frequency: int = 3,
    top_n: int = 20
) -> str:
    """Analyze platform differences, activity, or keyword co-occurrence.

    Args:
        insight_type: "platform_compare" compares attention across platforms
            (default); "platform_activity" analyzes posting frequency and active
            times; "keyword_cooccur" analyzes terms appearing together.
        topic: Optional topic keyword for platform_compare mode.
        date_range: Optional range object with start/end in YYYY-MM-DD format,
            for example {"start": "2025-01-01", "end": "2025-01-07"}. Do not pass
            an integer. Relative dates can be resolved with resolve_date_range.
        min_frequency: Minimum co-occurrence count for keyword_cooccur; default 3.
        top_n: Number of leading results for keyword_cooccur; default 20.

    Returns:
        JSON analytical insights.

    Examples:
        analyze_data_insights(insight_type="platform_compare", topic="AI")
        analyze_data_insights(insight_type="platform_activity", date_range={"start": "2025-01-01", "end": "2025-01-07"})
        analyze_data_insights(insight_type="keyword_cooccur", min_frequency=5, top_n=15)"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].analyze_data_insights_unified,
        insight_type=insight_type,
        topic=topic,
        date_range=date_range,
        min_frequency=min_frequency,
        top_n=top_n
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def analyze_sentiment(
    topic: Optional[str] = None,
    platforms: Optional[List[str]] = None,
    date_range: Optional[Union[Dict[str, str], str]] = None,
    limit: int = 50,
    sort_by_weight: bool = True,
    include_url: bool = False
) -> str:
    """Analyze news sentiment and popularity trends.

    Resolve relative dates with resolve_date_range first for a precise range.

    Args:
        topic: Optional topic keyword.
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        date_range: Range object with start/end in YYYY-MM-DD format; defaults to today.
        limit: Number of news items after title deduplication; default 50, maximum 100.
        sort_by_weight: Sort by popularity weight; default True.
        include_url: Include source URLs; default False to reduce output size.

    Returns:
        JSON containing sentiment distribution, popularity trends, and related news.

    Examples:
        analyze_sentiment(topic="AI", date_range={"start": "2025-01-01", "end": "2025-01-07"})"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].analyze_sentiment,
        topic=topic,
        platforms=platforms,
        date_range=date_range,
        limit=limit,
        sort_by_weight=sort_by_weight,
        include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def find_related_news(
    reference_title: str,
    date_range: Optional[Union[Dict[str, str], str]] = None,
    threshold: float = 0.5,
    limit: int = 50,
    include_url: bool = False
) -> str:
    """Find news related to a reference headline, including historical data.

    Args:
        reference_title: A complete or partial reference headline.
        date_range: Omit for today. Supports "today", "yesterday", "last_week",
            "last_month", or an object with start/end in YYYY-MM-DD format.
        threshold: Similarity threshold from 0 to 1; default 0.5. Higher is stricter.
        limit: Maximum number of results; default 50.
        include_url: Include source URLs; default False to reduce output size.

    Returns:
        A JSON list of related news, ordered by similarity.

    Examples:
        find_related_news(reference_title="Tesla price cuts")
        find_related_news(reference_title="AI breakthrough", date_range="last_week")"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['search'].find_related_news_unified,
        reference_title=reference_title,
        date_range=date_range,
        threshold=threshold,
        limit=limit,
        include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def generate_summary_report(
    report_type: str = "daily",
    date_range: Optional[Union[Dict[str, str], str]] = None
) -> str:
    """Generate a daily or weekly summary of trending news.

    Args:
        report_type: "daily" (default) or "weekly".
        date_range: Optional custom range object with start/end in YYYY-MM-DD format,
            for example {"start": "2025-01-01", "end": "2025-01-07"}. Do not pass
            an integer. Relative dates can be resolved with resolve_date_range.

    Returns:
        A JSON summary report containing Markdown content."""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].generate_summary_report,
        report_type=report_type,
        date_range=date_range
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def aggregate_news(
    date_range: Optional[Union[Dict[str, str], str]] = None,
    platforms: Optional[List[str]] = None,
    similarity_threshold: float = 0.7,
    limit: int = 50,
    include_url: bool = False
) -> str:
    """Deduplicate and combine related news across platforms.

    Merge reports of the same event into one item with cross-platform coverage
    and combined popularity information.

    Args:
        date_range: Date range; defaults to today.
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        similarity_threshold: Similarity threshold from 0.3 to 1.0; default 0.7.
            Higher is stricter.
        limit: Maximum number of aggregated items; default 50.
        include_url: Include source URLs; default False.

    Returns:
        JSON containing deduplication statistics, aggregated news, and platform coverage.

    Examples:
        aggregate_news()
        aggregate_news(similarity_threshold=0.8)"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].aggregate_news,
        date_range=date_range,
        platforms=platforms,
        similarity_threshold=similarity_threshold,
        limit=limit,
        include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def compare_periods(
    period1: Union[Dict[str, str], str],
    period2: Union[Dict[str, str], str],
    topic: Optional[str] = None,
    compare_type: str = "overview",
    platforms: Optional[List[str]] = None,
    top_n: int = 10
) -> str:
    """Compare news data from two periods.

    Compare topics, platform activity, and news volume, for example week over week
    or month over month, optionally focusing on one topic.

    Args:
        period1: Baseline period: an object with start/end in YYYY-MM-DD format,
            or "today", "yesterday", "this_week", "last_week", "this_month",
            or "last_month".
        period2: Comparison period, using the same format as period1.
        topic: Optional topic keyword to focus the comparison.
        compare_type: "overview" (default) compares volume, keywords, and leading
            news; "topic_shift" identifies rising, falling, and new topics;
            "platform_activity" compares news volume by platform.
        platforms: Optional platform filter, such as ['zhihu', 'weibo'].
        top_n: Number of leading results; default 10.

    Returns:
        JSON containing periods, compare_type, and the appropriate overview,
        topic_shift, or platform_comparison results.

    Examples:
        compare_periods(period1="last_week", period2="this_week")
        compare_periods(period1="last_month", period2="this_month", compare_type="topic_shift")
        compare_periods(period1={"start": "2025-01-01", "end": "2025-01-07"},
                        period2={"start": "2025-01-08", "end": "2025-01-14"}, topic="AI")"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['analytics'].compare_periods,
        period1=period1,
        period2=period2,
        topic=topic,
        compare_type=compare_type,
        platforms=platforms,
        top_n=top_n
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Search tools ====================

@mcp.tool
async def search_news(
    query: str,
    search_mode: str = "keyword",
    date_range: Optional[Union[Dict[str, str], str]] = None,
    platforms: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: str = "relevance",
    threshold: float = 0.6,
    include_url: bool = False,
    include_rss: bool = False,
    rss_limit: int = 20
) -> str:
    """Search hot-list news and optionally RSS using several search modes.

    Resolve relative dates with resolve_date_range first for a precise range.

    Args:
        query: Search keyword or content fragment.
        search_mode: "keyword" for exact keyword matches (default), "fuzzy" for
            similar content, or "entity" for people, places, and organizations.
        date_range: Range with start/end in YYYY-MM-DD format; defaults to today.
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        limit: Maximum number of hot-list results; default 50.
        sort_by: "relevance", "weight", or "date"; default "relevance".
        threshold: Similarity threshold in fuzzy mode, from 0 to 1; default 0.6.
        include_url: Include source URLs; default False.
        include_rss: Also search RSS data; default False.
        rss_limit: Maximum number of RSS results; default 20.

    Returns:
        JSON containing hot-list results and optional RSS results.

    Examples:
        search_news(query="AI")
        search_news(query="AI", include_rss=True)
        search_news(query="Tesla", date_range={"start": "2025-01-01", "end": "2025-01-07"})"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['search'].search_news_unified,
        query=query,
        search_mode=search_mode,
        date_range=date_range,
        platforms=platforms,
        limit=limit,
        sort_by=sort_by,
        threshold=threshold,
        include_url=include_url,
        include_rss=include_rss,
        rss_limit=rss_limit
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Configuration and system tools ====================

@mcp.tool
async def get_current_config(
    section: str = "all"
) -> str:
    """Get the current system configuration.

    Args:
        section: "all" (default), "crawler", "push", "keywords", or "weights".

    Returns:
        JSON configuration details."""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['config'].get_current_config, section=section)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_system_status() -> str:
    """Get system status and health information.

    Returns:
        JSON containing the system version, data statistics, cache status, and
        related operational information."""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['system'].get_system_status)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def check_version(
    proxy_url: Optional[str] = None
) -> str:
    """Check for updates to both TrendRadar and the MCP server.

    Compare the installed versions with the remote versions on GitHub.

    Args:
        proxy_url: Optional proxy for GitHub access, such as http://127.0.0.1:7890.

    Returns:
        JSON version comparisons and update availability for both components.

    Examples:
        check_version()
        check_version(proxy_url="http://127.0.0.1:7890")"""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['system'].check_version, proxy_url=proxy_url)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def trigger_crawl(
    platforms: Optional[List[str]] = None,
    save_to_local: bool = False,
    include_url: bool = False
) -> str:
    """Run one collection task, optionally persisting the results.

    Args:
        platforms: Platform IDs such as ['zhihu', 'weibo']; omit for all platforms.
        save_to_local: Save results in the local output directory; default False.
        include_url: Include source URLs; default False to reduce output size.

    Returns:
        JSON task status, successful and failed platform lists, and collected news.

    Examples:
        trigger_crawl(platforms=['zhihu'])
        trigger_crawl(save_to_local=True)"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['system'].trigger_crawl,
        platforms=platforms, save_to_local=save_to_local, include_url=include_url
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Storage synchronization tools ====================

@mcp.tool
async def sync_from_remote(
    days: int = 7
) -> str:
    """Download data from remote storage to the local installation.

    For example, the crawler may upload to Cloudflare R2 while the MCP server
    downloads that data for local queries and analysis.

    Args:
        days: Number of recent days to download; default 7. Use 0 to skip,
            7 for the past week, or 30 for the past month.

    Returns:
        JSON containing success, synced_files, synced_dates, skipped_dates
        (already local), failed_dates with errors, and a result message.

    Examples:
        sync_from_remote()
        sync_from_remote(days=30)

    Note:
        Configure storage.remote in config/config.yaml or set S3_ENDPOINT_URL,
        S3_BUCKET_NAME, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY."""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['storage'].sync_from_remote, days=days)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_storage_status() -> str:
    """Get storage configuration and status.

    Returns:
        JSON describing the configured backend, local and remote storage status,
        and data-pull settings."""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['storage'].get_storage_status)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def list_available_dates(
    source: str = "both"
) -> str:
    """List dates with available local or remote data.

    Args:
        source: "local", "remote", or "both" to list and compare both (default).

    Returns:
        JSON containing dates for each selected source and comparison results.

    Examples:
        list_available_dates()
        list_available_dates(source="local")"""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['storage'].list_available_dates, source=source)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Article reader tools ====================

@mcp.tool
async def read_article(
    url: str,
    timeout: int = 30
) -> str:
    """Read an article URL and return Markdown suitable for analysis.

    Uses Jina AI Reader to convert a page to Markdown and remove common page
    clutter such as navigation and advertisements. Useful for article details,
    summaries, translation, and content analysis.

    Suggested flow:
        1. Search with search_news(include_url=True) to find article links.
        2. Call read_article(url=the_link).
        3. Analyze, summarize, or translate the returned Markdown.

    Args:
        url: Required article URL starting with http:// or https://.
        timeout: Request timeout in seconds; default 30, clamped to 10-60.

    Returns:
        JSON containing the article's Markdown content.

    Examples:
        read_article(url="https://example.com/news/123")

    Note:
        Requests are spaced 5 seconds apart. Paywalls, login requirements, and
        the reader service's availability can limit the content returned."""
    tools = _get_tools()
    timeout = min(max(timeout, 10), 60)
    result = await asyncio.to_thread(
        tools['article'].read_article,
        url=url, timeout=timeout
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def read_articles_batch(
    urls: List[str],
    timeout: int = 30
) -> str:
    """Read up to five articles, spacing requests 5 seconds apart.

    Suggested flow:
        1. Search with search_news(include_url=True) to collect article links.
        2. Call read_articles_batch(urls=[...]).
        3. Compare or synthesize the returned article content.

    Args:
        urls: Required article URLs; at most five are processed.
        timeout: Timeout per article in seconds; default 30, clamped to 10-60.

    Returns:
        JSON containing each article's content and status.

    Examples:
        read_articles_batch(urls=["https://a.com/1", "https://b.com/2"])

    Note:
        URLs after the first five are skipped. A failure for one article does
        not stop the other articles from being processed."""
    tools = _get_tools()
    timeout = min(max(timeout, 10), 60)
    result = await asyncio.to_thread(
        tools['article'].read_articles_batch,
        urls=urls, timeout=timeout
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Notification tools ====================


@mcp.tool
async def get_channel_format_guide(channel: Optional[str] = None) -> str:
    """Get formatting guidance for notification channels.

    Return supported Markdown features, format restrictions, and formatting
    prompts. Call before send_notification to prepare content for a channel.

    Format overview:
        - Feishu: bold, colored <font> text, links, and horizontal rules.
        - DingTalk: level-3 headings, bold, quotes, and rules; no colors.
        - WeCom: bold, links, and quotes; no headings or horizontal rules.
        - Telegram: converted to HTML; bold, italic, strikethrough, code,
          links, and block quotes.
        - ntfy: standard Markdown without colors.
        - Bark: concise iOS notifications with bold and links.
        - Slack: converted to mrkdwn, with *bold*, ~strikethrough~, and <url|text>.
        - Email: converted to HTML with headings, styles, and horizontal rules.
        - Generic webhook: standard Markdown or a custom payload template.

    Args:
        channel: Optional channel ID; omit for all channels. Supported IDs:
            feishu, dingtalk, wework, telegram, email, ntfy, bark, slack,
            generic_webhook.

    Returns:
        JSON feature support, restrictions, and formatting prompts by channel.

    Examples:
        get_channel_format_guide()
        get_channel_format_guide(channel="feishu")
        get_channel_format_guide(channel="telegram")"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['notification'].get_channel_format_guide,
        channel=channel
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def get_notification_channels() -> str:
    """Get configured notification channels and their status.

    Inspect configuration and environment-variable settings for Feishu, DingTalk,
    WeCom, Telegram, email, ntfy, Bark, Slack, and generic webhooks.

    Returns:
        JSON indicating whether each channel is configured and where its settings
        came from.

    Examples:
        get_notification_channels()"""
    tools = _get_tools()
    result = await asyncio.to_thread(tools['notification'].get_notification_channels)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
async def send_notification(
    message: str,
    title: str = "TrendRadar Notification",
    channels: Optional[List[str]] = None,
) -> str:
    """Send a message to configured notification channels.

    Accept Markdown content and adapt it to each channel:
        - Feishu: Markdown cards with bold, colored text, links, and rules.
        - DingTalk: Markdown with headings reduced to level 3; font tags and
          strikethrough are removed.
        - WeCom: Markdown without headings, rules, font tags, or strikethrough.
        - Telegram: HTML converted from bold, italic, strikethrough, and quotes.
        - Email: a styled HTML message with headings and emphasis.
        - ntfy: Markdown with font tags removed.
        - Bark: concise Markdown reduced to bold and links.
        - Slack: mrkdwn with converted emphasis and links.
        - Generic webhook: Markdown, optionally in a custom template.

    Call get_channel_format_guide first for detailed channel-specific guidance.

    Args:
        message: Required Markdown message body.
        title: Message title; defaults to "TrendRadar Notification".
        channels: Optional channel IDs; omit to send to all configured channels.
            Supported IDs: feishu, dingtalk, wework, telegram, email, ntfy,
            bark, slack, generic_webhook.

    Returns:
        JSON delivery status for each channel.

    Examples:
        send_notification(message="**Test message**")
        send_notification(message="Urgent notice", title="System alert", channels=["feishu", "dingtalk"])"""
    tools = _get_tools()
    result = await asyncio.to_thread(
        tools['notification'].send_notification,
        message=message, title=title, channels=channels
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Server entry point ====================

def run_server(
    project_root: Optional[str] = None,
    transport: str = 'stdio',
    host: str = '0.0.0.0',
    port: int = 3333
):
    """Start the MCP server.

    Args:
        project_root: Project root directory.
        transport: "stdio" or "http"; defaults to "stdio".
        host: HTTP listen address; defaults to 0.0.0.0.
        port: HTTP listen port; defaults to 3333."""
    # Initialize tool instances
    _get_tools(project_root)

    # Print startup information to stderr in stdio mode; stdout carries JSON-RPC
    import sys
    _banner_stdout = sys.stdout
    if transport == 'stdio':
        sys.stdout = sys.stderr

    print()
    print("=" * 60)
    print("  TrendRadar MCP Server - FastMCP 2.0")
    print("=" * 60)
    print(f"  Transport: {transport.upper()}")

    if transport == 'stdio':
        print("  Protocol: MCP over stdio (standard input/output)")
        print("  Communication: MCP client over standard input/output")
    elif transport == 'http':
        print(f"  Protocol: MCP over HTTP")
        print(f"  Listening on: {host}:{port}")

    if project_root:
        print(f"  Project directory: {project_root}")
    else:
        print("  Project directory: current directory")

    print()
    print("  Registered tools:")
    print("    === Date resolution (recommended first)===")
    print("    0. resolve_date_range       - Resolve natural-language date ranges")
    print()
    print("    === News queries===")
    print("    1. get_latest_news        - Get the latest news")
    print("    2. get_news_by_date       - Query news by date (relative expressions supported)")
    print("    3. get_trending_topics    - Get trending topics (automatic extraction supported)")
    print()
    print("    === RSS queries ===")
    print("    4. get_latest_rss         - Get the latest RSS items")
    print("    5. search_rss             - Search RSS items")
    print("    6. get_rss_feeds_status   - Get RSS feed status")
    print()
    print("    === Search tools ===")
    print("    7. search_news            - Search news (keyword/fuzzy/entity)")
    print("    8. find_related_news      - Find related news (including historical data)")
    print()
    print("    === Advanced analysis ===")
    print("    9. analyze_topic_trend      - Analyze topic trends, lifecycle, spikes, and outlook")
    print("    10. analyze_data_insights   - Analyze platforms, activity, and term co-occurrence")
    print("    11. analyze_sentiment       - Analyze news sentiment")
    print("    12. aggregate_news          - Aggregate and deduplicate cross-platform news")
    print("    13. compare_periods         - Compare periods (week over week/month over month)")
    print("    14. generate_summary_report - Generate daily/weekly summaries")
    print()
    print("    === Configuration and system management ===")
    print("    15. get_current_config      - Get the current configuration")
    print("    16. get_system_status       - Get system status")
    print("    17. check_version           - Compare installed and remote versions")
    print("    18. trigger_crawl           - Run a collection task")
    print()
    print("    === Storage synchronization tools ===")
    print("    19. sync_from_remote        - Download remote data to local storage")
    print("    20. get_storage_status      - Get storage configuration and status")
    print("    21. list_available_dates    - List dates with local/remote data")
    print()
    print("    === Article reading ===")
    print("    22. read_article            - Read one article as Markdown")
    print("    23. read_articles_batch     - Read several articles with rate limiting")
    print()
    print("    === Notification tools ===")
    print("    24. get_channel_format_guide  - Get channel formatting guidance")
    print("    25. get_notification_channels - Get configured notification channel status")
    print("    26. send_notification         - Send notifications with format conversion")
    print("=" * 60)
    print()

    sys.stdout = _banner_stdout

    # Run the requested transport
    if transport == 'stdio':
        mcp.run(transport='stdio')
    elif transport == 'http':
        # HTTP transport
        mcp.run(
            transport='http',
            host=host,
            port=port,
            path='/mcp'  # HTTP endpoint path
        )
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='TrendRadar MCP Server - Trending news aggregation tools',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
See README-Cherry-Studio.md for setup instructions.
        """
    )
    parser.add_argument(
        '--transport',
        choices=['stdio', 'http'],
        default='stdio',
        help='Transport: stdio (default) or http'
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='HTTP listen address (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=3333,
        help='HTTP listen port (default: 3333)'
    )
    parser.add_argument(
        '--project-root',
        help='Project root directory'
    )

    args = parser.parse_args()

    run_server(
        project_root=args.project_root,
        transport=args.transport,
        host=args.host,
        port=args.port
    )
