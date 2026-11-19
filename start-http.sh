#!/bin/bash

echo "╔════════════════════════════════════════╗"
echo "║  TrendRadar MCP Server (HTTP mode)    ║"
echo "╚════════════════════════════════════════╝"
echo ""

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "❌ [ERROR] Virtual environment not found"
    echo "Run ./setup-mac.sh to set up the project first"
    echo ""
    exit 1
fi

echo "[Mode] HTTP (for remote access)"
echo "[URL] http://localhost:3333/mcp"
echo "[Tip] Press Ctrl+C to stop the server"
echo ""

uv run python -m mcp_server.server --transport http --host 0.0.0.0 --port 3333
