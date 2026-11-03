#!/usr/bin/env python3
"""抖音内容一键提取管線：链接 → 下载 → 本地识别 → transcript 产物。

用法:
  python dy_pipeline.py "<分享链接>" --output output/xxx
  python dy_pipeline.py "<分享链接>" --info-only
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg_mod  # noqa: E402
from douyin_resolver import resolve  # noqa: E402
from note_builder import find_existing_note  # noqa: E402

CHUNK = 1 << 16


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"#\S+", "", text or "").strip()
    text = re.sub(r'[\\/:*?"<>|\n\r\t]', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or "douyin"


def valid_video(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return probe.returncode == 0 and float(probe.stdout.strip()) > 0
    except ValueError:
        return False


def download(url: str, dest: Path, referer: str = "https://www.douyin.com/") -> Path:
    headers = {"User-Agent": cfg_mod.DESKTOP_UA, "Referer": referer}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=dest.name + ".",
                                     suffix=".part", delete=False) as temp:
        partial = Path(temp.name)
    try:
        with requests.get(url, headers=headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with partial.open("wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
        if got == 0 or total and got != total:
            raise IOError(f"下载不完整: {got}/{total} 字节")
        if dest.suffix.lower() == ".mp4" and not valid_video(partial):
            raise IOError("下载结果不是可解码的 MP4")
        os.link(partial, dest)  # 目标已存在时失败，不覆盖已有文件
    finally:
        partial.unlink(missing_ok=True)
    return dest


def download_images(info: dict, out_dir: Path) -> list[Path]:
    paths = []
    for i, url in enumerate(info.get("images", []), 1):
        ext = ".webp" if ".webp" in url else ".png" if ".png" in url else ".jpg"
        p = out_dir / f"image_{i:03d}{ext}"
        try:
            download(url, p)
            paths.append(p)
        except Exception as e:
            print(f"  图片 {i} 下载失败: {e}", file=sys.stderr)
    return paths


def write_transcript_md(info: dict, text: str, out_dir: Path, *, with_ts: bool = False) -> Path:
    rows = [
        ("类型", "图文" if info["type"] == "note" else "视频"),
        ("ID", f"`{info['video_id']}`"),
        ("作者", info.get("author") or "—"),
        ("来源", info.get("source_url", "")),
        ("提取时间", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    if info["type"] == "video":
        rows.insert(3, ("时长", f"{round((info.get('duration_ms') or 0) / 1000)}s"))
    table = "| 属性 | 值 |\n|------|----|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)

    body = [f"# {info['title']}", "", table, "", "---", ""]
    if info.get("low_speech"):
        body += ["> ⚠️ 语音内容很少，可能是纯音乐/BGM，转写结果仅供参考。", ""]
    if info["type"] == "video":
        body += ["## 逐字稿", "", text or "（无语音内容）"]
    else:
        body += ["## 正文内容", "", text or "（无正文）", ""]
        images = sorted(out_dir.glob("image_*"))
        if images:
            body += ["## 原始图片", ""]
            body += [f"![图片{i}]({p.name})" for i, p in enumerate(images, 1)]
    p = out_dir / "transcript.md"
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def run(link: str, out_dir: Path, *, info_only=False, spk=False, save_video=True,
        quiet=False, info: dict | None = None, engine: str = "", vault: str = "",
        video_only: bool = False) -> dict:
    t0 = time.time()

    def log(msg):
        if not quiet:
            print(msg, flush=True)

    log("① 解析链接…")
    if info is None:
        info = resolve(link)
    log(f"   {'图文' if info['type'] == 'note' else '视频'} | {info['title'][:40]} | 作者 {info.get('author') or '—'}")
    if info_only:
        return info
    if video_only and info["type"] != "video":
        raise ValueError("当前版本只支持公开视频，尚不支持图文作品")

    vault = vault or cfg_mod.load().get("vault") or ""
    if vault:
        existing = find_existing_note(vault, str(info["video_id"]), info["type"])
        if existing:
            raise FileExistsError(f"作品已归档，跳过下载与转写：{existing}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "video_info.json").write_text(
        json.dumps({k: v for k, v in info.items() if k != "images"} |
                   {"image_count": len(info.get("images", []))},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if info["type"] == "note":
        log(f"② 下载图片 {len(info['images'])} 张…")
        download_images(info, out_dir)
        text = info["title"]
        (out_dir / "transcript_raw.txt").write_text(text, encoding="utf-8")
        write_transcript_md(info, text, out_dir)
        log(f"✅ 完成，用时 {time.time() - t0:.1f}s → {out_dir}")
        return {**info, "text": text, "out_dir": str(out_dir)}

    # 视频：下载 → 抽音频 → 识别
    video_path = out_dir / f"{info['video_id']}.mp4"
    if video_path.exists() and not valid_video(video_path):
        raise IOError(f"已有视频文件不完整或不可解码，请先人工检查：{video_path}")
    if not video_path.exists():
        log(f"② 下载视频 {info['video_id']}.mp4…")
        download(info["play_url"], video_path)
    log(f"   视频 {video_path.stat().st_size / 1024 / 1024:.1f} MB")

    log("③ 本地语音识别（FunASR 离线）…")
    from local_asr import LocalASR, format_srt, format_timestamp_text

    asr = LocalASR(spk=spk, engine=engine)
    result = asr.transcribe(video_path)
    text = result["text"]
    info["low_speech"] = result.get("low_speech", False)
    (out_dir / "video_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "transcript_raw.txt").write_text(text, encoding="utf-8")
    (out_dir / "transcript.srt").write_text(format_srt(result["sentences"]), encoding="utf-8")
    (out_dir / "sentences.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_transcript_md(info, format_timestamp_text(result["sentences"]), out_dir, with_ts=True)

    if not save_video:
        video_path.unlink(missing_ok=True)
        log("   已删除视频（--no-video）")

    log(f"✅ 完成，{len(text)} 字，用时 {time.time() - t0:.1f}s → {out_dir}")
    return {**info, "text": text, "out_dir": str(out_dir)}


def main():
    ap = argparse.ArgumentParser(description="抖音内容提取管线（本地离线识别）")
    ap.add_argument("link", help="分享链接或分享文案")
    ap.add_argument("--output", "-o", default="", help="输出目录（默认 output/<ID>）")
    ap.add_argument("--vault", default="", help="知识库目录；用于下载前检查重复作品")
    ap.add_argument("--info-only", action="store_true", help="只解析，不下载")
    ap.add_argument("--video-only", action="store_true", help="仅处理视频，图文在下载前停止")
    ap.add_argument("--spk", action="store_true", help="启用说话人分离")
    ap.add_argument("--engine", choices=["auto", "hybrid", "paraformer", "sensevoice"],
                    default="", help="识别引擎（默认取 config.json 的 engine）")
    ap.add_argument("--no-video", action="store_true", help="识别后删除视频文件")
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    try:
        resolved = resolve(args.link)
        out_dir = Path(args.output) if args.output else Path(cfg["output_dir"]) / str(resolved["video_id"])
        info = run(args.link, out_dir, info_only=args.info_only, spk=args.spk, info=resolved,
                   save_video=not args.no_video, quiet=args.quiet, engine=args.engine,
                   vault=args.vault, video_only=args.video_only)
        print(json.dumps({k: v for k, v in info.items() if k in
                          ("video_id", "type", "title", "author", "text", "out_dir")},
                         ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
