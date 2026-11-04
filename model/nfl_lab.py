#!/usr/bin/env python3
"""NFL model lab: diagnose Ledger Model v1, build candidate models A-F, and score them walk-forward.

Protocol (fixed before any test number was looked at):
  * Every feature for a game uses only games from earlier weeks (point-in-time snapshots by season and week).
  * Development seasons 2012-2017: the only seasons used to choose settings, compare variants and fit ensemble weights.
  * Test seasons 2018-2025: scored once with frozen choices. Each test season's models are refit on all seasons
    before it (expanding window); nothing is tuned on them.
  * Live 2026 to date: the same, as a prospective check.
  * Benchmarks: the closing market (nflverse closing spread, total and moneylines, margin removed), Ledger Model v1,
    a home-field-only baseline and a point-differential baseline. A same-time (forecast-time) market is not available
    historically: nflverse carries closing numbers only. The page's own saved prices cover that from Nov 2026 on.

  python3 model/nfl_data.py            # build the play-by-play tables (once; ~1 minute)
  python3 model/nfl_lab.py             # run everything, write model/results/nfl_lab.{json,md}
  python3 model/nfl_lab.py --quick     # dev seasons only, for development
"""
import argparse, json, math, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache", "nfl")
OLD = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "JAC": "JAX"}
FIRST, BURN, DEV, TEST, LIVE = 2007, 2009, (2012, 2017), (2018, 2025), 2026   # BURN: first season predicted out of sample
BE = 110 / 210
VERSION = "lab-1"


# ================================================================ data
def novig(a, b):
    def ip(x):
        return np.where(x > 0, 100 / (x + 100), -x / (-x + 100))
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b) & (np.abs(a) >= 100) & (np.abs(b) >= 100)
    pa, pb = ip(np.where(ok, a, 100)), ip(np.where(ok, b, 100))
    return np.where(ok, pa / (pa + pb), np.nan)


def load_games(path=None):
    g = pd.read_csv(path or os.path.join(CACHE, "games.csv"), low_memory=False)
    g = g[g["season"] >= 2006].copy()
    for c in ("home_team", "away_team"):
        g[c] = g[c].replace(OLD)
    g["neutral"] = (g["location"] == "Neutral").astype(int)
    g["played"] = g["home_score"].notna() & g["away_score"].notna()
    g["margin"] = g["home_score"] - g["away_score"]
    g["total"] = g["home_score"] + g["away_score"]
    g["p_ml"] = novig(g["home_moneyline"], g["away_moneyline"])
    g["p_cov"] = novig(g["home_spread_odds"], g["away_spread_odds"])          # home covers the closing spread
    g["p_over"] = novig(g["over_odds"], g["under_odds"])
    g["dome"] = g["roof"].isin(["dome", "closed"]).astype(int)
    g["wind_f"] = np.where(g["dome"] == 1, 0, g["wind"].fillna(g["wind"].median()))
    g["temp_f"] = np.where(g["dome"] == 1, 70, g["temp"].fillna(60))
    g["rest_diff"] = (g["home_rest"].fillna(7) - g["away_rest"].fillna(7)).clip(-7, 7)
    g["post"] = (g["game_type"] != "REG").astype(int)
    g = g.sort_values(["season", "week", "gameday", "game_id"]).reset_index(drop=True)
    return g


def team_long(g, tg):
    """One row per team per played game: what it did on offense and allowed on defense in that game."""
    t = tg.copy()
    t["posteam"] = t["posteam"].replace(OLD); t["defteam"] = t["defteam"].replace(OLD)
    per = lambda a, b: a / b.where(b > 0)
    t["o_epa"] = per(t["epa"], t["plays"]); t["o_pass"] = per(t["pass_epa"], t["pass_n"]); t["o_rush"] = per(t["rush_epa"], t["rush_n"])
    t["o_sr"] = per(t["success"], t["plays"]); t["o_xpl"] = per(t["explosive"], t["plays"]); t["o_early"] = per(t["early_epa"], t["early_n"])
    t["o_pace"] = t["plays_all"]; t["o_to"] = t["turnovers"]
    keep = ["game_id", "posteam", "o_epa", "o_pass", "o_rush", "o_sr", "o_xpl", "o_early", "o_pace", "o_to"]
    off = t[keep].rename(columns={"posteam": "team"})
    de = t[keep].rename(columns={"posteam": "opp"})
    de.columns = ["game_id", "opp"] + ["d" + c[1:] for c in keep[2:]]        # what the opponent's offense did = allowed
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        x = g.loc[g["played"], ["game_id", "season", "week", f"{side}_team", f"{other}_team", f"{side}_score", f"{other}_score"]].copy()
        x.columns = ["game_id", "season", "week", "team", "opp", "pf", "pa"]
        rows.append(x)
    L = pd.concat(rows)
    L = L.merge(off, on=["game_id", "team"], how="left").merge(de, on=["game_id", "opp"], how="left")
    return L.sort_values(["season", "week", "game_id"]).reset_index(drop=True)


# ================================================================ point-in-time team features (models A, C, D, E)
OFF = ["o_epa", "o_pass", "o_rush", "o_sr", "o_xpl", "o_early", "pf"]
DEF = ["d_epa", "d_pass", "d_rush", "d_sr", "d_xpl", "d_early", "pa"]


def snapshots(L, lam=0.9, rho=0.55, prior=4.0, adjust=2):
    """Team ratings before every (season, week): recency-weighted, opponent-adjusted means of each stat, shrunk toward
    the league mean by `prior` pseudo-games. Uses only rows from earlier weeks. Returns {(season, week): DataFrame}."""
    L = L.copy()
    L["key"] = L["season"] * 100 + L["week"]
    keys = sorted(set(zip(L["season"], L["week"])))
    weeks = sorted(set(keys) | {(s, w) for s in range(L["season"].min(), L["season"].max() + 1) for w in range(1, 23)})
    out = {}
    cols = OFF + DEF + ["o_pace", "o_to"]
    for s, w in weeks:
        k = s * 100 + w
        H = L[L["key"] < k]
        H = H[H["season"] >= s - 2]
        if H.empty:
            continue
        H = H.assign(ago=H.groupby("team").cumcount(ascending=False))
        wt = lam ** H["ago"].to_numpy() * rho ** (s - H["season"].to_numpy())
        H = H.assign(w=wt)
        lg = {c: np.nansum(H[c] * H["w"]) / np.nansum(H["w"] * H[c].notna()) for c in cols}
        def wmean(frame, col):
            v = frame[col].to_numpy(); ww = frame["w"].to_numpy(); m = np.isfinite(v)
            return (np.sum(v[m] * ww[m]) + prior * lg[col]) / (np.sum(ww[m]) + prior)
        R = {c: H.groupby("team").apply(lambda f, c=c: wmean(f, c), include_groups=False) for c in cols}
        R = pd.DataFrame(R)
        for _ in range(adjust):     # opponent adjustment: what each team did relative to the opponents it faced
            opp_def = R[DEF].rename(columns=dict(zip(DEF, OFF))) - pd.Series({o: lg[d] for o, d in zip(OFF, DEF)})
            opp_off = R[OFF].rename(columns=dict(zip(OFF, DEF))) - pd.Series({d: lg[o] for o, d in zip(OFF, DEF)})
            A = H[["team", "opp", "w"] + OFF + DEF].copy()
            od = opp_def.reindex(A["opp"]).to_numpy(); oo = opp_off.reindex(A["opp"]).to_numpy()
            A[OFF] = A[OFF].to_numpy() - np.nan_to_num(od)
            A[DEF] = A[DEF].to_numpy() - np.nan_to_num(oo)
            R2 = {c: A.groupby("team").apply(lambda f, c=c: wmean(f, c), include_groups=False) for c in OFF + DEF}
            R = R.assign(**R2)
        R["n"] = H.groupby("team")["w"].sum()
        out[(s, w)] = R
    return out


def qb_values(g, Q, prior_n=150.0, prior_mu=-0.05, unknown_mu=-0.12, lam=0.985):
    """Each starting QB's shrunk EPA per dropback from his own earlier games (any team), and the same for the QB his
    team started most recently. Starting QBs come from nflverse game records (known before kickoff in practice)."""
    Q = Q.copy(); Q["posteam"] = Q["posteam"].replace(OLD)
    Q = Q.merge(g[["game_id", "season", "week"]], on=["game_id", "season", "week"], how="inner")
    Q["key"] = Q["season"] * 100 + Q["week"]
    hist = {}      # qb id -> list of (key, dropbacks, epa)
    for r in Q.sort_values("key").itertuples():
        hist.setdefault(r.passer_player_id, []).append((r.key, r.dropbacks, r.epa))
    def val(qid, key):
        if not isinstance(qid, str) or qid not in hist:
            return unknown_mu, 0.0
        n = e = 0.0
        prev = [h for h in hist[qid] if h[0] < key]
        for i, (k, d, x) in enumerate(reversed(prev[-60:])):
            wt = lam ** i
            n += wt * d; e += wt * x
        mu = prior_mu if n > 0 else unknown_mu
        return (e + prior_n * mu) / (n + prior_n), n
    last = {}     # team -> last starting QB id before this game
    out = []
    for r in g.itertuples():
        key = r.season * 100 + r.week
        row = {}
        for side in ("home", "away"):
            team, qid = getattr(r, f"{side}_team"), getattr(r, f"{side}_qb_id")
            v, n = val(qid, key)
            pv, _ = val(last.get(team), key) if last.get(team) else (v, 0)
            row[f"{side}_qb"] = v; row[f"{side}_qb_n"] = n
            row[f"{side}_qb_chg"] = v - pv if last.get(team) and last.get(team) != qid else 0.0
        out.append(row)
        if r.played:
            last[r.home_team] = r.home_qb_id; last[r.away_team] = r.away_qb_id
    return pd.DataFrame(out, index=g.index)


def game_features(g, snaps, qb):
    """Home-minus-away matchup features for every game from the snapshot before its week."""
    F = []
    for r in g.itertuples():
        R = snaps.get((r.season, r.week))
        row = {"_ok": False}
        if R is not None and r.home_team in R.index and r.away_team in R.index:
            h, a = R.loc[r.home_team], R.loc[r.away_team]
            row = {"_ok": True}
            for o, d in zip(OFF, DEF):
                row[f"m_{o}"] = (h[o] + a[d]) - (a[o] + h[d])          # home offense vs away defense, minus the reverse
                                                                    # (d_* is what a defense allows: higher = worse)
                row[f"t_{o}"] = (h[o] + a[d]) + (a[o] + h[d])          # both offenses vs both defenses (for totals)
            row["pace"] = h["o_pace"] + a["o_pace"]; row["to_diff"] = (h["o_to"] - a["o_to"])
            row["n_min"] = min(h["n"], a["n"])
        F.append(row)
    F = pd.DataFrame(F, index=g.index)
    F["hfa"] = 1 - g["neutral"]; F["rest"] = g["rest_diff"] / 7; F["div"] = g["div_game"].fillna(0)
    F["dome"] = g["dome"]; F["wind"] = g["wind_f"]; F["temp"] = g["temp_f"]; F["post"] = g["post"]
    F["early"] = (g["week"] <= 4).astype(int)
    F["qb"] = qb["home_qb"] - qb["away_qb"]; F["qb_chg"] = qb["home_qb_chg"] - qb["away_qb_chg"]
    F["qb_sum"] = qb["home_qb"] + qb["away_qb"]
    return F


# ================================================================ model B: dynamic ratings (Kalman-filtered points ratings)
def kalman(g, q=1.0, q_season=25.0, regress=0.6, sigma=13.0, hfa0=2.5, hfa_q=0.02, cap=24, tot_k=0.06):
    """Team margin ratings as a random walk observed through each game's margin (capped), with a home-field state that
    drifts slowly, and offense/defense points ratings with a league scoring level that adapts. Predictions are made
    before each game's update. Returns per-game (margin, total) predictions."""
    r, P, O, D, season, hfa, Ph = {}, {}, {}, {}, None, hfa0, 1.0
    lgp = 22.0
    pm, pt = np.full(len(g), np.nan), np.full(len(g), np.nan)
    last_week = {}
    for i, x in enumerate(g.itertuples()):
        if x.season != season:
            season = x.season
            for t in r:
                r[t] *= regress; P[t] = P[t] * regress ** 2 + q_season
                O[t] *= regress; D[t] *= regress
        h, a = x.home_team, x.away_team
        for t in (h, a):
            if t not in r:
                r[t], P[t], O[t], D[t] = 0.0, 40.0, 0.0, 0.0
            # time between games adds uncertainty
            P[t] += q * max(1, (x.season * 100 + x.week) - last_week.get(t, x.season * 100 + x.week - 1))
        hf = 0.0 if x.neutral else hfa
        pm[i] = r[h] - r[a] + hf
        pt[i] = 2 * lgp + O[h] + O[a] - D[h] - D[a]
        if not x.played:
            continue
        y = max(-cap, min(cap, x.margin))
        S = P[h] + P[a] + sigma ** 2 + (0 if x.neutral else Ph)
        e = y - pm[i]
        r[h] += P[h] / S * e; r[a] -= P[a] / S * e
        if not x.neutral:
            hfa += Ph / S * e; Ph += hfa_q
        P[h] -= P[h] ** 2 / S; P[a] -= P[a] ** 2 / S
        if not x.neutral:
            Ph -= Ph ** 2 / S
        eh = x.home_score - (lgp + hf / 2 + O[h] - D[a]); ea = x.away_score - (lgp - hf / 2 + O[a] - D[h])
        O[h] += tot_k * eh; D[a] -= tot_k * eh; O[a] += tot_k * ea; D[h] -= tot_k * ea
        lgp += 0.01 * ((x.home_score + x.away_score) / 2 - lgp)
        last_week[h] = last_week[a] = x.season * 100 + x.week
    return pm, pt


# ================================================================ v1 and simple baselines
def ledger_v1(g):
    sys.path.insert(0, HERE)
    import ledger_model as LM
    res = json.load(open(os.path.join(HERE, "results", "ledger_model.json")))
    p = res["leagues"]["NFL"]["params"]
    G = []
    for x in g.itertuples():
        G.append({"id": x.game_id, "season": int(x.season), "week": int(x.week), "date": str(x.gameday), "home": x.home_team,
                  "away": x.away_team, "neutral": bool(x.neutral), "hdiv": "fbs", "adiv": "fbs",
                  "hp": float(x.home_score) if x.played else None, "ap": float(x.away_score) if x.played else None})
    out, _ = LM.run(G, p, LM.SETTINGS["NFL"]["lg0"])      # same order as g
    return np.array([m for _, m, _ in out]), np.array([t for _, _, t in out]), p


def pd_baseline(g, lam=0.9, prior=4.0, hfa=1.8):
    """Season-to-date points margin per game, shrunk, plus home field."""
    m = np.full(len(g), np.nan); acc = {}
    season = None
    for i, x in enumerate(g.itertuples()):
        if x.season != season:
            season = x.season; acc = {t: (v[0] * 0.3, v[1] * 0.3) for t, v in acc.items()}
        def rate(t):
            s, n = acc.get(t, (0.0, 0.0)); return s / (n + prior)
        m[i] = rate(x.home_team) - rate(x.away_team) + (0 if x.neutral else hfa)
        if x.played:
            for t, v in ((x.home_team, x.margin), (x.away_team, -x.margin)):
                s, n = acc.get(t, (0.0, 0.0)); acc[t] = (s * lam + v, n * lam + 1)
    return m


# ================================================================ probabilities from a predicted margin / total
class Dist:
    """P(outcome = n | prediction) from training pairs (prediction, actual), smoothed over nearby predictions, so key
    numbers (3, 7, ...) keep their real weight. Falls back to a normal curve where data is thin."""
    def __init__(self, pred, actual, bw=1.25, width=40):
        ok = np.isfinite(pred) & np.isfinite(actual)
        self.p, self.a = np.asarray(pred)[ok], np.asarray(actual)[ok].astype(int)
        self.sigma = float(np.std(self.a - self.p)) if ok.sum() > 10 else 13.5
        self.bw, self.width, self.cache = bw, width, {}

    def pmf(self, mu):
        k = round(float(mu) * 4) / 4
        if k in self.cache:
            return self.cache[k]
        z = (self.p - k) / self.bw
        w = np.exp(-0.5 * z * z); w[np.abs(z) > 4] = 0
        lo, hi = int(math.floor(k)) - self.width, int(math.floor(k)) + self.width
        shift = self.a - np.round(self.p - k).astype(int)            # recentre each pair on k
        m = (shift >= lo) & (shift <= hi) & (w > 0)
        counts = np.bincount(shift[m] - lo, weights=w[m], minlength=hi - lo + 1)
        n = counts.sum()
        grid = np.arange(lo, hi + 1)
        normal = np.exp(-0.5 * ((grid - k) / self.sigma) ** 2); normal /= normal.sum()
        alpha = n / (n + 200.0)
        pm = alpha * (counts / n if n > 0 else normal) + (1 - alpha) * normal
        self.cache[k] = (lo, pm)
        return lo, pm

    def above(self, mu, x):
        """P(outcome > x), P(outcome == x)."""
        lo, pm = self.pmf(mu)
        grid = np.arange(lo, lo + len(pm))
        return float(pm[grid > x + 1e-9].sum()), float(pm[np.abs(grid - x) < 1e-9].sum())


def probs(mu_m, mu_t, g, dm, dt):
    """Home win, home cover at the closing spread, over at the closing total (pushes and ties removed)."""
    pw, pc, po = np.full(len(g), np.nan), np.full(len(g), np.nan), np.full(len(g), np.nan)
    for i, (m, t, s, tot) in enumerate(zip(mu_m, mu_t, g["spread_line"].to_numpy(), g["total_line"].to_numpy())):
        if np.isfinite(m):
            a, at = dm.above(m, 0); pw[i] = a / (1 - at) if at < 1 else 0.5
            if np.isfinite(s):
                a, at = dm.above(m, s); pc[i] = a / (1 - at) if at < 1 else 0.5
        if np.isfinite(t) and np.isfinite(tot):
            a, at = dt.above(t, tot); po[i] = a / (1 - at) if at < 1 else 0.5
    return pw, pc, po


# ================================================================ metrics
def clip(p):
    return np.clip(p, 1e-6, 1 - 1e-6)


def ll(p, y):
    p = clip(p); return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def calib(p, y):
    """Calibration slope and intercept (logistic of outcome on logit p) and expected calibration error (10 bins)."""
    p = clip(p); x = np.log(p / (1 - p))
    if len(p) < 50:
        return None, None, None
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1e6).fit(x.reshape(-1, 1), y)
    bins = np.clip((p * 10).astype(int), 0, 9)
    ece = sum(abs(p[bins == b].mean() - y[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any())
    return float(lr.coef_[0][0]), float(lr.intercept_[0]), float(ece)


def paired(base, model):
    d = base - model; n = len(d)
    if n < 30:
        return None, None
    return float(d.mean()), float(d.mean() / (d.std(ddof=1) / math.sqrt(n))) if d.std() > 0 else None


def ats(edge, y, thr):
    m = (np.abs(edge) >= thr) & (edge != 0)
    w = int(((edge > 0) == (y == 1))[m].sum()); l = int(m.sum() - w)
    return w, l


def evaluate(name, g, mu_m, mu_t, pw, pc, po, mask):
    """All metrics for one model on the games in mask (played games only)."""
    m = mask & g["played"].to_numpy()
    out = {"model": name, "n": int(m.sum())}
    y_su = (g["margin"].to_numpy() > 0).astype(float); tie = g["margin"].to_numpy() == 0
    k = m & np.isfinite(pw) & ~tie
    if k.any():
        out["su_acc"] = float(((pw[k] > 0.5) == (y_su[k] == 1)).mean())
        out["su_brier"] = float(((pw[k] - y_su[k]) ** 2).mean()); out["su_ll"] = float(ll(pw[k], y_su[k]).mean())
        out["su_cal"] = calib(pw[k], y_su[k]); out["su_n"] = int(k.sum())
        km = k & np.isfinite(g["p_ml"].to_numpy())
        out["su_vs_close"] = paired(ll(g["p_ml"].to_numpy()[km], y_su[km]), ll(pw[km], y_su[km])) + (int(km.sum()),)
    ok = m & np.isfinite(mu_m)
    out["mae_margin"] = float(np.abs(g["margin"].to_numpy()[ok] - mu_m[ok]).mean()) if ok.any() else None
    out["rmse_margin"] = float(np.sqrt(((g["margin"].to_numpy()[ok] - mu_m[ok]) ** 2).mean())) if ok.any() else None
    okt = m & np.isfinite(mu_t)
    out["mae_total"] = float(np.abs(g["total"].to_numpy()[okt] - mu_t[okt]).mean()) if okt.any() else None
    # spread vs the closing line
    d = g["margin"].to_numpy() - g["spread_line"].to_numpy()
    ks = m & np.isfinite(pc) & np.isfinite(d) & (d != 0)
    if ks.any():
        y = (d[ks] > 0).astype(float)
        base = np.where(np.isfinite(g["p_cov"].to_numpy()[ks]), g["p_cov"].to_numpy()[ks], 0.5)
        out["sp_vs_close"] = paired(ll(base, y), ll(pc[ks], y)) + (int(ks.sum()),)
        edge = (mu_m - g["spread_line"].to_numpy())[ks]
        out["ats"] = {str(t): ats(edge, y, t) for t in (0, 1, 2, 3)}
        out["sp_cal"] = calib(pc[ks], y)
    dt = g["total"].to_numpy() - g["total_line"].to_numpy()
    kt = m & np.isfinite(po) & np.isfinite(dt) & (dt != 0)
    if kt.any():
        y = (dt[kt] > 0).astype(float)
        base = np.where(np.isfinite(g["p_over"].to_numpy()[kt]), g["p_over"].to_numpy()[kt], 0.5)
        out["tot_vs_close"] = paired(ll(base, y), ll(po[kt], y)) + (int(kt.sum()),)
        edge = (mu_t - g["total_line"].to_numpy())[kt]
        out["ou"] = {str(t): ats(edge, y, t) for t in (0, 2, 4)}
        out["tot_cal"] = calib(po[kt], y)
    return out


def market_eval(g, mask):
    m = mask & g["played"].to_numpy()
    y = (g["margin"].to_numpy() > 0).astype(float); tie = g["margin"].to_numpy() == 0
    k = m & np.isfinite(g["p_ml"].to_numpy()) & ~tie
    p = g["p_ml"].to_numpy()[k]
    sl, tl = g["spread_line"].to_numpy(), g["total_line"].to_numpy()
    ok = m & np.isfinite(sl); okt = m & np.isfinite(tl)
    return {"model": "Closing market", "n": int(m.sum()), "su_n": int(k.sum()), "su_acc": float(((p > 0.5) == (y[k] == 1)).mean()),
            "su_brier": float(((p - y[k]) ** 2).mean()), "su_ll": float(ll(p, y[k]).mean()), "su_cal": calib(p, y[k]),
            "mae_margin": float(np.abs(g["margin"].to_numpy()[ok] - sl[ok]).mean()), "rmse_margin": float(np.sqrt(((g["margin"].to_numpy()[ok] - sl[ok]) ** 2).mean())),
            "mae_total": float(np.abs(g["total"].to_numpy()[okt] - tl[okt]).mean())}


# ================================================================ the candidates
ALPHA = {"v": 10.0}


def fit_predict_linear(X, y, train, test, alpha=None):
    """Ridge regression on standardized features (so the penalty treats every feature alike)."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    ok = train & np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    mdl = make_pipeline(StandardScaler(), Ridge(alpha=ALPHA["v"] if alpha is None else alpha)).fit(X[ok], y[ok])
    pred = np.full(len(y), np.nan); te = test & np.all(np.isfinite(X), axis=1)
    pred[te] = mdl.predict(X[te]); return pred, mdl


def fit_predict_gbm(X, y, train, test, **kw):
    from sklearn.ensemble import HistGradientBoostingRegressor
    ok = train & np.isfinite(y)
    mdl = HistGradientBoostingRegressor(max_depth=kw.get("depth", 3), learning_rate=kw.get("lr", 0.04), max_iter=kw.get("iters", 250),
                                        min_samples_leaf=kw.get("leaf", 80), l2_regularization=kw.get("l2", 1.0), random_state=0).fit(X[ok], y[ok])
    pred = np.full(len(y), np.nan); pred[test] = mdl.predict(X[test]); return pred


A_M = [f"m_{o}" for o in OFF] + ["hfa", "rest", "post"]
A_T = [f"t_{o}" for o in OFF] + ["pace", "dome", "wind", "temp", "post"]
C_M = A_M + ["qb", "qb_chg"]
C_T = A_T + ["qb_sum"]
D_M = C_M + ["to_diff", "div", "early", "dome", "wind", "n_min", "kal_m"]
D_T = C_T + ["early", "n_min", "kal_t"]


def walk(g, F, seasons, fn):
    """For each season S: fit on FIRST..S-1, predict S. fn(train_mask, test_mask) -> (margin, total) arrays."""
    pm, pt = np.full(len(g), np.nan), np.full(len(g), np.nan)
    for S in seasons:
        tr = ((g["season"] >= FIRST) & (g["season"] < S) & g["played"] & F["_ok"]).to_numpy()
        te = ((g["season"] == S) & F["_ok"]).to_numpy()
        m, t = fn(tr, te)
        pm[te] = m[te]; pt[te] = t[te]
    return pm, pt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--shadow-only", action="store_true", help="only refresh model/results/nfl_shadow.json (frozen settings)")
    a = ap.parse_args(argv)
    t0 = time.time()
    g = load_games()
    L = team_long(g, pd.read_csv(os.path.join(CACHE, "team_games.csv")))
    Q = pd.read_csv(os.path.join(CACHE, "qb_games.csv"))
    seasons_all = list(range(BURN, (DEV[1] if a.quick else LIVE) + 1))
    log = lambda s: print(f"[{time.time() - t0:5.0f}s] {s}", file=sys.stderr)

    # ---------- development: choose settings on 2012-2017 only
    dev = ((g["season"] >= DEV[0]) & (g["season"] <= DEV[1])).to_numpy()
    qb = qb_values(g, Q)
    log("QB values")
    if a.shadow_only:
        # weekly refresh of the shadow model's published numbers, with the settings the last full run froze
        prev = json.load(open(os.path.join(a.out, "nfl_lab.json")))
        ALPHA["v"] = prev["settings"]["ridge_alpha"]
        sn = snapshots(L, lam=prev["settings"]["snapshots"]["lam"], rho=prev["settings"]["snapshots"]["rho"])
        F = game_features(g, sn, qb)
        name = (prev.get("shadow") or {}).get("model") or "C · Efficiency + quarterback"
        info = export_shadow(g, F, sn, qb, name, prev["settings"], a.out, shadow_summary(prev, name))
        log(f"shadow export: {json.dumps(info)}")
        return 0
    best_snap = None
    for lam, rho in ((0.9, 0.55), (0.93, 0.65), (0.96, 0.7), (0.98, 0.75)):
        sn = snapshots(L, lam=lam, rho=rho)
        F = game_features(g, sn, qb)
        X = F[A_M].to_numpy(float)
        pm, _ = walk(g, F, range(DEV[0], DEV[1] + 1), lambda tr, te: (fit_predict_linear(X, g["margin"].to_numpy(float), tr, te)[0], np.full(len(g), np.nan)))
        e = np.nanmean(np.abs(g["margin"].to_numpy()[dev] - pm[dev]))
        log(f"snapshots lam={lam} rho={rho}: dev margin MAE {e:.3f}")
        if best_snap is None or e < best_snap[0]:
            best_snap = (e, lam, rho, F, sn)
    _, LAM, RHO, F, SN = best_snap
    best_a = None
    XC = F[C_M].to_numpy(float)
    for al in (1.0, 10.0, 100.0, 1000.0):
        pm, _ = walk(g, F, range(DEV[0], DEV[1] + 1), lambda tr, te: (fit_predict_linear(XC, g["margin"].to_numpy(float), tr, te, al)[0], np.full(len(g), np.nan)))
        e = np.nanmean(np.abs(g["margin"].to_numpy()[dev] - pm[dev])); log(f"ridge alpha={al}: dev MAE {e:.3f}")
        if best_a is None or e < best_a[0]:
            best_a = (e, al)
    ALPHA["v"] = best_a[1]
    best_k = None
    for q, qs, reg, cap, hq in ((0.5, 20, 0.6, 21, 0.02), (0.5, 20, 0.6, 21, 0.001), (0.25, 15, 0.65, 21, 0.001), (0.5, 15, 0.7, 21, 0.0005),
                                (1.0, 25, 0.6, 24, 0.001), (0.35, 20, 0.6, 18, 0.001)):
        km, kt = kalman(g, q=q, q_season=qs, regress=reg, cap=cap, hfa_q=hq)
        e = np.nanmean(np.abs(g["margin"].to_numpy()[dev] - km[dev]))
        et = np.nanmean(np.abs(g["total"].to_numpy()[dev] - kt[dev]))
        log(f"kalman q={q} qs={qs} reg={reg} cap={cap} hfa_q={hq}: dev MAE {e:.3f} total {et:.3f}")
        if best_k is None or e < best_k[0]:
            best_k = (e, dict(q=q, q_season=qs, regress=reg, cap=cap, hfa_q=hq), km, kt)
    _, KP, KM, KT = best_k
    F["kal_m"], F["kal_t"] = KM, KT

    y_m, y_t = g["margin"].to_numpy(float), g["total"].to_numpy(float)
    XA_M, XA_T = F[A_M].to_numpy(float), F[A_T].to_numpy(float)
    XC_M, XC_T = F[C_M].to_numpy(float), F[C_T].to_numpy(float)
    XD_M, XD_T = F[D_M].to_numpy(float), F[D_T].to_numpy(float)
    # model E: home and away points separately (each team's offense vs the other's defense), then margin and total
    def e_fn(tr, te):
        hp, _ = fit_predict_linear(np.column_stack([XC_M, XC_T]), g["home_score"].to_numpy(float), tr, te)
        apn, _ = fit_predict_linear(np.column_stack([XC_M, XC_T]), g["away_score"].to_numpy(float), tr, te)
        return hp - apn, hp + apn
    gbm_cfg = None
    for cfg in (dict(depth=2, lr=0.03, iters=300, leaf=100), dict(depth=3, lr=0.04, iters=250, leaf=80), dict(depth=4, lr=0.03, iters=200, leaf=150)):
        pm, _ = walk(g, F, range(DEV[0], DEV[1] + 1), lambda tr, te: (fit_predict_gbm(XD_M, y_m, tr, te, **cfg), np.full(len(g), np.nan)))
        e = np.nanmean(np.abs(y_m[dev] - pm[dev])); log(f"gbm {cfg}: dev MAE {e:.3f}")
        if gbm_cfg is None or e < gbm_cfg[0]:
            gbm_cfg = (e, cfg)
    GB = gbm_cfg[1]

    cands = {
        "A · Opponent-adjusted efficiency": lambda tr, te: (fit_predict_linear(XA_M, y_m, tr, te)[0], fit_predict_linear(XA_T, y_t, tr, te)[0]),
        "C · Efficiency + quarterback": lambda tr, te: (fit_predict_linear(XC_M, y_m, tr, te)[0], fit_predict_linear(XC_T, y_t, tr, te)[0]),
        "D · Gradient-boosted matchup": lambda tr, te: (fit_predict_gbm(XD_M, y_m, tr, te, **GB), fit_predict_gbm(XD_T, y_t, tr, te, **GB)),
        "E · Separate team scores": e_fn,
    }
    preds = {"Ledger Model v1": ledger_v1(g)[:2], "B · Dynamic ratings (Kalman)": (KM, KT),
             "Point-differential baseline": (pd_baseline(g), np.full(len(g), np.nan)),
             "Home-field-only baseline": (np.where(g["neutral"] == 1, 0.0, 1.8), np.full(len(g), np.nan))}
    for name, fn in cands.items():
        preds[name] = walk(g, F, seasons_all, fn); log(name)

    # ---------- F: ensemble weights from out-of-sample predictions of earlier seasons only
    base = ["A · Opponent-adjusted efficiency", "B · Dynamic ratings (Kalman)", "C · Efficiency + quarterback", "D · Gradient-boosted matchup", "E · Separate team scores"]
    from scipy.optimize import nnls
    fm, ft = np.full(len(g), np.nan), np.full(len(g), np.nan)
    weights = {}
    for S in seasons_all:
        if S <= BURN + 1:
            continue
        tr = ((g["season"] >= BURN) & (g["season"] < S) & g["played"]).to_numpy()
        te = (g["season"] == S).to_numpy()
        for target, out_arr, idx in ((y_m, fm, 0), (y_t, ft, 1)):
            M = np.column_stack([preds[b][idx] for b in base])
            ok = tr & np.all(np.isfinite(M), axis=1) & np.isfinite(target)
            if ok.sum() < 200:
                continue
            w, _ = nnls(M[ok], target[ok])
            okt = te & np.all(np.isfinite(M), axis=1)
            out_arr[okt] = M[okt] @ w
            weights.setdefault(str(S), {})["margin" if idx == 0 else "total"] = dict(zip(base, [round(float(v), 3) for v in w]))
    preds["F · Ensemble (weights from earlier seasons)"] = (fm, ft)
    log("ensemble")

    # ---------- probabilities: scoring distribution fitted on each season's training seasons only
    PW, PC, PO = {}, {}, {}
    for name, (pm, pt) in preds.items():
        pw, pc, po = [np.full(len(g), np.nan) for _ in range(3)]
        for S in seasons_all:
            tr = ((g["season"] >= BURN) & (g["season"] < S) & g["played"]).to_numpy() & np.isfinite(pm)
            te = (g["season"] == S).to_numpy()
            if tr.sum() < 300:
                continue
            dm = Dist(pm[tr], y_m[tr]); dt = Dist(pt[tr & np.isfinite(pt)], y_t[tr & np.isfinite(pt)]) if np.isfinite(pt[tr]).sum() > 300 else Dist(np.array([44.0] * 400), np.array([44] * 400))
            sub = g[te]
            a_, b_, c_ = probs(pm[te], pt[te] if np.isfinite(pt[te]).any() else np.full(te.sum(), np.nan), sub, dm, dt)
            pw[te], pc[te], po[te] = a_, b_, c_
        PW[name], PC[name], PO[name] = pw, pc, po
    log("probabilities")

    # ---------- evaluation
    masks = {"dev": dev, "test": ((g["season"] >= TEST[0]) & (g["season"] <= TEST[1])).to_numpy(), "live": (g["season"] == LIVE).to_numpy()}
    if a.quick:
        masks = {"dev": dev}
    res = {"version": VERSION, "updatedAt": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
           "protocol": {"first_training_season": FIRST, "dev": DEV, "test": TEST, "live": LIVE},
           "settings": {"snapshots": {"lam": LAM, "rho": RHO}, "ridge_alpha": ALPHA["v"], "kalman": KP, "gbm": GB}, "ensemble_weights": weights, "results": {}}
    common = g["played"].to_numpy().copy()
    for name in preds:
        common &= np.isfinite(preds[name][0]) & np.isfinite(PW[name])
    res["common_games_note"] = "Every model is scored on the same games: those every model could predict."
    masks = {k: v & common for k, v in masks.items()}
    for split, mk in masks.items():
        rows = [market_eval(g, mk)]
        for name in preds:
            rows.append(evaluate(name, g, preds[name][0], preds[name][1], PW[name], PC[name], PO[name], mk))
        res["results"][split] = rows
    # by season (test and live) for each model: SU log loss vs close, spread log-loss gain vs close, margin MAE
    by = {}
    for S in range(TEST[0], LIVE + 1):
        mk = (g["season"] == S).to_numpy()
        by[str(S)] = {name: {k: v for k, v in evaluate(name, g, preds[name][0], preds[name][1], PW[name], PC[name], PO[name], mk).items()
                             if k in ("n", "su_ll", "su_vs_close", "mae_margin", "sp_vs_close", "ats")} for name in preds}
        by[str(S)]["Closing market"] = market_eval(g, mk)
    res["by_season"] = by
    # categories on the test seasons for every model
    test = masks.get("test", dev)
    sl = np.abs(g["spread_line"].to_numpy())
    cats = {"close spread 0-3": sl <= 3, "close spread 3.5-7": (sl > 3) & (sl <= 7), "close spread over 7": sl > 7,
            "division games": g["div_game"].to_numpy() == 1, "weeks 1-4": g["week"].to_numpy() <= 4, "weeks 5+": g["week"].to_numpy() > 4,
            "starting QB changed": (F["qb_chg"].to_numpy() != 0)}
    res["categories"] = {c: {name: {k: v for k, v in evaluate(name, g, preds[name][0], preds[name][1], PW[name], PC[name], PO[name], test & cm).items()
                                    if k in ("n", "mae_margin", "sp_vs_close", "su_vs_close")} for name in preds} | {"Closing market": market_eval(g, test & cm)}
                         for c, cm in cats.items()}
    # market + model: does the best candidate add anything to the closing market? (fit on dev OOS, scored on test)
    res["blend"] = blend_tests(g, preds, PW, PC, masks)
    res["diagnosis"] = diagnose(g, preds, F, masks)
    res["vs_v1"] = vs_v1(g, preds, PW, masks)
    res["shadow"] = choose_shadow(g, preds, PW, masks["dev"])
    res["verdicts"] = verdicts(res)
    os.makedirs(a.out, exist_ok=True)
    if not a.quick:
        res["shadow"]["export"] = export_shadow(g, F, SN, qb, res["shadow"]["model"], res["settings"], a.out, shadow_summary(res, res["shadow"]["model"]))
        log("shadow export")
    np.save(os.path.join(CACHE, "lab_preds.npy"), {k: v for k, v in preds.items()}, allow_pickle=True)
    with open(os.path.join(a.out, "nfl_lab.json"), "w") as fh:
        json.dump(res, fh, default=lambda o: None if isinstance(o, float) and not math.isfinite(o) else o, separators=(",", ":"))
    with open(os.path.join(a.out, "nfl_lab.md"), "w") as fh:
        fh.write(report(res))
    print(report(res))
    log("done")
    return 0


def diagnose(g, preds, F, masks):
    """Why a model fails: does it know anything the closing line does not, and where are its errors?"""
    out = {}
    m_all = g["played"].to_numpy()
    for name in ("Ledger Model v1", "F · Ensemble (weights from earlier seasons)", "C · Efficiency + quarterback"):
        if name not in preds:
            continue
        pm, pt = preds[name]
        d = {}
        for split, mk in masks.items():
            k = mk & m_all & np.isfinite(pm) & np.isfinite(g["spread_line"].to_numpy())
            if k.sum() < 100:
                continue
            res = g["margin"].to_numpy()[k] - g["spread_line"].to_numpy()[k]       # what the close missed
            edge = pm[k] - g["spread_line"].to_numpy()[k]                           # where the model disagrees
            X = np.column_stack([np.ones(k.sum()), edge]); beta, *_ = np.linalg.lstsq(X, res, rcond=None)
            r = res - X @ beta; se = math.sqrt((r @ r) / (len(r) - 2) / ((edge - edge.mean()) @ (edge - edge.mean())))
            hm = k & (g["neutral"].to_numpy() == 0)
            seasons = sorted(set(g["season"].to_numpy()[k]))
            d[split] = {
                "n": int(k.sum()),
                "info_slope": float(beta[1]), "info_t": float(beta[1] / se),          # 0 = the disagreement carries no information
                "mean_abs_disagreement": float(np.abs(edge).mean()),
                "home_bias_by_season": {str(S): round(float((g["margin"].to_numpy() - pm)[hm & (g["season"].to_numpy() == S)].mean()), 2) for S in seasons},
                "total_bias_by_season": {str(S): round(float((g["total"].to_numpy() - pt)[k & (g["season"].to_numpy() == S) & np.isfinite(pt)].mean()), 2) for S in seasons} if np.isfinite(pt).any() else None,
                "mae_vs_close_weeks_1_4": float(np.abs(g["margin"].to_numpy() - pm)[k & (g["week"].to_numpy() <= 4)].mean() - np.abs(g["margin"].to_numpy() - g["spread_line"].to_numpy())[k & (g["week"].to_numpy() <= 4)].mean()),
                "mae_vs_close_weeks_5_plus": float(np.abs(g["margin"].to_numpy() - pm)[k & (g["week"].to_numpy() > 4)].mean() - np.abs(g["margin"].to_numpy() - g["spread_line"].to_numpy())[k & (g["week"].to_numpy() > 4)].mean()),
                "mae_vs_close_qb_changed": float(np.abs(g["margin"].to_numpy() - pm)[k & (F["qb_chg"].to_numpy() != 0)].mean() - np.abs(g["margin"].to_numpy() - g["spread_line"].to_numpy())[k & (F["qb_chg"].to_numpy() != 0)].mean()),
                "games_qb_changed": int((k & (F["qb_chg"].to_numpy() != 0)).sum()),
                "cover_rate_se": float(math.sqrt(0.25 / k.sum())),
            }
        out[name] = d
    hm = m_all & (g["neutral"].to_numpy() == 0)
    out["actual_home_margin_by_season"] = {str(S): round(float(g["margin"].to_numpy()[hm & (g["season"].to_numpy() == S)].mean()), 2) for S in range(2006, LIVE + 1)}
    out["actual_points_per_game_by_season"] = {str(S): round(float(g["total"].to_numpy()[m_all & (g["season"].to_numpy() == S)].mean()), 1) for S in range(2006, LIVE + 1)}
    return out


def blend_tests(g, preds, PW, PC, masks):
    """For each model: logit(p) = c0 + c1*logit(market) + c2*logit(model), fitted on dev seasons' out-of-sample
    predictions, scored on test. A positive, significant gain is the deployment gate."""
    from sklearn.linear_model import LogisticRegression
    out = {}
    if "test" not in masks:
        return out
    dev, test = masks["dev"], masks["test"]
    y_su = (g["margin"].to_numpy() > 0).astype(float); tie = g["margin"].to_numpy() == 0
    d = g["margin"].to_numpy() - g["spread_line"].to_numpy(); y_sp = (d > 0).astype(float)
    lg = lambda p: np.log(clip(p) / (1 - clip(p)))
    for name in preds:
        r = {}
        for kind, mkt, mod, y, valid in (("su", g["p_ml"].to_numpy(), PW[name], y_su, ~tie),
                                         ("spread", np.where(np.isfinite(g["p_cov"].to_numpy()), g["p_cov"].to_numpy(), 0.5), PC[name], y_sp, np.isfinite(d) & (d != 0))):
            ok = valid & np.isfinite(mkt) & np.isfinite(mod) & g["played"].to_numpy()
            tr, te = ok & dev, ok & test
            if tr.sum() < 200 or te.sum() < 200:
                continue
            X = lambda m: np.column_stack([lg(mkt[m]), lg(mod[m])])
            lr = LogisticRegression(C=1e4).fit(X(tr), y[tr])
            pb = lr.predict_proba(X(te))[:, 1]
            gain, t = paired(ll(mkt[te], y[te]), ll(pb, y[te]))
            seasons = {}
            for S in range(TEST[0], TEST[1] + 1):
                ms = te & (g["season"].to_numpy() == S)
                if ms.sum() > 30:
                    seasons[str(S)] = float((ll(mkt[ms], y[ms]) - ll(lr.predict_proba(X(ms))[:, 1], y[ms])).mean())
            r[kind] = {"n": int(te.sum()), "w_market": float(lr.coef_[0][0]), "w_model": float(lr.coef_[0][1]), "gain": gain, "t": t,
                       "seasons_positive": sum(1 for v in seasons.values() if v > 0), "seasons": seasons,
                       "qualifies": bool(gain is not None and gain > 0 and t is not None and t >= 2 and lr.coef_[0][1] > 0 and sum(1 for v in seasons.values() if v > 0) >= 5)}
        out[name] = r
    return out


def vs_v1(g, preds, PW, masks):
    """Every model against Ledger Model v1 on the same games, paired. Positive = better than v1."""
    base, out = "Ledger Model v1", {}
    y = (g["margin"].to_numpy() > 0).astype(float); tie = g["margin"].to_numpy() == 0
    mg, tt = g["margin"].to_numpy(float), g["total"].to_numpy(float)
    for split, mk in masks.items():
        m = mk & g["played"].to_numpy()
        d = {}
        for name in preds:
            if name == base:
                continue
            k = m & ~tie & np.isfinite(PW[base]) & np.isfinite(PW[name])
            r = {"su": paired(ll(PW[base][k], y[k]), ll(PW[name][k], y[k])) + (int(k.sum()),)}
            km = m & np.isfinite(preds[base][0]) & np.isfinite(preds[name][0])
            r["margin_abs_err"] = paired(np.abs(mg[km] - preds[base][0][km]), np.abs(mg[km] - preds[name][0][km])) + (int(km.sum()),)
            kt = m & np.isfinite(preds[base][1]) & np.isfinite(preds[name][1])
            if kt.sum() > 30:
                r["total_abs_err"] = paired(np.abs(tt[kt] - preds[base][1][kt]), np.abs(tt[kt] - preds[name][1][kt])) + (int(kt.sum()),)
            d[name] = r
        out[split] = d
    return out


# models the private score-update task can compute from published team numbers (ridge on the snapshot features)
EXPORTABLE = {"A · Opponent-adjusted efficiency": (A_M, A_T), "C · Efficiency + quarterback": (C_M, C_T)}


def choose_shadow(g, preds, PW, dev):
    """The shadow model, chosen on development seasons only: the exportable candidate with the lowest straight-up log
    loss. Also reports how far the full ensemble (not exportable) is from it, paired, on the same seasons."""
    y = (g["margin"].to_numpy() > 0).astype(float); tie = g["margin"].to_numpy() == 0
    m = dev & g["played"].to_numpy() & ~tie
    k = m.copy()
    for name in preds:
        k &= np.isfinite(PW[name])
    scores = {name: float(ll(PW[name][k], y[k]).mean()) for name in EXPORTABLE}
    best = min(scores, key=scores.get)
    ens = "F · Ensemble (weights from earlier seasons)"
    gain, t = paired(ll(PW[best][k], y[k]), ll(PW[ens][k], y[k]))
    return {"model": best, "rule": "exportable candidate with the lowest development-season straight-up log loss",
            "dev_log_loss": scores, "ensemble_vs_choice_dev": {"gain": gain, "t": t, "n": int(k.sum())}}


TRAINING = {"Ledger Model v1": ("settings tuned on 2006–2015; ratings walk forward from 2006", "ledger-v1"),
            "B · Dynamic ratings (Kalman)": ("settings chosen on 2012–2017; ratings walk forward from 2006", "lab-1/B"),
            "Point-differential baseline": ("no fitted settings; season-to-date margins", "lab-1/pd"),
            "Home-field-only baseline": ("none (home team by 1.8)", "lab-1/hfa"),
            "A · Opponent-adjusted efficiency": ("2007 to the season before (refit each season)", "lab-1/A"),
            "C · Efficiency + quarterback": ("2007 to the season before (refit each season)", "lab-1/C"),
            "D · Gradient-boosted matchup": ("2007 to the season before (refit each season)", "lab-1/D"),
            "E · Separate team scores": ("2007 to the season before (refit each season)", "lab-1/E"),
            "F · Ensemble (weights from earlier seasons)": ("A–E as above; weights from 2009 to the season before", "lab-1/F")}


def verdicts(res):
    """Deployment verdict for every model: it qualifies only if combining it with the closing market beats the market
    on test seasons (blend gate). Reasons are listed for every failure."""
    out = {}
    rows = {r["model"]: r for r in res["results"].get("test", [])}
    for name, r in rows.items():
        if name == "Closing market":
            continue
        b = (res.get("blend") or {}).get(name) or {}
        why = []
        for key, label in (("su_vs_close", "straight-up"), ("sp_vs_close", "against the spread"), ("tot_vs_close", "on totals")):
            v = r.get(key)
            if v and v[0] is not None and v[0] < 0:
                why.append(f"worse than the closing market {label} (log loss {v[0]:+.4f} per game, t {v[1]:.1f}, {v[2]} games)")
        for kind, label in (("su", "straight-up"), ("spread", "against the spread")):
            x = b.get(kind)
            if x and not x.get("qualifies"):
                why.append(f"adds nothing to the closing market {label} when blended with it (gain {x['gain']:+.4f}, t {x['t']:.1f}, "
                           f"better in {x['seasons_positive']} of 8 test seasons)")
        cal = r.get("su_cal")
        if cal and cal[0] is not None and not (0.8 <= cal[0] <= 1.2):
            why.append(f"straight-up chances miscalibrated (slope {cal[0]:.2f})")
        q = any((b.get(k) or {}).get("qualifies") for k in ("su", "spread"))
        out[name] = {"qualifies": bool(q), "reasons": [] if q else why}
    return out


NFL_TO_ESPN = {"LA": "LAR", "WAS": "WSH"}


def shadow_summary(res, name):
    """The shadow model's headline test-season numbers, carried in nfl_shadow.json for the page."""
    r = next((x for x in res["results"].get("test", []) if x["model"] == name), None)
    if not r:
        return None
    pick = lambda v: {"gain": v[0], "t": v[1], "n": v[2]} if v and v[0] is not None else None
    return {"seasons": list(res["protocol"]["test"]), "su_vs_close": pick(r.get("su_vs_close")), "sp_vs_close": pick(r.get("sp_vs_close")),
            "tot_vs_close": pick(r.get("tot_vs_close")), "qualifies": (res.get("verdicts") or {}).get(name, {}).get("qualifies", False)}


def export_shadow(g, F, snaps, qb, name, settings, out_dir, summary=None):
    """Publish what the private score-update task needs to compute the shadow model for upcoming games: the ridge
    coefficients (fitted on every season before the live one, as the walk-forward test did), each team's current
    ratings, and its listed starting quarterback's value. Team-level numbers only, no game predictions.
    Checks itself: the published numbers must reproduce the lab's own predictions for each team's next game."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    sys.path.insert(0, os.path.dirname(HERE))
    import apply_scores as AS
    ET = ZoneInfo("America/New_York")
    cm, ct = EXPORTABLE[name]
    y_m, y_t = g["margin"].to_numpy(float), g["total"].to_numpy(float)
    tr = ((g["season"] >= FIRST) & (g["season"] < LIVE) & g["played"] & F["_ok"]).to_numpy()
    te = ((g["season"] == LIVE) & F["_ok"]).to_numpy()
    parts, lab = {}, {}
    for key, cols, y in (("margin", cm, y_m), ("total", ct, y_t)):
        pred, mdl = fit_predict_linear(F[cols].to_numpy(float), y, tr, te)
        sc, rg = mdl.named_steps["standardscaler"], mdl.named_steps["ridge"]
        parts[key] = {"features": list(cols), "mean": [float(v) for v in sc.mean_], "scale": [float(v) for v in sc.scale_],
                      "coef": [float(v) for v in rg.coef_], "intercept": float(rg.intercept_)}
        lab[key] = pred
    esp = lambda t: NFL_TO_ESPN.get(t, t)
    live = g[g["season"] == LIVE]
    up = live[~live["played"]]
    teams = {}
    for t in sorted(set(live["home_team"]) | set(live["away_team"])):
        nx = up[(up["home_team"] == t) | (up["away_team"] == t)]
        if nx.empty:
            continue
        i = nx.index[0]; r = g.loc[i]
        R = snaps.get((LIVE, int(r["week"])))
        if R is None or t not in R.index:
            continue
        side = "home" if r["home_team"] == t else "away"
        homes = live[live["home_team"] == t]
        q, qc = float(qb.loc[i, f"{side}_qb"]), float(qb.loc[i, f"{side}_qb_chg"])
        teams[esp(t)] = {
            "snap": {c: float(R.loc[t, c]) for c in OFF + DEF + ["o_pace"]},
            "qb_last": q - qc, "dome_home": int(homes["dome"].iloc[-1]) if len(homes) else 0,
            "next": {"date": str(r["gameday"]), "week": int(r["week"]), "side": side, "opp": esp(r["away_team"] if side == "home" else r["home_team"]),
                     "qb": q, "qb_chg": qc, "qb_name": None if pd.isna(r[f"{side}_qb_name"]) else str(r[f"{side}_qb_name"]),
                     "rest": float(r[f"{side}_rest"]) if pd.notna(r[f"{side}_rest"]) else 7.0,
                     "dome": int(r["dome"]), "wind": float(r["wind_f"]), "temp": float(r["temp_f"]), "post": int(r["post"])}}
    # data cutoff: the end of the last game the data includes (kickoff, US Eastern, plus four hours)
    played = g[g["played"]]
    kos = [dt.datetime.fromisoformat(f"{d}T{tm if isinstance(tm, str) else '13:00'}") for d, tm in zip(played["gameday"], played["gametime"])]
    asof = (max(kos).replace(tzinfo=ET) + dt.timedelta(hours=4)).isoformat() if kos else None
    S = {"model": "lab-1-" + TRAINING[name][1].split("/")[1], "name": name, "version": VERSION,
         "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "asof": asof,
         "trainedOn": [FIRST, LIVE - 1], "settings": settings, "pairs": [[o, d] for o, d in zip(OFF, DEF)],
         "margin": parts["margin"], "total": parts["total"],
         "fill": {"wind": float(g["wind"].median()), "temp": 60.0},
         "note": "Shadow model: recorded before kickoff for evaluation only; never used for picks, value or stakes.",
         "test": summary, "teams": teams}
    # self-check against the lab's own predictions for games that are both teams' next game
    diffs = []
    for i in up.index:
        r = g.loc[i]
        h, a = teams.get(esp(r["home_team"])), teams.get(esp(r["away_team"]))
        if not h or not a or h["next"]["date"] != str(r["gameday"]) or a["next"]["date"] != str(r["gameday"]) or not te[i]:
            continue
        e = {"home": {"abbr": esp(r["home_team"])}, "away": {"abbr": esp(r["away_team"])}, "neutral": bool(r["neutral"])}
        ko = dt.datetime.fromisoformat(f"{r['gameday']}T{r['gametime'] if isinstance(r['gametime'], str) else '13:00'}").replace(tzinfo=ET)
        x = AS.shadow_numbers(S, "NFL", e, False, ko)
        if x and np.isfinite(lab["margin"][i]) and np.isfinite(lab["total"][i]):
            diffs.append(max(abs(x["m"] - lab["margin"][i]), abs(x["t"] - lab["total"][i])))
    S["check"] = {"games": len(diffs), "max_diff": round(max(diffs), 3) if diffs else None}
    if diffs and max(diffs) > 0.06:
        raise SystemExit(f"shadow export does not reproduce the lab's predictions (max diff {max(diffs):.3f})")
    with open(os.path.join(out_dir, "nfl_shadow.json"), "w") as fh:
        json.dump(S, fh, separators=(",", ":"))
    return {"model": S["model"], "teams": len(teams), "asof": asof, **S["check"]}


def fmt(v, p=3):
    return "—" if v is None or (isinstance(v, float) and not math.isfinite(v)) else (f"{v:.{p}f}" if isinstance(v, float) else str(v))


def report(res):
    L = [f"# NFL model lab ({res['version']})", "", f"Updated {res['updatedAt']}. Development seasons {res['protocol']['dev'][0]}–{res['protocol']['dev'][1]} chose every setting; "
         f"test seasons {res['protocol']['test'][0]}–{res['protocol']['test'][1]} were scored once, each refit on all seasons before it. Live = {res['protocol']['live']} to date.", "",
         f"Settings chosen on development seasons: `{json.dumps(res['settings'])}`", ""]
    for split, rows in res["results"].items():
        L += [f"## {split.capitalize()} seasons", "", "| Model | Games | SU acc | SU Brier | SU log loss | SU gain vs close (t) | Margin MAE | Total MAE | Spread gain vs close (t) | ATS 2+ | Totals gain vs close (t) | SU calib slope |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            svc = r.get("su_vs_close"); spc = r.get("sp_vs_close"); tvc = r.get("tot_vs_close"); cal = r.get("su_cal") or (None, None, None)
            a2 = (r.get("ats") or {}).get("2")
            L.append(f"| {r['model']} | {r.get('su_n', r['n'])} | {fmt(r.get('su_acc'))} | {fmt(r.get('su_brier'), 4)} | {fmt(r.get('su_ll'), 4)} | "
                     f"{(fmt(svc[0], 4) + ' (' + fmt(svc[1], 2) + ')') if svc and svc[0] is not None else '—'} | {fmt(r.get('mae_margin'), 2)} | {fmt(r.get('mae_total'), 2)} | "
                     f"{(fmt(spc[0], 4) + ' (' + fmt(spc[1], 2) + ')') if spc and spc[0] is not None else '—'} | {(f'{a2[0]}–{a2[1]}') if a2 else '—'} | "
                     f"{(fmt(tvc[0], 4) + ' (' + fmt(tvc[1], 2) + ')') if tvc and tvc[0] is not None else '—'} | {fmt(cal[0], 2)} |")
        L.append("")
    if res.get("blend"):
        L += ["## Market + model (fitted on development seasons, scored on test seasons)", "", "| Model | SU gain (t) | SU seasons + | Spread gain (t) | Spread seasons + | Qualifies |", "|---|---|---|---|---|---|"]
        for name, r in res["blend"].items():
            su, sp = r.get("su", {}), r.get("spread", {})
            L.append(f"| {name} | {fmt(su.get('gain'), 4)} ({fmt(su.get('t'), 2)}) | {su.get('seasons_positive', '—')}/8 | {fmt(sp.get('gain'), 4)} ({fmt(sp.get('t'), 2)}) | {sp.get('seasons_positive', '—')}/8 | {'yes' if su.get('qualifies') or sp.get('qualifies') else 'no'} |")
        L.append("")
    L += ["Gains are average log-loss improvements per game over the closing market (positive = better than the market); t is the paired t statistic.",
          "A same-time (forecast-time) market is not available historically (nflverse keeps closing numbers only), so the closing market is the "
          "only historical reference. It is the stronger of the two: a model that cannot match it may still match an earlier price, which the "
          "shadow record below measures from now on.", ""]
    pr = lambda v: f"{fmt(v[0], 4)} ({fmt(v[1], 2)})" if v and v[0] is not None else "—"
    rec = lambda x: f"{x[0]}–{x[1]} ({100 * x[0] / max(1, x[0] + x[1]):.1f}%)" if x else "—"
    test = {r["model"]: r for r in res["results"].get("test", [])}
    if test:
        L += ["## Spreads and totals (test seasons, closing lines)", "",
              "| Model | ATS all | ATS 2+ | ATS 3+ | Cover calib slope | O/U all | O/U 4+ | Over calib slope |", "|---|---|---|---|---|---|---|---|"]
        for name, r in test.items():
            if name == "Closing market":
                continue
            at, ou = r.get("ats") or {}, r.get("ou") or {}
            L.append(f"| {name} | {rec(at.get('0'))} | {rec(at.get('2'))} | {rec(at.get('3'))} | {fmt((r.get('sp_cal') or [None])[0], 2)} | "
                     f"{rec(ou.get('0'))} | {rec(ou.get('4'))} | {fmt((r.get('tot_cal') or [None])[0], 2)} |")
        L += ["", "Records pick the side of the closing line the model's number points to (pushes and exact agreement left out). Break-even at −110 is 52.4%.", ""]
    if res.get("vs_v1", {}).get("test"):
        L += ["## Against Ledger Model v1 (test seasons, same games, paired)", "",
              "| Model | SU log-loss gain (t) | Margin abs-error gain (t) | Total abs-error gain (t) | Games |", "|---|---|---|---|---|"]
        for name, r in res["vs_v1"]["test"].items():
            L.append(f"| {name} | {pr(r.get('su'))} | {pr(r.get('margin_abs_err'))} | {pr(r.get('total_abs_err'))} | {r['su'][2]} |")
        L += ["", "Positive = better than Ledger Model v1.", ""]
    if res.get("by_season"):
        names = [n for n in next(iter(res["by_season"].values())) if n != "Closing market"]
        L += ["## By season: straight-up log-loss gain vs the closing market (× 1000; positive = better than the market)", "",
              "| Model | " + " | ".join(res["by_season"]) + " |", "|---|" + "---|" * len(res["by_season"])]
        for n in names:
            L.append(f"| {n} | " + " | ".join(fmt(1000 * v[n]["su_vs_close"][0], 0) if v[n].get("su_vs_close") and v[n]["su_vs_close"][0] is not None else "—"
                                            for v in res["by_season"].values()) + " |")
        L.append("| Games | " + " | ".join(str(v["Closing market"]["su_n"]) for v in res["by_season"].values()) + " |")
        L.append("")
    if res.get("categories"):
        L += ["## Market categories (test seasons): gain vs the closing market, straight-up / spread (t)", "",
              "| Model | " + " | ".join(res["categories"]) + " |", "|---|" + "---|" * len(res["categories"])]
        for n in [n for n in next(iter(res["categories"].values())) if n != "Closing market"]:
            L.append(f"| {n} | " + " | ".join(f"{pr(c[n].get('su_vs_close'))} / {pr(c[n].get('sp_vs_close'))}" for c in res["categories"].values()) + " |")
        L.append("| Games | " + " | ".join(str(c["Closing market"]["n"]) for c in res["categories"].values()) + " |")
        L.append("")
    if res.get("verdicts"):
        p = res["protocol"]
        L += ["## Deployment verdict", "",
              "A model qualifies only if blending it with the closing market beats the market alone on the test seasons: gain above zero, t of 2 or more, "
              "a positive model weight, and better in at least 5 of the 8 seasons.", "",
              "| Model | Version | Training | Testing | Games | Qualifies | Reasons |", "|---|---|---|---|---|---|---|"]
        for name, v in res["verdicts"].items():
            tr, ver = TRAINING.get(name, ("—", "—"))
            L.append(f"| {name} | {ver} | {tr} | {p['test'][0]}–{p['test'][1]} | {test.get(name, {}).get('su_n', '—')} | {'yes' if v['qualifies'] else 'no'} | "
                     f"{'; '.join(v['reasons']) or '—'} |")
        L.append("")
    if res.get("shadow"):
        s = res["shadow"]; e = s.get("ensemble_vs_choice_dev") or {}
        L += ["## Shadow model", "",
              f"Chosen on development seasons only ({s['rule']}): **{s['model']}**. Development log loss: "
              + ", ".join(f"{k.split(' · ')[0]} {v:.4f}" for k, v in s["dev_log_loss"].items())
              + f". The full ensemble F was {fmt(e.get('gain'), 4)} better per game on the same {e.get('n')} development games (t {fmt(e.get('t'), 2)}), "
              "too small to justify a model the score-update task cannot compute from published team numbers.", "",
              "It is recorded before every NFL kickoff in the private ledger (append-only, with its version and data cutoff) and never used for a pick, "
              "a value verdict or a stake. It would be promoted only after its verified pregame record beats the same-time market prices the ledger saves.", ""]
        if s.get("export"):
            x = s["export"]
            L += [f"Published numbers: model/results/nfl_shadow.json ({x['teams']} teams, data through {x['asof']}); "
                  f"self-check reproduced the lab's predictions for {x['games']} upcoming games (largest difference {x['max_diff']} points).", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
