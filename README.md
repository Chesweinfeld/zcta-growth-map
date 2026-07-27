# Where America grew — ZCTA growth map

An interactive choropleth of population and housing growth for every U.S. ZIP
Code Tabulation Area, built from Census bulk data and served locally.

```bash
python3 scripts/serve.py     # http://127.0.0.1:8787
```

Live site: **GitHub Pages**, published from `web/` by
`.github/workflows/pages.yml` on every push to `main`.

## What it shows

Four metrics, switchable: population %, housing-unit %, people added, units
added. Hover for a ZCTA, click for its full detail, filter by state or minimum
population, and search a ZIP code to fly to it. The Top 100 panel re-ranks
under whatever filters are active.

Top of the list, unsurprisingly, is exurban master-planned development:
Lakewood Ranch FL (+803%), Nocatee FL (+747%), Fulshear TX (+727%), Enterprise
NV (+643%), Prosper TX (+447%). By raw headcount it is Katy TX (+84,349) and
northwest Houston.

## The time window

**ACS 2007–2011 five-year → ACS 2020–2024 five-year: 13 years, not 15.**

ZCTA-level data does not exist before the 2011 release — the ACS only began
publishing ZCTAs when it adopted 2010 census geography — and 2024 is the newest
release. This is the widest window the ACS offers for this geography. (The
Decennial census would reach back to 2010 exactly, but mixing a complete count
with a survey estimate makes the change figure a methodology artifact as much
as a measurement.)

Both endpoints are five-year *estimates* with sampling error. Small ZCTAs are
the least certain, which is why the pipeline and the UI both apply a floor.

## Boundary changes — the thing that quietly breaks ZCTA time series

ZCTAs were redrawn between the 2010 and 2020 vintages. A ZIP code can cover
different ground at the two endpoints, so its "growth" is partly a mapping
change. `build_dataset.py` measures this from the Census 2010→2020 ZCTA
relationship file: for each 2020 ZCTA, the share of its land area that came
from the same-numbered 2010 ZCTA.

The median share is 0.95 — nearly everything moved a little — so the gate is
set at **0.75**, flagging the 3,425 ZCTAs where at least a quarter of the area
is new territory. Those are excluded from rankings by default and marked in the
detail panel; the "Hide ZCTAs whose boundaries were redrawn" checkbox controls
whether they are drawn at all.

Two further guards: ZCTAs need ≥1,000 people at both endpoints to be ranked,
and ≥400 housing units for the housing metrics (dorm, base and prison ZCTAs
hold thousands of people in almost no housing units, which turns any
construction into a meaningless percentage).

Of 32,921 mapped ZCTAs, **21,229 are rankable**.

## Pipeline

```bash
python3 scripts/fetch_acs.py      # Census API -> data/raw/acs_{2011,2024}.csv
python3 scripts/build_dataset.py  # join + flags -> web/data/*
bash    scripts/build_tiles.sh    # GeoJSON -> web/tiles/zctas.pmtiles
```

| Step | Source | Notes |
|---|---|---|
| `fetch_acs.py` | ACS 5-year API, tables B01003 (population) and B25001 (housing units) | Needs `CENSUS_API_KEY` in `.env`. Retries on the bogus "Invalid Key" HTML the API returns when it throttles a burst. |
| `build_dataset.py` | + 2020 cartographic ZCTA boundaries, ZCTA↔place/county relationship files | Computes metrics, place labels, boundary-stability flags, centroids, and the national roll-up. |
| `build_tiles.sh` | tippecanoe | z3–z10 into a single `web/tiles/zctas.pmtiles`. `--no-tiny-polygon-reduction` so dense urban ZCTAs keep their own color instead of being merged into neighbors. Only the nine attributes the map paints or filters on are kept (`-y`). |

Outputs: `web/data/` (summary, per-ZCTA sidecar, state outlines),
`web/tiles/zctas.pmtiles` (34 MB), and `data/zcta_growth.csv` — the full flat
table if you want to do your own analysis.

## Performance

The first build shipped 111 MB of loose `.pbf` tiles across 16,463 files, and
3 MB of JSON blocking the first paint. Three changes:

- **Attributes trimmed to what the map uses.** Labels and detail-panel numbers
  were being repeated in all eight zoom levels; they now live once in
  `data/zctas.json` and are joined client-side by ZIP. Roughly 3.5× off the tiles.
- **One PMTiles archive instead of 16,463 files**, read by HTTP range request.
  Compression is on (it was disabled for the old dev server).
- **The sidecar loads after the map paints**, not before, and is not awaited.

Result: **111 MB → 34 MB**, first paint ~640 KB → ~180 KB. The map is
interactive before the sidecar lands; the ZIP search stays disabled until it
does, and the ranking fills in.

Range requests are the one hosting requirement. GitHub Pages supports them;
`scripts/serve.py` implements them so local development matches.

Raw downloads land in `data/raw/` and `data/geo/`, both gitignored. To refresh
from scratch, delete `data/raw/*.csv` and re-run.

## Front end

MapLibre GL and pmtiles (plus fflate, which pmtiles needs to inflate tiles),
vendored in `web/vendor/` — no CDN, no build step, no npm install. The pmtiles
ESM build imports `fflate` as a bare specifier, which browsers cannot resolve;
the vendored copy is rewritten to a relative path.
Basemap tiles come from CARTO (attribution in the map corner); the "Show street
basemap" checkbox turns them off for an offline-clean view with just state
outlines.

The diverging blue↔red scale is fixed, not quantile-derived, so a ZCTA does not
change color when you change a filter. Nine classes, symmetric about a neutral
band at ±2%. Light and dark are separately stepped palettes, both validated for
lightness monotonicity and contrast against their own surface; the palette lives
in `PALETTE` in `app.js` and drives both the map and the legend swatches.

Note that a browser extension that force-darkens pages will invert the panel
without touching the map canvas, which looks like a theming bug but isn't — use
the ◐ button to set the theme explicitly.

## Caveats worth repeating before anyone quotes a number

- Percentages on small ZCTAs are noisy; the ±1,000-person floor helps but does
  not eliminate this.
- A "fast-growing ZCTA" is often a ZIP code that was mostly empty land in 2011.
  High percentage growth and large absolute growth are different maps — switch
  between them.
- Boundary-flagged ZCTAs are excluded from rankings, not from reality; a ZIP
  code that was split may genuinely have boomed.

## Deploying

The site is static and self-contained under `web/`. To publish:

1. Create an empty GitHub repo (public — Pages is free for public repos).
2. `git remote add origin git@github.com:<you>/<repo>.git && git push -u origin main`
3. Repo **Settings → Pages → Source: GitHub Actions**. The included workflow
   does the rest.

The link is `https://<you>.github.io/<repo>/`. All asset paths are relative, so
the project subpath works without configuration.

One caveat about committing tiles: `zctas.pmtiles` is a 34 MB binary, and every
rebuild you commit adds another 34 MB to history that `git clone` will carry
forever. Fine occasionally; if you end up regenerating often, move the archive
to object storage (Cloudflare R2, S3) and point the `zctas` source URL at it —
the client code needs no other change. Don't reach for Git LFS: GitHub Pages
serves LFS pointer files, not the content, and the map breaks.

Also note the basemap comes from CARTO's public tile service. That is someone
else's infrastructure; at real traffic, self-host or switch providers.
