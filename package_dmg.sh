#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
APP="dist/VideoToMD.app"
DMG="dist/VideoToMD-arm64.dmg"
if [[ ! -d "$APP" ]]; then
  echo "请先运行 bash build_mac.sh 生成 .app。" >&2
  exit 1
fi

STAGE="$(mktemp -d "$PWD/dist/dmg-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/VideoToMD.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "VideoToMD" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
hdiutil verify "$DMG"
echo "已生成：$DMG"
