$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m PyInstaller --noconfirm --clean --windowed --onedir --name MediaToolbox --add-binary "ffmpeg/ffmpeg.exe;ffmpeg" --add-binary "ffmpeg/ffprobe.exe;ffmpeg" main.py
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
