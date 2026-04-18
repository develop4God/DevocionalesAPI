#!/bin/bash
# run_batch_claude.sh
# ───────────────────
# Launcher for the Anthropic Message Batches devotional pipeline.
#
# This 2-step workflow replaces the server/client approach:
#   Step 1 (submit)  — batch_claude_submit.py
#     Reads seed JSON → submits ALL entries as one Anthropic batch → saves state file
#
#   Step 2 (collect) — batch_claude_collect.py
#     Polls batch until done → downloads results → builds devotionals → saves JSON
#
# Usage:
#   ./run_batch_claude.sh submit    # interactive (Tkinter)
#   ./run_batch_claude.sh collect   # auto-finds latest state file
#   ./run_batch_claude.sh           # shows this help

REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$REPODIR"

# Activate venv if present (same logic as run_client.sh)
VENV="$REPODIR/.venv"
if [[ -f "$VENV/bin/activate" ]]; then
    source "$VENV/bin/activate"
fi

STEP="${1:-help}"

case "$STEP" in
    submit)
        echo "==> Submitting batch to Anthropic..."
        python3 batch_claude_submit.py "${@:2}"
        ;;
    collect)
        echo "==> Collecting batch results from Anthropic..."
        python3 batch_claude_collect.py "${@:2}"
        ;;
    *)
        echo ""
        echo "Anthropic Message Batches — Devotional Generator"
        echo "─────────────────────────────────────────────────"
        echo ""
        echo "  Step 1 — Submit (interactive):"
        echo "    ./run_batch_claude.sh submit"
        echo ""
        echo "  Step 1 — Submit (CLI):"
        echo "    ./run_batch_claude.sh submit \\"
        echo "      --seed seeds/2025/seed_ar_NAV_2025.json \\"
        echo "      --lang ar --version NAV \\"
        echo "      --output 2025/yearly_devotionals/AR \\"
        echo "      [--model claude-haiku-4-5-20251001] \\"
        echo "      [--start-date 2025-06-01] \\"
        echo "      [--limit 10]"
        echo ""
        echo "  Step 2 — Collect (auto-finds latest state file):"
        echo "    ./run_batch_claude.sh collect"
        echo ""
        echo "  Step 2 — Collect (specific state file):"
        echo "    ./run_batch_claude.sh collect --state batch_state_ar_NAV_<ts>.json"
        echo ""
        echo "  Step 2 — Collect with faster polling (30s):"
        echo "    ./run_batch_claude.sh collect --poll-interval 30"
        echo ""
        echo "Models (cheapest → best quality):"
        echo "  claude-haiku-4-5-20251001    — default, ~\$0.25/\$1.25 per MTok at batch prices"
        echo "  claude-sonnet-4-5-20251001   — better quality, ~\$1.50/\$7.50 per MTok"
        echo "  claude-opus-4-5-20251001     — highest quality, ~\$7.50/\$37.50 per MTok"
        echo ""
        echo "Note: ANTHROPIC_API_KEY must be set in .env or environment."
        echo ""
        ;;
esac
