# Changelog

All notable changes to artifact. are documented here.

## [0.1.0.0] - 2026-03-22

### Added
- **Best Ideas** now detects how many times you've returned to a thread after a 30+ day gap — the "quietly carrying" signal. A thread you've come back to 4 times over 2 years ranks higher than 8 conversations in a single sprint.
- Results now show "returned N×" so you can see at a glance which threads you keep coming back to.

### Changed
- **Best Ideas** clusters are now tighter (0.80 similarity vs 0.74) — you get specific idea threads, not broad topic categories.
- Clusters with 30+ conversations are excluded: those are domains, not ideas.
- Clusters where the average conversation is fewer than 5 messages are excluded: lookup questions, not thinking.
- Scoring now leads with returns (how often you came back) rather than raw conversation count.
