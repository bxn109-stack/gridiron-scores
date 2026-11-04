#!/usr/bin/env python3
"""Audit of the value engine's global adjustment: does moving the market's chance by the record of all leans make
game-by-game predictions better than the market alone?

Reads the ledger's league docs (saved with ArtifactData out_dir) and the push tables in model/results/ledger_model.json.
For every graded spread or total lean with a closing number, the market's chance at the lean's own number comes from the
closing number and the push tables (a lean at -3 when the close is -3.5 is worth a little more than 50%). The adjusted
chance is the engine's posterior: the market chance moved by the record of leans graded in EARLIER weeks only.
Both are scored on the actual results (pushes left out). Prints a JSON summary; writes nothing to the repository, since
the picks are private.

  python3 model/value_audit.py --docs DIR
"""
import argparse, glob, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def split(pmf, x):
    above, below, at = pmf["hi"], pmf["lo"], 0.0
    for i, q in enumerate(pmf["p"]):
        n = pmf["n0"] + i
        if n > x + 1e-9: above += q
        elif n < x - 1e-9: below += q
        else: at += q
    t = above + below + at
    return above / t, at / t, below / t


def row(table, x):
    if x is None:
        return None
    return table.get(f"{round(x * 2) / 2:g}")


def chance(lean, line, close, push):
    """Market chance (pushes removed) that the lean wins at its own number, anchored at 50% at the closing number."""
    mk, side, ln = lean.get("mk"), lean.get("side"), lean.get("ln")
    if mk == "spread" and side in ("A", "H"):
        fav = (line or {}).get("fav")
        s_line = -ln if side == fav else ln                      # the side's own line when picked
        s_close = close                                          # clv.close is the side's line at the close
        hm = -s_close if side == "H" else s_close                # closing expected home margin
        pmf = row(push["margin"], hm)
        if not pmf:
            return None
        home_line = s_line if side == "H" else -s_line
        a, at, b = split(pmf, -home_line); a0, at0, b0 = split(pmf, hm)
        ph = a / (a + b) - (a0 / (a0 + b0) - 0.5)
        return ph if side == "H" else 1 - ph
    if mk == "total" and side in ("O", "U"):
        pmf = row(push["total"], close)
        if not pmf:
            return None
        a, at, b = split(pmf, ln); a0, at0, b0 = split(pmf, close)
        po = a / (a + b) - (a0 / (a0 + b0) - 0.5)
        return po if side == "O" else 1 - po
    return None


def ll(p, y):
    p = min(1 - 1e-9, max(1e-9, p)); return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y == 1]; neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--model", default=os.path.join(HERE, "results", "ledger_model.json"))
    a = ap.parse_args(argv)
    M = json.load(open(a.model))["leagues"]
    rows = []
    for path in sorted(glob.glob(os.path.join(a.docs, "slates", "*", "leagues", "*.json"))):
        slate, lg = path.split(os.sep)[-3], os.path.basename(path)[:-5]
        push = (M.get("NFL") if lg == "NFL" else M.get("FBS") or {}).get("push")
        for g in (json.load(open(path)).get("games") or {}).values():
            gr, lock = g.get("gr") or {}, g.get("lock") or {}
            lean, line = lock.get("lean") or g.get("lean") or {}, lock.get("line") or g.get("line") or {}
            if gr.get("lean") not in ("W", "L") or not g.get("tracked"):
                continue
            close = (g.get("clv") or {}).get("close")
            p = chance(lean, line, close, push) if close is not None and push else None
            rows.append({"slate": slate, "lg": lg, "y": 1 if gr["lean"] == "W" else 0, "p": p, "conf": lean.get("conf"), "star": bool(g.get("star"))})
    have = [r for r in rows if r["p"] is not None]
    slates = sorted({r["slate"] for r in rows})
    for r in have:                              # the engine's adjustment, using only earlier weeks' record
        prior = [x for x in rows if x["slate"] < r["slate"]]
        W = sum(x["y"] for x in prior); N = len(prior)
        r["p_adj"] = (100 * r["p"] + W) / (100 + N)
    def score(key, xs):
        L = [ll(x[key], x["y"]) for x in xs]; B = [(x[key] - x["y"]) ** 2 for x in xs]
        return {"log_loss": sum(L) / len(L), "brier": sum(B) / len(B), "mean_p": sum(x[key] for x in xs) / len(xs), "auc": auc([x[key] for x in xs], [x["y"] for x in xs])}
    d = [ll(x["p"], x["y"]) - ll(x["p_adj"], x["y"]) for x in have]
    mu = sum(d) / len(d); sd = math.sqrt(sum((v - mu) ** 2 for v in d) / (len(d) - 1))
    tiers = {}
    for k in (1, 2, 3):
        xs = [r for r in rows if r["conf"] == k]
        if xs:
            w = sum(r["y"] for r in xs); n = len(xs)
            tiers[str(k)] = {"n": n, "win": w / n}
    out = {"graded_leans": len(rows), "with_closing_number": len(have), "win_rate": sum(r["y"] for r in have) / len(have),
           "market_only": score("p", have), "with_global_adjustment": score("p_adj", have),
           "adjustment_gain": {"mean": mu, "t": mu / (sd / math.sqrt(len(d))) if sd > 0 else None},
           "tiers": tiers, "slates": slates}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
