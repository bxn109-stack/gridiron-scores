"""Tests for the BPI-against-the-close backtest (model/bb_bpi.py)."""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bb_bpi as T  # noqa: E402


def rows(n, seed, edge_skill, start="2025-11-05"):
    """Synthetic games: the market line is truth plus noise; BPI is truth plus noise scaled by skill."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        truth = rnd.gauss(0, 8)
        hm = round((truth + rnd.gauss(0, 2.5)) * 2) / 2
        bpi_m = truth + rnd.gauss(0, 2.5 / max(edge_skill, 1e-3)) if edge_skill else hm + rnd.gauss(0, 3)
        margin = round(truth + rnd.gauss(0, 10))
        z = bpi_m / 11.0
        month = 11 if i < n // 3 else 2
        out.append({"id": str(i), "date": f"2026-{month:02d}-10T00:00Z" if month == 2 else f"{start}T00:00Z", "hm": hm, "margin": margin, "z": z})
    return out


class BpiTest(unittest.TestCase):
    def test_pregame_hp(self):
        self.assertEqual(T.pregame_hp({"predictor": {"homeTeam": {"gameProjection": "71.4"}}}), 71.4)
        self.assertEqual(T.pregame_hp({"predictor": {"awayTeam": {"gameProjection": "30"}}}), 70.0)
        self.assertEqual(T.pregame_hp({"winprobability": [{"homeWinPercentage": 0.625}]}), 62.5)
        self.assertIsNone(T.pregame_hp({"predictor": {}}))
        self.assertIsNone(T.pregame_hp(None))

    def test_fetch_caches_and_respects_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = T.DATA
            T.DATA = tmp
            try:
                games = [{"id": str(i)} for i in range(5)]
                calls = []
                get = lambda url: calls.append(url) or {"predictor": {"homeTeam": {"gameProjection": "60"}}}
                cache, used = T.fetch_bpi("MBB", 2026, games, 3, get, pause=0)
                self.assertEqual((used, len(cache)), (3, 3))
                cache, used = T.fetch_bpi("MBB", 2026, games, 10, get, pause=0)
                self.assertEqual((used, len(cache)), (2, 5))            # only the missing ones are asked
            finally:
                T.DATA = old

    def test_no_skill_fails_and_skill_passes(self):
        none = {2026: rows(3000, 1, 0)}
        r = T.analyze("MBB", none)
        self.assertIsNone(r["gap"])
        good = {2026: rows(3000, 2, 3.0)}
        r = T.analyze("MBB", good)
        self.assertIsNotNone(r["gap"])
        self.assertTrue(all(row["test"]["pct"] is None or row["test"]["pct"] > 50 for row in r["table"] if row["gap"] >= r["gap"]))


if __name__ == "__main__":
    unittest.main()
