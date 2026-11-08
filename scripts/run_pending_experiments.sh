#!/bin/bash
# Run the run19 / run20 / run21 experiments one after another, skipping the finished ones.
# Usage: PYTHON=/path/to/python scripts/run_pending_experiments.sh
# A run counts as finished when results/<run>/runs/<run>_test exists (written by the final test eval).
# An interrupted run resumes from its last.pt (the training script does this itself).
# PYTHON needs ultralytics==8.4.183 and roboflow; ROBOFLOW_API_KEY is only used if v3_raw is missing
# (work/v3_raw is expected to exist already; see colab_runs/local/work).
set -u
PY="${PYTHON:-python3}"
cd "$(dirname "$0")/../colab_runs/local" || exit 1
for f in YOLOv8n_run20s1_*.py YOLOv8n_run20s2_*.py YOLOv8n_run19_*.py YOLOv8n_run19s1_*.py YOLOv8n_run19s2_*.py \
         YOLOv8n_run21_*.py YOLOv8n_run21s1_*.py YOLOv8n_run21s2_*.py; do
  run=$(grep -m1 "^RUN = " "$f" | sed "s/RUN = '\(.*\)'/\1/")
  if [ -d "results/$run/runs/${run}_test" ]; then echo "skip $run (done)"; continue; fi
  echo "START $run $(date +%H:%M:%S)"
  "$PY" -I "$f" > "/tmp/${run}.log" 2>&1
  echo "END $run exit=$? $(date +%H:%M:%S)"
done
