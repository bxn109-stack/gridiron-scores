"""Tests for the basketball robot (fetch_bb.py): date files, win chances, quotes and archive completion."""
import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fetch_bb as BB  # noqa: E402
import fetch_scores as FS  # noqa: E402

UTC = dt.timezone.utc


def event(eid, date, state="pre", home=("150", "Duke", "DUKE", None), away=("349", "Army", "ARMY", None), hs=None, as_=None,
          odds=True, completed=False, lines=None):
    def comp(t, side, score):
        return {"homeAway": side, "score": score, "linescores": [{"value": v} for v in (lines or [])],
                "team": {"id": t[0], "location": t[1], "name": t[1], "displayName": t[1], "shortDisplayName": t[1],
                         "abbreviation": t[2]}, "curatedRank": {"current": t[3] or 99}, "records": [{"type": "total", "summary": "1-0"}]}
    o = [{"provider": {"name": "Draft Kings"}, "details": "DUKE -24.5", "overUnder": 141.5,
          "pointSpread": {"home": {"close": {"line": "-24.5", "odds": "-110"}}, "away": {"close": {"line": "+24.5", "odds": "-110"}}},
          "total": {"over": {"close": {"line": "o141.5", "odds": "-110"}}, "under": {"close": {"line": "u141.5", "odds": "-110"}}},
          "moneyline": {"home": {"close": {"odds": "-5000"}}, "away": {"close": {"odds": "+1800"}}}}] if odds else []
    return {"id": eid, "date": date, "competitions": [{
        "status": {"type": {"state": state, "name": "STATUS_FINAL" if completed else ("STATUS_SCHEDULED" if state == "pre" else "STATUS_IN_PROGRESS"),
                            "completed": completed, "shortDetail": "Final" if completed else "7:00 PM"}, "period": 2 if state != "pre" else 0, "displayClock": "0:00"},
        "neutralSite": False, "odds": o,
        "competitors": [comp(home, "home", hs), comp(away, "away", as_)]}]}


class BasketballRobot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir, self.old_box = BB.DIR, BB.BOX_DIR
        BB.DIR = os.path.join(self.tmp.name, "scores", "bb")
        BB.BOX_DIR = os.path.join(self.tmp.name, "box", "bb")

    def tearDown(self):
        BB.DIR, BB.BOX_DIR = self.old_dir, self.old_box
        self.tmp.cleanup()

    def feed(self, events_by_lg):
        def fetch(dates, retrieved):
            out = {}
            for lg, evs in events_by_lg.items():
                for ev in evs:
                    g = FS.parse_event(ev, lg, retrieved)
                    out[g["id"]] = g
            return out, len(dates) * len(BB.FEEDS), len(dates) * len(BB.FEEDS)
        return fetch

    def test_parse_pred(self):
        self.assertEqual(BB.parse_pred({"predictor": {"homeTeam": {"gameProjection": "97.3"}}}), {"hp": 97.3, "src": "ESPN BPI"})
        self.assertEqual(BB.parse_pred({"predictor": {"awayTeam": {"gameProjection": "40"}}})["hp"], 60.0)
        self.assertIsNone(BB.parse_pred({"predictor": {"homeTeam": {"gameProjection": "0"}}}))
        self.assertIsNone(BB.parse_pred({}))
        self.assertIsNone(BB.parse_pred(None))

    def test_day_file_quotes_close_and_pred(self):
        now = dt.datetime(2026, 11, 10, 15, 0, tzinfo=UTC)            # 10 AM ET
        ev = event("401", "2026-11-11T00:00Z")                         # 7 PM ET on Nov 10
        asked = []
        BB.run(False, now, self.feed({"MBB": [ev]}), lambda lg, gid: asked.append((lg, gid)) or {"predictor": {"homeTeam": {"gameProjection": "98.5"}}})
        day = FS.read_json(os.path.join(BB.DIR, "2026-11-10.json"), {})
        g = day["games"]["401"]
        self.assertEqual(g["lg"], "MBB")
        self.assertEqual(g["pred"]["hp"], 98.5)
        self.assertEqual(g["pred"]["at"], now.isoformat(timespec="seconds"))
        self.assertEqual(g["close"]["hm"], 24.5)                       # home favored by 24.5
        self.assertEqual(g["quotes"]["DraftKings"]["spread"]["H"], [-24.5, -110])
        self.assertEqual(asked, [("MBB", "401")])
        self.assertFalse(day["complete"])
        # an hour later: the win chance is not re-fetched (refresh every 3 hours) and is kept
        asked.clear()
        BB.run(False, now + dt.timedelta(hours=1), self.feed({"MBB": [ev]}), lambda lg, gid: asked.append(gid) or {})
        g = FS.read_json(os.path.join(BB.DIR, "2026-11-10.json"), {})["games"]["401"]
        self.assertEqual(asked, [])
        self.assertEqual(g["pred"]["hp"], 98.5)

    def test_final_keeps_pregame_numbers_and_completes_day(self):
        now = dt.datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
        BB.run(False, now, self.feed({"MBB": [event("401", "2026-11-11T00:00Z")]}), lambda lg, gid: {"predictor": {"homeTeam": {"gameProjection": "98.5"}}})
        later = dt.datetime(2026, 11, 11, 15, 0, tzinfo=UTC)          # next day
        fin = event("401", "2026-11-11T00:00Z", state="post", completed=True, hs="88", as_="61", lines=[40, 48], odds=False)
        asked = []
        BB.run(False, later, self.feed({"MBB": [fin]}), lambda lg, gid: self.fail("no win chance after tip"),
               lambda lg, gid: asked.append((lg, gid)) or SUMMARY)
        day = FS.read_json(os.path.join(BB.DIR, "2026-11-10.json"), {})
        g = day["games"]["401"]
        self.assertEqual((g["home"]["score"], g["away"]["score"]), (88, 61))
        self.assertEqual(g["home"]["lines"], [40, 48])
        self.assertEqual(g["pred"]["hp"], 98.5)                         # pregame number kept
        self.assertEqual(g["close"]["hm"], 24.5)                        # closing line kept
        self.assertIn("DraftKings", g["qclose"])
        self.assertTrue(day["complete"])                                # past date, every game over, every feed answered
        # the finished game's box score is saved under its US Eastern date, once
        self.assertEqual(asked, [("MBB", "401")])
        box = FS.read_json(os.path.join(BB.BOX_DIR, "2026-11-10.json"), {})
        self.assertEqual(box["games"]["401"]["lg"], "MBB")
        self.assertEqual(len(box["games"]["401"]["players"]), 2)
        BB.run(False, later + dt.timedelta(minutes=5), self.feed({"MBB": [fin]}), lambda lg, gid: {}, lambda lg, gid: self.fail("box already saved"))

    def test_parse_box_bb(self):
        b = BB.parse_box_bb(SUMMARY)
        p = {x["n"]: x for x in b["players"]}
        self.assertNotIn("Bench Guy", p)                                # did not play
        d = p["Cooper Flagg"]
        self.assertEqual((d["pts"], d["reb"], d["ast"], d["stl"], d["blk"], d["to"]), (28, 9, 4, 2, 1, 3))
        self.assertEqual((d["fgm"], d["fga"], d["tpm"], d["tpa"], d["ftm"], d["fta"], d["min"]), (10, 18, 3, 7, 5, 6, 34))
        self.assertTrue(d["start"])
        self.assertEqual(d["fp"], round(28 + 1.2*9 + 1.5*4 + 3*1 + 3*2 - 3, 2))
        self.assertEqual(b["teams"]["DUKE"]["st"]["fga"], 70)
        self.assertEqual(b["teams"]["DUKE"]["st"]["to"], 11)
        self.assertTrue(b["teams"]["DUKE"]["home"])
        self.assertIsNone(BB.parse_box_bb({}))

    def test_nothing_before_first_slate(self):
        now = dt.datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
        self.assertEqual(BB.run(False, now, self.feed({}), lambda *a: {}), 0)
        self.assertFalse(os.path.exists(BB.DIR))


LABELS =["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"]
SUMMARY = {
    "header": {"competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "150"}}, {"homeAway": "away", "team": {"id": "349"}}]}]},
    "boxscore": {
        "teams": [{"team": {"id": "150", "abbreviation": "DUKE"}, "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "31-70", "label": "FG"},
            {"name": "offensiveRebounds", "displayValue": "12", "label": "Offensive Rebounds"},
            {"name": "turnovers", "displayValue": "11", "label": "TO"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "17-22", "label": "FT"}]}],
        "players": [{"team": {"id": "150", "abbreviation": "DUKE"}, "statistics": [{"labels": LABELS, "athletes": [
            {"athlete": {"displayName": "Cooper Flagg"}, "starter": True, "didNotPlay": False,
             "stats": ["34", "10-18", "3-7", "5-6", "2", "7", "9", "4", "2", "1", "3", "2", "+20", "28"]},
            {"athlete": {"displayName": "Bench Guy"}, "starter": False, "didNotPlay": True, "stats": []}]}]},
            {"team": {"id": "349", "abbreviation": "ARMY"}, "statistics": [{"labels": LABELS, "athletes": [
                {"athlete": {"displayName": "Army Guard"}, "starter": True,
                 "stats": ["30", "5-12", "2-6", "2-2", "0", "3", "3", "5", "1", "0", "2", "3", "-20", "14"]}]}]}]}}


if __name__ == "__main__":
    unittest.main()
