#!/bin/bash
# SLURM job: scan the local ESGF tree ($ESGF) into ./catalog/*.csv.
# Submitted by .github/workflows/catalog-site.yml so the heavy Lustre scan runs
# on a compute node instead of the login node hosting the GitHub runner.
# Manual use (from the repo root):  ESGF=/path/to/tree sbatch scripts/hpc/catalog_job.sh
#SBATCH --job-name=esgf-toolkit-catalog
#SBATCH -p meteo_long
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=12:00:00
#SBATCH --output=logs/catalog-%j.out

set -euo pipefail
source ~/.bashrc || true
cd "${SLURM_SUBMIT_DIR:-.}"

: "${ESGF:?ESGF must point to the local download tree}"
echo "$(date -Is) scanning $ESGF on $(hostname)"
pixi run stac_catalog -- --csv-dir ./catalog
echo "$(date -Is) done"
