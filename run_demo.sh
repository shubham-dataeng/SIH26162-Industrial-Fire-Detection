#!/bin/bash
set -e

# SIH26162 — One-Command Demo Recovery Script (Phase 12)
# Clears any stuck process on port 5000 and starts Flask in foreground.

echo "==> Preparing SIH26162 Demo Environment..."

# 1. Clear any process occupying port 5000 (portable fallback)
echo "==> Clearing port 5000..."
fuser -k 5000/tcp 2>/dev/null || true

# 2. Check and activate virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f "./venv/bin/activate" ]; then
    echo "ERROR: Virtual environment not found at ./venv"
    echo "Please set up the environment first by running:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source ./venv/bin/activate

# 3. Launch Flask app in foreground
echo "==> Starting SIH26162 Industrial Fire Detection Dashboard on http://127.0.0.1:5000 ..."
flask --app backend/app run
