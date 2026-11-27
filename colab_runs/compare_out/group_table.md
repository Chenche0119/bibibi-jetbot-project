| Group | Dataset | imgsz | Seeds | Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|---|
| run01 baseline | v2 raw 5-class | 320 | 1 | 0.970 | 0.895 | 0.968 | 0.509 |
| run02 baseline | v2 dynamic 2-class | 320 | 1 | 0.856 | 0.890 | 0.907 | 0.509 |
| run03 rot15 | v2 dynamic 2-class | 320 | 1 | 0.826 | 0.828 | 0.859 | 0.371 |
| run04 baseline | v3 dynamic 2-class | 320 | 3 | 0.810 ± 0.064 | 0.667 ± 0.047 | 0.727 ± 0.019 | 0.378 ± 0.002 |
| run05 offline45grid | v3 dynamic 2-class | 320 | 1 | 0.787 | 0.637 | 0.676 | 0.321 |
| run06 rot15 | v3 dynamic 2-class | 320 | 1 | 0.812 | 0.718 | 0.741 | 0.307 |
| run07 rot30 | v3 dynamic 2-class | 320 | 1 | 0.892 | 0.658 | 0.747 | 0.317 |
| run08 rot45 | v3 dynamic 2-class | 320 | 1 | 0.875 | 0.677 | 0.749 | 0.314 |
| run09 offlineflip | v3 dynamic 2-class | 320 | 3 | 0.785 ± 0.074 | 0.695 ± 0.013 | 0.742 ± 0.016 | 0.374 ± 0.008 |
| run10 offlinesmallangle | v3 dynamic 2-class | 320 | 1 | 0.766 | 0.700 | 0.729 | 0.388 |
| run11 offlinelargeangle | v3 dynamic 2-class | 320 | 1 | 0.845 | 0.640 | 0.731 | 0.363 |
| run12 offlineflip_img416 | v3 dynamic 2-class | 416 | 5 | 0.804 ± 0.087 | 0.707 ± 0.006 | 0.758 ± 0.026 | 0.398 ± 0.022 |
| run13 baseline | v3 no-parked | 320 | 3 | 0.823 ± 0.026 | 0.649 ± 0.018 | 0.726 ± 0.026 | 0.381 ± 0.017 |
| run14 offlineflip | v3 no-parked | 320 | 5 | 0.812 ± 0.070 | 0.686 ± 0.060 | 0.748 ± 0.021 | 0.386 ± 0.024 |
| run15 stride2 | v3 no-parked | 320 | 3 | 0.780 ± 0.028 | 0.606 ± 0.034 | 0.673 ± 0.032 | 0.364 ± 0.014 |
| run16 stride4 | v3 no-parked | 320 | 3 | 0.718 ± 0.109 | 0.579 ± 0.040 | 0.631 ± 0.010 | 0.329 ± 0.003 |
| run17 stride2_matched | v3 no-parked | 320 | 3 | 0.702 ± 0.012 | 0.636 ± 0.016 | 0.673 ± 0.015 | 0.342 ± 0.004 |
| run18 stride4_matched | v3 no-parked | 320 | 3 | 0.755 ± 0.097 | 0.565 ± 0.033 | 0.625 ± 0.004 | 0.317 ± 0.015 |
| run19 flip_hsv | v3 dynamic 2-class + HSV | 320 | 3 | 0.775 ± 0.024 | 0.706 ± 0.017 | 0.749 ± 0.009 | 0.393 ± 0.003 |
| run20 flip_box9dfl2 | v3 dynamic 2-class + box/dfl | 320 | 3 | 0.768 ± 0.062 | 0.701 ± 0.015 | 0.742 ± 0.026 | 0.389 ± 0.011 |
| run21 flip_mosaic_close25 | v3 dynamic 2-class + mosaic | 320 | 3 | 0.847 ± 0.032 | 0.794 ± 0.064 | 0.863 ± 0.015 | 0.450 ± 0.012 |
| run22 flip_nostop | v3 dynamic 2-class + no early stop (control) | 320 | 3 | 0.785 ± 0.074 | 0.695 ± 0.013 | 0.742 ± 0.016 | 0.374 ± 0.008 |
| run23 flip_mosaic_noclose | v3 dynamic 2-class + mosaic, never closed | 320 | 5 | 0.858 ± 0.056 | 0.785 ± 0.052 | 0.864 ± 0.014 | 0.454 ± 0.010 |
| run24 flip_mosaic_scale | v3 dynamic 2-class + mosaic + scale/translate | 320 | 5 | 0.898 ± 0.019 | 0.845 ± 0.016 | 0.912 ± 0.011 | 0.505 ± 0.017 |
| run25 flip_mosaic_scale09 | v3 dynamic 2-class + mosaic + scale 0.9 | 320 | 3 | 0.900 ± 0.014 | 0.832 ± 0.034 | 0.909 ± 0.007 | 0.529 ± 0.017 |
