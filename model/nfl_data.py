#!/usr/bin/env python3
"""Point-in-time NFL data for the model lab, built only from public nflverse files.

Downloads each season's play-by-play once, keeps only what the models use, and writes two small tables:
  model/.cache/nfl/team_games.csv   one row per team per game: offensive efficiency in that game
                                    (EPA per play, pass and rush EPA, success rate, explosive plays, pace, turnovers),
                                    garbage time removed (win probability outside 5-95%, nflfastR's non-market model)
  model/.cache/nfl/qb_games.csv     one row per passer per game: dropbacks and EPA (to value starting quarterbacks)
Every number here describes a game that has been played; the lab only uses rows from before each forecast.
The market-based columns in play-by-play (vegas_wp, spread_line, ...) are never read.

  python3 model/nfl_data.py [--first 2006] [--last 2026] [--refresh-last]
"""
import argparse, csv, gzip, io, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache", "nfl")
PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{y}.csv.gz"
GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
COLS = ["game_id", "season", "week", "season_type", "posteam", "defteam", "play_type", "epa", "success", "yards_gained",
        "pass", "rush", "qb_dropback", "passer_player_id", "passer_player_name", "interception", "fumble_lost", "wp",
        "down", "game_seconds_remaining", "qb_scramble", "sack"]
OLD = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "JAC": "JAX"}


def fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron-ledger-model"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as fh:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    fh.write(b)
            return
        except Exception as e:
            if i == 3:
                raise
            print(f"retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))


def season_tables(y, tmp):
    """Team-game and QB-game rows for one season of play-by-play."""
    import pandas as pd
    path = os.path.join(tmp, f"pbp_{y}.csv.gz")
    fetch(PBP.format(y=y), path)
    df = pd.read_csv(path, compression="gzip", usecols=lambda c: c in COLS, low_memory=False)
    os.remove(path)
    df = df[df["posteam"].notna() & df["epa"].notna() & df["play_type"].isin(["pass", "run"])]
    df["posteam"] = df["posteam"].replace(OLD); df["defteam"] = df["defteam"].replace(OLD)
    live = df[(df["wp"] >= 0.05) & (df["wp"] <= 0.95)]          # garbage time out
    df["explosive"] = ((df["pass"] == 1) & (df["yards_gained"] >= 20)) | ((df["rush"] == 1) & (df["yards_gained"] >= 10))
    live = live.assign(explosive=df.loc[live.index, "explosive"])
    g = live.groupby(["game_id", "season", "week", "season_type", "posteam", "defteam"])
    tg = pd.DataFrame({
        "plays": g.size(),
        "epa": g["epa"].sum(),
        "pass_n": g["pass"].sum(),
        "pass_epa": g.apply(lambda x: x.loc[x["pass"] == 1, "epa"].sum(), include_groups=False),
        "rush_n": g["rush"].sum(),
        "rush_epa": g.apply(lambda x: x.loc[x["rush"] == 1, "epa"].sum(), include_groups=False),
        "success": g["success"].sum(),
        "explosive": g["explosive"].sum(),
        "early_n": g.apply(lambda x: (x["down"] <= 2).sum(), include_groups=False),
        "early_epa": g.apply(lambda x: x.loc[x["down"] <= 2, "epa"].sum(), include_groups=False),
    }).reset_index()
    # pace and turnovers use every offensive play, garbage time included
    a = df.groupby(["game_id", "posteam"])
    allp = pd.DataFrame({"plays_all": a.size(), "turnovers": a["interception"].sum() + a["fumble_lost"].sum()}).reset_index()
    tg = tg.merge(allp, on=["game_id", "posteam"], how="left")
    q = df[(df["qb_dropback"] == 1) & df["passer_player_id"].notna()]
    qg = q.groupby(["game_id", "season", "week", "posteam", "passer_player_id"]).agg(
        name=("passer_player_name", "first"), dropbacks=("epa", "size"), epa=("epa", "sum")).reset_index()
    return tg, qg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2006)
    ap.add_argument("--last", type=int, default=int(time.strftime("%Y")) if int(time.strftime("%m")) >= 3 else int(time.strftime("%Y")) - 1)
    ap.add_argument("--refresh-last", action="store_true", help="re-download the current season (it changes every week)")
    a = ap.parse_args(argv)
    import pandas as pd
    os.makedirs(CACHE, exist_ok=True)
    per = os.path.join(CACHE, "seasons"); os.makedirs(per, exist_ok=True)
    for y in range(a.first, a.last + 1):
        tp, qp = os.path.join(per, f"team_{y}.csv"), os.path.join(per, f"qb_{y}.csv")
        if os.path.exists(tp) and os.path.exists(qp) and not (a.refresh_last and y == a.last):
            continue
        t0 = time.time()
        tg, qg = season_tables(y, per)
        tg.to_csv(tp, index=False); qg.to_csv(qp, index=False)
        print(f"{y}: {len(tg)} team-games, {len(qg)} passer-games in {time.time() - t0:.0f}s", file=sys.stderr)
    T = pd.concat([pd.read_csv(os.path.join(per, f"team_{y}.csv")) for y in range(a.first, a.last + 1)])
    Q = pd.concat([pd.read_csv(os.path.join(per, f"qb_{y}.csv")) for y in range(a.first, a.last + 1)])
    T.to_csv(os.path.join(CACHE, "team_games.csv"), index=False)
    Q.to_csv(os.path.join(CACHE, "qb_games.csv"), index=False)
    gp = os.path.join(CACHE, "games.csv")
    fetch(GAMES, gp)
    print(f"team_games {len(T)}, qb_games {len(Q)}, games.csv saved", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
