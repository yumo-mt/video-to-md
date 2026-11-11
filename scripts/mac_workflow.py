#!/usr/bin/env python3
"""Standalone Mac app workflow. Outputs are kept outside the Skill's temporary folder."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

from douyin_resolver import resolve
from dy_pipeline import run
from dy_pipeline import download
from note_builder import _knowledge, render


def stage(name: str) -> None:
    print(json.dumps({"stage": name}, ensure_ascii=False), flush=True)


def export_audio(video: Path, audio: Path) -> None:
    if audio.exists():
        return
    part = audio.with_suffix(".part.m4a")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                        "-vn", "-c:a", "aac", "-b:a", "192k", str(part)], check=True)
        part.replace(audio)
    finally:
        part.unlink(missing_ok=True)


def chat_endpoint(url: str) -> str:
    url = url.strip().rstrip("/")
    if url == "https://api.deepseek.com":
        return url + "/chat/completions"
    if url == "https://api.deepseek.com/v1":
        return url + "/chat/completions"
    if not url.startswith("https://") or not url.endswith("/chat/completions"):
        raise ValueError("AI API 地址应是完整的 HTTPS Chat Completions 地址，例如 https://api.deepseek.com/chat/completions")
    return url


def summarize(folder: Path, api_url: str, model: str, api_key: str) -> Path:
    transcript = (folder / "transcript_raw.txt").read_text(encoding="utf-8").strip()
    if not transcript:
        raise ValueError("逐字稿为空，无法生成重点")
    api_url = chat_endpoint(api_url)
    prompt = ("根据以下逐字稿输出 JSON 对象。字段：title 字符串、category 字符串、tags 字符串数组、"
              "related_notes 空数组、conclusion/key_points/steps/caveats 数组。每个数组元素是"
              "{text: 字符串, source_quotes: [逐字稿中逐字存在的短句]}。conclusion 至少一条。"
              "不要编造，不要输出 Markdown 或代码围栏。\n\n逐字稿：\n" + transcript)
    response = requests.post(api_url, headers={"Authorization": f"Bearer {api_key}"},
                             json={"model": model, "messages": [
                                 {"role": "system", "content": "你是严谨的中文视频内容整理助手。只返回 JSON。"},
                                 {"role": "user", "content": prompt}], "temperature": 0}, timeout=180)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    knowledge_path = folder / "knowledge.json"
    knowledge_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    validated = _knowledge(folder, transcript)
    info = json.loads((folder / "video_info.json").read_text(encoding="utf-8"))
    note = folder / "重点整理.md"
    note.write_text(render(validated, datetime.now().strftime("%Y-%m-%d"),
                           info["source_url"]), encoding="utf-8")
    return note


def clean_cache(cache_root: Path, now: float | None = None) -> None:
    """Delete only expired app-owned job directories."""
    now = time.time() if now is None else now
    if not cache_root.is_dir():
        return
    for job in cache_root.iterdir():
        marker = job / ".expires_at"
        if job.is_symlink() or not job.is_dir() or not marker.is_file():
            continue
        try:
            if float(marker.read_text()) <= now:
                shutil.rmtree(job)
        except (ValueError, OSError):
            continue


def replace_output(source: Path, target: Path) -> None:
    """Replace one generated file atomically; leave other files in its folder alone."""
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".video-to-md-", delete=False) as handle:
        pending = Path(handle.name)
    try:
        shutil.copy2(source, pending)
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def speaker_sentences(sentences: list[dict]) -> list[dict]:
    """Add readable speaker labels to sentence text without changing timestamps."""
    labels = {}
    out = []
    for sentence in sentences:
        speaker = sentence.get("speaker")
        if speaker is None:
            label = "说话人未识别"
        else:
            labels.setdefault(str(speaker), f"说话人 {len(labels) + 1}")
            label = labels[str(speaker)]
        out.append({**sentence, "text": f"{label}：{sentence['text']}"})
    return out


def run_task(source: str, source_type: str, target: str, destination: Path, *,
             keep: set[str] | None = None, vault: Path | None = None,
             api_url: str = "", model: str = "", api_key: str = "",
             cache_root: Path | None = None, on_stage=stage,
             distinguish_speakers: bool = False) -> dict:
    """Run only the stages required by the selected final output."""
    if source_type not in {"douyin", "video", "audio"} or target not in {"video", "audio", "transcript", "obsidian"}:
        raise ValueError("不支持的输入或目标类型")
    if (source_type, target) in {("video", "video"), ("audio", "audio"), ("audio", "video")}:
        raise ValueError("最终产物必须与本地输入不同，音频也不能生成视频")
    if target == "obsidian" and source_type != "douyin":
        raise ValueError("当前仅支持将抖音视频整理到 Obsidian")
    if distinguish_speakers and target not in {"transcript", "obsidian"}:
        raise ValueError("只有转写与笔记任务可以区分说话人")
    if target == "obsidian" and (not vault or not vault.is_dir() or not all((api_url, model, api_key))):
        raise ValueError("Obsidian 笔记需要知识库和完整的 AI 设置")
    if not source.strip():
        raise ValueError("请选择文件或粘贴链接")
    keep = set(keep or ())
    if not keep <= {"video", "audio", "transcript"}:
        raise ValueError("无效的保留选项")
    cache_root = cache_root or Path.home() / "Library" / "Caches" / "video-to-md" / "jobs"
    cache_root.mkdir(parents=True, exist_ok=True)
    clean_cache(cache_root)
    job = Path(tempfile.mkdtemp(prefix="job-", dir=cache_root))
    (job / ".expires_at").write_text(str(time.time() + 7 * 86400))
    try:
        info = None
        if source_type == "douyin":
            on_stage("解析抖音链接")
            info = resolve(source)
            if info["type"] != "video":
                raise ValueError("当前仅支持公开视频")
            video = job / f"{info['video_id']}.mp4"
            on_stage("下载视频")
            download(info["play_url"], video)
            (job / "video_info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            stem = str(info["video_id"])
        else:
            media = Path(source).expanduser().resolve()
            if not media.is_file():
                raise FileNotFoundError(f"文件不存在：{media}")
            if media.suffix.lower() not in ({".mp4", ".mov", ".mkv", ".m4v"} if source_type == "video" else {".mp3", ".m4a", ".wav", ".aac", ".flac", ".mp4"}):
                raise ValueError("不支持的媒体文件格式")
            stem = media.stem
            video = media if source_type == "video" else None
        audio = None
        if target in {"audio", "transcript", "obsidian"} and source_type != "audio":
            on_stage("提取音频")
            audio = job / "音频.m4a"
            export_audio(video, audio)
        elif source_type == "audio":
            audio = media
        transcript = None
        subtitle = None
        if target in {"transcript", "obsidian"}:
            on_stage("语音转文字")
            from local_asr import LocalASR, format_srt
            if distinguish_speakers:
                from config import load
                cfg = load()
                cfg["spk_model"] = "campplus"
                model_path = Path(cfg["_models_dir"]) / "campplus" / "campplus_cn_common.bin"
                if not model_path.is_file():
                    raise FileNotFoundError("说话人模型未下载，请先点击下载说话人模型")
                result = LocalASR(cfg, spk=True, engine="paraformer").transcribe(audio)
            else:
                result = LocalASR().transcribe(audio)
            sentences = speaker_sentences(result["sentences"]) if distinguish_speakers else result["sentences"]
            text = "\n".join(s["text"] for s in sentences) if distinguish_speakers and sentences else result["text"]
            transcript = job / "逐字稿.txt"
            transcript.write_text(text, encoding="utf-8")
            subtitle = job / "字幕.srt"
            subtitle.write_text(format_srt(sentences), encoding="utf-8")
            (job / "transcript_raw.txt").write_text(text, encoding="utf-8")
        wanted = set(keep)
        if target in {"video", "audio", "transcript"}:
            wanted.add(target)
        available = {"video": video, "audio": audio, "transcript": transcript}
        wanted = {name for name in wanted if available.get(name) is not None}
        output = None
        if wanted:
            output = destination.expanduser().resolve() / stem
            if output.is_symlink() or output.exists() and not output.is_dir():
                raise ValueError(f"输出路径不是普通文件夹：{output}")
            output.mkdir(parents=True, exist_ok=True)
            for name in wanted:
                src = available[name]
                dst = output / ({"video": f"{stem}{src.suffix}", "audio": f"音频{src.suffix}", "transcript": "逐字稿.txt"}[name])
                replace_output(src, dst)
                if name == "transcript" and subtitle:
                    replace_output(subtitle, output / "字幕.srt")
        note_path = None
        if target == "obsidian":
            on_stage("整理并写入 Obsidian")
            summarize(job, api_url, model, api_key)
            from note_builder import build
            staging = job / str(info["video_id"])
            staging.mkdir()
            for name in ("video_info.json", "transcript_raw.txt", "knowledge.json"):
                shutil.copy2(job / name, staging / name)
            note_path = Path(build(staging, vault=str(vault), require_knowledge=True)["note_path"])
        (job / ".expires_at").write_text(str(time.time() + 86400))
        on_stage("完成；未保留的临时文件将在 24 小时后清理")
        return {"output": str(output or note_path), "note": str(note_path) if note_path else "",
                "preview": str(note_path or (output / "逐字稿.txt" if target == "transcript" else ""))}
    except Exception:
        # Keep failed jobs for seven days so a user can retry or inspect them.
        raise


def process(link: str, destination: Path, *, api_url: str = "", model: str = "",
            api_key: str = "", on_stage=stage) -> Path:
    on_stage("解析链接")
    info = resolve(link)
    if info["type"] != "video":
        raise ValueError("当前仅支持公开视频")
    folder = destination.expanduser().resolve() / str(info["video_id"])
    if folder.is_symlink():
        raise ValueError("输出目录不能是符号链接")
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "transcript_raw.txt").exists():
        on_stage("下载视频与转写")
        run(link, folder, info=info, save_video=True, video_only=True, quiet=True)
    video = folder / f"{info['video_id']}.mp4"
    if not video.exists():
        raise FileNotFoundError(f"视频不存在，无法导出音频：{video}")
    on_stage("导出音频")
    export_audio(video, folder / "音频.m4a")
    for source, name in (("transcript_raw.txt", "逐字稿.txt"), ("transcript.srt", "字幕.srt")):
        target = folder / name
        if not target.exists():
            shutil.copyfile(folder / source, target)
    if api_url or model or api_key:
        if not all((api_url, model, api_key)):
            raise ValueError("重点整理需要完整的 API 地址、模型名称和 API Key")
        on_stage("整理重点")
        summarize(folder, api_url, model, api_key)
    on_stage("完成")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description="Mac App 视频处理核心")
    parser.add_argument("link")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--api-url", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    try:
        folder = process(args.link, Path(args.destination), api_url=args.api_url,
                         model=args.model, api_key=os.getenv("VIDEO_TO_MD_API_KEY", ""))
        print(json.dumps({"output": str(folder)}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
