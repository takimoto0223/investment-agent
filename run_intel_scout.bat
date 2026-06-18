@echo off
setlocal
cd /d %~dp0
if not exist logs mkdir logs

set MODE=intel_scout
set LOG=logs\%MODE%_runner.log
set PYTHON=python

echo [%date% %time%] ========== IntelScout 起動 ========== >> "%LOG%"
%PYTHON% main.py --mode %MODE% >> "%LOG%" 2>&1
set EXIT=%ERRORLEVEL%

if %EXIT% EQU 0 (
    echo [%date% %time%] 完了 (exit=0) >> "%LOG%"
    goto :EOF
)

echo [%date% %time%] 失敗 (exit=%EXIT%), 5分後にリトライ >> "%LOG%"
timeout /t 300 /nobreak > nul

%PYTHON% main.py --mode %MODE% >> "%LOG%" 2>&1
set EXIT=%ERRORLEVEL%
if %EXIT% EQU 0 (
    echo [%date% %time%] リトライ完了 >> "%LOG%"
) else (
    echo [%date% %time%] リトライも失敗 (exit=%EXIT%) >> "%LOG%"
)
endlocal
