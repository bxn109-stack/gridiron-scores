# College football efficiency model backtest

The run stopped with an error at 2026-09-27 13:15 UTC.

```
Traceback (most recent call last):
  File "/home/runner/work/gridiron-scores/gridiron-scores/model/cfb_eff.py", line 362, in <module>
    sys.exit(main())
             ^^^^^^
  File "/home/runner/work/gridiron-scores/gridiron-scores/model/cfb_eff.py", line 312, in main
    w, e_l, e_b, n = blend(out_all, key, TUNE, TEST)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/runner/work/gridiron-scores/gridiron-scores/model/cfb_eff.py", line 236, in blend
    w = solve(X, y)
        ^^^^^^^^^^^
  File "/home/runner/work/gridiron-scores/gridiron-scores/model/cfb_eff.py", line 102, in solve
    n = len(X[0])
            ~^^^
IndexError: list index out of range
```

Fetch log:

- talent 2014: downloaded in 0 s, 0 rows; returning production 2014: downloaded in 0 s, 125 rows
- talent 2015: downloaded in 0 s, 232 rows; returning production 2015: downloaded in 0 s, 128 rows
- talent 2016: downloaded in 0 s, 237 rows; returning production 2016: downloaded in 0 s, 128 rows
- talent 2017: downloaded in 0 s, 157 rows; returning production 2017: downloaded in 0 s, 128 rows
- talent 2018: downloaded in 0 s, 236 rows; returning production 2018: downloaded in 0 s, 130 rows
- talent 2019: downloaded in 0 s, 231 rows; returning production 2019: downloaded in 0 s, 130 rows
- talent 2020: downloaded in 0 s, 219 rows; returning production 2020: downloaded in 0 s, 130 rows
- talent 2021: downloaded in 0 s, 224 rows; returning production 2021: downloaded in 0 s, 128 rows
- talent 2022: downloaded in 0 s, 233 rows; returning production 2022: downloaded in 0 s, 130 rows
- talent 2023: downloaded in 0 s, 238 rows; returning production 2023: downloaded in 0 s, 131 rows
- talent 2024: downloaded in 0 s, 134 rows; returning production 2024: downloaded in 0 s, 133 rows
- talent 2025: downloaded in 0 s, 134 rows; returning production 2025: downloaded in 0 s, 134 rows
- advanced stats 2014 regular: downloaded in 4 s, 1618 rows
- advanced stats 2014 postseason: downloaded in 0 s, 78 rows
- advanced stats 2015 regular: downloaded in 4 s, 1644 rows
- advanced stats 2015 postseason: downloaded in 0 s, 82 rows
- advanced stats 2016 regular: downloaded in 4 s, 1632 rows
- advanced stats 2016 postseason: downloaded in 0 s, 82 rows
- advanced stats 2017 regular: downloaded in 4 s, 1658 rows
- advanced stats 2017 postseason: downloaded in 0 s, 80 rows
- advanced stats 2018 regular: downloaded in 5 s, 1684 rows
- advanced stats 2018 postseason: downloaded in 0 s, 78 rows
- advanced stats 2019 regular: downloaded in 4 s, 1694 rows
- advanced stats 2019 postseason: downloaded in 0 s, 80 rows
- advanced stats 2020 regular: downloaded in 4 s, 1084 rows
- advanced stats 2020 postseason: downloaded in 0 s, 52 rows
- advanced stats 2021 regular: downloaded in 3 s, 1698 rows
- advanced stats 2021 postseason: downloaded in 1 s, 76 rows
- advanced stats 2022 regular: downloaded in 6 s, 2822 rows
- advanced stats 2022 postseason: downloaded in 0 s, 96 rows
- advanced stats 2023 regular: downloaded in 7 s, 2850 rows
- advanced stats 2023 postseason: downloaded in 1 s, 134 rows
- advanced stats 2024 regular: downloaded in 7 s, 3112 rows
- advanced stats 2024 postseason: downloaded in 0 s, 100 rows
- advanced stats 2025 regular: downloaded in 7 s, 3216 rows
- advanced stats 2025 postseason: downloaded in 0 s, 100 rows
