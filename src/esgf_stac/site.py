"""Static-site snapshot: local catalogue + watchlist completeness + health -> JSON.

The GitHub Pages site under ``docs/`` reads three files written here:

- ``local.json``   one row per local dataset (newest local version only)
- ``compare.json`` local-vs-ESGF completeness, only for watchlist entries
- ``meta.json``    totals, generation time, node health, watchlist definitions

Tables are stored compactly as ``{"columns": [...], "rows": [[...], ...]}``.
"""

import csv
import json
import os
import re
import time
import tomllib

from .cql import parse_filter_values

SNAPSHOT_VERSION = 1

# Columns exported for the holdings table (subset of local.LOCAL_COLUMNS).
SITE_LOCAL_COLUMNS = [
    "collection", "activity", "domain", "institution", "model", "driving_model",
    "experiment", "member", "frequency", "variable", "version",
    "start", "end", "n_years", "size_gb", "leaf_dir",
]

# Columns exported for the watchlist comparison table.
SITE_COMPARE_COLUMNS = [
    "watch", "collection", "domain", "model", "driving_model", "experiment", "member",
    "frequency", "variable", "version", "start", "end", "status",
    "files_local", "files_remote", "size_local_gb", "size_remote_gb", "item_id",
]

# Facet keys a watchlist entry may filter on (same names as the CLI flags).
WATCH_KEYS = ("model", "rcm_model", "driving_model", "experiment", "variable",
              "frequency", "grid", "member", "domain", "activity")

# Columns where a facet may live in a comparison row (STAC record) or local row.
_ROW_ALIASES = {
    "domain": ("domain", "domain_id", "grid", "grid_label"),
    "grid": ("grid", "grid_label", "domain_id"),
    "activity": ("activity", "activity_id"),
    "variable": ("variable", "variable_id"),
    "rcm_model": ("rcm_model",),
}

_NUMERIC = {"n_years", "size_gb", "files_local", "files_remote", "size_local_gb", "size_remote_gb"}


# --------------------------------------------------------------------------- watchlist

def load_watchlist(path: str) -> list:
    """Read ``[[watch]]`` entries from a TOML file -> [{name, collection, filters}].

    Each entry needs a ``collection``; every other key from WATCH_KEYS is a
    comma-separated string (or list) of values, exactly like the CLI flags.
    A missing file means an empty watchlist.
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    out = []
    for i, entry in enumerate(data.get("watch", []), 1):
        unknown = set(entry) - set(WATCH_KEYS) - {"name", "collection", "description"}
        if unknown:
            raise ValueError(f"{path}: watch #{i} has unknown key(s): {', '.join(sorted(unknown))}")
        if not entry.get("collection"):
            raise ValueError(f"{path}: watch #{i} needs a 'collection'")
        filters = {k: ",".join(parse_filter_values(entry[k])) for k in WATCH_KEYS if entry.get(k)}
        name = entry.get("name") or " · ".join([entry["collection"], *filters.values()])
        out.append({"name": name, "collection": entry["collection"],
                    "description": entry.get("description", ""), "filters": filters})
    names = [w["name"] for w in out]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"{path}: duplicate watch name(s): {', '.join(sorted(dupes))}")
    return out


def stac_filters(watch: dict) -> dict:
    """Watchlist filters -> STAC search filters.

    In the STAC API the CORDEX domain (e.g. EUR-12) is ``domain_id``, which the
    toolkit queries through the ``grid`` column, so ``domain`` is sent as ``grid``.
    """
    f = dict(watch["filters"])
    if "domain" in f:
        f["grid"] = ",".join(parse_filter_values(f.get("grid")) + parse_filter_values(f.pop("domain")))
    return f


def row_matches_watch(row: dict, watch: dict) -> bool:
    """True when a catalogue/comparison row falls inside a watchlist entry."""
    if (row.get("collection") or "") != watch["collection"]:
        return False
    for key, raw in watch["filters"].items():
        wanted = set(parse_filter_values(raw))
        have = set()
        for col in _ROW_ALIASES.get(key, (key,)):
            have.update(p.strip() for p in str(row.get(col) or "").split(",") if p.strip())
        if not have & wanted:
            return False
    return True


# --------------------------------------------------------------------------- rows

def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _num(value):
    if value in (None, ""):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _clean(col: str, value):
    if col in _NUMERIC:
        return _num(value)
    return "" if value is None else str(value)


def table(rows: list, columns: list) -> dict:
    """Rows (dicts) -> compact {columns, rows} table."""
    return {"columns": list(columns),
            "rows": [[_clean(c, r.get(c)) for c in columns] for r in rows]}


_PERIOD_RE = re.compile(r"(\d{4})-(\d{2})\D+(\d{4})-(\d{2})")


def with_period(row: dict) -> dict:
    """Fill start/end/n_years from the display time_period when they are missing.

    Catalogues written before those columns existed only carry "YYYY-MM–YYYY-MM".
    """
    if row.get("start") or not row.get("time_period"):
        return row
    m = _PERIOD_RE.search(str(row["time_period"]))
    if not m:
        return row
    y1, m1, y2, m2 = (int(g) for g in m.groups())
    months = max(0, (y2 - y1) * 12 + (m2 - m1) + 1)
    return {**row, "start": f"{y1:04d}-{m1:02d}", "end": f"{y2:04d}-{m2:02d}",
            "n_years": round(months / 12, 1)}


def latest_local_rows(rows: list) -> list:
    """Keep the newest local version of each dataset.

    Catalogues that predate the ``latest`` column keep every row.
    """
    if not rows or "latest" not in rows[0]:
        return list(rows)
    return [r for r in rows if _truthy(r.get("latest"))]


def compare_site_row(row: dict, watch_name: str) -> dict:
    """STAC comparison row (cli.catalog.fmt_row / catalog.csv) -> site row."""
    return {
        "watch": watch_name,
        "collection": row.get("collection", ""),
        "domain": row.get("domain_id") or row.get("grid_label") or row.get("domain") or "",
        "model": row.get("model", ""),
        "driving_model": row.get("driving_model", ""),
        "experiment": row.get("experiment", ""),
        "member": row.get("member", ""),
        "frequency": row.get("frequency", ""),
        "variable": row.get("variable") or row.get("variable_id") or "",
        "version": row.get("version", ""),
        "start": (row.get("temporal_start") or row.get("start_datetime") or "")[:7],
        "end": (row.get("temporal_end") or row.get("end_datetime") or "")[:7],
        "status": row.get("status", ""),
        "files_local": row.get("files_local"),
        "files_remote": row.get("files_remote"),
        "size_local_gb": row.get("size_local_gb"),
        "size_remote_gb": row.get("size_remote_gb"),
        "item_id": row.get("item_id", ""),
    }


def read_health_rows(path: str) -> list:
    """health.csv -> [{kind, name, status, detail, timestamp}] (empty if missing)."""
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [{k: r.get(k, "") for k in ("timestamp", "kind", "name", "status", "detail")}
                for r in csv.DictReader(f)]


# --------------------------------------------------------------------------- snapshot

def _status_counts(rows: list) -> dict:
    counts = {"complete": 0, "partial": 0, "missing": 0}
    for r in rows:
        s = r.get("status")
        counts[s if s in counts else "missing"] += 1
    return counts


def build_snapshot(local_rows: list, watchlist: list, compare_by_watch: dict,
                   health_rows: list, errors: dict | None = None,
                   generated_at: str | None = None) -> dict:
    """Assemble {"local.json": ..., "compare.json": ..., "meta.json": ...} payloads.

    ``compare_by_watch`` maps watch name -> list of comparison rows (STAC records);
    a watch absent from the mapping was not evaluated (offline / failed).
    """
    errors = errors or {}
    latest = [with_period(r) for r in latest_local_rows(local_rows)]
    compare_rows = [compare_site_row(r, name)
                    for w in watchlist for name in [w["name"]]
                    for r in compare_by_watch.get(name, [])]
    watches = []
    for w in watchlist:
        rows = compare_by_watch.get(w["name"])
        watches.append({
            **w,
            "evaluated": rows is not None,
            "error": errors.get(w["name"], ""),
            "counts": _status_counts(rows or []),
            "datasets": len(rows or []),
        })
    size_gb = sum(_num(r.get("size_gb")) or 0 for r in latest)
    meta = {
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totals": {
            "datasets": len(latest),
            "older_versions": len(local_rows) - len(latest),
            "size_gb": round(size_gb, 3),
            "collections": sorted({r.get("collection") or "other" for r in latest}),
            "variables": len({r.get("variable") for r in latest if r.get("variable")}),
            "models": len({r.get("model") for r in latest if r.get("model")}),
        },
        "health": {
            "timestamp": next((h["timestamp"] for h in health_rows if h.get("timestamp")), ""),
            "up": sum(1 for h in health_rows if h["status"] == "ok"),
            "down": sum(1 for h in health_rows if h["status"] != "ok"),
            "nodes": health_rows,
        },
        "watchlist": watches,
    }
    return {
        "local.json": table(latest, SITE_LOCAL_COLUMNS),
        "compare.json": table(compare_rows, SITE_COMPARE_COLUMNS),
        "meta.json": meta,
    }


def write_snapshot(payloads: dict, out_dir: str) -> list:
    """Write each payload as compact JSON into out_dir; returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, payload in payloads.items():
        path = os.path.join(out_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)  # atomic: the site never sees a half-written file
        written.append(path)
    return written
