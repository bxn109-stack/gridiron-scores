# gridiron-scores

A small robot for Barry's Gridiron Ledger picks page.

- **`fetch_scores.py`** runs on GitHub Actions every 15 minutes during US game windows (and once a day in full). It reads ESPN's public scoreboard for the NFL, FBS and FCS and saves:
  - `scores/latest.json`: every game from 4 days ago to 7 days ahead, with status, clock, score, quarter scores, rank and record.
  - `box/<id>.json`: player box scores for finished NFL and FBS games, with a PrizePicks-style fantasy score.
- **`apply_scores.py`** is run by Claude's score-update task. It matches the ledger's games to these files, grades the picks and props, and writes small database updates.

- **`model/`** holds backtests of home-made college models against past betting lines, using College Football Data (the API key is a repository secret; raw downloads stay in the Actions cache). Summaries land in `model/results/`.

Only public game data lives here. No picks, no bets, no personal data.

To pause the robot: GitHub → this repo → Actions → "Fetch scores" → ⋯ → Disable workflow.
