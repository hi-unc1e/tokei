#!/bin/bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <version> <dmg-path> <output-json>" >&2
    exit 2
fi

VERSION="${1#v}"
DMG_PATH="$2"
OUTPUT_PATH="$3"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "invalid version: $1" >&2
    exit 2
fi
if [ ! -f "$DMG_PATH" ]; then
    echo "DMG not found: $DMG_PATH" >&2
    exit 2
fi

SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
printf '{\n  "tag_name": "v%s",\n  "download_url": "https://github.com/hi-unc1e/tokei/releases/download/v%s/Tokei-v%s.dmg",\n  "sha256": "%s"\n}\n' \
    "$VERSION" "$VERSION" "$VERSION" "$SHA256" > "$OUTPUT_PATH"
