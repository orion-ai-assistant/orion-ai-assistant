#!/bin/bash

# Color codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

MODE=${1:-docker}

echo -e "${CYAN}==========================================${NC}"
echo -e "${CYAN}   Orion AI Assistant Native Installer    ${NC}"
echo -e "${CYAN}==========================================${NC}"
echo -e "Installation Mode: ${YELLOW}${MODE}${NC}"

if ! command -v git &> /dev/null; then
    echo -e "${RED}ERROR: 'git' is not installed!${NC}"
    exit 1
fi
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo -e "${RED}ERROR: 'python' is not installed!${NC}"
    exit 1
fi

PYTHON_CMD="python"
if command -v python3 &> /dev/null; then PYTHON_CMD="python3"; fi

TARGET_DIR="$HOME/.local/share/OrionAIAssistant"
REPO_URL="https://github.com/orion-ai-assistant/orion-ai-assistant.git"

echo -e "\n[1/2] Setting up Orion AI Assistant directory..."
if [ ! -d "$TARGET_DIR" ]; then
    echo "Cloning fresh copy from GitHub..."
    git clone "$REPO_URL" "$TARGET_DIR" || exit 1
else
    echo "Directory exists, pulling latest changes..."
    cd "$TARGET_DIR" || exit 1
    git fetch origin main
    git reset --hard origin/main
fi

cd "$TARGET_DIR" || exit 1

echo -e "\n[2/3] Handing over to Unified Python Setup..."
$PYTHON_CMD orion.py setup "$MODE"

if [ $? -ne 0 ]; then
    echo -e "\n${RED}[ERROR] Setup failed.${NC}"
    exit 1
fi

echo -e "\n[3/3] Starting Orion Installer..."
$PYTHON_CMD orion.py installer
