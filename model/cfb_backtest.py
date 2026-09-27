#!/usr/bin/env python3
"""College football spread & total model, backtested against closing AND opening lines.

Runs on GitHub Actions (reads CFBD_API_KEY from a repository secret; never commit the key).
Walk-forward team ratings: every prediction uses only games played before it.
Tune on 2014-2019, test on 2021-2025 (2020 is played through but not scored).

Writes model/results/cfb_backtest.md (summary only; raw data stays in the Actions cache).
"""
import itertools, json, os, statistics as st, sys, time, urllib.parse, urllib.request
from collections import defaultdict

BASE = "https://api.collegefootballdata.com"
KEY = os.environ.get("CFBD_API_KEY", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
YEARS = range(2014, 2026)
TUNE, TEST = (2014, 2019), (2021, 2025)
P4 = {"ACC", "Big Ten", "Big 12", "SEC", "Pac-12", "FBS Independents"}
PROVIDERS = ["consensus", "DraftKings", "ESPN Bet", "Bovada", "William Hill (New Jersey)", "teamrankings", "numberfire"]


def f(d, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def get(path, **params):
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, path.strip("/").replace("/", "_") + "_" + "_".join(f"{k}{v}" for k, v in sorted(params.items())) + ".json")
    if os.path.exists(fn):
        with open(fn) as fh:
            return json.load(fh)
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except Exception as e:
            code = getattr(e, "code", None)
            if i == 3 or (code is not None and 400 <= code < 500 and code != 429):
                raise
            print(f"retry {path} {params}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    with open(fn, "w") as fh:
        json.dump(data, fh)
    return data


def load():
    games = []
    for y in YEARS:
        lines = {}
        for stype in ("regular", "postseason"):
            for ln in get("/lines", year=y, seasonType=stype):
                lines[f(ln, "id")] = ln
            for g in get("/games", year=y, seasonType=stype):
                hp, ap = f(g, "homePoints", "home_points"), f(g, "awayPoints", "away_points")
                if hp is None or ap is None:
                    continue
                games.append({
                    "id": f(g, "id"), "season": y, "week": f(g, "week", default=0), "stype": stype,
                    "date": f(g, "startDate", "start_date", default=""),
                    "neutral": bool(f(g, "neutralSite", "neutral_site", default=False)),
                    "home": f(g, "homeTeam", "home_team"), "away": f(g, "awayTeam", "away_team"),
                    "hc": f(g, "homeConference", "home_conference", default="") or "",
                    "ac": f(g, "awayConference", "away_conference", default="") or "",
                    "hdiv": (f(g, "homeClassification", "home_division", default="") or "").lower(),
                    "adiv": (f(g, "awayClassification", "away_division", default="") or "").lower(),
                    "hp": float(hp), "ap": float(ap),
                })
        for g in games:
            if g["season"] != y:
                continue
            ln = lines.get(g["id"])
            g["close"] = g["open"] = g["tot"] = g["tot_open"] = None
            if not ln:
                continue
            books = {b.get("provider"): b for b in (ln.get("lines") or [])}
            def med(key):
                vals = []
                for p in PROVIDERS + list(books):
                    b = books.get(p)
                    if b is not None and b.get(key) is not None:
                        try:
                            vals.append(float(b[key]))
                        except (TypeError, ValueError):
                            pass
                return st.median(vals) if vals else None
            # CFBD spreads are from the home team's side: -7 means the home team is favored by 7.
            sp, so = med("spread"), med("spreadOpen") if any("spreadOpen" in b for b in books.values()) else med("spread_open")
            g["close"] = None if sp is None else -sp          # expected home margin by the market
            g["open"] = None if so is None else -so
            g["tot"] = med("overUnder") if any("overUnder" in b for b in books.values()) else med("over_under")
            g["tot_open"] = med("overUnderOpen") if any("overUnderOpen" in b for b in books.values()) else med("over_under_open")
    games.sort(key=lambda g: (g["date"], g["id"]))
    return games


def run(G, p):
    R = defaultdict(lambda: None); O = defaultdict(float); D = defaultdict(float)
    season = None; out = []; lg = 28.0
    for g in G:
        if g["season"] != season:
            season = g["season"]
            for t in list(R):
                if R[t] is not None:
                    R[t] *= p["regress"]
            for t in list(O):
                O[t] *= p["regress"]; D[t] *= p["regress"]
        h, a = g["home"], g["away"]
        for t, div in ((h, g["hdiv"]), (a, g["adiv"])):
            if R[t] is None:
                R[t] = 0.0 if div == "fbs" else p["fcs_start"]
        hfa = 0.0 if g["neutral"] else p["hfa"]
        pm = R[h] - R[a] + hfa
        pt = 2 * lg + O[h] + O[a] - D[h] - D[a]
        out.append((g, pm, pt))
        m = max(-p["cap"], min(p["cap"], g["hp"] - g["ap"]))
        err = m - pm
        R[h] += p["k"] * err; R[a] -= p["k"] * err
        eh = g["hp"] - (lg + hfa / 2 + O[h] - D[a]); ea = g["ap"] - (lg - hfa / 2 + O[a] - D[h])
        O[h] += p["kt"] * eh; D[a] -= p["kt"] * eh; O[a] += p["kt"] * ea; D[h] -= p["kt"] * ea
        lg += 0.001 * ((g["hp"] + g["ap"]) / 2 - lg)
    return out


def group(g):
    if g["hdiv"] == "fcs" or g["adiv"] == "fcs":
        return "FCS involved"
    if g["hc"] in P4 and g["ac"] in P4:
        return "Power vs Power"
    if g["hc"] in P4 or g["ac"] in P4:
        return "Power vs Group of 5"
    return "Group of 5 vs Group of 5"


def mae(out, yrs):
    xs = [(g, m) for g, m, t in out if yrs[0] <= g["season"] <= yrs[1] and g["close"] is not None]
    return st.mean(abs(g["hp"] - g["ap"] - m) for g, m in xs), st.mean(abs(g["hp"] - g["ap"] - g["close"]) for g, m in xs), len(xs)


def ats(out, yrs, thr, line_key="close", grp=None):
    w = l = 0
    for g, m, t in out:
        line = g[line_key]
        if line is None or not (yrs[0] <= g["season"] <= yrs[1]) or (grp and group(g) != grp):
            continue
        edge = m - line
        if abs(edge) < thr:
            continue
        # bet at line_key; grade against the result at that same number
        c = (g["hp"] - g["ap"] - line) * (1 if edge > 0 else -1)
        if c > 0: w += 1
        elif c < 0: l += 1
    n = w + l
    return w, l, (w / n if n else float("nan")), (((w * 100 / 110) - l) / n if n else float("nan"))


def ou(out, yrs, thr, key="tot"):
    w = l = 0
    for g, m, t in out:
        line = g[key]
        if line is None or not (yrs[0] <= g["season"] <= yrs[1]):
            continue
        e = t - line
        if abs(e) < thr:
            continue
        d = g["hp"] + g["ap"] - line
        if d == 0:
            continue
        if (d > 0) == (e > 0): w += 1
        else: l += 1
    n = w + l
    return w, l, (w / n if n else float("nan")), (((w * 100 / 110) - l) / n if n else float("nan"))


GRID = dict(k=[0.06, 0.09, 0.12], regress=[0.5, 0.65, 0.8], hfa=[2.0, 2.5, 3.0], cap=[21, 28], fcs_start=[-12.0, -20.0])


def tune(G):
    """Best points-rating settings by average miss on the tuning seasons only."""
    start = dict(k=0.08, kt=0.05, regress=0.65, hfa=2.5, cap=28, fcs_start=-18.0)
    best = None
    for vals in itertools.product(*GRID.values()):
        p = dict(start, **dict(zip(GRID, vals)))
        e = mae(run(G, p), TUNE)[0]
        if best is None or e < best[0]:
            best = (e, p)
    return best[1]


def main():
    if not KEY:
        print("CFBD_API_KEY secret is not set. Add it under Settings -> Secrets and variables -> Actions.")
        return 0
    G = load()
    with_line = sum(1 for g in G if g["close"] is not None)
    print(f"{len(G)} games loaded, {with_line} with a closing line, {sum(1 for g in G if g['open'] is not None)} with an opening line")
    grid = GRID
    p = tune(G)
    out = run(G, p)
    L = ["# College football model backtest", "",
         f"Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Walk-forward ratings; tuned on {TUNE[0]}–{TUNE[1]}, tested on {TEST[0]}–{TEST[1]} (never seen while tuning).",
         f"Games: {len(G)} ({with_line} with a closing line). Tuned settings: `{json.dumps({k: p[k] for k in grid})}`", ""]
    for name, yrs in (("Tuning seasons", TUNE), ("Test seasons", TEST)):
        e, em, n = mae(out, yrs)
        L.append(f"- {name}: model average miss {e:.2f} pts vs closing line {em:.2f} pts ({n} games)")
    L += ["", "## Spread bets on test seasons (at −110)", "", "| Model disagrees by | vs closing line | vs opening line |", "|---|---|---|"]
    for thr in (1, 2, 3, 4, 5, 7, 10):
        a = ats(out, TEST, thr, "close"); b = ats(out, TEST, thr, "open")
        L.append(f"| {thr}+ pts | {a[0]}–{a[1]} ({a[2]:.1%}, ROI {a[3]:+.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}, ROI {b[3]:+.1%}) |")
    L += ["", "## By matchup type (test seasons, model disagrees by 3+ pts)", "", "| Matchup | vs closing | vs opening |", "|---|---|---|"]
    for grp in ("Power vs Power", "Power vs Group of 5", "Group of 5 vs Group of 5", "FCS involved"):
        a = ats(out, TEST, 3, "close", grp); b = ats(out, TEST, 3, "open", grp)
        L.append(f"| {grp} | {a[0]}–{a[1]} ({a[2]:.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}) |")
    L += ["", "## Total bets on test seasons", "", "| Model total differs by | vs closing total | vs opening total |", "|---|---|---|"]
    for thr in (2, 4, 6, 8):
        a = ou(out, TEST, thr, "tot"); b = ou(out, TEST, thr, "tot_open")
        L.append(f"| {thr}+ pts | {a[0]}–{a[1]} ({a[2]:.1%}, ROI {a[3]:+.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}, ROI {b[3]:+.1%}) |")
    L += ["", "Break-even at −110 is 52.4%. Small groups (under ~200 bets) swing a lot by chance."]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "cfb_backtest.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
