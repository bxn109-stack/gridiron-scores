"""Tests for the basketball history (model/bb_data.py), Ledger Model (model/bb_model.py) and Claude Model (model/bb_cm.py)."""
import datetime as dt
import json
import os
import random
import sys
import tempfile
import unittest

def _rj(path):
    with open(path) as f:
        return json.load(f)


HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "model"))
sys.path.insert(0, HERE)
import bb_data as D  # noqa: E402
import bb_model as BM  # noqa: E402
import bb_cm as CM  # noqa: E402
from test_fetch_bb import SUMMARY, event  # noqa: E402


def seasons(tmp, lg="NBA", lines=True, n_days=60):
    """Synthetic seasons: teams with fixed strengths, box totals, and (optionally) a sharp closing line."""
    rnd = random.Random(7)
    teams = [str(i) for i in range(16)]
    o = {t: rnd.gauss(0, 4) for t in teams}
    d = {t: rnd.gauss(0, 4) for t in teams}
    for s in (2023, 2024, 2025, 2026, 2027):
        rows, n = [], 0
        for day in range(n_days if s < 2027 else 5):
            rnd.shuffle(teams)
            for i in range(0, 8, 2):
                h, a = teams[i], teams[i + 1]
                pace = 99 + rnd.gauss(0, 3)
                hs = round((114 + o[h] - d[a] + 1.5 + rnd.gauss(0, 9)) * pace / 100)
                as_ = round((114 + o[a] - d[h] - 1.5 + rnd.gauss(0, 9)) * pace / 100)
                hs += hs == as_
                mu = (o[h] - d[a]) - (o[a] - d[h]) + 3
                st = lambda pts: [round(pace * .88), round(pace * .41), round(pace * .25), round(pace * .19), 35, 12, 44, None, 25, None, pts]
                date = (dt.date(s - 1, 11, 1) + dt.timedelta(days=day)).isoformat()
                rows.append([f"{s}{n}", f"{date}T23:00Z", 2, 0, h, a, h, a, hs, as_, round((mu + rnd.gauss(0, 2)) * 2) / 2 if lines else None,
                             228.5 if lines else None, None, None, "DraftKings" if lines else None, st(hs), st(as_)])
                n += 1
        with open(os.path.join(tmp, f"{lg}_{s}.json"), "w") as f:
            json.dump({"lg": lg, "season": s, "cols": BM.COLS, "done": [], "games": rows}, f)


class History(unittest.TestCase):
    def test_parse_game_and_season_dates(self):
        ev = event("401", "2026-03-01T00:00Z", state="post", completed=True, hs="88", as_="61")
        ev["season"] = {"type": 2}
        ev["competitions"][0]["competitors"][0]["statistics"] = [{"name": "fieldGoalsAttempted", "displayValue": "60"},
                                                                 {"name": "points", "displayValue": "88"}]
        r = D.parse_game(ev, "MBB")
        self.assertEqual(r[:12], ["401", "2026-03-01T00:00Z", 2, 0, "150", "349", "Duke", "Army", 88, 61, 24.5, 141.5])
        self.assertEqual(r[15][0], 60)
        ev["season"] = {"type": 1}
        self.assertIsNone(D.parse_game(ev, "MBB"))                            # preseason is skipped
        self.assertIsNone(D.parse_game(event("402", "2026-03-01T00:00Z"), "MBB"))  # not finished
        days = D.season_dates("MBB", 2026, dt.date(2026, 11, 10))
        self.assertEqual((days[0], days[-1]), (dt.date(2025, 11, 1), dt.date(2026, 4, 15)))

    def test_lines_from_game_summary(self):
        summ = json.loads(json.dumps(SUMMARY))
        comps = summ["header"]["competitions"][0]["competitors"]
        comps[0]["team"]["abbreviation"], comps[1]["team"]["abbreviation"] = "DUKE", "ARMY"
        summ["pickcenter"] = [{"provider": {"name": "Draft Kings"}, "details": "DUKE -24.5", "overUnder": 141.5,
                               "homeTeamOdds": {"moneyLine": -5000}, "awayTeamOdds": {"moneyLine": 1800}}]
        d = {"games": [["401", "2026-03-01T00:00Z", 2, 0, "150", "349", "Duke", "Army", 88, 61, None, None, None, None, None,
                        [60, 30, 20, 15, 20, 7, 40, None, 15, None, 88], None]], "tried": []}
        self.assertEqual(D.lines_backfill("MBB", d, 5, lambda url: summ, pause=0), 1)
        r = d["games"][0]
        self.assertEqual((r[10], r[11], r[12], r[13], r[14]), (24.5, 141.5, -5000, 1800, "DraftKings"))
        self.assertEqual((r[15][7], r[15][9]), (12, 11))                      # offensive rebounds and turnovers filled in
        self.assertEqual(d["tried"], ["401"])
        # a game whose summary has no line is not asked again; a failed request is
        d["games"].append(["402"] + r[1:10] + [None] * 5 + [None, None])
        self.assertEqual(D.lines_backfill("MBB", d, 5, lambda url: None, pause=0), 1)
        self.assertEqual(d["tried"], ["401"])
        self.assertEqual(D.lines_backfill("MBB", d, 5, lambda url: {}, pause=0), 1)
        self.assertEqual(D.lines_backfill("MBB", d, 5, lambda url: self.fail("asked again"), pause=0), 0)


class Models(unittest.TestCase):
    def test_ledger_model_runs_with_and_without_lines(self):
        for lines in (False, True):
            with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
                seasons(data, lines=lines)
                BM.main(["--leagues", "NBA", "--data", data, "--out", out])
                r = _rj((os.path.join(out, "bb_model.json")))["leagues"]["NBA"]
                self.assertNotIn("error", r)
                self.assertEqual(len(r["ratings"]), 16)
                self.assertEqual(r["lined"] > 0, lines)
                if not lines:
                    self.assertEqual(r["spread"]["why"], "not enough games with a closing line")
                else:
                    self.assertGreater(len(r["push"]["margin"]), 0)

    def test_claude_model_numbers(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as box, tempfile.TemporaryDirectory() as out:
            seasons(data, lines=True)
            with open(os.path.join(box, f"{BM.current_season('NBA') - 1}-11-08.json"), "w") as f:
                json.dump({"games": {"1": {"lg": "NBA", "players": [{"n": "A B", "tm": "ATL", "min": 34, "pts": 25, "reb": 5, "ast": 9, "fp": 44.0}]}}}, f)
            CM.main(["--leagues", "NBA", "--data", data, "--box", box, "--out", out])
            r = _rj((os.path.join(out, "bb_cm.json")))["leagues"]["NBA"]
            self.assertNotIn("error", r)
            self.assertEqual(set(r["params"]), {"k", "kp", "regress", "hca", "b2b"})
            self.assertGreaterEqual(len(r["teams"]), 12)                  # the teams that played this season
            self.assertIn("pass", r["gate"])
            self.assertGreater(r["backtest"]["atsN"], 0)
            self.assertEqual(r["players"]["ATL"]["A B"]["pts"], 25.0)


if __name__ == "__main__":
    unittest.main()
