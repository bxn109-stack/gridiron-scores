#!/usr/bin/env python3
"""Efficiency-based college football model, backtested against closing AND opening lines.

Builds on cfb_backtest.py. Every team also gets opponent-adjusted offense and defense ratings from
College Football Data's per-game advanced stats (garbage time excluded): points added per play (EPA/PPA),
success rate and pace. Ratings update one game at a time, so each prediction only uses earlier games.
Recruiting talent and returning production act as an early-season prior.

A straight-line blend of those inputs (plus the points rating) is fitted on 2014-2019 only, then scored
on 2021-2025, which it never saw. Writes model/results/cfb_efficiency.md (summary only).
"""
import itertools, json, os, statistics as st, sys, time, traceback, urllib.parse, urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfb_backtest as B  # noqa: E402
from cfb_backtest import f, YEARS, TUNE, TEST, group, ats, ou  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


LOG = []                                   # fetch timings, written into the results for troubleshooting
BUDGET = float(os.environ.get("FETCH_BUDGET_S", "1500"))
T_START = time.time()


def fetch(path, timeout=150, **params):
    """Cached GET with a total time budget. Returns (data or None, note). Uses the same cache files as cfb_backtest."""
    fn = os.path.join(B.CACHE, path.strip("/").replace("/", "_") + "_" + "_".join(f"{k}{v}" for k, v in sorted(params.items())) + ".json")
    if os.path.exists(fn):
        with open(fn) as fh:
            return json.load(fh), "cached"
    if time.time() - T_START > BUDGET:
        return None, "skipped (time budget; the next run picks it up)"
    url = f"{B.BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {B.KEY}", "Accept": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:
        return None, f"failed after {time.time() - t:.0f} s ({type(e).__name__}: {str(e)[:80]})"
    os.makedirs(B.CACHE, exist_ok=True)
    with open(fn, "w") as fh:
        json.dump(data, fh)
    return data, f"downloaded in {time.time() - t:.0f} s"


def adv_rows(y, stype):
    data, note = fetch("/stats/game/advanced", year=y, seasonType=stype, excludeGarbageTime="true")
    LOG.append(f"advanced stats {y} {stype}: {note}, {len(data) if data is not None else 0} rows")
    if data is not None or note.startswith("skipped"):
        return data or []
    rows = []                              # the whole-season call failed: try one week at a time
    for wk in range(1, 17 if stype == "regular" else 2):
        d, n = fetch("/stats/game/advanced", timeout=90, year=y, week=wk, seasonType=stype, excludeGarbageTime="true")
        if d is None:
            LOG.append(f"  week {wk}: {n}")
            if n.startswith("skipped"):
                break
            continue
        rows += d
    LOG.append(f"  by week: {len(rows)} rows")
    return rows


def load_extra():
    adv, talent, ret, sample = {}, {}, {}, None
    for y in YEARS:                        # small lookups first
        d, n = fetch("/talent", year=y)
        for r in d or []:
            v = num(f(r, "talent"))
            if v is not None:
                talent[(y, f(r, "team", "school"))] = v
        d2, n2 = fetch("/player/returning", year=y)
        for r in d2 or []:
            v = num(f(r, "percentPPA", "percent_ppa"))
            if v is not None:
                ret[(y, f(r, "team"))] = v
        LOG.append(f"talent {y}: {n}, {len(d or [])} rows; returning production {y}: {n2}, {len(d2 or [])} rows")
    for y in YEARS:
        for stype in ("regular", "postseason"):
            for r in adv_rows(y, stype):
                sample = sample or r
                gid, team = f(r, "gameId", "game_id"), f(r, "team")
                o, d = r.get("offense") or {}, r.get("defense") or {}
                adv[(gid, team)] = {"y": y, "ppa": num(f(o, "ppa")), "sr": num(f(o, "successRate", "success_rate")),
                                    "plays": num(f(o, "plays")), "dppa": num(f(d, "ppa")), "dsr": num(f(d, "successRate", "success_rate"))}
    return adv, talent, ret, sample


def solve(X, y, ridge=1e-6):
    """Least squares by normal equations (small, pure Python)."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) + (ridge if i == j else 0.0) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[piv], b[c], b[piv] = A[piv], A[c], b[piv], b[c]
        if abs(A[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                m = A[r][c] / A[c][c]
                for k in range(c, n):
                    A[r][k] -= m * A[c][k]
                b[r] -= m * b[c]
    return [b[i] / A[i][i] if abs(A[i][i]) > 1e-12 else 0.0 for i in range(n)]


def dot(w, x):
    return sum(a * b for a, b in zip(w, x))


def features(G, base_out, adv, talent, ret, p):
    """Walk forward through every game; return per-game feature rows built only from earlier games."""
    EO, ED, SO, SD, PC = (defaultdict(float) for _ in range(5))
    y0 = min((v["y"] for v in adv.values()), default=None)
    first = [v for v in adv.values() if v["y"] == y0 and v["ppa"] is not None]   # league averages from the first season only
    LG = {"ppa": st.mean(v["ppa"] for v in first) if first else 0.0,
          "sr": st.mean(v["sr"] for v in first if v["sr"] is not None) if first else 0.4,
          "plays": st.mean(v["plays"] for v in first if v["plays"] is not None) if first else 70.0}
    tmin = {}
    for (y, t), v in talent.items():
        tmin[y] = min(v, tmin.get(y, v))
    rmean = defaultdict(list)
    for (y, t), v in ret.items():
        rmean[y].append(v)
    rmean = {y: st.mean(v) for y, v in rmean.items()}
    seen, season, rows = set(), None, []
    for g, pm, pt in base_out:
        y = g["season"]
        if y != season:
            season = y
            for D in (EO, ED, SO, SD):
                for t in list(D):
                    D[t] *= p["re"]
            for t in list(PC):
                PC[t] *= p["re"]
        h, a = g["home"], g["away"]
        for t, div in ((h, g["hdiv"]), (a, g["adiv"])):
            if t not in seen:
                seen.add(t)
                if div != "fbs":
                    EO[t], ED[t], SO[t], SD[t] = -p["fcs_e"], p["fcs_e"], -p["fcs_e"] / 3, p["fcs_e"] / 3
        home = 0.0 if g["neutral"] else 1.0
        hfa = 0.0 if g["neutral"] else B_P["hfa"]
        e_h, e_a = EO[h] + ED[a], EO[a] + ED[h]          # expected PPA/play for each offense vs average
        s_h, s_a = SO[h] + SD[a], SO[a] + SD[h]
        pace = PC[h] + PC[a]
        early = 0.0 if g["stype"] == "postseason" else max(0.0, (8 - (g["week"] or 1)) / 7)
        th, ta = talent.get((y, h), tmin.get(y)), talent.get((y, a), tmin.get(y))
        tdiff = ((th - ta) / 100.0) if th is not None and ta is not None else 0.0
        rh, ra = ret.get((y, h), rmean.get(y)), ret.get((y, a), rmean.get(y))
        rdiff = (rh - ra) if rh is not None and ra is not None else 0.0
        rows.append({"g": g, "pm": pm, "pt": pt,
                     "xm": [home, pm - hfa, e_h - e_a, s_h - s_a, tdiff, tdiff * early, rdiff * early],
                     "xt": [1.0, pt, e_h + e_a, s_h + s_a, pace]})
        # update after the game
        k, ks = p["k"], p["k"]
        for off, de, key in ((h, a, g["id"]), (a, h, g["id"])):
            s = adv.get((key, off))
            if not s:
                continue
            if s["ppa"] is not None:
                err = s["ppa"] - (LG["ppa"] + EO[off] + ED[de])
                EO[off] += k * err; ED[de] += k * err
                LG["ppa"] += 0.001 * (s["ppa"] - LG["ppa"])
            if s["sr"] is not None:
                err = s["sr"] - (LG["sr"] + SO[off] + SD[de])
                SO[off] += ks * err; SD[de] += ks * err
                LG["sr"] += 0.001 * (s["sr"] - LG["sr"])
        sh, sa = adv.get((g["id"], h)), adv.get((g["id"], a))
        if sh and sa and sh["plays"] is not None and sa["plays"] is not None:
            err = sh["plays"] + sa["plays"] - (2 * LG["plays"] + PC[h] + PC[a])
            PC[h] += p["kp"] * err; PC[a] += p["kp"] * err
            LG["plays"] += 0.001 * ((sh["plays"] + sa["plays"]) / 2 - LG["plays"])
    return rows


def in_yrs(g, yrs):
    return yrs[0] <= g["season"] <= yrs[1]


def fit(rows, cols, target="m"):
    X, y = [], []
    for r in rows:
        g = r["g"]
        if not in_yrs(g, TUNE) or g["close"] is None:
            continue
        if target == "m":
            X.append([r["xm"][i] for i in cols]); y.append(max(-42, min(42, g["hp"] - g["ap"])))
        else:
            if g["tot"] is None:
                continue
            X.append([r["xt"][i] for i in cols]); y.append(g["hp"] + g["ap"])
    return solve(X, y), len(y)


def mae_of(out, yrs):
    xs = [(g, m) for g, m, t in out if in_yrs(g, yrs) and g["close"] is not None]
    return st.mean(abs(g["hp"] - g["ap"] - m) for g, m in xs), len(xs)


def line_move(out, yrs, thr):
    """When the model disagrees with the OPENING line, how often does the line close toward the model?"""
    tw = aw = 0; pts = []
    for g, m, t in out:
        if not in_yrs(g, yrs) or g["open"] is None or g["close"] is None:
            continue
        e = m - g["open"]
        if abs(e) < thr:
            continue
        mv = (g["close"] - g["open"]) * (1 if e > 0 else -1)   # + = line moved toward the model's side
        pts.append(mv)
        if mv > 0: tw += 1
        elif mv < 0: aw += 1
    n = len(pts)
    return n, tw, aw, (st.mean(pts) if n else float("nan"))


def blend(out, key, yrs_fit, yrs_test):
    """Fit margin = a·line + b·model on tuning seasons; score on test. Beats the line only if the model adds information."""
    X, y = [], []
    for g, m, t in out:
        if in_yrs(g, yrs_fit) and g[key] is not None:
            X.append([g[key], m]); y.append(max(-42, min(42, g["hp"] - g["ap"])))
    w = solve(X, y)
    xs = [(g, m) for g, m, t in out if in_yrs(g, yrs_test) and g[key] is not None]
    e_line = st.mean(abs(g["hp"] - g["ap"] - g[key]) for g, m in xs)
    e_bl = st.mean(abs(g["hp"] - g["ap"] - (w[0] * g[key] + w[1] * m)) for g, m in xs)
    return w, e_line, e_bl, len(xs)


B_P = {}


def main():
    if not B.KEY:
        print("CFBD_API_KEY secret is not set. Add it under Settings -> Secrets and variables -> Actions.")
        return 0
    t0 = time.time()
    G = B.load()
    adv, talent, ret, sample = load_extra()
    B_P.update(B.tune(G))
    base_out = B.run(G, B_P)
    both = sum(1 for g in G if (g["id"], g["home"]) in adv and (g["id"], g["away"]) in adv)
    lined = [g for g in G if g["close"] is not None]
    both_l = sum(1 for g in lined if (g["id"], g["home"]) in adv and (g["id"], g["away"]) in adv)

    MCOLS_EFF = [0, 2, 3, 4, 5, 6]      # home, PPA, success rate, talent, early talent, early returning
    MCOLS_ALL = [0, 1, 2, 3, 4, 5, 6]   # + points rating
    TCOLS = [0, 1, 2, 3, 4]
    grid = dict(k=[0.04, 0.07, 0.10], re=[0.5, 0.7], kp=[0.05], fcs_e=[0.15, 0.30])
    best = None
    for vals in itertools.product(*grid.values()):
        p = dict(zip(grid, vals))
        rows = features(G, base_out, adv, talent, ret, p)
        w, n = fit(rows, MCOLS_ALL)
        out = [(r["g"], dot(w, [r["xm"][i] for i in MCOLS_ALL]), 0.0) for r in rows]
        e = mae_of(out, TUNE)[0]
        if best is None or e < best[0]:
            best = (e, p)
    p = best[1]
    rows = features(G, base_out, adv, talent, ret, p)
    w_all, n_fit = fit(rows, MCOLS_ALL)
    w_eff, _ = fit(rows, MCOLS_EFF)
    w_tot, n_tfit = fit(rows, TCOLS, "t")
    out_pts = base_out
    out_eff = [(r["g"], dot(w_eff, [r["xm"][i] for i in MCOLS_EFF]), 0.0) for r in rows]
    out_all = [(r["g"], dot(w_all, [r["xm"][i] for i in MCOLS_ALL]), dot(w_tot, [r["xt"][i] for i in TCOLS])) for r in rows]

    names = ["home field", "points rating", "PPA/play edge", "success-rate edge", "talent (per 100)", "talent, early weeks", "returning production, early weeks"]
    L = ["# College football efficiency model backtest", "",
         f"Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. Walk-forward ratings from garbage-time-free advanced stats (points added per play, success rate, pace), "
         f"plus recruiting talent and returning production early in the season. Blend weights fitted on {TUNE[0]}–{TUNE[1]} only; scored on {TEST[0]}–{TEST[1]}.",
         "",
         "## Data check", "",
         f"- Games: {len(G)}; with a closing line: {len(lined)}; with advanced stats for both teams: {both} (of the lined games: {both_l})",
         f"- Talent ratings: {len(talent)} team-seasons; returning production: {len(ret)} team-seasons",
         f"- Advanced-stat fields seen: `{', '.join(sorted((sample or {}).keys()))}`; offense fields: `{', '.join(sorted(((sample or {}).get('offense') or {}).keys()))}`",
         *[f"- {x}" for x in LOG],
         f"- Efficiency settings: `{json.dumps(p)}`; points-rating settings: `{json.dumps({k: B_P[k] for k in B.GRID})}`",
         f"- Spread blend weights (fitted on {n_fit} tuning games): " + ", ".join(f"{names[i]} {w:+.3f}" for i, w in zip(MCOLS_ALL, w_all)),
         f"- Total blend weights ({n_tfit} games): " + ", ".join(f"{nm} {w:+.3f}" for nm, w in zip(["constant", "points-based total", "PPA sum", "success-rate sum", "pace"], w_tot)),
         "",
         "## Average miss on the final margin (lower is better)", "",
         "| Model | Tuning seasons | Test seasons |", "|---|---|---|"]
    for nm, out in (("Closing line (the market)", None), ("Points rating only (old model)", out_pts), ("Efficiency only", out_eff), ("Efficiency + points (new model)", out_all)):
        if out is None:
            cells = []
            for yrs in (TUNE, TEST):
                xs = [g for g in G if in_yrs(g, yrs) and g["close"] is not None]
                cells.append(f"{st.mean(abs(g['hp'] - g['ap'] - g['close']) for g in xs):.2f} ({len(xs)} games)")
        else:
            cells = [f"{mae_of(out, yrs)[0]:.2f}" for yrs in (TUNE, TEST)]
        L.append(f"| {nm} | {cells[0]} | {cells[1]} |")

    L += ["", "## Does the model add anything the line doesn't already know? (test seasons)", "",
          "Fits final margin = a × line + b × model on the tuning seasons, then checks the average miss on the test seasons. "
          "If the blend misses by less than the line alone, the model carries real information the market missed.", "",
          "| Line | Weight on line | Weight on model | Line alone | Line + model | Games |", "|---|---|---|---|---|---|"]
    for key, nm in (("close", "Closing"), ("open", "Opening")):
        w, e_l, e_b, n = blend(out_all, key, TUNE, TEST)
        L.append(f"| {nm} | {w[0]:.2f} | {w[1]:.2f} | {e_l:.3f} | {e_b:.3f} | {n} |")

    L += ["", "## Spread bets on test seasons, new model (at −110)", "",
          "| Model disagrees by | vs closing line | vs opening line |", "|---|---|---|"]
    for thr in (1, 2, 3, 4, 5, 7, 10):
        a = ats(out_all, TEST, thr, "close"); b = ats(out_all, TEST, thr, "open")
        L.append(f"| {thr}+ pts | {a[0]}–{a[1]} ({a[2]:.1%}, ROI {a[3]:+.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}, ROI {b[3]:+.1%}) |")

    L += ["", "## Same, old points-only model, for comparison", "", "| Model disagrees by | vs closing line | vs opening line |", "|---|---|---|"]
    for thr in (2, 4, 7):
        a = ats(out_pts, TEST, thr, "close"); b = ats(out_pts, TEST, thr, "open")
        L.append(f"| {thr}+ pts | {a[0]}–{a[1]} ({a[2]:.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}) |")

    L += ["", "## Does the line move toward the model after it opens? (test seasons)", "",
          "Betting the opening number and watching it close in your direction (beating the closing line) is the best-known sign of a real edge.", "",
          "| Model disagrees with opener by | Games | Closed toward model | Closed away | Average move toward model |", "|---|---|---|---|---|"]
    for thr in (2, 3, 5, 7):
        n, tw, aw, mv = line_move(out_all, TEST, thr)
        L.append(f"| {thr}+ pts | {n} | {tw} ({tw / n:.0%}) | {aw} ({aw / n:.0%}) | {mv:+.2f} pts |" if n else f"| {thr}+ pts | 0 | — | — | — |")

    L += ["", "## By matchup type (test seasons, new model disagrees by 3+ pts)", "", "| Matchup | vs closing | vs opening |", "|---|---|---|"]
    for grp in ("Power vs Power", "Power vs Group of 5", "Group of 5 vs Group of 5", "FCS involved"):
        a = ats(out_all, TEST, 3, "close", grp); b = ats(out_all, TEST, 3, "open", grp)
        L.append(f"| {grp} | {a[0]}–{a[1]} ({a[2]:.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}) |")

    L += ["", "## Total bets on test seasons, new model", "", "| Model total differs by | vs closing total | vs opening total |", "|---|---|---|"]
    for thr in (2, 4, 6, 8):
        a = ou(out_all, TEST, thr, "tot"); b = ou(out_all, TEST, thr, "tot_open")
        L.append(f"| {thr}+ pts | {a[0]}–{a[1]} ({a[2]:.1%}, ROI {a[3]:+.1%}) | {b[0]}–{b[1]} ({b[2]:.1%}, ROI {b[3]:+.1%}) |")
    L += ["", f"Break-even at −110 is 52.4%. Small groups (under ~200 bets) swing a lot by chance. Run time {time.time() - t0:.0f} s."]

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "cfb_efficiency.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


def report_failure():
    """Write what went wrong into the results file (the Actions log is not visible from the picks setup)."""
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "cfb_efficiency.md"), "w") as fh:
        fh.write("# College football efficiency model backtest\n\nThe run stopped with an error at "
                 + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()) + ".\n\n```\n" + traceback.format_exc()[-3000:] + "```\n\n"
                 + "Fetch log:\n\n" + "\n".join(f"- {x}" for x in LOG) + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        report_failure()
        sys.exit(0)
