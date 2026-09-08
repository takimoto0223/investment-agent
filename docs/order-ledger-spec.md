# 発注結末の 3 値化と意図キー台帳 設計記録

> **ステータス**: 2026-09-08 設計完了・**実装は承認待ち**。棚卸しの観測はすべて実コードと
> インストール済み alpaca-py 0.43.4 の実測。残タスクは [docs/backlog.md](backlog.md) を参照。
> 元の型: addyosmani/agent-skills の api-and-interface-design (呼び出しの結末は成功・失敗・不明の 3 つ、
> 冪等キーは意図から導出して unique 制約で claim、再送は依存の劣化に相関する)。Vault の決定
> `10_Wiki/second-brain/2026-09-08-decision-addyosmani_agent-skills…` の「投資」表 1 行目と保留事項 ⑥。

---

## 問題 (棚卸しで確定した事実)

| # | 事実 | 場所 |
|---|---|---|
| 1 | `OrderResult` は `success: bool` の 2 値。同じ dataclass が 2 本に重複定義 | brokers/alpaca.py, brokers/kabu.py |
| 2 | 発注は `except Exception` で一括して `success=False`。ReadTimeout・5xx・接続断も「失敗」になる | 両ブローカーの `send_*_order` |
| 3 | alpaca-py は **HTTP timeout を一切付けない** (rest.py に timeout の記述ゼロ)。発注が無限に待ちうる | alpaca.common.rest.RESTClient |
| 4 | alpaca-py の既定は **429 と 504 で 3 回まで自動再送** (3 秒待ち)。POST /orders も対象 → SDK 内で二重発注が起きる経路 | `DEFAULT_RETRY_EXCEPTION_CODES=[429, 504]` |
| 5 | `client_order_id` を渡していない。alpaca-py は `OrderRequest.client_order_id` と `TradingClient.get_order_by_client_id()` を持つ | brokers/alpaca.py |
| 6 | kabu の `POST /sendorder` にはクライアント側の注文識別子の欄が無い。`GET /orders` は symbol / side / cashmargin / updtime / state で絞れ、`ID` `RecvTime` `OrderQty` `Price` を返す | kabu_STATION_API.yaml |
| 7 | 発注の呼び出し元は main.py に 9 か所。発注の前に意図を永続化する処理は無い。JP セッションは `paper=True` 固定で送らない、US は常に paper 口座へ送る | main.py |
| 8 | ScalpDay_US の再スクリーニングは「Alpaca の保有銘柄」と `active_positions` で重複を避ける。結末が「不明」だと両方に載らず、5 分後の再スクリーニングで同じ銘柄を出せる | run_scalpday_us_session |
| 9 | ScalpDay_US (22:30〜) と MomentSwing_US (23:30〜) は別プロセスで同時刻に動く | run_*.bat |
| 10 | 大口承認 (総資産 40% かつ 1,000 万円) は Critic のプロンプト規則のみで、承認を受けて発注するコード経路はまだ無い | prompts/all_agents.py |
| 11 | brokers の発注テストは無い。テストは unittest 形式、pytest 9.1 で実行 | tests/ |

未確認 (設計はこれに依存しない): Alpaca が同一 `client_order_id` の 2 通目を拒否するか (公式 docs 2 頁に明記なし)。

## 案の比較 (評価軸: トークンコスト最小 × 作業時間最短)

| 案 | 中身 | Claude が動く量 | 人の手間 | 残る穴 |
|---|---|---|---|---|
| A 最小 | 結末 3 値 + 例外分類 + 不明時の照会だけ。台帳なし | 小 (brokers 2 本 + テスト) | なし | プロセス再起動をまたぐ二重発注は防げない |
| **B 推し** | A + 意図キー台帳 (SQLite 1 ファイル、UNIQUE で claim)。発注は `place_order` の一本道 | 中 (台帳 ~80 行 + 配線 9 か所 + テスト) | paper 検証の起動 1 回 | 承認経路との接続は経路ができてから |
| C 重 | B + ブローカー状態の常時同期 + 承認ワークフロー | 大 | 承認 UI の判断 | 繋ぐ先 (承認経路) がまだ無い → YAGNI |

## 設計 (案 B)

### 1. 結末 3 値 — `brokers/order_result.py` (2 本の重複定義をここへ統合)

```python
class OrderOutcome(str, Enum):
    SENT = "sent"          # ブローカーが受け付けた (order_id あり)
    FAILED = "failed"      # 届いていない確証あり → 再送してよい
    UNKNOWN = "unknown"    # 届いたか分からない → 照会してから。再送しない

@dataclass
class OrderResult:
    outcome: OrderOutcome
    order_id: Optional[str]
    message: str
    raw: Optional[object] = None
    @property
    def success(self) -> bool:       # 互換: main.py の `if result.success` は無改修で動く
        return self.outcome is OrderOutcome.SENT
```

例外の分類 `classify_transport_error(exc) -> OrderOutcome` (両ブローカー共通):

| 分類 | 条件 |
|---|---|
| FAILED | `ConnectTimeout`、接続前の `ConnectionError` (`NewConnectionError` / 接続拒否)、HTTP 4xx (Alpaca は `APIError.status_code`、kabu は `HTTPError.response.status_code`)、kabu の `Result != 0` |
| UNKNOWN | `ReadTimeout`、送信後の `ConnectionError` (reset・切断)、HTTP 5xx、JSON デコード失敗、想定外の例外 (安全側) |
| UNKNOWN (要照会) | Alpaca の 4xx でもメッセージに `client_order_id must be unique` を含む = すでに届いている |

alpaca-py の是正 (事実 3・4): `TradingClient` を `retry_exception_codes=[429]` で生成し (504 の自動再送を切る)、
Session に timeout を付ける (最小の手は実装時に決める)。意図キーがあっても SDK 内再送は 422 で弾かれるだけで
経路として不健全なので切る。

### 2. 意図キー — `brokers/order_ledger.py`

`make_intent_key(strategy, market, symbol, side, session_date, tag)` → `"{strategy}:{market}:{symbol}:{side}:{session_date}:{tag}"`

| 場面 | tag | 一意性の根拠 |
|---|---|---|
| MomentSwing の新規 | `entry` | 同一銘柄は 1 日 1 回 |
| ScalpDay の新規・再エントリー | `entry@<シグナル足の時刻 bars_5min[-1]["t"]>` | 同日の再エントリーを足で区別。時計でなくデータから導く |
| 損切り | `stop` | 建玉 1 つに 1 回 |
| 引け前決済 | `eod` | 同上 |

- `session_date` はセッション開始時の JST 日付 (同じ晩の再実行で同じ値になる)。
- Alpaca は `client_order_id=key` に載せる (128 字以内。最長 60 字程度)。kabu は欄が無いので台帳のみ。
- キーを組み立てるのは main.py の各セッション関数 (strategy と market を知っているのはそこ)。UUID・現在時刻からは作らない。

### 3. 台帳 — `logs/order_ledger.sqlite3` (`.gitignore` の `*.sqlite3` 済み)

```sql
CREATE TABLE IF NOT EXISTS orders (
  intent_key TEXT PRIMARY KEY, broker TEXT, symbol TEXT, side TEXT, qty REAL, price REAL,
  outcome TEXT, order_id TEXT, message TEXT, claimed_at TEXT, updated_at TEXT);
```

- `claim(key, **intent) -> bool`: INSERT。PRIMARY KEY 衝突で False (既存が FAILED なら UPDATE して True)。
- `mark(key, outcome, order_id, message)`、`get(key)`、`pending_unknown()`。
- SQLite を選ぶ理由 (事実 9): 2 プロセスが同時に書く。JSON の read-modify-write は競合し、ロックを自前で書くと台帳本体より長い。
  sqlite3 は標準ライブラリで UNIQUE が原子的 (元の型「unique 制約で claim」そのもの)。

### 4. 発注の一本道 — `place_order(ledger, key, intent, send, lookup) -> OrderResult`

1. `claim` が False → 既存行が SENT なら送らずにその結果を返す。UNKNOWN なら 5 へ。
2. `send()` を 1 回だけ呼ぶ。
3. SENT / FAILED → `mark` して返す。
4. (再送はここには無い。FAILED 後に出し直すかは呼び出し元の判断で、出し直せば claim が UPDATE で通る)
5. UNKNOWN → `lookup()` を **3 回・2 秒間隔** (Alpaca: `get_order_by_client_id(key)`。kabu: `get_orders(symbol, side, updtime=claimed_at)`
   から claimed_at 以降・同数量の注文を探す)。見つかれば SENT に格上げ、見つからなければ UNKNOWN のまま `mark` + WARNING。
   **再送は絶対にしない。** 上限の根拠: 照会 timeout 5 秒 × 3 + 待ち 4 秒 = 最大 19 秒で、ScalpDay の監視周期 (30〜120 秒) に収まる。
6. それ以降は自動で触らない。次のセッション起動時の `reconcile_unknown()` (UNKNOWN 行を 1 回だけ照会し直す) と朝晩レポートに任せる。

### 5. main.py 9 か所の配線と UNKNOWN の扱い

- 新規が UNKNOWN → 監視対象に入れる (order_id=None)。US の引け前決済は `get_positions()` で実在を見てから close するので安全。
  JP の決済は key `eod` が一意なので二重決済にならない。
- 決済 (stop / eod) が UNKNOWN → 「建玉が残っているかもしれない」。WARNING + 台帳 + レポート。
- JP の `paper=True` の枝は今まで通り送らない (台帳にも書かない)。

### 6. レポート

`CXOReportContext` に `order_alerts: list[dict]` を足し、朝晩レポートに「結末が不明のままの発注: 銘柄・時刻・意図キー」の表 (無ければ非表示)。

### 7. 「承認 1 回 = 発注 1 回」

今は承認の実装経路が無い (事実 10)。承認経路を作るときに承認記録へ `intent_key` を持たせ、承認の前に claim する。今回は触らない (backlog に 1 行)。

### 8. テスト (実 API は叩かない)

- `tests/test_order_result.py`: 例外 → 3 値の分類表。サボタージュ: ReadTimeout を FAILED に分類したら落ちる。
- `tests/test_order_ledger.py` (tmp sqlite): 同じキー 2 回 claim → 2 回目 False / 2 接続 (別プロセス相当) でも UNIQUE /
  UNKNOWN → lookup で SENT 格上げ / lookup 3 回失敗で UNKNOWN 保持・send 呼び出し 1 回。
- `tests/test_alpaca_broker.py` (TradingClient を Mock): `client_order_id` が載る / `retry_exception_codes` に 504 が無い / timeout が付く。

### 9. paper で「不明」を作る検証 — `scripts/check_order_unknown_path.py` (paper 限定ガード)

`AlpacaBroker` の送信を「本物の `submit_order` を呼んだ直後に `ReadTimeout` を投げる」ように差し替えて 1 株の成行を出す →
`place_order` が UNKNOWN → 照会 → SENT に格上げ、Alpaca 側の注文は 1 件だけ、を確認。同じ台本で 2 通目を素で送り、
Alpaca が同一 `client_order_id` を弾くか (422 か) も見る。timeout を極端に短くする案は「届いたか」が運任せで再現性が無いので採らない。
実行は本人 (paper でも発注はルール上 Claude が押さない)。kabu 接続後に検証環境 (18081) で同じ台本を流す。

## 段取り (刻む。1 段 = 1 コミット、テスト緑で次へ)

1. brokers: `order_result.py` 統合 + 3 値 + 分類 + Alpaca `client_order_id` 引数 + SDK の再送・timeout 是正 + テスト (main.py は無改修)
2. `order_ledger.py` + `place_order` + テスト
3. main.py 9 か所の配線 + 起動時 `reconcile_unknown`
4. レポート配線
5. paper 検証 → 結果 (Alpaca の重複拒否の有無を含む) を backlog へ

## 判断点への答え (spawn 票の 3 点)

| 判断点 | 答え | 理由 |
|---|---|---|
| 台帳の置き場 | `logs/order_ledger.sqlite3` | 2 プロセス同時書き込み。標準ライブラリ、依存ゼロ |
| 不明時の自動照会 | 3 回 × 2 秒。以降は起動時 1 回 + レポート。再送ゼロ | 監視周期に収める。人が朝晩で気づける |
| paper で不明を作る方法 | 送信直後に ReadTimeout を注入 | 注文は確実に届いた状態で「不明」を再現できる |

## 意図的に触っていないもの

- 承認ワークフロー (経路がまだ無い)、`close_position` の冪等化 (Alpaca 側が建玉ベースで自然に冪等)、FXRebalance の両替 (発注ではない)。
- JP の paper 枝を「台帳には書く」に変えること (実弾配線のときに判断)。
