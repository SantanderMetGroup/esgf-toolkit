"""Local download-directory helpers."""

import os
import re

from .config import DRS_LAYOUTS
from .cql import parse_filter_values

# Columns of the per-collection catalogue CSVs (row order).
LOCAL_COLUMNS = [
    "collection", "activity", "domain", "institution", "driving_institution",
    "model", "rcm_model", "driving_model", "experiment", "member",
    "frequency", "grid", "variable", "version", "latest", "leaf_dir",
    "time_period", "start", "end", "n_years", "size_bytes", "size_gb",
]

# Columns filled in after grouping (excluded from the sort key).
_DERIVED_COLUMNS = ("latest", "time_period", "start", "end", "n_years", "size_bytes", "size_gb")

# Frequencies without a time axis (no date range in the filename).
FIXED_FREQUENCIES = {"fx", "fixed", "ofx"}

_VERSION_RE = re.compile(r"v\d+")
# Filename date range: YYYY, YYYYMM, YYYYMMDD or sub-daily YYYYMMDDhh[mm] stamps.
_STAMP = r"(\d{12}|\d{10}|\d{8}|\d{6}|\d{4})"
_DATE_RANGE_RE = re.compile(rf"_{_STAMP}-{_STAMP}(?:-clim)?(?:\.nc)?$")


def scan_local(out_dir: str):
    """Return ({relpath: size}, {basename: [relpaths]}) in a single walk."""
    sizes = {}
    base = {}
    for root, _, files in os.walk(out_dir):
        for f in files:
            full = os.path.join(root, f)
            rp = os.path.relpath(full, out_dir).replace(os.sep, "/")
            try:
                sizes[rp] = os.path.getsize(full)
            except OSError:
                continue
            base.setdefault(f, []).append(rp)
    return sizes, base


def is_present(lp: str, size, out_dir: str, sizes: dict, base: dict) -> bool:
    """Check whether a remote file is already downloaded (by path, then basename)."""
    if lp in sizes:
        return sizes[lp] == size if size else True
    for cand in base.get(os.path.basename(lp), []):
        if cand in sizes:
            return sizes[cand] == size if size else True
    return False



def _format_ym(yyyymm: str) -> str:
    if not yyyymm or len(yyyymm) < 6:
        return yyyymm
    return f"{yyyymm[:4]}-{yyyymm[4:6]}"


def _extract_temporal_range_from_name(name: str):
    """(start, end) as YYYYMM strings from a filename date range, else (None, None).

    Accepts YYYY, YYYYMM, YYYYMMDD and sub-daily YYYYMMDDhh[mm] stamps, e.g.
    ``tas_..._1hr_195101010030-195112312330.nc`` -> ("195101", "195112").
    """
    m = _DATE_RANGE_RE.search(name)
    if not m:
        return None, None
    s, e = m.group(1), m.group(2)
    s = s[:6] if len(s) >= 6 else s + "01"
    e = e[:6] if len(e) >= 6 else e + "12"
    return s, e


def _months_between(start: str, end: str) -> int:
    """Inclusive number of months between two YYYYMM strings."""
    y1, m1, y2, m2 = int(start[:4]), int(start[4:6]), int(end[:4]), int(end[4:6])
    return max(0, (y2 - y1) * 12 + (m2 - m1) + 1)


def temporal_fields(relpaths, frequency: str = "") -> dict:
    """time_period / start / end / n_years for the files of one leaf directory.

    The period is the union envelope (earliest start to latest end) of the
    filename date ranges; fixed-frequency (fx) data gets time_period "fixed".
    """
    if (frequency or "").lower() in FIXED_FREQUENCIES:
        return {"time_period": "fixed", "start": "", "end": "", "n_years": ""}
    starts, ends = [], []
    for rp in relpaths:
        if rp.endswith(".part"):
            continue
        s, e = _extract_temporal_range_from_name(os.path.basename(rp))
        if s and e:
            starts.append(s)
            ends.append(e)
    if not starts:
        return {"time_period": "", "start": "", "end": "", "n_years": ""}
    smin, emax = min(starts), max(ends)
    start, end = _format_ym(smin), _format_ym(emax)
    return {"time_period": f"{start}–{end}", "start": start, "end": end,
            "n_years": round(_months_between(smin, emax) / 12, 1)}


def _map_facets(raw: dict) -> dict:
    """DRS title keys -> catalogue facet columns (missing facets become '')."""
    return {
        "activity": raw.get("activity_id") or "",
        "domain": raw.get("domain_id") or "",
        "institution": raw.get("institution_id") or "",
        "driving_institution": raw.get("driving_institution_id") or "",
        "model": raw.get("source_id") or raw.get("driving_source_id") or "",
        "rcm_model": raw.get("source_id") or "",
        "driving_model": raw.get("driving_source_id") or "",
        "experiment": raw.get("experiment_id") or raw.get("driving_experiment_id") or "",
        "member": raw.get("variant_label") or raw.get("driving_variant_label") or "",
        "frequency": raw.get("frequency") or "",
        "grid": raw.get("grid_label") or "",
        "variable": raw.get("variable_id") or "",
    }


def parse_drs(relpath: str) -> dict:
    """Split a relative file path into its DRS dataset, leaf dir and facets.

    The mirrored download path is the STAC dataset id with '/' separators; the
    trailing 'vNNNNNNN' version directory is not part of the dataset identity.
    """
    parts = relpath.split("/")[:-1]
    if not parts:
        return {"collection": "", "dataset": "", "leaf": ".", "facets": _map_facets({})}
    head = parts[0]
    keys = coll_key = None
    for prefix, layout, ckey in DRS_LAYOUTS:
        if head.startswith(prefix):
            keys, coll_key = layout, ckey
            break
    if len(parts) > 1 and _VERSION_RE.fullmatch(parts[-1]):
        ds_parts = parts[:-1]
    else:
        ds_parts = parts
    raw = {}
    if keys:
        for cand in (ds_parts, parts):
            if len(cand) == len(keys):
                raw = dict(zip(keys, cand))
                break
    if raw.get(coll_key):
        collection = raw[coll_key]
    elif keys and head.startswith(("MIP-DRS", "MIP-DS")) and len(parts) > 1:
        collection = parts[1]
    else:
        collection = head
    return {
        "collection": collection,
        "dataset": ".".join(ds_parts),
        "leaf": "/".join(parts),
        "facets": _map_facets(raw),
    }


def _row_matches(row: dict, filters: dict) -> bool:
    """True when every active filter (comma-separated values) hits the row."""
    for col, raw in filters.items():
        values = set(parse_filter_values(raw))
        if not values:
            continue
        cell = str(row.get(col) or "")
        parts = {p.strip() for p in cell.split(",") if p.strip()}
        if cell not in values and not (parts & values):
            return False
    return True


def _dataset_dir(leaf: str, version: str) -> str:
    return leaf.rsplit("/", 1)[0] if version else leaf


def catalog_rows(sizes: dict, filters: dict | None = None) -> dict:
    """Group scanned files into {collection: [leaf-dir rows]} with facet filters.

    One row per leaf directory (= one dataset version) with the parsed DRS
    facets, the version and whether it is the newest local version (`latest`),
    the temporal period covered (display `time_period` plus machine-friendly
    `start`/`end`/`n_years`) and total size; '.part' files are ignored.
    """
    active = {k: v for k, v in (filters or {}).items() if v}
    grouped = {}
    group_files = {}
    for relpath, size in sizes.items():
        if relpath.endswith(".part"):
            continue
        info = parse_drs(relpath)
        row = dict(info["facets"])
        row["collection"] = info["collection"]
        row["leaf_dir"] = info["leaf"]
        if not _row_matches(row, active):
            continue
        key = (info["collection"], info["leaf"])
        if key not in grouped:
            last = info["leaf"].rsplit("/", 1)[-1]
            row["version"] = last if _VERSION_RE.fullmatch(last) else ""
            row["size_bytes"] = 0
            grouped[key] = row
        grouped[key]["size_bytes"] += size or 0
        group_files.setdefault(key, []).append(relpath)

    newest = {}  # (collection, dataset dir) -> highest local version
    for (coll, leaf), row in grouped.items():
        k = (coll, _dataset_dir(leaf, row["version"]))
        newest[k] = max(newest.get(k, ""), row["version"])

    out = {}
    order = [c for c in LOCAL_COLUMNS if c not in _DERIVED_COLUMNS]
    for (coll, leaf), row in grouped.items():
        row["latest"] = row["version"] == newest[(coll, _dataset_dir(leaf, row["version"]))]
        row["size_gb"] = round(row["size_bytes"] / 1e9, 3)
        row.update(temporal_fields(group_files[(coll, leaf)], row.get("frequency", "")))
        out.setdefault(coll or "other", []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: tuple(str(r.get(c, "")) for c in order))
    return out
