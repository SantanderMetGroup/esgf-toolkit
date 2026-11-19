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

SNAPSHOT_VERSION = 2

# Max incomplete (partial + missing) datasets listed per watch. ESGF collections
# are large (CMIP7 ~123k items); the counts always cover every dataset, only the
# table is capped so the JSON stays small enough for a browser.
MAX_COMPARE_ROWS = 10000

# Columns exported for the holdings table (subset of local.LOCAL_COLUMNS).
SITE_LOCAL_COLUMNS = [
    "collection", "activity", "domain", "institution", "gcm", "rcm",
    "experiment", "member", "frequency", "variable", "version",
    "start", "end", "n_years", "size_gb", "leaf_dir",
]

# Columns exported for the watchlist comparison table.
SITE_COMPARE_COLUMNS = [
    "watch", "collection", "domain", "gcm", "rcm", "experiment", "member",
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

ALL_SCENARIOS = ("all", "*")


def _scenarios(value) -> list:
    """Scenario (ESGF experiment) spec -> sorted list; [] means every scenario."""
    values = parse_filter_values(value)
    if not values or any(v.lower() in ALL_SCENARIOS for v in values):
        return []
    return sorted(set(values))


def parse_variables(value, where: str) -> dict:
    """``variables`` table -> {variable: {frequency: [scenarios]}}; [] = all scenarios.

    Accepted TOML values per variable:
      tas = "day"                              one frequency, every scenario
      hurs = ["1hr", "day"]                    several frequencies, every scenario
      tas = { day = "all", "1hr" = "evaluation" }   per-frequency scenarios
                                                    (string, list, or "all")
    """
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{where}: 'variables' must be a non-empty table such as "
                         f"[watch.variables] with entries like tas = \"day\"")
    out = {}
    for var, spec in value.items():
        if isinstance(spec, dict):
            freqs = {str(f): _scenarios(sc) for f, sc in spec.items()}
        else:
            freqs = {f: [] for f in sorted(set(parse_filter_values(spec)))}
        if not freqs:
            raise ValueError(f"{where}: variable '{var}' needs a frequency (e.g. \"day\")")
        out[str(var)] = freqs
    return out


def _narrow(scenarios: list, watch_level: set, where: str, what: str) -> list:
    """Combine a pair's scenarios with the watch-level ``experiment`` filter (AND)."""
    if not watch_level:
        return scenarios
    if not scenarios:
        return sorted(watch_level)
    both = sorted(set(scenarios) & watch_level)
    if not both:
        raise ValueError(f"{where}: {what} lists scenario(s) {', '.join(scenarios)} but the watch "
                         f"itself is limited to experiment = {', '.join(sorted(watch_level))}")
    return both


def load_watchlist(path: str) -> list:
    """Read ``[[watch]]`` entries from a TOML file.

    Returns [{name, collection, description, filters, variables}]. Each entry needs a
    ``collection``; every other key from WATCH_KEYS is a comma-separated string (or
    list) of values, exactly like the CLI flags. ``variables`` is an explicit
    {variable: frequency(ies)} table and replaces the ``variable``/``frequency``
    keys (combining them would be ambiguous). A missing file means an empty watchlist.
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    out = []
    for i, entry in enumerate(data.get("watch", []), 1):
        where = f"{path}: watch #{i}"
        unknown = set(entry) - set(WATCH_KEYS) - {"name", "collection", "description", "variables"}
        if unknown:
            raise ValueError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}")
        if not entry.get("collection"):
            raise ValueError(f"{where} needs a 'collection'")
        variables = {}
        if "variables" in entry:
            clash = {"variable", "frequency"} & set(entry)
            if clash:
                raise ValueError(f"{where}: use either 'variables' or {' / '.join(sorted(clash))}, not both")
            variables = parse_variables(entry["variables"], where)
        filters = {k: ",".join(parse_filter_values(entry[k])) for k in WATCH_KEYS if entry.get(k)}
        watch_level = set(parse_filter_values(filters.get("experiment")))
        for var, freqs in variables.items():
            for freq, scenarios in freqs.items():
                _narrow(scenarios, watch_level, where, f"{var} at {freq}")  # raises on a clash
        name = entry.get("name") or " · ".join([entry["collection"], *filters.values()])
        out.append({"name": name, "collection": entry["collection"],
                    "description": entry.get("description", ""), "filters": filters,
                    "variables": variables})
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


def stac_queries(watch: dict) -> list:
    """STAC filter dicts that together select exactly this watch.

    A plain watch is one query. With ``variables`` there is one query per distinct
    (frequency, scenarios) combination - variables sharing both are OR-ed - because
    STAC filters are ANDed: variable=pr,tas + frequency=1hr,day in one query would
    also select tas at 1hr when only tas at day was requested.
    """
    base = stac_filters(watch)
    watch_level = set(parse_filter_values(base.get("experiment")))
    groups = {}
    for var, freqs in (watch.get("variables") or {}).items():
        for freq, scenarios in freqs.items():
            both = sorted(set(scenarios) & watch_level) if scenarios and watch_level else \
                (scenarios or sorted(watch_level))
            if scenarios and watch_level and not both:
                continue  # can never match (load_watchlist rejects this)
            groups.setdefault((freq, tuple(both)), []).append(var)
    if not groups:
        return [base]
    queries = []
    for (freq, scenarios), vs in sorted(groups.items()):
        q = {**base, "variable": ",".join(vs), "frequency": freq}
        if scenarios:
            q["experiment"] = ",".join(scenarios)
        queries.append(q)
    return queries


def _row_values(row: dict, key: str) -> set:
    values = set()
    for col in _ROW_ALIASES.get(key, (key,)):
        values.update(p.strip() for p in str(row.get(col) or "").split(",") if p.strip())
    return values


def row_matches_watch(row: dict, watch: dict) -> bool:
    """True when a catalogue/comparison row falls inside a watchlist entry."""
    if (row.get("collection") or "") != watch["collection"]:
        return False
    for key, raw in watch["filters"].items():
        if not _row_values(row, key) & set(parse_filter_values(raw)):
            return False
    pairs = watch.get("variables")
    if pairs:  # explicit (variable, frequency[, scenario]) combinations
        freqs, scenarios = _row_values(row, "frequency"), _row_values(row, "experiment")
        for var in _row_values(row, "variable"):
            for freq in freqs:
                wanted = pairs.get(var, {}).get(freq)
                if wanted is not None and (not wanted or scenarios & set(wanted)):
                    return True
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


def split_models(row: dict) -> tuple[str, str]:
    """(gcm, rcm) of a catalogue or comparison row.

    Regional collections (CORDEX) have a driving model - the GCM - and a regional
    model (RCM). Global collections (CMIP7, ...) only have the model itself, which
    is the GCM.
    """
    driving, model = row.get("driving_model") or "", row.get("model") or ""
    return (driving, model) if driving else (model, "")


def with_models(row: dict) -> dict:
    gcm, rcm = split_models(row)
    return {**row, "gcm": gcm, "rcm": rcm}


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
        "gcm": split_models(row)[0],
        "rcm": split_models(row)[1],
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

STATUSES = ("complete", "partial", "missing")


def summarize_compare(rows, watch_name: str, max_rows: int = MAX_COMPARE_ROWS) -> dict:
    """Stream comparison rows (STAC records) into one watch's snapshot summary.

    ``rows`` may be a generator: each row is reduced to its compact site form at
    once, so only the listed incomplete datasets stay in memory. Returns
    {counts, datasets, rows, omitted}: counts cover every dataset, ``rows`` lists
    up to ``max_rows`` incomplete ones (all partial first, then missing) and
    ``omitted`` is how many incomplete datasets did not fit.
    """
    counts = dict.fromkeys(STATUSES, 0)
    partial, missing = [], []
    for r in rows:
        status = r.get("status") if r.get("status") in counts else "missing"
        counts[status] += 1
        if status == "complete":
            continue
        bucket = partial if status == "partial" else missing
        if len(bucket) < max_rows:
            bucket.append(compare_site_row({**r, "status": status}, watch_name))
    kept = partial[:max_rows]
    kept += missing[:max(0, max_rows - len(kept))]
    incomplete = counts["partial"] + counts["missing"]
    return {"counts": counts, "datasets": sum(counts.values()), "rows": kept,
            "omitted": incomplete - len(kept)}


def build_snapshot(local_rows: list, watchlist: list, compare_by_watch: dict,
                   health_rows: list, errors: dict | None = None,
                   generated_at: str | None = None) -> dict:
    """Assemble {"local.json": ..., "compare.json": ..., "meta.json": ...} payloads.

    ``compare_by_watch`` maps watch name -> summarize_compare() result; a watch
    absent from the mapping was not evaluated (offline / failed).
    """
    errors = errors or {}
    latest = [with_models(with_period(r)) for r in latest_local_rows(local_rows)]
    compare_rows = [row for w in watchlist
                    for row in compare_by_watch.get(w["name"], {}).get("rows", [])]
    watches = []
    for w in watchlist:
        summary = compare_by_watch.get(w["name"])
        watches.append({
            **w,
            "evaluated": summary is not None,
            "error": errors.get(w["name"], ""),
            "counts": summary["counts"] if summary else dict.fromkeys(STATUSES, 0),
            "datasets": summary["datasets"] if summary else 0,
            "rows_listed": len(summary["rows"]) if summary else 0,
            "rows_omitted": summary["omitted"] if summary else 0,
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
            "gcms": len({r["gcm"] for r in latest if r["gcm"]}),
            "rcms": len({r["rcm"] for r in latest if r["rcm"]}),
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
