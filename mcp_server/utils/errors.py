"""
自定义错误类

定义MCP Server使用的所有自定义异常类型。
"""

from typing import Optional, List, Callable


# ==================== 延迟加载支持的平台列表 ====================

_get_supported_platforms: Optional[Callable[[], List[str]]] = None


def _load_supported_platforms() -> List[str]:
    """延迟加载支持的平台列表"""
    global _get_supported_platforms
    if _get_supported_platforms is None:
        try:
            from .validators import get_supported_platforms
            _get_supported_platforms = get_supported_platforms
        except ImportError:
            # 降级：返回空列表
            return []
    return _get_supported_platforms()


class MCPError(Exception):
    """MCP工具错误基类"""

    def __init__(self, message: str, code: str = "MCP_ERROR", suggestion: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion

    def to_dict(self) -> dict:
        """转换为字典格式"""
        error_dict = {
            "code": self.code,
            "message": self.message
        }
        if self.suggestion:
            error_dict["suggestion"] = self.suggestion
        return error_dict


class DataNotFoundError(MCPError):
    """数据不存在错误"""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__(
            message=message,
            code="DATA_NOT_FOUND",
            suggestion=suggestion or "Check the date range or wait for the crawler to finish"
        )


class InvalidParameterError(MCPError):
    """参数无效错误"""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__(
            message=message,
            code="INVALID_PARAMETER",
            suggestion=suggestion or "Check the parameter format"
        )


class ConfigurationError(MCPError):
    """配置错误"""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__(
            message=message,
            code="CONFIGURATION_ERROR",
            suggestion=suggestion or "Check the configuration file"
        )


class PlatformNotSupportedError(MCPError):
    """平台不支持错误"""

    def __init__(self, platform: str):
        supported = _load_supported_platforms()
        suggestion = f"Supported platforms: {', '.join(supported)}" if supported else "Check the platform settings in config/config.yaml"
        super().__init__(
            message=f"Platform '{platform}' is not supported",
            code="PLATFORM_NOT_SUPPORTED",
            suggestion=suggestion
        )


class CrawlTaskError(MCPError):
    """爬取任务错误"""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__(
            message=message,
            code="CRAWL_TASK_ERROR",
            suggestion=suggestion or "Try again later or inspect the logs"
        )


class FileParseError(MCPError):
    """文件解析错误"""

    def __init__(self, file_path: str, reason: str):
        super().__init__(
            message=f"Parsing file {file_path} failed: {reason}",
            code="FILE_PARSE_ERROR",
            suggestion="Check that the file format is correct"
        )
