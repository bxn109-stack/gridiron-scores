# NFL model lab (lab-1)

Updated 2026-11-10T00:28Z. Development seasons 2012–2017 chose every setting; test seasons 2018–2025 were scored once, each refit on all seasons before it. Live = 2026 to date.

Settings chosen on development seasons: `{"snapshots": {"lam": 0.93, "rho": 0.65}, "ridge_alpha": 10.0, "kalman": {"q": 0.25, "q_season": 15, "regress": 0.65, "cap": 21, "hfa_q": 0.001}, "gbm": {"depth": 2, "lr": 0.03, "iters": 300, "leaf": 100}}`

## Dev seasons

| Model | Games | SU acc | SU Brier | SU log loss | SU gain vs close (t) | Margin MAE | Total MAE | Spread gain vs close (t) | ATS 2+ | Totals gain vs close (t) | SU calib slope |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Closing market | 1330 | 0.670 | 0.2116 | 0.6110 | — | 10.07 | 10.65 | — | — | — | 1.08 |
| Ledger Model v1 | 1331 | 0.663 | 0.2161 | 0.6212 | -0.0104 (-2.11) | 10.22 | 10.88 | -0.0078 (-1.49) | 307–286 | -0.0150 (-3.46) | 1.19 |
| B · Dynamic ratings (Kalman) | 1331 | 0.654 | 0.2168 | 0.6224 | -0.0116 (-2.40) | 10.28 | 10.95 | -0.0123 (-2.33) | 299–275 | -0.0178 (-3.98) | 1.08 |
| Point-differential baseline | 1331 | 0.650 | 0.2189 | 0.6274 | -0.0165 (-3.18) | 10.36 | — | -0.0170 (-3.15) | 321–321 | — | 1.16 |
| Home-field-only baseline | 1331 | 0.574 | 0.2444 | 0.6819 | -0.0710 (-7.95) | 11.19 | — | -0.0565 (-5.89) | 474–475 | — | 2.36 |
| A · Opponent-adjusted efficiency | 1331 | 0.651 | 0.2165 | 0.6221 | -0.0113 (-2.25) | 10.28 | 10.91 | -0.0108 (-2.02) | 322–289 | -0.0103 (-2.46) | 1.09 |
| C · Efficiency + quarterback | 1331 | 0.654 | 0.2147 | 0.6176 | -0.0068 (-1.48) | 10.22 | 10.87 | -0.0041 (-0.87) | 312–276 | -0.0109 (-2.61) | 1.11 |
| D · Gradient-boosted matchup | 1331 | 0.657 | 0.2168 | 0.6227 | -0.0119 (-2.59) | 10.30 | 10.84 | -0.0089 (-1.83) | 328–304 | -0.0113 (-2.52) | 1.08 |
| E · Separate team scores | 1331 | 0.660 | 0.2151 | 0.6189 | -0.0081 (-1.67) | 10.24 | 10.92 | -0.0058 (-1.11) | 349–303 | -0.0140 (-3.20) | 1.07 |
| F · Ensemble (weights from earlier seasons) | 1331 | 0.657 | 0.2141 | 0.6162 | -0.0054 (-1.26) | 10.19 | 10.83 | -0.0053 (-1.11) | 285–248 | -0.0114 (-3.00) | 1.08 |

## Test seasons

| Model | Games | SU acc | SU Brier | SU log loss | SU gain vs close (t) | Margin MAE | Total MAE | Spread gain vs close (t) | ATS 2+ | Totals gain vs close (t) | SU calib slope |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Closing market | 2219 | 0.664 | 0.2108 | 0.6089 | — | 9.83 | 10.42 | — | — | — | 1.06 |
| Ledger Model v1 | 2219 | 0.637 | 0.2232 | 0.6369 | -0.0280 (-6.63) | 10.24 | 10.77 | -0.0209 (-4.45) | 567–565 | -0.0169 (-4.29) | 1.04 |
| B · Dynamic ratings (Kalman) | 2219 | 0.638 | 0.2225 | 0.6359 | -0.0269 (-6.80) | 10.21 | 10.82 | -0.0224 (-5.09) | 494–529 | -0.0169 (-4.42) | 0.98 |
| Point-differential baseline | 2219 | 0.630 | 0.2239 | 0.6383 | -0.0294 (-6.76) | 10.22 | — | -0.0199 (-4.23) | 577–562 | — | 1.02 |
| Home-field-only baseline | 2219 | 0.544 | 0.2485 | 0.6902 | -0.0813 (-10.88) | 11.03 | — | -0.0623 (-7.42) | 838–787 | — | 0.04 |
| A · Opponent-adjusted efficiency | 2219 | 0.632 | 0.2223 | 0.6351 | -0.0262 (-6.36) | 10.22 | 10.71 | -0.0196 (-4.33) | 525–562 | -0.0138 (-3.60) | 1.00 |
| C · Efficiency + quarterback | 2219 | 0.650 | 0.2187 | 0.6269 | -0.0180 (-4.91) | 10.12 | 10.59 | -0.0151 (-3.86) | 496–499 | -0.0080 (-2.37) | 1.02 |
| D · Gradient-boosted matchup | 2219 | 0.648 | 0.2198 | 0.6297 | -0.0207 (-5.62) | 10.16 | 10.64 | -0.0168 (-4.13) | 515–522 | -0.0101 (-2.70) | 1.03 |
| E · Separate team scores | 2219 | 0.644 | 0.2191 | 0.6280 | -0.0191 (-5.09) | 10.13 | 10.61 | -0.0162 (-3.95) | 530–525 | -0.0089 (-2.56) | 1.01 |
| F · Ensemble (weights from earlier seasons) | 2219 | 0.653 | 0.2186 | 0.6268 | -0.0179 (-5.19) | 10.10 | 10.58 | -0.0158 (-4.13) | 456–467 | -0.0079 (-2.47) | 1.01 |

## Live seasons

| Model | Games | SU acc | SU Brier | SU log loss | SU gain vs close (t) | Margin MAE | Total MAE | Spread gain vs close (t) | ATS 2+ | Totals gain vs close (t) | SU calib slope |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Closing market | 134 | 0.679 | 0.2157 | 0.6234 | — | 9.45 | 10.55 | — | — | — | 1.16 |
| Ledger Model v1 | 134 | 0.627 | 0.2218 | 0.6332 | -0.0098 (-0.60) | 9.91 | 11.19 | -0.0240 (-1.35) | 26–32 | -0.0414 (-2.54) | 1.25 |
| B · Dynamic ratings (Kalman) | 134 | 0.612 | 0.2250 | 0.6387 | -0.0154 (-0.90) | 9.95 | 11.23 | -0.0339 (-1.95) | 28–36 | -0.0454 (-2.88) | 1.14 |
| Point-differential baseline | 134 | 0.619 | 0.2264 | 0.6428 | -0.0194 (-1.08) | 9.83 | — | -0.0242 (-1.32) | 31–34 | — | 1.24 |
| Home-field-only baseline | 134 | 0.500 | 0.2518 | 0.6967 | -0.0733 (-2.55) | 10.54 | — | -0.0365 (-1.24) | 47–44 | — | 11.80 |
| A · Opponent-adjusted efficiency | 134 | 0.619 | 0.2191 | 0.6274 | -0.0040 (-0.23) | 9.88 | 11.13 | -0.0202 (-1.15) | 28–35 | -0.0390 (-2.49) | 1.19 |
| C · Efficiency + quarterback | 134 | 0.649 | 0.2135 | 0.6167 | 0.0067 (0.42) | 9.82 | 11.17 | -0.0158 (-0.93) | 25–29 | -0.0379 (-2.75) | 1.30 |
| D · Gradient-boosted matchup | 134 | 0.649 | 0.2148 | 0.6178 | 0.0056 (0.35) | 9.80 | 11.20 | -0.0180 (-1.10) | 34–33 | -0.0415 (-2.83) | 1.37 |
| E · Separate team scores | 134 | 0.649 | 0.2131 | 0.6154 | 0.0080 (0.49) | 9.79 | 11.28 | -0.0151 (-0.87) | 31–32 | -0.0451 (-3.15) | 1.33 |
| F · Ensemble (weights from earlier seasons) | 134 | 0.634 | 0.2148 | 0.6184 | 0.0050 (0.33) | 9.81 | 11.16 | -0.0199 (-1.23) | 23–27 | -0.0386 (-2.82) | 1.28 |

## Market + model (fitted on development seasons, scored on test seasons)

| Model | SU gain (t) | SU seasons + | Spread gain (t) | Spread seasons + | Qualifies |
|---|---|---|---|---|---|
| Ledger Model v1 | -0.0034 (-2.41) | 2/8 | -0.0021 (-1.50) | 2/8 | no |
| B · Dynamic ratings (Kalman) | -0.0020 (-2.10) | 2/8 | -0.0018 (-1.59) | 2/8 | no |
| Point-differential baseline | -0.0013 (-1.45) | 3/8 | -0.0019 (-1.60) | 3/8 | no |
| Home-field-only baseline | 0.0002 (0.37) | 3/8 | -0.0017 (-1.47) | 2/8 | no |
| A · Opponent-adjusted efficiency | -0.0020 (-1.74) | 1/8 | -0.0017 (-1.38) | 2/8 | no |
| C · Efficiency + quarterback | -0.0020 (-1.40) | 2/8 | -0.0024 (-1.56) | 3/8 | no |
| D · Gradient-boosted matchup | -0.0007 (-0.88) | 3/8 | -0.0019 (-1.49) | 3/8 | no |
| E · Separate team scores | -0.0016 (-1.21) | 3/8 | -0.0024 (-1.50) | 3/8 | no |
| F · Ensemble (weights from earlier seasons) | -0.0023 (-1.77) | 2/8 | -0.0023 (-1.68) | 3/8 | no |

Gains are average log-loss improvements per game over the closing market (positive = better than the market); t is the paired t statistic.
A same-time (forecast-time) market is not available historically (nflverse keeps closing numbers only), so the closing market is the only historical reference. It is the stronger of the two: a model that cannot match it may still match an earlier price, which the shadow record below measures from now on.

## Spreads and totals (test seasons, closing lines)

| Model | ATS all | ATS 2+ | ATS 3+ | Cover calib slope | O/U all | O/U 4+ | Over calib slope |
|---|---|---|---|---|---|---|---|
| Ledger Model v1 | 1075–1100 (49.4%) | 567–565 (50.1%) | 368–350 (51.3%) | 0.07 | 1098–1107 (49.8%) | 167–189 (46.9%) | -0.03 |
| B · Dynamic ratings (Kalman) | 1071–1104 (49.2%) | 494–529 (48.3%) | 311–318 (49.4%) | -0.05 | 1086–1119 (49.3%) | 170–200 (45.9%) | -0.05 |
| Point-differential baseline | 1091–1084 (50.2%) | 577–562 (50.7%) | 389–381 (50.5%) | 0.10 | — | — | — |
| Home-field-only baseline | 1127–1048 (51.8%) | 838–787 (51.6%) | 739–696 (51.5%) | 0.06 | — | — | — |
| A · Opponent-adjusted efficiency | 1082–1093 (49.7%) | 525–562 (48.3%) | 353–346 (50.5%) | 0.07 | 1138–1067 (51.6%) | 184–202 (47.7%) | 0.05 |
| C · Efficiency + quarterback | 1062–1113 (48.8%) | 496–499 (49.8%) | 297–284 (51.1%) | 0.07 | 1120–1085 (50.8%) | 126–125 (50.2%) | 0.15 |
| D · Gradient-boosted matchup | 1109–1066 (51.0%) | 515–522 (49.7%) | 323–328 (49.6%) | 0.04 | 1125–1080 (51.0%) | 176–164 (51.8%) | 0.14 |
| E · Separate team scores | 1063–1112 (48.9%) | 530–525 (50.2%) | 324–314 (50.8%) | 0.06 | 1122–1083 (50.9%) | 134–138 (49.3%) | 0.14 |
| F · Ensemble (weights from earlier seasons) | 1077–1098 (49.5%) | 456–467 (49.4%) | 253–257 (49.6%) | 0.02 | 1122–1083 (50.9%) | 97–86 (53.0%) | 0.11 |

Records pick the side of the closing line the model's number points to (pushes and exact agreement left out). Break-even at −110 is 52.4%.

## Against Ledger Model v1 (test seasons, same games, paired)

| Model | SU log-loss gain (t) | Margin abs-error gain (t) | Total abs-error gain (t) | Games |
|---|---|---|---|---|
| B · Dynamic ratings (Kalman) | 0.0011 (0.51) | 0.0260 (0.68) | -0.0492 (-1.59) | 2219 |
| Point-differential baseline | -0.0014 (-0.49) | 0.0200 (0.45) | — | 2219 |
| Home-field-only baseline | -0.0533 (-8.36) | -0.7875 (-8.26) | — | 2219 |
| A · Opponent-adjusted efficiency | 0.0018 (1.13) | 0.0219 (0.75) | 0.0613 (1.44) | 2219 |
| C · Efficiency + quarterback | 0.0100 (3.71) | 0.1173 (2.65) | 0.1793 (3.91) | 2219 |
| D · Gradient-boosted matchup | 0.0073 (2.50) | 0.0842 (1.68) | 0.1338 (2.74) | 2219 |
| E · Separate team scores | 0.0089 (2.89) | 0.1075 (2.15) | 0.1611 (3.38) | 2219 |
| F · Ensemble (weights from earlier seasons) | 0.0101 (4.31) | 0.1447 (3.90) | 0.1931 (5.61) | 2219 |

Positive = better than Ledger Model v1.

## By season: straight-up log-loss gain vs the closing market (× 1000; positive = better than the market)

| Model | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| Ledger Model v1 | -11 | -26 | -42 | -27 | -32 | -25 | -33 | -28 | -10 |
| B · Dynamic ratings (Kalman) | -14 | -24 | -26 | -33 | -33 | -30 | -30 | -25 | -15 |
| Point-differential baseline | -7 | -22 | -42 | -44 | -36 | -29 | -41 | -13 | -19 |
| Home-field-only baseline | -62 | -81 | -112 | -74 | -78 | -58 | -102 | -85 | -73 |
| A · Opponent-adjusted efficiency | -7 | -21 | -43 | -28 | -33 | -18 | -38 | -21 | -4 |
| C · Efficiency + quarterback | 5 | -5 | -40 | -8 | -26 | -4 | -32 | -32 | 7 |
| D · Gradient-boosted matchup | 0 | -17 | -25 | -20 | -27 | -20 | -24 | -32 | 6 |
| E · Separate team scores | -0 | -6 | -39 | -6 | -28 | -10 | -31 | -32 | 8 |
| F · Ensemble (weights from earlier seasons) | 1 | -8 | -30 | -12 | -25 | -14 | -27 | -27 | 5 |
| Games | 265 | 266 | 268 | 284 | 282 | 285 | 285 | 284 | 134 |

## Market categories (test seasons): gain vs the closing market, straight-up / spread (t)

| Model | close spread 0-3 | close spread 3.5-7 | close spread over 7 | division games | weeks 1-4 | weeks 5+ | starting QB changed |
|---|---|---|---|---|---|---|---|
| Ledger Model v1 | -0.0228 (-3.44) / -0.0214 (-3.04) | -0.0255 (-3.74) / -0.0167 (-2.33) | -0.0401 (-4.42) / -0.0271 (-2.38) | -0.0392 (-5.09) / -0.0314 (-3.60) | -0.0200 (-2.27) / -0.0147 (-1.57) | -0.0303 (-6.31) / -0.0227 (-4.20) | -0.0509 (-4.09) / -0.0209 (-1.46) |
| B · Dynamic ratings (Kalman) | -0.0223 (-3.55) / -0.0214 (-3.20) | -0.0227 (-3.50) / -0.0175 (-2.63) | -0.0409 (-5.02) / -0.0319 (-3.02) | -0.0388 (-5.30) / -0.0313 (-3.76) | -0.0260 (-3.05) / -0.0241 (-2.64) | -0.0272 (-6.09) / -0.0219 (-4.36) | -0.0447 (-3.86) / -0.0180 (-1.37) |
| Point-differential baseline | -0.0290 (-4.23) / -0.0286 (-3.93) | -0.0228 (-3.19) / -0.0142 (-1.94) | -0.0408 (-4.53) / -0.0160 (-1.49) | -0.0380 (-4.99) / -0.0245 (-2.92) | -0.0344 (-3.67) / -0.0255 (-2.59) | -0.0279 (-5.69) / -0.0182 (-3.41) | -0.0438 (-3.75) / -0.0147 (-1.14) |
| Home-field-only baseline | -0.0166 (-2.66) / -0.0136 (-2.24) | -0.0707 (-6.09) / -0.0493 (-4.28) | -0.1981 (-9.02) / -0.1575 (-5.74) | -0.0918 (-7.05) / -0.0722 (-4.81) | -0.0673 (-4.84) / -0.0451 (-2.89) | -0.0853 (-9.74) / -0.0674 (-6.84) | -0.0928 (-5.53) / -0.0443 (-2.30) |
| A · Opponent-adjusted efficiency | -0.0198 (-2.97) / -0.0188 (-2.68) | -0.0247 (-3.65) / -0.0183 (-2.56) | -0.0384 (-4.69) / -0.0231 (-2.27) | -0.0361 (-4.85) / -0.0269 (-3.19) | -0.0188 (-2.11) / -0.0181 (-1.97) | -0.0283 (-6.12) / -0.0201 (-3.86) | -0.0463 (-3.88) / -0.0181 (-1.34) |
| C · Efficiency + quarterback | -0.0182 (-2.96) / -0.0162 (-2.50) | -0.0144 (-2.47) / -0.0155 (-2.61) | -0.0236 (-3.21) / -0.0130 (-1.50) | -0.0245 (-3.80) / -0.0177 (-2.52) | -0.0166 (-2.05) / -0.0166 (-2.01) | -0.0184 (-4.48) / -0.0147 (-3.29) | -0.0284 (-2.91) / -0.0140 (-1.31) |
| D · Gradient-boosted matchup | -0.0182 (-2.99) / -0.0158 (-2.50) | -0.0169 (-2.93) / -0.0160 (-2.70) | -0.0308 (-3.98) / -0.0196 (-1.96) | -0.0217 (-3.28) / -0.0184 (-2.55) | -0.0189 (-2.40) / -0.0141 (-1.66) | -0.0213 (-5.10) / -0.0176 (-3.80) | -0.0304 (-3.10) / -0.0116 (-1.05) |
| E · Separate team scores | -0.0179 (-2.84) / -0.0157 (-2.37) | -0.0134 (-2.22) / -0.0144 (-2.34) | -0.0304 (-4.11) / -0.0198 (-2.12) | -0.0276 (-4.18) / -0.0194 (-2.67) | -0.0161 (-1.94) / -0.0108 (-1.25) | -0.0200 (-4.76) / -0.0178 (-3.82) | -0.0270 (-2.77) / -0.0115 (-1.06) |
| F · Ensemble (weights from earlier seasons) | -0.0178 (-3.06) / -0.0167 (-2.68) | -0.0138 (-2.49) / -0.0139 (-2.40) | -0.0247 (-3.72) / -0.0176 (-2.05) | -0.0261 (-4.20) / -0.0205 (-2.93) | -0.0162 (-2.08) / -0.0169 (-2.08) | -0.0184 (-4.80) / -0.0155 (-3.57) | -0.0275 (-2.95) / -0.0102 (-0.97) |
| Games | 820 | 875 | 532 | 784 | 509 | 1718 | 498 |

## Deployment verdict

A model qualifies only if blending it with the closing market beats the market alone on the test seasons: gain above zero, t of 2 or more, a positive model weight, and better in at least 5 of the 8 seasons.

| Model | Version | Training | Testing | Games | Qualifies | Reasons |
|---|---|---|---|---|---|---|
| Ledger Model v1 | ledger-v1 | settings tuned on 2006–2015; ratings walk forward from 2006 | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0280 per game, t -6.6, 2219 games); worse than the closing market against the spread (log loss -0.0209 per game, t -4.5, 2175 games); worse than the closing market on totals (log loss -0.0169 per game, t -4.3, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0034, t -2.4, better in 2 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0021, t -1.5, better in 2 of 8 test seasons) |
| B · Dynamic ratings (Kalman) | lab-1/B | settings chosen on 2012–2017; ratings walk forward from 2006 | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0269 per game, t -6.8, 2219 games); worse than the closing market against the spread (log loss -0.0224 per game, t -5.1, 2175 games); worse than the closing market on totals (log loss -0.0169 per game, t -4.4, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0020, t -2.1, better in 2 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0018, t -1.6, better in 2 of 8 test seasons) |
| Point-differential baseline | lab-1/pd | no fitted settings; season-to-date margins | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0294 per game, t -6.8, 2219 games); worse than the closing market against the spread (log loss -0.0199 per game, t -4.2, 2175 games); adds nothing to the closing market straight-up when blended with it (gain -0.0013, t -1.4, better in 3 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0019, t -1.6, better in 3 of 8 test seasons) |
| Home-field-only baseline | lab-1/hfa | none (home team by 1.8) | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0813 per game, t -10.9, 2219 games); worse than the closing market against the spread (log loss -0.0623 per game, t -7.4, 2175 games); adds nothing to the closing market straight-up when blended with it (gain +0.0002, t 0.4, better in 3 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0017, t -1.5, better in 2 of 8 test seasons); straight-up chances miscalibrated (slope 0.04) |
| A · Opponent-adjusted efficiency | lab-1/A | 2007 to the season before (refit each season) | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0262 per game, t -6.4, 2219 games); worse than the closing market against the spread (log loss -0.0196 per game, t -4.3, 2175 games); worse than the closing market on totals (log loss -0.0138 per game, t -3.6, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0020, t -1.7, better in 1 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0017, t -1.4, better in 2 of 8 test seasons) |
| C · Efficiency + quarterback | lab-1/C | 2007 to the season before (refit each season) | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0180 per game, t -4.9, 2219 games); worse than the closing market against the spread (log loss -0.0151 per game, t -3.9, 2175 games); worse than the closing market on totals (log loss -0.0080 per game, t -2.4, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0020, t -1.4, better in 2 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0024, t -1.6, better in 3 of 8 test seasons) |
| D · Gradient-boosted matchup | lab-1/D | 2007 to the season before (refit each season) | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0207 per game, t -5.6, 2219 games); worse than the closing market against the spread (log loss -0.0168 per game, t -4.1, 2175 games); worse than the closing market on totals (log loss -0.0101 per game, t -2.7, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0007, t -0.9, better in 3 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0019, t -1.5, better in 3 of 8 test seasons) |
| E · Separate team scores | lab-1/E | 2007 to the season before (refit each season) | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0191 per game, t -5.1, 2219 games); worse than the closing market against the spread (log loss -0.0162 per game, t -3.9, 2175 games); worse than the closing market on totals (log loss -0.0089 per game, t -2.6, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0016, t -1.2, better in 3 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0024, t -1.5, better in 3 of 8 test seasons) |
| F · Ensemble (weights from earlier seasons) | lab-1/F | A–E as above; weights from 2009 to the season before | 2018–2025 | 2219 | no | worse than the closing market straight-up (log loss -0.0179 per game, t -5.2, 2219 games); worse than the closing market against the spread (log loss -0.0158 per game, t -4.1, 2175 games); worse than the closing market on totals (log loss -0.0079 per game, t -2.5, 2205 games); adds nothing to the closing market straight-up when blended with it (gain -0.0023, t -1.8, better in 2 of 8 test seasons); adds nothing to the closing market against the spread when blended with it (gain -0.0023, t -1.7, better in 3 of 8 test seasons) |

## Shadow model

Chosen on development seasons only (exportable candidate with the lowest development-season straight-up log loss): **C · Efficiency + quarterback**. Development log loss: A 0.6221, C 0.6176. The full ensemble F was 0.0014 better per game on the same 1331 development games (t 0.89), too small to justify a model the score-update task cannot compute from published team numbers.

It is recorded before every NFL kickoff in the private ledger (append-only, with its version and data cutoff) and never used for a pick, a value verdict or a stake. It would be promoted only after its verified pregame record beats the same-time market prices the ledger saves.

Published numbers: model/results/nfl_shadow.json (32 teams, data through 2026-11-09T00:20:00-05:00); self-check reproduced the lab's predictions for 13 upcoming games (largest difference 0.048 points).

