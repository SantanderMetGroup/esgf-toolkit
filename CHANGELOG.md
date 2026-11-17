# Changelog

## Unreleased

- Catalogue website: `docs/` static site (holdings table, coverage matrix,
  watchlist completeness vs ESGF, node health) fed by the new `stac_site`
  command; `watchlist.toml`; GitHub Actions workflow publishing to Pages from a
  self-hosted HPC runner; offline test workflow.
- Local catalogue: sub-daily filename dates (`YYYYMMDDhhmm`) parsed, `fx`
  marked `fixed`, new `version`, `latest`, `start`, `end`, `n_years` columns.
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
