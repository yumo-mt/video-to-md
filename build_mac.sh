#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "当前只构建 Apple Silicon Mac 版本。" >&2
  exit 1
fi
if [[ ! -x .venv/bin/pyinstaller ]]; then
  echo "请先在项目环境安装 requirements-app.txt 和 pyinstaller。" >&2
  exit 1
fi
if [[ ! -d vendor-browsers ]] || ! find vendor-browsers -maxdepth 1 -name 'chromium-*' | grep -q .; then
  echo "请先运行 PLAYWRIGHT_BROWSERS_PATH=\"$PWD/vendor-browsers\" .venv/bin/python -m playwright install chromium --no-shell" >&2
  exit 1
fi
mkdir -p vendor-bin
ICONSET="${TMPDIR:-/tmp}/video-to-md.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -s format png -z "$size" "$size" assets/app-icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -s format png -z "$double" "$double" assets/app-icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
.venv/bin/python - "$ICONSET" assets/app-icon.icns <<'PY'
import sys
from pathlib import Path

iconset, output = Path(sys.argv[1]), Path(sys.argv[2])
chunks = b""
for size, kind in ((16, b"icp4"), (32, b"icp5"), (64, b"icp6"),
                   (128, b"ic07"), (256, b"ic08"), (512, b"ic09"), (1024, b"ic10")):
    name = f"icon_{size}x{size}.png" if size in (16, 32, 128, 256, 512) else f"icon_{size // 2}x{size // 2}@2x.png"
    data = (iconset / name).read_bytes()
    chunks += kind + (len(data) + 8).to_bytes(4, "big") + data
output.write_bytes(b"icns" + (len(chunks) + 8).to_bytes(4, "big") + chunks)
PY
FFMPEG_SOURCE="$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
cp "$FFMPEG_SOURCE" vendor-bin/ffmpeg
chmod +x vendor-bin/ffmpeg
export PYINSTALLER_CONFIG_DIR="${TMPDIR:-/tmp}/video-to-md-pyinstaller"
.venv/bin/pyinstaller --noconfirm --windowed --onedir --name VideoToMD --icon assets/app-icon.icns \
  --paths scripts --collect-all funasr --collect-all modelscope \
  --collect-all playwright --add-binary 'vendor-bin/ffmpeg:bin' mac_app.py
ditto vendor-browsers dist/VideoToMD.app/Contents/Resources/browsers
codesign --force --deep --sign - dist/VideoToMD.app
echo "已生成：dist/VideoToMD.app"
