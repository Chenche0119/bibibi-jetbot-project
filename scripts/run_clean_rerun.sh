#!/bin/bash
# Re-run run20s2 and run21s1 outside ~/Desktop (iCloud-synced), then copy results back.
# Usage: PYTHON=<python> scripts/run_clean_rerun.sh
set -u
PY="${PYTHON:-python3}"
SRC="$(cd "$(dirname "$0")/../colab_runs/local" && pwd)"
T="$HOME/yolo_tmp/local"
mkdir -p "$T/work" && ln -sfn "$(readlink "$SRC/work/v3_raw" || echo "$SRC/work/v3_raw")" "$T/work/v3_raw"
for pat in YOLOv8n_run20s2_*.py YOLOv8n_run21s1_*.py; do
  f=$(cd "$SRC" && ls $pat); cp "$SRC/$f" "$T/$f"
  run=$(grep -m1 "^RUN = " "$T/$f" | sed "s/RUN = '\(.*\)'/\1/")
  echo "START $run $(date +%H:%M:%S)"
  (cd "$T" && "$PY" -I "$f") 2>&1 | tr '\r' '\n' | grep -v "Scanning\|it/s\|s/it" | tee "$T/$run.log"
  echo "END $run $(date +%H:%M:%S)"
  d="$T/work/data2_$run"
  echo "counts train=$(ls $d/train/images|wc -l) valid=$(ls $d/valid/images|wc -l) test=$(ls $d/test/images|wc -l) (expect 1328/135/142)"
  [ -d "$T/results/$run" ] && rm -rf "$SRC/results/$run" && cp -R "$T/results/$run" "$SRC/results/$run"
done
