# College football efficiency model backtest

Updated 2026-09-27 13:16 UTC. Walk-forward ratings from garbage-time-free advanced stats (points added per play, success rate, pace), plus recruiting talent and returning production early in the season. Blend weights fitted on 2014–2019 only; scored on 2021–2025.

## Data check

- Games: 27513; with a closing line: 12666; with advanced stats for both teams: 12875 (of the lined games: 12390)
- Talent ratings: 2275 team-seasons; returning production: 1555 team-seasons
- Games with an opening line: tuning seasons 0, test seasons 4405
- Advanced-stat fields seen: `defense, gameId, offense, opponent, season, seasonType, team, week`; offense fields: `drives, explosiveness, lineYards, lineYardsTotal, openFieldYards, openFieldYardsTotal, passingDowns, passingPlays, plays, powerSuccess, ppa, rushingPlays, secondLevelYards, secondLevelYardsTotal, standardDowns, stuffRate, successRate, totalPPA`
- talent 2014: cached, 0 rows; returning production 2014: cached, 125 rows
- talent 2015: cached, 232 rows; returning production 2015: cached, 128 rows
- talent 2016: cached, 237 rows; returning production 2016: cached, 128 rows
- talent 2017: cached, 157 rows; returning production 2017: cached, 128 rows
- talent 2018: cached, 236 rows; returning production 2018: cached, 130 rows
- talent 2019: cached, 231 rows; returning production 2019: cached, 130 rows
- talent 2020: cached, 219 rows; returning production 2020: cached, 130 rows
- talent 2021: cached, 224 rows; returning production 2021: cached, 128 rows
- talent 2022: cached, 233 rows; returning production 2022: cached, 130 rows
- talent 2023: cached, 238 rows; returning production 2023: cached, 131 rows
- talent 2024: cached, 134 rows; returning production 2024: cached, 133 rows
- talent 2025: cached, 134 rows; returning production 2025: cached, 134 rows
- advanced stats 2014 regular: cached, 1618 rows
- advanced stats 2014 postseason: cached, 78 rows
- advanced stats 2015 regular: cached, 1644 rows
- advanced stats 2015 postseason: cached, 82 rows
- advanced stats 2016 regular: cached, 1632 rows
- advanced stats 2016 postseason: cached, 82 rows
- advanced stats 2017 regular: cached, 1658 rows
- advanced stats 2017 postseason: cached, 80 rows
- advanced stats 2018 regular: cached, 1684 rows
- advanced stats 2018 postseason: cached, 78 rows
- advanced stats 2019 regular: cached, 1694 rows
- advanced stats 2019 postseason: cached, 80 rows
- advanced stats 2020 regular: cached, 1084 rows
- advanced stats 2020 postseason: cached, 52 rows
- advanced stats 2021 regular: cached, 1698 rows
- advanced stats 2021 postseason: cached, 76 rows
- advanced stats 2022 regular: cached, 2822 rows
- advanced stats 2022 postseason: cached, 96 rows
- advanced stats 2023 regular: cached, 2850 rows
- advanced stats 2023 postseason: cached, 134 rows
- advanced stats 2024 regular: cached, 3112 rows
- advanced stats 2024 postseason: cached, 100 rows
- advanced stats 2025 regular: cached, 3216 rows
- advanced stats 2025 postseason: cached, 100 rows
- Efficiency settings: `{"k": 0.1, "re": 0.5, "kp": 0.05, "fcs_e": 0.3}`; points-rating settings: `{"k": 0.12, "regress": 0.8, "hfa": 3.0, "cap": 28, "fcs_start": -20.0}`
- Spread blend weights (fitted on 5186 tuning games): home field +2.795, points rating +0.726, PPA/play edge -1.275, success-rate edge +64.180, talent (per 100) +1.167, talent, early weeks +1.776, returning production, early weeks +10.705
- Total blend weights (4801 games): constant +17.094, points-based total +0.703, PPA sum +23.991, success-rate sum +4.442, pace +0.155

## Average miss on the final margin (lower is better)

| Model | Tuning seasons | Test seasons |
|---|---|---|
| Closing line (the market) | 12.48 (5186 games) | 12.07 (6913 games) |
| Points rating only (old model) | 14.29 | 13.80 |
| Efficiency only | 13.46 | 13.27 |
| Efficiency + points (new model) | 13.21 | 12.90 |

## Does the model add anything the line doesn't already know?

Fits final margin = a × line + b × model on earlier seasons, then checks the average miss on later seasons it never saw. If the blend misses by less than the line alone, the model carries real information the market missed.

| Line | Fitted on | Scored on | Weight on line | Weight on model | Line alone | Line + model | Games |
|---|---|---|---|---|---|---|---|
| Closing | 2014–2019 | 2021–2025 | 0.82 | 0.12 | 12.066 | 12.129 | 6913 |
| Opening | 2021–2022 | 2023–2025 | 0.84 | 0.07 | 12.191 | 12.275 | 2675 |

## Spread bets on test seasons, new model (at −110)

| Model disagrees by | vs closing line | vs opening line |
|---|---|---|
| 1+ pts | 2786–2810 (49.8%, ROI -5.0%) | 1743–1722 (50.3%, ROI -4.0%) |
| 2+ pts | 2208–2258 (49.4%, ROI -5.6%) | 1349–1350 (50.0%, ROI -4.6%) |
| 3+ pts | 1699–1756 (49.2%, ROI -6.1%) | 1030–1014 (50.4%, ROI -3.8%) |
| 4+ pts | 1309–1348 (49.3%, ROI -5.9%) | 787–753 (51.1%, ROI -2.4%) |
| 5+ pts | 1001–1019 (49.6%, ROI -5.4%) | 573–540 (51.5%, ROI -1.7%) |
| 7+ pts | 559–572 (49.4%, ROI -5.6%) | 299–295 (50.3%, ROI -3.9%) |
| 10+ pts | 241–276 (46.6%, ROI -11.0%) | 109–127 (46.2%, ROI -11.8%) |

## Same, old points-only model, for comparison

| Model disagrees by | vs closing line | vs opening line |
|---|---|---|
| 2+ pts | 2417–2444 (49.7%) | 1527–1508 (50.3%) |
| 4+ pts | 1583–1640 (49.1%) | 978–1005 (49.3%) |
| 7+ pts | 880–934 (48.5%) | 543–575 (48.6%) |

## Does the line move toward the model after it opens? (test seasons)

Betting the opening number and watching it close in your direction (beating the closing line) is the best-known sign of a real edge.

| Model disagrees with opener by | Games | Closed toward model | Closed away | Average move toward model |
|---|---|---|---|---|
| 2+ pts | 2740 | 1345 (49%) | 1008 (37%) | +0.39 pts |
| 3+ pts | 2074 | 1007 (49%) | 765 (37%) | +0.40 pts |
| 5+ pts | 1134 | 552 (49%) | 411 (36%) | +0.50 pts |
| 7+ pts | 603 | 286 (47%) | 229 (38%) | +0.55 pts |

## By matchup type (test seasons, new model disagrees by 3+ pts)

| Matchup | vs closing | vs opening |
|---|---|---|
| Power vs Power | 350–387 (47.5%) | 378–347 (52.1%) |
| Power vs Group of 5 | 185–177 (51.1%) | 187–167 (52.8%) |
| Group of 5 vs Group of 5 | 301–340 (47.0%) | 301–325 (48.1%) |
| FCS involved | 863–852 (50.3%) | 164–175 (48.4%) |

## Total bets on test seasons, new model

| Model total differs by | vs closing total | vs opening total |
|---|---|---|
| 2+ pts | 2266–2135 (51.5%, ROI -1.7%) | 1407–1323 (51.5%, ROI -1.6%) |
| 4+ pts | 1246–1177 (51.4%, ROI -1.8%) | 739–711 (51.0%, ROI -2.7%) |
| 6+ pts | 627–564 (52.6%, ROI +0.5%) | 331–319 (50.9%, ROI -2.8%) |
| 8+ pts | 236–242 (49.4%, ROI -5.7%) | 115–117 (49.6%, ROI -5.4%) |

Break-even at −110 is 52.4%. Small groups (under ~200 bets) swing a lot by chance. Run time 15 s.
