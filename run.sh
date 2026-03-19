#!/usr/bin/env bash
# Launch chatgpt-stats using the local venv.

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "First-time setup needed. Running setup..."
    bash setup.sh
fi

.venv/bin/python chatgpt_stats.py
