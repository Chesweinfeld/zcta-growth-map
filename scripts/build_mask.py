"""Build the "no ZCTA here" mask: US land that belongs to no ZCTA.

ZCTAs are built from address ranges and do not tile the country - they cover
about 80% of its land area. The remaining fifth (wilderness, parks, desert,
large water, some military land) has no polygon at all, so without this mask
those holes render as bare basemap, reading as "nothing here" rather than
"no estimate", which is what they actually are.

Output:
  web/data/nozcta.geojson   ~19 MB, gitignored - an intermediate, like
                            zctas.geojson. Tile it, do not ship it:

  tippecanoe --output=web/tiles/nozcta.pmtiles --layer=nozcta \
    --name="Land with no ZCTA" --minimum-zoom=3 --maximum-zoom=13 \
    --maximum-tile-bytes=1000000 --simplification=6 \
    --no-tiny-polygon-reduction --force web/data/nozcta.geojson

As GeoJSON the mask is 6 MB gzipped and would block first paint. As PMTiles it
is range-read with a 0.4 KB root directory, so it costs essentially nothing
until you look at a region that has holes.

Takes ~2 minutes, almost all of it the union of 33,791 polygons.
"""

import time
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data" / "geo"
OUT = ROOT / "web" / "data"

# ZCTAs are simplified before the union purely to make it tractable. This opens
# hairline gaps between neighbours that would otherwise paint as grey slivers
# along every ZCTA border, which MIN_PART_AREA then removes.
UNION_TOL = 0.0001      # ~11 m
# Land simplification, matching the state outlines.
LAND_TOL = 0.0005       # ~55 m
# Output simplification. The mask sits under the choropleth, so its only visible
# edge is where it meets a ZCTA or the coast.
OUT_TOL = 0.0002        # ~22 m
# Drop union artefacts. 99.98% of the masked area lives in parts at or above
# this size; the rest is 314,431 slivers contributing 0.02%. Real gaps - parks,
# wilderness, water - are orders of magnitude larger.
MIN_PART_AREA = 100_000  # m^2, measured in EPSG:5070 equal-area


def main() -> None:
    t = time.time()

    z = gpd.read_file(
        f"zip://{GEO / 'tl_2020_us_zcta520.zip'}!tl_2020_us_zcta520.shp",
        columns=["ZCTA5CE20"],
    )
    z["geometry"] = z.geometry.simplify(UNION_TOL, preserve_topology=True)
    covered = z.geometry.union_all()
    print(f"unioned {len(z):,} ZCTAs  {time.time() - t:.0f}s", flush=True)

    s = gpd.read_file(f"zip://{GEO / 'cb_2020_us_state_500k.zip'}")[["STUSPS", "geometry"]]
    # Four Pacific territories the 20m file omits; none has ZCTA coverage, so
    # they would otherwise become lone grey shapes in the ocean.
    s = s[~s["STUSPS"].isin(["AS", "GU", "MP", "VI"])]
    s["geometry"] = s.geometry.simplify(LAND_TOL, preserve_topology=True)
    land = s.geometry.union_all()

    gap = land.difference(covered)
    parts = gpd.GeoDataFrame(
        geometry=list(gap.geoms) if gap.geom_type == "MultiPolygon" else [gap],
        crs=s.crs,
    )
    before = len(parts)
    parts = parts[parts.to_crs(5070).area >= MIN_PART_AREA].copy()
    parts["geometry"] = parts.geometry.simplify(OUT_TOL, preserve_topology=True)
    parts = parts[~parts.geometry.is_empty & parts.geometry.notna()]

    p = OUT / "nozcta.geojson"
    p.write_text(parts.to_json())
    area = parts.to_crs(5070).area.sum() / 1e6
    print(f"kept {len(parts):,} of {before:,} parts; dropped the rest as slivers")
    print(f"masked area {area:,.0f} km2  ->  {p} ({p.stat().st_size / 1e6:.1f} MB)")
    print(f"done in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
