#!/usr/bin/env bash
# GeoJSON -> a single PMTiles archive the browser range-requests.
#
# Only the attributes the map paints or filters on are kept: labels and the
# detail-panel numbers would otherwise be repeated in every one of the eight
# zoom levels, which was most of the weight. Those live in data/zctas.json and
# are joined client-side by ZIP code.
set -euo pipefail
cd "$(dirname "$0")/.."

IN=web/data/zctas.geojson
OUT=web/tiles/zctas.pmtiles

[ -f "$IN" ] || { echo "missing $IN - run build_dataset.py first"; exit 1; }
mkdir -p "$(dirname "$OUT")"

tippecanoe \
  --output="$OUT" \
  --layer=zctas \
  --name="ZCTA growth 2011-2024" \
  --minimum-zoom=3 --maximum-zoom=10 \
  --maximum-tile-bytes=3000000 \
  --simplification=6 \
  --no-tiny-polygon-reduction \
  -y zcta -y state -y pop_2024 -y comparable_hu -y boundary_changed \
  -y pop_pct -y hu_pct -y pop_change -y hu_change \
  --force \
  "$IN"

echo "tiles: $(du -h "$OUT" | cut -f1) in $OUT"
