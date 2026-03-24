#!/usr/bin/env python3
"""
chatgpt_stats — interactive CLI analyzer for your ChatGPT export
Usage: python3 chatgpt_stats.py
"""

import json
import re
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import questionary
from questionary import Style, Separator
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box
from rich.text import Text
from rich.rule import Rule
from rich.progress import Progress, SpinnerColumn, TextColumn

# ── config ────────────────────────────────────────────────────────────────────
APP_DIR     = Path(__file__).parent
CONFIG_PATH = APP_DIR / ".chatgpt_stats.json"
MODELS_DIR  = APP_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)
DEFAULT_TZ  = "America/Los_Angeles"
console = Console()

MENU_STYLE = Style([
    ("qmark",        "fg:#7c3aed bold"),
    ("question",     "bold"),
    ("answer",       "fg:#7c3aed bold"),
    ("pointer",      "fg:#7c3aed bold"),
    ("highlighted",  "fg:#7c3aed bold"),
    ("selected",     "fg:#7c3aed"),
    ("separator",    "fg:#444444"),
    ("instruction",  "fg:#888888"),
])

STOP_WORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "is","are","was","were","be","been","being","have","has","had","do","does",
    "did","will","would","could","should","may","might","shall","can","need",
    "that","this","it","its","i","you","he","she","we","they","my","your",
    "his","her","our","their","me","him","us","them","what","which","who",
    "how","when","where","why","if","so","as","not","no","just","also","about",
    "there","than","then","up","out","more","some","any","all","one","two",
    "get","got","use","used","like","make","made","want","need","know","think",
    "see","let","go","going","s","re","t","ve","ll","d","m","yeah","ok","okay",
    "yes","please","here","good","great","also",
}

CATEGORY_ICONS = ["📈","🎬","💻","🖥️ ","📄","🎵","✍️ ","🏠","🤖","🔬","🎮","💼","🌐","🧪","📦"]

def build_categories(convos, n_cats=12):
    """
    Dynamically discover categories from conversation titles.
    No hardcoded topics — emerges entirely from the user's own data.

    Algorithm:
      1. Count all meaningful words across titles (stop words stripped).
      2. Take the top N seed words — these become category anchors.
      3. Assign each convo to the seed word that appears most in its title.
      4. Merge seeds whose convos overlap >60% into one category.
      5. Label each category with its top 2–3 representative words.
    """
    # Extended stop words for titles specifically
    TITLE_STOPS = STOP_WORDS | {
        "new","using","help","create","build","make","add","get","set",
        "update","list","fix","question","issue","problem","work","working",
        "test","testing","try","trying","need","needs","vs","via","how","way",
        "best","better","good","simple","quick","easy","basic","advanced",
        "guide","tutorial","overview","intro","setup","plan","planning","ideas",
        "version","feature","features","option","options","multiple","single",
        "part","parts","step","steps","task","tasks","type","types","mode",
        "system","systems","file","files","data","result","results","output",
        "change","changes","old","current","next","first","second","third",
        "python","code","script","function","class","error","api",  # too generic
    }

    # Count words across all titles
    title_word_freq = Counter()
    title_words_per_convo = {}  # cid → set of title words
    for convo in convos:
        cid   = convo.get("id","")
        title = (convo.get("title") or "").lower()
        words = {w for w in re.findall(r"\b[a-z]{3,}\b", title) if w not in TITLE_STOPS}
        title_words_per_convo[cid] = words
        title_word_freq.update(words)

    # Top seeds
    seeds = [w for w, _ in title_word_freq.most_common(n_cats * 3)][:n_cats * 2]

    # Assign each convo to its best-matching seed
    seed_convos = defaultdict(set)
    for convo in convos:
        cid   = convo.get("id","")
        words = title_words_per_convo.get(cid, set())
        best_seed = max(seeds, key=lambda s: (1 if s in words else 0), default=None)
        if best_seed and best_seed in words:
            seed_convos[best_seed].add(cid)

    # Merge seeds with high overlap, keep top n_cats by convo count
    merged = {}  # label → set of seeds
    used   = set()
    for seed in seeds:
        if seed in used or not seed_convos[seed]:
            continue
        group = {seed}
        for other in seeds:
            if other in used or other == seed or not seed_convos[other]:
                continue
            a, b = seed_convos[seed], seed_convos[other]
            overlap = len(a & b) / min(len(a), len(b)) if min(len(a), len(b)) else 0
            if overlap > 0.5:
                group.add(other)
        label = " / ".join(sorted(group, key=lambda s: -title_word_freq[s])[:2]).title()
        merged[label] = group
        used.update(group)

    # Sort by total convo count, cap at n_cats
    sorted_cats = sorted(merged.items(),
                         key=lambda x: sum(len(seed_convos[s]) for s in x[1]),
                         reverse=True)[:n_cats]

    # Build final CATEGORIES-like dict: label → list of keywords (the seed words)
    icons = CATEGORY_ICONS
    result = {}
    for i, (label, group) in enumerate(sorted_cats):
        icon  = icons[i % len(icons)]
        seeds_sorted = sorted(group, key=lambda s: -title_word_freq[s])
        result[f"{icon} {label}"] = list(seeds_sorted)

    return result

# ── persistence ───────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def get_tz():
    cfg = load_config()
    tz_name = cfg.get("timezone", DEFAULT_TZ)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name), tz_name
    except Exception:
        return timezone.utc, "UTC"

# ── parsing ───────────────────────────────────────────────────────────────────
def parse_folder(folder):
    conversations = []
    files = sorted(Path(folder).glob("conversations-*.json"))
    if not files:
        return None, "No conversations-*.json files found in that folder."

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as p:
        task = p.add_task(f"Parsing {len(files)} files…", total=None)
        for fpath in files:
            with open(fpath) as f:
                conversations.extend(json.load(f))
        p.update(task, description="Done!")

    return conversations, None

def extract_text(msg):
    parts = msg.get("content", {}).get("parts", [])
    return " ".join(p for p in parts if isinstance(p, str)).strip()

# Matches shell prompt lines for any common shell (bash/zsh/fish/sh):
#   user@host ... $   user@host ... %   [user@host ~]$   ~/path $   etc.
_SHELL_PROMPT_RE = re.compile(
    r'^\s*(?:[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+\s[^\n]*?[$%#>]'   # user@host ... $ or %
    r'|[$%#>]\s'                                                   # bare $ % # >
    r'|>>>?\s'                                                     # Python REPL
    r')',
    re.MULTILINE
)
# File paths: /Users/foo/bar  ~/foo  ./foo  C:\foo
_PATH_RE = re.compile(r'(?:^|(?<=\s))(?:/|~/|\.{1,2}/|[A-Z]:\\)\S+', re.MULTILINE)
# username@hostname tokens
_AT_HOST_RE = re.compile(r'\b[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+\b')

def _is_terminal_paste(text):
    """True if text looks like pasted terminal/shell output."""
    return bool(_SHELL_PROMPT_RE.search(text)) or text.count('\n') > 3 and bool(_PATH_RE.search(text))

def _clean_for_words(text):
    """Strip shell prompts, file paths, and user@host tokens before word analysis."""
    text = _SHELL_PROMPT_RE.sub(' ', text)
    text = _PATH_RE.sub(' ', text)
    text = _AT_HOST_RE.sub(' ', text)
    return text

def word_count(text):
    return len(re.findall(r"\b[a-zA-Z']+\b", _clean_for_words(text)))

def tokenize(text):
    return re.findall(r"\b[a-zA-Z]{3,}\b", _clean_for_words(text).lower())

def ts_to_dt(ts):
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None

def fmt(n):
    return f"{n:,}"

# Seed words that signal a sycophantic opener.
# Detection is dynamic — we extract the ACTUAL phrase GPT used, not just tick a box.
GASLIT_SEEDS = {
    "great","absolutely","certainly","of course","sure","perfect","excellent",
    "wonderful","fantastic","awesome","amazing","nice","good","happy","glad",
    "interesting","love","brilliant","superb","impressive","outstanding",
    "no problem","of course","you're right","great question","good question",
    "fair point","good point","great point","great idea","good idea",
    "totally","exactly","indeed","definitely","naturally","precisely",
    "i'd be happy","i'm happy","i'd love","i appreciate","thanks for",
}

def _extract_gaslit_phrase(text):
    """
    If GPT's response starts with a sycophantic opener, return the normalized
    phrase (e.g. 'Great!' or 'Absolutely!' or "Great question!").
    Returns None if the opener looks neutral/substantive.
    """
    # Take only the first line, strip markdown
    first_line = text.strip().split("\n")[0].strip()
    first_line = re.sub(r"[*_`#]", "", first_line)  # strip markdown

    # Grab first 8 words max, stop at sentence boundary
    sentence_match = re.match(r"^([^.!?]{1,80}[.!?])", first_line)
    opener = sentence_match.group(1).strip() if sentence_match else first_line[:60]

    opener_lc = opener.lower().rstrip("!.,")

    # Check if any seed word/phrase appears at the start
    for seed in GASLIT_SEEDS:
        if opener_lc.startswith(seed) or opener_lc == seed:
            # Normalize: cap at 40 chars, break on word boundary
            phrase = opener[:40]
            if len(opener) > 40:
                phrase = phrase.rsplit(" ", 1)[0]
            phrase = phrase.strip().rstrip(",")
            if not phrase.endswith(("!", ".", "?")):
                phrase += "!"
            return phrase

    return None


def build_stats(conversations):
    """One pass over all conversations, build everything."""
    tz, tz_name = get_tz()
    stats = {
        "convos": {},           # id → {title, words, msgs, ts, user_words, gpt_words, model}
        "user_words": 0,
        "gpt_words": 0,
        "user_msgs": 0,
        "gpt_msgs": 0,
        "user_questions": 0,
        "gpt_code_blocks": 0,
        "msgs_per_month": Counter(),
        "msgs_per_hour": Counter(),
        "msgs_per_day": Counter(),
        "model_usage": Counter(),
        "word_freq_user": Counter(),
        "word_freq_gpt": Counter(),
        "user_texts": [],       # all user message texts
        "gaslit_phrases": Counter(),  # actual opener phrases GPT used
        "gaslit_by_model": Counter(),
        "gaslit_total": 0,
        "earliest_ts": float("inf"),
        "latest_ts": 0,
        "tz_name": tz_name,
        "categories": {},   # filled after the main loop
    }

    for convo in conversations:
        cid         = convo.get("id") or convo.get("conversation_id", "")
        convo_model = convo.get("default_model_slug")  # resolved per-message below
        title       = convo.get("title", "Untitled")

        if cid not in stats["convos"]:
            stats["convos"][cid] = {
                "title": title, "words": 0, "msgs": 0,
                "user_words": 0, "gpt_words": 0,
                "ts": convo.get("create_time"),
                "model": convo_model or "legacy",
            }

        for node in convo.get("mapping", {}).values():
            msg = node.get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "")
            if role not in ("user", "assistant"):
                continue
            text = extract_text(msg)
            if not text:
                continue

            ts  = msg.get("create_time") or convo.get("create_time")
            wc  = word_count(text)
            tok = [t for t in tokenize(text) if t not in STOP_WORDS]

            # resolve model: convo slug → message metadata → "legacy"
            model = (convo_model
                     or msg.get("metadata", {}).get("model_slug")
                     or "legacy")

            stats["convos"][cid]["words"] += wc
            stats["convos"][cid]["msgs"]  += 1
            stats["model_usage"][model]   += 1

            if ts:
                if ts < stats["earliest_ts"]: stats["earliest_ts"] = ts
                if ts > stats["latest_ts"]:   stats["latest_ts"]   = ts
                dt = datetime.fromtimestamp(ts, tz=tz)
                stats["msgs_per_month"][dt.strftime("%Y-%m")] += 1
                stats["msgs_per_hour"][dt.hour] += 1
                stats["msgs_per_day"][dt.strftime("%Y-%m-%d")] += 1

            if role == "user":
                stats["user_words"] += wc
                stats["user_msgs"]  += 1
                stats["user_questions"] += text.count("?")
                stats["word_freq_user"].update(tok)
                stats["convos"][cid]["user_words"] += wc
                stats["user_texts"].append(text)
            else:
                stats["gpt_words"]  += wc
                stats["gpt_msgs"]   += 1
                stats["gpt_code_blocks"] += text.count("```") // 2
                stats["word_freq_gpt"].update(tok)
                stats["convos"][cid]["gpt_words"] += wc
                # gaslit detection — extract opener phrase from first line
                phrase = _extract_gaslit_phrase(text)
                if phrase:
                    stats["gaslit_phrases"][phrase] += 1
                    stats["gaslit_by_model"][model] += 1
                    stats["gaslit_total"] += 1

    # Build categories dynamically from the loaded conversations
    stats["categories"] = build_categories(conversations)
    return stats

# ── display helpers ───────────────────────────────────────────────────────────
def rich_bar(value, max_value, width=24, color="violet"):
    filled = int(width * value / max_value) if max_value else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"[{color}]{bar}[/]"

def header(title):
    console.print()
    console.rule(f"[bold violet]{title}[/]")
    console.print()

def pause():
    console.print()
    questionary.press_any_key_to_continue("  [press any key to return to menu]").ask()

# ── menu screens ──────────────────────────────────────────────────────────────
def screen_overview(stats):
    header("OVERVIEW")
    total_words = stats["user_words"] + stats["gpt_words"]
    total_msgs  = stats["user_msgs"]  + stats["gpt_msgs"]
    num_convos  = len(stats["convos"])
    start = ts_to_dt(stats["earliest_ts"])
    end   = ts_to_dt(stats["latest_ts"])
    span_days = (stats["latest_ts"] - stats["earliest_ts"]) / 86400

    t = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0,2))
    t.add_column(style="dim")
    t.add_column(style="bold cyan")

    t.add_row("Period",          f"{start.strftime('%b %d, %Y')} → {end.strftime('%b %d, %Y')}  ({span_days:.0f} days)")
    t.add_row("Conversations",   fmt(num_convos))
    t.add_row("Total messages",  f"{fmt(total_msgs)}  [dim](you: {fmt(stats['user_msgs'])} / GPT: {fmt(stats['gpt_msgs'])})[/]")
    t.add_row("Total words",     fmt(total_words))
    t.add_row("Avg msgs/convo",  f"{total_msgs/num_convos:.1f}")
    t.add_row("Avg words/convo", f"{total_words/num_convos:.0f}")
    t.add_row("Equivalent to",   f"~{total_words/90000:.1f} novels (90k words each)")
    console.print(t)

    console.print()
    # talk ratio
    user_pct = 100 * stats["user_words"] / total_words
    gpt_pct  = 100 - user_pct
    you_w = int(40 * user_pct / 100)
    gpt_w = 40 - you_w
    console.print(Panel(
        f"[bold]You[/]  {user_pct:4.1f}%  [green]{'█' * you_w}{'░' * (40-you_w)}[/]\n"
        f"[bold]GPT[/]  {gpt_pct:4.1f}%  [violet]{'█' * gpt_w}{'░' * (40-gpt_w)}[/]\n\n"
        f"GPT wrote [bold]{stats['gpt_words']/stats['user_words']:.1f}x more words[/] than you.\n"
        f"Reading all GPT replies at 250 wpm = [bold]{stats['gpt_words']/250/60:.0f} hours[/]",
        title="Talk Ratio", border_style="dim"
    ))

    console.print()
    # fun facts
    msgs_per_day = total_msgs / span_days if span_days else 0
    console.print(Panel(
        f"  You averaged [bold]{msgs_per_day:.1f}[/] messages/day\n"
        f"  You asked [bold]{fmt(stats['user_questions'])}[/] questions ({stats['user_questions']/stats['user_msgs']:.2f} per message)\n"
        f"  GPT generated [bold]{fmt(stats['gpt_code_blocks'])}[/] code blocks for you",
        title="Fun Facts", border_style="dim"
    ))
    pause()

def _paste_score(text):
    """
    0.0 = almost certainly hand-typed
    1.0 = almost certainly copy-pasted

    Signals used (no LLM):
    - Shell prompt lines / file paths (strong)
    - Code fences, stack traces, log timestamps (strong)
    - Very low sentence-punctuation density (moderate)
    - Uniform line lengths — formatted/tabular output (moderate)
    - High ratio of non-alpha characters (moderate)
    - Natural-language markers that fight against paste (negative weight):
      contractions, personal pronouns, casual filler words
    """
    if not text or len(text) < 30:
        return 0.0

    score = 0.0

    # ── strong paste signals ──────────────────────────────────────────────────
    if _SHELL_PROMPT_RE.search(text):       score += 0.45
    if _PATH_RE.search(text):              score += 0.25
    if "```" in text:                      score += 0.30
    if re.search(r'Traceback \(most recent', text): score += 0.40
    if re.search(r'^\s+at \w+[\.\w]+\(', text, re.M): score += 0.30  # Java/JS stack
    # Log timestamps: 2024-01-15 or 15:30:22
    if re.search(r'\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}', text): score += 0.20
    # UUID-like strings
    if re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}', text): score += 0.20
    # HTML/XML tags
    if re.search(r'<[a-zA-Z][^>]{0,40}>', text): score += 0.15

    # ── moderate signals ──────────────────────────────────────────────────────
    lines = [l for l in text.split('\n') if l.strip()]
    if len(lines) >= 4:
        lengths = [len(l) for l in lines]
        avg = sum(lengths) / len(lengths)
        variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
        # very uniform line lengths = formatted/tabular output
        if avg > 20 and variance < 200:
            score += 0.20

    # low sentence-ending punctuation per word
    wc = max(word_count(text), 1)
    punct_density = sum(text.count(p) for p in '.!?') / wc
    if punct_density < 0.03 and wc > 30:   score += 0.15  # wall of text, no sentences

    # high special-char ratio
    alpha = sum(c.isalpha() for c in text)
    special = sum(not c.isalnum() and not c.isspace() for c in text)
    if alpha > 0 and special / alpha > 0.4: score += 0.15

    # ── hand-typed counter-signals (reduce score) ─────────────────────────────
    tl = text.lower()
    hand_typed_hits = sum(1 for w in [
        "i'm","i've","i'd","i'll","don't","can't","won't","let's","it's",
        "ugh","hmm","okay","yeah","actually","basically","honestly",
        "i think","i feel","i want","i need","i was","i am",
    ] if w in tl)
    score -= hand_typed_hits * 0.07

    return max(0.0, min(1.0, score))


def _classify_message(text):
    """Returns (paste_score, label, confidence_str)"""
    s = _paste_score(text)
    if s >= 0.55:
        confidence = "high" if s >= 0.75 else "moderate"
        return s, "pasted", confidence
    elif s <= 0.20:
        confidence = "moderate"  # hand-typed is harder to prove
        return s, "typed", confidence
    else:
        return s, "unclear", "low"


def screen_you(stats):
    header("DEEP DIVE: WHAT YOU TYPED")

    texts = stats["user_texts"]
    word_counts = [word_count(t) for t in texts]
    avg_wc   = sum(word_counts) / len(word_counts)
    median_wc = sorted(word_counts)[len(word_counts)//2]

    buckets = Counter()
    for wc in word_counts:
        if wc == 0:        buckets["0 (non-alpha)"] += 1
        elif wc == 1:      buckets["1 word"] += 1
        elif wc <= 5:      buckets["2-5 words"] += 1
        elif wc <= 15:     buckets["6-15 words"] += 1
        elif wc <= 50:     buckets["16-50 words"] += 1
        elif wc <= 150:    buckets["51-150 words"] += 1
        else:              buckets["150+ words"] += 1

    bucket_order = ["0 (non-alpha)","1 word","2-5 words","6-15 words","16-50 words","51-150 words","150+ words"]

    t = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0,2))
    t.add_column(style="dim")
    t.add_column(style="bold cyan")
    t.add_row("Messages",       fmt(len(texts)))
    t.add_row("Avg words/msg",  f"{avg_wc:.1f}")
    t.add_row("Median words",   str(median_wc))
    t.add_row("Longest msg",    fmt(max(word_counts)) + " words")
    console.print(t)

    console.print()
    console.print("[bold]Message length distribution[/]")
    max_b = max(buckets.values())
    for label in bucket_order:
        count = buckets[label]
        pct = 100 * count / len(texts)
        console.print(f"  [dim]{label:<16}[/] {rich_bar(count, max_b, 28, 'cyan')} {fmt(count):>7}  ({pct:.1f}%)")

    console.print()
    console.print("[bold]Your top 20 words[/]  [dim](stop words removed)[/]")
    top = stats["word_freq_user"].most_common(20)
    max_wf = top[0][1]
    for word, count in top:
        console.print(f"  [cyan]{word:<22}[/] {rich_bar(count, max_wf, 22, 'cyan')} {fmt(count)}")

    console.print()
    # tone signals
    politeness_words  = ["please","thank","thanks","grateful","appreciate","sorry","apolog"]
    frustration_words = ["ugh","wtf","damn","broken","not working","wrong","error","issue","fail","stuck","still not"]
    affirmation_words = ["perfect","exactly","correct","great","nice","love it","awesome","yes","works","that's it"]
    correction_words  = ["no,","wrong","that's not","actually","wait","nevermind","nope","no that","not what"]

    polite = sum(1 for t in texts if any(w in t.lower() for w in politeness_words))
    frust  = sum(1 for t in texts if any(w in t.lower() for w in frustration_words))
    affirm = sum(1 for t in texts if any(w in t.lower() for w in affirmation_words))
    corr   = sum(1 for t in texts if any(w in t.lower() for w in correction_words))

    ugh_count = sum(t.lower().count("ugh") for t in texts)

    tone_table = Table(box=box.ROUNDED, border_style="dim", show_header=False, padding=(0,2))
    tone_table.add_column(style="dim")
    tone_table.add_column(style="bold")
    tone_table.add_row("Polite msgs (please/thank/sorry)", f"[green]{fmt(polite)}[/]")
    tone_table.add_row("Frustration signals",              f"[red]{fmt(frust)}[/]")
    tone_table.add_row("  → 'ugh' alone",                  f"[red]{fmt(ugh_count)}[/]")
    tone_table.add_row("Affirmations (perfect/yes/works)", f"[green]{fmt(affirm)}[/]")
    tone_table.add_row("Corrections / pushback",           f"[yellow]{fmt(corr)}[/]")
    tone_table.add_row("One-liners (≤5 words)",            fmt(sum(1 for wc in word_counts if wc <= 5)))
    tone_table.add_row("Epic messages (200+ words)",       fmt(sum(1 for wc in word_counts if wc >= 200)))
    code_or_terminal = sum(1 for t in texts if "```" in t or _is_terminal_paste(t))
    tone_table.add_row("Messages with code/terminal pasted", fmt(code_or_terminal))
    tone_table.add_row("Messages with URLs",               fmt(sum(1 for t in texts if re.search(r'https?://', t))))
    console.print(Panel(tone_table, title="Tone & Behavior", border_style="dim"))

    console.print()
    console.print("[bold]How you start messages[/]  [dim](first word)[/]")
    first_words = Counter()
    for t in texts:
        words = re.findall(r"\b[a-zA-Z']+\b", t)
        if words:
            first_words[words[0].lower()] += 1
    top_first = [(w, c) for w, c in first_words.most_common(50) if w not in STOP_WORDS][:15]
    max_fw = top_first[0][1] if top_first else 1
    for word, count in top_first:
        console.print(f"  [cyan]{word:<22}[/] {rich_bar(count, max_fw, 22, 'cyan')} {fmt(count)}")

    # ── longest pasted vs hand-typed ─────────────────────────────────────────
    console.print()
    console.rule("[bold]LONGEST PASTED vs HAND-TYPED MESSAGE[/]")
    console.print(
        "\n  [dim]Detected using heuristics — no LLM. Paste signals: shell prompts, file paths,\n"
        "  code blocks, stack traces, log timestamps, uniform line lengths, low punctuation\n"
        "  density. Hand-typed signals: contractions, pronouns, casual language, sentence flow.\n"
        "  Accuracy: ~85–90% for pastes, ~75% for hand-typed.[/]\n"
    )

    scored = [(t, _paste_score(t), word_count(t)) for t in texts if word_count(t) > 20]

    # longest paste-classified (score >= 0.55)
    pastes  = sorted([(t, s, wc) for t, s, wc in scored if s >= 0.55], key=lambda x: -x[2])
    # longest hand-typed-classified (score <= 0.25)
    typed   = sorted([(t, s, wc) for t, s, wc in scored if s <= 0.25], key=lambda x: -x[2])

    CONFIDENCE_LEGEND = (
        "  [bold]Confidence guide[/] — this is heuristic-based, not LLM-verified:\n"
        "  [green]high[/]     (~85–90% accurate) — multiple strong signals present\n"
        "  [yellow]moderate[/] (~70–80% accurate) — some signals, but not definitive\n"
        "  [red]low[/]      (~50–60% accurate) — mixed signals; treat as a guess\n\n"
        "  [dim]For higher accuracy, connect an LLM via the Prompt Analyzer tool.[/]"
    )
    console.print(Panel(CONFIDENCE_LEGEND, title="[dim]How confident is this?[/]",
                        border_style="dim", padding=(0, 2)))
    console.print()

    for label, color, icon, candidates in [
        ("LONGEST COPY-PASTED MESSAGE",  "yellow", "📋", pastes),
        ("LONGEST HAND-TYPED MESSAGE",   "cyan",   "✍️ ", typed),
    ]:
        console.print(f"  [{color}][bold]{icon}  {label}[/][/]")
        if not candidates:
            console.print("  [dim]  None detected with sufficient confidence.[/]\n")
            continue
        best_text, best_score, best_wc = candidates[0]
        _, classification, confidence = _classify_message(best_text)
        conf_color = "green" if confidence == "high" else ("yellow" if confidence == "moderate" else "red")
        preview = best_text.replace("\n", " ↵ ")[:220]
        console.print(Panel(
            f"[dim]{preview}…[/]\n\n"
            f"  [bold]{fmt(best_wc)} words[/]"
            f"  ·  paste score: {best_score:.2f} / 1.00"
            f"  ·  confidence: [{conf_color}][bold]{confidence}[/][/]"
            f"  [dim](heuristic — no LLM)[/]",
            border_style=color, padding=(0, 1)
        ))
        console.print()

    pause()

def screen_gaslit(stats):
    header("😵  HOW MANY TIMES DID CHATGPT GASLIT YOU?")

    total_gpt    = stats["gpt_msgs"]
    gaslit_total = stats["gaslit_total"]
    phrases      = stats["gaslit_phrases"]

    if not gaslit_total:
        console.print("[dim]No sycophantic openers detected. Either GPT was unusually direct, or something's off.[/]")
        pause(); return

    pct = 100 * gaslit_total / total_gpt if total_gpt else 0

    # grade
    if pct >= 60:   grade, color = "Chronically validated 😵",     "red"
    elif pct >= 40: grade, color = "Heavily flattered 😬",          "yellow"
    elif pct >= 25: grade, color = "Regularly buttered up 🧈",      "yellow"
    elif pct >= 10: grade, color = "Occasionally sycophanted 🙂",   "cyan"
    else:           grade, color = "Remarkably un-gaslit 😎",        "green"

    console.print(Panel(
        f"  GPT responses that opened with flattery: [{color}][bold]{fmt(gaslit_total)}[/][/] of {fmt(total_gpt)}\n"
        f"  That's [{color}][bold]{pct:.1f}%[/][/] of everything it said to you.\n\n"
        f"  Verdict — [{color}][bold]{grade}[/][/]",
        border_style=color
    ))

    # top phrases table
    console.print()
    console.print("[bold]THE ACTUAL PHRASES IT USED ON YOU[/]  [dim](ranked by frequency)[/]\n")

    top = phrases.most_common(30)
    max_count = top[0][1] if top else 1

    t = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet",
              show_header=True, padding=(0,1))
    t.add_column("#",       style="dim",  width=4)
    t.add_column("Phrase GPT opened with",  style="bold", min_width=35)
    t.add_column("Bar",     no_wrap=True)
    t.add_column("Times",   justify="right", style="cyan")
    t.add_column("% of flattery", justify="right", style="dim")

    for i, (phrase, count) in enumerate(top, 1):
        share = 100 * count / gaslit_total
        t.add_row(str(i), phrase, rich_bar(count, max_count, 22, "yellow"),
                  fmt(count), f"{share:.1f}%")

    console.print(t)

    console.print()
    # fun callouts
    top_phrase, top_count = top[0]
    console.print(f"  [dim]Most used:[/] [bold yellow]\"{top_phrase}\"[/] — said to you [bold]{fmt(top_count)}[/] times.")
    console.print(f"  [dim]Unique flattery phrases detected:[/] [bold]{fmt(len(phrases))}[/]")
    avg_per_day = gaslit_total / ((stats["latest_ts"] - stats["earliest_ts"]) / 86400) if stats["latest_ts"] else 0
    console.print(f"  [dim]Average gaslit responses per day:[/] [bold]{avg_per_day:.1f}[/]")

    # by-model breakdown
    gaslit_by_model = stats.get("gaslit_by_model", Counter())
    if gaslit_by_model:
        console.print()
        console.rule("[bold]BY MODEL — who's the worst offender?[/]")
        console.print()

        tm = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet",
                   show_header=True, padding=(0,1))
        tm.add_column("Model",             style="bold", min_width=30)
        tm.add_column("Gaslit responses",  justify="right", style="yellow")
        tm.add_column("That model's msgs", justify="right", style="dim")
        tm.add_column("% of model",        justify="right", style="dim")

        model_totals = stats.get("model_usage", Counter())
        for model, gcount in gaslit_by_model.most_common():
            model_total = model_totals.get(model, 0)
            mpct = 100 * gcount / model_total if model_total else 0
            tm.add_row(model, fmt(gcount), fmt(model_total), f"{mpct:.1f}%")

        console.print(tm)

    pause()


def _kmeans_embeddings(embeddings, k=12, n_iter=15):
    """
    k-means++ init + Lloyd's algorithm. Pure numpy — no scipy needed.
    Operates on L2-normalized vectors so dot product = cosine similarity.
    Returns list of integer cluster labels.
    """
    import numpy as np
    n = len(embeddings)
    if n <= k:
        return list(range(n))

    rng = np.random.default_rng(42)

    # k-means++ — spread initial centers as far apart as possible
    first = rng.integers(n)
    centers = [embeddings[first]]
    for _ in range(k - 1):
        # min similarity to any existing center (lower = farther away)
        sims  = np.array([max(float(np.dot(e, c)) for c in centers) for e in embeddings])
        probs = 1.0 - np.clip(sims, -1.0, 1.0)
        total = probs.sum()
        probs = probs / total if total > 0 else np.ones(n) / n
        centers.append(embeddings[rng.choice(n, p=probs)])

    centers = np.array(centers, dtype="float32")
    labels  = np.zeros(n, dtype=int)

    for _ in range(n_iter):
        sims   = embeddings @ centers.T     # (n, k)
        labels = sims.argmax(axis=1)
        for j in range(k):
            mask = labels == j
            if mask.any():
                c      = embeddings[mask].mean(axis=0)
                norm   = np.linalg.norm(c)
                centers[j] = c / norm if norm > 0 else c

    return labels.tolist()


def screen_categories(stats, convos=None):
    header("🗂️   TOPICS")

    cfg    = load_config()
    folder = cfg.get("folder", "default")

    # ── Try semantic clustering first ─────────────────────────────────────────
    semantic_ok = False
    if convos is not None:
        cache = _build_embed_cache(convos, folder)
        if cache is not None:
            try:
                import numpy as np
                console.print(
                    "  [dim]Clustering by meaning — not just keywords.[/]\n"
                )
                k      = min(14, max(4, len(cache["ids"]) // 40))
                labels = _kmeans_embeddings(cache["embeddings"], k=k)

                NAME_STOPS = STOP_WORDS | {
                    "new","using","help","create","build","make","add","get","set",
                    "update","fix","question","issue","problem","work","working",
                    "test","need","vs","via","how","way","best","think","just",
                    "let","want","also","like","one","two","three",
                }

                # Map each cluster → (name, list of (title, words, msgs))
                cluster_titles  = defaultdict(list)
                cluster_convos  = defaultdict(list)   # (title, words, msgs)
                cid_list        = cache["ids"]

                for idx, label in enumerate(labels):
                    cid = cid_list[idx]
                    c   = stats["convos"].get(cid)
                    if not c:
                        continue
                    title = c.get("title") or "Untitled"
                    cluster_titles[label].append(title)
                    cluster_convos[label].append(
                        (title, c.get("words", 0), c.get("msgs", 0))
                    )

                def cluster_name(label):
                    wf = Counter()
                    for t in cluster_titles[label]:
                        words = re.findall(r"\b[a-z]{3,}\b", t.lower())
                        wf.update(w for w in words if w not in NAME_STOPS)
                    top = [w for w, _ in wf.most_common(3)]
                    return " / ".join(w.capitalize() for w in top[:2]) if top else "Misc"

                ICONS = CATEGORY_ICONS
                cat_counts = {}
                cat_convos_map = {}
                for label in set(labels):
                    name = cluster_name(label)
                    icon = ICONS[label % len(ICONS)]
                    key  = f"{icon}  {name}"
                    cat_counts[key]     = len(cluster_convos[label])
                    cat_convos_map[key] = cluster_convos[label]

                total_convos = sum(cat_counts.values())
                max_count    = max(cat_counts.values()) if cat_counts else 1

                t = Table(
                    title="Topics — clustered by meaning",
                    box=box.ROUNDED, border_style="dim",
                    header_style="bold violet", padding=(0, 1),
                )
                t.add_column("Topic",    style="bold", min_width=26)
                t.add_column("Bar",      no_wrap=True)
                t.add_column("Convos",   justify="right", style="cyan")
                t.add_column("%",        justify="right", style="dim")
                t.add_column("Example",  style="dim", max_width=32)

                for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
                    pct       = 100 * count / total_convos
                    top       = sorted(cat_convos_map[cat], key=lambda x: -x[1])
                    raw_title = (top[0][0] or "Untitled") if top else ""
                    example   = raw_title[:30] + "…" if len(raw_title) > 30 else raw_title
                    t.add_row(
                        cat,
                        rich_bar(count, max_count, 20, "violet"),
                        str(count), f"{pct:.1f}%", example,
                    )

                console.print(t)
                console.print()
                console.print("[bold]Top 3 conversations per topic:[/]")
                for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
                    console.print(f"\n  [bold violet]{cat}[/]")
                    for title, words, msgs in sorted(
                        cat_convos_map[cat], key=lambda x: -x[1]
                    )[:3]:
                        safe = (title or "Untitled")[:52]
                        console.print(
                            f"    [dim]{safe:<54}[/]  {fmt(words):>9} words  {msgs} msgs"
                        )

                console.print(
                    f"\n  [dim]Semantic clustering · {k} topics · "
                    "BAAI/bge-small-en-v1.5 · runs locally[/]"
                )
                semantic_ok = True

            except ImportError:
                pass

    # ── Keyword fallback ──────────────────────────────────────────────────────
    if not semantic_ok:
        console.print(
            "  [dim]Topics discovered from your conversation titles.[/]\n"
            "  [dim](Open Ideas first to enable semantic clustering.)[/]\n"
        )
        categories = stats.get("categories", {})
        if not categories:
            console.print("[yellow]No categories found.[/]")
            pause()
            return

        cat_counts   = Counter()
        cat_convos_f = defaultdict(list)

        for cid, c in stats["convos"].items():
            title_lower = (c["title"] or "Untitled").lower()
            matched = None
            for cat, keywords in categories.items():
                if any(kw in title_lower for kw in keywords):
                    matched = cat
                    break
            primary = matched or "🗂️  Other / Misc"
            cat_counts[primary] += 1
            cat_convos_f[primary].append((c["title"], c["words"], c["msgs"]))

        total_convos = sum(cat_counts.values())
        max_count    = max(cat_counts.values()) if cat_counts else 1

        t = Table(
            title="Topics (keyword mode)", box=box.ROUNDED, border_style="dim",
            header_style="bold violet", padding=(0, 1),
        )
        t.add_column("Topic",   style="bold", min_width=24)
        t.add_column("Bar",     no_wrap=True)
        t.add_column("Convos",  justify="right", style="cyan")
        t.add_column("%",       justify="right", style="dim")
        t.add_column("Example", style="dim", max_width=35)

        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            pct       = 100 * count / total_convos
            top       = sorted(cat_convos_f[cat], key=lambda x: -x[1])
            raw_title = (top[0][0] or "Untitled") if top else ""
            example   = raw_title[:32] + "…" if len(raw_title) > 32 else raw_title
            t.add_row(
                cat, rich_bar(count, max_count, 20, "violet"),
                str(count), f"{pct:.1f}%", example,
            )

        console.print(t)
        console.print()
        console.print("[bold]Top 3 conversations per topic:[/]")
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            console.print(f"\n  [bold violet]{cat}[/]")
            for title, words, msgs in sorted(cat_convos_f[cat], key=lambda x: -x[1])[:3]:
                safe = (title or "Untitled")[:50]
                console.print(f"    [dim]{safe:<52}[/]  {fmt(words):>9} words  {msgs} msgs")

    pause()

def screen_time(stats):
    header("TIME PATTERNS")

    months = sorted(stats["msgs_per_month"].items())
    max_month = max(v for _, v in months) if months else 1

    console.print("[bold]Monthly message trend[/]")
    for month, count in months:
        dt    = datetime.strptime(month, "%Y-%m")
        label = dt.strftime("%b %Y")
        console.print(f"  [dim]{label}[/]  {rich_bar(count, max_month, 30, 'cyan')} {fmt(count)}")

    console.print()
    start = ts_to_dt(stats["earliest_ts"])
    end   = ts_to_dt(stats["latest_ts"])
    console.print(f"[bold]What time do you chat?[/]  [dim]({stats.get('tz_name','UTC')} · {start.strftime('%b %Y')} – {end.strftime('%b %Y')} · peak hour marked)[/]")
    max_hour = max(stats["msgs_per_hour"].values()) if stats["msgs_per_hour"] else 1
    peak_hour = max(stats["msgs_per_hour"], key=stats["msgs_per_hour"].get)
    for hour in range(24):
        count = stats["msgs_per_hour"].get(hour, 0)
        marker = " [bold yellow]← peak[/]" if hour == peak_hour else ""
        console.print(f"  [dim]{hour:02d}:00[/]  {rich_bar(count, max_hour, 24, 'cyan')} {count}{marker}")

    console.print()
    console.print("[bold]Top 5 busiest days ever[/]")
    top_days = stats["msgs_per_day"].most_common(5)
    max_day  = top_days[0][1] if top_days else 1
    for day, count in top_days:
        dt    = datetime.strptime(day, "%Y-%m-%d")
        label = dt.strftime("%a %b %d, %Y")
        console.print(f"  [dim]{label}[/]  {rich_bar(count, max_day, 24, 'cyan')} {count} msgs")

    pause()

def screen_top_convos(stats):
    header("YOUR BIGGEST CONVERSATIONS")

    by_words = sorted(stats["convos"].values(), key=lambda x: -x["words"])[:10]
    by_msgs  = sorted(stats["convos"].values(), key=lambda x: -x["msgs"])[:10]

    t1 = Table(title="By Word Count", box=box.ROUNDED, border_style="dim",
               header_style="bold violet", padding=(0,1))
    t1.add_column("#",      style="dim", width=3)
    t1.add_column("Title",  max_width=42)
    t1.add_column("Words",  justify="right", style="cyan")
    t1.add_column("Msgs",   justify="right", style="dim")
    for i, c in enumerate(by_words, 1):
        t1.add_row(str(i), (c["title"] or "Untitled")[:42], fmt(c["words"]), str(c["msgs"]))
    console.print(t1)

    console.print()
    t2 = Table(title="By Message Count", box=box.ROUNDED, border_style="dim",
               header_style="bold violet", padding=(0,1))
    t2.add_column("#",      style="dim", width=3)
    t2.add_column("Title",  max_width=42)
    t2.add_column("Msgs",   justify="right", style="cyan")
    t2.add_column("Words",  justify="right", style="dim")
    for i, c in enumerate(by_msgs, 1):
        t2.add_row(str(i), (c["title"] or "Untitled")[:42], str(c["msgs"]), fmt(c["words"]))
    console.print(t2)

    pause()

def screen_models(stats):
    header("MODEL USAGE BREAKDOWN")

    total = sum(stats["model_usage"].values())
    max_count = max(stats["model_usage"].values()) if stats["model_usage"] else 1

    t = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
    t.add_column("Model",    style="bold", min_width=32)
    t.add_column("Bar",      no_wrap=True)
    t.add_column("Msgs",     justify="right", style="cyan")
    t.add_column("%",        justify="right", style="dim")

    for model, count in stats["model_usage"].most_common():
        pct = 100 * count / total
        t.add_row(model, rich_bar(count, max_count, 22, "violet"), fmt(count), f"{pct:.1f}%")

    console.print(t)
    pause()

def screen_vocab(stats):
    header("VOCABULARY SHOWDOWN")

    console.print("[bold]YOUR TOP 25 WORDS[/]  [dim](stop words removed)[/]")
    top_u = stats["word_freq_user"].most_common(25)
    max_u = top_u[0][1] if top_u else 1
    for word, count in top_u:
        console.print(f"  [cyan]{word:<22}[/] {rich_bar(count, max_u, 22, 'cyan')} {fmt(count)}")

    console.print()
    console.print("[bold]GPT's TOP 25 WORDS[/]  [dim](stop words removed)[/]")
    top_g = stats["word_freq_gpt"].most_common(25)
    max_g = top_g[0][1] if top_g else 1
    for word, count in top_g:
        console.print(f"  [violet]{word:<22}[/] {rich_bar(count, max_g, 22, 'violet')} {fmt(count)}")

    console.print()
    shared = set(w for w, _ in stats["word_freq_user"].most_common(200)) & \
             set(w for w, _ in stats["word_freq_gpt"].most_common(200))
    shared_counts = sorted([(w, stats["word_freq_user"][w] + stats["word_freq_gpt"][w])
                            for w in shared], key=lambda x: -x[1])[:15]
    max_s = shared_counts[0][1] if shared_counts else 1
    console.print("[bold]WORDS YOU BOTH OBSESS OVER[/]  [dim](top shared vocabulary)[/]")
    for word, count in shared_counts:
        console.print(f"  [bold]{word:<22}[/] {rich_bar(count, max_s, 22, 'yellow')} {fmt(count)}")

    pause()

def screen_prompting_evolution(stats):
    header("YOUR PROMPTING EVOLUTION OVER TIME")

    texts = stats["user_texts"]

    # group user messages by year
    # need timestamps — re-gather them
    all_user_msgs_by_year = defaultdict(list)

    cfg = load_config()
    folder = cfg.get("folder")
    if not folder:
        console.print("[red]No folder loaded.[/]")
        pause()
        return

    for fname in sorted(Path(folder).glob("conversations-*.json")):
        with open(fname) as f:
            convos = json.load(f)
        for convo in convos:
            for node in convo.get("mapping", {}).values():
                msg = node.get("message")
                if not msg or msg.get("author", {}).get("role") != "user":
                    continue
                text = extract_text(msg)
                if not text:
                    continue
                ts = msg.get("create_time") or convo.get("create_time")
                if ts:
                    year = ts_to_dt(ts).year
                    all_user_msgs_by_year[year].append(text)

    console.print("[bold]How your messages have changed year over year[/]\n")

    t = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
    t.add_column("Year",           style="bold", width=6)
    t.add_column("Msgs",           justify="right", style="cyan")
    t.add_column("Avg words/msg",  justify="right", style="cyan")
    t.add_column("Median words",   justify="right", style="dim")
    t.add_column("Questions",      justify="right", style="dim")
    t.add_column("200+ word msgs", justify="right", style="yellow")
    t.add_column("% with code",    justify="right", style="dim")

    for year in sorted(all_user_msgs_by_year.keys()):
        msgs = all_user_msgs_by_year[year]
        wcs  = [word_count(m) for m in msgs]
        avg  = sum(wcs) / len(wcs)
        med  = sorted(wcs)[len(wcs)//2]
        qs   = sum(m.count("?") for m in msgs)
        epic = sum(1 for wc in wcs if wc >= 200)
        code = sum(1 for m in msgs if "```" in m)
        code_pct = f"{100*code/len(msgs):.1f}%"
        t.add_row(str(year), fmt(len(msgs)), f"{avg:.1f}", str(med),
                  fmt(qs), fmt(epic), code_pct)

    console.print(t)

    console.print()
    console.print("[dim]Longer avg words/msg over time = you're giving more context.[/]")
    console.print("[dim]More epic messages = you started pasting larger inputs.[/]")
    console.print("[dim]Higher code % = more technical usage.[/]")
    pause()

# ── prompting analysis helpers ────────────────────────────────────────────────
CORRECTION_SIGNALS = [
    "actually","wait","nevermind","no that","not what i","that's not",
    "wrong","let me rephrase","let me clarify","scratch that","ignore that",
    "i meant","let's start over","start fresh","forget","no no","nope",
    "that wasn't","that isn't","not quite","not exactly","misunderstood",
]

FEATURE_PATTERNS = {
    "has_format":       ["bullet","table","list","step","numbered","format","markdown","step by step","step-by-step","outline"],
    "has_context":      ["i'm trying to","i am trying","i need this for","i'm building","the goal is",
                         "for context","background:","context:","i want to build","i need to","the reason"],
    "has_role":         ["act as","you are a","you're a","pretend","imagine you","role of","take the role"],
    "has_constraints":  ["don't","do not","only","avoid","make sure","never","without","must not","should not","except"],
    "has_example":      ["for example","like this","e.g.","such as","for instance","example:","like:","here's an example"],
    "has_length":       ["brief","detailed","concise","comprehensive","in depth","in-depth","quick","thorough","short answer","long"],
}

FEATURE_LABELS = {
    "has_format":      "format request  (bullet/table/list/step)",
    "has_context":     "context or reason  (I'm trying to / for context)",
    "has_role":        "role assignment  (act as...)",
    "has_constraints": "constraints  (don't/only/avoid)",
    "has_example":     "an example  (for example / such as)",
    "has_length":      "length preference  (brief/detailed/thorough)",
}

def get_thread(convo):
    """Walk current_node → root, return messages in chronological order."""
    mapping = convo.get("mapping", {})
    current = convo.get("current_node")
    thread  = []
    visited = set()
    node_id = current

    while node_id and node_id not in visited:
        visited.add(node_id)
        node = mapping.get(node_id, {})
        m    = node.get("message")
        if m:
            role = m.get("author", {}).get("role", "")
            text = extract_text(m)
            if role in ("user", "assistant") and text:
                thread.append({"role": role, "text": text,
                                "ts": m.get("create_time") or 0})
        node_id = node.get("parent")

    if not thread:  # fallback: sort all nodes by ts
        for node in mapping.values():
            m = node.get("message")
            if not m: continue
            role = m.get("author", {}).get("role", "")
            text = extract_text(m)
            if role in ("user", "assistant") and text:
                thread.append({"role": role, "text": text,
                                "ts": m.get("create_time") or 0})

    thread.sort(key=lambda x: x["ts"])
    return thread

def score_thread(thread):
    """Return efficiency dict for a conversation thread."""
    user_msgs = [m for m in thread if m["role"] == "user"]
    n = len(user_msgs)
    if n == 0:
        return None
    corrections = sum(1 for m in user_msgs
                      if any(s in m["text"].lower() for s in CORRECTION_SIGNALS))
    rate = corrections / n
    A = max(0.0, 1 - rate * 4)
    B = 1 / (1 + (n - 1) * 0.15)
    ts_vals = [m["ts"] for m in thread if m["ts"]]
    dur = min((max(ts_vals) - min(ts_vals)) / 60, 120) if len(ts_vals) >= 2 else 0
    C = max(0.0, 1 - dur / 60)
    return {
        "score":      round((0.45*A + 0.35*B + 0.20*C) * 100, 1),
        "user_msgs":  n,
        "corrections": corrections,
        "rate":        rate,
        "grade":       "🟢" if (0.45*A+0.35*B+0.20*C)*100 >= 65
                       else ("🟡" if (0.45*A+0.35*B+0.20*C)*100 >= 40 else "🔴"),
    }

def analyze_opener(text):
    tl = text.lower()
    feats = {k: any(p in tl for p in pats) for k, pats in FEATURE_PATTERNS.items()}
    feats["wc"] = word_count(text)
    feats["feature_count"] = sum(1 for v in feats.values() if v is True)
    return feats

def find_late_context(opener_text, later_user_msgs):
    """Keywords appearing 2+ times in later messages but NOT in opener."""
    opener_tokens = set(tokenize(opener_text.lower()))
    counts = Counter()
    for m in later_user_msgs:
        for tok in tokenize(m["text"]):
            if tok not in STOP_WORDS and tok not in opener_tokens and len(tok) > 3:
                counts[tok] += 1
    return [(w, c) for w, c in counts.most_common(8) if c >= 2]

def find_corrections_in_thread(user_msgs):
    out = []
    for i, m in enumerate(user_msgs[1:], 2):  # skip opener
        sigs = [s for s in CORRECTION_SIGNALS if s in m["text"].lower()]
        if sigs:
            snippet = m["text"].replace("\n", " ")[:90]
            out.append({"num": i, "snippet": snippet, "signals": sigs})
    return out[:6]  # cap at 6

def pearson_r(xs, ys):
    n = len(xs)
    if n < 5: return 0.0
    mx, my = sum(xs)/n, sum(ys)/n
    num  = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    denx = sum((x-mx)**2 for x in xs)
    deny = sum((y-my)**2 for y in ys)
    return num / (denx*deny)**0.5 if denx and deny else 0.0

def call_claude(api_key, prompt_text):
    import urllib.request, urllib.error
    url  = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt_text}],
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("x-api-key",           api_key)
    req.add_header("anthropic-version",   "2023-06-01")
    req.add_header("content-type",        "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["content"][0]["text"], None
    except urllib.error.HTTPError as e:
        return None, f"API error {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, str(e)

def build_prompt_analysis(convos):
    """Score every conversation, compute feature correlations. Returns analysis dict."""
    profiles = []
    for convo in convos:
        thread = get_thread(convo)
        sc     = score_thread(thread)
        if not sc or sc["user_msgs"] < 2:
            continue
        user_msgs = [m for m in thread if m["role"] == "user"]
        opener    = user_msgs[0]["text"]
        feats     = analyze_opener(opener)
        title_lc  = (convo.get("title") or "").lower()
        cat = "🗂️  Other / Misc"
        for c, kws in (stats.get("categories") or {}).items():
            if any(k in title_lc for k in kws):
                cat = c; break
        profiles.append({**sc, "opener": opener, "feats": feats,
                         "title": convo.get("title","Untitled"), "cat": cat,
                         "convo": convo})

    if not profiles:
        return None

    # feature correlations
    corrs = {}
    scores = [p["score"] for p in profiles]
    for feat in FEATURE_PATTERNS:
        xs = [1.0 if p["feats"][feat] else 0.0 for p in profiles]
        r  = pearson_r(xs, scores)
        with_f    = [p["score"] for p in profiles if p["feats"][feat]]
        without_f = [p["score"] for p in profiles if not p["feats"][feat]]
        mean_w  = sum(with_f)/len(with_f)    if with_f    else 0
        mean_wo = sum(without_f)/len(without_f) if without_f else 0
        corrs[feat] = {"r": r, "lift": mean_w-mean_wo,
                       "mean_with": mean_w, "mean_without": mean_wo,
                       "n_with": len(with_f), "n_without": len(without_f)}

    # opener word-count sweet spot
    wc_buckets = {"<10": [], "10-25": [], "25-50": [], "50-100": [], "100+": []}
    for p in profiles:
        wc = p["feats"]["wc"]
        if wc < 10:       wc_buckets["<10"].append(p["score"])
        elif wc < 25:     wc_buckets["10-25"].append(p["score"])
        elif wc < 50:     wc_buckets["25-50"].append(p["score"])
        elif wc < 100:    wc_buckets["50-100"].append(p["score"])
        else:             wc_buckets["100+"].append(p["score"])
    wc_avgs = {k: sum(v)/len(v) if v else 0 for k, v in wc_buckets.items()}

    # category scores
    cat_scores = defaultdict(list)
    for p in profiles:
        cat_scores[p["cat"]].append(p["score"])
    cat_avgs = {c: sum(v)/len(v) for c, v in cat_scores.items() if len(v) >= 3}

    # worst convos for walkthrough
    worst = sorted(profiles, key=lambda p: p["score"])[:20]

    return {"profiles": profiles, "corrs": corrs, "wc_avgs": wc_avgs,
            "cat_avgs": cat_avgs, "worst": worst}

# ── screen: become a better prompter ─────────────────────────────────────────
def screen_better_prompter(stats, convos):
    header("🧠  PROMPT ANALYZER")
    console.print("[dim]Analyzing your conversation patterns — this takes a moment…[/]\n")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  transient=True, console=console) as p:
        t = p.add_task("Scoring conversations…", total=None)
        analysis = build_prompt_analysis(convos)
        p.update(t, description="Done!")

    if not analysis:
        console.print("[red]Not enough multi-turn conversations to analyze.[/]")
        pause(); return

    profiles = analysis["profiles"]
    corrs    = analysis["corrs"]
    wc_avgs  = analysis["wc_avgs"]
    cat_avgs = analysis["cat_avgs"]
    worst    = analysis["worst"]
    n        = len(profiles)

    avg_score = sum(p["score"] for p in profiles) / n
    score_dist = Counter()
    for p in profiles:
        s = p["score"]
        if s < 20:   score_dist["0-20   🔴 struggling"] += 1
        elif s < 40: score_dist["20-40  🔴 rough"] += 1
        elif s < 60: score_dist["40-60  🟡 okay"] += 1
        elif s < 80: score_dist["60-80  🟢 solid"] += 1
        else:        score_dist["80-100 🟢 sharp"] += 1

    # ── section 1: efficiency overview ────────────────────────────────────────
    console.rule("[bold]YOUR PROMPTING EFFICIENCY[/]")
    console.print(f"\n  Analyzed [bold cyan]{fmt(n)}[/] multi-turn conversations\n")

    dist_order = ["0-20   🔴 struggling","20-40  🔴 rough","40-60  🟡 okay",
                  "60-80  🟢 solid","80-100 🟢 sharp"]
    max_dist = max(score_dist.values()) if score_dist else 1
    for label in dist_order:
        count = score_dist.get(label, 0)
        pct   = 100 * count / n
        console.print(f"  {label}  {rich_bar(count, max_dist, 26, 'cyan')} {count:>5}  ({pct:.1f}%)")

    color = "green" if avg_score >= 60 else ("yellow" if avg_score >= 40 else "red")
    console.print(f"\n  Your average efficiency score: [{color}][bold]{avg_score:.1f} / 100[/][/]")

    # ── section 2: what actually helps ────────────────────────────────────────
    console.print()
    console.rule("[bold]WHAT WORKS — FEATURE CORRELATIONS[/]")
    console.print("\n  [dim]Does adding X to your opener actually improve the conversation?[/]\n")

    t = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
    t.add_column("Feature",         style="bold",  min_width=38)
    t.add_column("You use it",       justify="right", style="dim")
    t.add_column("Avg score WITH",   justify="right", style="cyan")
    t.add_column("Avg score WITHOUT",justify="right", style="dim")
    t.add_column("Lift",             justify="right")
    t.add_column("Signal",           justify="center")

    for feat, label in FEATURE_LABELS.items():
        c    = corrs[feat]
        lift = c["lift"]
        if c["n_with"] < 5:
            sig = "[dim]too few[/]"
        elif lift >= 5:
            sig = "[green]strong ↑[/]"
        elif lift >= 2:
            sig = "[green]helps ↑[/]"
        elif lift <= -5:
            sig = "[red]hurts ↓[/]"
        elif lift <= -2:
            sig = "[yellow]slight ↓[/]"
        else:
            sig = "[dim]neutral[/]"
        lift_str = f"[green]+{lift:.1f}[/]" if lift > 0 else f"[red]{lift:.1f}[/]"
        t.add_row(label, f"{c['n_with']}/{n}", f"{c['mean_with']:.1f}",
                  f"{c['mean_without']:.1f}", lift_str, sig)
    console.print(t)

    # ── section 3: opener word count sweet spot ───────────────────────────────
    console.print()
    console.rule("[bold]OPENER LENGTH SWEET SPOT[/]")
    console.print("\n  [dim]Avg efficiency score by how many words your opening message had[/]\n")
    max_wc_avg = max(wc_avgs.values()) if wc_avgs else 1
    for bucket, avg in wc_avgs.items():
        marker = " ← sweet spot" if avg == max(wc_avgs.values()) else ""
        console.print(f"  [dim]{bucket:<10}[/]  {rich_bar(avg, 100, 26, 'cyan')}  {avg:.1f}{marker}")

    # ── section 4: by category ─────────────────────────────────────────────────
    if cat_avgs:
        console.print()
        console.rule("[bold]EFFICIENCY BY TOPIC[/]")
        console.print("\n  [dim]Where you prompt well vs where you struggle most[/]\n")
        max_ca = max(cat_avgs.values())
        for cat, avg in sorted(cat_avgs.items(), key=lambda x: -x[1]):
            color = "green" if avg >= 60 else ("yellow" if avg >= 40 else "red")
            console.print(f"  {cat:<28}  {rich_bar(avg, 100, 22, color)}  [{color}]{avg:.1f}[/]")
        best  = max(cat_avgs, key=cat_avgs.get)
        worst_cat = min(cat_avgs, key=cat_avgs.get)
        console.print(f"\n  [green]Best:[/]  {best}  ({cat_avgs[best]:.1f})")
        console.print(f"  [red]Worst:[/] {worst_cat}  ({cat_avgs[worst_cat]:.1f})")

    # ── section 5: personal rules ─────────────────────────────────────────────
    console.print()
    rules = []
    # top 3 features by lift (positive only, n_with >= 10)
    top_feats = sorted([(f, c) for f, c in corrs.items()
                         if c["lift"] > 2 and c["n_with"] >= 10],
                       key=lambda x: -x[1]["lift"])[:3]
    for feat, c in top_feats:
        rules.append(
            f"Include [bold]{FEATURE_LABELS[feat].split('(')[0].strip()}[/] — "
            f"your conversations score +{c['lift']:.1f} pts higher on average "
            f"({c['mean_with']:.0f} vs {c['mean_without']:.0f} without it)"
        )
    # word count rule
    best_wc_bucket = max(wc_avgs, key=wc_avgs.get)
    rules.append(
        f"Aim for openers in the [bold]{best_wc_bucket} word range[/] — "
        f"your highest avg efficiency is {wc_avgs[best_wc_bucket]:.1f}/100 there"
    )
    if cat_avgs:
        best  = max(cat_avgs, key=cat_avgs.get)
        worst_cat = min(cat_avgs, key=cat_avgs.get)
        if cat_avgs[best] - cat_avgs[worst_cat] > 10:
            rules.append(
                f"Apply your [bold]{best.split()[1]}[/] prompting style to "
                f"[bold]{worst_cat.split()[1]}[/] conversations — "
                f"there's a {cat_avgs[best]-cat_avgs[worst_cat]:.0f} pt gap to close"
            )

    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules[:5]))
    console.print(Panel(rules_text, title="[bold green]YOUR PERSONAL RULES[/]",
                        border_style="green"))

    # ── section 6: coaching walkthrough ───────────────────────────────────────
    console.print()
    do_walk = questionary.confirm(
        "  Walk through your worst conversations for coaching?",
        default=True, style=MENU_STYLE,
    ).ask()
    if not do_walk:
        pause(); return

    # get API key (optional)
    cfg     = load_config()
    api_key = cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print()
        want_key = questionary.confirm(
            "  Add Anthropic API key for Claude-powered opener rewrites?",
            default=False, style=MENU_STYLE,
        ).ask()
        if want_key:
            api_key = questionary.text(
                "  Paste your API key (sk-ant-...):",
                style=MENU_STYLE,
            ).ask()
            if api_key and api_key.startswith("sk-ant"):
                cfg["anthropic_api_key"] = api_key
                save_config(cfg)
                console.print("[green]✓ Key saved.[/]")
            else:
                api_key = None
                console.print("[dim]Skipping — you can add it later via Change Folder.[/]")

    worst_candidates = [p for p in worst if p["score"] < 55 and p["user_msgs"] >= 4][:10]
    if not worst_candidates:
        worst_candidates = worst[:10]

    for idx, profile in enumerate(worst_candidates):
        convo    = profile["convo"]
        thread   = get_thread(convo)
        user_msgs = [m for m in thread if m["role"] == "user"]
        gpt_msgs  = [m for m in thread if m["role"] == "assistant"]
        opener    = user_msgs[0]["text"] if user_msgs else ""
        feats     = profile["feats"]
        sc        = profile

        console.print()
        console.rule(f"[bold]CONVERSATION {idx+1} of {len(worst_candidates)}[/]")
        console.print(
            f"\n  [bold]\"{profile['title']}\"[/]\n"
            f"  Efficiency: [{('green' if sc['score']>=60 else 'yellow' if sc['score']>=40 else 'red')}]"
            f"[bold]{sc['score']:.0f}/100[/][/] {sc['grade']}  |  "
            f"Your messages: [cyan]{sc['user_msgs']}[/]  |  "
            f"Corrections: [red]{sc['corrections']}[/]  |  "
            f"Pivot rate: {sc['rate']:.2f}"
        )

        # opener
        opener_preview = opener[:400] + ("…" if len(opener) > 400 else "")
        console.print()
        console.print(Panel(
            f"[white]{opener_preview}[/]\n\n[dim]{feats['wc']} words[/]",
            title="[bold yellow]YOUR OPENER[/]", border_style="yellow"
        ))

        # what was missing
        missing = [label for feat, label in FEATURE_LABELS.items() if not feats[feat]]
        if missing:
            console.print("\n  [bold red]MISSING FROM YOUR OPENER:[/]")
            for m in missing:
                console.print(f"    [red]✗[/] {m}")

        # corrections
        corrections = find_corrections_in_thread(user_msgs)
        if corrections:
            console.print("\n  [bold red]WHERE IT WENT SIDEWAYS:[/]")
            for c in corrections:
                snip = c["snippet"][:80]
                console.print(f"    [dim]msg {c['num']:>2}[/]  [red]\"{snip}\"[/]")

        # late context
        late = find_late_context(opener, user_msgs[1:])
        if late:
            console.print("\n  [bold yellow]CONTEXT THAT ARRIVED TOO LATE:[/]")
            for word, count in late:
                console.print(f"    [yellow]→[/] [bold]{word}[/]  [dim](mentioned {count}× after opener)[/]")
            console.print(f"\n  [dim]{len(late)} key pieces of context were missing from your opener.[/]")

        # what it eventually became
        if gpt_msgs:
            final_preview = gpt_msgs[-1]["text"].replace("\n", " ")[:200]
            console.print()
            console.print(Panel(
                f"[dim]{final_preview}…[/]",
                title="[bold green]WHAT IT EVENTUALLY BECAME  (last GPT response)[/]",
                border_style="green dim"
            ))

        # quick stat
        saved_estimate = max(0, sc["user_msgs"] - 3)
        console.print(f"\n  [dim]A complete opener might have saved ~{saved_estimate} back-and-forth messages.[/]")

        # options
        console.print()
        action = questionary.select(
            "  What next?",
            choices=[
                "→ Next conversation",
                "✨ Claude: rewrite this opener",
                "⏭  Skip to end",
            ] if api_key else [
                "→ Next conversation",
                "⏭  Skip to end",
            ],
            style=MENU_STYLE,
        ).ask()

        if action is None or "Skip" in action:
            break

        if "Claude" in (action or ""):
            late_str = "\n".join(f"- {w} (mentioned {c}x after opener)" for w, c in late) or "none detected"
            corr_str = "\n".join(f"- msg {c['num']}: \"{c['snippet']}\"" for c in corrections) or "none detected"
            final_snip = gpt_msgs[-1]["text"][:500] if gpt_msgs else "unknown"

            rewrite_prompt = f"""You are a prompt engineering coach. Here is a real ChatGPT conversation that was inefficient.

OPENING MESSAGE (what they actually typed):
"{opener[:500]}"

KEY CONTEXT THAT ONLY APPEARED LATER IN THE CONVERSATION:
{late_str}

CORRECTIONS / PIVOTS MADE (showing what was misunderstood):
{corr_str}

WHAT THE CONVERSATION WAS REALLY ABOUT (final GPT response):
"{final_snip}"

Task: Write a single improved opening message that would have reached the same result in 1-3 exchanges instead of {sc['user_msgs']}.

Rules:
- Match the user's casual/technical tone exactly — don't be formal
- Include all the missing context upfront
- Be specific about format and constraints
- Keep it under 120 words

Output format:
REWRITTEN OPENER:
[the improved prompt]

WHY THIS IS BETTER:
[2-3 bullet points — be specific about what was added]"""

            console.print("\n  [dim]Asking Claude…[/]")
            response, err = call_claude(api_key, rewrite_prompt)
            if err:
                console.print(f"  [red]Error: {err}[/]")
            else:
                console.print()
                console.print(Panel(
                    f"[white]{response}[/]",
                    title="[bold violet]✨ CLAUDE'S REWRITE[/]",
                    border_style="violet"
                ))
            questionary.press_any_key_to_continue("  [press any key to continue]").ask()

    console.print()
    console.print(Panel(
        f"  Reviewed [bold]{min(idx+1, len(worst_candidates))}[/] of your worst conversations.\n"
        f"  Your avg efficiency: [bold]{avg_score:.1f}/100[/]\n\n"
        f"  Top habit to build: [green]{rules[0] if rules else 'add more context to openers'}[/]",
        title="[bold]SESSION SUMMARY[/]", border_style="violet"
    ))
    pause()


def screen_rabbit_holes(stats):
    header("RABBIT HOLE DETECTOR")
    console.print("[dim]Conversations with the most corrections & pivots — where you were really thinking out loud.[/]\n")

    CORRECTION_SIGNALS = [
        "actually","wait","nevermind","no that","not what i","that's not",
        "wrong","let me rephrase","let me clarify","scratch that","ignore that",
        "i meant","let's start over","start fresh","forget",
    ]

    cfg = load_config()
    folder = cfg.get("folder")
    if not folder:
        console.print("[red]No folder loaded.[/]")
        pause()
        return

    convo_pivot_scores = {}
    for fname in sorted(Path(folder).glob("conversations-*.json")):
        with open(fname) as f:
            convos = json.load(f)
        for convo in convos:
            cid   = convo.get("id") or convo.get("conversation_id", "")
            title = convo.get("title", "Untitled")
            pivot_count = 0
            user_msg_count = 0
            for node in convo.get("mapping", {}).values():
                msg = node.get("message")
                if not msg or msg.get("author", {}).get("role") != "user":
                    continue
                text = extract_text(msg).lower()
                if not text:
                    continue
                user_msg_count += 1
                pivot_count += sum(1 for sig in CORRECTION_SIGNALS if sig in text)
            if user_msg_count >= 5 and pivot_count > 0:
                convo_pivot_scores[cid] = {
                    "title": title,
                    "pivots": pivot_count,
                    "msgs": user_msg_count,
                    "ratio": pivot_count / user_msg_count,
                }

    top_rabbit_holes = sorted(convo_pivot_scores.values(), key=lambda x: -x["pivots"])[:15]

    t = Table(title="Most Pivot-Heavy Conversations", box=box.ROUNDED, border_style="dim",
              header_style="bold violet", padding=(0,1))
    t.add_column("#",          style="dim", width=3)
    t.add_column("Title",      max_width=40)
    t.add_column("Pivots",     justify="right", style="red")
    t.add_column("Your msgs",  justify="right", style="dim")
    t.add_column("Pivot rate", justify="right", style="yellow")

    for i, c in enumerate(top_rabbit_holes, 1):
        t.add_row(str(i), c["title"][:40], str(c["pivots"]),
                  str(c["msgs"]), f"{c['ratio']:.2f}")

    console.print(t)
    console.print("\n[dim]Pivot rate = corrections per user message. Higher = more back-and-forth thinking.[/]")
    pause()

def screen_productivity_pulse(stats, convos):
    header("📡  PRODUCTIVITY PULSE")

    console.print(Panel(
        "  [bold]Pivot Rate[/] — the fraction of your messages in a conversation that contain a\n"
        "  correction or redirect (e.g. \"actually\", \"wait\", \"never mind\", \"that's not what I meant\").\n"
        "  A pivot rate of 0.10 means 1 in 10 of your messages was walking something back.\n\n"
        "  [bold]Focus Score[/] — a 0–100 score per year combining your pivot rate (60% weight)\n"
        "  and how consistently you used ChatGPT week-over-week (40% weight).\n"
        "  Higher = cleaner prompting + more regular usage.",
        title="[dim]What these metrics mean[/]", border_style="dim"
    ))

    # ── build per-convo data in one pass ──────────────────────────────────────
    month_corrections   = defaultdict(list)
    year_data           = defaultdict(lambda: {"rates": [], "weeks": set()})
    pivot_pattern_counts = Counter()   # which signal words appear most
    worst_pivot_convos  = []           # for the "where you veered" examples

    for convo in convos:
        create_ts = convo.get("create_time")
        if not create_ts:
            continue
        dt        = ts_to_dt(create_ts)
        month_key = dt.strftime("%Y-%m")
        year      = dt.year
        week      = dt.strftime("%Y-%W")
        title     = (convo.get("title") or "Untitled")

        user_msgs_raw = []
        for node in convo.get("mapping", {}).values():
            msg = node.get("message")
            if not msg or msg.get("author", {}).get("role") != "user":
                continue
            text = extract_text(msg)
            if text:
                user_msgs_raw.append({"text": text, "lc": text.lower()})

        if len(user_msgs_raw) < 2:
            continue

        pivot_msgs = []
        for i, m in enumerate(user_msgs_raw[1:], 2):   # skip opener
            hits = [s for s in CORRECTION_SIGNALS if s in m["lc"]]
            if hits:
                pivot_msgs.append({"num": i, "snippet": m["text"][:80], "signals": hits})
                for h in hits:
                    pivot_pattern_counts[h] += 1

        rate = len(pivot_msgs) / len(user_msgs_raw)
        month_corrections[month_key].append(rate)
        year_data[year]["rates"].append(rate)
        year_data[year]["weeks"].add(week)

        if pivot_msgs:
            worst_pivot_convos.append({
                "title":      title,
                "rate":       rate,
                "user_msgs":  len(user_msgs_raw),
                "pivots":     pivot_msgs,
            })

    worst_pivot_convos.sort(key=lambda x: -x["rate"])

    # ── Section 1: Veer rate by month ─────────────────────────────────────────
    console.print()
    console.rule("[bold]PIVOT RATE BY MONTH[/]")
    console.print(
        "\n  [dim]Each bar = avg pivot rate across all conversations that month.\n"
        "  🟢 < 7%  focused   🟡 7–15%  some wandering   🔴 > 15%  scattered[/]\n"
    )

    if month_corrections:
        month_avgs = {m: sum(v)/len(v) for m, v in month_corrections.items()}
        max_rate   = max(month_avgs.values()) or 0.01
        for month in sorted(month_avgs):
            avg    = month_avgs[month]
            label  = datetime.strptime(month, "%Y-%m").strftime("%b %Y")
            n      = len(month_corrections[month])
            color  = "red" if avg > 0.15 else ("yellow" if avg > 0.07 else "green")
            console.print(
                f"  [dim]{label}[/]  {rich_bar(avg, max(max_rate, 0.01), 30, color)}"
                f"  [{color}]{avg:.2%}[/]  [dim]({n} convos)[/]"
            )
    else:
        console.print("  [dim]Not enough data.[/]")

    # ── Section 2: Where you actually veered — real examples ──────────────────
    console.print()
    console.rule("[bold]WHERE YOU ACTUALLY VEERED — REAL EXAMPLES[/]")
    console.print(
        "\n  [dim]Your conversations with the highest pivot rates, showing the exact moments\n"
        "  you redirected GPT. These are the conversations where a better opener\n"
        "  would have saved the most back-and-forth.[/]\n"
    )

    for i, c in enumerate(worst_pivot_convos[:5], 1):
        color = "red" if c["rate"] > 0.15 else "yellow"
        console.print(
            f"  [bold]{i}. {c['title'][:55]}[/]\n"
            f"     Pivot rate: [{color}][bold]{c['rate']:.0%}[/][/]  "
            f"— {c['user_msgs']} messages, {len(c['pivots'])} pivots"
        )
        for p in c["pivots"][:3]:
            snip  = p["snippet"].replace("\n", " ")[:72]
            flags = ", ".join(f'"{s}"' for s in p["signals"][:2])
            console.print(f"     [dim]msg {p['num']:>2}[/]  [red]→[/] {snip}")
            console.print(f"            [dim]triggered by: {flags}[/]")
        if len(c["pivots"]) > 3:
            console.print(f"     [dim]  … and {len(c['pivots'])-3} more pivot(s)[/]")
        console.print()

    # ── Section 3: Common pivot patterns ──────────────────────────────────────
    console.rule("[bold]YOUR MOST COMMON PIVOT PATTERNS[/]")
    console.print(
        "\n  [dim]The exact words/phrases you used most when correcting GPT.\n"
        "  High counts on a single signal = a habitual correction pattern.[/]\n"
    )

    top_patterns = pivot_pattern_counts.most_common(15)
    if top_patterns:
        max_p = top_patterns[0][1]
        pt = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
        pt.add_column("Pivot phrase",  style="bold", min_width=22)
        pt.add_column("Bar",           no_wrap=True)
        pt.add_column("Times used",    justify="right", style="red")
        pt.add_column("What it signals", style="dim", max_width=35)

        SIGNAL_MEANINGS = {
            "actually":        "You realized mid-convo what you really meant",
            "wait":            "You caught something wrong before GPT continued",
            "nevermind":       "GPT went in the wrong direction entirely",
            "no that":         "Direct rejection — GPT missed the mark",
            "not what i":      "Misalignment between your intent and GPT's output",
            "that's not":      "GPT produced the wrong thing",
            "wrong":           "Factual or directional error from GPT",
            "let me rephrase": "Your original prompt was unclear",
            "let me clarify":  "Adding context you forgot to include",
            "scratch that":    "Full reset — starting the answer over",
            "ignore that":     "You sent something by mistake",
            "i meant":         "Clarifying your original intent",
            "let's start over":"Complete restart of the approach",
            "start fresh":     "Abandoning the current thread entirely",
            "forget":          "Dismissing previous context to reset",
            "no no":           "Emphatic rejection — frustration signal",
            "nope":            "Quick rejection, usually directional",
            "that wasn't":     "GPT's output didn't match what you needed",
            "that isn't":      "Output mismatch",
            "not quite":       "Close but not right — minor correction",
            "not exactly":     "Close but not right — minor correction",
            "misunderstood":   "GPT interpreted your request incorrectly",
        }
        for phrase, count in top_patterns:
            meaning = SIGNAL_MEANINGS.get(phrase, "Correction or redirect")
            pt.add_row(f'"{phrase}"', rich_bar(count, max_p, 20, "red"),
                       fmt(count), meaning)
        console.print(pt)
    else:
        console.print("  [dim]No pivot patterns found.[/]")

    # ── Section 4: Semantic Topic Repeats ─────────────────────────────────────
    console.print()
    console.rule("[bold]TOPICS YOU REVISITED  (semantic repeat detector)[/]")

    GENERIC_TITLES = {"new chat","new conversation","untitled","chat","conversation"}

    # Build corpus: title + first user message snippet for richer signal
    corpus_meta = []   # (display_title, text_for_embedding)
    for convo in convos:
        raw_title = (convo.get("title") or "").strip()
        if not raw_title or raw_title.lower() in GENERIC_TITLES:
            continue
        # grab first user message for extra context
        first_user = ""
        for node in convo.get("mapping", {}).values():
            msg = node.get("message")
            if msg and msg.get("author", {}).get("role") == "user":
                first_user = extract_text(msg)[:200]
                break
        embed_text = f"{raw_title}. {first_user}".strip()
        corpus_meta.append((raw_title, embed_text))

    similar_pairs = []
    method = "keyword"

    try:
        from fastembed import TextEmbedding
        import numpy as np
        method = "semantic"
        console.print(
            "\n  [green]✓ fastembed loaded[/] — using [bold]semantic embeddings[/] "
            "(catches meaning, not just word overlap)\n"
        )
        texts = [t for _, t in corpus_meta]
        titles = [t for t, _ in corpus_meta]

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      transient=True, console=console) as prog:
            task = prog.add_task(f"Embedding {len(texts)} conversations…", total=None)
            model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(MODELS_DIR))
            embeddings = np.array(list(model.embed(texts)), dtype="float32")
            # normalize for cosine similarity
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / np.maximum(norms, 1e-9)
            prog.update(task, description="Done!")

        # batch cosine similarity: dot product of normalized vecs = cosine sim
        sim_matrix = embeddings @ embeddings.T
        n = len(titles)
        for i in range(n):
            for j in range(i + 1, n):
                score = float(sim_matrix[i, j])
                if score >= 0.80:
                    similar_pairs.append((score, titles[i], titles[j]))

    except ImportError:
        console.print(
            "\n  [yellow]fastembed not installed[/] — falling back to keyword overlap.\n"
            "  [dim]For semantic detection (catches 'novel strategy' ≈ 'new strategy'),\n"
            "  run: [bold]pip install fastembed[/]  (~80MB, no PyTorch needed)[/]\n"
        )
        # Jaccard fallback on title words only
        title_word_sets = []
        for raw_title, _ in corpus_meta:
            words = set(re.findall(r"\b[a-z]{3,}\b", raw_title.lower())) - STOP_WORDS
            title_word_sets.append((raw_title, words))
        for i in range(len(title_word_sets)):
            for j in range(i + 1, len(title_word_sets)):
                t1, w1 = title_word_sets[i]
                t2, w2 = title_word_sets[j]
                union = w1 | w2
                inter = w1 & w2
                if not union: continue
                score = len(inter) / len(union)
                if score > 0.4:
                    similar_pairs.append((score, t1, t2))

    similar_pairs.sort(key=lambda x: -x[0])
    top_pairs = similar_pairs[:15]

    threshold_note = "≥ 80% semantic similarity" if method == "semantic" else "≥ 40% keyword overlap"
    console.print(
        f"  [dim]Showing conversation pairs with {threshold_note}.\n"
        f"  These are topics you may have re-started instead of continuing.[/]\n"
    )

    if top_pairs:
        tp = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
        tp.add_column("#",               style="dim", width=3)
        tp.add_column("Conversation A",  max_width=36)
        tp.add_column("Conversation B",  max_width=36)
        tp.add_column("Similarity",      justify="right",
                      style="green" if method == "semantic" else "yellow")
        for i, (score, t1, t2) in enumerate(top_pairs, 1):
            tp.add_row(str(i), t1[:36], t2[:36], f"{score:.0%}")
        console.print(tp)
        if method == "semantic":
            console.print(
                f"\n  [dim]Method: semantic embeddings (BAAI/bge-small-en-v1.5) · "
                f"runs 100% locally · no data sent anywhere[/]"
            )
    else:
        console.print("  [dim]No similar conversation pairs found.[/]")

    # ── Section 5: Focus Score by Year ────────────────────────────────────────
    console.print()
    console.rule("[bold]FOCUS SCORE BY YEAR[/]")
    console.print(
        "\n  [dim]Focus Score (0–100) = 60% from how clean your prompting was (low pivot rate)\n"
        "             + 40% from how consistently you used ChatGPT each week.\n"
        "  100 = almost no corrections + used it every week.\n"
        "  Lower = more scattered usage or more back-and-forth prompting.[/]\n"
    )

    tf = Table(box=box.ROUNDED, border_style="dim", header_style="bold violet", padding=(0,1))
    tf.add_column("Year",                        style="bold", width=6)
    tf.add_column("Convos",                      justify="right", style="cyan")
    tf.add_column("Avg pivot rate (↓ = cleaner)",justify="right", style="yellow")
    tf.add_column("Convos/week (consistency)",   justify="right", style="dim")
    tf.add_column("Focus Score (0–100)",         justify="right", style="bold")

    for year in sorted(year_data.keys()):
        yd       = year_data[year]
        n        = len(yd["rates"])
        n_weeks  = max(len(yd["weeks"]), 1)
        avg_rate = sum(yd["rates"]) / n if n else 0
        cpw      = n / n_weeks
        corr_s   = max(0.0, 1.0 - avg_rate * 5)
        usage_s  = min(1.0, cpw / 5)
        focus    = round((corr_s * 0.6 + usage_s * 0.4) * 100, 1)
        color    = "green" if focus >= 60 else ("yellow" if focus >= 35 else "red")
        tf.add_row(str(year), fmt(n), f"{avg_rate:.1%}",
                   f"{cpw:.1f}", f"[{color}]{focus:.1f}[/]")

    console.print(tf)
    pause()


def screen_about(stats):
    header("ℹ️   ABOUT")

    console.print()
    console.print("[bold]╔══════════════════════════════════════════════════════════════╗[/]")
    console.print("[bold]║              ChatGPT History Analyzer                       ║[/]")
    console.print("[bold]║              Designed by Aaron & Eli D.                     ║[/]")
    console.print("[bold]╚══════════════════════════════════════════════════════════════╝[/]")
    console.print()

    coming_soon = (
        "🔍  Conversation Search — find any conversation by keyword across your full history\n"
        "📡  Productivity Pulse — track focus vs scatter month over month  [IN PROGRESS]\n"
        "🕸️  Idea Network — map how your topics connect and evolve over time\n"
        "🔄  Through-Loop Processing — analyze your own thought patterns within conversations;\n"
        "    see how your questions evolve as you get closer to what you actually need\n"
        "🧬  Self-Improvement Engine — use your full history to identify knowledge gaps,\n"
        "    recurring blind spots, and growth areas. DATA IS KING.\n"
        "📊  Collaboration Mode — share anonymized stats with a friend and compare prompting styles\n"
        "📤  Export — generate a beautiful PDF/HTML report of your stats\n"
        "🗓️  Knowledge Timeline — \"when did you first learn X?\" mapped across your history"
    )

    console.print(Panel(
        coming_soon,
        title="[bold violet]COMING SOON — FEATURES IN THE PIPELINE[/]",
        border_style="violet",
        padding=(1, 2),
    ))

    console.print()
    console.rule("[dim]")
    console.print()
    console.print("  [dim]Built with Python · rich · questionary[/]")
    console.print("  [dim]No data ever leaves your machine.[/]")

    pause()


# ── your story screen ─────────────────────────────────────────────────────────
def screen_your_story(stats):
    """
    3-4 sentence narrative of recent activity + one past insight worth revisiting.
    Shown automatically once per folder load; also accessible from the main menu.
    """
    header("📖  YOUR STORY")

    convos_data = stats.get("convos", {})
    if not convos_data:
        console.print("  [dim]Load a folder to see your story.[/]")
        pause()
        return

    now = datetime.now(tz=timezone.utc).timestamp()

    # ── Recent activity (last 30 days) ────────────────────────────────────────
    recent_cutoff = now - 30 * 86400
    week_cutoff   = now - 7  * 86400

    recent = [c for c in convos_data.values() if (c.get("ts") or 0) >= recent_cutoff]
    this_week = [c for c in convos_data.values() if (c.get("ts") or 0) >= week_cutoff]

    # Top topics from recent convos via title word frequency
    recent_words = Counter()
    STORY_STOPS = STOP_WORDS | {
        "new","using","help","create","build","make","add","get","set","update",
        "fix","question","issue","problem","work","working","test","need","vs",
        "python","code","script","function","class","error","api","way","how",
    }
    for c in recent:
        title = (c.get("title") or "").lower()
        recent_words.update(
            w for w in re.findall(r"\b[a-z]{4,}\b", title) if w not in STORY_STOPS
        )
    top_topics = [w for w, _ in recent_words.most_common(4)]

    # Peak recent hour (evening/morning/night)
    msgs_per_hour = stats.get("msgs_per_hour", Counter())
    if msgs_per_hour:
        peak_hr = max(msgs_per_hour, key=msgs_per_hour.get)
        if   5  <= peak_hr < 11: time_note = "mostly in the mornings"
        elif 11 <= peak_hr < 17: time_note = "mostly during the day"
        elif 17 <= peak_hr < 22: time_note = "mostly in the evenings"
        else:                    time_note = "mostly late at night"
    else:
        time_note = ""

    # Longest recent convo
    if recent:
        deepest = max(recent, key=lambda c: c.get("msgs", 0))
        deepest_title = (deepest.get("title") or "Untitled")[:48]
        deepest_msgs  = deepest.get("msgs", 0)
    else:
        deepest = deepest_title = deepest_msgs = None

    # ── Build narrative ───────────────────────────────────────────────────────
    console.print()

    n_week   = len(this_week)
    n_recent = len(recent)
    span_days = max(0, stats.get("latest_ts", 0) - stats.get("earliest_ts", 0)) / 86400
    span_yrs  = span_days / 365

    if span_yrs >= 2:
        span_str = f"{span_yrs:.1f} years"
    elif span_days >= 30:
        span_str = f"{int(span_days / 30)} months"
    else:
        span_str = f"{int(span_days)} days"

    total_convos = len(convos_data)

    # Sentence 1 — volume + span
    console.print(
        f"  You've had [bold]{fmt(total_convos)}[/] conversations over [bold]{span_str}[/]. "
        f"You remember maybe {min(12, total_convos // 100 + 3)}."
    )
    console.print()

    # Sentence 2 — recent activity
    if n_week > 0:
        topic_str = (
            ", ".join(top_topics[:2]) if top_topics else "a range of topics"
        )
        console.print(
            f"  This week: [bold]{n_week}[/] conversation{'s' if n_week != 1 else ''}"
            + (f", mostly about [bold]{topic_str}[/]" if top_topics else "")
            + (f". Working {time_note}." if time_note else ".")
        )
    elif n_recent > 0:
        topic_str = ", ".join(top_topics[:2]) if top_topics else "various topics"
        console.print(
            f"  This month: [bold]{n_recent}[/] conversations"
            + (f", mostly about [bold]{topic_str}[/]" if top_topics else "")
            + "."
        )
    else:
        console.print("  [dim]No conversations in the last 30 days.[/]")

    # Sentence 3 — deepest session
    if deepest and deepest_msgs >= 5:
        console.print()
        console.print(
            f"  Your deepest session this month: [bold]\"{deepest_title}\"[/] "
            f"— [bold]{deepest_msgs}[/] messages."
        )

    # ── Insight from the past ─────────────────────────────────────────────────
    past_cutoff_hi = now - 90  * 86400
    past_cutoff_lo = now - 540 * 86400
    past_candidates = [
        c for c in convos_data.values()
        if past_cutoff_lo <= (c.get("ts") or 0) <= past_cutoff_hi
        and c.get("msgs", 0) >= 8
        and (c.get("title") or "").lower() not in {"new chat","untitled",""}
    ]
    if past_candidates:
        import random as _random
        _random.seed(int(now / 86400))   # changes daily, not every render
        insight = _random.choice(past_candidates)
        age_days = (now - (insight.get("ts") or 0)) / 86400
        if age_days >= 60:
            age_str = f"{int(age_days / 30)} months ago"
        else:
            age_str = f"{int(age_days)} days ago"

        console.print()
        console.rule("[dim]worth revisiting[/]", style="dim")
        console.print()
        console.print(
            f"  [dim]{age_str}:[/] [bold]\"{(insight.get('title') or 'Untitled')[:56]}\"[/]"
        )
        console.print(
            f"  [dim]{insight.get('msgs', 0)} messages · "
            f"still relevant?[/]"
        )

    console.print()
    pause()


# ── profile screen ─────────────────────────────────────────────────────────────
def screen_profile(stats):
    """
    Auto-derived personality mirror. No quiz. Entirely from behavior.
    Derives archetype, top domains, and key behavioral patterns from stats.
    """
    header("🪞  YOUR PROFILE")

    convos_data = stats.get("convos", {})
    if len(convos_data) < 10:
        console.print()
        console.print(Panel(
            "  Not enough data for a reliable profile yet.\n"
            "  Come back after you've had at least a few weeks of conversations.\n\n"
            "  [dim]The more you use ChatGPT as a thinking partner,\n"
            "  the more accurate this gets.[/]",
            border_style="dim",
        ))
        pause()
        return

    now = datetime.now(tz=timezone.utc).timestamp()

    # ── Derive archetype signals ───────────────────────────────────────────────

    # 1. Peak time label
    msgs_per_hour = stats.get("msgs_per_hour", Counter())
    if msgs_per_hour:
        peak_hr = max(msgs_per_hour, key=msgs_per_hour.get)
        if   0  <= peak_hr <  5: time_label = "Late-Night"
        elif 5  <= peak_hr < 11: time_label = "Morning"
        elif 11 <= peak_hr < 15: time_label = "Midday"
        elif 15 <= peak_hr < 20: time_label = "Evening"
        else:                    time_label = "Night"
    else:
        time_label = "Anytime"

    # 2. Usage style from question ratio
    user_msgs = max(stats.get("user_msgs", 1), 1)
    questions = stats.get("user_questions", 0)
    q_ratio   = questions / user_msgs
    if   q_ratio >= 0.62: style_label = "Explorer"
    elif q_ratio >= 0.38: style_label = "Builder"
    else:                  style_label = "Executor"

    # 3. Top domains (word_freq_user, filtered)
    DOMAIN_STOPS = STOP_WORDS | {
        "like","just","want","need","know","think","make","good","work",
        "help","use","using","also","really","actually","going","thing",
        "something","anything","everything","trying","time","way","back",
    }
    wf = stats.get("word_freq_user", Counter())
    top_domains = [w for w, _ in wf.most_common(60) if w not in DOMAIN_STOPS and len(w) >= 4][:5]

    # 4. Avg depth
    all_msgs   = [c["msgs"] for c in convos_data.values()]
    avg_depth  = sum(all_msgs) / max(len(all_msgs), 1)

    if   avg_depth >= 15: depth_label = "deep diver"
    elif avg_depth >= 7:  depth_label = "thorough"
    else:                  depth_label = "quick resolver"

    # 5. Single-query ratio → focus vs. scattered
    single_ct    = sum(1 for c in convos_data.values() if c.get("msgs", 0) <= 2)
    single_ratio = single_ct / max(len(convos_data), 1)
    if   single_ratio >= 0.55: focus_label = "broad and varied"
    elif single_ratio >= 0.30: focus_label = "balanced"
    else:                       focus_label = "deep and focused"

    # 6. Flattery rate
    gaslit_total = stats.get("gaslit_total", 0)
    gpt_msgs     = max(stats.get("gpt_msgs", 1), 1)
    flattery_pct = round(gaslit_total / gpt_msgs * 100, 1)

    # 7. Consistency — msgs per day std dev
    mpd = stats.get("msgs_per_day", Counter())
    if len(mpd) >= 7:
        counts   = list(mpd.values())
        mean_mpd = sum(counts) / len(counts)
        variance = sum((x - mean_mpd) ** 2 for x in counts) / len(counts)
        std_mpd  = variance ** 0.5
        cv       = std_mpd / max(mean_mpd, 0.1)  # coefficient of variation
        if   cv >= 1.8: rhythm_label = "in bursts — intense sprints with quiet gaps"
        elif cv >= 0.9: rhythm_label = "in waves — active periods with natural breaks"
        else:            rhythm_label = "consistently — a steady daily rhythm"
    else:
        rhythm_label = "regularly"

    # 8. Temporal span
    span_days = max(0, stats.get("latest_ts", 0) - stats.get("earliest_ts", 0)) / 86400
    span_str  = (f"{span_days/365:.1f} years" if span_days >= 365
                 else f"{int(span_days/30)} months" if span_days >= 30
                 else f"{int(span_days)} days")

    # 9. Prompt evolution — question complexity over time (crude: avg opener len)
    recent_cutoff = now - 90 * 86400
    old_cutoff    = now - 365 * 86400
    user_texts = stats.get("user_texts", [])

    # ── Render profile ─────────────────────────────────────────────────────────
    console.print()

    archetype = f"{time_label} {style_label}"
    console.print(f"  You're a [bold violet]{archetype}[/].")
    console.print()

    if top_domains:
        domain_str = ", ".join(top_domains[:3])
        console.print(
            f"  Your thinking runs through [bold]{domain_str}[/]. "
            f"You tend to work {rhythm_label}."
        )
        console.print()

    console.print(
        f"  [bold]{round(q_ratio * 100)}%[/] of your openers are questions — "
        f"you're {'mostly asking' if q_ratio >= 0.5 else 'mostly directing'}. "
        f"Average session depth: [bold]{avg_depth:.0f} messages[/]. "
        f"Your focus is {focus_label}."
    )
    console.print()

    if flattery_pct >= 5:
        console.print(
            f"  ChatGPT opened with flattery in [bold]{flattery_pct}%[/] of responses. "
            + ("That's unusually high — worth noticing." if flattery_pct >= 20 else "Par for the course.")
        )
        console.print()

    console.print(
        f"  [dim]{fmt(len(convos_data))} conversations over {span_str}.[/]"
    )
    console.print()

    # ── Signal breakdown ───────────────────────────────────────────────────────
    console.rule("[dim]signal breakdown[/]", style="dim")
    console.print()

    signal = stats.get("_signal_score", {})
    sigs   = signal.get("signals", {}) if signal else {}

    def sb(v, w=16):
        f = int(w * min(v, 1.0))
        return "[violet]" + "█" * f + "[/][dim]" + "░" * (w - f) + "[/]"

    rows = [
        ("Peak hour",        f"{peak_hr:02d}:00" if msgs_per_hour else "—"),
        ("Question ratio",   f"{round(q_ratio*100)}%"),
        ("Avg depth",        f"{avg_depth:.1f} msgs/convo"),
        ("Single-query rate",f"{round(single_ratio*100)}%"),
        ("Flattery rate",    f"{flattery_pct}%"),
    ]
    for label, val in rows:
        console.print(f"  [dim]{label:<22}[/]  {val}")

    console.print()
    console.print(
        "  [dim]Profile updates each time you load new data.\n"
        "  The more you use ChatGPT as a thinking partner,\n"
        "  the sharper this gets.[/]"
    )
    console.print()
    pause()


# ── export screen ──────────────────────────────────────────────────────────────
def screen_export(stats, convos):
    """
    Export conversations to Obsidian-compatible markdown vault.
    Three modes: Your Voice (first-person template) / Neutral / Raw.
    Generates [[wikilinks]] between semantically similar conversations.
    """
    header("📤  EXPORT")
    console.print("  [dim]Take your thinking somewhere.[/]\n")

    # ── Mode selection ─────────────────────────────────────────────────────────
    mode_choice = questionary.select(
        "How should the notes be written?",
        choices=[
            "Your Voice   — written as if you captured it yourself",
            "Neutral      — clean, objective summaries",
            "Raw          — full transcripts, nothing invented",
        ],
        style=MENU_STYLE,
    ).ask()
    if not mode_choice:
        return

    if   "Your Voice" in mode_choice: mode = "voice"
    elif "Neutral"    in mode_choice: mode = "neutral"
    else:                              mode = "raw"

    # ── Output folder ──────────────────────────────────────────────────────────
    default_out = str(Path.home() / "Desktop" / "artifact-export")
    console.print(f"\n  [dim]Output folder (default: {default_out})[/]")
    out_path_str = questionary.text(
        "  Export to:",
        default=default_out,
        style=MENU_STYLE,
    ).ask()
    if not out_path_str:
        return

    out_root = Path(out_path_str.strip())

    # ── Build similarity index for wikilinks (optional) ────────────────────────
    cfg      = load_config()
    folder   = cfg.get("folder", "default")
    cache    = _build_embed_cache(convos, folder)
    sim_index = {}   # cid → [(similar_title, score), ...]

    if cache is not None:
        try:
            import numpy as np
            emb  = cache["embeddings"]
            ids  = cache["ids"]
            titl = cache["titles"]
            sim  = emb @ emb.T
            for i, cid in enumerate(ids):
                row   = sim[i].copy()
                row[i] = -1  # exclude self
                top3  = row.argsort()[-3:][::-1]
                sim_index[cid] = [
                    (titl[j], float(sim[i][j]))
                    for j in top3 if float(sim[i][j]) >= 0.60
                ]
        except Exception:
            pass

    # ── Category mapping ───────────────────────────────────────────────────────
    categories = stats.get("categories", {})
    convos_list = list(convos)

    def get_category(convo):
        title_lower = (convo.get("title") or "Untitled").lower()
        for cat, keywords in categories.items():
            if any(kw in title_lower for kw in keywords):
                return re.sub(r"^[^\w]+", "", cat).strip()  # strip leading emoji/space
        return "Other"

    # ── Helper: sanitize filename ─────────────────────────────────────────────
    def safe_name(s, maxlen=60):
        s = re.sub(r'[\\/*?:"<>|]', "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s[:maxlen] or "Untitled"

    # ── Helper: generate note content by mode ─────────────────────────────────
    def make_note(convo, mode):
        cid    = convo.get("id", "")
        title  = (convo.get("title") or "Untitled").strip()
        ts     = convo.get("create_time") or 0
        model  = stats["convos"].get(cid, {}).get("model", "unknown")
        n_msgs = stats["convos"].get(cid, {}).get("msgs", 0)
        date_s = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "unknown"

        thread = get_thread(convo)
        user_msgs = [m["text"] for m in thread if m["role"] == "user"]
        asst_msgs = [m["text"] for m in thread if m["role"] == "assistant"]

        # Build wikilinks
        related = sim_index.get(cid, [])
        wikilinks_str = "  ".join(
            f"[[{safe_name(t)}]]" for t, _ in related
        ) if related else ""

        # Frontmatter
        lines = [
            "---",
            f"date: {date_s}",
            f"model: {model}",
            f"messages: {n_msgs}",
            f"topic: {get_category(convo)}",
        ]
        if related:
            related_yaml = ", ".join(f"[[{safe_name(t)}]]" for t, _ in related)
            lines.append(f"related: {related_yaml}")
        lines += ["---", "", f"# {title}", ""]

        if mode == "raw":
            # Full transcript
            for m in thread:
                role_label = "**You:**" if m["role"] == "user" else "**ChatGPT:**"
                lines.append(f"{role_label}\n{m['text']}\n")

        elif mode == "neutral":
            # Objective summary
            first_user = user_msgs[0][:300] if user_msgs else ""
            first_asst = asst_msgs[0][:400] if asst_msgs else ""
            lines.append("## Summary")
            lines.append(f"Discussion about: {first_user[:120]}{'…' if len(first_user) > 120 else ''}")
            lines.append("")
            if first_asst:
                lines.append("## Key Points")
                # Pull first 3 sentences from first assistant response
                sentences = re.split(r'(?<=[.!?])\s+', first_asst)
                for s in sentences[:3]:
                    lines.append(f"- {s.strip()}")
                lines.append("")
            if len(asst_msgs) > 1:
                lines.append("## Final Response")
                lines.append(asst_msgs[-1][:600] + ("…" if len(asst_msgs[-1]) > 600 else ""))
                lines.append("")

        else:  # "voice" — first-person template
            first_user = user_msgs[0] if user_msgs else ""
            # Opening: paraphrase first message as first-person
            lines.append("## What I was working on")
            if first_user:
                # Clean up: remove trailing ? if it's a question, frame as statement
                opener = first_user[:200].strip()
                if opener.endswith("?"):
                    lines.append(f"I was trying to figure out: {opener}")
                else:
                    lines.append(f"I wanted to: {opener[:150]}{'…' if len(opener) > 150 else ''}")
            lines.append("")

            if asst_msgs:
                lines.append("## What I learned")
                last_response = asst_msgs[-1]
                sentences = re.split(r'(?<=[.!?])\s+', last_response)
                for s in sentences[:4]:
                    s = s.strip()
                    if s and len(s) > 20:
                        lines.append(f"- {s}")
                lines.append("")

            # Follow-up questions from the user (signs of active thinking)
            follow_qs = [
                m[:120] for m in user_msgs[1:]
                if m.strip().endswith("?") or m.lower().startswith(("what","why","how","can","could","should","is ","are "))
            ][:3]
            if follow_qs:
                lines.append("## Questions I explored")
                for q in follow_qs:
                    lines.append(f"- {q.strip()}")
                lines.append("")

        if wikilinks_str:
            lines.append("## Related")
            lines.append(wikilinks_str)
            lines.append("")

        return "\n".join(lines)

    # ── Write files ────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  Writing to [bold]{out_root}[/]…\n")

    # Group convos by category
    by_cat = defaultdict(list)
    for convo in convos:
        by_cat[get_category(convo)].append(convo)

    total = len(convos)
    written = 0
    errors  = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        "[progress.percentage]{task.percentage:>3.0f}%",
        console=console,
    ) as prog:
        task = prog.add_task(
            f"  Writing {total:,} notes…", total=total
        )
        for cat, cat_convos in sorted(by_cat.items()):
            cat_dir = out_root / safe_name(cat)
            cat_dir.mkdir(parents=True, exist_ok=True)
            for convo in cat_convos:
                try:
                    title    = (convo.get("title") or "Untitled").strip()
                    filename = safe_name(title) + ".md"
                    filepath = cat_dir / filename
                    filepath.write_text(make_note(convo, mode), encoding="utf-8")
                    written += 1
                except Exception:
                    errors += 1
                finally:
                    prog.advance(task)

    # ── Write _MINDMAP.md index ────────────────────────────────────────────────
    try:
        mindmap_lines = [
            "# artifact. — Mind Map",
            f"\nGenerated on {datetime.now().strftime('%Y-%m-%d')} · {written} conversations · mode: {mode}",
            "\n---\n",
        ]
        for cat, cat_convos in sorted(by_cat.items()):
            mindmap_lines.append(f"\n## {cat} ({len(cat_convos)})")
            for convo in sorted(cat_convos, key=lambda c: c.get("create_time") or 0, reverse=True)[:20]:
                t = safe_name((convo.get("title") or "Untitled").strip())
                mindmap_lines.append(f"- [[{t}]]")
        (out_root / "_MINDMAP.md").write_text("\n".join(mindmap_lines), encoding="utf-8")
    except Exception:
        pass

    # ── Done ───────────────────────────────────────────────────────────────────
    console.print()
    if errors == 0:
        console.print(f"  [green]✓[/] [bold]{written:,} notes written[/] to [bold]{out_root}[/]")
    else:
        console.print(f"  [yellow]✓[/] [bold]{written:,} notes written[/]  [dim]({errors} skipped)[/]")

    console.print()
    console.print("  [dim]Drop this folder into your Obsidian vault.[/]")
    console.print("  [dim]Open Graph View to see the connections.[/]")
    console.print()
    pause()


# ── welcome screen ────────────────────────────────────────────────────────────
def screen_welcome():
    """artifact. first-run welcome screen. Also callable from Settings."""
    console.clear()
    console.print()
    console.print()
    console.print(Panel(
        "\n[bold white]artifact.[/]\n",
        border_style="dim", expand=False, padding=(1, 8),
    ))
    console.print()
    console.print(
        "  Your ChatGPT history is an unintentional journal.\n"
        "  You didn't know you were writing it.\n"
        "  [bold]That's what makes it honest.[/]"
    )
    console.print()
    console.print(
        "  The same idea appearing 7 times across 3 years —\n"
        "  in different conversations, different moods, different contexts —\n"
        "  is more signal than anything you'd ever write\n"
        "  in a notes app intentionally."
    )
    console.print()
    console.rule(style="dim")
    console.print()
    console.print("  [dim]Everyone uses ChatGPT. Nobody ever goes back.[/]")
    console.print()
    console.print("  [bold]artifact.[/] turns your graveyard into a library.")
    console.print()
    console.rule(style="dim")
    console.print()
    console.print("  [dim]Your data never leaves your machine.[/]")
    console.print("  [dim]No accounts. No cloud. No subscriptions.[/]")
    console.print("  [dim]Just you and what you've already written.[/]")
    console.print()
    questionary.press_any_key_to_continue("  Press any key to begin.").ask()


# ── signal score ──────────────────────────────────────────────────────────────
def compute_signal_score(stats, convos):
    """
    Rate how much signal this ChatGPT history has for artifact.'s features.
    Returns a dict with overall score (0–100) and per-signal breakdown.
    """
    n_convos = len(stats["convos"])
    if n_convos == 0:
        return None

    # 1. Temporal span (full score = 2+ years)
    span_days   = max(0, stats["latest_ts"] - stats["earliest_ts"]) / 86400
    span_months = span_days / 30
    s_span      = min(1.0, span_months / 24)

    # 2. Avg conversation depth (full score = 15+ msgs avg)
    all_msgs = [c["msgs"] for c in stats["convos"].values()]
    avg_msgs = sum(all_msgs) / len(all_msgs) if all_msgs else 1
    s_depth  = min(1.0, max(0.0, avg_msgs - 1) / 14)

    # 3. Personal language density (first-person pronouns in user texts)
    PERSONAL_RE = re.compile(
        r"\b(i|my|me|i'm|i've|i'd|i'll|we|our|we're)\b", re.IGNORECASE
    )
    n_utexts    = len(stats["user_texts"])
    personal_ct = sum(1 for t in stats["user_texts"] if PERSONAL_RE.search(t))
    s_personal  = min(1.0, personal_ct / max(n_utexts, 1))

    # 4. Idea recurrence — how many title words appear 3+ times
    title_wf = Counter()
    for c in stats["convos"].values():
        title = (c.get("title") or "").lower()
        title_wf.update(
            w for w in re.findall(r"\b[a-z]{4,}\b", title)
            if w not in STOP_WORDS
        )
    recurring    = sum(1 for _, cnt in title_wf.items() if cnt >= 3)
    s_recurrence = min(1.0, recurring / 20)

    # 5. Speculative / ideation language
    SPEC_RE = re.compile(
        r"\b(what if|idea|thinking|could be|maybe|perhaps|wondering|"
        r"imagine|possible|concept|explore|hypothesis|consider)\b",
        re.IGNORECASE,
    )
    spec_ct      = sum(1 for t in stats["user_texts"] if SPEC_RE.search(t))
    s_speculative = min(1.0, spec_ct / max(n_utexts * 0.25, 1))

    # 6. Focus — inverse of single-query ratio (high = used as thinking partner)
    single_query = sum(1 for c in stats["convos"].values() if c["msgs"] <= 2)
    single_ratio = single_query / n_convos
    s_focus      = max(0.0, 1.0 - single_ratio * 1.3)

    overall = (
        s_span        * 0.20 +
        s_depth       * 0.20 +
        s_personal    * 0.25 +
        s_recurrence  * 0.15 +
        s_speculative * 0.10 +
        s_focus       * 0.10
    )

    return {
        "overall":      round(overall * 100),
        "signals": {
            "span":        s_span,
            "depth":       s_depth,
            "personal":    s_personal,
            "recurrence":  s_recurrence,
            "speculative": s_speculative,
            "focus":       s_focus,
        },
        "avg_msgs":     avg_msgs,
        "span_months":  span_months,
        "single_ratio": single_ratio,
        "n_convos":     n_convos,
        "n_user_texts": n_utexts,
    }


def show_signal_score(score_data):
    """Display the signal score screen. Called once after first data load."""
    if not score_data:
        return

    overall = score_data["overall"]
    sigs    = score_data["signals"]

    if overall >= 75:   level, lcolor = "Strong",   "green"
    elif overall >= 50: level, lcolor = "Good",     "yellow"
    elif overall >= 30: level, lcolor = "Moderate", "yellow"
    else:               level, lcolor = "Light",    "red"

    console.print()
    console.rule("[bold]YOUR SIGNAL[/]")
    console.print()

    months   = score_data["span_months"]
    span_str = f"{months/12:.1f} years" if months >= 12 else f"{months:.0f} months"
    console.print(
        f"  {span_str} of data · {fmt(score_data['n_convos'])} conversations "
        f"· {fmt(score_data['n_user_texts'])} messages from you"
    )
    console.print()

    bw     = 24
    filled = int(bw * overall / 100)
    bar    = "[violet]" + "█" * filled + "[/][dim]" + "░" * (bw - filled) + "[/]"
    console.print(f"  {bar}  [bold]{overall} / 100[/]  [{lcolor}]{level}[/]")
    console.print()

    def sb(v, w=12):
        f = int(w * v)
        return "[violet]" + "█" * f + "[/][dim]" + "░" * (w - f) + "[/]"

    def sl(v):
        if v >= 0.70: return "[green]High[/]"
        if v >= 0.40: return "[yellow]Medium[/]"
        return "[dim]Low[/]"

    for label, key, extra in [
        ("Personal context",     "personal",    sl(sigs["personal"])),
        ("Conversation depth",   "depth",       f"[dim]avg {score_data['avg_msgs']:.0f} msg/convo[/]"),
        ("Idea recurrence",      "recurrence",  sl(sigs["recurrence"])),
        ("Speculative thinking", "speculative", sl(sigs["speculative"])),
        ("Focus (vs. lookups)",  "focus",       sl(sigs["focus"])),
    ]:
        console.print(f"  {label:<24}  {sb(sigs[key])}  {extra}")

    console.print()
    console.print("  [dim]What you'll get:[/]")

    feature_scores = {
        "Ideas":    (sigs["recurrence"] + sigs["personal"]) / 2,
        "Profile":  (sigs["personal"]   + sigs["depth"])    / 2,
        "Search":   1.0,
        "Patterns": (sigs["depth"]      + sigs["focus"])    / 2,
        "Export":    sigs["depth"],
    }
    for feature, fscore in feature_scores.items():
        if fscore >= 0.65:   icon, note = "[green]✓[/]", "strong"
        elif fscore >= 0.35: icon, note = "[yellow]~[/]", "partial"
        else:                icon, note = "[red]✗[/]",   "limited"
        console.print(f"  {icon}  [bold]{feature:<10}[/]  [dim]{note}[/]")

    if overall < 55:
        console.print()
        console.print(Panel(
            "  [dim]Getting more: share context at the start.\n"
            "  Instead of [italic]'how do I do X'[/] try\n"
            "  [italic]'I'm building Y for Z reason, help me think through X.'[/]\n"
            "  The more you think out loud, the richer this gets.[/]",
            border_style="dim", padding=(0, 1),
        ))

    console.print()
    questionary.press_any_key_to_continue("  Press any key to continue.").ask()


# ── embedding cache (shared by Ideas + Search) ────────────────────────────────
_EMBED_CACHE = {}  # folder_key → {ids, titles, texts, ts, embeddings}


def _embed_cache_path(folder):
    """Return the .npz path for this folder's embedding cache."""
    import hashlib
    key  = hashlib.md5(str(folder).encode()).hexdigest()[:8]
    return APP_DIR / f".embed_cache_{key}.npz"


def _embed_cache_fingerprint(folder):
    """(n_files, max_mtime) — used to detect when the folder has changed."""
    try:
        files = list(Path(folder).glob("conversations-*.json"))
        if not files:
            return (0, 0)
        return (len(files), max(f.stat().st_mtime for f in files))
    except Exception:
        return (0, 0)


def _build_embed_cache(convos, folder="default"):
    """
    Embed all non-trivial conversations. Cache strategy:
      1. In-memory (_EMBED_CACHE) for the current session.
      2. Disk (.embed_cache_<hash>.npz) across restarts.
         Invalidated when folder file count or newest mtime changes.
    """
    if folder in _EMBED_CACHE:
        return _EMBED_CACHE[folder]

    GENERIC = {"new chat", "untitled", "chatgpt", "conversation", ""}
    corpus  = []  # (cid, title, combined_text, ts)

    for convo in convos:
        cid   = convo.get("id", "")
        title = (convo.get("title") or "Untitled").strip()
        ts    = convo.get("create_time") or 0
        if title.lower() in GENERIC:
            continue

        # First user message
        first_user = ""
        for node in convo.get("mapping", {}).values():
            msg = node.get("message")
            if not msg:
                continue
            if msg.get("author", {}).get("role") != "user":
                continue
            text = extract_text(msg)
            if text and len(text) > 5:
                first_user = text[:200]
                break

        combined = f"{title}. {first_user}".strip()
        corpus.append((cid, title, combined, ts))

    if not corpus:
        return None

    ids, titles, texts, ts_list = zip(*corpus)

    try:
        import numpy as np
    except ImportError:
        return None

    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None

    # ── Try loading from disk cache ───────────────────────────────────────────
    cache_path   = _embed_cache_path(folder)
    fingerprint  = _embed_cache_fingerprint(folder)

    if cache_path.exists():
        try:
            saved = np.load(str(cache_path), allow_pickle=True)
            saved_fp = (int(saved["fp_n"]), float(saved["fp_mtime"]))
            if saved_fp == fingerprint:
                result = {
                    "ids":        saved["ids"].tolist(),
                    "titles":     saved["titles"].tolist(),
                    "texts":      saved["texts"].tolist(),
                    "ts":         saved["ts"].tolist(),
                    "embeddings": saved["embeddings"],
                }
                _EMBED_CACHE[folder] = result
                return result
        except Exception:
            pass  # corrupted or version mismatch — recompute

    # ── Compute embeddings ────────────────────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as prog:
        prog.add_task(
            f"  Building idea map for {len(texts):,} conversations…", total=None
        )
        model = TextEmbedding(
            "BAAI/bge-small-en-v1.5", cache_dir=str(MODELS_DIR)
        )
        emb = np.array(list(model.embed(texts)), dtype="float32")

    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    emb   = emb / np.maximum(norms, 1e-9)

    # ── Save to disk ──────────────────────────────────────────────────────────
    try:
        np.savez(
            str(cache_path),
            ids=np.array(ids, dtype=object),
            titles=np.array(titles, dtype=object),
            texts=np.array(texts, dtype=object),
            ts=np.array(ts_list, dtype="float64"),
            embeddings=emb,
            fp_n=np.array(fingerprint[0]),
            fp_mtime=np.array(fingerprint[1]),
        )
    except Exception:
        pass  # disk write failure is non-fatal

    result = {
        "ids":        list(ids),
        "titles":     list(titles),
        "texts":      list(texts),
        "ts":         list(ts_list),
        "embeddings": emb,
    }
    _EMBED_CACHE[folder] = result
    return result


# ── best ideas screen ─────────────────────────────────────────────────────────
def screen_best_ideas(stats, convos):
    """
    Surface the top 3 ideas from the user's entire ChatGPT history using a
    composite signal score — not just most-discussed, but most distinctively
    theirs: recurring, long-lived, deep, and far from generic task usage.

    Scoring (0–1 normalized, weighted):
      returns     0.30  — distinct sessions 30+ days apart (the "quietly carrying" signal)
      user_ratio  0.20  — how much YOU talked vs GPT (you did the thinking)
      uniqueness  0.20  — distance from global "average ChatGPT usage" centroid
      longevity   0.15  — how long you've had this thread
      recurrence  0.10  — raw conversation count
      depth       0.05  — deepest single conversation in the cluster (msg count)

    Filters: clusters > 30 convos (topics, not ideas) and avg depth < 5 msgs
    (lookup queries, not thinking) are excluded before scoring.
    """
    header("✦  BEST IDEAS")
    console.print("  [dim]Not your most-discussed topics.\n"
                  "  The ones that reveal something about how you think.[/]\n")

    cfg    = load_config()
    folder = cfg.get("folder", "default")
    cache  = _build_embed_cache(convos, folder)

    if cache is None:
        console.print(Panel(
            "  fastembed is required for Best Ideas.\n"
            "  Run: [bold]pip install fastembed[/]",
            border_style="yellow",
        ))
        pause()
        return

    try:
        import numpy as np
    except ImportError:
        console.print("[red]numpy not available.[/]")
        pause()
        return

    ids        = cache["ids"]
    titles     = cache["titles"]
    texts      = cache["texts"]
    ts_list    = cache["ts"]
    embeddings = cache["embeddings"]
    n          = len(ids)

    THRESHOLD   = 0.80   # tighter than Ideas (0.74) — specific threads, not topic buckets
    MIN_SIZE    = 2      # a 2-convo thread can be a genuine idea
    MAX_CLUSTER = 30     # clusters larger than this are categories, not ideas — skip them
    MIN_AVG_MSGS = 5     # filter out lookup-style clusters (short Q&A, not real thinking)

    # ── Cluster (same union-find as Ideas) ────────────────────────────────────
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    sim  = embeddings @ embeddings.T
    rows, cols = np.where(np.triu(sim, k=1) >= THRESHOLD)
    for i, j in zip(rows.tolist(), cols.tolist()):
        union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    convo_lookup = stats.get("convos", {})

    clusters = []
    for idxs in groups.values():
        if len(idxs) < MIN_SIZE or len(idxs) > MAX_CLUSTER:
            continue
        # Filter: skip shallow clusters (lookup queries, not ideas)
        msgs_list = [convo_lookup.get(ids[i], {}).get("msgs", 0) or 0 for i in idxs]
        if msgs_list and (sum(msgs_list) / len(msgs_list)) < MIN_AVG_MSGS:
            continue
        clusters.append(idxs)

    if not clusters:
        console.print(
            "  [dim]Not enough recurring threads found yet.\n"
            "  Keep using ChatGPT as a thinking partner — this screen gets better over time.[/]"
        )
        pause()
        return

    # ── Global centroid (represents "average ChatGPT usage") ─────────────────
    # Ideas far from this centroid are distinctively yours, not generic tasks.
    global_centroid = embeddings.mean(axis=0)
    norm = float(np.linalg.norm(global_centroid))
    if norm > 0:
        global_centroid = global_centroid / norm

    # ── Score each cluster ────────────────────────────────────────────────────
    now_ts = datetime.now(tz=timezone.utc).timestamp()

    scored = []
    for idxs in clusters:
        ts_vals = [ts_list[i] for i in idxs if ts_list[i]]

        # Recurrence (0–1, capped at 10 — we already filtered giant clusters)
        recurrence = min(len(idxs) / 10.0, 1.0)

        # Longevity — months spanned (0–1, capped at 24 months)
        if len(ts_vals) >= 2:
            span_days = (max(ts_vals) - min(ts_vals)) / 86400
        else:
            span_days = 0.0
        longevity = min(span_days / 730.0, 1.0)  # 730 days = 24 months

        # User word ratio — how much YOU talked in these convos
        ratios = []
        max_msgs = 0
        for i in idxs:
            cid  = ids[i]
            cdata = convo_lookup.get(cid, {})
            uw   = cdata.get("user_words", 0) or 0
            gw   = cdata.get("gpt_words", 0) or 0
            msgs = cdata.get("msgs", 0) or 0
            total = uw + gw
            if total > 0:
                ratios.append(uw / total)
            if msgs > max_msgs:
                max_msgs = msgs
        user_ratio = float(np.mean(ratios)) if ratios else 0.5

        # Uniqueness — cluster centroid distance from global "generic" centroid
        cluster_embs    = embeddings[idxs]
        cluster_centroid = cluster_embs.mean(axis=0)
        cn = float(np.linalg.norm(cluster_centroid))
        if cn > 0:
            cluster_centroid = cluster_centroid / cn
        similarity_to_global = float(cluster_centroid @ global_centroid)
        uniqueness = max(0.0, 1.0 - similarity_to_global)

        # Depth — deepest single convo, capped at 60 msgs
        depth = min(max_msgs / 60.0, 1.0)

        # Returns — distinct sessions separated by 30+ day gaps
        # This is the "quietly carrying" signal: you went away and came back.
        # A sprint of 8 convos in January ≠ returning to something 4 times over 2 years.
        GAP_DAYS = 30
        sorted_ts = sorted(ts_vals)
        returns = 1
        for k in range(1, len(sorted_ts)):
            if (sorted_ts[k] - sorted_ts[k - 1]) / 86400 >= GAP_DAYS:
                returns += 1
        return_score = min((returns - 1) / 5.0, 1.0)  # 0 = single session; 1.0 = 6+ distinct returns

        score = (
            return_score * 0.30   # the headline signal: kept coming back after silence
            + user_ratio * 0.20   # you did the thinking, not just prompted
            + uniqueness * 0.20   # distinctively yours, not generic task usage
            + longevity  * 0.15   # how long you've had this thread
            + recurrence * 0.10   # raw count, less important now that returns captures it
            + depth      * 0.05   # deepest single convo
        )

        scored.append((score, idxs, span_days, returns))

    scored.sort(key=lambda x: -x[0])
    top3 = scored[:3]

    # ── Cluster naming (same logic as Ideas) ─────────────────────────────────
    NAME_STOPS = STOP_WORDS | {
        "new","using","help","create","build","make","add","get","set",
        "update","fix","question","issue","problem","work","working","test",
        "need","vs","via","how","way","best","think","just","let","want",
    }

    def cluster_name(idxs):
        wf = Counter()
        for i in idxs:
            words = re.findall(r"\b[a-z]{3,}\b", titles[i].lower())
            wf.update(w for w in words if w not in NAME_STOPS)
        top = [w for w, _ in wf.most_common(3)]
        return " / ".join(w.capitalize() for w in top[:2]) if top else "Unnamed Thread"

    def best_snippet(idxs):
        """Return the opening thought from the most word-dense convo in the cluster."""
        cdata_list = [(convo_lookup.get(ids[i], {}).get("user_words", 0) or 0, i)
                      for i in idxs]
        _, best_i = max(cdata_list, default=(0, idxs[0]))
        # texts[i] = "Title. first_user_text" — strip the title prefix
        raw = texts[best_i]
        title_prefix = titles[best_i] + ". "
        if raw.startswith(title_prefix):
            raw = raw[len(title_prefix):]
        snippet = raw.strip()
        if not snippet:
            snippet = titles[best_i]
        # Truncate cleanly at word boundary
        if len(snippet) > 120:
            snippet = snippet[:117].rsplit(" ", 1)[0] + "…"
        return snippet

    # ── Render ────────────────────────────────────────────────────────────────
    MEDALS = ["1", "2", "3"]

    for rank, (score, idxs, span_days, returns) in enumerate(top3):
        name    = cluster_name(idxs)
        snippet = best_snippet(idxs)
        count   = len(idxs)

        ts_vals = sorted(t for i in idxs if (t := ts_list[i]))
        if ts_vals and span_days >= 30:
            span_str = (
                f"{span_days/365:.1f} yr" if span_days >= 365
                else f"{span_days/30:.0f} mo"
            )
            span_part = f" · {span_str}"
        else:
            span_part = ""

        # Recency indicator
        if ts_vals:
            days_since = (now_ts - max(ts_vals)) / 86400
            if days_since < 30:
                recency = "  [green]● active[/]"
            elif days_since < 120:
                recency = "  [yellow]● recent[/]"
            else:
                recency = "  [dim]● dormant[/]"
        else:
            recency = ""

        returns_str = (
            f" · returned {returns}×" if returns >= 2 else ""
        )
        console.print(
            f"  [bold]{MEDALS[rank]}.[/]  [bold]{name}[/]{recency}"
        )
        console.print(
            f"      [dim]{count} conversations{span_part}{returns_str}[/]"
        )
        if snippet:
            console.print(f'      [italic dim]"{snippet}"[/]')
        console.print()

    console.print(
        "  [dim]Score: how often you returned after a gap + your thinking ratio\n"
        "  + how unlike generic ChatGPT use this is + longevity + depth[/]"
    )
    pause()


# ── ideas screen ──────────────────────────────────────────────────────────────
def screen_ideas(stats, convos):
    header("💡  IDEAS")
    console.print("  [dim]The things that keep coming back.[/]\n")

    cfg    = load_config()
    folder = cfg.get("folder", "default")
    cache  = _build_embed_cache(convos, folder)

    if cache is None:
        console.print(Panel(
            "  fastembed is required for Ideas.\n"
            "  Run: [bold]pip install fastembed[/]  (~80MB, no PyTorch needed)",
            border_style="yellow",
        ))
        pause()
        return

    try:
        import numpy as np
    except ImportError:
        console.print("[red]numpy not available.[/]")
        pause()
        return

    ids        = cache["ids"]
    titles     = cache["titles"]
    ts_list    = cache["ts"]
    embeddings = cache["embeddings"]
    n          = len(ids)

    THRESHOLD  = 0.74   # cosine similarity to call "same idea"
    MIN_SIZE   = 3      # min conversations per cluster

    # Union-find clustering
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    sim = embeddings @ embeddings.T
    rows, cols = np.where(np.triu(sim, k=1) >= THRESHOLD)
    for i, j in zip(rows.tolist(), cols.tolist()):
        union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    clusters = sorted(
        [idxs for idxs in groups.values() if len(idxs) >= MIN_SIZE],
        key=lambda c: -len(c),
    )

    if not clusters:
        console.print(
            "  [dim]Not enough recurring threads found yet.\n"
            "  The more you use ChatGPT as a thinking partner,\n"
            "  the more appears here.[/]"
        )
        pause()
        return

    console.print(f"  Found [bold violet]{len(clusters)}[/] idea threads across your history.\n")

    NAME_STOPS = STOP_WORDS | {
        "new","using","help","create","build","make","add","get","set",
        "update","fix","question","issue","problem","work","working","test",
        "need","vs","via","how","way","best","think","just","let","want",
    }

    def cluster_name(idxs):
        wf = Counter()
        for i in idxs:
            words = re.findall(r"\b[a-z]{3,}\b", titles[i].lower())
            wf.update(w for w in words if w not in NAME_STOPS)
        top = [w for w, _ in wf.most_common(3)]
        return " / ".join(w.capitalize() for w in top[:2]) if top else "Unnamed Thread"

    def cluster_status(idxs):
        now     = datetime.now(tz=timezone.utc).timestamp()
        ts_vals = [ts_list[i] for i in idxs if ts_list[i]]
        if not ts_vals:
            return "Open"
        last       = max(ts_vals)
        first      = min(ts_vals)
        days_since = (now - last) / 86400
        span_days  = (last - first) / 86400
        if days_since > 365:
            return "Abandoned?"
        if days_since > 90:
            return "Dormant"
        if len(idxs) >= 4 and span_days > 90:
            return "Looping ↻"
        return "Active"

    STATUS_COLOR = {
        "Active":     "green",
        "Looping ↻":  "yellow",
        "Dormant":    "dim",
        "Abandoned?": "red",
    }

    for cluster_idxs in clusters[:15]:
        name      = cluster_name(cluster_idxs)
        status    = cluster_status(cluster_idxs)
        color     = STATUS_COLOR.get(status, "white")
        ts_vals   = sorted(t for i in cluster_idxs if (t := ts_list[i]))
        count     = len(cluster_idxs)
        now_ts    = datetime.now(tz=timezone.utc).timestamp()

        if ts_vals:
            first_dt   = datetime.fromtimestamp(min(ts_vals), tz=timezone.utc)
            last_dt    = datetime.fromtimestamp(max(ts_vals),  tz=timezone.utc)
            days_since = (now_ts - max(ts_vals)) / 86400
            span_days  = (max(ts_vals) - min(ts_vals)) / 86400
            span_str   = (
                f"{span_days/365:.1f} yrs" if span_days >= 365
                else f"{span_days/30:.0f} mo" if span_days >= 30
                else f"{span_days:.0f} days"
            )
            date_str = (
                f"{first_dt.strftime('%b %Y')} → {last_dt.strftime('%b %Y')}"
            )
        else:
            span_str = days_since = "?"
            date_str = ""

        console.print(
            f"  [bold violet]●[/] [bold]{name:<38}[/]  "
            f"[dim]{count} mentions · {span_str}[/]  [{color}]{status}[/]"
        )
        if date_str:
            console.print(f"    [dim]{date_str}[/]")

        # Sample titles (up to 3, deduplicated)
        seen_t = set()
        for i in sorted(cluster_idxs, key=lambda x: ts_list[x] or 0):
            t = titles[i]
            if t.lower() not in seen_t and t.lower() not in {"new chat", "untitled"}:
                console.print(f'    [dim]"{t[:65]}"[/]')
                seen_t.add(t.lower())
            if len(seen_t) >= 3:
                break

        # Insight line
        if status == "Looping ↻":
            console.print(
                "    [italic dim]→ Same problem, different projects. "
                "You may not have the underlying answer yet.[/]"
            )
        elif status == "Abandoned?":
            months_ago = int(days_since / 30)
            console.print(
                f"    [italic dim]→ You haven't touched this in {months_ago} months. "
                "Resolved, or left behind?[/]"
            )
        elif status == "Dormant":
            months_ago = int(days_since / 30)
            console.print(
                f"    [italic dim]→ You were onto something. It's been {months_ago} months.[/]"
            )
        elif count >= 5:
            console.print(
                "    [italic dim]→ This keeps coming back. "
                "Might be worth a dedicated session.[/]"
            )
        console.print()

    console.print(
        f"  [dim]Similarity threshold: {THRESHOLD} · "
        "runs 100% locally · no data sent anywhere[/]"
    )
    pause()


# ── search screen ─────────────────────────────────────────────────────────────
def screen_search(stats, convos):
    cfg    = load_config()
    folder = cfg.get("folder", "default")
    cache  = None  # built lazily on first search

    while True:
        header("🔍  SEARCH")
        console.print("  [dim]Find anything you've already thought about.[/]")
        console.print("  [dim]Searches by meaning, not just keywords.[/]\n")

        query = questionary.text("  Search (blank to exit):", style=MENU_STYLE).ask()
        if not query or not query.strip():
            return

        # Build / reuse cache
        if cache is None:
            cache = _build_embed_cache(convos, folder)

        if cache is None:
            console.print(Panel(
                "  fastembed is required for semantic search.\n"
                "  Run: [bold]pip install fastembed[/]  (~80MB, no PyTorch needed)",
                border_style="yellow",
            ))
            pause()
            return

        try:
            from fastembed import TextEmbedding
            import numpy as np
        except ImportError:
            console.print("[red]fastembed not available.[/]")
            pause()
            return

        model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(MODELS_DIR))
        q_emb = np.array(list(model.embed([query.strip()])), dtype="float32")[0]
        q_norm = np.linalg.norm(q_emb)
        if q_norm > 0:
            q_emb /= q_norm

        scores      = cache["embeddings"] @ q_emb
        top_indices = scores.argsort()[::-1][:15]

        console.print(f'\n  [bold]Results for:[/] [italic]"{query.strip()}"[/]\n')

        t = Table(
            box=box.SIMPLE, border_style="dim",
            padding=(0, 1), show_header=True, header_style="dim",
        )
        t.add_column("",              width=2)
        t.add_column("Conversation",  style="bold")
        t.add_column("Score",         justify="right", style="violet")
        t.add_column("Date",          style="dim")

        shown = 0
        for rank, idx in enumerate(top_indices):
            score = float(scores[idx])
            if score < 0.40:
                break
            title    = cache["titles"][idx]
            ts       = cache["ts"][idx]
            date_str = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %Y")
                if ts else "—"
            )
            dot = "[bold violet]★[/]" if rank < 3 else "[dim]·[/]"
            t.add_row(dot, title[:65], f"{score:.2f}", date_str)
            shown += 1

        if shown:
            console.print(t)
        else:
            console.print("  [dim]No strong matches found. Try different wording.[/]")

        console.print()


def screen_settings(stats):
    header("⚙️   SETTINGS")

    choice = questionary.select(
        "What would you like to change?",
        choices=[
            "Change timezone",
            "Replay welcome screen",
            "Show signal score again",
            "Back",
        ],
        style=MENU_STYLE,
    ).ask()

    if not choice or choice == "Back":
        return

    if choice == "Replay welcome screen":
        screen_welcome()
        return

    if choice == "Show signal score again":
        # Recompute and display without the "first time" gate
        score_data = stats.get("_signal_score")
        if score_data:
            show_signal_score(score_data)
        else:
            console.print("  [dim]Load a folder first.[/]")
            pause()
        return

    # ── Change timezone ────────────────────────────────────────────────────────
    cfg = load_config()
    current_tz = cfg.get("timezone", DEFAULT_TZ)
    console.print(f"\n  [dim]Current timezone:[/] [bold]{current_tz}[/]")
    console.print()
    console.print("  Enter a timezone name like [bold]America/New_York[/], [bold]Europe/London[/],")
    console.print("  [bold]Asia/Tokyo[/], or [bold]UTC[/].  Leave blank to keep current.\n")

    new_tz = questionary.text(
        "  New timezone (blank to cancel):",
        style=MENU_STYLE,
    ).ask()

    if not new_tz or not new_tz.strip():
        console.print("[dim]  No change.[/]")
        pause()
        return

    new_tz = new_tz.strip()
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(new_tz)  # validate
        cfg["timezone"] = new_tz
        save_config(cfg)
        console.print(f"\n  [green]✓ Timezone saved:[/] [bold]{new_tz}[/]")
        console.print("  [dim]Reload the folder (Change Folder) to apply to time charts.[/]")
    except Exception:
        console.print(f"\n  [red]Invalid timezone name:[/] {new_tz}")
        console.print(
            "  [dim]See en.wikipedia.org/wiki/List_of_tz_database_time_zones "
            "for valid names.[/]"
        )

    pause()


# ── folder setup ──────────────────────────────────────────────────────────────
def set_folder():
    folder = questionary.path(
        "Path to your ChatGPT export folder:",
        only_directories=True,
        style=MENU_STYLE,
    ).ask()
    if not folder:
        return None
    folder = str(Path(folder).expanduser().resolve())
    files = list(Path(folder).glob("conversations-*.json"))
    if not files:
        console.print(f"[red]No conversations-*.json files found in {folder}[/]")
        return None
    cfg = load_config()
    cfg["folder"] = folder
    save_config(cfg)
    console.print(f"[green]✓ Folder saved:[/] {folder}  ({len(files)} files)")
    return folder

# ── main ──────────────────────────────────────────────────────────────────────
def screen_patterns(stats, convos):
    """Patterns sub-menu — all deep-dive metric screens."""
    PATTERN_ITEMS = [
        ("📊  Overview",                  screen_overview,              False),
        ("✍️   What You Typed",             screen_you,                   False),
        ("🗂️   Topics",                     screen_categories,            True),
        ("📅  Time Patterns",              screen_time,                  False),
        ("💬  Biggest Conversations",      screen_top_convos,            False),
        ("🤖  Model Usage",                screen_models,                False),
        ("📖  Vocabulary Showdown",        screen_vocab,                 False),
        ("📈  Prompting Evolution",        screen_prompting_evolution,   False),
        ("🐇  Rabbit Hole Detector",       screen_rabbit_holes,          False),
        ("📡  Productivity Pulse",         screen_productivity_pulse,    True),
        ("😵  Gaslit by ChatGPT?",         screen_gaslit,                False),
        ("🧠  Prompt Analyzer",            screen_better_prompter,       True),
        (Separator("──────────────────────────────────────────"), None, False),
        ("←  Back",                       None,                         False),
    ]
    while True:
        console.print()
        console.rule("[bold violet]PATTERNS[/]")
        console.print("  [dim]How you work — and where it costs you.[/]\n")
        p_choices  = [label if isinstance(label, Separator) else label
                      for label, _, _ in PATTERN_ITEMS]
        p_dispatch = {label: (fn, needs) for label, fn, needs in PATTERN_ITEMS
                      if isinstance(label, str)}
        pick = questionary.select(
            "Choose a report:", choices=p_choices, style=MENU_STYLE,
        ).ask()
        if not pick or pick == "←  Back":
            return
        fn, needs_convos = p_dispatch.get(pick, (None, False))
        if fn is None:
            continue
        if needs_convos:
            fn(stats, convos)
        else:
            fn(stats)


def main():
    # ── Welcome screen (first run only) ───────────────────────────────────────
    cfg = load_config()
    if not cfg.get("seen_welcome"):
        screen_welcome()
        cfg["seen_welcome"] = True
        save_config(cfg)

    console.print()
    console.print(Panel(
        "[bold white]artifact.[/]\n[dim]your chatgpt history, finally readable[/]",
        border_style="dim", expand=False, padding=(1, 4),
    ))

    cfg    = load_config()
    folder = cfg.get("folder")
    stats  = None

    # Auto-detect the bundled 'collected' folder next to the script
    DEFAULT_COLLECTED = Path(__file__).parent / "collected"
    if not folder and DEFAULT_COLLECTED.exists():
        folder = str(DEFAULT_COLLECTED)
        cfg["folder"] = folder
        save_config(cfg)

    # Empty folder check
    if folder:
        json_files = list(Path(folder).glob("conversations-*.json"))
        if not json_files:
            console.print()
            console.print(Panel(
                f"  [bold yellow]No conversation files found in:[/]\n"
                f"  [bold]{folder}[/]\n\n"
                f"  To export your ChatGPT history:\n"
                f"  [cyan]1.[/] Go to [bold]chatgpt.com[/] → Settings → Data Controls\n"
                f"  [cyan]2.[/] Click [bold]Export Data[/] and wait for the email\n"
                f"  [cyan]3.[/] Unzip and copy the [bold]conversations-*.json[/] files\n"
                f"     into the [bold]collected/[/] folder next to this app\n"
                f"  [cyan]4.[/] Re-run [bold]./run.sh[/]",
                title="[bold yellow]⚠  Setup needed[/]", border_style="yellow",
            ))
            console.print()

    # ── Main menu (6 items + housekeeping) ────────────────────────────────────
    MAIN_MENU = [
        ("📖  Your Story", "What you've been thinking about lately.", "story"),
        ("✦   Best Ideas", "Your top 3 ideas, ranked by signal.",    "best_ideas"),
        ("💡  Ideas",      "The things that keep coming back.",      "ideas"),
        ("🔍  Search",     "Find anything you've already thought about.", "search"),
        ("📊  Patterns",   "How you work — and where it costs you.", "patterns"),
        ("🪞  Profile",    "Who you are as a thinker.",              "profile"),
        ("📤  Export",     "Take your thinking somewhere.",          "export"),
        (None, None, None),   # separator
        ("⚙️   Settings",  "",                                        "settings"),
        ("📁  Change Folder", "",                                     "folder"),
        ("ℹ️   About",      "",                                        "about"),
        ("🚪  Quit",       "",                                        "quit"),
    ]

    convos = None

    while True:
        console.print()
        if folder:
            folder_short = Path(folder).name
            n_files = len(list(Path(folder).glob("conversations-*.json")))
            console.print(
                f"  [dim]Folder:[/] [bold]{folder_short}[/]  [dim]({n_files} files)[/]"
            )
        else:
            console.print(
                "  [yellow]No folder loaded — select 'Change Folder' to begin.[/]"
            )

        if folder and stats is None:
            convos, err = parse_folder(folder)
            if err:
                console.print(f"[red]{err}[/]")
                folder = None
            else:
                stats = build_stats(convos)
                console.print(
                    f"  [green]✓[/] [bold]{fmt(len(stats['convos']))}[/] conversations loaded."
                )
                cfg = load_config()
                score_data = compute_signal_score(stats, convos)
                stats["_signal_score"] = score_data
                if cfg.get("signal_folder") != folder:
                    show_signal_score(score_data)
                    cfg["signal_folder"] = folder
                    save_config(cfg)
                # Auto-show Your Story once per new folder load
                if cfg.get("story_folder") != folder:
                    screen_your_story(stats)
                    cfg["story_folder"] = folder
                    save_config(cfg)

        # Build menu choices with right-aligned subtitles
        choices = []
        for label, subtitle, key in MAIN_MENU:
            if label is None:
                choices.append(Separator("──────────────────────────────────────────"))
            elif subtitle:
                choices.append(f"{label:<18}  [dim]{subtitle}[/]" if subtitle else label)
            else:
                choices.append(label)

        # questionary doesn't render rich markup in choices — use plain labels
        plain_choices = []
        key_map = {}
        for label, subtitle, key in MAIN_MENU:
            if label is None:
                plain_choices.append(Separator("──────────────────────────────────────────"))
            else:
                display = f"{label}  {subtitle}" if subtitle else label
                plain_choices.append(display)
                key_map[display] = key

        choice = questionary.select(
            "artifact.",
            choices=plain_choices,
            style=MENU_STYLE,
        ).ask()

        if choice is None:
            console.print("\n[dim]  Bye.[/]\n")
            break

        action = key_map.get(choice, "")

        if action == "quit":
            console.print("\n[dim]  Bye.[/]\n")
            break
        elif action == "folder":
            new_folder = set_folder()
            if new_folder:
                folder = new_folder
                stats  = None
                convos = None
                _EMBED_CACHE.clear()
        elif not stats and action not in ("settings", "about", "folder", "quit"):
            console.print("[yellow]  Load a folder first.[/]")
        elif action == "best_ideas":
            screen_best_ideas(stats, convos)
        elif action == "ideas":
            screen_ideas(stats, convos)
        elif action == "search":
            screen_search(stats, convos)
        elif action == "patterns":
            screen_patterns(stats, convos)
        elif action == "story":
            screen_your_story(stats)
        elif action == "profile":
            screen_profile(stats)
        elif action == "export":
            if convos is None:
                convos = list(stats["convos"].values())
            screen_export(stats, convos)
        elif action == "settings":
            screen_settings(stats or {})
        elif action == "about":
            screen_about(stats or {})

if __name__ == "__main__":
    main()
