"""
参数验证工具

提供统一的参数验证功能。
支持 MCP 客户端将参数序列化为字符串的情况。
"""

from datetime import datetime
from typing import List, Optional, Union
import os
import json
import yaml
import ast

from .errors import InvalidParameterError
from .date_parser import DateParser


# ==================== 辅助函数：处理字符串序列化 ====================

def _parse_string_to_list(value: str) -> List[str]:
    """
    将字符串解析为列表

    支持格式：
    - JSON 数组: '["zhihu", "weibo"]'
    - Python 列表字符串: "['zhihu', 'weibo']"
    - 逗号分隔: "zhihu, weibo" 或 "zhihu,weibo"

    Args:
        value: 字符串值

    Returns:
        解析后的列表

    Raises:
        InvalidParameterError: 解析失败
    """
    value = value.strip()

    if not value:
        return []

    # 尝试 JSON 解析: '["zhihu", "weibo"]'
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        # 如果解析结果不是列表，继续尝试其他方式
    except json.JSONDecodeError:
        pass

    # 尝试 Python 字面量解析: "['zhihu', 'weibo']"
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        if isinstance(parsed, str):
            # 单个字符串，包装成列表
            return [parsed]
    except (ValueError, SyntaxError):
        pass

    # 尝试逗号分隔: "zhihu, weibo" 或 "zhihu,weibo"
    if ',' in value:
        items = [item.strip() for item in value.split(',')]
        return [item for item in items if item]

    # 单个值
    return [value]


def _parse_string_to_int(value: str, param_name: str = "parameter") -> int:
    """
    将字符串解析为整数

    Args:
        value: 字符串值
        param_name: 参数名（用于错误消息）

    Returns:
        解析后的整数

    Raises:
        InvalidParameterError: 解析失败
    """
    value = value.strip()

    try:
        # 尝试直接转换
        return int(value)
    except ValueError:
        pass

    # 尝试解析浮点数后取整
    try:
        return int(float(value))
    except ValueError:
        raise InvalidParameterError(
            f"{param_name} must be an integer; could not parse: {value}",
            suggestion=f"Provide a valid integer, such as 10, 50, or 100"
        )


def _parse_string_to_float(value: str, param_name: str = "parameter") -> float:
    """
    将字符串解析为浮点数

    Args:
        value: 字符串值
        param_name: 参数名（用于错误消息）

    Returns:
        解析后的浮点数

    Raises:
        InvalidParameterError: 解析失败
    """
    value = value.strip()

    try:
        return float(value)
    except ValueError:
        raise InvalidParameterError(
            f"{param_name} must be numeric; could not parse: {value}",
            suggestion=f"Provide a valid number, such as 0.6 or 3.0"
        )


def _parse_string_to_bool(value: str) -> bool:
    """
    将字符串解析为布尔值

    Args:
        value: 字符串值

    Returns:
        解析后的布尔值
    """
    value = value.strip().lower()

    if value in ('true', '1', 'yes', 'on'):
        return True
    elif value in ('false', '0', 'no', 'off', ''):
        return False
    else:
        # 默认非空字符串为 True
        return bool(value)


# 平台列表 mtime 缓存（避免每次 MCP 调用都重新读取 config.yaml）
_platforms_cache: Optional[List[str]] = None
_platforms_config_mtime: float = 0.0
_platforms_config_path: Optional[str] = None


def get_supported_platforms() -> List[str]:
    """
    从 config.yaml 动态获取支持的平台列表（带 mtime 缓存）

    仅当 config.yaml 被修改时才重新读取，避免每次 MCP 调用的重复 IO。

    Returns:
        平台ID列表

    Note:
        - 读取失败时返回空列表，允许所有平台通过（降级策略）
        - 平台列表来自 config/config.yaml 中的 platforms 配置
    """
    global _platforms_cache, _platforms_config_mtime, _platforms_config_path

    try:
        if _platforms_config_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            _platforms_config_path = os.path.normpath(
                os.path.join(current_dir, "..", "..", "config", "config.yaml")
            )

        current_mtime = os.path.getmtime(_platforms_config_path)

        if _platforms_cache is not None and current_mtime == _platforms_config_mtime:
            return _platforms_cache

        with open(_platforms_config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            platforms_config = config.get('platforms', {})
            sources = platforms_config.get('sources', [])
            _platforms_cache = [p['id'] for p in sources if 'id' in p and p.get('enabled', True)]
            _platforms_config_mtime = current_mtime
            return _platforms_cache
    except Exception as e:
        print(f"Warning: Could not load platform configuration: {e}")
        return []


def validate_platforms(platforms: Optional[Union[List[str], str]]) -> List[str]:
    """
    验证平台列表

    Args:
        platforms: 平台ID列表或字符串，None表示使用 config.yaml 中配置的所有平台
                   支持多种格式：
                   - None: 使用默认平台
                   - ["zhihu", "weibo"]: JSON 数组
                   - '["zhihu", "weibo"]': JSON 数组字符串
                   - "['zhihu', 'weibo']": Python 列表字符串
                   - "zhihu, weibo": 逗号分隔字符串
                   - "zhihu": 单个平台字符串

    Returns:
        验证后的平台列表

    Raises:
        InvalidParameterError: 平台不支持

    Note:
        - platforms=None 时，返回 config.yaml 中配置的平台列表
        - 会验证平台ID是否在 config.yaml 的 platforms 配置中
        - 配置加载失败时，允许所有平台通过（降级策略）
    """
    supported_platforms = get_supported_platforms()

    if platforms is None:
        # 返回配置文件中的平台列表（用户的默认配置）
        return supported_platforms if supported_platforms else []

    # 支持字符串形式的列表输入（某些 MCP 客户端会将 JSON 数组序列化为字符串）
    if isinstance(platforms, str):
        platforms = _parse_string_to_list(platforms)
        if not platforms:
            # 空字符串或解析后为空，使用默认平台
            return supported_platforms if supported_platforms else []

    if not isinstance(platforms, list):
        raise InvalidParameterError("platforms must be a list")

    if not platforms:
        # 空列表时，返回配置文件中的平台列表
        return supported_platforms if supported_platforms else []

    # 如果配置加载失败（supported_platforms为空），允许All platforms通过
    if not supported_platforms:
        print("Warning: Platform configuration is unavailable; skipping platform validation")
        return platforms

    # 验证每个平台是否在配置中
    invalid_platforms = [p for p in platforms if p not in supported_platforms]
    if invalid_platforms:
        raise InvalidParameterError(
            f"Unsupported platform: {', '.join(invalid_platforms)}",
            suggestion=f"Supported platforms (from config.yaml): {', '.join(supported_platforms)}"
        )

    return platforms


def validate_limit(limit: Optional[Union[int, str]], default: int = 20, max_limit: int = 1000) -> int:
    """
    验证数量限制参数

    Args:
        limit: 限制数量（整数或字符串）
        default: 默认值
        max_limit: 最大限制

    Returns:
        验证后的限制值

    Raises:
        InvalidParameterError: 参数无效
    """
    if limit is None:
        return default

    # 支持字符串形式的整数（某些 MCP 客户端会将数字序列化为字符串）
    if isinstance(limit, str):
        limit = _parse_string_to_int(limit, "limit")

    if not isinstance(limit, int):
        raise InvalidParameterError("limit must be an integer")

    if limit <= 0:
        raise InvalidParameterError("limit must be greater than 0")

    if limit > max_limit:
        raise InvalidParameterError(
            f"limit cannot exceed {max_limit}",
            suggestion=f"Use pagination or a lower limit"
        )

    return limit


def validate_date(date_str: str) -> datetime:
    """
    验证日期格式

    Args:
        date_str: 日期字符串 (YYYY-MM-DD)

    Returns:
        datetime对象

    Raises:
        InvalidParameterError: 日期格式错误
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise InvalidParameterError(
            f"Invalid date format: {date_str}",
            suggestion="Use YYYY-MM-DD format, for example: 2025-10-11"
        )


def normalize_date_range(date_range: Optional[Union[dict, str]]) -> Optional[Union[dict, str]]:
    """
    规范化 date_range 参数

    某些 MCP 客户端（特别是 HTTP 方式）会将 JSON 对象序列化为字符串传入。
    此函数尝试将 JSON 字符串解析为 dict，如果不是 JSON 格式则保持原样。

    Args:
        date_range: 日期范围，可能是:
            - dict: {"start": "2025-01-01", "end": "2025-01-07"}
            - JSON 字符串: '{"start": "2025-01-01", "end": "2025-01-07"}'
            - 普通字符串: "今天", "昨天", "2025-01-01"
            - None

    Returns:
        规范化后的 date_range（dict 或普通字符串）

    Examples:
        >>> normalize_date_range('{"start":"2025-01-01","end":"2025-01-07"}')
        {"start": "2025-01-01", "end": "2025-01-07"}
        >>> normalize_date_range("今天")
        "今天"
        >>> normalize_date_range({"start": "2025-01-01", "end": "2025-01-07"})
        {"start": "2025-01-01", "end": "2025-01-07"}
    """
    if date_range is None:
        return None

    # 如果已经是 dict，直接返回
    if isinstance(date_range, dict):
        return date_range

    # 如果是字符串，尝试解析为 JSON
    if isinstance(date_range, str):
        # 检查是否看起来像 JSON 对象
        stripped = date_range.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass  # 解析失败，当作普通字符串处理

    return date_range


def validate_date_range(date_range: Optional[Union[dict, str]]) -> Optional[tuple]:
    """
    验证日期范围

    Args:
        date_range: 日期范围，支持多种格式：
            - dict: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
            - JSON 字符串: '{"start": "2025-01-01", "end": "2025-01-07"}'
            - 单日字符串: "2025-01-01"（自动转为同一天的范围）
            - 自然语言: "今天", "昨天", "本周", "最近7天" 等

    Returns:
        (start_date, end_date) 元组，或 None

    Raises:
        InvalidParameterError: 日期范围无效
    """
    if date_range is None:
        return None

    # 支持字符串形式的输入
    if isinstance(date_range, str):
        stripped = date_range.strip()

        # 1. 检查是否是 JSON 对象格式
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                date_range = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise InvalidParameterError(
                    f"Could not parse date_range JSON: {e}",
                    suggestion='Use valid JSON: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}'
                )
        # 2. 检查是否是单日字符串格式 YYYY-MM-DD
        elif len(stripped) == 10 and stripped[4] == '-' and stripped[7] == '-':
            try:
                single_date = datetime.strptime(stripped, "%Y-%m-%d")
                return (single_date, single_date)
            except ValueError:
                raise InvalidParameterError(
                    f"Invalid date format: {stripped}",
                    suggestion="Use YYYY-MM-DD format, for example: 2025-10-11"
                )
        # 3. 尝试自然语言解析
        else:
            try:
                result = DateParser.resolve_date_range_expression(stripped)
                if result.get("success"):
                    dr = result["date_range"]
                    start_date = datetime.strptime(dr["start"], "%Y-%m-%d")
                    end_date = datetime.strptime(dr["end"], "%Y-%m-%d")
                    return (start_date, end_date)
                else:
                    raise InvalidParameterError(
                        f"Unrecognized date expression: {stripped}",
                        suggestion='Supported formats: YYYY-MM-DD, {"start": "...", "end": "..."}, or natural language (today, this week, last 7 days)'
                    )
            except InvalidParameterError:
                raise
            except Exception:
                raise InvalidParameterError(
                    f"Date parsing failed: {stripped}",
                    suggestion='Supported formats: YYYY-MM-DD, {"start": "...", "end": "..."}, or natural language (today, this week, last 7 days)'
                )

    if not isinstance(date_range, dict):
        raise InvalidParameterError(
            "date_range must be a dictionary, a date string, or a valid JSON string",
            suggestion='For example: {"start": "2025-10-01", "end": "2025-10-11"} or "2025-10-01"'
        )

    start_str = date_range.get("start")
    end_str = date_range.get("end")

    if not start_str or not end_str:
        raise InvalidParameterError(
            "date_range must contain start and end fields",
            suggestion='For example: {"start": "2025-10-01", "end": "2025-10-11"}'
        )

    start_date = validate_date(start_str)
    end_date = validate_date(end_str)

    if start_date > end_date:
        raise InvalidParameterError(
            "The start date cannot be after the end date",
            suggestion=f"start: {start_str}, end: {end_str}"
        )

    # 检查日期是否在未来
    today = datetime.now().date()
    if start_date.date() > today or end_date.date() > today:
        # 获取可用日期范围提示
        try:
            from ..services.data_service import DataService
            data_service = DataService()
            earliest, latest = data_service.get_available_date_range()

            if earliest and latest:
                available_range = f"{earliest.strftime('%Y-%m-%d')} to {latest.strftime('%Y-%m-%d')}"
            else:
                available_range = "No data available"
        except Exception:
            available_range = "Unknown (check the output directory)"

        future_dates = []
        if start_date.date() > today:
            future_dates.append(start_str)
        if end_date.date() > today and end_str != start_str:
            future_dates.append(end_str)

        raise InvalidParameterError(
            f"Future dates cannot be queried: {', '.join(future_dates)} (current date: {today.strftime('%Y-%m-%d')})",
            suggestion=f"Available date range: {available_range}"
        )

    return (start_date, end_date)


def validate_keyword(keyword: str) -> str:
    """
    验证关键词

    Args:
        keyword: 搜索关键词

    Returns:
        处理后的关键词

    Raises:
        InvalidParameterError: 关键词无效
    """
    if not keyword:
        raise InvalidParameterError("keyword cannot be empty")

    if not isinstance(keyword, str):
        raise InvalidParameterError("keyword must be a string")

    keyword = keyword.strip()

    if not keyword:
        raise InvalidParameterError("keyword cannot contain only whitespace")

    if len(keyword) > 100:
        raise InvalidParameterError(
            "keyword cannot exceed 100 characters",
            suggestion="Use a shorter keyword"
        )

    return keyword


def validate_top_n(top_n: Optional[Union[int, str]], default: int = 10) -> int:
    """
    验证TOP N参数

    Args:
        top_n: TOP N数量（整数或字符串）
        default: 默认值

    Returns:
        验证后的值

    Raises:
        InvalidParameterError: 参数无效
    """
    return validate_limit(top_n, default=default, max_limit=100)


def validate_mode(mode: Optional[str], valid_modes: List[str], default: str) -> str:
    """
    验证模式参数

    Args:
        mode: 模式字符串
        valid_modes: 有效模式列表
        default: 默认模式

    Returns:
        验证后的模式

    Raises:
        InvalidParameterError: 模式无效
    """
    if mode is None:
        return default

    if not isinstance(mode, str):
        raise InvalidParameterError("mode must be a string")

    if mode not in valid_modes:
        raise InvalidParameterError(
            f"Invalid mode: {mode}",
            suggestion=f"Supported modes: {', '.join(valid_modes)}"
        )

    return mode


def validate_config_section(section: Optional[str]) -> str:
    """
    验证配置节参数

    Args:
        section: 配置节名称

    Returns:
        验证后的配置节

    Raises:
        InvalidParameterError: 配置节无效
    """
    valid_sections = ["all", "crawler", "push", "keywords", "weights"]
    return validate_mode(section, valid_sections, "all")


def validate_threshold(
    threshold: Optional[Union[float, int, str]],
    default: float = 0.6,
    min_value: float = 0.0,
    max_value: float = 1.0,
    param_name: str = "threshold"
) -> float:
    """
    验证阈值参数（浮点数）

    Args:
        threshold: 阈值（浮点数、整数或字符串）
        default: 默认值
        min_value: 最小值
        max_value: 最大值
        param_name: 参数名（用于错误消息）

    Returns:
        验证后的阈值

    Raises:
        InvalidParameterError: 参数无效
    """
    if threshold is None:
        return default

    # 支持字符串形式的数字（某些 MCP 客户端会将数字序列化为字符串）
    if isinstance(threshold, str):
        threshold = _parse_string_to_float(threshold, param_name)

    # 整数转浮点数
    if isinstance(threshold, int):
        threshold = float(threshold)

    if not isinstance(threshold, float):
        raise InvalidParameterError(
            f"{param_name} must be numeric",
            suggestion=f"Provide a number between {min_value} and {max_value}"
        )

    if threshold < min_value or threshold > max_value:
        raise InvalidParameterError(
            f"{param_name} must be between {min_value} and {max_value}; current value: {threshold}",
            suggestion=f"Recommended value: {default}"
        )

    return threshold


def validate_date_query(
    date_query: str,
    allow_future: bool = False,
    max_days_ago: int = 365
) -> datetime:
    """
    验证并解析日期查询字符串

    Args:
        date_query: 日期查询字符串
        allow_future: 是否允许未来日期
        max_days_ago: 允许查询的最大天数

    Returns:
        解析后的datetime对象

    Raises:
        InvalidParameterError: 日期查询无效

    Examples:
        >>> validate_date_query("昨天")
        datetime(2025, 10, 10)
        >>> validate_date_query("2025-10-10")
        datetime(2025, 10, 10)
    """
    if not date_query:
        raise InvalidParameterError(
            "The date query cannot be empty",
            suggestion="Provide a date query, such as today, yesterday, or 2025-10-10"
        )

    # 使用DateParser解析日期
    parsed_date = DateParser.parse_date_query(date_query)

    # 验证日期不在未来
    if not allow_future:
        DateParser.validate_date_not_future(parsed_date)

    # 验证日期不太久远
    DateParser.validate_date_not_too_old(parsed_date, max_days=max_days_ago)

    return parsed_date

