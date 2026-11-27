# Valid vs test summary

Test file: test_per_run.csv; 142 test images (dynamic 2-class: 228 instances, no-parked: 206).

## v3 dynamic 2-class family (run04-12, run19-25): 16 groups
- Spearman rank correlation of group means, valid vs test, mAP50: 0.75
- Spearman rank correlation of group means, valid vs test, mAP50-95: 0.85
- mAP50 test minus valid: mean -0.067, range -0.097 to -0.040
- mAP50 order, valid: run24 > run25 > run23 > run21 > run12 > run19 > run08 > run07 > run20 > run09 > run22 > run06 > run11 > run10 > run04 > run05
- mAP50 order, test:  run25 > run24 > run23 > run21 > run20 > run09 > run12 > run06 > run22 > run19 > run04 > run11 > run08 > run07 > run10 > run05

## v3 no-parked family (run13-18): 6 groups
- Spearman rank correlation of group means, valid vs test, mAP50: 0.89
- Spearman rank correlation of group means, valid vs test, mAP50-95: 0.94
- mAP50 test minus valid: mean -0.008, range -0.022 to +0.016
- mAP50 order, valid: run14 > run13 > run17 > run15 > run16 > run18
- mAP50 order, test:  run13 > run14 > run15 > run17 > run16 > run18

## Seed-level comparison on test (mAP50 / mAP50-95)
Exact two-sided permutation test on the difference of means (seeds are exchangeable under the null); small n, read as a rough guide.

- run24 vs run22, mAP50: 0.846 vs 0.691 (diff +0.155); worst run24 seed 0.831 vs best run22 seed 0.696; permutation p = 1/56 = 0.018
- run24 vs run22, mAP50-95: 0.479 vs 0.376 (diff +0.102); worst run24 seed 0.465 vs best run22 seed 0.383; permutation p = 1/56 = 0.018
- run24 vs run23, mAP50: 0.846 vs 0.780 (diff +0.065); worst run24 seed 0.831 vs best run23 seed 0.812; permutation p = 2/252 = 0.008
- run24 vs run23, mAP50-95: 0.479 vs 0.430 (diff +0.049); worst run24 seed 0.465 vs best run23 seed 0.442; permutation p = 2/252 = 0.008
- run25 vs run24, mAP50: 0.864 vs 0.846 (diff +0.018); worst run25 seed 0.859 vs best run24 seed 0.862; permutation p = 3/56 = 0.054
- run25 vs run24, mAP50-95: 0.476 vs 0.479 (diff -0.003); worst run25 seed 0.467 vs best run24 seed 0.493; permutation p = 41/56 = 0.732
- run24 vs run09, mAP50: 0.846 vs 0.700 (diff +0.146); worst run24 seed 0.831 vs best run09 seed 0.723; permutation p = 1/56 = 0.018
- run24 vs run09, mAP50-95: 0.479 vs 0.382 (diff +0.097); worst run24 seed 0.465 vs best run09 seed 0.384; permutation p = 1/56 = 0.018
