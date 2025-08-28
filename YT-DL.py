import sys
import os
import re
import shutil
import subprocess
import platform
from PyQt6 import QtWidgets, QtCore, QtGui
import yt_dlp
import qdarkstyle
from language import texts

# Remove __pycache__ on startup if it exists
if os.path.exists('__pycache__'):
    shutil.rmtree('__pycache__', ignore_errors=True)

# Remove ANSI codes from yt-dlp logs
def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

# Background worker that handles the actual download and conversion
class BrejaxWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)

    def __init__(self, url, out_folder, quality, playlist, format_type='mp3'):
        super().__init__()
        self.url = url
        self.out_folder = out_folder
        self.quality = quality
        self.playlist = playlist
        # format_type: 'mp3' or 'mp4' (mp3 -> extract audio, mp4 -> download+merge video+audio)
        self.format_type = format_type
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        if self.format_type == 'mp4':
            options = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': os.path.join(self.out_folder, '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'quiet': True,
                'noplaylist': not self.playlist,
                'progress_hooks': [self.progress_hook]
            }
        else:
            options = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(self.out_folder, '%(title)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': str(self.quality),
                }],
                'quiet': True,
                'noplaylist': not self.playlist,
                'progress_hooks': [self.progress_hook]
            }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                try:
                    info = ydl.extract_info(self.url, download=False)
                    if info:
                        if info.get('entries'):
                            entries = [e for e in info.get('entries') if e is not None]
                            pname = info.get('title', 'Playlist')
                            self.progress.emit(f"Title loaded: Playlist: {pname} ({len(entries)} items)")
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

    def progress_hook(self, d):
        if not self._is_running:
            raise yt_dlp.utils.DownloadError("Download stopped by user")

        status = d.get('status')
        info = d.get('info_dict') or d.get('filename') or {}
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

class BrejaxDownloaderUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.lang = 'en'
        self.resize(700, 460)
        self.worker_thread = None
        self.worker = None
        self.init_ui()

    def init_ui(self):
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.font = QtGui.QFont("Segoe UI", 11)

        lang_layout = QtWidgets.QHBoxLayout()
        lang_layout.addStretch()

        self.lang_label = QtWidgets.QLabel()
        self.lang_label.setFont(self.font)
        lang_layout.addWidget(self.lang_label)

        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItems(["English", "Deutsch"])
        self.lang_combo.setCurrentIndex(0)
        self.lang_combo.setFont(self.font)
        self.lang_combo.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.lang_combo.currentIndexChanged.connect(self.change_language)
        lang_layout.addWidget(self.lang_combo)

        self.layout.addLayout(lang_layout)

        self.lbl_url = QtWidgets.QLabel()
        self.lbl_url.setFont(self.font)
        self.layout.addWidget(self.lbl_url)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setFont(self.font)
        self.url_input.installEventFilter(self)
        self.layout.addWidget(self.url_input)

        folder_layout = QtWidgets.QHBoxLayout()
        self.folder_path = QtWidgets.QLineEdit(os.getcwd())
        self.folder_path.setReadOnly(True)
        self.folder_path.setFont(self.font)
        self.folder_path.installEventFilter(self)
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

        options_layout = QtWidgets.QHBoxLayout()

        self.lbl_quality = QtWidgets.QLabel()
        self.lbl_quality.setFont(self.font)
        options_layout.addWidget(self.lbl_quality)

        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItems(["128", "192", "256", "320"])
        self.quality_combo.setCurrentText("192")
        self.quality_combo.setFont(self.font)
        options_layout.addWidget(self.quality_combo)

        self.lbl_format = QtWidgets.QLabel()
        self.lbl_format.setFont(self.font)
        options_layout.addWidget(self.lbl_format)

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["MP3", "MP4"])
        self.format_combo.setCurrentText("MP3")
        self.format_combo.setFont(self.font)
        options_layout.addWidget(self.format_combo)

        self.playlist_checkbox = QtWidgets.QCheckBox()
        self.playlist_checkbox.setFont(self.font)
        options_layout.addWidget(self.playlist_checkbox)

        options_layout.addStretch()
        self.layout.addLayout(options_layout)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Bold))
        self.layout.addWidget(self.status_label)

        btn_layout = QtWidgets.QHBoxLayout()

        self.btn_start = QtWidgets.QPushButton()
        self.btn_start.setFont(self.font)
        self.btn_start.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_start.clicked.connect(self.start_download)
        self.btn_start.setStyleSheet(self.get_grey_button_style())
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QtWidgets.QPushButton()
        self.btn_stop.setFont(self.font)
        self.btn_stop.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(self.get_grey_button_style(stop=True))
        btn_layout.addWidget(self.btn_stop)

        self.layout.addLayout(btn_layout)

        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Consolas", 10))
        self.layout.addWidget(self.log_output)

        self.set_texts()

    def set_texts(self):
        try:
            t = texts[self.lang]
        except Exception:
            t = {}
        def g(k, fallback):
            try:
                return t[k]
            except Exception:
                return fallback

        self.setWindowTitle(g('window_title', 'YouTube MP3 Downloader'))
        self.lbl_url.setText(g('youtube_link', 'YouTube Link'))
        self.url_input.setPlaceholderText(g('url_placeholder', 'Paste YouTube URL here...'))
        self.url_input.setToolTip(g('url_tooltip', 'Enter video or playlist URL'))
        self.folder_path.setToolTip(g('folder_tooltip', 'Output folder for MP3s'))
        self.btn_folder.setText(g('btn_folder', 'Browse'))
        self.btn_folder.setToolTip(g('btn_folder_tooltip', 'Select output folder'))
        self.btn_open_folder.setText(g('btn_open_folder', 'Open'))
        self.btn_open_folder.setToolTip(g('btn_open_folder_tooltip', 'Open output folder'))
        self.lbl_quality.setText(g('mp3_quality', 'MP3 Quality (kbps)'))
        self.lbl_quality.setToolTip(g('quality_tooltip', 'Select MP3 bitrate'))
        self.quality_combo.setToolTip(g('quality_combo_tooltip', 'Choose bitrate quality'))
        self.playlist_checkbox.setText(g('playlist_checkbox', 'Download playlist'))
        self.playlist_checkbox.setToolTip(g('playlist_tooltip', 'Download a playlist if URL points to one'))
        self.btn_start.setText(g('btn_start', 'Start Download'))
        self.btn_start.setToolTip(g('btn_start_tooltip', 'Start downloading'))
        self.btn_stop.setText(g('btn_stop', 'Stop'))
        self.btn_stop.setToolTip(g('btn_stop_tooltip', 'Stop current download'))
        self.lang_label.setText(g('language_label', 'Language:'))
        self.lbl_format.setText(g('format_label', 'Format:'))

    def change_language(self, index):
        self.lang = 'en' if index == 0 else 'de'
        self.set_texts()

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
        self.log_output.append(clean_msg)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def on_progress(self, msg):
        self.log(msg)
        clean = strip_ansi_codes(msg)
        if clean.startswith("Title loaded:"):
            title = clean.replace("Title loaded:", "").strip()
            self.status_label.setText(f"🎵 {title}")
        elif clean.startswith("Downloading:") or "|" in clean or "ETA" in clean:
            self.status_label.setText(clean)
        elif clean.lower().startswith("finished") or clean.lower().startswith("download finished"):
            self.status_label.setText("🔄 Converting...")
        else:
            short = (clean[:120] + '...') if len(clean) > 120 else clean
            self.status_label.setText(short)

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QtWidgets.QMessageBox.warning(self, "Missing Link", "Please enter a valid YouTube URL!")
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

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText("🔄 Preparing download...")
        self.log_output.clear()

        self.log(f"URL: {url}")
        self.log(f"Saving to: {out_folder}")
        self.log(f"Quality: {quality} kbps")
        self.log(f"Playlist mode: {'On' if playlist else 'Off'}")
        self.log(f"Format: {format_choice.upper()}")

        # start worker in separate QThread to avoid blocking UI
        self.worker = BrejaxWorker(url, out_folder, quality, playlist, format_type=format_choice)
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
