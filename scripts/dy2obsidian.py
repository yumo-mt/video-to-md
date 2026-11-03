#!/usr/bin/env python3
"""一条命令：抖音链接 → 本地转写；已有 Agent 知识文件时归档笔记。

用法:
  python dy2obsidian.py "https://v.douyin.com/xxx/"
  python dy2obsidian.py "<分享文案>" --vault ~/Desktop/tuling
  python dy2obsidian.py "<链接>" --dry-run          # 已有 knowledge.json 时预览笔记
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg_mod  # noqa: E402
import dy_pipeline  # noqa: E402
import note_builder  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="抖音 → Obsidian 一条命令")
    ap.add_argument("link", help="抖音分享链接或分享文案")
    ap.add_argument("--vault", default="", help="Obsidian vault 根目录")
    ap.add_argument("--output", "-o", default="", help="原始文件目录（默认 output/<帖子ID>）")
    ap.add_argument("--keep-video", action="store_true", help="保留视频文件")
    ap.add_argument("--spk", action="store_true", help="说话人分离")
    ap.add_argument("--engine", choices=["auto", "hybrid", "paraformer", "sensevoice"],
                    default="", help="识别引擎")
    ap.add_argument("--dry-run", action="store_true", help="只生成预览，不写入 vault")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    missing = cfg_mod.check(cfg)
    if missing:
        print("环境检查未通过：", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        sys.exit(2)

    pre = dy_pipeline.run(args.link, Path(cfg["output_dir"]) / "_tmp", info_only=True,
                          quiet=True)
    out_dir = Path(args.output) if args.output else Path(cfg["output_dir"]) / str(pre["video_id"])

    info = dy_pipeline.run(args.link, out_dir, spk=args.spk,
                           save_video=args.keep_video,
                           quiet=args.json, info=pre, engine=args.engine, vault=args.vault)
    if (out_dir / "knowledge.json").is_file():
        res = note_builder.build(out_dir, vault=args.vault,
                                 dry_run=args.dry_run, require_knowledge=True)
    else:
        res = {"knowledge_status": "awaiting_agent", "transcript_path": str(out_dir / "transcript_raw.txt")}

    if args.json:
        print(json.dumps({"info": {k: info.get(k) for k in
                                   ("video_id", "type", "title", "author", "text")},
                          "note": res}, ensure_ascii=False, indent=2))
    elif res.get("note_path"):
        print(f"\n笔记: {res['note_path']}")
        print("原始文件: 已清理")
    elif res.get("preview"):
        print(res["preview"])
    else:
        print(f"\n转写稿: {res['transcript_path']}\n等待 Agent 提炼知识后再归档。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
