#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${BOLD}╔════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  TrendRadar MCP Setup (Mac)        ║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════╝${NC}"
echo ""

# 获取项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1

echo -e "📍 Project directory: ${BLUE}${PROJECT_ROOT}${NC}"
echo ""

# 检查 UV 是否已安装
if ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}[1/3] 🔧 UV is not installed; installing automatically...${NC}"
    echo "Tip: UV is a fast Python package manager. Install it once."
    echo ""
    curl -LsSf https://astral.sh/uv/install.sh | sh

    echo ""
    echo "Refreshing the PATH environment variable..."
    echo ""

    # 添加 UV 到 PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    # 验证 UV 是否真正可用
    if ! command -v uv &> /dev/null; then
        echo -e "${RED}❌ [ERROR] UV installation failed${NC}"
        echo ""
        echo "Possible causes:"
        echo "  1. Network problem prevented downloading the installer"
        echo "  2. Insufficient permissions for the installation path"
        echo "  3. The installation script failed"
        echo ""
        echo "Suggested steps:"
        echo "  1. Check your network connection"
        echo "  2. Manual installation: https://docs.astral.sh/uv/getting-started/installation/"
        echo "  3. Or run: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    echo -e "${GREEN}✅ [SUCCESS] UV installed${NC}"
    echo -e "${YELLOW}⚠️  Run this script again to continue${NC}"
    exit 0
else
    echo -e "${GREEN}[1/3] ✅ UV is installed${NC}"
    uv --version
fi

echo ""
echo "[2/3] 📦 Installing project dependencies..."
echo "Tip: This may take 1-2 minutes."
echo ""

# 创建虚拟环境并安装依赖
uv sync

if [ $? -ne 0 ]; then
    echo ""
    echo -e "${RED}❌ [ERROR] Dependency installation failed${NC}"
    echo "Check your network connection and try again"
    exit 1
fi

echo ""
echo -e "${GREEN}[3/3] ✅ Checking configuration files...${NC}"
echo ""

# 检查配置文件
if [ ! -f "config/config.yaml" ]; then
    echo -e "${YELLOW}⚠️  [WARNING] Configuration file not found: config/config.yaml${NC}"
    echo "Make sure the configuration file exists"
    echo ""
fi

# 添加执行权限
chmod +x start-http.sh 2>/dev/null || true

# 获取 UV 路径
UV_PATH=$(which uv)

echo ""
echo -e "${BOLD}╔════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           Setup complete!                   ║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════╝${NC}"
echo ""
echo "📋 Next steps:"
echo ""
echo "  1️⃣  Open Cherry Studio"
echo "  2️⃣  Go to Settings > MCP Servers > Add Server"
echo "  3️⃣  Enter this configuration:"
echo ""
echo "      Name: TrendRadar"
echo "      Description: Trending news aggregator"
echo "      Type: STDIO"
echo -e "      Command: ${BLUE}${UV_PATH}${NC}"
echo "      Arguments (one per line):"
echo -e "        ${BLUE}--directory${NC}"
echo -e "        ${BLUE}${PROJECT_ROOT}${NC}"
echo -e "        ${BLUE}run${NC}"
echo -e "        ${BLUE}python${NC}"
echo -e "        ${BLUE}-m${NC}"
echo -e "        ${BLUE}mcp_server.server${NC}"
echo ""
echo "  4️⃣  Save and enable the MCP server"
echo ""
echo "📖 See README-Cherry-Studio.md for instructions. Keep this window open to copy the arguments."
echo ""
