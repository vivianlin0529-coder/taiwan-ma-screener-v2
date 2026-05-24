#!/usr/bin/env python3
"""
Taiwan MA Screener v3 — 均線糾結篩選（日更）
篩選條件（依圖片需求更新）:
  ①  排除 ETF（代號首碼 "0"）
  ②  排除金融保險股（含金融/保險/銀行/金控/證券/票券）
  ③  成交量 ≥ 1000 張
  ④  日線 MA 方向：排除 MA5↓ MA10↓ MA22↓（下下下）；週線需往上
  ⑤  BIAS 三線合一: BIAS1<2.4% | BIAS2<3.0% | BIAS3<6.4%
  ⑥  布林縮軌：今日上軌 < 昨日上軌 且 今日下軌 > 昨日下軌
標註（非篩選條件）:
  ⑦  外資連三買超 → 橘底標註
  ⑧  營收超越去年同期 → 粉紅底標註（MOPS 資料）
排序：成交量由大到小
"""

import os, sys, time, json, logging, warnings
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import twstock
from datetime import datetime, timedelta, date
from io import StringIO

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ── 參數 ──────────────────────────────────────────────────────────────────────
VOLUME_MIN   = 1000
BIAS1_MAX    = 0.024    # 2.4%
BIAS2_MAX    = 0.030    # 3.0%
BIAS3_MAX    = 0.064    # 6.4%
FOREIGN_N    = 3
SLEEP        = 0.35

EMAIL_SENDER   = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
EMAIL_RECEIVER = os.environ.get('MAIL_TO', EMAIL_SENDER)
GMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

FIN_KEYWORDS = ['金融', '保險', '銀行', '金控', '證券', '票券']

# ── 外資連買 ──────────────────────────────────────────────────────────────────
def _recent_trading_dates(n=12):
    dates, d = [], datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    return dates

def _fetch_twse_foreign_day(ds):
    try:
        data = requests.get(
            f'https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={ds}&selectType=ALL',
            timeout=20).json()
        if data.get('stat') != 'OK' or not data.get('data'): return {}
        result = {}
        for row in data['data']:
            code = str(row[0]).strip()
            if not (len(code) == 4 and code.isdigit()): continue
            try: result[code] = int(str(row[4]).replace(',','').replace('+','').strip())
            except: pass
        return result
    except: return {}

def _fetch_tpex_foreign_day(ds):
    try:
        ymd = f'{ds[:4]}/{ds[4:6]}/{ds[6:]}'
        raw = b''
        for chunk in requests.get(f'https://www.tpex.org.tw/openapi/v1/tpex_fund_wforeign?date={ymd}',
                                  timeout=30, stream=True).iter_content(8192): raw += chunk
        result = {}
        for r in json.loads(raw):
            code = str(r.get('SecuritiesCompanyCode','')).strip()
            if not (len(code) == 4 and code.isdigit()): continue
            try: result[code] = int(str(r.get('ForeignNetBuy','0') or '0').replace(',','').replace('+','').strip())
            except: pass
        return result
    except: return {}

def build_foreign_dict():
    log.info(f'⑦  抓外資連買資料（最近 {FOREIGN_N} 交易日）...')
    candidates = _recent_trading_dates(FOREIGN_N + 7)
    collected = []
    for ds in candidates:
        if len(collected) >= FOREIGN_N: break
        twse = _fetch_twse_foreign_day(ds); time.sleep(0.3)
        tpex = _fetch_tpex_foreign_day(ds); time.sleep(0.3)
        merged = {**tpex, **twse}
        if merged:
            collected.append(merged)
            log.info(f'   外資 {ds}: TWSE {len(twse)} + TPEx {len(tpex)}')
    if len(collected) < FOREIGN_N:
        log.warning(f'外資資料不足 {FOREIGN_N} 天，跳過連買標註')
        return {}
    all_codes = set()
    for d in collected: all_codes.update(d.keys())
    return {code: [d.get(code, 0) for d in collected] for code in all_codes}

# ── 營收超越去年同期 ─────────────────────────────────────────────────────────
def build_revenue_yoy_dict():
    """
    透過 TWSE/TPEx MOPS 月營收 API 建立 {code: True/False} 字典。
    若 API 失敗則回傳空 dict（不影響主要篩選流程）。
    """
    today = date.today()
    # 最新可用月份：通常當月10日後才有上月資料
    if today.day >= 10:
        rev_month = today.month - 1 if today.month > 1 else 12
        rev_year_g = today.year if today.month > 1 else today.year - 1
    else:
        rev_month = today.month - 2 if today.month > 2 else (12 + today.month - 2)
        rev_year_g = today.year if today.month > 2 else today.year - 1
    rev_year_roc = rev_year_g - 1911
    rev_year_roc_ly = rev_year_roc - 1

    log.info(f'⑧  抓月營收 民國{rev_year_roc}年{rev_month}月 與 去年同月...')

    def fetch_mops_revenue(typek, year_roc, month):
        try:
            resp = requests.post(
                'https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs',
                data={'step':1,'firstin':1,'off':1,'TYPEK':typek,'year':year_roc,'month':month},
                headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/x-www-form-urlencoded'},
                timeout=30)
            tables = pd.read_html(StringIO(resp.text), header=0)
            result = {}
            for tbl in tables:
                cols = [str(c) for c in tbl.columns]
                code_col = next((c for c in cols if '代號' in c or 'code' in c.lower()), None)
                rev_col  = next((c for c in cols if '營業收入' in c or '月' in c and '收入' in c), None)
                if code_col and rev_col:
                    for _, row in tbl.iterrows():
                        code = str(row[code_col]).strip()
                        if not (len(code) == 4 and code.isdigit()): continue
                        try:
                            val = float(str(row[rev_col]).replace(',',''))
                            result[code] = val
                        except: pass
            return result
        except Exception as e:
            log.debug(f'MOPS {typek} {year_roc}/{month}: {e}')
            return {}

    rev_yoy = {}
    for typek in ['sii', 'otc']:
        cur  = fetch_mops_revenue(typek, rev_year_roc, rev_month);  time.sleep(1)
        prev = fetch_mops_revenue(typek, rev_year_roc_ly, rev_month); time.sleep(1)
        for code, val in cur.items():
            if code in prev and prev[code] > 0:
                rev_yoy[code] = val > prev[code]

    log.info(f'   營收字典: {len(rev_yoy)} 檔（粉紅底標記若超越去年同期）')
    return rev_yoy

# ── 市值字典 ─────────────────────────────────────────────────────────────────
def build_shares_dict():
    shares = {}
    try:
        for d in requests.get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L', timeout=20).json():
            s = d.get('已發行普通股數或TDR原股發行股數','0') or '0'
            shares[d.get('公司代號','')] = int(s.replace(',',''))
    except: pass
    try:
        raw = b''
        for chunk in requests.get('https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
                                  timeout=30, stream=True).iter_content(8192): raw += chunk
        for d in json.loads(raw):
            s = d.get('IssueShares','0') or '0'
            shares[d.get('SecuritiesCompanyCode','')] = int(s.replace(',',''))
    except: pass
    log.info(f'股數字典: {len(shares)} 筆')
    return shares

# ── 候選股清單 ────────────────────────────────────────────────────────────────
def fetch_candidates():
    twse_map = {}
    try:
        for r in requests.get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL', timeout=20).json():
            code = r['Code']
            if len(code) == 4 and code.isdigit():
                twse_map[code] = {'vol': int((r.get('TradeVolume','0') or '0').replace(',','')), 'suffix':'.TW'}
    except Exception as e: log.error(f'TWSE list: {e}')

    tpex_map = {}
    for attempt in range(3):
        try:
            raw = b''
            for chunk in requests.get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
                                      timeout=30, stream=True).iter_content(8192): raw += chunk
            for r in json.loads(raw):
                code = r['SecuritiesCompanyCode']
                if len(code) == 4 and code.isdigit():
                    tpex_map[code] = {'vol': int((r.get('TradingShares','0') or '0').replace(',','')), 'suffix':'.TWO'}
            break
        except: time.sleep(2)

    active = {**tpex_map, **twse_map}
    candidates, cnt_etf, cnt_fin, cnt_vol = [], 0, 0, 0
    for code, meta in active.items():
        if code.startswith('0'):                                      cnt_etf += 1; continue
        info  = twstock.codes.get(code)
        group = (info.group if info else '') or ''
        if any(kw in group for kw in FIN_KEYWORDS):                   cnt_fin += 1; continue
        if meta['vol'] / 1000 < VOLUME_MIN:                           cnt_vol += 1; continue
        candidates.append({'code':code, 'name':info.name if info else code, 'group':group, 'suffix':meta['suffix']})

    log.info(f'候選股 {len(candidates)} 檔 （排除 ETF:{cnt_etf}/金融:{cnt_fin}/量不足:{cnt_vol}）')
    return candidates

# ── 單股篩選 ──────────────────────────────────────────────────────────────────
def screen_one(stock, shares_dict, foreign_dict, rev_yoy_dict):
    code, suffix = stock['code'], stock['suffix']
    try:
        ticker = yf.Ticker(f'{code}{suffix}')
        df = ticker.history(period='14mo', auto_adjust=True)
        if df is None or df.empty or len(df) < 65: return None
        df.index = pd.to_datetime(df.index).tz_localize(None)

        # ── 計算 MA ──
        for p in [5, 10, 22, 60]:
            df[f'MA{p}'] = df['Close'].rolling(p, min_periods=p).mean()

        # ── Bollinger Bands（20期 2σ）──
        mid = df['Close'].rolling(20).mean()
        sig = df['Close'].rolling(20).std(ddof=0)
        df['BB_upper'] = mid + 2 * sig
        df['BB_lower'] = mid - 2 * sig

        df = df.dropna(subset=['MA60','MA22','MA10','MA5','BB_upper','BB_lower'])
        if len(df) < 3: return None

        latest, prev = df.iloc[-1], df.iloc[-2]
        close    = float(latest['Close'])
        vol_lots = float(latest['Volume']) / 1000

        # ③ 量
        if vol_lots < VOLUME_MIN: return None

        # ── MA 方向 ──
        ma5  = float(latest['MA5']);  ma5p  = float(prev['MA5'])
        ma10 = float(latest['MA10']); ma10p = float(prev['MA10'])
        ma22 = float(latest['MA22']); ma22p = float(prev['MA22'])
        ma60 = float(latest['MA60'])

        ma5_dir  = '↑' if ma5  > ma5p  else '↓'
        ma10_dir = '↑' if ma10 > ma10p else '↓'
        ma22_dir = '↑' if ma22 > ma22p else '↓'

        # ④ 排除 下下下
        if ma5_dir == '↓' and ma10_dir == '↓' and ma22_dir == '↓': return None

        # ④ 週線需往上
        df_w = df.resample('W-FRI').last().dropna(subset=['Close'])
        if len(df_w) >= 2:
            if float(df_w['Close'].iloc[-1]) <= float(df_w['Close'].iloc[-2]):
                return None

        # ⑤ BIAS 糾結
        bias1 = abs(ma5  - ma10) / ma10
        bias2 = abs(ma10 - ma22) / ma22
        bias3 = abs(ma22 - ma60) / ma60
        if not (bias1 < BIAS1_MAX and bias2 < BIAS2_MAX and bias3 < BIAS3_MAX): return None

        # ⑥ 布林縮軌
        bbu_t = float(latest['BB_upper']); bbu_y = float(prev['BB_upper'])
        bbl_t = float(latest['BB_lower']); bbl_y = float(prev['BB_lower'])
        if not (bbu_t < bbu_y and bbl_t > bbl_y): return None

        # ── 布林狀態 ──
        bb_status = '突破上軌' if close > bbu_t else ('跌破下軌' if close < bbl_t else '')

        # ── 達華斯箱 ──
        rec20 = df.iloc[-20:]
        box_top = float(rec20['High'].max())
        box_wid = (box_top - float(rec20['Low'].min())) / box_top * 100
        below_t = (box_top - close) / box_top * 100
        darvas  = '✓' if (box_wid < 12.0 and below_t < 5.0) else ''

        # ⑦ 外資連三買（標註）
        f3 = foreign_dict.get(code, [])
        foreign_ok = len(f3) >= FOREIGN_N and all(n > 0 for n in f3[-FOREIGN_N:])

        # ⑧ 營收超越去年同期（標註）
        revenue_ok = rev_yoy_dict.get(code, False)

        # ── 市值 ──
        shares = shares_dict.get(code, 0)
        mktcap = round(shares * close / 1e8, 0) if shares > 0 and close > 0 else None

        # ── 漲跌幅 ──
        prev_c = float(prev['Close'])
        chg    = (close - prev_c) / prev_c * 100

        return {
            '股票代號':   code,
            '股票名稱':   stock['name'],
            '市值(億)':   mktcap,
            '收盤價':     round(close, 2),
            '漲跌幅(%)':  round(chg, 2),
            'MA5':        round(ma5,  1),
            'MA10':       round(ma10, 1),
            'MA22':       round(ma22, 1),
            'MA5方向':    ma5_dir,
            'MA10方向':   ma10_dir,
            'MA22方向':   ma22_dir,
            'BIAS1':      bias1,
            'BIAS2':      bias2,
            'BIAS3':      bias3,
            '布林上軌':   round(bbu_t, 2),
            '布林上軌昨': round(bbu_y, 2),
            '布林下軌':   round(bbl_t, 2),
            '布林下軌昨': round(bbl_y, 2),
            '布林狀態':   bb_status,
            '達華斯箱':   darvas,
            '外資連三買': foreign_ok,
            '營收超越去年': revenue_ok,
            '成交量(張)': int(vol_lots),
        }
    except Exception as e:
        log.debug(f'[{code}] skip: {e}')
        return None

# ── 主流程 ────────────────────────────────────────────────────────────────────
def run(max_stocks=None):
    log.info('='*65)
    log.info('  Taiwan MA Screener v3 — 均線糾結+布林縮軌')
    log.info('='*65)
    t0 = datetime.now()

    shares_dict  = build_shares_dict()
    foreign_dict = build_foreign_dict()
    rev_yoy_dict = build_revenue_yoy_dict()
    candidates   = fetch_candidates()

    if max_stocks:
        candidates = candidates[:max_stocks]
        log.info(f'[PREVIEW] 只掃前 {max_stocks} 檔')

    results, total = [], len(candidates)
    cnt_pass = 0

    for i, stock in enumerate(candidates):
        if i % 50 == 0 and i > 0:
            log.info(f'進度 {i}/{total} 通過:{len(results)}')
        r = screen_one(stock, shares_dict, foreign_dict, rev_yoy_dict)
        if r:
            results.append(r)
            flag = ''
            if r['外資連三買']:  flag += '🟠外資 '
            if r['營收超越去年']: flag += '🩷營收 '
            if r['達華斯箱']:    flag += '🟡箱型 '
            log.info(f"✅ {r['股票代號']} {r['股票名稱']:8s} 量:{r['成交量(張)']}張 "
                     f"方向:{r['MA5方向']}{r['MA10方向']}{r['MA22方向']} "
                     f"BIAS:{r['BIAS2']:.3%} {flag}")
        time.sleep(SLEEP)

    elapsed = (datetime.now() - t0).seconds
    log.info(f'完成: {len(results)} 檔通過 / {total} 檔掃描 / {elapsed}s')

    if results:
        df_out = (pd.DataFrame(results)
                  .sort_values('成交量(張)', ascending=False)
                  .reset_index(drop=True))
        os.makedirs('output', exist_ok=True)
        label    = datetime.now().strftime('%Y%m%d_%H%M')
        csv_path = f'output/screener_{label}.csv'
        df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
        log.info(f'CSV: {csv_path}')
        return df_out
    log.info('今日無符合條件股票')
    return pd.DataFrame()

if __name__ == '__main__':
    max_n = None
    if '--preview' in sys.argv:
        idx = sys.argv.index('--preview')
        max_n = int(sys.argv[idx+1]) if idx+1 < len(sys.argv) else 30
    run(max_stocks=max_n)
