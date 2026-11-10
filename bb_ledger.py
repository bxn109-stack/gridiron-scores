#!/usr/bin/env python3
"""Gridiron Ledger basketball: build the day's board from the robot's feed, then lock and grade the picks.

Run by Claude's basketball scheduled tasks, not by GitHub. Standard library only. It borrows the football grader's
team matching, grading, pick-lock and hash-chain helpers from apply_scores.py (which must sit next to this file) and
never reads or writes a football document.

  python3 bb_ledger.py --docs DIR --out OUT --slate YYYY-MM-DD [--ctx ctx.json] [--feed URL|DIR] [--now ISO]
                       [--box URL|DIR] [--model URL|PATH] [--cm URL|PATH]
  python3 bb_ledger.py --list --slate YYYY-MM-DD [--feed URL|DIR]     # tonight's games with a line, one per line
  python3 bb_ledger.py --docs DIR --out OUT --grade-only               # lock and grade only, no board build

DIR holds the docs saved by ArtifactData (out_dir):
  DIR/meta/bbconfig.json, DIR/bbslates/<date>/leagues/<NBA|WNBA|MBB|WBB>.json, DIR/bbpregame/<date>.json,
  DIR/bbresults/<date>.json, DIR/bbcmresults/<date>.json. Missing docs are fine.

Board build (for --slate): every game on that US Eastern date with a posted spread gets a GAME object (football
schema). The line is DraftKings' number from ESPN. The projections are ESPN's pregame win chance (BPI, from the
robot) converted to a margin, plus any projections, expert picks and player props Claude found (ctx.json):
  {"<gameId>": {"model": [{src, fav: "A"|"H", m?, p?, t?}],          t = the source's projected total
                "experts": [{src, pick, side: "A"|"H"|"O"|"U"}],       O/U = a pick on the total
                "props": [{pl, tm, mk, ln, side: "O"|"U"|"Y", odds?, src, why}],
                "signal": "market signal with its source (e.g. reverse line movement, Action Network)",
                "desc": str, "facts": [str], "inj": str, "out": ["players ruled out"]}}
The lean follows the football LEAN METHOD (spread, or the total when it clearly beats the spread case), and stars
follow the football star rules (a confidence-2 lean, an independent market signal and a fair price; at most
MAX_STARS a day). Props are appended (never reordered or removed: saved slips point at them by position). Games within
15 minutes of tip, started or finished are never touched.

Grading: picks (line, lean, star, props) are locked at tip from the last copy saved before tip (verified), exactly as
football; finished games are graded (straight up, lean, star, props from the robot's box scores) into
bbresults/<date>. Sportsbook quotes, the pick history, the Ledger Model's numbers, push tables and gates
(model/results/bb_model.json) go to bbpregame/<date>; the Claude Model's cards (from model/results/bb_cm.json) are
built before tip and graded into bbcmresults/<date>; meta/bblive gets a one-line note. Prints a JSON manifest:
  [{"op": "update"|"set", "collection": ..., "doc_id": ..., "file_path": ...}, ...]
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_scores as A  # noqa: E402  (football grader: helpers only)

ROOT_URL = "https://raw.githubusercontent.com/bxn109-stack/gridiron-scores/main"
RAW = f"{ROOT_URL}/scores/bb"
UTC = dt.timezone.utc
ET = A.ET
LEAGUES = ("NBA", "WNBA", "MBB", "WBB")
COLLEGE = {"MBB", "WBB"}
MAX_STARS = 3                                   # stars per day across all leagues (football: 2-6 a week)
MOVE = {"NBA": 1.0, "WNBA": 1.0, "MBB": 1.5, "WBB": 1.5}   # line move since the open that counts as a market signal
TOT_DIV = 4.5                                   # totals: points of edge per score point (spreads use 3)
WORST_PRICE = -115                              # a star's price must be at least this good
# Scale that turns a win chance into a projected margin (margin = SIG * inverse-normal(chance)). Fitted to posted
# DraftKings spreads against ESPN BPI chances on Nov 9-10, 2026 (MBB 39 games -> 14.1, NBA 9 -> 10.8, WBB 2 -> 16.1),
# so BPI margins sit on the market's scale on average and the leans are not tilted toward underdogs by the conversion.
SIG = {"NBA": 11.0, "WNBA": 11.0, "MBB": 14.0, "WBB": 15.0}
LOCK_BEFORE = dt.timedelta(minutes=15)
DEAD = ("STATUS_POSTPONED", "STATUS_CANCELED")


# ---------- small helpers ----------
def slug(s):
    s = A.norm(s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "team"


def inv_phi(p):
    """Inverse of the standard normal CDF (Acklam's approximation; plenty for converting win chances)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        return -inv_phi(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fmt_ln(x):
    if x is None:
        return "?"
    x = float(x)
    s = f"{abs(x):g}"
    return "pk" if x == 0 else (f"+{s}" if x > 0 else f"-{s}")


def et_day(ko):
    return ko.astimezone(ET).date()


def team_name(t):
    return t.get("short") or t.get("loc") or t.get("disp") or t.get("name") or "?"


def game_id(lg, e):
    return f"{lg}-{slug(team_name(e['away']))}-{slug(team_name(e['home']))}"


# ---------- feed ----------
def load_day(feed, d):
    src = f"{feed}/{d.isoformat()}.json" if re.match(r"https?://", feed) else os.path.join(feed, f"{d.isoformat()}.json")
    try:
        return A.load(src) or {}
    except Exception as ex:  # a missing or unreadable day is not fatal
        print(f"feed {d}: {ex}", file=sys.stderr)
        return {}


def load_status(feed):
    src = f"{feed}/status.json" if re.match(r"https?://", feed) else os.path.join(feed, "status.json")
    try:
        return A.load(src) or {}
    except Exception:
        return {}


def book_line(e):
    """(fav "A"|"H", spread >= 0, total) from DraftKings' current number (or the last one before tip), else ESPN's line."""
    for key in ("quotes", "qclose"):
        q = (e.get(key) or {}).get("DraftKings") or next(iter((e.get(key) or {}).values()), None)
        if not q:
            continue
        sp = (q.get("spread") or {}).get("H")
        tot = (q.get("total") or {}).get("O")
        hl = sp[0] if sp and sp[0] is not None else None
        t = tot[0] if tot and tot[0] is not None else None
        if hl is not None or t is not None:
            if hl is None:
                return None, None, t, q.get("book")
            return ("H" if hl <= 0 else "A"), abs(hl), t, q.get("book")
    o = e.get("odds") or e.get("close") or {}
    if o.get("hm") is not None or o.get("tot") is not None:
        hm = o.get("hm")
        if hm is None:
            return None, None, o.get("tot"), o.get("book")
        return ("H" if hm >= 0 else "A"), abs(hm), o.get("tot"), o.get("book")
    return None, None, None, None


# ---------- LEAN METHOD ----------
def home_margin(x, sig):
    fav, m, p = x.get("fav"), x.get("m"), x.get("p")
    if m is None and p is not None:
        try:
            p = float(p)
        except (TypeError, ValueError):
            return None
        if p > 1:
            p /= 100
        m = sig * inv_phi(p)
    if m is None or fav not in ("A", "H"):
        return None
    return float(m) if fav == "H" else -float(m)


def spread_case(g):
    """(lean, score) on the spread, or (None, 0)."""
    line = g.get("line") or {}
    sp, fav = line.get("sp"), line.get("fav")
    if sp is None or fav not in ("A", "H"):
        return None, 0.0
    sig = SIG.get(g["lg"], 11.0)
    projs = [(x.get("src"), home_margin(x, sig)) for x in g.get("model") or []]
    projs = [(s, v) for s, v in projs if v is not None]
    if not projs:
        return None, 0.0
    line_hm = sp if fav == "H" else -sp
    avg = statistics.mean(v for _, v in projs)
    diff = avg - line_hm
    xs = [x for x in g.get("experts") or [] if x.get("side") in ("A", "H")]
    votes = sum(1 if x["side"] == "H" else -1 for x in xs)
    score = clamp(diff / 3, -1.5, 1.5) + clamp(0.5 * votes, -1, 1)
    side = "H" if score > 0 else "A"
    conf = 1
    if abs(score) >= 1.25 and votes and (diff > 0) == (votes > 0) and len(projs) >= 2:
        conf = 2
    ln = 0.0 if sp == 0 else (-sp if side == fav else sp)
    names = {"A": g["a"]["n"], "H": g["h"]["n"]}
    pf = "H" if avg >= 0 else "A"
    n = len(projs)
    why = (f"Projections ({n}) average {names[pf]} by {abs(avg):.1f}" if n > 1 else f"Projections (1) from {projs[0][0]} have {names[pf]} by {abs(avg):.1f}")
    why += f" against a line of {names[fav]} {fmt_ln(-sp)}, so the value side is {names[side]} {fmt_ln(ln)}."
    if votes:
        why += f" Expert picks: net {abs(votes)} toward {names['H' if votes > 0 else 'A']}."
    elif xs:
        why += " Expert picks: net even."
    return {"mk": "spread", "side": side, "ln": ln, "conf": conf, "why": why}, score


def total_case(g):
    """(lean, score) on the total from projected totals (t) and expert total picks, or (None, 0)."""
    tot = (g.get("line") or {}).get("tot")
    if tot is None:
        return None, 0.0
    projs = [(x.get("src"), float(x["t"])) for x in g.get("model") or [] if isinstance(x.get("t"), (int, float))]
    if not projs:
        return None, 0.0
    avg = statistics.mean(v for _, v in projs)
    diff = avg - tot
    xs = [x for x in g.get("experts") or [] if x.get("side") in ("O", "U")]
    votes = sum(1 if x["side"] == "O" else -1 for x in xs)
    score = clamp(diff / TOT_DIV, -1.5, 1.5) + clamp(0.5 * votes, -1, 1)
    side = "O" if score > 0 else "U"
    conf = 2 if abs(score) >= 1.25 and votes and (diff > 0) == (votes > 0) and len(projs) >= 2 else 1
    why = f"Projected totals ({len(projs)}) average {avg:.1f} against a total of {tot:g}, so the lean is the {'Over' if side == 'O' else 'Under'}."
    if votes:
        why += f" Expert picks: net {abs(votes)} toward the {'Over' if votes > 0 else 'Under'}."
    return {"mk": "total", "side": side, "ln": float(tot), "conf": conf, "why": why}, score


def lean_eval(g):
    """The LEAN METHOD: the spread lean, unless the total case clearly favors the total (a confidence-2 total case,
    a confidence-1 spread case and a bigger score). Returns (lean, score)."""
    sl, ss = spread_case(g)
    tl, ts = total_case(g)
    if tl and tl["conf"] == 2 and (not sl or (sl["conf"] == 1 and abs(ts) > abs(ss))):
        return tl, ts
    return sl, ss


def lean_for(g):
    return lean_eval(g)[0]


def side_quote(e, lean, swapped=False):
    """DraftKings' (or the first book's) current and opening number and price for the lean's side, in the game's
    orientation: (line now, price now, line at open)."""
    if not lean or not e:
        return None, None, None
    mk, side = lean["mk"], lean["side"]
    key = {"spread": "spread", "total": "total"}.get(mk)
    if not key:
        return None, None, None
    sel = {"A": "H", "H": "A"}.get(side, side) if swapped and key == "spread" else side

    def pick(qs):
        q = (qs or {}).get("DraftKings") or next(iter((qs or {}).values()), None)
        v = ((q or {}).get(key) or {}).get(sel)
        return (v[0], v[1]) if v and v[0] is not None else (None, None)
    now_ln, now_p = pick(e.get("quotes") or e.get("qclose"))
    open_ln, _ = pick(e.get("qopen"))
    return now_ln, now_p, open_ln


def star_case(g, e, score, signal):
    """Football's star rules: a confidence-2 lean, at least one independent market signal (the line moving toward the
    side since it opened, or a signal Claude found and sourced), and a price at least as good as WORST_PRICE.
    Returns starWhy or None."""
    lean = g.get("lean")
    if not lean or lean.get("conf", 1) < 2 or lean["mk"] not in ("spread", "total"):
        return None
    now_ln, price, open_ln = side_quote(e, lean)
    names = {"A": g["a"]["n"], "H": g["h"]["n"], "O": "the Over", "U": "the Under"}
    sigs = []
    if now_ln is not None and open_ln is not None:
        # a spread moving toward the side makes that side's number worse (smaller); a total moving toward the Over rises
        moved = (open_ln - now_ln) if lean["mk"] == "spread" else ((now_ln - open_ln) if lean["side"] == "O" else (open_ln - now_ln))
        if moved >= MOVE.get(g["lg"], 1.5):
            sigs.append(f"the line moved {moved:g} toward {names[lean['side']]} since it opened ({fmt_ln(open_ln) if lean['mk'] == 'spread' else f'{open_ln:g}'} to "
                        f"{fmt_ln(now_ln) if lean['mk'] == 'spread' else f'{now_ln:g}'} at DraftKings)")
    if signal:
        sigs.append(str(signal).strip().rstrip("."))
    if not sigs or price is None or price < WORST_PRICE:
        return None
    return (f"Confidence-2 lean (projections and expert picks agree, score {abs(score):.2f}), plus " + "; ".join(sigs)
            + f"; price {'+' if price > 0 else ''}{price} at DraftKings.")


# ---------- board build ----------
def bpi_entry(e, lg):
    p = e.get("pred") or {}
    hp = p.get("hp")
    if hp is None or p.get("stale"):
        return None
    fav = "H" if hp >= 50 else "A"
    pw = hp if fav == "H" else 100 - hp
    return {"src": p.get("src") or "ESPN BPI", "fav": fav, "m": round(SIG.get(lg, 11.0) * inv_phi(pw / 100), 1), "p": round(pw, 1)}


def describe(e, lg):
    def t(x):
        r = f"No. {x['rank']} " if x.get("rank") else ""
        return f"{r}{team_name(x)}" + (f" ({x['rec']})" if x.get("rec") else "")
    s = f"{t(e['away'])} {'vs.' if e.get('neutral') else 'at'} {t(e['home'])}"
    return s + (", neutral site." if e.get("neutral") else ".")


PROP_KEYS = ("pl", "tm", "mk", "ln", "side", "odds", "src", "why")


def merge_props(old, new):
    """Existing props keep their position (saved slips point at them by index); a prop for the same player and market
    is updated in place before tip; new ones are appended. Props need a player, a market, a side and (except Yes
    props) a line."""
    out = [dict(p) for p in old or []]
    idx = {(A.pnorm(p.get("pl")), A.norm(p.get("mk"))): i for i, p in enumerate(out)}
    for p in new or []:
        if not isinstance(p, dict) or not p.get("pl") or not p.get("mk") or p.get("side") not in ("O", "U", "Y"):
            continue
        if p["side"] != "Y" and not isinstance(p.get("ln"), (int, float)):
            continue
        clean = {k: p.get(k) for k in PROP_KEYS if p.get(k) is not None}
        k = (A.pnorm(p["pl"]), A.norm(p["mk"]))
        if k in idx:
            out[idx[k]] = clean
        else:
            idx[k] = len(out)
            out.append(clean)
    return out


def build_game(e, lg, old, ctx, now):
    fav, sp, tot, book = book_line(e)
    gid = old.get("id") if old else game_id(lg, e)
    c = ctx.get(gid) or ctx.get(e["id"]) or {}
    # research merges by source: a later run's source replaces the same source from earlier today, the rest are kept
    bpi = bpi_entry(e, lg)
    new_m = [{k: x.get(k) for k in ("src", "fav", "m", "p", "t") if x.get(k) is not None} for x in c.get("model") or []
             if isinstance(x, dict) and x.get("src") and (x.get("fav") in ("A", "H") or isinstance(x.get("t"), (int, float)))]
    srcs = {x["src"] for x in new_m} | ({bpi["src"]} if bpi else set())
    model = ([bpi] if bpi else []) + [x for x in (old or {}).get("model") or [] if x.get("src") not in srcs] + new_m
    if not bpi and old:
        model = [x for x in old.get("model") or [] if x.get("src") == "ESPN BPI" and x.get("src") not in {y["src"] for y in new_m}] + \
                [x for x in model if x.get("src") != "ESPN BPI"]
    new_x = [{k: x.get(k) for k in ("src", "pick", "side", "score") if x.get(k) is not None}
             for x in c.get("experts") or [] if isinstance(x, dict) and x.get("src")]
    xsrcs = {x["src"] for x in new_x}
    experts = [x for x in (old or {}).get("experts") or [] if x.get("src") not in xsrcs] + new_x
    g = {
        "id": gid, "lg": lg, "eid": e["id"], "ko": A.when(e["date"]).astimezone(ET).isoformat(timespec="seconds"),
        "a": {"n": team_name(e["away"]), "r": e["away"].get("rank"), "rec": e["away"].get("rec")},
        "h": {"n": team_name(e["home"]), "r": e["home"].get("rank"), "rec": e["home"].get("rec")},
        "neu": bool(e.get("neutral")), "tv": (old or {}).get("tv"),
        "line": {"fav": fav, "sp": sp, "tot": tot, "src": "DraftKings via ESPN" if (book or "").replace(" ", "").lower() in ("draftkings", "") else f"{book} via ESPN"},
        "model": model, "experts": experts,
        "star": False, "starWhy": "",
        "desc": c.get("desc") or (old or {}).get("desc") or describe(e, lg),
        "facts": c.get("facts") or (old or {}).get("facts") or [],
        "inj": c.get("inj") or (old or {}).get("inj"),
        "props": merge_props((old or {}).get("props"), c.get("props")),
        "st": "pre", "sc": None, "clk": None, "tracked": True, "gr": None,
    }
    if c.get("out") or (old or {}).get("out"):
        g["out"] = [str(x) for x in (c.get("out") or (old or {}).get("out"))][:20]
    if c.get("signal") or (old or {}).get("signal"):
        g["signal"] = str(c.get("signal") or old.get("signal"))[:300]
    lean, score = lean_eval(g)
    if old and old.get("lean") and lean and (old["lean"].get("mk"), old["lean"].get("side"), old["lean"].get("ln")) != (lean["mk"], lean["side"], lean["ln"]):
        lean["why"] = f"Updated {now.astimezone(ET).strftime('%a %-I:%M %p')}: " + lean["why"]
    elif old and old.get("lean") and lean and old["lean"].get("why", "").startswith("Updated ") and \
            (old["lean"].get("mk"), old["lean"].get("side"), old["lean"].get("ln")) == (lean["mk"], lean["side"], lean["ln"]):
        lean["why"] = old["lean"]["why"].split(": ", 1)[0] + ": " + lean["why"]
    g["lean"] = lean
    g["_score"] = score
    g["_star"] = star_case(g, e, score, g.get("signal"))
    return g


def assign_stars(slate, built, docs, now):
    """At most MAX_STARS stars a day: games already within 15 minutes of tip keep theirs and count first; the rest go to
    the qualifying games with the strongest lean scores. A star that no longer qualifies is taken off before tip."""
    locked = 0
    for lg, doc in docs.items():
        for gid, g in (doc.get("games") or {}).items():
            ko = A.when(g.get("ko"))
            if g.get("star") and ko and (ko - now <= LOCK_BEFORE or g.get("st") not in (None, "pre")) and gid not in (built.get(lg) or {}):
                locked += 1
    cands = sorted(((abs(g.get("_score", 0)), lg, gid) for lg, gs in built.items() for gid, g in gs.items() if g.get("_star")), reverse=True)
    keep = {(lg, gid) for _, lg, gid in cands[:max(0, MAX_STARS - locked)]}
    for lg, gs in built.items():
        for gid, g in gs.items():
            on = (lg, gid) in keep
            g["star"], g["starWhy"] = on, (g["_star"] if on else "")
            g.pop("_star", None)
            g.pop("_score", None)


def build(slate, feed_day, docs, ctx, now, leagues, CMd=None, cm_rows=None):
    """New/updated games per league for the slate date. Returns ({lg: {gid: game or changed fields}}, new game count).
    With the Claude Model's numbers (CMd) each game also gets its cm card; its checklist rows go to cm_rows[lg][gid]."""
    out, n_new = {}, 0
    full = {}
    by_eid = {}
    for lg in leagues:
        for gid, g in ((docs.get(lg) or {}).get("games") or {}).items():
            if g.get("eid"):
                by_eid[g["eid"]] = (lg, gid, g)
    for e in (feed_day.get("games") or {}).values():
        lg = e.get("lg")
        if lg not in leagues or e.get("status") in DEAD or e.get("state") != "pre":
            continue
        ko = A.when(e.get("date"))
        if not ko or et_day(ko) != slate or ko - now <= LOCK_BEFORE:
            continue
        hit = by_eid.get(e["id"])
        old = hit[2] if hit else None
        if old and (old.get("st") not in (None, "pre") or (A.when(old.get("ko")) and A.when(old["ko"]) - now <= LOCK_BEFORE)):
            continue
        fav, sp, _, _ = book_line(e)
        if sp is None and not old:
            continue                                   # only games with a posted spread
        g = build_game(e, lg, old, ctx, now)
        if sp is None and old:
            g["line"] = old.get("line")
            g["lean"] = old.get("lean")
            g["_star"] = g["_star"] if old.get("star") else None
        keep = ("lock", "lockdiff", "clv", "pv", "per", "cm", "cmgr", "cmpv", "cmpa")
        for k in keep:
            if old and k in old:
                g[k] = old[k]
        if not old:
            ids = set((docs.get(lg) or {}).get("games") or {}) | set(full.get(lg) or {})
            if g["id"] in ids:
                g["id"] = f"{g['id']}-{e['id']}"
            n_new += 1
        if CMd:
            card, rows = cm_card(g, e, lg, CMd, now)
            if card:
                oc = (old or {}).get("cm")
                if oc and {k: v for k, v in oc.items() if k != "at"} == {k: v for k, v in card.items() if k != "at"}:
                    card = oc                              # unchanged: keep its build time
                g["cm"] = card
                if cm_rows is not None:
                    cm_rows.setdefault(lg, {})[old["id"] if old else g["id"]] = {"rows": rows}
        full.setdefault(lg, {})[old["id"] if old else g["id"]] = (g, old)
    assign_stars(slate, {lg: {gid: g for gid, (g, _) in gs.items()} for lg, gs in full.items()}, docs, now)
    for lg, gs in full.items():
        for gid, (g, old) in gs.items():
            if old:
                changed = {k: v for k, v in g.items() if old.get(k) != v}
                if changed:
                    out.setdefault(lg, {})[gid] = changed
            else:
                out.setdefault(lg, {})[gid] = g
    return out, n_new


# ---------- grading ----------
SNAP_KEYS = ("line", "lean", "star", "props")


def snap_of(g):
    """The parts of a game that make up the pick, without free text (keeps the pick history small)."""
    line, lean = g.get("line") or {}, g.get("lean")
    return {"line": {k: line.get(k) for k in ("fav", "sp", "tot")},
            "lean": {k: lean.get(k) for k in ("mk", "side", "team", "ln", "conf") if k in lean} if lean else None,
            "star": bool(g.get("star")),
            "props": [{k: p.get(k) for k in ("pl", "mk", "ln", "side", "odds")} for p in (g.get("props") or [])]}


def lock_diff(g, lock):
    cur = snap_of(g)
    return [k for k in SNAP_KEYS if cur.get(k) != lock.get(k)]


def finished(g):
    """Final, graded, and every prop valued or settled (a prop waits for the box score)."""
    if g.get("st") != "final":
        return False
    if not g.get("tracked"):
        return True
    gr = g.get("gr")
    if not gr:
        return False
    props = g.get("props") or []
    if props and (len(gr.get("props") or []) < len(props) or None in gr["props"]):
        return False
    if g.get("cm") and g["cm"].get("tracked") is not False and not g.get("cmgr"):
        return False
    return True


# ---------- player props ----------
WORD = {"pts": "pts", "points": "pts", "point": "pts", "reb": "reb", "rebs": "reb", "rebounds": "reb", "rebound": "reb",
        "ast": "ast", "asts": "ast", "assists": "ast", "assist": "ast", "stl": "stl", "stls": "stl", "steals": "stl",
        "steal": "stl", "blk": "blk", "blks": "blk", "blocks": "blk", "block": "blk", "blocked": "blk",
        "to": "to", "tos": "to", "turnovers": "to", "turnover": "to", "pra": ("pts", "reb", "ast")}
THREE = {"3", "3pt", "3pm", "3pts", "3s", "three", "threes", "3pointers", "3pointer", "pointers", "pointer"}


def bb_market(mk):
    """A basketball prop market -> the box-score fields it adds up (tuple), "fp", "dd", "td" or None (not gradable)."""
    m = A.norm(mk)
    if not m or "first" in m.split() or "alt" in m.split() or "(" in str(mk or ""):
        return None
    if "fantasy" in m:
        return "fp"
    if "double double" in m:
        return "dd"
    if "triple double" in m:
        return "td"
    w = m.replace("3 pointers", "3pointers").replace("3 pt", "3pt").replace("3 point", "3pt").split()
    att = "attempted" in w or "attempts" in w or "att" in w
    if "offensive" in w and any(x.startswith("reb") for x in w):
        return ("oreb",)
    if "defensive" in w and any(x.startswith("reb") for x in w):
        return ("dreb",)
    if any(x in THREE for x in w):
        return ("tpa",) if att else ("tpm",)
    if "minutes" in w or w == ["min"]:
        return ("min",)
    if ("field" in w and "goals" in w) or "fg" in w or "fgm" in w:
        return ("fga",) if att else ("fgm",)
    if ("free" in w and "throws" in w) or "ft" in w or "ftm" in w:
        return ("fta",) if att else ("ftm",)
    parts = []
    for x in w:
        v = WORD.get(x)
        if isinstance(v, tuple):
            parts.extend(v)
        elif v:
            parts.append(v)
    return tuple(dict.fromkeys(parts)) or None


def bb_stat(mk, p):
    """The player's number for a market from a box-score line, or None when it cannot be read."""
    kind = bb_market(mk)
    if kind is None:
        return None
    g = lambda k: p.get(k) or 0
    if kind == "fp":
        return p.get("fp")
    if kind in ("dd", "td"):
        n = sum(1 for k in ("pts", "reb", "ast", "stl", "blk") if g(k) >= 10)
        return 1 if n >= (2 if kind == "dd" else 3) else 0
    return sum(g(k) for k in kind)


# ---------- the Ledger Model (model/results/bb_model.json) ----------
def model_numbers(M, lg, e, swapped):
    """Ledger Model margin (ledger home minus away) and total for an ESPN game, or None without both ratings."""
    L = ((M or {}).get("leagues") or {}).get(lg) or {}
    R = L.get("ratings") or {}
    h, a = R.get(str((e.get("home") or {}).get("id"))), R.get(str((e.get("away") or {}).get("id")))
    if not h or not a:
        return None
    st_ = L.get("state") or {}
    m = h["r"] - a["r"] + (0.0 if e.get("neutral") else (st_.get("hfa") or 0.0))
    t = 2 * (st_.get("lg") or 0) + h["o"] + a["o"] - h["d"] - a["d"]
    return {"m": round(-m if swapped else m, 1), "t": round(t, 1), "v": (M or {}).get("updatedAt")}


def gates_of(M):
    if not M:
        return None
    g = A.gates_summary({"updatedAt": M.get("updatedAt"), "leagues": {k: v for k, v in (M.get("leagues") or {}).items() if k in LEAGUES}}, None)
    return g if g.get("leagues") else None


# ---------- the Claude Model (model/results/bb_cm.json), gated ----------
CM_VERSION = "bbcm-1"
# The basketball checklist: [id, domain, name, tier, how it is measured]. Statuses per game: measured, proxy, context
# (Claude's research), bench (market numbers, used only to compare and grade) and none (no public data; no weight).
CM_VARS = [
    ["E1", "Efficiency", "Adjusted offensive rating", "A", "Points per 100 possessions, opponent-adjusted, walk-forward"],
    ["E2", "Efficiency", "Adjusted defensive rating", "A", "Points allowed per 100 possessions vs an average offense (lower is better)"],
    ["E3", "Efficiency", "Adjusted net rating", "A", "E1 minus E2"],
    ["E4", "Efficiency", "Raw offensive rating this season", "B", "Points per 100 possessions from box scores"],
    ["E5", "Efficiency", "Raw defensive rating this season", "B", "Opponent points per 100 possessions"],
    ["P1", "Pace", "Adjusted pace", "A", "Possessions per game above or below the league, walk-forward"],
    ["P2", "Pace", "Raw pace this season", "B", "FGA - OREB + TO + 0.44 FTA per game"],
    ["P3", "Pace", "Projected possessions in this game", "A", "League pace plus both teams' pace ratings"],
    ["F1", "Four factors: offense", "Effective field-goal %", "B", "(FGM + 0.5 x 3PM) / FGA"],
    ["F2", "Four factors: offense", "Turnover rate", "B", "Turnovers per 100 possessions"],
    ["F3", "Four factors: offense", "Offensive rebound rate", "B", "OREB / (OREB + opponent DREB)"],
    ["F4", "Four factors: offense", "Free-throw rate", "B", "FTA / FGA"],
    ["G1", "Four factors: defense", "Opponent effective FG %", "B", "Allowed"],
    ["G2", "Four factors: defense", "Forced turnover rate", "B", "Opponent turnovers per 100 possessions"],
    ["G3", "Four factors: defense", "Opponent offensive rebound rate", "B", "Allowed"],
    ["G4", "Four factors: defense", "Opponent free-throw rate", "B", "Allowed"],
    ["S1", "Schedule", "Days since the last game", "B", "From the schedule"],
    ["S2", "Schedule", "Second night of a back-to-back", "A", "Fitted effect in points on tuning seasons"],
    ["S3", "Schedule", "Home court", "A", "Fitted in points on tuning seasons (0 at a neutral site)"],
    ["S4", "Schedule", "Games played this season", "C", "Sample size behind the ratings"],
    ["I1", "Players", "Players ruled out", "A", "From the injury news Claude read (named with its source on the board)"],
    ["I2", "Players", "Points lost to absences", "A", "0.15 x (points + 0.7 assists + 0.3 rebounds) per absent player, scaled by minutes"],
    ["I3", "Players", "Top scorer's points per game", "C", "This season's box scores"],
    ["I4", "Players", "Rotation size (15+ minutes a game)", "C", "This season's box scores"],
    ["M1", "Projection", "Projected points", "A", "Efficiency x projected possessions / 100"],
    ["M2", "Projection", "Projected margin", "A", "Home minus away, with home court, rest and absences"],
    ["M3", "Projection", "Win chance", "A", "Normal curve on the margin with the backtest's spread of outcomes"],
    ["B1", "Market (compare and grade only)", "Spread", "-", "DraftKings via ESPN"],
    ["B2", "Market (compare and grade only)", "Total", "-", "DraftKings via ESPN"],
    ["N1", "No public data", "Travel distance and time zones", "-", "Not in the free data"],
    ["N2", "No public data", "Referee crew tendencies", "-", "Not in the free data"],
    ["N3", "No public data", "Lineup net ratings", "-", "Not in the free data"],
    ["N4", "No public data", "Shot quality and tracking data", "-", "Not in the free data"],
    ["N5", "No public data", "Minutes restrictions and player load", "-", "Not in the free data"],
    ["N6", "No public data", "Motivation, lookahead and rivalry", "-", "Not measurable"],
]


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def fair_am(p):
    if not p or p <= 0 or p >= 1:
        return None
    return f"{'-' if p >= 0.5 else '+'}{round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)}"


def cm_league(CMd, lg):
    L = ((CMd or {}).get("leagues") or {}).get(lg) or {}
    return None if "error" in L or not L.get("teams") else L


def cm_player(L, tm, name):
    roster = ((L.get("players") or {}).get(tm) or {})
    cands = [dict(v, n=k) for k, v in roster.items()]
    return A.find_player(name, cands) if cands else None


def prop_proj(L, team_ids, abbrs, prop, pf):
    """Model projection for one prop: {mu, sd, p (chance of the prop's own side among non-pushes), side (the model's
    side), pm (chance of the model's side)} or None."""
    kind = bb_market(prop.get("mk"))
    if kind is None:
        return None
    tms = [prop.get("tm")] if prop.get("tm") else []
    tms += [t for t in abbrs if t not in tms]
    pl = None
    for t in tms:
        pl = cm_player(L, t, prop.get("pl"))
        if pl:
            tm = t
            break
    if not pl or (pl.get("g") or 0) < 3:
        return None
    f = pf.get(tm, 1.0)
    if kind == "dd" or kind == "td":
        if kind == "td":
            return None
        rate = (pl.get("dd", 0) * pl["g"] + 0.1) / (pl["g"] + 1)
        return {"mu": round(rate, 3), "p": round(rate, 4) if prop.get("side") == "Y" else None, "side": "Y", "pm": round(rate, 4)}
    comps = ("fp",) if kind == "fp" else kind
    mu = sum(0.6 * (pl.get(k) or 0) + 0.4 * (pl.get("r5_" + k) or pl.get(k) or 0) for k in comps) * f
    sd = math.sqrt(sum(max(pl.get("sd_" + k) or 0, 0.3 * (pl.get(k) or 0) + 0.8) ** 2 for k in comps))
    ln, side = prop.get("ln"), prop.get("side")
    if ln is None:
        return {"mu": round(mu, 2), "sd": round(sd, 2), "p": None}
    if float(ln).is_integer():
        over, under = 1 - phi((ln + 0.5 - mu) / sd), phi((ln - 0.5 - mu) / sd)
    else:
        over = 1 - phi((ln - mu) / sd)
        under = 1 - over
    po = over / (over + under) if over + under > 0 else 0.5
    ms = "O" if po >= 0.5 else "U"
    return {"mu": round(mu, 2), "sd": round(sd, 2), "p": round(po if side == "O" else 1 - po, 4) if side in ("O", "U") else None,
            "side": ms, "pm": round(max(po, 1 - po), 4)}


def cm_card(g, e, lg, CMd, now):
    """Claude's own card for a game (the football cm schema), or None when either team has no rating."""
    L = cm_league(CMd, lg)
    if not L or not e:
        return None, None
    T = L["teams"]
    hid, aid = str(e["home"].get("id")), str(e["away"].get("id"))
    h, a = T.get(hid), T.get(aid)
    if not h or not a:
        return None, None
    P = L.get("params") or {}
    eff, pace0 = L.get("lg_eff", 100.0), L.get("lg_pace", 70.0)
    neutral = bool(e.get("neutral"))
    hca = 0.0 if neutral else P.get("hca", 0.0)
    pc = pace0 + h.get("p", 0) + a.get("p", 0)
    day = et_day(A.when(e["date"])).isoformat() if e.get("date") else ""

    def rest(t):
        try:
            return (dt.date.fromisoformat(day) - dt.date.fromisoformat(t["last"])).days if t.get("last") else None
        except ValueError:
            return None
    rh, ra = rest(h), rest(a)
    b2b = P.get("b2b", 0.0) * ((1 if rh == 1 else 0) - (1 if ra == 1 else 0))
    oh, oa = eff + h.get("o", 0) - a.get("d", 0), eff + a.get("o", 0) - h.get("d", 0)
    # absences from Claude's injury research: a share of each absent player's production, scaled by his minutes
    abbr = {"H": e["home"].get("abbr"), "A": e["away"].get("abbr")}
    lost = {"H": 0.0, "A": 0.0}
    outs = {"H": [], "A": []}
    for name in g.get("out") or []:
        for s in ("H", "A"):
            pl = cm_player(L, abbr[s], name)
            if pl:
                lost[s] += 0.15 * ((pl.get("pts") or 0) + 0.7 * (pl.get("ast") or 0) + 0.3 * (pl.get("reb") or 0)) * min(1.0, (pl.get("min") or 0) / 30)
                outs[s].append(pl["n"])
                break
    base_m = (oh - oa) * pc / 100
    m = base_m + hca + b2b - lost["H"] + lost["A"]
    t = (oh + oa) * pc / 100 - 0.5 * (lost["H"] + lost["A"])
    sig_m = L.get("sigma_m") or 12.0
    wp = 100 * phi(m / sig_m)
    sc = {"a": int(round((t - m) / 2)), "h": int(round((t + m) / 2))}
    if sc["a"] == sc["h"]:
        sc["h" if m >= 0 else "a"] += 1
    line = g.get("line") or {}
    fav_l, sp, tot = line.get("fav"), line.get("sp"), line.get("tot")
    names = {"A": g["a"]["n"], "H": g["h"]["n"]}
    picks = {}
    gate, gt = L.get("gate") or {}, L.get("gate_total") or {}

    def cover(edge, G):
        b, a_, sc_ = G.get("b_fit"), G.get("a_fit", 0.0), G.get("scale") or 1.0
        if b is None or b <= 0:
            return 0.5
        return 1 / (1 + math.exp(-(a_ + b * edge / sc_)))
    if sp is not None and fav_l in ("A", "H"):
        line_hm = sp if fav_l == "H" else -sp
        edge = m - line_hm
        side = "H" if edge > 0 else "A"
        ph = cover(edge, gate)
        picks["spread"] = {"side": side, "ln": 0.0 if sp == 0 else (-sp if side == fav_l else sp),
                           "p": round(100 * (ph if side == "H" else 1 - ph), 1), "edge": round(abs(edge), 1)}
        mk_p = 100 * phi(line_hm / sig_m)
    else:
        mk_p = None
    ml_side = "H" if wp >= 50 else "A"
    p_ml = wp if ml_side == "H" else 100 - wp
    picks["ml"] = {"side": ml_side, "p": round(p_ml, 1), "fair": fair_am(p_ml / 100)}
    if mk_p is not None:
        picks["ml"]["mkt"] = round(mk_p if ml_side == "H" else 100 - mk_p, 1)
        picks["ml"]["value"] = p_ml - picks["ml"]["mkt"] >= 3
    if tot is not None:
        edge = t - tot
        po = cover(edge, gt)
        side = "O" if edge > 0 else "U"
        picks["total"] = {"side": side, "ln": float(tot), "p": round(100 * (po if side == "O" else 1 - po), 1), "edge": round(edge, 1)}
    # props: Claude's side and chance for every prop on the board (same order), plus projections without a line
    pf = {}
    for s, t_ in (("H", h), ("A", a)):
        tp = pace0 + t_.get("p", 0)
        pf[abbr[s]] = max(0.9, min(1.1, pc / tp)) if tp > 0 else 1.0
    cprops = []
    for p in g.get("props") or []:
        x = prop_proj(L, [hid, aid], [abbr["H"], abbr["A"]], p, pf)
        if not x or x.get("pm") is None or (p.get("side") == "Y" and x.get("side") != "Y"):
            cprops.append(None)
            continue
        side = "Y" if p.get("side") == "Y" else x["side"]
        cprops.append({"pl": p["pl"], "tm": p.get("tm"), "mk": p["mk"], "ln": p.get("ln"), "side": side,
                       "proj": round(x["mu"] * (100 if side == "Y" else 1), 1) if side == "Y" else round(x["mu"], 1),
                       "p": round(100 * x["pm"], 1), "why": f"Season and last-five average, pace-adjusted ({x['mu']:.1f})" if side != "Y" else "Share of games with a double-double",
                       "src": "Claude Model from ESPN box scores"})
    proj = []
    for s in ("A", "H"):
        roster = ((L.get("players") or {}).get(abbr[s]) or {})
        top = sorted(((v.get("min") or 0, k, v) for k, v in roster.items() if (v.get("g") or 0) >= 3 and k not in outs[s]), reverse=True)[:2]
        for _, k, v in top:
            mu = (0.6 * (v.get("pts") or 0) + 0.4 * (v.get("r5_pts") or v.get("pts") or 0)) * pf.get(abbr[s], 1.0)
            sd = max(v.get("sd_pts") or 0, 0.3 * (v.get("pts") or 0) + 0.8)
            proj.append({"pl": k, "tm": abbr[s], "mk": "Points", "proj": round(mu, 1), "lo": round(max(0, mu - 0.674 * sd), 1), "hi": round(mu + 0.674 * sd, 1)})
    cand = [(abs(v["p"] - 50), k) for k, v in picks.items() if k in ("spread", "total") and v.get("p") is not None]
    cand += [(abs(x["p"] - 50), "prop") for x in cprops[:1] if x]
    best = max(cand)[1] if cand else "ml"
    if best == "prop":
        i0 = next(i for i, x in enumerate(cprops) if x)
        if i0 != 0:
            best = "spread" if "spread" in picks else "ml"
    passed = bool(gate.get("pass"))
    conf = 2 if passed and best == "spread" and picks.get("spread", {}).get("edge", 0) >= (gate.get("thr") or 99) else 1
    winner = "H" if m >= 0 else "A"
    why = (f"Claude projects {names[winner]} by {abs(m):.1f} and {t:.0f} points at {pc:.0f} possessions"
           + (f"; {', '.join(outs['H'] + outs['A'])} out" if outs["H"] or outs["A"] else "") + ".")
    if not passed:
        why += " Not validated: its backtest has not beaten the closing line, so this is for comparison only."
    drivers = [{"k": "Offense vs defense", "e": round(base_m, 1)}, {"k": "Home court" if not neutral else "Neutral site", "e": round(hca, 1)}]
    if b2b:
        drivers.append({"k": "Back-to-back", "e": round(b2b, 1)})
    if lost["H"] or lost["A"]:
        drivers.append({"k": "Absences", "e": round(lost["A"] - lost["H"], 1)})
    ff = lambda t_, side, k: ((t_.get("ff") or {}).get(side) or {}).get(k)
    rows = []

    def row(i, va, vh, status, edge=None, note=None):
        rows.append([i, va, vh, edge, status, note] if note else [i, va, vh, edge, status])
    sg = lambda v: None if v is None else (f"{names['H' if v > 0 else 'A']} +{abs(v):.1f}" if v else "even")
    row("E1", round(eff + a.get("o", 0), 1), round(eff + h.get("o", 0), 1), "measured")
    row("E2", round(eff - a.get("d", 0), 1), round(eff - h.get("d", 0), 1), "measured")
    row("E3", round(a.get("o", 0) + a.get("d", 0), 1), round(h.get("o", 0) + h.get("d", 0), 1), "measured", sg(round(base_m, 1)))
    row("E4", ff(a, "off", "rtg"), ff(h, "off", "rtg"), "measured" if ff(h, "off", "rtg") else "none")
    row("E5", ff(a, "def", "rtg"), ff(h, "def", "rtg"), "measured" if ff(h, "def", "rtg") else "none")
    row("P1", round(a.get("p", 0), 1), round(h.get("p", 0), 1), "measured")
    row("P2", (a.get("ff") or {}).get("pace"), (h.get("ff") or {}).get("pace"), "measured" if (h.get("ff") or {}).get("pace") else "none")
    row("P3", round(pc, 1), round(pc, 1), "measured")
    for vid, side, k in (("F1", "off", "efg"), ("F2", "off", "tov"), ("F3", "off", "orb"), ("F4", "off", "ftr"),
                         ("G1", "def", "efg"), ("G2", "def", "tov"), ("G3", "def", "orb"), ("G4", "def", "ftr")):
        va, vh = ff(a, side, k), ff(h, side, k)
        row(vid, va, vh, "measured" if va is not None and vh is not None else "none")
    row("S1", ra, rh, "measured" if ra is not None or rh is not None else "none")
    row("S2", ra == 1, rh == 1, "measured", sg(round(b2b, 1)) if b2b else None)
    row("S3", 0.0, round(hca, 1), "measured", sg(round(hca, 1)) if hca else None)
    row("S4", (a.get("ff") or {}).get("g"), (h.get("ff") or {}).get("g"), "measured")
    row("I1", ", ".join(outs["A"]) or "none found", ", ".join(outs["H"]) or "none found", "context" if g.get("out") else "none")
    row("I2", round(lost["A"], 1), round(lost["H"], 1), "proxy" if g.get("out") else "none", sg(round(lost["A"] - lost["H"], 1)) if lost["A"] or lost["H"] else None)
    for s, t_ in (("A", a), ("H", h)):
        pass
    roster = lambda s: ((L.get("players") or {}).get(abbr[s]) or {})
    topsc = lambda s: max([v.get("pts") or 0 for v in roster(s).values() if (v.get("g") or 0) >= 3] or [None]) if roster(s) else None
    rot = lambda s: sum(1 for v in roster(s).values() if (v.get("min") or 0) >= 15 and (v.get("g") or 0) >= 3) if roster(s) else None
    row("I3", topsc("A"), topsc("H"), "measured" if roster("H") and roster("A") else "none")
    row("I4", rot("A"), rot("H"), "measured" if roster("H") and roster("A") else "none")
    row("M1", sc["a"], sc["h"], "measured")
    row("M2", round(-m, 1), round(m, 1), "measured", sg(round(m, 1)))
    row("M3", round(100 - wp, 1), round(wp, 1), "measured")
    row("B1", None, (f"{names[fav_l]} -{sp:g}" if sp else "pick'em") if sp is not None and fav_l in ("A", "H") else None, "bench")
    row("B2", None, tot, "bench")
    for vid in ("N1", "N2", "N3", "N4", "N5", "N6"):
        row(vid, None, None, "none")
    cov = {"total": len(CM_VARS)}
    for r in rows:
        cov[r[4]] = cov.get(r[4], 0) + 1
    card = {"v": CM_VERSION, "at": now.astimezone(UTC).isoformat(timespec="seconds"), "tracked": True, "sc": sc,
            "hm": round(m, 1), "wp": round(wp, 1), "tot": round(t, 1),
            "fair": {"fav": "H" if m >= 0 else "A", "sp": round(abs(m) * 2) / 2, "tot": round(t * 2) / 2,
                     "mlA": fair_am(1 - wp / 100), "mlH": fair_am(wp / 100)},
            "mkt": {"fav": fav_l, "sp": sp, "tot": tot}, "picks": picks, "best": best, "conf": conf, "why": why,
            "drivers": drivers, "props": cprops, "proj": proj, "cov": cov}
    return card, rows


def cm_grade(g, cm, sc, pv, players):
    """Grade a Claude Model card from the final score. Returns (cmgr, cmpv, cmpa, results row)."""
    pk = cm.get("picks") or {}
    side_sc = {"A": sc["a"], "H": sc["h"]}
    other = {"A": "H", "H": "A"}
    gr = {}
    s = pk.get("spread")
    if s and s.get("side") in side_sc and s.get("ln") is not None:
        gr["spread"] = A.grade_game({"lg": g["lg"], "line": cm.get("mkt") or {}, "lean": {"mk": "spread", "side": s["side"], "ln": s["ln"]}}, sc["a"], sc["h"])[1]
    t = pk.get("total")
    if t and t.get("ln") is not None:
        gr["total"] = A.wl((sc["a"] + sc["h"] - t["ln"]) * (1 if t["side"] == "O" else -1))
    ml = pk.get("ml")
    if ml and ml.get("side") in side_sc:
        gr["ml"] = A.wl(side_sc[ml["side"]] - side_sc[other[ml["side"]]])
    if isinstance(cm.get("hm"), (int, float)) and cm["hm"]:
        gr["su"] = A.wl((sc["h"] - sc["a"]) * (1 if cm["hm"] > 0 else -1))
    props = cm.get("props") or []
    cmpv, pgr = [], []
    for i, p in enumerate(props):
        v = pv[i] if i < len(pv) else None
        cmpv.append(v)
        pgr.append(A.grade_ou(v, p.get("ln"), p.get("side")) if p and v is not None else None)
    gr["props"] = pgr
    best = cm.get("best")
    gr["best"] = gr.get(best) if best in ("spread", "total", "ml") else (pgr[0] if best == "prop" and pgr else None)
    cmpa = []
    for q in cm.get("proj") or []:
        pl = A.find_player(q.get("pl"), players) if players else None
        cmpa.append(bb_stat(q.get("mk"), pl) if pl else None)
    cnt = {"W": 0, "L": 0, "P": 0}
    for r in pgr:
        if r in cnt:
            cnt[r] += 1
    row = {"lg": g["lg"], "best": gr.get("best"), "spread": gr.get("spread"), "ml": gr.get("ml"), "total": gr.get("total"),
           "su": gr.get("su"), "props": cnt}
    if isinstance(cm.get("hm"), (int, float)):
        row["mae"] = round(abs((sc["h"] - sc["a"]) - cm["hm"]), 1)
    if isinstance(cm.get("tot"), (int, float)):
        row["tae"] = round(abs((sc["h"] + sc["a"]) - cm["tot"]), 1)
    return gr, cmpv, cmpa, row


def cm_model_doc(CMd):
    """meta/bbcmmodel for the page: each league's gate and out-of-sample backtest."""
    if not CMd:
        return None
    gate, bt, notes = {}, {}, []
    for lg, L in (CMd.get("leagues") or {}).items():
        if "error" in L:
            notes.append(f"{lg}: not run ({L['error'][:120]})")
            continue
        G, b = L.get("gate") or {}, L.get("backtest") or {}
        gate[lg] = {"pass": bool(G.get("pass")), "why": G.get("why"), "ats": b.get("ats"), "n": b.get("atsN"), "t": G.get("t"), "thr": G.get("thr")}
        bt[lg] = b
        if L.get("stale"):
            notes.append(f"{lg}: the last run failed, so the previous numbers are shown")
    notes.append("Gated: until a league's spread gate passes, its picks are for comparison only and move no chance.")
    return {"version": CMd.get("version"), "updatedAt": CMd.get("updatedAt"), "gate": gate, "backtest": bt, "notes": notes}


def match_bb(g, pool):
    """Like the football matcher, restricted to the game's own league (men's and women's teams share names)."""
    return A.match(dict(g, lg="_" + g["lg"]), [e for e in pool if e.get("lg") == g["lg"]])


def grade_docs(docs_by_slate, pre_old, feed, now, now_iso, pool_cache, opts=None):
    """Lock, live-update and grade. Returns (league changes {(slate, lg): {gid: ch}}, pregame {slate: body parts},
    results {slate: {gid: row}}, counters). opts: box (URL|dir of box/bb), M (Ledger Model results)."""
    opts = opts or {}
    M, box_src = opts.get("M"), opts.get("box")
    changes, pregame, results, cmres = {}, {}, {}, {}
    n = {"upd": 0, "graded": 0, "lock": 0, "ver": 0, "late": 0, "unmatched": [], "void": 0, "gaps": 0, "cm": 0}
    box_cache = {}

    def box(d):
        if d not in box_cache:
            box_cache[d] = {}
            if box_src:
                src = f"{box_src}/{d.isoformat()}.json" if re.match(r"https?://", box_src) else os.path.join(box_src, f"{d.isoformat()}.json")
                try:
                    box_cache[d] = (A.load(src) or {}).get("games") or {}
                except Exception as ex:
                    print(f"box {d}: {ex}", file=sys.stderr)
        return box_cache[d]

    for slate, lgdocs in sorted(docs_by_slate.items()):
        d0 = dt.date.fromisoformat(slate)
        pool, by_id = [], {}
        for d in (d0 - dt.timedelta(days=1), d0, d0 + dt.timedelta(days=1)):
            if d not in pool_cache:
                pool_cache[d] = list((load_day(feed, d).get("games") or {}).values())
            for e in pool_cache[d]:
                if e["id"] not in by_id:
                    by_id[e["id"]] = e
                    pool.append(e)
        old_pg = pre_old.get(slate) or {}
        pg = pregame.setdefault(slate, {"games": {}, "snaps": {}, "preds": {}})
        for lg, doc in lgdocs.items():
            for gid, g in (doc.get("games") or {}).items():
                ko = A.when(g.get("ko"))
                if not ko or finished(g):
                    continue
                e = by_id.get(g.get("eid")) if g.get("eid") else None
                swapped = bool(g.get("eswap"))
                if not e:
                    e, swapped = match_bb(g, pool)
                old_snaps = list(((old_pg.get("snaps") or {}).get(gid)) or [])
                old_preds = list(((old_pg.get("preds") or {}).get(gid)) or [])
                if ko > now:
                    snap = snap_of(g)
                    last = {k: v for k, v in (old_snaps[-1] if old_snaps else {}).items() if k not in ("at", "h")}
                    if snap != last:
                        pg["snaps"][gid] = old_snaps + [dict(snap, at=now_iso, h=A.chain(old_snaps[-1].get("h") if old_snaps else None, snap, now_iso))]
                old_ent = (old_pg.get("games") or {}).get(gid) or {}
                if e and (e.get("quotes") or e.get("qopen") or e.get("qclose") or (ko > now and M)):
                    ent = {"state": e.get("state"), "quotes": e.get("quotes") if e.get("state") == "pre" else None,
                           "open": e.get("qopen"), "close": e.get("qclose")}
                    if swapped:
                        ent = {k: A.swap_quotes(v) if k != "state" else v for k, v in ent.items()}
                    if ko > now and M:
                        close_e = e.get("close")
                        if close_e and swapped:
                            close_e = dict(close_e, hm=-close_e["hm"] if close_e.get("hm") is not None else None)
                        hm, tot = A.market_numbers(ent.get("quotes") or ent.get("close") or ent.get("open"), close_e, g.get("line"))
                        push = ((M.get("leagues") or {}).get(lg) or {}).get("push") or {}
                        pm = {"m": A.pmf_row(push.get("margin"), hm), "t": A.pmf_row(push.get("total"), tot)}
                        if pm["m"] or pm["t"]:
                            ent["pmf"] = pm
                        lm = model_numbers(M, lg, e, swapped)
                        if lm:
                            ent["lm"] = lm
                            L_ = (M.get("leagues") or {}).get(lg) or {}
                            rec = {"model": "bb-ledger-v1", "version": M.get("updatedAt"), "asof": (L_.get("state") or {}).get("asof"),
                                   "m": lm["m"], "t": lm["t"], "pp": None}
                            prev = next((x for x in reversed(old_preds) if x.get("model") == rec["model"]), None)
                            if {k: (prev or {}).get(k) for k in A.PRED_KEYS} != rec:
                                pg["preds"][gid] = old_preds + [dict(rec, at=now_iso, h=A.chain(old_preds[-1].get("h") if old_preds else None, rec, now_iso))]
                    else:
                        for k in ("pmf", "lm"):
                            if old_ent.get(k) is not None:
                                ent[k] = old_ent[k]
                    if old_ent != ent:
                        pg["games"][gid] = ent
                if ko > now + dt.timedelta(hours=1):
                    continue
                if not e:
                    if ko < now - dt.timedelta(minutes=20):
                        n["unmatched"].append(f"{g['a']['n']} @ {g['h']['n']}")
                    continue
                ea, eh = (e["home"], e["away"]) if swapped else (e["away"], e["home"])
                ch = {}
                if g.get("eid") != e["id"]:
                    ch["eid"] = e["id"]
                    if swapped:
                        ch["eswap"] = True
                state = e.get("state")
                lock = g.get("lock")
                if not lock and g.get("tracked") and (ko <= now or state in ("in", "post")):
                    late = now - ko > dt.timedelta(hours=12)
                    before = [x for x in old_snaps if A.when(x.get("at")) and A.when(x["at"]) < ko]
                    if before:
                        lock = dict({k: before[-1].get(k) for k in SNAP_KEYS}, at=now_iso, src="last version saved before tip",
                                    pre=before[-1]["at"], v=True, h=before[-1].get("h"), chain_ok=A.verify_chain(before, SNAP_KEYS))
                        n["ver"] += 1
                    else:
                        lock = dict(snap_of(g), at=now_iso, v=False,
                                    src="back-filled after the game (no copy saved before tip)" if late else "no copy saved before tip; as found at the first update after it")
                    if late:
                        lock["late"] = True
                        n["late"] += 1
                    # the model's last prediction made before tip from data that ended before tip; none = unavailable
                    okp = [x for x in old_preds if A.when(x.get("at")) and A.when(x["at"]) < ko and (not x.get("asof") or A.when_any(x["asof"]) < ko)]
                    if okp and okp[-1].get("m") is not None:
                        lp = okp[-1]
                        lock["lm"] = {"m": lp["m"], "t": lp.get("t"), "at": lp["at"], "version": lp.get("version"), "model": lp.get("model")}
                    ch["lock"] = lock
                    n["lock"] += 1
                if lock:
                    diff = lock_diff(g, lock)
                    if diff != (g.get("lockdiff") or []):
                        ch["lockdiff"] = diff
                gl = dict(g, line=dict(g.get("line") or {}, **(lock["line"] or {})), lean=lock["lean"], star=lock["star"]) if lock else g
                lprops = (lock.get("props") if lock else None)
                if lprops is None:
                    lprops = [{k: p.get(k) for k in ("pl", "mk", "ln", "side")} for p in (g.get("props") or [])]
                if state in ("in", "post") and g.get("tracked") and (g.get("clv") is None or ("p" not in (g.get("clv") or {}) and not g.get("clvp"))):
                    cv = g.get("clv") or (A.clv_of(gl, e.get("close"), swapped) if not (lock or {}).get("late") else None)
                    pre_ent = pg["games"].get(gid) or old_ent
                    cbooks = e.get("qclose")
                    if cbooks and swapped:
                        cbooks = A.swap_quotes(cbooks)
                    cc = A.closing_chance(gl, cbooks, pre_ent.get("pmf")) if not (lock or {}).get("late") else None
                    if cc is not None:
                        cv = dict(cv or {}, p=cc["p"], push=cc["push"])
                    if cv is not None and cv != g.get("clv"):
                        ch["clv"] = cv
                if state == "pre" or ea.get("score") is None or eh.get("score") is None:
                    if e.get("status") in DEAD + ("STATUS_DELAYED",) and g.get("clk") != e.get("detail"):
                        ch["clk"] = e.get("detail")
                    if ch:
                        changes.setdefault((slate, lg), {})[gid] = ch
                    continue
                sc = {"a": ea["score"], "h": eh["score"]}
                if state == "in":
                    clk = e.get("detail") or "Live"
                    if g.get("st") != "live" or g.get("sc") != sc or g.get("clk") != clk:
                        ch.update({"st": "live", "sc": sc, "clk": clk})
                        n["upd"] += 1
                elif state == "post" and e.get("completed"):
                    if g.get("st") != "final" or g.get("sc") != sc:
                        ch.update({"st": "final", "sc": sc, "clk": None})
                        n["upd"] += 1
                    la, lh = ea.get("lines") or [], eh.get("lines") or []
                    if len(la) >= 2 and len(la) == len(lh) and None not in la + lh:
                        per = {"a": la, "h": lh}
                        if g.get("per") != per:
                            ch["per"] = per
                    # props: actual values from the robot's box score, graded from the locked props
                    props = g.get("props") or []
                    gr = g.get("gr") or {}
                    pv = list(g.get("pv") or [None] * len(props))
                    pv += [None] * (len(props) - len(pv))
                    pgr = list(gr.get("props") or [None] * len(props))
                    pgr += [None] * (len(props) - len(pgr))
                    players = None
                    if props or (g.get("cm") or {}).get("proj"):
                        bx = box(et_day(ko)).get(e["id"]) or box(et_day(A.when(e["date"]))).get(e["id"])
                        players = (bx or {}).get("players")
                    for i, p in enumerate(props):
                        if i >= len(lprops):                  # added after the lock: shown, never graded
                            pgr[i] = "X"
                            continue
                        lp = lprops[i]
                        pl = A.find_player(lp.get("pl"), players) if players else None
                        if pl:
                            v = bb_stat(lp.get("mk"), pl)
                            if v is not None:
                                pv[i] = v
                        if pgr[i] is None:
                            pgr[i] = A.grade_ou(pv[i], lp.get("ln"), lp.get("side"))
                        if pgr[i] is None and players and now - ko > A.VOID_AFTER:
                            pgr[i] = "U"                     # not in the settled box score (did not play) or not gradable
                            n["void"] += 1
                        if pgr[i] is None:
                            n["gaps"] += 1
                    if props and pv != (g.get("pv") or []):
                        ch["pv"] = pv
                    if g.get("tracked"):
                        su, lean, star = A.grade_game(gl, sc["a"], sc["h"])
                        new_gr = {"su": su, "lean": lean, "star": star, "props": pgr}
                        if gr != new_gr:
                            ch["gr"] = new_gr
                            if not g.get("gr"):
                                n["graded"] += 1
                            l = gl.get("lean") or {}
                            cnt = {"W": 0, "L": 0, "P": 0, "U": 0}
                            for r in pgr:
                                if r in cnt:
                                    cnt[r] += 1
                            row = {"lg": g["lg"], "su": su, "lean": lean, "star": star, "conf": l.get("conf"), "mk": l.get("mk"),
                                   "props": cnt, "locked": bool(lock), "v": bool((lock or {}).get("v"))}
                            cv = ch.get("clv") or g.get("clv")
                            if cv:
                                if cv.get("pts") is not None:
                                    row["clv"] = cv["pts"]
                                if cv.get("p") is not None:
                                    row["cp"] = cv["p"]
                            if lock and lock.get("lm"):
                                cl = e.get("close") or {}
                                chm = cl.get("hm")
                                if chm is not None and swapped:
                                    chm = -chm
                                mr = A.model_result(lock["lm"], chm, cl.get("tot"), sc)
                                if mr:
                                    row["lme"] = mr
                            if lock and lock.get("pp"):
                                rows = [[pp_, 1 if pgr[i] == "W" else 0, A.implied_from_text((lprops[i] if i < len(lprops) else {}).get("odds"))]
                                        for i, pp_ in enumerate(lock["pp"]) if pp_ is not None and i < len(pgr) and pgr[i] in ("W", "L")]
                                if rows:
                                    row["pp"] = rows
                            results.setdefault(slate, {})[gid] = row
                    cm = g.get("cm")
                    if cm and cm.get("tracked") is not False:
                        cgr, cpv, cpa, crow = cm_grade(g, cm, sc, pv, players)
                        if cgr != g.get("cmgr") or cpv != (g.get("cmpv") or []) or cpa != (g.get("cmpa") or []):
                            if not g.get("cmgr"):
                                n["cm"] += 1
                            ch.update({"cmgr": cgr, "cmpv": cpv, "cmpa": cpa})
                            cmres.setdefault(slate, {})[gid] = crow
                elif g.get("clk") != e.get("detail"):
                    ch["clk"] = e.get("detail")
                if ch:
                    changes.setdefault((slate, lg), {})[gid] = ch
    return changes, pregame, results, n, cmres


# ---------- config ----------
def slate_label(d):
    return d.strftime("%a %b ") + str(d.day)


def config_update(cfg, slate, leagues, now_iso):
    cfg = json.loads(json.dumps(cfg or {}))
    slates = cfg.get("slates") or []
    changed = False
    if slate not in [s.get("id") for s in slates]:
        d = dt.date.fromisoformat(slate)
        slates.append({"id": slate, "label": slate_label(d), "start": slate, "end": slate})
        slates.sort(key=lambda s: s["id"])
        changed = True
    if (cfg.get("current") or "") < slate:
        cfg["current"] = slate
        changed = True
    if cfg.get("leagues") != list(leagues):
        cfg["leagues"] = list(leagues)
        changed = True
    for k, v in (("season", "2026-27"), ("tz", "America/New_York"), ("sport", "basketball"),
                 ("notes", "Picks lock at tip. Only games with tracked=true are graded. One board per US Eastern date.")):
        if k not in cfg:
            cfg[k] = v
            changed = True
    cfg["slates"] = slates
    if changed:
        cfg["updatedAt"] = now_iso
    return cfg, changed


# ---------- io ----------
def write(out, name, obj):
    p = os.path.join(out, name)
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    return p


def read_docs(docs_dir):
    lg_docs, pre, res = {}, {}, {}
    for path in sorted(glob.glob(os.path.join(docs_dir, "bbslates", "*", "leagues", "*.json"))):
        slate, lg = path.split(os.sep)[-3], os.path.basename(path)[:-5]
        d = A.load(path) or {}
        lg_docs.setdefault(slate, {})[lg] = d.get("data", d) if "games" not in d and isinstance(d.get("data"), dict) else d
    for path in glob.glob(os.path.join(docs_dir, "bbpregame", "*.json")):
        d = A.load(path) or {}
        pre[os.path.basename(path)[:-5]] = d.get("data", d) if "games" not in d and isinstance(d.get("data"), dict) else d
    for path in glob.glob(os.path.join(docs_dir, "bbresults", "*.json")):
        res[os.path.basename(path)[:-5]] = True
    for path in glob.glob(os.path.join(docs_dir, "bbcmresults", "*.json")):
        res["cm:" + os.path.basename(path)[:-5]] = True
    cfg_path = os.path.join(docs_dir, "meta", "bbconfig.json")
    cfg = A.load(cfg_path) if os.path.exists(cfg_path) else None
    if cfg and "slates" not in cfg and isinstance(cfg.get("data"), dict):
        cfg = cfg["data"]
    return lg_docs, pre, res, cfg


def list_games(feed, slate, leagues, now, board=None):
    """Games with a spread for the date; with the day's board docs, also what research each game already has."""
    d = dt.date.fromisoformat(slate)
    rows = []
    on_board = {g.get("eid"): g for doc in (board or {}).values() for g in (doc.get("games") or {}).values()}
    for e in (load_day(feed, d).get("games") or {}).values():
        if e.get("lg") not in leagues or e.get("status") in DEAD:
            continue
        fav, sp, tot, _ = book_line(e)
        if sp is None:
            continue
        ko = A.when(e["date"])
        bpi = bpi_entry(e, e["lg"])
        rows.append({"id": game_id(e["lg"], e), "lg": e["lg"], "tip": ko.astimezone(ET).strftime("%-I:%M %p"),
                     "away": team_name(e["away"]), "home": team_name(e["home"]),
                     "ranks": [e["away"].get("rank"), e["home"].get("rank")],
                     "line": f"{team_name(e['home'] if fav == 'H' else e['away'])} {fmt_ln(-sp)}", "tot": tot,
                     "bpi": f"{bpi['fav']} {bpi['p']}%" if bpi else None, "started": ko - now <= LOCK_BEFORE, "_ko": ko})
        b = on_board.get(e["id"])
        if b:
            rows[-1].update({"id": b["id"], "proj": sum(1 for x in b.get("model") or [] if x.get("src") != "ESPN BPI"),
                             "experts": len(b.get("experts") or []), "props": len(b.get("props") or []),
                             "conf": (b.get("lean") or {}).get("conf"), "star": bool(b.get("star"))})
    rows.sort(key=lambda r: (LEAGUES.index(r["lg"]), r.pop("_ko")))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs")
    ap.add_argument("--out")
    ap.add_argument("--slate", help="US Eastern date to build (YYYY-MM-DD)")
    ap.add_argument("--ctx", help="Claude's research for the slate (JSON)")
    ap.add_argument("--feed", default=RAW)
    ap.add_argument("--now")
    ap.add_argument("--leagues", default="NBA,WNBA,MBB,WBB")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--grade-only", action="store_true")
    ap.add_argument("--box", help="player box scores, box/bb (URL or dir); default: the robot's, when --feed is the robot's")
    ap.add_argument("--model", help="Ledger Model results, bb_model.json (URL or path); default: the robot's")
    ap.add_argument("--cm", help="Claude Model numbers, bb_cm.json (URL or path); default: the robot's")
    args = ap.parse_args(argv)
    robot = args.feed == RAW
    box_src = args.box or (f"{ROOT_URL}/box/bb" if robot else None)

    def opt_load(src, name):
        if not src:
            return None
        try:
            return A.load(src)
        except Exception as ex:
            print(f"{name} unavailable: {ex}", file=sys.stderr)
            return None
    M = opt_load(args.model or (f"{ROOT_URL}/model/results/bb_model.json" if robot else None), "Ledger Model results")
    CM = opt_load(args.cm or (f"{ROOT_URL}/model/results/bb_cm.json" if robot else None), "Claude Model numbers")
    now = A.when(args.now) if args.now else dt.datetime.now(UTC)
    now_iso = now.astimezone(UTC).isoformat(timespec="seconds")
    leagues = tuple(x for x in args.leagues.split(",") if x in LEAGUES)
    if args.list:
        board = read_docs(args.docs)[0].get(args.slate) if args.docs else None
        for r in list_games(args.feed, args.slate, leagues, now, board):
            print(json.dumps(r))
        return
    os.makedirs(args.out, exist_ok=True)
    status = load_status(args.feed)
    lg_docs, pre_old, res_exist, cfg = read_docs(args.docs)
    existed = {(s, lg) for s, v in lg_docs.items() for lg in v}
    manifest, notes = [], []

    built, cm_rows = {}, {}
    if args.slate and not args.grade_only:
        day = load_day(args.feed, dt.date.fromisoformat(args.slate))
        ctx = A.load(args.ctx) if args.ctx and os.path.exists(args.ctx) else {}
        built, n_new = build(dt.date.fromisoformat(args.slate), day, lg_docs.get(args.slate) or {}, ctx or {}, now, leagues, CM, cm_rows)
        for lg, games in built.items():
            doc = lg_docs.setdefault(args.slate, {}).setdefault(lg, {"slate": args.slate, "league": lg, "games": {}})
            for gid, ch in games.items():
                doc["games"][gid] = dict(doc["games"].get(gid) or {}, **ch)
        notes.append(f"board {args.slate}: {sum(len(v) for v in built.values())} game(s) written ({n_new} new)")
        active = list((cfg or {}).get("leagues") or ["NBA", "MBB", "WBB"])
        active += [lg for lg in LEAGUES if lg in built and lg not in active]
        cfg2, changed = config_update(cfg, args.slate, sorted(active, key=LEAGUES.index), now_iso)
        if changed:
            f = write(args.out, "bbconfig.json", cfg2)
            manifest.append({"op": "set", "collection": "meta", "doc_id": "bbconfig", "file_path": f})

    changes, pregame, results, n, cmres = grade_docs(lg_docs, pre_old, args.feed, now, now_iso, {}, {"box": box_src, "M": M})

    # league docs: one write each, build and grading merged
    keys = set(changes) | {(args.slate, lg) for lg in built}
    for slate, lg in sorted(keys):
        games = {}
        for gid, ch in (built.get(lg) or {}).items() if slate == args.slate else []:
            games[gid] = dict(ch)
        for gid, ch in (changes.get((slate, lg)) or {}).items():
            games[gid] = dict(games.get(gid) or {}, **ch)
        if not games:
            continue
        if (slate, lg) in existed:
            body = {"games": games, "liveAt": now_iso}
            if slate == args.slate and lg in built:
                body.update({"updatedAt": now_iso, "complete": True})
            f = write(args.out, f"{slate}__{lg}.json", body)
            manifest.append({"op": "update", "collection": f"bbslates/{slate}/leagues", "doc_id": lg, "file_path": f})
        else:
            full = lg_docs[slate][lg]
            body = {"slate": slate, "league": lg, "updatedAt": now_iso, "liveAt": now_iso, "complete": True, "games": full["games"]}
            f = write(args.out, f"{slate}__{lg}.json", body)
            manifest.append({"op": "set", "collection": f"bbslates/{slate}/leagues", "doc_id": lg, "file_path": f})

    gates = gates_of(M)
    for slate, pg in sorted(pregame.items()):
        old = pre_old.get(slate) or {}
        extra = {}
        # price coverage of the day's tracked games (phase gate) and the model gates the page reads
        tracked = priced = 0
        all_ent = dict(old.get("games") or {})
        all_ent.update(pg["games"])
        for doc in (lg_docs.get(slate) or {}).values():
            for gid, g in (doc.get("games") or {}).items():
                if g.get("tracked"):
                    tracked += 1
                    en = all_ent.get(gid) or {}
                    if en.get("close") or en.get("quotes"):
                        priced += 1
        cov = {"tracked": tracked, "priced": priced}
        if cov != old.get("coverage"):
            extra["coverage"] = cov
        if gates and gates != old.get("gates"):
            extra["gates"] = gates
        if not pg["games"] and not pg["snaps"] and not pg.get("preds") and not extra:
            continue
        exists = slate in pre_old
        body = dict({"slate": slate, "updatedAt": now_iso, "feedCheckedAt": status.get("checkedAt"), "games": pg["games"], "snaps": pg["snaps"]}, **extra)
        if pg.get("preds"):
            body["preds"] = pg["preds"]
        f = write(args.out, f"{slate}__pregame.json", body)
        manifest.append({"op": "update" if exists else "set", "collection": "bbpregame", "doc_id": slate, "file_path": f})

    for slate, rows in sorted(results.items()):
        body = {"slate": slate, "label": slate_label(dt.date.fromisoformat(slate)), "updatedAt": now_iso, "games": rows}
        f = write(args.out, f"{slate}__results.json", body)
        manifest.append({"op": "update" if slate in res_exist else "set", "collection": "bbresults", "doc_id": slate, "file_path": f})

    for slate, rows in sorted(cmres.items()):
        body = {"slate": slate, "label": slate_label(dt.date.fromisoformat(slate)), "updatedAt": now_iso, "games": rows}
        f = write(args.out, f"{slate}__cmresults.json", body)
        manifest.append({"op": "update" if "cm:" + slate in res_exist else "set", "collection": "bbcmresults", "doc_id": slate, "file_path": f})

    # the Claude Model's checklist rows (one doc per league and day), its model card and the checklist itself
    for lg, rows in sorted(cm_rows.items()):
        p = os.path.join(args.docs, "bbcmvars", args.slate, "leagues", f"{lg}.json")
        old = A.load(p) if os.path.exists(p) else None
        old = (old or {}).get("data", old) if old and "games" not in old else old
        games = dict((old or {}).get("games") or {})
        games.update(rows)
        if games != (old or {}).get("games"):
            f = write(args.out, f"{args.slate}__cmvars_{lg}.json", {"slate": args.slate, "league": lg, "updatedAt": now_iso, "games": games})
            manifest.append({"op": "set", "collection": f"bbcmvars/{args.slate}/leagues", "doc_id": lg, "file_path": f})
    if CM:
        for name, body in (("bbcmmodel", cm_model_doc(CM)), ("bbcmvars", {"version": CM_VERSION, "vars": CM_VARS})):
            p = os.path.join(args.docs, "meta", f"{name}.json")
            old = A.load(p) if os.path.exists(p) else None
            old = (old or {}).get("data", old) if old and "version" not in old else old
            if body and body != old:
                f = write(args.out, f"{name}.json", body)
                manifest.append({"op": "set", "collection": "meta", "doc_id": name, "file_path": f})

    bits = notes + [f"{n['upd']} game{'s' if n['upd'] != 1 else ''} updated", f"{n['graded']} graded"]
    if n["lock"]:
        bits.append(f"{n['lock']} pick{'s' if n['lock'] != 1 else ''} locked, {n['ver']} from a copy saved before tip"
                    + (f" ({n['late']} back-filled)" if n["late"] else ""))
    if n["unmatched"]:
        bits.append(f"{len(n['unmatched'])} not found in the feed")
    if n["cm"]:
        bits.append(f"{n['cm']} Claude Model card{'s' if n['cm'] != 1 else ''} graded")
    if n["gaps"]:
        bits.append(f"{n['gaps']} prop{'s' if n['gaps'] != 1 else ''} waiting on box scores")
    if n["void"]:
        bits.append(f"{n['void']} prop{'s' if n['void'] != 1 else ''} unresolved (player or market not in the box score)")
    ck = status.get("checkedAt")
    if ck and (now - A.when(ck)).total_seconds() > 45 * 60:
        bits.append(f"basketball feed last checked {int((now - A.when(ck)).total_seconds() // 60)} min ago")
    if not ck:
        bits.append("basketball feed unreachable")
    rest = bits[len(notes):]
    note = "; ".join(notes + [", ".join(rest)])
    live = {"ranAt": now_iso, "updated": n["upd"], "graded": n["graded"], "note": note[:1].upper() + note[1:] + "."}
    if n["unmatched"]:
        live["unmatched"] = n["unmatched"][:20]
    f = write(args.out, "bblive.json", live)
    manifest.append({"op": "set", "collection": "meta", "doc_id": "bblive", "file_path": f})
    print(json.dumps(manifest))
    print(live["note"], file=sys.stderr)


if __name__ == "__main__":
    main()
