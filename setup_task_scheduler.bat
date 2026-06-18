@echo off
:: ============================================================
:: InvestmentAgent タスクスケジューラ一括登録スクリプト (v6 命名)
::
:: 登録タスク一覧:
::  06:00(毎日)   InvestAgent_MorningReport     morning_report    リトライ1回
::  08:00(毎日)   InvestAgent_IntelScout_AM     intel_scout       リトライ1回
::  09:05(月〜金) InvestAgent_ScalpDay_JP       scalpday_jp       リトライなし
::  09:15(月〜金) InvestAgent_MomentSwing_JP    moment_swing_jp   リトライなし
::  10:30(月〜金) InvestAgent_FXRebalance_JP    fx_rebalance      リトライなし
::  17:00(毎日)   InvestAgent_IntelScout_PM     intel_scout       リトライ1回
::  21:00(毎日)   InvestAgent_EveningReport     evening_report    リトライ1回
::  00:25(火〜土) InvestAgent_FXRebalance_US    fx_rebalance      リトライなし
::  00:30(火〜土) InvestAgent_MomentSwing_US    moment_swing_us   リトライなし
::  00:35(火〜土) InvestAgent_ScalpDay_US       scalpday_us       リトライなし
::
:: 削除タスク (旧命名):
::  InvestAgent_Intelligence  (intel_scout に移行済み)
::  InvestAgent_Daytrade      (→ InvestAgent_ScalpDay_JP に改名)
::  InvestAgent_JPSwing       (→ InvestAgent_MomentSwing_JP に改名)
::  InvestAgent_USValue       (→ InvestAgent_MomentSwing_US に改名)
::  InvestAgent_USPaper       (→ InvestAgent_ScalpDay_US に改名)
::  InvestAgent_FXRebalance   (→ InvestAgent_FXRebalance_JP に改名)
::
:: US セッション 00:25〜00:35 JST(翌日/火〜土)の根拠:
::   00:30 JST = UTC前日 15:30
::   EDT 夏時間(UTC-4): 11:30 ET  開場(09:30)から2h後、終了(16:00)まで4.5h残
::   EST 冬時間(UTC-5): 10:30 ET  開場(09:30)から1h後、終了(16:00)まで5.5h残
::   → 夏(22:30 JST開場)・冬(23:30 JST開場) どちらでも確実に開場後
::   → 火〜土 JST = 月〜金 ET (US 取引日に対応)
::
:: JP セッション FXRebalance_JP 10:30 JST の根拠:
::   JP 午前セッション中(09:00-11:30)に起動 → is_jp_open() == True が保証
::
:: 実行方法: 管理者権限のコマンドプロンプトで実行
:: ============================================================

setlocal EnableDelayedExpansion
set WDIR=%~dp0

echo.
echo ============================================================
echo  InvestmentAgent タスクスケジューラ登録 (v6 命名)
echo ============================================================

:: ────────────────────────────────────────────────
:: 旧タスクを削除（存在しない場合はスキップ）
:: ────────────────────────────────────────────────
echo.
echo [削除] 旧タスク群 (旧命名 → 新命名に移行)...
for %%T in (
    InvestAgent_Intelligence
    InvestAgent_Daytrade
    InvestAgent_JPSwing
    InvestAgent_USValue
    InvestAgent_USPaper
    InvestAgent_FXRebalance
) do (
    schtasks /delete /tn "%%T" /f > nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo   削除済み: %%T
    ) else (
        echo   未存在: %%T (スキップ)
    )
)

:: ────────────────────────────────────────────────
:: 1. 朝次レポート  06:00 (毎日) -- リトライ1回
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_MorningReport (06:00 毎日)...
schtasks /create /tn "InvestAgent_MorningReport" ^
  /tr "\"%WDIR%run_morning_report.bat\"" ^
  /sc daily /st 06:00 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 2. IntelScout AM窓  08:00 (毎日) -- リトライ1回
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_IntelScout_AM (08:00 毎日)...
schtasks /create /tn "InvestAgent_IntelScout_AM" ^
  /tr "\"%WDIR%run_intel_scout.bat\"" ^
  /sc daily /st 08:00 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 3. ScalpDay_JP  09:05 (月〜金) -- リトライなし
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_ScalpDay_JP (09:05 月〜金)...
schtasks /create /tn "InvestAgent_ScalpDay_JP" ^
  /tr "\"%WDIR%run_scalpday_jp.bat\"" ^
  /sc weekly /d MON,TUE,WED,THU,FRI /st 09:05 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 4. MomentSwing_JP  09:15 (月〜金) -- リトライなし
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_MomentSwing_JP (09:15 月〜金)...
schtasks /create /tn "InvestAgent_MomentSwing_JP" ^
  /tr "\"%WDIR%run_moment_swing_jp.bat\"" ^
  /sc weekly /d MON,TUE,WED,THU,FRI /st 09:15 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 5. FXRebalance_JP  10:30 (月〜金) -- リトライなし
::    JP 午前セッション中(09:00-11:30)に起動 → is_jp_open() == True が保証
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_FXRebalance_JP (10:30 月〜金)...
schtasks /create /tn "InvestAgent_FXRebalance_JP" ^
  /tr "\"%WDIR%run_fx_rebalance.bat\"" ^
  /sc weekly /d MON,TUE,WED,THU,FRI /st 10:30 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 6. IntelScout PM窓  17:00 (毎日) -- リトライ1回
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_IntelScout_PM (17:00 毎日)...
schtasks /create /tn "InvestAgent_IntelScout_PM" ^
  /tr "\"%WDIR%run_intel_scout.bat\"" ^
  /sc daily /st 17:00 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 7. 夜間レポート  21:00 (毎日) -- リトライ1回
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_EveningReport (21:00 毎日)...
schtasks /create /tn "InvestAgent_EveningReport" ^
  /tr "\"%WDIR%run_evening_report.bat\"" ^
  /sc daily /st 21:00 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 8. FXRebalance_US  00:25 (火〜土) -- リトライなし
::    00:25 JST = UTC前日 15:25 = EDT 11:25 ET / EST 10:25 ET
::    → MomentSwing_US / ScalpDay_US の 5分前に通貨ポジション確定
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_FXRebalance_US (00:25 火〜土)...
schtasks /create /tn "InvestAgent_FXRebalance_US" ^
  /tr "\"%WDIR%run_fx_rebalance.bat\"" ^
  /sc weekly /d TUE,WED,THU,FRI,SAT /st 00:25 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 9. MomentSwing_US  00:30 (火〜土) -- リトライなし
::    00:30 JST = UTC前日 15:30 = EDT 11:30 ET / EST 10:30 ET
::    → 夏冬ともに開場後かつ終了 4〜5.5h 前
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_MomentSwing_US (00:30 火〜土)...
schtasks /create /tn "InvestAgent_MomentSwing_US" ^
  /tr "\"%WDIR%run_moment_swing_us.bat\"" ^
  /sc weekly /d TUE,WED,THU,FRI,SAT /st 00:30 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 10. ScalpDay_US  00:35 (火〜土) -- リトライなし
:: ────────────────────────────────────────────────
echo.
echo [登録] InvestAgent_ScalpDay_US (00:35 火〜土)...
schtasks /create /tn "InvestAgent_ScalpDay_US" ^
  /tr "\"%WDIR%run_scalpday_us.bat\"" ^
  /sc weekly /d TUE,WED,THU,FRI,SAT /st 00:35 ^
  /ru "%USERNAME%" /f
if %ERRORLEVEL% EQU 0 (echo   OK) else (echo   [ERROR] 登録失敗)

:: ────────────────────────────────────────────────
:: 登録結果確認
:: ────────────────────────────────────────────────
echo.
echo ============================================================
echo  登録済みタスク一覧 (InvestAgent_*)
echo ============================================================
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_MorningReport"    2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_IntelScout_AM"    2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_ScalpDay_JP"      2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_MomentSwing_JP"   2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_FXRebalance_JP"   2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_IntelScout_PM"    2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_EveningReport"    2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_FXRebalance_US"   2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_MomentSwing_US"   2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"
schtasks /query /fo LIST /v ^
  /tn "InvestAgent_ScalpDay_US"      2>nul | findstr /i "タスク名 次回実行時刻 状態 TaskName Next Run Status"

echo.
echo ============================================================
echo  完了。タスクスケジューラで確認: taskschd.msc
echo ============================================================
endlocal
