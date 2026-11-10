#!/usr/bin/env python3
"""Basketball history for the models: every finished game with its final score, ESPN's last listed line (the closing
line for past games) and team box-score totals, one compact file per league and season. Runs on GitHub Actions
(the "Basketball models" workflow); standard library only.

  python3 model/bb_data.py [--leagues NBA,MBB,WBB,WNBA] [--seasons 2023,...] [--max-requests N] [--boxes N] [--lines N]

ESPN's scoreboard drops the odds once a game is old, so closing lines (and the turnovers and offensive rebounds the
scoreboard leaves out) are back-filled from each game's ESPN summary ("pickcenter" and the team box score), up to
--lines requests per run, newest seasons first; a game whose summary has no line is not asked again. This season's
games also take their closing line from the robot's own files (scores/bb).

Writes (public game data only)
  data/bb/<LG>_<season>.json   {lg, season, updatedAt, cols, done: [dates fully fetched], games: [[...], ...]}
                               season = the year the season ends (2026 = 2025-26); the WNBA uses its calendar year.
                               cols: id, date (UTC ISO), type (2 regular, 3 postseason), neutral, home id, away id,
                               home name, away name, home score, away score, close home margin (+ = home favored),
                               close total, home moneyline, away moneyline, book, home stats, away stats
                               stats = [fga, fgm, fta, ftm, tpa, tpm, reb, oreb, ast, to, pts] (None when ESPN omits one)
  data/bb/raw_<LG>.json        one raw ESPN event per league, to check the parser against ESPN's real format
  box/bb/<date>.json           player box scores for this season's finished games older than the robot's window
                               (fetch_bb.py saves the recent ones)
"""
import argparse
import datetime as dt
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fetch_bb as BB  # noqa: E402
import fetch_scores as FS  # noqa: E402
import odds_providers as OP  # noqa: E402

ET = FS.ET
DATA = os.path.join(ROOT, "data", "bb")
COLS = ["id", "date", "type", "neu", "hid", "aid", "h", "a", "hs", "as", "hm", "tot", "mlh", "mla", "book", "hst", "ast"]
STATS = ["fga", "fgm", "fta", "ftm", "tpa", "tpm", "reb", "oreb", "ast", "to", "pts"]
SNAME = {"fieldGoalsAttempted": "fga", "fieldGoalsMade": "fgm", "freeThrowsAttempted": "fta", "freeThrowsMade": "ftm",
         "threePointFieldGoalsAttempted": "tpa", "threePointFieldGoalsMade": "tpm", "rebounds": "reb",
         "offensiveRebounds": "oreb", "assists": "ast", "turnovers": "to", "totalTurnovers": "to", "points": "pts"}
# months a season's games fall in (inclusive): NBA Oct-Jun, college Nov-Apr, WNBA May-Oct
WINDOW = {"NBA": ((-1, 10, 1), (0, 6, 30)), "MBB": ((-1, 11, 1), (0, 4, 15)), "WBB": ((-1, 11, 1), (0, 4, 15)),
          "WNBA": ((0, 5, 1), (0, 10, 31))}
LEAGUE_ORDER = {"NBA": 0, "WNBA": 1, "MBB": 2, "WBB": 3}
SEASONS = {"NBA": [2023, 2024, 2025, 2026, 2027], "MBB": [2023, 2024, 2025, 2026, 2027],
           "WBB": [2023, 2024, 2025, 2026, 2027], "WNBA": [2023, 2024, 2025, 2026]}


def season_dates(lg, season, today):
    (y0, m0, d0), (y1, m1, d1) = WINDOW[lg]
    a, b = dt.date(season + y0, m0, d0), min(dt.date(season + y1, m1, d1), today - dt.timedelta(days=1))
    out = []
    while a <= b:
        out.append(a)
        a += dt.timedelta(days=1)
    return out


def current_season(lg, today):
    if lg == "WNBA":
        return today.year
    return today.year + 1 if today.month >= 7 else today.year


def team_stats(c):
    st = {}
    for s in c.get("statistics") or []:
        k = SNAME.get(s.get("name"))
        v = FS.to_num(s.get("displayValue"))
        if k and v is not None:
            st[k] = int(v) if float(v).is_integer() else v
    return [st.get(k) for k in STATS] if st else None


def parse_game(ev, lg):
    """Raw ESPN scoreboard event -> compact row, or None (not finished, preseason, or missing a score)."""
    g = FS.parse_event(ev, lg, "1970-01-01T00:00:00+00:00")
    if not g or not g.get("completed") or g["home"].get("score") is None or g["away"].get("score") is None:
        return None
    stype = ((ev.get("season") or {}).get("type"))
    if stype == 1:
        return None
    comp = (ev.get("competitions") or [{}])[0]
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), {})
    away = next((c for c in cs if c.get("homeAway") == "away"), {})
    cur, _ = g.get("_q") or ({}, {})
    q = cur.get("DraftKings") or next(iter(cur.values()), {}) if cur else {}
    ml = (q or {}).get("ml") or {}
    o = g.get("odds") or {}
    return [g["id"], g["date"], stype, 1 if g["neutral"] else 0, g["home"]["id"], g["away"]["id"],
            g["home"].get("short") or g["home"].get("loc"), g["away"].get("short") or g["away"].get("loc"),
            g["home"]["score"], g["away"]["score"], o.get("hm"), o.get("tot"), ml.get("H"), ml.get("A"),
            OP.canon(o.get("book")) if o.get("book") else None, team_stats(home), team_stats(away)]


def path_for(lg, season):
    return os.path.join(DATA, f"{lg}_{season}.json")


def load(lg, season):
    d = FS.read_json(path_for(lg, season), {})
    if d.get("cols") != COLS:
        d = {}
    return {"lg": lg, "season": season, "cols": COLS, "done": list(d.get("done") or []), "games": list(d.get("games") or []),
            "tried": list(d.get("tried") or [])}


def summary_line(summ):
    """ESPN game summary -> ({hm, tot, book} or None, (home ml, away ml), {home: stats, away: stats} from the team box)."""
    comps = ((summ.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []
    home = next((c for c in comps if c.get("homeAway") == "home"), {})
    away = next((c for c in comps if c.get("homeAway") == "away"), {})
    ha, aa = (home.get("team") or {}).get("abbreviation"), (away.get("team") or {}).get("abbreviation")
    items = [o for o in (summ.get("pickcenter") or []) + (summ.get("odds") or []) if isinstance(o, dict)]
    comp = {"odds": items}
    line = FS.odds_of(comp, ha, aa) if items else None
    ml = (None, None)
    if items:
        cur, _ = OP.espn_quotes(comp, "1970-01-01T00:00:00+00:00")
        q = cur.get("DraftKings") or next(iter(cur.values()), {}) if cur else {}
        m = (q or {}).get("ml") or {}
        ml = (m.get("H"), m.get("A"))
    box = BB.parse_box_bb(summ) or {}
    stats = {}
    for abbr, t in (box.get("teams") or {}).items():
        side = "home" if t.get("home") else "away" if t.get("home") is False else None
        if side:
            stats[side] = t.get("st") or {}
    return line, ml, stats


def fill_stats(arr, st):
    """Add turnovers and offensive rebounds (and anything else missing) from a summary's team box to a stats list."""
    if not st:
        return arr
    arr = list(arr) if arr else [None] * len(STATS)
    for i, k in enumerate(STATS):
        if arr[i] is None and st.get(k) is not None:
            arr[i] = st[k]
    return arr


def lines_backfill(lg, d, budget, get=None, pause=0.15):
    """Closing lines (and full team box totals) for games that have none, from ESPN's summary. Returns requests used."""
    get = get or FS.get_json
    tried = set(d.get("tried") or [])
    used = 0
    for r in d["games"]:
        if used >= budget:
            break
        if r[10] is not None or r[0] in tried:
            continue
        summ = get(f"{BB.BASE}/{BB.SPORT[lg]}/summary?event={r[0]}")
        used += 1
        time.sleep(pause)
        if summ is None:
            continue                                     # network trouble: ask again next run
        tried.add(r[0])
        line, ml, stats = summary_line(summ)
        if line:
            r[10], r[11] = line.get("hm"), line.get("tot")
            r[14] = OP.canon(line.get("book")) if line.get("book") else r[14]
        if ml[0] is not None:
            r[12], r[13] = ml
        r[15] = fill_stats(r[15], stats.get("home"))
        r[16] = fill_stats(r[16], stats.get("away"))
    d["tried"] = sorted(tried)
    return used


def robot_closes(lg, d):
    """This season's closing lines from the robot's own day files (scores/bb/<date>.json, saved before tip)."""
    n = 0
    cache = {}
    for r in d["games"]:
        if r[10] is not None or not r[1]:
            continue
        day = FS.et_date({"date": r[1]})
        if day not in cache:
            cache[day] = (FS.read_json(BB.day_path(day), {}).get("games") or {}) if day else {}
        g = cache[day].get(r[0])
        c = (g or {}).get("close") or {}
        if c.get("hm") is not None or c.get("tot") is not None:
            r[10], r[11], r[14] = c.get("hm"), c.get("tot"), OP.canon(c.get("book")) if c.get("book") else None
            n += 1
    return n


def fetch_season(lg, season, today, budget, get=None, pause=0.15):
    """Fetch the dates of one season not fetched in full yet. Returns (data, requests used, raw sample or None)."""
    get = get or FS.get_json
    d = load(lg, season)
    done = set(d["done"])
    rows = {r[0]: r for r in d["games"]}
    used, sample = 0, None
    sport, extra = BB.SPORT[lg], next(x for l, _, x in BB.FEEDS if l == lg)
    for day in season_dates(lg, season, today):
        key = day.isoformat()
        if key in done or used >= budget:
            continue
        data = get(f"{BB.BASE}/{sport}/scoreboard?dates={day.strftime('%Y%m%d')}{extra}")
        used += 1
        time.sleep(pause)
        if data is None:
            continue
        evs = data.get("events") or []
        all_over = True
        for ev in evs:
            st = (((ev.get("competitions") or [{}])[0].get("status") or {}).get("type") or {})
            if st.get("state") != "post" and st.get("name") not in BB.DEAD:
                all_over = False
            r = parse_game(ev, lg)
            if r:
                rows[r[0]] = r
                if sample is None:
                    sample = ev
        if all_over and day < today - dt.timedelta(days=2):
            done.add(key)
    d["done"] = sorted(done)
    d["games"] = sorted(rows.values(), key=lambda r: (r[1] or "", r[0]))
    return d, used, sample


def backfill_boxes(lg, d, today, limit, fetch=None):
    """Box scores for this season's finished games that are older than the robot's own window (it saves the rest)."""
    old = today - dt.timedelta(days=4)
    games = [{"id": r[0], "lg": lg, "date": r[1], "state": "post", "completed": True} for r in d["games"]
             if r[1] and FS.et_date({"date": r[1]}) and FS.et_date({"date": r[1]}) < old]
    return BB.save_boxes(games, dt.datetime.now(dt.timezone.utc), fetch, limit) if games else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="NBA,MBB,WBB,WNBA")
    ap.add_argument("--seasons", default="", help="comma list; default: each league's last few seasons and the current one")
    ap.add_argument("--max-requests", type=int, default=4000)
    ap.add_argument("--boxes", type=int, default=1500, help="box scores to back-fill for the current season per run")
    ap.add_argument("--lines", type=int, default=7000, help="game summaries to read per run for missing closing lines")
    args = ap.parse_args(argv)
    today = dt.datetime.now(dt.timezone.utc).astimezone(ET).date()
    budget = args.max_requests
    os.makedirs(DATA, exist_ok=True)
    for lg in [x for x in args.leagues.split(",") if x in WINDOW]:
        seasons = [int(x) for x in args.seasons.split(",") if x] or SEASONS[lg]
        cur = current_season(lg, today)
        for season in seasons:
            if season > cur or budget <= 0:
                continue
            d, used, sample = fetch_season(lg, season, today, budget)
            budget -= used
            n_lines = sum(1 for r in d["games"] if r[10] is not None)
            n_stats = sum(1 for r in d["games"] if r[15])
            if used or not os.path.exists(path_for(lg, season)):
                d["updatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                FS.write_json(path_for(lg, season), d)
            if sample:
                FS.write_json(os.path.join(DATA, f"raw_{lg}.json"), sample)
            print(f"{lg} {season}: {len(d['games'])} games ({n_lines} with a line, {n_stats} with team stats), "
                  f"{len(d['done'])} dates complete, {used} requests")
            if season == cur and args.boxes > 0:
                n = backfill_boxes(lg, d, today, args.boxes)
                print(f"{lg} {season}: {n} older box score(s) back-filled")
    # closing lines: this season's from the robot's files, older ones from ESPN's game summaries (newest seasons first,
    # leagues in turn, so every league's test seasons fill before its tuning seasons)
    lines_budget = args.lines
    queue = []
    for lg in [x for x in args.leagues.split(",") if x in WINDOW]:
        seasons = [int(x) for x in args.seasons.split(",") if x] or SEASONS[lg]
        cur = current_season(lg, today)
        # the pro leagues are small (about 1,200 games a season): fill them whole first, then college newest-first
        queue += [((0, LEAGUE_ORDER[lg], -s) if lg in ("NBA", "WNBA") else (1, -s, LEAGUE_ORDER.get(lg, 9)), lg, s) for s in seasons if s <= cur]
    for _, lg, season in sorted(queue):
        if not os.path.exists(path_for(lg, season)):
            continue
        d = load(lg, season)
        n_robot = robot_closes(lg, d) if season == current_season(lg, today) else 0
        used = lines_backfill(lg, d, lines_budget) if lines_budget > 0 else 0
        lines_budget -= used
        if used or n_robot:
            d["updatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            FS.write_json(path_for(lg, season), d)
        n_lines = sum(1 for r in d["games"] if r[10] is not None)
        print(f"{lg} {season}: closing lines on {n_lines} of {len(d['games'])} games ({used} summaries read, {n_robot} from the robot)")
    print(f"{args.max_requests - budget} scoreboard requests, {args.lines - lines_budget} summaries in total")


if __name__ == "__main__":
    main()
