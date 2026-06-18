@echo off
setlocal
cd /d %~dp0
if not exist logs mkdir logs

set MODE=fx_rebalance
set LOG=logs\%MODE%_runner.log
set PYTHON=python

echo [%date% %time%] ========== FXRebalance 起動 ========== >> "%LOG%"
%PYTHON% main.py --mode %MODE% >> "%LOG%" 2>&1
set EXIT=%ERRORLEVEL%

if %EXIT% EQU 0 (
    echo [%date% %time%] 完了 (exit=0) >> "%LOG%"
) else (
    echo [%date% %time%] 異常終了 (exit=%EXIT%) -- 発注系: リトライ不可、要手動確認 >> "%LOG%"
)
endlocal
