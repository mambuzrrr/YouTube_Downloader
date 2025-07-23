### 🔧 FFmpeg Installation Script (Windows)

You can use this batch script to automatically download, extract, and add FFmpeg to your system `PATH`.  
Make sure [7-Zip](https://www.7-zip.org/) is installed at `C:\Program Files\7-Zip\7z.exe`, or adjust the path in the script.

```batch
@echo off
SETLOCAL ENABLEDELAYEDEXPANSION

echo ========================================
echo     FFmpeg Downloader + PATH Setup
echo ========================================

:: Target folder
set "DEST=C:\ffmpeg"
set "ZIPFILE=ffmpeg-latest-full.7z"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z"

:: Check if 7z.exe exists
set "SEVENZIP=C:\Program Files\7-Zip\7z.exe"
if not exist "%SEVENZIP%" (
    echo [!] 7z.exe not found at: %SEVENZIP%
    echo [!] Please install 7-Zip or adjust the path in the script.
    pause
    exit /b
)

:: Create target folder
if not exist %DEST% (
    mkdir %DEST%
)

cd /d %DEST%

echo [*] Downloading FFmpeg...
curl -L -o %ZIPFILE% %FFMPEG_URL%

echo [*] Extracting FFmpeg...
"%SEVENZIP%" x %ZIPFILE% -o%DEST% -y >nul

:: Find extracted folder (ffmpeg-*)
for /d %%i in (%DEST%\ffmpeg-*) do (
    set "FFDIR=%%i"
    goto :found
)

:found
echo [*] FFmpeg extracted to: !FFDIR!

:: Add bin to PATH
set "NEWPATH=!FFDIR!\bin"
echo [*] Adding !NEWPATH! to system PATH...

:: Get current PATH
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do (
    set "OLDPATH=%%B"
)

:: Append if not already in PATH
echo !OLDPATH! | find /I "!NEWPATH!" >nul
if errorlevel 1 (
    setx PATH "!OLDPATH!;!NEWPATH!" /M
    echo [✓] FFmpeg added to system PATH.
) else (
    echo [i] FFmpeg path is already in PATH.
)

echo.
echo [✓] Done! Please restart your console or PC for changes to take effect.
pause
```

:: 💡 Tip: You can run this script as administrator by saving it as `install_ffmpeg.bat`, right-clicking, and selecting **Run as Administrator**.
