// Where the PMTiles archive lives. This is the only file to edit when moving
// tiles between hosts.
//
// Local default: the file written by scripts/build_tiles.sh, resolved relative
// to the page so it works from a GitHub Pages project subpath.
//
// To serve from object storage, set an absolute URL, e.g.
//   export const TILES_URL = "https://tiles.example.com/zctas.pmtiles";
//   export const TILES_URL = "https://pub-<hash>.r2.dev/zctas.pmtiles";
// The bucket must allow cross-origin GETs *with* the Range header and expose
// Content-Range - see scripts/r2-cors.json and the README.
//
// ?tiles=<url> overrides at runtime, for testing a bucket before committing.
const override = new URLSearchParams(location.search).get("tiles");

export const TILES_URL =
  override || new URL("tiles/zctas.pmtiles", location.href).href;
