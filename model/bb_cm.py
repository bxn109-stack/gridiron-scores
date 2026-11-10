#!/usr/bin/env python3
"""Basketball Claude Model: Claude's own basketball algorithm, built only from box-score data, and gated.

It never uses other models, expert picks or the betting market as inputs. The market's closing line is used only to
score the backtest and, on the page, to pick a side and to grade. It is labeled unvalidated (and never moves a chance
or the value check) until it passes the same out-of-sample gate as the Ledger Model on seasons it never saw.

The model, per league:
  * possessions per team-game = FGA - OREB + TO + 0.44 FTA (when ESPN omits OREB or TO, a league-fitted fraction of
    FGA + 0.44 FTA); points per 100 possessions for offense and defense, and pace,
  * walk-forward, opponent-adjusted ratings: offense O, defense D and pace P per team, each nudged after every game
    toward what it showed against that opponent (rates k and kp), shrunk toward the league between seasons,
  * home court (points), and a rest effect for the second night of a back-to-back, both fitted on tuning seasons,
  * projected score = (league efficiency + O - opponent D +/- home court / 2) x projected pace / 100,
  * cover and over chances: a logistic fit of the outcome on the model's edge over the line, on tuning seasons only,
  * four-factor profiles (effective FG%, turnover rate, offensive rebound rate, free-throw rate) for the checklist,
  * player projections for props from this season's box scores (season and last-five means, pace-adjusted).
Settings are tuned on the early seasons, then the whole walk-forward run is scored on later seasons. The gate: the
football edge gate (log-loss gain over the closing line with t >= 2, and 52.4%+ in each of the last two test seasons).

Output: model/results/bb_cm.json (team and player numbers, fits, backtest and gate) and bb_cm.md.
  python3 model/bb_cm.py [--leagues NBA,MBB,WBB,WNBA] [--data DIR] [--box DIR] [--out DIR]
"""
import argparse
import glob
import itertools
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from zoneinfo import ZoneInfo


def _rj(path):
    with open(path) as f:
        return json.load(f)


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ledger_model as LM  # noqa: E402
import bb_model as BM  # noqa: E402

VERSION = "bbcm-1"
BOX = os.path.join(ROOT, "box", "bb")
STATS = ["fga", "fgm", "fta", "ftm", "tpa", "tpm", "reb", "oreb", "ast", "to", "pts"]
LG_EFF = {"NBA": 114.0, "WNBA": 103.0, "MBB": 104.0, "WBB": 95.0}
LG_PACE = {"NBA": 99.0, "WNBA": 80.0, "MBB": 68.0, "WBB": 70.0}
GRID = dict(k=[0.04, 0.07, 0.11], kp=[0.03, 0.06], regress=[0.5, 0.7, 0.85], hca=[2.0, 3.0, 4.0])
RES_CAP = 30.0            # points per 100 possessions: the most one game can count above or below expectation
PROP_STATS = ["min", "pts", "reb", "ast", "stl", "blk", "to", "tpm", "fgm", "ftm", "oreb", "dreb", "fp"]
PROP_MAIN = ["pts", "reb", "ast", "stl", "blk", "to", "tpm", "fp"]     # markets books post: these keep their spread and last five


# ---------------------------------------------------------------- data
def load_games(lg, data):
    """bb_model's game dicts plus each side's box totals (None when ESPN gave none)."""
    rows = []
    for s in BM.seasons_on_disk(lg, data):
        d = _rj((os.path.join(data, f"{lg}_{s}.json")))
        cols = d.get("cols") or BM.COLS
        for r in d.get("games") or []:
            rows.append((s, dict(zip(cols, r))))
    G = {g["id"]: g for g in BM.load_bb(lg, data)}
    for s, r in rows:
        g = G.get(r["id"])
        if g:
            g["hst"] = dict(zip(STATS, r["hst"])) if r.get("hst") else None
            g["ast"] = dict(zip(STATS, r["ast"])) if r.get("ast") else None
    return sorted(G.values(), key=lambda g: (g["date"], str(g["id"])))


def raw_poss(x):
    if not x or x.get("fga") is None or x.get("fta") is None:
        return None, False
    if x.get("oreb") is not None and x.get("to") is not None:
        return x["fga"] - x["oreb"] + x["to"] + 0.44 * x["fta"], True
    return x["fga"] + 0.44 * x["fta"], False


def poss_factor(G):
    """Fraction that turns FGA + 0.44 FTA into possessions when OREB/TO are missing (fitted where both are present)."""
    num = den = 0.0
    for g in G:
        for x in (g.get("hst"), g.get("ast")):
            p, full = raw_poss(x)
            if full:
                num += p
                den += x["fga"] + 0.44 * x["fta"]
    return num / den if den > 1000 else 0.97


def game_pace(g, f):
    ps = []
    for x in (g.get("hst"), g.get("ast")):
        p, full = raw_poss(x)
        if p is not None:
            ps.append(p if full else p * f)
    return st.mean(ps) if ps else None


ET = ZoneInfo("America/New_York")


def et_day(iso):
    """US Eastern date of a game (a 7 PM ET tip is the next day in UTC)."""
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date().isoformat()
    except (ValueError, AttributeError):
        return (iso or "")[:10]


def rest_days(G):
    """Days since each side's previous game (None for its first of the season)."""
    last = {}
    out = {}
    for g in G:
        day = et_day(g["date"])
        for side, t in (("h", g["home"]), ("a", g["away"])):
            prev = last.get((g["season"], t))
            if prev:
                try:
                    out[(g["id"], side)] = (time.mktime(time.strptime(day, "%Y-%m-%d")) - time.mktime(time.strptime(prev, "%Y-%m-%d"))) / 86400
                except ValueError:
                    pass
            last[(g["season"], t)] = day
    return out


# ---------------------------------------------------------------- the engine
def run(G, p, lg, f, rest):
    """Walk forward. Returns [(g, margin, total, pace)] predicted before each game, and the final state."""
    O, D, P, N = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(int)
    eff, pace0 = LG_EFF[lg], LG_PACE[lg]
    season, out = None, []
    for g in G:
        if g["season"] != season:
            season = g["season"]
            for d_ in (O, D, P):
                for t in list(d_):
                    d_[t] *= p["regress"]
            N.clear()
        h, a = g["home"], g["away"]
        for t, div in ((h, g.get("hdiv")), (a, g.get("adiv"))):
            if t not in O and div == "non":
                O[t], D[t] = -8.0, -8.0                 # non-Division I opponents start well below average
        hca = 0.0 if g["neutral"] else p["hca"]
        pc = pace0 + P[h] + P[a]
        b2b = p.get("b2b", 0.0) * ((1 if rest.get((g["id"], "h")) == 1 else 0) - (1 if rest.get((g["id"], "a")) == 1 else 0))
        oh = eff + O[h] - D[a]
        oa = eff + O[a] - D[h]
        m = (oh - oa) * pc / 100 + hca + b2b
        t = (oh + oa) * pc / 100
        out.append((g, m, t, pc))
        if g.get("hdiv") == "non" or g.get("adiv") == "non":
            continue                                     # games against non-Division I teams teach the ratings nothing
        gp = game_pace(g, f)
        if gp is None:
            # no box totals: learn from points alone, at the projected pace
            gp = pc
        if gp <= 0:
            continue
        ah = (g["hp"] - hca / 2) / gp * 100
        aa = (g["ap"] + hca / 2) / gp * 100
        k = p["k"] * (1.5 if N[h] < 5 or N[a] < 5 else 1.0)
        cap = lambda x, c: max(-c, min(c, x))           # one blowout cannot move a team far
        eh, ea = cap(ah - oh, RES_CAP), cap(aa - oa, RES_CAP)
        O[h] += k * eh; D[a] -= k * eh
        O[a] += k * ea; D[h] -= k * ea
        ep = cap(gp - pc, 12.0)
        P[h] += p["kp"] * ep / 2; P[a] += p["kp"] * ep / 2
        N[h] += 1; N[a] += 1
    return out, {"O": dict(O), "D": dict(D), "P": dict(P)}


def d1(g):
    return g.get("hdiv") != "non" and g.get("adiv") != "non"


def tune(G, lg, f, rest, yrs):
    best = None
    for vals in itertools.product(*GRID.values()):
        p = dict(zip(GRID, vals))
        out, _ = run(G, p, lg, f, rest)
        xs = [abs(g["hp"] - g["ap"] - m) for g, m, t, pc in out if yrs[0] <= g["season"] <= yrs[1] and d1(g)]
        if xs:
            e = st.mean(xs)
            if best is None or e < best[0]:
                best = (e, p)
    p = best[1]
    # the back-to-back effect: the average miss for teams on the second night, fitted on the tuning seasons
    out, _ = run(G, dict(p, b2b=0.0), lg, f, rest)
    res = []
    for g, m, t, pc in out:
        if not (yrs[0] <= g["season"] <= yrs[1]) or not d1(g):
            continue
        hb, ab = rest.get((g["id"], "h")) == 1, rest.get((g["id"], "a")) == 1
        if hb != ab:
            res.append((g["hp"] - g["ap"] - m) * (1 if hb else -1))
    p["b2b"] = round(max(-4.0, min(0.0, st.mean(res))), 2) if len(res) >= 100 else 0.0
    return p


def lm_rows(out):
    """Rows the football gate functions read: (g, margin, total)."""
    return [(g, m, t) for g, m, t, pc in out]


def backtest(out, yrs):
    sp_w = sp_l = su_w = su_n = ou_w = ou_l = 0
    errs, mk_errs, terrs = [], [], []
    for g, m, t, pc in out:
        if not (yrs[0] <= g["season"] <= yrs[1]) or not d1(g):
            continue
        d = g["hp"] - g["ap"]
        errs.append(abs(d - m))
        if d != 0 and m != 0:
            su_n += 1
            su_w += (d > 0) == (m > 0)
        terrs.append(abs(g["hp"] + g["ap"] - t))
        if g["close"] is not None:
            mk_errs.append(abs(d - g["close"]))
            c = d - g["close"]
            e = m - g["close"]
            if c != 0 and e != 0:
                if (c > 0) == (e > 0):
                    sp_w += 1
                else:
                    sp_l += 1
        if g["tot"] is not None:
            c = g["hp"] + g["ap"] - g["tot"]
            e = t - g["tot"]
            if c != 0 and e != 0:
                if (c > 0) == (e > 0):
                    ou_w += 1
                else:
                    ou_l += 1
    r = lambda x: round(x, 2) if x is not None else None
    return {"seasons": f"{yrs[0]}–{yrs[1]}", "games": len(errs), "ats": r(100 * sp_w / (sp_w + sp_l)) if sp_w + sp_l else None,
            "atsN": sp_w + sp_l, "su": r(100 * su_w / su_n) if su_n else None, "ou": r(100 * ou_w / (ou_w + ou_l)) if ou_w + ou_l else None,
            "ouN": ou_w + ou_l, "mae": r(st.mean(errs)) if errs else None, "mae_mkt": r(st.mean(mk_errs)) if mk_errs else None,
            "tot_mae": r(st.mean(terrs)) if terrs else None}


def four_factors(G, f, season):
    """Season-to-date four factors per team, for the checklist (offense and defense)."""
    acc = defaultdict(lambda: defaultdict(float))
    for g in G:
        if g["season"] != season:
            continue
        for t, mine, theirs in ((g["home"], g.get("hst"), g.get("ast")), (g["away"], g.get("ast"), g.get("hst"))):
            if not mine or not theirs or mine.get("fga") is None or theirs.get("fga") is None:
                continue
            A = acc[t]
            A["g"] += 1
            for pre, x in (("", mine), ("d_", theirs)):
                for k in ("fga", "fgm", "tpm", "fta", "ftm", "to", "oreb", "reb", "pts"):
                    if x.get(k) is not None:
                        A[pre + k] += x[k]
                        A[pre + k + "_n"] += 1
                p, full = raw_poss(x)
                if p is not None:
                    A[pre + "poss"] += p if full else p * f
    out = {}
    for t, A in acc.items():
        if A["g"] < 1:
            continue

        def ff(pre):
            fga = A[pre + "fga"] or 1
            res = {"efg": round(100 * (A[pre + "fgm"] + 0.5 * A[pre + "tpm"]) / fga, 1), "ftr": round(100 * A[pre + "fta"] / fga, 1)}
            if A[pre + "to_n"] and A[pre + "poss"]:
                res["tov"] = round(100 * A[pre + "to"] / A[pre + "poss"], 1)
            opp = "d_" if pre == "" else ""
            if A[pre + "oreb_n"] and A[opp + "reb_n"]:
                dreb_opp = A[opp + "reb"] - A[opp + "oreb"]
                res["orb"] = round(100 * A[pre + "oreb"] / max(1, A[pre + "oreb"] + dreb_opp), 1)
            if A[pre + "poss"]:
                res["rtg"] = round(100 * A[pre + "pts"] / A[pre + "poss"], 1)
            return res
        out[t] = {"g": int(A["g"]), "off": ff(""), "def": ff("d_"), "pace": round(A["poss"] / A["g"], 1) if A["poss"] else None}
    return out


# ---------------------------------------------------------------- players (props)
def players(lg, box_dir, season_start):
    """This season's per-game lines from box/bb -> {team abbr: {player: summary}}."""
    lines = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(box_dir, "*.json"))):
        day = os.path.basename(path)[:-5]
        if day < season_start:
            continue
        try:
            d = _rj((path))
        except ValueError:
            continue
        for gid, gm in (d.get("games") or {}).items():
            if gm.get("lg") != lg:
                continue
            for p in gm.get("players") or []:
                if p.get("n") and p.get("tm"):
                    lines[(p["tm"], p["n"])].append((day, p))
    out = defaultdict(dict)
    for (tm, n), xs in lines.items():
        xs.sort(key=lambda z: z[0])
        ps = [p for _, p in xs]
        if not ps:
            continue
        row = {"g": len(ps), "last": xs[-1][0]}
        for k in PROP_STATS:
            vals = [float(p.get(k) or 0) for p in ps]
            row[k] = round(st.mean(vals), 2)
            row["sd_" + k] = round(st.pstdev(vals), 2) if len(vals) > 1 else None
            row["r5_" + k] = round(st.mean(vals[-5:]), 2)
        row["dd"] = round(sum(1 for p in ps if sum(1 for k in ("pts", "reb", "ast", "stl", "blk") if (p.get(k) or 0) >= 10) >= 2) / len(ps), 3)
        if row["min"] < 8:
            continue                                     # deep bench: no props are posted for them
        keep = {"g", "last", "dd"} | set(PROP_STATS) | {f"{x}_{k}" for x in ("sd", "r5") for k in PROP_MAIN}
        out[tm][n] = {k: (round(v, 1) if isinstance(v, float) and k != "dd" else v) for k, v in row.items() if v is not None and k in keep}
    return out


# ---------------------------------------------------------------- one league
def league(lg, G, box_dir):
    t0 = time.time()
    s = BM.SETTINGS[lg]
    f = poss_factor(G)
    rest = rest_days(G)
    p = tune(G, lg, f, rest, s["tune"])
    out, state = run(G, p, lg, f, rest)
    rows = lm_rows(out)
    gs = dict(s, tune=s["tune"], test=s["test"])
    sp, tt, ml = LM.edge_gate(rows, gs, "spread"), LM.edge_gate(rows, gs, "total"), LM.ml_gate(rows, gs)
    sig_m = st.pstdev([g["hp"] - g["ap"] - m for g, m, t, pc in out if s["tune"][0] <= g["season"] <= s["tune"][1]]) or 12.0
    sig_t = st.pstdev([g["hp"] + g["ap"] - t for g, m, t, pc in out if s["tune"][0] <= g["season"] <= s["tune"][1]]) or 18.0
    cur = BM.current_season(lg)
    names, last = {}, {}
    for g in G:
        names[g["home"]], names[g["away"]] = g.get("hname"), g.get("aname")
        if g["season"] == cur:
            last[g["home"]] = last[g["away"]] = et_day(g["date"])
    ff = four_factors(G, f, cur)
    teams = {}
    for t in set(state["O"]) | set(state["D"]) | set(state["P"]):
        if t not in last and t not in ff:
            continue                                     # not playing this season
        teams[t] = {"n": names.get(t), "o": round(state["O"].get(t, 0.0), 2), "d": round(state["D"].get(t, 0.0), 2),
                    "p": round(state["P"].get(t, 0.0), 2), "last": last.get(t)}
        if t in ff:
            teams[t]["ff"] = ff[t]
    keep = ("pass", "why", "a", "b", "a_fit", "b_fit", "scale", "n_test", "gain", "t", "thr", "ats", "ats_by_season", "ll_market", "ll_model")
    has_stats = sum(1 for g in G if g.get("hst"))
    season_start = f"{cur - 1}-10-01" if lg != "WNBA" else f"{cur}-05-01"
    return {
        "version": VERSION, "params": p, "poss_factor": round(f, 4), "lg_eff": LG_EFF[lg], "lg_pace": LG_PACE[lg],
        "sigma_m": round(sig_m, 2), "sigma_t": round(sig_t, 2), "tune": list(s["tune"]), "test": list(s["test"]),
        "games": len(G), "with_stats": has_stats, "lined": sum(1 for g in G if g["close"] is not None), "asof": max((g["date"] for g in G), default=None),
        "gate": {k: v for k, v in sp.items() if k in keep}, "gate_total": {k: v for k, v in tt.items() if k in keep},
        "gate_ml": {k: v for k, v in ml.items() if k in ("pass", "why", "w", "w_fit", "sigma", "n_test", "gain", "t")},
        "backtest": backtest(out, s["test"]), "teams": teams,
        "players": players(lg, box_dir, season_start), "seconds": round(time.time() - t0, 1)}


def md(res):
    L = ["# Basketball Claude Model", "", f"Updated {res['updatedAt']}. Claude's own algorithm from box-score data only (possessions, efficiency, pace, "
         "four factors, rest). Tuned on early seasons, scored walk-forward on later seasons it never saw. Gated: it is labeled "
         "unvalidated and moves no chance until the spread gate passes. Break-even at −110 is 52.4%.", ""]
    for lg, r in res["leagues"].items():
        if "error" in r:
            L += [f"## {lg}", "", f"Not run: {r['error']}", ""]
            continue
        b, g = r["backtest"], r["gate"]
        L += [f"## {lg}", "",
              f"Settings `{json.dumps(r['params'])}`; {r['with_stats']} of {r['games']} games had box totals.",
              f"- Test seasons {b['seasons']}: {b['ats']}% against the closing spread ({b['atsN']} games), {b['su']}% winners, "
              f"{b['ou']}% on totals ({b['ouN']}); margin miss {b['mae']} vs the close's {b['mae_mkt']}",
              f"- Gate: **{'PASS' if g.get('pass') else 'FAIL'}** ({g.get('why')}); games with a closing line: {r.get('lined', 0)}",
              f"- {len(r['teams'])} teams rated this season; players with box scores: {sum(len(v) for v in r['players'].values())}", ""]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="NBA,MBB,WBB,WNBA")
    ap.add_argument("--data", default=BM.DATA)
    ap.add_argument("--box", default=BOX)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args(argv)
    res = {"updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "version": VERSION, "leagues": {}}
    for lg in [x for x in a.leagues.split(",") if x in BM.SETTINGS]:
        try:
            G = load_games(lg, a.data)
            res["leagues"][lg] = league(lg, G, a.box)
            print(f"{lg}: {len(G)} games in {res['leagues'][lg]['seconds']}s", file=sys.stderr)
        except Exception as e:
            print(f"{lg} failed: {e}", file=sys.stderr)
            res["leagues"][lg] = {"error": str(e)[:300]}
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "bb_cm.json")
    if os.path.exists(path):
        try:
            old = _rj((path))
            for k, v in res["leagues"].items():
                if "error" in v and "error" not in (old.get("leagues", {}).get(k) or {"error": 1}):
                    res["leagues"][k] = dict(old["leagues"][k], stale=v["error"])
        except Exception:
            pass
    with open(path, "w") as fh:
        json.dump(res, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "bb_cm.md"), "w") as fh:
        fh.write(md(res))
    print(md(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
