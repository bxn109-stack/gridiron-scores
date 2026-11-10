#!/usr/bin/env python3
"""Basketball Ledger Model: the football Ledger Model's engine (model/ledger_model.py) run on basketball.

Same method and the same gates as football, so the page treats both sports alike:
  * walk-forward team ratings (each prediction uses only games played before it),
  * settings tuned on the early seasons only, then scored on later seasons it never saw,
  * the spread and total gates (log-loss gain over the closing line with t >= 2, and 52.4%+ in each of the last two
    test seasons) and the moneyline blend gate; a failed gate gives the model no weight on the page,
  * push tables (the chance a game lands exactly on a number, given the closing spread or total),
  * current team ratings keyed by ESPN team id, which bb_ledger.py turns into each game's model numbers.

Data: data/bb/<LG>_<season>.json from model/bb_data.py (ESPN scores and ESPN's last listed line, which for a
finished game is its closing line). Output: model/results/bb_model.json (+ .md). Aggregate numbers and ratings only.

  python3 model/bb_model.py [--leagues NBA,MBB,WBB,WNBA] [--data DIR] [--out DIR]
"""
import argparse
import itertools
import json
import statistics
import os
import sys
import time
from collections import Counter


def _rj(path):
    with open(path) as f:
        return json.load(f)


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger_model as LM  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data", "bb")
# Seasons are labelled by the year they end (2026 = 2025-26); the WNBA by its calendar year. Tuned on the first two,
# tested on the next two; the current season is scored live.
COMMON = dict(tune=(2023, 2024), test=(2025, 2026), width=20, bw=1.0, start=dict(kt=0.04))
COLLEGE = dict(COMMON, tune=(2024, 2024))     # 6,000 games a season: one tuning season is plenty
SETTINGS = {
    "NBA": dict(COMMON, lg0=114.0, grid=dict(k=[0.03, 0.05, 0.07], regress=[0.5, 0.65, 0.8], hfa=[1.5, 2.25, 3.0], cap=[20, 30]),
                kt=[0.01, 0.02, 0.04], spread_grid=(-20.0, 20.0), total_grid=(195.0, 260.0)),
    "WNBA": dict(COMMON, lg0=81.0, grid=dict(k=[0.03, 0.05, 0.07], regress=[0.5, 0.65, 0.8], hfa=[1.5, 2.25, 3.0], cap=[20, 30]),
                 kt=[0.01, 0.02, 0.04], spread_grid=(-20.0, 20.0), total_grid=(140.0, 190.0)),
    "MBB": dict(COLLEGE, lg0=71.0, grid=dict(k=[0.06, 0.08, 0.11], regress=[0.7, 0.85, 0.95], hfa=[2.5, 3.25, 4.0], cap=[20, 30], fcs_start=[-12.0, -20.0]),
                kt=[0.02, 0.04, 0.06], spread_grid=(-35.0, 35.0), total_grid=(110.0, 180.0)),
    "WBB": dict(COLLEGE, lg0=66.0, grid=dict(k=[0.06, 0.08, 0.11], regress=[0.7, 0.85, 0.95], hfa=[2.5, 3.25, 4.0], cap=[25, 35], fcs_start=[-12.0, -20.0]),
                kt=[0.02, 0.04, 0.06], spread_grid=(-40.0, 40.0), total_grid=(100.0, 175.0)),
}
COLS = ["id", "date", "type", "neu", "hid", "aid", "h", "a", "hs", "as", "hm", "tot", "mlh", "mla", "book", "hst", "ast"]
D1_MIN_GAMES = 8          # a team with fewer games than this in every loaded season is treated as non-Division I


def seasons_on_disk(lg, data):
    out = []
    for fn in os.listdir(data) if os.path.isdir(data) else []:
        if fn.startswith(lg + "_") and fn.endswith(".json"):
            try:
                out.append(int(fn[len(lg) + 1:-5]))
            except ValueError:
                pass
    return sorted(out)


def load_bb(lg, data=DATA):
    """Every finished game of every season on disk -> the football engine's game dicts (close = expected home margin)."""
    rows = []
    for s in seasons_on_disk(lg, data):
        d = _rj((os.path.join(data, f"{lg}_{s}.json")))
        cols = d.get("cols") or COLS
        rows += [(s, dict(zip(cols, r))) for r in d.get("games") or []]
    counts = {}
    for s, r in rows:
        c = counts.setdefault(s, Counter())
        c[r["hid"]] += 1
        c[r["aid"]] += 1
    d1 = {t for c in counts.values() for t, n in c.items() if n >= D1_MIN_GAMES}
    G = []
    for s, r in rows:
        if r["hs"] is None or r["as"] is None:
            continue
        G.append({"id": r["id"], "season": s, "date": r["date"] or "", "neutral": bool(r["neu"]),
                  "home": str(r["hid"]), "away": str(r["aid"]), "hname": r["h"], "aname": r["a"],
                  "hdiv": "fbs" if r["hid"] in d1 else "non", "adiv": "fbs" if r["aid"] in d1 else "non",
                  "hp": float(r["hs"]), "ap": float(r["as"]), "close": r["hm"], "tot": r["tot"], "mlh": r["mlh"], "mla": r["mla"]})
    G.sort(key=lambda g: (g["date"], str(g["id"])))
    return G


def current_season(lg):
    t = time.gmtime()
    if lg == "WNBA":
        return t.tm_year
    return t.tm_year + 1 if t.tm_mon >= 7 else t.tm_year


def league(name, G):
    s = SETTINGS[name]
    have = sorted({g["season"] for g in G})
    if not have:
        raise RuntimeError("no games on disk yet")
    lined = [g for g in G if g["close"] is not None]
    # without closing lines the ratings are still built (the page shows them for comparison); the gates simply fail
    # with "not enough games with a closing line" until model/bb_data.py has back-filled them
    LM.SETTINGS[name] = s                       # the football engine reads its settings by league name
    res = league_run(name, G, current_season(name), s)
    res["seasons"] = have
    res["lined"] = len(lined)
    return res


def tune(G, s):
    """The football tuner, scored on actual margins and totals of every tuning game (it needs no closing lines)."""
    yrs = s["tune"]
    start = dict(s["start"], k=0.06, regress=0.6, hfa=2.0, cap=24)

    def err(out, kind):
        xs = [abs((g["hp"] - g["ap"] - m) if kind == "m" else (g["hp"] + g["ap"] - t)) for g, m, t in out
              if yrs[0] <= g["season"] <= yrs[1] and g["hp"] is not None]
        return sum(xs) / len(xs) if xs else None
    best = None
    for vals in itertools.product(*s["grid"].values()):
        p = dict(start, **dict(zip(s["grid"], vals)))
        e = err(LM.run(G, p, s["lg0"])[0], "m")
        if e is not None and (best is None or e < best[0]):
            best = (e, p)
    if best is None:
        raise RuntimeError("no finished games in the tuning seasons")
    bt = None
    for kt in s["kt"]:
        q = dict(best[1], kt=kt)
        e = err(LM.run(G, q, s["lg0"])[0], "t")
        if e is not None and (bt is None or e < bt[0]):
            bt = (e, q)
    return bt[1]


def league_run(name, G, now_year, s):
    """football's ledger_model.league with the tuner above."""
    t0 = time.time()
    p = tune(G, s)
    out, state = LM.run(G, p, s["lg0"])
    sp, tt, ml = LM.edge_gate(out, s, "spread"), LM.edge_gate(out, s, "total"), LM.ml_gate(out, s)
    e_t, e_c, n_t = LM.mae(out, s["test"])
    lw, ll_ = LM.ats_at(LM.cover_rows(out, (now_year, now_year), "spread"), sp.get("thr", 3))
    ratings = {t: {"r": round(state["R"].get(t, 0.0), 2), "o": round(state["O"].get(t, 0.0), 2), "d": round(state["D"].get(t, 0.0), 2)}
               for t in sorted(state["R"])}
    resid = [g["hp"] - g["ap"] - m for g, m, t in out if s["tune"][0] <= g["season"] <= s["tune"][1] and g["hp"] is not None]
    sigma = statistics.pstdev(resid) if resid else None
    return {"params": p, "tune": list(s["tune"]), "test": list(s["test"]), "games": len(G),
            "mae": {"model": e_t, "close": e_c, "n": n_t, "total_model": LM.tot_mae(out, s["test"])},
            "spread": sp, "total": tt, "ml": ml, "sigma": sigma,
            "live": {"season": now_year, "ats": [lw, ll_], "thr": sp.get("thr", 3)},
            "state": {"lg": round(state["lg"], 3), "hfa": p["hfa"], "asof": max((g["date"] for g in G if g["hp"] is not None), default=None)},
            "ratings": ratings, "push": {"margin": LM.pmf_table(G, "margin", s), "total": LM.pmf_table(G, "total", s)},
            "seconds": round(time.time() - t0, 1)}


def md(results):
    f = lambda v, d=2: "—" if v is None else f"{v:.{d}f}"
    L = ["# Basketball Ledger Model", "", f"Updated {results['updatedAt']}. The football Ledger Model's engine on basketball: walk-forward points ratings; "
         "settings tuned on early seasons, scored on later seasons never seen while tuning, against ESPN's closing lines. Break-even at −110 is 52.4%.", ""]
    for name, r in results["leagues"].items():
        if "error" in r:
            L += [f"## {name}", "", f"Not run: {r['error']}", ""]
            continue
        sp, tt, ml, m = r["spread"], r["total"], r["ml"], r["mae"]
        L += [f"## {name}", "",
              f"Seasons on disk {r.get('seasons')}; {r.get('lined', 0)} games with a closing line. Tuned on {r['tune'][0]}–{r['tune'][1]}, tested on {r['test'][0]}–{r['test'][1]}. "
              f"Settings `{json.dumps({k: r['params'][k] for k in ('k', 'kt', 'regress', 'hfa', 'cap') if k in r['params']})}`.",
              f"- Average miss on the margin, test seasons: model {f(m.get('model'))} pts vs closing line {f(m.get('close'))} pts ({m.get('n', 0)} games)",
              f"- Spread gate: **{'PASS' if sp.get('pass') else 'FAIL'}** ({sp.get('why')})",
              f"- Total gate: **{'PASS' if tt.get('pass') else 'FAIL'}** ({tt.get('why')})",
              f"- Moneyline blend gate: **{'PASS' if ml.get('pass') else 'FAIL'}** ({ml.get('why')})",
              f"- Ratings for {len(r['ratings'])} teams; push tables: {len(r['push']['margin'])} spread rows, {len(r['push']['total'])} total rows", ""]
    L += ["A failed gate means the page gives this model no weight: it is shown for comparison only."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="NBA,MBB,WBB,WNBA")
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args(argv)
    results = {"updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "version": 1, "sport": "basketball", "leagues": {}}
    for name in [x for x in a.leagues.split(",") if x in SETTINGS]:
        try:
            G = load_bb(name, a.data)
            results["leagues"][name] = league(name, G)
            print(f"{name}: {len(G)} games, done in {results['leagues'][name]['seconds']}s", file=sys.stderr)
        except Exception as e:   # one league failing must not wipe the others' results
            print(f"{name} failed: {e}", file=sys.stderr)
            results["leagues"][name] = {"error": str(e)[:300]}
    os.makedirs(a.out, exist_ok=True)
    prev = os.path.join(a.out, "bb_model.json")
    if os.path.exists(prev):   # keep the last good run of a league that failed this time
        try:
            old = _rj((prev))
            for k, v in results["leagues"].items():
                if "error" in v and k in old.get("leagues", {}) and "error" not in old["leagues"][k]:
                    results["leagues"][k] = dict(old["leagues"][k], stale=v["error"])
        except Exception:
            pass
    with open(prev, "w") as fh:
        json.dump(results, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "bb_model.md"), "w") as fh:
        fh.write(md(results))
    print(md(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
