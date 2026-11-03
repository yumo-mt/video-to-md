#!/usr/bin/env python3
"""项目配置：config.json 覆盖默认值，DY_VAULT 可指定知识库。"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    "vault": "",
    "models_dir": "models",
    "asr_model": "paraformer-zh",
    "vad_model": "fsmn-vad",
    "punc_model": "ct-punc",
    "spk_model": "",
    "device": "cpu",
    "output_dir": "output",
    "save_video": False,
    "engine": "hybrid",
    "sensevoice_model": "sensevoice",
    "browser_channel": "chrome",
    "download_speaker_model": False,
}

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    cfg["vault"] = os.getenv("DY_VAULT", cfg.get("vault") or "")
    cfg["_root"] = str(ROOT)
    cfg["_models_dir"] = str((ROOT / cfg["models_dir"]).resolve()) if not Path(
        cfg["models_dir"]).is_absolute() else cfg["models_dir"]
    return cfg


def save(patch: dict) -> Path:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    cfg.update(patch)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return CONFIG_PATH


def check(cfg: dict | None = None) -> list[str]:
    """返回缺失项说明列表，空列表表示环境就绪。"""
    import shutil

    cfg = cfg or load()
    problems = []
    md = Path(cfg["_models_dir"])
    engine = cfg.get("engine", "hybrid")
    keys = ["vad_model"]
    if engine in ("hybrid", "auto", "paraformer"):
        keys += ["asr_model", "punc_model"]
    if engine in ("hybrid", "auto", "sensevoice"):
        keys += ["sensevoice_model"]
    if cfg.get("spk_model"):
        keys.append("spk_model")
    for key in keys:
        name = cfg.get(key)
        if not name:
            continue
        d = md / name
        if not d.is_dir() or not any(d.glob("*.pt")) and not any(d.glob("*.bin")):
            problems.append(f"模型缺失: {d}")
    if not shutil.which("ffmpeg"):
        problems.append("未找到 ffmpeg")
    if not shutil.which("ffprobe"):
        problems.append("未找到 ffprobe")
    try:
        import funasr  # noqa: F401
    except ImportError:
        problems.append("未安装 funasr（请用 .venv/bin/python 运行）")
    return problems
