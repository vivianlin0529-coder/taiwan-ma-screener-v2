#!/usr/bin/env python3
"""
screener_10quan.py — 十全奧義選股（日更）
=============================================
十全奧義 10 條件（對應原始截圖編號）:
  1.  近一交易日股價創 3 日來新高
  2.  近一交易日股價 > 3 元
  3.  近一交易日股價振幅 > 1%      (High - Low) / prev_close
  4.  近一日成交量 > 200 張
  5.  近一日週轉率 > 0.5%          volume / shares_outstanding
  6.  近 1 季 EPS 合計 > 0 元     本益比 > 0 即代表 EPS 為正（TWSE/TPEx 本益比 API）
  7.  本益比 < 20                  TWSE/TPEx 本益比 API 一次抓全市場，不拖慢速度
  8.  9K 大於前一值                KD(9) 的 K 線今 > 昨
  9.  今輔線值(5,10,10 XMACD) > 昨輔線值
  10. 昨輔線值(5,10,10 XMACD) <= 前輔線值  （OSC V 底反轉）

排序：成交量由大到小
注意：條件 6/7 使用 TWSE/TPEx 本益比 API（非 yfinance），一次抓全市場，速度快
"""

import os, sys, time, json, logging, warnings
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import twstock
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── 篩選參數 ──────────────────────────────────────────────────────────────────
PRICE_MIN    = 3.0    # 條件 2：股價下限（元）
AMP_MIN      = 1.0    # 條件 3：振幅下限（%）
VOLUME_MIN   = 200    # 條件 4：成交量下限（張）
TURNOVER_MIN = 0.5    # 條件 5：週轉率下限（%）
PE_MAX       = 20.0   # 條件 7：本益比上限
SLEEP        = 0.35   # 每檔間隔（秒），避免被 API 擋掉

EMAIL_SENDER   = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
EMAIL_RECEIVER = os.environ.get('MAIL_TO', EMAIL_SENDER)
GMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

FIN_KEYWORDS = ['金融', '保險', '銀行', '金控', '證券', '票券']


# ─────────────────────────────────────────────────────────────────────────────
# 輔助：股數字典（計算週轉率用）
# ─────────────────────────────────────────────────────────────────────────────
def build_shares_dict() -> dict:
    shares = {}
    # TWSE
    try:
        for d in requests.get(
                'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
                timeout=20).json():
            s = d.get('已發行普通股數或TDR原股發行股數', '0') or '0'
            shares[d.get('公司代號', '')] = int(s.replace(',', ''))
    except Exception as e:
        log.warning(f'TWSE shares: {e}')
    # TPEx
    try:
        raw = b''
        for chunk in requests.get(
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
                timeout=30, stream=True).iter_content(8192):
            raw += chunk
        for d in json.loads(raw):
            s = d.get('IssueShares', '0') or '0'
            shares[d.get('SecuritiesCompanyCode', '')] = int(s.replace(',', ''))
    except Exception as e:
        log.warning(f'TPEx shares: {e}')
    log.info(f'股數字典: {len(shares)} 筆')
    return shares


# ─────────────────────────────────────────────────────────────────────────────
# 本益比字典（條件 6 EPS>0 / 條件 7 PE<20）
# TWSE: openapi BWIBBU_d  ／  TPEx: pera_result.php
# 一次抓全市場，per-stock 只做 dict lookup → 不拖慢主流程
# ─────────────────────────────────────────────────────────────────────────────
def build_pe_dict() -> dict:
    """回傳 {code: pe_float}；缺資料不進 dict（screen_one 略過該條件）"""
    pe = {}

    # ── TWSE 上市本益比 ──────────────────────────────────────────────────────
    try:
        for d in requests.get(
                'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d',
                timeout=20).json():
            code = d.get('Code', '').strip()
            val  = d.get('PEratio', '').strip()
            if not (len(code) == 4 and code.isdigit()): continue
            if val in ('', '--', '-', 'N/A'): continue
            try:
                pe[code] = float(val)
            except ValueError:
                pass
        log.info(f'TWSE 本益比: {len(pe)} 筆')
    except Exception as e:
        log.warning(f'TWSE BWIBBU_d: {e}')

    # ── TPEx 上櫃本益比 ──────────────────────────────────────────────────────
    try:
        d_now = datetime.now()
        while d_now.weekday() >= 5:       # 找最近交易日
            d_now -= timedelta(days=1)
        roc_date = f'{d_now.year - 1911}/{d_now.month:02d}/{d_now.day:02d}'
        url  = ('https://www.tpex.org.tw/web/stock/aftertrading/'
                f'peratio_analysis/pera_result.php?l=zh-tw&d={roc_date}&s=0,asc&o=json')
        resp = requests.get(url, timeout=20,
                            headers={'User-Agent': 'Mozilla/5.0',
                                     'Referer': 'https://www.tpex.org.tw'}).json()
        rows   = resp['tables'][0]['data']
        before = len(pe)
        for row in rows:
            code = str(row[0]).strip()
            val  = str(row[2]).strip()
            if not (len(code) == 4 and code.isdigit()): continue
            if val in ('', '--', '-', 'N/A'): continue
            try:
                pe[code] = float(val)
            except ValueError:
                pass
        log.info(f'TPEx 本益比: {len(pe) - before} 筆  總計: {len(pe)} 筆')
    except Exception as e:
        log.warning(f'TPEx pera_result: {e}')

    return pe


# ─────────────────────────────────────────────────────────────────────────────
# 候選股清單（TWSE + TPEx，排除 ETF / 金融 / 量不足）
# ─────────────────────────────────────────────────────────────────────────────
def fetch_candidates() -> list:
    twse_map, tpex_map = {}, {}

    # TWSE 當日行情
    try:
        for r in requests.get(
                'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
                timeout=20).json():
            code = r['Code']
            if len(code) == 4 and code.isdigit():
                twse_map[code] = {
                    'vol': int((r.get('TradeVolume', '0') or '0').replace(',', '')),
                    'suffix': '.TW'
                }
    except Exception as e:
        log.error(f'TWSE list: {e}')

    # TPEx 當日行情（retry 3次）
    for attempt in range(3):
        try:
            raw = b''
            for chunk in requests.get(
                    'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
                    timeout=30, stream=True).iter_content(8192):
                raw += chunk
            for r in json.loads(raw):
                code = r['SecuritiesCompanyCode']
                if len(code) == 4 and code.isdigit():
                    tpex_map[code] = {
                        'vol': int((r.get('TradingShares', '0') or '0').replace(',', '')),
                        'suffix': '.TWO'
                    }
            break
        except Exception as e:
            log.warning(f'TPEx attempt {attempt + 1}: {e}')
            time.sleep(2)

    active = {**tpex_map, **twse_map}
    candidates = []
    cnt_etf = cnt_fin = cnt_vol = 0

    for code, meta in active.items():
        # ① 排除 ETF
        if code.startswith('0'):
            cnt_etf += 1
            continue
        # ② 排除金融保險
        info  = twstock.codes.get(code)
        group = (info.group if info else '') or ''
        if any(kw in group for kw in FIN_KEYWORDS):
            cnt_fin += 1
            continue
        # 初步量過濾：條件 4（200 張）
        if meta['vol'] / 1000 < VOLUME_MIN:
            cnt_vol += 1
            continue
        candidates.append({
            'code':   code,
            'name':   info.name if info else code,
            'group':  group,
            'suffix': meta['suffix'],
        })

    log.info(f'候選股 {len(candidates)} 檔'
             f'（排除 ETF:{cnt_etf} / 金融:{cnt_fin} / 量不足:{cnt_vol}）')
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# 技術指標計算
# ─────────────────────────────────────────────────────────────────────────────
def compute_kd(df: pd.DataFrame, period: int = 9) -> list:
    """
    台股 KD 平滑計算（2/3 加權）
    回傳 K 值 list，最末為最新交易日
    """
    lo = df['Low'].rolling(period, min_periods=period).min()
    hi = df['High'].rolling(period, min_periods=period).max()
    rng = hi - lo
    rsv = ((df['Close'] - lo) / rng.replace(0, np.nan) * 100).fillna(50)

    K = [50.0]
    for v in rsv.dropna():
        K.append(round(2 / 3 * K[-1] + 1 / 3 * float(v), 4))
    return K  # K[-1]=今, K[-2]=昨


def compute_macd_osc(series: pd.Series,
                     fast: int = 5,
                     slow: int = 10,
                     signal: int = 10) -> pd.Series:
    """
    XMACD 輔線值（histogram）
    參數 (5, 10, 10) 對應截圖「5.10.10 XMACD」
    """
    dif = series.ewm(span=fast, adjust=False).mean() \
        - series.ewm(span=slow, adjust=False).mean()
    sig = dif.ewm(span=signal, adjust=False).mean()
    return dif - sig   # 輔線值



# ─────────────────────────────────────────────────────────────────────────────
# 單股篩選主函式
# ─────────────────────────────────────────────────────────────────────────────
def screen_one(stock: dict, shares_dict: dict, pe_dict: dict) -> dict | None:
    code, suffix = stock['code'], stock['suffix']
    try:
        ticker = yf.Ticker(f'{code}{suffix}')
        df = ticker.history(period='6mo', auto_adjust=True)
        if df is None or df.empty:
            return None

        # 排除週六日（yfinance 偶爾夾帶假列）
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[df.index.dayofweek < 5]   # 0=週一 … 4=週五
        if len(df) < 30:
            return None

        latest     = df.iloc[-1]
        prev       = df.iloc[-2]
        close      = float(latest['Close'])
        high_t     = float(latest['High'])
        low_t      = float(latest['Low'])
        prev_close = float(prev['Close'])
        vol_lots   = float(latest['Volume']) / 1000

        # ── 條件 2：股價 > 3 元 ──────────────────────────────────────────────
        if close <= PRICE_MIN:
            return None

        # ── 條件 1：創 3 日新高 ───────────────────────────────────────────────
        if close < float(df['Close'].iloc[-3:].max()):
            return None

        # ── 條件 3：振幅 > 1% ────────────────────────────────────────────────
        amplitude = (high_t - low_t) / prev_close * 100
        if amplitude <= AMP_MIN:
            return None

        # ── 條件 4：成交量 > 200 張（最新一日再確認）──────────────────────────
        if vol_lots < VOLUME_MIN:
            return None

        # ── 條件 5：週轉率 > 0.5% ────────────────────────────────────────────
        shares   = shares_dict.get(code, 0)
        turnover = (float(latest['Volume']) / shares * 100) if shares > 0 else 0.0
        if turnover < TURNOVER_MIN:
            return None

        # ── 條件 6：近1季 EPS > 0（PE>0 即代表獲利為正）─────────────────────
        # ── 條件 7：本益比 < 20 ──────────────────────────────────────────────
        pe_val = pe_dict.get(code)          # None = 無資料 → 略過不排除
        if pe_val is not None:
            if pe_val <= 0:                 # 條件 6：EPS<=0（虧損）→ 排除
                return None
            if pe_val >= PE_MAX:            # 條件 7：PE>=20 → 排除
                return None

        # ── 條件 8：9K 今 > 昨 ───────────────────────────────────────────────
        K_vals = compute_kd(df)
        if len(K_vals) < 2:
            return None
        k_today = K_vals[-1]
        k_prev  = K_vals[-2]
        if k_today <= k_prev:
            return None

        # ── 條件 9 & 10：MACD OSC V 底反轉 ─────────────────────────────────
        osc = compute_macd_osc(df['Close'], fast=5, slow=10, signal=10)
        if len(osc) < 3:
            return None
        osc_t  = float(osc.iloc[-1])   # 今
        osc_y  = float(osc.iloc[-2])   # 昨
        osc_yy = float(osc.iloc[-3])   # 前

        # 條件 9：今 > 昨
        if osc_t <= osc_y:
            return None
        # 條件 10：昨 <= 前
        if osc_y > osc_yy:
            return None

        # ── 計算漲跌幅 ────────────────────────────────────────────────────────
        chg = (close - prev_close) / prev_close * 100

        return {
            '股票代號':  code,
            '股票名稱':  stock['name'],
            '收盤價':    round(close, 2),
            '漲跌幅(%)': round(chg, 2),
            '振幅(%)':   round(amplitude, 2),
            '量(張)':    int(vol_lots),
            '換手率(%)': round(turnover, 2),
            'P/E':       round(pe_val, 1) if pe_val is not None else None,
            'K值(9)':    round(k_today, 1),
            'K值昨':     round(k_prev,  1),
            'OSC今':     round(osc_t,  4),
            'OSC昨':     round(osc_y,  4),
            'OSC前':     round(osc_yy, 4),
            '3日新高':   '✓',
        }
    except Exception as e:
        log.debug(f'[{code}] skip: {e}')
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def run(max_stocks: int | None = None) -> pd.DataFrame:
    log.info('=' * 60)
    log.info('  十全奧義選股  screener_10quan v2')
    log.info('=' * 60)
    t0 = datetime.now()

    shares_dict = build_shares_dict()
    pe_dict     = build_pe_dict()
    candidates  = fetch_candidates()

    if max_stocks:
        candidates = candidates[:max_stocks]
        log.info(f'[PREVIEW] 只掃前 {max_stocks} 檔')

    results, total = [], len(candidates)
    for i, stock in enumerate(candidates):
        if i % 50 == 0 and i > 0:
            log.info(f'進度 {i}/{total}  通過:{len(results)}')
        r = screen_one(stock, shares_dict, pe_dict)
        if r:
            results.append(r)
            log.info(
                f"✅ {r['股票代號']} {r['股票名稱']:8s} "
                f"收:{r['收盤價']} 振:{r['振幅(%)']:.1f}% "
                f"K:{r['K值(9)']:.1f}→{r['K值昨']:.1f}  "
                f"OSC:{r['OSC今']:.4f}←{r['OSC昨']:.4f}←{r['OSC前']:.4f}"
            )
        time.sleep(SLEEP)

    elapsed = int((datetime.now() - t0).total_seconds())
    log.info(f'完成: {len(results)} 檔通過 / {total} 檔掃描 / {elapsed}s')

    if not results:
        log.info('今日無符合條件股票')
        return pd.DataFrame()

    df_out = (pd.DataFrame(results)
              .sort_values('量(張)', ascending=False)
              .reset_index(drop=True))
    os.makedirs('output', exist_ok=True)
    label    = datetime.now().strftime('%Y%m%d_%H%M')
    csv_path = f'output/quan_{label}.csv'
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    log.info(f'CSV: {csv_path}')
    return df_out


if __name__ == '__main__':
    max_n = None
    if '--preview' in sys.argv:
        idx   = sys.argv.index('--preview')
        max_n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
    run(max_stocks=max_n)
