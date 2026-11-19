import json

import pytest

from esgf_stac.cli.catalog import write_collection_csvs
from esgf_stac.cli.site import main
from esgf_stac.local import catalog_rows
from esgf_stac.site import (
    SITE_COMPARE_COLUMNS,
    SITE_LOCAL_COLUMNS,
    build_snapshot,
    load_watchlist,
    row_matches_watch,
    split_models,
    stac_filters,
    summarize_compare,
    with_period,
)

from .test_local import _sizes

WATCHLIST = """
[[watch]]
name = "EUR daily"
collection = "CORDEX-CMIP6"
domain = "EUR-12"
variable = ["tas", "pr"]

[[watch]]
collection = "CMIP7"
frequency = "mon"
"""


def _compare_rows():
    base = {"collection": "CORDEX-CMIP6", "domain_id": "EUR-12", "model": "M1",
            "driving_model": "D1", "experiment": "historical", "member": "r1i1p1f1",
            "frequency": "day", "version": "v20250101",
            "temporal_start": "1950-01-16", "temporal_end": "2014-12-16"}
    return [
        {**base, "variable": "tas", "status": "complete", "files_local": 8, "files_remote": 8,
         "size_local_gb": 1.5, "size_remote_gb": 1.5, "item_id": "a"},
        {**base, "variable": "pr", "status": "partial", "files_local": "3", "files_remote": "8",
         "size_local_gb": "0.5", "size_remote_gb": "1.4", "item_id": "b"},
        {**base, "variable": "hurs", "status": "missing", "files_local": 0, "files_remote": 8,
         "size_local_gb": 0, "size_remote_gb": 1.2, "item_id": "c"},
        {**base, "domain_id": "NAM-12", "variable": "tas", "status": "missing", "item_id": "d"},
    ]


def test_load_watchlist(tmp_path):
    p = tmp_path / "w.toml"
    p.write_text(WATCHLIST)
    w = load_watchlist(str(p))
    assert [x["name"] for x in w] == ["EUR daily", "CMIP7 · mon"]
    assert w[0]["filters"] == {"variable": "tas,pr", "domain": "EUR-12"}
    assert load_watchlist(str(tmp_path / "absent.toml")) == []


@pytest.mark.parametrize("body, msg", [
    ('[[watch]]\nvariable = "tas"\n', "collection"),
    ('[[watch]]\ncollection = "X"\nvarible = "tas"\n', "unknown key"),
    ('[[watch]]\nname = "a"\ncollection = "X"\n[[watch]]\nname = "a"\ncollection = "Y"\n', "duplicate"),
])
def test_load_watchlist_rejects_bad_entries(tmp_path, body, msg):
    p = tmp_path / "w.toml"
    p.write_text(body)
    with pytest.raises(ValueError, match=msg):
        load_watchlist(str(p))


def test_stac_filters_maps_domain_to_grid():
    w = {"collection": "CORDEX-CMIP6", "filters": {"domain": "EUR-12", "variable": "tas"}}
    assert stac_filters(w) == {"grid": "EUR-12", "variable": "tas"}


def test_row_matches_watch_uses_aliases():
    w = {"collection": "CORDEX-CMIP6", "filters": {"domain": "EUR-12", "variable": "tas,pr"}}
    rows = _compare_rows()
    assert [row_matches_watch(r, w) for r in rows] == [True, True, False, False]
    assert not row_matches_watch({**rows[0], "collection": "CMIP7"}, w)


def test_split_models_cordex_and_global():
    assert split_models({"driving_model": "MPI-ESM1-2-HR", "model": "ICON-CLM"}) == ("MPI-ESM1-2-HR", "ICON-CLM")
    assert split_models({"driving_model": "", "model": "CanESM5-1"}) == ("CanESM5-1", "")  # CMIP7
    assert split_models({}) == ("", "")


def test_summarize_compare_counts_everything_but_caps_rows():
    rows = ([{"status": "complete"}] * 5
            + [{"status": "partial", "item_id": f"p{i}"} for i in range(3)]
            + [{"status": "missing", "item_id": f"m{i}"} for i in range(10)])
    s = summarize_compare(iter(rows), "w", max_rows=6)  # a generator is accepted
    assert s["counts"] == {"complete": 5, "partial": 3, "missing": 10}
    assert s["datasets"] == 18
    assert len(s["rows"]) == 6 and s["omitted"] == 7
    assert [r["status"] for r in s["rows"]] == ["partial"] * 3 + ["missing"] * 3  # partial first
    assert all(r["watch"] == "w" for r in s["rows"])
    full = summarize_compare(rows, "w")
    assert len(full["rows"]) == 13 and full["omitted"] == 0  # complete rows are never listed


def test_with_period_fills_legacy_rows():
    assert with_period({"time_period": "1951-01–2014-12"})["n_years"] == 64.0
    assert with_period({"time_period": "fixed"}) == {"time_period": "fixed"}
    row = {"time_period": "x", "start": "2000-01"}
    assert with_period(row) is row


def test_build_snapshot_shapes():
    local = [r for rows in catalog_rows(_sizes()).values() for r in rows]
    watch = [{"name": "EUR daily", "collection": "CORDEX-CMIP6", "description": "",
              "filters": {"domain": "EUR-12"}},
             {"name": "offline", "collection": "CMIP7", "description": "", "filters": {}}]
    compare = {"EUR daily": summarize_compare(_compare_rows()[:3], "EUR daily")}
    health = [{"timestamp": "t", "kind": "fs", "name": "h1", "status": "ok", "detail": ""},
              {"timestamp": "t", "kind": "fs", "name": "h2", "status": "down", "detail": ""}]
    snap = build_snapshot(local, watch, compare, health, generated_at="2026-01-01T00:00:00Z")

    lt = snap["local.json"]
    assert lt["columns"] == SITE_LOCAL_COLUMNS
    assert len(lt["rows"]) == len(local) - 1  # the older CORDEX version is dropped
    meta = snap["meta.json"]
    assert meta["totals"]["older_versions"] == 1
    assert meta["health"]["up"] == 1 and meta["health"]["down"] == 1
    eur, off = meta["watchlist"]
    assert eur["evaluated"] and eur["counts"] == {"complete": 1, "partial": 1, "missing": 1}
    assert eur["datasets"] == 3 and eur["rows_listed"] == 2 and eur["rows_omitted"] == 0
    assert not off["evaluated"] and off["datasets"] == 0

    ct = snap["compare.json"]
    assert ct["columns"] == SITE_COMPARE_COLUMNS
    rows = [dict(zip(ct["columns"], r)) for r in ct["rows"]]
    pr = next(r for r in rows if r["variable"] == "pr")
    assert pr["files_local"] == 3 and pr["size_remote_gb"] == 1.4  # CSV strings -> numbers
    assert pr["domain"] == "EUR-12" and pr["start"] == "1950-01" and pr["watch"] == "EUR daily"
    assert (pr["gcm"], pr["rcm"]) == ("D1", "M1")
    assert {r["variable"] for r in rows} == {"pr", "hurs"}  # complete datasets are not listed
    json.dumps(snap)  # serialisable


def test_main_offline_writes_snapshot(tmp_path, monkeypatch):
    import csv

    csv_dir = tmp_path / "catalog"
    write_collection_csvs(catalog_rows(_sizes()), str(csv_dir))
    compare_csv = tmp_path / "compare.csv"
    rows = _compare_rows()
    with open(compare_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        w.writerows(rows)
    wl = tmp_path / "w.toml"
    wl.write_text(WATCHLIST)
    out = tmp_path / "site"
    main(["--csv-dir", str(csv_dir), "--watchlist", str(wl), "--compare-csv", str(compare_csv),
          "--health", str(tmp_path / "none.csv"), "--site-data", str(out)])
    meta = json.loads((out / "meta.json").read_text())
    # hurs is outside the watch (tas,pr) and NAM-12 outside its domain
    assert meta["watchlist"][0]["counts"] == {"complete": 1, "partial": 1, "missing": 0}
    assert meta["watchlist"][1]["datasets"] == 0
    local = json.loads((out / "local.json").read_text())
    tl = dict(zip(local["columns"], next(r for r in local["rows"] if r[0] == "CMIP7")))
    assert tl["start"] == "1850-01" and tl["n_years"] == 250


def test_cmip7_rows_get_gcm_and_no_rcm():
    cmip7 = ("MIP-DRS7/CMIP7/C4MIP/CCCma/CanESM5-1/esm-flat10/r1i1p2f1/glb/mon/tas/"
             "tavg-h2m-hxy-u/g120/v20190429/tas_mon_185001-217012.nc")
    local = [r for rows in catalog_rows({cmip7: 10}).values() for r in rows]
    snap = build_snapshot(local, [], {}, [])
    lt = snap["local.json"]
    row = dict(zip(lt["columns"], lt["rows"][0]))
    assert row["collection"] == "CMIP7"
    assert (row["gcm"], row["rcm"]) == ("CanESM5-1", "")
    assert (row["frequency"], row["variable"], row["start"], row["end"]) == ("mon", "tas", "1850-01", "2170-12")
    assert snap["meta.json"]["totals"]["gcms"] == 1 and snap["meta.json"]["totals"]["rcms"] == 0
