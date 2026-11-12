#!/usr/bin/env python3
"""Does ESPN's BPI beat basketball closing lines, and from what gap? A backtest for the basketball lean rule.

For past games that have a closing line in data/bb (see bb_data.py), this fetches ESPN's pregame win chance for the
home team from each game's summary (the matchup predictor, or the first point of ESPN's win-probability chart, which
is the same pregame number), turns it into a projected margin, and grades "take the side BPI likes against the closing
spread" at a range of minimum gaps between BPI and the line.

Fair test: the margin scale (SIG) and the gap are chosen on a tuning set and scored on a test set the choice never
saw. NBA tunes on 2022-23 and tests on 2025-26; college (one season with closing lines) tunes on games before Jan 15
and tests on the rest. A gap passes only if it beats the break-even rate at -110 (52.4%) on both sets, with at least
100 test games and the test rate at least one standard error above break-even (Nov 12, 2026: the first run had
women's college "passing" at a 1-point gap with 52.5% on test games, which is break-even, not an edge). The smallest
passing gap is the rule. If none passes, the lean rule stays off for that league.

Runs on GitHub Actions ("Basketball BPI test" workflow); standard library only.
  python3 model/bb_bpi.py [--max-requests N] [--leagues NBA,MBB,WBB] [--analyze-only]
Writes
  data/bb/bpi_<LG>_<season>.json   {eid: home win chance in percent, or null when ESPN had none}
  model/results/bb_bpi.json, model/results/bb_bpi.md
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fetch_bb as BB  # noqa: E402
import fetch_scores as FS  # noqa: E402

DATA = os.path.join(ROOT, "data", "bb")
OUT = os.path.join(ROOT, "model", "results")
BREAK_EVEN = 52.38
MIN_TEST = 100
Z_MIN = 1.0      # the test-set rate must sit at least one standard error above break-even, not just touch it
GAPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12]
# (league, tuning seasons, test seasons, split date for single-season college tests)
PLAN = {
    "NBA": {"seasons": [2023, 2026], "tune": [2023], "test": [2026]},
    "MBB": {"seasons": [2026], "split": "2026-01-15"},
    "WBB": {"seasons": [2026], "split": "2026-01-15"},
}


def inv_phi(p):
    """Inverse standard normal CDF (Acklam), as in bb_ledger.py."""
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


def pregame_hp(summ):
    """Home win chance (percent) before tip from an ESPN summary, or None."""
    if not summ:
        return None
    p = (summ.get("predictor") or {})
    hp = FS.to_num((p.get("homeTeam") or {}).get("gameProjection"))
    if hp is None:
        ap = FS.to_num((p.get("awayTeam") or {}).get("gameProjection"))
        hp = 100 - ap if ap is not None else None
    if hp is None:
        wp = summ.get("winprobability") or []
        if wp and isinstance(wp[0], dict):
            x = FS.to_num(wp[0].get("homeWinPercentage"))
            hp = x * 100 if x is not None and x <= 1 else x
    if hp is None or not 0 < hp < 100:
        return None
    return round(hp, 1)


def load_season(lg, season):
    path = os.path.join(DATA, f"{lg}_{season}.json")
    if not os.path.exists(path):
        return []
    d = FS.read_json(path, {})
    cols = d.get("cols") or []
    ix = {c: i for i, c in enumerate(cols)}
    out = []
    for r in d.get("games") or []:
        if r[ix["hm"]] is None or r[ix["hs"]] is None or r[ix["as"]] is None:
            continue
        out.append({"id": str(r[ix["id"]]), "date": r[ix["date"]], "hm": float(r[ix["hm"]]),
                    "margin": r[ix["hs"]] - r[ix["as"]], "neu": bool(r[ix["neu"]])})
    return out


def fetch_bpi(lg, season, games, budget, get=None, pause=0.12):
    path = os.path.join(DATA, f"bpi_{lg}_{season}.json")
    cache = FS.read_json(path, {})
    get = get or FS.get_json
    used = 0
    for g in games:
        if used >= budget:
            break
        if g["id"] in cache:
            continue
        summ = get(f"{BB.BASE}/{BB.SPORT[lg]}/summary?event={g['id']}")
        used += 1
        if pause:
            time.sleep(pause)
        if summ is None:
            continue                       # network trouble: ask again next run
        cache[g["id"]] = pregame_hp(summ)
        if used % 200 == 0:
            FS.write_json(path, cache)
    FS.write_json(path, cache)
    return cache, used


def fit_sig(rows):
    """Scale that best maps inverse-normal(BPI chance) onto the closing line (least squares through 0)."""
    zz = sum(r["z"] * r["z"] for r in rows)
    return sum(r["z"] * r["hm"] for r in rows) / zz if zz else None


def z_over_break_even(rec):
    n = rec["w"] + rec["l"]
    return (rec["w"] / n - BREAK_EVEN / 100) / math.sqrt(0.25 / n) if n else None


def grade(rows, sig, gap):
    w = l = p = 0
    for r in rows:
        edge = sig * r["z"] - r["hm"]
        if abs(edge) < gap or edge == 0:
            continue
        cover = r["margin"] - r["hm"]        # > 0: home covered
        if cover == 0:
            p += 1
        elif (cover > 0) == (edge > 0):
            w += 1
        else:
            l += 1
    n = w + l
    return {"w": w, "l": l, "p": p, "pct": round(100 * w / n, 2) if n else None,
            "z": round((w - n / 2) / math.sqrt(n / 4), 2) if n else None}


def analyze(lg, rows_by_season):
    plan = PLAN[lg]
    allrows = [r for rs in rows_by_season.values() for r in rs]
    if "split" in plan:
        tune = [r for r in allrows if r["date"][:10] < plan["split"]]
        test = [r for r in allrows if r["date"][:10] >= plan["split"]]
        split_txt = f"tuned on games before {plan['split']}, tested on games after"
    else:
        tune = [r for s in plan["tune"] for r in rows_by_season.get(s, [])]
        test = [r for s in plan["test"] for r in rows_by_season.get(s, [])]
        split_txt = f"tuned on {', '.join(map(str, plan['tune']))}, tested on {', '.join(map(str, plan['test']))}"
    early = [r for r in allrows if dt.date.fromisoformat(r["date"][:10]).month in (10, 11)]
    res = {"lg": lg, "split": split_txt, "n_tune": len(tune), "n_test": len(test), "n_early": len(early)}
    if len(tune) < 50 or len(test) < 50:
        res.update({"gap": None, "note": "not enough games with both a closing line and an ESPN win chance"})
        return res
    sig = fit_sig(tune)
    res["sig"] = round(sig, 2)
    table = []
    passing = None
    for gap in GAPS:
        t, s, e = grade(tune, sig, gap), grade(test, sig, gap), grade(early, sig, gap)
        zb = z_over_break_even(s)
        ok = (t["pct"] or 0) >= BREAK_EVEN and (s["pct"] or 0) >= BREAK_EVEN and (s["w"] + s["l"]) >= MIN_TEST and (zb or 0) >= Z_MIN
        s["zbe"] = round(zb, 2) if zb is not None else None
        table.append({"gap": gap, "tune": t, "test": s, "early": e, "pass": ok})
        if ok and passing is None and gap > 0:
            passing = gap
    res["table"] = table
    res["gap"] = passing
    res["note"] = (f"BPI beats the close by a gap of {passing}+ points on both sets" if passing is not None
                   else "no gap beats the closing line on both the tuning and the test set")
    return res


def write_report(results, used):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    out = {"updatedAt": now, "breakEven": BREAK_EVEN, "minTest": MIN_TEST, "leagues": results}
    os.makedirs(OUT, exist_ok=True)
    FS.write_json(os.path.join(OUT, "bb_bpi.json"), out)
    md = [f"# Basketball: ESPN BPI against the closing line\n",
          f"Updated {now}. Take the side ESPN's BPI likes against the closing spread when BPI's projected margin differs "
          f"from the line by at least the gap. The margin scale and gap are chosen on the tuning set and scored on a test "
          f"set they never saw; a gap passes only at {BREAK_EVEN}%+ (break-even at -110) on both, with {MIN_TEST}+ test games "
          f"and the test rate at least {Z_MIN:g} standard error above break-even (z over break-even in the Test column). Many gaps "
          f"and three leagues are tried, so a pass is a candidate rule that the live background record must confirm. "
          f"Requests this run: {used}.\n"]
    for lg, r in results.items():
        md.append(f"## {lg}\n")
        md.append(f"{r['split']}; {r['n_tune']} tuning and {r['n_test']} test games with an ESPN win chance "
                  f"({r['n_early']} in October-November). " + (f"Margin scale {r['sig']}. " if r.get("sig") else "")
                  + f"**{r['note']}**.\n")
        if r.get("table"):
            md.append("| Gap | Tuning | Test (z over break-even) | Oct–Nov | Pass |\n|---|---|---|---|---|")
            for row in r["table"]:
                f = lambda x: f"{x['w']}-{x['l']} ({x['pct']}%)" if x["pct"] is not None else "—"
                zt = f" z {row['test']['zbe']}" if row["test"].get("zbe") is not None else ""
                md.append(f"| {row['gap']}+ | {f(row['tune'])} | {f(row['test'])}{zt} | {f(row['early'])} | {'yes' if row['pass'] else 'no'} |")
            md.append("")
    with open(os.path.join(OUT, "bb_bpi.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="NBA,MBB,WBB")
    ap.add_argument("--max-requests", type=int, default=9000)
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args(argv)
    budget = 0 if args.analyze_only else args.max_requests
    results, used = {}, 0
    for lg in [x for x in args.leagues.split(",") if x in PLAN]:
        rows_by_season = {}
        for season in PLAN[lg]["seasons"]:
            games = load_season(lg, season)
            games.sort(key=lambda g: g["date"], reverse=True)       # newest first, so a cut-off run still helps
            cache, n = fetch_bpi(lg, season, games, max(0, budget - used))
            used += n
            rows = []
            for g in games:
                hp = cache.get(g["id"])
                if hp is not None:
                    rows.append(dict(g, z=inv_phi(hp / 100)))
            rows_by_season[season] = rows
            print(f"{lg} {season}: {len(games)} games with a closing line, {len(rows)} with ESPN's win chance, {n} requests")
        results[lg] = analyze(lg, rows_by_season)
        print(f"{lg}: {results[lg]['note']}")
    write_report(results, used)


if __name__ == "__main__":
    main()
