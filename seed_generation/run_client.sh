#!/bin/bash

# 1. Identify the script location
REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

# 2. Move into that directory
cd "$REPODIR"

SCRIPT="${1:-client_generate_from_seed.py}"
echo "📡 Starting Seed Generation Client → $SCRIPT"
./venv/bin/python3 "$SCRIPT"
