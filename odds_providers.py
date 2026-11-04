#!/usr/bin/env python3
"""Sportsbook odds adapters for the Gridiron Ledger robot. Standard library only.

Every adapter turns a provider's response into the same normalized shape, so a provider can be swapped
without touching the grader or the page:

  book quotes = {"book": "DraftKings", "src": "espn", "ts": <retrieved ISO>, "pts": <provider ISO or None>,
                 "ml":     {"A": -150, "H": 130},                      # American prices
                 "spread": {"A": [3.5, -110], "H": [-3.5, -110]},      # [line for that side, price]
                 "total":  {"O": [47.5, -110], "U": [47.5, -110]},
                 "dq": ["..."]}                                         # data-quality warnings, if any

flatten() expands that into one record per selection with the full field set
(event, book, market, selection, line, price, provider_ts, retrieved_ts, status, source).

Rules: a price is only ever copied from the provider. Missing prices stay None; nothing is estimated here.
"""
import datetime as dt
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

UTC = dt.timezone.utc


# ---------- small parsers ----------
def am(x):
    """American price from a provider value: -110, '-110', '+130', 'EVEN'. None if absent or not a price."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        v = int(round(x))
    else:
        s = str(x).strip().upper().replace("−", "-")
        if s in ("EVEN", "EV", "PK"):
            return 100
        m = re.fullmatch(r"([+-]?\d{3,5})", s)
        if not m:
            return None
        v = int(m.group(1))
    if -100 < v < 100:          # not a valid American price
        return None
    return v


def num(x):
    """Line from '−3.5', '+3.5', 'o47.5', 'u47.5', 'PK', 3.5."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().lower().replace("−", "-")
    if s in ("pk", "pick", "even", "ev"):
        return 0.0
    s = re.sub(r"^[ou]\s*", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def implied(price):
    """Break-even probability of an American price (includes the book's margin)."""
    if price is None:
        return None
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def check(q):
    """Data-quality warnings for one book's quotes (does not change any value)."""
    dq = []
    sp = q.get("spread") or {}
    if "A" in sp and "H" in sp and sp["A"][0] is not None and sp["H"][0] is not None and abs(sp["A"][0] + sp["H"][0]) > 0.01:
        dq.append("spread lines are not mirror images")
    tot = q.get("total") or {}
    if "O" in tot and "U" in tot and tot["O"][0] is not None and tot["U"][0] is not None and abs(tot["O"][0] - tot["U"][0]) > 0.01:
        dq.append("over and under lines differ")
    for mk, pair in (("ml", ("A", "H")), ("spread", ("A", "H")), ("total", ("O", "U"))):
        d = q.get(mk) or {}
        prices = [d[s] if mk == "ml" else d[s][1] for s in pair if s in d]
        if len(prices) == 2 and None not in prices:
            over = implied(prices[0]) + implied(prices[1])
            if over < 0.995:
                dq.append(f"{mk} prices imply under 100% ({over:.3f})")
            elif over > 1.15:
                dq.append(f"{mk} margin over 15% ({over:.3f})")
    return dq


def flatten(event_id, q, status="open"):
    """One record per selection, with every field the spec asks for."""
    out = []
    for mk in ("ml", "spread", "total"):
        for sel, v in (q.get(mk) or {}).items():
            line, price = (None, v) if mk == "ml" else (v[0], v[1])
            out.append({"event": event_id, "book": q.get("book"), "market": mk, "selection": sel, "line": line, "price": price,
                        "provider_ts": q.get("pts"), "retrieved_ts": q.get("ts"), "status": status, "source": q.get("src")})
    return out


def empty(q):
    return not any(q.get(k) for k in ("ml", "spread", "total"))


# ---------- ESPN scoreboard (DraftKings via ESPN today) ----------
def _espn_side(block, key, sub):
    b = (block or {}).get(key) or {}
    return b.get(sub) or {}


def espn_book(o, retrieved, which="close"):
    """One ESPN odds object -> normalized quotes. which='close' is ESPN's current number, 'open' its opener.
    Handles both the newer moneyline/pointSpread/total blocks and the older homeTeamOdds/awayTeamOdds fields."""
    q = {"book": (o.get("provider") or {}).get("name"), "src": "espn", "ts": retrieved, "pts": None}
    ml, sp, tot = {}, {}, {}
    for side, key in (("A", "away"), ("H", "home")):
        p = am(_espn_side(o.get("moneyline"), key, which).get("odds"))
        if p is not None:
            ml[side] = p
        s = _espn_side(o.get("pointSpread"), key, which)
        ln, p = num(s.get("line")), am(s.get("odds"))
        if ln is not None:
            sp[side] = [ln, p]
    for side, key in (("O", "over"), ("U", "under")):
        s = _espn_side(o.get("total"), key, which)
        ln, p = num(s.get("line")), am(s.get("odds"))
        if ln is not None:
            tot[side] = [ln, p]
    if which == "close":          # older field layout only carries the current number
        hto, ato = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
        if not ml:
            for side, t in (("A", ato), ("H", hto)):
                p = am(t.get("moneyLine"))
                if p is not None:
                    ml[side] = p
        if not sp and num(o.get("spread")) is not None:
            hl = num(o.get("spread"))      # ESPN's spread is the home team's line
            sp = {"H": [hl, am(hto.get("spreadOdds"))], "A": [-hl if hl else 0.0, am(ato.get("spreadOdds"))]}
        if not tot and num(o.get("overUnder")) is not None:
            t = num(o.get("overUnder"))
            tot = {"O": [t, am(o.get("overOdds"))], "U": [t, am(o.get("underOdds"))]}
    if ml:
        q["ml"] = ml
    if sp:
        q["spread"] = sp
    if tot:
        q["total"] = tot
    dq = check(q)
    if dq:
        q["dq"] = dq
    return q


def espn_quotes(comp, retrieved):
    """All books in an ESPN competition: ({book: quotes} current, {book: quotes} opening)."""
    cur, opn = {}, {}
    for o in comp.get("odds") or []:
        c = espn_book(o, retrieved, "close")
        if not empty(c) and c.get("book"):
            cur[c["book"]] = c
        op = espn_book(o, retrieved, "open")
        if not empty(op) and op.get("book"):
            op["opener"] = True
            opn[op["book"]] = op
    return cur, opn


# ---------- The Odds API (only when ODDS_API_KEY is set) ----------
ODDS_API = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
ODDS_SPORTS = {"NFL": "americanfootball_nfl", "CFB": "americanfootball_ncaaf"}
ODDS_BOOKS = "draftkings,fanduel,betmgm"


def _n(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.replace("&", " and ")).split())


def oddsapi_fetch(sport, key, timeout=30):
    """Returns (events, remaining_requests). Raises on HTTP errors."""
    qs = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american",
                                 "dateFormat": "iso", "bookmakers": ODDS_BOOKS})
    req = urllib.request.Request(ODDS_API.format(sport=sport) + "?" + qs, headers={"User-Agent": "gridiron-scores/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r), r.headers.get("x-requests-remaining")


def oddsapi_books(ev, retrieved):
    """One The Odds API event -> {book title: normalized quotes} (home/away from the event's own fields)."""
    home, away = ev.get("home_team"), ev.get("away_team")
    side_of = {home: "H", away: "A"}
    out = {}
    for bk in ev.get("bookmakers") or []:
        q = {"book": bk.get("title"), "src": "oddsapi", "ts": retrieved, "pts": bk.get("last_update")}
        for m in bk.get("markets") or []:
            k = m.get("key")
            for oc in m.get("outcomes") or []:
                price = am(oc.get("price"))
                if k == "h2h" and oc.get("name") in side_of:
                    q.setdefault("ml", {})[side_of[oc["name"]]] = price
                elif k == "spreads" and oc.get("name") in side_of:
                    q.setdefault("spread", {})[side_of[oc["name"]]] = [num(oc.get("point")), price]
                elif k == "totals" and oc.get("name") in ("Over", "Under"):
                    q.setdefault("total", {})["O" if oc["name"] == "Over" else "U"] = [num(oc.get("point")), price]
        if not empty(q):
            dq = check(q)
            if dq:
                q["dq"] = dq
            out[q["book"]] = q
    return out


def match_event(ev, games):
    """Find the ESPN game for a The Odds API event: same two teams (full names) and kickoff within 6 hours."""
    try:
        t = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None, False
    h, a = _n(ev.get("home_team")), _n(ev.get("away_team"))
    for g in games:
        try:
            gt = dt.datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if abs((gt - t).total_seconds()) > 6 * 3600:
            continue
        gh, ga = _n(g["home"].get("disp")), _n(g["away"].get("disp"))
        if (gh, ga) == (h, a):
            return g, False
        if (gh, ga) == (a, h):
            return g, True
    return None, False


def swap_sides(q):
    """Quotes written with home/away reversed (neutral-site listings) -> our orientation."""
    out = dict(q)
    for mk in ("ml", "spread"):
        if mk in q:
            out[mk] = {{"A": "H", "H": "A"}[s]: v for s, v in q[mk].items()}
    return out


def due(state_path, every_min, now):
    try:
        with open(state_path) as f:
            last = json.load(f).get("last_pull")
        return not last or (now - dt.datetime.fromisoformat(last)).total_seconds() >= every_min * 60
    except (OSError, ValueError):
        return True


def oddsapi_merge(games, key, state_path, every_min=240, now=None, fetch=oddsapi_fetch):
    """Pull The Odds API at most every `every_min` minutes and attach quotes to matching games (in place).
    Returns a short status string for the log."""
    now = now or dt.datetime.now(UTC)
    if not key:
        return "The Odds API: no key set (add the ODDS_API_KEY secret to enable DraftKings/FanDuel/BetMGM prices)"
    if not due(state_path, every_min, now):
        return "The Odds API: not due yet"
    retrieved = now.isoformat(timespec="seconds")
    matched, remaining, errors = 0, None, []
    for lg_key, sport in ODDS_SPORTS.items():
        pool = [g for g in games if (g["lg"] == "NFL") == (lg_key == "NFL") and g.get("state") == "pre"]
        if not pool:
            continue
        try:
            events, remaining = fetch(sport, key)
        except Exception as e:  # keep the robot running; record the failure
            errors.append(f"{sport}: {e}")
            continue
        for ev in events:
            g, swapped = match_event(ev, pool)
            if not g:
                continue
            books = oddsapi_books(ev, retrieved)
            if swapped:
                books = {b: swap_sides(q) for b, q in books.items()}
            g.setdefault("quotes", {}).update(books)
            matched += 1
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump({"last_pull": retrieved, "remaining": remaining, "matched": matched, "errors": errors}, f)
    return f"The Odds API: {matched} games matched, {remaining} requests left" + (f", errors: {errors}" if errors else "")
