#!/usr/bin/env python3
"""
screener_10quan.py — 十全奧義選股（日更）
基本條件:
  B1. 近一交易日股價創 3 日新高
  B2. 股價 > 3 元
十全奧義參數:
  P1. 日振幅 > 1%           (high - low) / prev_close * 100
  P2. 成交量 > 200 張
  P3. 換手率 > 0.5%         volume / shares * 100
  P4. 近1季 EPS > 0
  P5. 本益比 < 20
  [P6, P7 未擷取到，後續補入]
  P8. 9K 今 > 9K 昨         KD 指標 K 值上升
  P9. 今 MACD OSC(5,10,10) > 昨 MACD OSC
  P10.昨 MACD OSC(5,10,10) <= 前 MACD OSC（V底反轉，今日開始往上）
排序：成交量由大到小
"""

import os, sys, time, json, logging, warnings
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import twstock
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ── 篩選參數 ───────────────────────────────────────────────────────────────────
PRICE_MIN      = 3.0      # B2  股價下限
AMP_MIN        = 1.0      # P1  振幅 %
VOLUME_MIN     = 200      # P2  成交量（張）
TURNOVER_MIN   = 0.5      # P3  換手率 %
PE_MAX         = 20.0     # P5  本益比上限
SLEEP          = 0.35

EMAIL_SENDER   = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
EMAIL_RECEIVER = os.environ.get('MAIL_TO', EMAIL_SENDER)
GMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

FIN_KEYWORDS   = ['金融', '保險', '銀行', '金控', '證券', '票券']

# ── 市值/股數字典 ─────────────────────────────────────────────────────────────
def build_shares_dict():
    shares = {}
    try:
        for d in requests.get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L', timeout=20).json():
            s = d.get('已發行普通股數或TDR原股發行股數', '0') or '0'
            shares[d.get('公司代號', '')] = int(s.replace(',', ''))
    except: pass
    try:
        raw = b''
        for chunk in requests.get('https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
                                  timeout=30, stream=True).iter_content(8192):
            raw += chunk
        for d in json.loads(raw):
            s = d.get('IssueShares', '0') or '0'
            shares[d.get('SecuritiesCompanyCode', '')] = int(s.replace(',', ''))
    except: pass
    log.info(f'股數字典: {len(shares)} 筆')
    return shares

# ── 候選股清單 ────────────────────────────────────────────────────────────────
def fetch_candidates():
    twse_map, tpex_map = {}, {}
    try:
        for r in requests.get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
                              timeout=20).json():
            code = r['Code']
            if len(code) == 4 and code.isdigit():
                twse_map[code] = {
                    'vol': int((r.get('TradeVolume', '0') or '0').replace(',', '')),
                    'suffix': '.TW'
                }
    except Exception as e:
        log.error(f'TWSE list: {e}')

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
        except:
            time.sleep(2)

    active = {**tpex_map, **twse_map}
    candidates = []
    cnt_etf = cnt_fin = cnt_vol = 0
    for code, meta in active.items():
        if code.startswith('0'):
            cnt_etf += 1; continue
        info  = twstock.codes.get(code)
        group = (info.group if info else '') or ''
        if any(kw in group for kw in FIN_KEYWORDS):
            cnt_fin += 1; continue
        # 初步量過濾（200張）
        if meta['vol'] / 1000 < VOLUME_MIN:
            cnt_vol += 1; continue
        candidates.append({
            'code':   code,
            'name':   info.name if info else code,
            'group':  group,
            'suffix': meta['suffix'],
        })
    log.info(f'候選股 {len(candidates)} 檔 '
             f'（排除 ETF:{cnt_etf}/金融:{cnt_fin}/量不足:{cnt_vol}）')
    return candidates

# ── KD 指標（9日，台股平滑化）─────────────────────────────────────────────────
def compute_kd(df, period=9):
    """回傳 K 序列（list），最後至少 3 個值"""
    low_min  = df['Low'].rolling(period, min_periods=period).min()
    high_max = df['High'].rolling(period, min_periods=period).max()
    diff     = high_max - low_min
    rsv      = ((df['Close'] - low_min) / diff.replace(0, np.nan) * 100).fillna(50)

    K_vals = [50.0]
    for v in rsv.dropna():
        K_vals.append(round(2 / 3 * K_vals[-1] + 1 / 3 * float(v), 4))
    return K_vals  # 最後[-1]=今, [-2]=昨

# ── MACD OSC（5,10,10）─────────────────────────────────────────────────────────
def compute_macd_osc(series, fast=5, slow=10, signal=10):
    ema_f  = series.ewm(span=fast,   adjust=False).mean()
    ema_s  = series.ewm(span=slow,   adjust=False).mean()
    dif    = ema_f - ema_s
    sig    = dif.ewm(span=signal, adjust=False).mean()
    osc    = dif - sig   # 輔線值 / histogram
    return osc

# ── EPS / P/E（yfinance，允許失敗）──────────────────────────────────────────
def get_fundamentals(ticker_obj):
    try:
        info = ticker_obj.fast_info
        pe   = getattr(info, 'p_e_ratio', None)
        # 季EPS透過 quarterly_earnings
        qe = ticker_obj.quarterly_earnings
        if qe is not None and not qe.empty:
            eps_q = float(qe['Earnings'].iloc[-1]) if 'Earnings' in qe.columns else None
        else:
            eps_q = None
        return pe, eps_q
    except:
        return None, None

# ── 單股篩選 ──────────────────────────────────────────────────────────────────
def screen_one(stock, shares_dict):
    code, suffix = stock['code'], stock['suffix']
    try:
        ticker = yf.Ticker(f'{code}{suffix}')
        df = ticker.history(period='6mo', auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        # 排除週六日（yfinance 偶爾夾帶週末列）
        df = df[df.index.dayofweek < 5]
        if df.empty or len(df) < 30: return None

        latest = df.iloc[-1]
        prev   = df.iloc[-2]

        close      = float(latest['Close'])
        high_today = float(latest['High'])
        low_today  = float(latest['Low'])
        prev_close = float(prev['Close'])
        vol_lots   = float(latest['Volume']) / 1000

        # B2 股價 > 3 元
        if close <= PRICE_MIN:
            return None

        # B1 創 3 日新高（最近3根的最高 Close 就是今天）
        three_day_high = float(df['Close'].iloc[-3:].max())
        if close < three_day_high:
            return None

        # P1 振幅 > 1%
        amplitude = (high_today - low_today) / prev_close * 100
        if amplitude <= AMP_MIN:
            return None

        # P2 成交量 > 200 張（已在候選股預篩過，但再確認最新一日）
        if vol_lots < VOLUME_MIN:
            return None

        # P3 換手率 > 0.5%
        shares = shares_dict.get(code, 0)
        turnover = (float(latest['Volume']) / shares * 100) if shares > 0 else 0.0
        if turnover < TURNOVER_MIN:
            return None

        # P4/P5 基本面（允許資料缺失則跳過該條件）
        pe, eps_q = get_fundamentals(ticker)
        if eps_q is not None and eps_q <= 0:
            return None
        if pe is not None and pe >= PE_MAX:
            return None

        # P8 K 值今 > 昨
        K_vals = compute_kd(df)
        if len(K_vals) < 2:
            return None
        k_today = K_vals[-1];  k_prev = K_vals[-2]
        if k_today <= k_prev:
            return None

        # P9 MACD OSC 今 > 昨
        # P10 MACD OSC 昨 < 前（V底反轉）
        osc = compute_macd_osc(df['Close'], fast=5, slow=10, signal=10)
        if len(osc) < 3:
            return None
        osc_t  = float(osc.iloc[-1])
        osc_y  = float(osc.iloc[-2])
        osc_yy = float(osc.iloc[-3])
        if not (osc_t > osc_y and osc_y <= osc_yy):
            return None   # P9: 今>昨  P10: 昨<=前（OSC V底反轉）

        # 漲跌幅
        chg = (close - prev_close) / prev_close * 100

        return {
            '股票代號':   code,
            '股票名稱':   stock['name'],
            '收盤價':     round(close, 2),
            '漲跌幅(%)':  round(chg, 2),
            '振幅(%)':    round(amplitude, 2),
            '量(張)':     int(vol_lots),
            '換手率(%)':  round(turnover, 2),
            'EPS(季)':    round(eps_q, 2) if eps_q is not None else None,
            'P/E':        round(pe, 1) if pe is not None else None,
            'K值':        round(k_today, 1),
            'MACD OSC':   round(osc_t, 4),
            'OSC昨':      round(osc_y, 4),
            'OSC前':      round(osc_yy, 4),
            '3日新高':    '✓',
        }
    except Exception as e:
        log.debug(f'[{code}] skip: {e}')
        return None

# ── 主流程 ────────────────────────────────────────────────────────────────────
def run(max_stocks=None):
    log.info('=' * 60)
    log.info('  十全奧義選股 screener_10quan v1')
    log.info('=' * 60)
    t0 = datetime.now()

    shares_dict = build_shares_dict()
    candidates  = fetch_candidates()

    if max_stocks:
        candidates = candidates[:max_stocks]
        log.info(f'[PREVIEW] 只掃前 {max_stocks} 檔')

    results, total = [], len(candidates)
    for i, stock in enumerate(candidates):
        if i % 50 == 0 and i > 0:
            log.info(f'進度 {i}/{total} 通過:{len(results)}')
        r = screen_one(stock, shares_dict)
        if r:
            results.append(r)
            log.info(
                f"✅ {r['股票代號']} {r['股票名稱']:8s} "
                f"收:{r['收盤價']} 振:{r['振幅(%)']:.1f}% "
                f"K:{r['K值']:.1f} OSC:{r['MACD OSC']:.4f}"
            )
        time.sleep(SLEEP)

    elapsed = (datetime.now() - t0).seconds
    log.info(f'完成: {len(results)} 檔 / {total} 掃描 / {elapsed}s')

    if results:
        df_out = (pd.DataFrame(results)
                  .sort_values('量(張)', ascending=False)
                  .reset_index(drop=True))
        os.makedirs('output', exist_ok=True)
        label    = datetime.now().strftime('%Y%m%d_%H%M')
        csv_path = f'output/quan_{label}.csv'
        df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
        log.info(f'CSV: {csv_path}')
        return df_out
    log.info('今日無符合條件股票')
    return pd.DataFrame()


if __name__ == '__main__':
    max_n = None
    if '--preview' in sys.argv:
        idx   = sys.argv.index('--preview')
        max_n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
    run(max_stocks=max_n)
