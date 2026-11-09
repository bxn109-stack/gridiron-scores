#!/usr/bin/env python3
"""Gridiron Ledger basketball: build the day's board from the robot's feed, then lock and grade the picks.

Run by Claude's basketball scheduled tasks, not by GitHub. Standard library only. It borrows the football grader's
team matching, grading, pick-lock and hash-chain helpers from apply_scores.py (which must sit next to this file) and
never reads or writes a football document.

  python3 bb_ledger.py --docs DIR --out OUT --slate YYYY-MM-DD [--ctx ctx.json] [--feed URL|DIR] [--now ISO]
  python3 bb_ledger.py --list --slate YYYY-MM-DD [--feed URL|DIR]     # tonight's games with a line, one per line
  python3 bb_ledger.py --docs DIR --out OUT --grade-only               # lock and grade only, no board build

DIR holds the docs saved by ArtifactData (out_dir):
  DIR/meta/bbconfig.json, DIR/bbslates/<date>/leagues/<NBA|WNBA|MBB|WBB>.json, DIR/bbpregame/<date>.json,
  DIR/bbresults/<date>.json. Missing docs are fine.

Board build (for --slate): every game on that US Eastern date with a posted spread gets a GAME object (football
schema). The line is DraftKings' number from ESPN. The projections are ESPN's pregame win chance (BPI, from the
robot) converted to a margin, plus any projections and expert picks Claude found and wrote to ctx.json:
  {"<gameId>": {"model": [{src, fav: "A"|"H", m?, p?}], "experts": [{src, pick, side: "A"|"H"}],
                "desc": str, "facts": [str], "inj": str}}
The lean follows the runbook's LEAN METHOD. Games within 15 minutes of tip, started or finished are never touched.

Grading: picks are locked at tip from the last copy saved before tip (verified), exactly as football; finished games
are graded (straight up, lean) and written to bbresults/<date>; sportsbook quotes and the pick history go to
bbpregame/<date>; meta/bblive gets a one-line note. Prints a JSON manifest of writes:
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

RAW = "https://raw.githubusercontent.com/bxn109-stack/gridiron-scores/main/scores/bb"
UTC = dt.timezone.utc
ET = A.ET
LEAGUES = ("NBA", "WNBA", "MBB", "WBB")
COLLEGE = {"MBB", "WBB"}
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


def lean_for(g):
    line = g.get("line") or {}
    sp, fav = line.get("sp"), line.get("fav")
    if sp is None or fav not in ("A", "H"):
        return None
    sig = SIG.get(g["lg"], 11.0)
    projs = [(x.get("src"), home_margin(x, sig)) for x in g.get("model") or []]
    projs = [(s, v) for s, v in projs if v is not None]
    if not projs:
        return None
    line_hm = sp if fav == "H" else -sp
    avg = statistics.mean(v for _, v in projs)
    diff = avg - line_hm
    votes = sum(1 if x.get("side") == "H" else -1 if x.get("side") == "A" else 0 for x in g.get("experts") or [])
    score = clamp(diff / 3, -1.5, 1.5) + clamp(0.5 * votes, -1, 1)
    side = "H" if score > 0 else "A"
    conf = 1
    if abs(score) >= 1.25 and votes and (diff > 0) == (votes > 0) and len(projs) >= 2:
        conf = 2
    ln = 0.0 if sp == 0 else (-sp if side == fav else sp)
    names = {"A": g["a"]["n"], "H": g["h"]["n"]}
    pf = "H" if avg >= 0 else "A"
    src = projs[0][0] if len(projs) == 1 else f"{len(projs)} projections"
    why = f"{src} {'has' if len(projs) == 1 else 'average'} {names[pf]} by {abs(avg):.1f} against a line of {names[fav]} {fmt_ln(-sp)}"
    why += f", so the value side is {names[side]} {fmt_ln(ln)}."
    if votes:
        why += f" Expert picks lean {names['H' if votes > 0 else 'A']} ({abs(votes)} net)."
    return {"mk": "spread", "side": side, "ln": ln, "conf": conf, "why": why}


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


def build_game(e, lg, old, ctx, now):
    fav, sp, tot, book = book_line(e)
    gid = old.get("id") if old else game_id(lg, e)
    c = ctx.get(gid) or ctx.get(e["id"]) or {}
    model = [m for m in [bpi_entry(e, lg)] if m]
    for x in c.get("model") or []:
        if isinstance(x, dict) and x.get("src") and x.get("fav") in ("A", "H"):
            model.append({k: x.get(k) for k in ("src", "fav", "m", "p") if x.get(k) is not None})
    experts = [{k: x.get(k) for k in ("src", "pick", "side", "score") if x.get(k) is not None}
               for x in c.get("experts") or [] if isinstance(x, dict) and x.get("src")]
    if not experts and old:
        experts = old.get("experts") or []
    if len(model) == 1 and old:                       # keep researched projections from an earlier run today
        model += [x for x in old.get("model") or [] if x.get("src") != model[0]["src"]]
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
        "props": (old or {}).get("props") or [],
        "st": "pre", "sc": None, "clk": None, "tracked": True, "gr": None,
    }
    lean = lean_for(g)
    if old and old.get("lean") and lean and (old["lean"].get("side"), old["lean"].get("ln")) != (lean["side"], lean["ln"]):
        lean["why"] = f"Updated {now.astimezone(ET).strftime('%a %-I:%M %p')}: " + lean["why"]
    elif old and old.get("lean") and lean and old["lean"].get("why", "").startswith("Updated ") and \
            (old["lean"].get("side"), old["lean"].get("ln")) == (lean["side"], lean["ln"]):
        lean["why"] = old["lean"]["why"].split(": ", 1)[0] + ": " + lean["why"]
    g["lean"] = lean
    return g


def build(slate, feed_day, docs, ctx, now, leagues):
    """New/updated games per league for the slate date. Returns ({lg: {gid: game or changed fields}}, new game count)."""
    out, n_new = {}, 0
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
        keep = ("lock", "lockdiff", "clv", "pv", "per")
        for k in keep:
            if old and k in old:
                g[k] = old[k]
        if old:
            changed = {k: v for k, v in g.items() if old.get(k) != v}
            if changed:
                out.setdefault(lg, {})[old["id"]] = changed
        else:
            ids = set((docs.get(lg) or {}).get("games") or {}) | set(out.get(lg) or {})
            if g["id"] in ids:
                g["id"] = f"{g['id']}-{e['id']}"
            out.setdefault(lg, {})[g["id"]] = g
            n_new += 1
    return out, n_new


# ---------- grading ----------
SNAP_KEYS = ("line", "lean", "star", "props")


def snap_of(g):
    """The parts of a game that make up the pick, without free text (keeps the pick history small)."""
    line, lean = g.get("line") or {}, g.get("lean")
    return {"line": {k: line.get(k) for k in ("fav", "sp", "tot")},
            "lean": {k: lean.get(k) for k in ("mk", "side", "team", "ln", "conf") if k in lean} if lean else None,
            "star": bool(g.get("star")), "props": []}


def lock_diff(g, lock):
    cur = snap_of(g)
    return [k for k in SNAP_KEYS if cur.get(k) != lock.get(k)]


def finished(g):
    return g.get("st") == "final" and (not g.get("tracked") or bool(g.get("gr")))


def match_bb(g, pool):
    """Like the football matcher, restricted to the game's own league (men's and women's teams share names)."""
    return A.match(dict(g, lg="_" + g["lg"]), [e for e in pool if e.get("lg") == g["lg"]])


def grade_docs(docs_by_slate, pre_old, feed, now, now_iso, pool_cache):
    """Lock, live-update and grade. Returns (league changes {(slate, lg): {gid: ch}}, pregame {slate: body parts},
    results {slate: {gid: row}}, counters)."""
    changes, pregame, results = {}, {}, {}
    n = {"upd": 0, "graded": 0, "lock": 0, "ver": 0, "late": 0, "unmatched": []}
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
        pg = pregame.setdefault(slate, {"games": {}, "snaps": {}})
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
                if ko > now:
                    snap = snap_of(g)
                    last = {k: v for k, v in (old_snaps[-1] if old_snaps else {}).items() if k not in ("at", "h")}
                    if snap != last:
                        pg["snaps"][gid] = old_snaps + [dict(snap, at=now_iso, h=A.chain(old_snaps[-1].get("h") if old_snaps else None, snap, now_iso))]
                old_ent = (old_pg.get("games") or {}).get(gid) or {}
                if e and (e.get("quotes") or e.get("qopen") or e.get("qclose")):
                    ent = {"state": e.get("state"), "quotes": e.get("quotes") if e.get("state") == "pre" else None,
                           "open": e.get("qopen"), "close": e.get("qclose")}
                    if swapped:
                        ent = {k: A.swap_quotes(v) if k != "state" else v for k, v in ent.items()}
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
                    ch["lock"] = lock
                    n["lock"] += 1
                if lock:
                    diff = lock_diff(g, lock)
                    if diff != (g.get("lockdiff") or []):
                        ch["lockdiff"] = diff
                gl = dict(g, line=dict(g.get("line") or {}, **(lock["line"] or {})), lean=lock["lean"], star=lock["star"]) if lock else g
                if state in ("in", "post") and g.get("tracked") and g.get("clv") is None:
                    cv = A.clv_of(gl, e.get("close"), swapped) if not (lock or {}).get("late") else None
                    if cv is not None:
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
                    if g.get("tracked"):
                        su, lean, star = A.grade_game(gl, sc["a"], sc["h"])
                        new_gr = {"su": su, "lean": lean, "star": star, "props": []}
                        if (g.get("gr") or {}) != new_gr:
                            ch["gr"] = new_gr
                            if not g.get("gr"):
                                n["graded"] += 1
                            l = gl.get("lean") or {}
                            row = {"lg": g["lg"], "su": su, "lean": lean, "star": star, "conf": l.get("conf"), "mk": l.get("mk"),
                                   "props": {"W": 0, "L": 0, "P": 0, "U": 0}, "locked": bool(lock), "v": bool((lock or {}).get("v"))}
                            cv = ch.get("clv") or g.get("clv")
                            if cv and cv.get("pts") is not None:
                                row["clv"] = cv["pts"]
                            results.setdefault(slate, {})[gid] = row
                elif g.get("clk") != e.get("detail"):
                    ch["clk"] = e.get("detail")
                if ch:
                    changes.setdefault((slate, lg), {})[gid] = ch
    return changes, pregame, results, n


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
    cfg_path = os.path.join(docs_dir, "meta", "bbconfig.json")
    cfg = A.load(cfg_path) if os.path.exists(cfg_path) else None
    if cfg and "slates" not in cfg and isinstance(cfg.get("data"), dict):
        cfg = cfg["data"]
    return lg_docs, pre, res, cfg


def list_games(feed, slate, leagues, now):
    d = dt.date.fromisoformat(slate)
    rows = []
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
                     "bpi": f"{bpi['fav']} {bpi['p']}%" if bpi else None, "started": ko - now <= LOCK_BEFORE})
    rows.sort(key=lambda r: (LEAGUES.index(r["lg"]), r["tip"]))
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
    args = ap.parse_args(argv)
    now = A.when(args.now) if args.now else dt.datetime.now(UTC)
    now_iso = now.astimezone(UTC).isoformat(timespec="seconds")
    leagues = tuple(x for x in args.leagues.split(",") if x in LEAGUES)
    if args.list:
        for r in list_games(args.feed, args.slate, leagues, now):
            print(json.dumps(r))
        return
    os.makedirs(args.out, exist_ok=True)
    status = load_status(args.feed)
    lg_docs, pre_old, res_exist, cfg = read_docs(args.docs)
    existed = {(s, lg) for s, v in lg_docs.items() for lg in v}
    manifest, notes = [], []

    built = {}
    if args.slate and not args.grade_only:
        day = load_day(args.feed, dt.date.fromisoformat(args.slate))
        ctx = A.load(args.ctx) if args.ctx and os.path.exists(args.ctx) else {}
        built, n_new = build(dt.date.fromisoformat(args.slate), day, lg_docs.get(args.slate) or {}, ctx or {}, now, leagues)
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

    changes, pregame, results, n = grade_docs(lg_docs, pre_old, args.feed, now, now_iso, {})

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

    for slate, pg in sorted(pregame.items()):
        if not pg["games"] and not pg["snaps"]:
            continue
        exists = slate in pre_old
        body = {"slate": slate, "updatedAt": now_iso, "feedCheckedAt": status.get("checkedAt"), "games": pg["games"], "snaps": pg["snaps"]}
        f = write(args.out, f"{slate}__pregame.json", body)
        manifest.append({"op": "update" if exists else "set", "collection": "bbpregame", "doc_id": slate, "file_path": f})

    for slate, rows in sorted(results.items()):
        body = {"slate": slate, "label": slate_label(dt.date.fromisoformat(slate)), "updatedAt": now_iso, "games": rows}
        f = write(args.out, f"{slate}__results.json", body)
        manifest.append({"op": "update" if slate in res_exist else "set", "collection": "bbresults", "doc_id": slate, "file_path": f})

    bits = notes + [f"{n['upd']} game{'s' if n['upd'] != 1 else ''} updated", f"{n['graded']} graded"]
    if n["lock"]:
        bits.append(f"{n['lock']} pick{'s' if n['lock'] != 1 else ''} locked, {n['ver']} from a copy saved before tip"
                    + (f" ({n['late']} back-filled)" if n["late"] else ""))
    if n["unmatched"]:
        bits.append(f"{len(n['unmatched'])} not found in the feed")
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
