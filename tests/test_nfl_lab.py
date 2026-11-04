"""Tests for the NFL model lab: point-in-time features, walk-forward splits and the shadow export.

Needs numpy, pandas and scikit-learn (installed in CI); skipped without them. The end-to-end export check also needs
the play-by-play cache built by model/nfl_data.py and is skipped when it is missing."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, ROOT)
try:
    import numpy as np
    import pandas as pd
    import sklearn  # noqa: F401
    import nfl_lab as LAB
except ImportError:          # pragma: no cover
    LAB = None


def synthetic():
    """Four teams, one season, six weeks: team A is strong on offense, D weak on defense."""
    teams = ["A", "B", "C", "D"]
    rows, games, gid = [], [], 0
    pairs = [("A", "B"), ("C", "D"), ("A", "C"), ("B", "D"), ("A", "D"), ("B", "C")]
    for w in range(1, 7):
        h, a = pairs[(w - 1) % len(pairs)]
        gid += 1
        games.append({"game_id": f"g{gid}", "season": 2020, "week": w, "home_team": h, "away_team": a})
        for t, o in ((h, a), (a, h)):
            base = 0.2 if t == "A" else 0.0
            leak = 0.15 if o == "D" else 0.0
            rows.append({"game_id": f"g{gid}", "season": 2020, "week": w, "team": t, "opp": o, "pf": 24 + 40 * (base + leak), "pa": 20.0,
                         "o_epa": base + leak, "o_pass": base + leak, "o_rush": base, "o_sr": 0.45 + base / 4, "o_xpl": 0.08, "o_early": base,
                         "o_pace": 62.0, "o_to": 1.0})
    L = pd.DataFrame(rows)
    allowed = L[["game_id", "opp"] + [c for c in L.columns if c.startswith("o_")] + ["pf"]].rename(columns={"opp": "team"})
    allowed.columns = ["game_id", "team"] + ["d" + c[1:] for c in allowed.columns[2:-1]] + ["pa_"]
    L = L.drop(columns=["pa"]).merge(allowed.rename(columns={"pa_": "pa"}), on=["game_id", "team"])
    return L, pd.DataFrame(games)


@unittest.skipIf(LAB is None, "numpy, pandas and scikit-learn are needed")
class PointInTime(unittest.TestCase):
    def test_snapshot_uses_only_earlier_weeks(self):
        L, _ = synthetic()
        before = LAB.snapshots(L, lam=0.9, rho=0.6)
        L2 = L.copy()
        L2.loc[L2["week"] == 4, "o_epa"] += 5.0                     # rewrite week 4's results
        after = LAB.snapshots(L2, lam=0.9, rho=0.6)
        for w in (2, 3, 4):
            pd.testing.assert_frame_equal(before[(2020, w)], after[(2020, w)])
        self.assertFalse(before[(2020, 5)].equals(after[(2020, 5)]))

    def test_matchup_sign_and_opponent_adjustment(self):
        L, G = synthetic()
        S = LAB.snapshots(L, lam=0.95, rho=0.6)[(2020, 7)]
        self.assertGreater(S.loc["A", "o_epa"], S.loc["B", "o_epa"])      # A's offense is better
        self.assertGreater(S.loc["D", "d_epa"], S.loc["B", "d_epa"])      # D allows more
        g = pd.DataFrame([{"season": 2020, "week": 7, "home_team": "A", "away_team": "D", "neutral": 0, "rest_diff": 0, "div_game": 0,
                           "dome": 0, "wind_f": 5.0, "temp_f": 60.0, "post": 0}])
        qb = pd.DataFrame([{"home_qb": 0.1, "away_qb": 0.0, "home_qb_chg": 0.0, "away_qb_chg": 0.0}])
        F = LAB.game_features(g, {(2020, 7): S}, qb)
        self.assertGreater(F.loc[0, "m_o_epa"], 0)                         # good offense at home against a leaky defense
        g2 = g.assign(home_team="D", away_team="A")
        self.assertLess(LAB.game_features(g2, {(2020, 7): S}, qb).loc[0, "m_o_epa"], 0)

    def test_walk_never_trains_on_the_season_it_predicts(self):
        g = pd.DataFrame({"season": [2019] * 3 + [2020] * 3 + [2021] * 3, "played": True})
        F = pd.DataFrame({"_ok": [True] * 9})
        seen = []

        def fn(tr, te):
            seen.append((set(g["season"][tr]), set(g["season"][te])))
            return np.zeros(9), np.zeros(9)
        old = LAB.FIRST
        try:
            LAB.FIRST = 2019
            LAB.walk(g, F, [2020, 2021], fn)
        finally:
            LAB.FIRST = old
        self.assertEqual(seen, [({2019}, {2020}), ({2019, 2020}, {2021})])

    def test_qb_value_uses_only_earlier_games(self):
        g = pd.DataFrame([{"game_id": f"q{w}", "season": 2020, "week": w, "home_team": "A", "away_team": "B", "home_qb_id": "Q1",
                           "away_qb_id": "Q2" if w < 4 else "Q3", "played": True} for w in range(1, 6)])
        Q = pd.DataFrame([{"game_id": f"q{w}", "season": 2020, "week": w, "posteam": "A", "passer_player_id": "Q1", "dropbacks": 40,
                           "epa": 8.0 if w < 5 else -40.0} for w in range(1, 6)]
                         + [{"game_id": f"q{w}", "season": 2020, "week": w, "posteam": "B", "passer_player_id": "Q2", "dropbacks": 40, "epa": 6.0}
                            for w in range(1, 4)])
        v = LAB.qb_values(g, Q)
        self.assertAlmostEqual(v.loc[0, "home_qb"], -0.12)                 # no earlier games: an unproven starter
        self.assertGreater(v.loc[4, "home_qb"], v.loc[1, "home_qb"])       # week 5's bad game is not in week 5's value
        self.assertNotEqual(v.loc[3, "away_qb_chg"], 0)                    # B changed starters in week 4
        self.assertEqual(v.loc[2, "away_qb_chg"], 0)

    def test_ats_leaves_out_exact_agreement(self):
        w, l = LAB.ats(np.array([0.0, 1.0, -2.0, 3.0]), np.array([1, 1, 1, 0]), 0)
        self.assertEqual((w, l), (1, 2))


@unittest.skipIf(LAB is None or not os.path.exists(os.path.join(ROOT, "model", ".cache", "nfl", "team_games.csv")),
                 "play-by-play cache not built")
class ShadowExport(unittest.TestCase):
    def test_export_reproduces_the_lab(self):
        with tempfile.TemporaryDirectory() as out:
            res = os.path.join(ROOT, "model", "results", "nfl_lab.json")
            if not os.path.exists(res):
                self.skipTest("no lab results")
            with open(res) as fh, open(os.path.join(out, "nfl_lab.json"), "w") as o:
                o.write(fh.read())
            self.assertEqual(LAB.main(["--shadow-only", "--out", out]), 0)
            with open(os.path.join(out, "nfl_shadow.json")) as fh:
                S = json.load(fh)
            self.assertLessEqual(S["check"]["max_diff"] or 0, 0.06)
            for t in S["teams"].values():
                self.assertEqual(set(t), {"snap", "qb_last", "dome_home", "next"})   # team numbers only


if __name__ == "__main__":
    unittest.main()
