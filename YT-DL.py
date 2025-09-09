import sys
import os
import re
import shutil
import subprocess
import platform
import json
import time
from typing import Optional
from PyQt6 import QtWidgets, QtCore, QtGui
import yt_dlp
import qdarkstyle
from language import texts
import utils # utils.py

# Settings file
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".brejax_settings.json")

# App version & developer
APP_VERSION = "1.6.1"
APP_DEVELOPER = "Rico"

def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(d: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Remove __pycache__ on startup
if os.path.exists('__pycache__'):
    shutil.rmtree('__pycache__', ignore_errors=True)

# Remove ANSI codes from yt-dlp logs
def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

class BrejaxWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)

    def __init__(self, url: str, out_folder: str, quality: int, playlist: bool,
                 format_type: str = 'mp3', embed_metadata: bool = True,
                 save_thumbnail: bool = True, resolution_label: str = "Auto (best)",
                 ffmpeg_path: Optional[str] = None):
        super().__init__()
        self.url = url
        self.out_folder = out_folder
        self.quality = quality
        self.playlist = playlist
        self.format_type = format_type  # mp3, m4a, opus, wav, mp4, best audio, aac, flac, alac, ogg
        self.embed_metadata = embed_metadata
        self.save_thumbnail = save_thumbnail
        self.resolution_label = resolution_label
        self._is_running = True
        self.ffmpeg_path = ffmpeg_path

    def stop(self):
        self._is_running = False

    def run(self):
        outtmpl = os.path.join(self.out_folder, '%(title)s.%(ext)s')
        options = {
            'outtmpl': outtmpl,
            'quiet': True,
            'noplaylist': not self.playlist,
            'progress_hooks': [self.progress_hook],
            'no_warnings': True,
            'writethumbnail': bool(self.save_thumbnail),
        }

        format_choice = (self.format_type or "").lower().strip()
        postprocessors = []

        # Video case (MP4) - allow resolution selection via utils.get_video_format
        if format_choice == 'mp4':
            # get format string from utils
            fmt = utils.get_video_format(self.resolution_label)
            options.update({'format': fmt, 'merge_output_format': 'mp4'})
            # merging requires ffmpeg
            need_ffmpeg_for_merge = True
        else:
            need_ffmpeg_for_merge = False

        # Audio extraction/conversion codecs
        if format_choice in ['mp3', 'm4a', 'opus', 'wav', 'aac', 'flac', 'alac', 'ogg']:
            options.update({'format': 'bestaudio/best'})
            # set preferredcodec mapping
            preferredcodec = 'mp3' if format_choice == 'mp3' else (
                'm4a' if format_choice == 'm4a' else (
                    'opus' if format_choice == 'opus' else (
                        'wav' if format_choice == 'wav' else (
                            'aac' if format_choice == 'aac' else (
                                'flac' if format_choice == 'flac' else (
                                    'alac' if format_choice == 'alac' else (
                                        'ogg' if format_choice == 'ogg' else 'mp3'
                                    )
                                )
                            )
                        )
                    )
                )
            )
            postprocessors.append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': preferredcodec,
                'preferredquality': str(self.quality),
            })
        elif format_choice in ['bestaudio', 'best audio (no convert)']:
            options.update({'format': 'bestaudio/best'})

        # Add metadata embed if requested
        if self.embed_metadata:
            postprocessors.append({'key': 'FFmpegMetadata'})

        # Add thumbnail embedding for audio formats (non-MP4)
        if self.save_thumbnail and format_choice != 'mp4':
            postprocessors.append({'key': 'EmbedThumbnail'})

        if postprocessors:
            options['postprocessors'] = postprocessors

        # If conversion/merge/embed required but ffmpeg missing -> error
        need_ffmpeg = False
        if postprocessors:
            need_ffmpeg = True
        if need_ffmpeg_for_merge:
            need_ffmpeg = True

        if need_ffmpeg and not self.ffmpeg_path:
            self.error.emit("FFmpeg not found. Some postprocessing (convert/embed/merge) requires ffmpeg.")
            return

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                # pre-fetch info for title/playlist and space estimate
                try:
                    info = ydl.extract_info(self.url, download=False)
                    if info:
                        if info.get('entries'):
                            entries = [e for e in info.get('entries') if e is not None]
                            pname = info.get('title', 'Playlist')
                            self.progress.emit(f"Title loaded: Playlist: {pname} ({len(entries)} items)")
                            total = utils.estimate_total_size_from_entries(entries)
                            free = utils.bytes_free(self.out_folder)
                            if total and free and total > free * 0.95:
                                self.error.emit("Not enough disk space for this playlist. Aborting.")
                                return
                        else:
                            title = info.get('title', 'Unknown title')
                            self.progress.emit(f"Title loaded: {title}")
                except Exception as e:
                    self.progress.emit(f"Could not pre-fetch info: {e}")

                self.progress.emit(f"Starting download (format: {self.format_type.upper()})")
                ydl.download([self.url])

            self.finished.emit()
        except Exception as e:
            try:
                msg = str(e)
            except Exception:
                msg = "Unknown error during download."
            self.error.emit(msg)

    # (rest unchanged)
    def _attempt_rename_final(self, info_dict, expected_ext):
        title = None
        if isinstance(info_dict, dict):
            title = info_dict.get('title')
        if not title:
            return
        safe = utils.sanitize_filename(title)

        guessed = None
        try:
            guessed = info_dict.get('filepath') or info_dict.get('filename') or info_dict.get('_filename')
        except Exception:
            guessed = None

        target_candidate = None
        if guessed:
            try:
                base = os.path.splitext(guessed)[0]
                final_guess = base + "." + expected_ext
                if os.path.exists(final_guess):
                    target_candidate = final_guess
            except Exception:
                target_candidate = None

        if not target_candidate:
            try:
                for fname in os.listdir(self.out_folder):
                    path = os.path.join(self.out_folder, fname)
                    if not os.path.isfile(path):
                        continue
                    stem = os.path.splitext(fname)[0].lower()
                    if safe.lower() in stem or (title and title.lower() in stem):
                        if fname.lower().endswith("." + expected_ext.lower()):
                            target_candidate = path
                            break
            except Exception:
                target_candidate = None

        if not target_candidate:
            return

        final_name = f"{safe}.{expected_ext}"
        final_path = os.path.join(self.out_folder, final_name)
        final_path = utils.unique_path(final_path)
        try:
            os.rename(target_candidate, final_path)
            self.progress.emit(f"Saved as: {os.path.basename(final_path)}")
        except Exception:
            self.progress.emit(f"Could not rename file: {os.path.basename(target_candidate)}")

    def progress_hook(self, d):
        if not self._is_running:
            raise yt_dlp.utils.DownloadError("Download stopped by user")

        status = d.get('status')
        info = d.get('info_dict') or {}
        title = None
        if isinstance(info, dict):
            title = info.get('title')
        elif isinstance(info, str):
            title = os.path.splitext(os.path.basename(info))[0]

        if status == 'downloading':
            pct = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '').strip()
            eta = d.get('_eta_str', '').strip()
            msg_parts = []
            if title:
                msg_parts.append(f"Downloading: {title}")
            if pct:
                msg_parts.append(pct)
            if speed:
                msg_parts.append(f"@ {speed}")
            if eta:
                msg_parts.append(f"ETA {eta}")
            self.progress.emit(" | ".join(msg_parts))
        elif status == 'finished':
            if title:
                self.progress.emit(f"Finished download: {title} — converting/merging...")
            else:
                self.progress.emit("Download finished, converting...")
            expected_ext = 'mp4' if self.format_type == 'mp4' else (
                'mp3' if self.format_type == 'mp3' else (
                    'm4a' if self.format_type == 'm4a' else (
                        'opus' if self.format_type == 'opus' else (
                            'wav' if self.format_type == 'wav' else 'mp3'
                        )
                    )
                )
            )
            QtCore.QTimer.singleShot(1500, lambda: self._attempt_rename_final(info, expected_ext))
        elif status == 'postprocessing':
            pp = d.get('postprocessor', {})
            if isinstance(pp, dict):
                key = pp.get('key', '')
                self.progress.emit(f"Postprocessing: {key}")
            else:
                self.progress.emit("Postprocessing...")
        else:
            txt = d.get('info_dict', {}).get('title') if isinstance(d.get('info_dict'), dict) else None
            self.progress.emit(str(d.get('status') or txt or d))

class BrejaxDownloaderUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.lang = 'en'
        self.resize(760, 520)
        self.worker_thread: Optional[QtCore.QThread] = None
        self.worker: Optional[BrejaxWorker] = None
        self.ffmpeg = utils.find_ffmpeg()
        self.settings = load_settings()
        self.init_ui()

        if not self.ffmpeg:
            self.log("⚠️ FFmpeg not found. Some postprocessing will fail without ffmpeg.")
            try:
                QtWidgets.QMessageBox.warning(self, "FFmpeg not found",
                                              "FFmpeg nicht gefunden. MP3-Konvertierung oder Einbetten von Thumbnails funktioniert ohne FFmpeg nicht.")
            except Exception:
                pass

    def init_ui(self):
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.font = QtGui.QFont("Segoe UI", 11)

        # top language + version row
        top_row = QtWidgets.QHBoxLayout()
        top_row.addStretch()
        self.lang_label = QtWidgets.QLabel()
        self.lang_label.setFont(self.font)
        top_row.addWidget(self.lang_label)
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItems(["English", "Deutsch"])
        self.lang_combo.setCurrentIndex(0 if self.settings.get("lang", "en") == "en" else 1)
        self.lang_combo.setFont(self.font)
        self.lang_combo.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.lang_combo.currentIndexChanged.connect(self.change_language)
        top_row.addWidget(self.lang_combo)

        # version/developer label
        self.info_label = QtWidgets.QLabel(f"Version: {APP_VERSION} | Developer: {APP_DEVELOPER}")
        self.info_label.setFont(QtGui.QFont("Segoe UI", 9))
        self.info_label.setStyleSheet("color: #aab6c3;")
        top_row.addWidget(self.info_label)
        top_row.addStretch()
        self.layout.addLayout(top_row)

        # YouTube Link Input
        self.lbl_url = QtWidgets.QLabel()
        self.lbl_url.setFont(self.font)
        self.layout.addWidget(self.lbl_url)
        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setFont(self.font)
        self.url_input.installEventFilter(self)
        self.layout.addWidget(self.url_input)

        # Output folder picker + open folder button
        folder_layout = QtWidgets.QHBoxLayout()
        last_folder = self.settings.get("last_folder", os.getcwd())
        self.folder_path = QtWidgets.QLineEdit(last_folder)
        self.folder_path.setReadOnly(True)
        self.folder_path.setFont(self.font)
        folder_layout.addWidget(self.folder_path)

        self.btn_folder = QtWidgets.QPushButton()
        self.btn_folder.setFont(self.font)
        self.btn_folder.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_folder.clicked.connect(self.select_folder)
        self.btn_folder.setStyleSheet(self.get_grey_button_style())
        folder_layout.addWidget(self.btn_folder)

        self.btn_open_folder = QtWidgets.QPushButton()
        self.btn_open_folder.setFont(self.font)
        self.btn_open_folder.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        self.btn_open_folder.setStyleSheet(self.get_grey_button_style())
        folder_layout.addWidget(self.btn_open_folder)

        self.layout.addLayout(folder_layout)

        # Quality & Format & Resolution & Playlist options + extra checkboxes
        options_layout = QtWidgets.QHBoxLayout()
        self.lbl_quality = QtWidgets.QLabel()
        self.lbl_quality.setFont(self.font)
        options_layout.addWidget(self.lbl_quality)

        self.quality_combo = QtWidgets.QComboBox()
        # keep previous quality choices but add more presets if you'd like
        self.quality_combo.addItems(["128", "192", "256", "320"])
        self.quality_combo.setCurrentText(str(self.settings.get("quality", "192")))
        self.quality_combo.setFont(self.font)
        options_layout.addWidget(self.quality_combo)

        self.lbl_format = QtWidgets.QLabel()
        self.lbl_format.setFont(self.font)
        options_layout.addWidget(self.lbl_format)

        self.format_combo = QtWidgets.QComboBox()
        # more audio options added + mp4 and best-audio
        self.format_combo.addItems(["MP3", "M4A", "AAC", "OPUS", "WAV", "FLAC", "ALAC", "OGG", "MP4", "Best audio (no convert)"])
        self.format_combo.setCurrentText(self.settings.get("format", "MP3"))
        self.format_combo.setFont(self.font)
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        options_layout.addWidget(self.format_combo)

        # Resolution selector (only relevant for MP4)
        self.lbl_resolution = QtWidgets.QLabel()
        self.lbl_resolution.setFont(self.font)
        self.lbl_resolution.setText("Resolution:")
        options_layout.addWidget(self.lbl_resolution)
        self.resolution_combo = QtWidgets.QComboBox()
        self.resolution_combo.addItems(["Auto (best)", "360p", "480p", "720p", "1080p", "1440p", "2160p (4K)"])
        self.resolution_combo.setCurrentText(self.settings.get("resolution", "Auto (best)"))
        self.resolution_combo.setFont(self.font)
        options_layout.addWidget(self.resolution_combo)

        self.playlist_checkbox = QtWidgets.QCheckBox()
        self.playlist_checkbox.setFont(self.font)
        self.playlist_checkbox.setChecked(self.settings.get("playlist", False))
        options_layout.addWidget(self.playlist_checkbox)

        self.embed_metadata_cb = QtWidgets.QCheckBox("Embed metadata")
        self.embed_metadata_cb.setChecked(self.settings.get("embed_metadata", True))
        options_layout.addWidget(self.embed_metadata_cb)

        self.save_thumbnail_cb = QtWidgets.QCheckBox("Save thumbnail file")
        self.save_thumbnail_cb.setChecked(self.settings.get("save_thumbnail", True))
        options_layout.addWidget(self.save_thumbnail_cb)

        self.auto_open_cb = QtWidgets.QCheckBox("Auto-open folder")
        self.auto_open_cb.setChecked(self.settings.get("auto_open", False))
        options_layout.addWidget(self.auto_open_cb)

        options_layout.addStretch()
        self.layout.addLayout(options_layout)

        # Hide resolution combo if format not MP4 initially
        self.on_format_changed(self.format_combo.currentIndex())

        # Status label
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Bold))
        self.layout.addWidget(self.status_label)

        # Start/Stop buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton()
        self.btn_start.setFont(self.font)
        self.btn_start.clicked.connect(self.start_download)
        self.btn_start.setStyleSheet(self.get_grey_button_style())
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QtWidgets.QPushButton()
        self.btn_stop.setFont(self.font)
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(self.get_grey_button_style(stop=True))
        btn_layout.addWidget(self.btn_stop)

        self.layout.addLayout(btn_layout)

        # Log output window
        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Consolas", 10))
        self.layout.addWidget(self.log_output)

        self.set_texts()

    def set_texts(self):
        # try to get texts for current language, fall back safely if keys missing
        try:
            t = texts[self.lang]
        except Exception:
            t = {}
        # helper
        def g(k, fallback):
            try:
                return t[k]
            except Exception:
                return fallback

        self.setWindowTitle(g('window_title', 'YouTube Downloader'))
        self.lbl_url.setText(g('youtube_link', 'YouTube Link'))
        self.url_input.setPlaceholderText(g('url_placeholder', 'Paste YouTube URL here...'))
        self.url_input.setToolTip(g('url_tooltip', 'Enter video or playlist URL'))
        self.folder_path.setToolTip(g('folder_tooltip', 'Output folder for files'))
        # folder buttons
        self.btn_folder.setText(g('btn_folder', 'Browse'))
        self.btn_folder.setToolTip(g('btn_folder_tooltip', 'Select output folder'))
        self.btn_open_folder.setText(g('btn_open_folder', 'Open'))
        self.btn_open_folder.setToolTip(g('btn_open_folder_tooltip', 'Open output folder'))
        # quality / format
        self.lbl_quality.setText(g('mp3_quality', 'Quality (kbps)'))
        self.lbl_quality.setToolTip(g('quality_tooltip', 'Audio bitrate'))
        self.quality_combo.setToolTip(g('quality_combo_tooltip', 'Choose bitrate quality'))
        self.lbl_format.setText(g('format_label', 'Format:'))
        self.format_combo.setToolTip(g('format_combo_tooltip', 'Choose output format'))
        # resolution
        self.lbl_resolution.setToolTip("Choose video resolution (only used for MP4).")
        self.resolution_combo.setToolTip("Select desired MP4 resolution; if not available yt-dlp will pick nearest.")
        # playlist
        self.playlist_checkbox.setText(g('playlist_checkbox', 'Download playlist'))
        self.playlist_checkbox.setToolTip(g('playlist_tooltip', 'Download playlist if URL points to one'))
        # extra options
        self.embed_metadata_cb.setText(g('embed_metadata', 'Embed metadata'))
        self.embed_metadata_cb.setToolTip(g('embed_metadata_tooltip', 'Write metadata into the audio file'))
        self.save_thumbnail_cb.setText(g('save_thumbnail', 'Save thumbnail file'))
        self.save_thumbnail_cb.setToolTip(g('save_thumbnail_tooltip', 'Save/embed video thumbnail'))
        self.auto_open_cb.setText(g('auto_open', 'Auto-open folder'))
        self.auto_open_cb.setToolTip(g('auto_open_tooltip', 'Open output folder when finished'))
        # buttons
        self.btn_start.setText(g('btn_start', 'Start Download'))
        self.btn_start.setToolTip(g('btn_start_tooltip', 'Start downloading'))
        self.btn_stop.setText(g('btn_stop', 'Stop'))
        self.btn_stop.setToolTip(g('btn_stop_tooltip', 'Stop current download'))
        # labels
        self.lang_label.setText(g('language_label', 'Language:'))

    def change_language(self, index):
        self.lang = 'en' if index == 0 else 'de'
        self.set_texts()
        self.settings['lang'] = self.lang
        save_settings(self.settings)

    def on_format_changed(self, index):
        # Show/hide resolution controls depending on format
        fmt = self.format_combo.currentText().lower()
        is_mp4 = (fmt == 'mp4')
        self.lbl_resolution.setVisible(is_mp4)
        self.resolution_combo.setVisible(is_mp4)

    def get_grey_button_style(self, stop=False):
        return f"""
            QPushButton {{
                background-color: {'#555555' if stop else '#888888'};
                color: white; font-weight: bold;
                padding: 8px 14px; border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {'#777777' if stop else '#aaaaaa'};
            }}
            QPushButton:pressed {{
                background-color: {'#444444' if stop else '#666666'};
            }}
        """

    def select_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select target folder", os.getcwd())
        if folder:
            self.folder_path.setText(folder)
            self.settings['last_folder'] = folder
            save_settings(self.settings)

    def open_output_folder(self):
        folder = self.folder_path.text()
        if not os.path.isdir(folder):
            QtWidgets.QMessageBox.warning(self, "Folder not found", "The selected folder does not exist.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Could not open folder", f"Failed to open folder: {e}")

    def log(self, msg):
        clean_msg = strip_ansi_codes(msg)
        now = time.strftime("%H:%M:%S")
        self.log_output.append(f"[{now}] {clean_msg}")
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def on_progress(self, msg):
        self.log(msg)
        clean = strip_ansi_codes(msg)
        if clean.startswith("Title loaded:"):
            title = clean.replace("Title loaded:", "").strip()
            self.status_label.setText(f"🎵 {title}")
        elif clean.startswith("Downloading:") or "|" in clean or "ETA" in clean:
            self.status_label.setText(clean)
        elif clean.lower().startswith("finished") or clean.lower().startswith("download finished") or "saved as" in clean.lower():
            self.status_label.setText("🔄 Converting...")
        else:
            short = (clean[:120] + '...') if len(clean) > 120 else clean
            self.status_label.setText(short)

    def start_download(self, from_queue: bool = False):
        url = self.url_input.text().strip()
        if not url:
            QtWidgets.QMessageBox.warning(self, "Missing Link", "Please enter a valid URL!")
            return
        out_folder = self.folder_path.text()
        if not os.path.isdir(out_folder):
            QtWidgets.QMessageBox.warning(self, "Invalid Folder", "Please choose an existing output folder.")
            return
        try:
            quality = int(self.quality_combo.currentText())
        except Exception:
            quality = 192
        playlist = self.playlist_checkbox.isChecked()
        format_choice = self.format_combo.currentText().lower()
        embed_metadata = self.embed_metadata_cb.isChecked()
        save_thumbnail = self.save_thumbnail_cb.isChecked()
        auto_open = self.auto_open_cb.isChecked()
        resolution_label = self.resolution_combo.currentText()

        # Save settings
        self.settings['last_folder'] = out_folder
        self.settings['quality'] = quality
        self.settings['playlist'] = playlist
        self.settings['format'] = self.format_combo.currentText()
        self.settings['embed_metadata'] = embed_metadata
        self.settings['save_thumbnail'] = save_thumbnail
        self.settings['auto_open'] = auto_open
        self.settings['resolution'] = resolution_label
        save_settings(self.settings)

        # determine if ffmpeg is needed:
        need_ffmpeg = format_choice in ['mp3', 'm4a', 'opus', 'wav', 'aac', 'flac', 'alac', 'ogg'] or embed_metadata or save_thumbnail or format_choice == 'mp4'
        if need_ffmpeg and not self.ffmpeg:
            QtWidgets.QMessageBox.critical(self, "FFmpeg missing", "FFmpeg not found. Install ffmpeg and add it to PATH for conversion and embedding.")
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText("🔄 Preparing download...")
        self.log_output.clear()

        self.log(f"URL: {url}")
        self.log(f"Saving to: {out_folder}")
        self.log(f"Quality: {quality} kbps")
        self.log(f"Playlist mode: {'On' if playlist else 'Off'}")
        self.log(f"Format: {format_choice.upper()}")
        if format_choice == 'mp4':
            self.log(f"Resolution: {resolution_label}")
        self.log(f"Embed metadata: {'Yes' if embed_metadata else 'No'}")
        self.log(f"Save thumbnail: {'Yes' if save_thumbnail else 'No'}")

        # Ensure output folder exists
        utils.ensure_dir(out_folder)

        # create worker
        self.worker = BrejaxWorker(url, out_folder, quality, playlist,
                                   format_type=format_choice,
                                   embed_metadata=embed_metadata,
                                   save_thumbnail=save_thumbnail,
                                   resolution_label=resolution_label,
                                   ffmpeg_path=self.ffmpeg)
        self.worker_thread = QtCore.QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.download_finished)
        self.worker.error.connect(self.download_error)
        self.worker_thread.start()

    def stop_download(self):
        if self.worker:
            self.worker.stop()
            self.log("Download stopped by user.")
            self.status_label.setText("⏹️ Stopped.")
            self.btn_stop.setEnabled(False)
            self.btn_start.setEnabled(True)
            if self.worker_thread:
                self.worker_thread.quit()
                try:
                    self.worker_thread.wait(timeout=2000)
                except Exception:
                    pass
                self.worker_thread = None
                self.worker = None

    def download_finished(self):
        self.log("✅ All done!")
        self.status_label.setText("✅ Done!")
        try:
            QtWidgets.QMessageBox.information(self, "Complete", "Saved successfully.")
        except Exception:
            pass

        # Auto-open folder if enabled
        if self.settings.get("auto_open", False):
            try:
                folder = self.folder_path.text()
                if platform.system() == "Windows":
                    os.startfile(folder)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
            except Exception:
                pass

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.worker_thread:
            try:
                self.worker_thread.quit()
                self.worker_thread.wait()
            except Exception:
                pass
        self.worker_thread = None
        self.worker = None

    def download_error(self, err_msg):
        self.log(f"❌ Error: {err_msg}")
        self.status_label.setText("❌ Error occurred.")
        try:
            QtWidgets.QMessageBox.critical(self, "Error", err_msg)
        except Exception:
            pass
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.worker_thread:
            try:
                self.worker_thread.quit()
                self.worker_thread.wait()
            except Exception:
                pass
        self.worker_thread = None
        self.worker = None

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.ToolTip:
            QtWidgets.QToolTip.showText(event.globalPos(), obj.toolTip(), obj)
            return True
        return super().eventFilter(obj, event)

# Entry point
def brejax_main():
    app = QtWidgets.QApplication(sys.argv)
    try:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt6())
    except Exception:
        pass
    window = BrejaxDownloaderUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    brejax_main()
