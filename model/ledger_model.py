#!/usr/bin/env python3
"""Ledger Model: a points-rating model for the NFL and college football that anyone can rebuild from this file.

Everything the page uses from a model comes from here, and every number can be re-run:
  * walk-forward team ratings (each prediction uses only games played before it),
  * settings tuned on early seasons only, then scored on later seasons it never saw,
  * a logistic fit that turns "model minus closing line" into a cover chance, scored by log loss against the
    market's own 50/50 (Phase 3 gate) and used as the market+model blend only if it passes (Phase 4 gate),
  * a moneyline blend of the no-vig market chance and the model chance, with the same out-of-sample test,
  * push tables: the chance a game lands exactly on a number, given the closing spread or total (Phase 2),
  * current team ratings, so the score-update task can price this week's games privately.

Data: NFL from nflverse (public CSV on GitHub); college from College Football Data (needs CFBD_API_KEY, a repository
secret; never commit it). Raw downloads stay in the Actions cache. Output: model/results/ledger_model.json (+ .md).
Only aggregate numbers and team ratings are written; no picks.

  python3 model/ledger_model.py [--nfl-csv PATH] [--leagues NFL,FBS] [--out DIR]
"""
import argparse, csv, io, itertools, json, math, os, statistics as st, sys, time, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
NFL_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFL_OLD = {"OAK": "LV", "SD": "LAC", "STL": "LA"}          # relocated teams keep one rating
NFL_TO_ESPN = {"LA": "LAR", "WAS": "WSH"}                  # nflverse abbreviation -> ESPN abbreviation
BE = 110 / 210                                             # break-even at -110

SETTINGS = {
    "NFL": dict(tune=(2006, 2015), test=(2016, 2025), lg0=21.5,
                grid=dict(k=[0.04, 0.06, 0.08, 0.1], regress=[0.4, 0.55, 0.7], hfa=[1.0, 1.5, 2.0, 2.5], cap=[17, 24]),
                kt=[0.02, 0.04, 0.06], start=dict(kt=0.04, fcs_start=0.0),
                spread_grid=(-21.0, 21.0), total_grid=(30.0, 62.0), width=16, bw=1.0),
    "FBS": dict(tune=(2014, 2019), test=(2021, 2025), lg0=28.0,
                grid=dict(k=[0.06, 0.09, 0.12], regress=[0.5, 0.65, 0.8], hfa=[2.0, 2.5, 3.0], cap=[21, 28], fcs_start=[-12.0, -20.0]),
                kt=[0.03, 0.05, 0.08], start=dict(kt=0.05),
                spread_grid=(-45.0, 45.0), total_grid=(35.0, 85.0), width=16, bw=1.5),
}


# ---------------------------------------------------------------- data
def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron-ledger-model"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            if i == 3:
                raise
            print(f"retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def load_nfl(text):
    """nflverse games.csv -> game dicts. spread_line and result are both home-minus-away, so close = expected home margin."""
    G = []
    for r in csv.DictReader(io.StringIO(text)):
        h, a = NFL_OLD.get(r["home_team"], r["home_team"]), NFL_OLD.get(r["away_team"], r["away_team"])
        hp, ap = num(r.get("home_score")), num(r.get("away_score"))
        G.append({"id": r["game_id"], "season": int(r["season"]), "week": int(r["week"] or 0),
                  "date": f"{r['gameday']}T{r.get('gametime') or '13:00'}", "home": h, "away": a,
                  "neutral": r.get("location") == "Neutral", "hdiv": "fbs", "adiv": "fbs",
                  "hp": hp, "ap": ap, "close": num(r.get("spread_line")), "tot": num(r.get("total_line")),
                  "mlh": num(r.get("home_moneyline")), "mla": num(r.get("away_moneyline"))})
    G.sort(key=lambda g: (g["date"], g["id"]))
    return G


def load_cfb(current_year, fresh=True):
    """College games from College Football Data, keyed by ESPN team ids (CFBD uses ESPN's ids)."""
    sys.path.insert(0, HERE)
    import cfb_backtest as CB
    if fresh:   # this season's results change every week: never reuse a cached copy of it
        for fn in os.listdir(CB.CACHE) if os.path.isdir(CB.CACHE) else []:
            if f"year{current_year}" in fn:
                os.remove(os.path.join(CB.CACHE, fn))
    f, G = CB.f, []
    for y in range(2014, current_year + 1):
        for stype in ("regular", "postseason"):
            lines = {f(ln, "id"): ln for ln in CB.get("/lines", year=y, seasonType=stype)}
            for g in CB.get("/games", year=y, seasonType=stype):
                hp, ap = f(g, "homePoints", "home_points"), f(g, "awayPoints", "away_points")
                gg = {"id": f(g, "id"), "season": y, "week": f(g, "week", default=0),
                      "date": f(g, "startDate", "start_date", default=""),
                      "neutral": bool(f(g, "neutralSite", "neutral_site", default=False)),
                      "home": str(f(g, "homeId", "home_id", default=f(g, "homeTeam", "home_team"))),
                      "away": str(f(g, "awayId", "away_id", default=f(g, "awayTeam", "away_team"))),
                      "hname": f(g, "homeTeam", "home_team"), "aname": f(g, "awayTeam", "away_team"),
                      "hdiv": (f(g, "homeClassification", "home_division", default="") or "").lower(),
                      "adiv": (f(g, "awayClassification", "away_division", default="") or "").lower(),
                      "hp": num(hp), "ap": num(ap), "close": None, "tot": None, "mlh": None, "mla": None}
                ln = lines.get(gg["id"])
                if ln:
                    books = ln.get("lines") or []
                    def med(*keys):
                        vals = [num(b.get(k)) for b in books for k in keys if b.get(k) is not None]
                        vals = [v for v in vals if v is not None]
                        return st.median(vals) if vals else None
                    sp = med("spread")
                    gg["close"] = None if sp is None else -sp          # CFBD spread is the home side's number
                    gg["tot"] = med("overUnder", "over_under")
                    gg["mlh"], gg["mla"] = med("homeMoneyline", "home_moneyline"), med("awayMoneyline", "away_moneyline")
                G.append(gg)
    G.sort(key=lambda g: (g["date"], str(g["id"])))
    return G


# ---------------------------------------------------------------- the rating engine
def run(G, p, lg0):
    """Walk forward through the games. Returns per-game predictions (made before the game) and the final state."""
    R, O, D = {}, defaultdict(float), defaultdict(float)
    season, out, lg = None, [], lg0
    for g in G:
        if g["season"] != season:
            season = g["season"]
            for t in R:
                R[t] *= p["regress"]
            for t in list(O):
                O[t] *= p["regress"]; D[t] *= p["regress"]
        h, a = g["home"], g["away"]
        for t, div in ((h, g.get("hdiv")), (a, g.get("adiv"))):
            if t not in R:
                R[t] = 0.0 if div in ("fbs", "", None) else p.get("fcs_start", 0.0)
        hfa = 0.0 if g["neutral"] else p["hfa"]
        pm = R[h] - R[a] + hfa
        pt = 2 * lg + O[h] + O[a] - D[h] - D[a]
        out.append((g, pm, pt))
        if g["hp"] is None or g["ap"] is None:
            continue
        m = max(-p["cap"], min(p["cap"], g["hp"] - g["ap"]))
        err = m - pm
        R[h] += p["k"] * err; R[a] -= p["k"] * err
        eh = g["hp"] - (lg + hfa / 2 + O[h] - D[a]); ea = g["ap"] - (lg - hfa / 2 + O[a] - D[h])
        O[h] += p["kt"] * eh; D[a] -= p["kt"] * eh; O[a] += p["kt"] * ea; D[h] -= p["kt"] * ea
        lg += 0.001 * ((g["hp"] + g["ap"]) / 2 - lg)
    return out, {"R": R, "O": dict(O), "D": dict(D), "lg": lg}


def played(out, yrs, key="close"):
    return [(g, m, t) for g, m, t in out if yrs[0] <= g["season"] <= yrs[1] and g["hp"] is not None and g[key] is not None]


def mae(out, yrs):
    xs = played(out, yrs)
    if not xs:
        return None, None, 0
    return (st.mean(abs(g["hp"] - g["ap"] - m) for g, m, t in xs),
            st.mean(abs(g["hp"] - g["ap"] - g["close"]) for g, m, t in xs), len(xs))


def tot_mae(out, yrs):
    xs = played(out, yrs, "tot")
    return st.mean(abs(g["hp"] + g["ap"] - t) for g, m, t in xs) if xs else None


def tune(G, s):
    start = dict(s["start"], k=0.06, regress=0.6, hfa=2.0, cap=24)
    best = None
    for vals in itertools.product(*s["grid"].values()):
        p = dict(start, **dict(zip(s["grid"], vals)))
        e = mae(run(G, p, s["lg0"])[0], s["tune"])[0]
        if e is not None and (best is None or e < best[0]):
            best = (e, p)
    p = best[1]
    bt = None
    for kt in s["kt"]:
        q = dict(p, kt=kt)
        e = tot_mae(run(G, q, s["lg0"])[0], s["tune"])
        if e is not None and (bt is None or e < bt[0]):
            bt = (e, q)
    return bt[1]


# ---------------------------------------------------------------- probability fits and the gates
def sig(z):
    return 1 / (1 + math.exp(-max(-35, min(35, z))))


def logit(p):
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1 - p))


def fit_logistic(X, y, offset=None, iters=50, ridge=1e-6):
    """Newton's method for P(y=1) = sig(offset + X·w). X rows include any intercept column you want."""
    k = len(X[0]) if X else 0
    w = [0.0] * k
    off = offset or [0.0] * len(X)
    for _ in range(iters):
        g = [0.0] * k; H = [[ridge if i == j else 0.0 for j in range(k)] for i in range(k)]
        for x, yy, o in zip(X, y, off):
            p = sig(o + sum(a * b for a, b in zip(w, x)))
            for i in range(k):
                g[i] += (yy - p) * x[i]
                for j in range(k):
                    H[i][j] += p * (1 - p) * x[i] * x[j]
        step = solve(H, g)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(v) for v in step) < 1e-9:
            break
    return w


def solve(A, b):
    n = len(b); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-15:
            continue
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-15 else 0.0 for i in range(n)]


def ll(p, y):
    p = min(1 - 1e-12, max(1e-12, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def compare(base, model):
    """Per-game log-loss improvement of model over base: mean, t statistic, n."""
    d = [b - m for b, m in zip(base, model)]
    n = len(d)
    if n < 30:
        return {"n": n, "gain": None, "t": None}
    mu = st.mean(d); sd = st.pstdev(d)
    return {"n": n, "gain": mu, "t": mu / (sd / math.sqrt(n)) if sd > 0 else None}


def no_vig(a, b):
    def imp(x):
        return None if x is None or abs(x) < 100 else (100 / (x + 100) if x > 0 else -x / (-x + 100))
    pa, pb = imp(a), imp(b)
    return None if pa is None or pb is None else pa / (pa + pb)


def cover_rows(out, yrs, kind):
    """(x = model edge in points from the home/over side, y = 1 if home covers / over wins), pushes dropped."""
    rows = []
    for g, m, t in out:
        if not (yrs[0] <= g["season"] <= yrs[1]) or g["hp"] is None:
            continue
        if kind == "spread" and g["close"] is not None:
            d = g["hp"] - g["ap"] - g["close"]; x = m - g["close"]
        elif kind == "total" and g["tot"] is not None:
            d = g["hp"] + g["ap"] - g["tot"]; x = t - g["tot"]
        else:
            continue
        if d != 0:
            rows.append((x, 1 if d > 0 else 0, g["season"]))
    return rows


def ats_at(rows, thr):
    w = sum(1 for x, y, s in rows if abs(x) >= thr and (y == 1) == (x > 0))
    l = sum(1 for x, y, s in rows if abs(x) >= thr and (y == 1) != (x > 0))
    return w, l


def edge_gate(out, s, kind):
    """Phase 3 gate for one market. The model passes only if, on seasons it never saw while tuning, its cover chance
    beats the market's 50% by log loss with t >= 2 AND its picks at the threshold chosen on tuning seasons won at
    least 52.4% in each of the last two test seasons. Otherwise its blend weight is 0."""
    tr, te = cover_rows(out, s["tune"], kind), cover_rows(out, s["test"], kind)
    if len(tr) < 200 or len(te) < 200:
        return {"pass": False, "why": "not enough games with a closing line", "n_test": len(te), "b": 0.0, "scale": 1.0}
    scale = st.pstdev([x for x, y, _ in tr]) or 1.0
    a, b = fit_logistic([[1.0, x / scale] for x, y, _ in tr], [y for x, y, _ in tr])
    base = [ll(0.5, y) for x, y, _ in te]
    mod = [ll(sig(a + b * x / scale), y) for x, y, _ in te]
    cmp_ = compare(base, mod)
    thr = max((1, 2, 3, 4, 5, 7), key=lambda t: (lambda w, l: (w / (w + l) if w + l >= 100 else 0))(*ats_at(tr, t)))
    w, l = ats_at(te, thr)
    last = sorted({s_ for _, _, s_ in te})[-2:]
    by = {str(y): ats_at([r for r in te if r[2] == y], thr) for y in last}
    seasons_ok = all(w_ / (w_ + l_) >= BE for w_, l_ in by.values() if w_ + l_) and all(w_ + l_ for w_, l_ in by.values())
    ok = b > 0 and cmp_["gain"] is not None and cmp_["gain"] > 0 and (cmp_["t"] or 0) >= 2 and seasons_ok
    why = ("passed" if ok else
           "the model's edge has the wrong sign" if b <= 0 else
           f"no reliable gain over the closing line (log-loss gain {cmp_['gain']:+.4f}, t = {cmp_['t']:.2f})" if not ((cmp_["gain"] or 0) > 0 and (cmp_["t"] or 0) >= 2) else
           "did not win 52.4% in each of the last two test seasons")
    return {"pass": ok, "why": why, "a": a if ok else 0.0, "b": b if ok else 0.0, "b_fit": b, "a_fit": a, "scale": scale,
            "n_test": len(te), "ll_market": st.mean(base), "ll_model": st.mean(mod), "gain": cmp_["gain"], "t": cmp_["t"],
            "thr": thr, "ats": [w, l], "ats_by_season": by}


def ml_gate(out, s):
    """Phase 4 moneyline blend: logit P(home win) = c0 + c1·logit(no-vig market) + c2·logit(model). Passes only if it
    beats the market alone out of sample (log loss, t >= 2); then the page may use it, else the market stands."""
    def rows(yrs):
        R = []
        for g, m, t in out:
            if yrs[0] <= g["season"] <= yrs[1] and g["hp"] is not None and g["hp"] != g["ap"]:
                pm = no_vig(g["mlh"], g["mla"])
                if pm is not None:
                    R.append((pm, m, 1 if g["hp"] > g["ap"] else 0))
        return R
    tr, te = rows(s["tune"]), rows(s["test"])
    if len(tr) < 200 or len(te) < 200:
        return {"pass": False, "why": "not enough games with moneylines", "n_test": len(te), "w": [0.0, 1.0, 0.0], "sigma": None}
    sigma = st.pstdev([g["hp"] - g["ap"] - m for g, m, t in played(out, s["tune"])]) or 13.0
    pmod = lambda m: min(0.995, max(0.005, 0.5 * (1 + math.erf(m / (sigma * math.sqrt(2))))))
    w = fit_logistic([[1.0, logit(pm), logit(pmod(m))] for pm, m, y in tr], [y for _, _, y in tr])
    base = [ll(pm, y) for pm, m, y in te]
    blend = [ll(sig(w[0] + w[1] * logit(pm) + w[2] * logit(pmod(m))), y) for pm, m, y in te]
    c = compare(base, blend)
    ok = w[2] > 0 and (c["gain"] or 0) > 0 and (c["t"] or 0) >= 2
    return {"pass": ok, "why": "passed" if ok else f"no reliable gain over the no-vig moneyline (log-loss gain {(c['gain'] or 0):+.4f}, t = {(c['t'] or 0):.2f})",
            "w": w if ok else [0.0, 1.0, 0.0], "w_fit": w, "sigma": sigma, "n_test": len(te),
            "ll_market": st.mean(base), "ll_blend": st.mean(blend), "gain": c["gain"], "t": c["t"]}


# ---------------------------------------------------------------- push tables (Phase 2)
def pmf_table(G, kind, s):
    """P(outcome = n) for whole numbers near the closing number, smoothed over nearby closing numbers.
    kind 'margin': outcome = home margin, keyed by the close (expected home margin); 'total': outcome = total points."""
    rows = [((g["close"] if kind == "margin" else g["tot"]), (g["hp"] - g["ap"] if kind == "margin" else g["hp"] + g["ap"]))
            for g in G if g["hp"] is not None and (g["close"] if kind == "margin" else g["tot"]) is not None]
    lo, hi = s["spread_grid"] if kind == "margin" else s["total_grid"]
    width, bw, table = s["width"], s["bw"], {}
    x = lo
    while x <= hi + 1e-9:
        c = int(math.floor(x + 0.5)); cnt = defaultdict(float); tot = below = above = 0.0
        for L, y in rows:
            z = (L - x) / bw
            if abs(z) > 4:
                continue
            w = math.exp(-z * z / 2); tot += w
            if y < c - width: below += w
            elif y > c + width: above += w
            else: cnt[int(y)] += w
        if tot >= 25:
            table[f"{x:g}"] = {"n0": c - width, "p": [round(cnt[n] / tot, 4) for n in range(c - width, c + width + 1)],
                               "lo": round(below / tot, 4), "hi": round(above / tot, 4), "w": round(tot, 1)}
        x += 0.5
    return table


# ---------------------------------------------------------------- one league
def league(name, G, now_year):
    s = SETTINGS[name]
    t0 = time.time()
    p = tune(G, s)
    out, state = run(G, p, s["lg0"])
    sp, tt, ml = edge_gate(out, s, "spread"), edge_gate(out, s, "total"), ml_gate(out, s)
    e_t, e_c, n_t = mae(out, s["test"])
    live = cover_rows(out, (now_year, now_year), "spread")
    lw, ll_ = ats_at(live, sp["thr"])
    ratings = {}
    teams = set(state["R"])
    for t in sorted(teams):
        key = NFL_TO_ESPN.get(t, t) if name == "NFL" else t
        ratings[key] = {"r": round(state["R"].get(t, 0.0), 2), "o": round(state["O"].get(t, 0.0), 2), "d": round(state["D"].get(t, 0.0), 2)}
    sigma = st.pstdev([g["hp"] - g["ap"] - m for g, m, t in played(out, s["tune"])])
    res = {"params": p, "tune": list(s["tune"]), "test": list(s["test"]), "games": len(G),
           "mae": {"model": e_t, "close": e_c, "n": n_t, "total_model": tot_mae(out, s["test"])},
           "spread": sp, "total": tt, "ml": ml, "sigma": sigma,
           "live": {"season": now_year, "ats": [lw, ll_], "thr": sp["thr"]},
           "state": {"lg": round(state["lg"], 3), "hfa": p["hfa"], "asof": max((g["date"] for g in G if g["hp"] is not None), default=None)},
           "ratings": ratings,
           "push": {"margin": pmf_table(G, "margin", s), "total": pmf_table(G, "total", s)},
           "seconds": round(time.time() - t0, 1)}
    return res


def md(results):
    L = ["# Ledger Model", "", f"Updated {results['updatedAt']}. Walk-forward points ratings; settings tuned on early seasons, scored on later seasons never seen while tuning. Break-even at −110 is 52.4%.", ""]
    for name, r in results["leagues"].items():
        if "error" in r:
            L += [f"## {name}", "", f"Not run: {r['error']}", ""]
            continue
        sp, tt, ml = r["spread"], r["total"], r["ml"]
        L += [f"## {name}", "",
              f"Tuned on {r['tune'][0]}–{r['tune'][1]}, tested on {r['test'][0]}–{r['test'][1]}. Settings `{json.dumps({k: r['params'][k] for k in ('k', 'kt', 'regress', 'hfa', 'cap')})}`.",
              f"- Average miss on the margin, test seasons: model {r['mae']['model']:.2f} pts vs closing line {r['mae']['close']:.2f} pts ({r['mae']['n']} games)",
              f"- Spread gate: **{'PASS' if sp['pass'] else 'FAIL'}** ({sp['why']}). Test ATS when the model disagrees by {sp.get('thr', '?')}+ pts: {sp.get('ats', ['?', '?'])[0]}–{sp.get('ats', ['?', '?'])[1]}",
              f"- Total gate: **{'PASS' if tt['pass'] else 'FAIL'}** ({tt['why']}). Test record at {tt.get('thr', '?')}+ pts: {tt.get('ats', ['?', '?'])[0]}–{tt.get('ats', ['?', '?'])[1]}",
              f"- Moneyline blend gate: **{'PASS' if ml['pass'] else 'FAIL'}** ({ml['why']})",
              f"- This season so far (live, out of sample): {r['live']['ats'][0]}–{r['live']['ats'][1]} at {r['live']['thr']}+ pts",
              f"- Push tables: {len(r['push']['margin'])} spread rows, {len(r['push']['total'])} total rows", ""]
    L += ["A failed gate means the page gives this model no weight: it is shown for comparison only."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nfl-csv", default=None, help="local nflverse games.csv (default: download)")
    ap.add_argument("--leagues", default="NFL,FBS")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--year", type=int, default=int(time.strftime("%Y")) if int(time.strftime("%m")) >= 3 else int(time.strftime("%Y")) - 1)
    a = ap.parse_args(argv)
    results = {"updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "version": 1, "leagues": {}}
    for name in a.leagues.split(","):
        try:
            if name == "NFL":
                G = load_nfl(open(a.nfl_csv).read() if a.nfl_csv else fetch(NFL_URL))
            elif name == "FBS":
                if not os.environ.get("CFBD_API_KEY", "").strip():
                    raise RuntimeError("CFBD_API_KEY is not set")
                G = load_cfb(a.year)
            else:
                continue
            results["leagues"][name] = league(name, G, a.year)
            print(f"{name}: done in {results['leagues'][name]['seconds']}s", file=sys.stderr)
        except Exception as e:   # one league failing must not wipe the other's results
            print(f"{name} failed: {e}", file=sys.stderr)
            results["leagues"][name] = {"error": str(e)[:300]}
    os.makedirs(a.out, exist_ok=True)
    prev = os.path.join(a.out, "ledger_model.json")
    if os.path.exists(prev):   # keep the last good run of a league that failed this time
        try:
            old = json.load(open(prev))
            for k, v in results["leagues"].items():
                if "error" in v and k in old.get("leagues", {}) and "error" not in old["leagues"][k]:
                    results["leagues"][k] = dict(old["leagues"][k], stale=v["error"])
        except Exception:
            pass
    with open(prev, "w") as fh:
        json.dump(results, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "ledger_model.md"), "w") as fh:
        fh.write(md(results))
    print(md(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
