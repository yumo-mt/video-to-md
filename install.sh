#!/usr/bin/env bash
# One interactive macOS installer for video-to-md. Re-running is safe.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "目前只支持 macOS。" >&2
  exit 1
fi

BREW_BIN=""
find_brew() {
  if command -v brew >/dev/null 2>&1; then
    BREW_BIN="$(command -v brew)"
  elif [[ -x /opt/homebrew/bin/brew ]]; then
    BREW_BIN=/opt/homebrew/bin/brew
  elif [[ -x /usr/local/bin/brew ]]; then
    BREW_BIN=/usr/local/bin/brew
  fi
  if [[ -n "$BREW_BIN" ]]; then
    eval "$("$BREW_BIN" shellenv)"
  fi
}

find_python() {
  PYTHON_BIN=""
  if command -v python3.13 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.13)"
  elif [[ -n "$BREW_BIN" && -x "$("$BREW_BIN" --prefix python@3.13 2>/dev/null)/bin/python3.13" ]]; then
    PYTHON_BIN="$("$BREW_BIN" --prefix python@3.13)/bin/python3.13"
  fi
}

show_status() {
  find_brew
  find_python
  echo "Homebrew: ${BREW_BIN:-未安装}"
  echo "Python 3.13: ${PYTHON_BIN:-未安装}"
  echo "FFmpeg: $(command -v ffmpeg || echo 未安装)"
  echo "FFprobe: $(command -v ffprobe || echo 未安装)"
  if [[ -d /Applications/Google\ Chrome.app || -d "$HOME/Applications/Google Chrome.app" ]]; then
    echo "浏览器: Google Chrome"
  else
    echo "浏览器: 将安装 Playwright Chromium"
  fi
  echo "Python 环境: $([[ -x .venv/bin/python ]] && echo 已创建 || echo 待创建)"
  echo "模型: $([[ -d models ]] && echo 请运行自检确认 || echo 待下载)"
}

if [[ "${1:-}" == "--check" ]]; then
  show_status
  if [[ -x .venv/bin/python ]]; then
    .venv/bin/python scripts/setup.py || true
  fi
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo "用法: bash install.sh [--check]" >&2
  exit 2
fi

show_status
echo
echo "本脚本会补齐缺失的系统依赖、创建项目专用 Python 环境，并下载所需语音模型。"
echo "可能需要管理员密码、网络连接和至少约 6 GB 空闲磁盘；已有文件和模型会复用。"
if [[ ! -t 0 ]]; then
  echo "请在交互式终端运行：bash install.sh" >&2
  exit 1
fi
read -r -p "继续安装？[y/N] " ANSWER
case "$ANSWER" in y|Y|yes|YES) ;; *) echo "已取消"; exit 0 ;; esac

if [[ -z "$PYTHON_BIN" || ! -x "$(command -v ffmpeg || true)" || ! -x "$(command -v ffprobe || true)" ]]; then
  if [[ -z "$BREW_BIN" ]]; then
    echo "正在从 Homebrew 官方安装脚本安装 Homebrew…"
    BREW_INSTALLER="$(mktemp)"
    trap 'rm -f "$BREW_INSTALLER"' EXIT
    curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREW_INSTALLER"
    /bin/bash "$BREW_INSTALLER"
    rm -f "$BREW_INSTALLER"
    trap - EXIT
    find_brew
    if [[ -z "$BREW_BIN" ]]; then
      echo "Homebrew 未成功安装。请查看 https://brew.sh/ 后重试。" >&2
      exit 1
    fi
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "安装 Python 3.13…"
  "$BREW_BIN" install python@3.13
  find_python
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "未找到 Python 3.13，请检查 Homebrew 安装结果。" >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "安装 FFmpeg…"
  "$BREW_BIN" install ffmpeg
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "创建项目专用 Python 环境…"
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f config.json ]]; then
  cp config.example.json config.json
fi

if [[ -d /Applications/Google\ Chrome.app || -d "$HOME/Applications/Google Chrome.app" ]]; then
  echo "使用已安装的 Google Chrome。"
else
  echo "安装 Playwright Chromium…"
  .venv/bin/python -m playwright install chromium
  .venv/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('config.json')
cfg = json.loads(p.read_text(encoding='utf-8'))
if cfg.get('browser_channel', 'chrome') == 'chrome':
    cfg['browser_channel'] = 'chromium'
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
fi

CURRENT_VAULT="$(.venv/bin/python - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('config.json').read_text(encoding='utf-8')).get('vault') or '')
PY
)"
if [[ -z "$CURRENT_VAULT" ]]; then
  echo "如果已有 Obsidian 知识库，可输入其文件夹路径；留空则稍后配置。"
  read -r -p "Obsidian vault 路径: " VAULT_PATH
  if [[ -n "$VAULT_PATH" ]]; then
    VIDEO_TO_MD_VAULT="$VAULT_PATH" .venv/bin/python - <<'PY'
import json
import os
from pathlib import Path
p = Path('config.json')
vault = Path(os.environ['VIDEO_TO_MD_VAULT']).expanduser().resolve()
if not vault.is_dir():
    raise SystemExit(f'知识库目录不存在: {vault}')
cfg = json.loads(p.read_text(encoding='utf-8'))
cfg['vault'] = str(vault)
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
  fi
fi

echo "下载当前识别引擎所需模型（已存在的模型会跳过）…"
.venv/bin/python scripts/setup.py --models
.venv/bin/python scripts/setup.py

SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
SKILL_LINK="$SKILLS_DIR/video-to-md"
if [[ "$ROOT_DIR" != "$SKILL_LINK" ]]; then
  mkdir -p "$SKILLS_DIR"
  if [[ ! -e "$SKILL_LINK" && ! -L "$SKILL_LINK" ]]; then
    ln -s "$ROOT_DIR" "$SKILL_LINK"
    echo "已将 Skill 加入 Codex: $SKILL_LINK"
  elif [[ -L "$SKILL_LINK" && "$(readlink "$SKILL_LINK")" == "$ROOT_DIR" ]]; then
    echo "Codex Skill 已就绪: $SKILL_LINK"
  else
    echo "安装已完成，但 $SKILL_LINK 已存在且指向其他目录；请自行检查 Skill 目录。" >&2
    exit 1
  fi
fi

echo "安装完成。重新打开 Codex 聊天，即可使用 video-to-md Skill。"
