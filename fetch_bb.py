#!/usr/bin/env python3
"""Fetch NBA, WNBA and Division I college basketball (men's and women's) scores, sportsbook quotes and ESPN's
pregame win chance (BPI matchup predictor) from ESPN's public API, as one small JSON file per US Eastern date.

Runs on GitHub Actions (see .github/workflows/bb_scores.yml). Standard library only. The football robot
(fetch_scores.py) is not changed by this file; it only borrows its parsing helpers.

Writes
  scores/bb/<YYYY-MM-DD>.json   every game that tips on that US Eastern date:
                                {date, complete, checkedAt, games: {<espnId>: game}}
                                game = the football robot's format (teams, score, period scores, status, quotes with
                                opening and closing numbers) plus `pred` = ESPN's pregame win chance for the home team.
  scores/bb/status.json         {checkedAt, updatedAt, dates} so the grader can tell how fresh the feed is
  box/bb/<YYYY-MM-DD>.json      player box scores for that date's finished games (player props are graded from these):
                                {date, updatedAt, games: {<espnId>: {lg, teams: {<abbr>: {id, home, st}}, players: [...]}}}
                                player = {n, tm, min, pts, reb, ast, stl, blk, to, tpm, fgm, fga, ftm, fta, oreb, dreb,
                                pf, start, fp}; fp = PrizePicks basketball fantasy score

Usage
  python3 fetch_bb.py          quick run: yesterday and today (ET), plus the win chance of games starting soon
  python3 fetch_bb.py --full   full run: three days back to two days ahead, plus archive catch-up
  python3 fetch_bb.py --busy   exit 0 if a game is on or tips within 45 minutes
"""
import datetime as dt
import json
import os
import sys
import time

import fetch_scores as FS

ET = FS.ET
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball"
FEEDS = [  # league label, ESPN sport path, extra query (groups=50 = Division I)
    ("NBA", "nba", ""),
    ("WNBA", "wnba", ""),
    ("MBB", "mens-college-basketball", "&groups=50&limit=500"),
    ("WBB", "womens-college-basketball", "&groups=50&limit=500"),
]
SPORT = {lg: path for lg, path, _ in FEEDS}
DAYS_BACK, DAYS_AHEAD = 3, 2
ARCHIVE_START = dt.date(2026, 11, 9)   # first basketball slate; earlier dates are never fetched
BACKFILL_MAX_DATES = 10                # incomplete past dates re-fetched per run
PRED_EVERY_MIN = 180                   # refresh a pregame win chance at most this often
PRED_HORIZON_H = 36                    # only games tipping within this many hours
PRED_MAX_PER_RUN = 400
CHECK_EVERY_MIN = 30
ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(ROOT, "scores", "bb")
BOX_DIR = os.path.join(ROOT, "box", "bb")
BOX_MAX_PER_RUN = 300                  # box scores fetched per run (catch-up spreads over several runs)
DEAD = ("STATUS_POSTPONED", "STATUS_CANCELED")


def day_path(d):
    return os.path.join(DIR, f"{d.isoformat()}.json")


def box_path(d):
    return os.path.join(BOX_DIR, f"{d.isoformat()}.json")


# ---------- box scores ----------
# ESPN basketball box score labels -> our field names ("7-15" style values are split into made and attempted).
PAIR = {"FG": ("fgm", "fga"), "3PT": ("tpm", "tpa"), "FT": ("ftm", "fta")}
ONE = {"MIN": "min", "OREB": "oreb", "DREB": "dreb", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
       "TO": "to", "PF": "pf", "PTS": "pts"}
KEY_LABEL = {"minutes": "MIN", "fieldGoalsMade-fieldGoalsAttempted": "FG", "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "3PT",
             "freeThrowsMade-freeThrowsAttempted": "FT", "offensiveRebounds": "OREB", "defensiveRebounds": "DREB",
             "rebounds": "REB", "assists": "AST", "steals": "STL", "blocks": "BLK", "turnovers": "TO", "fouls": "PF",
             "points": "PTS", "totalTurnovers": "TO"}


def fantasy_bb(p):
    """PrizePicks basketball fantasy score: points 1, rebounds 1.2, assists 1.5, blocks 3, steals 3, turnovers -1."""
    g = lambda k: p.get(k) or 0
    return round(g("pts") + 1.2 * g("reb") + 1.5 * g("ast") + 3 * g("blk") + 3 * g("stl") - g("to"), 2)


def _put(p, label, v):
    label = KEY_LABEL.get(label, label)
    if label in PAIR:
        s = str(v or "")
        if "-" in s:
            a, b = (FS.to_int(x) for x in s.split("-", 1))
            if a is not None and b is not None:
                p[PAIR[label][0]], p[PAIR[label][1]] = a, b
        return
    f = ONE.get(label)
    if f:
        n = FS.to_int(str(v).split(":")[0]) if f == "min" else FS.to_int(v)
        if n is not None:
            p[f] = n


def parse_box_bb(summary):
    """ESPN game summary -> {teams: {abbr: {id, home, st}}, players: [...]} or None (no box score yet)."""
    box = (summary or {}).get("boxscore") or {}
    players, teams = [], {}
    homes = {}
    for c in (((summary or {}).get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []:
        homes[str((c.get("team") or {}).get("id"))] = c.get("homeAway") == "home"
    for t in box.get("teams") or []:
        tm = t.get("team") or {}
        st = {}
        for s in t.get("statistics") or []:
            lab = KEY_LABEL.get(s.get("name")) or s.get("abbreviation") or s.get("label")
            if lab:
                _put(st, lab, s.get("displayValue"))
        teams[tm.get("abbreviation") or str(tm.get("id"))] = {"id": str(tm.get("id")), "home": homes.get(str(tm.get("id"))), "st": st}
    for side in box.get("players") or []:
        abbr = (side.get("team") or {}).get("abbreviation")
        for cat in side.get("statistics") or []:
            labels = cat.get("labels") or []
            keys = cat.get("keys") or cat.get("names") or []
            for a in cat.get("athletes") or []:
                ath = a.get("athlete") or {}
                stats = a.get("stats") or []
                if a.get("didNotPlay") or not stats:
                    continue
                p = {"n": ath.get("displayName") or ath.get("shortName"), "tm": abbr}
                for i, v in enumerate(stats):
                    lab = labels[i] if i < len(labels) else (keys[i] if i < len(keys) else None)
                    if lab:
                        _put(p, lab, v)
                if not p.get("min") and not any(p.get(k) for k in ("pts", "reb", "ast", "fga", "fta")):
                    continue                     # listed but did not play
                if a.get("starter"):
                    p["start"] = True
                p["fp"] = fantasy_bb(p)
                players.append(p)
    if not players:
        return None
    return {"teams": teams, "players": players}


def save_boxes(games, now_utc, fetch=None, limit=BOX_MAX_PER_RUN):
    """Box scores for finished games that have none saved yet, one file per US Eastern date. Returns how many were saved."""
    pause = 0.2 if fetch is None else 0
    fetch = fetch or (lambda lg, gid: FS.get_json(f"{BASE}/{SPORT[lg]}/summary?event={gid}"))
    by_day = {}
    for g in games:
        if g.get("state") == "post" and g.get("completed") and g.get("lg") in SPORT:
            d = FS.et_date(g)
            if d:
                by_day.setdefault(d, []).append(g)
    saved = 0
    retrieved = now_utc.isoformat(timespec="seconds")
    for d in sorted(by_day, reverse=True):
        path = box_path(d)
        cur = FS.read_json(path, {})
        have = dict(cur.get("games") or {})
        n0 = len(have)
        for g in sorted(by_day[d], key=lambda x: x["id"]):
            if g["id"] in have or saved >= limit:
                continue
            b = parse_box_bb(fetch(g["lg"], g["id"]))
            time.sleep(pause)
            if not b:
                continue
            have[g["id"]] = dict(b, lg=g["lg"])
            saved += 1
        if len(have) > n0:
            os.makedirs(BOX_DIR, exist_ok=True)
            FS.write_json(path, {"date": d.isoformat(), "updatedAt": retrieved, "games": dict(sorted(have.items()))})
    return saved


def parse_pred(summary):
    """ESPN's matchup predictor -> {"hp": home win chance in percent} or None."""
    p = (summary or {}).get("predictor") or {}
    h, a = p.get("homeTeam") or {}, p.get("awayTeam") or {}
    hp = FS.to_num(h.get("gameProjection"))
    if hp is None:
        ap = FS.to_num(a.get("gameProjection"))
        hp = 100 - ap if ap is not None else None
    if hp is None or not 0 < hp < 100:
        return None
    return {"hp": round(hp, 1), "src": "ESPN BPI"}


def fetch_dates(dates, retrieved):
    """Scoreboards for YYYYMMDD dates -> ({id: game}, feeds that answered, feeds asked)."""
    out, ok, asked = {}, 0, 0
    for lg, sport, extra in FEEDS:
        for d in dates:
            asked += 1
            data = FS.get_json(f"{BASE}/{sport}/scoreboard?dates={d}{extra}")
            if not data:
                continue
            ok += 1
            for ev in data.get("events") or []:
                g = FS.parse_event(ev, lg, retrieved)
                if g:
                    out[g["id"]] = g
    return out, ok, asked


def merge(g, old_g):
    """The football robot's quote bookkeeping, plus the closing line and the last pregame win chance."""
    g["close"] = g["odds"] if (g["state"] == "pre" and g.get("odds")) else old_g.get("close")
    if old_g.get("pred"):
        g["pred"] = old_g["pred"]
    g = FS.merge_quotes(g, old_g)
    FS.sync_open_close(g)
    return g


def pred_due(g, now):
    if g.get("state") != "pre" or g.get("status") in DEAD:
        return False
    try:
        ko = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return False
    if not now < ko <= now + dt.timedelta(hours=PRED_HORIZON_H):
        return False
    at = (g.get("pred") or {}).get("at")
    if not at:
        return True
    return (now - dt.datetime.fromisoformat(at)).total_seconds() >= PRED_EVERY_MIN * 60


def refresh_preds(games, now, retrieved, fetch=None):
    """Pregame win chances for games tipping soon. A chance is only ever saved before tip (stamped `at`)."""
    pause = 0.15 if fetch is None else 0   # be gentle with ESPN on real runs
    fetch = fetch or (lambda lg, gid: FS.get_json(f"{BASE}/{SPORT[lg]}/summary?event={gid}"))
    n = 0
    for g in sorted(games, key=lambda x: x.get("date") or ""):
        if n >= PRED_MAX_PER_RUN:
            break
        if not pred_due(g, now):
            continue
        n += 1
        p = parse_pred(fetch(g["lg"], g["id"]))
        if p:
            g["pred"] = dict(p, at=retrieved)
        elif g.get("pred"):
            g["pred"] = dict(g["pred"], at=retrieved, stale=True)
        time.sleep(pause)
    return n


def write_day(d, games, complete, retrieved):
    """Write one date file when something other than the check time changed. Returns True when written."""
    path = day_path(d)
    cur = FS.read_json(path, {})
    body = {"date": d.isoformat(), "complete": bool(complete), "games": games}
    if {k: cur.get(k) for k in body} == body and os.path.exists(path):
        return False
    FS.write_json(path, dict(body, updatedAt=retrieved))
    return True


def busy():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(ET).date()
    for d in (today - dt.timedelta(days=1), today):
        for g in (FS.read_json(day_path(d), {}).get("games") or {}).values():
            if g.get("state") == "in":
                return 0
            if g.get("state") == "pre" and g.get("status") not in DEAD:
                try:
                    ko = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if now - dt.timedelta(minutes=30) <= ko <= now + dt.timedelta(minutes=45):
                    return 0
    return 1


def run(full, now_utc=None, fetch_dates_fn=fetch_dates, pred_fetch=None, box_fetch=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    retrieved = now_utc.isoformat(timespec="seconds")
    today = now_utc.astimezone(ET).date()
    span = range(-DAYS_BACK, DAYS_AHEAD + 1) if full else range(-1, 1)
    days = [today + dt.timedelta(days=k) for k in span]
    # catch up past dates that never got a complete file
    if full:
        d = ARCHIVE_START
        missing = []
        while d < today - dt.timedelta(days=DAYS_BACK):
            if not FS.read_json(day_path(d), {}).get("complete"):
                missing.append(d)
            d += dt.timedelta(days=1)
        days = missing[:BACKFILL_MAX_DATES] + days
    days = [d for d in days if d >= ARCHIVE_START]
    if not days:
        print("Nothing to fetch yet (before the first basketball slate).")
        return 0

    fresh, ok, asked = fetch_dates_fn([d.strftime("%Y%m%d") for d in days], retrieved)
    if ok == 0:
        print("No feed answered; leaving files unchanged.")
        return 0
    by_day = {}
    for g in fresh.values():
        d = FS.et_date(g)
        if d in days:
            by_day.setdefault(d, []).append(g)
    olds = {d: FS.read_json(day_path(d), {}).get("games") or {} for d in days}
    merged = {}
    for d in days:
        cur = {}
        for g in by_day.get(d, []):
            cur[g["id"]] = merge(g, olds[d].get(g["id"]) or {})
        # a feed that did not answer this run: keep what we had for that league
        for gid, og in olds[d].items():
            if gid not in cur and gid not in fresh:
                cur[gid] = og
        merged[d] = cur
    n_pred = refresh_preds([g for d in days for g in merged[d].values()], now_utc, retrieved, pred_fetch)
    n_box = save_boxes([g for d in days for g in merged[d].values()], now_utc, box_fetch)

    written = 0
    for d in days:
        games = dict(sorted(merged[d].items(), key=lambda kv: (kv[1].get("date") or "", kv[1]["lg"], kv[0])))
        over = d < today and ok == asked and FS.day_over(games.values(), d)
        if d < today - dt.timedelta(days=14):
            over = True
        if write_day(d, games, over, retrieved):
            written += 1

    st_path = os.path.join(DIR, "status.json")
    old_st = FS.read_json(st_path, {})
    last = old_st.get("checkedAt")
    stale = not last or (now_utc - dt.datetime.fromisoformat(last)).total_seconds() > CHECK_EVERY_MIN * 60
    if written or stale:
        FS.write_json(st_path, {"source": "ESPN public scoreboard", "tz": "America/New_York", "checkedAt": retrieved,
                                "updatedAt": retrieved if written else old_st.get("updatedAt", retrieved),
                                "leagues": [lg for lg, _, _ in FEEDS]})
    n_games = sum(len(v) for v in merged.values())
    print(f"Basketball: {n_games} games over {len(days)} dates, {written} date file(s) written, "
          f"{n_pred} win chance(s) checked, {n_box} box score(s) saved, {ok}/{asked} feeds answered")
    return written


def main():
    if "--busy" in sys.argv:
        sys.exit(busy())
    run("--full" in sys.argv)


if __name__ == "__main__":
    main()
