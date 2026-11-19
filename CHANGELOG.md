# Changelog

## Unreleased

- Catalogue website: `docs/` static site (holdings table, coverage matrix,
  watchlist completeness vs ESGF, node health) fed by the new `stac_site`
  command; `watchlist.toml`; GitHub Actions workflow publishing to Pages from a
  self-hosted HPC runner; offline test workflow.
- Local catalogue: sub-daily filename dates (`YYYYMMDDhhmm`) parsed, `fx`
  marked `fixed`, new `version`, `latest`, `start`, `end`, `n_years` columns.
- Local catalogue now recognises the `MIP-DRS7/CMIP7/...` layout (CMIP7 data was
  filed under a bogus `MIP-DRS7` collection with empty facets).
- Site: GCM/RCM columns (CMIP7 models show as GCM), period shown as
  start – end, watchlist table limited to partial/missing datasets and capped
  per watch (`--max-rows`) so CMIP7-sized watches stay small.
- `scripts/hpc/catalog_job.sh`: source the profile before `set -u`
  (`BASHRCSOURCED: unbound variable` aborted the SLURM job).
- `stac_plot`: local cells show years covered by the union of datasets
  (no more summed years across runs) plus the dataset count.

## 0.2.0 — 2026-09-24

- Split monolithic `esgf.py` into `src/esgf_stac/` utilities
  (`config`, `cql`, `client`, `records`, `files`, `tables`, `health`, `local`).
- Moved CLIs to `src/esgf_stac/cli/` (`search`, `health`, `download`, `catalog`, `plot`)
  with shared argparse helpers in `cli/common.py`.
- Added `README.md`, `LICENSE`, `CONTRIBUTING.md`, offline `tests/`.
- `esgf.py` kept as backward-compat shim.

## 0.1.0

- Initial flat scripts: `esgf.py`, `stac_*.py`, `search_nodes.py`.
