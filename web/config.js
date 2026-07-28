// Where the PMTiles archive lives. This is the only file to edit when moving
// tiles between hosts.
//
// Live: the public r2.dev URL of the Cloudflare R2 bucket `zcta-tiles`. CORS is
// applied from scripts/r2-cors.json - cross-origin GETs with the Range header,
// with Content-Range exposed back. Verified: preflight returns 204 with
// Access-Control-Allow-Headers: range, and a ranged GET returns 206 with
// Content-Range plus Access-Control-Expose-Headers.
//
// If the map is blank, check that pairing first - it is the usual culprit, and
// a plain 206 in curl does NOT prove it, since curl ignores CORS entirely. Send
// an Origin header and look for access-control-allow-origin in the response.
//
// To move hosts, replace this one string. The bucket must allow cross-origin
// GETs *with* the Range header and expose Content-Range.
//
// The key is versioned by maxzoom, and that is load-bearing: objects go up with
// `immutable, max-age=31536000`, so a rebuild MUST land on a new key and update
// this line. Reusing a key strands clients on the old archive for a year. The
// previous z10 build is still at .../zctas.pmtiles if this one needs backing out.
//
// ?tiles=<url> overrides at runtime, for testing a bucket before committing;
// ?tiles=tiles/zctas.pmtiles falls back to a local build under web/tiles/.
const override = new URLSearchParams(location.search).get("tiles");

export const TILES_URL =
  override ||
  "https://pub-87663236083743889aff2a008693c67f.r2.dev/zctas-z13.pmtiles";
