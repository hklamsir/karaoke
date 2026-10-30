@echo off
rem Karaoke 字幕編輯器 UI 啟動器（ADR-014 R24：UI 必須用 .venu-ui，唔係 .venv）
if not exist "%~dp0.venv-ui\Scripts\python.exe" (
    echo [run_ui] 首次使用：建立 .venv-ui 並安裝依賴…
    py -3 -m venv "%~dp0.venv-ui"
    "%~dp0.venv-ui\Scripts\python.exe" -m pip install -r "%~dp0requirements-ui.txt"
)
"%~dp0.venv-ui\Scripts\python.exe" -m ui %*
