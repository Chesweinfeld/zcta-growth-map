#!/usr/bin/env bash
# GeoJSON -> a single PMTiles archive the browser range-requests.
#
# Full resolution on purpose: z13 at tippecanoe's default detail. Zooming in has
# to stay sharp, so the speed work lives in the client (paint-driven filters,
# feature-state hover) rather than in coarser geometry.
#
# maxzoom was 10, which looked fine until you zoomed past it - MapLibre overzooms
# beyond maxzoom, stretching z10 geometry, and flyTo() lands at 10.5 already. z13
# costs archive size (34 MB -> 192 MB) but NOT load speed: PMTiles is read by
# byte range, so a client only ever fetches the tiles it displays, and the root
# directory grew to just 0.8 KB.
#
# The output key is versioned by maxzoom on purpose. Objects are uploaded with
# `immutable, max-age=31536000`, so reusing a key would leave clients on a stale
# archive for a year. New build -> new name -> update TILES_URL in web/config.js.
#
# Only the attributes the map paints or filters on are kept: labels and the
# detail-panel numbers would otherwise be repeated in every one of the eight
# zoom levels, which was most of the weight. Those live in data/zctas.json and
# are joined client-side by ZIP code.
set -euo pipefail
cd "$(dirname "$0")/.."

IN=web/data/zctas.geojson
OUT=web/tiles/zctas-z13-tiger-full.pmtiles

[ -f "$IN" ] || { echo "missing $IN - run build_dataset.py first"; exit 1; }
mkdir -p "$(dirname "$OUT")"

tippecanoe \
  --output="$OUT" \
  --layer=zctas \
  --name="ZCTA growth 2011-2024" \
  --minimum-zoom=3 --maximum-zoom=13 \
  --maximum-tile-bytes=3000000 \
  --simplification=6 \
  --no-tiny-polygon-reduction \
  -y zcta -y state -y pop_2024 -y comparable_hu -y boundary_changed \
  -y pop_pct -y hu_pct -y pop_change -y hu_change \
  --force \
  "$IN"

echo "tiles: $(du -h "$OUT" | cut -f1) in $OUT"
