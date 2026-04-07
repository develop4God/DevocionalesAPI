#!/bin/bash

# Get the directory where the script is located
REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

echo "🚀 Starting Seed API Server on port 50003..."

# Run uvicorn using the absolute path to the venv python module
# This removes the need to 'activate' the venv first
$REPODIR/venv/bin/python3 -m uvicorn API_Server_Seed:app \
    --host 0.0.0.0 \
    --port 50003 \
    --reload
