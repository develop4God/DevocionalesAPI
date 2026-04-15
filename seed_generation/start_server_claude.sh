#!/bin/bash

# 1. Identify the script location
REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

# 2. MOVE into that directory (Crucial for Uvicorn to find your .py file)
cd "$REPODIR"

echo "🚀 Starting Claude-powered API Server on port 50004..."

# 3. Run using the venv python module
./venv/bin/python3 -m uvicorn API_Server_Seed_Claude:app \
    --host 0.0.0.0 \
    --port 50004 \
    --reload
