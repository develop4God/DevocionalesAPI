#!/bin/bash

# 1. Identify the script location
REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

# 2. Move into that directory
cd "$REPODIR"

echo "📡 Starting Seed Generation Client..."

# 3. Run the client script using the venv python
./venv/bin/python3 client_generate_from_seed.py
