import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mac_workflow
from local_asr import merge_sentences


class MacWorkflowTests(unittest.TestCase):
    def test_speaker_labels_and_boundaries(self):
        sentences = [
            {"start": 0, "end": 1, "text": "你好", "speaker": 0},
            {"start": 1, "end": 2, "text": "你好。", "speaker": 1},
        ]
        self.assertEqual(len(merge_sentences(sentences)), 2)
        labeled = mac_workflow.speaker_sentences(sentences)
        self.assertEqual([s["text"] for s in labeled], ["说话人 1：你好", "说话人 2：你好。"])

    def test_speaker_option_uses_optional_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "clip.m4a"
            source.write_bytes(b"audio")
            model = root / "models" / "campplus"
            model.mkdir(parents=True)
            (model / "campplus_cn_common.bin").write_bytes(b"model")
            result_data = {"text": "你好。再见。", "sentences": [
                {"start": 0, "end": 1, "text": "你好。", "speaker": 0},
                {"start": 1, "end": 2, "text": "再见。", "speaker": 1}]}
            with patch("config.load", return_value={"_models_dir": str(root / "models")}), \
                    patch("local_asr.LocalASR") as asr:
                asr.return_value.transcribe.return_value = result_data
                result = mac_workflow.run_task(str(source), "audio", "transcript", root / "out",
                    cache_root=root / "cache", distinguish_speakers=True)
            self.assertIn("说话人 2：再见。", (Path(result["output"]) / "逐字稿.txt").read_text())
            self.assertTrue(asr.call_args.kwargs["spk"])
            self.assertEqual(asr.call_args.kwargs["engine"], "paraformer")

    def test_same_type_local_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for kind, target in (("video", "video"), ("audio", "audio")):
                with self.assertRaisesRegex(ValueError, "不同"):
                    mac_workflow.run_task("file", kind, target, root, cache_root=root / "cache")

    def test_existing_output_replaces_generated_file_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = {"video_id": "123456789012345", "type": "video", "play_url": "https://example.com/video"}
            output = root / "out" / info["video_id"]
            output.mkdir(parents=True)
            (output / "123456789012345.mp4").write_bytes(b"old")
            (output / "my-note.txt").write_text("keep")
            def fake_download(_url, path):
                path.write_bytes(b"new")
            with patch.object(mac_workflow, "resolve", return_value=info), \
                    patch.object(mac_workflow, "download", side_effect=fake_download):
                mac_workflow.run_task("link", "douyin", "video", root / "out", cache_root=root / "cache")
            self.assertEqual((output / "123456789012345.mp4").read_bytes(), b"new")
            self.assertEqual((output / "my-note.txt").read_text(), "keep")

    def test_deepseek_base_url_becomes_chat_endpoint(self):
        self.assertEqual(mac_workflow.chat_endpoint("https://api.deepseek.com/"),
                         "https://api.deepseek.com/chat/completions")
        with self.assertRaisesRegex(ValueError, "完整"):
            mac_workflow.chat_endpoint("https://example.com/")

    def test_download_only_does_not_transcribe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = {"video_id": "123456789012345", "type": "video", "play_url": "https://example.com/video"}
            def fake_download(_url, path):
                path.write_bytes(b"video")
            with patch.object(mac_workflow, "resolve", return_value=info), \
                    patch.object(mac_workflow, "download", side_effect=fake_download), \
                    patch.object(mac_workflow, "export_audio") as audio:
                result = mac_workflow.run_task("link", "douyin", "video", root / "out", cache_root=root / "cache")
            self.assertTrue((Path(result["output"]) / "123456789012345.mp4").exists())
            audio.assert_not_called()

    def test_local_video_to_audio_and_audio_to_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            def fake_audio(_video, path):
                path.write_bytes(b"audio")
            with patch.object(mac_workflow, "export_audio", side_effect=fake_audio):
                result = mac_workflow.run_task(str(video), "video", "audio", root / "out", cache_root=root / "cache")
            audio = Path(result["output"]) / "音频.m4a"
            self.assertTrue(audio.exists())
            with patch("local_asr.LocalASR") as asr:
                asr.return_value.transcribe.return_value = {"text": "你好", "sentences": []}
                result = mac_workflow.run_task(str(audio), "audio", "transcript", root / "text", cache_root=root / "cache")
            self.assertEqual((Path(result["output"]) / "逐字稿.txt").read_text(), "你好")

    def test_cache_removes_only_expired_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "job-old"
            old.mkdir()
            (old / ".expires_at").write_text("1")
            unknown = root / "other"
            unknown.mkdir()
            mac_workflow.clean_cache(root, now=2)
            self.assertFalse(old.exists())
            self.assertTrue(unknown.exists())

    def test_douyin_to_obsidian_discards_intermediates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            vault.mkdir()
            info = {"video_id": "123456789012345", "type": "video", "title": "测试", "play_url": "https://example.com/video",
                    "source_url": "https://www.douyin.com/video/123456789012345"}
            knowledge = {"title": "测试知识", "category": "测试", "tags": ["测试"], "related_notes": [],
                         "conclusion": [{"text": "内容", "source_quotes": ["原文"]}],
                         "key_points": [], "steps": [], "caveats": []}
            def fake_download(_url, path):
                path.write_bytes(b"video")
            def fake_audio(_video, path):
                path.write_bytes(b"audio")
            def fake_summary(job, *_args):
                (job / "knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
            with patch.object(mac_workflow, "resolve", return_value=info), \
                    patch.object(mac_workflow, "download", side_effect=fake_download), \
                    patch.object(mac_workflow, "export_audio", side_effect=fake_audio), \
                    patch.object(mac_workflow, "summarize", side_effect=fake_summary), \
                    patch("local_asr.LocalASR") as asr:
                asr.return_value.transcribe.return_value = {"text": "原文", "sentences": []}
                result = mac_workflow.run_task("link", "douyin", "obsidian", root / "out", vault=vault,
                    api_url="https://example.com", model="model", api_key="key", cache_root=root / "cache")
            self.assertTrue(Path(result["note"]).is_file())
            self.assertFalse((root / "out").exists())
            self.assertEqual(len(list((root / "cache").glob("job-*"))), 1)

    def test_process_keeps_four_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = {"video_id": "123456789012345", "type": "video", "title": "测试"}

            def fake_run(_link, folder, **_kwargs):
                (folder / f"{info['video_id']}.mp4").write_bytes(b"video")
                (folder / "transcript_raw.txt").write_text("原文", encoding="utf-8")
                (folder / "transcript.srt").write_text("字幕", encoding="utf-8")

            def fake_audio(_video, audio):
                audio.write_bytes(b"audio")

            with patch.object(mac_workflow, "resolve", return_value=info), \
                    patch.object(mac_workflow, "run", side_effect=fake_run), \
                    patch.object(mac_workflow, "export_audio", side_effect=fake_audio):
                folder = mac_workflow.process("link", root, on_stage=lambda _name: None)
            self.assertEqual(folder.name, info["video_id"])
            for name in (f"{info['video_id']}.mp4", "音频.m4a", "逐字稿.txt", "字幕.srt"):
                self.assertTrue((folder / name).exists(), name)

    def test_summary_rejects_invented_quote(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "transcript_raw.txt").write_text("真实原文", encoding="utf-8")
            (folder / "video_info.json").write_text(json.dumps({"source_url": "https://www.douyin.com/video/123"}))
            data = {"title": "标题", "category": "测试", "tags": [], "related_notes": [],
                    "conclusion": [{"text": "编造", "source_quotes": ["不存在的原文"]}]}
            class Response:
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"choices": [{"message": {"content": json.dumps(data)}}]}
            with patch.object(mac_workflow.requests, "post", return_value=Response()) as post:
                with self.assertRaisesRegex(ValueError, "原文依据"):
                    mac_workflow.summarize(folder, "https://example.com/chat/completions", "model", "key", "自定义整理要求")
            self.assertIn("自定义整理要求\n\n逐字稿：\n真实原文",
                          post.call_args.kwargs["json"]["messages"][1]["content"])
            self.assertFalse((folder / "重点整理.md").exists())


if __name__ == "__main__":
    unittest.main()
