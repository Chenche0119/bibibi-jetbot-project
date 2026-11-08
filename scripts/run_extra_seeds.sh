#!/bin/bash
# Run run12 / run14 seed3 and seed4 outside ~/Desktop (iCloud-synced), copy results back, check dataset sizes.
# Usage: [PATS="YOLOv8n_runXX_*.py ..."] PYTHON=<python> scripts/run_extra_seeds.sh  (PATS defaults to run12/run14 seed3,4)
set -u
PY="${PYTHON:-python3}"
SRC="$(cd "$(dirname "$0")/../colab_runs/local" && pwd)"
MAIN="$HOME/yolo_tmp/main_work"   # symlink to the main checkout's colab_runs/local/work (v3_raw, v3_noparked)
T="$HOME/yolo_tmp/local"
mkdir -p "$T/work" "$SRC/results"
ln -sfn "$MAIN/v3_raw" "$T/work/v3_raw"; ln -sfn "$MAIN/v3_noparked" "$T/work/v3_noparked"
for pat in ${PATS:-YOLOv8n_run12s3_*.py YOLOv8n_run12s4_*.py YOLOv8n_run14s3_*.py YOLOv8n_run14s4_*.py}; do
  f=$(cd "$SRC" && ls $pat); cp "$SRC/$f" "$T/$f"
  run=$(grep -m1 "^RUN = " "$T/$f" | sed "s/RUN = '\(.*\)'/\1/")
  if [ -d "$SRC/results/$run/runs/${run}_test" ]; then echo "skip $run (done)"; continue; fi
  echo "START $run $(date +%H:%M:%S)"
  (cd "$T" && "$PY" -I "$f") 2>&1 | tr '\r' '\n' | grep -v "Scanning\|it/s\|s/it\|deterministic implementation" | tee "$T/$run.log"
  d="$T/work/data2_$run"
  echo "END $run $(date +%H:%M:%S) | counts train=$(ls $d/train/images|wc -l) valid=$(ls $d/valid/images|wc -l) test=$(ls $d/test/images|wc -l)"
  [ -d "$T/results/$run" ] && rm -rf "$SRC/results/$run" && cp -R "$T/results/$run" "$SRC/results/$run"
done
echo ALL DONE
