# リファクタ積み残し仕様

## 別PRで対応: report/template.py のデータクラス名改名

v6 命名一気通貫（2026-06-19 実施）では `report/template.py` 内のデータクラス名は
HTML レポート生成コードへの連鎖が大きいためスコープ外とした。次の PR で対応すること。

| 現在の名前 | 改名先候補 | 使用ファイル |
|---|---|---|
| `DaytradeCandidate` | `ScalpDayJP_Candidate` | template.py, cxo.py, test_cxo_report.py, main.py |
| `ValueDecision` | `MomentSwingUS_Decision` | template.py, cxo.py, test_cxo_report.py |
| `MorningReportData.daytrade_candidates` | `scalpday_jp_candidates` | template.py, cxo.py |
| `MorningReportData.daytrade_records` | `scalpday_us_records` | template.py, cxo.py |
| `MorningReportData.daytrade_gross_pl` | `scalpday_us_gross_pl` | template.py, cxo.py |
| `MorningReportData.daytrade_fees` | `scalpday_us_fees` | template.py, cxo.py |
| `MorningReportData.daytrade_net_pl` | `scalpday_us_net_pl` | template.py, cxo.py |

注意: `build_morning_html()` 内の HTML テンプレート文字列（日本語ラベルを含む）にも
"デイトレ" が残るため、表示ラベルをどこまで変えるかも合わせて検討すること。
