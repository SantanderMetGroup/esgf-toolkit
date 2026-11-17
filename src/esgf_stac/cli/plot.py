#!/usr/bin/env python3
"""Heatmap of local downloads (model x variable temporal coverage); --compare adds completeness."""

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict

import requests

from esgf_stac import NODES, iter_items, load_health, parse_filter_values
from esgf_stac.cli.catalog import fmt_row
from esgf_stac.cli.common import add_health_arg, add_node_arg, add_out_arg, union_fieldnames
from esgf_stac.local import scan_local

DEFAULT_CSV = "./catalog.csv"
DEFAULT_CSV_DIR = "./catalog"


def read_csv_rows(csv_path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(csv_path, rows):
    if not rows:
        return
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=union_fieldnames(rows))
        writer.writeheader()
        writer.writerows(rows)


def read_local_rows(csv_dir: str) -> list:
    rows = []
    for path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
        rows.extend(read_csv_rows(path))
    return rows


def _is_downloaded(row: dict) -> bool:
    """True when a row has local data (comparison row or local catalogue row)."""
    if "files_local" in row:
        try:
            return int(float(row.get("files_local") or 0)) > 0
        except (TypeError, ValueError):
            return False
    if "n_files" in row:  # catalogues written before time_period existed
        try:
            return int(float(row.get("n_files") or 0)) > 0
        except (TypeError, ValueError):
            return False
    if str(row.get("time_period") or "").strip():
        return True
    try:
        return float(row.get("size_bytes") or 0) > 0
    except (TypeError, ValueError):
        return False


def filter_downloaded_variables(rows: list) -> list:
    """Keep every row whose variable has at least one downloaded file.

    Missing datasets of a downloaded variable are kept so completeness is shown.
    """
    keep = {r.get("variable") or "?" for r in rows if _is_downloaded(r)}
    return [r for r in rows if (r.get("variable") or "?") in keep]


DOMAIN_ALIASES = ("domain", "domain_id", "grid", "grid_label")


def _cell_values(row: dict, column: str) -> set:
    """Values of a filter column, following domain/grid aliases across catalogue layouts."""
    columns = DOMAIN_ALIASES if column in ("domain", "grid") else (column,)
    values = set()
    for col in columns:
        for part in str(row.get(col) or "").split(","):
            part = part.strip()
            if part:
                values.add(part)
    return values


def filter_rows(rows, collection: str | None, filters: dict) -> list:
    active = [(col, set(parse_filter_values(raw))) for col, raw in filters.items()
              if parse_filter_values(raw)]
    out = []
    for r in rows:
        if collection and r.get("collection") != collection:
            continue
        ok = True
        for col, vals in active:
            if not (_cell_values(r, col) & vals):
                ok = False
                break
        if ok:
            out.append(r)
    return out


def compute_rows(args):
    node = NODES[args.node]
    filters = {"model": args.model, "experiment": args.experiment, "variable": args.variable,
               "frequency": args.frequency, "grid": args.grid, "member": args.member,
               "domain": args.domain, "activity": getattr(args, "activity", None)}
    sizes, base = scan_local(args.out)
    _, statuses = load_health(args.health)
    return [r for item in iter_items(node, args.collection, filters)
            if (r := fmt_row(item, args.out, sizes, base, statuses, show_missing_only=False))]


COVERAGE_METRICS = ("coverage", "files", "time")  # "files"/"time": legacy aliases

_YM_RE = re.compile(r"(\d{4})-?(\d{2})")


def _month_index(ym: str):
    m = _YM_RE.match(str(ym or "").strip())
    return int(m.group(1)) * 12 + int(m.group(2)) - 1 if m else None


def row_interval(row: dict):
    """(first, last) month index covered by a local catalogue row, or None."""
    s, e = _month_index(row.get("start")), _month_index(row.get("end"))
    if s is None or e is None:
        found = _YM_RE.findall(str(row.get("time_period") or ""))
        if len(found) < 2:
            return None
        s = int(found[0][0]) * 12 + int(found[0][1]) - 1
        e = int(found[1][0]) * 12 + int(found[1][1]) - 1
    return (s, e) if e >= s else None


def union_months(intervals) -> int:
    """Months covered by the union of inclusive (first, last) month intervals.

    Overlapping runs (other experiments, members, domains) are counted once,
    so a cell never reports more years than the calendar actually spans.
    """
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e + 1:
            if cur_e is not None:
                total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s + 1
    return total


def _fmt_years(months: int) -> str:
    return f"{months // 12}y" if months % 12 == 0 else f"{months / 12:.1f}y"


def aggregate(rows, metric: str = "status"):
    """Per-frequency model x variable grids.

    Returns {frequency: (labels, variables, score, annotate)}.
    - "status": score = completeness in [0, 1], annotation "complete/total".
    - "coverage" (local catalogue): score = years covered by the *union* of the
      datasets' periods, annotation "<years> (<n datasets>)". Frequencies without
      time info (fx) score the dataset count instead.
    """
    import numpy as np

    coverage = metric in COVERAGE_METRICS
    multi = len({r.get("collection") or "?" for r in rows}) > 1
    result = {}
    for freq in sorted({r.get("frequency") or "?" for r in rows}):
        buckets = defaultdict(lambda: {"n": 0, "complete": 0, "partial": 0, "missing": 0,
                                       "intervals": []})
        for r in rows:
            if (r.get("frequency") or "?") != freq:
                continue
            coll, model, var = r.get("collection") or "?", r.get("model") or "?", r.get("variable") or "?"
            b = buckets[(f"{coll} · {model}" if multi else model, var)]
            b["n"] += 1
            if coverage:
                iv = row_interval(r)
                if iv:
                    b["intervals"].append(iv)
            else:
                status = r.get("status") or "missing"
                b[status if status in ("complete", "partial", "missing") else "missing"] += 1
        labels = sorted({k[0] for k in buckets})
        variables = sorted({k[1] for k in buckets})
        li, vi = {x: i for i, x in enumerate(labels)}, {x: i for i, x in enumerate(variables)}
        score = np.full((len(labels), len(variables)), np.nan)
        annotate = np.zeros((len(labels), len(variables)), dtype=object)
        for (lab, var), b in buckets.items():
            i, j = li[lab], vi[var]
            if coverage and b["intervals"]:
                months = union_months(b["intervals"])
                score[i, j] = months / 12
                annotate[i, j] = f"{_fmt_years(months)} ({b['n']})"
            elif coverage:
                score[i, j] = b["n"]
                annotate[i, j] = str(b["n"])
            else:
                score[i, j] = (b["complete"] + 0.5 * b["partial"]) / b["n"]
                annotate[i, j] = f"{b['complete']}/{b['n']}"
        result[freq] = (labels, variables, score, annotate)
    return result


def _coverage_panel_title(freq: str, freq_rows: list) -> tuple[str, str]:
    """(subplot title, colorbar label) for a local-coverage panel."""
    n = len(freq_rows)
    if any(row_interval(r) for r in freq_rows):
        first = min(iv[0] for r in freq_rows if (iv := row_interval(r)))
        last = max(iv[1] for r in freq_rows if (iv := row_interval(r)))
        return (f"{freq} ({n} datasets, {first // 12}–{last // 12})",
                "years covered (union of datasets)")
    return f"{freq} ({n} datasets, no time axis)", "datasets"


def plot(rows, title_extra, out_path, metric: str = "status"):
    import matplotlib.pyplot as plt
    import numpy as np

    if not rows:
        print("No dataset rows to plot.", file=sys.stderr)
        sys.exit(1)
    coverage = metric in COVERAGE_METRICS
    freq_data = aggregate(rows, metric)
    n = len(rows)
    n_freqs = len(freq_data)
    n_cols = min(2, n_freqs)
    n_rows = (n_freqs + n_cols - 1) // n_cols
    max_cols = max(len(v[1]) for v in freq_data.values())
    max_rows = max(len(v[0]) for v in freq_data.values())
    fig_w = max(8.0, min(28.0, max_cols * 0.7 * n_cols + 4.0))
    fig_h = max(4.0, min(48.0, max_rows * 0.35 * n_rows + 3.0))
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(fig_w, fig_h),
                             constrained_layout=True, squeeze=False)
    for idx, (freq, (labels, variables, score, annotate)) in enumerate(sorted(freq_data.items())):
        ax = axes[idx // n_cols][idx % n_cols]
        n_rows_f, n_cols_f = score.shape
        freq_rows = [r for r in rows if (r.get("frequency") or "?") == freq]
        if coverage:
            cmap = plt.get_cmap("Blues")
            vmax = float(np.nanmax(score)) if np.isfinite(score).any() else 1.0
            vmax = vmax if vmax > 0 else 1.0
            subplot_title, cbar_label = _coverage_panel_title(freq, freq_rows)
            text_color = lambda v, vmax=vmax: "white" if v > 0.5 * vmax else "#212121"
        else:
            cmap = plt.get_cmap("YlGn")
            vmax = 1.0
            cbar_label = "completeness"
            nc = sum(1 for r in freq_rows if r.get("status") == "complete")
            np_ = sum(1 for r in freq_rows if r.get("status") == "partial")
            nm = sum(1 for r in freq_rows if r.get("status") == "missing")
            text_color = lambda v: "#212121" if v < 0.5 else "white"
            subplot_title = f"{freq} ({nc} complete, {np_} partial, {nm} missing)"
        cmap = cmap.with_extremes(bad="#eceff1")
        im = ax.imshow(score, cmap=cmap, vmin=0.0, vmax=vmax, aspect="auto", interpolation="nearest")
        ax.set_xticks(range(n_cols_f), labels=variables, rotation=90, fontsize=8)
        ax.set_yticks(range(n_rows_f), labels=labels, fontsize=8)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        if n_rows_f * n_cols_f <= 400:
            for i in range(n_rows_f):
                for j in range(n_cols_f):
                    if not np.isnan(score[i, j]):
                        ax.text(j, i, annotate[i, j], ha="center", va="center", fontsize=6,
                                color=text_color(float(score[i, j])))
        ax.set_title(subplot_title, fontsize=10)
        cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
        cbar.set_label(cbar_label, fontsize=8)
        cbar.ax.tick_params(labelsize=7)
    for idx in range(n_freqs, n_rows * n_cols):
        axes[idx // n_cols][idx % n_cols].set_visible(False)
    if coverage:
        size_tb = sum(float(r.get("size_bytes") or 0) for r in rows) / 1e12
        fig.suptitle(f"ESGF local holdings — temporal coverage (model x variable)\n{title_extra}"
                     f"{n} datasets   —   {size_tb:.2f} TB   —   cell: years covered (n datasets)",
                     fontsize=12, x=0.01, ha="left")
        summary = f"{n} datasets, {size_tb:.2f} TB"
    else:
        nc_all = sum(1 for r in rows if r.get("status") == "complete")
        np_all = sum(1 for r in rows if r.get("status") == "partial")
        nm_all = sum(1 for r in rows if r.get("status") == "missing")
        fig.suptitle(f"ESGF Dataset Download Status\n{title_extra}"
                     f"{n} datasets   —   {nc_all} complete    {np_all} partial    {nm_all} missing",
                     fontsize=12, x=0.01, ha="left")
        summary = f"{n} datasets: {nc_all} complete, {np_all} partial, {nm_all} missing"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_path} ({summary})")


def main():
    import matplotlib
    matplotlib.use("Agg")
    ap = argparse.ArgumentParser(description=__doc__)
    add_node_arg(ap)
    ap.add_argument("--collection", help="Filter by collection (e.g. CMIP7)")
    ap.add_argument("--model", help="Model / source_id, comma-separated")
    ap.add_argument("--experiment", help="Experiment / scenario, comma-separated")
    ap.add_argument("--variable", help="Variable_id, comma-separated")
    ap.add_argument("--frequency", help="Frequency, comma-separated")
    ap.add_argument("--grid", help="Grid label / domain_id, comma-separated (e.g. gr, EUR-12)")
    ap.add_argument("--member", help="Variant/member label, comma-separated")
    ap.add_argument("--domain", help="Domain/realm, comma-separated (e.g. EUR-12, atmos)")
    ap.add_argument("--activity", help="Activity_id, comma-separated")
    add_out_arg(ap, "Local download dir to scan (--compare --refresh)")
    ap.add_argument("--csv-dir", default=DEFAULT_CSV_DIR,
                    help=f"Local per-collection CSVs to plot (default: {DEFAULT_CSV_DIR})")
    ap.add_argument("--csv", default="", help=f"Comparison CSV for --compare (default: {DEFAULT_CSV})")
    ap.add_argument("--compare", action="store_true",
                    help="Plot local-vs-STAC completeness from the comparison catalogue")
    ap.add_argument("--refresh", action="store_true",
                    help="With --compare: re-query STAC and rewrite the CSV")
    ap.add_argument("--downloaded-only", action="store_true",
                    help="Only plot variables that have downloaded data")
    add_health_arg(ap)
    ap.add_argument("--image", default="./catalog.png", help="Output image (default: ./catalog.png)")
    args = ap.parse_args()

    filters = {"model": args.model, "experiment": args.experiment, "variable": args.variable,
               "frequency": args.frequency, "grid": args.grid, "member": args.member,
               "domain": args.domain, "activity": args.activity}
    if args.compare:
        csv_path = args.csv or DEFAULT_CSV
        from_stac = args.refresh or not os.path.exists(csv_path)
        if from_stac:
            rows = compute_rows(args)
            if rows:
                write_csv_rows(csv_path, rows)
            title_extra = (f"collection={args.collection or '*'}  model={args.model or '*'}  "
                           f"variable={args.variable or '*'}  experiment={args.experiment or '*'}\n")
        else:
            rows = filter_rows(read_csv_rows(csv_path), args.collection, filters)
            title_extra = f"from {csv_path}\n"
        metric = "status"
    else:
        if not glob.glob(os.path.join(args.csv_dir, "*.csv")):
            print(f"No local catalogue CSVs in {args.csv_dir} - run stac_catalog first.",
                  file=sys.stderr)
            sys.exit(1)
        rows = filter_rows(read_local_rows(args.csv_dir), args.collection, filters)
        title_extra = f"from {args.csv_dir}\n"
        metric = "coverage"
    if args.downloaded_only:
        rows = filter_downloaded_variables(rows)
        title_extra += "downloaded variables only\n"
    if not rows:
        print("No dataset rows to plot.", file=sys.stderr)
        sys.exit(1)
    try:
        plot(rows, title_extra, args.image, metric)
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
