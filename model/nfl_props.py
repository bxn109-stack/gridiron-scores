#!/usr/bin/env python3
"""NFL player-prop projections from nflverse weekly player stats (public data).

For every player who has played this season it writes a projection per market: a weighted average of recent games
(this season weighted over last season), and a spread around it fitted from how far real games land from projections
made the same way, walk-forward. Markets: passing yards, attempts, completions, passing TDs, interceptions, rushing
yards, rushing attempts, receptions, receiving yards, rushing + receiving yards, anytime TD (rushing + receiving TDs).

Output: model/results/nfl_players.json (aggregate projections only, no picks). The score-update task matches the
ledger's props to these players privately. A prop shows a chance on the page only after the calibration gate (graded
props with a projection locked before kickoff) passes; until then the projection is shown as unvalidated.

  python3 model/nfl_props.py [--week-csv PATH ...] [--season 2026] [--out DIR]
"""
import argparse, csv, io, json, math, os, re, statistics as st, sys, time, unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.csv"
MARKETS = {
    "pass_yds": lambda r: n(r, "passing_yards"), "pass_att": lambda r: n(r, "attempts"), "comp": lambda r: n(r, "completions"),
    "pass_td": lambda r: n(r, "passing_tds"), "int": lambda r: n(r, "passing_interceptions"),
    "rush_yds": lambda r: n(r, "rushing_yards"), "rush_att": lambda r: n(r, "carries"),
    "rec": lambda r: n(r, "receptions"), "rec_yds": lambda r: n(r, "receiving_yards"),
    "rr_yds": lambda r: n(r, "rushing_yards") + n(r, "receiving_yards"),
    "td": lambda r: n(r, "rushing_tds") + n(r, "receiving_tds"),
}
COUNT = {"pass_att", "comp", "pass_td", "int", "rush_att", "rec", "td"}
DECAY, LAST_SEASON_W, MAX_GAMES = 0.85, 0.5, 14
ESPN_TEAM = {"LA": "LAR", "WAS": "WSH"}


def n(r, k):
    try:
        return float(r.get(k) or 0)
    except ValueError:
        return 0.0


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fetch(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron-ledger-model"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode("utf-8")


def load(texts):
    rows = []
    for t in texts:
        for r in csv.DictReader(io.StringIO(t)):
            if r.get("season_type") not in ("REG", "POST"):
                continue
            rows.append(r)
    rows.sort(key=lambda r: (int(r["season"]), int(r["week"])))
    return rows


def project(hist, season):
    """hist: list of (season, week, value), oldest first. Weighted mean, recent games and this season count more."""
    if not hist:
        return None, 0
    w_sum = v_sum = 0.0
    recent = hist[-MAX_GAMES:]
    for i, (s, wk, v) in enumerate(reversed(recent)):
        w = DECAY ** i * (1.0 if s == season else LAST_SEASON_W)
        w_sum += w; v_sum += w * v
    return v_sum / w_sum, sum(1 for s, _, _ in recent if s == season)


def build(rows, season):
    """Walk forward: projection before each game, then the residuals that fit the spread model, then today's projections."""
    hist = defaultdict(lambda: defaultdict(list))   # player -> market -> [(season, week, value)]
    resid = defaultdict(list)                       # market -> [(mu, actual)]
    meta = {}
    for r in rows:
        pid = r["player_id"]; s, wk = int(r["season"]), int(r["week"])
        meta[pid] = {"name": r.get("player_display_name") or r.get("player_name"), "team": ESPN_TEAM.get(r.get("team"), r.get("team")),
                     "pos": r.get("position"), "season": s, "week": wk}
        for mk, fn in MARKETS.items():
            v = fn(r)
            mu, k = project(hist[pid][mk], s)
            if mu is not None and k >= 3 and mu > 0.05:
                resid[mk].append((mu, v))
            hist[pid][mk].append((s, wk, v))
    sd_model = {}
    for mk, xs in resid.items():
        if len(xs) < 200:
            continue
        # sd grows with the projection: fit sd = a + b*mu on bins of mu
        xs.sort()
        bins = [xs[i:i + max(50, len(xs) // 20)] for i in range(0, len(xs), max(50, len(xs) // 20))]
        pts = [(st.mean(m for m, _ in b), st.pstdev([v - m for m, v in b])) for b in bins if len(b) >= 30]
        if len(pts) >= 2:
            mx, my = st.mean(p for p, _ in pts), st.mean(q for _, q in pts)
            sxx = sum((p - mx) ** 2 for p, _ in pts) or 1e-9
            b = sum((p - mx) * (q - my) for p, q in pts) / sxx
            a = my - b * mx
            sd_model[mk] = [round(a, 4), round(b, 4)]
    players = {}
    for pid, m in meta.items():
        if m["season"] != season:
            continue
        proj = {}
        for mk in MARKETS:
            mu, k = project(hist[pid][mk], season)
            if mu is not None and k >= 2 and mu > 0.05:
                proj[mk] = round(mu, 2)
        if proj:
            key = norm_name(m["name"])
            players.setdefault(key, []).append({"name": m["name"], "team": m["team"], "pos": m["pos"], "last": m["week"],
                                                "n": len(hist[pid]["rec"]) and sum(1 for s, _, _ in hist[pid]["rec"] if s == season), "m": proj})
    pit = coverage(resid, sd_model)
    return players, sd_model, pit


def sd_for(sd_model, mk, mu):
    a, b = sd_model.get(mk, [None, None])
    if a is None:
        return None
    return max(0.35 if mk in COUNT else 3.0, a + b * mu)


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def p_over(mk, mu, sd, line):
    """Chance the stat finishes over the line (whole-number stats get a continuity correction). Anytime TD: chance of 1+."""
    if mk == "td" and line is None:
        return 1 - math.exp(-max(mu, 1e-6))
    if sd is None or line is None:
        return None
    adj = 0.5 if mk in COUNT and float(line).is_integer() else 0.0
    return 1 - phi((line + adj - mu) / sd) if not adj else 1 - phi((line + 0.5 - mu) / sd)


def coverage(resid, sd_model):
    """How often actual games fall inside the model's middle 50% band (should be near 50%) per market."""
    out = {}
    for mk, xs in resid.items():
        sdm = [(mu, v, sd_for(sd_model, mk, mu)) for mu, v in xs]
        sdm = [(mu, v, s) for mu, v, s in sdm if s]
        if len(sdm) < 200:
            continue
        inside = sum(1 for mu, v, s in sdm if abs(v - mu) <= 0.674 * s)
        out[mk] = {"n": len(sdm), "inside_mid50": round(inside / len(sdm), 3)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-csv", nargs="*", default=None)
    ap.add_argument("--season", type=int, default=int(time.strftime("%Y")) if int(time.strftime("%m")) >= 3 else int(time.strftime("%Y")) - 1)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args(argv)
    texts = [open(p).read() for p in a.week_csv] if a.week_csv else [fetch(URL.format(y=y)) for y in (a.season - 1, a.season)]
    rows = load(texts)
    players, sd_model, pit = build(rows, a.season)
    last_week = max((int(r["week"]) for r in rows if int(r["season"]) == a.season), default=None)
    out = {"updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "version": 1, "season": a.season, "throughWeek": last_week,
           "decay": DECAY, "sd": sd_model, "coverage": pit, "players": players}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "nfl_players.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"{sum(len(v) for v in players.values())} players, through week {last_week}; middle-50% coverage: "
          + ", ".join(f"{k} {v['inside_mid50']:.0%}" for k, v in sorted(pit.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
