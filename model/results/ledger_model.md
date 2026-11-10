# Ledger Model

Updated 2026-11-10T00:04Z. Walk-forward points ratings; settings tuned on early seasons, scored on later seasons never seen while tuning. Break-even at −110 is 52.4%.

## NFL

Tuned on 2006–2015, tested on 2016–2025. Settings `{"k": 0.06, "kt": 0.04, "regress": 0.7, "hfa": 2.5, "cap": 24}`.
- Average miss on the margin, test seasons: model 10.18 pts vs closing line 9.78 pts (2761 games)
- Spread gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0004, t = -0.44)). Test ATS when the model disagrees by 2+ pts: 684–691
- Total gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0016, t = -2.08)). Test record at 5+ pts: 85–114
- Moneyline blend gate: **FAIL** (no reliable gain over the no-vig moneyline (log-loss gain -0.0011, t = -1.58))
- This season so far (live, out of sample): 26–32 at 2+ pts
- Push tables: 67 spread rows, 55 total rows

## FBS

Tuned on 2014–2019, tested on 2021–2025. Settings `{"k": 0.12, "kt": 0.08, "regress": 0.8, "hfa": 3.0, "cap": 28}`.
- Average miss on the margin, test seasons: model 13.82 pts vs closing line 12.07 pts (6913 games)
- Spread gate: **FAIL** (no reliable gain over the closing line (log-loss gain -0.0008, t = -1.88)). Test ATS when the model disagrees by 1+ pts: 2879–2895
- Total gate: **FAIL** (the model's edge has the wrong sign). Test record at 7+ pts: 287–307
- Moneyline blend gate: **FAIL** (not enough games with moneylines)
- This season so far (live, out of sample): 493–532 at 1+ pts
- Push tables: 160 spread rows, 93 total rows

A failed gate means the page gives this model no weight: it is shown for comparison only.
