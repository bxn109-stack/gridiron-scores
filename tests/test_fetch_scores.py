"""Tests for the robot's quote bookkeeping and season archive (fetch_scores.py)."""
import os
import sys
import tempfile
import unittest
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fetch_scores as FS  # noqa: E402

DK1 = {"book": "DraftKings", "src": "espn", "ts": "2026-11-04T10:00:00+00:00", "pts": None,
       "spread": {"A": [3.5, -110], "H": [-3.5, -110]}}
DK2 = dict(DK1, ts="2026-11-04T12:00:00+00:00")
DK3 = dict(DK1, ts="2026-11-04T14:00:00+00:00", spread={"A": [3.0, -110], "H": [-3.0, -110]})
FD = {"book": "FanDuel", "src": "oddsapi", "ts": "2026-11-04T09:00:00+00:00", "spread": {"A": [3.5, -105], "H": [-3.5, -115]}}


def game(state, cur, opn=None):
    return {"id": "1", "lg": "NFL", "state": state, "_q": ({b["book"]: b for b in cur}, opn or {})}


class MergeQuotes(unittest.TestCase):
    def test_unchanged_price_keeps_first_seen_time(self):
        g1 = FS.merge_quotes(game("pre", [DK1]), {})
        self.assertEqual(g1["quotes"]["DraftKings"]["ts"], DK1["ts"])
        self.assertTrue(g1["qopen"]["DraftKings"]["first_seen"])
        g2 = FS.merge_quotes(game("pre", [DK2]), g1)
        self.assertEqual(g2["quotes"]["DraftKings"]["ts"], DK1["ts"])   # same number -> original time
        g3 = FS.merge_quotes(game("pre", [DK3]), g2)
        self.assertEqual(g3["quotes"]["DraftKings"]["ts"], DK3["ts"])   # line moved -> new time
        self.assertEqual(g3["qopen"]["DraftKings"]["spread"]["H"], [-3.5, -110])  # opener never changes
        self.assertEqual(g3["qclose"]["DraftKings"]["spread"]["H"], [-3.0, -110])
        self.assertNotIn("_q", g3)

    def test_espn_opener_used_when_given(self):
        op = dict(DK1, spread={"A": [2.5, -110], "H": [-2.5, -110]}, opener=True)
        g = FS.merge_quotes(game("pre", [DK1], {"DraftKings": op}), {})
        self.assertEqual(g["qopen"]["DraftKings"]["spread"]["H"], [-2.5, -110])

    def test_other_adapters_kept_between_pulls(self):
        old = {"quotes": {"FanDuel": FD}, "qopen": {"FanDuel": FD}}
        g = FS.merge_quotes(game("pre", [DK1]), old)
        self.assertEqual(set(g["quotes"]), {"DraftKings", "FanDuel"})

    def test_frozen_after_kickoff(self):
        g1 = FS.merge_quotes(game("pre", [DK1]), {})
        g2 = FS.merge_quotes(game("in", [DK3]), g1)
        self.assertEqual(g2["qclose"]["DraftKings"]["spread"]["H"], [-3.5, -110])
        self.assertEqual(g2["quotes"], g1["quotes"])

    def test_backfilled_game_is_marked(self):
        g = FS.merge_quotes(game("post", [DK1]), {})
        self.assertTrue(g["qclose"]["DraftKings"]["post_capture"])
        self.assertNotIn("quotes", g)

    def test_sync_open_close(self):
        g = FS.merge_quotes(game("pre", [DK1]), {})
        g["quotes"]["FanDuel"] = FD
        FS.sync_open_close(g)
        self.assertIn("FanDuel", g["qclose"])
        self.assertTrue(g["qopen"]["FanDuel"]["first_seen"])


class Archive(unittest.TestCase):
    def test_merge_by_date(self):
        import datetime as dt
        with tempfile.TemporaryDirectory() as d:
            day = dt.date(2026, 10, 4)
            a = {"id": "1", "lg": "NFL", "completed": True}
            b = {"id": "2", "lg": "NFL", "completed": True}
            self.assertEqual(FS.archive({day: [a]}, d), 1)
            self.assertEqual(FS.archive({day: [a]}, d), 0)          # nothing new -> no write
            self.assertEqual(FS.archive({day: [b]}, d), 1)
            with open(os.path.join(d, "2026-10-04.json")) as fh:
                got = json.load(fh)
            self.assertEqual(set(got["games"]), {"1", "2"})

    def test_et_date(self):
        self.assertEqual(str(FS.et_date({"date": "2026-10-05T00:20Z"})), "2026-10-04")   # Sunday night game
        self.assertIsNone(FS.et_date({"date": None}))


if __name__ == "__main__":
    unittest.main()
