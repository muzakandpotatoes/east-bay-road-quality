#!/usr/bin/env bash
# Rebuild everything from the two source PDFs. Idempotent.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-python3}"
R=data/raw
O=data/out
mkdir -p "$R" "$O"

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36'
get() { [ -s "$2" ] || curl -sSL --fail -A "$UA" -o "$2" "$1"; }

echo "==> source PDFs"
get "https://berkeleyca.gov/sites/default/files/2026-07/2024%20Pavement%20Management%20Plan%20Update%20%28PTAP25%29.pdf" \
    "$R/berkeley_ptap25.pdf"
get "https://berkeleyca.gov/sites/default/files/2026-07/List%20of%20Five-Year%20Street%20Rehab%20Plan%20and%205-Yr%20FF%20Plan%20Paving%20Projects%20FY27-31.pdf" \
    "$R/berkeley_5yr_plan.pdf"
get "https://berkeleyca.gov/sites/default/files/documents/Streets_on_Moratorium.pdf" \
    "$R/berkeley_moratorium.pdf"
if [ ! -s "$R/oakland_ptap25.pdf" ]; then
  cat >&2 <<'MSG'
ERROR: data/raw/oakland_ptap25.pdf is missing.

oaklandca.gov sits behind an Akamai rule that refuses non-browser clients, so
this one file cannot be scripted. Download it in a browser and save it there:
https://www.oaklandca.gov/files/assets/city/v/1/transportation/documents/streets/street-paving/final-report-oakland-ptap-25.pdf
(expected ~15.96 MB)
MSG
  exit 1
fi

cd scripts
echo "==> extract PCI tables (validated against each report's own totals)"
$PY extract_berkeley.py ../$R/berkeley_ptap25.pdf ../$R/berkeley_pci.csv
$PY extract_oakland.py  ../$R/oakland_ptap25.pdf  ../$R/oakland_pci.csv
[ -s ../$R/albany_ptap22.pdf ] && $PY extract_albany.py ../$R/albany_ptap22.pdf ../$R/albany_pci.csv
$PY extract_berkeley_overlays.py ../$R/berkeley_5yr_plan.pdf ../$R/berkeley_moratorium.pdf \
     ../$R/berkeley_plan.csv ../$R/berkeley_moratorium.csv

echo "==> fetch geometry from the cities' ArcGIS services"
$PY fetch_geometry.py ../$R
# OSM tops up the handful of streets missing from Berkeley's centerline layer;
# the pipeline still runs (with a slightly lower match rate) if Overpass is down.
[ -s ../$R/berkeley_osm_streets.geojson ] || $PY fetch_osm.py ../$R/berkeley_osm_streets.geojson || true

echo "==> join PCI rows to geometry"
$PY join_city.py Berkeley ../$R/berkeley_pci.csv ../$R/berkeley_centerlines.geojson \
     ../$O/berkeley_pci_segments.geojson ../$O/berkeley_join_report.json \
     ../$R/berkeley_osm_streets.geojson
$PY join_oakland.py  ../$R/oakland_pci.csv ../$R/oakland_pci_sections.geojson \
     ../$O/oakland_pci_segments.geojson ../$O/oakland_join_report.json
# Albany: same PDF-listing shape as Berkeley, and its centerlines are already
# in Berkeley's layer, filtered to Albany so repeated street names cannot cross.
if [ -s ../$R/albany_pci.csv ]; then
  $PY join_city.py Albany ../$R/albany_pci.csv ../$R/berkeley_centerlines.geojson \
       ../$O/albany_pci_segments.geojson ../$O/albany_join_report.json \
       ../$R/albany_osm_streets.geojson Albany
fi

echo "==> context overlays"
$PY join_berkeley_overlays.py ../$R/berkeley_plan.csv ../$R/berkeley_moratorium.csv \
     ../$R/berkeley_centerlines.geojson ../$O/berkeley_paving_plan.geojson \
     ../$O/berkeley_moratorium.geojson ../$O/berkeley_overlay_report.json \
     ../$R/berkeley_osm_streets.geojson
$PY build_oakland_overlays.py ../$R/oakland_pci_sections.geojson \
     ../$O/oakland_paving_plan.geojson ../$O/oakland_moratorium.geojson

echo "==> combine, pack, build map"
CITY_ARGS="../$O/berkeley_pci_segments.geojson ../$O/berkeley_join_report.json \
           ../$O/oakland_pci_segments.geojson ../$O/oakland_join_report.json"
[ -s ../$O/albany_pci_segments.geojson ] && CITY_ARGS="$CITY_ARGS \
           ../$O/albany_pci_segments.geojson ../$O/albany_join_report.json"
$PY combine.py ../$O/east_bay_pci.geojson ../$O/east_bay_pci.csv ../$O/summary.json $CITY_ARGS

# Bike network: needs data/out/bikeways.geojson from ./run_bikeways.sh.
if [ -s ../$O/bikeways.geojson ]; then
  echo "==> bike network overlay"
  $PY bike_overlay.py ../$O/east_bay_pci.geojson ../$O/bikeways.geojson \
       ../$O/bike_offstreet.geojson ../$O/summary.json
else
  echo "==> no bikeways.geojson; run ./run_bikeways.sh first to include the bike layer"
  echo '{"type":"FeatureCollection","features":[]}' > ../$O/bike_offstreet.geojson
fi
$PY pack.py ../$O/east_bay_pci.geojson ../$O/berkeley_paving_plan.geojson \
     ../$O/berkeley_moratorium.geojson ../$O/oakland_paving_plan.geojson \
     ../$O/oakland_moratorium.geojson ../$O/bike_offstreet.geojson \
     ../$O/summary.json ../$O/payload.json
$PY build_map.py ../$O/payload.json ../east_bay_pavement_map.html
echo "==> done: east_bay_pavement_map.html"
