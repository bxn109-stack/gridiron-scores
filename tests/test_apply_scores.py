"""Tests for apply_scores.py: archive grading, pick locks, voids, pushes and the pregame record."""
import json
import math
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

    def model(self, obj):
        dump(obj, os.path.join(self.tmp, "model.json"))

    def players(self, obj):
        dump(obj, os.path.join(self.tmp, "players.json"))

    def shadow(self, obj):
        dump(obj, os.path.join(self.tmp, "shadow.json"))

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
                            "--final", os.path.join(self.tmp, "final"), "--now", now,
                            "--model", os.path.join(self.tmp, "model.json"), "--players", os.path.join(self.tmp, "players.json"),
                            "--shadow", os.path.join(self.tmp, "shadow.json")] + self.extra,
                           capture_output=True, text=True)
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
            self.assertTrue(ch["lock"]["late"])                 # taken 10 days after kickoff: marked as a back-fill
            self.assertIn("back-filled", ch["lock"]["src"])
            res = files[("results", "2026-10-24")]["games"]["NFL-broncos-chiefs"]
            self.assertEqual(res["props"], {"W": 1, "L": 0, "P": 0, "U": 1})
            self.assertTrue(res["locked"])
            self.assertIn("season archive", err)

    def test_moneyline_lean_counted_at_its_price(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            old = espn("E7", "2026-10-25T17:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(27, 24),
                       lines=((7, 7, 6, 7), (3, 7, 7, 7)))
            R.final("2026-10-25", [old])
            R.feed([])
            g = ledger_game("NFL-broncos-chiefs", "2026-10-25T13:00:00-04:00", "Broncos", "Chiefs",
                            lean={"mk": "ml", "side": "A", "ln": 145, "conf": 1, "why": "Broncos projected to win outright."})
            R.doc("2026-10-24", "NFL", [g])
            man, files, err = R.go()
            res = files[("results", "2026-10-24")]["games"]["NFL-broncos-chiefs"]
            self.assertEqual((res["lean"], res["mk"], res["am"]), ("W", "ml", 145))   # Broncos won outright at +145

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

    def test_favorite_line_signed_or_as_points(self):
        # the runbook stores a favorite's line signed (-3.5); older picks stored points (3.5): both grade the same
        for ln in (-3.5, 3.5):
            g = {"lg": "NFL", "line": {"fav": "H", "sp": 3.5}, "lean": {"mk": "spread", "side": "H", "ln": ln}}
            self.assertEqual(AS.grade_game(g, 20, 23)[1], "L", ln)    # favorite won by 3, laid 3.5
            self.assertEqual(AS.grade_game(g, 20, 24)[1], "W", ln)
            self.assertEqual(AS.clv_of(g, {"hm": 3.5, "book": "DraftKings"}, False)["pts"], 0.0)
            self.assertEqual(AS.clv_of(g, {"hm": 4.5, "book": "DraftKings"}, False)["pts"], 1.0)   # closed -4.5: got a point


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


def fake_model():
    n0 = -12
    p = [0.01] * 33
    p[3 - n0], p[4 - n0], p[7 - n0] = 0.10, 0.05, 0.06
    rest = 1 - sum(p)
    row = {"n0": n0, "p": p, "lo": rest / 2, "hi": rest / 2, "w": 100}
    t0 = 45 - 16
    tp = [0.02] * 33
    tp[44 - t0] = 0.05
    trest = 1 - sum(tp)
    trow = {"n0": t0, "p": tp, "lo": trest / 2, "hi": trest / 2, "w": 100}
    return {"updatedAt": "2026-11-04T09:17Z", "leagues": {"NFL": {
        "ratings": {"DEN": {"r": -1.0, "o": 0.5, "d": 0.2}, "KC": {"r": 2.0, "o": 1.0, "d": -0.5}},
        "state": {"lg": 22.0, "hfa": 2.0, "asof": "2026-11-02T20:15"},
        "push": {"margin": {"3.5": row}, "total": {"44.5": trow}},
        "spread": {"pass": False, "why": "no reliable gain", "a": 0.0, "b": 0.0, "scale": 3.0, "thr": 2, "ats": [684, 691]},
        "total": {"pass": False, "why": "no reliable gain"}, "ml": {"pass": False, "w": [0.0, 1.0, 0.0]},
        "mae": {"model": 10.2, "close": 9.8, "n": 2761}, "live": {"ats": [24, 29], "thr": 2}}}}


def fake_players():
    return {"updatedAt": "2026-11-04T09:20Z", "throughWeek": 8, "sd": {"rec_yds": [12.0, 0.4]}, "coverage": {},
            "players": {"courtlandsutton": [{"name": "Courtland Sutton", "team": "DEN", "m": {"rec_yds": 70.0, "td": 0.5}}]}}


class ModelLayer(unittest.TestCase):
    """Phases 2-4 data path: push tables, the model's numbers and prop projections reach pregame, the lock and results."""

    def test_pregame_lock_and_results(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            R.model(fake_model()); R.players(fake_players())
            close = {"hm": 3.5, "tot": 44.5, "book": "DraftKings"}
            pre = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre",
                       quotes={"DraftKings": DK}, qopen={"DraftKings": DK})
            R.feed([pre])
            prop = {"pl": "Courtland Sutton", "tm": "DEN", "mk": "Rec yds", "ln": 50.5, "side": "O", "odds": "-115"}
            g = ledger_game("G9", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E9", props=[prop])
            R.doc("2026-11-07", "NFL", [g])
            _, files, _ = R.go()
            pg = files[("pregame", "2026-11-07")]
            ent = pg["games"]["G9"]
            self.assertEqual(ent["pmf"]["m"]["n0"], -12)                     # the 3.5 row (home -3.5)
            self.assertEqual(ent["pmf"]["t"]["n0"], 29)
            self.assertEqual(ent["lm"]["m"], 5.0)                             # 2 - (-1) + 2 home field
            self.assertAlmostEqual(ent["lm"]["t"], 45.8, places=6)
            self.assertAlmostEqual(ent["pp"][0]["p"], 0.687, places=2)        # 70 projected vs 50.5, sd 40
            self.assertFalse(pg["gates"]["leagues"]["NFL"]["spread"]["pass"])
            self.assertEqual(pg["coverage"], {"tracked": 1, "priced": 1})
            R.pregame("2026-11-07", pg)
            # kickoff: the lock keeps what the model said before the game
            live = dict(pre, state="in", status="STATUS_IN_PROGRESS", close=close, qclose={"DraftKings": DK})
            live["away"] = dict(pre["away"], score=7); live["home"] = dict(pre["home"], score=10)
            R.feed([live])
            _, files, _ = R.go("2026-11-08T18:30:00+00:00")
            ch = files[("slates/2026-11-07/leagues", "NFL")]["games"]["G9"]
            self.assertEqual(ch["lock"]["lm"]["m"], 5.0)
            self.assertAlmostEqual(ch["lock"]["pp"][0], 0.687, places=2)
            self.assertTrue(0.5 < ch["clv"]["p"] < 0.6)                       # -3 is a better number than the -3.5 close
            self.assertAlmostEqual(ch["clv"]["push"], 0.10, places=2)
            g.update(ch)
            R.doc("2026-11-07", "NFL", [g])
            # final: home wins by 7, total 47; Sutton 60 yards
            fin = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(20, 27),
                       lines=((7, 3, 7, 3), (7, 10, 3, 7)), close=close, qclose={"DraftKings": DK})
            R.feed([fin]); R.box("E9", [{"n": "Courtland Sutton", "tm": "DEN", "rec": 5, "recYds": 60}])
            _, files, _ = R.go("2026-11-08T22:00:00+00:00")
            res = files[("results", "2026-11-07")]["games"]["G9"]
            self.assertEqual(res["lean"], "W")
            self.assertTrue(0.5 < res["cp"] < 0.6)
            self.assertEqual(res["lme"], [1.5, "W", 1.3, "W"])
            self.assertEqual(len(res["pp"]), 1)
            self.assertEqual(res["pp"][0][1], 1)
            self.assertAlmostEqual(res["pp"][0][2], (115 / 215) / 1.045, places=3)

    def test_no_model_files_no_model_fields(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            R.feed([espn("E3", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre", quotes={"DraftKings": DK})])
            R.doc("2026-11-07", "NFL", [ledger_game("G3", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E3")])
            _, files, _ = R.go()
            ent = files[("pregame", "2026-11-07")]["games"]["G3"]
            for k in ("pmf", "lm", "pp"):
                self.assertNotIn(k, ent)

    def test_prop_projection_sides_and_markets(self):
        P = fake_players()
        over = AS.prop_projection(P, {"pl": "Courtland Sutton", "tm": "DEN", "mk": "Rec yds", "ln": 50.5, "side": "O"})
        under = AS.prop_projection(P, {"pl": "Courtland Sutton", "tm": "DEN", "mk": "Rec yds", "ln": 50.5, "side": "U"})
        self.assertAlmostEqual(over["p"] + under["p"], 1, places=3)
        td = AS.prop_projection(P, {"pl": "Courtland Sutton", "mk": "Anytime TD", "side": "Y"})
        self.assertAlmostEqual(td["p"], 1 - math.exp(-0.5), places=3)
        self.assertIsNone(AS.prop_projection(P, {"pl": "Courtland Sutton", "mk": "Longest rec", "ln": 20.5, "side": "O"}))
        self.assertIsNone(AS.prop_projection(P, {"pl": "Courtland Sutton", "mk": "Rec yds (alt 70+)", "ln": 69.5, "side": "O"}))
        self.assertIsNone(AS.prop_projection(P, {"pl": "Nobody Here", "mk": "Rec yds", "ln": 50.5, "side": "O"}))

    def test_model_result_against_the_close(self):
        self.assertEqual(AS.model_result({"m": 5.0, "t": 40.0}, 3.5, 44.5, {"a": 20, "h": 23}), [1.5, "L", -4.5, "W"])
        self.assertEqual(AS.model_result({"m": 3.5}, 3.5, None, {"a": 0, "h": 7})[:2], [0.0, None])



class VerifiedLocks(unittest.TestCase):
    """Assignment 4: a pick gets pregame credit only from a copy saved before kickoff; model predictions likewise."""

    def snap(self, g, at, prev=None):
        body = {k: AS.pick_snapshot(g)[k] for k in AS.SNAP_KEYS}
        return dict(body, at=at, h=AS.chain(prev, body, at))

    def started(self, R):
        e = espn("E7", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="in",
                 status="STATUS_IN_PROGRESS")
        e["away"]["score"], e["home"]["score"] = 0, 7
        R.feed([e])

    def test_lock_is_the_last_pregame_copy(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            g = ledger_game("G7", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E7")
            s1 = self.snap(g, "2026-11-08T14:00:00+00:00")
            R.pregame("2026-11-07", {"slate": "2026-11-07", "games": {}, "snaps": {"G7": [s1]}})
            g["lean"] = {"mk": "spread", "side": "A", "ln": 3, "conf": 1}           # changed later, never saved before kickoff
            R.doc("2026-11-07", "NFL", [g]); self.started(R)
            _, files, err = R.go("2026-11-08T18:20:00+00:00")
            lock = files[("slates/2026-11-07/leagues", "NFL")]["games"]["G7"]["lock"]
            self.assertTrue(lock["v"]); self.assertTrue(lock["chain_ok"])
            self.assertEqual(lock["lean"]["side"], "H")                             # the pregame copy, not the later edit
            self.assertEqual(lock["pre"], "2026-11-08T14:00:00+00:00")
            self.assertIn("lean", files[("slates/2026-11-07/leagues", "NFL")]["games"]["G7"]["lockdiff"])

    def test_no_pregame_copy_means_unverified(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            g = ledger_game("G7", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E7")
            R.doc("2026-11-07", "NFL", [g]); self.started(R)
            _, files, _ = R.go("2026-11-08T18:20:00+00:00")
            lock = files[("slates/2026-11-07/leagues", "NFL")]["games"]["G7"]["lock"]
            self.assertFalse(lock["v"])
            self.assertIn("no copy saved before kickoff", lock["src"])

    def test_predictions_after_kickoff_get_no_credit(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t)
            g = ledger_game("G7", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E7")
            late = {"model": "ledger-v1", "version": "v", "asof": "2026-11-02T20:15", "m": 4.0, "t": 44.0, "pp": None, "at": "2026-11-08T18:05:00+00:00"}
            leaky = dict(late, at="2026-11-08T12:00:00+00:00", asof="2026-11-08T19:00:00Z")   # made before kickoff from later data
            R.pregame("2026-11-07", {"slate": "2026-11-07", "games": {}, "snaps": {}, "preds": {"G7": [leaky, late]}})
            R.doc("2026-11-07", "NFL", [g]); self.started(R)
            _, files, _ = R.go("2026-11-08T18:20:00+00:00")
            self.assertNotIn("lm", files[("slates/2026-11-07/leagues", "NFL")]["games"]["G7"]["lock"])

    def test_hash_chain_detects_edits(self):
        g = ledger_game("G7", "", "Broncos", "Chiefs")
        a = self.snap(g, "2026-11-06T11:00:00+00:00")
        g["lean"] = {"mk": "spread", "side": "A", "ln": 3, "conf": 1}
        b = self.snap(g, "2026-11-07T15:00:00+00:00", a["h"])
        self.assertTrue(AS.verify_chain([a, b], AS.SNAP_KEYS))
        tampered = dict(a, lean={"mk": "spread", "side": "A", "ln": 3, "conf": 3})
        self.assertFalse(AS.verify_chain([tampered, b], AS.SNAP_KEYS))
        self.assertFalse(AS.verify_chain([b], AS.SNAP_KEYS))                       # a removed record breaks the chain

    def test_prediction_records_are_appended_not_replaced(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t); R.model(fake_model())
            pre = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre", quotes={"DraftKings": DK})
            R.feed([pre])
            R.doc("2026-11-07", "NFL", [ledger_game("G9", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E9")])
            _, files, _ = R.go()
            pg = files[("pregame", "2026-11-07")]
            self.assertEqual(len(pg["preds"]["G9"]), 1)
            first = pg["preds"]["G9"][0]
            self.assertEqual(first["model"], "ledger-v1"); self.assertEqual(first["version"], "2026-11-04T09:17Z")
            R.pregame("2026-11-07", pg)
            m = fake_model(); m["updatedAt"] = "2026-11-06T09:17Z"; m["leagues"]["NFL"]["ratings"]["KC"]["r"] = 3.0
            R.model(m)
            _, files, _ = R.go("2026-11-06T10:00:00+00:00")
            preds = files[("pregame", "2026-11-07")]["preds"]["G9"]
            self.assertEqual(len(preds), 2)
            self.assertEqual(preds[0], first)                                       # the earlier record is untouched
            self.assertTrue(AS.verify_chain(preds, AS.PRED_KEYS))


def fake_shadow(version="2026-11-04T10:00:00Z", kc_qb=0.15):
    nxt = lambda side, opp, qb: {"date": "2026-11-08", "week": 10, "side": side, "opp": opp, "qb": qb, "qb_chg": 0.0, "qb_name": "QB",
                                  "rest": 7.0, "dome": 0, "wind": 8.0, "temp": 60.0, "post": 0}
    return {"model": "lab-1-C", "name": "C · Efficiency + quarterback", "version": "lab-1", "updatedAt": version, "asof": "2026-11-03T00:15:00-05:00",
            "pairs": [["o_epa", "d_epa"]], "fill": {"wind": 8.0, "temp": 60.0},
            "margin": {"features": ["m_o_epa", "hfa", "qb"], "mean": [0, 0, 0], "scale": [1, 1, 1], "coef": [10, 2, 5], "intercept": 0.0},
            "total": {"features": ["t_o_epa", "pace"], "mean": [0, 120], "scale": [1, 1], "coef": [20, 0.1], "intercept": 44.0},
            "test": {"qualifies": False},
            "teams": {"KC": {"snap": {"o_epa": 0.1, "d_epa": -0.05, "o_pace": 64}, "qb_last": 0.15, "dome_home": 0, "next": nxt("home", "DEN", kc_qb)},
                      "DEN": {"snap": {"o_epa": 0.0, "d_epa": 0.02, "o_pace": 62}, "qb_last": 0.05, "dome_home": 0, "next": nxt("away", "KC", 0.05)}}}


class ShadowModel(unittest.TestCase):
    """Assignment 6: the lab's candidate is recorded before kickoff for evaluation only, beside the existing model."""

    def test_numbers_from_published_team_values(self):
        e = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre")
        ko = AS.when("2026-11-08T13:00:00-05:00")
        x = AS.shadow_numbers(fake_shadow(), "NFL", e, False, ko)
        self.assertEqual(x, {"m": 4.2, "t": 46.0})                              # 10*0.17 + 2 + 5*0.10; 44 + 20*0.07 + 0.1*6
        self.assertEqual(AS.shadow_numbers(fake_shadow(), "NFL", e, True, ko)["m"], -4.2)
        # not the team's next listed game: the last starter's value and no listed rest
        S = fake_shadow(kc_qb=0.0)
        self.assertEqual(AS.shadow_numbers(S, "NFL", e, False, AS.when("2026-11-15T13:00:00-05:00"))["m"], 4.2)
        self.assertEqual(AS.shadow_numbers(S, "NFL", e, False, ko)["m"], 3.5)       # 1.7 + 2 + 5*(0.0-0.05), to 0.1
        self.assertIsNone(AS.shadow_numbers(S, "FBS", e, False, ko))
        S["margin"]["features"].append("unknown_feature"); S["margin"]["mean"].append(0); S["margin"]["scale"].append(1); S["margin"]["coef"].append(1)
        self.assertIsNone(AS.shadow_numbers(S, "NFL", e, False, ko))

    def test_recorded_before_kickoff_kept_apart_and_graded(self):
        with tempfile.TemporaryDirectory() as t:
            R = Run(t); R.model(fake_model()); R.players(fake_players()); R.shadow(fake_shadow())
            close = {"hm": 3.5, "tot": 44.5, "book": "DraftKings"}
            pre = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), state="pre", quotes={"DraftKings": DK})
            R.feed([pre])
            g = ledger_game("G9", "2026-11-08T13:00:00-05:00", "Broncos", "Chiefs", eid="E9")
            R.doc("2026-11-07", "NFL", [g])
            _, files, _ = R.go()
            pg = files[("pregame", "2026-11-07")]
            preds = pg["preds"]["G9"]
            self.assertEqual([p["model"] for p in preds], ["ledger-v1", "lab-1-C"])
            self.assertEqual((preds[1]["m"], preds[1]["t"], preds[1]["asof"]), (4.2, 46.0, "2026-11-03T00:15:00-05:00"))
            self.assertTrue(AS.verify_chain(preds, AS.PRED_KEYS))
            self.assertNotIn("sm", pg["games"]["G9"])                               # never shown with the game
            self.assertEqual(pg["gates"]["shadow"]["model"], "lab-1-C")
            R.pregame("2026-11-07", pg)
            _, files, _ = R.go("2026-11-05T17:00:00+00:00")                         # nothing changed: nothing appended
            self.assertNotIn("preds", files.get(("pregame", "2026-11-07"), {}))
            R.shadow(fake_shadow("2026-11-06T10:00:00Z", kc_qb=0.0))                 # new numbers: only the shadow record is added
            _, files, _ = R.go("2026-11-06T17:00:00+00:00")
            preds = files[("pregame", "2026-11-07")]["preds"]["G9"]
            self.assertEqual([p["model"] for p in preds], ["ledger-v1", "lab-1-C", "lab-1-C"])
            self.assertEqual(preds[2]["m"], 3.5)
            self.assertTrue(AS.verify_chain(preds, AS.PRED_KEYS))
            pg = files[("pregame", "2026-11-07")]; R.pregame("2026-11-07", pg)
            live = dict(pre, state="in", status="STATUS_IN_PROGRESS", close=close, qclose={"DraftKings": DK})
            live["away"] = dict(pre["away"], score=7); live["home"] = dict(pre["home"], score=10)
            R.feed([live])
            _, files, _ = R.go("2026-11-08T18:30:00+00:00")
            ch = files[("slates/2026-11-07/leagues", "NFL")]["games"]["G9"]
            self.assertEqual(ch["lock"]["lm"]["model"], "ledger-v1")
            self.assertEqual((ch["lock"]["sm"]["model"], ch["lock"]["sm"]["m"]), ("lab-1-C", 3.5))
            g.update(ch); R.doc("2026-11-07", "NFL", [g])
            fin = espn("E9", "2026-11-08T18:00Z", ("Denver", "Broncos", "DEN"), ("Kansas City", "Chiefs", "KC"), sc=(20, 27),
                       lines=((7, 3, 7, 3), (7, 10, 3, 7)), close=close, qclose={"DraftKings": DK})
            R.feed([fin])
            _, files, _ = R.go("2026-11-08T22:00:00+00:00")
            res = files[("results", "2026-11-07")]["games"]["G9"]
            self.assertEqual(res["sme"], [0.0, None, 1.5, "W"])                      # same as the close; 46 vs 44.5, Over won (47)
            self.assertEqual(res["smv"], "lab-1-C")
            self.assertEqual(res["lme"], [1.5, "W", 1.3, "W"])                       # the existing model is unaffected


if __name__ == "__main__":
    unittest.main()
