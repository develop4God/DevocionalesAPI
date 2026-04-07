#!/bin/bash
# Always use the correct venv for seed_generation
VENV=/home/develop4god/python/DevocionalesAPI/seed_generation/venv
SCRIPT_DIR=/home/develop4god/python/DevocionalesAPI/seed_generation

echo "Starting API_Server_Seed with correct venv..."
cd "$SCRIPT_DIR"
"$VENV/bin/python" API_Server_Seed.py
