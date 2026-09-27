# College football model backtest

Updated 2026-09-27 13:15 UTC. Walk-forward ratings; tuned on 2014–2019, tested on 2021–2025 (never seen while tuning).
Games: 27513 (12666 with a closing line). Tuned settings: `{"k": 0.12, "regress": 0.8, "hfa": 3.0, "cap": 28, "fcs_start": -20.0}`

- Tuning seasons: model average miss 14.29 pts vs closing line 12.48 pts (5186 games)
- Test seasons: model average miss 13.80 pts vs closing line 12.07 pts (6913 games)

## Spread bets on test seasons (at −110)

| Model disagrees by | vs closing line | vs opening line |
|---|---|---|
| 1+ pts | 2885–2893 (49.9%, ROI -4.7%) | 1838–1837 (50.0%, ROI -4.5%) |
| 2+ pts | 2417–2444 (49.7%, ROI -5.1%) | 1527–1508 (50.3%, ROI -3.9%) |
| 3+ pts | 1970–2034 (49.2%, ROI -6.1%) | 1226–1234 (49.8%, ROI -4.9%) |
| 4+ pts | 1583–1640 (49.1%, ROI -6.2%) | 978–1005 (49.3%, ROI -5.8%) |
| 5+ pts | 1289–1347 (48.9%, ROI -6.6%) | 813–840 (49.2%, ROI -6.1%) |
| 7+ pts | 880–934 (48.5%, ROI -7.4%) | 543–575 (48.6%, ROI -7.3%) |
| 10+ pts | 523–591 (46.9%, ROI -10.4%) | 347–370 (48.4%, ROI -7.6%) |

## By matchup type (test seasons, model disagrees by 3+ pts)

| Matchup | vs closing | vs opening |
|---|---|---|
| Power vs Power | 439–489 (47.3%) | 429–437 (49.5%) |
| Power vs Group of 5 | 247–245 (50.2%) | 253–245 (50.8%) |
| Group of 5 vs Group of 5 | 349–356 (49.5%) | 335–319 (51.2%) |
| FCS involved | 935–944 (49.8%) | 209–233 (47.3%) |

## Total bets on test seasons

| Model total differs by | vs closing total | vs opening total |
|---|---|---|
| 2+ pts | 2116–1999 (51.4%, ROI -1.8%) | 1378–1243 (52.6%, ROI +0.4%) |
| 4+ pts | 1153–1050 (52.3%, ROI -0.1%) | 665–610 (52.2%, ROI -0.4%) |
| 6+ pts | 495–490 (50.3%, ROI -4.1%) | 256–261 (49.5%, ROI -5.5%) |
| 8+ pts | 194–212 (47.8%, ROI -8.8%) | 103–109 (48.6%, ROI -7.2%) |

Break-even at −110 is 52.4%. Small groups (under ~200 bets) swing a lot by chance.
