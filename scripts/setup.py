#!/usr/bin/env python3
"""检查环境；安装 Python 依赖和下载模型需显式传入选项。"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import config as cfg_mod  # noqa: E402


def check() -> list[str]:
    problems = cfg_mod.check()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        problems.append("未安装 playwright")
        sync_playwright = None
    channel = cfg_mod.load().get("browser_channel", "chrome")
    if channel == "chrome" and not any(p.exists() for p in (
        Path("/Applications/Google Chrome.app"),
        Path.home() / "Applications/Google Chrome.app",
    )):
        problems.append("未找到 Google Chrome；请安装 Chrome，或配置 browser_channel 为 chromium")
    elif channel == "chromium" and sync_playwright:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                problems.append("Playwright Chromium 未安装；请运行 bash install.sh")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="video-to-md 环境准备与自检")
    ap.add_argument("--install", action="store_true", help="安装 requirements.txt 中的 Python 依赖")
    ap.add_argument("--models", action="store_true", help="下载当前引擎需要的模型（约 1–2 GB）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.install:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    if args.models:
        from download_models import download_required
        download_required(cfg_mod.load())

    cfg = cfg_mod.load()
    problems = check()
    if args.json:
        print(json.dumps({"problems": problems}, ensure_ascii=False))
    else:
        print(f"Python: {sys.version.split()[0]} ({sys.executable})")
        print(f"FFmpeg: {shutil.which('ffmpeg') or '未安装'}")
        print(f"模型目录: {cfg['_models_dir']}")
        print(f"引擎: {cfg.get('engine', 'hybrid')}")
        print(f"Vault: {cfg.get('vault') or '未配置'}")
        print("环境就绪" if not problems else "待处理：\n- " + "\n- ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
