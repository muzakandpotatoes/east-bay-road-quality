#!/usr/bin/env bash
# Collect and normalise bikeway data for Berkeley and Oakland.
# Separate from run_all.sh: this data is not wired into the map yet.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-python3}"
R=data/raw/bikeways
O=data/out
mkdir -p "$R" "$O"

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36'
[ -s data/raw/berkeley_bike_plan_2026.pdf ] || curl -sSL --fail -A "$UA" \
  -o data/raw/berkeley_bike_plan_2026.pdf \
  "https://berkeleyca.gov/sites/default/files/2026-07/Berkeley%20Bicycle%20Plan%202026.pdf"

cd scripts
echo "==> published bikeway layers + OpenStreetMap"
$PY fetch_bikeways.py ../$R

echo "==> Berkeley Bicycle Plan 2026 (Tier 1 projects, network mileage)"
$PY extract_berkeley_bike_plan.py ../data/raw/berkeley_bike_plan_2026.pdf \
    ../$R/berkeley_plan_projects.csv ../$R/berkeley_plan_mileage.json

echo "==> normalise"
$PY normalize_bikeways.py ../$R ../$O/bikeways.geojson ../$O/bikeways.csv ../$O/bikeways_summary.json
echo "==> done"
