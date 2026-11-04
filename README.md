# gridiron-scores

A small robot for Barry's Gridiron Ledger picks page.

- **`fetch_scores.py`** runs on GitHub Actions every 5 minutes during US game windows (and once a day in full). It reads ESPN's public scoreboard for the NFL, FBS and FCS and saves:
  - `scores/latest.json`: every game from 4 days ago to 7 days ahead, with status, clock, score, quarter scores, rank, record and sportsbook quotes (current, opening and last pre-kickoff numbers per book, each with the time it was first seen).
  - `scores/final/<date>.json`: every finished game for the season, one file per US Eastern date, so old games can still be graded.
  - `box/<id>.json`: player box scores for finished NFL and FBS games (kept for the season), with a PrizePicks-style fantasy score.
  - `odds/espn_sample.json`: one raw ESPN odds object, to check the parser against ESPN's real format.
- **`odds_providers.py`** turns each odds source into one normalized format. ESPN (DraftKings) works with no setup. To add FanDuel and BetMGM, add a free [The Odds API](https://the-odds-api.com) key as the repository secret `ODDS_API_KEY`; the robot then pulls it at most every 4 hours, paced so the free tier's 500 requests a month last the month (each pull costs 3 per sport, so about every 8 hours during football season, half as often when no game starts within a day). Prices are only ever copied from a provider, never estimated.
- **`apply_scores.py`** is run by Claude's score-update task. It matches the ledger's games to these files, grades them (W, L, P; a prop is U when the player or market is missing from the box score a day later, X when it was added after the lock) and writes small database updates. Before kickoff (`--pregame`) it keeps two append-only, hash-linked records: every version of each pick, and every model prediction with its model version, creation time and data cutoff. When a game starts, the pick is locked from the **last version saved before kickoff** and marked verified; a pick with no copy saved before kickoff is locked as found and marked unverified (back-filled when first seen more than 12 hours after kickoff). A model prediction made after kickoff, or from data that ends after kickoff, gets no pregame credit. It also saves the week's sportsbook quotes.
- **`web/ledger-core.js`** is the betting math the page uses (no-vig probabilities, expected value with pushes, value verdicts, straight/parlay/PrizePicks settlement), kept here so it is tested. A bet is called value only when a model that passed its out-of-sample gate prices it more than 1% above the market; the overall lean record no longer moves any chance, because an audit found it made game-by-game predictions worse. Tested with `node --test web/*.test.js`. The page itself is not in this repository.
- **`tests/`** has the Python tests (`python3 -m unittest discover -s tests`); the Tests workflow runs them and the JavaScript tests on every push.

- **`model/`** holds the models, rerun by the Model backtest workflow on every change and on Tuesday and Friday mornings:
  - `ledger_model.py`: the Ledger Model, walk-forward points ratings for the NFL (nflverse) and college (College Football Data; the key is a repository secret). Settings are tuned on early seasons and scored on later ones; each market has a gate (log-loss gain over the closing line with t ≥ 2 and 52.4%+ in each of the last two test seasons) and gets no weight unless it passes. It also writes the push tables (how often games land on each number, given the closing spread or total) and current team ratings.
  - `nfl_props.py`: NFL player projections from nflverse weekly stats. Prop chances stay off on the page until locked projections beat the price-implied chance on 300+ graded props.
  - `nfl_data.py` and `nfl_lab.py`: the NFL model lab. `nfl_data.py` reduces nflverse play-by-play to team-game and passer-game tables (cached, not committed). `nfl_lab.py` builds point-in-time features (recency-weighted, opponent-adjusted efficiency; starting-QB values; dynamic ratings) and compares candidate models walk-forward: settings chosen on 2012–2017 only, 2018–2025 scored once with a refit each season, 2026 live. Every candidate is compared with the closing market, the Ledger Model and simple baselines on the same games; results are in `model/results/nfl_lab.md`. So far no candidate beats the closing market.
  - Shadow mode: the lab's best exportable candidate publishes team-level numbers to `model/results/nfl_shadow.json` (refreshed Tuesday and Friday with frozen settings). `apply_scores.py` records its prediction before every NFL kickoff, beside the Ledger Model's, for evaluation only; it never feeds a pick, a value verdict or a stake.
  - `value_audit.py`: checks whether moving the market's chance by the overall lean record improves game-by-game predictions (it reads the private ledger and prints to the screen; nothing is written here).
  - `cfb_backtest.py`, `cfb_eff.py`: earlier college backtests, kept for reference.

  Reproduce the NFL lab: `pip install numpy pandas scikit-learn scipy`, then `python3 model/nfl_data.py` (about a minute) and `python3 model/nfl_lab.py` (about 8 minutes).
  - Results land in `model/results/` (aggregate numbers and ratings only; raw downloads stay in the Actions cache).

Only public game data lives here. No picks, no bets, no personal data.

To pause the robot: GitHub → this repo → Actions → "Fetch scores" → ⋯ → Disable workflow.
