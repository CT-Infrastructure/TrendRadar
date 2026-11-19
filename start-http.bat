@echo off
chcp 65001 >nul

echo ============================================================
echo   TrendRadar MCP Server (HTTP mode)
echo ============================================================
echo.

REM 检查虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo ❌ [ERROR] Virtual environment not found
    echo Run setup-windows.bat or setup-windows-en.bat to set up the project first
    echo.
    pause
    exit /b 1
)

echo [Mode] HTTP (for remote access)
echo [URL] http://localhost:3333/mcp
echo [Tip] Press Ctrl+C to stop the server
echo.

uv run python -m mcp_server.server --transport http --host 0.0.0.0 --port 3333

pause
