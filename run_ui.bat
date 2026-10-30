@echo off
rem Karaoke UI launcher (ADR-014 R24: UI needs .venv-ui, NOT .venv)
rem NOTE: keep this file ASCII-only - cmd.exe mis-reads UTF-8 Chinese as GBK
if not exist "%~dp0.venv-ui\Scripts\python.exe" (
    echo [run_ui] First run: creating .venv-ui and installing dependencies...
    py -3 -m venv "%~dp0.venv-ui"
    "%~dp0.venv-ui\Scripts\python.exe" -m pip install -r "%~dp0requirements-ui.txt"
    rem CLI deps too: the UI imports main (dashscope for key test, opencc, etc.)
    "%~dp0.venv-ui\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
)
"%~dp0.venv-ui\Scripts\python.exe" -m ui %*
