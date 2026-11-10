# College football efficiency model backtest

Updated 2026-11-10 00:07 UTC. Walk-forward ratings from garbage-time-free advanced stats (points added per play, success rate, pace), plus recruiting talent and returning production early in the season. Blend weights fitted on 2014–2019 only; scored on 2021–2025.

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
- Spread blend weights (fitted on 5186 tuning games): home field +2.795, points rating +0.725, PPA/play edge -1.171, success-rate edge +64.030, talent (per 100) +1.166, talent, early weeks +1.777, returning production, early weeks +10.704
- Total blend weights (4801 games): constant +17.084, points-based total +0.703, PPA sum +23.898, success-rate sum +4.596, pace +0.155

## Average miss on the final margin (lower is better)

| Model | Tuning seasons | Test seasons |
|---|---|---|
| Closing line (the market) | 12.48 (5186 games) | 12.07 (6913 games) |
| Points rating only (old model) | 14.29 | 13.80 |
| Efficiency only | 13.46 | 13.26 |
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
| 1+ pts | 2781–2805 (49.8%, ROI -5.0%) | 1746–1723 (50.3%, ROI -3.9%) |
| 2+ pts | 2201–2260 (49.3%, ROI -5.8%) | 1347–1355 (49.9%, ROI -4.8%) |
| 3+ pts | 1700–1750 (49.3%, ROI -5.9%) | 1023–1005 (50.4%, ROI -3.7%) |
| 4+ pts | 1312–1346 (49.4%, ROI -5.8%) | 785–754 (51.0%, ROI -2.6%) |
| 5+ pts | 996–1015 (49.5%, ROI -5.4%) | 571–535 (51.6%, ROI -1.4%) |
| 7+ pts | 560–571 (49.5%, ROI -5.5%) | 296–291 (50.4%, ROI -3.7%) |
| 10+ pts | 242–275 (46.8%, ROI -10.6%) | 108–128 (45.8%, ROI -12.6%) |

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
| 2+ pts | 2743 | 1347 (49%) | 1009 (37%) | +0.38 pts |
| 3+ pts | 2058 | 1000 (49%) | 756 (37%) | +0.39 pts |
| 5+ pts | 1127 | 554 (49%) | 404 (36%) | +0.52 pts |
| 7+ pts | 595 | 279 (47%) | 228 (38%) | +0.55 pts |

## By matchup type (test seasons, new model disagrees by 3+ pts)

| Matchup | vs closing | vs opening |
|---|---|---|
| Power vs Power | 349–386 (47.5%) | 374–344 (52.1%) |
| Power vs Group of 5 | 186–174 (51.7%) | 186–165 (53.0%) |
| Group of 5 vs Group of 5 | 301–341 (46.9%) | 298–323 (48.0%) |
| FCS involved | 864–849 (50.4%) | 165–173 (48.8%) |

## Total bets on test seasons, new model

| Model total differs by | vs closing total | vs opening total |
|---|---|---|
| 2+ pts | 2259–2133 (51.4%, ROI -1.8%) | 1404–1321 (51.5%, ROI -1.6%) |
| 4+ pts | 1241–1169 (51.5%, ROI -1.7%) | 740–714 (50.9%, ROI -2.8%) |
| 6+ pts | 628–561 (52.8%, ROI +0.8%) | 327–315 (50.9%, ROI -2.8%) |
| 8+ pts | 234–245 (48.9%, ROI -6.7%) | 113–121 (48.3%, ROI -7.8%) |

Break-even at −110 is 52.4%. Small groups (under ~200 bets) swing a lot by chance. Run time 9 s.
