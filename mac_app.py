#!/usr/bin/env python3
"""Mac desktop entry point for video-to-md."""

import os
import re
import sys
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from PySide6.QtCore import QSettings, QThread, Signal, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QCheckBox,
                               QMessageBox, QPushButton, QScrollArea, QTextEdit, QLayout,
                               QVBoxLayout, QWidget, QProgressBar)
from PySide6.QtCore import QUrl

ROOT = Path(__file__).resolve().parent
os.environ["PATH"] = os.pathsep.join(("/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", "")))
if getattr(sys, "frozen", False):
    resources = Path(sys.executable).resolve().parents[1] / "Resources"
    os.environ["PATH"] = os.pathsep.join((str(resources / "bin"), os.environ["PATH"]))
    os.environ["VIDEO_TO_MD_BUNDLED_FFMPEG"] = "1"
    os.environ.setdefault("VIDEO_TO_MD_MODELS_DIR", str(Path.home() / "Library" / "Application Support" / "video-to-md" / "models"))
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(resources / "browsers")
    os.environ["VIDEO_TO_MD_BROWSER_CHANNEL"] = "chromium"
sys.path.insert(0, str(ROOT / "scripts"))
from mac_workflow import run_task, clean_cache, chat_endpoint, DEFAULT_NOTE_PROMPT  # noqa: E402
import config as cfg_mod  # noqa: E402
from download_models import download_required  # noqa: E402

KEY_SERVICE = "video-to-md/mac-app"

STYLE = """
QMainWindow, QWidget#content, QScrollArea { background: #f6f8f6; }
QWidget { font-family: 'PingFang SC', 'Helvetica Neue'; font-size: 14px; color: #1c2d28; }
QFrame#card, QFrame#activityCard { background: #ffffff; border: 1px solid #e3e9e4; border-radius: 10px; }
QFrame#modelCard { background: #edf5ef; border: none; border-radius: 8px; }
QLabel#brand { color: #172c23; font-size: 25px; font-weight: 700; }
QLabel#subtitle, QLabel#hint { color: #5d7066; font-size: 13px; }
QLabel#cardTitle { color: #1c2d28; font-size: 17px; font-weight: 600; }
QLabel#fieldLabel { color: #344a3d; font-size: 13px; font-weight: 600; }
QLabel#modelStatus { color: #25533a; font-weight: 600; }
QLineEdit, QTextEdit { background: white; border: 1px solid #cdd9d0; border-radius: 7px;
                       padding: 8px 11px; selection-background-color: #d7e8d8; }
QLineEdit { min-height: 22px; }
QLineEdit:focus, QTextEdit:focus { border: 1px solid #3d805b; }
QPushButton { background: white; color: #244736; border: 1px solid #cdd9d0;
              border-radius: 7px; padding: 8px 14px; min-height: 22px; font-weight: 600; }
QPushButton:hover { background: #f2f7f2; }
QPushButton:disabled { color: #77897e; background: #f3f5f3; border-color: #e1e7e2; }
QPushButton#choice { background: #f7f9f7; color: #344a3d; border-color: #e2e9e3; }
QPushButton#choice:checked { background: #e5f1e7; color: #1d5637; border-color: #9abfa4; }
QPushButton#choice:disabled { background: #f4f5f4; color: #8b998f; border-color: #edf0ed; }
QPushButton#primary { background: #214b34; color: white; border-color: #214b34; padding: 9px 22px; }
QPushButton#primary:hover { background: #2d6245; }
QPushButton#primary:disabled { background: #a8baad; border-color: #a8baad; color: white; }
QPushButton#modelButton { background: white; }
QProgressBar { border: none; border-radius: 3px; background: #deebe1; height: 7px; text-align: center; }
QProgressBar::chunk { background: #397a53; border-radius: 3px; }
"""


class ChoiceRow(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, choices):
        super().__init__()
        self.choices = choices
        self.buttons = []
        self.index = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        for i, (title, _) in enumerate(choices):
            button = QPushButton(title)
            button.setObjectName("choice")
            button.setCheckable(True)
            button.setChecked(i == 0)
            button.clicked.connect(lambda _checked, index=i: self.setCurrentIndex(index))
            row.addWidget(button, 1)
            self.buttons.append(button)

    def count(self):
        return len(self.choices)

    def currentData(self):
        return self.choices[self.index][1]

    def itemData(self, index):
        return self.choices[index][1]

    def setCurrentIndex(self, index):
        if index == self.index or not self.buttons[index].isEnabled():
            return
        self.index = index
        for i, button in enumerate(self.buttons):
            button.setChecked(i == index)
        self.currentIndexChanged.emit(index)

    def setOptionEnabled(self, index, enabled):
        self.buttons[index].setEnabled(enabled)
        self.buttons[index].setVisible(enabled)


class ModelDownloadWorker(QThread):
    progress = Signal(str)
    completed = Signal()
    failed = Signal(str)

    def __init__(self, speakers: bool = False):
        super().__init__()
        self.speakers = speakers

    def run(self) -> None:
        try:
            cfg = cfg_mod.load()
            if self.speakers:
                cfg.update(spk_model="campplus", download_speaker_model=True)
            else:
                cfg.update(spk_model="", download_speaker_model=False)
            download_required(cfg, on_progress=self.progress.emit)
            self.completed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class Worker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, source: str, kind: str, target: str, dest: str, keep: set[str], vault: str, url: str, model: str, key: str, note_prompt: str, speakers: bool = False):
        super().__init__()
        self.options = (source, kind, target, dest, keep, vault, url, model, key, note_prompt, speakers)

    def run(self) -> None:
        source, kind, target, dest, keep, vault, url, model, key, note_prompt, speakers = self.options
        try:
            result = run_task(source, kind, target, Path(dest), keep=keep,
                              vault=Path(vault) if vault else None, api_url=url, model=model,
                              api_key=key, note_prompt=note_prompt, on_stage=self.progress.emit,
                              distinguish_speakers=speakers)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频转知识")
        self.resize(1040, 840)
        self.setMinimumSize(760, 600)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("video-to-md", "mac-app")
        self.worker = None
        self.model_worker = None
        self.result_folder = ""
        cache_root = Path.home() / "Library" / "Caches" / "video-to-md" / "jobs"
        clean_cache(cache_root)
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.timeout.connect(lambda: clean_cache(cache_root))
        self.cleanup_timer.start(60 * 60 * 1000)

        self.source_type = ChoiceRow([("抖音链接", "douyin"), ("本地视频", "video"), ("本地音频", "audio")])
        self.source_type.currentIndexChanged.connect(self.update_choices)
        self.target = ChoiceRow([("Obsidian 笔记", "obsidian"), ("视频文件", "video"),
                                 ("音频文件", "audio"), ("逐字稿", "transcript")])
        self.target.currentIndexChanged.connect(self.update_choices)
        self.keep_video = QCheckBox("保留视频")
        self.keep_audio = QCheckBox("保留音频")
        self.keep_transcript = QCheckBox("保留逐字稿与字幕")
        self.speakers = QCheckBox("区分说话人（可选，需额外下载模型）")
        self.speakers.toggled.connect(self.refresh_models)
        self.vault = QLineEdit(self.settings.value("vault", ""))
        self.vault.setPlaceholderText("选择 Obsidian 知识库文件夹")
        choose_vault = QPushButton("选择…")
        choose_vault.clicked.connect(self.choose_vault)
        vault_row = QHBoxLayout()
        vault_row.addWidget(self.vault)
        vault_row.addWidget(choose_vault)

        self.link = QLineEdit()
        self.link.setPlaceholderText("粘贴公开抖音视频链接")
        choose_source = QPushButton("选择文件…")
        choose_source.clicked.connect(self.choose_source)
        self.choose_source_button = choose_source
        source_row = QHBoxLayout()
        source_row.addWidget(self.link)
        source_row.addWidget(choose_source)
        self.dest = QLineEdit(self.settings.value("destination", str(Path.home() / "Movies" / "视频转知识")))
        choose = QPushButton("选择…")
        choose.clicked.connect(self.choose_folder)
        dest_row = QHBoxLayout()
        dest_row.setContentsMargins(0, 0, 0, 0)
        dest_row.addWidget(self.dest)
        dest_row.addWidget(choose)

        saved_url = self.settings.value("api_url", "")
        self.api_url = QLineEdit(chat_endpoint(saved_url) if saved_url in
                                 {"https://api.deepseek.com", "https://api.deepseek.com/"} else saved_url)
        self.api_url.setPlaceholderText("https://…/chat/completions")
        self.model = QLineEdit(self.settings.value("model", ""))
        self.model.setPlaceholderText("模型名称")
        try:
            saved_key = keyring.get_password(KEY_SERVICE, "api_key") or ""
        except KeyringError:
            saved_key = ""
        self.key = QLineEdit(saved_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API Key 存入 macOS 钥匙串")

        source_card = QFrame()
        source_card.setObjectName("card")
        source = QVBoxLayout(source_card)
        source.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        source.setContentsMargins(24, 22, 24, 24)
        source.setSpacing(10)
        source.addWidget(self.label("输入与产物", "cardTitle"))
        source.addWidget(self.label("选择素材，再指定要得到的文件或笔记。", "hint"))
        source.addWidget(self.label("输入来源", "fieldLabel"))
        source.addWidget(self.source_type)
        source.addLayout(source_row)
        source.addWidget(self.label("最终产物", "fieldLabel"))
        source.addWidget(self.target)
        keep_row = QHBoxLayout()
        for control in (self.keep_video, self.keep_audio, self.keep_transcript):
            keep_row.addWidget(control)
        source.addLayout(keep_row)
        source.addWidget(self.speakers)
        self.speaker_hint = self.label("说话人标记由模型推断，重叠讲话或长音频可能分错。", "hint")
        source.addWidget(self.speaker_hint)
        source.addWidget(self.label("未保留的临时文件在任务成功 24 小时后清理。", "hint"))
        source.addWidget(self.label("保存目录", "fieldLabel"))
        source.addLayout(dest_row)
        self.vault_label = self.label("Obsidian 知识库", "fieldLabel")
        source.addWidget(self.vault_label)
        source.addLayout(vault_row)
        self.vault_row = (self.vault, choose_vault)

        ai_card = QFrame()
        ai_card.setObjectName("card")
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        ai_layout.setContentsMargins(24, 22, 24, 24)
        ai_layout.setSpacing(10)
        ai_title = QHBoxLayout()
        ai_title.addWidget(self.label("笔记整理", "cardTitle"))
        ai_title.addStretch()
        preset = QPushButton("使用 DeepSeek 配置")
        preset.clicked.connect(self.use_deepseek)
        ai_title.addWidget(preset)
        ai_layout.addLayout(ai_title)
        ai_layout.addWidget(self.label("连接兼容 Chat Completions 的服务，整理逐字稿。", "hint"))
        ai_layout.addWidget(self.label("API 地址", "fieldLabel"))
        ai_layout.addWidget(self.api_url)
        ai_layout.addWidget(self.label("模型", "fieldLabel"))
        ai_layout.addWidget(self.model)
        ai_layout.addWidget(self.label("API Key", "fieldLabel"))
        ai_layout.addWidget(self.key)
        ai_layout.addWidget(self.label("笔记提示词", "fieldLabel"))
        self.note_prompt = QTextEdit()
        self.note_prompt.setPlainText(self.settings.value("note_prompt", DEFAULT_NOTE_PROMPT))
        self.note_prompt.setFixedHeight(150)
        ai_layout.addWidget(self.note_prompt)
        ai_layout.addWidget(self.label("逐字稿会自动附加。请保留 JSON 字段和原文引用要求，否则可能无法生成笔记。", "hint"))
        self.ai_card = ai_card

        self.start = QPushButton("开始处理")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_job)
        self.model_status = QLabel()
        self.model_status.setObjectName("modelStatus")
        self.download_models = QPushButton("下载语音模型")
        self.download_models.setObjectName("modelButton")
        self.download_models.clicked.connect(self.start_model_download)
        model_card = QFrame()
        model_card.setObjectName("modelCard")
        model_row = QHBoxLayout(model_card)
        model_row.setContentsMargins(18, 10, 18, 10)
        model_row.addWidget(self.model_status)
        model_row.addStretch()
        model_row.addWidget(self.download_models)
        self.model_card = model_card
        self.model_detail = QLabel("")
        self.model_detail.setObjectName("hint")
        self.model_detail.hide()
        self.model_progress = QProgressBar()
        self.model_progress.setTextVisible(False)
        self.model_progress.hide()
        model_box = QVBoxLayout()
        model_box.setSpacing(5)
        model_box.addWidget(model_card)
        model_box.addWidget(self.model_detail)
        model_box.addWidget(self.model_progress)
        self.open_button = QPushButton("在 Finder 中打开")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_folder)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start)
        buttons.addWidget(self.open_button)
        buttons.addStretch()

        self.status = QLabel("选择输入和最终产物，准备开始。")
        self.status.setObjectName("modelStatus")
        self.status.setWordWrap(True)
        self.task_progress = QProgressBar()
        self.task_progress.setTextVisible(False)
        self.task_progress.setRange(0, 1)
        self.task_progress.setValue(0)
        activity_card = QFrame()
        activity_card.setObjectName("activityCard")
        activity = QVBoxLayout(activity_card)
        activity.setContentsMargins(20, 14, 20, 14)
        activity.setSpacing(8)
        activity.addWidget(self.status)
        activity.addWidget(self.task_progress)
        activity.addLayout(buttons)
        self.task_steps = []
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        preview_card = QFrame()
        preview_card.setObjectName("card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(24, 20, 24, 24)
        self.preview.setMinimumHeight(160)
        preview_layout.addWidget(self.label("结果预览", "cardTitle"))
        preview_layout.addWidget(self.preview, 1)

        content = QWidget()
        content.setObjectName("content")
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(0, 8, 0, 24)
        layout.setSpacing(16)
        layout.addWidget(source_card)
        layout.addWidget(ai_card)
        layout.addWidget(preview_card)

        root = QWidget()
        root.setObjectName("content")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(32, 24, 32, 20)
        root_layout.setSpacing(14)
        root_layout.addWidget(self.label("视频转知识", "brand"))
        root_layout.addWidget(self.label("下载视频、提取音频、生成逐字稿与 Obsidian 笔记", "subtitle"))
        scroller = QScrollArea()
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setWidgetResizable(True)
        scroller.setWidget(content)
        root_layout.addLayout(model_box)
        root_layout.addWidget(activity_card)
        root_layout.addWidget(scroller, 1)
        self.setCentralWidget(root)
        self.update_choices()

    @staticmethod
    def label(text: str, name: str) -> QLabel:
        item = QLabel(text)
        item.setObjectName(name)
        return item

    def refresh_models(self) -> bool:
        cfg = cfg_mod.load()
        base_cfg = dict(cfg, spk_model="")
        missing = any(p.startswith("模型缺失") for p in cfg_mod.check(base_cfg))
        needs_asr = self.target.currentData() in {"transcript", "obsidian"}
        speaker_needed = needs_asr and self.speakers.isChecked()
        speaker_path = Path(cfg["_models_dir"]) / "campplus" / "campplus_cn_common.bin"
        speaker_missing = speaker_needed and not speaker_path.is_file()
        self.model_status.setText("语音模型尚未安装 · 转写与笔记需要下载约 2.1 GB" if missing else
                                  "说话人模型未安装 · 勾选区分说话人后需下载" if speaker_missing else
                                  "语音模型已就绪 · 可本地转写" + ("并区分说话人" if speaker_needed else ""))
        self.download_models.setText("下载语音与说话人模型" if missing and speaker_needed else
                                     "下载语音模型" if missing else "下载说话人模型")
        self.download_models.setVisible(missing or speaker_missing)
        self.download_models.setEnabled((missing or speaker_missing) and not (self.model_worker and self.model_worker.isRunning()))
        self.start.setEnabled(not ((missing and needs_asr) or speaker_missing) and not (self.worker and self.worker.isRunning()))
        if not (self.worker and self.worker.isRunning()):
            self.status.setText("需要先下载所选语音模型才能开始此任务。" if (missing and needs_asr) or speaker_missing else "选择输入和最终产物，准备开始。")
        return not ((missing and needs_asr) or speaker_missing)

    def update_choices(self) -> None:
        kind = self.source_type.currentData()
        allowed = {"douyin": {"video", "audio", "transcript", "obsidian"},
                   "video": {"audio", "transcript"},
                   "audio": {"transcript"}}[kind]
        for i in range(self.target.count()):
            self.target.setOptionEnabled(i, self.target.itemData(i) in allowed)
        if self.target.currentData() not in allowed:
            self.target.setCurrentIndex(next(i for i in range(self.target.count()) if self.target.itemData(i) in allowed))
        target = self.target.currentData()
        self.choose_source_button.setVisible(kind != "douyin")
        self.link.setPlaceholderText("粘贴公开抖音视频链接" if kind == "douyin" else "选择本地文件")
        for widget in self.vault_row:
            widget.setVisible(target == "obsidian")
        self.vault_label.setVisible(target == "obsidian")
        self.ai_card.setVisible(target == "obsidian")
        self.keep_video.setVisible(kind in {"douyin", "video"} and target != "video")
        self.keep_audio.setVisible(target in {"transcript", "obsidian"})
        self.keep_transcript.setVisible(target == "obsidian")
        self.speakers.setVisible(target in {"transcript", "obsidian"})
        self.speaker_hint.setVisible(target in {"transcript", "obsidian"})
        self.api_url.setEnabled(target == "obsidian")
        self.model.setEnabled(target == "obsidian")
        self.key.setEnabled(target == "obsidian")
        self.refresh_models()

    def choose_source(self) -> None:
        kind = self.source_type.currentData()
        filters = "视频 (*.mp4 *.mov *.mkv *.m4v)" if kind == "video" else "音频 (*.mp3 *.m4a *.wav *.aac *.flac *.mp4)"
        path, _ = QFileDialog.getOpenFileName(self, "选择媒体文件", str(Path.home()), filters)
        if path:
            self.link.setText(path)

    def choose_vault(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择 Obsidian 知识库", self.vault.text())
        if folder:
            self.vault.setText(folder)

    def use_deepseek(self) -> None:
        self.api_url.setText("https://api.deepseek.com/chat/completions")
        self.model.setText("deepseek-flash")

    def start_model_download(self) -> None:
        self.download_models.setEnabled(False)
        self.model_detail.show()
        self.model_detail.setText("正在准备下载，请保持网络连接…")
        self.model_progress.setRange(0, 0)
        self.model_progress.show()
        self.model_worker = ModelDownloadWorker(self.speakers.isVisible() and self.speakers.isChecked())
        self.model_worker.progress.connect(self.model_download_progress)
        self.model_worker.completed.connect(self.model_download_done)
        self.model_worker.failed.connect(self.model_download_failed)
        self.model_worker.finished.connect(self.refresh_models)
        self.model_worker.start()

    def model_download_progress(self, message: str) -> None:
        match = re.search(r"(\d+)/(\d+)：(.+)", message)
        self.model_detail.setText(f"正在下载第 {match[1]}/{match[2]} 个模型：{match[3]}（文件进度未知）" if match else message)

    def model_download_done(self) -> None:
        self.model_progress.hide()
        self.model_detail.setText("模型下载完成。")
        if self.refresh_models():
            self.model_detail.setText("模型下载完成，可以开始转写。")
        else:
            self.model_detail.setText("仍有模型缺失，请重试下载。")

    def model_download_failed(self, error: str) -> None:
        self.model_progress.hide()
        self.download_models.setEnabled(True)
        self.model_detail.setText("模型下载失败，可重试。")
        QMessageBox.critical(self, "模型下载失败", error)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dest.text())
        if folder:
            self.dest.setText(folder)

    def start_job(self) -> None:
        if not self.refresh_models():
            return
        link, dest = self.link.text().strip(), self.dest.text().strip()
        url, model, key = self.api_url.text().strip(), self.model.text().strip(), self.key.text().strip()
        if not link or not dest:
            QMessageBox.warning(self, "信息不完整", "请填写视频链接和保存目录。")
            return
        target = self.target.currentData()
        if target == "obsidian" and (not all((url, model, key)) or not Path(self.vault.text()).is_dir()):
            QMessageBox.warning(self, "设置不完整", "请选择 Obsidian 知识库，并填写 API 地址、模型和 API Key。")
            return
        note_prompt = self.note_prompt.toPlainText().strip()
        if target == "obsidian" and not note_prompt:
            QMessageBox.warning(self, "提示词为空", "请填写笔记提示词。")
            return
        if target == "obsidian":
            try:
                url = chat_endpoint(url)
                self.api_url.setText(url)
            except ValueError as exc:
                QMessageBox.warning(self, "API 地址不正确", str(exc))
                return
        if key:
            try:
                keyring.set_password(KEY_SERVICE, "api_key", key)
            except KeyringError as exc:
                QMessageBox.warning(self, "无法保存 API Key", f"钥匙串不可用，本次仍可继续：{exc}")
        self.settings.setValue("destination", dest)
        self.settings.setValue("api_url", url)
        self.settings.setValue("model", model)
        self.settings.setValue("vault", self.vault.text())
        if target == "obsidian":
            self.settings.setValue("note_prompt", note_prompt)
        self.start.setEnabled(False)
        self.open_button.setEnabled(False)
        self.preview.clear()
        keep = {name for name, widget in (("video", self.keep_video), ("audio", self.keep_audio),
                                          ("transcript", self.keep_transcript)) if widget.isVisible() and widget.isChecked()}
        self.worker = Worker(link, self.source_type.currentData(), target, dest, keep,
                             self.vault.text(), url, model, key, note_prompt,
                             self.speakers.isVisible() and self.speakers.isChecked())
        self.task_steps = (["解析抖音链接", "下载视频"] if self.source_type.currentData() == "douyin" else [])
        if target in {"audio", "transcript", "obsidian"} and self.source_type.currentData() != "audio":
            self.task_steps.append("提取音频")
        if target in {"transcript", "obsidian"}:
            self.task_steps.append("语音转文字")
        if target == "obsidian":
            self.task_steps.append("整理并写入 Obsidian")
        self.task_steps.append("保存结果")
        self.task_progress.setRange(0, len(self.task_steps))
        self.task_progress.setValue(0)
        self.status.setText(f"准备处理 · 共 {len(self.task_steps)} 步")
        self.worker.progress.connect(self.job_progress)
        self.worker.completed.connect(self.job_done)
        self.worker.failed.connect(self.job_failed)
        self.worker.finished.connect(lambda: self.start.setEnabled(True))
        self.worker.start()

    def job_progress(self, message: str) -> None:
        if message.startswith("完成"):
            self.task_progress.setValue(self.task_progress.maximum())
            self.status.setText("所有步骤已完成")
            return
        if message in self.task_steps:
            index = self.task_steps.index(message) + 1
            self.task_progress.setValue(index - 1)
            self.status.setText(f"正在进行第 {index}/{len(self.task_steps)} 步：{message}")
        else:
            self.status.setText(message)

    def job_done(self, result: dict) -> None:
        self.task_progress.setValue(self.task_progress.maximum())
        self.result_folder = result["output"]
        self.status.setText(f"完成：{self.result_folder}")
        self.open_button.setEnabled(True)
        preview = Path(result["preview"]) if result["preview"] else None
        self.preview.setPlainText(preview.read_text(encoding="utf-8") if preview and preview.is_file() else "")

    def job_failed(self, error: str) -> None:
        self.status.setText(f"处理失败 · {error}")
        QMessageBox.critical(self, "处理失败", error)

    def open_folder(self) -> None:
        path = Path(self.result_folder)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
