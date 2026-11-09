"""Redraw the run comparison charts and tables (val metrics at each run's best epoch).

Usage: python3 -I make_run_charts.py <charts_dir> <results_dir> [<results_dir> ...]

Keeps every row already in <charts_dir>/per_run_table.csv (rows for runs whose results are not
in the given results dirs are kept as-is), then adds / refreshes rows for runs found under the
results dirs whose run_id is listed in NEW_RUNS. Rewrites per_run_table.csv, group_table.csv,
group_table.md and the four chart_*.png files. Best epoch = max mAP50(B); std is the sample std.
"""
import csv, glob, os, re, statistics as st, sys
from collections import OrderedDict, defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Hiragino Sans", "PingFang HK", "Arial Unicode MS", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

charts = sys.argv[1]
roots = sys.argv[2:]
# runs added on top of the existing table: prefix -> dataset key (variant = run name minus the v3... prefix)
NEW_RUNS = {"run01": "v2raw5class", "run02": "v2dynamic2class", "run03": "v2dynamic2class",
            "run12": "v3dynamic2class", "run14": "v3noparked", "run15": "v3noparked", "run16": "v3noparked", "run17": "v3noparked", "run18": "v3noparked",
            "run19": "v3dynamic2class_hsv", "run20": "v3dynamic2class_loss",
            "run21": "v3dynamic2class_mosaic", "run22": "v3dynamic2class_nostop", "run23": "v3dynamic2class_mosaicnoclose", "run24": "v3dynamic2class_mosaicscale"}
DATASETS = OrderedDict([
    ("v2raw5class", ("v2 raw 5-class (not comparable)", "#bab0ac")),
    ("v2dynamic2class", ("v2 dynamic 2-class (older data)", "#9c755f")),
    ("v3dynamic2class", ("v3 dynamic 2-class", "#4c78a8")),
    ("v3noparked", ("v3 no-parked", "#f58518")),
    ("v3dynamic2class_hsv", ("v3 dynamic 2-class + HSV (this round)", "#59a14f")),
    ("v3dynamic2class_loss", ("v3 dynamic 2-class + box/dfl tweak (this round)", "#7b4fa3")),
    ("v3dynamic2class_mosaic", ("v3 dynamic 2-class + mosaic/close_mosaic (this round)", "#e15759")),
    ("v3dynamic2class_nostop", ("v3 dynamic 2-class + no early stop, mosaic off (control)", "#edc948")),
    ("v3dynamic2class_mosaicnoclose", ("v3 dynamic 2-class + mosaic, never closed", "#b07aa1")),
    ("v3dynamic2class_mosaicscale", ("v3 dynamic 2-class + mosaic + scale/translate", "#76b7b2")),
])
FIELDS = ["run_id", "group", "dataset", "variant", "seed", "imgsz", "epochs_run", "best_epoch",
          "precision", "recall", "mAP50", "mAP50-95", "val_cls_loss"]


def read_results(path):
    with open(path, encoding="utf-8") as fh:
        return [{k.strip(): v.strip() for k, v in x.items()} for x in csv.DictReader(fh)]


def curve_path(run_id):
    for r in roots:
        hits = glob.glob(os.path.join(r, "*", "runs", run_id, "results.csv"))
        if hits:
            return hits[0]
    return None


rows = OrderedDict()
with open(os.path.join(charts, "per_run_table.csv"), encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        rows[r["run_id"]] = r
for root in roots:
    for path in sorted(glob.glob(os.path.join(root, "*", "runs", "*", "results.csv"))):
        run_id = os.path.basename(os.path.dirname(path))
        m = re.match(r"run(\d+)(?:s(\d+))?_(.+?)(?:_seed\d+)?$", run_id)
        if not m or f"run{m.group(1)}" not in NEW_RUNS:
            continue
        res = read_results(path)
        best = max(res, key=lambda x: float(x["metrics/mAP50(B)"]))
        ds = NEW_RUNS[f"run{m.group(1)}"]
        variant = re.sub(r"^v\d(?:dynamic2class|noparked|raw5class)_", "", m.group(3))
        imgsz = "320"
        ap = os.path.join(os.path.dirname(path), "args.yaml")
        if os.path.exists(ap):
            hit = re.search(r"^imgsz:\s*(\d+)", open(ap, encoding="utf-8").read(), re.M)
            imgsz = hit.group(1) if hit else imgsz
        rows[run_id] = {
            "run_id": run_id, "group": f"run{m.group(1)} {variant}", "dataset": ds, "variant": variant,
            "seed": m.group(2) or "0", "imgsz": imgsz, "epochs_run": len(res), "best_epoch": int(best["epoch"]),
            "precision": f'{float(best["metrics/precision(B)"]):.4f}', "recall": f'{float(best["metrics/recall(B)"]):.4f}',
            "mAP50": f'{float(best["metrics/mAP50(B)"]):.4f}', "mAP50-95": f'{float(best["metrics/mAP50-95(B)"]):.4f}',
            "val_cls_loss": f'{float(best["val/cls_loss"]):.4f}'}

rows = OrderedDict(sorted(rows.items(), key=lambda kv: (int(re.match(r"run(\d+)", kv[0]).group(1)), int(kv[1]["seed"]))))
with open(os.path.join(charts, "per_run_table.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(rows.values())

groups = OrderedDict()
for r in rows.values():
    groups.setdefault(r["group"], []).append(r)


def ms(vals):
    return (st.mean(vals), st.stdev(vals) if len(vals) > 1 else None)


G = []
for g, rs in groups.items():
    d = {"group": g, "n": len(rs), "dataset": rs[0]["dataset"], "imgsz": rs[0]["imgsz"]}
    for k in ("precision", "recall", "mAP50", "mAP50-95"):
        d[k] = ms([float(x[k]) for x in rs])
    d["best_epoch"] = st.mean(float(x["best_epoch"]) for x in rs)
    G.append(d)

with open(os.path.join(charts, "group_table.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["group", "n_seeds", "dataset", "imgsz", "precision_mean", "precision_std", "recall_mean", "recall_std",
                "mAP50_mean", "mAP50_std", "mAP50-95_mean", "mAP50-95_std", "best_epoch_mean"])
    for d in G:
        row = [d["group"], d["n"], d["dataset"], d["imgsz"]]
        for k in ("precision", "recall", "mAP50", "mAP50-95"):
            row += [round(d[k][0], 4), "" if d[k][1] is None else round(d[k][1], 4)]
        w.writerow(row + [round(d["best_epoch"], 4)])

LABEL = {"v2raw5class": "v2 raw 5-class", "v2dynamic2class": "v2 dynamic 2-class", "v3dynamic2class": "v3 dynamic 2-class", "v3noparked": "v3 no-parked", "v3dynamic2class_loss": "v3 dynamic 2-class + box/dfl", "v3dynamic2class_hsv": "v3 dynamic 2-class + HSV", "v3dynamic2class_mosaic": "v3 dynamic 2-class + mosaic", "v3dynamic2class_nostop": "v3 dynamic 2-class + no early stop (control)", "v3dynamic2class_mosaicnoclose": "v3 dynamic 2-class + mosaic, never closed", "v3dynamic2class_mosaicscale": "v3 dynamic 2-class + mosaic + scale/translate"}


def fmt(t):
    return f"{t[0]:.3f}" if t[1] is None else f"{t[0]:.3f} ± {t[1]:.3f}"


with open(os.path.join(charts, "group_table.md"), "w", encoding="utf-8") as fh:
    fh.write("| Group | Dataset | imgsz | Seeds | Precision | Recall | mAP50 | mAP50-95 |\n|---|---|---|---|---|---|---|---|\n")
    for d in G:
        fh.write(f'| {d["group"]} | {LABEL[d["dataset"]]} | {d["imgsz"]} | {d["n"]} | ' +
                 " | ".join(fmt(d[k]) for k in ("precision", "recall", "mAP50", "mAP50-95")) + " |\n")


def color(ds):
    return DATASETS[ds][1]


def xlabel(g):
    a, b = g.split(" ", 1)
    return f"{a}\n{b}".replace("_", "\n")


x = list(range(len(G)))
# 1) mAP by group
fig, axs = plt.subplots(2, 1, figsize=(10.4, 6.4), dpi=200, sharex=True)
for ax, k, lab, lo, hi in ((axs[0], "mAP50", "mAP50 (val)", 0.58, 1.0), (axs[1], "mAP50-95", "mAP50-95 (val)", 0.24, 0.56)):
    for i, d in enumerate(G):
        ax.bar(i, d[k][0], color=color(d["dataset"]), width=0.8, zorder=2)
        if d[k][1] is not None:
            ax.errorbar(i, d[k][0], yerr=d[k][1], color="k", capsize=3, lw=1.5, zorder=3)
        for r in groups[d["group"]]:
            ax.plot(i, float(r[k]), "o", color="k", ms=3.5, alpha=0.7, zorder=4)
    ax.set_ylim(lo, hi); ax.set_ylabel(lab); ax.grid(axis="y", alpha=0.3, zorder=0)
axs[0].set_title("Model comparison: best-epoch validation metrics (bar = seed mean, whisker = std, dot = seed)", pad=34)
axs[1].set_xticks(x); axs[1].set_xticklabels([xlabel(d["group"]).replace("offline", "offline\n") for d in G], fontsize=6.5)
used = [k for k in DATASETS if any(d["dataset"] == k for d in G)]
axs[0].legend(handles=[plt.Rectangle((0, 0), 1, 1, color=color(k)) for k in used], labels=[DATASETS[k][0] for k in used], loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=7, ncol=3)
fig.tight_layout(); fig.savefig(os.path.join(charts, "chart_map_by_group.png")); plt.close(fig)

# 2) precision / recall
fig, ax = plt.subplots(figsize=(10.4, 4), dpi=200)
for i, d in enumerate(G):
    for off, k, c in ((-0.2, "precision", "#59a14f"), (0.2, "recall", "#e15759")):
        ax.bar(i + off, d[k][0], width=0.4, color=c, zorder=2)
        if d[k][1] is not None:
            ax.errorbar(i + off, d[k][0], yerr=d[k][1], color="k", capsize=2, lw=1.5, zorder=3)
ax.set_ylim(0.48, 1.0); ax.grid(axis="y", alpha=0.3, zorder=0)
ax.set_xticks(x); ax.set_xticklabels([xlabel(d["group"]).replace("offline", "offline\n") for d in G], fontsize=6.5)
ax.set_title("Precision vs Recall by group (val, best epoch)")
ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color="#59a14f"), plt.Rectangle((0, 0), 1, 1, color="#e15759")], labels=["Precision", "Recall"], loc="upper right")
fig.tight_layout(); fig.savefig(os.path.join(charts, "chart_precision_recall.png")); plt.close(fig)

# 3) scatter, every run
fig, ax = plt.subplots(figsize=(5.6, 4.9), dpi=200)
for ds in DATASETS:
    pts = [r for r in rows.values() if r["dataset"] == ds]
    if pts:
        ax.scatter([float(r["mAP50"]) for r in pts], [float(r["mAP50-95"]) for r in pts], s=28, color=color(ds), label=DATASETS[ds][0], zorder=3)
        for r in pts:
            ax.annotate(r["run_id"].split("_")[0], (float(r["mAP50"]), float(r["mAP50-95"])), xytext=(3, 3), textcoords="offset points", fontsize=4.5)
ax.set_xlabel("mAP50"); ax.set_ylabel("mAP50-95"); ax.grid(alpha=0.3)
ax.set_title("Every run: mAP50 vs mAP50-95 (val)", fontsize=9); ax.legend(fontsize=6, loc="upper left")
fig.tight_layout(); fig.savefig(os.path.join(charts, "chart_scatter_runs.png")); plt.close(fig)

# 4) learning curves: mean over seeds still training, per dataset panel
fig, axs = plt.subplots(1, 3, figsize=(16.8, 4), dpi=200, sharey=True)
panels = [("v2 (older data)", ["v2raw5class", "v2dynamic2class"]), ("v3 dynamic 2-class", ["v3dynamic2class", "v3dynamic2class_hsv", "v3dynamic2class_loss", "v3dynamic2class_mosaic", "v3dynamic2class_nostop", "v3dynamic2class_mosaicnoclose", "v3dynamic2class_mosaicscale"]), ("v3 no-parked", ["v3noparked"])]
for ax, (title, dss) in zip(axs, panels):
    n = 0
    for d in G:
        if d["dataset"] not in dss:
            continue
        curves = []
        for r in groups[d["group"]]:
            p = curve_path(r["run_id"])
            if p:
                curves.append([float(x["metrics/mAP50(B)"]) for x in read_results(p)])
        if not curves:
            continue
        L = max(len(c) for c in curves)
        ys = [st.mean([c[e] for c in curves if len(c) > e]) for e in range(L)]
        special = {"v3dynamic2class_loss": ("k", 1.8), "v3dynamic2class_hsv": ("#59a14f", 1.8), "v3dynamic2class_mosaic": ("#e15759", 1.8), "v3dynamic2class_nostop": ("#edc948", 1.8), "v3dynamic2class_mosaicnoclose": ("#b07aa1", 1.8), "v3dynamic2class_mosaicscale": ("#76b7b2", 1.8)}
        col, lw = special.get(d["dataset"], (f"C{n % 10}", 1.2))
        ax.plot(range(1, L + 1), ys, color=col, lw=lw, label=d["group"], zorder=4)
        n += 1
    ax.set_title(f"val mAP50 curve - {title}", fontsize=10); ax.set_xlabel("epoch"); ax.grid(alpha=0.3)
    ax.legend(fontsize=5.5)
axs[0].set_ylabel("mAP50 (mean over seeds still training)")
fig.tight_layout(); fig.savefig(os.path.join(charts, "chart_learning_curves.png")); plt.close(fig)
print("rows", len(rows), "groups", len(G))
