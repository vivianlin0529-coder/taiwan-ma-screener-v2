# Taiwan MA Screener v2

台股均線縮軌篩選器 — 自動每日掃描並輸出符合條件的股票清單。

## 篩選邏輯（由大到小）

| 關卡 | 條件 | 說明 |
|------|------|------|
| ① | 排除 ETF | 代號首碼為 `0` 者（0050, 00878…） |
| ② | 排除特定產業 | 金融保險業、紡織纖維、通信網路業 |
| ③ | 成交量 ≥ 1000 張 | 流動性過濾，排除冷門股 |
| ④ | 均線糾結（三線合一） | BIAS1 < 2.4% ｜ BIAS2 < 3.0% ｜ BIAS3 < 6.4% |
| ⑤ | 日線縮軌 | MA5/10/20 spread < 5%，且連續 3 日收縮 |
| ⑥ | 週線縮軌 | WMA5/10/20 spread < 8%，且連續 2 週收縮 |

### BIAS 定義
```
BIAS1 = |MA5  - MA10| / MA10 × 100%
BIAS2 = |MA10 - MA20| / MA20 × 100%
BIAS3 = |MA20 - MA60| / MA60 × 100%
```

### 縮軌（Contraction）定義
```
Spread = (max(MAx, MAy, MAz) - min(MAx, MAy, MAz)) / Close × 100%
縮軌   = Spread < 門檻 AND 連續 N 期 Spread 遞減
```

## 使用方法

### 本機執行
```bash
pip install -r requirements.txt

# 全量掃描（約 1900 檔，需 15-20 分鐘）
python screener.py

# Preview 模式（只跑前 50 檔，快速測試）
python screener.py --preview 50
```

### GitHub Actions
- **自動排程**：每個交易日 14:10（台灣時間）自動執行全量掃描
- **手動觸發**：Actions → Daily MA Screener → Run workflow
  - `preview` 填 `0` = 全量；填數字 = 只跑前 N 檔

## 輸出欄位

| 欄位 | 說明 |
|------|------|
| `code` | 股票代號 |
| `name` | 股票名稱 |
| `group` | 產業別 |
| `close` | 最新收盤價 |
| `vol_k` | 成交量（張） |
| `bias1/2/3` | 三線偏差 % |
| `daily_spread` | 日線 MA 縮軌幅度 % |
| `weekly_spread` | 週線 MA 縮軌幅度 % |

結果存於 `output/screener_YYYYMMDD_HHMM.csv`。

## 技術架構

- 股票清單：`twstock` 函式庫
- 歷史行情：`yfinance`（`.TW` 後綴）
- 自動化：GitHub Actions（`schedule` + `workflow_dispatch`）
