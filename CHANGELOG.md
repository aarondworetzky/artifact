# Changelog

All notable changes to artifact. are documented here.

## [0.1.0.0] - 2026-03-22

### Added
- `screen_best_ideas()` — surfaces your top 3 most personally significant idea threads using composite signal scoring
- Return count signal: detects how many distinct sessions (30+ day gaps) you've returned to a thread — the "quietly carrying" indicator
- Cluster size cap (`MAX_CLUSTER=30`): filters topic buckets, keeping only specific idea threads
- Average depth filter (`MIN_AVG_MSGS=5`): excludes lookup-style clusters (short Q&A, not real thinking)

### Changed
- Best Ideas similarity threshold raised from 0.74 → 0.80 for tighter, more specific clusters
- Scoring weights rebalanced: returns (0.30) leads, followed by user_ratio (0.20), uniqueness (0.20), longevity (0.15), recurrence (0.10), depth (0.05)
- Recurrence normalization cap lowered from 15 → 10 (large clusters already filtered)
- Result metadata now shows "returned N×" when a thread has multiple distinct sessions

### Fixed
- Score footer text updated to reflect new return-based scoring formula
- `best_snippet` docstring corrected to match actual sort key (user word density, not message count)
