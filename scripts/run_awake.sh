#!/usr/bin/env bash
# run_awake.sh — run a batch job with sleep/idle/lid-close inhibited only
# for as long as the job is actually running. The inhibitor releases
# itself automatically the moment the job exits (success or failure) —
# no need to remember to Ctrl+C a separate terminal afterward.
#
# Usage:
#   scripts/run_awake.sh -- <command> [args...]
#
# Example:
#   scripts/run_awake.sh -- python3 -m seed_generation.generate_from_seed \
#     --seed seed_generation/2027/seeds/PT/seed_pt_ARC_missing_162.json \
#     --lang pt --version ARC --provider ollama --model gemma4:26b \
#     --output-dir seed_generation/data/output/pt --resume
#
# --shutdown: power off the machine when the job finishes, instead of
# just returning to normal sleep behavior.

set -euo pipefail

SHUTDOWN=0
if [[ "${1:-}" == "--shutdown" ]]; then
  SHUTDOWN=1
  shift
fi

if [[ "${1:-}" != "--" ]]; then
  echo "Usage: $0 [--shutdown] -- <command> [args...]" >&2
  exit 1
fi
shift

if [[ $# -eq 0 ]]; then
  echo "No command given after --" >&2
  exit 1
fi

echo "Running under sleep/idle/lid inhibit: $*"
systemd-inhibit --what=sleep:idle:handle-lid-switch \
  --why="batch job: $*" --mode=block "$@"
status=$?

echo "Job exited with status $status — inhibit released, normal power behavior restored."

if [[ $SHUTDOWN -eq 1 ]]; then
  echo "Shutting down in 60s (Ctrl+C to cancel)..."
  sleep 60
  systemctl poweroff
fi

exit $status
