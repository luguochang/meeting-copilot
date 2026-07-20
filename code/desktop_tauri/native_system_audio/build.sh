#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$ROOT/Sources/MeetingCopilotNativeSystemAudio/main.swift"
INFO_PLIST="$ROOT/Info.plist"
OUTPUT="${1:-$ROOT/.build/meeting-copilot-native-system-audio}"
ARCH="$(uname -m)"

case "$ARCH" in
  arm64|x86_64) ;;
  *)
    printf 'unsupported macOS architecture: %s\n' "$ARCH" >&2
    exit 2
    ;;
esac

mkdir -p "$(dirname -- "$OUTPUT")"
xcrun swiftc \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -target "$ARCH-apple-macos13.0" \
  -framework AVFoundation \
  -framework CoreGraphics \
  -framework CoreMedia \
  -framework Foundation \
  -framework ScreenCaptureKit \
  -Xlinker -sectcreate \
  -Xlinker __TEXT \
  -Xlinker __info_plist \
  -Xlinker "$INFO_PLIST" \
  "$SOURCE" \
  -o "$OUTPUT"
chmod 755 "$OUTPUT"
