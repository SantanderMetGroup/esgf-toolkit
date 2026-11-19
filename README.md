# ESGF STAC Toolkit

Search, monitor, download, catalogue and plot **ESGF** climate data through the
ESGF-NG STAC API (`discovery.east/west.esgf.io`).

![pixi](https://img.shields.io/badge/managed%20with-pixi-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-blue)

## Features

- **Search** (`stac_search`) — metadata table of matching datasets, no downloads.
- **Health** (`stac_health`) — probe STAC nodes + file servers into `health.csv`,
  with daemon mode for periodic checks.
- **Download** (`stac_download`) — resumable, parallel, checksum-verified downloads
  with replica failover (healthy hosts first).
- **Catalogue** (`stac_catalog`) — per-collection CSVs of the local download
  tree (`--out`, default `$ESGF`)
  (one row per leaf directory: DRS facets + temporal period covered + size);
  with `--compare` it prints a completeness report in the same style as
  `stac_search` (search columns + `variable_id` + `local/remote` + `status`)
  and writes the master CSV (`complete / partial / missing`).
- **Plot** (`stac_plot`) — model × variable heatmap of local temporal coverage
  (`catalog.png`); `--compare` plots remote completeness instead.
- **Site** (`stac_site`) — JSON snapshot for the static catalogue website in
  `docs/` (filterable holdings table, coverage matrix, watchlist completeness
  vs ESGF, node health), published to GitHub Pages from the HPC.

## Quickstart

```bash
pixi install
pixi run stac_health                        # write health.csv
pixi run stac_search -- --collection CORDEX-CMIP6 --variable tas --limit 5
pixi run stac_download -- --collection CORDEX-CMIP6 --variable tas --dry-run
pixi run stac_catalog -- --show                    # scan $ESGF -> ./catalog/<collection>.csv
pixi run stac_plot                           # writes catalog.png from ./catalog
pixi run stac_site                           # docs/data/*.json for the website
pixi run site_serve                          # browse it at http://localhost:8000
```

Or with plain pip (editable install):

```bash
pip install -e .
python -m esgf_stac.cli.search --variable tas --limit 5
```

## Repository layout

```
src/esgf_stac/        library (importable)
  config.py           NODES, prefixes, column profiles, title layouts
  cql.py              CQL2 filter builders (eq/cql_or/build_body/...)
  client.py           HTTP session, search(), iter_items() pagination
  records.py          STAC item -> flat record (prefix strip, title fallback)
  files.py            iter_files(), checksum + verify_file()
  tables.py           items_to_dataframe(), collapse_variables(), combine_time(), printing
  health.py           probe STAC/file servers, load/write health.csv
  local.py            scan_local(), parse_drs(), catalog_rows(), is_present()
  site.py             website snapshot: watchlist, JSON tables (local/compare/meta)
  cli/                one module per command: search, health, download, catalog, plot, site
    common.py         shared --node / filter / --health argparse helpers
esgf.py               backward-compat shim (prefer `import esgf_stac`)
scripts/legacy/       one-off experiments (e.g. search_nodes.py)
docs/                 static catalogue website (GitHub Pages); data/ is generated
watchlist.toml        dataset groups compared with ESGF on the website
.github/workflows/    tests (GitHub-hosted) + catalogue-site (self-hosted HPC runner)
tests/                pytest suite (offline, no network)
examples/             example commands and sample outputs
data/                 local runtime outputs (git-ignored): esgf_data/, *.csv, *.png
```


## CLI reference

All commands share `--node {east,west}` (default `east`) and comma-separated
facet filters: `--collection --model --rcm-model --driving-model --experiment
--variable --frequency --grid --member --domain --activity`.
The local data root `--out` defaults to `$ESGF` (falling back to `./esgf_data`)
for `stac_catalog`, `stac_plot` and `stac_download`.

| Command | Purpose | Key outputs |
|---|---|---|
| `stac_search [--limit 20] [--matched] [--columns COLS] [--full] [--csv out.csv]` | metadata table + availability | stdout table, one row per dataset (`n_variables`, `time`), optional CSV |
| `stac_health [--interval 0] [--include-additional]` | probe hosts | `health.csv`, console dashboard |
| `stac_download [--out $ESGF] [--jobs 4] [--dry-run] [--manifest m.csv]` | download with resume + verify | files under `--out`, manifest CSV |
| `stac_catalog [--out $ESGF] [--csv-dir ./catalog] [--compare] [--csv ./catalog.csv] [--missing-only] [--show]` | local inventory (temporal period per leaf dir); `--compare`: completeness report vs STAC | `<collection>.csv` per collection; with `--compare` also printed report + `catalog.csv` |
| `stac_plot [--csv-dir ./catalog] [--compare] [--csv ./catalog.csv] [--refresh] [--image ./catalog.png]` | local temporal-coverage heatmap (`--compare`: completeness) | `catalog.png` |
| `stac_site [--csv-dir ./catalog] [--watchlist watchlist.toml] [--compare-csv catalog.csv \| --no-compare] [--max-rows 10000] [--site-data docs/data]` | website snapshot; watchlist entries compared with ESGF | `docs/data/{local,compare,meta}.json` |

Availability flags (`avail` column) come from `health.csv`:
`ok` all replicas healthy · `down` all down · `warn` mixed · `?` unknown.

## Examples

```bash
# How many CORDEX tas datasets exist?
python -m esgf_stac.cli.search --collection CORDEX-CMIP6 --variable tas --matched

# Peek at CMIP7 monthly tas for two models
python -m esgf_stac.cli.search --collection CMIP7 --variable tas \
  --frequency mon --model UKESM1-3-LL,EC-Earth3 --limit 10

# One row per dataset: n_variables counts the available variables,
# time is start -> end collapsed into one column, size/files are summed
python -m esgf_stac.cli.search --collection CMIP7 --variable tas,pr \
  --frequency mon --limit 10
# `--csv` always writes the raw per-variable rows; --columns widens the table
# (e.g. variable_id for the actual list, start_datetime/end_datetime for ISO bounds)
python -m esgf_stac.cli.search --collection CORDEX-CMIP6 --variable tas,pr \
  --columns model,experiment,variable_id,n_variables,time,size_bytes

# Dry-run a download (list files + hosts, download nothing)
python -m esgf_stac.cli.download --collection CORDEX-CMIP6 --variable tas --dry-run

# Catalogue what you already have (one CSV per collection, offline; $ESGF default)
python -m esgf_stac.cli.catalog --show

# Compare local vs STAC: search-style table + complete/partial/missing verdict
python -m esgf_stac.cli.catalog --collection CORDEX-CMIP6 --compare \
  --csv ./catalog_compare.csv --missing-only

# Heatmap of local temporal coverage (model x variable)
python -m esgf_stac.cli.plot --csv-dir ./catalog --image ./catalog.png

# Heatmap of local-vs-remote completeness instead
python -m esgf_stac.cli.plot --compare --csv ./catalog_compare.csv
```

## Development

```bash
pixi run test        # pytest
pixi run lint        # ruff (if configured)
```

## License

MIT — see [LICENSE](LICENSE).


## Guide: Catalogs and Plots

### Catalogs (`stac_catalog`)

The local catalog scans your downloaded ESGF data tree (default `$ESGF` or `./esgf_data`) and groups files by leaf directory (the final dataset/version directory in the DRS layout). For each leaf directory it records the DRS facets, the `version` (and `latest`: whether it is the newest version present locally) and the **temporal period covered** by all files in that directory, inferred from filename date ranges (`YYYY`, `YYYYMM`, `YYYYMMDD` or sub-daily `YYYYMMDDhhmm`). The period is stored as a display string (`time_period`, e.g. `1951-01–2014-12`) and as machine-friendly `start` / `end` / `n_years`. Fixed fields (`fx`) get `time_period = fixed`.

- Output: one CSV per collection in `--csv-dir` (default `./catalog/`) following `LOCAL_COLUMNS` from `esgf_stac.local`. Each row corresponds to one leaf directory.
- Flags: `--out` sets the data root to scan; `--csv-dir` where per-collection CSVs are written; `--show` prints a compact table; `--collection` and other facet filters restrict what is scanned/written.
- With `--compare`: queries the STAC API for matching datasets and produces a completeness report (`complete` / `partial` / `missing`) comparing local files to remote assets, writing `--csv` (default `./catalog.csv`) and printing a search-style table.

### Plots (`stac_plot`)

- **Local catalogs** (default, reading from `--csv-dir`): produces a heatmap per frequency with rows = models and columns = variables. Each cell reads `<years> (<n datasets>)`: the years covered by the **union** of the datasets' periods (overlapping experiments/members/domains count once, so a cell never exceeds the calendar span) and how many datasets contribute. Panels without a time axis (`fx`) show dataset counts. Use `--downloaded-only` to restrict to variables that have any local data. Filter with `--collection`, `--model`, `--variable`, etc.
- **Comparison mode** (`--compare`): reads the comparison CSV (or refreshes from STAC with `--refresh`) and plots dataset completeness (`complete`/`partial`/`missing`) as before.

### Tips

- Run `stac_catalog --show` to inspect what you have at a glance (temporal periods per dataset/version).
- Use `stac_plot` after building catalogs to visualize coverage across models/variables and frequencies.
- If a leaf directory contains multiple files spanning different time ranges, the catalog records the envelope (min start to max end).
- The PNG is a quick offline overview; it necessarily collapses domain, GCM/RCM, experiment and member. For browsing the full catalogue use the website below.

## Catalogue website (GitHub Pages)

A static site in `docs/` (plain HTML + JS, no build step, no external
dependencies) that reads a JSON snapshot written by `stac_site`:

| Tab | Shows |
|---|---|
| **Holdings** | one row per local dataset (newest version), every facet as a filter with live counts, period as start – end date, sort, CSV export of the filtered view |
| **Coverage matrix** | choose rows/columns (GCM, RCM, GCM / RCM, variable, experiment, domain, …) and the cell value (years covered as a union, dataset count, size); click a cell to list its datasets |
| **Watchlist vs ESGF** | per `watchlist.toml` entry: % complete and a complete / partial / missing bar, plus the table of incomplete datasets |
| **Node health** | last `stac_health` result |

Filters and the active tab are kept in the URL, so a filtered view can be shared as a link.

### Run it locally

```bash
pixi run stac_catalog                       # ./catalog/*.csv
pixi run stac_site                          # docs/data/*.json (queries ESGF for the watchlist)
pixi run stac_site -- --compare-csv catalog.csv   # offline: reuse a stac_catalog --compare CSV
pixi run stac_site -- --no-compare          # local holdings only
pixi run site_serve                         # http://localhost:8000
```

Browsers block `fetch()` on `file://` pages, so serve `docs/` over HTTP rather than opening `index.html` directly.

### Watchlist

`watchlist.toml` lists the dataset groups to compare with ESGF. Each `[[watch]]`
needs a `collection`; the other facets use the CLI flag names (`model`,
`rcm_model`, `driving_model`, `experiment`, `grid`, `member`, `domain`,
`activity`; values comma-separated or a TOML list). **Variables are listed
explicitly in a `[watch.variables]` table, variable = temporal resolution** (or a
list of them):

```toml
[[watch]]
name = "CORDEX-CMIP6 EUR-12"
collection = "CORDEX-CMIP6"
domain = "EUR-12"            # sent to STAC as domain_id

[watch.variables]
tasmax = "day"                                 # daily, every scenario
hurs = ["1hr", "day"]                          # hourly + daily, every scenario
tas = { day = "all", "1hr" = "evaluation" }    # daily every scenario, hourly only evaluation
pr = { "1hr" = ["evaluation", "historical"] }  # several scenarios: a list
orog = "fx"
```

**Scenarios** (the ESGF `experiment`: `evaluation`, `historical`, `ssp370`, ...)
are optional per variable and frequency. A plain string or list of frequencies
(`tasmax = "day"`) and `"all"` both mean *every scenario*; use the
`{ frequency = scenario(s) }` form to narrow one frequency without touching the
others. A watch-level `experiment = ...` key still works and is ANDed with these.

Only the listed combinations are compared: `tasmax = "day"` does not include
hourly `tasmax`, and `"1hr" = "evaluation"` does not include hourly `ssp370`.
Frequencies are the ESGF names (`1hr`, `6hr`, `day`, `mon`, `fx`, ...).
`variables` replaces the plain `variable` / `frequency` keys; a watch cannot use
both. A watch without `variables` selects every variable. Internally each
distinct (frequency, scenarios) combination becomes one STAC query, because STAC
filters are ANDed (one query for `tas` + `pr`, `1hr` + `day` would also select
`tas` at `1hr`).

Each entry costs one STAC query per rebuild, so keep the list focused. The full
`--compare` catalogue (tens of thousands of rows) is deliberately not published:
`compare.json` lists only **partial and missing** datasets, at most `--max-rows`
(default 10000) per watch, partial first. The completeness counts on the cards
always cover every dataset, and a card says when its table is cut off - add
filters to the watch to list them all. (A watch without `variables` is large: CORDEX-CMIP6 has
~32k datasets on ESGF, CMIP7 ~127k, and querying all of CMIP7 takes minutes.)

**GCM / RCM:** CORDEX rows show the driving model as *GCM* and the regional
model as *RCM*; global collections such as CMIP7 only have a GCM.

### Publishing from the HPC

`.github/workflows/catalog-site.yml` runs on a **self-hosted runner on an HPC
login node** (daily at 05:17 UTC, on demand, and on pushes touching the site):
`stac_health` → `stac_catalog` submitted as a SLURM job
(`scripts/hpc/catalog_job.sh`, `sbatch --wait`, log in `logs/`) → `stac_site`
(on the login node, which has internet access) → upload `docs/` as a Pages
artifact. The deploy step runs on a GitHub-hosted runner. The snapshot is never committed.

One-time setup:

1. **Runner** (on the HPC login node, its own folder; runners are per repo):
   *Settings → Actions → Runners → New self-hosted runner* (Linux x64), run the
   download commands it shows, then
   `./config.sh --url https://github.com/SantanderMetGroup/esgf-toolkit --token <TOKEN> --labels hpc`
   and keep `./run.sh` alive (tmux/screen, like other runners on the machine).
2. **Variables** (*Settings → Secrets and variables → Actions → Variables*):
   `ESGF_ROOT` = absolute path of the download tree; optionally
   `SLURM_PARTITION` (default `meteo_long`).
3. **Pages** (*Settings → Pages → Source*): **GitHub Actions**.
4. **Run** *Actions → catalogue-site → Run workflow*.

> **Visibility:** on a public repository (or a public Pages site), the snapshot
> publishes dataset ids, local relative paths, sizes and node health. Use a
> private repository with private Pages if that is a concern. Self-hosted
> runners on **public** repositories can be triggered by fork pull requests, so
> keep the HPC workflow off `pull_request` events (it already is) and restrict
> *Settings → Actions → Fork pull request workflows*.
