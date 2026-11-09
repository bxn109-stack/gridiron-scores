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


if __name__ == "__main__":
    unittest.main()
