@echo off
chcp 65001 >nul
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo 绋嬪簭寮傚父閫€鍑猴紝閿欒浠ｇ爜锛?errorlevel%
    pause
)
