#!/usr/bin/env python3
"""Turn the ESPN score files in this repo into Gridiron Ledger database updates.

Run by Claude's score-update task, not by GitHub. Standard library only.

  python3 apply_scores.py --docs DIR --out OUTDIR [--scores URL|PATH] [--box URL|DIR]

DIR holds league docs saved by ArtifactData (out_dir): DIR/slates/<slate>/leagues/<NFL|FBS|FCS>.json,
and optionally DIR/results/<slate>.json. The script:
  - matches each started ledger game to its ESPN game (saves the ESPN id as `eid` for next time),
  - writes st / sc / clk, and on final also per (quarter scores) and pv (prop results),
  - grades tracked games (su, lean, star, props) exactly as the runbook says,
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

RAW = "https://raw.githubusercontent.com/bxn109-stack/gridiron-scores/main"
UTC = dt.timezone.utc
COLLEGE = {"FBS", "FCS"}


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
    table = [("pass yds", "passYds"), ("passing yds", "passYds"), ("pass td", "passTD"), ("rush yds", "rushYds"),
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


def finished(g):
    """True when a game needs nothing more: final, quarter scores saved, props valued and graded."""
    if g.get("st") != "final" or not g.get("per"):
        return False
    props = g.get("props") or []
    pv = g.get("pv") or []
    if props and (len(pv) < len(props) or None in pv):
        return False
    if g.get("tracked"):
        gr = g.get("gr")
        if not gr or (props and None in (gr.get("props") or [None])):
            return False
    return True


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scores", default=f"{RAW}/scores/latest.json")
    ap.add_argument("--box", default=f"{RAW}/box")
    ap.add_argument("--now", default=None, help="ISO time to use instead of the clock (testing)")
    args = ap.parse_args()
    now = when(args.now) if args.now else dt.datetime.now(UTC)
    now_iso = now.astimezone(UTC).isoformat(timespec="seconds")
    os.makedirs(args.out, exist_ok=True)

    feed = load(args.scores)
    if not feed or not feed.get("games"):
        live = {"ranAt": now_iso, "updated": 0, "graded": 0, "note": "The score feed is empty or unreachable right now. Nothing changed."}
        _write(args.out, "live.json", live)
        print(json.dumps([{"op": "set", "collection": "meta", "doc_id": "live", "file_path": os.path.join(args.out, "live.json")}]))
        return
    pool = feed["games"]
    by_id = {e["id"]: e for e in pool}
    feed_age = (now - when(feed.get("updatedAt"))).total_seconds() / 60 if feed.get("updatedAt") else None

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
    for path in sorted(glob.glob(os.path.join(args.docs, "slates", "*", "leagues", "*.json"))):
        slate, lgname = path.split(os.sep)[-3], os.path.basename(path)[:-5]
        doc = load(path) or {}
        games = doc.get("games") or {}
        changes, results = {}, {}
        for gid, g in games.items():
            ko = when(g.get("ko"))
            if not ko or ko > now + dt.timedelta(hours=1) or ko < now - dt.timedelta(days=3):
                continue
            gr = g.get("gr") or {}
            props = g.get("props") or []
            if finished(g):
                continue
            e = by_id.get(g.get("eid")) if g.get("eid") else None
            swapped = bool(g.get("eswap"))
            if not e:
                e, swapped = match(g, pool)
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
                    if players:
                        pl = find_player(p.get("pl"), players)
                        v = stat_for(p.get("mk"), pl) if pl else None
                        if v is not None:
                            pv[i] = v
                    if pgr[i] is None:
                        pgr[i] = grade_ou(pv[i], p.get("ln"), p.get("side"))
                    if pgr[i] is None:
                        prop_gaps += 1
                if props and pv != (g.get("pv") or []):
                    ch["pv"] = pv
                if g.get("tracked"):
                    su, lean, star = grade_game(g, sc["a"], sc["h"])
                    new_gr = {"su": su, "lean": lean, "star": star, "props": pgr}
                    if gr != new_gr:
                        ch["gr"] = new_gr
                        if not g.get("gr"):
                            n_grade += 1
                        cnt = {"W": 0, "L": 0, "P": 0}
                        for r in pgr:
                            if r in cnt:
                                cnt[r] += 1
                        l = g.get("lean") or {}
                        results[gid] = {"lg": g["lg"], "su": su, "lean": lean, "star": star,
                                        "conf": l.get("conf"), "mk": l.get("mk"), "props": cnt}
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
