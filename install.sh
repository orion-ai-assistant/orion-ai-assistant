#!/bin/bash

# Color codes
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}==========================================${NC}"
echo -e "${CYAN}   Orion AI Assistant Native Installer    ${NC}"
echo -e "${CYAN}==========================================${NC}"
echo ""
echo -e "${RED}ERROR: Installation is currently Windows-only!${NC}"
echo -e "Orion AI Assistant docker setup is optimized for Windows PowerShell."
echo -e "macOS / Linux support is under active development and will be released in the future."
echo ""
echo -e "Please install and run Orion AI Assistant on a ${YELLOW}Windows 10/11${NC} system with ${YELLOW}Docker Desktop${NC}."
echo -e "You can run the installation script in PowerShell using:"
echo -e "${CYAN}powershell -c \"iex(irm raw.github.com/orion-ai-assistant/orion-ai-assistant/main/install.ps1)\"${NC}"
echo ""
echo -e "${CYAN}==========================================${NC}"

exit 1
