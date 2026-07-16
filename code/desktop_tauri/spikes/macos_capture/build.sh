#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/.build"
OUTPUT="${BUILD_DIR}/macos-capture-spike"
ARCH="$(uname -m)"

case "${ARCH}" in
  arm64|x86_64) ;;
  *)
    echo "unsupported macOS architecture: ${ARCH}" >&2
    exit 2
    ;;
esac

mkdir -p "${BUILD_DIR}"
xcrun swiftc \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -target "${ARCH}-apple-macos13.0" \
  -framework AVFoundation \
  -framework CoreGraphics \
  -framework CoreMedia \
  -framework ScreenCaptureKit \
  -Xlinker -sectcreate \
  -Xlinker __TEXT \
  -Xlinker __info_plist \
  -Xlinker "${SCRIPT_DIR}/Info.plist" \
  "${SCRIPT_DIR}/Sources/MacOSCaptureSpike/main.swift" \
  -o "${OUTPUT}"

echo "${OUTPUT}"
