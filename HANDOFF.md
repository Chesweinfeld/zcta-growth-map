# Handoff — ZCTA growth map

Context for a fresh session. Project lives at `~/Desktop/zcta-growth-map`.
Read `README.md` for the full picture; this file is what a new chat needs to
pick up work.

## What it is

An interactive choropleth of population and housing growth for all 32,921 U.S.
ZIP Code Tabulation Areas. MapLibre GL + PMTiles, static site, no build step.
Local: `python3 scripts/serve.py` → http://localhost:8787

## Decisions already made (don't relitigate)

- **Window is 13 years, not the 15 the user first asked for.** ACS 2007–2011 →
  ACS 2020–2024. ZCTA-level ACS data does not exist before the 2011 release.
  The user was told and accepted this. Decennial 2010 was rejected: mixing a
  complete count with a survey estimate makes the delta a methodology artifact.
- **Metrics**: population % and housing-unit % (user's pick), plus people added
  and units added.
- **Full tile resolution is deliberate.** z10, tippecanoe default detail. A
  coarser build (maxzoom 9 + `-D 10`) was tried and **explicitly rejected** by
  the user — "i still want to be able to zoom so you should not mess with the
  resolution". They are fine with a slow first load; they want interaction
  smooth. Do not shrink the archive by degrading geometry.
- **Hosting**: GitHub Pages for the app, object storage (R2) for the tiles,
  public link, no auth.

## Data caveats baked into the pipeline

- **ZCTA boundaries were redrawn between the 2010 and 2020 vintages**, so a
  naive join reports mapping changes as growth. `build_dataset.py` measures the
  share of each 2020 ZCTA's land that came from the same-numbered 2010 ZCTA
  (Census relationship file). Median is 0.95, so the gate is **0.75**, flagging
  3,425 ZCTAs. Excluded from rankings, flagged in the detail panel, toggleable.
- Rank guards: ≥1,000 people at both endpoints; ≥400 housing units for housing
  metrics (dorm/base/prison ZCTAs hold thousands of people in ~no housing).
- **21,229 of 32,921 are rankable.** Top results sanity-check against reality:
  Lakewood Ranch FL +803%, Nocatee FL +747%, Fulshear TX +727%, Prosper TX
  +447%; by headcount Katy TX +84,349.

## Architecture

```
scripts/fetch_acs.py      Census API -> data/raw/acs_{2011,2024}.csv
scripts/build_dataset.py  join + flags -> web/data/{zctas.json,summary.json,states.geojson}
                          + web/data/zctas.geojson (52MB intermediate, gitignored)
scripts/build_tiles.sh    tippecanoe -> web/tiles/zctas.pmtiles (34MB, gitignored)
scripts/upload_tiles.sh   push archive to R2 or S3
scripts/serve.py          dev server WITH range-request support (PMTiles needs it)
```

- Tiles carry only the 9 attributes the map paints/filters on. Labels and
  detail-panel numbers live once in `web/data/zctas.json` (3.5 MB, 1.3 MB gz),
  loaded **after** first paint and joined client-side by ZIP.
- `web/config.js` holds `TILES_URL` — the single line to change when tiles move.
  `?tiles=<url>` overrides at runtime for testing.
- Everything vendored in `web/vendor/` (maplibre, pmtiles, fflate). No CDN.

## Performance work (already done — the lag was client-side)

Filters compile to a `fill-opacity` expression rather than layer filters
(changing a layer filter re-parses every loaded tile from raw vector data — that
was the slider stutter). Hover/selection use `feature-state` with
`promoteId: "zcta"`. Theme/basemap toggles mutate the style in place instead of
`setStyle()`. Plus `fill-antialias: false`, `fadeDuration: 0`, no world copies,
bigger tile cache, more parse workers, repaints coalesced per frame.

`passesFilter()` in `app.js` mirrors the filter rules in JS — zero-opacity
features still emit pointer events, so hover must re-check.

## State of the repo

Local git, branch `main`, **two commits, nothing pushed**. `.git` is 2.9 MB —
the 34 MB PMTiles blob was removed from history on purpose (`git rm --cached` +
amend + `reflog expire` + `gc`). **Do not commit `web/tiles/`.** `.env` holds
the Census API key and is gitignored.

## Open items

1. **Need from user: bucket name + GitHub username.** They go into
   `web/config.js` (`TILES_URL`) and `scripts/{r2,s3}-cors.json` (origins).
2. Upload tiles: `BUCKET=<name> bash scripts/upload_tiles.sh`, then set CORS
   once. The bucket **must** allow the `Range` header and expose
   `Content-Range` — this is the failure mode to check first if the map is blank.
   A failed tile load shows an on-screen message naming the URL.
3. Push to GitHub, then Settings → Pages → Source: GitHub Actions. Workflow is
   already committed at `.github/workflows/pages.yml`.
4. The Cloudflare plugin is installed, but its MCP servers
   (`cloudflare-api`, `-bindings`, `-builds`, `-observability`) still need OAuth
   authorization in an interactive session before they can be used to verify the
   bucket/CORS from inside a chat.

## Gotchas discovered the hard way

- **Census API now requires a key for every request** and returns an HTML
  "Invalid Key" page when it throttles a burst — `fetch_acs.py` retries on
  non-JSON for this reason. Bulk files at www2.census.gov need no key.
- **MapLibre v6 has no default export** — named imports only.
- **The pmtiles ESM build imports `fflate` as a bare specifier**, which browsers
  can't resolve. The vendored copy is rewritten to a relative path.
- **Python's `SimpleHTTPRequestHandler` has no range support**, so PMTiles will
  not load locally without the custom handler in `serve.py`.
- **The in-app Browser pane has no WebGL2**, so the map cannot be verified
  there. Panel logic can be (it renders independently of the map by design).
  Chrome/Safari verification was done via screenshots at read-only access.
- **The user's Chrome force-darkens pages** (an extension), inverting the HTML
  panel but not the map canvas. Looks like a theming bug, isn't. The theme is
  stamped explicitly on `<html>` at boot so the panel and map can't disagree.
- Unexplained: the same tippecanoe flags produced 18.4 MB via `build_tiles.sh`
  but 9.5 MB run directly. Moot now that full resolution is the decision, but
  don't trust a size comparison unless both builds ran the same way.

## Verified vs not

Verified: the whole data pipeline, panel interactions (metric switch, state
filter, min-pop, ranking, search) against the current data layout, map rendering
in **Chrome and Safari**, hover + tooltip + sidecar join, paint-based filters
actually hiding features, range requests, and the GitHub Pages **subpath** URL
shape (`/repo-name/`).

Not verified: frame rates while dragging/zooming. Browsers were read-only in
that session, so no profiling. If the user reports stutter, ask which specific
interaction and profile that one.
