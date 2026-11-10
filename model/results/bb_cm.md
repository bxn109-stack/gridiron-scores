# Basketball Claude Model

Updated 2026-11-10T20:43Z. Claude's own algorithm from box-score data only (possessions, efficiency, pace, four factors, rest). Tuned on early seasons, scored walk-forward on later seasons it never saw. Gated: it is labeled unvalidated and moves no chance until the spread gate passes. Break-even at −110 is 52.4%.

## NBA

Settings `{"k": 0.04, "kp": 0.03, "regress": 0.7, "hca": 3.0, "b2b": -2.65}`; 5445 of 5445 games had box totals.
- Test seasons 2025–2026: 49.44% against the closing spread (1066 games), 67.23% winners, 51.22% on totals (1066); margin miss 11.25 vs the close's 11.2
- Gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0003, t = -0.29)); games with a closing line: 2527
- 30 teams rated this season; players with box scores: 368

## MBB

Settings `{"k": 0.11, "kp": 0.03, "regress": 0.85, "hca": 3.0, "b2b": -1.39}`; 25569 of 25586 games had box totals.
- Test seasons 2025–2026: 50.5% against the closing spread (4766 games), 71.82% winners, 49.58% on totals (4790); margin miss 9.34 vs the close's 8.86
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 5129
- 503 teams rated this season; players with box scores: 4715

## WBB

Settings `{"k": 0.11, "kp": 0.06, "regress": 0.85, "hca": 3.0, "b2b": 0.0}`; 24235 of 24261 games had box totals.
- Test seasons 2025–2026: None% against the closing spread (0 games), 76.6% winners, None% on totals (0); margin miss 9.81 vs the close's None
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 44
- 505 teams rated this season; players with box scores: 4620

## WNBA

Settings `{"k": 0.07, "kp": 0.06, "regress": 0.5, "hca": 2.0, "b2b": 0.0}`; 1193 of 1193 games had box totals.
- Test seasons 2025–2026: 54.55% against the closing spread (352 games), 66.17% winners, 50.0% on totals (352); margin miss 10.38 vs the close's 9.77
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 353
- 17 teams rated this season; players with box scores: 236

