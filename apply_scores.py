#!/usr/bin/env python3
"""Turn the ESPN score files in this repo into Gridiron Ledger database updates.

Run by Claude's score-update task, not by GitHub. Standard library only.

  python3 apply_scores.py --docs DIR --out OUTDIR [--scores URL|PATH] [--box URL|DIR]

DIR holds league docs saved by ArtifactData (out_dir): DIR/slates/<slate>/leagues/<NFL|FBS|FCS>.json,
and optionally DIR/results/<slate>.json. The script:
  - matches each started ledger game to its ESPN game (saves the ESPN id as `eid` for next time),
  - writes st / sc / clk, and on final also per (quarter scores) and pv (prop results),
  - grades tracked games (su, lean, star, props) exactly as the runbook says, from a pick snapshot (`lock`) taken the
    first time the game is seen after kickoff, so later edits cannot change a graded pick,
  - reads the season archive (scores/final/<date>.json) for games older than the live feed's 4-day window,
  - marks a prop unresolved (U) when the player or market is missing from the final box score a day after the game,
  - writes pregame/<slate>: sportsbook quotes (current, opening, closing), an append-only history of the picks, the
    chance of landing on each nearby number (push tables), the Ledger Model's numbers and NFL prop projections, plus
    the model gates (from model/results in this repo), so the page can price pushes and check each phase's gate,
  - records at the lock what the closing market and the model said, so the page can measure them after the game,
  - writes one update file per changed doc plus meta/live, and prints a JSON manifest:
      [{"op": "update"|"set", "collection": ..., "doc_id": ..., "file_path": ...}, ...]
Never changes line, lean, star, props or tracked.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from zoneinfo import ZoneInfo

RAW = "https://raw.githubusercontent.com/bxn109-stack/gridiron-scores/main"
UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")
COLLEGE = {"FBS", "FCS"}
VOID_AFTER = dt.timedelta(hours=30)   # a prop is marked unresolved only once the box score has had a day to settle


# ---------- loading ----------
def load(src):
    if re.match(r"https?://", src):
        url = src + ("&" if "?" in src else "?") + f"t={int(time.time())}"  # skip the CDN's 5-minute cache
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gridiron-ledger"}), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if "404" in str(e):
                return None
            raise
    if os.path.exists(src):
        with open(src) as f:
            return json.load(f)
    return None


def when(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


# ---------- team matching ----------
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("'", "").replace("\u2019", "").replace("&", " and ").replace("st.", "state")
    s = re.sub(r"\((naia|d2|d3|dii|diii|d-ii|d-iii|fcs|fbs)\)", " ", s)  # division tags, not part of the name
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = " ".join(s.split())
    return ALIAS.get(s, s)


# Names that differ between the ledger's sources and ESPN (both sides map to one spelling).
ALIAS = {
    "ualbany": "albany", "uconn": "connecticut", "umass": "massachusetts", "app state": "appalachian state",
    "appalachian st": "appalachian state", "pitt": "pittsburgh", "ole miss": "mississippi", "nc state": "north carolina state",
    "southern miss": "southern mississippi", "ul monroe": "louisiana monroe", "ulm": "louisiana monroe",
    "sam houston": "sam houston state", "etsu": "east tennessee state", "liu": "long island university",
    "fiu": "florida international", "fau": "florida atlantic", "usf": "south florida", "ucf": "central florida",
    "smu": "southern methodist", "tcu": "texas christian", "byu": "brigham young", "unlv": "nevada las vegas",
    "utep": "texas el paso", "utsa": "texas san antonio", "uab": "alabama birmingham", "lsu": "louisiana state",
    "nc a and t": "north carolina a and t", "mcneese": "mcneese state", "nicholls": "nicholls state",
    "grambling": "grambling state", "ut martin": "tennessee martin", "ul lafayette": "louisiana",
    "miami fl": "miami", "hawaii": "hawaii", "san jose state": "san jose state",
}


def team_sim(name, t):
    n = norm(name)
    if not n:
        return 0.0
    best = 0.0
    for field in ("loc", "short", "abbr", "name", "disp"):
        c = norm(t.get(field))
        if not c:
            continue
        if n == c:
            return 1.0
        if field == "disp" and c.startswith(n + " "):
            best = max(best, 0.95)
        a, b = set(n.split()), set(c.split())
        j = len(a & b) / len(a | b)
        best = max(best, 0.85 * j)
    return best


def pair_score(sa, sb, close):
    """Both teams must match; or one team's name matches exactly at the same kickoff (a team plays once a day)."""
    score = min(sa, sb)
    if close and max(sa, sb) >= 1.0:
        score = max(score, 0.81)
    return score


def match(g, pool):
    """Best ESPN game for ledger game g. Returns (espn_game, swapped) or (None, None)."""
    ko = when(g["ko"])
    lg = g["lg"]
    cands = []
    for e in pool:
        if lg == "NFL" and e["lg"] != "NFL":
            continue
        if lg in COLLEGE and e["lg"] not in COLLEGE:
            continue
        ed = when(e.get("date"))
        if not ed or abs((ed - ko).total_seconds()) > 36 * 3600:
            continue
        close = abs((ed - ko).total_seconds()) <= 90 * 60
        straight = pair_score(team_sim(g["a"]["n"], e["away"]), team_sim(g["h"]["n"], e["home"]), close)
        flipped = pair_score(team_sim(g["a"]["n"], e["home"]), team_sim(g["h"]["n"], e["away"]), close) - 0.05
        if straight >= flipped:
            cands.append((straight, e, False))
        else:
            cands.append((flipped, e, True))
    cands.sort(key=lambda x: -x[0])
    if not cands or cands[0][0] < 0.8:
        return None, None
    if len(cands) > 1 and cands[1][0] >= 0.8 and cands[0][0] - cands[1][0] < 0.05:
        return None, None  # ambiguous: leave it alone
    return cands[0][1], cands[0][2]


# ---------- players ----------
def pnorm(s):
    return " ".join(SUFFIX.sub(" ", norm(s)).split())


def find_player(name, players):
    n = pnorm(name)
    exact = [p for p in players if pnorm(p.get("n")) == n]
    if len(exact) == 1:
        return exact[0]
    parts = n.split()
    if not parts:
        return None
    last, first = parts[-1], parts[0][:1]
    lf = [p for p in players if pnorm(p.get("n")).split()[-1:] == [last] and pnorm(p.get("n"))[:1] == first]
    if len(lf) == 1:
        return lf[0]
    return None


def stat_for(mk, p):
    m = norm(mk)
    g = lambda k: p.get(k) or 0
    if m.startswith("anytime td"):
        return g("rushTD") + g("recTD") + g("retTD")
    if m.startswith("fantasy"):
        return p.get("fp", 0)
    if m.startswith("rush rec yds") or m.startswith("rush and rec"):
        return g("rushYds") + g("recYds")
    if (m.startswith("pass att") or m.startswith("passing att") or m.startswith("completions")) and \
            ("passAtt" not in p and "comp" not in p):
        return None                          # older box files did not record these
    if m.startswith("kicking") and "kickPts" not in p:
        return None
    table = [("pass att", "passAtt"), ("passing att", "passAtt"), ("completions", "comp"), ("kicking points", "kickPts"),
             ("pass yds", "passYds"), ("passing yds", "passYds"), ("pass td", "passTD"), ("rush yds", "rushYds"),
             ("rushing yds", "rushYds"), ("rec yds", "recYds"), ("receiving yds", "recYds"), ("receptions", "rec"),
             ("longest rec", "longRec"), ("longest rush", "longRush"), ("rush att", "rushAtt"), ("carries", "rushAtt"),
             ("interceptions", "int"), ("int thrown", "int")]
    for pre, key in table:
        if m.startswith(pre):
            return g(key)
    return None


def grade_ou(v, ln, side):
    if v is None:
        return None
    if side == "Y":
        return "W" if v >= 1 else "L"
    if ln is None:
        return None
    d = (v - ln) if side == "O" else (ln - v)
    return "W" if d > 0 else "L" if d < 0 else "P"


# ---------- grading ----------
def wl(x):
    return "W" if x > 0 else "L" if x < 0 else "P"


def grade_game(g, a, h):
    sc = {"A": a, "H": h}
    other = {"A": "H", "H": "A"}
    line = g.get("line") or {}
    fav = line.get("fav") if line.get("sp") is not None else ((g.get("model") or [{}])[0].get("fav") or line.get("fav"))
    su = wl(sc[fav] - sc[other[fav]]) if fav in sc else None
    if su == "P" and g["lg"] != "NFL":
        su = None
    lean = None
    l = g.get("lean") or {}
    mk, side, ln = l.get("mk"), l.get("side"), l.get("ln")
    if mk == "spread" and side in sc and ln is not None:
        m = sc[side] - sc[other[side]]
        lean = wl(m - ln if side == line.get("fav") else m + ln)
    elif mk == "total" and ln is not None:
        lean = wl((a + h - ln) * (1 if side == "O" else -1))
    elif mk == "tt" and ln is not None and l.get("team") in sc:
        lean = wl((sc[l["team"]] - ln) * (1 if side == "O" else -1))
    elif mk == "ml" and side in sc:
        lean = wl(sc[side] - sc[other[side]])
    return su, lean, (lean if g.get("star") else None)


def clock_text(e):
    st = e.get("status") or ""
    per = e.get("period") or 0
    clk = e.get("clock") or ""
    if st == "STATUS_HALFTIME":
        return "Half"
    if st == "STATUS_END_PERIOD":
        return f"End Q{per}" if per <= 4 else "End OT"
    if per > 4:
        return f"OT {clk}".strip()
    if per:
        return f"Q{per} {clk}".strip()
    return e.get("detail") or "Live"


def clv_of(g, close, swapped):
    """Points the pick beat (+) or lost (-) the closing line by. None when it can't be measured."""
    if not close:
        return None
    l = g.get("lean") or {}
    mk, side, ln = l.get("mk"), l.get("side"), l.get("ln")
    if ln is None:
        return None
    if mk == "spread" and side in ("A", "H") and close.get("hm") is not None:
        hm = -close["hm"] if swapped else close["hm"]          # closing home margin in our orientation
        fav = (g.get("line") or {}).get("fav")
        s_pick = -ln if side == fav else ln                     # the side's spread when we picked it
        s_close = -hm if side == "H" else hm                    # the side's spread at the close
        return {"pts": round(s_pick - s_close, 1), "close": s_close, "book": close.get("book")}
    if mk == "total" and side in ("O", "U") and close.get("tot") is not None:
        pts = close["tot"] - ln if side == "O" else ln - close["tot"]
        return {"pts": round(pts, 1), "close": close["tot"], "book": close.get("book")}
    return None


def finished(g):
    """True when a game needs nothing more: final, quarter scores saved, props valued and graded."""
    if g.get("st") != "final" or not g.get("per"):
        return False
    props = g.get("props") or []
    pv = g.get("pv") or []
    pgr = (g.get("gr") or {}).get("props") or []
    if props:
        for i in range(len(props)):
            done_res = i < len(pgr) and pgr[i] in ("V", "X", "U")
            if not done_res and (i >= len(pv) or pv[i] is None):
                return False
    if g.get("tracked"):
        gr = g.get("gr")
        if not gr or (props and None in (gr.get("props") or [None])):
            return False
    return True


# ---------- pick locks and pregame history ----------
def pick_snapshot(g):
    """The parts of a game that make up the pick. Compared and stored as-is (no derived numbers)."""
    return {"line": g.get("line"), "lean": g.get("lean"), "star": bool(g.get("star")),
            "props": [{k: p.get(k) for k in ("pl", "mk", "ln", "side", "odds")} for p in (g.get("props") or [])]}


def lock_diff(g, lock):
    cur = pick_snapshot(g)
    return [k for k in ("line", "lean", "star", "props") if cur.get(k) != lock.get(k)]


def swap_quotes(q):
    """Book quotes written for ESPN's home/away -> the ledger's orientation when the two are reversed."""
    if not isinstance(q, dict):
        return q
    out = {}
    for b, v in q.items():
        v = dict(v)
        for mk in ("ml", "spread"):
            if isinstance(v.get(mk), dict):
                v[mk] = {{"A": "H", "H": "A"}.get(s, s): x for s, x in v[mk].items()}
        out[b] = v
    return out


# ---------- Ledger Model, push tables and prop projections (model/results in this repo) ----------
NFL_ESPN = {"LA": "LAR", "WAS": "WSH"}
PROP_MARKETS = [("pass yds", "pass_yds"), ("passing yds", "pass_yds"), ("pass att", "pass_att"), ("passing att", "pass_att"),
                ("completions", "comp"), ("pass td", "pass_td"), ("interceptions", "int"), ("int thrown", "int"),
                ("rush yds", "rush_yds"), ("rushing yds", "rush_yds"), ("rush att", "rush_att"), ("carries", "rush_att"),
                ("receptions", "rec"), ("rec yds", "rec_yds"), ("receiving yds", "rec_yds"), ("rush rec yds", "rr_yds"),
                ("rush and rec", "rr_yds"), ("anytime td", "td")]
COUNT_MARKETS = {"pass_att", "comp", "pass_td", "int", "rush_att", "rec", "td"}


def pname(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def phi(z):
    import math
    return 0.5 * (1 + math.erf(z / 2 ** 0.5))


def model_league(lg):
    return "NFL" if lg == "NFL" else "FBS" if lg in COLLEGE else None


def pmf_row(table, x):
    """Row of a push table for market number x (rounded to the nearest half point); None outside the table."""
    if not table or x is None:
        return None
    k = round(x * 2) / 2
    row = table.get(f"{k:g}")
    return {"n0": row["n0"], "p": row["p"], "lo": row["lo"], "hi": row["hi"]} if row else None


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def market_numbers(quotes, close=None, line=None):
    """Expected home margin and total from book quotes (ledger orientation), else ESPN's close, else the ledger line."""
    hm = median([-(q.get("spread") or {}).get("H", [None])[0] if (q.get("spread") or {}).get("H") and (q["spread"]["H"][0] is not None) else None
                 for q in (quotes or {}).values()])
    tot = median([(q.get("total") or {}).get("O", [None])[0] if (q.get("total") or {}).get("O") else None for q in (quotes or {}).values()])
    if hm is None and close and close.get("hm") is not None:
        hm = close["hm"]
    if tot is None and close and close.get("tot") is not None:
        tot = close["tot"]
    if hm is None and line and line.get("sp") is not None and line.get("fav") in ("A", "H"):
        hm = line["sp"] if line["fav"] == "H" else -line["sp"]
    if tot is None and line:
        tot = line.get("tot")
    return hm, tot


def model_numbers(M, lg, e, swapped):
    """Ledger Model margin (ledger home minus away) and total for an ESPN game, or None when a team has no rating."""
    L = (M or {}).get("leagues", {}).get(model_league(lg) or "") or {}
    R = L.get("ratings") or {}
    if lg == "FCS" or not R:
        return None
    key = (lambda t: t.get("abbr")) if lg == "NFL" else (lambda t: str(t.get("id")))
    h, a = R.get(key(e.get("home") or {})), R.get(key(e.get("away") or {}))
    if not h or not a:
        return None
    st_ = L.get("state") or {}
    hfa = 0.0 if e.get("neutral") else (st_.get("hfa") or 0.0)
    m = h["r"] - a["r"] + hfa
    t = 2 * (st_.get("lg") or 0) + h["o"] + a["o"] - h["d"] - a["d"]
    return {"m": round(-m if swapped else m, 1), "t": round(t, 1), "v": (M or {}).get("updatedAt")}


def prop_market(mk):
    m = norm(mk)
    if "(" in str(mk or "") or "alt" in m.split():
        return None                       # alternate-line props have their own pricing; not modeled
    for pre, key in PROP_MARKETS:
        if m.startswith(pre):
            return key
    return None


def prop_projection(P, prop):
    """Model chance that the picked side of a prop wins (among non-pushes), from nflverse-based projections."""
    mk = prop_market(prop.get("mk"))
    if not P or not mk:
        return None
    cands = (P.get("players") or {}).get(pname(prop.get("pl")))
    if not cands:
        return None
    tm = (prop.get("tm") or "").upper()
    c = next((x for x in cands if x.get("team") == tm), None) or (cands[0] if len(cands) == 1 else None)
    if not c or mk not in (c.get("m") or {}):
        return None
    mu = c["m"][mk]
    side, ln = prop.get("side"), prop.get("ln")
    if mk == "td" or side == "Y":
        import math
        lam = c["m"].get("td") if mk == "td" else None
        if lam is None:
            return None
        p = 1 - math.exp(-lam) if side in ("Y", "O", None) else math.exp(-lam)
        return {"mu": mu, "p": round(p, 4)}
    a, b = (P.get("sd") or {}).get(mk, [None, None])
    if a is None or ln is None or side not in ("O", "U"):
        return {"mu": mu, "p": None}
    sd = max(0.35 if mk in COUNT_MARKETS else 3.0, a + b * mu)
    if mk in COUNT_MARKETS and float(ln).is_integer():
        over, under = 1 - phi((ln + 0.5 - mu) / sd), phi((ln - 0.5 - mu) / sd)
    else:
        over = 1 - phi((ln - mu) / sd); under = 1 - over
    win, lose = (over, under) if side == "O" else (under, over)
    return {"mu": mu, "p": round(win / (win + lose), 4) if win + lose > 0 else None}


def implied_from_text(odds):
    """Price-implied chance of a prop (with a typical 4.5% two-way margin taken out); None without a sportsbook price."""
    m = re.match(r"^\s*([+\-\u2212]\d{3,5})\b", str(odds or ""))
    if not m:
        return None
    am = int(m.group(1).replace("\u2212", "-"))
    if -100 < am < 100:
        return None
    ip = 100 / (am + 100) if am > 0 else -am / (-am + 100)
    return round(min(0.98, max(0.02, ip / 1.045)), 4)


def closing_chance(gl, books, pmfs, swapped_close=None):
    """Chance the locked lean wins among non-pushes according to the closing market: the push table gives the shape,
    the closing no-vig price at the closing number gives the level. None when it cannot be read."""
    l = gl.get("lean") or {}
    mk, side, ln = l.get("mk"), l.get("side"), l.get("ln")
    if not books or ln is None or mk not in ("spread", "total") or not pmfs:
        return None
    import math

    def novig(a, b):
        def ip(x):
            return 100 / (x + 100) if x > 0 else -x / (-x + 100)
        if a is None or b is None or -100 < a < 100 or -100 < b < 100:
            return None
        return ip(a) / (ip(a) + ip(b))

    def split(pmf, x):
        above, below, at = pmf.get("hi", 0), pmf.get("lo", 0), 0
        for i, q in enumerate(pmf["p"]):
            n = pmf["n0"] + i
            if n > x + 1e-9: above += q
            elif n < x - 1e-9: below += q
            else: at += q
        tot = above + below + at
        return (above / tot, at / tot, below / tot) if tot > 0 else None

    if mk == "spread" and side in ("A", "H") and pmfs.get("m"):
        fav = (gl.get("line") or {}).get("fav")
        s_line = -ln if side == fav else ln                     # the side's own line
        home_line = s_line if side == "H" else -s_line
        ps, lines = [], []
        for q in books.values():
            hq, aq = (q.get("spread") or {}).get("H"), (q.get("spread") or {}).get("A")
            if hq and aq and hq[0] is not None and aq[0] is not None and abs(hq[0] + aq[0]) < 1e-9:
                p = novig(hq[1], aq[1])
                if p is not None:
                    ps.append(p); lines.append(hq[0])
        if not ps:
            return None
        cl = median(lines); pc = median([p for p, x in zip(ps, lines) if x == cl])
        sp_ = split(pmfs["m"], -home_line); sc_ = split(pmfs["m"], -cl)
        if not sp_ or not sc_:
            return None
        ph = sp_[0] / (sp_[0] + sp_[2]) + (pc - sc_[0] / (sc_[0] + sc_[2]))
        p = ph if side == "H" else 1 - ph
        return {"p": round(min(0.995, max(0.005, p)), 4), "push": round(sp_[1], 4), "close": cl}
    if mk == "total" and side in ("O", "U") and pmfs.get("t"):
        ps, lines = [], []
        for q in books.values():
            o, u = (q.get("total") or {}).get("O"), (q.get("total") or {}).get("U")
            if o and u and o[0] is not None and o[0] == u[0]:
                p = novig(o[1], u[1])
                if p is not None:
                    ps.append(p); lines.append(o[0])
        if not ps:
            return None
        cl = median(lines); pc = median([p for p, x in zip(ps, lines) if x == cl])
        sp_ = split(pmfs["t"], ln); sc_ = split(pmfs["t"], cl)
        if not sp_ or not sc_:
            return None
        po = sp_[0] / (sp_[0] + sp_[2]) + (pc - sc_[0] / (sc_[0] + sc_[2]))
        p = po if side == "O" else 1 - po
        return {"p": round(min(0.995, max(0.005, p)), 4), "push": round(sp_[1], 4), "close": cl}
    return None


def model_result(lm, close_hm, close_tot, sc):
    """How the model's own side did against the closing line: [spread edge, W/L/P, total edge, W/L/P]."""
    if not lm or not sc:
        return None
    out = [None, None, None, None]
    if close_hm is not None and lm.get("m") is not None:
        edge = lm["m"] - close_hm
        d = (sc["h"] - sc["a"]) - close_hm
        out[0], out[1] = round(edge, 1), (None if edge == 0 else wl(d if edge > 0 else -d))
    if close_tot is not None and lm.get("t") is not None:
        edge = lm["t"] - close_tot
        d = sc["h"] + sc["a"] - close_tot
        out[2], out[3] = round(edge, 1), (None if edge == 0 else wl(d if edge > 0 else -d))
    return out


def gates_summary(M, P):
    """The small part of the model files the page needs: each gate's verdict and headline numbers."""
    out = {}
    for name, L in ((M or {}).get("leagues") or {}).items():
        if "error" in L:
            out[name] = {"error": L["error"]}
            continue
        g = lambda d: {k: d.get(k) for k in ("pass", "why", "a", "b", "scale", "w", "n_test", "gain", "t", "thr", "ats", "ll_market", "ll_model", "ll_blend") if k in d}
        out[name] = {"spread": g(L.get("spread") or {}), "total": g(L.get("total") or {}), "ml": g(L.get("ml") or {}),
                     "mae": L.get("mae"), "live": L.get("live"), "sigma": L.get("sigma"), "test": L.get("test"), "tune": L.get("tune"),
                     "asof": (L.get("state") or {}).get("asof"), "stale": L.get("stale")}
    meta = {"updatedAt": (M or {}).get("updatedAt"), "leagues": out}
    if P:
        meta["props"] = {"updatedAt": P.get("updatedAt"), "throughWeek": P.get("throughWeek"), "coverage": P.get("coverage")}
    return meta


def et_date(t):
    return t.astimezone(ET).date()


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scores", default=f"{RAW}/scores/latest.json")
    ap.add_argument("--box", default=f"{RAW}/box")
    ap.add_argument("--final", default=f"{RAW}/scores/final", help="season archive of finished games (URL or dir)")
    ap.add_argument("--pregame", action="store_true", help="also write pregame/<slate> (needs DIR/pregame/<slate>.json read first)")
    ap.add_argument("--now", default=None, help="ISO time to use instead of the clock (testing)")
    ap.add_argument("--model", default=f"{RAW}/model/results/ledger_model.json", help="Ledger Model results (URL or path)")
    ap.add_argument("--players", default=f"{RAW}/model/results/nfl_players.json", help="NFL prop projections (URL or path)")
    args = ap.parse_args()
    now = when(args.now) if args.now else dt.datetime.now(UTC)
    now_iso = now.astimezone(UTC).isoformat(timespec="seconds")
    os.makedirs(args.out, exist_ok=True)

    feed = load(args.scores)
    if not feed or not isinstance(feed.get("games"), list):
        live = {"ranAt": now_iso, "updated": 0, "graded": 0, "note": "The score feed is empty or unreachable right now. Nothing changed."}
        _write(args.out, "live.json", live)
        print(json.dumps([{"op": "set", "collection": "meta", "doc_id": "live", "file_path": os.path.join(args.out, "live.json")}]))
        return
    pool = list(feed["games"])
    by_id = {e["id"]: e for e in pool}
    feed_age = (now - when(feed.get("updatedAt"))).total_seconds() / 60 if feed.get("updatedAt") else None
    checked = feed.get("checkedAt") or feed.get("updatedAt")
    M = P = None
    if args.pregame:
        for name in ("model", "players"):
            try:
                v = load(getattr(args, name))
            except Exception as ex:
                print(f"{name} file unavailable: {ex}", file=sys.stderr)
                v = None
            if name == "model":
                M = v
            else:
                P = v
    gates = gates_summary(M, P) if M or P else None

    # games older than the live feed's window come from the season archive (one file per US Eastern date)
    doc_paths = sorted(glob.glob(os.path.join(args.docs, "slates", "*", "leagues", "*.json")))
    window_lo = et_date(now) - dt.timedelta(days=3)
    need = set()
    for path in doc_paths:
        for g in ((load(path) or {}).get("games") or {}).values():
            ko = when(g.get("ko"))
            if ko and ko <= now and not finished(g) and et_date(ko) < window_lo:
                need |= {et_date(ko) + dt.timedelta(days=k) for k in (-1, 0, 1)}
    arch_n = 0
    for d in sorted(need):
        src = f"{args.final}/{d.isoformat()}.json" if re.match(r"https?://", args.final) else os.path.join(args.final, f"{d.isoformat()}.json")
        try:
            arch = load(src) or {}
        except Exception:
            arch = {}
        for e in (arch.get("games") or {}).values():
            if e.get("id") and e["id"] not in by_id:
                pool.append(e)
                by_id[e["id"]] = e
                arch_n += 1

    box_cache = {}

    def box(eid):
        if eid not in box_cache:
            src = f"{args.box}/{eid}.json" if re.match(r"https?://", args.box) else os.path.join(args.box, f"{eid}.json")
            try:
                box_cache[eid] = (load(src) or {}).get("players")
            except Exception:
                box_cache[eid] = None
        return box_cache[eid]

    manifest, n_upd, n_grade, unmatched, prop_gaps, results_by_slate = [], 0, 0, [], 0, {}
    n_lock, n_late, n_void, pregame = 0, 0, 0, {}
    for path in doc_paths:
        slate, lgname = path.split(os.sep)[-3], os.path.basename(path)[:-5]
        doc = load(path) or {}
        games = doc.get("games") or {}
        changes, results = {}, {}
        pg = pregame.setdefault(slate, {"games": {}, "snaps": {}, "_old": load(os.path.join(args.docs, "pregame", f"{slate}.json")) or {}})
        for gid, g in games.items():
            ko = when(g.get("ko"))
            if not ko:
                continue
            gr = g.get("gr") or {}
            props = g.get("props") or []
            if finished(g):
                continue
            e = by_id.get(g.get("eid")) if g.get("eid") else None
            swapped = bool(g.get("eswap"))
            if not e:
                e, swapped = match(g, pool)
            # pregame record: quotes from the feed and an append-only history of the pick before kickoff
            old_snaps = list(((pg["_old"].get("snaps") or {}).get(gid)) or [])
            if ko > now:
                snap = pick_snapshot(g)
                last = {k: v for k, v in (old_snaps[-1] if old_snaps else {}).items() if k != "at"}
                if snap != last:
                    pg["snaps"][gid] = old_snaps + [dict(snap, at=now_iso)]
            old_ent = (pg["_old"].get("games") or {}).get(gid) or {}
            if e and (e.get("quotes") or e.get("qopen") or e.get("qclose") or (args.pregame and ko > now and (M or P))):
                ent = {"state": e.get("state"), "quotes": e.get("quotes") if e.get("state") == "pre" else None,
                       "open": e.get("qopen"), "close": e.get("qclose")}
                if swapped:
                    ent = {k: swap_quotes(v) if k != "state" else v for k, v in ent.items()}
                if ko > now and (M or P):
                    # numbers that only make sense before kickoff; after it, the last ones stay as they were
                    close_e = e.get("close")
                    if close_e and swapped:
                        close_e = dict(close_e, hm=-close_e["hm"] if close_e.get("hm") is not None else None)
                    hm, tot = market_numbers(ent.get("quotes") or ent.get("close") or ent.get("open"), close_e, g.get("line"))
                    push = ((M or {}).get("leagues", {}).get(model_league(g["lg"]) or "") or {}).get("push") or {}
                    pm = {"m": pmf_row(push.get("margin"), hm), "t": pmf_row(push.get("total"), tot)}
                    if pm["m"] or pm["t"]:
                        ent["pmf"] = pm
                    lm = model_numbers(M, g["lg"], e, swapped)
                    if lm:
                        ent["lm"] = lm
                    if g["lg"] == "NFL" and P and props:
                        ent["pp"] = [prop_projection(P, p) for p in props]
                else:
                    for k in ("pmf", "lm", "pp"):
                        if old_ent.get(k) is not None:
                            ent[k] = old_ent[k]
                if old_ent != ent:
                    pg["games"][gid] = ent
            if ko > now + dt.timedelta(hours=1):
                continue
            if not e:
                if ko < now - dt.timedelta(minutes=20):
                    unmatched.append(f"{g['a']['n']} @ {g['h']['n']}")
                continue
            ea, eh = (e["home"], e["away"]) if swapped else (e["away"], e["home"])
            ch = {}
            if g.get("eid") != e["id"]:
                ch["eid"] = e["id"]
                if swapped:
                    ch["eswap"] = True
            state = e.get("state")
            # lock the pick the first time the game is seen after kickoff; grading uses the lock from then on
            lock = g.get("lock")
            if not lock and g.get("tracked") and (ko <= now or state in ("in", "post")):
                late = now - ko > dt.timedelta(hours=12)
                lock = dict(pick_snapshot(g), at=now_iso,
                            src="back-filled after the game (no snapshot from before kickoff)" if late else "first score update after kickoff")
                if late:
                    lock["late"] = True
                    n_late += 1
                if old_snaps:
                    before = [x for x in old_snaps if when(x.get("at")) and when(x["at"]) <= ko]
                    if before:
                        lock["pre"] = before[-1]["at"]
                pre_ent = pg["games"].get(gid) or old_ent
                if not late and pre_ent.get("lm"):
                    lock["lm"] = pre_ent["lm"]                       # what the model said before kickoff
                if not late and pre_ent.get("pp"):
                    lock["pp"] = [(x or {}).get("p") for x in pre_ent["pp"]][:len(lock["props"])]
                ch["lock"] = lock
                n_lock += 1
            if lock:
                diff = lock_diff(g, lock)
                if diff != (g.get("lockdiff") or []):
                    ch["lockdiff"] = diff
            gl = dict(g, line=lock["line"], lean=lock["lean"], star=lock["star"]) if lock else g
            lprops = lock["props"] if lock else [{k: p.get(k) for k in ("pl", "mk", "ln", "side")} for p in props]
            if state in ("in", "post") and g.get("tracked") and (g.get("clv") is None or ("p" not in (g.get("clv") or {}) and not g.get("clvp"))):
                cv = g.get("clv") or clv_of(gl, e.get("close"), swapped)
                pre_ent = pg["games"].get(gid) or old_ent
                cbooks = e.get("qclose")
                if cbooks and swapped:
                    cbooks = swap_quotes(cbooks)
                cc = closing_chance(gl, cbooks, pre_ent.get("pmf")) if not (lock or {}).get("late") else None
                if cc is not None:
                    cv = dict(cv or {}, p=cc["p"], push=cc["push"])
                if cv is not None and cv != g.get("clv"):
                    ch["clv"] = cv
            if state == "pre" or ea.get("score") is None or eh.get("score") is None:
                if e.get("status") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_DELAYED") and g.get("clk") != e.get("detail"):
                    ch["clk"] = e.get("detail")
                if ch:
                    changes[gid] = ch
                continue
            sc = {"a": ea["score"], "h": eh["score"]}
            if state == "in":
                clk = clock_text(e)
                if g.get("st") != "live" or g.get("sc") != sc or g.get("clk") != clk:
                    ch.update({"st": "live", "sc": sc, "clk": clk})
                    n_upd += 1
            elif state == "post" and e.get("completed"):
                if g.get("st") != "final" or g.get("sc") != sc:
                    ch.update({"st": "final", "sc": sc, "clk": None})
                    n_upd += 1
                la, lh = ea.get("lines") or [], eh.get("lines") or []
                if len(la) >= 4 and len(lh) >= 4 and None not in la + lh:
                    per = {"a": la, "h": lh}
                    if g.get("per") != per:
                        ch["per"] = per
                # props: actual values
                pv = list(g.get("pv") or [None] * len(props))
                pv += [None] * (len(props) - len(pv))
                players = box(e["id"]) if props else None
                pgr = list(gr.get("props") or [None] * len(props))
                pgr += [None] * (len(props) - len(pgr))
                for i, p in enumerate(props):
                    if i >= len(lprops):          # added after the lock: shown, never graded
                        pgr[i] = "X"
                        continue
                    lp = lprops[i]
                    pl = find_player(lp.get("pl"), players) if players else None
                    if pl:
                        v = stat_for(lp.get("mk"), pl)
                        if v is not None:
                            pv[i] = v
                    if pgr[i] is None:
                        pgr[i] = grade_ou(pv[i], lp.get("ln"), lp.get("side"))
                    if pgr[i] is None and players and now - ko > VOID_AFTER:
                        # a day after the game the box score is settled. Not in it: inactive (books void) or played
                        # without a stat (a loss for Overs), which the box score cannot tell apart; a market the box
                        # score does not carry (D/ST, kicking, etc.) cannot be graded either. Marked unresolved.
                        pgr[i] = "U"
                        n_void += 1
                    if pgr[i] is None:
                        prop_gaps += 1
                if props and pv != (g.get("pv") or []):
                    ch["pv"] = pv
                if g.get("tracked"):
                    su, lean, star = grade_game(gl, sc["a"], sc["h"])
                    new_gr = {"su": su, "lean": lean, "star": star, "props": pgr}
                    if gr != new_gr:
                        ch["gr"] = new_gr
                        if not g.get("gr"):
                            n_grade += 1
                        cnt = {"W": 0, "L": 0, "P": 0, "U": 0}
                        for r in pgr:
                            if r in cnt:
                                cnt[r] += 1
                        l = gl.get("lean") or {}
                        results[gid] = {"lg": g["lg"], "su": su, "lean": lean, "star": star,
                                        "conf": l.get("conf"), "mk": l.get("mk"), "props": cnt, "locked": bool(lock)}
                        cv = ch.get("clv") or g.get("clv")
                        if cv:
                            if cv.get("pts") is not None:
                                results[gid]["clv"] = cv.get("pts")
                            if cv.get("p") is not None:
                                results[gid]["cp"] = cv["p"]
                        if lock and lock.get("lm"):
                            cl = e.get("close") or {}
                            chm = cl.get("hm")
                            if chm is not None and swapped:
                                chm = -chm
                            mr = model_result(lock["lm"], chm, cl.get("tot"), sc)
                            if mr:
                                results[gid]["lme"] = mr
                        if lock and lock.get("pp"):
                            rows = []
                            for i, pp_ in enumerate(lock["pp"]):
                                if pp_ is None or i >= len(pgr) or pgr[i] not in ("W", "L"):
                                    continue
                                rows.append([pp_, 1 if pgr[i] == "W" else 0, implied_from_text((lprops[i] if i < len(lprops) else {}).get("odds"))])
                            if rows:
                                results[gid]["pp"] = rows
            else:
                if g.get("clk") != e.get("detail"):
                    ch["clk"] = e.get("detail")
            if ch:
                changes[gid] = ch
        if changes:
            f = _write(args.out, f"{slate}__{lgname}.json", {"games": changes, "liveAt": now_iso})
            manifest.append({"op": "update", "collection": f"slates/{slate}/leagues", "doc_id": lgname, "file_path": f})
        if results:
            results_by_slate.setdefault(slate, {}).update(results)

    for slate, pg in pregame.items():
        if not args.pregame:
            continue
        # price coverage for the slate's tracked games (Phase 2 gate)
        tracked = priced = 0
        all_ent = dict(pg["_old"].get("games") or {}); all_ent.update(pg["games"])
        for path in doc_paths:
            if path.split(os.sep)[-3] != slate:
                continue
            for gid, g in ((load(path) or {}).get("games") or {}).items():
                if g.get("tracked"):
                    tracked += 1
                    en = all_ent.get(gid) or {}
                    if en.get("close") or en.get("quotes"):
                        priced += 1
        cov = {"tracked": tracked, "priced": priced}
        extra = {}
        if cov != pg["_old"].get("coverage"):
            extra["coverage"] = cov
        if gates and gates != pg["_old"].get("gates"):
            extra["gates"] = gates
        if not pg["games"] and not pg["snaps"] and not extra:
            continue
        exists = os.path.exists(os.path.join(args.docs, "pregame", f"{slate}.json"))
        body = dict({"slate": slate, "updatedAt": now_iso, "feedCheckedAt": checked, "games": pg["games"], "snaps": pg["snaps"]}, **extra)
        f = _write(args.out, f"{slate}__pregame.json", body)
        manifest.append({"op": "update" if exists else "set", "collection": "pregame", "doc_id": slate, "file_path": f})

    for slate, res in results_by_slate.items():
        rpath = os.path.join(args.docs, "results", f"{slate}.json")
        body = {"slate": slate, "updatedAt": now_iso, "games": res}
        f = _write(args.out, f"{slate}__results.json", body)
        manifest.append({"op": "update" if os.path.exists(rpath) else "set", "collection": "results", "doc_id": slate, "file_path": f})

    bits = [f"{n_upd} game{'s' if n_upd != 1 else ''} updated", f"{n_grade} graded"]
    if unmatched:
        bits.append(f"{len(unmatched)} not found in the feed")
    if prop_gaps:
        bits.append(f"{prop_gaps} prop{'s' if prop_gaps != 1 else ''} waiting on box scores")
    if n_void:
        bits.append(f"{n_void} prop{'s' if n_void != 1 else ''} unresolved (player or market not in the box score)")
    if n_lock:
        bits.append(f"{n_lock} pick{'s' if n_lock != 1 else ''} locked" + (f" ({n_late} back-filled after the game)" if n_late else " at kickoff"))
    if arch_n:
        bits.append(f"{arch_n} older games read from the season archive")
    if feed_age is not None and feed_age > 45:
        bits.append(f"feed last changed {int(feed_age)} min ago")
    live = {"ranAt": now_iso, "updated": n_upd, "graded": n_grade, "note": ", ".join(bits) + "."}
    if unmatched:
        live["unmatched"] = unmatched[:20]
    f = _write(args.out, "live.json", live)
    manifest.append({"op": "set", "collection": "meta", "doc_id": "live", "file_path": f})
    print(json.dumps(manifest))
    print(live["note"], file=sys.stderr)


def _write(out, name, obj):
    p = os.path.join(out, name)
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    return p


if __name__ == "__main__":
    main()
