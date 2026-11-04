# gridiron-scores

A small robot for Barry's Gridiron Ledger picks page.

- **`fetch_scores.py`** runs on GitHub Actions every 5 minutes during US game windows (and once a day in full). It reads ESPN's public scoreboard for the NFL, FBS and FCS and saves:
  - `scores/latest.json`: every game from 4 days ago to 7 days ahead, with status, clock, score, quarter scores, rank, record and sportsbook quotes (current, opening and last pre-kickoff numbers per book, each with the time it was first seen).
  - `scores/final/<date>.json`: every finished game for the season, one file per US Eastern date, so old games can still be graded.
  - `box/<id>.json`: player box scores for finished NFL and FBS games (kept for the season), with a PrizePicks-style fantasy score.
  - `odds/espn_sample.json`: one raw ESPN odds object, to check the parser against ESPN's real format.
- **`odds_providers.py`** turns each odds source into one normalized format. ESPN (DraftKings) works with no setup. To add FanDuel and BetMGM, add a free [The Odds API](https://the-odds-api.com) key as the repository secret `ODDS_API_KEY`; the robot then pulls it at most every 4 hours to stay inside the free quota. Prices are only ever copied from a provider, never estimated.
- **`apply_scores.py`** is run by Claude's score-update task. It matches the ledger's games to these files, locks each pick the first time the game is seen after kickoff (picks first seen more than 12 hours after kickoff are marked back-filled), grades from that lock (W, L, P; a prop is U when the player or market is missing from the box score a day later, X when it was added after the lock), and writes small database updates. With `--pregame` it also saves the week's sportsbook quotes and an append-only history of each pick before kickoff.
- **`web/ledger-core.js`** is the betting math the page uses (no-vig probabilities, expected value with pushes, value verdicts, straight/parlay/PrizePicks settlement), kept here so it is tested (`node --test web/*.test.js`). The page itself is not in this repository.
- **`tests/`** has the Python tests (`python3 -m unittest discover -s tests`); the Tests workflow runs them and the JavaScript tests on every push.

- **`model/`** holds the models, rerun by the Model backtest workflow on every change and on Tuesday and Friday mornings:
  - `ledger_model.py`: the Ledger Model, walk-forward points ratings for the NFL (nflverse) and college (College Football Data; the key is a repository secret). Settings are tuned on early seasons and scored on later ones; each market has a gate (log-loss gain over the closing line with t ≥ 2 and 52.4%+ in each of the last two test seasons) and gets no weight unless it passes. It also writes the push tables (how often games land on each number, given the closing spread or total) and current team ratings.
  - `nfl_props.py`: NFL player projections from nflverse weekly stats. Prop chances stay off on the page until locked projections beat the price-implied chance on 300+ graded props.
  - `cfb_backtest.py`, `cfb_eff.py`: earlier college backtests, kept for reference.
  - Results land in `model/results/` (aggregate numbers and ratings only; raw downloads stay in the Actions cache).

Only public game data lives here. No picks, no bets, no personal data.

To pause the robot: GitHub → this repo → Actions → "Fetch scores" → ⋯ → Disable workflow.
