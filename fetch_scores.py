#!/usr/bin/env python3
"""Fetch NFL, FBS and FCS scores from ESPN's public scoreboard and save them as small JSON files.

Runs on GitHub Actions (see .github/workflows/scores.yml). Standard library only.

Writes
  scores/latest.json          every game from 4 days ago to 7 days ahead (US Eastern dates), with sportsbook quotes
  scores/final/<date>.json    every finished game, kept for the whole season (one file per US Eastern date)
  box/<eventId>.json          player box score for each finished NFL and FBS game, kept for the season
  odds/espn_sample.json       one raw ESPN odds object, so the odds parser can be checked against the real format
  odds/oddsapi_state.json     last The Odds API pull (only when the ODDS_API_KEY secret is set)

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

import odds_providers as OP

ET = ZoneInfo("America/New_York")
BASE = "https://site.api.espn.com/apis/site/v2/sports/football"
FEEDS = [  # league label, ESPN sport path, extra query
    ("NFL", "nfl", ""),
    ("FBS", "college-football", "&groups=80&limit=400"),
    ("FCS", "college-football", "&groups=81&limit=400"),
]
BOX_LEAGUES = {"NFL", "FBS"}  # books post props for these, so their box scores are worth saving
KEEP_DAYS_BACK, KEEP_DAYS_AHEAD = 4, 7  # ahead: lets the Friday board build read the coming week's schedule
ARCHIVE_START = dt.date(2026, 9, 22)     # first ledger slate; finals from here on are archived for grading
BACKFILL_MAX_DATES = 60                  # archive dates fetched per run when catching up
BOX_MAX_PER_RUN = 600
CHECK_EVERY_MIN = 30                     # rewrite latest.json at least this often so its checkedAt stays fresh
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


def parse_event(ev, lg, retrieved=None):
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
        "_q": OP.espn_quotes(comp, retrieved) if retrieved else ({}, {}),
    }


def merge_quotes(g, old_g):
    """Attach sportsbook quotes to a freshly parsed game.
    quotes = current numbers by book (pre-game only; frozen at kickoff); each keeps `ts` = when that exact number was
    first seen, so an unchanged price keeps its original time. qopen = first number per book (ESPN's opener when it
    gives one). qclose = the last pre-kickoff numbers. Quotes from other adapters (The Odds API) are kept between pulls."""
    cur, opn = g.pop("_q", ({}, {}))
    for k in ("quotes", "qopen", "qclose"):       # older runs stored ESPN's "Draft Kings" spelling
        if old_g.get(k):
            old_g = dict(old_g, **{k: {OP.canon(b): dict(v, book=OP.canon(b)) for b, v in old_g[k].items()}})
    old_q = old_g.get("quotes") or {}
    if g["state"] == "pre":
        q = {b: v for b, v in old_q.items() if v.get("src") != "espn"}
        for b, v in cur.items():
            ov = old_q.get(b)
            if ov and {k: ov.get(k) for k in ("ml", "spread", "total")} == {k: v.get(k) for k in ("ml", "spread", "total")}:
                v = dict(v, ts=ov.get("ts"))
            q[b] = v
        if q:
            g["quotes"] = q
        qopen = dict(old_g.get("qopen") or {})
        for b, v in q.items():
            if b not in qopen:
                qopen[b] = opn.get(b) or dict(v, first_seen=True)
        if qopen:
            g["qopen"] = qopen
        if q:
            g["qclose"] = q
    else:
        for k in ("quotes", "qopen", "qclose"):
            if old_g.get(k):
                g[k] = old_g[k]
        if not old_g and cur:   # first seen after kickoff (back-fill): keep what ESPN still shows, marked as such
            g["qclose"] = {b: dict(v, post_capture=True) for b, v in cur.items()}
    return g


def sync_open_close(g):
    """After another adapter added books to a pre-game: those books join qclose and, if new, qopen."""
    if g.get("state") != "pre" or not g.get("quotes"):
        return
    g["qclose"] = g["quotes"]
    qopen = dict(g.get("qopen") or {})
    for b, v in g["quotes"].items():
        qopen.setdefault(b, dict(v, first_seen=True))
    g["qopen"] = qopen


# ---------- box scores ----------
# ESPN stat keys per category -> our field names. Labels are the fallback when keys are missing.
CATS = {
    "passing": ({"passingYards": "passYds", "passingTouchdowns": "passTD", "interceptions": "int"},
                {"YDS": "passYds", "TD": "passTD", "INT": "int"}),
    "rushing": ({"rushingAttempts": "rushAtt", "rushingYards": "rushYds", "rushingTouchdowns": "rushTD", "longRushing": "longRush"},
                {"CAR": "rushAtt", "YDS": "rushYds", "TD": "rushTD", "LONG": "longRush"}),
    "receiving": ({"receptions": "rec", "receivingYards": "recYds", "receivingTouchdowns": "recTD", "longReception": "longRec", "receivingTargets": "tgt"},
                  {"REC": "rec", "YDS": "recYds", "TD": "recTD", "LONG": "longRec", "TGTS": "tgt"}),
    "kicking": ({"totalKickingPoints": "kickPts"}, {"PTS": "kickPts"}),
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
                    k = keys[i] if i < len(keys) else (labels[i] if i < len(labels) else None)
                    if k in ("completions/passingAttempts", "C/ATT") and "/" in str(v):
                        c, at = (to_int(x) for x in str(v).split("/", 1))
                        if c is not None and at is not None:
                            p["comp"], p["passAtt"] = c, at
                        continue
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


def et_date(g):
    try:
        return dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(ET).date()
    except (KeyError, ValueError, AttributeError):
        return None


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))


def fetch_dates(dates, retrieved, sample):
    """Scoreboards for the given YYYYMMDD dates -> ({id: game}, number of feeds that answered)."""
    out, fetched = {}, 0
    for lg, sport, extra in FEEDS:
        for d in dates:
            data = get_json(f"{BASE}/{sport}/scoreboard?dates={d}{extra}")
            if not data:
                continue
            fetched += 1
            for ev in data.get("events") or []:
                if sample is not None and not sample:
                    for o in ((ev.get("competitions") or [{}])[0].get("odds") or [])[:1]:
                        sample.update({"captured": retrieved, "league": lg, "event": ev.get("id"), "odds": o})
                g = parse_event(ev, lg, retrieved)
                if g:
                    out[g["id"]] = g
    return out, fetched


def archive(games_by_date, final_dir, complete=()):
    """Merge finished games into scores/final/<date>.json. Dates in `complete` had their whole scoreboard fetched with
    every game over, so the file is marked complete and never re-fetched. Returns the number of files written."""
    written = 0
    for d in set(games_by_date) | set(complete):
        path = os.path.join(final_dir, f"{d.isoformat()}.json")
        cur = read_json(path, {"date": d.isoformat(), "games": {}})
        new = dict(cur.get("games") or {})
        for g in games_by_date.get(d, []):
            new[g["id"]] = g
        done = bool(cur.get("complete")) or d in complete
        if new != cur.get("games") or done != bool(cur.get("complete")) or not os.path.exists(path):
            write_json(path, {"date": d.isoformat(), "complete": done, "games": new})
            written += 1
    return written


def day_over(games, d):
    """True when every game on US Eastern date d is final, postponed or cancelled (nothing more will change)."""
    on = [g for g in games if et_date(g) == d]
    return all(g.get("completed") or g.get("status") in ("STATUS_POSTPONED", "STATUS_CANCELED") for g in on)


def main():
    if "--busy" in sys.argv:
        sys.exit(busy())
    full = "--full" in sys.argv
    now_utc = dt.datetime.now(dt.timezone.utc)
    retrieved = now_utc.isoformat(timespec="seconds")
    now = now_utc.astimezone(ET)
    today = now.date()
    days = range(-KEEP_DAYS_BACK, KEEP_DAYS_AHEAD + 1) if full else range(-1, 1)
    dates = [(today + dt.timedelta(days=d)).strftime("%Y%m%d") for d in days]

    path = os.path.join(ROOT, "scores", "latest.json")
    old = read_json(path, {"games": []})
    games = {g["id"]: g for g in old.get("games", [])}

    sample = {}
    fresh, fetched = fetch_dates(dates, retrieved, sample)
    for gid, g in fresh.items():
        old_g = games.get(gid) or {}
        # closing line = the last line seen before kickoff
        g["close"] = g["odds"] if (g["state"] == "pre" and g.get("odds")) else old_g.get("close")
        games[gid] = merge_quotes(g, old_g)

    if fetched == 0:
        print("No feed answered; leaving files unchanged.")
        return

    # keep only the rolling window
    lo = (today - dt.timedelta(days=KEEP_DAYS_BACK)).isoformat()
    hi = (today + dt.timedelta(days=KEEP_DAYS_AHEAD + 1)).isoformat()
    kept = sorted((g for g in games.values() if lo <= (g.get("date") or "")[:10] <= hi),
                  key=lambda g: (g.get("date") or "", g["lg"], g["id"]))

    # more books when a The Odds API key is configured (rate-limited; see odds_providers.oddsapi_merge)
    print(OP.oddsapi_merge(kept, os.environ.get("ODDS_API_KEY", "").strip(), os.path.join(ROOT, "odds", "oddsapi_state.json"),
                           every_min=int(os.environ.get("ODDS_API_EVERY_MIN", "240") or 240), now=now_utc))
    for g in kept:
        sync_open_close(g)

    new = {"source": "ESPN public scoreboard", "tz": "America/New_York", "games": kept}
    old_cmp = {k: v for k, v in old.items() if k not in ("updatedAt", "checkedAt")}
    last_check = old.get("checkedAt") or old.get("updatedAt")
    stale = not last_check or (now_utc - dt.datetime.fromisoformat(last_check)).total_seconds() > CHECK_EVERY_MIN * 60
    if old_cmp != new or stale:
        new["updatedAt"] = retrieved if old_cmp != new else old.get("updatedAt", retrieved)
        new["checkedAt"] = retrieved
        write_json(path, new)
        print(f"scores/latest.json written: {len(kept)} games" + ("" if old_cmp != new else " (freshness check only)"))
    else:
        print("Scores unchanged")
    if sample:
        sp = os.path.join(ROOT, "odds", "espn_sample.json")
        prev = read_json(sp, {})
        if {k: v for k, v in prev.items() if k != "captured"} != {k: v for k, v in sample.items() if k != "captured"}:
            write_json(sp, sample)

    # season archive of finished games (grading reads this, so games older than the window still get graded)
    final_dir = os.path.join(ROOT, "scores", "final")
    by_date = {}
    for g in kept:
        if g.get("completed"):
            by_date.setdefault(et_date(g), []).append(g)
    by_date.pop(None, None)
    fetched_days = {dt.datetime.strptime(x, "%Y%m%d").date() for x in dates}
    whole = {d for d in fetched_days if d < today and day_over(fresh.values(), d)}
    n_arch = archive(by_date, final_dir, whole)
    # catch up dates that were never fetched in full (first run after this change back-fills the season; a file
    # written only from the edge of the live window is not complete, so its date is fetched again)
    missing = []
    d = ARCHIVE_START
    while d < today - dt.timedelta(days=1):
        if not read_json(os.path.join(final_dir, f"{d.isoformat()}.json"), {}).get("complete"):
            missing.append(d)
        d += dt.timedelta(days=1)
    for d in missing[:BACKFILL_MAX_DATES]:
        got, ok = fetch_dates([d.strftime("%Y%m%d")], retrieved, None)
        if ok < len(FEEDS):
            continue
        done = [merge_quotes(g, games.get(g["id"]) or {}) for g in got.values() if g.get("completed") and et_date(g) == d]
        over = day_over(got.values(), d) or d < today - dt.timedelta(days=14)
        n_arch += archive({d: done}, final_dir, {d} if over else ())
    if n_arch:
        print(f"Archive: {n_arch} date file(s) written" + (f", {len(missing)} incomplete before this run" if missing else ""))

    # box scores for finished NFL and FBS games we have not saved yet (window first, then the archive)
    box_dir = os.path.join(ROOT, "box")
    os.makedirs(box_dir, exist_ok=True)
    archived = {}
    for name in sorted(os.listdir(final_dir)) if os.path.isdir(final_dir) else []:
        for g in (read_json(os.path.join(final_dir, name), {}).get("games") or {}).values():
            archived[g["id"]] = g
    want = [g for g in kept if g.get("completed")] + [g for gid, g in sorted(archived.items(), reverse=True)]
    saved, tried, seen = 0, 0, set()
    for g in want:
        if g["id"] in seen or g["lg"] not in BOX_LEAGUES or not g.get("completed"):
            continue
        seen.add(g["id"])
        bp = os.path.join(box_dir, f"{g['id']}.json")
        if os.path.exists(bp) or tried >= BOX_MAX_PER_RUN:
            continue
        tried += 1
        sport = "nfl" if g["lg"] == "NFL" else "college-football"
        summ = get_json(f"{BASE}/{sport}/summary?event={g['id']}")
        players = parse_box(summ)
        if not players:
            continue
        write_json(bp, {"id": g["id"], "lg": g["lg"], "players": players})
        saved += 1
        time.sleep(0.25)
    if saved:
        print(f"Saved {saved} box scores")

    # keep box scores for every game in the window or the season archive
    keep_ids = {g["id"] for g in kept} | set(archived)
    for name in os.listdir(box_dir):
        if name.endswith(".json") and name[:-5] not in keep_ids:
            os.remove(os.path.join(box_dir, name))


if __name__ == "__main__":
    main()
