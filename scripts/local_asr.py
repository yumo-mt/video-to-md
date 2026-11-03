#!/usr/bin/env python3
"""本地语音识别：FunASR，全程离线。

引擎：
  - hybrid     ：默认。paraformer 出中文与时间轴，sensevoice 校正英文/数字/专有名词后融合
  - sensevoice ：多语种（中/英/日/粤/韩），英文与专有名词识别更好，自带标点/ITN
  - paraformer ：中文最强（SeACo，支持热词），时间戳最准

特性：
  - 任意音视频输入（自动 ffmpeg 转 16k 单声道 wav）
  - VAD 切分 + 长音频批处理，无时长上限
  - 逐句时间戳 + SRT 字幕
  - 可选 campplus 说话人分离
  - 热词表（hotwords.txt）+ 术语纠错表（config.json 的 glossary）

用法:
  python local_asr.py audio.mp3 --srt out.srt
  python local_asr.py video.mp4 --engine paraformer --hotwords hotwords.txt
"""

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg_mod  # noqa: E402

# SenseVoice 输出里的 <|zh|><|NEUTRAL|><|Speech|><|withitn|> 等标记
SV_TAG_RE = re.compile(r"<\|[^|]*\|>")


def _ffmpeg_to_wav(src: Path, dst: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def load_hotwords(cfg: dict, path: str | None = None) -> list[str]:
    words: list[str] = list(cfg.get("hotwords") or [])
    p = Path(path) if path else Path(cfg["_root"]) / (cfg.get("hotwords_file") or "hotwords.txt")
    if p and p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    return list(dict.fromkeys(words))


LATIN_UPPER = ("ai", "tts", "asr", "ocr", "llm", "gpu", "cpu", "h100", "a100",
               "api", "sdk", "tts2", "nlp", "rag", "aigc", "vlog", "app")
KANA = "ぁ-んァ-ヶー"
LATIN_TITLE = ("english", "chinese", "python", "javascript", "obsidian", "github",
               "claude", "openai", "google", "microsoft", "apple", "mac", "windows")


def split_long_sentences(sentences: list[dict], max_chars: int = 24) -> list[dict]:
    """把过长的句子按标点拆成适合做字幕的短句，时间按字数比例分摊。"""
    out = []
    for s in sentences:
        text = s["text"].strip()
        if len(text) <= max_chars or s["end"] <= s["start"]:
            out.append(s)
            continue
        raw = [p for p in re.split(r"(?<=[，。！？、；])", text) if p.strip()]
        pieces = []
        for p in raw:
            while len(p) > max_chars:
                cut = max(p.rfind("，", 0, max_chars), p.rfind("、", 0, max_chars))
                if cut < max_chars // 2:
                    cut = max_chars - 1
                pieces.append(p[: cut + 1])
                p = p[cut + 1:]
            if p:
                pieces.append(p)
        # 短的相邻片段再组合到 max_chars 附近
        merged, buf = [], ""
        for p in pieces:
            if len(buf) + len(p) <= max_chars:
                buf += p
            else:
                if buf:
                    merged.append(buf)
                buf = p
        if buf:
            merged.append(buf)
        total = sum(len(m) for m in merged) or 1
        span = s["end"] - s["start"]
        cur = s["start"]
        for m in merged:
            dur = span * len(m) / total
            out.append({**s, "start": round(cur, 2), "end": round(cur + dur, 2), "text": m})
            cur += dur
    return out


def _unspace(text: str) -> str:
    """paraformer 不带标点模型时逐字输出空格，这里还原成正常中文。"""
    text = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+", "", text)
    text = re.sub(r"\s+(?=[\u4e00-\u9fff])", "", text)
    return text.strip()


def cleanup_text(text: str) -> str:
    """清掉分块解码带来的噪声：孤立假名、重复标点、多余空格。"""
    text = re.sub(rf"[{KANA}]{{1,4}}[？?！!。]?", "", text)
    text = re.sub(r"([，。！？、])\1+", r"\1", text)
    text = re.sub(r"，。", "。", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip().lstrip("，、 ")
    return text


def merge_sentences(sentences: list[dict]) -> list[dict]:
    """合并被切块/切分打碎的句子：

    - 前一句没有句末标点（被硬切）→ 直接并
    - 两句之间几乎没有停顿（块边界 gap≈0），且其中一句很短 → 并
    """
    out = []
    for s in sentences:
        if not out:
            out.append(dict(s))
            continue
        prev = out[-1]
        gap = s["start"] - prev["end"]
        tail = prev["text"].rstrip()
        nxt = s["text"].strip()
        # 只并「被硬切开的半句」；正常句号结尾的一律保留，保证 SRT 逐句粒度
        hard_cut = not tail.endswith("。！？!?…")
        merged_len = len(tail) + len(nxt)
        if hard_cut and merged_len <= 80:
            prev["text"] = tail + s["text"].lstrip()
            prev["end"] = s["end"]
        else:
            out.append(dict(s))
    return out


def normalize_latin(text: str) -> str:
    """统一英文写法：中文夹单字母转大写、常见缩写转大写。"""
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s*([a-z])\s*(?=[\u4e00-\u9fff])",
                  lambda m: m.group(1).upper(), text)
    for w in LATIN_UPPER:
        text = re.sub(rf"(?<![A-Za-z]){w}(?![A-Za-z])", w.upper(), text, flags=re.IGNORECASE)
    # 英文里孤立的 i 应为 I（如 "oh i like"）
    text = re.sub(r"(?<=[\s,，.。])i(?=[\s,，.。!?])", "I", text)
    for w in LATIN_TITLE:
        text = re.sub(rf"(?<![A-Za-z]){w}(?![A-Za-z])",
                      lambda m: w.capitalize(), text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z])artificial analysis(?![A-Za-z])",
                  "Artificial Analysis", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def apply_glossary(text: str, glossary: dict) -> str:
    """按术语表纠错，长词优先，忽略大小写。"""
    if not glossary:
        return text
    for src in sorted(glossary, key=len, reverse=True):
        text = re.sub(re.escape(src), glossary[src], text, flags=re.IGNORECASE)
    return text


class LocalASR:
    """FunASR 本地识别器（懒加载，可多次调用）。

    分块策略：自己跑 fsmn-vad，再把超过 CHUNK_SEC 的长段按能量最低点强制切开，
    逐块解码后拼接。原因：背景音乐连贯时 VAD 会把几十秒并成一段，而模型对超长
    单段解码会静默丢内容（实测 52 秒一段丢了 10 秒口播）。
    """

    CHUNK_SEC = 20.0
    MIN_SEG_SEC = 0.3

    def __init__(self, cfg: dict | None = None, spk: bool = False, engine: str = "",
                 hotwords: list[str] | None = None):
        self.cfg = cfg or cfg_mod.load()
        self.spk = spk
        self.engine = engine or self.cfg.get("engine") or "auto"
        self.hotwords = hotwords if hotwords is not None else load_hotwords(self.cfg)
        self.model = None
        self._sv_model = None
        self._vad_model = None
        self._punc_model = None
        self._resolved_engine = None

    # ---------- 模型加载 ----------
    def _pick_engine(self) -> str:
        if self._resolved_engine:
            return self._resolved_engine
        md = Path(self.cfg["_models_dir"])
        sv = md / (self.cfg.get("sensevoice_model") or "sensevoice")
        has_sv = (sv / "model.pt").is_file()
        if self.engine == "auto":
            self._resolved_engine = "hybrid" if has_sv else "paraformer"
        elif self.engine == "hybrid" and not has_sv:
            self._resolved_engine = "paraformer"
        else:
            self._resolved_engine = self.engine
        return self._resolved_engine

    def _load_vad(self):
        if self._vad_model is not None:
            return self._vad_model
        from funasr import AutoModel
        md = Path(self.cfg["_models_dir"])
        self._vad_model = AutoModel(model=str(md / self.cfg["vad_model"]),
                                    disable_update=True, disable_pbar=True, disable_log=True)
        return self._vad_model

    def _load_punc(self):
        if getattr(self, "_punc_model", None) is not None:
            return self._punc_model
        from funasr import AutoModel
        md = Path(self.cfg["_models_dir"])
        self._punc_model = AutoModel(model=str(md / self.cfg["punc_model"]),
                                     disable_update=True, disable_pbar=True, disable_log=True)
        return self._punc_model

    def _load_paraformer(self):
        if self.model is not None:
            return self.model
        from funasr import AutoModel
        md = Path(self.cfg["_models_dir"])
        kwargs = dict(
            model=str(md / self.cfg["asr_model"]),
            punc_model=str(md / self.cfg["punc_model"]) if self.cfg.get("punc_model") else None,
            device=self.cfg.get("device", "cpu"),
            disable_update=True, disable_pbar=True, disable_log=True,
        )
        if self.spk and self.cfg.get("spk_model"):
            kwargs["spk_model"] = str(md / self.cfg["spk_model"])
            kwargs["spk_kwargs"] = {"sv_threshold": 0.6}
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        self.model = AutoModel(**kwargs)
        return self.model

    def _load_sensevoice(self):
        if self._sv_model is not None:
            return self._sv_model
        from funasr import AutoModel
        md = Path(self.cfg["_models_dir"])
        self._sv_model = AutoModel(
            model=str(md / (self.cfg.get("sensevoice_model") or "sensevoice")),
            device=self.cfg.get("device", "cpu"),
            disable_update=True, disable_pbar=True, disable_log=True,
        )
        return self._sv_model

    # ---------- 分块 ----------
    def _vad_segments(self, wav: Path) -> list[tuple[float, float]]:
        res = self._load_vad().generate(input=str(wav))
        segs = []
        for seg in (res[0].get("value") if res else []) or []:
            start, end = seg[0] / 1000, seg[1] / 1000
            if end - start >= self.MIN_SEG_SEC:
                segs.append((start, end))
        return segs

    @staticmethod
    def _rms_profile(wav: Path, hop: float = 0.1) -> tuple:
        import numpy as np
        import soundfile as sf
        data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        step = max(1, int(sr * hop))
        n = len(mono) // step
        rms = np.sqrt((mono[: n * step].reshape(n, step) ** 2).mean(axis=1))
        return rms, hop

    def _plan_chunks(self, wav: Path) -> list[tuple[float, float]]:
        """返回 [(start, end)]，每块不超过 CHUNK_SEC，切点取能量最低处。"""
        segs = self._vad_segments(wav)
        if not segs:
            return []
        rms, hop = self._rms_profile(wav)
        chunks = []
        for start, end in segs:
            cur = start
            while end - cur > self.CHUNK_SEC:
                lo = int((cur + self.CHUNK_SEC - 6) / hop)
                hi = int((cur + self.CHUNK_SEC) / hop)
                lo = max(0, min(lo, len(rms) - 1))
                hi = max(lo + 1, min(hi, len(rms)))
                cut = (lo + int(rms[lo:hi].argmin())) * hop
                if cut <= cur + 1:
                    cut = cur + self.CHUNK_SEC / 2
                chunks.append((cur, cut))
                cur = cut
            chunks.append((cur, end))
        return [(a, b) for a, b in chunks if b - a >= self.MIN_SEG_SEC]

    @staticmethod
    def _slice(wav: Path, chunks: list[tuple[float, float]], tmpdir: Path) -> list[Path]:
        paths = []
        for i, (a, b) in enumerate(chunks):
            out = tmpdir / f"chunk_{i:04d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                 "-i", str(wav), "-ac", "1", "-ar", "16000", "-f", "wav", str(out)],
                check=True, capture_output=True)
            paths.append(out)
        return paths

    # ---------- 识别 ----------
    def _paraformer_chunks(self, paths: list[Path], chunks) -> tuple[str, list[dict]]:
        model = self._load_paraformer()
        kwargs = dict(input=[str(p) for p in paths], batch_size_s=300, sentence_timestamp=True)
        if self.hotwords:
            kwargs["hotword"] = " ".join(self.hotwords)
        texts, sentences = [], []
        # 逐块单独解码：list/batch 推理不返回 sentence_info，且块内标点更自然
        for (a, b), path in zip(chunks, paths):
            res = model.generate(input=str(path), batch_size_s=60, sentence_timestamp=True,
                                 **({"hotword": " ".join(self.hotwords)} if self.hotwords else {}))
            res = res[0] if res else {}
            chunk_text = (res.get("text") or "").strip()
            texts.append(chunk_text)
            info = res.get("sentence_info") or []
            if info:
                for s in info:
                    seg = (s.get("text") or "").strip()
                    if not seg:
                        continue
                    sentences.append({
                        "start": round(a + (s.get("start") or 0) / 1000, 2),
                        "end": round(a + (s.get("end") or 0) / 1000, 2),
                        "text": seg, "speaker": s.get("spk"),
                    })
            elif chunk_text:
                sentences.append({"start": round(a, 2), "end": round(b, 2),
                                  "text": chunk_text, "speaker": None})
        return "".join(texts), sentences

    def _sensevoice_chunks(self, paths: list[Path]) -> list[str]:
        model = self._load_sensevoice()
        results = model.generate(input=[str(p) for p in paths], language="auto",
                                 use_itn=True, batch_size_s=300)
        out = []
        for res in results:
            raw = SV_TAG_RE.sub("", (res.get("text") or "")).replace("\n", " ")
            out.append(re.sub(r"\s+", " ", raw).strip())
        return out

    def transcribe(self, media: str | Path) -> dict:
        media = Path(media)
        tmpdir = Path(tempfile.mkdtemp(prefix="dyasr_"))
        wav = _ffmpeg_to_wav(media, tmpdir / "audio.wav")
        try:
            chunks = self._plan_chunks(wav)
            if not chunks:
                return {"text": "", "sentences": [], "duration": 0,
                        "low_speech": True, "engine": self._pick_engine()}
            paths = self._slice(wav, chunks, tmpdir)
            eng = self._pick_engine()

            if eng == "sensevoice":
                texts = self._sensevoice_chunks(paths)
                text = "".join(texts)
                duration = chunks[-1][1]
                sentences = _split_with_timestamps(text, duration)
            else:
                pf_text, sentences = self._paraformer_chunks(paths, chunks)
                text = pf_text
                if eng == "hybrid":
                    try:
                        sv_texts = self._sensevoice_chunks(paths)
                    except Exception:
                        sv_texts = []
                    if sv_texts and len(sv_texts) == len(chunks):
                        text = self._fuse_chunks(pf_text, sv_texts, sentences, chunks)
                    if not sentences:
                        sentences = _split_with_timestamps(text, chunks[-1][1])
                if not sentences:
                    sentences = _split_with_timestamps(text, chunks[-1][1])
        finally:
            for p in tmpdir.glob("*"):
                p.unlink(missing_ok=True)
            try:
                tmpdir.rmdir()
            except OSError:
                pass

        glossary = self.cfg.get("glossary") or {}
        text = cleanup_text(normalize_latin(apply_glossary(text.strip(), glossary)))
        sentences = merge_sentences(sentences)
        sentences = split_long_sentences(sentences)
        for s in sentences:
            s["text"] = cleanup_text(normalize_latin(apply_glossary(s["text"], glossary)))

        duration = sentences[-1]["end"] if sentences else (chunks[-1][1] if chunks else 0)
        low_speech = bool(duration > 5 and len(text) / max(duration, 1) < 0.8)
        return {"text": text, "sentences": sentences, "duration": duration,
                "low_speech": low_speech, "engine": eng, "chunks": len(chunks)}

    PUNCT_CHARS = "，。！？、；：,.!?;:"

    def _repunctuate(self, text: str, sentences: list[dict]):
        """对无标点全文重做断句，并把标点回填到原句时间戳上。"""
        if not text or not sentences or not self.cfg.get("punc_model"):
            return text, sentences
        try:
            out = self._load_punc().generate(input=text)[0]["text"]
        except Exception:
            return text, sentences
        strip = lambda s: re.sub(r"[，。！？、；：,.!?;:\s]", "", s)
        if not out or strip(out) != strip(text):
            return text, sentences
        idxs = [i for i, c in enumerate(out) if c not in self.PUNCT_CHARS]
        res, pos = [], 0
        for s in sentences:
            n = len(s["text"])
            a, b = pos, pos + n
            pos = b
            if a >= len(idxs):
                break
            st = idxs[a]
            en = idxs[min(b - 1, len(idxs) - 1)] + 1
            res.append({**s, "text": out[st:en].strip()})
        return out, res

    def _fuse_chunks(self, pf_text: str, sv_texts: list[str],
                     sentences: list[dict], chunks) -> str:
        """逐块融合：块内对齐融合，避免整篇对齐的漂移。"""
        if not sentences:
            return pf_text
        out = []
        for (a, b), sv in zip(chunks, sv_texts):
            segs = [s for s in sentences if a - 0.01 <= s["start"] < b - 0.01]
            if not segs:
                continue
            pf_part = "".join(s["text"] for s in segs)
            if not sv:
                out.append(pf_part)
                continue
            fused, pmap = fuse_text(pf_part, sv)
            fixed = remap_sentences(segs, pf_part, fused, pmap)
            for s, fs in zip(segs, fixed):
                s["text"] = fs["text"]
            out.append(fused)
        return "".join(out)


def _alnum(s: str) -> int:
    return sum(c.isascii() and c.isalnum() for c in s)


def _alnum_ratio(s: str) -> float:
    return _alnum(s) / max(1, len(s.replace(" ", "")))


def fuse_text(pf_text: str, sv_text: str) -> tuple[str, list[tuple[int, int, int, int]]]:
    """把 sensevoice 的英文/数字校正合入 paraformer 文本。

    只在两种情况下采信 sensevoice：整体以英文为主（英文片段），或差异块很短
    （典型如「h一百」→「H100」）。其余保留 paraformer，避免把其中文幻觉带进来。

    返回 (融合文本, 映射表)；映射表项为 (pf_start, fused_start, pf_len, fused_len)。
    去空格在构建过程中逐块完成，保证映射表与最终文本严格对齐。
    """
    sm = difflib.SequenceMatcher(None, pf_text, sv_text, autojunk=False)
    raw = []                      # (pf_start, pf_len, piece)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        p, sv = pf_text[i1:i2], sv_text[j1:j2]
        if tag == "equal":
            piece = p
        elif tag == "replace":
            take = bool(sv) and _alnum(sv) > _alnum(p) and (
                _alnum_ratio(p) >= 0.5 or len(sv) <= 8)
            piece = sv if take else p
        elif tag == "delete":
            piece = p
        else:  # insert
            piece = sv if re.fullmatch(r"[\x00-\x7f]+", sv or "x") and len(sv) <= 12 else ""
        raw.append((i1, i2 - i1, piece))

    def is_cn(ch: str) -> bool:
        return bool(ch) and "\u4e00" <= ch <= "\u9fff"

    pieces, pmap, acc = [], [], ""
    for k, (pf_start, pf_len, piece) in enumerate(raw):
        prev_ch = acc[-1:] if acc else ""
        next_first = ""
        for nxt in raw[k + 1:]:
            if nxt[2]:
                next_first = nxt[2][0]
                break
        if is_cn(prev_ch):
            piece = re.sub(r"^\s+", "", piece)
        if is_cn(next_first):
            piece = re.sub(r"\s+$", "", piece)
        piece = re.sub(r"(?<=[\u4e00-\u9fff])\s+", "", piece)
        piece = re.sub(r"\s+(?=[\u4e00-\u9fff])", "", piece)
        pmap.append((pf_start, len(acc), pf_len, len(piece)))
        pieces.append(piece)
        acc += piece

    return acc.strip(), pmap


def _map_index(pmap: list[tuple[int, int, int, int]], idx: int) -> int:
    """paraformer 字符下标 → 融合文本下标（块内按比例插值）。"""
    for pf_start, f_start, pf_len, f_len in pmap:
        if idx < pf_start + max(pf_len, 1):
            if pf_len <= 0:
                return f_start
            offset = min(max(idx - pf_start, 0), pf_len) * f_len // pf_len
            return f_start + offset
    return pmap[-1][1] + pmap[-1][3] if pmap else 0


def remap_sentences(sentences: list[dict], pf_text: str,
                    fused: str, pmap) -> list[dict]:
    """把融合文本按原句边界切回句子，切点吸附到最近的标点，保证不丢字、不重字。"""
    if not sentences or not fused:
        return sentences

    PUNCT = "，。！？、；：,.!?;:"
    bounds, acc = [0], 0
    for s in sentences[:-1]:
        acc += len(s["text"])
        bounds.append(acc)

    n = len(sentences)
    cuts = [0]
    for idx, b in enumerate(bounds[1:], 1):
        remaining = n - idx                     # 后面还剩几句
        lo = cuts[-1] + 1                       # 至少切掉 1 个字，保证严格递增
        hi = max(lo, len(fused) - remaining)    # 给后面每句留 1 个字
        pos = min(max(_map_index(pmap, b), lo), hi)
        best, best_dist = pos, 10 ** 9
        for cand in range(max(lo, pos - 12), min(hi, pos + 12) + 1):
            if cand > 0 and fused[cand - 1] in PUNCT:
                dist = abs(cand - pos)
                if dist < best_dist:
                    best, best_dist = cand, dist
        cuts.append(min(max(best, lo), hi))
    cuts.append(len(fused))

    out, prev = [], 0
    for s, cut in zip(sentences, cuts[1:]):
        piece = fused[prev:cut].strip()
        prev = cut
        out.append({**s, "text": piece or s["text"]})
    return out


def _split_with_timestamps(text: str, duration: float) -> list[dict]:
    """无逐句时间戳时按句切分，按字数比例回推时间（SRT 近似）。"""
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", text) if p.strip()]
    if not parts:
        return []
    total = sum(len(p) for p in parts) or 1
    out, cursor = [], 0.0
    for p in parts:
        span = duration * len(p) / total if duration else 0.0
        out.append({"start": round(cursor, 2), "end": round(cursor + span, 2),
                    "text": p, "speaker": None})
        cursor += span
    return out


def format_srt(sentences: list[dict]) -> str:
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, s in enumerate(sentences, 1):
        lines += [str(i), f"{ts(s['start'])} --> {ts(s['end'])}", s["text"], ""]
    return "\n".join(lines)


def format_timestamp_text(sentences: list[dict], gap: float = 2.0) -> str:
    """按时间戳分段：说话间隔超过 gap 秒换段，保留 [mm:ss] 锚点便于回看。"""
    if not sentences:
        return ""
    paras, buf, prev_end = [], [], None
    for s in sentences:
        if prev_end is not None and s["start"] - prev_end >= gap:
            paras.append("\n".join(buf))
            buf = []
        mm, ss = divmod(int(s["start"]), 60)
        buf.append(f"[{mm:02d}:{ss:02d}] {s['text']}")
        prev_end = s["end"]
    if buf:
        paras.append("\n".join(buf))
    return "\n\n".join(paras)


def main():
    ap = argparse.ArgumentParser(description="本地 FunASR 语音识别（离线）")
    ap.add_argument("media", help="音视频文件")
    ap.add_argument("--engine", choices=["auto", "hybrid", "paraformer", "sensevoice"], default="",
                    help="识别引擎（默认 auto：优先 sensevoice）")
    ap.add_argument("--hotwords", help="热词文件（默认 hotwords.txt）")
    ap.add_argument("--json", help="输出 JSON（含逐句时间戳）")
    ap.add_argument("--srt", help="输出 SRT 字幕")
    ap.add_argument("--spk", action="store_true", help="说话人分离（campplus，仅 paraformer）")
    ap.add_argument("--models", help="模型目录")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    if args.models:
        cfg["_models_dir"] = str(Path(args.models).resolve())

    hotwords = load_hotwords(cfg, args.hotwords) if args.hotwords else None
    asr = LocalASR(cfg, spk=args.spk, engine=args.engine, hotwords=hotwords)
    result = asr.transcribe(args.media)

    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.srt:
        Path(args.srt).write_text(format_srt(result["sentences"]), encoding="utf-8")
    print(result["text"])


if __name__ == "__main__":
    main()
