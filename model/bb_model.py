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
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger_model as LM  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data", "bb")
# Seasons are labelled by the year they end (2026 = 2025-26); the WNBA by its calendar year. Tuned on the first two,
# tested on the next two; the current season is scored live.
COMMON = dict(tune=(2023, 2024), test=(2025, 2026), width=20, bw=1.0, start=dict(kt=0.04))
SETTINGS = {
    "NBA": dict(COMMON, lg0=114.0, grid=dict(k=[0.03, 0.05, 0.07], regress=[0.5, 0.65, 0.8], hfa=[1.5, 2.25, 3.0], cap=[20, 30]),
                kt=[0.01, 0.02, 0.04], spread_grid=(-20.0, 20.0), total_grid=(195.0, 260.0)),
    "WNBA": dict(COMMON, lg0=81.0, grid=dict(k=[0.03, 0.05, 0.07], regress=[0.5, 0.65, 0.8], hfa=[1.5, 2.25, 3.0], cap=[20, 30]),
                 kt=[0.01, 0.02, 0.04], spread_grid=(-20.0, 20.0), total_grid=(140.0, 190.0)),
    "MBB": dict(COMMON, lg0=71.0, grid=dict(k=[0.04, 0.06, 0.08], regress=[0.55, 0.7, 0.85], hfa=[2.5, 3.25, 4.0], cap=[20, 30], fcs_start=[-12.0, -20.0]),
                kt=[0.02, 0.04, 0.06], spread_grid=(-35.0, 35.0), total_grid=(110.0, 180.0)),
    "WBB": dict(COMMON, lg0=66.0, grid=dict(k=[0.04, 0.06, 0.08], regress=[0.55, 0.7, 0.85], hfa=[2.5, 3.25, 4.0], cap=[25, 35], fcs_start=[-12.0, -20.0]),
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
        d = json.load(open(os.path.join(data, f"{lg}_{s}.json")))
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
    if len(lined) < 400:
        raise RuntimeError(f"only {len(lined)} games with a closing line on disk")
    LM.SETTINGS[name] = s                       # the football engine reads its settings by league name
    res = LM.league(name, G, current_season(name))
    res["seasons"] = have
    res["lined"] = len(lined)
    return res


def md(results):
    text = LM.md(results).replace("# Ledger Model", "# Basketball Ledger Model", 1)
    return text


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
            old = json.load(open(prev))
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
