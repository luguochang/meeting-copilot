#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_DIR="$(mktemp -d)"
FONT_URL="https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf"
SOURCE_FONT="$TEMP_DIR/NotoSansSC-VF.ttf"
RANGED_FONT="$TEMP_DIR/NotoSansSC-site-range.ttf"
OUTPUT_FONT="$ROOT_DIR/public/fonts/talktrace-sans-sc.woff2"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

command -v rg >/dev/null
command -v uvx >/dev/null

curl -L --fail --retry 2 -o "$SOURCE_FONT" "$FONT_URL"

uvx --from fonttools fonttools varLib.instancer \
  "$SOURCE_FONT" \
  wght=400:750 \
  --output="$RANGED_FONT"

mkdir -p "$(dirname "$OUTPUT_FONT")"

uvx --from fonttools --with brotli pyftsubset \
  "$RANGED_FONT" \
  --text-file=<(rg --no-filename --no-line-number '.+' \
    "$ROOT_DIR/src" \
    "$ROOT_DIR/index.html" \
    "$ROOT_DIR/public/releases/latest.json" \
    "$ROOT_DIR/public/site.webmanifest") \
  --output-file="$OUTPUT_FONT" \
  --flavor=woff2 \
  --layout-features='*' \
  --no-hinting

echo "Generated $OUTPUT_FONT"
