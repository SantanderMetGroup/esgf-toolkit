#!/usr/bin/env python3
"""Export the catalogue snapshot (JSON) that the GitHub Pages site in docs/ displays.

Reads the local per-collection CSVs written by stac_catalog, health.csv and the
watchlist; for every watchlist entry it compares local files with ESGF (STAC
query, or offline from an existing comparison CSV with --compare-csv).
"""

import argparse
import glob
import os
import sys

import requests

from esgf_stac import NODES, iter_items, load_health
from esgf_stac.cli.catalog import fmt_row
from esgf_stac.cli.common import add_health_arg, add_node_arg, add_out_arg
from esgf_stac.cli.plot import read_csv_rows, read_local_rows
from esgf_stac.local import scan_local
from esgf_stac.site import (
    build_snapshot,
    load_watchlist,
    read_health_rows,
    row_matches_watch,
    stac_filters,
    write_snapshot,
)

DEFAULT_SITE_DATA = "./docs/data"
DEFAULT_WATCHLIST = "./watchlist.toml"


def compare_from_csv(watchlist: list, csv_path: str) -> dict:
    """Offline: select each watch's rows from an existing stac_catalog --compare CSV."""
    rows = read_csv_rows(csv_path)
    return {w["name"]: [r for r in rows if row_matches_watch(r, w)] for w in watchlist}


def compare_from_stac(watchlist: list, args) -> tuple[dict, dict]:
    """Query STAC for each watch and compare against the local tree (scanned once)."""
    node = NODES[args.node]
    _, statuses = load_health(args.health)
    print(f"Scanning {args.out} for the watchlist comparison...")
    sizes, base = scan_local(args.out) if os.path.isdir(args.out) else ({}, {})
    results, errors = {}, {}
    for w in watchlist:
        try:
            items = iter_items(node, w["collection"], stac_filters(w), max_items=args.max_items)
            rows = [r for item in items
                    if (r := fmt_row(item, args.out, sizes, base, statuses, show_missing_only=False))]
        except requests.exceptions.RequestException as e:
            errors[w["name"]] = f"{type(e).__name__}: {e}"
            print(f"  {w['name']}: STAC query failed ({errors[w['name']]})", file=sys.stderr)
            continue
        results[w["name"]] = rows
        if args.max_items and len(rows) >= args.max_items:
            errors[w["name"]] = f"truncated at --max-items {args.max_items}"
        print(f"  {w['name']}: {len(rows)} dataset(s)")
    return results, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    add_node_arg(ap)
    add_out_arg(ap, "Local download dir (scanned for the watchlist comparison)")
    add_health_arg(ap)
    ap.add_argument("--csv-dir", default="./catalog",
                    help="Local per-collection CSVs from stac_catalog (default: ./catalog)")
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST,
                    help=f"Watchlist TOML (default: {DEFAULT_WATCHLIST})")
    ap.add_argument("--site-data", default=DEFAULT_SITE_DATA,
                    help=f"Output dir for the JSON snapshot (default: {DEFAULT_SITE_DATA})")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--compare-csv", default="",
                     help="Offline: take watchlist completeness from this stac_catalog --compare CSV")
    src.add_argument("--no-compare", action="store_true",
                     help="Skip the watchlist comparison (local holdings only)")
    ap.add_argument("--max-items", type=int, default=20000,
                    help="Max STAC items per watchlist entry (default: 20000, 0 = no limit)")
    args = ap.parse_args(argv)

    if not glob.glob(os.path.join(args.csv_dir, "*.csv")):
        print(f"No local catalogue CSVs in {args.csv_dir} - run stac_catalog first.", file=sys.stderr)
        sys.exit(1)
    local_rows = read_local_rows(args.csv_dir)
    try:
        watchlist = load_watchlist(args.watchlist)
    except (ValueError, OSError) as e:
        print(f"Invalid watchlist: {e}", file=sys.stderr)
        sys.exit(1)

    errors = {}
    if args.no_compare or not watchlist:
        compare = {}
    elif args.compare_csv:
        compare = compare_from_csv(watchlist, args.compare_csv)
    else:
        compare, errors = compare_from_stac(watchlist, args)

    payloads = build_snapshot(local_rows, watchlist, compare, read_health_rows(args.health), errors)
    paths = write_snapshot(payloads, args.site_data)
    t = payloads["meta.json"]["totals"]
    print(f"Snapshot: {t['datasets']} dataset(s), {t['size_gb'] / 1000:.2f} TB, "
          f"{len(watchlist)} watch(es), {len(payloads['compare.json']['rows'])} compared row(s)")
    for p in paths:
        print(f"  wrote {p}")


if __name__ == "__main__":
    main()
