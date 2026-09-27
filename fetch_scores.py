#!/usr/bin/env python3
"""Fetch NFL, FBS and FCS scores from ESPN's public scoreboard and save them as small JSON files.

Runs on GitHub Actions (see .github/workflows/scores.yml). Standard library only.

Writes
  scores/latest.json   every game from 4 days ago to 7 days ahead (US Eastern dates)
  box/<eventId>.json   player box score for each finished NFL and FBS game

Usage
  python3 fetch_scores.py          quick run: refreshes yesterday and today (ET)
  python3 fetch_scores.py --full   full run: refreshes the whole window
  python3 fetch_scores.py --busy   exit 0 if a game is on or starts within 45 minutes
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASE = "https://site.api.espn.com/apis/site/v2/sports/football"
FEEDS = [  # league label, ESPN sport path, extra query
    ("NFL", "nfl", ""),
    ("FBS", "college-football", "&groups=80&limit=400"),
    ("FCS", "college-football", "&groups=81&limit=400"),
]
BOX_LEAGUES = {"NFL", "FBS"}  # books post props for these, so their box scores are worth saving
KEEP_DAYS_BACK, KEEP_DAYS_AHEAD = 4, 7  # ahead: lets the Tuesday board build read the coming week's schedule
HEADERS = {"User-Agent": "gridiron-scores/1.0 (personal scoreboard)", "Accept": "application/json"}
ROOT = os.path.dirname(os.path.abspath(__file__))


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:  # network blips are normal; retry a couple of times
            if i == tries - 1:
                print(f"WARN {url}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))


def to_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def team_of(c):
    t = c.get("team") or {}
    rank = (c.get("curatedRank") or {}).get("current")
    return {
        "id": t.get("id"),
        "loc": t.get("location"),
        "name": t.get("name"),
        "disp": t.get("displayName"),
        "short": t.get("shortDisplayName"),
        "abbr": t.get("abbreviation"),
        "score": to_int(c.get("score")),
        "lines": [to_int(l.get("value")) for l in (c.get("linescores") or [])],
        "rank": rank if isinstance(rank, int) and 0 < rank < 99 else None,
        "rec": next((r.get("summary") for r in (c.get("records") or []) if r.get("type") in (None, "total")), None),
    }


def to_num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def odds_of(comp, home_abbr, away_abbr):
    """The book line ESPN shows before kickoff, as the home team's expected margin (+ = home favored)."""
    for o in comp.get("odds") or []:
        tot = to_num(o.get("overUnder"))
        hm = None
        det = (o.get("details") or "").strip()          # e.g. "KC -3.5" or "EVEN"
        if det.upper() in ("EVEN", "PK", "PICK"):
            hm = 0.0
        elif det:
            parts = det.rsplit(" ", 1)
            if len(parts) == 2 and to_num(parts[1]) is not None:
                ab, num = parts[0].strip(), to_num(parts[1])
                if ab == home_abbr:
                    hm = -num
                elif ab == away_abbr:
                    hm = num
        if hm is None and to_num(o.get("spread")) is not None:
            hm = -to_num(o.get("spread"))                   # ESPN's spread is the home team's line
        if hm is not None or tot is not None:
            return {"hm": hm, "tot": tot, "book": (o.get("provider") or {}).get("name"), "details": det or None}
    return None


def parse_event(ev, lg):
    comp = (ev.get("competitions") or [{}])[0]
    status = comp.get("status") or ev.get("status") or {}
    ty = status.get("type") or {}
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), None)
    away = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    return {
        "id": str(ev.get("id")),
        "lg": lg,
        "date": ev.get("date") or comp.get("date"),
        "state": ty.get("state"),          # pre | in | post
        "status": ty.get("name"),          # STATUS_FINAL, STATUS_IN_PROGRESS, STATUS_HALFTIME, STATUS_POSTPONED ...
        "completed": bool(ty.get("completed")),
        "detail": ty.get("shortDetail") or ty.get("detail"),
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "neutral": bool(comp.get("neutralSite")),
        "away": team_of(away),
        "home": team_of(home),
        "odds": odds_of(comp, (home.get("team") or {}).get("abbreviation"), (away.get("team") or {}).get("abbreviation")),
    }


# ---------- box scores ----------
# ESPN stat keys per category -> our field names. Labels are the fallback when keys are missing.
CATS = {
    "passing": ({"passingYards": "passYds", "passingTouchdowns": "passTD", "interceptions": "int"},
                {"YDS": "passYds", "TD": "passTD", "INT": "int"}),
    "rushing": ({"rushingAttempts": "rushAtt", "rushingYards": "rushYds", "rushingTouchdowns": "rushTD", "longRushing": "longRush"},
                {"CAR": "rushAtt", "YDS": "rushYds", "TD": "rushTD", "LONG": "longRush"}),
    "receiving": ({"receptions": "rec", "receivingYards": "recYds", "receivingTouchdowns": "recTD", "longReception": "longRec", "receivingTargets": "tgt"},
                  {"REC": "rec", "YDS": "recYds", "TD": "recTD", "LONG": "longRec", "TGTS": "tgt"}),
    "fumbles": ({"fumblesLost": "fumLost"}, {"LOST": "fumLost"}),
    "kickReturns": ({"kickReturnTouchdowns": "retTD"}, {"TD": "retTD"}),
    "puntReturns": ({"puntReturnTouchdowns": "retTD"}, {"TD": "retTD"}),
}


def fantasy(p):
    """PrizePicks NFL fantasy score (return TDs included; 2-point conversions are not in box scores)."""
    g = lambda k: p.get(k) or 0
    return round(g("passYds") * 0.04 + g("passTD") * 4 - g("int") + g("rushYds") * 0.1 + g("rushTD") * 6
                 + g("rec") + g("recYds") * 0.1 + g("recTD") * 6 + g("retTD") * 6 - g("fumLost"), 2)


def parse_box(summary):
    players = {}
    for side in ((summary or {}).get("boxscore") or {}).get("players") or []:
        abbr = (side.get("team") or {}).get("abbreviation")
        for cat in side.get("statistics") or []:
            spec = CATS.get(cat.get("name"))
            if not spec:
                continue
            by_key, by_label = spec
            keys, labels = cat.get("keys") or [], cat.get("labels") or []
            for a in cat.get("athletes") or []:
                ath = a.get("athlete") or {}
                pid = str(ath.get("id") or ath.get("displayName"))
                p = players.setdefault(pid, {"n": ath.get("displayName"), "tm": abbr})
                for i, v in enumerate(a.get("stats") or []):
                    f = by_key.get(keys[i]) if i < len(keys) else None
                    if f is None and i < len(labels):
                        f = by_label.get(labels[i])
                    n = to_int(v)
                    if f and n is not None:
                        p[f] = p.get(f, 0) + n if f == "retTD" else n
    out = []
    for p in players.values():
        p["fp"] = fantasy(p)
        out.append(p)
    return out


def busy():
    """Exit code 0 when a game is in progress or kicks off within 45 minutes (keep the robot running)."""
    try:
        with open(os.path.join(ROOT, "scores", "latest.json")) as f:
            games = json.load(f).get("games", [])
    except (OSError, ValueError):
        return 1
    now = dt.datetime.now(dt.timezone.utc)
    for g in games:
        if g.get("state") == "in":
            return 0
        if g.get("state") == "pre" and g.get("status") not in ("STATUS_POSTPONED", "STATUS_CANCELED"):
            try:
                ko = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if now - dt.timedelta(minutes=30) <= ko <= now + dt.timedelta(minutes=45):
                return 0
    return 1


def main():
    if "--busy" in sys.argv:
        sys.exit(busy())
    full = "--full" in sys.argv
    now = dt.datetime.now(ET)
    today = now.date()
    days = range(-KEEP_DAYS_BACK, KEEP_DAYS_AHEAD + 1) if full else range(-1, 1)
    dates = [(today + dt.timedelta(days=d)).strftime("%Y%m%d") for d in days]

    path = os.path.join(ROOT, "scores", "latest.json")
    try:
        with open(path) as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {"games": []}
    games = {g["id"]: g for g in old.get("games", [])}

    fetched = 0
    for lg, sport, extra in FEEDS:
        for d in dates:
            data = get_json(f"{BASE}/{sport}/scoreboard?dates={d}{extra}")
            if not data:
                continue
            fetched += 1
            for ev in data.get("events") or []:
                g = parse_event(ev, lg)
                if g:
                    old_g = games.get(g["id"]) or {}
                    # closing line = the last line seen before kickoff
                    g["close"] = g["odds"] if (g["state"] == "pre" and g.get("odds")) else old_g.get("close")
                    games[g["id"]] = g

    # keep only the rolling window
    lo = (today - dt.timedelta(days=KEEP_DAYS_BACK)).isoformat()
    hi = (today + dt.timedelta(days=KEEP_DAYS_AHEAD + 1)).isoformat()
    kept = sorted((g for g in games.values() if lo <= (g.get("date") or "")[:10] <= hi),
                  key=lambda g: (g.get("date") or "", g["lg"], g["id"]))

    if fetched == 0:
        print("No feed answered; leaving files unchanged.")
        return

    new = {"source": "ESPN public scoreboard", "tz": "America/New_York", "games": kept}
    old_cmp = {k: v for k, v in old.items() if k != "updatedAt"}
    if old_cmp != new:
        new["updatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(new, f, separators=(",", ":"))
        print(f"scores/latest.json updated: {len(kept)} games")
    else:
        print("Scores unchanged")

    # box scores for finished games we have not saved yet
    box_dir = os.path.join(ROOT, "box")
    os.makedirs(box_dir, exist_ok=True)
    saved = 0
    for g in kept:
        if g["lg"] not in BOX_LEAGUES or not g.get("completed"):
            continue
        bp = os.path.join(box_dir, f"{g['id']}.json")
        if os.path.exists(bp):
            continue
        sport = "nfl" if g["lg"] == "NFL" else "college-football"
        summ = get_json(f"{BASE}/{sport}/summary?event={g['id']}")
        players = parse_box(summ)
        if not players:
            continue
        with open(bp, "w") as f:
            json.dump({"id": g["id"], "lg": g["lg"], "players": players}, f, separators=(",", ":"))
        saved += 1
        time.sleep(0.3)
    if saved:
        print(f"Saved {saved} box scores")

    # drop box scores older than the window
    keep_ids = {g["id"] for g in kept}
    for name in os.listdir(box_dir):
        if name.endswith(".json") and name[:-5] not in keep_ids:
            os.remove(os.path.join(box_dir, name))


if __name__ == "__main__":
    main()
