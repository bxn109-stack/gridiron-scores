# Basketball Claude Model

Updated 2026-11-12T04:58Z. Claude's own algorithm from box-score data only (possessions, efficiency, pace, four factors, rest). Tuned on early seasons, scored walk-forward on later seasons it never saw. Gated: it is labeled unvalidated and moves no chance until the spread gate passes. Break-even at −110 is 52.4%.

## NBA

Settings `{"k": 0.04, "kp": 0.03, "regress": 0.7, "hca": 3.0, "b2b": -2.65}`; 5451 of 5451 games had box totals.
- Test seasons 2025–2026: 49.44% against the closing spread (1066 games), 67.23% winners, 51.22% on totals (1066); margin miss 11.25 vs the close's 11.2
- Gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0003, t = -0.29)); games with a closing line: 2533
- 30 teams rated this season; players with box scores: 370

## MBB

Settings `{"k": 0.11, "kp": 0.03, "regress": 0.85, "hca": 3.0, "b2b": -1.39}`; 25637 of 25655 games had box totals.
- Test seasons 2025–2026: 50.61% against the closing spread (4766 games), 71.9% winners, 49.52% on totals (4790); margin miss 9.34 vs the close's 8.86
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 5156
- 513 teams rated this season; players with box scores: 5191

## WBB

Settings `{"k": 0.11, "kp": 0.06, "regress": 0.85, "hca": 3.0, "b2b": 0.0}`; 24286 of 24312 games had box totals.
- Test seasons 2025–2026: 50.8% against the closing spread (811 games), 76.71% winners, 46.98% on totals (796); margin miss 9.81 vs the close's 9.82
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 852
- 517 teams rated this season; players with box scores: 4970

## WNBA

Settings `{"k": 0.07, "kp": 0.06, "regress": 0.5, "hca": 2.0, "b2b": 0.0}`; 1193 of 1193 games had box totals.
- Test seasons 2025–2026: 54.96% against the closing spread (353 games), 66.17% winners, 49.86% on totals (353); margin miss 10.38 vs the close's 9.83
- Gate: **FAIL** (not enough games with a closing line); games with a closing line: 354
- 17 teams rated this season; players with box scores: 236

