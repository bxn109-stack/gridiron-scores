# Basketball: ESPN BPI against the closing line

Updated 2026-11-12T03:34+00:00. Take the side ESPN's BPI likes against the closing spread when BPI's projected margin differs from the line by at least the gap. The margin scale and gap are chosen on the tuning set and scored on a test set they never saw; a gap passes only at 52.38%+ (break-even at -110) on both, with 100+ test games. Requests this run: 7964.

## NBA

tuned on 2023, tested on 2026; 1320 tuning and 1061 test games with an ESPN win chance (355 in October-November). Margin scale 10.26. **no gap beats the closing line on both the tuning and the test set**.

| Gap | Tuning | Test | Oct–Nov | Pass |
|---|---|---|---|---|
| 0+ | 646-650 (49.85%) | 519-542 (48.92%) | 179-169 (51.44%) | no |
| 1+ | 427-410 (51.02%) | 420-415 (50.3%) | 126-102 (55.26%) | no |
| 2+ | 268-223 (54.58%) | 305-317 (49.04%) | 79-52 (60.31%) | no |
| 3+ | 144-124 (53.73%) | 197-203 (49.25%) | 38-28 (57.58%) | no |
| 4+ | 82-70 (53.95%) | 127-131 (49.22%) | 23-14 (62.16%) | no |
| 5+ | 35-35 (50.0%) | 77-77 (50.0%) | 8-6 (57.14%) | no |
| 6+ | 17-19 (47.22%) | 42-53 (44.21%) | 3-0 (100.0%) | no |
| 7+ | 8-16 (33.33%) | 27-29 (48.21%) | 1-0 (100.0%) | no |
| 8+ | 6-10 (37.5%) | 14-13 (51.85%) | — | no |
| 10+ | 1-0 (100.0%) | 5-4 (55.56%) | — | no |
| 12+ | — | 2-0 (100.0%) | — | no |

## MBB

tuned on games before 2026-01-15, tested on games after; 1849 tuning and 2910 test games with an ESPN win chance (263 in October-November). Margin scale 11.67. **no gap beats the closing line on both the tuning and the test set**.

| Gap | Tuning | Test | Oct–Nov | Pass |
|---|---|---|---|---|
| 0+ | 907-942 (49.05%) | 1438-1472 (49.42%) | 118-145 (44.87%) | no |
| 1+ | 685-689 (49.85%) | 999-997 (50.05%) | 94-111 (45.85%) | no |
| 2+ | 471-489 (49.06%) | 607-591 (50.67%) | 69-75 (47.92%) | no |
| 3+ | 303-312 (49.27%) | 327-292 (52.83%) | 47-54 (46.53%) | no |
| 4+ | 189-187 (50.27%) | 136-115 (54.18%) | 33-38 (46.48%) | no |
| 5+ | 114-124 (47.9%) | 51-44 (53.68%) | 21-30 (41.18%) | no |
| 6+ | 67-73 (47.86%) | 22-15 (59.46%) | 10-20 (33.33%) | no |
| 7+ | 41-39 (51.25%) | 9-3 (75.0%) | 4-14 (22.22%) | no |
| 8+ | 21-26 (44.68%) | 6-1 (85.71%) | 3-9 (25.0%) | no |
| 10+ | 6-8 (42.86%) | 3-0 (100.0%) | 0-3 (0.0%) | no |
| 12+ | 3-2 (60.0%) | 3-0 (100.0%) | 0-1 (0.0%) | no |

## WBB

tuned on games before 2026-01-15, tested on games after; 263 tuning and 547 test games with an ESPN win chance (27 in October-November). Margin scale 16.11. **BPI beats the close by a gap of 1+ points on both sets**.

| Gap | Tuning | Test | Oct–Nov | Pass |
|---|---|---|---|---|
| 0+ | 137-126 (52.09%) | 285-262 (52.1%) | 16-11 (59.26%) | no |
| 1+ | 119-105 (53.12%) | 249-225 (52.53%) | 14-11 (56.0%) | yes |
| 2+ | 104-89 (53.89%) | 211-194 (52.1%) | 10-10 (50.0%) | no |
| 3+ | 87-64 (57.62%) | 174-164 (51.48%) | 8-7 (53.33%) | no |
| 4+ | 67-49 (57.76%) | 146-132 (52.52%) | 6-4 (60.0%) | yes |
| 5+ | 58-34 (63.04%) | 119-109 (52.19%) | 5-2 (71.43%) | no |
| 6+ | 43-27 (61.43%) | 95-70 (57.58%) | 4-2 (66.67%) | yes |
| 7+ | 31-21 (59.62%) | 68-51 (57.14%) | 2-1 (66.67%) | yes |
| 8+ | 24-14 (63.16%) | 50-36 (58.14%) | 2-1 (66.67%) | no |
| 10+ | 17-8 (68.0%) | 19-12 (61.29%) | 1-1 (50.0%) | no |
| 12+ | 7-4 (63.64%) | 9-3 (75.0%) | 0-1 (0.0%) | no |

