#!/usr/bin/env bash
# ⚡ zero-copy-agent 1-Click Startup Script (Linux / macOS / WSL)
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "======================================================="
echo "  ⚡ zero-copy-agent Launcher"
echo "======================================================="

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

# Create virtual environment if missing
if [ ! -d "venv" ]; then
    echo "[1/3] Creating virtual environment (venv)..."
    python3 -m venv venv
fi

echo "[2/3] Activating virtual environment..."
source venv/bin/activate

# Install / update requirements
echo "[3/3] Checking dependencies and browser engine..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
python3 -m playwright install chromium 2>/dev/null || true

# Check optional arguments
if [ "$1" == "--test" ]; then
    echo "Running quick integration test..."
    python3 test_client.py
    exit 0
fi

if [ "$1" == "--login" ] || [ "$1" == "--auth" ]; then
    PROVIDER="${2:-gemini}"
    echo "Launching interactive browser authentication for $PROVIDER..."
    python3 -m core.auth --provider "$PROVIDER"
    exit 0
fi

echo ""
echo "✅ Environment ready! Starting zero-copy-agent gateway..."
echo ""

exec python3 main.py
