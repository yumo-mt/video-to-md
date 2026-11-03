import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dy_pipeline
import download_models
import note_builder


class FakeResponse:
    headers = {"content-length": "10"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield b"short"


class P0Tests(unittest.TestCase):
    def test_model_download_selects_engine_and_skips_existing_weights(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "fsmn-vad").mkdir()
            (root / "fsmn-vad" / "model.pt").write_bytes(b"existing")
            cfg = {"_models_dir": str(root), "engine": "paraformer",
                   "vad_model": "fsmn-vad", "asr_model": "paraformer-zh",
                   "punc_model": "ct-punc", "sensevoice_model": "sensevoice",
                   "spk_model": "", "download_speaker_model": False}

            def fake_download(*, model_id, local_dir):
                target = Path(local_dir)
                target.mkdir()
                (target / "model.pt").write_bytes(model_id.encode())

            with patch("modelscope.hub.snapshot_download.snapshot_download", side_effect=fake_download) as mock:
                download_models.download_required(cfg)
            self.assertEqual(mock.call_count, 2)
            self.assertFalse((root / "sensevoice").exists())

    def test_existing_url_skips_download_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            note = vault / "AI" / "语音合成" / "已归档.md"
            note.parent.mkdir(parents=True)
            note.write_text('---\nurl: "https://www.douyin.com/video/123456789012345"\n---\n# 已归档\n')
            output = root / "output" / "123456789012345"
            info = {"video_id": "123456789012345", "type": "video", "title": "测试"}
            with self.assertRaisesRegex(FileExistsError, "跳过下载与转写"):
                dy_pipeline.run("unused", output, info=info, vault=str(vault), quiet=True)
            self.assertFalse(output.exists())

    def test_unknown_file_blocks_cleanup_before_note_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, vault = root / "123456789012345", root / "vault"
            source.mkdir()
            vault.mkdir()
            (source / "video_info.json").write_text(json.dumps({"video_id": source.name, "type": "video"}))
            (source / "transcript_raw.txt").write_text("有用的原文")
            (source / "knowledge.json").write_text(json.dumps({
                "title": "知识标题", "category": "AI/测试", "tags": ["测试"], "related_notes": [],
                "conclusion": [{"text": "结论", "source_quotes": ["有用的原文"]}]
            }))
            (source / "personal.txt").write_text("unrelated")
            with self.assertRaisesRegex(ValueError, "非管线文件"):
                note_builder.build(source, vault=str(vault))
            self.assertTrue((source / "personal.txt").exists())
            self.assertFalse((vault / "AI" / "测试" / "知识标题.md").exists())

    def test_incomplete_download_leaves_no_target(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            dy_pipeline.requests, "get", return_value=FakeResponse()
        ):
            target = Path(temp) / "video.mp4"
            with self.assertRaisesRegex(IOError, "下载不完整"):
                dy_pipeline.download("https://example.com/video", target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_knowledge_requires_real_quote_and_existing_note_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, vault = root / "123456789012345", root / "vault"
            source.mkdir()
            vault.mkdir()
            info = {"video_id": "123456789012345", "type": "video", "title": "示例",
                    "author": "作者", "source_url": "https://v.douyin.com/test/", "duration_ms": 10000}
            (source / "video_info.json").write_text(json.dumps(info), encoding="utf-8")
            (source / "transcript_raw.txt").write_text("一行字可以生成新声音。仅限研究使用。", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "缺少 knowledge.json"):
                note_builder.build(source, dry_run=True)

            knowledge = {"title": "文字生成声音的用法", "category": "AI/语音合成", "tags": ["语音合成"],
                         "related_notes": ["AI/工具/已有知识"],
                         "conclusion": [{"text": "可以从文字生成声音", "source_quotes": ["一行字可以生成新声音"]}],
                         "key_points": [], "steps": [], "caveats": []}
            (source / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            old_dir = vault / "AI" / "工具"
            old_dir.mkdir(parents=True)
            (old_dir / "已有知识.md").write_text("# 已有知识\n", encoding="utf-8")
            result = note_builder.build(source, vault=str(vault), require_knowledge=True)
            note = Path(result["note_path"])
            self.assertEqual(note.name, "文字生成声音的用法.md")
            self.assertIn("## 摘要", note.read_text())
            self.assertIn('title: "文字生成声音的用法"', note.read_text())
            self.assertEqual(note.parent, vault / "AI" / "语音合成")
            self.assertIn("## 摘要", note.read_text())
            self.assertIn('category: "AI/语音合成"', note.read_text())
            self.assertIn('tags: ["语音合成"]', note.read_text())
            self.assertIn('url: "https://www.douyin.com/video/123456789012345"', note.read_text())
            self.assertIn("[[AI/工具/已有知识]]", note.read_text())
            self.assertIn("[[AI/语音合成/文字生成声音的用法]]", (old_dir / "已有知识.md").read_text())
            for unwanted in ("source:", "author:", "duration:", "## 时间轴", "## 视频", "## 逐字稿", "## 📎"):
                self.assertNotIn(unwanted, note.read_text())
            self.assertTrue(result["source_deleted"])
            self.assertFalse(source.exists())
            self.assertFalse((vault / "assets").exists())
            before = note.read_bytes()
            source.mkdir()
            (source / "video_info.json").write_text(json.dumps(info), encoding="utf-8")
            (source / "transcript_raw.txt").write_text("一行字可以生成新声音。仅限研究使用。", encoding="utf-8")
            (source / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            with self.assertRaises(FileExistsError):
                note_builder.build(source, vault=str(vault), require_knowledge=True)
            self.assertEqual(note.read_bytes(), before)
            self.assertTrue((source / "transcript_raw.txt").exists())

            del knowledge["title"]
            (source / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "知识标题"):
                note_builder.build(source, dry_run=True, require_knowledge=True)

            knowledge["title"] = "文字生成声音的用法"
            knowledge["conclusion"][0]["source_quotes"] = ["不存在于逐字稿"]
            (source / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "原文依据"):
                note_builder.build(source, dry_run=True, require_knowledge=True)

            knowledge["conclusion"][0]["source_quotes"] = ["一行字可以生成新声音"]
            knowledge["category"] = "AI/../其他"
            (source / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "category"):
                note_builder.build(source, dry_run=True, require_knowledge=True)


if __name__ == "__main__":
    unittest.main()
