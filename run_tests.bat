@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem run_tests.bat - one-click test runner (auto acceptance gate)
rem
rem 用法：
rem   run_tests.bat                 全部測試（含 slow）
rem   run_tests.bat -m "not slow"   跳過大數據量測試
rem   run_tests.bat -k cancel       只跑取消相關
rem
rem 判準：exit code 0 才算通過。
rem   xfail(strict=True) 的 XPASS 會令 exit code = 1 —— 即「功能已實作但
rem   未拆 xfail 標記」同樣算失敗（刻意設計，見 docs/代碼審查報告.html §9.2）
rem ===========================================================================

set "PY=.venv-ui\Scripts\python.exe"

if not exist "%PY%" (
    echo [X] 找不到 .venv-ui 的 Python。請先建立環境：
    echo     python -m venv .venv-ui
    echo     .venv-ui\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ui.txt -r requirements-dev.txt
    exit /b 1
)

"%PY%" -c "import pytest" >nul 2>&1
if errorlevel 1 (
    echo [X] .venv-ui 未安裝 pytest，請執行：
    echo     .venv-ui\Scripts\python.exe -m pip install -r requirements-dev.txt
    exit /b 1
)

echo ============================================================
echo  Karaoke AI Subtitler - 自動驗收
echo ============================================================
echo.

"%PY%" -m pytest tests -v --tb=short %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] 全部通過。xfail 數量 = 待實作功能清單長度。
) else (
    echo [X] 有測試失敗或 XPASS，請看上面的 FAILED / XPASS 段落。
)
exit /b %RC%
