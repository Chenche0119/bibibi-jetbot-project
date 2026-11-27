"""Summarize gate_test results from local run logs (run26 / run27 style).

Usage: python3 summarize_gate_test.py [logs_dir] [--md] [--baseline runNN]

The v4 run scripts print the held-out gate_test evaluation only to stdout, so results.csv
does not hold it. This script reads <logs_dir>/run*.log and pulls, per run:
  - test mAP50 (original v3 test split, line starting with "mAP50 ")
  - gate_test mAP50 / P / R at conf=0.001 and conf=0.25, plus person AP50 at conf=0.001
Runs are grouped by name with the seed suffix (sN) removed and reported as mean / std.
With --baseline runNN, every other group also gets, per metric, the delta against that group and a
verdict ('above noise' only when |delta| > 2 x baseline std, the same rule as summarize_colab_runs).
gate_test is evaluation-only: it must not be used to pick epochs, conf thresholds or seeds.
"""
import glob, os, re, statistics as st, sys
from collections import defaultdict

TEST_RE = re.compile(r"^mAP50 ([\d.]+) mAP50-95 ([\d.]+) P ([\d.]+) R ([\d.]+)")
GATE_RE = re.compile(r"^gate_test conf=([\d.]+): mAP50 ([\d.]+) mAP50-95 ([\d.]+) P ([\d.]+) R ([\d.]+)")
CLASS_RE = re.compile(r"^\s+(\S+) AP50 ([\d.]+) P ([\d.]+) R ([\d.]+)")


def parse_log(text):
    """Return a dict of metrics parsed from one run log (progress bars use \\r, so split on both)."""
    out, conf = {}, None
    for line in re.split(r"[\r\n]+", text):
        if m := TEST_RE.match(line):
            out["test_mAP50"] = float(m.group(1))
        elif m := GATE_RE.match(line):
            conf = m.group(1)
            out[f"gate{conf}_mAP50"] = float(m.group(2))
            out[f"gate{conf}_P"] = float(m.group(4))
            out[f"gate{conf}_R"] = float(m.group(5))
        elif conf and (m := CLASS_RE.match(line)):
            out[f"gate{conf}_{m.group(1)}_AP50"] = float(m.group(2))
        elif line.strip() and not line.startswith(" "):
            conf = None  # per-class lines only follow their gate_test header
    return out


def group_name(run_id):
    m = re.match(r"run(\d+)(?:s\d+)?$", run_id)
    return f"run{m.group(1)}" if m else run_id


def summarize(runs, keys):
    """runs: {run_id: metrics}. Return {group: {key: (mean, std or None, n)}}."""
    groups = defaultdict(lambda: defaultdict(list))
    for run_id, metrics in runs.items():
        for k in keys:
            if k in metrics:
                groups[group_name(run_id)][k].append(metrics[k])
    return {g: {k: (st.mean(v), st.stdev(v) if len(v) > 1 else None, len(v)) for k, v in d.items()}
            for g, d in groups.items()}


KEYS = ["test_mAP50", "gate0.001_mAP50", "gate0.001_person_AP50", "gate0.25_mAP50", "gate0.25_R"]


def to_markdown(summary, keys=KEYS):
    lines = ["| group | n | " + " | ".join(keys) + " |", "|---|---|" + "---|" * len(keys)]
    for g, d in sorted(summary.items()):
        n = max(v[2] for v in d.values()) if d else 0
        cells = []
        for k in keys:
            if k not in d:
                cells.append("-")
            else:
                mean, std, _ = d[k]
                cells.append(f"{mean:.3f}" + (f" ± {std:.3f}" if std is not None else ""))
        lines.append(f"| {g} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def compare_to_baseline(summary, baseline, keys=KEYS):
    """Return {group: {key: (delta, verdict)}} for every group other than baseline."""
    base = summary.get(baseline)
    if base is None:
        raise KeyError(f"baseline group {baseline!r} not found; groups: {sorted(summary)}")
    out = {}
    for g, d in summary.items():
        if g == baseline:
            continue
        row = {}
        for k in keys:
            if k not in d or k not in base:
                continue
            delta = d[k][0] - base[k][0]
            b_std = base[k][1]
            if b_std is None:
                verdict = "no baseline std"
            else:
                verdict = "above noise" if abs(delta) > 2 * b_std else "within noise"
            row[k] = (delta, verdict)
        out[g] = row
    return out


def comparison_markdown(comparison, baseline, keys=KEYS):
    lines = [f"| group vs {baseline} | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for g, row in sorted(comparison.items()):
        cells = [f"{row[k][0]:+.3f} ({row[k][1]})" if k in row else "-" for k in keys]
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def parse_args(argv):
    md, baseline, rest = False, None, []
    it = iter(argv)
    for a in it:
        if a == "--md":
            md = True
        elif a == "--baseline":
            baseline = next(it, None)
            if baseline is None:
                sys.exit("--baseline needs a group name, e.g. --baseline run26")
        else:
            rest.append(a)
    return md, baseline, rest


def main():
    md, baseline, args = parse_args(sys.argv[1:])
    root = args[0] if args else os.path.expanduser("~/yolo_tmp/v4/logs")
    runs = {}
    for path in sorted(glob.glob(os.path.join(root, "run*.log"))):
        with open(path, encoding="utf-8", errors="replace") as fh:
            metrics = parse_log(fh.read())
        if metrics:
            runs[os.path.splitext(os.path.basename(path))[0]] = metrics
    for run_id, m in runs.items():
        print(run_id, *(f"{k}={m[k]:.3f}" for k in KEYS if k in m), sep="\t")
    summary = summarize(runs, KEYS)
    if md:
        print(to_markdown(summary))
    if baseline:
        comparison = compare_to_baseline(summary, baseline)
        if md:
            print()
            print(comparison_markdown(comparison, baseline))
        else:
            for g, row in sorted(comparison.items()):
                print(f"{g} vs {baseline}", *(f"{k}={d:+.3f} {v}" for k, (d, v) in row.items()), sep="\t")
    print(f"{len(runs)} run logs parsed from {root}")


if __name__ == "__main__":
    main()
