# gridiron-scores

A small robot for Barry's Gridiron Ledger picks page.

- **`fetch_scores.py`** runs on GitHub Actions every 15 minutes during US game windows (and once a day in full). It reads ESPN's public scoreboard for the NFL, FBS and FCS and saves:
  - `scores/latest.json`: every game from 4 days ago to 7 days ahead, with status, clock, score, quarter scores, rank, record and sportsbook quotes (current, opening and last pre-kickoff numbers per book, each with the time it was first seen).
  - `scores/final/<date>.json`: every finished game for the season, one file per US Eastern date, so old games can still be graded.
  - `box/<id>.json`: player box scores for finished NFL and FBS games (kept for the season), with a PrizePicks-style fantasy score.
  - `odds/espn_sample.json`: one raw ESPN odds object, to check the parser against ESPN's real format.
- **`odds_providers.py`** turns each odds source into one normalized format. ESPN (DraftKings) works with no setup. To add FanDuel and BetMGM, add a free [The Odds API](https://the-odds-api.com) key as the repository secret `ODDS_API_KEY`; the robot then pulls it at most every 4 hours to stay inside the free quota. Prices are only ever copied from a provider, never estimated.
- **`apply_scores.py`** is run by Claude's score-update task. It matches the ledger's games to these files, locks each pick the first time the game is seen after kickoff, grades from that lock (pushes, voids for players who did not play), and writes small database updates.
- **`tests/`** has the automated tests (`python3 -m unittest discover -s tests`), run on every push by the Tests workflow.

- **`model/`** holds backtests of home-made college models against past betting lines, using College Football Data (the API key is a repository secret; raw downloads stay in the Actions cache). Summaries land in `model/results/`.

Only public game data lives here. No picks, no bets, no personal data.

To pause the robot: GitHub → this repo → Actions → "Fetch scores" → ⋯ → Disable workflow.
