#!/usr/bin/env python3
"""把提取产物整理成以知识为中心的 Obsidian 笔记。

用法:
  python note_builder.py output/test1                 # 迁入 vault
  python note_builder.py output/test1 --dry-run       # 只预览，不动文件
  python note_builder.py output/test1 --vault ~/Desktop/tuling
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg_mod  # noqa: E402


def clean_title(t: str) -> str:
    t = re.sub(r"#\S+", "", t or "")
    t = re.sub(r"第\d+集\s*[|｜]\s*", "", t)
    t = re.sub(r'[\\/:*?"<>|\n\r\t]', " ", t)
    return re.sub(r"\s+", " ", t).strip()


def note_stem(info: dict, knowledge: dict | None = None) -> str:
    title = clean_title((knowledge or {}).get("title") or info.get("title") or "")
    title = re.split(r"[|｜]", title)[0].strip()
    return title[:60] or "抖音摘录"


def category_path(category: str) -> Path:
    parts = category.split("/")
    if any(not part.strip() or part in (".", "..") or clean_title(part) != part for part in parts):
        raise ValueError("knowledge.json: category 必须是安全的分类路径，如 AI/语音合成")
    return Path(*parts)


def related_paths(vault: Path, names: list[str]) -> list[Path]:
    paths = []
    for name in names:
        if name.startswith("/") or name.endswith(".md") or ".." in name.split("/"):
            raise ValueError(f"关联笔记路径无效：{name}")
        matches = [vault / f"{name}.md"] if "/" in name else list(vault.rglob(f"{name}.md"))
        matches = [p for p in matches if p.is_file() and not p.is_symlink()]
        if len(matches) != 1:
            raise ValueError(f"关联笔记不存在或标题不唯一：{name}")
        paths.append(matches[0])
    return paths


def find_existing_note(vault: str | Path, video_id: str, kind: str = "video") -> Path | None:
    """Find an already archived work by its canonical URL, regardless of title."""
    root = Path(vault).expanduser()
    if not root.is_dir():
        return None
    url_line = f'url: "https://www.douyin.com/{kind}/{video_id}"'
    for note in root.rglob("*.md"):
        if note.is_symlink() or any(part.startswith(".") for part in note.relative_to(root).parts):
            continue
        try:
            frontmatter = note.read_text(encoding="utf-8").split("---", 2)[1]
        except (OSError, IndexError, UnicodeError):
            continue
        if url_line in frontmatter.splitlines():
            return note
    return None


def check_cleanup_dir(out_dir: Path, video_id: str) -> None:
    """Only remove a complete pipeline output folder, never an arbitrary input path."""
    allowed = {"video_info.json", "transcript_raw.txt", "transcript.srt",
               "sentences.json", "transcript.md", "knowledge.json", f"{video_id}.mp4"}
    if out_dir.is_symlink() or not out_dir.is_dir() or out_dir.name != video_id:
        raise ValueError(f"原始文件目录必须是以帖子 ID 命名的真实目录：{out_dir}")
    for path in out_dir.iterdir():
        if path.is_symlink() or not path.is_file() or (
            path.name not in allowed and not re.fullmatch(r"image_\d{3}\.(jpg|png|webp)", path.name)
        ):
            raise ValueError(f"原始文件目录含非管线文件，已停止归档以避免误删：{path}")


def _knowledge(out_dir: Path, transcript: str) -> dict | None:
    """Read Agent-authored knowledge only when every claim cites the transcript."""
    path = out_dir / "knowledge.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not clean_title(data["title"]):
        raise ValueError("knowledge.json: 缺少 Agent 生成的知识标题")
    if not isinstance(data.get("category"), str) or not data["category"].strip():
        raise ValueError("knowledge.json: 缺少知识分类")
    category_path(data["category"])
    if not isinstance(data.get("tags"), list) or any(not isinstance(t, str) or not t.strip() for t in data["tags"]):
        raise ValueError("knowledge.json: tags 必须是非空文字组成的列表")
    if not isinstance(data.get("related_notes", []), list) or any(
        not isinstance(t, str) or not t.strip() or t.endswith(".md") or "\\" in t
        for t in data.get("related_notes", [])
    ):
        raise ValueError("knowledge.json: related_notes 必须是现有笔记的标题列表")
    for section in ("conclusion", "key_points", "steps", "caveats"):
        items = data.get(section, [])
        if not isinstance(items, list):
            raise ValueError(f"knowledge.json: {section} 必须是列表")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError(f"knowledge.json: {section} 存在空结论")
            quotes = item.get("source_quotes")
            if not isinstance(quotes, list) or not quotes or any(
                not isinstance(q, str) or not q.strip() or q not in transcript for q in quotes
            ):
                raise ValueError(f"knowledge.json: {section} 的原文依据缺失或与逐字稿不符")
    if not data.get("conclusion"):
        raise ValueError("knowledge.json: 至少需要一条摘要结论")
    return data


def render(knowledge: dict, note_date: str, url: str) -> str:
    title = clean_title(knowledge["title"])

    fm = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {note_date}",
        f"url: {json.dumps(url, ensure_ascii=False)}",
        f"category: {json.dumps(knowledge['category'], ensure_ascii=False)}",
        f"tags: {json.dumps(knowledge['tags'], ensure_ascii=False)}",
        "---",
        "",
    ]
    body = [f"# {title}", ""]

    body += ["## 摘要", ""]
    for item in knowledge["conclusion"]:
        body += [item["text"], "", "原文依据：" + "；".join(f"“{q}”" for q in item["source_quotes"]), ""]
    for key, heading in (("key_points", "核心要点"),
                         ("steps", "可操作步骤"), ("caveats", "限制与注意事项")):
        if knowledge.get(key):
            body += [f"## {heading}", ""]
            for item in knowledge[key]:
                body += [f"- {item['text']}",
                         "  - 原文依据：" + "；".join(f"“{q}”" for q in item["source_quotes"])]
            body += [""]

    if knowledge.get("related_notes"):
        body += ["## 相关知识", ""]
        body += [f"- [[{name}]]" for name in knowledge["related_notes"]]
        body += [""]

    return "\n".join(fm + body).strip() + "\n"


def build(out_dir: str | Path, *, vault: str = "", dry_run=False,
          require_knowledge: bool = False) -> dict:
    out_dir = Path(out_dir)
    cfg = cfg_mod.load()
    vault = vault or cfg.get("vault") or ""
    info_path = out_dir / "video_info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"{out_dir} 缺少 video_info.json，请先跑 dy_pipeline.py")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    info.setdefault("video_id", out_dir.name)
    video_id = str(info["video_id"])
    if not video_id.isdigit():
        raise ValueError("帖子缺少有效 ID，无法生成源作品地址")
    kind = "note" if info.get("type") == "note" else "video"
    url = f"https://www.douyin.com/{kind}/{video_id}"
    raw = out_dir / "transcript_raw.txt"
    text = raw.read_text(encoding="utf-8").strip() if raw.is_file() else ""
    knowledge = _knowledge(out_dir, text)
    if not knowledge:
        raise ValueError("缺少 knowledge.json：请先由 Agent 根据逐字稿提炼并附原文依据")
    stem = note_stem(info, knowledge)
    note_date = datetime.now().strftime("%Y-%m-%d")

    md = render(knowledge, note_date, url)

    if dry_run:
        print(md)
        return {"note_path": None, "asset_dir": None, "preview": md,
                "knowledge_status": "extracted"}

    if not vault:
        raise ValueError("未配置 vault：请在 config.json 设置 vault，或用 --vault 指定")

    vault_path = Path(vault).expanduser()
    if not vault_path.is_dir():
        raise FileNotFoundError(f"vault 不存在: {vault_path}")
    check_cleanup_dir(out_dir, video_id)

    note_dir = vault_path / category_path(knowledge["category"])
    note_path = note_dir / f"{stem}.md"
    previous = find_existing_note(vault_path, video_id, kind)
    if previous:
        raise FileExistsError(f"作品已归档：{previous}")
    if note_path.exists() or note_path.is_symlink():
        raise FileExistsError(f"笔记已存在，已停止归档：{note_path}")
    related = related_paths(vault_path, knowledge.get("related_notes", []))
    new_ref = note_path.relative_to(vault_path).with_suffix("").as_posix()
    if note_path in related:
        raise ValueError("不能关联笔记自身")
    knowledge = dict(knowledge, related_notes=[p.relative_to(vault_path).with_suffix("").as_posix() for p in related])
    md = render(knowledge, note_date, url)
    note_dir.mkdir(parents=True, exist_ok=True)

    with note_path.open("x", encoding="utf-8") as note_file:
        note_file.write(md)
    for target in related:
        old = target.read_text(encoding="utf-8")
        link = f"- [[{new_ref}]]"
        if link not in old:
            if "\n## 相关知识\n" in old:
                old = old.rstrip() + "\n" + link + "\n"
            else:
                old = old.rstrip() + "\n\n## 相关知识\n\n" + link + "\n"
            target.write_text(old, encoding="utf-8")
    shutil.rmtree(out_dir)
    return {"note_path": str(note_path), "source_deleted": True, "knowledge_status": "extracted"}


def main():
    ap = argparse.ArgumentParser(description="提取产物 → Obsidian 笔记")
    ap.add_argument("out_dir", help="dy_pipeline.py 的输出目录")
    ap.add_argument("--vault", default="", help="Obsidian vault 根目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印 Markdown")
    ap.add_argument("--require-knowledge", action="store_true", help="缺少经核对的 knowledge.json 时拒绝归档")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = build(args.out_dir, vault=args.vault,
                dry_run=args.dry_run, require_knowledge=args.require_knowledge)
    if args.json and res.get("note_path"):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif res.get("note_path"):
        print(f"笔记: {res['note_path']}")
        print("原始文件: 已清理")


if __name__ == "__main__":
    main()
