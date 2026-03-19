# artifact. — Project Brief

## What This Is

artifact. is a Python CLI app that turns your ChatGPT export JSON data into a
personal intelligence tool. It reads your conversation history, finds patterns,
surfaces recurring ideas, and gives your history back to you in plain English.

**The core insight:**
Your ChatGPT history is an unintentional journal. You didn't know you were
writing it. That's what makes it honest.

**The gap we fill:**
Everyone uses ChatGPT. Nobody ever goes back.
artifact. turns your graveyard into a library.

**Designed by Aaron & Eli D.**

---

## Current State

### Built ✅
- Data parsing + stats engine (`build_stats`)
- First-run welcome screen (artifact. ethos, shown once)
- Signal Score — data quality rating 0–100 across 5 signals
- Ideas screen — semantic clustering of recurring thoughts (fastembed)
- Search screen — semantic search with cosine similarity scoring
- Patterns sub-menu — 11 deep-dive metric screens
- Topics/Categories — semantic k-means clustering via fastembed
- Settings — timezone, replay welcome, signal score
- Embedding cache — shared across Ideas, Search, Topics (`_EMBED_CACHE`)
- gstack installed and configured

### Pending 🔧
- **Profile** — auto-derived personality mirror (next up)
- **Export** — Obsidian markdown with 3 voice modes (Your Voice / Neutral / Raw)
- **Your Story** — narrative home screen shown before menu each launch

---

## Architecture

### Files
```
chatgpt_stats.py     Main app (~2800 lines). Single file by design.
setup.sh             Creates venv, installs deps, pre-warms fastembed model
run.sh               Launches app via .venv/bin/python
requirements.txt     rich, questionary, fastembed
collected/           Drop conversations-*.json files here
models/              fastembed model cache (BAAI/bge-small-en-v1.5, ~64MB)
.chatgpt_stats.json  User config (folder, timezone, seen_welcome, etc.)
CLAUDE.md            This file
```

### Data flow
```
collected/*.json
  → parse_folder()
  → build_stats(conversations) → stats dict (aggregated, fast)
                               → _build_embed_cache() → semantic features
                                  (lazy, cached in _EMBED_CACHE by folder key)
```

### Key constants
```python
APP_DIR     = Path(__file__).parent        # always app-relative, never home dir
CONFIG_PATH = APP_DIR / ".chatgpt_stats.json"
MODELS_DIR  = APP_DIR / "models"           # fastembed cache
```

### Stats dict keys
```
convos              dict: cid → {title, words, msgs, user_words, gpt_words, ts, model}
user_words          int
gpt_words           int
user_msgs           int
gpt_msgs            int
user_questions      int
gpt_code_blocks     int
msgs_per_month      Counter
msgs_per_hour       Counter
msgs_per_day        Counter
model_usage         Counter
word_freq_user      Counter
word_freq_gpt       Counter
user_texts          list[str]  — all raw user message texts
gaslit_phrases      Counter
gaslit_by_model     Counter
gaslit_total        int
earliest_ts         float
latest_ts           float
tz_name             str
categories          dict: label → [keywords]  (keyword-based, for fallback)
_signal_score       dict  (computed post-load, stored in stats)
```

### Screen conventions
```python
screen_*(stats)              # read-only, aggregated stats only
screen_*(stats, convos)      # needs raw convo data
```
- All screens end with `pause()`
- `header(title)` for section rule headers
- `rich_bar(value, max_value, width, color)` for bar charts
- Voice: direct, warm, leads with insight ("You keep coming back to this.")

### Screens that need convos
```python
NEEDS_CONVOS = {
    screen_better_prompter,
    screen_productivity_pulse,
    screen_ideas,
    screen_search,
    screen_categories,   # for semantic k-means
}
```

### Embedding infrastructure
```python
_EMBED_CACHE = {}   # folder_key → {ids, titles, texts, ts, embeddings}

_build_embed_cache(convos, folder)
# Builds or returns cached embeddings.
# Model: BAAI/bge-small-en-v1.5 via fastembed, cached in ./models/
# Corpus: title + first user message (200 chars) per convo
# Returns None if fastembed not installed.
```

---

## Design / Voice

**Never say:** "14 semantic clusters detected."
**Always say:** "You keep coming back to this idea."

Lead with the insight. Show the data after. One thing per screen.
Progressive disclosure. No jargon. Sounds like a person, not a report.

Apple-esque: clean, opinionated, says the uncomfortable thing gently.

---

## Stack

- Python 3 · rich >= 13 · questionary >= 2 · fastembed >= 0.3
- fastembed: BAAI/bge-small-en-v1.5, ~64MB ONNX, no PyTorch, cached in `./models/`
- No external APIs. Runs 100% locally. Ships as a self-contained zip.

---

## Profile Screen (next feature to build)

Auto-derived personality mirror. No quiz. Entirely from behavior.

**Data sources available in `stats`:**
- `msgs_per_hour` → when they work (morning/night person)
- `user_questions / user_msgs` → question ratio (explorer vs. directive)
- `word_freq_user` most common words → dominant domains
- `signal_score["signals"]` → personal density, depth, recurrence
- Avg conversation length → deep diver vs. quick lookup
- `gaslit_total / gpt_msgs` → how much flattery they received
- `msgs_per_day` variance → consistent vs. bursty usage

**Output format:**
```
  You're a Late-Night Builder with a wide curiosity radius.

  You explore broadly (top domains: trading, film, systems)
  and execute narrowly — your idea-to-ship ratio is roughly 1:6.

  74% of your openers are questions. You give context
  40% more than you did a year ago — you're getting better at this.

  You tend to restart when stuck rather than push through.
  Your highest-resolution sessions happen when you include
  a concrete example in your first message.

  Longest open thread: 2 years. (see Ideas)
```

---

## Export Screen (future)

Obsidian markdown export. Three modes:
- **Your Voice** — 1st person, reads like your own notes
- **Neutral** — clean wiki-style summary
- **Raw** — full transcript, nothing invented

Folder structure: one .md per convo, grouped by semantic topic cluster.
Wikilinks between related convos derived from similarity scores.

---

## gstack

Use `/browse` for any web browsing — never `mcp__claude-in-chrome__*` tools.

**Suggest the right skill at the right moment:**

| Moment | Skill |
|--------|-------|
| Planning a new screen/feature | `/plan-eng-review` |
| After writing a screen function | `/review` |
| Debugging something broken | `/debug` |
| Testing the app end-to-end | `/qa` |
| Ready to commit | `/ship` |
| End of session | `/retro` |
| Brainstorming product direction | `/office-hours` |
| Strategy / product decisions | `/plan-ceo-review` |

**If skills malfunction:**
```bash
cd ~/.claude/skills/gstack && ~/.bun/bin/bun run build
```

**All skills:** `/office-hours` `/plan-ceo-review` `/plan-eng-review`
`/plan-design-review` `/design-consultation` `/review` `/ship` `/browse`
`/qa` `/qa-only` `/design-review` `/setup-browser-cookies` `/retro`
`/debug` `/document-release` `/gstack-upgrade`

---

## Design System

Always read `DESIGN.md` before making any visual or UI decisions.
All font choices, colors, spacing, border radii, icon usage, and aesthetic
direction are defined there. Do not deviate without explicit user approval.

Key decisions to internalize:
- Fonts: **Fraunces** (display) + **Instrument Sans** (body/UI) + **Geist Mono** (data)
- Accent color: **#C17F3E** amber/copper — the only color used for interactive elements and highlights
- Dark mode default: bg `#141412`, primary text `#C8C3BA` (warm parchment, not stark white)
- Icons: emoji in CLI (rich terminal), **Lucide Icons** for any web/GUI layer
- No purple, no teal, no gradient buttons, no colored icon circles
- In QA mode: flag any code that doesn't match DESIGN.md.
