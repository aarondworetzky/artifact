#!/usr/bin/env bash
# Run this once to set up the app.
# Works on macOS and Linux.

set -e
cd "$(dirname "$0")"

echo "Setting up chatgpt-stats..."

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install it from https://python.org"
    exit 1
fi

# Create venv inside the app folder
python3 -m venv .venv

# Install dependencies into it
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# Create the collected folder if it doesn't exist
mkdir -p collected

# Pre-warm the fastembed model so it's cached locally in ./models/
# (skips the download on first run — also makes the app shareable as a zip)
echo ""
echo "Downloading semantic embedding model (~80MB, one-time)..."
.venv/bin/python3 - <<'PYWARM'
from fastembed import TextEmbedding
from pathlib import Path
models_dir = Path(__file__).parent / "models" if False else Path("models")
models_dir.mkdir(exist_ok=True)
# Trigger download by instantiating — fastembed stores to cache_dir
TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(models_dir))
print("  ✓ Model cached in ./models/")
PYWARM

echo ""
echo "✓ Setup complete!"
echo ""
echo "Next step: drop your ChatGPT export JSON files into the 'collected/' folder."
echo "  (Go to chatgpt.com → Settings → Data Controls → Export Data,"
echo "   then copy the conversations-*.json files here.)"
echo ""
echo "Then run:"
echo "  ./run.sh"
