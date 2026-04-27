import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets
import yt_dlp

try:
    import qdarkstyle
except Exception:
    qdarkstyle = None

from language import texts
import utils


SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".brejax_settings.json")
SETTINGS_RECOVERED = False

APP_VERSION = "1.7.0"
APP_DEVELOPER = "Rico"

FORMAT_OPTIONS = [
    "MP3",
    "M4A",
    "AAC",
    "OPUS",
    "WAV",
    "FLAC",
    "ALAC",
    "OGG",
    "MP4",
    "Best audio (no convert)",
]
RESOLUTION_OPTIONS = [
    "Auto (best)",
    "360p",
    "480p",
    "720p",
    "1080p",
    "1440p",
    "2160p (4K)",
]
QUALITY_OPTIONS = ["128", "192", "256", "320"]
SETTINGS_DEFAULTS = {
    "lang": "en",
    "last_folder": os.getcwd(),
    "quality": "192",
    "playlist": False,
    "format": "MP3",
    "embed_metadata": True,
    "save_thumbnail": True,
    "auto_open": False,
    "resolution": "Auto (best)",
}


def strip_ansi_codes(text: str) -> str:
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", str(text))


def load_settings() -> dict:
    global SETTINGS_RECOVERED
    SETTINGS_RECOVERED = False

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("Settings file is not a JSON object.")
    except FileNotFoundError:
        return dict(SETTINGS_DEFAULTS)
    except Exception:
        SETTINGS_RECOVERED = True
        try:
            if os.path.exists(SETTINGS_FILE):
                backup_path = SETTINGS_FILE + ".broken"
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.replace(SETTINGS_FILE, backup_path)
        except Exception:
            pass
        return dict(SETTINGS_DEFAULTS)

    merged = dict(SETTINGS_DEFAULTS)
    merged.update(raw)

    if merged.get("lang") not in ("en", "de"):
        merged["lang"] = SETTINGS_DEFAULTS["lang"]
    if str(merged.get("quality")) not in QUALITY_OPTIONS:
        merged["quality"] = SETTINGS_DEFAULTS["quality"]
    if merged.get("format") not in FORMAT_OPTIONS:
        merged["format"] = SETTINGS_DEFAULTS["format"]
    if merged.get("resolution") not in RESOLUTION_OPTIONS:
        merged["resolution"] = SETTINGS_DEFAULTS["resolution"]
    if not os.path.isdir(str(merged.get("last_folder", ""))):
        merged["last_folder"] = SETTINGS_DEFAULTS["last_folder"]

    for key in ("playlist", "embed_metadata", "save_thumbnail", "auto_open"):
        merged[key] = bool(merged.get(key))

    return merged


def save_settings(data: dict) -> None:
    try:
        merged = dict(SETTINGS_DEFAULTS)
        if isinstance(data, dict):
            merged.update(data)

        settings_dir = os.path.dirname(SETTINGS_FILE) or "."
        os.makedirs(settings_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="brejax_settings_",
            suffix=".json",
            dir=settings_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(merged, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, SETTINGS_FILE)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
    except Exception:
        pass


class BrejaxWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    progress_value = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)

    def __init__(
        self,
        url: str,
        out_folder: str,
        quality: int,
        playlist: bool,
        *,
        format_type: str = "mp3",
        embed_metadata: bool = True,
        save_thumbnail: bool = True,
        resolution_label: str = "Auto (best)",
        ffmpeg_path: Optional[str] = None,
        lang: str = "en",
    ):
        super().__init__()
        self.url = url
        self.out_folder = out_folder
        self.quality = quality
        self.playlist = playlist
        self.format_type = format_type
        self.embed_metadata = embed_metadata
        self.save_thumbnail = save_thumbnail
        self.resolution_label = resolution_label
        self.ffmpeg_path = ffmpeg_path
        self.lang = lang
        self._is_running = True

    def t(self, key: str, fallback: str = "") -> str:
        return texts.get(self.lang, texts["en"]).get(key, fallback or key)

    def stop(self) -> None:
        self._is_running = False

    def run(self) -> None:
        outtmpl = os.path.join(self.out_folder, "%(title)s.%(ext)s")
        options = {
            "outtmpl": outtmpl,
            "quiet": True,
            "noplaylist": not self.playlist,
            "progress_hooks": [self.progress_hook],
            "no_warnings": True,
            "writethumbnail": bool(self.save_thumbnail),
        }

        format_choice = (self.format_type or "").lower().strip()
        postprocessors = []
        need_ffmpeg_for_merge = False

        if self.ffmpeg_path:
            options["ffmpeg_location"] = self.ffmpeg_path

        if format_choice == "mp4":
            options["format"] = utils.get_video_format(self.resolution_label)
            options["merge_output_format"] = "mp4"
            need_ffmpeg_for_merge = True
        elif format_choice in ["mp3", "m4a", "opus", "wav", "aac", "flac", "alac", "ogg"]:
            codec_map = {
                "mp3": "mp3",
                "m4a": "m4a",
                "opus": "opus",
                "wav": "wav",
                "aac": "aac",
                "flac": "flac",
                "alac": "alac",
                "ogg": "vorbis",
            }
            options["format"] = "bestaudio/best"
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": codec_map.get(format_choice, "mp3"),
                    "preferredquality": str(self.quality),
                }
            )
        else:
            options["format"] = "bestaudio/best"

        if self.embed_metadata:
            postprocessors.append({"key": "FFmpegMetadata"})

        if self.save_thumbnail and format_choice != "mp4":
            postprocessors.append({"key": "EmbedThumbnail"})

        if postprocessors:
            options["postprocessors"] = postprocessors

        need_ffmpeg = bool(postprocessors) or need_ffmpeg_for_merge
        if need_ffmpeg and not self.ffmpeg_path:
            self.error.emit(self.t("msg_ffmpeg_required"))
            return

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                try:
                    info = ydl.extract_info(self.url, download=False)
                    if info:
                        if info.get("entries"):
                            entries = [entry for entry in info.get("entries") if entry is not None]
                            playlist_title = info.get("title", "Playlist")
                            self.progress.emit(
                                self.t("playlist_loaded").format(
                                    title=playlist_title,
                                    count=len(entries),
                                )
                            )
                            total_size = utils.estimate_total_size_from_entries(entries)
                            free_space = utils.bytes_free(self.out_folder)
                            if total_size and free_space and total_size > free_space * 0.95:
                                self.error.emit(self.t("disk_space_error"))
                                return
                        else:
                            self.progress.emit(
                                self.t("title_loaded").format(
                                    title=info.get("title", "Unknown title")
                                )
                            )
                except Exception as exc:
                    self.progress.emit(self.t("prefetch_failed").format(error=str(exc)))

                self.progress.emit(
                    self.t("download_starting").format(format=self.format_type.upper())
                )
                ydl.download([self.url])

            self.progress_value.emit(100)
            self.finished.emit()
        except yt_dlp.utils.DownloadError as exc:
            message = str(exc).strip()
            if "Download stopped by user" in message:
                self.error.emit("__STOPPED__")
            else:
                self.error.emit(message or self.t("msg_error"))
        except Exception as exc:
            self.error.emit(str(exc) if str(exc) else self.t("msg_error"))

    def progress_hook(self, data: dict) -> None:
        if not self._is_running:
            raise yt_dlp.utils.DownloadError("Download stopped by user")

        status = data.get("status")
        info = data.get("info_dict") or {}
        title = info.get("title") if isinstance(info, dict) else None

        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if total:
                try:
                    self.progress_value.emit(max(0, min(100, int((downloaded / total) * 100))))
                except Exception:
                    pass

            pct = (data.get("_percent_str") or "").strip()
            speed = (data.get("_speed_str") or "").strip()
            eta = (data.get("_eta_str") or "").strip()

            parts = []
            if title:
                parts.append(f"Downloading: {title}")
            if pct:
                parts.append(pct)
            if speed:
                parts.append(f"@ {speed}")
            if eta:
                parts.append(f"ETA {eta}")
            self.progress.emit(" | ".join(parts))
            return

        if status == "finished":
            self.progress_value.emit(100)
            if title:
                self.progress.emit(f"Finished download: {title} - converting/merging...")
            else:
                self.progress.emit("Download finished - converting...")
            return

        if status == "postprocessing":
            self.progress_value.emit(100)
            postprocessor = data.get("postprocessor", {})
            if isinstance(postprocessor, dict):
                self.progress.emit(f"Postprocessing: {postprocessor.get('key', '')}")
            else:
                self.progress.emit("Postprocessing...")
            return

        self.progress.emit(str(data.get("status") or title or data))


class BrejaxDownloaderUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.lang = self.settings.get("lang", "en")
        self.ffmpeg = utils.find_ffmpeg()
        self.worker_thread: Optional[QtCore.QThread] = None
        self.worker: Optional[BrejaxWorker] = None

        self.resize(820, 640)
        self.init_ui()
        self.apply_language()
        self.refresh_ffmpeg_notice()

        if SETTINGS_RECOVERED:
            QtWidgets.QMessageBox.information(
                self,
                self.t("settings_reset_title"),
                f"{self.t('msg_settings_reset')}\n{self.t('msg_bad_settings_backup')}",
            )
            self.log(self.t("msg_settings_reset"))

        if self.ffmpeg:
            self.log(self.t("ffmpeg_detected").format(path=self.ffmpeg))
        else:
            self.log(self.t("msg_ffmpeg_missing"))

        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clip_text = (clipboard.text() or "").strip()
            if utils.is_url(clip_text):
                self.url_input.setText(clip_text)

    def t(self, key: str, fallback: str = "") -> str:
        return texts.get(self.lang, texts["en"]).get(key, fallback or key)

    def init_ui(self) -> None:
        self.setObjectName("mainWindow")
        self.setStyleSheet(
            """
            QWidget#mainWindow {
                background-color: #1b1f27;
                color: #e5ecf4;
                font-family: "Segoe UI";
                font-size: 10.5pt;
            }
            QLabel {
                background: transparent;
            }
            QLabel[class="sectionTitle"] {
                font-size: 10pt;
                font-weight: 700;
                color: #d7e2ee;
                padding-bottom: 4px;
                background: transparent;
            }
            QLineEdit, QComboBox, QTextEdit {
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                padding: 8px 10px;
                background: rgba(255,255,255,0.04);
                selection-background-color: #4d8cff;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid rgba(102, 170, 255, 0.75);
                background: rgba(255,255,255,0.06);
            }
            QCheckBox {
                spacing: 7px;
                color: #d6dfeb;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QProgressBar {
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 9px;
                text-align: center;
                min-height: 22px;
                font-weight: 700;
                background: rgba(255,255,255,0.04);
            }
            QProgressBar::chunk {
                background-color: #4b9dff;
                border-radius: 7px;
            }
            QTextEdit {
                font-family: Consolas;
                font-size: 10pt;
            }
            QFrame[class="card"] {
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
                background: rgba(255,255,255,0.03);
            }
            """
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)

        self.title_label = QtWidgets.QLabel()
        self.title_label.setStyleSheet("font-size: 16pt; font-weight: 700; color: #f0f4fb;")
        title_box.addWidget(self.title_label)

        self.subtitle_label = QtWidgets.QLabel()
        self.subtitle_label.setStyleSheet("font-size: 9pt; color: #9fb1c3;")
        title_box.addWidget(self.subtitle_label)

        header.addLayout(title_box)
        header.addStretch()

        self.lang_label = QtWidgets.QLabel()
        self.lang_label.setStyleSheet("color: #c8d6e5; font-weight: 600;")
        header.addWidget(self.lang_label)

        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItems(["English", "Deutsch"])
        self.lang_combo.setCurrentIndex(0 if self.lang == "en" else 1)
        self.lang_combo.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.lang_combo)
        root.addLayout(header)

        main_card = QtWidgets.QFrame()
        main_card.setObjectName("mainCard")
        main_card.setProperty("class", "card")
        main_layout = QtWidgets.QVBoxLayout(main_card)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self.lbl_url = QtWidgets.QLabel()
        self.lbl_url.setProperty("class", "sectionTitle")
        main_layout.addWidget(self.lbl_url)

        self.url_input = QtWidgets.QLineEdit()
        main_layout.addWidget(self.url_input)

        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(10)

        self.folder_path = QtWidgets.QLineEdit(self.settings.get("last_folder", os.getcwd()))
        self.folder_path.setReadOnly(True)
        folder_row.addWidget(self.folder_path, 1)

        self.btn_folder = QtWidgets.QPushButton()
        self.btn_folder.clicked.connect(self.select_folder)
        folder_row.addWidget(self.btn_folder)

        self.btn_open_folder = QtWidgets.QPushButton()
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        folder_row.addWidget(self.btn_open_folder)
        main_layout.addLayout(folder_row)

        option_grid = QtWidgets.QGridLayout()
        option_grid.setHorizontalSpacing(12)
        option_grid.setVerticalSpacing(10)

        self.lbl_quality = QtWidgets.QLabel()
        option_grid.addWidget(self.lbl_quality, 0, 0)
        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItems(QUALITY_OPTIONS)
        self.quality_combo.setCurrentText(str(self.settings.get("quality", "192")))
        option_grid.addWidget(self.quality_combo, 0, 1)

        self.lbl_format = QtWidgets.QLabel()
        option_grid.addWidget(self.lbl_format, 0, 2)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS)
        self.format_combo.setCurrentText(self.settings.get("format", "MP3"))
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        option_grid.addWidget(self.format_combo, 0, 3)

        self.lbl_resolution = QtWidgets.QLabel()
        option_grid.addWidget(self.lbl_resolution, 1, 0)
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems(RESOLUTION_OPTIONS)
        self.resolution_combo.setCurrentText(self.settings.get("resolution", "Auto (best)"))
        option_grid.addWidget(self.resolution_combo, 1, 1)

        self.playlist_checkbox = QtWidgets.QCheckBox()
        self.playlist_checkbox.setChecked(self.settings.get("playlist", False))
        option_grid.addWidget(self.playlist_checkbox, 1, 2, 1, 2)

        main_layout.addLayout(option_grid)

        checkbox_row = QtWidgets.QHBoxLayout()
        checkbox_row.setSpacing(18)

        self.embed_metadata_cb = QtWidgets.QCheckBox()
        self.embed_metadata_cb.setChecked(self.settings.get("embed_metadata", True))
        checkbox_row.addWidget(self.embed_metadata_cb)

        self.save_thumbnail_cb = QtWidgets.QCheckBox()
        self.save_thumbnail_cb.setChecked(self.settings.get("save_thumbnail", True))
        checkbox_row.addWidget(self.save_thumbnail_cb)

        self.auto_open_cb = QtWidgets.QCheckBox()
        self.auto_open_cb.setChecked(self.settings.get("auto_open", False))
        checkbox_row.addWidget(self.auto_open_cb)
        checkbox_row.addStretch()
        main_layout.addLayout(checkbox_row)

        self.format_hint = QtWidgets.QLabel()
        self.format_hint.setStyleSheet("color: #94a8bd; font-size: 9pt;")
        main_layout.addWidget(self.format_hint)

        root.addWidget(main_card)

        status_card = QtWidgets.QFrame()
        status_card.setProperty("class", "card")
        status_layout = QtWidgets.QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 16, 16, 16)
        status_layout.setSpacing(10)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet(
            "padding: 10px 12px; border-radius: 10px; font-weight: 700;"
        )
        status_layout.addWidget(self.status_label)

        self.progress_caption = QtWidgets.QLabel()
        self.progress_caption.setProperty("class", "sectionTitle")
        status_layout.addWidget(self.progress_caption)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.progress_bar)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)

        self.btn_start = QtWidgets.QPushButton()
        self.btn_start.clicked.connect(self.start_download)
        self.btn_start.setStyleSheet(self.get_button_style(primary=True))
        button_row.addWidget(self.btn_start)

        self.btn_stop = QtWidgets.QPushButton()
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(self.get_button_style(primary=False))
        button_row.addWidget(self.btn_stop)
        button_row.addStretch()
        status_layout.addLayout(button_row)

        root.addWidget(status_card)

        log_card = QtWidgets.QFrame()
        log_card.setProperty("class", "card")
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(10)

        self.log_label = QtWidgets.QLabel()
        self.log_label.setProperty("class", "sectionTitle")
        log_layout.addWidget(self.log_label)

        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output)

        root.addWidget(log_card, 1)
        self.on_format_changed(self.format_combo.currentIndex())

    def apply_language(self) -> None:
        self.setWindowTitle(self.t("window_title"))
        self.title_label.setText(self.t("window_title"))
        self.subtitle_label.setText(
            self.t("version_label").format(version=APP_VERSION, developer=APP_DEVELOPER)
        )
        self.lang_label.setText(self.t("language_label"))
        self.lbl_url.setText(self.t("youtube_link"))
        self.url_input.setPlaceholderText(self.t("url_placeholder"))
        self.url_input.setToolTip(self.t("url_tooltip"))
        self.folder_path.setToolTip(self.t("folder_tooltip"))
        self.btn_folder.setText(self.t("btn_folder"))
        self.btn_folder.setToolTip(self.t("btn_folder_tooltip"))
        self.btn_open_folder.setText(self.t("btn_open_folder"))
        self.btn_open_folder.setToolTip(self.t("btn_open_folder_tooltip"))
        self.lbl_quality.setText(self.t("mp3_quality"))
        self.lbl_quality.setToolTip(self.t("quality_tooltip"))
        self.quality_combo.setToolTip(self.t("quality_combo_tooltip"))
        self.lbl_format.setText(self.t("format_label"))
        self.format_combo.setToolTip(self.t("format_combo_tooltip"))
        self.lbl_resolution.setText(self.t("resolution_label"))
        self.lbl_resolution.setToolTip(self.t("resolution_tooltip"))
        self.resolution_combo.setToolTip(self.t("resolution_combo_tooltip"))
        self.playlist_checkbox.setText(self.t("playlist_checkbox"))
        self.playlist_checkbox.setToolTip(self.t("playlist_tooltip"))
        self.embed_metadata_cb.setText(self.t("embed_metadata"))
        self.embed_metadata_cb.setToolTip(self.t("embed_metadata_tooltip"))
        self.save_thumbnail_cb.setText(self.t("save_thumbnail"))
        self.save_thumbnail_cb.setToolTip(self.t("save_thumbnail_tooltip"))
        self.auto_open_cb.setText(self.t("auto_open"))
        self.auto_open_cb.setToolTip(self.t("auto_open_tooltip"))
        self.progress_caption.setText(self.t("progress_label"))
        self.log_label.setText(self.t("log_label"))
        self.log_output.setPlaceholderText(self.t("log_placeholder"))
        self.btn_start.setText(self.t("btn_start"))
        self.btn_start.setToolTip(self.t("btn_start_tooltip"))
        self.btn_stop.setText(self.t("btn_stop"))
        self.btn_stop.setToolTip(self.t("btn_stop_tooltip"))
        self.refresh_format_hint()
        if not self.worker:
            self.set_status(self.t("status_idle"), state="idle")

    def refresh_ffmpeg_notice(self) -> None:
        if self.ffmpeg:
            return
        self.set_status(self.t("status_ffmpeg_missing"), state="warning")

    def set_status(self, text: str, *, state: str = "idle") -> None:
        styles = {
            "idle": ("#d8e3ef", "rgba(255,255,255,0.05)", "rgba(255,255,255,0.06)"),
            "active": ("#8ccfff", "rgba(74,157,255,0.12)", "rgba(74,157,255,0.28)"),
            "success": ("#88e4ab", "rgba(81,194,120,0.12)", "rgba(81,194,120,0.28)"),
            "warning": ("#f4c87a", "rgba(244,200,122,0.12)", "rgba(244,200,122,0.28)"),
            "error": ("#ff9f9f", "rgba(220,96,96,0.12)", "rgba(220,96,96,0.28)"),
        }
        fg, bg, border = styles.get(state, styles["idle"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "padding: 10px 12px;"
            "border-radius: 10px;"
            "font-weight: 700;"
            f"color: {fg};"
            f"background: {bg};"
            f"border: 1px solid {border};"
        )

    def refresh_format_hint(self) -> None:
        is_mp4 = self.format_combo.currentText().lower() == "mp4"
        self.format_hint.setText(
            self.t("format_info_video") if is_mp4 else self.t("format_info_audio")
        )

    def get_button_style(self, *, primary: bool) -> str:
        if primary:
            base = "#3f8cff"
            hover = "#5b9dff"
            pressed = "#2f73dc"
        else:
            base = "#5a6270"
            hover = "#727b89"
            pressed = "#4b535f"
        return (
            "QPushButton {"
            f"background-color: {base};"
            "color: white;"
            "font-weight: 700;"
            "padding: 9px 16px;"
            "border-radius: 10px;"
            "border: none;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {pressed};"
            "}"
            "QPushButton:disabled {"
            "background-color: #464c57;"
            "color: #9da8b4;"
            "}"
        )

    def select_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            self.t("btn_folder"),
            self.folder_path.text() or os.getcwd(),
        )
        if folder:
            self.folder_path.setText(folder)
            self.settings["last_folder"] = folder
            save_settings(self.settings)

    def open_output_folder(self) -> None:
        folder = self.folder_path.text().strip()
        if not os.path.isdir(folder):
            QtWidgets.QMessageBox.warning(
                self,
                self.t("folder_not_found_title"),
                self.t("msg_folder_missing"),
            )
            return
        try:
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                self.t("open_folder_failed_title"),
                f"{self.t('msg_folder_open_failed')} {exc}",
            )

    def log(self, message: str) -> None:
        clean = strip_ansi_codes(message)
        timestamp = time.strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {clean}")
        bar = self.log_output.verticalScrollBar()
        bar.setValue(bar.maximum())

    def on_progress(self, message: str) -> None:
        self.log(message)
        clean = strip_ansi_codes(message)
        lower = clean.lower()
        if lower.startswith("title loaded:") or lower.startswith("playlist loaded:"):
            display = clean.split(":", 1)[1].strip() if ":" in clean else clean
            self.set_status(display, state="active")
        elif lower.startswith("finished download") or lower.startswith("postprocessing"):
            self.set_status(self.t("status_converting"), state="warning")
        else:
            short = clean if len(clean) <= 140 else f"{clean[:137]}..."
            self.set_status(short, state="active")

    def on_progress_value(self, value: int) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(max(0, min(100, value)))

    def change_language(self, index: int) -> None:
        self.lang = "en" if index == 0 else "de"
        self.settings["lang"] = self.lang
        save_settings(self.settings)
        self.apply_language()

    def on_format_changed(self, _index: int) -> None:
        is_mp4 = self.format_combo.currentText().lower() == "mp4"
        self.lbl_resolution.setVisible(is_mp4)
        self.resolution_combo.setVisible(is_mp4)
        self.refresh_format_hint()

    def set_download_controls_enabled(self, enabled: bool) -> None:
        self.btn_start.setEnabled(enabled)
        self.btn_stop.setEnabled(not enabled)
        self.url_input.setEnabled(enabled)
        self.btn_folder.setEnabled(enabled)
        self.btn_open_folder.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.playlist_checkbox.setEnabled(enabled)
        self.format_combo.setEnabled(enabled)
        self.resolution_combo.setEnabled(enabled)
        self.embed_metadata_cb.setEnabled(enabled)
        self.save_thumbnail_cb.setEnabled(enabled)
        self.auto_open_cb.setEnabled(enabled)
        self.lang_combo.setEnabled(enabled)

    def build_worker(self, url: str, out_folder: str, quality: int) -> BrejaxWorker:
        return BrejaxWorker(
            url,
            out_folder,
            quality,
            self.playlist_checkbox.isChecked(),
            format_type=self.format_combo.currentText().lower(),
            embed_metadata=self.embed_metadata_cb.isChecked(),
            save_thumbnail=self.save_thumbnail_cb.isChecked(),
            resolution_label=self.resolution_combo.currentText(),
            ffmpeg_path=self.ffmpeg,
            lang=self.lang,
        )

    def start_download(self) -> None:
        if self.worker_thread is not None:
            return

        url = self.url_input.text().strip()
        if not url or not utils.is_url(url):
            QtWidgets.QMessageBox.warning(
                self,
                self.t("missing_link_title"),
                self.t("msg_no_url"),
            )
            return

        out_folder = self.folder_path.text().strip()
        if not os.path.isdir(out_folder):
            QtWidgets.QMessageBox.warning(
                self,
                self.t("invalid_folder_title"),
                self.t("msg_invalid_folder"),
            )
            return

        try:
            quality = int(self.quality_combo.currentText())
        except Exception:
            quality = int(SETTINGS_DEFAULTS["quality"])

        format_choice = self.format_combo.currentText().lower()
        embed_metadata = self.embed_metadata_cb.isChecked()
        save_thumbnail = self.save_thumbnail_cb.isChecked()

        need_ffmpeg = (
            format_choice in ["mp3", "m4a", "opus", "wav", "aac", "flac", "alac", "ogg", "mp4"]
            or embed_metadata
            or save_thumbnail
        )
        if need_ffmpeg and not self.ffmpeg:
            QtWidgets.QMessageBox.critical(
                self,
                self.t("ffmpeg_missing_title"),
                self.t("msg_ffmpeg_required"),
            )
            return

        self.settings.update(
            {
                "lang": self.lang,
                "last_folder": out_folder,
                "quality": str(quality),
                "playlist": self.playlist_checkbox.isChecked(),
                "format": self.format_combo.currentText(),
                "embed_metadata": embed_metadata,
                "save_thumbnail": save_thumbnail,
                "auto_open": self.auto_open_cb.isChecked(),
                "resolution": self.resolution_combo.currentText(),
            }
        )
        save_settings(self.settings)

        self.log_output.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.set_download_controls_enabled(False)
        self.set_status(self.t("status_preparing"), state="active")

        self.log(f"URL: {url}")
        self.log(f"Output: {out_folder}")
        self.log(f"Format: {self.format_combo.currentText()}")
        self.log(f"Quality: {quality} kbps")
        if format_choice == "mp4":
            self.log(f"Resolution: {self.resolution_combo.currentText()}")

        self.worker = self.build_worker(url, out_folder, quality)
        self.worker_thread = QtCore.QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.progress_value.connect(self.on_progress_value)
        self.worker.finished.connect(self.download_finished)
        self.worker.error.connect(self.download_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.cleanup_worker)
        self.worker_thread.start()

    def stop_download(self) -> None:
        if self.worker:
            self.worker.stop()
            self.log(self.t("download_stopped_log"))
            self.set_status(self.t("status_stopped"), state="warning")
        self.btn_stop.setEnabled(False)

    def cleanup_worker(self) -> None:
        if self.worker_thread:
            try:
                self.worker_thread.deleteLater()
            except Exception:
                pass
        if self.worker:
            try:
                self.worker.deleteLater()
            except Exception:
                pass
        self.worker_thread = None
        self.worker = None
        self.set_download_controls_enabled(True)

    def download_finished(self) -> None:
        self.log(self.t("all_done_log"))
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(100)
        self.set_status(self.t("status_done"), state="success")
        QtWidgets.QMessageBox.information(
            self,
            self.t("download_complete_title"),
            self.t("msg_done"),
        )

        if self.settings.get("auto_open", False):
            try:
                self.open_output_folder()
            except Exception:
                pass

    def download_error(self, message: str) -> None:
        if message == "__STOPPED__":
            self.progress_bar.setVisible(False)
            self.set_status(self.t("status_stopped"), state="warning")
            QtWidgets.QMessageBox.information(
                self,
                self.t("download_complete_title"),
                self.t("msg_download_stopped"),
            )
            return

        self.log(self.t("error_log").format(error=message))
        self.progress_bar.setVisible(False)
        self.set_status(self.t("status_error"), state="error")
        QtWidgets.QMessageBox.critical(
            self,
            self.t("error_title"),
            message or self.t("msg_error"),
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.worker:
            self.worker.stop()
        save_settings(self.settings)
        super().closeEvent(event)


def brejax_main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    if qdarkstyle is not None:
        try:
            app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt6())
        except Exception:
            pass

    window = BrejaxDownloaderUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    brejax_main()
