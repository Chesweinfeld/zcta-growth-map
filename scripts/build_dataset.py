"""Join the two ACS vintages into one growth table + a GeoJSON of the polygons.

Outputs (web/data/):
  zctas.geojson   full-resolution 2020-vintage ZCTA polygons with growth props
  summary.json    national roll-up + metric distributions for the legend

Growth metrics are 2007-2011 ACS -> 2020-2024 ACS (13 years apart; ZCTA-level
ACS 5-year data does not exist before the 2011 release, so this is the widest
window available).
"""

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GEO = ROOT / "data" / "geo"
OUT = ROOT / "web" / "data"

# Small-base guard: a ZCTA of 300 people going to 900 is a 200% "boom" that is
# mostly ACS sampling noise. Both endpoints must clear this.
MIN_POP = 1000
# Same idea for housing units.
MIN_HU = 400
# Douglas-Peucker tolerance in degrees. ~0.5 m: strips exactly-collinear noise
# from the source and nothing else. Deliberately below tippecanoe's own
# quantization at maxzoom (a z13 tile is ~4.9 km across over 4096 units, so
# ~1.2 m per unit), which makes the tiler the limiting factor rather than this
# step - the thing that should decide detail is the zoom level, not a constant
# chosen years earlier for a delivery mechanism the project no longer uses.
#
# This was 0.0025 (~250 m), which was catastrophic for urban ZCTAs - it left
# 10025 as a FIVE-vertex polygon and 10001 as thirteen, so ZIP boundaries cut
# straight across blocks instead of following streets. The comment justifying it
# said the file had to stay "small enough for the browser to handle as plain
# GeoJSON", but the browser stopped loading GeoJSON when the map moved to
# PMTiles; zctas.geojson is now only an intermediate feeding tippecanoe, and
# tippecanoe does its own per-zoom generalization (--simplification=6) while
# keeping full detail at maxzoom. Generalizing here just destroyed data the
# tiler would have kept.
SIMPLIFY_TOL = 0.000005
# Share of a 2020 ZCTA's land that came from the same-numbered 2010 ZCTA.
# Below this the number was re-cut enough that the comparison is not
# like-for-like. Almost every ZCTA moved a little between vintages (median
# share is 0.95), so a tight gate would throw out most of the country; 0.75
# targets the ones where a quarter of the area is genuinely new territory.
STABLE_AREA_SHARE = 0.75


def load_acs() -> pd.DataFrame:
    a = pd.read_csv(RAW / "acs_2011.csv", dtype={"zcta": str})
    b = pd.read_csv(RAW / "acs_2024.csv", dtype={"zcta": str})
    a["zcta"] = a["zcta"].str.zfill(5)
    b["zcta"] = b["zcta"].str.zfill(5)
    df = a.merge(b, on="zcta", suffixes=("_2011", "_2024"))

    # ACS uses negative sentinels for suppressed/unavailable cells.
    for c in ["pop_2011", "pop_2024", "housing_units_2011", "housing_units_2024"]:
        df.loc[df[c] < 0, c] = pd.NA

    df["pop_change"] = df["pop_2024"] - df["pop_2011"]
    df["pop_pct"] = (df["pop_change"] / df["pop_2011"].replace(0, pd.NA)) * 100
    df["hu_change"] = df["housing_units_2024"] - df["housing_units_2011"]
    df["hu_pct"] = (df["hu_change"] / df["housing_units_2011"].replace(0, pd.NA)) * 100

    df["comparable"] = (
        (df["pop_2011"] >= MIN_POP) & (df["pop_2024"] >= MIN_POP)
    ).fillna(False)
    # Housing needs its own guard: dorm/base/prison ZCTAs hold thousands of
    # people in ~zero housing units, which turns any construction into a
    # nonsense percentage.
    df["comparable_hu"] = (
        (df["housing_units_2011"] >= MIN_HU) & (df["housing_units_2024"] >= MIN_HU)
    ).fillna(False)
    return df


def load_boundary_stability() -> pd.DataFrame:
    """From the 2010->2020 ZCTA relationship file: for each 2020 ZCTA, what
    fraction of its land area came from the 2010 ZCTA with the same code."""
    rel = pd.read_csv(
        GEO / "tab20_zcta510_zcta520_natl.txt",
        sep="|",
        dtype=str,
        usecols=lambda c: c.startswith(("GEOID_ZCTA5_", "AREALAND_PART")),
    )
    rel.columns = [c.strip() for c in rel.columns]
    old = next(c for c in rel.columns if c.endswith("_10"))
    new = next(c for c in rel.columns if c.endswith("_20"))
    rel["AREALAND_PART"] = pd.to_numeric(rel["AREALAND_PART"], errors="coerce").fillna(0)

    total = rel.groupby(new)["AREALAND_PART"].sum()
    same = (
        rel[rel[old] == rel[new]].groupby(new)["AREALAND_PART"].sum().reindex(total.index).fillna(0)
    )
    share = (same / total.replace(0, pd.NA)).fillna(0)
    return pd.DataFrame({"zcta": share.index, "same_area_share": share.values})


def _dominant(path: Path, kind: str, col: str) -> pd.DataFrame:
    """For each ZCTA, the {place,county} it overlaps most by land area."""
    rel = pd.read_csv(
        path, sep="|", dtype=str,
        usecols=lambda c: c.strip() in
        {"GEOID_ZCTA5_20", f"NAMELSAD_{kind}_20", "AREALAND_PART"},
    )
    rel.columns = [c.strip() for c in rel.columns]
    rel["AREALAND_PART"] = pd.to_numeric(rel["AREALAND_PART"], errors="coerce").fillna(0)
    rel = rel.dropna(subset=[f"NAMELSAD_{kind}_20"])
    rel = rel.sort_values("AREALAND_PART", ascending=False).drop_duplicates("GEOID_ZCTA5_20")
    return rel[["GEOID_ZCTA5_20", f"NAMELSAD_{kind}_20"]].rename(
        columns={"GEOID_ZCTA5_20": "zcta", f"NAMELSAD_{kind}_20": col}
    )


def load_names() -> pd.DataFrame:
    """A human label per ZCTA: dominant place + state, e.g. '34211 - Bradenton, FL'.

    The relationship files carry place and county names but not the state, so
    the state comes from the county FIPS prefix via the state shapefile.
    """
    place = _dominant(GEO / "tab20_zcta520_place20_natl.txt", "PLACE", "place")

    cty = pd.read_csv(
        GEO / "tab20_zcta520_county20_natl.txt", sep="|", dtype=str,
        usecols=lambda c: c.strip() in
        {"GEOID_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART"},
    )
    cty.columns = [c.strip() for c in cty.columns]
    cty["AREALAND_PART"] = pd.to_numeric(cty["AREALAND_PART"], errors="coerce").fillna(0)
    cty = cty.dropna(subset=["GEOID_COUNTY_20"])
    cty = cty.sort_values("AREALAND_PART", ascending=False).drop_duplicates("GEOID_ZCTA5_20")
    cty["statefp"] = cty["GEOID_COUNTY_20"].str[:2]
    cty = cty.rename(columns={"GEOID_ZCTA5_20": "zcta",
                              "NAMELSAD_COUNTY_20": "county"})[["zcta", "county", "statefp"]]

    states = gpd.read_file(f"zip://{GEO / 'cb_2020_us_state_20m.zip'}")[["STATEFP", "STUSPS"]]
    cty = cty.merge(states.rename(columns={"STATEFP": "statefp", "STUSPS": "state"}),
                    on="statefp", how="left")

    names = cty.merge(place, on="zcta", how="left")
    names["place"] = names["place"].str.replace(
        r"\s+(city|town|village|borough|CDP|municipality)$", "", regex=True)
    names["label"] = names["place"].fillna(names["county"]) + ", " + names["state"].fillna("")
    return names[["zcta", "label", "place", "county", "state"]]


def load_geometry() -> gpd.GeoDataFrame:
    # TIGER/Line, not the cartographic boundary file. cb_2020_us_zcta520_500k is
    # pre-generalized by Census to 1:500,000 - fine for a national thumbnail,
    # but it cuts corners across city blocks, which is visible the moment you
    # zoom past a metro area. TIGER follows the actual boundary. It costs a
    # 528 MB download and a heavier build; the tiles are range-read, so it does
    # not cost load speed.
    zf = GEO / "tl_2020_us_zcta520.zip"
    with zipfile.ZipFile(zf) as z:
        name = next(n for n in z.namelist() if n.endswith(".shp"))
    gdf = gpd.read_file(f"zip://{zf}!{name}")
    code = next(c for c in gdf.columns if c.startswith("ZCTA5CE"))
    gdf = gdf.rename(columns={code: "zcta"})[["zcta", "ALAND20", "geometry"]]
    gdf["zcta"] = gdf["zcta"].astype(str).str.zfill(5)
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL, preserve_topology=True)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    return gdf


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    acs = load_acs()
    stability = load_boundary_stability()
    gdf = (
        load_geometry()
        .merge(acs, on="zcta", how="inner")
        .merge(stability, on="zcta", how="left")
        .merge(load_names(), on="zcta", how="left")
    )
    gdf["same_area_share"] = gdf["same_area_share"].fillna(0)
    gdf["boundary_changed"] = gdf["same_area_share"] < STABLE_AREA_SHARE

    # Density, so "growth" can be read against how built-up the place already was.
    sqmi = gdf["ALAND20"] / 2_589_988.0
    gdf["density_2024"] = (gdf["pop_2024"] / sqmi.replace(0, pd.NA)).round(1)

    keep = [
        "zcta", "label", "state", "pop_2011", "pop_2024", "pop_change", "pop_pct",
        "housing_units_2011", "housing_units_2024", "hu_change", "hu_pct",
        "density_2024", "comparable", "comparable_hu", "boundary_changed",
        "same_area_share",
        "geometry",
    ]
    gdf = gdf[keep]
    for c in ["pop_pct", "hu_pct"]:
        gdf[c] = pd.to_numeric(gdf[c], errors="coerce").replace(
            [float("inf"), float("-inf")], pd.NA).round(1)
    gdf["same_area_share"] = gdf["same_area_share"].round(3)
    for c in ["pop_2011", "pop_2024", "pop_change", "housing_units_2011",
              "housing_units_2024", "hu_change"]:
        gdf[c] = gdf[c].astype("Int64")

    gdf.to_file(OUT / "zctas.geojson", driver="GeoJSON", coordinate_precision=4)

    stable = ~gdf["boundary_changed"]
    ranked = gdf[gdf["comparable"] & stable]
    ranked_hu = gdf[gdf["comparable_hu"] & stable]
    pool = {"pop_pct": ranked, "pop_change": ranked,
            "hu_pct": ranked_hu, "hu_change": ranked_hu}
    summary = {
        "window": {"from": "ACS 2007-2011", "to": "ACS 2020-2024", "years": 13},
        "counts": {
            "zctas_mapped": int(len(gdf)),
            "comparable": int(gdf["comparable"].sum()),
            "boundary_changed": int(gdf["boundary_changed"].sum()),
            "ranked": int(len(ranked)),
        },
        "min_pop": MIN_POP,
        "national": {
            "pop_2011": int(gdf["pop_2011"].sum()),
            "pop_2024": int(gdf["pop_2024"].sum()),
            "hu_2011": int(gdf["housing_units_2011"].sum()),
            "hu_2024": int(gdf["housing_units_2024"].sum()),
            "share_growing": round(float((ranked["pop_pct"] > 0).mean()), 3),
            "median_pop_pct": round(float(ranked["pop_pct"].median()), 1),
        },
        "states": sorted(gdf["state"].dropna().unique().tolist()),
        "quantiles": {
            m: {str(q): float(pool[m][m].quantile(q))
                for q in [0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]}
            for m in ["pop_pct", "hu_pct", "pop_change", "hu_change"]
        },
        "top": {
            m: pool[m].nlargest(100, m)[
                ["zcta", "label", m, "pop_2011", "pop_2024"]
            ].to_dict("records")
            for m in ["pop_pct", "hu_pct", "pop_change", "hu_change"]
        },
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # State outlines, so the map still has structure with the basemap off.
    # Hairlines only - the no-ZCTA mask below carries its own coastline at 500k,
    # so this does not need to be accurate as a filled shape and can stay cheap.
    states = gpd.read_file(f"zip://{GEO / 'cb_2020_us_state_20m.zip'}")[["STUSPS", "geometry"]]
    states["geometry"] = states.geometry.simplify(0.02, preserve_topology=True)
    states.to_file(OUT / "states.geojson", driver="GeoJSON", coordinate_precision=3)

    # Flat table for the search box and the ranking panel (no geometry).
    table = pd.DataFrame(gdf.drop(columns="geometry"))
    table.to_csv(ROOT / "data" / "zcta_growth.csv", index=False)

    # One sidecar for every ZCTA: labels, centroids, and the detail-panel
    # numbers. It carries everything the tiles no longer need to repeat at each
    # zoom level, and is fetched after the map has already painted.
    cen = gdf.to_crs(3857).geometry.centroid.to_crs(4326)
    table["lon"] = cen.x.round(4).values
    table["lat"] = cen.y.round(4).values
    cols = ["zcta", "label", "state", "lon", "lat",
            "pop_2011", "pop_2024", "pop_pct", "pop_change",
            "housing_units_2011", "housing_units_2024", "hu_pct", "hu_change",
            "density_2024", "comparable", "comparable_hu", "boundary_changed"]
    rows = table[cols].astype(object).where(lambda d: d.notna(), None)
    (OUT / "zctas.json").write_text(
        json.dumps({"cols": cols, "rows": rows.values.tolist()}, separators=(",", ":"))
    )

    print(f"mapped {len(gdf):,} ZCTAs; {len(ranked):,} rankable "
          f"({gdf['boundary_changed'].sum():,} excluded as re-cut, "
          f"{(~gdf['comparable']).sum():,} below {MIN_POP} pop)")
    print(f"geojson: {(OUT / 'zctas.geojson').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
