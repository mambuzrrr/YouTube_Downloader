import sys
import os
import re
import shutil
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

    def __init__(self, url, out_folder, quality, playlist):
        super().__init__()
        self.url = url
        self.out_folder = out_folder
        self.quality = quality
        self.playlist = playlist
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        options = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(self.out_folder, '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': self.quality,
            }],
            'quiet': True,
            'noplaylist': not self.playlist,
            'progress_hooks': [self.progress_hook]
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([self.url])
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def progress_hook(self, d):
        if not self._is_running:
            raise yt_dlp.utils.DownloadError("Download stopped by user")
        if d['status'] == 'downloading':
            pct = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '').strip()
            self.progress.emit(f"Downloading... {pct} @ {speed}")
        elif d['status'] == 'finished':
            self.progress.emit("Download finished, converting...")

class BrejaxDownloaderUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.lang = 'en'
        self.resize(650, 380)
        self.worker_thread = None
        self.worker = None
        self.init_ui()

    def init_ui(self):
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.font = QtGui.QFont("Segoe UI", 11)

        # Language Switcher
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

        # YouTube Link Input
        self.lbl_url = QtWidgets.QLabel()
        self.lbl_url.setFont(self.font)
        self.layout.addWidget(self.lbl_url)

        self.url_input = QtWidgets.QLineEdit()
        self.url_input.setFont(self.font)
        self.url_input.installEventFilter(self)
        self.layout.addWidget(self.url_input)

        # Output folder picker
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

        self.layout.addLayout(folder_layout)

        # Quality & Playlist options
        options_layout = QtWidgets.QHBoxLayout()

        self.lbl_quality = QtWidgets.QLabel()
        self.lbl_quality.setFont(self.font)
        options_layout.addWidget(self.lbl_quality)

        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItems(["128", "192", "256", "320"])
        self.quality_combo.setCurrentText("192")
        self.quality_combo.setFont(self.font)
        options_layout.addWidget(self.quality_combo)

        self.playlist_checkbox = QtWidgets.QCheckBox()
        self.playlist_checkbox.setFont(self.font)
        options_layout.addWidget(self.playlist_checkbox)

        self.layout.addLayout(options_layout)

        # Status
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Bold))
        self.layout.addWidget(self.status_label)

        # Start/Stop buttons
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

        # Log output window
        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Consolas", 10))
        self.layout.addWidget(self.log_output)

        self.set_texts()

    def set_texts(self):
        t = texts[self.lang]
        self.setWindowTitle(t['window_title'])
        self.lbl_url.setText(t['youtube_link'])
        self.url_input.setPlaceholderText(t['url_placeholder'])
        self.url_input.setToolTip(t['url_tooltip'])
        self.folder_path.setToolTip(t['folder_tooltip'])
        self.btn_folder.setText(t['btn_folder'])
        self.btn_folder.setToolTip(t['btn_folder_tooltip'])
        self.lbl_quality.setText(t['mp3_quality'])
        self.lbl_quality.setToolTip(t['quality_tooltip'])
        self.quality_combo.setToolTip(t['quality_combo_tooltip'])
        self.playlist_checkbox.setText(t['playlist_checkbox'])
        self.playlist_checkbox.setToolTip(t['playlist_tooltip'])
        self.btn_start.setText(t['btn_start'])
        self.btn_start.setToolTip(t['btn_start_tooltip'])
        self.btn_stop.setText(t['btn_stop'])
        self.btn_stop.setToolTip(t['btn_stop_tooltip'])
        self.lang_label.setText(t['language_label'])

    def change_language(self, index):
        self.lang = 'en' if index == 0 else 'de'
        self.set_texts()

    def get_grey_button_style(self, stop=False):
        return f"""
            QPushButton {{
                background-color: {'#555555' if stop else '#888888'};
                color: white; font-weight: bold;
                padding: 8px 20px; border-radius: 8px;
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

    def log(self, msg):
        clean_msg = strip_ansi_codes(msg)
        self.log_output.append(clean_msg)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QtWidgets.QMessageBox.warning(self, "Missing Link", "Please enter a valid YouTube URL!")
            return
        out_folder = self.folder_path.text()
        quality = int(self.quality_combo.currentText())
        playlist = self.playlist_checkbox.isChecked()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText("🔄 Downloading...")
        self.log_output.clear()

        self.log(f"URL: {url}")
        self.log(f"Saving to: {out_folder}")
        self.log(f"Quality: {quality} kbps")
        self.log(f"Playlist mode: {'On' if playlist else 'Off'}")

        self.worker = BrejaxWorker(url, out_folder, quality, playlist)
        self.worker_thread = QtCore.QThread()
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.log)
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

    def download_finished(self):
        self.log("✅ All done!")
        self.status_label.setText("✅ Done!")
        QtWidgets.QMessageBox.information(self, "Complete", "MP3 saved successfully.")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.worker_thread.quit()
        self.worker_thread.wait()

    def download_error(self, err_msg):
        self.log(f"❌ Error: {err_msg}")
        self.status_label.setText("❌ Error occurred.")
        QtWidgets.QMessageBox.critical(self, "Error", err_msg)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.worker_thread.quit()
        self.worker_thread.wait()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.ToolTip:
            QtWidgets.QToolTip.showText(event.globalPos(), obj.toolTip(), obj)
            return True
        return super().eventFilter(obj, event)

# Entry point
def brejax_main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt6())
    window = BrejaxDownloaderUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    brejax_main()
