"""Tests for the basketball board build and grader (bb_ledger.py)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bb_ledger as B  # noqa: E402


def rj(path):
    with open(path) as f:
        return json.load(f)


def wj(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def team(i, short, rank=None, score=None, lines=None):
    return {"id": i, "loc": short, "name": short, "disp": short, "short": short, "abbr": short[:4].upper(),
            "score": score, "lines": lines or [], "rank": rank, "rec": "2-0"}


def fgame(eid, lg, date, away, home, hl=None, tot=None, hp=None, state="pre", sa=None, sh=None, lines=(None, None)):
    q = {}
    if hl is not None:
        q = {"DraftKings": {"book": "DraftKings", "src": "espn", "ts": "2026-11-10T15:00:00+00:00",
                            "spread": {"A": [-hl, -110], "H": [hl, -110]}, "total": {"O": [tot, -110], "U": [tot, -110]}}}
    g = {"id": eid, "lg": lg, "date": date, "state": state, "status": {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL"}[state],
         "completed": state == "post", "detail": "Final" if state == "post" else "2nd Half 8:00", "neutral": False,
         "away": team("1" + eid, away, score=sa, lines=lines[0]), "home": team("2" + eid, home, rank=5, score=sh, lines=lines[1]),
         "close": {"hm": -hl if hl is not None else None, "tot": tot, "book": "DraftKings"}}
    if state == "pre" and q:
        g["quotes"], g["qopen"], g["qclose"] = q, q, q
    elif q:
        g["qclose"] = q
    if hp is not None:
        g["pred"] = {"hp": hp, "src": "ESPN BPI", "at": "2026-11-10T15:00:00+00:00"}
    return g


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = os.path.join(self.tmp.name, "feed")
        self.docs = os.path.join(self.tmp.name, "docs")
        os.makedirs(self.feed)
        os.makedirs(self.docs)

    def tearDown(self):
        self.tmp.cleanup()

    def put_feed(self, date, games):
        wj({"date": date, "games": {g["id"]: g for g in games}}, os.path.join(self.feed, f"{date}.json"))
        wj({"checkedAt": "2026-11-10T15:00:00+00:00"}, os.path.join(self.feed, "status.json"))

    def run_ledger(self, now, slate="2026-11-10", extra=()):
        out = os.path.join(self.tmp.name, "out", now.replace(":", ""))
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            B.main(["--docs", self.docs, "--out", out, "--feed", self.feed, "--now", now] + (["--slate", slate] if slate else []) + list(extra))
        return json.loads(buf.getvalue())

    def apply(self, manifest):
        """What ArtifactData would do: set replaces, update merges the games map one level deep."""
        for w in manifest:
            path = os.path.join(self.docs, w["collection"], w["doc_id"] + ".json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            body = rj(w["file_path"])
            if w["op"] == "update" and os.path.exists(path):
                cur = rj(path)
                for k, v in body.items():
                    if isinstance(v, dict) and isinstance(cur.get(k), dict):
                        for gk, gv in v.items():
                            cur[k][gk] = dict(cur[k].get(gk) or {}, **gv) if isinstance(gv, dict) and isinstance(cur[k].get(gk), dict) else gv
                    else:
                        cur[k] = v
                body = cur
            wj(body, path)

    def doc(self, *parts):
        return rj(os.path.join(self.docs, *parts) + ".json")

    def test_lean_method(self):
        g = {"lg": "MBB", "a": {"n": "Army"}, "h": {"n": "Duke"}, "line": {"fav": "H", "sp": 20.0},
             "model": [{"src": "x", "fav": "H", "m": 26.0}], "experts": []}
        l = B.lean_for(g)
        self.assertEqual((l["side"], l["ln"], l["conf"]), ("H", -20.0, 1))      # projection beats the line: favorite
        g["model"] = [{"src": "x", "fav": "H", "m": 14.0}]
        l = B.lean_for(g)
        self.assertEqual((l["side"], l["ln"]), ("A", 20.0))                     # dog gets the points
        # conf 2 needs two projections, |score| >= 1.25 and experts agreeing
        g["model"] = [{"src": "x", "fav": "H", "m": 26.0}, {"src": "y", "fav": "H", "m": 25.0}]
        g["experts"] = [{"src": "e", "side": "H"}]
        self.assertEqual(B.lean_for(g)["conf"], 2)
        g["experts"] = [{"src": "e", "side": "A"}, {"src": "f", "side": "A"}]
        self.assertEqual(B.lean_for(g)["conf"], 1)                              # projections and experts disagree
        g["model"] = []
        self.assertIsNone(B.lean_for(g))                                        # no projection, no lean

    def test_build_lock_grade_cycle(self):
        tip = "2026-11-11T00:00Z"                                               # 7 PM ET Nov 10
        pre = [fgame("9", "MBB", tip, "Army", "Duke", hl=-24.5, tot=141.5, hp=98.0),
               fgame("8", "MBB", tip, "Rice", "Penn", hl=None, hp=60.0),          # no line: skipped
               fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, hp=53.5)]
        self.put_feed("2026-11-10", pre)
        m = self.run_ledger("2026-11-10T16:00:00Z")
        self.apply(m)
        mbb = self.doc("bbslates/2026-11-10/leagues", "MBB")
        self.assertEqual(list(mbb["games"]), ["MBB-army-duke"])
        g = mbb["games"]["MBB-army-duke"]
        self.assertEqual(g["line"], {"fav": "H", "sp": 24.5, "tot": 141.5, "src": "DraftKings via ESPN"})
        self.assertEqual(g["model"][0]["src"], "ESPN BPI")
        self.assertTrue(g["tracked"])
        self.assertEqual(g["ko"], "2026-11-10T19:00:00-05:00")
        cfg = self.doc("meta", "bbconfig")
        self.assertEqual(cfg["current"], "2026-11-10")
        self.assertEqual(cfg["slates"][0]["label"], "Tue Nov 10")
        pg = self.doc("bbpregame", "2026-11-10")
        self.assertEqual(len(pg["snaps"]["MBB-army-duke"]), 1)
        self.assertNotIn("why", json.dumps(pg["snaps"]))                       # compact pick history
        first = g["lean"]

        # line moves before tip: lean recomputed, marked Updated, pick history grows
        pre[0] = fgame("9", "MBB", tip, "Army", "Duke", hl=-30.5, tot=141.5, hp=98.0)
        self.put_feed("2026-11-10", pre)
        self.apply(self.run_ledger("2026-11-10T22:00:00Z"))
        g = self.doc("bbslates/2026-11-10/leagues", "MBB")["games"]["MBB-army-duke"]
        self.assertEqual(g["line"]["sp"], 30.5)
        if g["lean"]["ln"] != first["ln"]:
            self.assertTrue(g["lean"]["why"].startswith("Updated "))
        self.assertEqual(len(self.doc("bbpregame", "2026-11-10")["snaps"]["MBB-army-duke"]), 2)
        locked_lean = g["lean"]

        # inside 15 minutes of tip a build changes nothing
        pre[0] = fgame("9", "MBB", tip, "Army", "Duke", hl=-10.5, tot=141.5, hp=98.0)
        self.put_feed("2026-11-10", pre)
        self.apply(self.run_ledger("2026-11-10T23:50:00Z"))
        g = self.doc("bbslates/2026-11-10/leagues", "MBB")["games"]["MBB-army-duke"]
        self.assertEqual(g["line"]["sp"], 30.5)

        # live, then final: locked from the last copy saved before tip and graded
        self.put_feed("2026-11-10", [fgame("9", "MBB", tip, "Army", "Duke", hl=-30.5, tot=141.5, state="in", sa=20, sh=40),
                                     fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="in", sa=50, sh=48)])
        self.apply(self.run_ledger("2026-11-11T00:40:00Z", slate=None))
        g = self.doc("bbslates/2026-11-10/leagues", "MBB")["games"]["MBB-army-duke"]
        self.assertEqual(g["st"], "live")
        self.assertTrue(g["lock"]["v"])
        self.assertEqual(g["lock"]["lean"]["ln"], locked_lean["ln"])
        self.put_feed("2026-11-10", [fgame("9", "MBB", tip, "Army", "Duke", hl=-30.5, tot=141.5, state="post", sa=55, sh=80, lines=([25, 30], [40, 40])),
                                     fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="post", sa=110, sh=112)])
        self.apply(self.run_ledger("2026-11-11T03:00:00Z", slate=None))
        g = self.doc("bbslates/2026-11-10/leagues", "MBB")["games"]["MBB-army-duke"]
        self.assertEqual((g["st"], g["sc"]), ("final", {"a": 55, "h": 80}))
        self.assertEqual(g["per"], {"a": [25, 30], "h": [40, 40]})
        side = locked_lean["side"]
        self.assertEqual(g["gr"]["lean"], "L" if side == "H" else "W")            # Duke won by 25 < 30.5
        self.assertEqual(g["gr"]["su"], "W")
        res = self.doc("bbresults", "2026-11-10")
        self.assertEqual(res["games"]["MBB-army-duke"]["lg"], "MBB")
        self.assertTrue(res["games"]["MBB-army-duke"]["v"])
        nba = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual(nba["gr"]["su"], "W")                                    # Hawks (favorite) won by 2
        self.assertEqual(nba["gr"]["lean"], "W" if nba["lock"]["lean"]["side"] == "H" else "L")
        live = self.doc("meta", "bblive")
        self.assertEqual(live["graded"], 2)
        # a further run changes nothing for finished games
        m = self.run_ledger("2026-11-11T04:00:00Z", slate=None)
        self.assertEqual([w["doc_id"] for w in m], ["bblive"])

    def test_research_context_adds_sources(self):
        tip = "2026-11-11T00:00Z"
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, hp=53.5)])
        ctx = os.path.join(self.tmp.name, "ctx.json")
        wj({"NBA-lakers-hawks": {"model": [{"src": "Dimers", "fav": "H", "m": 7.0}], "experts": [{"src": "Covers", "pick": "Hawks -1.5", "side": "H"}],
                                 "facts": ["Lakers on the second night of a back-to-back (Covers)"]}}, ctx)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--ctx", ctx]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual([x["src"] for x in g["model"]], ["ESPN BPI", "Dimers"])
        self.assertEqual(g["lean"]["side"], "H")
        self.assertEqual(g["facts"], ["Lakers on the second night of a back-to-back (Covers)"])
        # a later run without research keeps what was found
        self.apply(self.run_ledger("2026-11-10T22:00:00Z"))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual([x["src"] for x in g["model"]], ["ESPN BPI", "Dimers"])
        self.assertEqual(len(g["experts"]), 1)
        # an afternoon refresh adds a new source and updates one: the morning's other research is kept
        wj({"NBA-lakers-hawks": {"model": [{"src": "DRatings", "fav": "H", "m": 3.0, "t": 230.0}, {"src": "Dimers", "fav": "H", "m": 5.0}],
                                 "experts": [{"src": "SI", "pick": "Hawks -1.5", "side": "H"}]}}, ctx)
        self.apply(self.run_ledger("2026-11-10T20:45:00Z", extra=["--ctx", ctx]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual([(x["src"], x.get("m")) for x in g["model"]], [("ESPN BPI", g["model"][0]["m"]), ("DRatings", 3.0), ("Dimers", 5.0)])
        self.assertEqual([x["src"] for x in g["experts"]], ["Covers", "SI"])
        self.assertEqual(g["facts"], ["Lakers on the second night of a back-to-back (Covers)"])

    def test_markets_and_stats(self):
        p = {"pts": 28, "reb": 9, "ast": 4, "stl": 2, "blk": 1, "to": 3, "tpm": 3, "fgm": 10, "ftm": 5, "oreb": 2, "fp": 47.8}
        for mk, v in [("Points", 28), ("Rebounds", 9), ("Assists", 4), ("Pts+Reb+Ast", 41), ("PRA", 41), ("Pts + Reb", 37),
                      ("Reb+Ast", 13), ("3-PT Made", 3), ("3-Pointers Made", 3), ("Threes", 3), ("Blks+Stls", 3), ("Steals", 2),
                      ("Turnovers", 3), ("Fantasy Score", 47.8), ("Double Double", 0), ("Offensive Rebounds", 2),
                      ("Free Throws Made", 5), ("Field Goals Made", 10)]:
            self.assertEqual(B.bb_stat(mk, p), v, mk)
        self.assertEqual(B.bb_stat("Double Double", dict(p, reb=10)), 1)
        self.assertIsNone(B.bb_stat("First Basket", p))
        self.assertIsNone(B.bb_stat("Points (alt)", p))

    def test_props_locked_and_graded_from_box(self):
        tip = "2026-11-11T00:00Z"
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, hp=53.5)])
        ctx = os.path.join(self.tmp.name, "ctx.json")
        props = [{"pl": "Trae Young", "tm": "ATL", "mk": "Points", "ln": 24.5, "side": "O", "odds": "-115", "src": "Covers", "why": "x"},
                 {"pl": "LeBron James", "tm": "LAL", "mk": "Pts+Reb+Ast", "ln": 38.5, "side": "U", "src": "Action", "why": "y"},
                 {"pl": "Nobody", "mk": "Rebounds", "side": "O"}]                       # no line: dropped
        wj({"NBA-lakers-hawks": {"props": props}}, ctx)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--ctx", ctx]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual([p["pl"] for p in g["props"]], ["Trae Young", "LeBron James"])
        # a later refresh moves one line and adds a prop: positions are kept, the new one is appended
        wj({"NBA-lakers-hawks": {"props": [dict(props[1], ln=37.5), {"pl": "Jalen Johnson", "tm": "ATL", "mk": "Rebounds", "ln": 8.5, "side": "O", "src": "SI", "why": "z"}]}}, ctx)
        self.apply(self.run_ledger("2026-11-10T22:00:00Z", extra=["--ctx", ctx]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual([(p["pl"], p["ln"]) for p in g["props"]], [("Trae Young", 24.5), ("LeBron James", 37.5), ("Jalen Johnson", 8.5)])
        snaps = self.doc("bbpregame", "2026-11-10")["snaps"]["NBA-lakers-hawks"]
        self.assertEqual(len(snaps[-1]["props"]), 3)
        # after tip a prop is added: shown, never graded (X)
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="in", sa=50, sh=48)])
        self.apply(self.run_ledger("2026-11-11T00:30:00Z", slate=None))
        late = os.path.join(self.docs, "bbslates/2026-11-10/leagues/NBA.json")
        d = rj(late)
        d["games"]["NBA-lakers-hawks"]["props"].append({"pl": "Late Add", "mk": "Points", "ln": 5.5, "side": "O"})
        wj(d, late)
        box = os.path.join(self.tmp.name, "box")
        os.makedirs(box)
        wj({"games": {"7": {"lg": "NBA", "players": [{"n": "Trae Young", "tm": "ATL", "pts": 31, "reb": 3, "ast": 11},
                                                      {"n": "LeBron James", "tm": "LAL", "pts": 20, "reb": 8, "ast": 9}]}}},
           os.path.join(box, "2026-11-10.json"))
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="post", sa=110, sh=112)])
        self.apply(self.run_ledger("2026-11-11T03:00:00Z", slate=None, extra=["--box", box]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual(g["pv"][:2], [31, 37])
        self.assertEqual(g["gr"]["props"][:2], ["W", "W"])                         # 31 > 24.5; 37 < 37.5
        self.assertIsNone(g["gr"]["props"][2])                                     # Jalen Johnson: waiting on the box score
        self.assertEqual(g["gr"]["props"][3], "X")
        # a day later he is still missing: unresolved
        self.apply(self.run_ledger("2026-11-12T08:00:00Z", slate=None, extra=["--box", box]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual(g["gr"]["props"], ["W", "W", "U", "X"])
        self.assertEqual(self.doc("bbresults", "2026-11-10")["games"]["NBA-lakers-hawks"]["props"], {"W": 2, "L": 0, "P": 0, "U": 1})

    def test_total_lean_and_star(self):
        tip = "2026-11-11T00:00Z"
        g = fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=226.5, hp=50.5)
        g["qopen"] = {"DraftKings": {"book": "DraftKings", "spread": {"A": [3.5, -110], "H": [-3.5, -110]}, "total": {"O": [222.5, -110], "U": [222.5, -110]}}}
        self.put_feed("2026-11-10", [g])
        ctx = os.path.join(self.tmp.name, "ctx.json")
        # spread case weak (BPI near a pick'em vs Hawks -1.5); totals: three projections well over and two Over picks
        wj({"NBA-lakers-hawks": {"model": [{"src": "DRatings", "t": 236.0}, {"src": "Dimers", "fav": "H", "m": 1.0, "t": 234.0}],
                                 "experts": [{"src": "Covers", "pick": "Over 226.5", "side": "O"}, {"src": "SI", "pick": "Over 226.5", "side": "O"}]}}, ctx)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--ctx", ctx]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual((g["lean"]["mk"], g["lean"]["side"], g["lean"]["ln"], g["lean"]["conf"]), ("total", "O", 226.5, 2))
        # the total rose 4 points since it opened, toward the Over: a market signal, so the lean is a star
        self.assertTrue(g["star"])
        self.assertIn("moved 4 toward the Over", g["starWhy"])

    def test_star_cap_per_day(self):
        tip = "2026-11-11T00:00Z"
        games, ctx = [], {}
        for i in range(5):
            g = fgame(str(i), "MBB", tip, f"Away{i}", f"Home{i}", hl=-5.5, tot=140.5, hp=60.0)
            games.append(g)
            ctx[f"MBB-away{i}-home{i}"] = {"model": [{"src": "T-Rank", "fav": "H", "m": 12.0 + i}, {"src": "KenPom", "fav": "H", "m": 11.0 + i}],
                                           "experts": [{"src": "Covers", "side": "H", "pick": f"Home{i} -5.5"}],
                                           "signal": "Reverse line movement toward the home side (Action Network)"}
        self.put_feed("2026-11-10", games)
        path = os.path.join(self.tmp.name, "ctx.json")
        wj(ctx, path)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--ctx", path]))
        gs = self.doc("bbslates/2026-11-10/leagues", "MBB")["games"]
        stars = sorted(gid for gid, g in gs.items() if g["star"])
        self.assertEqual(len(stars), B.MAX_STARS)
        self.assertEqual(stars, ["MBB-away2-home2", "MBB-away3-home3", "MBB-away4-home4"])   # the strongest cases
        self.assertTrue(all(g["lean"]["conf"] == 2 for g in gs.values()))

    def test_claude_model_card_built_and_graded(self):
        tip = "2026-11-11T00:00Z"
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, hp=53.5)])
        cm = os.path.join(self.tmp.name, "bb_cm.json")
        trae = {"g": 10, "min": 35, "pts": 26.0, "r5_pts": 28.0, "sd_pts": 6.0, "reb": 3.0, "ast": 11.0, "dd": 0.4, "last": "2026-11-08"}
        wj({"updatedAt": "2026-11-10T12:00Z", "version": "bbcm-1", "leagues": {"NBA": {
            "params": {"hca": 2.5, "b2b": -1.5}, "lg_eff": 114.0, "lg_pace": 99.0, "sigma_m": 12.5,
            "gate": {"pass": False, "why": "no reliable gain", "a_fit": 0.0, "b_fit": 0.2, "scale": 4.0, "t": 0.4},
            "gate_total": {"pass": False, "a_fit": 0.0, "b_fit": 0.1, "scale": 5.0},
            "backtest": {"seasons": "2025–2026", "games": 2400, "ats": 50.9, "atsN": 2300},
            "teams": {"27": {"n": "Hawks", "o": 3.0, "d": 1.0, "p": 1.0, "last": "2026-11-09"},  # US Eastern dates
                      "17": {"n": "Lakers", "o": 1.0, "d": -1.0, "p": 0.5, "last": "2026-11-08"}},
            "players": {"HAWK": {"Trae Young": trae}, "LAKE": {"LeBron James": {"g": 10, "min": 34, "pts": 24.0, "reb": 8.0, "ast": 8.0}}}}}}, cm)
        ctx = os.path.join(self.tmp.name, "ctx.json")
        wj({"NBA-lakers-hawks": {"props": [{"pl": "Trae Young", "tm": "HAWK", "mk": "Points", "ln": 24.5, "side": "O", "src": "Covers", "why": "x"}],
                                 "out": ["LeBron James"]}}, ctx)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--ctx", ctx, "--cm", cm]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        c = g["cm"]
        # Hawks (home): 114+3-(-1)=118 vs Lakers 114+1-1=114 at 100.5 possessions: 4.02 + 2.5 home court
        # + LeBron out: 0.15*(24+5.6+2.4)*1 = 4.8 off the Lakers' side; the Hawks played yesterday: -1.5
        self.assertAlmostEqual(c["hm"], round(4.02 + 2.5 - 1.5 + 4.8, 1), places=1)
        self.assertEqual(c["picks"]["spread"]["side"], "H")
        self.assertEqual(c["picks"]["spread"]["ln"], -1.5)
        self.assertEqual(c["props"][0]["side"], "O")                          # 0.6*26 + 0.4*28 = 26.8 > 24.5
        self.assertIn("LeBron James out", c["why"])
        self.assertIn("Not validated", c["why"])
        self.assertEqual(c["conf"], 1)
        self.assertEqual(self.doc("meta", "bbcmmodel")["gate"]["NBA"]["pass"], False)
        self.assertEqual(len(self.doc("meta", "bbcmvars")["vars"]), len(B.CM_VARS))
        rows = self.doc("bbcmvars/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]["rows"]
        self.assertEqual({r[0] for r in rows}, {v[0] for v in B.CM_VARS})
        # final: graded from the score and the box score
        box = os.path.join(self.tmp.name, "box")
        os.makedirs(box)
        wj({"games": {"7": {"lg": "NBA", "players": [{"n": "Trae Young", "tm": "HAWK", "pts": 30}]}}}, os.path.join(box, "2026-11-10.json"))
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="post", sa=100, sh=110)])
        self.apply(self.run_ledger("2026-11-11T03:00:00Z", slate=None, extra=["--box", box, "--cm", cm]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual(g["cmgr"]["spread"], "W")                            # Hawks -1.5 won by 10
        self.assertEqual(g["cmgr"]["ml"], "W")
        self.assertEqual(g["cmgr"]["props"], ["W"])
        self.assertEqual(g["cmpv"], [30])
        row = self.doc("bbcmresults", "2026-11-10")["games"]["NBA-lakers-hawks"]
        self.assertEqual((row["spread"], row["props"]["W"]), ("W", 1))
        self.assertEqual(row["mae"], round(abs(10 - g["cm"]["hm"]), 1))

    def test_ledger_model_numbers_and_gates(self):
        tip = "2026-11-11T00:00Z"
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, hp=53.5)])
        model = os.path.join(self.tmp.name, "bb_model.json")
        wj({"updatedAt": "2026-11-10T12:00:00+00:00", "leagues": {"NBA": {
            "ratings": {"17": {"r": 2.0, "o": 1.0, "d": -1.0}, "27": {"r": -1.0, "o": 0.5, "d": 0.5}},
            "state": {"hfa": 2.5, "lg": 115.0, "asof": "2026-11-09"},
            "push": {"margin": {"1.5": {"n0": -3, "p": [0.04, 0.05, 0.05, 0.05], "lo": 0.4, "hi": 0.41}}},
            "spread": {"pass": False, "t": 0.5}, "total": {"pass": False}, "ml": {"pass": False}}}}, model)
        self.apply(self.run_ledger("2026-11-10T16:00:00Z", extra=["--model", model]))
        pg = self.doc("bbpregame", "2026-11-10")
        ent = pg["games"]["NBA-lakers-hawks"]
        # home Hawks id "27", away Lakers "17": -1 - 2 + 2.5 = -0.5 (Lakers by half a point); total 230 + (0.5 + 1.0) - (0.5 - 1.0) = 232
        self.assertEqual(ent["lm"]["m"], -0.5)
        self.assertEqual(ent["lm"]["t"], 232.0)
        self.assertEqual(ent["pmf"]["m"]["n0"], -3)
        self.assertIn("NBA", pg["gates"]["leagues"])
        self.assertEqual(pg["coverage"], {"tracked": 1, "priced": 1})
        self.assertEqual(pg["preds"]["NBA-lakers-hawks"][0]["model"], "bb-ledger-v1")
        # at tip the prediction made before tip is locked with the pick
        self.put_feed("2026-11-10", [fgame("7", "NBA", tip, "Lakers", "Hawks", hl=-1.5, tot=236.5, state="in", sa=50, sh=48)])
        self.apply(self.run_ledger("2026-11-11T00:30:00Z", slate=None, extra=["--model", model]))
        g = self.doc("bbslates/2026-11-10/leagues", "NBA")["games"]["NBA-lakers-hawks"]
        self.assertEqual(g["lock"]["lm"]["m"], -0.5)


if __name__ == "__main__":
    unittest.main()
