#!/usr/bin/env bash
# Push the PMTiles archive to object storage, so it stays out of git history.
#
#   BUCKET=zcta-tiles bash scripts/upload_tiles.sh          # Cloudflare R2
#   TARGET=s3 BUCKET=my-bucket bash scripts/upload_tiles.sh # S3
#
# Then set TILES_URL in web/config.js to the public URL and commit that.
set -euo pipefail
cd "$(dirname "$0")/.."

FILE=web/tiles/zctas-z13.pmtiles
KEY=${KEY:-zctas-z13.pmtiles}
TARGET=${TARGET:-r2}
: "${BUCKET:?set BUCKET to the bucket name}"

[ -f "$FILE" ] || { echo "missing $FILE - run scripts/build_tiles.sh first"; exit 1; }
echo "uploading $(du -h "$FILE" | cut -f1) to $TARGET://$BUCKET/$KEY"

case "$TARGET" in
  r2)
    # Range requests need the object served as an opaque binary; wrangler would
    # otherwise guess a type from the extension it does not know.
    npx --yes wrangler r2 object put "$BUCKET/$KEY" \
      --file "$FILE" \
      --content-type application/octet-stream \
      --cache-control "public, max-age=31536000, immutable" \
      --remote
    echo
    echo "Now apply CORS once (edit the origins in scripts/r2-cors.json first):"
    echo "  npx wrangler r2 bucket cors set $BUCKET --file scripts/r2-cors.json"
    ;;
  s3)
    aws s3 cp "$FILE" "s3://$BUCKET/$KEY" \
      --content-type application/octet-stream \
      --cache-control "public, max-age=31536000, immutable"
    echo
    echo "Now apply CORS once (same rules, S3 syntax):"
    echo "  aws s3api put-bucket-cors --bucket $BUCKET \\"
    echo "    --cors-configuration file://scripts/s3-cors.json"
    ;;
  *)
    echo "unknown TARGET '$TARGET' (expected r2 or s3)"; exit 1;;
esac
