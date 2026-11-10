# Basketball Ledger Model

Updated 2026-11-10T20:43Z. The football Ledger Model's engine on basketball: walk-forward points ratings; settings tuned on early seasons, scored on later seasons never seen while tuning, against ESPN's closing lines. Break-even at −110 is 52.4%.

## NBA

Seasons on disk [2023, 2024, 2025, 2026, 2027]; 2527 games with a closing line. Tuned on 2023–2024, tested on 2025–2026. Settings `{"k": 0.03, "kt": 0.04, "regress": 0.8, "hfa": 3.0, "cap": 30}`.
- Average miss on the margin, test seasons: model 11.73 pts vs closing line 11.20 pts (1066 games)
- Spread gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0006, t = -0.55))
- Total gate: **FAIL** (the model's edge has the wrong sign)
- Moneyline blend gate: **FAIL** (no reliable gain over the no-vig moneyline (log-loss gain -0.0024, t = -1.48))
- Ratings for 41 teams; push tables: 67 spread rows, 74 total rows

## MBB

Seasons on disk [2023, 2024, 2025, 2026, 2027]; 5129 games with a closing line. Tuned on 2024–2024, tested on 2025–2026. Settings `{"k": 0.11, "kt": 0.06, "regress": 0.95, "hfa": 4.0, "cap": 30}`.
- Average miss on the margin, test seasons: model 9.21 pts vs closing line 8.86 pts (4766 games)
- Spread gate: **FAIL** (not enough games with a closing line)
- Total gate: **FAIL** (not enough games with a closing line)
- Moneyline blend gate: **FAIL** (not enough games with moneylines)
- Ratings for 1043 teams; push tables: 100 spread rows, 92 total rows

## WBB

Seasons on disk [2023, 2024, 2025, 2026, 2027]; 44 games with a closing line. Tuned on 2024–2024, tested on 2025–2026. Settings `{"k": 0.11, "kt": 0.06, "regress": 0.95, "hfa": 3.25, "cap": 35}`.
- Average miss on the margin, test seasons: model — pts vs closing line — pts (0 games)
- Spread gate: **FAIL** (not enough games with a closing line)
- Total gate: **FAIL** (not enough games with a closing line)
- Moneyline blend gate: **FAIL** (not enough games with moneylines)
- Ratings for 922 teams; push tables: 0 spread rows, 0 total rows

## WNBA

Seasons on disk [2023, 2024, 2025, 2026]; 353 games with a closing line. Tuned on 2023–2024, tested on 2025–2026. Settings `{"k": 0.07, "kt": 0.04, "regress": 0.5, "hfa": 1.5, "cap": 30}`.
- Average miss on the margin, test seasons: model 10.06 pts vs closing line 9.75 pts (353 games)
- Spread gate: **FAIL** (not enough games with a closing line)
- Total gate: **FAIL** (not enough games with a closing line)
- Moneyline blend gate: **FAIL** (not enough games with moneylines)
- Ratings for 23 teams; push tables: 34 spread rows, 35 total rows

A failed gate means the page gives this model no weight: it is shown for comparison only.
