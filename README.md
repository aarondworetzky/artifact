# artifact.

> Your ChatGPT history is an unintentional journal. You didn't know you were writing it. That's what makes it honest.

**Everyone uses ChatGPT. Nobody ever goes back.**
artifact. turns your graveyard into a library.

---

## What it does

artifact. reads your ChatGPT export and gives your history back to you in plain English.

- **Your Story** — what you've been thinking about lately, and a random insight from your past worth revisiting
- **Best Ideas** — your top 3 most personally significant idea threads, ranked by how often you've returned to them and how distinctively yours they are
- **Ideas** — semantic clustering of recurring thoughts across all your conversations
- **Search** — find anything you've already worked through, ranked by similarity
- **Patterns** — 11 deep-dive screens: word frequency, time-of-day, model usage, conversation depth, and more
- **Profile** — auto-derived personality mirror: your archetype, domains, rhythms, and thinking style
- **Export** — Obsidian markdown vault with wikilinks between related conversations (3 voice modes: Your Voice / Neutral / Raw)

**100% local. No cloud. No API key required.** Runs entirely on your machine.

---

## Setup

**Requires:** Python 3.10+ · macOS or Linux

```bash
git clone https://github.com/YOUR_USERNAME/artifact.git
cd artifact.
./setup.sh
```

`setup.sh` creates a `.venv`, installs dependencies, and pre-warms the embedding model (~64MB, one-time download).

---

## Usage

**Step 1 — Export your ChatGPT data**

In ChatGPT: Settings → Data controls → Export data. You'll get a zip. Inside is `conversations.json`.

**Step 2 — Drop it in**

```bash
mkdir -p collected
cp /path/to/conversations.json collected/
```

You can drop multiple export files in `collected/` — artifact. merges them automatically.

**Step 3 — Run**

```bash
./run.sh
```

Arrow keys to navigate. Enter to select.

---

## Dependencies

```
rich >= 13.0       # Terminal UI
questionary >= 2.0 # Arrow-key menus
fastembed >= 0.3   # Local semantic embeddings (BAAI/bge-small-en-v1.5, ~64MB ONNX)
```

No PyTorch. No cloud calls. No data leaves your machine.

---

## Signal Score

On first load, artifact. shows a Signal Score (0–100) rating the quality of your data across 6 signals: conversation density, personal depth, question ratio, message length, recurrence, and time span. This tells you upfront how much signal your history has before you dig in.

---

## Privacy

Your data never leaves your machine. The embedding model runs locally via ONNX (no PyTorch dependency). The only network call is the one-time model download on first run.

---

## Built by

Aaron & Eli D.

---

## Roadmap

- [ ] Annual Review — Spotify Wrapped for your ChatGPT year
- [ ] Claude API integration — rewrite Obsidian exports in your actual writing style
- [ ] Web upload — drag your export zip, get insights in 60 seconds (no install)
