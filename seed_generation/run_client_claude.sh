#!/bin/bash
REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$REPODIR"
./run_client.sh client_generate_from_seed_claude.py
