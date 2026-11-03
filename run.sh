#!/usr/bin/env bash
# User-facing commands; Python and the virtual environment stay internal.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'HELP'
用法:
  bash run.sh extract '<公开抖音视频链接>' [--vault '<知识库路径>']
  bash run.sh preview 'output/<作品 ID>'
  bash run.sh archive 'output/<作品 ID>' [--vault '<知识库路径>']
  bash run.sh check

首次使用请先运行 bash install.sh。
HELP
}

ACTION="${1:-}"
case "$ACTION" in
  -h|--help|help|"") usage; exit 0 ;;
  check) exec bash install.sh --check ;;
  extract|preview|archive) shift ;;
  *) usage >&2; exit 2 ;;
esac

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "尚未安装项目运行环境。请先运行：bash install.sh" >&2
  exit 2
fi
if [[ $# -eq 0 ]]; then
  usage >&2
  exit 2
fi

case "$ACTION" in
  extract) exec "$PYTHON_BIN" scripts/dy_pipeline.py "$@" --no-video --video-only ;;
  preview) exec "$PYTHON_BIN" scripts/note_builder.py "$@" --dry-run --require-knowledge ;;
  archive) exec "$PYTHON_BIN" scripts/note_builder.py "$@" --require-knowledge ;;
esac
