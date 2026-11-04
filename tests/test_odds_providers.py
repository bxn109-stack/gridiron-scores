"""Tests for odds_providers.py (normalized sportsbook quotes). Run: python3 -m unittest discover -s tests"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import odds_providers as OP  # noqa: E402

TS = "2026-11-04T17:00:00+00:00"

# ESPN's newer odds layout (moneyline / pointSpread / total with open and close blocks)
ESPN_NEW = {
    "provider": {"id": "100", "name": "DraftKings"},
    "details": "KC -3.5", "overUnder": 47.5, "spread": -3.5,
    "moneyline": {"home": {"open": {"odds": "-160"}, "close": {"odds": "-185"}},
                  "away": {"open": {"odds": "+135"}, "close": {"odds": "+154"}}},
    "pointSpread": {"home": {"open": {"line": "-3", "odds": "-110"}, "close": {"line": "-3.5", "odds": "-108"}},
                    "away": {"open": {"line": "+3", "odds": "-110"}, "close": {"line": "+3.5", "odds": "-112"}}},
    "total": {"over": {"open": {"line": "o46.5", "odds": "-110"}, "close": {"line": "o47.5", "odds": "-105"}},
              "under": {"open": {"line": "u46.5", "odds": "-110"}, "close": {"line": "u47.5", "odds": "-115"}}},
}
# ESPN's older layout (prices on homeTeamOdds / awayTeamOdds)
ESPN_OLD = {
    "provider": {"name": "ESPN BET"}, "details": "BUF -7", "overUnder": 50.5, "spread": -7.0,
    "overOdds": -110, "underOdds": -110,
    "homeTeamOdds": {"moneyLine": -340, "spreadOdds": -105},
    "awayTeamOdds": {"moneyLine": 270, "spreadOdds": -115},
}


class Parsers(unittest.TestCase):
    def test_am(self):
        self.assertEqual(OP.am("-110"), -110)
        self.assertEqual(OP.am("+130"), 130)
        self.assertEqual(OP.am("−125"), -125)
        self.assertEqual(OP.am("EVEN"), 100)
        self.assertEqual(OP.am(-110.0), -110)
        for bad in (None, "", "OFF", "50", 99, "-99", "1.91"):
            self.assertIsNone(OP.am(bad), bad)

    def test_num(self):
        self.assertEqual(OP.num("o47.5"), 47.5)
        self.assertEqual(OP.num("u47.5"), 47.5)
        self.assertEqual(OP.num("-3.5"), -3.5)
        self.assertEqual(OP.num("+3"), 3.0)
        self.assertEqual(OP.num("PK"), 0.0)
        self.assertIsNone(OP.num("off"))

    def test_book_names(self):
        self.assertEqual(OP.canon("Draft Kings"), "DraftKings")
        self.assertEqual(OP.canon("FanDuel"), "FanDuel")
        self.assertEqual(OP.canon("Some Book"), "Some Book")
        self.assertEqual(OP.espn_book({"provider": {"name": "Draft Kings"}, "spread": -3}, TS)["book"], "DraftKings")

    def test_implied(self):
        self.assertAlmostEqual(OP.implied(-110), 110 / 210)
        self.assertAlmostEqual(OP.implied(150), 0.4)
        self.assertIsNone(OP.implied(None))


class Espn(unittest.TestCase):
    def test_new_layout_close_and_open(self):
        cur = OP.espn_book(ESPN_NEW, TS, "close")
        self.assertEqual(cur["book"], "DraftKings")
        self.assertEqual(cur["ml"], {"A": 154, "H": -185})
        self.assertEqual(cur["spread"], {"A": [3.5, -112], "H": [-3.5, -108]})
        self.assertEqual(cur["total"], {"O": [47.5, -105], "U": [47.5, -115]})
        self.assertEqual(cur["ts"], TS)
        self.assertIsNone(cur["pts"])
        self.assertNotIn("dq", cur)
        op = OP.espn_book(ESPN_NEW, TS, "open")
        self.assertEqual(op["spread"]["H"], [-3.0, -110])
        self.assertEqual(op["total"]["O"], [46.5, -110])

    def test_old_layout(self):
        cur = OP.espn_book(ESPN_OLD, TS, "close")
        self.assertEqual(cur["ml"], {"A": 270, "H": -340})
        self.assertEqual(cur["spread"], {"H": [-7.0, -105], "A": [7.0, -115]})
        self.assertEqual(cur["total"], {"O": [50.5, -110], "U": [50.5, -110]})
        # the old layout has no opener
        self.assertTrue(OP.empty(OP.espn_book(ESPN_OLD, TS, "open")))

    def test_line_without_price_is_kept_as_none(self):
        o = {"provider": {"name": "X"}, "spread": -2.5, "overUnder": 41}
        cur = OP.espn_book(o, TS)
        self.assertEqual(cur["spread"]["H"], [-2.5, None])
        self.assertEqual(cur["total"]["O"], [41.0, None])
        self.assertNotIn("ml", cur)

    def test_competition_quotes(self):
        cur, opn = OP.espn_quotes({"odds": [ESPN_NEW, ESPN_OLD]}, TS)
        self.assertEqual(set(cur), {"DraftKings", "ESPN BET"})
        self.assertEqual(set(opn), {"DraftKings"})
        self.assertTrue(opn["DraftKings"]["opener"])

    def test_quality_checks(self):
        q = {"spread": {"A": [3.5, -110], "H": [-3.0, -110]}, "total": {"O": [47.5, -110], "U": [48.5, -110]}, "ml": {"A": 120, "H": 110}}
        dq = OP.check(q)
        self.assertTrue(any("mirror" in x for x in dq))
        self.assertTrue(any("over and under" in x for x in dq))
        self.assertTrue(any("under 100%" in x for x in dq))

    def test_flatten_has_every_field(self):
        recs = OP.flatten("401", OP.espn_book(ESPN_NEW, TS))
        self.assertEqual(len(recs), 6)
        need = {"event", "book", "market", "selection", "line", "price", "provider_ts", "retrieved_ts", "status", "source"}
        for r in recs:
            self.assertEqual(set(r), need)
        ml = [r for r in recs if r["market"] == "ml"]
        self.assertTrue(all(r["line"] is None for r in ml))


ODDSAPI_EVENT = {
    "id": "abc", "sport_key": "americanfootball_nfl", "commence_time": "2026-11-08T18:00:00Z",
    "home_team": "Kansas City Chiefs", "away_team": "Denver Broncos",
    "bookmakers": [{"key": "fanduel", "title": "FanDuel", "last_update": "2026-11-04T16:58:00Z", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": -200}, {"name": "Denver Broncos", "price": 168}]},
        {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": -110, "point": -4.5}, {"name": "Denver Broncos", "price": -110, "point": 4.5}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": -112, "point": 44.5}, {"name": "Under", "price": -108, "point": 44.5}]}]}],
}
ESPN_GAME = {"id": "401", "lg": "NFL", "state": "pre", "date": "2026-11-08T18:00Z",
             "home": {"disp": "Kansas City Chiefs"}, "away": {"disp": "Denver Broncos"}}


class OddsApi(unittest.TestCase):
    def test_books(self):
        b = OP.oddsapi_books(ODDSAPI_EVENT, TS)["FanDuel"]
        self.assertEqual(b["ml"], {"H": -200, "A": 168})
        self.assertEqual(b["spread"], {"H": [-4.5, -110], "A": [4.5, -110]})
        self.assertEqual(b["total"], {"O": [44.5, -112], "U": [44.5, -108]})
        self.assertEqual(b["pts"], "2026-11-04T16:58:00Z")
        self.assertEqual(b["src"], "oddsapi")

    def test_match_and_swap(self):
        g, sw = OP.match_event(ODDSAPI_EVENT, [ESPN_GAME])
        self.assertIs(g, ESPN_GAME)
        self.assertFalse(sw)
        flipped = dict(ESPN_GAME, home={"disp": "Denver Broncos"}, away={"disp": "Kansas City Chiefs"})
        g, sw = OP.match_event(ODDSAPI_EVENT, [flipped])
        self.assertTrue(sw)
        q = OP.swap_sides(OP.oddsapi_books(ODDSAPI_EVENT, TS)["FanDuel"])
        self.assertEqual(q["ml"], {"A": -200, "H": 168})
        far = dict(ESPN_GAME, date="2026-11-09T18:00Z")
        self.assertEqual(OP.match_event(ODDSAPI_EVENT, [far]), (None, False))

    def test_merge_rate_limited_and_no_key(self):
        games = [dict(ESPN_GAME)]
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "odds", "state.json")
            self.assertIn("no key", OP.oddsapi_merge(games, "", st))
            calls = []

            def fake(sport, key):
                calls.append(sport)
                return ([ODDSAPI_EVENT] if sport == "americanfootball_nfl" else []), "487"
            now = dt.datetime(2026, 11, 4, 17, tzinfo=dt.timezone.utc)
            msg = OP.oddsapi_merge(games, "k", st, every_min=240, now=now, fetch=fake)
            self.assertIn("1 games matched", msg)
            self.assertEqual(games[0]["quotes"]["FanDuel"]["ml"]["H"], -200)
            self.assertEqual(calls, ["americanfootball_nfl"])     # no college games in the pool -> no college call
            self.assertIn("not due", OP.oddsapi_merge(games, "k", st, every_min=240, now=now + dt.timedelta(minutes=30), fetch=fake))
            with open(st) as fh:
                self.assertEqual(json.load(fh)["remaining"], "487")

    def test_merge_survives_errors(self):
        def boom(sport, key):
            raise RuntimeError("HTTP 401")
        with tempfile.TemporaryDirectory() as d:
            msg = OP.oddsapi_merge([dict(ESPN_GAME)], "k", os.path.join(d, "s.json"), fetch=boom)
            self.assertIn("HTTP 401", msg)


if __name__ == "__main__":
    unittest.main()
