"""Tests for model/ledger_model.py and model/nfl_props.py (Phases 2-4: push tables, gates, blend, prop projections)."""
import math
import os
import random
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "model"))
import ledger_model as LM  # noqa: E402
import nfl_props as NP  # noqa: E402

CSV_HEAD = "game_id,season,game_type,week,gameday,gametime,away_team,away_score,home_team,home_score,location,spread_line,total_line,away_moneyline,home_moneyline\n"


def game(i, season, home, away, hp, ap, close=None, tot=None, date=None):
    return {"id": f"g{i}", "season": season, "week": 1, "date": date or f"{season}-09-{(i % 28) + 1:02d}T13:00:{i % 60:02d}",
            "home": home, "away": away, "neutral": False, "hdiv": "fbs", "adiv": "fbs", "hp": hp, "ap": ap,
            "close": close, "tot": tot, "mlh": None, "mla": None}


class Loading(unittest.TestCase):
    def test_nfl_csv_orientation_and_relocations(self):
        text = CSV_HEAD + "2015_01_X,2015,REG,1,2015-09-13,13:00,OAK,20,KC,27,Home,3.5,44.5,150,-170\n" \
                          "2026_10_Y,2026,REG,10,2026-11-08,13:00,LV,,KC,,Home,-2.5,45,120,-140\n"
        G = LM.load_nfl(text)
        self.assertEqual(G[0]["away"], "LV")                 # Raiders keep one rating across the move
        self.assertEqual(G[0]["close"], 3.5)                 # spread_line is the expected home margin
        self.assertEqual(G[0]["hp"] - G[0]["ap"], 7)
        self.assertIsNone(G[1]["hp"])                        # an unplayed game


class Engine(unittest.TestCase):
    def test_predictions_only_use_earlier_games(self):
        p = dict(k=0.1, kt=0.05, regress=0.7, hfa=2.0, cap=24, fcs_start=0.0)
        G = [game(1, 2020, "A", "B", 30, 10), game(2, 2020, "A", "B", 30, 10), game(3, 2020, "A", "B", None, None)]
        out, state = LM.run(G, p, 21.5)
        self.assertEqual(out[0][1], 2.0)                     # first meeting: only home field
        self.assertGreater(out[1][1], 2.0)                   # learned from game 1
        self.assertGreater(state["R"]["A"], state["R"]["B"])
        self.assertEqual(len(out), 3)                        # the unplayed game gets a prediction, no update

    def test_logistic_fit_recovers_a_slope(self):
        rnd = random.Random(1)
        X, y = [], []
        for _ in range(4000):
            x = rnd.uniform(-2, 2)
            X.append([1.0, x]); y.append(1 if rnd.random() < LM.sig(0.8 * x) else 0)
        a, b = LM.fit_logistic(X, y)
        self.assertAlmostEqual(b, 0.8, delta=0.12)
        self.assertAlmostEqual(a, 0.0, delta=0.12)


class Gates(unittest.TestCase):
    def settings(self):
        return dict(LM.SETTINGS["NFL"], tune=(2000, 2009), test=(2010, 2019))

    def test_noise_fails_the_gate(self):
        rnd = random.Random(7)
        rows = []
        for i in range(6000):
            s = 2000 + i // 300
            close = rnd.choice([-7, -3.5, -3, 1, 3, 6.5])
            m = close + rnd.gauss(0, 13)
            rows.append((game(i, s, "H", "A", 20 + m, 20, close=close, tot=44.5), close + rnd.gauss(0, 3), 44 + rnd.gauss(0, 3)))
        g = LM.edge_gate(rows, self.settings(), "spread")
        self.assertFalse(g["pass"])
        self.assertEqual(g["b"], 0.0)                        # a failed gate carries no weight

    def test_real_signal_passes(self):
        rnd = random.Random(3)
        rows = []
        for i in range(6000):
            s = 2000 + i // 300
            close = rnd.choice([-7, -3.5, -3, 1, 3, 6.5])
            truth = close + rnd.gauss(0, 3)                    # the model knows something the close does not
            m = truth + rnd.gauss(0, 12)
            rows.append((game(i, s, "H", "A", 20 + m, 20, close=close), truth, 44.0))
        g = LM.edge_gate(rows, self.settings(), "spread")
        self.assertTrue(g["pass"], g["why"])
        self.assertGreater(g["b"], 0)


class PushTables(unittest.TestCase):
    def test_rows_sum_to_one_and_find_key_numbers(self):
        G = []
        rnd = random.Random(5)
        for i in range(3000):
            close = rnd.choice([3, 3.5, 2.5])
            margin = rnd.choice([3, 3, 3, 7, 10, -3, 1, 6, 14, -7])
            G.append(game(i, 2010, "H", "A", 20 + margin, 20, close=close, tot=44.5))
        s = dict(LM.SETTINGS["NFL"], spread_grid=(2.5, 3.5), total_grid=(44.5, 44.5))
        T = LM.pmf_table(G, "margin", s)
        row = T["3"]
        self.assertAlmostEqual(sum(row["p"]) + row["lo"] + row["hi"], 1.0, places=3)
        self.assertAlmostEqual(row["p"][3 - row["n0"]], 0.3, delta=0.03)


class Props(unittest.TestCase):
    def test_projection_weights_recent_games(self):
        mu, k = NP.project([(2026, 1, 10), (2026, 2, 10), (2026, 3, 100)], 2026)
        self.assertGreater(mu, 40)
        self.assertEqual(k, 3)
        mu2, _ = NP.project([(2025, 1, 100), (2026, 1, 10)], 2026)
        self.assertLess(mu2, 55)                             # last season counts half

    def test_over_chance_moves_with_the_line(self):
        a = NP.p_over("rec_yds", 70, 30, 50.5)
        b = NP.p_over("rec_yds", 70, 30, 80.5)
        self.assertGreater(a, 0.5); self.assertLess(b, 0.5)
        self.assertAlmostEqual(NP.p_over("td", 0.5, None, None), 1 - math.exp(-0.5))

    def test_names_normalize(self):
        self.assertEqual(NP.norm_name("Ja'Marr Chase"), "jamarrchase")
        self.assertEqual(NP.norm_name("Marvin Harrison Jr."), "marvinharrison")


if __name__ == "__main__":
    unittest.main()
