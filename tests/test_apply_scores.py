"""Tests for apply_scores.py: archive grading, pick locks, voids, pushes and the pregame record."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import apply_scores as AS  # noqa: E402

NOW = "2026-11-04T17:00:00+00:00"


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def read(path):
    with open(path) as f:
        return json.load(f)


def team(loc, name, abbr, score=None, lines=None):
    return {"loc": loc, "name": name, "disp": f"{loc} {name}", "short": loc, "abbr": abbr, "score": score, "lines": lines or []}


def espn(eid, date, away, home, state="post", sc=(0, 0), lines=((0, 0, 0, 0), (0, 0, 0, 0)), **kw):
    a, h = away, home
    e = {"id": eid, "lg": "NFL", "date": date, "state": state, "status": "STATUS_FINAL" if state == "post" else "STATUS_SCHEDULED",
         "completed": state == "post", "detail": "Final" if state == "post" else "Sun 1:00 PM", "period": 4, "clock": "0:00", "neutral": False,
         "away": team(*a, score=sc[0] if state != "pre" else 0, lines=list(lines[0]) if state == "post" else []),
         "home": team(*h, score=sc[1] if state != "pre" else 0, lines=list(lines[1]) if state == "post" else [])}
    e.update(kw)
    return e


DK = {"book": "DraftKings", "src": "espn", "ts": "2026-11-04T12:00:00+00:00", "pts": None,
      "ml": {"A": 150, "H": -170}, "spread": {"A": [3.5, -110], "H": [-3.5, -110]}, "total": {"O": [44.5, -110], "U": [44.5, -110]}}


def ledger_game(gid, ko, a, h, **kw):
    g = {"id": gid, "lg": "NFL", "ko": ko, "a": {"n": a}, "h": {"n": h}, "neu": False, "tracked": True,
         "line": {"fav": "H", "sp": 3, "tot": 44.5}, "lean": {"mk": "spread", "side": "H", "ln": 3, "conf": 2},
         "star": False, "props": [], "st": "pre", "sc": None}
    g.update(kw)
    return g


class Run:
    """Writes a docs dir + feed + archive + box files, runs apply_scores.py, returns (manifest, outputs)."""

    def __init__(self, tmp, extra=("--pregame",)):
        self.tmp, self.extra = tmp, list(extra)
        for d in ("docs/slates", "docs/results", "docs/pregame", "final", "box", "out"):
            os.makedirs(os.path.join(tmp, d), exist_ok=True)

    def doc(self, slate, lg, games):
        p = os.path.join(self.tmp, "docs", "slates", slate, "leagues")
        os.makedirs(p, exist_ok=True)
        dump({"slate": slate, "league": lg, "games": {g["id"]: g for g in games}}, os.path.join(p, f"{lg}.json"))

    def pregame(self, slate, body):
        dump(body, os.path.join(self.tmp, "docs", "pregame", f"{slate}.json"))

    def feed(self, games, checked="2026-11-04T16:55:00+00:00"):
        dump({"games": games, "updatedAt": checked, "checkedAt": checked}, os.path.join(self.tmp, "latest.json"))

    def final(self, date, games):
        dump({"date": date, "games": {e["id"]: e for e in games}}, os.path.join(self.tmp, "final", f"{date}.json"))

    def box(self, eid, players):
        dump({"id": eid, "players": players}, os.path.join(self.tmp, "box", f"{eid}.json"))

    def go(self, now=NOW):
        out = os.path.join(self.tmp, "out")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "apply_scores.py"), "--docs", os.path.join(self.tmp, "docs"), "--out", out,
                            "--scores", os.path.join(self.tmp, "latest.json"), "--box", os.path.join(self.tmp, "box"),
                            "--final", os.path.join(self.tmp, "final"), "--now", now] + self.extra, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr)
        man = json.loads(r.stdout)
        files = {(m["collection"], m["doc_id"]): read(m["file_path"]) for m in man}
        return man, files, r.stderr


class Grading(unittest.TestCase):
    def test_old_game_graded_from_archive_with_push_lock_and_unresolved(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            # 10 days old: outside the live feed, only in the season archive
            old = espn("E2", "2026-10-25T17:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(21, 24),
                       lines=((7, 7, 0, 7), (3, 7, 7, 7)))
            R.final("2026-10-25", [old])
            R.feed([])
            R.box("E2", [{"n": "Courtland Sutton", "tm": "DEN", "rec": 6, "recYds": 60}])
            g = ledger_game("NFL-broncos-chiefs", "2026-10-25T13:00:00-04:00", "Broncos", "Chiefs", props=[
                {"pl": "Courtland Sutton", "tm": "DEN", "mk": "Rec yds", "ln": 50.5, "side": "O", "odds": "-115"},
                {"pl": "Marvin Mims", "tm": "DEN", "mk": "Rec yds", "ln": 20.5, "side": "O", "odds": "-110"}])
            R.doc("2026-10-24", "NFL", [g])
            man, files, err = R.go()
            ch = files[("slates/2026-10-24/leagues", "NFL")]["games"]["NFL-broncos-chiefs"]
            self.assertEqual(ch["st"], "final")
            self.assertEqual(ch["sc"], {"a": 21, "h": 24})
            self.assertEqual(ch["gr"]["lean"], "P")              # Chiefs -3 won by 3: push
            self.assertEqual(ch["gr"]["su"], "W")
            self.assertEqual(ch["gr"]["props"], ["W", "U"])      # Mims not in the box score a week later: unresolved
            self.assertIn("lock", ch)
            self.assertEqual(ch["lock"]["lean"]["ln"], 3)
            res = files[("results", "2026-10-24")]["games"]["NFL-broncos-chiefs"]
            self.assertEqual(res["props"], {"W": 1, "L": 0, "P": 0, "U": 1})
            self.assertTrue(res["locked"])
            self.assertIn("season archive", err)

    def test_no_unresolved_before_a_day(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            e = espn("E5", "2026-11-04T13:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(10, 30),
                     lines=((0, 7, 3, 0), (7, 10, 3, 10)))
            R.feed([e])
            R.box("E5", [{"n": "Someone Else", "tm": "DEN", "recYds": 5}])
            g = ledger_game("G5", "2026-11-04T08:00:00-05:00", "Broncos", "Chiefs",
                            props=[{"pl": "Marvin Mims", "mk": "Rec yds", "ln": 20.5, "side": "O"}])
            R.doc("2026-10-31", "NFL", [g])
            _, files, _ = R.go()
            ch = files[("slates/2026-10-31/leagues", "NFL")]["games"]["G5"]
            self.assertEqual(ch["gr"]["props"], [None])

    def test_grading_uses_the_lock_not_later_edits(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            e = espn("E1", "2026-11-02T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(17, 27),
                     lines=((7, 0, 10, 0), (7, 10, 0, 10)))
            R.feed([e])
            lock = AS.pick_snapshot(ledger_game("G1", "", "", "", lean={"mk": "spread", "side": "A", "ln": 3, "conf": 1}))
            lock["at"] = "2026-11-02T18:05:00+00:00"
            # someone changed the lean to the winning side after kickoff
            g = ledger_game("G1", "2026-11-02T13:00:00-05:00", "Broncos", "Chiefs", lock=lock,
                            lean={"mk": "spread", "side": "H", "ln": 3, "conf": 3})
            R.doc("2026-10-31", "NFL", [g])
            _, files, _ = R.go()
            ch = files[("slates/2026-10-31/leagues", "NFL")]["games"]["G1"]
            self.assertEqual(ch["gr"]["lean"], "L")            # graded as the locked Broncos +3 (lost by 10)
            self.assertEqual(ch["lockdiff"], ["lean"])

    def test_prop_added_after_lock_is_not_graded(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            e = espn("E1", "2026-11-02T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(17, 27),
                     lines=((7, 0, 10, 0), (7, 10, 0, 10)))
            R.feed([e])
            R.box("E1", [{"n": "Travis Kelce", "tm": "KC", "rec": 8, "recYds": 90}])
            p1 = {"pl": "Travis Kelce", "mk": "Receptions", "ln": 5.5, "side": "O"}
            g0 = ledger_game("G1", "2026-11-02T13:00:00-05:00", "Broncos", "Chiefs", props=[p1])
            lock = dict(AS.pick_snapshot(g0), at="2026-11-02T18:05:00+00:00")
            g = dict(g0, lock=lock, props=[p1, {"pl": "Travis Kelce", "mk": "Rec yds", "ln": 60.5, "side": "O"}])
            R.doc("2026-10-31", "NFL", [g])
            _, files, _ = R.go()
            ch = files[("slates/2026-10-31/leagues", "NFL")]["games"]["G1"]
            self.assertEqual(ch["gr"]["props"], ["W", "X"])
            self.assertEqual(ch["lockdiff"], ["props"])

    def test_new_stat_markets(self):
        p = {"n": "QB", "passAtt": 31, "comp": 22, "kickPts": 0}
        self.assertEqual(AS.stat_for("Pass attempts", p), 31)
        self.assertEqual(AS.stat_for("Completions", p), 22)
        self.assertIsNone(AS.stat_for("Pass attempts", {"n": "Old box", "passYds": 200}))
        self.assertIsNone(AS.stat_for("Kicking points", {"n": "K"}))
        self.assertEqual(AS.stat_for("Kicking points", {"n": "K", "kickPts": 9}), 9)

    def test_finished_counts_void_and_excluded(self):
        g = ledger_game("G", "", "", "", st="final", per={"a": [0] * 4, "h": [0] * 4}, props=[{}, {}], pv=[3, None],
                        gr={"su": "W", "lean": "W", "star": None, "props": ["W", "U"]})
        self.assertTrue(AS.finished(g))
        g["gr"]["props"] = ["W", None]
        self.assertFalse(AS.finished(g))

    def test_totals_and_team_total_and_ml(self):
        g = ledger_game("G", "", "", "", lean={"mk": "total", "side": "U", "ln": 44.5})
        self.assertEqual(AS.grade_game(g, 20, 24)[1], "W")
        g = ledger_game("G", "", "", "", lean={"mk": "tt", "side": "O", "team": "A", "ln": 20.5})
        self.assertEqual(AS.grade_game(g, 21, 3)[1], "W")
        g = ledger_game("G", "", "", "", lean={"mk": "ml", "side": "A"})
        self.assertEqual(AS.grade_game(g, 20, 20)[1], "P")       # NFL tie: two-way moneyline is refunded
        g = ledger_game("G", "", "", "", lean={"mk": "spread", "side": "A", "ln": 3.5})
        self.assertEqual(AS.grade_game(g, 20, 23)[1], "W")       # +3.5 dog lost by 3
        # missing favorite: no straight-up grade instead of guessing a team
        g = ledger_game("G", "", "", "", line={"sp": 3, "tot": 40})
        self.assertIsNone(AS.grade_game(g, 10, 20)[0])


class Pregame(unittest.TestCase):
    def test_quotes_and_append_only_history(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            e = espn("E3", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre",
                     quotes={"DraftKings": DK}, qopen={"DraftKings": DK}, qclose={"DraftKings": DK})
            R.feed([e])
            g = ledger_game("G3", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E3")
            R.doc("2026-11-07", "NFL", [g])
            man, files, _ = R.go()
            pg = files[("pregame", "2026-11-07")]
            self.assertEqual(pg["feedCheckedAt"], "2026-11-04T16:55:00+00:00")
            self.assertEqual(pg["games"]["G3"]["quotes"]["DraftKings"]["spread"]["H"], [-3.5, -110])
            self.assertEqual(len(pg["snaps"]["G3"]), 1)
            self.assertEqual([m["op"] for m in man if m["collection"] == "pregame"], ["set"])
            # same pick again: nothing new written; then a changed lean appends a second entry
            R.pregame("2026-11-07", pg)
            man, files, _ = R.go("2026-11-04T18:00:00+00:00")
            self.assertNotIn(("pregame", "2026-11-07"), files)
            g["lean"] = {"mk": "spread", "side": "A", "ln": 3, "conf": 1}
            R.doc("2026-11-07", "NFL", [g])
            man, files, _ = R.go("2026-11-04T19:00:00+00:00")
            snaps = files[("pregame", "2026-11-07")]["snaps"]["G3"]
            self.assertEqual(len(snaps), 2)
            self.assertEqual(snaps[0], pg["snaps"]["G3"][0])      # earlier entry untouched
            self.assertEqual([m["op"] for m in man if m["collection"] == "pregame"], ["update"])

    def test_pregame_only_with_flag(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t, extra=())
            e = espn("E3", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre", quotes={"DraftKings": DK})
            R.feed([e])
            R.doc("2026-11-07", "NFL", [ledger_game("G3", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E3")])
            man, files, _ = R.go()
            self.assertFalse(any(m["collection"] == "pregame" for m in man))

    def test_swapped_listing_flips_quotes(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            # ESPN lists the game the other way round
            e = espn("E4", "2026-11-08T18:00Z", ("Kansas City", "Chiefs", "KC"), ("Denver", "Broncos", "DEN"), state="pre",
                     quotes={"DraftKings": DK})
            R.feed([e])
            g = ledger_game("G4", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E4", eswap=True)
            R.doc("2026-11-07", "NFL", [g])
            _, files, _ = R.go()
            q = files[("pregame", "2026-11-07")]["games"]["G4"]["quotes"]["DraftKings"]
            self.assertEqual(q["ml"], {"H": 150, "A": -170})
            self.assertEqual(q["spread"]["A"], [-3.5, -110])
            self.assertEqual(q["total"], DK["total"])


if __name__ == "__main__":
    unittest.main()
