import re
import os
import shutil
import platform
from typing import Optional, List, Dict

def sanitize_filename(name: str, replace_with: str = "_") -> str:
    """Remove filesystem-problematic characters and trim length."""
    if not isinstance(name, str):
        name = str(name)
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*\u0000]', replace_with, name)
    name = re.sub(r'\s+', ' ', name).strip()
    max_len = 200
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    if name == "":
        name = "unnamed"
    return name

def unique_path(path: str) -> str:
    """If path exists, append " (1)", " (2)", ... before extension."""
    base, ext = os.path.splitext(path)
    counter = 1
    new = path
    while os.path.exists(new):
        new = f"{base} ({counter}){ext}"
        counter += 1
    return new

def find_ffmpeg() -> Optional[str]:
    """Try to find ffmpeg executable in PATH or some common locations."""
    try:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
    except Exception:
        pass
    candidates = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None

def bytes_free(path: str) -> int:
    """Return free bytes on the filesystem containing path."""
    try:
        if not os.path.exists(path):
            path = os.path.dirname(path) or "."
        usage = shutil.disk_usage(path)
        return usage.free
    except Exception:
        return 0

def format_bytes(num: int) -> str:
    """Pretty print bytes (e.g., 123456 -> '120.56 KB')."""
    try:
        num = float(num)
    except Exception:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024.0:
            return f"{num:3.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} PB"

def ensure_dir(path: str) -> bool:
    """Ensure a directory exists; return True on success."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False

def is_url(text: str) -> bool:
    """Simple heuristic if string looks like an URL."""
    if not isinstance(text, str):
        return False
    return text.startswith("http://") or text.startswith("https://")

def estimate_total_size_from_entries(entries: List[Dict]) -> int:
    """Sum filesize/filesize_approx from a playlist entries list (if available)."""
    total = 0
    for e in entries:
        try:
            fs = e.get('filesize') or e.get('filesize_approx') or 0
            if fs:
                total += int(fs)
        except Exception:
            continue
    return total

def get_video_format(res_label: str) -> str:
    label = (res_label or "").strip().lower()
    # mapping to safe <= patterns so yt-dlp falls back gracefully
    mapping = {
        "auto (best)": "bestvideo+bestaudio/best",
        "360p": "bv[height<=360]+ba/best",
        "480p": "bv[height<=480]+ba/best",
        "720p": "bv[height<=720]+ba/best",
        "1080p": "bv[height<=1080]+ba/best",
        "1440p": "bv[height<=1440]+ba/best",
        "2160p (4k)": "bv[height<=2160]+ba/best",
    }
    # normalize keys
    for k, v in mapping.items():
        if k == label:
            return v
    # fallback to best
    return "bestvideo+bestaudio/best"
