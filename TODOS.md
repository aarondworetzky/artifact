# artifact. — TODOS

## Annual Review
**What:** Generate a full narrative of the user's last 12 months of ChatGPT usage on demand.
**Why:** The "Spotify Wrapped" mechanic. People share these. It's the viral moment for the CLI.
**Pros:** High shareability, clear value, reuses all existing stats + embed cache.
**Cons:** Narrative generation is tricky without LLM. Template version risks feeling generic.
**Context:** Build after Export (screen_export) ships and the template-based narrative approach is validated there. The Annual Review is essentially Your Story + Export combined into one long-form output. Start after v1 distribution to HN/Reddit.
**Depends on:** screen_export() shipped and validated, screen_your_story() pattern established.

---

## Claude API — Enhanced "Your Voice" Export
**What:** With an optional Claude API key, rewrite Obsidian notes in the user's actual writing style.
**Why:** Template-based Your Voice is good. Style-matched Your Voice is genuinely magical.
**Pros:** Makes the Obsidian export feel like YOUR notes, not a summary. Dramatically better.
**Cons:** Requires API key (friction), costs money per export, privacy concern for some users.
**Context:** call_claude() is already stubbed in chatgpt_stats.py (line 1218). The hook is there. First infer writing style from the user's top 20 messages, pass as context to rewrite each convo. Gate on `cfg.get("claude_api_key")`.
**Depends on:** screen_export() with template Your Voice shipped first.

---

## Textual TUI Wrapper
**What:** Replace questionary arrow-key menus with a Textual persistent sidebar + content panel.
**Why:** Mouse navigation, proper layout, looks like a real product. Path to pywebview/desktop app.
**Pros:** All existing screen_* logic reused. Same Python, different container. Textual made by rich team.
**Cons:** ~20% rewrite surface. Changes every screen_* function signature slightly.
**Context:** Do this AFTER v1 is validated with real users (Ben + HN/Reddit). Don't refactor before product-market fit. The CLI is the validation tool; Textual is the product wrapper.
**Depends on:** All core features (Profile, Export, Your Story) shipped and user-tested.
